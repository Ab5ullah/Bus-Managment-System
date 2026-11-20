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
