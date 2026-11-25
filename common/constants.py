"""
Global enums and status constants for the Bus Ticket Management System.
"""
from django.db import models


class UserRole(models.TextChoices):
    PASSENGER = 'PASSENGER', 'Passenger'
    COUNTER_AGENT = 'COUNTER_AGENT', 'Counter Agent'
    OPERATOR = 'OPERATOR', 'Operator / Admin'
    CONDUCTOR = 'CONDUCTOR', 'Conductor / Driver'
    SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'


class Gender(models.TextChoices):
    MALE = 'MALE', 'Male'
    FEMALE = 'FEMALE', 'Female'
    OTHER = 'OTHER', 'Other'


class BookingStatus(models.TextChoices):
    """
    Unified booking lifecycle state machine:
    PENDING -> CONFIRMED -> COMPLETED
                         -> PARTIALLY_CANCELLED
                         -> CANCELLED
                         -> REFUNDED
                         -> NO_SHOW
            -> EXPIRED
    """
    PENDING = 'PENDING', 'Pending Payment'
    CONFIRMED = 'CONFIRMED', 'Confirmed'
    PARTIALLY_CANCELLED = 'PARTIALLY_CANCELLED', 'Partially Cancelled'
    CANCELLED = 'CANCELLED', 'Cancelled'
    COMPLETED = 'COMPLETED', 'Completed'
    REFUNDED = 'REFUNDED', 'Refunded'
    NO_SHOW = 'NO_SHOW', 'No Show'
    EXPIRED = 'EXPIRED', 'Expired'


class BookingSeatStatus(models.TextChoices):
    """
    Lifecycle status of an individual seat inside a booking.
    Directly indexed to avoid table joins on availability queries.
    Active states: LOCKED, BOOKED
    Inactive states: CANCELLED, COMPLETED, NO_SHOW
    """
    LOCKED = 'LOCKED', 'Locked'
    BOOKED = 'BOOKED', 'Booked'
    CANCELLED = 'CANCELLED', 'Cancelled'
    COMPLETED = 'COMPLETED', 'Completed'
    NO_SHOW = 'NO_SHOW', 'No Show'


class SeatType(models.TextChoices):
    WINDOW = 'WINDOW', 'Window'
    AISLE = 'AISLE', 'Aisle'
    SLEEPER_LOWER = 'SLEEPER_LOWER', 'Sleeper Lower'
    SLEEPER_UPPER = 'SLEEPER_UPPER', 'Sleeper Upper'


class BusType(models.TextChoices):
    STANDARD_AC = 'STANDARD_AC', 'Standard AC'
    EXECUTIVE = 'EXECUTIVE', 'Executive'
    SLEEPER = 'SLEEPER', 'Sleeper'
    LUXURY = 'LUXURY', 'Luxury'


class BusStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    MAINTENANCE = 'MAINTENANCE', 'Maintenance'
    RETIRED = 'RETIRED', 'Retired'


class TripStatus(models.TextChoices):
    SCHEDULED = 'SCHEDULED', 'Scheduled'
    BOARDING = 'BOARDING', 'Boarding'
    DEPARTED = 'DEPARTED', 'Departed'
    COMPLETED = 'COMPLETED', 'Completed'
    CANCELLED = 'CANCELLED', 'Cancelled'


class PaymentMethod(models.TextChoices):
    CASH_COUNTER = 'CASH_COUNTER', 'Cash at Counter'
    CARD_ONLINE = 'CARD_ONLINE', 'Card (Online Gateway)'
    WALLET = 'WALLET', 'Mobile Wallet'
    STRIPE = 'STRIPE', 'Stripe'


class PaymentStatus(models.TextChoices):
    INITIATED = 'INITIATED', 'Initiated'
    SUCCESS = 'SUCCESS', 'Success'
    FAILED = 'FAILED', 'Failed'
    REFUNDED = 'REFUNDED', 'Refunded'


class TicketStatus(models.TextChoices):
    ISSUED = 'ISSUED', 'Issued'
    BOARDED = 'BOARDED', 'Boarded'
    CANCELLED = 'CANCELLED', 'Cancelled'
    EXPIRED = 'EXPIRED', 'Expired'
    NO_SHOW = 'NO_SHOW', 'No Show'


class RefundStatus(models.TextChoices):
    REQUESTED = 'REQUESTED', 'Requested'
    PROCESSED = 'PROCESSED', 'Processed'
    FAILED = 'FAILED', 'Failed'


class DiscountType(models.TextChoices):
    PERCENTAGE = 'PERCENTAGE', 'Percentage (%)'
    FLAT = 'FLAT', 'Flat Amount (PKR)'


class DynamicRuleType(models.TextChoices):
    SURGE_OCCUPANCY = 'SURGE_OCCUPANCY', 'Occupancy Surge'
    EARLY_BIRD = 'EARLY_BIRD', 'Early Bird Discount'
    LAST_MINUTE = 'LAST_MINUTE', 'Last Minute Adjustment'
    WEEKEND = 'WEEKEND', 'Weekend Pricing'


