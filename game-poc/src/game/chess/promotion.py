"""
chess/promotion.py — Pawn promotion helpers (Stage 1)
======================================================

Resolves a pawn promotion on the board.

Called by the RulesEngine when a PromotePawn action is executed.
Kept separate so the promotion logic is independently testable.
"""

from __future__ import annotations

from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import PieceType


VALID_PROMOTION_TYPES: frozenset[str] = frozenset(
    {"queen", "rook", "bishop", "knight"}
)


def apply_promotion(
    board: BoardState,
    position: Position,
    new_piece_type_str: str,
) -> tuple[str, str]:
    """
    Replace the pawn at ``position`` with a piece of ``new_piece_type_str``.

    Returns (old_piece_id, new_piece_id) for event construction.

    Raises ValueError if the target piece type is invalid or no pawn is present.
    """
    if new_piece_type_str not in VALID_PROMOTION_TYPES:
        raise ValueError(
            f"Invalid promotion piece type: {new_piece_type_str!r}. "
            f"Valid options: {sorted(VALID_PROMOTION_TYPES)}"
        )

    unit = board.get_unit(position)
    if unit is None:
        raise ValueError(f"No piece at {position} to promote.")
    if unit.piece.piece_type != PieceType.PAWN:
        raise ValueError(
            f"Piece at {position} is a {unit.piece.piece_type.value}, not a pawn."
        )

    old_id = unit.piece.id
    new_type = PieceType(new_piece_type_str)
    new_id = f"{unit.owner}-{new_piece_type_str}-promoted-{position.to_algebraic()}"

    new_piece = ChessPiece(id=new_id, owner=unit.owner, piece_type=new_type)
    # Preserve any active Monster — the vessel upgrades under it
    new_unit = UnitInstance(piece=new_piece, monster_id=unit.monster_id)
    board.place_unit(position, new_unit)

    return old_id, new_id


def is_promotion_rank(position: Position, owner: str) -> bool:
    """Return True if ``position`` is a promotion rank for ``owner``."""
    return (owner == "white" and position.rank == 7) or (
        owner == "black" and position.rank == 0
    )
