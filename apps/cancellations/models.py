"""
Database models for Module 9: Cancellation & Refund Engine.
Enforces multi-tier refund policies, non-refundable service fees,
partial seat releases, and operator-initiated bulk cancellations.
"""
import secrets
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone

from common.constants import RefundStatus
from common.models import BaseModel


def generate_refund_number() -> str:
    """
    Generates a unique audit-compliant refund reference number.
    Format: REF-YYYYMMDD-XXXXXX (e.g. REF-20261123-9F8C1D)
    """
    date_str = timezone.now().strftime("%Y%m%d")
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    random_suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"REF-{date_str}-{random_suffix}"


class CancellationPolicy(BaseModel):
    """
    Rule-based cancellation tier defining refund percentages and administrative fees
    based on the time remaining before trip departure.
    """
    name = models.CharField(
        max_length=120,
        help_text="Policy tier name (e.g. 'Standard >24 Hours', 'Moderate 6-24 Hours')"
    )
    min_hours_before_departure = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Minimum hours remaining before departure for this policy tier to trigger"
    )
    refund_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Percentage of gross ticket fare refunded (0.00 to 100.00)"
    )
    cancellation_fee_fixed = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Fixed administrative fee charged upon cancellation"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this cancellation policy tier is actively evaluated"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Detailed customer-facing explanation of policy terms"
    )

    class Meta:
        verbose_name = "Cancellation Policy"
        verbose_name_plural = "Cancellation Policies"
        ordering = ['-min_hours_before_departure']

    def __str__(self):
        return f"{self.name} (>= {self.min_hours_before_departure}h: {self.refund_percentage}%)"


class Refund(BaseModel):
    """
    Audit-compliant financial record of a full or partial cancellation refund.
    Tracks refunded fare, tax portions, retained cancellation fees, and gateway execution.
    """
    refund_number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        default=generate_refund_number,
        help_text="Unique audit refund identifier"
    )
    booking = models.ForeignKey(
        'bookings.Booking',
        on_delete=models.PROTECT,
        related_name='refunds',
        help_text="Booking being cancelled or partially refunded"
    )
    payment = models.ForeignKey(
        'payments.Payment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='refunds',
        help_text="Payment transaction refunded via gateway"
    )
    ticket = models.ForeignKey(
        'tickets.Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='refunds',
        help_text="Specific ticket if this refund is for an individual seat cancellation"
    )
    seats = models.ManyToManyField(
        'bookings.BookingSeat',
        blank=True,
        related_name='refunds',
        help_text="Seats affected by this refund event"
    )
    policy_applied = models.ForeignKey(
        CancellationPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='refunds',
        help_text="Cancellation policy tier evaluated for this refund"
    )
    gross_fare_refunded = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Base seat fare refunded to the passenger"
    )
    tax_refunded = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Government / sales tax portion refunded"
    )
    cancellation_fee_charged = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Administrative cancellation fee retained by operator"
    )
    net_refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Final net monetary amount credited back to passenger"
    )
    status = models.CharField(
        max_length=25,
        choices=RefundStatus.choices,
        default=RefundStatus.PROCESSED,
        db_index=True,
        help_text="Processing state of this refund"
    )
    is_operator_cancellation = models.BooleanField(
        default=False,
        help_text="Whether this refund resulted from an operator-initiated trip cancellation"
    )
    reason = models.TextField(
        blank=True,
        default='',
        help_text="Reason provided by passenger or operator"
    )
    processed_at = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when refund was calculated and issued"
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_refunds',
        help_text="Staff agent or customer who triggered the cancellation"
    )

    class Meta:
        verbose_name = "Refund Record"
        verbose_name_plural = "Refund Records"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['refund_number']),
            models.Index(fields=['booking', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Refund {self.refund_number} | {self.booking.pnr} | Net: PKR {self.net_refund_amount}"
