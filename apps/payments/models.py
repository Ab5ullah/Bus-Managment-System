"""
Database models for Module 7: Payments, Webhooks & Financial Reconciliation.
Enforces strict monetary precision, idempotent payment processing, and audit receipts.
"""
import uuid
import secrets
from decimal import Decimal
from django.db import models
from django.utils import timezone
from common.models import BaseModel
from common.constants import PaymentMethod, PaymentStatus


def generate_receipt_number() -> str:
    """
    Generates a unique, audit-compliant financial receipt number.
    Format: REC-YYYYMMDD-XXXXXX (e.g. REC-20261123-A8F19C)
    """
    date_str = timezone.now().strftime("%Y%m%d")
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    random_suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"REC-{date_str}-{random_suffix}"


class Payment(BaseModel):
    """
    Record of a financial transaction for a booking.
    Tracks payment gateway lifecycle, idempotency key, raw gateway payloads,
    and refund states.
    """
    booking = models.ForeignKey(
        'bookings.Booking',
        on_delete=models.PROTECT,
        related_name='payments',
        help_text="Booking associated with this payment intent"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Authoritative transaction amount in the specified currency"
    )
    currency = models.CharField(
        max_length=10,
        default='PKR',
        help_text="ISO currency code (e.g. PKR, USD)"
    )
    method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARD_ONLINE,
        help_text="Payment method or channel"
    )
    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED,
        db_index=True,
        help_text="Lifecycle status of this payment attempt"
    )
    gateway_name = models.CharField(
        max_length=50,
        default='mock',
        help_text="Identifier for the gateway provider (e.g. mock, stripe, easypaisa, jazzcash, cash)"
    )
    gateway_reference = models.CharField(
        max_length=120,
        blank=True,
        default='',
        db_index=True,
        help_text="External transaction reference ID returned by the payment gateway"
    )
    idempotency_key = models.CharField(
        max_length=120,
        unique=True,
        db_index=True,
        help_text="Client or gateway idempotency key preventing duplicate charges"
    )
    gateway_response_raw = models.JSONField(
        default=dict,
        blank=True,
        help_text="Verbatim JSON response payload captured from the gateway/webhook"
    )
    failure_reason = models.TextField(
        blank=True,
        default='',
        help_text="Detailed error message if the payment was rejected or failed"
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when payment was successfully captured"
    )

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['booking', 'status']),
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['gateway_name', 'gateway_reference']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Payment {self.id} | {self.booking.pnr} | {self.amount} {self.currency} [{self.status}]"

    @property
    def is_successful(self) -> bool:
        return self.status == PaymentStatus.SUCCESS

    @property
    def is_pending(self) -> bool:
        return self.status == PaymentStatus.INITIATED


class PaymentReceipt(BaseModel):
    """
    Immutable financial receipt issued upon successful payment capture.
    Provides complete financial audit trail (gross, tax, service fee, discounts, net).
    """
    payment = models.OneToOneField(
        Payment,
        on_delete=models.CASCADE,
        related_name='receipt',
        help_text="Associated successful payment record"
    )
    receipt_number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        default=generate_receipt_number,
        help_text="Unique audit-compliant receipt identifier"
    )
    gross_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Gross ticket fare total before taxes and fees"
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total discount applied"
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total tax captured"
    )
    service_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Platform or counter service fee retained"
    )
    net_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total net amount paid by customer"
    )
    issued_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when receipt was issued"
    )

    class Meta:
        verbose_name = "Payment Receipt"
        verbose_name_plural = "Payment Receipts"
        ordering = ['-issued_at']

    def __str__(self):
        return f"Receipt {self.receipt_number} | {self.net_amount} {self.payment.currency}"
