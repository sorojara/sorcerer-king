"""
Board State — Stage 0
=====================

Each square knows everything relevant about that location:

    SquareState.unit          — the UnitInstance currently occupying it (or None)
    SquareState.trap_ids      — IDs of TrapInstances whose radius covers this square
    SquareState.building_id   — ID of a BuildingInstance placed here (or None)
    SquareState.territory_owner — "white" | "black" | None  (placeholder, Stage 9)
    SquareState.temporary_effects — short string tags from Spell / effect resolution

BoardState is a thin dict-based wrapper over 64 SquareStates.
All higher-level queries go through the board (e.g. find_unit, find_king).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from game.chess.pieces import Position, UnitInstance
from game.core.phases import PieceType


# ─────────────────────────────────────────────────────────────────────────────
# SquareState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SquareState:
    """
    Represents everything relevant about a single board square.

    Design note: squares are **not** purely containers for pieces.
    Traps, Buildings, Territory, and temporary Spell effects all attach here.
    """

    position: Position

    unit: UnitInstance | None = None

    # IDs of TrapInstances whose activation radius includes this square.
    trap_ids: list[str] = field(default_factory=list)

    # ID of a BuildingInstance occupying this square (if any).
    building_id: str | None = None

    # Territorial control (set by Building/Ritual effects). Stage 9 placeholder.
    territory_owner: str | None = None

    # Short string tags from active Spell effects: "blocked", "slowed", …
    temporary_effects: list[str] = field(default_factory=list)

    def is_occupied(self) -> bool:
        return self.unit is not None

    def has_effect(self, effect: str) -> bool:
        return effect in self.temporary_effects

    def add_effect(self, effect: str) -> None:
        if effect not in self.temporary_effects:
            self.temporary_effects.append(effect)

    def remove_effect(self, effect: str) -> None:
        self.temporary_effects = [e for e in self.temporary_effects if e != effect]


# ─────────────────────────────────────────────────────────────────────────────
# BoardState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BoardState:
    """
    The full 8×8 board, represented as a dict of Position → SquareState.

    Invariant: all 64 squares are always present.
    """

    squares: dict[Position, SquareState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure all 64 squares exist.
        for file in range(8):
            for rank in range(8):
                pos = Position(file, rank)
                if pos not in self.squares:
                    self.squares[pos] = SquareState(position=pos)

    def get_square(self, pos: Position) -> SquareState:
        return self.squares[pos]

    def get_unit(self, pos: Position) -> UnitInstance | None:
        return self.squares[pos].unit

    def place_unit(self, pos: Position, unit: UnitInstance) -> None:
        self.squares[pos].unit = unit

    def remove_unit(self, pos: Position) -> UnitInstance | None:
        sq = self.squares[pos]
        unit = sq.unit
        sq.unit = None
        return unit

    def move_unit(self, source: Position, target: Position) -> UnitInstance | None:
        """
        Move unit from source to target.
        Returns any unit that was previously occupying target (a capture).
        """
        unit = self.remove_unit(source)
        if unit is None:
            raise ValueError(f"No unit at source square {source}")
        captured = self.remove_unit(target)
        self.place_unit(target, unit)
        return captured

    # ── Query helpers ─────────────────────────────────────────────────────

    def find_unit_by_piece_id(self, piece_id: str) -> tuple[Position, UnitInstance] | None:
        """Return the (position, unit) for the given piece ID, or None."""
        for pos, sq in self.squares.items():
            if sq.unit is not None and sq.unit.piece.id == piece_id:
                return pos, sq.unit
        return None

    def find_king(self, owner: str) -> tuple[Position, UnitInstance] | None:
        """Return the (position, unit) for the owner's King, or None."""
        for pos, sq in self.squares.items():
            if (
                sq.unit is not None
                and sq.unit.owner == owner
                and sq.unit.piece.piece_type == PieceType.KING
            ):
                return pos, sq.unit
        return None

    def all_units_for(self, owner: str) -> list[tuple[Position, UnitInstance]]:
        """Return all (position, unit) pairs belonging to owner."""
        return [
            (pos, sq.unit)
            for pos, sq in self.squares.items()
            if sq.unit is not None and sq.unit.owner == owner
        ]

    def all_units(self) -> list[tuple[Position, UnitInstance]]:
        """Return all (position, unit) pairs on the board."""
        return [
            (pos, sq.unit)
            for pos, sq in self.squares.items()
            if sq.unit is not None
        ]

    @staticmethod
    def make_standard_start() -> "BoardState":
        """
        Create a BoardState with the standard chess starting position.
        Piece IDs follow the pattern: ``"{owner}-{type}-{file}"``
        (e.g. ``"white-pawn-a"``, ``"black-knight-g"``).
        """
        from game.chess.pieces import ChessPiece, UnitInstance
        from game.core.phases import PieceType

        board = BoardState()

        def place(owner: str, ptype: PieceType, file: int, rank: int, pid: str) -> None:
            piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
            unit = UnitInstance(piece=piece)
            board.place_unit(Position(file, rank), unit)

        back_row: list[PieceType] = [
            PieceType.ROOK, PieceType.KNIGHT, PieceType.BISHOP, PieceType.QUEEN,
            PieceType.KING, PieceType.BISHOP, PieceType.KNIGHT, PieceType.ROOK,
        ]
        file_names = list("abcdefgh")

        for f, ptype in enumerate(back_row):
            name = ptype.value
            place("white", ptype, f, 0, f"white-{name}-{file_names[f]}")
            place("black", ptype, f, 7, f"black-{name}-{file_names[f]}")

        for f in range(8):
            place("white", PieceType.PAWN, f, 1, f"white-pawn-{file_names[f]}")
            place("black", PieceType.PAWN, f, 6, f"black-pawn-{file_names[f]}")

        return board
