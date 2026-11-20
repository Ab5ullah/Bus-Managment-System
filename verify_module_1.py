"""
Interactive verification script for Module 1: Authentication, Accounts & RBAC.
Executes complete authentication lifecycle:
  Register -> Verify OTP -> Login (Phone & Email) -> Profile -> Saved Passengers -> Refresh & Logout -> Lockout Test.

Usage:
  python verify_module_1.py
"""
import os
import sys
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from accounts.models import User

from rest_framework.test import APIClient
from django.urls import reverse


def run_verification():
    print("=" * 70)
    print("MODULE 1 VERIFICATION: AUTHENTICATION, ACCOUNTS & RBAC")
    print("=" * 70)

    client = APIClient()

    # Clean up test accounts if present
    test_phone = "+923001112233"
    test_email = "ali.raza@example.com"
    User.objects.filter(phone=test_phone).delete()
    User.objects.filter(email=test_email).delete()

    # 1. Registration
    print("\n[1/7] Testing User Registration (/api/auth/register/)...")
    reg_url = reverse('auth:register')
    reg_payload = {
        "phone": test_phone,
        "email": test_email,
        "password": "StrongPassword999!",
        "full_name": "Ali Raza",
        "gender": "MALE",
        "role": "PASSENGER"
    }
    reg_res = client.post(reg_url, reg_payload, format='json')
    assert reg_res.status_code == 201, f"Expected 201, got {reg_res.status_code}"
    reg_data = reg_res.json()
    otp_code = reg_data['data']['otp_preview']
    print(f"  User registered successfully: {test_phone}")
    print(f"  Generated OTP Code (for verification): {otp_code}")
    print("  --> PASS: Registration & OTP creation operational!")

    # 2. OTP Verification
    print("\n[2/7] Testing OTP Verification (/api/auth/verify-otp/)...")
    verify_url = reverse('auth:verify-otp')
    verify_res = client.post(verify_url, {
        "identifier": test_phone,
        "otp_code": otp_code,
        "purpose": "REGISTRATION"
    }, format='json')
    assert verify_res.status_code == 200, f"Expected 200, got {verify_res.status_code}"
    user = User.objects.get(phone=test_phone)
    assert user.is_verified is True
    print("  --> PASS: Phone verified, account status = ACTIVE!")

    # 3. Authentication (Phone & Email)
    print("\n[3/7] Testing Multi-Identifier Authentication (/api/auth/login/)...")
    login_url = reverse('auth:login')

    # Phone login
    login_phone_res = client.post(login_url, {
        "identifier": test_phone,
        "password": "StrongPassword999!"
    }, format='json')
    assert login_phone_res.status_code == 200
    tokens = login_phone_res.json()['data']['tokens']
    access_token = tokens['access']
    refresh_token = tokens['refresh']
    print(f"  Login with Phone: SUCCESS (Access JWT: {access_token[:25]}...)")

    # Email login
    login_email_res = client.post(login_url, {
        "identifier": test_email,
        "password": "StrongPassword999!"
    }, format='json')
    assert login_email_res.status_code == 200
    print("  Login with Email: SUCCESS")
    print("  --> PASS: Dual-identifier JWT authentication operational!")

    # 4. Profile Management
    print("\n[4/7] Testing Profile Retrieval & Update (/api/accounts/profile/)...")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    profile_url = reverse('accounts:profile')

    get_prof = client.get(profile_url)
    assert get_prof.status_code == 200
    print(f"  Fetched Profile: {get_prof.json()['data']['full_name']}")

    update_prof = client.put(profile_url, {
        "full_name": "Ali Raza Khan",
        "emergency_contact": "+923009998877",
        "cnic_id_number": "35201-7654321-1"
    }, format='json')
    assert update_prof.status_code == 200
    assert update_prof.json()['data']['full_name'] == "Ali Raza Khan"
    print("  --> PASS: Profile updated successfully!")

    # 5. Saved Companion Passengers
    print("\n[5/7] Testing Saved Companion Passengers (/api/accounts/saved-passengers/)...")
    passengers_url = reverse('accounts:saved-passengers-list')
    create_p = client.post(passengers_url, {
        "full_name": "Fatima Raza",
        "gender": "FEMALE",
        "age": 25,
        "cnic_id_number": "35201-1122334-2"
    }, format='json')
    assert create_p.status_code == 201
    p_id = create_p.json()['data']['id']
    print(f"  Saved Passenger Created: Fatima Raza (ID: {p_id})")

    list_p = client.get(passengers_url)
    assert list_p.status_code == 200
    assert len(list_p.json()['data']) >= 1
    print("  --> PASS: Saved passengers CRUD operational!")

    # 6. Token Rotation & Blacklist Logout
    print("\n[6/7] Testing Token Rotation & Blacklist Logout...")
    refresh_url = reverse('auth:refresh')
    rotate_res = client.post(refresh_url, {"refresh": refresh_token}, format='json')
    assert rotate_res.status_code == 200
    rotated_refresh_token = rotate_res.json()['data'].get('refresh', refresh_token)
    print("  Token refreshed and rotated successfully.")

    logout_url = reverse('auth:logout')
    logout_res = client.post(logout_url, {"refresh": rotated_refresh_token}, format='json')
    assert logout_res.status_code == 200
    print("  Token blacklisted successfully upon logout.")

    # Try refreshing blacklisted token
    blocked_refresh = client.post(refresh_url, {"refresh": rotated_refresh_token}, format='json')
    assert blocked_refresh.status_code in (400, 401)
    print("  --> PASS: Blacklisted token correctly rejected!")

    # 7. Account Lockout Security Check
    print("\n[7/7] Testing Account Lockout Security (5 Failed Passwords)...")
    client.credentials()  # Clear credentials

    for i in range(1, 5):
        fail_res = client.post(login_url, {
            "identifier": test_phone,
            "password": "WrongPasswordAttempt"
        }, format='json')
        assert fail_res.status_code == 401
        print(f"  Attempt {i}/5 failed (HTTP 401)")

    # 5th attempt locks the account
    lock_res = client.post(login_url, {
        "identifier": test_phone,
        "password": "WrongPasswordAttempt"
    }, format='json')
    assert lock_res.status_code == 403
    print("  Attempt 5/5: ACCOUNT LOCKED (HTTP 403 Forbidden)")

    # Even with correct password, login is blocked
    blocked_correct = client.post(login_url, {
        "identifier": test_phone,
        "password": "StrongPassword999!"
    }, format='json')
    assert blocked_correct.status_code == 403
    print(f"  Login attempt while locked: REJECTED (HTTP 403)")
    print("  --> PASS: Brute-force account lockout verified!")

    print("\n" + "=" * 70)
    print("ALL MODULE 1 VERIFICATIONS PASSED SUCCESSFULLY (100%)")
    print("=" * 70)


if __name__ == '__main__':
    run_verification()
