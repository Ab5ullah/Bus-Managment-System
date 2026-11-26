"""
Serializers for User Registration, OTP Verification, Authentication, and Profiles.
Follows Modular Monolith with Service Layer: serializers validate input and delegate to Services.
"""
import re
from rest_framework import serializers

from common.constants import UserRole
from .models import User, PassengerProfile, SavedPassenger, OTPPurpose, Gender
from .services import (
    RegistrationService,
    OTPService,
    AuthenticationService,
    PasswordResetService,
)


class UserPublicSerializer(serializers.ModelSerializer):
    """Public user details serializer."""
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'phone', 'email', 'role', 'role_display', 'is_verified', 'created_at']
        read_only_fields = ['id', 'role_display', 'is_verified', 'created_at']


class PassengerProfileSerializer(serializers.ModelSerializer):
    """Passenger profile details serializer."""
    user = UserPublicSerializer(read_only=True)

    class Meta:
        model = PassengerProfile
        fields = ['id', 'user', 'full_name', 'gender', 'dob', 'cnic_id_number', 'emergency_contact']
        read_only_fields = ['id', 'user']


class RegisterSerializer(serializers.Serializer):
    """Registration serializer for passengers and counter agents."""
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)
    full_name = serializers.CharField(max_length=150)
    gender = serializers.ChoiceField(choices=Gender.choices, default=Gender.MALE)
    role = serializers.ChoiceField(
        choices=[UserRole.PASSENGER, UserRole.COUNTER_AGENT],
        default=UserRole.PASSENGER
    )

    def validate_phone(self, value):
        cleaned = re.sub(r'[\s\-]', '', value)
        if len(cleaned) < 10 or not cleaned.replace('+', '').isdigit():
            raise serializers.ValidationError("Enter a valid phone number with country/area code.")
        if User.objects.filter(phone=cleaned).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return cleaned

    def validate_email(self, value):
        if value:
            value = value.strip().lower()
            if User.objects.filter(email=value).exists():
                raise serializers.ValidationError("An account with this email address already exists.")
        return value

    def create(self, validated_data):
        user, plain_otp = RegistrationService.register_user(
            phone=validated_data['phone'],
            password=validated_data['password'],
            full_name=validated_data['full_name'],
            email=validated_data.get('email'),
            gender=validated_data.get('gender', Gender.MALE),
            role=validated_data.get('role', UserRole.PASSENGER)
        )
        user.plain_otp = plain_otp
        return user


class VerifyOTPSerializer(serializers.Serializer):
    """OTP Verification serializer."""
    identifier = serializers.CharField(max_length=150)
    otp_code = serializers.CharField(max_length=6, min_length=6)
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices, default=OTPPurpose.REGISTRATION)

    def validate(self, attrs):
        otp_record = OTPService.verify_otp(
            identifier=attrs['identifier'],
            otp_code=attrs['otp_code'],
            purpose=attrs['purpose']
        )
        attrs['otp_record'] = otp_record
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    """Serializer for resending an OTP to phone or email."""
    identifier = serializers.CharField(max_length=150)
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices, default=OTPPurpose.REGISTRATION)

    def validate(self, attrs):
        user, plain_otp = OTPService.resend_otp(
            identifier=attrs['identifier'],
            purpose=attrs['purpose']
        )
        attrs['user'] = user
        attrs['plain_otp'] = plain_otp
        return attrs


class LoginSerializer(serializers.Serializer):
    """Multi-identifier login (Phone or Email) with brute-force lockout."""
    identifier = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user, tokens = AuthenticationService.authenticate_and_generate_tokens(
            identifier=attrs['identifier'],
            password=attrs['password']
        )
        attrs['user'] = user
        attrs['tokens'] = tokens
        return attrs


class LogoutSerializer(serializers.Serializer):
    """Logout serializer that blacklists the refresh token."""
    refresh = serializers.CharField()

    def validate(self, attrs):
        AuthenticationService.blacklist_token(attrs['refresh'])
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """Initiates password reset via OTP."""
    identifier = serializers.CharField(max_length=150)

    def validate(self, attrs):
        plain_otp = PasswordResetService.request_password_reset(attrs['identifier'])
        attrs['plain_otp'] = plain_otp
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Completes password reset with validated OTP and new password."""
    identifier = serializers.CharField(max_length=150)
    otp_code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        user = PasswordResetService.confirm_password_reset(
            identifier=attrs['identifier'],
            otp_code=attrs['otp_code'],
            new_password=attrs['new_password']
        )
        attrs['user'] = user
        return attrs


class SavedPassengerSerializer(serializers.ModelSerializer):
    """CRUD serializer for companion passenger records."""

    class Meta:
        model = SavedPassenger
        fields = ['id', 'full_name', 'gender', 'age', 'cnic_id_number', 'created_at']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class DeleteAccountSerializer(serializers.Serializer):
    """Serializer requiring password confirmation before deleting account."""
    password = serializers.CharField(write_only=True, required=True)
