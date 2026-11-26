"""
Service Layer for Accounts & Authentication.
Encapsulates business operations, transactional boundaries, and domain rules.
"""
from datetime import datetime, timezone
import re
from django.db import transaction
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from common.constants import UserRole
from .models import User, PassengerProfile, OTPVerification, OTPPurpose, Gender


class RegistrationService:
    """Orchestrates new user registration, profile initialization, and OTP dispatch."""

    @classmethod
    @transaction.atomic
    def register_user(
        cls,
        phone: str,
        password: str,
        full_name: str,
        email: str = None,
        gender: str = Gender.MALE,
        role: str = UserRole.PASSENGER
    ) -> tuple[User, str]:
        user = User.objects.create_user(
            phone=phone,
            email=email if email else None,
            password=password,
            role=role,
            is_verified=False
        )

        PassengerProfile.objects.create(
            user=user,
            full_name=full_name,
            gender=gender
        )

        # Generate initial OTP for account verification
        otp_instance, plain_otp = OTPVerification.generate_otp(
            identifier=phone,
            purpose=OTPPurpose.REGISTRATION,
            user=user
        )

        return user, plain_otp


class OTPService:
    """Handles generation, expiration, and verification of one-time passwords."""

    @classmethod
    def verify_otp(cls, identifier: str, otp_code: str, purpose: str) -> OTPVerification:
        cleaned_id = identifier.strip()
        code = otp_code.strip()

        otp_record = OTPVerification.objects.filter(
            identifier=cleaned_id,
            purpose=purpose,
            is_used=False
        ).order_by('-created_at').first()

        if not otp_record:
            raise ValidationError({"otp_code": "No active OTP found for this identifier. Please request a new code."})

        if not otp_record.verify(code):
            raise ValidationError({"otp_code": "Invalid or expired OTP code."})

        # If registration OTP, activate user account
        if purpose == OTPPurpose.REGISTRATION and otp_record.user:
            otp_record.user.is_verified = True
            otp_record.user.save(update_fields=['is_verified'])

        return otp_record

    @classmethod
    @transaction.atomic
    def resend_otp(cls, identifier: str, purpose: str = OTPPurpose.REGISTRATION, cooldown_seconds: int = 60) -> tuple[User, str]:
        """Re-issue a fresh OTP for REGISTRATION or PASSWORD_RESET with cooldown protection."""
        from datetime import timedelta
        from django.utils import timezone as dj_tz
        raw_id = identifier.strip()
        cleaned_phone = re.sub(r'[\s\-]', '', raw_id)

        user = User.objects.filter(phone=cleaned_phone).first() or User.objects.filter(email=raw_id.lower()).first()
        if not user:
            raise ValidationError({"identifier": "No account found with this phone number or email."})

        if purpose == OTPPurpose.REGISTRATION and user.is_verified:
            raise ValidationError({"identifier": "This account is already verified."})

        target_identifier = user.phone if user.phone == cleaned_phone else (user.email or user.phone)

        recent = OTPVerification.objects.filter(
            identifier=target_identifier,
            purpose=purpose,
            is_used=False,
            created_at__gt=dj_tz.now() - timedelta(seconds=cooldown_seconds),
        ).order_by('-created_at').first()
        if recent:
            wait_sec = max(1, int(cooldown_seconds - (dj_tz.now() - recent.created_at).total_seconds()))
            raise ValidationError({"identifier": f"Please wait {wait_sec} seconds before requesting a new OTP."})

        # Invalidate previous unused OTPs for this identifier & purpose
        OTPVerification.objects.filter(
            identifier=target_identifier,
            purpose=purpose,
            is_used=False,
        ).update(is_used=True)

        _, plain_otp = OTPVerification.generate_otp(
            identifier=target_identifier,
            purpose=purpose,
            user=user,
        )
        return user, plain_otp


class AuthenticationService:
    """Manages credentials authentication, lockout security, and SimpleJWT lifecycle."""

    @classmethod
    def authenticate_and_generate_tokens(cls, identifier: str, password: str) -> tuple[User, dict]:
        identifier = identifier.strip()
        cleaned_phone = re.sub(r'[\s\-]', '', identifier)

        # Look up by phone or email
        user = User.objects.filter(phone=cleaned_phone).first()
        if not user:
            user = User.objects.filter(email=identifier.lower()).first()

        if not user:
            raise AuthenticationFailed("Invalid phone/email or password.")

        if not user.is_active:
            raise PermissionDenied("This account is inactive. Please contact support.")

        if user.is_locked:
            minutes_left = max(1, int((user.locked_until - datetime.now(timezone.utc)).total_seconds() // 60))
            raise PermissionDenied(
                f"Account is temporarily locked due to 5 consecutive failed login attempts. "
                f"Please try again in {minutes_left} minutes."
            )

        if not user.check_password(password):
            user.record_failed_login(lockout_minutes=15, max_attempts=5)
            if user.is_locked:
                raise PermissionDenied(
                    "Account locked due to 5 consecutive failed login attempts. Try again in 15 minutes."
                )
            remaining_attempts = max(0, 5 - user.failed_login_attempts)
            raise AuthenticationFailed(
                f"Invalid password. {remaining_attempts} attempts remaining before account lock."
            )

        # Successful authentication -> reset counters
        user.reset_failed_logins()

        # Generate SimpleJWT tokens with custom claims
        refresh = RefreshToken.for_user(user)
        refresh['role'] = user.role
        refresh['phone'] = user.phone

        tokens = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }
        return user, tokens

    @classmethod
    def blacklist_token(cls, refresh_token_str: str) -> None:
        try:
            token = RefreshToken(refresh_token_str)
            token.blacklist()
        except Exception:
            raise ValidationError({"refresh": "Token is invalid, expired, or already blacklisted."})


class PasswordResetService:
    """Manages password reset requests and verification."""

    @classmethod
    def request_password_reset(cls, identifier: str) -> str:
        identifier = identifier.strip()
        cleaned_phone = re.sub(r'[\s\-]', '', identifier)

        user = User.objects.filter(phone=cleaned_phone).first() or User.objects.filter(email=identifier.lower()).first()
        if not user:
            return None

        _, plain_otp = OTPVerification.generate_otp(
            identifier=identifier,
            purpose=OTPPurpose.PASSWORD_RESET,
            user=user
        )
        return plain_otp

    @classmethod
    @transaction.atomic
    def confirm_password_reset(cls, identifier: str, otp_code: str, new_password: str) -> User:
        identifier = identifier.strip()
        otp_record = OTPVerification.objects.filter(
            identifier=identifier,
            purpose=OTPPurpose.PASSWORD_RESET,
            is_used=False
        ).order_by('-created_at').first()

        if not otp_record or not otp_record.verify(otp_code):
            raise ValidationError({"otp_code": "Invalid or expired password reset OTP."})

        user = otp_record.user
        if not user:
            cleaned_phone = re.sub(r'[\s\-]', '', identifier)
            user = User.objects.filter(phone=cleaned_phone).first() or User.objects.filter(email=identifier.lower()).first()

        if not user:
            raise ValidationError("User account not found.")

        user.set_password(new_password)
        user.reset_failed_logins()
        user.save()
        return user


class AccountDeletionService:
    """
    Handles GDPR / App-Store compliant self-service account deletion:
    - Verifies password
    - Blocks deletion if active upcoming trips exist
    - Anonymizes PII and frees phone/email for future re-registration
    - Blacklists all active JWT tokens
    """

    @classmethod
    @transaction.atomic
    def delete_account(cls, user: User, password: str) -> None:
        if not user.check_password(password):
            raise ValidationError({"password": "Incorrect password. Account deletion aborted."})

        from django.apps import apps
        from django.utils import timezone as dj_tz
        if apps.is_installed('bookings'):
            Booking = apps.get_model('bookings', 'Booking')
            SeatLock = apps.get_model('bookings', 'SeatLock')
            has_upcoming = Booking.objects.filter(
                user=user,
                status__in=['PENDING', 'CONFIRMED', 'PARTIALLY_CANCELLED'],
                trip__departure_datetime__gt=dj_tz.now(),
            ).exists()
            if has_upcoming:
                raise ValidationError({
                    "detail": "Cannot delete account while you have active upcoming bookings. Please cancel them first."
                })
            SeatLock.objects.filter(user=user).delete()

        try:
            from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
            for token in OutstandingToken.objects.filter(user=user):
                BlacklistedToken.objects.get_or_create(token=token)
        except Exception:
            pass

        user.saved_passengers.all().delete()
        if hasattr(user, 'profile'):
            user.profile.full_name = "Deleted User"
            user.profile.cnic_id_number = ""
            user.profile.emergency_contact = ""
            user.profile.dob = None
            user.profile.save()

        user.phone = f"del_{user.id.hex[:16]}"
        user.email = None
        user.is_active = False
        user.is_verified = False
        user.set_unusable_password()
        user.save()
