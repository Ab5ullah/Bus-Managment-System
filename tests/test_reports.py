"""
Comprehensive test suite for Module 11: Reports & Analytics Engine.
Tests:
  1. Revenue report aggregation (gross, discounts, tax, service fees, refunds, net retained)
  2. Daily, weekly, and monthly bucket grouping + date range filtering
  3. Unpaid PENDING/EXPIRED bookings strictly excluded from realized revenue
  4. Occupancy / Load Factor calculation: passenger-km / seat-km across overlapping segments
  5. Top Routes ranking and Peak Departure Hours breakdown
  6. Counter Agent walk-in sales aggregation & 5% commission reconciliation
  7. CSV & Excel (.xlsx) stream exporters
  8. REST API endpoints (/api/admin/reports/...) & Operator RBAC enforcement
"""
import csv
import io
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import (
    UserRole,
    BusType,
    BusStatus,
    TripStatus,
    BookingStatus,
    BookingSeatStatus,
    PaymentMethod,
    Gender,
)
from common.exceptions import BusinessLogicError
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.services import SeatLockService, BookingService
from cancellations.services import CancellationService
from reports.services import AnalyticsService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def bi_network(db):
    """
    Corridor: Lahore (0 km) -> Bhera (200 km) -> Islamabad (400 km)
    20-seat bus => 20 * 400 = 8000 seat-km per trip.
    """
    t_lhr = Terminal.objects.create(name="Lahore BI", code="LHR-BI", city="Lahore", is_active=True)
    t_bhr = Terminal.objects.create(name="Bhera BI", code="BHR-BI", city="Bhera", is_active=True)
    t_isb = Terminal.objects.create(name="Islamabad BI", code="ISB-BI", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-BI",
        name="Lahore-Islamabad BI Corridor",
        origin=t_lhr,
        destination=t_isb,
        total_distance_km=Decimal('400.00'),
        estimated_duration_minutes=240,
        stops_data=[
            {'terminal': t_lhr, 'stop_order': 0, 'distance_from_origin_km': Decimal('0.00'), 'offset_minutes': 0},
            {'terminal': t_bhr, 'stop_order': 1, 'distance_from_origin_km': Decimal('200.00'), 'offset_minutes': 120},
            {'terminal': t_isb, 'stop_order': 2, 'distance_from_origin_km': Decimal('400.00'), 'offset_minutes': 240},
        ]
    )

    bus = Bus.objects.create(
        registration_number="BI-BUS-20",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=20
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=5, ladies_rows=[])

    stops = list(route.stops.order_by('stop_order'))
    s0, s1, s2 = stops[0], stops[1], stops[2]

    FareService.set_fare_rule(route, s0, s2, Decimal('2000.00'))
    FareService.set_fare_rule(route, s0, s1, Decimal('1000.00'))
    FareService.set_fare_rule(route, s1, s2, Decimal('1000.00'))

    dep = timezone.now() + timedelta(hours=48)
    trip = Trip.objects.create(
        trip_number="TRIP-BI-01",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=dep + timedelta(minutes=240),
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('2000.00')
    )

    operator = User.objects.create_user(
        phone="+923001100001", role=UserRole.OPERATOR, password="Op123!", is_verified=True, is_staff=True
    )
    agent = User.objects.create_user(
        phone="+923001100002", role=UserRole.COUNTER_AGENT, password="Ag123!", is_verified=True
    )
    passenger = User.objects.create_user(
        phone="+923001100003", role=UserRole.PASSENGER, password="Pax123!", is_verified=True
    )

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': [s0, s1, s2],
        'operator': operator,
        'agent': agent,
        'passenger': passenger,
    }


def book_segment(network, seat_num, from_idx, to_idx, confirm=True, agent=None):
    trip = network['trip']
    stops = network['stops']
    s_from, s_to = stops[from_idx], stops[to_idx]
    seat = Seat.objects.get(bus=network['bus'], seat_number=seat_num)

    if agent:
        return BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=s_from,
            to_stop=s_to,
            agent_user=agent,
            passenger_name=f"Walkin {seat_num}",
            passenger_phone="+923009991111",
            passengers_data=[{"seat_id": seat.id, "passenger_name": f"Walkin {seat_num}", "gender": Gender.MALE}],
            payment_method=PaymentMethod.CASH_COUNTER,
        )

    lock = SeatLockService.lock_seats(trip=trip, seat_ids=[seat.id], from_stop=s_from, to_stop=s_to)
    b = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock['lock_token']),
        from_stop=s_from,
        to_stop=s_to,
        passenger_name=f"Web {seat_num}",
        passenger_phone=network['passenger'].phone,
        passengers_data=[{"seat_id": seat.id, "passenger_name": f"Web {seat_num}", "gender": Gender.MALE}],
        user=network['passenger']
    )
    if confirm:
        b.status = BookingStatus.CONFIRMED
        b.confirmed_at = timezone.now()
        b.save()
        b.seats.update(status=BookingSeatStatus.BOOKED)
    return b


@pytest.mark.django_db
class TestAnalyticsService:

    def test_revenue_aggregation_and_refund_netting(self, bi_network):
        # Booking 1: Confirmed full corridor (Gross 2000, Tax 100, Fee 50 => Collected 2150)
        book_segment(bi_network, '1A', 0, 2, confirm=True)
        # Booking 2: Confirmed then cancelled >24h (Collected 2150, Refunded 90% of 2100 = 1890 => Retained 260)
        b2 = book_segment(bi_network, '1B', 0, 2, confirm=True)
        CancellationService.seed_default_policies()
        CancellationService.cancel_booking(b2.pnr, reason="Test cancellation")
        # Booking 3: Unpaid PENDING booking (must be ignored!)
        book_segment(bi_network, '1C', 0, 2, confirm=False)

        rev = AnalyticsService.get_revenue_report(group_by='day')
        s = rev['summary']

        assert s['bookings_count'] == 2
        assert s['gross_fare'] == Decimal('4000.00')
        assert s['total_collected'] == Decimal('4300.00')
        assert s['refunds_issued'] == Decimal('1890.00')
        assert s['net_revenue'] == Decimal('2410.00')
        assert len(rev['series']) == 1

    def test_load_factor_passenger_km_formula(self, bi_network):
        # Bus capacity = 20 seats, Route = 400 km => 8000 seat-km
        # Booking 1: Seat 1A from stop 0->1 (200 km)
        # Booking 2: Seat 1A from stop 1->2 (200 km) -> total 400 pax-km on Seat 1A!
        # Booking 3: Seat 1B from stop 0->2 (400 km) -> total 400 pax-km on Seat 1B!
        # Total Passenger-km = 800; Seat-km = 8000; Load Factor = 10.00%
        book_segment(bi_network, '1A', 0, 1, confirm=True)
        book_segment(bi_network, '1A', 1, 2, confirm=True)
        book_segment(bi_network, '1B', 0, 2, confirm=True)

        occ = AnalyticsService.get_occupancy_report(trip_id=str(bi_network['trip'].id))
        assert occ['summary']['total_passenger_km'] == Decimal('800.00')
        assert occ['summary']['total_seat_km'] == Decimal('8000.00')
        assert occ['summary']['overall_load_factor_percentage'] == Decimal('10.00')

    def test_route_performance_and_peak_hours(self, bi_network):
        book_segment(bi_network, '2A', 0, 2, confirm=True)
        book_segment(bi_network, '2B', 0, 1, confirm=True)

        perf = AnalyticsService.get_route_performance_report()
        assert len(perf['top_routes']) == 1
        assert perf['top_routes'][0]['route_code'] == 'LHR-ISB-BI'
        assert perf['top_routes'][0]['seats_sold'] == 2
        assert perf['top_routes'][0]['fare_revenue'] == Decimal('3000.00')
        assert len(perf['peak_travel_hours']) == 1
        assert perf['peak_travel_hours'][0]['passengers'] == 2

    def test_counter_agent_sales_and_commission_reconciliation(self, bi_network):
        # Two walk-in sales via Counter Agent: 2000 + 1000 = 3000 gross
        book_segment(bi_network, '2C', 0, 2, agent=bi_network['agent'])
        book_segment(bi_network, '2D', 0, 1, agent=bi_network['agent'])

        rep = AnalyticsService.get_agent_sales_report(commission_rate=Decimal('5.00'))
        assert rep['summary']['total_agents'] == 1
        assert rep['summary']['total_gross_sales'] == Decimal('3000.00')
        assert rep['summary']['total_commissions_payable'] == Decimal('150.00')

    def test_invalid_date_range_raises_error(self):
        with pytest.raises(BusinessLogicError, match="start_date cannot be after end_date"):
            AnalyticsService.get_revenue_report(start_date='2026-12-10', end_date='2026-12-01')


@pytest.mark.django_db
class TestReportsAPIAndExports:

    def test_reports_rbac_and_endpoints(self, api_client, bi_network):
        book_segment(bi_network, '1A', 0, 2, confirm=True)
        book_segment(bi_network, '1B', 0, 1, agent=bi_network['agent'])

        # Passenger blocked (403)
        api_client.force_authenticate(user=bi_network['passenger'])
        assert api_client.get('/api/admin/reports/revenue/').status_code == status.HTTP_403_FORBIDDEN

        # Operator allowed (200)
        api_client.force_authenticate(user=bi_network['operator'])
        assert api_client.get('/api/admin/reports/revenue/?group_by=month').status_code == status.HTTP_200_OK
        assert api_client.get('/api/admin/reports/occupancy/').status_code == status.HTTP_200_OK
        assert api_client.get('/api/admin/reports/route-performance/').status_code == status.HTTP_200_OK
        assert api_client.get('/api/admin/reports/agent-sales/').status_code == status.HTTP_200_OK

    def test_csv_and_excel_export_streams(self, api_client, bi_network):
        book_segment(bi_network, '1A', 0, 2, confirm=True)
        api_client.force_authenticate(user=bi_network['operator'])

        # CSV export
        csv_res = api_client.get('/api/admin/reports/export/?type=revenue&format=csv')
        assert csv_res.status_code == status.HTTP_200_OK
        assert 'text/csv' in csv_res['Content-Type']
        rows = list(csv.reader(io.StringIO(csv_res.content.decode('utf-8'))))
        assert rows[0][0] == 'Period'
        assert len(rows) >= 2

        # Excel (.xlsx) export
        xlsx_res = api_client.get('/api/admin/reports/export/?type=occupancy&format=xlsx')
        assert xlsx_res.status_code == status.HTTP_200_OK
        assert 'spreadsheetml.sheet' in xlsx_res['Content-Type']
        wb = load_workbook(io.BytesIO(xlsx_res.content))
        ws = wb.active
        assert ws is not None
        assert ws['A1'].value == 'Trip Number'
        assert ws['A2'].value == 'TRIP-BI-01'
