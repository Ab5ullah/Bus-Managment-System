"""
Database models for Module 10: Promotions, Coupons & Dynamic Pricing.
Supports server-side coupon validation, usage quotas, and yield/dynamic pricing rules.
"""
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.constants import DiscountType, DynamicRuleType
from common.models import BaseModel


class Coupon(BaseModel):
    """
    Promotional discount code with temporal validity, minimum order requirements,
    maximum discount caps, and global/per-user usage limits.
    """
    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique uppercase promo code (e.g. EID20, WELCOME500)"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Internal or customer-facing campaign description"
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        default=DiscountType.PERCENTAGE,
        help_text="PERCENTAGE or FLAT fixed currency reduction"
    )
    discount_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Percentage (e.g. 15.00) or flat amount (e.g. 500.00)"
    )
    min_booking_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Minimum gross fare required to redeem this coupon"
    )
    max_discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Maximum discount cap in PKR for PERCENTAGE coupons (null = uncapped)"
    )
    valid_from = models.DateTimeField(
        default=timezone.now,
        help_text="Start timestamp when coupon becomes active"
    )
    valid_to = models.DateTimeField(
        help_text="Expiration timestamp after which coupon cannot be redeemed"
    )
    max_total_uses = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Global redemption cap across all passengers (null = unlimited)"
    )
    max_uses_per_user = models.PositiveIntegerField(
        default=1,
        help_text="Maximum times a single user/phone can apply this coupon"
    )
    current_usage_count = models.PositiveIntegerField(
        default=0,
        help_text="Atomic counter of successful redemptions"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Administrative kill-switch to immediately pause a campaign"
    )

    class Meta:
        verbose_name = "Promotional Coupon"
        verbose_name_plural = "Promotional Coupons"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['code', 'is_active']),
            models.Index(fields=['valid_from', 'valid_to']),
        ]

    def clean(self):
        super().clean()
        if self.discount_value <= Decimal('0.00'):
            raise ValidationError("Discount value must be greater than 0.00.")
        if self.discount_type == DiscountType.PERCENTAGE and self.discount_value > Decimal('100.00'):
            raise ValidationError("Percentage discount cannot exceed 100.00%.")
        if self.valid_to and self.valid_from and self.valid_to <= self.valid_from:
            raise ValidationError("valid_to must be strictly after valid_from.")

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        unit = "%" if self.discount_type == DiscountType.PERCENTAGE else "PKR"
        return f"{self.code} ({self.discount_value}{unit})"


class CouponUsage(BaseModel):
    """
    Audit log of coupon redemptions linked to specific bookings and users.
    """
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.PROTECT,
        related_name='usages',
        help_text="Redeemed coupon"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coupon_usages',
        help_text="Registered user who applied the coupon (if logged in)"
    )
    passenger_phone = models.CharField(
        max_length=30,
        blank=True,
        default='',
        db_index=True,
        help_text="Contact phone used for per-user limit tracking on guest bookings"
    )
    booking = models.OneToOneField(
        'bookings.Booking',
        on_delete=models.CASCADE,
        related_name='coupon_usage',
        help_text="Booking on which this coupon was applied"
    )
    discount_applied = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Exact monetary discount deducted from gross fare"
    )
    used_at = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when coupon was redeemed"
    )

    class Meta:
        verbose_name = "Coupon Usage"
        verbose_name_plural = "Coupon Usages"
        ordering = ['-used_at']
        indexes = [
            models.Index(fields=['coupon', 'user']),
            models.Index(fields=['coupon', 'passenger_phone']),
        ]

    def __str__(self):
        return f"{self.coupon.code} on {self.booking.pnr} (-PKR {self.discount_applied})"


class DynamicPricingRule(BaseModel):
    """
    Yield management rule adjusting segment ticket fares dynamically based on
    trip occupancy percentage, booking lead time, or weekend schedule.
    """
    name = models.CharField(
        max_length=120,
        help_text="Descriptive rule name (e.g. 'Peak 80% Surge', 'Early Bird 72h+')"
    )
    rule_type = models.CharField(
        max_length=30,
        choices=DynamicRuleType.choices,
        default=DynamicRuleType.SURGE_OCCUPANCY,
        db_index=True
    )
    route = models.ForeignKey(
        'routes.Route',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='dynamic_pricing_rules',
        help_text="Specific route scope; null applies globally to all routes"
    )
    min_occupancy_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Triggers SURGE_OCCUPANCY when booked seats >= this % of bus capacity"
    )
    hours_before_departure_min = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Minimum hours before departure (used for EARLY_BIRD)"
    )
    hours_before_departure_max = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum hours before departure (used for LAST_MINUTE)"
    )
    multiplier = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('1.00'),
        help_text="Fare multiplier (e.g. 1.25 = +25% surge, 0.85 = 15% discount)"
    )
    priority = models.PositiveIntegerField(
        default=10,
        db_index=True,
        help_text="Higher priority rules take precedence when multiple conditions match"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True
    )

    class Meta:
        verbose_name = "Dynamic Pricing Rule"
        verbose_name_plural = "Dynamic Pricing Rules"
        ordering = ['-priority', '-created_at']

    def clean(self):
        super().clean()
        if self.multiplier <= Decimal('0.00'):
            raise ValidationError("Pricing multiplier must be strictly positive.")

    def __str__(self):
        scope = self.route.code if self.route else "GLOBAL"
        return f"[{scope}] {self.name} (x{self.multiplier}, prio {self.priority})"
