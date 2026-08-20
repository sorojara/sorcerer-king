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
from game.mechanics.area import expand_area
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
    BUILDING_LABEL_BG,
    BUILDING_MARKER_ENEMY,
    BUILDING_MARKER_OWN,
    BUILDING_MARKER_UNDER_CONSTR,
    SUMMON_VESSEL_DOT,
    SUMMON_VESSEL_TINT,
    TERRITORY_BORDER_ENEMY,
    TERRITORY_BORDER_OWN,
    TERRITORY_TINT_ENEMY,
    TERRITORY_TINT_OWN,
    TRAP_LABEL_BG,
    TRAP_MARKER_ENEMY,
    TRAP_MARKER_ENEMY_ACTIVE,
    TRAP_MARKER_OWN,
    TRAP_MARKER_OWN_ACTIVE,
    WHITE_PIECE,
    WHITE_PIECE_SHADOW,
    ZONE_TINT_ENEMY,
    ZONE_TINT_ENEMY_ACTIVE,
    ZONE_TINT_OWN,
    ZONE_TINT_OWN_ACTIVE,
    ZONE_TINT_SPELL_ENEMY,
    ZONE_TINT_SPELL_OWN,
)

if TYPE_CHECKING:
    from game.core.observation import Observation


# ── Constants ──────────────────────────────────────────────────────────────
SQUARE_SIZE: int = 80       # pixels per square
# Stage 6: a left sidebar (hand card reference panel) now occupies the space
# left of the board — see ui/overlays.py HandInfoPanel and
# ui/pygame_app.py's LEFT_SIDEBAR_W. AppController is the single source of
# truth for this value; it's mirrored here so board_view's own coordinate
# math (_sq_to_screen / _screen_to_pos) and everything built on it (click
# hit-testing, tooltips) stays correct without threading an offset through
# every call site.
BOARD_OFFSET_X: int = 220   # left edge of board on the surface
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
        aura_colors: "dict[Position, tuple[int, int, int]] | None" = None,
        show_territory: bool = True,
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
        summon_vessel_dests: Stage 5 — valid vessel squares for the held monster
                              card; Stage 6 also reuses this same gold-ring
                              channel for Trap-placement / Spell-target squares
                              (the AppController decides which mode is active).
        inspect_pos        : Stage 5 — unit being inspected (purple tint, info panel)
        aura_colors        : Stage 6 — Position → RGB for every summoned piece's
                              archetype aura. Computed by AppController (it owns
                              the card registry lookup); this module only draws
                              whatever color it's handed. Positions with no
                              Monster, or missing from the map, get no aura.
        show_territory      : Stage 9 — whether to draw the Territory base
                              tint (AppController's toggle button flips this).
        """
        self._draw_squares(observation, selected_pos, legal_dests, castle_dests,
                           checked_player, summon_vessel_dests or [], inspect_pos,
                           show_territory)
        self._draw_coordinates()
        self._draw_traps(observation)
        self._draw_buildings(observation)
        self._draw_pieces(observation, aura_colors or {})

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
        show_territory: bool = True,
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

        # Stage 6: zones split by source so each gets a distinct tint.
        #
        # • Dormant Trap zones  → muted blue/red   (ZONE_TINT_OWN/ENEMY)
        # • Activated Trap zones→ vivid gold/orange (ZONE_TINT_*_ACTIVE)
        # • Active Spell zones  → purple/magenta    (ZONE_TINT_SPELL_*)
        #
        # Ownership framing is always relative to obs.player_id (the viewer).
        # Priority when a square falls in multiple sets (highest → lowest):
        #   activated trap > spell zone > dormant trap
        own_trap_active_squares:   set[Position] = set()
        enemy_trap_active_squares: set[Position] = set()
        own_trap_squares:          set[Position] = set()
        enemy_trap_squares:        set[Position] = set()
        own_spell_squares:         set[Position] = set()
        enemy_spell_squares:       set[Position] = set()

        for trap in obs.board.trap_locations:
            area = expand_area(trap.position, trap.radius, trap.shape)
            is_own = trap.owner == obs.player_id
            if getattr(trap, "activated", False):
                (own_trap_active_squares if is_own else enemy_trap_active_squares).update(area)
            else:
                (own_trap_squares if is_own else enemy_trap_squares).update(area)

        for eff in obs.board.square_effects:
            is_own = eff.owner == obs.player_id
            (own_spell_squares if is_own else enemy_spell_squares).add(eff.position)

        # Stage 9: Territory — the lowest-priority zone tint (README §8).
        # Computed either way (cheap — just two tuples off the Observation)
        # so the toggle button can flip visibility with no extra state here.
        own_territory = set(getattr(obs, "own_territory", ())) if show_territory else set()
        enemy_territory = set(getattr(obs, "opponent_territory", ())) if show_territory else set()

        for file in range(8):
            for rank in range(8):
                pos = Position(file, rank)
                is_light = (file + rank) % 2 == 0
                color = LIGHT_SQUARE if is_light else DARK_SQUARE
                sx, sy = _sq_to_screen(pos, self._flip)
                rect = pygame.Rect(sx, sy, SQUARE_SIZE, SQUARE_SIZE)
                pygame.draw.rect(self._surface, color, rect)

                # Stage 9: Territory tint — drawn first so every other zone
                # tint below still stacks visibly on top of it. A contested
                # square (both players' Territory) shows both tints blended.
                if pos in own_territory:
                    self._overlay.fill(TERRITORY_TINT_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                if pos in enemy_territory:
                    self._overlay.fill(TERRITORY_TINT_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 6: zone tints — drawn before selection/check so those
                # highlights always read on top.  Priority: activated trap >
                # spell zone > dormant trap.  Own tint beats enemy tint when
                # both would apply to the same square.
                if pos in own_trap_active_squares:
                    self._overlay.fill(ZONE_TINT_OWN_ACTIVE)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_trap_active_squares:
                    self._overlay.fill(ZONE_TINT_ENEMY_ACTIVE)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in own_spell_squares:
                    self._overlay.fill(ZONE_TINT_SPELL_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_spell_squares:
                    self._overlay.fill(ZONE_TINT_SPELL_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in own_trap_squares:
                    self._overlay.fill(ZONE_TINT_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_trap_squares:
                    self._overlay.fill(ZONE_TINT_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))

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

        # Stage 9: Territory borders — an opaque outline traced along the
        # outer edge of each Territory's footprint, so its shape reads
        # clearly at a glance even where fills overlap or sit under a
        # denser Trap/Spell tint.
        if own_territory:
            self._draw_territory_border(own_territory, TERRITORY_BORDER_OWN)
        if enemy_territory:
            self._draw_territory_border(enemy_territory, TERRITORY_BORDER_ENEMY)

        # Legal-move dots (drawn after base squares so they appear on top)
        for dest in legal_dests:
            self._draw_legal_dot(dest, obs)
        for dest in castle_dests:
            self._draw_castle_dot(dest)
        # Summon vessel dots (gold diamonds / rings drawn on top)
        for dest in (summon_vessel_dests or []):
            self._draw_summon_vessel_marker(dest)

    def _draw_territory_border(self, squares: "set[Position]", color: tuple) -> None:
        """
        Stage 9 — trace an opaque outline along the outer edge of a
        Territory footprint: for every square in ``squares``, draw a line
        on whichever of its four sides borders a square NOT in ``squares``
        (including the edge of the board itself).
        """
        LINE_W = 4
        for pos in squares:
            sx, sy = _sq_to_screen(pos, self._flip)
            # (delta-file, delta-rank, edge-line-in-screen-space)
            for df, dr in ((0, 1), (0, -1), (-1, 0), (1, 0)):
                nf, nr = pos.file + df, pos.rank + dr
                neighbor_in_zone = (
                    0 <= nf <= 7 and 0 <= nr <= 7
                    and Position(nf, nr) in squares
                )
                if neighbor_in_zone:
                    continue
                # dr=+1 (north, higher rank) is the TOP edge on screen
                # unless flipped; dr=-1 is the bottom; df=-1 is left; df=+1
                # is right — matches _sq_to_screen's own y-axis inversion.
                if dr == 1:
                    y_edge = sy if not self._flip else sy + SQUARE_SIZE
                    pygame.draw.line(self._surface, color, (sx, y_edge), (sx + SQUARE_SIZE, y_edge), LINE_W)
                elif dr == -1:
                    y_edge = sy + SQUARE_SIZE if not self._flip else sy
                    pygame.draw.line(self._surface, color, (sx, y_edge), (sx + SQUARE_SIZE, y_edge), LINE_W)
                elif df == -1:
                    pygame.draw.line(self._surface, color, (sx, sy), (sx, sy + SQUARE_SIZE), LINE_W)
                elif df == 1:
                    pygame.draw.line(self._surface, color, (sx + SQUARE_SIZE, sy), (sx + SQUARE_SIZE, sy + SQUARE_SIZE), LINE_W)

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

    def _draw_traps(self, obs: "Observation") -> None:
        """
        Stage 6 — every placed Trap gets a marker ring on its square (its
        existence and rough danger zone are always visible — that's the
        colored area from _draw_squares).  Its IDENTITY is only revealed
        for the viewer's own Traps: the name label is only drawn when
        ``trap.owner == obs.player_id``.  An enemy Trap shows a plain ring
        in the "enemy" color — you know something is there, not what it is.
        """
        for trap in getattr(obs, "trap_locations", ()):
            pos = trap.position
            sx, sy = _sq_to_screen(pos, self._flip)
            cx = sx + SQUARE_SIZE // 2
            cy = sy + SQUARE_SIZE // 2
            is_own = trap.owner == obs.player_id
            is_active = getattr(trap, "activated", False)
            if is_own:
                ring_color = TRAP_MARKER_OWN_ACTIVE if is_active else TRAP_MARKER_OWN
            else:
                ring_color = TRAP_MARKER_ENEMY_ACTIVE if is_active else TRAP_MARKER_ENEMY
            pygame.draw.circle(
                self._surface, ring_color, (cx, cy),
                SQUARE_SIZE // 2 - 3, 3,
            )
            if not is_own:
                continue
            short_name = trap.card_id.replace("_", " ").title()
            lbl = self._font_small.render(short_name, True, ring_color)
            lbl_bg = pygame.Surface((lbl.get_width() + 4, lbl.get_height() + 2), pygame.SRCALPHA)
            lbl_bg.fill(TRAP_LABEL_BG)
            lx = sx + (SQUARE_SIZE - lbl.get_width()) // 2
            ly = sy + 2
            self._surface.blit(lbl_bg, (lx - 2, ly - 1))
            self._surface.blit(lbl, (lx, ly))

    def _draw_buildings(self, obs: "Observation") -> None:
        """
        Stage 8 — every Building (public — README §12) gets an inset frame
        on its square, drawn under the chess piece that may occupy the same
        square (the Builder Pawn, or later any piece — see chess/board.py's
        ``building_id`` being independent of ``unit``).

        Amber while UNDER_CONSTRUCTION (with a remaining-turns count);
        green (own) / muted orange (enemy) once COMPLETE.  DESTROYED
        Buildings are never in ``building_locations`` for long — the square
        is cleared the same event that marks them destroyed — but the
        guard below skips them defensively either way.
        """
        from game.core.phases import ConstructionStatus

        for b in getattr(obs.board, "building_locations", ()):
            if getattr(b, "status", None) == ConstructionStatus.DESTROYED:
                continue
            pos = b.position
            sx, sy = _sq_to_screen(pos, self._flip)
            under_construction = b.status == ConstructionStatus.UNDER_CONSTRUCTION
            if under_construction:
                color = BUILDING_MARKER_UNDER_CONSTR
            else:
                color = BUILDING_MARKER_OWN if b.owner == obs.player_id else BUILDING_MARKER_ENEMY

            inset = 3
            rect = pygame.Rect(sx + inset, sy + inset, SQUARE_SIZE - inset * 2, SQUARE_SIZE - inset * 2)
            pygame.draw.rect(self._surface, color, rect, 3, border_radius=6)

            short_name = b.building_card_id.replace("_", " ").title()
            label = f"{short_name} ({b.remaining_turns})" if under_construction else short_name
            lbl = self._font_small.render(label, True, color)
            lbl_bg = pygame.Surface((lbl.get_width() + 4, lbl.get_height() + 2), pygame.SRCALPHA)
            lbl_bg.fill(BUILDING_LABEL_BG)
            # Bottom of the square — _draw_traps already owns the top-label
            # slot, and this is where a Building marker most often needs to
            # coexist with an ordinary (non-Monster) piece glyph above it.
            lx = sx + (SQUARE_SIZE - lbl.get_width()) // 2
            ly = sy + SQUARE_SIZE - lbl.get_height() - 3
            self._surface.blit(lbl_bg, (lx - 2, ly - 1))
            self._surface.blit(lbl, (lx, ly))

    def _draw_pieces(
        self,
        obs: "Observation",
        aura_colors: "dict[Position, tuple[int, int, int]]",
    ) -> None:
        """Render all pieces as Unicode glyphs centered on their squares.

        Stage 5: monster units get a gold underline bar and a tiny name label.
        Stage 6: monster units also get a colored aura keyed by archetype
        (see ui/archetype_colors.py) — drawn first so the glyph sits on top.
        """
        for unit_info in obs.board.units:
            pos = unit_info.position
            sx, sy = _sq_to_screen(pos, self._flip)
            glyph_pair = _GLYPHS.get(unit_info.piece_type, ("?", "?"))
            glyph = glyph_pair[0] if unit_info.owner == "white" else glyph_pair[1]

            has_monster = bool(getattr(unit_info, "monster_id", None))
            text_color = WHITE_PIECE if unit_info.owner == "white" else BLACK_PIECE
            shadow_color = WHITE_PIECE_SHADOW if unit_info.owner == "white" else BLACK_PIECE_SHADOW

            # Stage 6: archetype aura — soft glow + ring, behind the glyph.
            if has_monster and pos in aura_colors:
                color = aura_colors[pos]
                aura = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
                cx, cy = SQUARE_SIZE // 2, SQUARE_SIZE // 2
                radius = SQUARE_SIZE // 2 - 4
                pygame.draw.circle(aura, (*color, 70), (cx, cy), radius)
                pygame.draw.circle(aura, (*color, 190), (cx, cy), radius, 3)
                self._surface.blit(aura, (sx, sy))

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

    # ── Stage 6: hover tooltip ──────────────────────────────────────────────

    def draw_tooltip(
        self,
        mouse_pos: tuple[int, int],
        lines: list[tuple[str, tuple]],
    ) -> None:
        """
        Draw a small stacked-text tooltip box near ``mouse_pos``.

        ``lines`` is title-first: ``[(text, color), ...]``.  The caller
        (AppController) decides WHAT to show (Trap info, zone-effect info)
        — this only knows HOW to draw a box of text lines.  Clamped to stay
        fully on-screen.
        """
        from game.ui.colors import TOOLTIP_BG, TOOLTIP_BORDER

        if not lines:
            return

        surfs = [self._font_small.render(text, True, color) for text, color in lines]
        pad = 6
        line_h = max(s.get_height() for s in surfs) + 2
        w = max(s.get_width() for s in surfs) + pad * 2
        h = line_h * len(surfs) + pad * 2

        mx, my = mouse_pos
        x = mx + 16
        y = my + 16
        surface_w = self._surface.get_width()
        surface_h = self._surface.get_height()
        if x + w > surface_w:
            x = surface_w - w - 4
        if y + h > surface_h:
            y = surface_h - h - 4

        box = pygame.Surface((w, h), pygame.SRCALPHA)
        box.fill(TOOLTIP_BG)
        pygame.draw.rect(box, TOOLTIP_BORDER, box.get_rect(), 1)
        for i, surf in enumerate(surfs):
            box.blit(surf, (pad, pad + i * line_h))
        self._surface.blit(box, (x, y))
