"""
Financial and monetary utilities: Decimal precision, ROUND_HALF_UP rounding,
tax/fee computation, and prorated refund accounting.
"""
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings

CURRENCY_QUANTIZATION = Decimal('0.01')


def round_currency(value: Decimal) -> Decimal:
    """
    Standard banking round to 2 decimal places using ROUND_HALF_UP.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CURRENCY_QUANTIZATION, rounding=ROUND_HALF_UP)


def calculate_booking_financials(
    gross_fare: Decimal,
    discount_amount: Decimal = Decimal('0.00'),
    tax_rate_percentage: Decimal = None,
    service_fee_fixed: Decimal = None
) -> dict:
    """
    Authoritative server-side financial calculation for bookings.

    Formula:
    1. Gross Fare = sum of seat fares
    2. Discount = capped at Gross Fare
    3. Taxable Amount = Gross Fare - Discount
    4. Tax Amount = round(Taxable Amount * (tax_rate / 100))
    5. Service Fee = configured fixed fee
    6. Net Total Payable = Taxable Amount + Tax Amount + Service Fee
    """
    gross_fare = round_currency(gross_fare)
    discount_amount = round_currency(discount_amount)

    # Discount cannot exceed gross fare
    if discount_amount > gross_fare:
        discount_amount = gross_fare

    taxable_amount = gross_fare - discount_amount

    if tax_rate_percentage is None:
        tax_rate_percentage = getattr(settings, 'TAX_RATE_PERCENTAGE', Decimal('5.00'))
    else:
        tax_rate_percentage = Decimal(str(tax_rate_percentage))

    if service_fee_fixed is None:
        service_fee_fixed = getattr(settings, 'SERVICE_FEE_FIXED', Decimal('50.00'))
    else:
        service_fee_fixed = Decimal(str(service_fee_fixed))

    tax_amount = round_currency(taxable_amount * (tax_rate_percentage / Decimal('100.00')))
    service_fee = round_currency(service_fee_fixed)
    net_payable = taxable_amount + tax_amount + service_fee

    return {
        "gross_fare": gross_fare,
        "discount_amount": discount_amount,
        "taxable_amount": taxable_amount,
        "tax_rate_percentage": tax_rate_percentage,
        "tax_amount": tax_amount,
        "service_fee": service_fee,
        "net_payable": net_payable,
    }


def calculate_seat_refund(
    seat_price: Decimal,
    total_gross_fare: Decimal,
    total_discount: Decimal,
    refund_percentage: Decimal,
    tax_rate_percentage: Decimal = None
) -> dict:
    """
    Accurately calculate refund for an individual seat cancellation.

    Accounting Rules:
    - Discounts are distributed proportionally across seats based on seat price weight.
    - Effective fare paid for this seat = seat_price - prorated_discount.
    - Effective tax paid for this seat = round(seat_effective_fare * (tax_rate / 100)).
    - Refund is applied to (seat_effective_fare + seat_tax).
    - Service fees are strictly non-refundable.

    Returns:
    {
        "seat_effective_fare": Decimal,
        "seat_tax": Decimal,
        "refundable_base": Decimal,
        "refund_percentage": Decimal,
        "gross_refund": Decimal,
        "cancellation_fee": Decimal,
        "net_refund_amount": Decimal
    }
    """
    seat_price = round_currency(seat_price)
    total_gross_fare = round_currency(total_gross_fare)
    total_discount = round_currency(total_discount)
    refund_percentage = Decimal(str(refund_percentage))

    if tax_rate_percentage is None:
        tax_rate_percentage = getattr(settings, 'TAX_RATE_PERCENTAGE', Decimal('5.00'))
    else:
        tax_rate_percentage = Decimal(str(tax_rate_percentage))

    # Calculate prorated discount for this seat
    if total_gross_fare > Decimal('0.00'):
        prorated_discount = round_currency(total_discount * (seat_price / total_gross_fare))
    else:
        prorated_discount = Decimal('0.00')

    seat_effective_fare = max(Decimal('0.00'), seat_price - prorated_discount)
    seat_tax = round_currency(seat_effective_fare * (tax_rate_percentage / Decimal('100.00')))
    refundable_base = seat_effective_fare + seat_tax

    # Calculate refund and cancellation fee
    refund_factor = refund_percentage / Decimal('100.00')
    net_refund_amount = round_currency(refundable_base * refund_factor)
    cancellation_fee = refundable_base - net_refund_amount

    return {
        "seat_effective_fare": seat_effective_fare,
        "seat_tax": seat_tax,
        "refundable_base": refundable_base,
        "refund_percentage": refund_percentage,
        "cancellation_fee": cancellation_fee,
        "net_refund_amount": net_refund_amount
    }
