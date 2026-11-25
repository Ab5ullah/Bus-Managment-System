"""
Shared bus layout adjacency geometry and gender seating helpers.
Computes same-row side-by-side seat pairs while respecting center aisle boundaries.
"""
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence


def get_adjacent_columns(column: int, row_columns: Sequence[int]) -> List[int]:
    """
    Returns column numbers that sit directly next to `column` without an aisle between them.
    - 2x2 layout (cols 1..4): (1, 2) | aisle | (3, 4)
    - 2x1 / sleeper_2x1 (cols 1..3): (1, 2) | aisle | (3 solo)
    - 1x1 layout (cols 1..2): (1 solo) | aisle | (2 solo) -> []
    """
    max_col = max(row_columns) if row_columns else column
    if max_col == 4:
        pair_map = {1: [2], 2: [1], 3: [4], 4: [3]}
        return pair_map.get(column, [])
    if max_col == 3:
        pair_map = {1: [2], 2: [1]}
        return pair_map.get(column, [])
    if max_col <= 2:
        return []

    # Fallback for wider custom layouts (>4 cols): pair (1,2), (3,4), ...
    partner = column + 1 if (column % 2 == 1) else column - 1
    return [partner] if partner in row_columns else []


def find_adjacent_seats_map(seats: Iterable) -> Dict:
    """
    Groups physical Seat objects by (deck, row) and maps each seat.id
    to the list of directly adjacent Seat objects in the same row/deck.
    """
    by_row = defaultdict(list)
    seat_list = list(seats)
    for s in seat_list:
        by_row[(s.deck, s.row)].append(s)

    adj_map: Dict = {}
    for (_, _), row_seats in by_row.items():
        cols = [s.column for s in row_seats]
        by_col = {s.column: s for s in row_seats}
        for s in row_seats:
            adj_cols = get_adjacent_columns(s.column, cols)
            adj_map[s.id] = [by_col[c] for c in adj_cols if c in by_col]

    return adj_map
