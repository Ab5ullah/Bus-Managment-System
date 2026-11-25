"""
Domain Service Layer for Module 10: Promotions, Coupons & Dynamic Pricing.
Provides atomic, anti-tamper coupon redemption with quota enforcement,
and yield management dynamic pricing rules.
"""
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    DiscountType,
    DynamicRuleType,
)
from common.exceptions import BusinessLogicError, ResourceNotFoundError
from common.money import round_currency, calculate_booking_financials
from bookings.models import Booking, BookingSeat
from trips.models import Trip
from routes.models import RouteStop
from .models import Coupon, CouponUsage, DynamicPricingRule


class CouponService:
    """
    Authoritative Server-Side Coupon Engine.
    Never trusts client-supplied discounts; evaluates and locks coupon quotas atomically.
    """

    @classmethod
    def _compute_discount(cls, coupon: Coupon, gross_amount: Decimal) -> Decimal:
        """Calculates exact discount bounded by max_discount_amount and gross_amount."""
        gross_amount = round_currency(gross_amount)
        if coupon.discount_type == DiscountType.PERCENTAGE:
            raw_discount = round_currency(gross_amount * (coupon.discount_value / Decimal('100.00')))
            if coupon.max_discount_amount is not None and coupon.max_discount_amount > Decimal('0.00'):
                discount = min(raw_discount, round_currency(coupon.max_discount_amount))
            else:
                discount = raw_discount
        else:
            discount = round_currency(coupon.discount_value)

        return min(discount, gross_amount)

    @classmethod
    def validate_coupon(
        cls,
        code: str,
        gross_amount: Decimal,
        user=None,
        passenger_phone: str = '',
        lock_coupon: bool = False
    ) -> Tuple[Coupon, Decimal]:
        """
        Validates coupon eligibility, active window, minimum fare,
        global redemption quota, and per-user redemption limit.
        Returns (Coupon, computed_discount_amount).
        """
        clean_code = (code or '').strip().upper()
        if not clean_code:
            raise BusinessLogicError("Coupon code cannot be empty.")

        qs = Coupon.objects.select_for_update() if lock_coupon else Coupon.objects.all()
        coupon = qs.filter(code=clean_code).first()
        if not coupon:
            raise ResourceNotFoundError(f"Coupon code '{clean_code}' does not exist.")

        if not coupon.is_active:
            raise BusinessLogicError(f"Coupon '{clean_code}' is currently inactive.")

        now = timezone.now()
        if now < coupon.valid_from:
            raise BusinessLogicError(f"Coupon '{clean_code}' is not active until {coupon.valid_from:%Y-%m-%d %H:%M}.")
        if now > coupon.valid_to:
            raise BusinessLogicError(f"Coupon '{clean_code}' has expired.")

        gross_amount = round_currency(gross_amount)
        if gross_amount < coupon.min_booking_amount:
            raise BusinessLogicError(
                f"Minimum booking amount of PKR {coupon.min_booking_amount} is required to use '{clean_code}'."
            )

        if coupon.max_total_uses is not None and coupon.current_usage_count >= coupon.max_total_uses:
            raise BusinessLogicError(f"Coupon '{clean_code}' has reached its maximum global redemption limit.")

        # Per-user / per-phone limit check
        if coupon.max_uses_per_user is not None and coupon.max_uses_per_user > 0:
            if user is not None and getattr(user, 'is_authenticated', True) and getattr(user, 'id', None):
                user_uses = CouponUsage.objects.filter(coupon=coupon, user=user).count()
            elif passenger_phone:
                user_uses = CouponUsage.objects.filter(coupon=coupon, passenger_phone=passenger_phone.strip()).count()
            else:
                user_uses = 0

            if user_uses >= coupon.max_uses_per_user:
                raise BusinessLogicError(
                    f"You have already reached the maximum per-user limit ({coupon.max_uses_per_user}) for coupon '{clean_code}'."
                )

        discount = cls._compute_discount(coupon, gross_amount)
        return coupon, discount

    @classmethod
    @transaction.atomic
    def apply_coupon_to_booking(
        cls,
        pnr: str,
        code: str,
        user=None
    ) -> Tuple[Booking, CouponUsage]:
        """
        Atomically applies a promotional coupon to a PENDING booking,
        recalculates tax and net payable on the server, and increments coupon usage.
        """
        clean_pnr = (pnr or '').strip().upper()
        booking = Booking.objects.select_for_update().filter(pnr=clean_pnr).first()
        if not booking:
            raise ResourceNotFoundError(f"Booking with PNR '{clean_pnr}' not found.")

        if booking.status != BookingStatus.PENDING:
            raise BusinessLogicError(
                f"Coupons can only be applied to PENDING bookings (current status: {booking.status})."
            )

        if CouponUsage.objects.filter(booking=booking).exists():
            raise BusinessLogicError(
                "A coupon is already applied to this booking. Remove the existing coupon first."
            )

        effective_user = user if (user and getattr(user, 'is_authenticated', False)) else booking.user
        coupon, discount = cls.validate_coupon(
            code=code,
            gross_amount=booking.total_gross_fare,
            user=effective_user,
            passenger_phone=booking.passenger_phone,
            lock_coupon=True
        )

        # Authoritative financial recalculation
        financials = calculate_booking_financials(
            gross_fare=booking.total_gross_fare,
            discount_amount=discount,
            service_fee_fixed=booking.service_fee
        )

        booking.discount_amount = financials['discount_amount']
        booking.tax_amount = financials['tax_amount']
        booking.net_amount = financials['net_payable']
        booking.save(update_fields=['discount_amount', 'tax_amount', 'net_amount', 'updated_at'])

        coupon.current_usage_count += 1
        coupon.save(update_fields=['current_usage_count', 'updated_at'])

        usage = CouponUsage.objects.create(
            coupon=coupon,
            user=effective_user,
            passenger_phone=booking.passenger_phone,
            booking=booking,
            discount_applied=discount
        )

        return booking, usage

    @classmethod
    @transaction.atomic
    def remove_coupon_from_booking(cls, pnr: str) -> Booking:
        """
        Removes an applied coupon from a PENDING booking and restores original totals.
        """
        clean_pnr = (pnr or '').strip().upper()
        booking = Booking.objects.select_for_update().filter(pnr=clean_pnr).first()
        if not booking:
            raise ResourceNotFoundError(f"Booking with PNR '{clean_pnr}' not found.")

        if booking.status != BookingStatus.PENDING:
            raise BusinessLogicError(
                f"Cannot remove coupon from booking in '{booking.status}' status."
            )

        usage = CouponUsage.objects.select_for_update().filter(booking=booking).first()
        if not usage:
            raise BusinessLogicError(f"No coupon is currently applied to booking '{clean_pnr}'.")

        coupon = Coupon.objects.select_for_update().get(id=usage.coupon_id)
        if coupon.current_usage_count > 0:
            coupon.current_usage_count -= 1
            coupon.save(update_fields=['current_usage_count', 'updated_at'])

        usage.delete()

        financials = calculate_booking_financials(
            gross_fare=booking.total_gross_fare,
            discount_amount=Decimal('0.00'),
            service_fee_fixed=booking.service_fee
        )
        booking.discount_amount = Decimal('0.00')
        booking.tax_amount = financials['tax_amount']
        booking.net_amount = financials['net_payable']
        booking.save(update_fields=['discount_amount', 'tax_amount', 'net_amount', 'updated_at'])

        return booking


class DynamicPricingService:
    """
    Yield Management Engine evaluating live occupancy, booking lead time,
    and schedule conditions to calculate dynamic fare multipliers.
    """

    @classmethod
    def get_trip_occupancy_percentage(cls, trip: Trip) -> Decimal:
        """Returns current percentage (0.00 - 100.00) of booked/locked seats on trip."""
        total_seats = trip.bus.total_seats if (trip.bus and trip.bus.total_seats > 0) else 0
        if total_seats == 0:
            return Decimal('0.00')

        active_seat_count = BookingSeat.objects.filter(
            trip=trip,
            status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.LOCKED]
        ).values('seat_id').distinct().count()

        return round_currency((Decimal(active_seat_count) / Decimal(total_seats)) * Decimal('100.00'))

    @classmethod
    def evaluate_trip_multiplier(cls, trip: Trip) -> Dict[str, Any]:
        """
        Evaluates all active DynamicPricingRule entries matching this trip's route
        (or global rules) in descending priority order.
        Returns the highest-priority matching rule and its multiplier.
        """
        rules = DynamicPricingRule.objects.filter(
            is_active=True
        ).filter(
            Q(route=trip.route) | Q(route__isnull=True)
        ).order_by('-priority', '-created_at')

        occupancy_pct = cls.get_trip_occupancy_percentage(trip)
        now = timezone.now()
        hours_to_departure = (trip.departure_datetime - now).total_seconds() / 3600.0

        matched_rule = None
        for rule in rules:
            if rule.rule_type == DynamicRuleType.SURGE_OCCUPANCY:
                if occupancy_pct >= rule.min_occupancy_percentage:
                    matched_rule = rule
                    break
            elif rule.rule_type == DynamicRuleType.EARLY_BIRD:
                if rule.hours_before_departure_min is not None and hours_to_departure >= rule.hours_before_departure_min:
                    matched_rule = rule
                    break
            elif rule.rule_type == DynamicRuleType.LAST_MINUTE:
                if rule.hours_before_departure_max is not None and 0 < hours_to_departure <= rule.hours_before_departure_max:
                    matched_rule = rule
                    break
            elif rule.rule_type == DynamicRuleType.WEEKEND:
                if trip.departure_datetime.weekday() in (4, 5, 6):  # Fri, Sat, Sun
                    matched_rule = rule
                    break

        multiplier = matched_rule.multiplier if matched_rule else Decimal('1.00')
        return {
            "multiplier": multiplier,
            "matched_rule": matched_rule,
            "rule_name": matched_rule.name if matched_rule else "Standard Base Rate",
            "rule_type": matched_rule.rule_type if matched_rule else None,
            "occupancy_percentage": occupancy_pct,
            "hours_to_departure": round(hours_to_departure, 2),
        }

    @classmethod
    def calculate_dynamic_fare(
        cls,
        trip: Trip,
        from_stop: RouteStop,
        to_stop: RouteStop,
        seat_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Computes base fare via FareService and applies the active dynamic multiplier.
        """
        from trips.services import FareService
        base_fare = FareService.calculate_base_fare(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            seat_type=seat_type
        )
        eval_res = cls.evaluate_trip_multiplier(trip)
        adjusted_fare = round_currency(base_fare * eval_res['multiplier'])

        return {
            "base_fare": base_fare,
            "multiplier": eval_res['multiplier'],
            "adjusted_fare": adjusted_fare,
            "rule_name": eval_res['rule_name'],
            "rule_type": eval_res['rule_type'],
            "occupancy_percentage": eval_res['occupancy_percentage'],
            "hours_to_departure": eval_res['hours_to_departure'],
        }
