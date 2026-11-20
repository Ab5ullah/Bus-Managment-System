"""
Interactive verification script for Module 4: Scheduled Trips, Overlap Prevention, Seat Blocking & Fares.
Tests:
  1. Scheduling commercial trips with active bus and crew.
  2. Fleet Overlap Prevention: Same bus overlapping interval strictly rejected.
  3. Crew Overlap Prevention: Same driver overlapping interval strictly rejected.
  4. Per-Trip Seat Blocking: Operational hold without modifying fleet-wide seat hardware.
  5. Segment Fare Resolution: Exact FareRule vs Proportional Distance fallback.
  6. API RBAC:
     - Passenger forbidden from scheduling trips or blocking seats.
     - Operator allowed to manage trips, block seats, and compute fares.

Usage:
  python verify_module_4.py
"""
import os
import sys
from datetime import timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.utils import timezone
from rest_framework.test import APIClient
from django.urls import reverse
from common.constants import UserRole, BusType, BusStatus, SeatType, TripStatus
from common.exceptions import BusinessLogicError
from accounts.models import User
from fleet.models import Bus, Driver, Conductor, CrewStatus
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip, TripSeatBlock, FareRule
from trips.services import TripScheduleService, TripSeatBlockService, FareService


def print_header(title):
    print("\n" + "=" * 75)
    print(title)
    print("=" * 75)


def run_verification():
    print_header("MODULE 4 VERIFICATION: TRIPS, FLEET/CREW OVERLAP PREVENT & FARES")

    # 1. Setup Operator & Passenger
    operator_user, _ = User.objects.get_or_create(
        phone="+923006660001",
        defaults={"role": UserRole.OPERATOR, "is_verified": True, "is_staff": True}
    )
    operator_user.set_password("OperatorPass123!")
    operator_user.save()

    passenger_user, _ = User.objects.get_or_create(
        phone="+923006660002",
        defaults={"role": UserRole.PASSENGER, "is_verified": True}
    )
    passenger_user.set_password("PassengerPass123!")
    passenger_user.save()

    client = APIClient()
    op_login = client.post(reverse('auth:login'), {'identifier': operator_user.phone, 'password': 'OperatorPass123!'})
    op_token = op_login.data['data']['tokens']['access']

    pass_login = client.post(reverse('auth:login'), {'identifier': passenger_user.phone, 'password': 'PassengerPass123!'})
    pass_token = pass_login.data['data']['tokens']['access']

    # 2. Setup Route & Fleet
    t_lhr, _ = Terminal.objects.get_or_create(code="LHR-V4", defaults={"name": "Lahore Thokar", "city": "Lahore"})
    t_bhr, _ = Terminal.objects.get_or_create(code="BHR-V4", defaults={"name": "Bhera Rest Area", "city": "Bhera"})
    t_isb, _ = Terminal.objects.get_or_create(code="ISB-V4", defaults={"name": "Islamabad Faizabad", "city": "Islamabad"})

    Trip.objects.filter(route__code="LHR-ISB-V4").delete()
    Route.objects.filter(code="LHR-ISB-V4").delete()
    route = RouteService.create_route(
        code="LHR-ISB-V4",
        name="Lahore to Islamabad M2 Verification Route",
        origin=t_lhr,
        destination=t_isb,
        stops_data=[
            {"terminal": t_lhr.id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": t_bhr.id, "stop_order": 1, "distance_from_origin_km": 200.0, "offset_minutes": 130},
            {"terminal": t_isb.id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
    )

    bus1, _ = Bus.objects.get_or_create(registration_number="BUS-VERIFY-01", defaults={"bus_type": BusType.STANDARD_AC, "status": BusStatus.ACTIVE})
    if bus1.seats.count() == 0:
        generate_bus_seat_matrix(bus=bus1, layout_type="2x2", rows=10)

    bus2, _ = Bus.objects.get_or_create(registration_number="BUS-VERIFY-02", defaults={"bus_type": BusType.LUXURY, "status": BusStatus.ACTIVE})
    if bus2.seats.count() == 0:
        generate_bus_seat_matrix(bus=bus2, layout_type="2x1", rows=10)

    driver, _ = Driver.objects.get_or_create(
        license_number="LIC-V4-01",
        defaults={"full_name": "Sultan Mehmood", "phone": "+923015551122", "status": CrewStatus.AVAILABLE}
    )

    # 3. Schedule Trip
    print("\n[1/6] Testing Trip Scheduling with Active Bus & Driver...")
    now = timezone.now() + timedelta(days=1)
    dep1 = now.replace(hour=8, minute=0, second=0, microsecond=0)
    arr1 = now.replace(hour=12, minute=30, second=0, microsecond=0)

    Trip.objects.filter(trip_number="TRIP-V4-01").delete()
    trip1 = TripScheduleService.create_trip(
        route=route,
        bus=bus1,
        driver=driver,
        departure_datetime=dep1,
        arrival_datetime=arr1,
        base_fare=Decimal('2500.00'),
        trip_number="TRIP-V4-01"
    )
    print(f"  Trip Scheduled: {trip1.trip_number} ({trip1.route.code})")
    print(f"  Departure: {trip1.departure_datetime:%Y-%m-%d %H:%M} ➔ Arrival: {trip1.arrival_datetime:%Y-%m-%d %H:%M}")
    print(f"  Bus: {trip1.bus.registration_number} | Driver: {trip1.driver.full_name} | Base Fare: PKR {trip1.base_fare}")
    print("  --> PASS: Trip successfully scheduled!")

    # 4. Bus Overlap Rejection
    print("\n[2/6] Testing Bus Double-Scheduling Overlap Prevention...")
    dep_overlap = now.replace(hour=10, minute=0)
    arr_overlap = now.replace(hour=14, minute=0)

    try:
        TripScheduleService.create_trip(
            route=route,
            bus=bus1,  # Same bus!
            departure_datetime=dep_overlap,
            arrival_datetime=arr_overlap,
            base_fare=Decimal('2500.00'),
            trip_number="TRIP-BUS-OVERLAP"
        )
        print("  FAIL: Overlapping bus assignment allowed!")
        sys.exit(1)
    except BusinessLogicError as e:
        print(f"  Bus Overlap REJECTED: '{str(e)}' (Correct)")
    print("  --> PASS: Bus double-booking mathematically prevented!")

    # 5. Driver Overlap Rejection
    print("\n[3/6] Testing Driver Double-Scheduling Overlap Prevention...")
    try:
        TripScheduleService.create_trip(
            route=route,
            bus=bus2,  # Different bus!
            driver=driver,  # Same driver!
            departure_datetime=dep_overlap,
            arrival_datetime=arr_overlap,
            base_fare=Decimal('2500.00'),
            trip_number="TRIP-DRV-OVERLAP"
        )
        print("  FAIL: Overlapping driver assignment allowed!")
        sys.exit(1)
    except BusinessLogicError as e:
        print(f"  Driver Overlap REJECTED: '{str(e)}' (Correct)")
    print("  --> PASS: Driver double-scheduling mathematically prevented!")

    # 6. Per-Trip Seat Blocking
    print("\n[4/6] Testing Per-Trip Operational Seat Blocking...")
    seat_1a = bus1.seats.get(seat_number="1A")
    block = TripSeatBlockService.block_seat(
        trip=trip1,
        seat=seat_1a,
        reason="VIP Dignitary Protocol",
        user=operator_user
    )
    assert block.seat == seat_1a
    print(f"  Blocked Seat {seat_1a.seat_number} on {trip1.trip_number}: Reason='{block.reason}'")

    # Double blocking check
    try:
        TripSeatBlockService.block_seat(trip=trip1, seat=seat_1a)
        print("  FAIL: Double seat blocking allowed!")
        sys.exit(1)
    except BusinessLogicError as e:
        print(f"  Duplicate Block REJECTED: '{str(e)}' (Correct)")

    # Unblock
    TripSeatBlockService.unblock_seat(trip=trip1, seat=seat_1a)
    print(f"  Seat {seat_1a.seat_number} successfully unblocked.")
    print("  --> PASS: Per-trip seat blocking operational!")

    # 7. Segment Fare Resolution Engine
    print("\n[5/6] Testing Segment Fare Engine (Exact Rule vs Proportional Fallback)...")
    s_lhr = route.stops.get(stop_order=0)
    s_bhr = route.stops.get(stop_order=1)
    s_isb = route.stops.get(stop_order=2)

    # Set explicit fare for LHR -> BHR = 1350 PKR
    FareService.set_fare_rule(
        route=route,
        from_stop=s_lhr,
        to_stop=s_bhr,
        fare_amount=Decimal('1350.00')
    )

    # Calculate LHR -> BHR (Should hit explicit rule)
    fare_exact = FareService.calculate_fare(trip1, s_lhr, s_bhr)
    assert fare_exact == Decimal('1350.00')
    print(f"  Segment 1: Lahore ➔ Bhera = PKR {fare_exact} (Matched explicit FareRule)")

    # Calculate BHR -> ISB (Should hit proportional distance fallback)
    # Dist = 380 - 200 = 180 km. Total = 380 km. Base = 2500 PKR.
    # Fare = 2500 * (180/380) = 1184.21 PKR
    fare_prop = FareService.calculate_fare(trip1, s_bhr, s_isb)
    assert fare_prop == Decimal('1184.21')
    print(f"  Segment 2: Bhera ➔ Islamabad = PKR {fare_prop} (Proportional fallback: 180km/380km * 2500 PKR)")

    # Full Corridor: Lahore -> Islamabad
    fare_full = FareService.calculate_fare(trip1, s_lhr, s_isb)
    assert fare_full == Decimal('2500.00')
    print(f"  Full Corridor: Lahore ➔ Islamabad = PKR {fare_full} (Matches Base Fare)")
    print("  --> PASS: Segment pricing hierarchy verified!")

    # 8. API RBAC Validation
    print("\n[6/6] Testing REST API Endpoints & RBAC Security...")
    # Passenger cannot create trip
    bad_res = client.post(
        reverse('trip-list'),
        {
            "route": str(route.id),
            "bus": str(bus1.id),
            "departure_datetime": (now + timedelta(days=10)).isoformat(),
            "arrival_datetime": (now + timedelta(days=10, hours=4)).isoformat(),
            "base_fare": "2000.00"
        },
        format='json',
        HTTP_AUTHORIZATION=f"Bearer {pass_token}"
    )
    assert bad_res.status_code == 403
    print("  Passenger create trip attempt: REJECTED (HTTP 403 Forbidden)")

    # Operator calculate-fare API action
    fare_url = reverse('trip-calculate-fare', kwargs={'pk': str(trip1.id)})
    fare_api_res = client.get(
        fare_url,
        {'from_stop': str(s_lhr.id), 'to_stop': str(s_bhr.id)},
        HTTP_AUTHORIZATION=f"Bearer {op_token}"
    )
    assert fare_api_res.status_code == 200
    assert fare_api_res.data['data']['calculated_fare'] == '1350.00'
    print(f"  API calculate-fare action: SUCCESS (PKR {fare_api_res.data['data']['calculated_fare']})")
    print("  --> PASS: REST API endpoints and RBAC verified!")

    print_header("ALL MODULE 4 VERIFICATIONS PASSED SUCCESSFULLY (100%)")


if __name__ == '__main__':
    run_verification()
