"""
Piece Model — Stage 0
=====================

Separation of concerns:

    ChessPiece  — the chess identity (type, owner, canonical ID)
                  Never changes. Even after transformation.

    UnitInstance — the battlefield entity that lives on a square.
                   May wrap a ChessPiece alone, or a ChessPiece + Monster.

This separation makes "Dismiss Monster / restore Vessel" trivial:

    unit.monster_id = None   →  the Vessel is restored, piece identity intact.

It also means that when a Unit is captured the engine knows both the Monster
that was destroyed AND the Vessel (chess piece) that was lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game.core.phases import PieceType, VALID_VESSEL_TYPES

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Position
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class Position:
    """
    Immutable board coordinate.

    ``file`` is the column (0–7, a–h) and ``rank`` is the row (0–7, 1–8).
    Both are zero-indexed internally.  Helpers convert to/from algebraic
    notation (e.g. "e4" → Position(4, 3)).
    """

    file: int  # 0 = a-file, 7 = h-file
    rank: int  # 0 = rank 1, 7 = rank 8

    def __post_init__(self) -> None:
        if not (0 <= self.file <= 7 and 0 <= self.rank <= 7):
            raise ValueError(
                f"Position out of range: file={self.file}, rank={self.rank}"
            )

    # Hand-rolled instead of the dataclass-generated versions: a profile of
    # AI Stage 2's tree search (game/ai/search.py) showed Position's
    # __eq__/__hash__ among the hottest functions in the whole engine —
    # board.squares and every search node's unit map are dict[Position, …],
    # so these run millions of times per decision. The generated versions
    # build and hash a 2-tuple every call; file/rank are both 0–7, so
    # file * 8 + rank is a perfect (collision-free) hash computed directly
    # from the two ints, and __eq__ skips the tuple allocation entirely.
    # Defining these in the class body suppresses dataclass's own
    # generated __eq__/__hash__ (see the dataclasses docs on
    # already-defined dunders) — the ``order=True`` comparison methods
    # (__lt__ etc.) are untouched and still dataclass-generated.
    def __eq__(self, other: object) -> bool:
        if other.__class__ is not Position:
            return NotImplemented
        return self.file == other.file and self.rank == other.rank

    def __hash__(self) -> int:
        return self.file * 8 + self.rank

    @staticmethod
    def from_algebraic(notation: str) -> "Position":
        """Convert e.g. 'e4' → Position(4, 3)."""
        if len(notation) != 2:
            raise ValueError(f"Invalid algebraic notation: {notation!r}")
        file_char = notation[0].lower()
        rank_char = notation[1]
        if file_char not in "abcdefgh" or rank_char not in "12345678":
            raise ValueError(f"Invalid algebraic notation: {notation!r}")
        return Position(file=ord(file_char) - ord("a"), rank=int(rank_char) - 1)

    def to_algebraic(self) -> str:
        """Convert Position(4, 3) → 'e4'."""
        return f"{chr(ord('a') + self.file)}{self.rank + 1}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"Position({self.to_algebraic()!r})"


# ─────────────────────────────────────────────────────────────────────────────
# ChessPiece
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChessPiece:
    """
    The immutable identity of a chess piece.

    ``id`` is a canonical string like ``"white-pawn-a2"`` or ``"black-queen"``.
    It never changes across transformations — it is the piece's soul.
    """

    id: str
    owner: str        # "white" or "black"
    piece_type: PieceType

    @property
    def is_valid_vessel(self) -> bool:
        """Whether this piece type can host a Monster."""
        return self.piece_type in VALID_VESSEL_TYPES

    @property
    def is_king(self) -> bool:
        return self.piece_type == PieceType.KING


# ─────────────────────────────────────────────────────────────────────────────
# UnitInstance
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UnitInstance:
    """
    The entity that actually occupies a square.

    A plain chess piece has ``monster_id = None``.
    A Monster-transformed piece has ``monster_id`` set to a card ID.

    ``statuses`` holds short string tags applied by effects:
        "frozen", "enraged", "shielded", "marked", …

    ``builder_available`` tracks the once-per-match build right for Pawns.
    Only Pawns start with this set to True; non-Pawns ignore it.
    """

    piece: ChessPiece
    monster_id: str | None = None
    statuses: list[str] = field(default_factory=list)
    builder_available: bool = field(init=False)

    def __post_init__(self) -> None:
        # Only Pawns begin with builder rights.
        self.builder_available = (
            self.piece.piece_type == PieceType.PAWN
        )

    @property
    def is_monster(self) -> bool:
        return self.monster_id is not None

    @property
    def owner(self) -> str:
        return self.piece.owner

    def has_status(self, status: str) -> bool:
        return status in self.statuses

    def add_status(self, status: str) -> None:
        if status not in self.statuses:
            self.statuses.append(status)

    def remove_status(self, status: str) -> None:
        self.statuses = [s for s in self.statuses if s != status]
