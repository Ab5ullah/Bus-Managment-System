"""
Database Models for User Accounts, Profiles, and OTP Verification.
"""
import uuid
import secrets
from datetime import timedelta
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone

from common.models import TimeStampedModel, UUIDModel
from common.constants import UserRole
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """
    Custom user model where 'phone' is the primary login identifier,
    with unique email support, roles, and automated lockout security.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, unique=True, db_index=True)
    email = models.EmailField(unique=True, null=True, blank=True, db_index=True)
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.PASSENGER,
        db_index=True
    )
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Brute-force security / Account Lockout
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.phone} ({self.get_role_display()})"

    @property
    def is_locked(self) -> bool:
        """Check if account is currently locked due to failed login attempts."""
        if self.locked_until and self.locked_until > timezone.now():
            return True
        return False

    def record_failed_login(self, lockout_minutes: int = 15, max_attempts: int = 5):
        """
        Increment failed login attempts counter.
        Lock account if attempts reach max_attempts.
        """
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = timezone.now() + timedelta(minutes=lockout_minutes)
        self.save(update_fields=['failed_login_attempts', 'locked_until'])

    def reset_failed_logins(self):
        """Reset failed login counter and unlock account upon successful login."""
        if self.failed_login_attempts > 0 or self.locked_until is not None:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=['failed_login_attempts', 'locked_until'])


class OTPPurpose(models.TextChoices):
    REGISTRATION = 'REGISTRATION', 'Registration Verification'
    LOGIN = 'LOGIN', 'Login OTP'
    PASSWORD_RESET = 'PASSWORD_RESET', 'Password Reset'


class OTPVerification(UUIDModel, TimeStampedModel):
    """
    Stores cryptographically hashed one-time passwords with expiry.
    """
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='otps'
    )
    identifier = models.CharField(max_length=150, db_index=True)
    otp_hash = models.CharField(max_length=128)
    purpose = models.CharField(
        max_length=30,
        choices=OTPPurpose.choices,
        default=OTPPurpose.REGISTRATION
    )
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'OTP Verification'
        verbose_name_plural = 'OTP Verifications'
        ordering = ['-created_at']

    @classmethod
    def generate_otp(cls, identifier: str, purpose: str, user=None, validity_minutes: int = 5) -> tuple:
        """
        Generate a secure 6-digit random code, store hashed OTP, and return (otp_instance, plain_code).
        """
        plain_code = str(secrets.randbelow(900000) + 100000)  # Generates 100000 - 999999
        expires_at = timezone.now() + timedelta(minutes=validity_minutes)

        # Invalidate previous unused OTPs for the same identifier & purpose
        cls.objects.filter(
            identifier=identifier,
            purpose=purpose,
            is_used=False
        ).update(is_used=True)

        instance = cls.objects.create(
            user=user,
            identifier=identifier,
            otp_hash=make_password(plain_code),
            purpose=purpose,
            expires_at=expires_at
        )
        return instance, plain_code

    def verify(self, code: str) -> bool:
        """Verify code against hash and check expiration."""
        if self.is_used:
            return False
        if timezone.now() > self.expires_at:
            return False
        if check_password(code, self.otp_hash):
            self.is_used = True
            self.save(update_fields=['is_used'])
            return True
        return False


class Gender(models.TextChoices):
    MALE = 'MALE', 'Male'
    FEMALE = 'FEMALE', 'Female'
    OTHER = 'OTHER', 'Other'


class PassengerProfile(UUIDModel, TimeStampedModel):
    """
    Extended profile for passengers holding personal identification details.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    full_name = models.CharField(max_length=150)
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        default=Gender.MALE
    )
    dob = models.DateField(null=True, blank=True)
    cnic_id_number = models.CharField(max_length=30, blank=True, null=True)
    emergency_contact = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        verbose_name = 'Passenger Profile'
        verbose_name_plural = 'Passenger Profiles'

    def __str__(self):
        return f"{self.full_name} ({self.user.phone})"


class SavedPassenger(UUIDModel, TimeStampedModel):
    """
    Saved companion details for fast repeat booking by passengers.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='saved_passengers'
    )
    full_name = models.CharField(max_length=150)
    gender = models.CharField(max_length=10, choices=Gender.choices)
    age = models.PositiveSmallIntegerField()
    cnic_id_number = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        verbose_name = 'Saved Passenger'
        verbose_name_plural = 'Saved Passengers'
        ordering = ['full_name']

    def __str__(self):
        return f"{self.full_name} ({self.gender}, {self.age})"
