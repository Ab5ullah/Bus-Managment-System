"""
Analytics & Business Intelligence Service Layer for Module 11.
Computes financial revenue reconciliation, passenger-km / seat-km Load Factor,
route performance, peak departure hours, and counter agent commission payouts.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple

from django.db.models import Q, Sum
from django.utils import timezone

from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    PaymentStatus,
    RefundStatus,
    TripStatus,
)
from common.exceptions import BusinessLogicError
from common.money import round_currency
from bookings.models import Booking, BookingSeat
from cancellations.models import Refund
from trips.models import Trip
from routes.models import Route


class AnalyticsService:
    """
    Authoritative Business Intelligence & Reporting Service.
    """

    @staticmethod
    def _parse_date_range(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        tz = timezone.get_current_timezone()
        start_dt = None
        end_dt = None

        if start_date:
            try:
                d = date.fromisoformat(str(start_date).strip())
                start_dt = timezone.make_aware(datetime.combine(d, time.min), tz)
            except ValueError:
                raise BusinessLogicError(f"Invalid start_date '{start_date}'. Expected YYYY-MM-DD.")

        if end_date:
            try:
                d = date.fromisoformat(str(end_date).strip())
                end_dt = timezone.make_aware(datetime.combine(d, time.max), tz)
            except ValueError:
                raise BusinessLogicError(f"Invalid end_date '{end_date}'. Expected YYYY-MM-DD.")

        if start_dt and end_dt and start_dt > end_dt:
            raise BusinessLogicError("start_date cannot be after end_date.")

        return start_dt, end_dt

    @staticmethod
    def _bucket_key(dt: datetime, group_by: str) -> str:
        local_dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
        if group_by == 'month':
            return local_dt.strftime('%Y-%m')
        if group_by == 'week':
            monday = local_dt - timedelta(days=local_dt.weekday())
            return monday.strftime('%Y-%m-%d')
        return local_dt.strftime('%Y-%m-%d')

    @classmethod
    def get_revenue_report(
        cls,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: str = 'day'
    ) -> Dict[str, Any]:
        """
        Aggregates gross sales, promotional discounts, taxes, service fees,
        processed refunds, and retained net revenue grouped by day, week, or month.
        """
        group_by = (group_by or 'day').lower()
        if group_by not in ('day', 'week', 'month'):
            raise BusinessLogicError("group_by must be one of: 'day', 'week', 'month'.")

        start_dt, end_dt = cls._parse_date_range(start_date, end_date)

        # Realized bookings: confirmed, completed, partially cancelled, refunded, or having a successful/refunded payment
        realized_statuses = [
            BookingStatus.CONFIRMED,
            BookingStatus.COMPLETED,
            BookingStatus.PARTIALLY_CANCELLED,
            BookingStatus.REFUNDED,
        ]
        bookings_qs = Booking.objects.filter(
            Q(status__in=realized_statuses)
            | Q(confirmed_at__isnull=False)
            | Q(payments__status__in=[PaymentStatus.SUCCESS, PaymentStatus.REFUNDED])
        ).distinct()

        refunds_qs = Refund.objects.filter(status=RefundStatus.PROCESSED)

        if start_dt:
            bookings_qs = bookings_qs.filter(created_at__gte=start_dt)
            refunds_qs = refunds_qs.filter(processed_at__gte=start_dt)
        if end_dt:
            bookings_qs = bookings_qs.filter(created_at__lte=end_dt)
            refunds_qs = refunds_qs.filter(processed_at__lte=end_dt)

        buckets: Dict[str, Dict[str, Any]] = {}

        def _ensure_bucket(key: str) -> Dict[str, Any]:
            if key not in buckets:
                buckets[key] = {
                    'period': key,
                    'bookings_count': 0,
                    'gross_fare': Decimal('0.00'),
                    'discounts': Decimal('0.00'),
                    'tax_collected': Decimal('0.00'),
                    'service_fees': Decimal('0.00'),
                    'total_collected': Decimal('0.00'),
                    'refunds_issued': Decimal('0.00'),
                    'net_revenue': Decimal('0.00'),
                }
            return buckets[key]

        totals = {
            'bookings_count': 0,
            'gross_fare': Decimal('0.00'),
            'discounts': Decimal('0.00'),
            'tax_collected': Decimal('0.00'),
            'service_fees': Decimal('0.00'),
            'total_collected': Decimal('0.00'),
            'refunds_issued': Decimal('0.00'),
            'net_revenue': Decimal('0.00'),
        }

        for row in bookings_qs.values(
            'created_at', 'total_gross_fare', 'discount_amount', 'tax_amount', 'service_fee', 'net_amount'
        ):
            key = cls._bucket_key(row['created_at'], group_by)
            b = _ensure_bucket(key)
            b['bookings_count'] += 1
            b['gross_fare'] += row['total_gross_fare']
            b['discounts'] += row['discount_amount']
            b['tax_collected'] += row['tax_amount']
            b['service_fees'] += row['service_fee']
            b['total_collected'] += row['net_amount']

            totals['bookings_count'] += 1
            totals['gross_fare'] += row['total_gross_fare']
            totals['discounts'] += row['discount_amount']
            totals['tax_collected'] += row['tax_amount']
            totals['service_fees'] += row['service_fee']
            totals['total_collected'] += row['net_amount']

        for ref in refunds_qs.values('processed_at', 'net_refund_amount'):
            key = cls._bucket_key(ref['processed_at'], group_by)
            b = _ensure_bucket(key)
            b['refunds_issued'] += ref['net_refund_amount']
            totals['refunds_issued'] += ref['net_refund_amount']

        series: List[Dict[str, Any]] = []
        for key in sorted(buckets.keys()):
            item = buckets[key]
            item['gross_fare'] = round_currency(item['gross_fare'])
            item['discounts'] = round_currency(item['discounts'])
            item['tax_collected'] = round_currency(item['tax_collected'])
            item['service_fees'] = round_currency(item['service_fees'])
            item['total_collected'] = round_currency(item['total_collected'])
            item['refunds_issued'] = round_currency(item['refunds_issued'])
            item['net_revenue'] = round_currency(item['total_collected'] - item['refunds_issued'])
            series.append(item)

        totals['gross_fare'] = round_currency(totals['gross_fare'])
        totals['discounts'] = round_currency(totals['discounts'])
        totals['tax_collected'] = round_currency(totals['tax_collected'])
        totals['service_fees'] = round_currency(totals['service_fees'])
        totals['total_collected'] = round_currency(totals['total_collected'])
        totals['refunds_issued'] = round_currency(totals['refunds_issued'])
        totals['net_revenue'] = round_currency(totals['total_collected'] - totals['refunds_issued'])

        return {
            'group_by': group_by,
            'start_date': start_date,
            'end_date': end_date,
            'summary': totals,
            'series': series,
        }

    @classmethod
    def get_occupancy_report(
        cls,
        trip_id: Optional[str] = None,
        route_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculates Load Factor / Occupancy Rate per trip and overall:
        Load Factor (%) = (Total Passenger-Kilometers / Total Seat-Kilometers) * 100
        Accounts accurately for multi-stop segment turn-over!
        """
        start_dt, end_dt = cls._parse_date_range(start_date, end_date)

        trips_qs = Trip.objects.select_related('route', 'route__origin', 'route__destination', 'bus')
        if trip_id:
            trips_qs = trips_qs.filter(id=trip_id)
        else:
            trips_qs = trips_qs.exclude(status=TripStatus.CANCELLED)

        if route_id:
            trips_qs = trips_qs.filter(route_id=route_id)
        if start_dt:
            trips_qs = trips_qs.filter(departure_datetime__gte=start_dt)
        if end_dt:
            trips_qs = trips_qs.filter(departure_datetime__lte=end_dt)

        trips = list(trips_qs.order_by('-departure_datetime'))
        trip_ids = [t.id for t in trips]

        # Preload active booked/completed seats for all matching trips in 1 query
        pax_km_by_trip: Dict[Any, Decimal] = defaultdict(lambda: Decimal('0.00'))
        seats_count_by_trip: Dict[Any, int] = defaultdict(int)

        if trip_ids:
            active_seats = BookingSeat.objects.filter(
                trip_id__in=trip_ids,
                status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.COMPLETED]
            ).values(
                'trip_id',
                'from_stop__distance_from_origin_km',
                'to_stop__distance_from_origin_km'
            )
            for s in active_seats:
                dist = (s['to_stop__distance_from_origin_km'] or Decimal('0.00')) - (
                    s['from_stop__distance_from_origin_km'] or Decimal('0.00')
                )
                if dist > Decimal('0.00'):
                    pax_km_by_trip[s['trip_id']] += dist
                seats_count_by_trip[s['trip_id']] += 1

        total_pax_km = Decimal('0.00')
        total_seat_km = Decimal('0.00')
        trip_rows: List[Dict[str, Any]] = []

        for t in trips:
            bus_capacity = t.bus.total_seats if t.bus else 0
            route_dist = t.route.total_distance_km or Decimal('0.00')
            seat_km = round_currency(Decimal(bus_capacity) * route_dist)
            pax_km = round_currency(pax_km_by_trip[t.id])

            load_factor = (
                round_currency((pax_km / seat_km) * Decimal('100.00'))
                if seat_km > Decimal('0.00') else Decimal('0.00')
            )

            total_pax_km += pax_km
            total_seat_km += seat_km

            trip_rows.append({
                'trip_id': str(t.id),
                'trip_number': t.trip_number,
                'route_code': t.route.code,
                'route_name': t.route.name,
                'bus_registration': t.bus.registration_number if t.bus else '',
                'departure_datetime': t.departure_datetime.isoformat(),
                'status': t.status,
                'bus_capacity': bus_capacity,
                'booked_segments_count': seats_count_by_trip[t.id],
                'route_distance_km': route_dist,
                'passenger_km': pax_km,
                'seat_km': seat_km,
                'load_factor_percentage': load_factor,
            })

        overall_load_factor = (
            round_currency((total_pax_km / total_seat_km) * Decimal('100.00'))
            if total_seat_km > Decimal('0.00') else Decimal('0.00')
        )

        return {
            'summary': {
                'trips_evaluated': len(trip_rows),
                'total_passenger_km': round_currency(total_pax_km),
                'total_seat_km': round_currency(total_seat_km),
                'overall_load_factor_percentage': overall_load_factor,
            },
            'trips': trip_rows,
        }

    @classmethod
    def get_route_performance_report(
        cls,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Identifies top-performing routes by net revenue & passenger volume,
        plus peak travel hours distribution across scheduled departures.
        """
        start_dt, end_dt = cls._parse_date_range(start_date, end_date)

        realized_statuses = [
            BookingStatus.CONFIRMED,
            BookingStatus.COMPLETED,
            BookingStatus.PARTIALLY_CANCELLED,
        ]
        seats_qs = BookingSeat.objects.filter(
            booking__status__in=realized_statuses,
            status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.COMPLETED]
        ).select_related('trip__route', 'trip')

        if start_dt:
            seats_qs = seats_qs.filter(booking__created_at__gte=start_dt)
        if end_dt:
            seats_qs = seats_qs.filter(booking__created_at__lte=end_dt)

        route_stats: Dict[str, Dict[str, Any]] = {}
        hour_stats: Dict[int, Dict[str, Any]] = {
            h: {'hour': f"{h:02d}:00", 'passengers': 0, 'fare_revenue': Decimal('0.00')}
            for h in range(24)
        }

        for row in seats_qs.values(
            'trip__route_id',
            'trip__route__code',
            'trip__route__name',
            'trip__departure_datetime',
            'price'
        ):
            r_id = str(row['trip__route_id'])
            if r_id not in route_stats:
                route_stats[r_id] = {
                    'route_id': r_id,
                    'route_code': row['trip__route__code'],
                    'route_name': row['trip__route__name'],
                    'seats_sold': 0,
                    'fare_revenue': Decimal('0.00'),
                }
            route_stats[r_id]['seats_sold'] += 1
            route_stats[r_id]['fare_revenue'] += row['price']

            dep_dt = row['trip__departure_datetime']
            local_dep = timezone.localtime(dep_dt) if timezone.is_aware(dep_dt) else dep_dt
            h = local_dep.hour
            hour_stats[h]['passengers'] += 1
            hour_stats[h]['fare_revenue'] += row['price']

        top_routes = sorted(
            route_stats.values(),
            key=lambda x: (x['fare_revenue'], x['seats_sold']),
            reverse=True
        )
        for r in top_routes:
            r['fare_revenue'] = round_currency(r['fare_revenue'])

        peak_hours = []
        for h in range(24):
            entry = hour_stats[h]
            if entry['passengers'] > 0:
                entry['fare_revenue'] = round_currency(entry['fare_revenue'])
                peak_hours.append(entry)
        peak_hours.sort(key=lambda x: x['passengers'], reverse=True)

        return {
            'top_routes': top_routes,
            'peak_travel_hours': peak_hours,
        }

    @classmethod
    def get_agent_sales_report(
        cls,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        commission_rate: Decimal = Decimal('5.00')
    ) -> Dict[str, Any]:
        """
        Reconciles ticket sales volume and commission payouts per Counter Agent / Operator.
        """
        start_dt, end_dt = cls._parse_date_range(start_date, end_date)
        commission_rate = round_currency(Decimal(str(commission_rate)))
        if commission_rate < Decimal('0.00') or commission_rate > Decimal('50.00'):
            raise BusinessLogicError("Commission rate must be between 0.00% and 50.00%.")

        realized_statuses = [
            BookingStatus.CONFIRMED,
            BookingStatus.COMPLETED,
            BookingStatus.PARTIALLY_CANCELLED,
        ]
        bookings_qs = Booking.objects.filter(
            booked_by_agent__isnull=False,
            status__in=realized_statuses
        ).select_related('booked_by_agent', 'booked_by_agent__profile').prefetch_related('seats')

        if start_dt:
            bookings_qs = bookings_qs.filter(created_at__gte=start_dt)
        if end_dt:
            bookings_qs = bookings_qs.filter(created_at__lte=end_dt)

        agents_map: Dict[str, Dict[str, Any]] = {}
        total_gross = Decimal('0.00')
        total_commission = Decimal('0.00')

        for b in bookings_qs:
            agent = b.booked_by_agent
            a_id = str(agent.id)
            if a_id not in agents_map:
                profile = getattr(agent, 'profile', None)
                full_name = profile.full_name if (profile and profile.full_name) else agent.phone
                agents_map[a_id] = {
                    'agent_id': a_id,
                    'agent_name': full_name,
                    'agent_phone': agent.phone,
                    'role': agent.role,
                    'bookings_count': 0,
                    'seats_sold': 0,
                    'gross_sales': Decimal('0.00'),
                    'net_collected': Decimal('0.00'),
                    'commission_rate_percentage': commission_rate,
                    'commission_earned': Decimal('0.00'),
                }

            rec = agents_map[a_id]
            active_seats = b.seats.filter(status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.COMPLETED]).count()
            rec['bookings_count'] += 1
            rec['seats_sold'] += active_seats
            rec['gross_sales'] += b.total_gross_fare
            rec['net_collected'] += b.net_amount

        agent_rows = list(agents_map.values())
        for rec in agent_rows:
            rec['gross_sales'] = round_currency(rec['gross_sales'])
            rec['net_collected'] = round_currency(rec['net_collected'])
            rec['commission_earned'] = round_currency(
                rec['gross_sales'] * (commission_rate / Decimal('100.00'))
            )
            total_gross += rec['gross_sales']
            total_commission += rec['commission_earned']

        agent_rows.sort(key=lambda x: x['gross_sales'], reverse=True)

        return {
            'commission_rate_percentage': commission_rate,
            'summary': {
                'total_agents': len(agent_rows),
                'total_gross_sales': round_currency(total_gross),
                'total_commissions_payable': round_currency(total_commission),
            },
            'agents': agent_rows,
        }
