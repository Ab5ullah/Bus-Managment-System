"""
Comprehensive test suite for Module 1: Authentication, Accounts, OTP, and RBAC.
"""
from accounts.permissions import IsOperator
from accounts.permissions import IsCounterAgent
from accounts.permissions import IsPassenger
from accounts.models import OTPPurpose
from accounts.models import OTPVerification
from accounts.models import Gender
from accounts.models import PassengerProfile
from accounts.models import User
from datetime import timedelta
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import UserRole


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_user(db):
    user = User.objects.create_user(
        phone="+923001234567",
        email="passenger@example.com",
        password="SecurePassword123!",
        role=UserRole.PASSENGER,
        is_verified=True
    )
    PassengerProfile.objects.create(
        user=user,
        full_name="John Doe",
        gender=Gender.MALE
    )
    return user


@pytest.fixture
def agent_user(db):
    user = User.objects.create_user(
        phone="+923007654321",
        email="agent@example.com",
        password="SecurePassword123!",
        role=UserRole.COUNTER_AGENT,
        is_verified=True
    )
    PassengerProfile.objects.create(
        user=user,
        full_name="Agent Smith",
        gender=Gender.MALE
    )
    return user


@pytest.fixture
def operator_user(db):
    return User.objects.create_user(
        phone="+923009999999",
        email="operator@example.com",
        password="SecurePassword123!",
        role=UserRole.OPERATOR,
        is_verified=True
    )


@pytest.mark.django_db
class TestRegistration:
    """Test registration endpoints and OTP creation."""

    def test_successful_passenger_registration(self, api_client):
        url = reverse('auth:register')
        payload = {
            "phone": "+923111111111",
            "email": "newuser@example.com",
            "password": "Password123!",
            "full_name": "Ali Khan",
            "gender": "MALE",
            "role": "PASSENGER"
        }
        res = api_client.post(url, payload, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['success'] is True
        assert res.data['data']['user']['phone'] == "+923111111111"
        assert res.data['data']['verification_required'] is True
        assert "otp_preview" in res.data['data']

        # Verify DB records
        user = User.objects.get(phone="+923111111111")
        assert user.is_verified is False
        assert user.profile.full_name == "Ali Khan"
        assert OTPVerification.objects.filter(identifier="+923111111111", purpose=OTPPurpose.REGISTRATION).exists()

    def test_duplicate_phone_rejected(self, api_client, test_user):
        url = reverse('auth:register')
        payload = {
            "phone": test_user.phone,
            "email": "different@example.com",
            "password": "Password123!",
            "full_name": "Duplicate User",
        }
        res = api_client.post(url, payload, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert res.data['success'] is False
        assert res.data['error']['code'] == "VALIDATION_ERROR"
        assert "phone" in res.data['error']['details']

    def test_duplicate_email_rejected(self, api_client, test_user):
        url = reverse('auth:register')
        payload = {
            "phone": "+923222222222",
            "email": test_user.email,
            "password": "Password123!",
            "full_name": "Duplicate Email",
        }
        res = api_client.post(url, payload, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in res.data['error']['details']


@pytest.mark.django_db
class TestOTPVerification:
    """Test OTP validation, expiry, and single-use rules."""

    def test_successful_otp_verification(self, api_client):
        # Register user
        reg_res = api_client.post(reverse('auth:register'), {
            "phone": "+923333333333",
            "password": "Password123!",
            "full_name": "Test OTP",
        }, format='json')
        otp_code = reg_res.data['data']['otp_preview']

        # Verify OTP
        verify_url = reverse('auth:verify-otp')
        res = api_client.post(verify_url, {
            "identifier": "+923333333333",
            "otp_code": otp_code,
            "purpose": "REGISTRATION"
        }, format='json')

        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True

        user = User.objects.get(phone="+923333333333")
        assert user.is_verified is True

    def test_otp_replay_rejected(self, api_client):
        reg_res = api_client.post(reverse('auth:register'), {
            "phone": "+923444444444",
            "password": "Password123!",
            "full_name": "Replay User",
        }, format='json')
        otp_code = reg_res.data['data']['otp_preview']

        verify_url = reverse('auth:verify-otp')
        # First attempt -> succeeds
        res1 = api_client.post(verify_url, {
            "identifier": "+923444444444",
            "otp_code": otp_code,
            "purpose": "REGISTRATION"
        }, format='json')
        assert res1.status_code == status.HTTP_200_OK

        # Second attempt with same OTP -> fails
        res2 = api_client.post(verify_url, {
            "identifier": "+923444444444",
            "otp_code": otp_code,
            "purpose": "REGISTRATION"
        }, format='json')
        assert res2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAuthenticationAndLockout:
    """Test multi-identifier login, token generation, and account lockout."""

    def test_login_with_phone_success(self, api_client, test_user):
        url = reverse('auth:login')
        res = api_client.post(url, {
            "identifier": test_user.phone,
            "password": "SecurePassword123!"
        }, format='json')

        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True
        assert "tokens" in res.data['data']
        assert "access" in res.data['data']['tokens']
        assert "refresh" in res.data['data']['tokens']

    def test_login_with_email_success(self, api_client, test_user):
        url = reverse('auth:login')
        res = api_client.post(url, {
            "identifier": test_user.email,
            "password": "SecurePassword123!"
        }, format='json')

        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True
        assert res.data['data']['user']['email'] == test_user.email

    def test_account_lockout_after_five_failed_attempts(self, api_client, test_user):
        url = reverse('auth:login')

        # 4 failed attempts
        for i in range(4):
            res = api_client.post(url, {
                "identifier": test_user.phone,
                "password": "WrongPassword!"
            }, format='json')
            assert res.status_code == status.HTTP_401_UNAUTHORIZED
            test_user.refresh_from_db()
            assert test_user.failed_login_attempts == i + 1
            assert test_user.is_locked is False

        # 5th failed attempt -> locks account
        res5 = api_client.post(url, {
            "identifier": test_user.phone,
            "password": "WrongPassword!"
        }, format='json')
        assert res5.status_code == status.HTTP_403_FORBIDDEN
        test_user.refresh_from_db()
        assert test_user.is_locked is True

        # Even with correct password, login is blocked while locked
        res_locked = api_client.post(url, {
            "identifier": test_user.phone,
            "password": "SecurePassword123!"
        }, format='json')
        assert res_locked.status_code == status.HTTP_403_FORBIDDEN
        assert "locked" in res_locked.data['error']['message'].lower()


@pytest.mark.django_db
class TestTokenRefreshAndBlacklist:
    """Test JWT token rotation and blacklisting on logout."""

    def test_refresh_token_rotation(self, api_client, test_user):
        login_res = api_client.post(reverse('auth:login'), {
            "identifier": test_user.phone,
            "password": "SecurePassword123!"
        }, format='json')
        refresh_token = login_res.data['data']['tokens']['refresh']

        # Refresh
        refresh_url = reverse('auth:refresh')
        res = api_client.post(refresh_url, {"refresh": refresh_token}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert "access" in res.data['data']

    def test_logout_blacklists_token(self, api_client, test_user):
        login_res = api_client.post(reverse('auth:login'), {
            "identifier": test_user.phone,
            "password": "SecurePassword123!"
        }, format='json')
        tokens = login_res.data['data']['tokens']

        # Authenticate and logout
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        logout_res = api_client.post(reverse('auth:logout'), {"refresh": tokens['refresh']}, format='json')
        assert logout_res.status_code == status.HTTP_200_OK

        # Attempt to use blacklisted refresh token
        res_after = api_client.post(reverse('auth:refresh'), {"refresh": tokens['refresh']}, format='json')
        assert res_after.status_code == status.HTTP_401_UNAUTHORIZED or res_after.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPasswordResetFlow:
    """Test request and confirm password reset."""

    def test_full_password_reset_journey(self, api_client, test_user):
        # 1. Request OTP
        req_res = api_client.post(reverse('auth:password-reset-request'), {
            "identifier": test_user.phone
        }, format='json')
        assert req_res.status_code == status.HTTP_200_OK
        otp_code = req_res.data['data']['otp_preview']

        # 2. Confirm Reset
        confirm_res = api_client.post(reverse('auth:password-reset-confirm'), {
            "identifier": test_user.phone,
            "otp_code": otp_code,
            "new_password": "NewSecretPassword456!"
        }, format='json')
        assert confirm_res.status_code == status.HTTP_200_OK

        # 3. Old password fails
        old_login = api_client.post(reverse('auth:login'), {
            "identifier": test_user.phone,
            "password": "SecurePassword123!"
        }, format='json')
        assert old_login.status_code == status.HTTP_401_UNAUTHORIZED

        # 4. New password succeeds
        new_login = api_client.post(reverse('auth:login'), {
            "identifier": test_user.phone,
            "password": "NewSecretPassword456!"
        }, format='json')
        assert new_login.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestProfileAndSavedPassengers:
    """Test profile updates and companion passenger records."""

    def test_profile_read_and_update(self, api_client, test_user):
        api_client.force_authenticate(user=test_user)
        url = reverse('accounts:profile')

        # Read
        get_res = api_client.get(url)
        assert get_res.status_code == status.HTTP_200_OK
        assert get_res.data['data']['full_name'] == "John Doe"

        # Update
        put_res = api_client.put(url, {
            "full_name": "Johnathan Doe",
            "emergency_contact": "+923000000000",
            "cnic_id_number": "35201-1234567-1"
        }, format='json')
        assert put_res.status_code == status.HTTP_200_OK
        assert put_res.data['data']['full_name'] == "Johnathan Doe"
        assert put_res.data['data']['cnic_id_number'] == "35201-1234567-1"

    def test_saved_passengers_crud(self, api_client, test_user):
        api_client.force_authenticate(user=test_user)
        list_url = reverse('accounts:saved-passengers-list')

        # Create
        create_res = api_client.post(list_url, {
            "full_name": "Companion One",
            "gender": "FEMALE",
            "age": 28,
            "cnic_id_number": "35201-9999999-2"
        }, format='json')
        assert create_res.status_code == status.HTTP_201_CREATED
        p_id = create_res.data['data']['id']

        # List
        list_res = api_client.get(list_url)
        assert list_res.status_code == status.HTTP_200_OK
        assert len(list_res.data['data']) == 1

        # Delete
        detail_url = reverse('accounts:saved-passengers-detail', kwargs={'pk': p_id})
        del_res = api_client.delete(detail_url)
        assert del_res.status_code == status.HTTP_200_OK

        # Verify empty
        list_res2 = api_client.get(list_url)
        assert len(list_res2.data['data']) == 0


class TestRBACPermissions:
    """Test RBAC role permission classes directly."""

    def test_role_permissions(self, db, test_user, agent_user, operator_user):
        from unittest.mock import Mock

        def make_req(user):
            req = Mock()
            req.user = user
            return req

        view = Mock()

        # IsPassenger
        assert IsPassenger().has_permission(make_req(test_user), view) is True
        assert IsPassenger().has_permission(make_req(agent_user), view) is False

        # IsCounterAgent
        assert IsCounterAgent().has_permission(make_req(agent_user), view) is True
        assert IsCounterAgent().has_permission(make_req(test_user), view) is False

        # IsOperator
        assert IsOperator().has_permission(make_req(operator_user), view) is True
        assert IsOperator().has_permission(make_req(test_user), view) is False
