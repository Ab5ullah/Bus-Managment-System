"""
Management command to seed initial demo data:
1. Superuser / Admin (+923000000000)
2. Operator User (+923000000099)
3. Passenger User (+923000000088)
4. Sample Buses with dynamic seat matrices
5. Sample Drivers & Conductors
"""
from django.core.management.base import BaseCommand
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from accounts.models import User, PassengerProfile, Gender
from fleet.models import Bus, Driver, Conductor, CrewStatus
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip, TripSeatBlock, FareRule
from trips.services import TripScheduleService, TripSeatBlockService, FareService
from common.constants import UserRole, BusType, BusStatus, TripStatus


class Command(BaseCommand):
    help = "Seeds initial demo users, fleet vehicles, seat matrices, and crew members."

    def handle(self, *args, **options):
        self.stdout.write("--- Seeding Demo Data ---")

        # 1. Superuser / Admin
        admin_user, created = User.objects.get_or_create(
            phone="+923000000000",
            defaults={
                "email": "admin@busms.com",
                "role": UserRole.SUPER_ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "is_verified": True,
            }
        )
        if created:
            admin_user.set_password("AdminPassword123!")
            admin_user.save()
            PassengerProfile.objects.create(
                user=admin_user,
                full_name="System Administrator",
                gender=Gender.MALE
            )
            self.stdout.write(self.style.SUCCESS("Created Admin: +923000000000 / AdminPassword123!"))
        else:
            self.stdout.write("Admin user already exists.")

        # 2. Operator User
        op_user, created = User.objects.get_or_create(
            phone="+923000000099",
            defaults={
                "email": "operator@busms.com",
                "role": UserRole.OPERATOR,
                "is_staff": True,
                "is_verified": True,
            }
        )
        if created:
            op_user.set_password("OperatorPassword123!")
            op_user.save()
            PassengerProfile.objects.create(
                user=op_user,
                full_name="Fleet Operations Manager",
                gender=Gender.MALE
            )
            self.stdout.write(self.style.SUCCESS("Created Operator: +923000000099 / OperatorPassword123!"))
        else:
            self.stdout.write("Operator user already exists.")

        # 3. Passenger User
        p_user, created = User.objects.get_or_create(
            phone="+923000000088",
            defaults={
                "email": "passenger@busms.com",
                "role": UserRole.PASSENGER,
                "is_verified": True,
            }
        )
        if created:
            p_user.set_password("PassengerPassword123!")
            p_user.save()
            PassengerProfile.objects.create(
                user=p_user,
                full_name="Zainab Bibi",
                gender=Gender.FEMALE
            )
            self.stdout.write(self.style.SUCCESS("Created Passenger: +923000000088 / PassengerPassword123!"))
        else:
            self.stdout.write("Passenger user already exists.")

        # 4. Buses & Seat Matrices
        bus1, created = Bus.objects.get_or_create(
            registration_number="ISB-101",
            defaults={
                "name": "Faisal Movers Business Class",
                "bus_type": BusType.LUXURY,
                "status": BusStatus.ACTIVE,
                "amenities": ["WiFi", "Refreshments", "USB Ports", "Charging Sockets", "AC"],
            }
        )
        if created or bus1.seats.count() == 0:
            seats1 = generate_bus_seat_matrix(
                bus=bus1,
                layout_type='2x1',
                rows=10,
                ladies_rows=0
            )
            self.stdout.write(self.style.SUCCESS(f"Created Bus ISB-101 (2x1 Luxury) with {len(seats1)} seats"))

        bus2, created = Bus.objects.get_or_create(
            registration_number="LHR-202",
            defaults={
                "name": "Daewoo Express Standard",
                "bus_type": BusType.STANDARD_AC,
                "status": BusStatus.ACTIVE,
                "amenities": ["AC", "Audio System", "Reading Light"],
            }
        )
        if created or bus2.seats.count() == 0:
            seats2 = generate_bus_seat_matrix(
                bus=bus2,
                layout_type='2x2',
                rows=10,
                ladies_rows=0
            )
            self.stdout.write(self.style.SUCCESS(f"Created Bus LHR-202 (2x2 Standard) with {len(seats2)} seats"))

        # 5. Drivers
        driver1, _ = Driver.objects.get_or_create(
            license_number="PK-LHR-88219",
            defaults={
                "full_name": "Muhammad Aslam",
                "phone": "+923011112222",
                "experience_years": 8,
                "status": CrewStatus.AVAILABLE
            }
        )
        driver2, _ = Driver.objects.get_or_create(
            license_number="PK-ISB-77124",
            defaults={
                "full_name": "Tariq Mahmood",
                "phone": "+923013334444",
                "experience_years": 12,
                "status": CrewStatus.AVAILABLE
            }
        )
        self.stdout.write(self.style.SUCCESS("Ensured sample drivers exist."))

        # 6. Conductors
        conductor1, _ = Conductor.objects.get_or_create(
            employee_id="CND-1001",
            defaults={
                "full_name": "Rashid Minhas",
                "phone": "+923015556666",
                "status": CrewStatus.AVAILABLE
            }
        )
        conductor2, _ = Conductor.objects.get_or_create(
            employee_id="CND-1002",
            defaults={
                "full_name": "Bilal Ahmed",
                "phone": "+923017778888",
                "status": CrewStatus.AVAILABLE
            }
        )
        self.stdout.write(self.style.SUCCESS("Ensured sample conductors exist."))

        # 7. Terminals
        t_lhr, _ = Terminal.objects.get_or_create(
            code="LHR-01",
            defaults={
                "name": "Lahore Thokar Terminal",
                "city": "Lahore",
                "address": "Thokar Niaz Baig, Multan Road",
                "latitude": Decimal('31.469700'),
                "longitude": Decimal('74.272800'),
                "contact_number": "+9242111222333"
            }
        )
        t_bhr, _ = Terminal.objects.get_or_create(
            code="BHR-01",
            defaults={
                "name": "Bhera Service Area Station",
                "city": "Bhera",
                "address": "M2 Motorway Service Area Interchange",
                "latitude": Decimal('32.482000'),
                "longitude": Decimal('72.910000'),
                "contact_number": "+9248111222444"
            }
        )
        t_isb, _ = Terminal.objects.get_or_create(
            code="ISB-01",
            defaults={
                "name": "Islamabad Faizabad Terminal",
                "city": "Islamabad",
                "address": "Faizabad Interchange, I-8",
                "latitude": Decimal('33.663100'),
                "longitude": Decimal('73.084700'),
                "contact_number": "+9251111222555"
            }
        )
        t_pew, _ = Terminal.objects.get_or_create(
            code="PEW-01",
            defaults={
                "name": "Peshawar Haji Camp Terminal",
                "city": "Peshawar",
                "address": "GT Road, Near Haji Camp",
                "latitude": Decimal('34.015000'),
                "longitude": Decimal('71.580500'),
                "contact_number": "+9291111222666"
            }
        )
        t_fsd, _ = Terminal.objects.get_or_create(
            code="FSD-01",
            defaults={
                "name": "Faisalabad Kohistan Terminal",
                "city": "Faisalabad",
                "address": "Near Railway Station",
                "latitude": Decimal('31.418700'),
                "longitude": Decimal('73.079100'),
                "contact_number": "+9241111222777"
            }
        )
        self.stdout.write(self.style.SUCCESS("Ensured sample terminals exist."))

        # 8. Routes
        if not Route.objects.filter(code="LHR-ISB-M2").exists():
            RouteService.create_route(
                code="LHR-ISB-M2",
                name="Lahore to Islamabad via M2 Motorway",
                origin=t_lhr,
                destination=t_isb,
                stops_data=[
                    {"terminal": t_lhr.id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
                    {"terminal": t_bhr.id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
                    {"terminal": t_isb.id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
                ],
                description="Fastest motorway corridor connecting Lahore and the federal capital."
            )
            self.stdout.write(self.style.SUCCESS("Created Route LHR-ISB-M2 (3 stops, 380 km)"))

        if not Route.objects.filter(code="LHR-PEW-EXP").exists():
            RouteService.create_route(
                code="LHR-PEW-EXP",
                name="Grand Northern Express (Lahore to Peshawar via M2/M1)",
                origin=t_lhr,
                destination=t_pew,
                stops_data=[
                    {"terminal": t_lhr.id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
                    {"terminal": t_bhr.id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
                    {"terminal": t_isb.id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
                    {"terminal": t_pew.id, "stop_order": 3, "distance_from_origin_km": 540.0, "offset_minutes": 380},
                ],
                description="Long-distance corridor linking Punjab and Khyber Pakhtunkhwa."
            )
            self.stdout.write(self.style.SUCCESS("Created Route LHR-PEW-EXP (4 stops, 540 km)"))

        if not Route.objects.filter(code="LHR-FSD-DIR").exists():
            RouteService.create_route(
                code="LHR-FSD-DIR",
                name="Lahore to Faisalabad Direct",
                origin=t_lhr,
                destination=t_fsd,
                total_distance_km=Decimal('180.00'),
                estimated_duration_minutes=130,
                description="Direct economic corridor between Lahore and the Manchester of Pakistan."
            )
            self.stdout.write(self.style.SUCCESS("Created Route LHR-FSD-DIR (2 stops, 180 km)"))

        # 9. Scheduled Trips
        route_m2 = Route.objects.get(code="LHR-ISB-M2")
        tomorrow = timezone.now() + timedelta(days=1)
        trip_morning_dep = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
        trip_morning_arr = trip_morning_dep + timedelta(hours=4, minutes=20)

        if not Trip.objects.filter(trip_number="TRIP-DEMO-01").exists():
            trip1 = TripScheduleService.create_trip(
                route=route_m2,
                bus=bus1,
                driver=driver1,
                conductor=conductor1,
                departure_datetime=trip_morning_dep,
                arrival_datetime=trip_morning_arr,
                base_fare=Decimal('2500.00'),
                trip_number="TRIP-DEMO-01"
            )
            # Seat Block on Trip 1
            seat_1a = bus1.seats.filter(seat_number="1A").first()
            if seat_1a:
                TripSeatBlockService.block_seat(
                    trip=trip1,
                    seat=seat_1a,
                    reason="VIP Protocol Hold",
                    user=admin_user
                )
            self.stdout.write(self.style.SUCCESS("Created Trip TRIP-DEMO-01 (ISB-101 Morning Express)"))

        trip_afternoon_dep = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
        trip_afternoon_arr = trip_afternoon_dep + timedelta(hours=4, minutes=30)
        if not Trip.objects.filter(trip_number="TRIP-DEMO-02").exists():
            TripScheduleService.create_trip(
                route=route_m2,
                bus=bus2,
                driver=driver2,
                conductor=conductor2,
                departure_datetime=trip_afternoon_dep,
                arrival_datetime=trip_afternoon_arr,
                base_fare=Decimal('2000.00'),
                trip_number="TRIP-DEMO-02"
            )
            self.stdout.write(self.style.SUCCESS("Created Trip TRIP-DEMO-02 (LHR-202 Afternoon Express)"))

        # 10. Segment Fare Rules
        s_lhr = route_m2.stops.get(stop_order=0)
        s_bhr = route_m2.stops.get(stop_order=1)
        s_isb = route_m2.stops.get(stop_order=2)

        FareService.set_fare_rule(route=route_m2, from_stop=s_lhr, to_stop=s_bhr, fare_amount=Decimal('1200.00'))
        FareService.set_fare_rule(route=route_m2, from_stop=s_bhr, to_stop=s_isb, fare_amount=Decimal('1100.00'))
        FareService.set_fare_rule(route=route_m2, from_stop=s_lhr, to_stop=s_isb, fare_amount=Decimal('2200.00'))
        self.stdout.write(self.style.SUCCESS("Configured segment FareRules for Route LHR-ISB-M2"))

        self.stdout.write(self.style.SUCCESS("--- Seeding Completed Successfully! ---"))
