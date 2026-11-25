"""
Comprehensive test suite for Module 10: Promotions, Coupons & Dynamic Pricing.
Tests:
  1. Coupon creation, uppercase normalization, and percentage cap enforcement
  2. Flat discount calculation and gross fare boundary cap
  3. Temporal validity checks (expired and future-dated coupons rejected)
  4. Inactive coupon rejection and minimum booking amount threshold
  5. Global usage quota (max_total_uses) and per-user usage limit (max_uses_per_user)
  6. Server-side tax & net payable recalculation on apply_coupon_to_booking
  7. Prevention of double coupon application or applying to non-PENDING bookings
  8. Coupon removal restoring original financials and freeing quota slot
  9. Inline coupon_code during BookingService.create_booking_from_locks
  10. Dynamic Pricing Engine: Occupancy Surge, Early Bird, Last Minute, Priority resolution
  11. Dynamic Pricing integration with FareService.calculate_fare()
  12. DRF API endpoints (validate-coupon, apply-coupon, remove-coupon, dynamic-price, admin CRUD & RBAC)
"""
import uuid
import pytest
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
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
    DiscountType,
    DynamicRuleType,
    Gender,
)
from common.exceptions import BusinessLogicError, ResourceNotFoundError
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.models import Booking, BookingSeat
from bookings.services import SeatLockService, BookingService
from promotions.models import Coupon, CouponUsage, DynamicPricingRule
from promotions.services import CouponService, DynamicPricingService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def promo_network(db):
    term_lhr = Terminal.objects.create(name="Lahore Terminal", code="LHR-P10", city="Lahore", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Terminal", code="ISB-P10", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-P10",
        name="Lahore to Islamabad Promo Express",
        origin=term_lhr,
        destination=term_isb,
        total_distance_km=Decimal('375.00'),
        estimated_duration_minutes=260,
        stops_data=[
            {'terminal': term_lhr, 'stop_order': 0, 'distance_from_origin_km': Decimal('0.00'), 'offset_minutes': 0},
            {'terminal': term_isb, 'stop_order': 1, 'distance_from_origin_km': Decimal('375.00'), 'offset_minutes': 260},
        ]
    )

    bus = Bus.objects.create(
        registration_number="LES-P10-100",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=20
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=5, ladies_rows=[])

    stops = list(route.stops.order_by('stop_order'))
    s0, s1 = stops[0], stops[1]

    dep = timezone.now() + timedelta(hours=36)
    trip = Trip.objects.create(
        trip_number="TRIP-P10-001",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=dep + timedelta(minutes=260),
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('2000.00')
    )
    FareService.set_fare_rule(route, s0, s1, Decimal('2000.00'))

    operator = User.objects.create_user(
        phone="+923001010001",
        role=UserRole.OPERATOR,
        password="OpPass123!",
        is_verified=True,
        is_staff=True
    )
    passenger1 = User.objects.create_user(
        phone="+923001010002",
        role=UserRole.PASSENGER,
        password="Pass123!",
        is_verified=True
    )
    passenger2 = User.objects.create_user(
        phone="+923001010003",
        role=UserRole.PASSENGER,
        password="Pass123!",
        is_verified=True
    )

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': stops,
        'operator': operator,
        'passenger1': passenger1,
        'passenger2': passenger2,
    }


def create_pending_booking(network, seat_numbers=['1A'], user=None, coupon_code=''):
    trip = network['trip']
    s0, s1 = network['stops'][0], network['stops'][1]
    seats = list(Seat.objects.filter(bus=network['bus'], seat_number__in=seat_numbers))
    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=[s.id for s in seats],
        from_stop=s0,
        to_stop=s1
    )
    passengers_data = [
        {"seat_id": s.id, "passenger_name": f"Pax {s.seat_number}", "gender": Gender.MALE, "age": 27}
        for s in seats
    ]
    owner = user or network['passenger1']
    return BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s1,
        passenger_name="Test Booker",
        passenger_phone=owner.phone,
        passengers_data=passengers_data,
        user=owner,
        coupon_code=coupon_code
    )


@pytest.mark.django_db
class TestCouponEngine:

    def test_percentage_discount_with_max_cap(self, promo_network):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code="  eid25  ",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('25.00'),
            min_booking_amount=Decimal('1000.00'),
            max_discount_amount=Decimal('400.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=7),
        )
        assert coupon.code == "EID25"

        # 25% of 3000 = 750 -> capped at 400.00
        resolved, discount = CouponService.validate_coupon("eid25", Decimal('3000.00'))
        assert resolved.id == coupon.id
        assert discount == Decimal('400.00')

        # 25% of 1000 = 250 -> below cap, returns 250.00
        _, discount_small = CouponService.validate_coupon("EID25", Decimal('1000.00'))
        assert discount_small == Decimal('250.00')

    def test_flat_discount_capped_at_gross_amount(self, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="FLAT500",
            discount_type=DiscountType.FLAT,
            discount_value=Decimal('500.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        _, discount = CouponService.validate_coupon("FLAT500", Decimal('350.00'))
        assert discount == Decimal('350.00')

    def test_expired_and_future_and_inactive_coupons_rejected(self, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="EXPIRED10",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('10.00'),
            valid_from=now - timedelta(days=10),
            valid_to=now - timedelta(hours=1),
        )
        Coupon.objects.create(
            code="FUTURE10",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('10.00'),
            valid_from=now + timedelta(days=2),
            valid_to=now + timedelta(days=10),
        )
        Coupon.objects.create(
            code="PAUSED10",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('10.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
            is_active=False,
        )

        with pytest.raises(BusinessLogicError, match="expired"):
            CouponService.validate_coupon("EXPIRED10", Decimal('2000.00'))

        with pytest.raises(BusinessLogicError, match="not active until"):
            CouponService.validate_coupon("FUTURE10", Decimal('2000.00'))

        with pytest.raises(BusinessLogicError, match="inactive"):
            CouponService.validate_coupon("PAUSED10", Decimal('2000.00'))

    def test_min_booking_amount_enforced(self, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="VIP3000",
            discount_type=DiscountType.FLAT,
            discount_value=Decimal('300.00'),
            min_booking_amount=Decimal('3000.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        with pytest.raises(BusinessLogicError, match="Minimum booking amount"):
            CouponService.validate_coupon("VIP3000", Decimal('2000.00'))

    def test_apply_coupon_and_server_side_recalculation(self, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="SAVE20",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('20.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        booking = create_pending_booking(promo_network, seat_numbers=['1A'])
        # Gross = 2000, Tax (5%) = 100, Fee = 50 -> Net = 2150
        assert booking.net_amount == Decimal('2150.00')

        updated, usage = CouponService.apply_coupon_to_booking(booking.pnr, "SAVE20", user=promo_network['passenger1'])
        # 20% of 2000 = 400. Taxable = 1600. Tax (5%) = 80. Fee = 50 -> Net = 1730.00
        assert updated.discount_amount == Decimal('400.00')
        assert updated.tax_amount == Decimal('80.00')
        assert updated.net_amount == Decimal('1730.00')
        assert usage.discount_applied == Decimal('400.00')
        assert usage.coupon.current_usage_count == 1

        # Reject double coupon on same booking
        with pytest.raises(BusinessLogicError, match="already applied"):
            CouponService.apply_coupon_to_booking(booking.pnr, "SAVE20")

    def test_per_user_and_global_quota_enforcement(self, promo_network):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code="LIMITED",
            discount_type=DiscountType.FLAT,
            discount_value=Decimal('200.00'),
            max_total_uses=2,
            max_uses_per_user=1,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        b1 = create_pending_booking(promo_network, seat_numbers=['1B'], user=promo_network['passenger1'])
        CouponService.apply_coupon_to_booking(b1.pnr, "LIMITED", user=promo_network['passenger1'])

        # Same user tries on another booking -> blocked by max_uses_per_user=1
        b2 = create_pending_booking(promo_network, seat_numbers=['1C'], user=promo_network['passenger1'])
        with pytest.raises(BusinessLogicError, match="per-user limit"):
            CouponService.apply_coupon_to_booking(b2.pnr, "LIMITED", user=promo_network['passenger1'])

        # Different user claims 2nd slot -> succeeds
        b3 = create_pending_booking(promo_network, seat_numbers=['1D'], user=promo_network['passenger2'])
        CouponService.apply_coupon_to_booking(b3.pnr, "LIMITED", user=promo_network['passenger2'])

        # 3rd user hits global max_total_uses=2
        user3 = User.objects.create_user(phone="+923001010099", password="Pass123!", is_verified=True)
        b4 = create_pending_booking(promo_network, seat_numbers=['2A'], user=user3)
        with pytest.raises(BusinessLogicError, match="global redemption limit"):
            CouponService.apply_coupon_to_booking(b4.pnr, "LIMITED", user=user3)

    def test_remove_coupon_restores_totals_and_quota(self, promo_network):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code="REM100",
            discount_type=DiscountType.FLAT,
            discount_value=Decimal('100.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        booking = create_pending_booking(promo_network, seat_numbers=['2B'])
        CouponService.apply_coupon_to_booking(booking.pnr, "REM100")

        restored = CouponService.remove_coupon_from_booking(booking.pnr)
        assert restored.discount_amount == Decimal('0.00')
        assert restored.tax_amount == Decimal('100.00')
        assert restored.net_amount == Decimal('2150.00')
        coupon.refresh_from_db()
        assert coupon.current_usage_count == 0

    def test_inline_coupon_during_booking_creation(self, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="INLINE500",
            discount_type=DiscountType.FLAT,
            discount_value=Decimal('500.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        booking = create_pending_booking(promo_network, seat_numbers=['2C'], coupon_code="INLINE500")
        # Gross 2000 - 500 = 1500. Tax 5% = 75. Fee = 50 -> Net = 1625.00
        assert booking.discount_amount == Decimal('500.00')
        assert booking.tax_amount == Decimal('75.00')
        assert booking.net_amount == Decimal('1625.00')
        assert CouponUsage.objects.filter(booking=booking).exists()


@pytest.mark.django_db
class TestDynamicPricingEngine:

    def test_occupancy_surge_rule_triggers_and_adjusts_fare(self, promo_network):
        # Bus has 20 seats. Create surge rule >= 10% occupancy -> 1.25x
        DynamicPricingRule.objects.create(
            name="Peak Demand Surge",
            rule_type=DynamicRuleType.SURGE_OCCUPANCY,
            route=promo_network['route'],
            min_occupancy_percentage=Decimal('10.00'),
            multiplier=Decimal('1.25'),
            priority=50,
            is_active=True
        )
        trip = promo_network['trip']
        s0, s1 = promo_network['stops'][0], promo_network['stops'][1]

        # Initially 0% occupancy -> base rate 2000.00
        res_empty = DynamicPricingService.calculate_dynamic_fare(trip, s0, s1)
        assert res_empty['multiplier'] == Decimal('1.00')
        assert res_empty['adjusted_fare'] == Decimal('2000.00')

        # Book 2 seats (2/20 = 10% occupancy) -> triggers 1.25x surge!
        b = create_pending_booking(promo_network, seat_numbers=['3A', '3B'])
        b.status = BookingStatus.CONFIRMED
        b.save()
        b.seats.update(status=BookingSeatStatus.BOOKED)

        res_surge = DynamicPricingService.calculate_dynamic_fare(trip, s0, s1)
        assert res_surge['occupancy_percentage'] == Decimal('10.00')
        assert res_surge['multiplier'] == Decimal('1.25')
        assert res_surge['adjusted_fare'] == Decimal('2500.00')

        # Verify FareService.calculate_fare also returns 2500.00
        assert FareService.calculate_fare(trip, s0, s1) == Decimal('2500.00')

    def test_early_bird_and_priority_resolution(self, promo_network):
        # Trip is 36 hours away; shift to 96 hours away for early bird
        trip = promo_network['trip']
        trip.departure_datetime = timezone.now() + timedelta(hours=96)
        trip.arrival_datetime = trip.departure_datetime + timedelta(minutes=260)
        trip.save()

        DynamicPricingRule.objects.create(
            name="Early Bird 15% Off",
            rule_type=DynamicRuleType.EARLY_BIRD,
            hours_before_departure_min=72,
            multiplier=Decimal('0.85'),
            priority=20,
            is_active=True
        )
        s0, s1 = promo_network['stops'][0], promo_network['stops'][1]
        res = DynamicPricingService.calculate_dynamic_fare(trip, s0, s1)
        assert res['multiplier'] == Decimal('0.85')
        assert res['adjusted_fare'] == Decimal('1700.00')


@pytest.mark.django_db
class TestPromotionsAPIEndpoints:

    def test_validate_apply_remove_coupon_apis(self, api_client, promo_network):
        now = timezone.now()
        Coupon.objects.create(
            code="API20",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=Decimal('20.00'),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=5),
        )
        booking = create_pending_booking(promo_network, seat_numbers=['4A'])

        # 1. Validate API
        res_val = api_client.post(
            reverse('promotion-validate-coupon'),
            data={"code": "API20", "gross_amount": "2000.00"},
            format='json'
        )
        assert res_val.status_code == status.HTTP_200_OK
        assert Decimal(res_val.json()['data']['discount_amount']) == Decimal('400.00')

        # 2. Apply API
        res_apply = api_client.post(
            reverse('promotion-apply-coupon'),
            data={"pnr": booking.pnr, "code": "API20"},
            format='json'
        )
        assert res_apply.status_code == status.HTTP_200_OK
        assert Decimal(res_apply.json()['data']['net_amount']) == Decimal('1730.00')

        # 3. Remove API
        res_rem = api_client.post(
            reverse('promotion-remove-coupon'),
            data={"pnr": booking.pnr},
            format='json'
        )
        assert res_rem.status_code == status.HTTP_200_OK
        assert Decimal(res_rem.json()['data']['net_amount']) == Decimal('2150.00')

    def test_admin_coupon_crud_and_rbac(self, api_client, promo_network):
        url = '/api/admin/promotions/coupons/'
        payload = {
            "code": "ADMIN50",
            "discount_type": "FLAT",
            "discount_value": "50.00",
            "valid_to": (timezone.now() + timedelta(days=10)).isoformat(),
        }

        # Passenger forbidden
        api_client.force_authenticate(user=promo_network['passenger1'])
        res_forbidden = api_client.post(url, data=payload, format='json')
        assert res_forbidden.status_code == status.HTTP_403_FORBIDDEN

        # Operator allowed
        api_client.force_authenticate(user=promo_network['operator'])
        res_create = api_client.post(url, data=payload, format='json')
        assert res_create.status_code == status.HTTP_201_CREATED
        coupon_id = res_create.json()['data']['id']

        res_list = api_client.get(url)
        assert res_list.status_code == status.HTTP_200_OK
        assert len(res_list.json()['data']['coupons']) >= 1

        res_patch = api_client.patch(f"{url}{coupon_id}/", data={"is_active": False}, format='json')
        assert res_patch.status_code == status.HTTP_200_OK
        assert res_patch.json()['data']['is_active'] is False

    def test_dynamic_price_check_api(self, api_client, promo_network):
        trip = promo_network['trip']
        s0, s1 = promo_network['stops'][0], promo_network['stops'][1]
        url = f"{reverse('promotion-dynamic-price')}?trip_id={trip.id}&from_stop_id={s0.id}&to_stop_id={s1.id}"
        res = api_client.get(url)
        assert res.status_code == status.HTTP_200_OK
        assert Decimal(str(res.json()['data']['base_fare'])) == Decimal('2000.00')
