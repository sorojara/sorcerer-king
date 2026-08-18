"""
chess/state.py — Chess-specific runtime state (Stage 1)
=========================================================

Separates chess-rule bookkeeping from the card/deck/king-pool concerns
in PlayerState.

ChessState holds:
    • castling_rights   — which castling moves the player may still perform
    • en_passant_target — if the opponent's last move was a double pawn push,
                          this is the square that may be captured en passant
                          (stored at the GameState level, not per-player)

These are kept separate so they can be cleanly zeroed/restored in
simulation / tree-search without touching card state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from game.chess.pieces import Position


@dataclass
class CastlingRights:
    """
    Tracks the four possible castling rights.

    Each right starts True and becomes permanently False once:
        • The King moves, OR
        • The relevant Rook moves, OR
        • The relevant Rook is captured.

    No position-check here; legality (clear path, not through check) is
    evaluated at move-time in movement.py.
    """

    kingside: bool = True    # O-O
    queenside: bool = True   # O-O-O

    def revoke_all(self) -> None:
        self.kingside = False
        self.queenside = False

    def has_any(self) -> bool:
        return self.kingside or self.queenside
