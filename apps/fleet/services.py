"""
Fleet Service Layer: automated seat matrix generator, bus orchestration, and seat attributes.
"""
from django.db import transaction
from common.constants import SeatType
from .models import Bus, Seat


class SeatMatrixService:
    """Orchestrates algorithmic generation and regeneration of seat grids."""

    COLUMN_MAPS = {
        "2x2": [
            ("A", 1, SeatType.WINDOW),
            ("B", 2, SeatType.AISLE),
            ("C", 3, SeatType.AISLE),
            ("D", 4, SeatType.WINDOW),
        ],
        "2x1": [
            ("A", 1, SeatType.WINDOW),
            ("B", 2, SeatType.AISLE),
            ("C", 3, SeatType.WINDOW),
        ],
        "1x1": [
            ("A", 1, SeatType.WINDOW),
            ("B", 2, SeatType.WINDOW),
        ],
        "sleeper_2x1": [
            ("A", 1, SeatType.SLEEPER_LOWER),
            ("B", 2, SeatType.SLEEPER_LOWER),
            ("C", 3, SeatType.SLEEPER_LOWER),
        ]
    }

    @classmethod
    @transaction.atomic
    def generate_seat_matrix(
        cls,
        bus: Bus,
        layout_type: str = "2x2",
        rows: int = 10,
        deck_count: int = 1,
        ladies_rows: int = 0
    ) -> list[Seat]:
        """
        Generates physical seat records based on grid specifications and bulk-inserts them.
        """
        cols = cls.COLUMN_MAPS.get(layout_type, cls.COLUMN_MAPS["2x2"])
        seats_to_create = []

        # Remove existing seats if regenerating
        Seat.objects.filter(bus=bus).delete()

        for deck in range(1, deck_count + 1):
            for row in range(1, rows + 1):
                if isinstance(ladies_rows, (list, tuple, set)):
                    is_ladies = (deck == 1 and row in ladies_rows)
                else:
                    is_ladies = (deck == 1 and row <= ladies_rows)

                for col_letter, col_num, base_type in cols:
                    seat_type = base_type
                    if layout_type == "sleeper_2x1" and deck == 2:
                        seat_type = SeatType.SLEEPER_UPPER

                    deck_prefix = f"U{row}" if deck == 2 else f"{row}"
                    seat_number = f"{deck_prefix}{col_letter}"

                    seats_to_create.append(
                        Seat(
                            bus=bus,
                            seat_number=seat_number,
                            row=row,
                            column=col_num,
                            deck=deck,
                            seat_type=seat_type,
                            is_ladies_only=is_ladies,
                            is_active=True
                        )
                    )

        created_seats = Seat.objects.bulk_create(seats_to_create)
        bus.total_seats = len(created_seats)
        bus.save(update_fields=['total_seats'])
        return created_seats


class BusService:
    """Bus management domain operations."""

    @classmethod
    @transaction.atomic
    def create_bus(cls, bus_data: dict, layout_spec: dict = None) -> Bus:
        bus = Bus.objects.create(**bus_data)
        if layout_spec:
            SeatMatrixService.generate_seat_matrix(
                bus=bus,
                layout_type=layout_spec.get('layout_type', '2x2'),
                rows=layout_spec.get('rows', 10),
                deck_count=layout_spec.get('deck_count', 1),
                ladies_rows=layout_spec.get('ladies_rows', 0)
            )
        return bus

    @classmethod
    def update_seat(cls, seat: Seat, update_data: dict) -> Seat:
        for key, value in update_data.items():
            setattr(seat, key, value)
        seat.save()
        return seat


# Convenience functional alias
def generate_bus_seat_matrix(
    bus: Bus,
    layout_type: str = "2x2",
    rows: int = 10,
    deck_count: int = 1,
    ladies_rows: int = 0
) -> list[Seat]:
    return SeatMatrixService.generate_seat_matrix(
        bus=bus,
        layout_type=layout_type,
        rows=rows,
        deck_count=deck_count,
        ladies_rows=ladies_rows
    )
