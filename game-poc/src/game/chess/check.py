"""
chess/check.py — Check / stalemate / checkmate detection helpers (Stage 1)
===========================================================================

Thin wrappers over movement.py that name the detection concepts clearly.
Kept separate so other modules can import without a circular dependency.

These are the functions the RulesEngine calls; movement.py provides the
underlying logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.chess.board import BoardState
from game.chess.movement import (
    can_castle_kingside,
    can_castle_queenside,
    has_any_legal_move,
    is_in_check,
)
from game.chess.pieces import Position

if TYPE_CHECKING:
    from game.chess.chess_state import CastlingRights


def is_checkmate(
    board: BoardState,
    player_id: str,
    en_passant_target: Position | None = None,
    castling_rights: "CastlingRights | None" = None,
) -> bool:
    """
    Return True if ``player_id`` is in check and has no legal moves.
    In this game, checkmate triggers a Final Duel (SIEGE), not an instant win.
    """
    return is_in_check(board, player_id) and not has_any_legal_move(
        board, player_id, en_passant_target, castling_rights
    )


def is_stalemate(
    board: BoardState,
    player_id: str,
    en_passant_target: Position | None = None,
    castling_rights: "CastlingRights | None" = None,
) -> bool:
    """
    Return True if ``player_id`` is NOT in check but has no legal moves.
    Stalemate triggers a Final Duel (LAST_STAND) rather than a draw.
    """
    return (not is_in_check(board, player_id)) and not has_any_legal_move(
        board, player_id, en_passant_target, castling_rights
    )
