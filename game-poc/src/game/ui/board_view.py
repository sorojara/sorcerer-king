"""
ui/board_view.py — Board rendering (Stage 3)
=============================================

Draws:
  • 8×8 board squares with rank/file coordinate labels.
  • All chess pieces as Unicode glyphs (no image files required).
  • Selected-piece highlight (green tint on source square).
  • Legal-move dots / capture rings on destination squares.
  • Legal castling destinations (blue dot).
  • Check highlight (red tint on the King's square).

Design rules:
  • This module is PURE RENDERING — it never mutates game state.
  • It receives a PublicBoardState (from an Observation) + auxiliary
    display data passed by the AppController.  It never touches GameState.
  • All positions are in board coordinates (file 0–7, rank 0–7, rank 0 = rank 1).
  • Screen y=0 is the top; rank 7 (rank 8) is drawn at the top when
    white is at the bottom (``flip=False``).

Piece glyphs (Unicode chess symbols):
  White: ♔ ♕ ♖ ♗ ♘ ♙   (U+2654–U+2659)
  Black: ♚ ♛ ♜ ♝ ♞ ♟   (U+265A–U+265F)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from game.chess.pieces import Position
from game.core.actions import Castle, MovePiece
from game.core.phases import PieceType
from game.ui.colors import (
    ACTIVATABLE_DOT,
    BLACK_PIECE,
    BLACK_PIECE_SHADOW,
    CASTLE_DOT,
    CHECK_TINT,
    COORD_DARK,
    COORD_LIGHT,
    DARK_SQUARE,
    INSPECT_TINT,
    LEGAL_CAPTURE,
    LEGAL_DOT,
    LIGHT_SQUARE,
    SELECTED_TINT,
    SUMMON_VESSEL_DOT,
    SUMMON_VESSEL_TINT,
    WHITE_PIECE,
    WHITE_PIECE_SHADOW,
)

if TYPE_CHECKING:
    from game.core.observation import Observation


# ── Constants ──────────────────────────────────────────────────────────────
SQUARE_SIZE: int = 80       # pixels per square
BOARD_OFFSET_X: int = 0     # left edge of board on the surface
BOARD_OFFSET_Y: int = 0     # top edge of board on the surface
BOARD_PIXEL_SIZE: int = SQUARE_SIZE * 8  # 640 px

# Unicode piece glyphs: piece_type_value → (white_glyph, black_glyph)
_GLYPHS: dict[str, tuple[str, str]] = {
    "king":   ("♔", "♚"),
    "queen":  ("♕", "♛"),
    "rook":   ("♖", "♜"),
    "bishop": ("♗", "♝"),
    "knight": ("♘", "♞"),
    "pawn":   ("♙", "♟"),
}


def _sq_to_screen(pos: Position, flip: bool = False) -> tuple[int, int]:
    """
    Convert a board Position to the top-left pixel of its square.

    ``flip=False``  → rank 0 (rank 1) at bottom  (white perspective).
    ``flip=True``   → rank 0 at top               (black perspective, future).
    """
    x = BOARD_OFFSET_X + pos.file * SQUARE_SIZE
    if flip:
        y = BOARD_OFFSET_Y + pos.rank * SQUARE_SIZE
    else:
        y = BOARD_OFFSET_Y + (7 - pos.rank) * SQUARE_SIZE
    return x, y


def _screen_to_pos(mx: int, my: int, flip: bool = False) -> Position | None:
    """
    Convert a screen pixel (mx, my) to a board Position.
    Returns None if the click is outside the board area.
    """
    bx = mx - BOARD_OFFSET_X
    by = my - BOARD_OFFSET_Y
    if not (0 <= bx < BOARD_PIXEL_SIZE and 0 <= by < BOARD_PIXEL_SIZE):
        return None
    file = bx // SQUARE_SIZE
    if flip:
        rank = by // SQUARE_SIZE
    else:
        rank = 7 - (by // SQUARE_SIZE)
    try:
        return Position(file, rank)
    except ValueError:
        return None


class BoardView:
    """
    Stateless board renderer.

    Usage:
        view = BoardView(surface, font_large, font_small)
        view.draw(observation, selected_pos, legal_dests, in_check_player)
    """

    def __init__(
        self,
        surface: pygame.Surface,
        font_large: Any,   # _ft.Font — pygame.font not used (Py 3.14 bug)
        font_small: Any,
        flip: bool = False,
    ) -> None:
        self._surface = surface
        self._font_large = font_large
        self._font_small = font_small
        self._flip = flip

        # Pre-allocate overlay surface for alpha blending
        self._overlay = pygame.Surface(
            (SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA
        )

    # ── Public render entry point ─────────────────────────────────────────

    def draw(
        self,
        observation: "Observation",
        selected_pos: Position | None,
        legal_dests: list[Position],
        castle_dests: list[Position],
        checked_player: str | None,
        summon_vessel_dests: list[Position] | None = None,
        inspect_pos: Position | None = None,
    ) -> None:
        """
        Render the full board onto ``self._surface``.

        Parameters
        ----------
        observation        : current player observation (provides unit list)
        selected_pos       : currently selected source square (or None)
        legal_dests        : legal destination squares for the selected piece
        castle_dests       : legal castling destination squares (g1/c1 etc.)
        checked_player     : "white" or "black" if in check, else None
        summon_vessel_dests: Stage 5 — valid vessel squares for the held monster card
        inspect_pos        : Stage 5 — unit being inspected (purple tint, info panel)
        """
        self._draw_squares(observation, selected_pos, legal_dests, castle_dests,
                           checked_player, summon_vessel_dests or [], inspect_pos)
        self._draw_coordinates()
        self._draw_pieces(observation)

    # ── Private draw helpers ──────────────────────────────────────────────

    def _draw_squares(
        self,
        obs: "Observation",
        selected_pos: Position | None,
        legal_dests: list[Position],
        castle_dests: list[Position],
        checked_player: str | None,
        summon_vessel_dests: list[Position] | None = None,
        inspect_pos: Position | None = None,
    ) -> None:
        """Draw base square colors, then overlays for selection/legal/check/summon/inspect."""
        # Build a quick lookup: position → unit for check-king detection
        king_pos: Position | None = None
        if checked_player is not None:
            for unit_info in obs.board.units:
                if (
                    unit_info.piece_type == "king"
                    and unit_info.owner == checked_player
                ):
                    king_pos = unit_info.position
                    break

        vessel_set: set[Position] = set(summon_vessel_dests or [])

        for file in range(8):
            for rank in range(8):
                pos = Position(file, rank)
                is_light = (file + rank) % 2 == 0
                color = LIGHT_SQUARE if is_light else DARK_SQUARE
                sx, sy = _sq_to_screen(pos, self._flip)
                rect = pygame.Rect(sx, sy, SQUARE_SIZE, SQUARE_SIZE)
                pygame.draw.rect(self._surface, color, rect)

                # Check highlight (king square)
                if king_pos is not None and pos == king_pos:
                    self._overlay.fill(CHECK_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Selection highlight
                elif selected_pos is not None and pos == selected_pos:
                    self._overlay.fill(SELECTED_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 5: inspect tint (purple) — unit whose card info is shown
                elif inspect_pos is not None and pos == inspect_pos:
                    self._overlay.fill(INSPECT_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 5: summon vessel highlight (gold tint)
                elif pos in vessel_set:
                    self._overlay.fill(SUMMON_VESSEL_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

        # Legal-move dots (drawn after base squares so they appear on top)
        for dest in legal_dests:
            self._draw_legal_dot(dest, obs)
        for dest in castle_dests:
            self._draw_castle_dot(dest)
        # Summon vessel dots (gold diamonds / rings drawn on top)
        for dest in (summon_vessel_dests or []):
            self._draw_summon_vessel_marker(dest)

    def _draw_legal_dot(self, pos: Position, obs: "Observation") -> None:
        """Draw a dot (empty square) or ring (capture square) for a legal move."""
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        occupied = any(u.position == pos for u in obs.board.units)
        if occupied:
            # Ring for capture
            pygame.draw.circle(
                self._surface, LEGAL_CAPTURE, (cx, cy),
                SQUARE_SIZE // 2 - 4, 5
            )
        else:
            # Filled dot
            pygame.draw.circle(
                self._surface, LEGAL_DOT, (cx, cy),
                SQUARE_SIZE // 7
            )

    def _draw_castle_dot(self, pos: Position) -> None:
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        pygame.draw.circle(self._surface, CASTLE_DOT, (cx, cy), SQUARE_SIZE // 7)

    def _draw_summon_vessel_marker(self, pos: Position) -> None:
        """Draw a gold ring around a valid vessel square (summon mode)."""
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        pygame.draw.circle(
            self._surface, SUMMON_VESSEL_DOT, (cx, cy),
            SQUARE_SIZE // 2 - 5, 4   # thick ring
        )

    def _draw_coordinates(self) -> None:
        """Draw file letters (a–h) and rank numbers (1–8) on the board edges."""
        for file in range(8):
            letter = chr(ord("a") + file)
            is_light = (file + 0) % 2 == 0  # rank 0 = rank 1, always dark corner
            color = COORD_LIGHT if is_light else COORD_DARK
            surf = self._font_small.render(letter, True, color)
            # Bottom of each file column
            if not self._flip:
                sx = BOARD_OFFSET_X + file * SQUARE_SIZE + SQUARE_SIZE - surf.get_width() - 3
                sy = BOARD_OFFSET_Y + SQUARE_SIZE * 8 - surf.get_height() - 2
            else:
                sx = BOARD_OFFSET_X + file * SQUARE_SIZE + 3
                sy = BOARD_OFFSET_Y + 2
            self._surface.blit(surf, (sx, sy))

        for rank in range(8):
            number = str(rank + 1)
            is_light = (0 + rank) % 2 == 0
            color = COORD_LIGHT if is_light else COORD_DARK
            surf = self._font_small.render(number, True, color)
            if not self._flip:
                sx = BOARD_OFFSET_X + 3
                sy = BOARD_OFFSET_Y + (7 - rank) * SQUARE_SIZE + 3
            else:
                sx = BOARD_OFFSET_X + 3
                sy = BOARD_OFFSET_Y + rank * SQUARE_SIZE + 3
            self._surface.blit(surf, (sx, sy))

    def _draw_pieces(self, obs: "Observation") -> None:
        """Render all pieces as Unicode glyphs centered on their squares.

        Stage 5: monster units get a gold underline bar and a tiny name label.
        """
        for unit_info in obs.board.units:
            pos = unit_info.position
            sx, sy = _sq_to_screen(pos, self._flip)
            glyph_pair = _GLYPHS.get(unit_info.piece_type, ("?", "?"))
            glyph = glyph_pair[0] if unit_info.owner == "white" else glyph_pair[1]

            has_monster = bool(getattr(unit_info, "monster_id", None))
            text_color = WHITE_PIECE if unit_info.owner == "white" else BLACK_PIECE
            shadow_color = WHITE_PIECE_SHADOW if unit_info.owner == "white" else BLACK_PIECE_SHADOW

            # Shadow (offset by 1 pixel)
            shadow = self._font_large.render(glyph, True, shadow_color)
            sw = shadow.get_width()
            sh = shadow.get_height()
            bx = sx + (SQUARE_SIZE - sw) // 2 + 1
            by = sy + (SQUARE_SIZE - sh) // 2 + 1
            self._surface.blit(shadow, (bx, by))

            # Glyph (shifted slightly upward when monster label is shown)
            text = self._font_large.render(glyph, True, text_color)
            tw = text.get_width()
            th = text.get_height()
            glyph_offset_y = -6 if has_monster else 0
            bx = sx + (SQUARE_SIZE - tw) // 2
            by = sy + (SQUARE_SIZE - th) // 2 + glyph_offset_y
            self._surface.blit(text, (bx, by))

            # Stage 5: gold underline bar + monster name for transformed pieces
            if has_monster:
                # Gold underline bar at the bottom of the square
                bar_h = 4
                bar_y = sy + SQUARE_SIZE - bar_h - 1
                pygame.draw.rect(
                    self._surface, SUMMON_VESSEL_DOT,
                    pygame.Rect(sx + 3, bar_y, SQUARE_SIZE - 6, bar_h),
                    border_radius=2,
                )
                # Tiny monster name label
                monster_id = unit_info.monster_id
                short_name = monster_id.replace("_", " ").title() if monster_id else ""
                if short_name:
                    lbl = self._font_small.render(short_name, True, SUMMON_VESSEL_DOT)
                    lbl_x = sx + (SQUARE_SIZE - lbl.get_width()) // 2
                    lbl_y = bar_y - lbl.get_height() - 1
                    self._surface.blit(lbl, (lbl_x, lbl_y))

                # Stage 5+: activatable-ability badge (cyan dot, top-right corner)
                # Shown when the monster has at least one activatable effect.
                activatable = getattr(unit_info, "activatable_effects", ())
                if activatable:
                    badge_r = 6
                    badge_cx = sx + SQUARE_SIZE - badge_r - 3
                    badge_cy = sy + badge_r + 3
                    pygame.draw.circle(
                        self._surface, ACTIVATABLE_DOT,
                        (badge_cx, badge_cy), badge_r,
                    )
                    # White outline so it reads on any square colour
                    pygame.draw.circle(
                        self._surface, WHITE_PIECE,
                        (badge_cx, badge_cy), badge_r, 1,
                    )

    # ── Hit-test helper ───────────────────────────────────────────────────

    def pos_from_click(self, mx: int, my: int) -> Position | None:
        """Convert a mouse click to a board Position, or None if outside."""
        return _screen_to_pos(mx, my, self._flip)
