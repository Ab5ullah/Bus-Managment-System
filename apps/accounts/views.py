"""
Views for Authentication, OTP Verification, Profile, and Saved Passengers.
"""
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success
from .models import PassengerProfile, SavedPassenger
from .serializers import (
    RegisterSerializer,
    VerifyOTPSerializer,
    ResendOTPSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    PassengerProfileSerializer,
    SavedPassengerSerializer,
    UserPublicSerializer,
    DeleteAccountSerializer,
)
from .services import AccountDeletionService


class RegisterView(APIView):
    """
    Registers a new passenger or counter agent and generates a verification OTP.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register New User",
        request=RegisterSerializer,
        responses={201: OpenApiResponse(description="User registered successfully, OTP dispatched.")},
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return api_success(
            data={
                "user": UserPublicSerializer(user).data,
                "verification_required": True,
                # In development/test, return OTP in response for instant testing
                "otp_preview": getattr(user, 'plain_otp', None)
            },
            message="Registration successful. Please verify the OTP sent to your phone/email.",
            status_code=status.HTTP_201_CREATED
        )


class VerifyOTPView(APIView):
    """
    Verifies phone/email OTP for account activation or password reset.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Verify OTP",
        request=VerifyOTPSerializer,
        responses={200: OpenApiResponse(description="OTP verified successfully.")},
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return api_success(
            message="OTP verified successfully. Your account is now active.",
            status_code=status.HTTP_200_OK
        )


class ResendOTPView(APIView):
    """
    Resends a fresh 6-digit OTP for account verification or password reset.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Resend OTP",
        request=ResendOTPSerializer,
        responses={200: OpenApiResponse(description="New OTP generated and dispatched.")},
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plain_otp = serializer.validated_data.get('plain_otp')

        data = {
            "cooldown_seconds": 60,
            "otp_preview": plain_otp,
        }

        return api_success(
            data=data,
            message="A new OTP has been sent to your phone/email.",
            status_code=status.HTTP_200_OK
        )


class LoginView(APIView):
    """
    Authenticates user with Phone or Email and password.
    Enforces account lockout after 5 consecutive failed attempts.
    Returns JWT Access and Refresh tokens.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="User Login (JWT)",
        request=LoginSerializer,
        responses={200: OpenApiResponse(description="Login successful. JWT tokens returned.")},
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tokens = serializer.validated_data['tokens']

        profile_data = None
        if hasattr(user, 'profile'):
            profile_data = PassengerProfileSerializer(user.profile).data

        return api_success(
            data={
                "user": UserPublicSerializer(user).data,
                "profile": profile_data,
                "tokens": tokens
            },
            message="Authentication successful.",
            status_code=status.HTTP_200_OK
        )


class CustomTokenRefreshView(TokenRefreshView):
    """
    Rotates SimpleJWT access token with fresh validity.
    """
    @extend_schema(
        summary="Refresh JWT Access Token",
        tags=["Authentication"]
    )
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        return api_success(
            data=response.data,
            message="Token refreshed successfully."
        )


class LogoutView(APIView):
    """
    Logs out the user by blacklisting their refresh token.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Logout & Blacklist Token",
        request=LogoutSerializer,
        responses={200: OpenApiResponse(description="Token blacklisted successfully.")},
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return api_success(
            message="Logged out successfully. Token has been blacklisted.",
            status_code=status.HTTP_200_OK
        )


class PasswordResetRequestView(APIView):
    """
    Requests a password reset OTP for a phone number or email address.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Request Password Reset OTP",
        request=PasswordResetRequestSerializer,
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        plain_otp = serializer.validated_data.get('plain_otp')

        return api_success(
            data={"otp_preview": plain_otp} if plain_otp else {},
            message="If the phone/email exists, an OTP code has been dispatched."
        )


class PasswordResetConfirmView(APIView):
    """
    Validates password reset OTP and sets a new password.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Confirm Password Reset",
        request=PasswordResetConfirmSerializer,
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return api_success(
            message="Password reset successfully. You may now log in with your new password."
        )


class ProfileView(APIView):
    """
    Retrieve or update the authenticated passenger's profile.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Current User Profile",
        responses={200: PassengerProfileSerializer},
        tags=["Accounts"]
    )
    def get(self, request):
        profile, _ = PassengerProfile.objects.get_or_create(
            user=request.user,
            defaults={"full_name": "Passenger"}
        )
        serializer = PassengerProfileSerializer(profile)
        return api_success(data=serializer.data)

    @extend_schema(
        summary="Update Current User Profile",
        request=PassengerProfileSerializer,
        responses={200: PassengerProfileSerializer},
        tags=["Accounts"]
    )
    def put(self, request):
        profile, _ = PassengerProfile.objects.get_or_create(
            user=request.user,
            defaults={"full_name": "Passenger"}
        )
        serializer = PassengerProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return api_success(data=serializer.data, message="Profile updated successfully.")

    @extend_schema(
        summary="Delete Current User Account",
        request=DeleteAccountSerializer,
        tags=["Accounts"]
    )
    def delete(self, request):
        serializer = DeleteAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AccountDeletionService.delete_account(request.user, serializer.validated_data['password'])
        return api_success(message="Your account has been permanently deleted and anonymized.")


class DeleteAccountView(APIView):
    """
    Self-service account deletion endpoint (supports POST and DELETE).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Delete Account (POST)",
        request=DeleteAccountSerializer,
        tags=["Accounts"]
    )
    def post(self, request):
        serializer = DeleteAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AccountDeletionService.delete_account(request.user, serializer.validated_data['password'])
        return api_success(message="Your account has been permanently deleted and anonymized.")

    @extend_schema(
        summary="Delete Account (DELETE)",
        request=DeleteAccountSerializer,
        tags=["Accounts"]
    )
    def delete(self, request):
        return self.post(request)


class SavedPassengerViewSet(viewsets.ModelViewSet):
    """
    CRUD for saved companion passengers.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SavedPassengerSerializer

    def get_queryset(self):
        return SavedPassenger.objects.filter(user=self.request.user)

    @extend_schema(tags=["Accounts"])
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Accounts"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return api_success(
            data=serializer.data,
            message="Passenger saved successfully.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Accounts"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Accounts"])
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return api_success(data=serializer.data, message="Passenger updated successfully.")

    @extend_schema(tags=["Accounts"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return api_success(message="Passenger removed successfully.")
