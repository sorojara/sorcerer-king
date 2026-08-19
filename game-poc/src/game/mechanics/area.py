"""
mechanics/area.py — Stage 6: canonical area-of-effect expansion
==================================================================

Every Trap and Spell has a single, canonical area of effect described by
two card-level fields (see cards/card.py):

    radius   int    — how far the area extends from its center
    shape    str    — "square" (default) | "row" | "column"

This module is the ONE place that turns (center, radius, shape) into a
concrete list of board squares.  Effect handlers never do their own area
math — the caller (trap trigger detection, ActivateSpell dispatch) expands
the area first and then resolves the effect once per affected square/unit.
This is the "TARGET SELECTION" step of the canonical effect contract:

    TRIGGER → CONDITION → TARGET SELECTION → EFFECT → DURATION → EVENTS

"square"  radius R → the (2R+1)×(2R+1) block centred on ``center``
                      (radius 0 = just the center square itself;
                       radius 1 = the familiar 3×3 "activation area").
"row"     → every square on ``center``'s rank (the whole row, 8 squares).
"column"  → every square on ``center``'s file (the whole column, 8 squares).
            ``radius`` is ignored for row/column — the whole line is
            always affected, per veil_of_stillness's design.
"""

from __future__ import annotations

from game.chess.pieces import Position


def expand_area(center: Position, radius: int, shape: str = "square") -> list[Position]:
    """Return every board square covered by this area, including ``center``."""
    if shape == "row":
        return [Position(f, center.rank) for f in range(8)]
    if shape == "column":
        return [Position(center.file, r) for r in range(8)]

    # "square" (default): (2*radius + 1)^2 block, clipped to the board.
    squares: list[Position] = []
    for df in range(-radius, radius + 1):
        for dr in range(-radius, radius + 1):
            f, r = center.file + df, center.rank + dr
            if 0 <= f <= 7 and 0 <= r <= 7:
                squares.append(Position(f, r))
    return squares


def in_area(point: Position, center: Position, radius: int, shape: str = "square") -> bool:
    """True if ``point`` falls inside the area described by (center, radius, shape)."""
    if shape == "row":
        return point.rank == center.rank
    if shape == "column":
        return point.file == center.file
    return abs(point.file - center.file) <= radius and abs(point.rank - center.rank) <= radius
