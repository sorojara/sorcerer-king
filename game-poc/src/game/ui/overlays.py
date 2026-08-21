"""
ui/overlays.py — HUD overlays (Stage 3)
=========================================

Draws the sidebar panel that shows:
  • Active player (white / black) with colour indicator
  • Current phase label
  • Turn number
  • "CHECK!" warning when the active player is in check
  • "FINAL DUEL" notice when Phase.FINAL_DUEL is active
  • Promotion selection dialog (on-board overlay)
  • Controls hint strip at the bottom

Also owns the on-board promotion dialog: a row of four piece-choice
buttons overlaid on the board when a pawn reaches the back rank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from game.core.phases import Phase
from game.ui.colors import (
    DIALOG_BG,
    DIALOG_BORDER,
    DIALOG_HOVER,
    HUD_ACCENT,
    HUD_DUEL,
    HUD_LABEL,
    HUD_TEXT,
    HUD_WARN,
    SIDEBAR_BG,
    WHITE,
    BLACK,
)
from game.ui.board_view import BOARD_PIXEL_SIZE, SQUARE_SIZE

if TYPE_CHECKING:
    from game.core.observation import Observation
    from game.cards.card import AnyCard


# Promotion piece order shown in the dialog (left → right)
PROMOTION_PIECES = ["queen", "rook", "bishop", "knight"]

# Piece glyphs reused from board_view (import at runtime to avoid circular)
_PROMO_GLYPHS_WHITE = {"queen": "♕", "rook": "♖", "bishop": "♗", "knight": "♘"}
_PROMO_GLYPHS_BLACK = {"queen": "♛", "rook": "♜", "bishop": "♝", "knight": "♞"}


class SidebarOverlay:
    """
    Renders the info sidebar to the right of the board.

    ``x_offset`` is the left edge of the sidebar in screen coordinates.

    After calling ``draw()``, ``handle_click(mx, my)`` returns:
        "export"  — user clicked the Export button
        "import"  — user clicked the Import button
        None      — click missed both buttons
    """

    SIDEBAR_WIDTH: int = 200
    PADDING: int = 16
    BTN_H: int = 26   # button height in pixels

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,        # _ft.Font — pygame.font not used (Py 3.14 bug)
        font_small: Any,
        x_offset: int,
    ) -> None:
        self._surface = surface
        self._font = font
        self._font_small = font_small
        self._x = x_offset
        # Button rects — populated each draw() call
        self._btn_export: pygame.Rect | None = None
        self._btn_import: pygame.Rect | None = None
        self._btn_white_mode: pygame.Rect | None = None
        self._btn_black_mode: pygame.Rect | None = None
        self._btn_black_hand: pygame.Rect | None = None
        self._btn_territory: pygame.Rect | None = None
        self._btn_recompose: pygame.Rect | None = None
        self._btn_mercenary: pygame.Rect | None = None
        self._btn_mercenary_enabled: bool = False
        self._btn_build: pygame.Rect | None = None
        self._btn_build_enabled: bool = False
        self._mouse_pos: tuple[int, int] = (0, 0)
        # Stage 6: cached "Activatable" row rects: list of (token, Rect)
        self._activatable_btn_rects: list[tuple[str, pygame.Rect]] = []

    def update_mouse(self, pos: tuple[int, int]) -> None:
        self._mouse_pos = pos

    def handle_click(self, mx: int, my: int) -> str | None:
        """
        Return one of:
            'export'            — Export state button
            'import'            — Import state button
            'toggle_white'      — Toggle white player mode
            'toggle_black'      — Toggle black player mode
            'toggle_black_hand' — Toggle black-hand debug view
            'toggle_territory'  — Stage 9: Toggle the Territory board tint
            'recompose'         — Trigger DeclareRecompose (PREPARATION only)
            'mercenary'         — Open Mercenary piece-type picker (PREPARATION only)
            'build'             — Stage 8: Open the Building Pool picker (PREPARATION only)
            'trap:<id>'         — Stage 6: Activatable-section Trap button (id = trap_instance_id)
            'ability_at:<f>:<r>:<id>' — Stage 6: Activatable-section monster ability
                                 button, naming the piece's position since the
                                 Activatable list spans every unit, not just one
            None                — missed all buttons
        """
        if self._btn_export and self._btn_export.collidepoint(mx, my):
            return "export"
        if self._btn_import and self._btn_import.collidepoint(mx, my):
            return "import"
        if self._btn_white_mode and self._btn_white_mode.collidepoint(mx, my):
            return "toggle_white"
        if self._btn_black_mode and self._btn_black_mode.collidepoint(mx, my):
            return "toggle_black"
        if self._btn_black_hand and self._btn_black_hand.collidepoint(mx, my):
            return "toggle_black_hand"
        if self._btn_territory and self._btn_territory.collidepoint(mx, my):
            return "toggle_territory"
        if self._btn_recompose and self._btn_recompose.collidepoint(mx, my):
            return "recompose"
        # Mercenary: only fire if enabled (the button rect exists but may be
        # rendered as disabled — check by comparing colour isn't possible, so
        # the AppController passes show_mercenary_btn=False when disabled and we
        # skip the click by checking against an "enabled" sentinel we store below).
        if self._btn_mercenary and getattr(self, "_btn_mercenary_enabled", False) and self._btn_mercenary.collidepoint(mx, my):
            return "mercenary"
        if self._btn_build and getattr(self, "_btn_build_enabled", False) and self._btn_build.collidepoint(mx, my):
            return "build"
        for token, rect in self._activatable_btn_rects:
            if rect.collidepoint(mx, my):
                return token
        return None

    def draw(
        self,
        obs: "Observation",
        player_modes: dict | None = None,
        show_black_hand: bool = False,
        show_recompose_btn: bool = False,
        show_mercenary_btn: "bool | None" = None,
        show_build_btn: "bool | None" = None,
        show_territory: bool = True,
        activatable_entries: "list[tuple[str, str]] | None" = None,
    ) -> None:
        """
        Render the sidebar based on the current observation.

        ``player_modes``       — optional dict mapping player_id → "human" | "ai".
        ``show_black_hand``    — when True, the black-hand toggle button is shown active.
        ``show_recompose_btn``  — when True, the Recompose button is drawn and clickable.
        ``show_mercenary_btn``  — None = don't draw (non-PREPARATION phases).
                                  True = draw enabled (clickable, gold).
                                  False = draw disabled (greyed out, not clickable).
        ``show_build_btn``      — Stage 8: same tri-state convention as
                                  ``show_mercenary_btn`` for the Building
                                  Pool picker button.
        ``show_territory``      — Stage 9: current on/off state of the
                                  Territory board tint, shown as the toggle
                                  button's label (always drawn, unlike the
                                  tri-state buttons above).
        ``activatable_entries`` — Stage 6: (label, token) pairs for every currently
                                 activatable Trap/Monster-ability "in the field".
                                 One clickable row per entry; ``handle_click``
                                 returns the token verbatim. (Card detail itself
                                 lives in the left CardViewer, not here.)
        """
        modes = player_modes or {}

        # Background
        rect = pygame.Rect(self._x, 0, self.SIDEBAR_WIDTH, self._surface.get_height())
        pygame.draw.rect(self._surface, SIDEBAR_BG, rect)

        y = self.PADDING
        y = self._draw_divider(y)
        y += 8

        # Turn number
        y = self._draw_kv("Turn", str(obs.turn_number), y)
        y += 4

        # Active player
        colour = HUD_ACCENT
        symbol = "▶" if obs.active_player == "white" else "◀"
        y = self._draw_kv("Active", f"{symbol} {obs.active_player.capitalize()}", y,
                          value_color=colour)
        y += 4

        # Phase
        phase_name = obs.phase.value.replace("_", " ").title()
        y = self._draw_kv("Phase", phase_name, y)
        y += 12

        y = self._draw_divider(y)
        y += 8

        # ── Player mode toggles ───────────────────────────────────────────
        y = self._draw_line("Players", self._font_small, HUD_LABEL, y, center=False)
        y += 4
        btn_w = self.SIDEBAR_WIDTH - self.PADDING * 2
        btn_x = self._x + self.PADDING
        # White toggle
        w_mode = modes.get("white", "human")
        w_label = f"♔ White: {w_mode.upper()}"
        w_color = HUD_ACCENT if w_mode == "human" else HUD_DUEL
        self._btn_white_mode = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_w = self._btn_white_mode.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_w else DIALOG_BG,
                         self._btn_white_mode, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_white_mode, 1, border_radius=4)
        ws = self._font_small.render(w_label, True, w_color)
        self._surface.blit(ws, (self._btn_white_mode.x + (btn_w - ws.get_width()) // 2,
                                self._btn_white_mode.y + (self.BTN_H - ws.get_height()) // 2))
        y += self.BTN_H + 4
        # Black toggle
        b_mode = modes.get("black", "human")
        b_label = f"♚ Black: {b_mode.upper()}"
        b_color = HUD_ACCENT if b_mode == "human" else HUD_DUEL
        self._btn_black_mode = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_b = self._btn_black_mode.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_b else DIALOG_BG,
                         self._btn_black_mode, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_black_mode, 1, border_radius=4)
        bs = self._font_small.render(b_label, True, b_color)
        self._surface.blit(bs, (self._btn_black_mode.x + (btn_w - bs.get_width()) // 2,
                                self._btn_black_mode.y + (self.BTN_H - bs.get_height()) // 2))
        y += self.BTN_H + 4

        # Black hand debug toggle
        bh_label = "👁 Black Hand: ON" if show_black_hand else "👁 Black Hand: OFF"
        bh_color = HUD_DUEL if show_black_hand else HUD_LABEL
        self._btn_black_hand = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_bh = self._btn_black_hand.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_bh else DIALOG_BG,
                         self._btn_black_hand, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_black_hand, 1, border_radius=4)
        bhs = self._font_small.render(bh_label, True, bh_color)
        self._surface.blit(bhs, (self._btn_black_hand.x + (btn_w - bhs.get_width()) // 2,
                                 self._btn_black_hand.y + (self.BTN_H - bhs.get_height()) // 2))
        y += self.BTN_H + 4

        # Stage 9: Territory overlay toggle
        terr_label = "🗺 Territory: ON" if show_territory else "🗺 Territory: OFF"
        terr_color = HUD_ACCENT if show_territory else HUD_LABEL
        self._btn_territory = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_terr = self._btn_territory.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_terr else DIALOG_BG,
                         self._btn_territory, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_territory, 1, border_radius=4)
        ts = self._font_small.render(terr_label, True, terr_color)
        self._surface.blit(ts, (self._btn_territory.x + (btn_w - ts.get_width()) // 2,
                                self._btn_territory.y + (self.BTN_H - ts.get_height()) // 2))
        y += self.BTN_H + 8

        y = self._draw_divider(y)
        y += 12

        # Check warning
        if obs.own_in_check:
            y = self._draw_line("⚠  CHECK!", self._font, HUD_WARN, y, center=True)
            y += 8
        if obs.opponent_in_check:
            opp_name = ("black" if obs.active_player == "white" else "white").capitalize()
            y = self._draw_line(f"⚠  {opp_name} in Check", self._font_small, HUD_WARN, y, center=True)
            y += 8

        # Final Duel notice
        if obs.phase == Phase.FINAL_DUEL:
            y = self._draw_line("⚔  FINAL DUEL", self._font, HUD_DUEL, y, center=True)
            y += 8

        # Promotion pending notice
        if obs.phase == Phase.PROMOTION_SELECTION and obs.pending_decision_type:
            y = self._draw_line("Choose promotion", self._font_small, HUD_TEXT, y, center=True)
            y += 4

        # Recompose pending notice — shown for both human and AI turns.
        # For a human player the hand strip already shows the selection counter;
        # this overlay adds a second, board-level reminder.
        if obs.phase == Phase.RECOMPOSE_SELECTION:
            n = getattr(obs, "pending_decision_min", 0)
            y = self._draw_line(f"♻  Return {n} card(s) — click hand to pick", self._font_small, HUD_WARN, y, center=True)
            y += 4

        # Mercenary pending notices
        if obs.phase == Phase.MERCENARY_SELECTION:
            n = getattr(obs, "pending_decision_min", 0)
            y = self._draw_line(f"⚔  Pick {n} Monster card(s) to sacrifice", self._font_small, (200, 160, 80), y, center=True)
            y += 4
        if obs.phase == Phase.MERCENARY_PLACEMENT:
            y = self._draw_line("⚔  Click a square in your first 2 ranks", self._font_small, (200, 160, 80), y, center=True)
            y += 4

        # Reposition pending notice (blade_dancer after-capture)
        if (obs.phase == Phase.CHESS
                and obs.pending_decision_type == "reposition"):
            y = self._draw_line("⚡ Reposition your piece!", self._font_small, (80, 220, 220), y, center=True)
            y += 2
            y = self._draw_line("Click a highlighted square", self._font_small, HUD_LABEL, y, center=True)
            y += 4

        y += 20
        y = self._draw_divider(y)
        y += 8

        # Deck / hand sizes
        y = self._draw_kv("Own hand", str(len(obs.own_hand)), y)
        y = self._draw_kv("Own deck", str(obs.own_deck_count), y)
        y += 4
        y = self._draw_kv("Opp hand", str(obs.opponent_hand_count), y)
        y = self._draw_kv("Opp deck", str(obs.opponent_deck_count), y)

        # ── Stage 6: Activatable — every Trap/Monster-ability the player can
        # fire right now, "in the field" — not just on an inspected piece.
        self._activatable_btn_rects = []
        if activatable_entries:
            y += 12
            y = self._draw_divider(y)
            y += 6
            y = self._draw_line("⚡ Activatable", self._font_small, HUD_LABEL, y, center=False)
            y += 2
            for label, token in activatable_entries:
                row_rect = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
                hover = row_rect.collidepoint(self._mouse_pos)
                pygame.draw.rect(self._surface, DIALOG_HOVER if hover else DIALOG_BG,
                                 row_rect, border_radius=4)
                pygame.draw.rect(self._surface, (80, 220, 220), row_rect, 1, border_radius=4)
                lbl_surf = self._font_small.render(label, True, (80, 220, 220))
                self._surface.blit(lbl_surf, (
                    row_rect.x + 6, row_rect.y + (self.BTN_H - lbl_surf.get_height()) // 2,
                ))
                self._activatable_btn_rects.append((token, row_rect))
                y += self.BTN_H + 3

        # Recompose button — shown during PREPARATION for a human player.
        # Always rendered when show_recompose_btn is True.
        if show_recompose_btn:
            y += 8
            rc_label = "♻  Recompose"
            rc_color = HUD_TEXT
            self._btn_recompose = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
            hover_rc = self._btn_recompose.collidepoint(self._mouse_pos)
            pygame.draw.rect(self._surface,
                             DIALOG_HOVER if hover_rc else DIALOG_BG,
                             self._btn_recompose, border_radius=4)
            pygame.draw.rect(self._surface, DIALOG_BORDER,
                             self._btn_recompose, 1, border_radius=4)
            rcs = self._font_small.render(rc_label, True, rc_color)
            self._surface.blit(rcs, (
                self._btn_recompose.x + (btn_w - rcs.get_width()) // 2,
                self._btn_recompose.y + (self.BTN_H - rcs.get_height()) // 2,
            ))
            y += self.BTN_H + 4
        else:
            self._btn_recompose = None

        # Mercenary button — always rendered during PREPARATION for a human player
        # (show_mercenary_btn is always True during prep); enabled/disabled via colour.
        if show_mercenary_btn is not None:  # None = don't show at all (non-PREPARATION)
            mc_enabled = bool(show_mercenary_btn)  # True = clickable, False = greyed out
            self._btn_mercenary_enabled = mc_enabled
            mc_label = "⚔  Mercenary"
            mc_color  = (200, 160, 80) if mc_enabled else (80, 70, 50)
            mc_border = (160, 120, 40) if mc_enabled else (60, 50, 30)
            self._btn_mercenary = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
            hover_mc = mc_enabled and self._btn_mercenary.collidepoint(self._mouse_pos)
            pygame.draw.rect(self._surface,
                             DIALOG_HOVER if hover_mc else DIALOG_BG,
                             self._btn_mercenary, border_radius=4)
            pygame.draw.rect(self._surface, mc_border,
                             self._btn_mercenary, 1, border_radius=4)
            mcs = self._font_small.render(mc_label, True, mc_color)
            self._surface.blit(mcs, (
                self._btn_mercenary.x + (btn_w - mcs.get_width()) // 2,
                self._btn_mercenary.y + (self.BTN_H - mcs.get_height()) // 2,
            ))
        else:
            self._btn_mercenary = None

        # Build button — Stage 8: opens the Building Pool picker.
        if show_build_btn is not None:
            bd_enabled = bool(show_build_btn)
            self._btn_build_enabled = bd_enabled
            bd_label = "🏛  Build"
            bd_color  = (120, 200, 140) if bd_enabled else (70, 90, 75)
            bd_border = (90, 160, 110) if bd_enabled else (55, 70, 60)
            y += self.BTN_H + 4
            self._btn_build = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
            hover_bd = bd_enabled and self._btn_build.collidepoint(self._mouse_pos)
            pygame.draw.rect(self._surface,
                             DIALOG_HOVER if hover_bd else DIALOG_BG,
                             self._btn_build, border_radius=4)
            pygame.draw.rect(self._surface, bd_border,
                             self._btn_build, 1, border_radius=4)
            bds = self._font_small.render(bd_label, True, bd_color)
            self._surface.blit(bds, (
                self._btn_build.x + (btn_w - bds.get_width()) // 2,
                self._btn_build.y + (self.BTN_H - bds.get_height()) // 2,
            ))
        else:
            self._btn_build = None

        # Controls hint + Export/Import buttons at the fixed bottom position.
        # (Card detail now lives entirely in the left CardViewer.)
        self._draw_controls_hint()

    # ── Private helpers ───────────────────────────────────────────────────

    def _draw_line(
        self,
        text: str,
        font: Any,        # FTFont — pygame.font not used (Py 3.14 bug)
        color: tuple,
        y: int,
        center: bool = False,
    ) -> int:
        surf = font.render(text, True, color)
        if center:
            x = self._x + (self.SIDEBAR_WIDTH - surf.get_width()) // 2
        else:
            x = self._x + self.PADDING
        self._surface.blit(surf, (x, y))
        return y + surf.get_height() + 2

    def _draw_kv(
        self,
        label: str,
        value: str,
        y: int,
        value_color: tuple = HUD_TEXT,
    ) -> int:
        lsurf = self._font_small.render(label + ":", True, HUD_LABEL)
        vsurf = self._font_small.render(value, True, value_color)
        self._surface.blit(lsurf, (self._x + self.PADDING, y))
        self._surface.blit(vsurf, (self._x + self.SIDEBAR_WIDTH - vsurf.get_width() - self.PADDING, y))
        return y + max(lsurf.get_height(), vsurf.get_height()) + 3

    def _draw_divider(self, y: int) -> int:
        pygame.draw.line(
            self._surface,
            DIALOG_BORDER,
            (self._x + self.PADDING, y),
            (self._x + self.SIDEBAR_WIDTH - self.PADDING, y),
            1,
        )
        return y + 1

    def _draw_controls_hint(self) -> None:
        """Draw the Export/Import buttons at the bottom of the sidebar."""
        h = self._surface.get_height()
        btn_block = self.BTN_H * 2 + self.PADDING * 3

        # ── Export / Import buttons (always drawn, always on top) ─────────
        btn_w = self.SIDEBAR_WIDTH - self.PADDING * 2
        btn_x = self._x + self.PADDING
        btn_y = h - btn_block + self.PADDING

        self._btn_export = pygame.Rect(btn_x, btn_y, btn_w, self.BTN_H)
        self._btn_import = pygame.Rect(btn_x, btn_y + self.BTN_H + 6, btn_w, self.BTN_H)

        for btn, label in (
            (self._btn_export, "💾  Export state"),
            (self._btn_import, "📂  Import state"),
        ):
            hover = btn.collidepoint(self._mouse_pos)
            bg = DIALOG_HOVER if hover else DIALOG_BG
            pygame.draw.rect(self._surface, bg, btn, border_radius=4)
            pygame.draw.rect(self._surface, DIALOG_BORDER, btn, 1, border_radius=4)
            lsurf = self._font_small.render(label, True, HUD_TEXT)
            lx = btn.x + (btn.width - lsurf.get_width()) // 2
            ly = btn.y + (btn.height - lsurf.get_height()) // 2
            self._surface.blit(lsurf, (lx, ly))


class CardViewer:
    """
    Stage 6 (corrected) — dedicated LEFT sidebar: shows ONE card's full
    detail — name, type badge, effects, live statuses, description, and
    activatable-ability buttons — exactly like the old hover-driven panel
    used to, except selection is RIGHT-CLICK, never hover (hover proved
    unreliable). AppController owns which card is "active"; this class only
    renders it. Set it by right-clicking:

        • a card in your hand
        • a summoned piece on the board (shows its Monster + live statuses
          + ability buttons, same as before)
        • an entry in the "Active in this zone" list below (see next point)

    Right-clicking a board square that carries one or more of YOUR OWN
    active Trap/Spell effects (a Trap's activation area, or a persistent
    zone like frozen/scorched/blocked/cursed) does NOT open the viewer
    directly — it populates the "Active in this zone" list here instead;
    right-clicking one of ITS entries is what opens that card in the
    viewer above. (An empty list / no card selected shows a hint instead.)
    """

    PADDING: int = 10
    BTN_H: int = 26
    IMAGE_BOX_H: int = 200
    _TYPE_BADGES: dict = {
        "monster": ("MON", (200, 100, 100)),
        "spell":   ("SPC", (80, 140, 220)),
        "trap":    ("TRP", (160, 100, 220)),
    }

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,
        font_small: Any,
        width: int,
        registry: "object | None" = None,
        images_dir: "Any | None" = None,
    ) -> None:
        self._surface = surface
        self._font = font
        self._font_small = font_small
        self._width = width
        self._registry = registry
        self._images_dir = images_dir  # Path — see data/images/README.md
        self._mouse_pos: tuple[int, int] = (0, 0)
        # image_path -> loaded Surface, or None if load failed/missing
        # (cached so we don't re-hit the filesystem every frame).
        self._image_cache: "dict[str, Any]" = {}
        # Cached rects — populated each draw() call.
        self._ability_btn_rects: list[tuple[str, pygame.Rect]] = []
        self._zone_entry_rects: list[tuple[str, pygame.Rect]] = []

    def update_mouse(self, pos: tuple[int, int]) -> None:
        self._mouse_pos = pos

    def handle_ability_click(self, mx: int, my: int) -> str | None:
        """LEFT-click hit-test for the ability buttons. Returns the ability_id."""
        for ability_id, rect in self._ability_btn_rects:
            if rect.collidepoint(mx, my):
                return ability_id
        return None

    def zone_entry_from_click(self, mx: int, my: int) -> str | None:
        """
        RIGHT-click hit-test for the "Active in this zone" list. Returns
        the card_id of the entry clicked, so the caller can open it in the
        viewer.
        """
        for card_id, rect in self._zone_entry_rects:
            if rect.collidepoint(mx, my):
                return card_id
        return None

    def draw(
        self,
        card_id: str | None,
        ability_ids: "list[str] | None" = None,
        unit_statuses: "tuple[str, ...]" = (),
        zone_entries: "list[tuple[str, str]] | None" = None,
        content_height: "int | None" = None,
    ) -> None:
        """
        ``card_id``       — the card to show, or None for the empty state.
        ``ability_ids``   — activatable abilities on the inspected unit
                            (only meaningful when card_id came from a
                            right-clicked board piece); one button each.
        ``unit_statuses`` — live statuses of that inspected unit.
        ``zone_entries``  — (label, card_id) pairs for the "Active in this
                            zone" section from a right-clicked Trap/zone
                            square; None hides the section entirely.
        ``content_height`` — pixel height this panel is allowed to draw
                            into, measured from the top of the surface.
                            Defaults to the full surface height (the old
                            behaviour). Pass the boundary above the
                            EventLogPanel's strip (see ui/event_log.py) so
                            the two never overlap — content that would run
                            past it is clipped rather than drawn over the
                            log.
        """
        full_h = self._surface.get_height()
        h = content_height if content_height is not None else full_h
        rect = pygame.Rect(0, 0, self._width, h)
        pygame.draw.rect(self._surface, SIDEBAR_BG, rect)
        pygame.draw.line(self._surface, DIALOG_BORDER, (self._width, 0), (self._width, h), 1)

        prev_clip = self._surface.get_clip()
        self._surface.set_clip(pygame.Rect(0, 0, self._width, h))
        try:
            x = self.PADDING
            y = self.PADDING
            header = self._font_small.render("Card Viewer", True, HUD_LABEL)
            self._surface.blit(header, (x, y))
            y += header.get_height() + 6
            y = self._draw_divider(y)
            y += 8

            self._ability_btn_rects = []

            card = None
            if card_id is not None and self._registry is not None and card_id in self._registry:
                try:
                    card = self._registry.get(card_id)
                except Exception:
                    card = None

            if card is None:
                for line in ("Right-click a card, piece,", "or Trap zone to inspect it."):
                    hint = self._font_small.render(line, True, HUD_LABEL)
                    self._surface.blit(hint, (x, y))
                    y += hint.get_height() + 2
                y += 6
            else:
                y = self._draw_card_block(x, y, card, ability_ids or [], unit_statuses)

            self._zone_entry_rects = []
            if zone_entries:
                y += 4
                y = self._draw_divider(y)
                y += 6
                zh = self._font_small.render("Active in this zone:", True, HUD_LABEL)
                self._surface.blit(zh, (x, y))
                y += zh.get_height() + 4

                row_w = self._width - self.PADDING * 2
                for label, entry_card_id in zone_entries:
                    row_rect = pygame.Rect(x, y, row_w, self.BTN_H)
                    hover = row_rect.collidepoint(self._mouse_pos)
                    is_active = entry_card_id == card_id
                    bg = DIALOG_HOVER if hover else DIALOG_BG
                    border = (255, 210, 0) if is_active else DIALOG_BORDER
                    pygame.draw.rect(self._surface, bg, row_rect, border_radius=4)
                    pygame.draw.rect(self._surface, border, row_rect, 1, border_radius=4)
                    lbl_surf = self._font_small.render(label, True, HUD_TEXT)
                    self._surface.blit(lbl_surf, (
                        row_rect.x + 6, row_rect.y + (self.BTN_H - lbl_surf.get_height()) // 2,
                    ))
                    self._zone_entry_rects.append((entry_card_id, row_rect))
                    y += self.BTN_H + 3

                hint = self._font_small.render("(right-click an entry to view)", True, HUD_LABEL)
                self._surface.blit(hint, (x, y))
                y += hint.get_height() + 4

            # Content that got clipped out visually must not stay clickable.
            self._ability_btn_rects = [
                (aid, r) for aid, r in self._ability_btn_rects if r.bottom <= h
            ]
            self._zone_entry_rects = [
                (cid, r) for cid, r in self._zone_entry_rects if r.bottom <= h
            ]
        finally:
            self._surface.set_clip(prev_clip)

    def _draw_divider(self, y: int) -> int:
        pygame.draw.line(
            self._surface, DIALOG_BORDER,
            (self.PADDING, y), (self._width - self.PADDING, y), 1,
        )
        return y + 1

    def _get_image(self, card: "AnyCard") -> "pygame.Surface | None":
        """
        Load (and cache) ``card``'s artwork from data/images/, or None if
        it's missing/unloadable — the caller draws the "No Image Available"
        placeholder in that case.  A missing file is expected right now
        (see data/images/README.md) and must never raise.
        """
        path = getattr(card, "image_path", "") or ""
        if not path or self._images_dir is None:
            return None
        if path in self._image_cache:
            return self._image_cache[path]

        surf = None
        try:
            full_path = self._images_dir / path
            if full_path.is_file():
                surf = pygame.image.load(str(full_path)).convert_alpha()
        except Exception:
            surf = None
        self._image_cache[path] = surf
        return surf

    def _draw_image_box(self, x: int, y: int, card: "AnyCard", inner_w: int) -> int:
        """Draw the card's artwork, scaled to fit, or a placeholder box."""
        box_h = self.IMAGE_BOX_H
        box_rect = pygame.Rect(x, y, inner_w, box_h)
        pygame.draw.rect(self._surface, DIALOG_BG, box_rect, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, box_rect, 1, border_radius=4)

        img = self._get_image(card)
        if img is not None:
            iw, ih = img.get_size()
            if iw > 0 and ih > 0:
                scale = min((inner_w - 8) / iw, (box_h - 8) / ih)
                new_w = max(1, int(iw * scale))
                new_h = max(1, int(ih * scale))
                scaled = pygame.transform.smoothscale(img, (new_w, new_h))
                self._surface.blit(scaled, (
                    x + (inner_w - new_w) // 2, y + (box_h - new_h) // 2,
                ))
        else:
            label1 = self._font_small.render("No Image", True, HUD_LABEL)
            label2 = self._font_small.render("Available", True, HUD_LABEL)
            cx = x + inner_w // 2
            cy = y + box_h // 2
            self._surface.blit(label1, (cx - label1.get_width() // 2, cy - label1.get_height() - 1))
            self._surface.blit(label2, (cx - label2.get_width() // 2, cy + 1))

        return y + box_h + 8

    def _draw_card_block(
        self,
        x: int,
        y: int,
        card: "AnyCard",
        ability_ids: list[str],
        unit_statuses: "tuple[str, ...]",
    ) -> int:
        """Adapted from the old hover panel's layout — see module history."""
        from game.cards.card import CardType

        inner_w = self._width - self.PADDING * 2

        # ── Name ──────────────────────────────────────────────────────────
        name_surf = self._font.render(card.name, True, HUD_TEXT)
        if name_surf.get_width() > inner_w:
            name_surf = self._font_small.render(card.name, True, HUD_TEXT)
        self._surface.blit(name_surf, (x, y))
        y += name_surf.get_height() + 2

        # ── Type badge + archetype/spell-type/trigger ───────────────────────
        if card.card_type == CardType.MONSTER:
            badge_color = (200, 100, 100)
            archetype = getattr(card, "archetype", "")
            arch_line = f"MON  {archetype}" if archetype else "MON"
        elif card.card_type == CardType.SPELL:
            badge_color = (80, 140, 220)
            spell_type = getattr(card, "spell_type", None)
            arch_line = f"SPC  {spell_type.value}" if spell_type else "SPC"
        else:
            badge_color = (160, 100, 220)
            trigger = getattr(card, "trigger", None)
            arch_line = f"TRP  {trigger.value}" if trigger else "TRP"
        badge_surf = self._font_small.render(arch_line, True, badge_color)
        self._surface.blit(badge_surf, (x, y))
        y += badge_surf.get_height() + 4

        # ── Image (Stage 6) ──────────────────────────────────────────────
        y = self._draw_image_box(x, y, card, inner_w)

        # ── Effects ───────────────────────────────────────────────────────
        effects = getattr(card, "effects", [])
        if effects:
            eff_header = self._font_small.render("Effects:", True, HUD_LABEL)
            self._surface.blit(eff_header, (x, y))
            y += eff_header.get_height() + 1
            for eff in effects[:4]:
                eff_name = eff.type.replace("_", " ")
                eff_surf = self._font_small.render(f"• {eff_name}", True, (160, 200, 160))
                if eff_surf.get_width() > inner_w:
                    txt = eff_name
                    while txt and self._font_small.render(f"• {txt}…", True, (0, 0, 0)).get_width() > inner_w:
                        txt = txt[:-1]
                    eff_surf = self._font_small.render(f"• {txt}…", True, (160, 200, 160))
                self._surface.blit(eff_surf, (x + 4, y))
                y += eff_surf.get_height() + 1
            if len(effects) > 4:
                more_surf = self._font_small.render(f"  +{len(effects) - 4} more", True, HUD_LABEL)
                self._surface.blit(more_surf, (x + 4, y))
                y += more_surf.get_height() + 1
            y += 2

        # ── Live unit statuses (only when viewing a board-inspected unit) ──
        _HIDE_STATUS_PREFIXES = ("builder_available",)
        display_statuses = [
            s for s in unit_statuses
            if not any(s.startswith(p) for p in _HIDE_STATUS_PREFIXES)
        ]
        if display_statuses:
            st_header = self._font_small.render("Active:", True, HUD_LABEL)
            self._surface.blit(st_header, (x, y))
            y += st_header.get_height() + 1
            for status in display_statuses:
                if status.startswith("copied_effect:"):
                    label = "⟳ " + status[len("copied_effect:"):].replace("_", " ")
                    color = (120, 220, 255)
                elif status.startswith("challenge_issued:"):
                    label = "⚔ challenge issued"
                    color = (255, 160, 60)
                elif ":" in status:
                    key, _, val = status.partition(":")
                    label = f"{key.replace('_', ' ')}: {val}"
                    color = (180, 180, 180)
                else:
                    label = status.replace("_", " ")
                    color = (180, 180, 180)
                st_surf = self._font_small.render(f"  {label}", True, color)
                self._surface.blit(st_surf, (x + 4, y))
                y += st_surf.get_height() + 1
            y += 2

        # ── Description (word-wrap) ────────────────────────────────────────
        desc = (getattr(card, "description", "") or "").strip()
        if desc:
            desc_header = self._font_small.render("About:", True, HUD_LABEL)
            self._surface.blit(desc_header, (x, y))
            y += desc_header.get_height() + 1
            words = desc.split()
            line: list[str] = []
            lines_drawn = 0
            max_lines = 6
            for word in words:
                test = " ".join(line + [word])
                if self._font_small.render(test, True, (0, 0, 0)).get_width() > inner_w:
                    if line:
                        row_surf = self._font_small.render(" ".join(line), True, HUD_LABEL)
                        self._surface.blit(row_surf, (x, y))
                        y += row_surf.get_height() + 1
                        lines_drawn += 1
                        if lines_drawn >= max_lines:
                            break
                        line = [word]
                    else:
                        line = [word]
                else:
                    line.append(word)
            if line and lines_drawn < max_lines:
                row_surf = self._font_small.render(" ".join(line), True, HUD_LABEL)
                self._surface.blit(row_surf, (x, y))
                y += row_surf.get_height() + 1
            y += 4

        # ── Activatable ability buttons (LEFT-click) ────────────────────────
        if ability_ids:
            y = self._draw_divider(y)
            y += 4
            act_header = self._font_small.render("Abilities:", True, (80, 220, 220))
            self._surface.blit(act_header, (x, y))
            y += act_header.get_height() + 3
            for ability_id in ability_ids:
                btn_label = f"⚡ {ability_id.replace('_', ' ')}"
                btn_rect = pygame.Rect(x, y, inner_w, self.BTN_H)
                hover = btn_rect.collidepoint(self._mouse_pos)
                pygame.draw.rect(self._surface, DIALOG_HOVER if hover else DIALOG_BG, btn_rect, border_radius=4)
                pygame.draw.rect(self._surface, (80, 220, 220), btn_rect, 1, border_radius=4)
                btn_surf = self._font_small.render(btn_label, True, (80, 220, 220))
                self._surface.blit(btn_surf, (
                    btn_rect.x + (inner_w - btn_surf.get_width()) // 2,
                    btn_rect.y + (self.BTN_H - btn_surf.get_height()) // 2,
                ))
                self._ability_btn_rects.append((ability_id, btn_rect))
                y += self.BTN_H + 3

        return y


class PromotionDialog:
    """
    On-board overlay that lets the player pick a promotion piece.

    Displayed centered on the board when Phase.PROMOTION_SELECTION is active.
    ``owner`` is "white" or "black" — determines which glyphs are shown.
    Returns the chosen piece type string ("queen", "rook", etc.) from
    ``handle_click()``, or None if the click missed.
    """

    ITEM_SIZE: int = SQUARE_SIZE + 10  # slightly larger than a square
    PADDING: int = 8

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,        # _ft.Font — pygame.font not used (Py 3.14 bug)
        owner: str,
    ) -> None:
        self._surface = surface
        self._font = font
        self._owner = owner
        self._glyphs = _PROMO_GLYPHS_WHITE if owner == "white" else _PROMO_GLYPHS_BLACK
        self._rects: list[tuple[str, pygame.Rect]] = []
        self._mouse_pos: tuple[int, int] = (0, 0)

    def update_mouse(self, pos: tuple[int, int]) -> None:
        self._mouse_pos = pos

    def draw(self) -> None:
        """Render the promotion dialog centered on the board."""
        total_w = len(PROMOTION_PIECES) * self.ITEM_SIZE + self.PADDING * 2
        total_h = self.ITEM_SIZE + self.PADDING * 2
        cx = BOARD_PIXEL_SIZE // 2 - total_w // 2
        cy = BOARD_PIXEL_SIZE // 2 - total_h // 2

        # Dialog background
        bg_rect = pygame.Rect(cx, cy, total_w, total_h)
        pygame.draw.rect(self._surface, DIALOG_BG, bg_rect, border_radius=8)
        pygame.draw.rect(self._surface, DIALOG_BORDER, bg_rect, 2, border_radius=8)

        self._rects = []
        for i, piece_type in enumerate(PROMOTION_PIECES):
            item_rect = pygame.Rect(
                cx + self.PADDING + i * self.ITEM_SIZE,
                cy + self.PADDING,
                self.ITEM_SIZE,
                self.ITEM_SIZE,
            )
            self._rects.append((piece_type, item_rect))

            # Hover highlight
            if item_rect.collidepoint(self._mouse_pos):
                pygame.draw.rect(self._surface, DIALOG_HOVER, item_rect, border_radius=4)

            glyph = self._glyphs.get(piece_type, "?")
            text_color = (255, 255, 255) if self._owner == "white" else (30, 30, 30)
            glyph_surf = self._font.render(glyph, True, text_color)
            gx = item_rect.x + (item_rect.width - glyph_surf.get_width()) // 2
            gy = item_rect.y + (item_rect.height - glyph_surf.get_height()) // 2
            self._surface.blit(glyph_surf, (gx, gy))

    def handle_click(self, mx: int, my: int) -> str | None:
        """Return the chosen piece type if the click hit a button, else None."""
        for piece_type, rect in self._rects:
            if rect.collidepoint(mx, my):
                return piece_type
        return None
