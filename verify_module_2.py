"""
Interactive verification script for Module 2: Fleet Management (Buses, Seat Layout Matrix, Crew).
Tests:
  1. Operator Bus Creation with automated 2x2 seat matrix generator.
  2. Inspecting the generated seat map (Row, Col, Deck, Window/Aisle types, Ladies-Only).
  3. Regenerating seat layout to 2x1 Luxury.
  4. Updating seat attributes (is_ladies_only, hardware status is_active).
  5. Creating Driver and Conductor records with duplicate rejection.
  6. RBAC verification: Passengers blocked from fleet mutations.

Usage:
  python verify_module_2.py
"""
import os
import sys
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from rest_framework.test import APIClient
from django.urls import reverse
from common.constants import UserRole, BusType, BusStatus, SeatType
from accounts.models import User
from fleet.models import Bus, Driver, Conductor


def run_verification():
    print("=" * 70)
    print("MODULE 2 VERIFICATION: FLEET MANAGEMENT & SEAT MATRIX GENERATOR")
    print("=" * 70)

    client = APIClient()

    # Setup Operator and Passenger users
    operator_user, _ = User.objects.get_or_create(
        phone="+923000000099",
        defaults={
            "email": "operator.test@fleet.com",
            "role": UserRole.OPERATOR,
            "is_verified": True
        }
    )
    operator_user.set_password("OperatorPass123!")
    operator_user.save()

    passenger_user, _ = User.objects.get_or_create(
        phone="+923000000088",
        defaults={
            "email": "passenger.test@fleet.com",
            "role": UserRole.PASSENGER,
            "is_verified": True
        }
    )
    passenger_user.set_password("PassengerPass123!")
    passenger_user.save()

    # Clean up test buses
    Bus.objects.filter(registration_number__in=["VERIFY-40", "VERIFY-LUX"]).delete()
    Driver.objects.filter(license_number="LIC-VERIFY-01").delete()
    Conductor.objects.filter(employee_id="CND-VERIFY-01").delete()

    # 1. Bus Creation with Automated 2x2 Layout Generator
    print("\n[1/6] Testing Bus Creation with 2x2 Layout Matrix Generator...")
    client.force_authenticate(user=operator_user)
    bus_url = reverse('fleet:buses-list')

    bus_payload = {
        "registration_number": "VERIFY-40",
        "name": "Super Executive Liner",
        "bus_type": "EXECUTIVE",
        "amenities": ["High-Speed WiFi", "USB Ports", "Refreshments"],
        "status": "ACTIVE",
        "layout": {
            "layout_type": "2x2",
            "rows": 10,
            "ladies_rows": 2
        }
    }
    create_res = client.post(bus_url, bus_payload, format='json')
    assert create_res.status_code == 201, f"Expected 201, got {create_res.status_code}"
    bus_data = create_res.json()['data']
    bus_id = bus_data['id']
    total_seats = bus_data['total_seats']
    print(f"  Bus Created: {bus_data['registration_number']} (ID: {bus_id})")
    print(f"  Total Physical Seats Generated: {total_seats}")
    assert total_seats == 40, f"Expected 40 seats, got {total_seats}"
    print("  --> PASS: Bus and 40-seat matrix created successfully!")

    # 2. Inspect Seat Map
    print("\n[2/6] Inspecting Generated Seat Map (Types & Ladies-Only Rules)...")
    detail_url = reverse('fleet:buses-detail', kwargs={'pk': bus_id})
    detail_res = client.get(detail_url)
    assert detail_res.status_code == 200
    seats = detail_res.json()['data']['seats']

    # Check Seat 1A (Window, Ladies-Only)
    seat_1a = next(s for s in seats if s['seat_number'] == "1A")
    assert seat_1a['seat_type'] == "WINDOW"
    assert seat_1a['is_ladies_only'] is True
    print(f"  Seat 1A: Type={seat_1a['seat_type']}, Ladies-Only={seat_1a['is_ladies_only']} (Correct)")

    # Check Seat 1B (Aisle, Ladies-Only)
    seat_1b = next(s for s in seats if s['seat_number'] == "1B")
    assert seat_1b['seat_type'] == "AISLE"
    assert seat_1b['is_ladies_only'] is True
    print(f"  Seat 1B: Type={seat_1b['seat_type']}, Ladies-Only={seat_1b['is_ladies_only']} (Correct)")

    # Check Seat 5A (Row 5 -> Not ladies-only)
    seat_5a = next(s for s in seats if s['seat_number'] == "5A")
    assert seat_5a['is_ladies_only'] is False
    print(f"  Seat 5A: Type={seat_5a['seat_type']}, Ladies-Only={seat_5a['is_ladies_only']} (Correct)")
    print("  --> PASS: Seat types and demographic rules strictly verified!")

    # 3. Regenerate Seat Matrix (2x1 Luxury)
    print("\n[3/6] Testing Seat Matrix Re-generation (2x1 Luxury Layout)...")
    regen_url = reverse('fleet:buses-generate-seats', kwargs={'pk': bus_id})
    regen_res = client.post(regen_url, {
        "layout_type": "2x1",
        "rows": 10,
        "ladies_rows": 2
    }, format='json')
    assert regen_res.status_code == 200
    new_total = regen_res.json()['data']['total_seats']
    print(f"  Re-generated Layout: 2x1 Luxury -> Total Seats = {new_total}")
    assert new_total == 30, f"Expected 30 seats for 10 rows of 2x1, got {new_total}"
    print("  --> PASS: Dynamic matrix regeneration verified!")

    # 4. Patch Individual Seat (Hardware Damage & Custom Attribute)
    print("\n[4/6] Testing Individual Seat Attributes Update...")
    seat_to_patch = regen_res.json()['data']['seats'][0]
    patch_url = reverse('fleet:buses-update-seat', kwargs={'pk': bus_id, 'seat_id': seat_to_patch['id']})
    patch_res = client.patch(patch_url, {
        "is_active": False,  # Physical broken seat
        "is_ladies_only": True
    }, format='json')
    assert patch_res.status_code == 200
    patched_seat = patch_res.json()['data']
    assert patched_seat['is_active'] is False
    assert patched_seat['is_ladies_only'] is True
    print(f"  Seat {patched_seat['seat_number']} marked hardware damaged (is_active=False).")
    print("  --> PASS: Seat attribute patching operational!")

    # 5. Driver & Conductor Crew Management
    print("\n[5/6] Testing Crew Management (Drivers & Conductors)...")
    driver_url = reverse('fleet:drivers-list')
    drv_res = client.post(driver_url, {
        "full_name": "Umar Farooq",
        "license_number": "LIC-VERIFY-01",
        "phone": "+923001119988",
        "experience_years": 10,
        "status": "AVAILABLE"
    }, format='json')
    assert drv_res.status_code == 201
    print(f"  Driver created: Umar Farooq (Lic: LIC-VERIFY-01)")

    # Duplicate license rejected
    dup_drv = client.post(driver_url, {
        "full_name": "Duplicate Driver",
        "license_number": "LIC-VERIFY-01",
        "phone": "+923009990000",
    }, format='json')
    assert dup_drv.status_code == 400
    print("  Duplicate driver license correctly rejected (HTTP 400).")

    conductor_url = reverse('fleet:conductors-list')
    cnd_res = client.post(conductor_url, {
        "full_name": "Kashif Ali",
        "employee_id": "CND-VERIFY-01",
        "phone": "+923008887766",
        "status": "AVAILABLE"
    }, format='json')
    assert cnd_res.status_code == 201
    print(f"  Conductor created: Kashif Ali (Emp ID: CND-VERIFY-01)")
    print("  --> PASS: Crew management operational!")

    # 6. RBAC Check (Passengers cannot manage fleet)
    print("\n[6/6] Testing RBAC Security for Fleet Management...")
    client.force_authenticate(user=passenger_user)
    forbidden_res = client.post(bus_url, {"registration_number": "HACK-01"}, format='json')
    assert forbidden_res.status_code == 403
    print("  Passenger attempt to create bus: REJECTED (HTTP 403 Forbidden)")

    # But passengers can view the bus catalog
    public_res = client.get(bus_url)
    assert public_res.status_code == 200
    print("  Passenger view bus catalog: ALLOWED (HTTP 200 OK)")
    print("  --> PASS: Role-Based Access Control verified!")

    print("\n" + "=" * 70)
    print("ALL MODULE 2 VERIFICATIONS PASSED SUCCESSFULLY (100%)")
    print("=" * 70)


if __name__ == '__main__':
    run_verification()
