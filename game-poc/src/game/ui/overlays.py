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

Also owns:
  • CardViewer — the left sidebar's one-card detail panel.
  • CardZoomOverlay — the full-screen blow-up of a single card, opened by
    right-clicking a card block in the CardViewer and dismissed by
    clicking outside it.
  • PromotionDialog — a row of four piece-choice buttons overlaid on the
    board when a pawn reaches the back rank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from game.core.phases import Phase
from game.ui.colors import (
    DIALOG_BG,
    DIALOG_BORDER,
    DIALOG_HOVER,
    HEADER_ACCENT,
    HUD_ACCENT,
    HUD_DUEL,
    HUD_LABEL,
    HUD_TEXT,
    HUD_WARN,
    PANEL_BG_BOTTOM,
    PANEL_BG_TOP,
    SIDEBAR_BG,
    WHITE,
    BLACK,
)
from game.ui.board_view import BOARD_PIXEL_SIZE, SQUARE_SIZE
from game.ui.font import load_title_font

if TYPE_CHECKING:
    from game.core.observation import Observation
    from game.cards.card import AnyCard


# Promotion piece order shown in the dialog (left → right)
PROMOTION_PIECES = ["queen", "rook", "bishop", "knight"]

# Piece glyphs reused from board_view (import at runtime to avoid circular)
_PROMO_GLYPHS_WHITE = {"queen": "♕", "rook": "♖", "bishop": "♗", "knight": "♘"}
_PROMO_GLYPHS_BLACK = {"queen": "♛", "rook": "♜", "bishop": "♝", "knight": "♞"}

# ── Panel chrome shared by SidebarOverlay / CardViewer / EventLogPanel ──────
# A bold-serif title font (loaded once per size and cached) gives panel
# section headers and dramatic one-line banners ("CHECK!", "FINAL DUEL") a
# heavier, more "storybook" weight than the plain sans body font, without
# any new font asset — see ui/font.py's load_title_font.
TITLE_HEADER_PX: int = 17   # section headers: "Players" / "Card Viewer" / "Event Log"
TITLE_BANNER_PX: int = 34   # one-line dramatic banners: "CHECK!" / "FINAL DUEL"

_title_font_cache: dict[int, Any] = {}


def title_font(size: int) -> Any:
    """Return a cached bold-serif FTFont at ``size`` px (see load_title_font)."""
    f = _title_font_cache.get(size)
    if f is None:
        f = load_title_font(size)
        _title_font_cache[size] = f
    return f


_gradient_cache: dict[tuple[int, int, tuple, tuple], "pygame.Surface"] = {}


def draw_panel_gradient(
    surface: "pygame.Surface",
    rect: "pygame.Rect",
    top: tuple[int, int, int] = PANEL_BG_TOP,
    bottom: tuple[int, int, int] = PANEL_BG_BOTTOM,
) -> None:
    """
    Fill ``rect`` with a subtle vertical gradient (``top`` → ``bottom``)
    instead of a flat color, so HUD panels read as a lit surface rather
    than a solid swatch. The gradient bitmap is built once per (size,
    color) combination and reused every frame after that.
    """
    key = (rect.width, rect.height, top, bottom)
    grad = _gradient_cache.get(key)
    if grad is None:
        w, h = max(1, rect.width), max(1, rect.height)
        try:
            import numpy as np
            t = np.array(top, dtype=np.float32)
            b = np.array(bottom, dtype=np.float32)
            span = np.arange(h, dtype=np.float32)[:, None] / max(1, h - 1)
            rows = (t[None, :] + (b - t)[None, :] * span).astype(np.uint8)
            arr = np.repeat(rows[:, None, :], w, axis=1)
            arr = np.transpose(arr, (1, 0, 2))  # (w, h, 3) for surfarray
            grad = pygame.surfarray.make_surface(arr)
        except Exception:
            # numpy unavailable for some reason — flat fallback, still correct.
            grad = pygame.Surface((w, h))
            grad.fill(top)
        _gradient_cache[key] = grad
    surface.blit(grad, rect.topleft)


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
        self._btn_card_reveal: pygame.Rect | None = None
        self._btn_speed: pygame.Rect | None = None
        self._btn_recompose: pygame.Rect | None = None
        self._btn_mercenary: pygame.Rect | None = None
        self._btn_mercenary_enabled: bool = False
        self._btn_build: pygame.Rect | None = None
        self._btn_build_enabled: bool = False
        self._btn_king: pygame.Rect | None = None
        self._btn_king_enabled: bool = False
        self._btn_ritual: pygame.Rect | None = None
        self._btn_ritual_enabled: bool = False
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
            'toggle_card_reveal' — Stage 14: Toggle the activation card popup
            'toggle_speed'      — Cycle the AI think-speed (Normal/Fast/Instant)
            'recompose'         — Trigger DeclareRecompose (PREPARATION only)
            'mercenary'         — Open Mercenary piece-type picker (PREPARATION only)
            'build'             — Stage 8: Open the Building Pool picker (PREPARATION only)
            'king'              — Stage 10: Open the King picker (Coronation/Succession, PREPARATION only)
            'ritual'            — Stage 11: Open the Ritual picker (PREPARATION only)
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
        if self._btn_card_reveal and self._btn_card_reveal.collidepoint(mx, my):
            return "toggle_card_reveal"
        if self._btn_speed and self._btn_speed.collidepoint(mx, my):
            return "toggle_speed"
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
        if self._btn_king and getattr(self, "_btn_king_enabled", False) and self._btn_king.collidepoint(mx, my):
            return "king"
        if self._btn_ritual and getattr(self, "_btn_ritual_enabled", False) and self._btn_ritual.collidepoint(mx, my):
            return "ritual"
        for token, rect in self._activatable_btn_rects:
            if rect.collidepoint(mx, my):
                return token
        return None

    def _selector_label(
        self, glyph: str, side: str, mode: str, max_width: int
    ) -> str:
        """
        Fit a controller-selector label inside its button.

        The long form is "♔ White: HEURISTIC AI"; when that overflows, the
        side word is dropped ("♔ HEURISTIC AI") — the King glyph already
        says which side the button belongs to.  A README §51 play style can
        be longer still ("♔ White: OPPORTUNIST AI"), so the last resort
        drops the " AI" suffix too: every entry but HUMAN is an AI, and the
        colour already distinguishes them.
        """
        padding = 8

        def fits(text: str) -> bool:
            return self._font_small.render(text, True, HUD_LABEL).get_width() \
                <= max_width - padding

        for candidate in (
            f"{glyph} {side}: {mode}",
            f"{glyph} {mode}",
            f"{glyph} {mode.removesuffix(' AI')}",
        ):
            if fits(candidate):
                return candidate
        return f"{glyph} {mode.removesuffix(' AI')}"

    def draw(
        self,
        obs: "Observation",
        player_modes: dict | None = None,
        mode_labels: dict | None = None,
        show_black_hand: bool = False,
        show_recompose_btn: bool = False,
        show_mercenary_btn: "bool | None" = None,
        show_build_btn: "bool | None" = None,
        show_king_btn: "bool | None" = None,
        show_ritual_btn: "bool | None" = None,
        show_territory: bool = True,
        show_card_reveal: bool = True,
        ai_speed_label: str = "Normal",
        activatable_entries: "list[tuple[str, str]] | None" = None,
    ) -> None:
        """
        Render the sidebar based on the current observation.

        ``player_modes``       — optional dict mapping player_id → "human" | "ai".
        ``mode_labels``        — optional dict mapping player_id → the exact
                                 controller name to print on that side's
                                 selector button ("HUMAN" / "RANDOM AI" /
                                 "HEURISTIC AI" / "SEARCH AI" /
                                 "MONTE CARLO AI"). Falls back to the coarse
                                 ``player_modes`` value when omitted.
        ``show_black_hand``    — when True, the black-hand toggle button is shown active.
        ``show_recompose_btn``  — when True, the Recompose button is drawn and clickable.
        ``show_mercenary_btn``  — None = don't draw (non-PREPARATION phases).
                                  True = draw enabled (clickable, gold).
                                  False = draw disabled (greyed out, not clickable).
        ``show_build_btn``      — Stage 8: same tri-state convention as
                                  ``show_mercenary_btn`` for the Building
                                  Pool picker button.
        ``show_king_btn``       — Stage 10: same tri-state convention as
                                  ``show_mercenary_btn`` for the Coronation /
                                  Succession picker button. None hides the
                                  button entirely once the King Pool is
                                  fully spent (no HIDDEN King left).
        ``show_ritual_btn``     — Stage 11: same tri-state convention as
                                  ``show_mercenary_btn`` for the Ritual
                                  picker button. None hides the button
                                  entirely once every Ritual has been
                                  activated.
        ``show_territory``      — Stage 9: current on/off state of the
                                  Territory board tint, shown as the toggle
                                  button's label (always drawn, unlike the
                                  tri-state buttons above).
        ``show_card_reveal``    — Stage 14: current on/off state of the
                                  board-center activation popup
                                  (ActivationPopup) — every Monster summon /
                                  Spell cast / Trap spring / Ritual
                                  completion / Coronation briefly flashes
                                  its card there when this is on. The
                                  persistent CardViewer-sidebar overlay
                                  (SidebarActivationOverlay) is NOT gated
                                  by this — it always shows regardless.
        ``ai_speed_label``      — Current AI think-speed tier ("Normal" /
                                  "Fast" / "Instant"), shown on the speed
                                  toggle button. Normal keeps the visual
                                  think-pause a human wants when watching;
                                  Fast/Instant shrink it (and the search
                                  budget) so a spectated AI-vs-AI match
                                  doesn't drag — see AppController._AI_SPEEDS.
        ``activatable_entries`` — Stage 6: (label, token) pairs for every currently
                                 activatable Trap/Monster-ability "in the field".
                                 One clickable row per entry; ``handle_click``
                                 returns the token verbatim. (Card detail itself
                                 lives in the left CardViewer, not here.)
        """
        modes = player_modes or {}
        labels = mode_labels or {}

        # Background
        rect = pygame.Rect(self._x, 0, self.SIDEBAR_WIDTH, self._surface.get_height())
        draw_panel_gradient(self._surface, rect)

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

        # ── Player controller selectors ───────────────────────────────────
        # One button per side. Clicking it opens the player picker, which
        # lists every controller: the AI stages by difficulty, the README
        # §51 play styles, and the human.
        y = self._draw_header("Players", y)
        btn_w = self.SIDEBAR_WIDTH - self.PADDING * 2
        btn_x = self._x + self.PADDING
        # White selector
        w_mode = modes.get("white", "human")
        w_label = self._selector_label(
            "♔", "White", labels.get("white", w_mode.upper()), btn_w
        )
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
        # Black selector
        b_mode = modes.get("black", "human")
        b_label = self._selector_label(
            "♚", "Black", labels.get("black", b_mode.upper()), btn_w
        )
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
        bh_label = "◉ Black Hand: ON" if show_black_hand else "◉ Black Hand: OFF"
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
        terr_label = "▦ Territory: ON" if show_territory else "▦ Territory: OFF"
        terr_color = HUD_ACCENT if show_territory else HUD_LABEL
        self._btn_territory = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_terr = self._btn_territory.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_terr else DIALOG_BG,
                         self._btn_territory, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_territory, 1, border_radius=4)
        ts = self._font_small.render(terr_label, True, terr_color)
        self._surface.blit(ts, (self._btn_territory.x + (btn_w - ts.get_width()) // 2,
                                self._btn_territory.y + (self.BTN_H - ts.get_height()) // 2))
        y += self.BTN_H + 4

        # Stage 14: activation card-popup toggle
        cr_label = "▤ Card Reveal: ON" if show_card_reveal else "▤ Card Reveal: OFF"
        cr_color = HUD_ACCENT if show_card_reveal else HUD_LABEL
        self._btn_card_reveal = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_cr = self._btn_card_reveal.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_cr else DIALOG_BG,
                         self._btn_card_reveal, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_card_reveal, 1, border_radius=4)
        crs = self._font_small.render(cr_label, True, cr_color)
        self._surface.blit(crs, (self._btn_card_reveal.x + (btn_w - crs.get_width()) // 2,
                                 self._btn_card_reveal.y + (self.BTN_H - crs.get_height()) // 2))
        y += self.BTN_H + 4

        # AI speed toggle — cycles Normal → Fast → Instant. Normal is the
        # only tier coloured like an "off" state; Fast/Instant use the accent
        # colour so it reads as "sped up", matching the ON/OFF colour logic
        # the toggles above use.
        speed_label = f"⚡ AI Speed: {ai_speed_label}"
        speed_color = HUD_LABEL if ai_speed_label == "Normal" else HUD_ACCENT
        self._btn_speed = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
        hover_speed = self._btn_speed.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_HOVER if hover_speed else DIALOG_BG,
                         self._btn_speed, border_radius=4)
        pygame.draw.rect(self._surface, DIALOG_BORDER, self._btn_speed, 1, border_radius=4)
        sps = self._font_small.render(speed_label, True, speed_color)
        self._surface.blit(sps, (self._btn_speed.x + (btn_w - sps.get_width()) // 2,
                                 self._btn_speed.y + (self.BTN_H - sps.get_height()) // 2))
        y += self.BTN_H + 8

        y = self._draw_divider(y)
        y += 12

        # Check warning
        if obs.own_in_check:
            y = self._draw_line("⚠  CHECK!", title_font(TITLE_BANNER_PX), HUD_WARN, y, center=True)
            y += 8
        if obs.opponent_in_check:
            opp_name = ("black" if obs.active_player == "white" else "white").capitalize()
            y = self._draw_line(f"⚠  {opp_name} in Check", self._font_small, HUD_WARN, y, center=True)
            y += 8

        # Final Duel notice
        if obs.phase == Phase.FINAL_DUEL:
            y = self._draw_line("⚔  FINAL DUEL", title_font(TITLE_BANNER_PX), HUD_DUEL, y, center=True)
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
            bd_label = "♜  Build"
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

        # King button — Stage 10: opens the Coronation/Succession picker.
        if show_king_btn is not None:
            kg_enabled = bool(show_king_btn)
            self._btn_king_enabled = kg_enabled
            kg_label = "♚  King"
            kg_color  = (210, 180, 110) if kg_enabled else (80, 72, 55)
            kg_border = (170, 140, 70) if kg_enabled else (60, 55, 45)
            y += self.BTN_H + 4
            self._btn_king = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
            hover_kg = kg_enabled and self._btn_king.collidepoint(self._mouse_pos)
            pygame.draw.rect(self._surface,
                             DIALOG_HOVER if hover_kg else DIALOG_BG,
                             self._btn_king, border_radius=4)
            pygame.draw.rect(self._surface, kg_border,
                             self._btn_king, 1, border_radius=4)
            kgs = self._font_small.render(kg_label, True, kg_color)
            self._surface.blit(kgs, (
                self._btn_king.x + (btn_w - kgs.get_width()) // 2,
                self._btn_king.y + (self.BTN_H - kgs.get_height()) // 2,
            ))
        else:
            self._btn_king = None

        # Ritual button — Stage 11: opens the Ritual picker.
        if show_ritual_btn is not None:
            rt_enabled = bool(show_ritual_btn)
            self._btn_ritual_enabled = rt_enabled
            rt_label = "✵  Ritual"
            rt_color  = (200, 130, 230) if rt_enabled else (80, 65, 90)
            rt_border = (160, 90, 210) if rt_enabled else (60, 50, 65)
            y += self.BTN_H + 4
            self._btn_ritual = pygame.Rect(btn_x, y, btn_w, self.BTN_H)
            hover_rt = rt_enabled and self._btn_ritual.collidepoint(self._mouse_pos)
            pygame.draw.rect(self._surface,
                             DIALOG_HOVER if hover_rt else DIALOG_BG,
                             self._btn_ritual, border_radius=4)
            pygame.draw.rect(self._surface, rt_border,
                             self._btn_ritual, 1, border_radius=4)
            rts = self._font_small.render(rt_label, True, rt_color)
            self._surface.blit(rts, (
                self._btn_ritual.x + (btn_w - rts.get_width()) // 2,
                self._btn_ritual.y + (self.BTN_H - rts.get_height()) // 2,
            ))
        else:
            self._btn_ritual = None

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

    def _draw_header(self, text: str, y: int) -> int:
        """Section title in the bold-serif title font, with a short
        bronze accent rule underneath — gives panel sections real
        hierarchy above the plain HUD_LABEL rows beneath them."""
        surf = title_font(TITLE_HEADER_PX).render(text, True, HUD_TEXT)
        x = self._x + self.PADDING
        self._surface.blit(surf, (x, y))
        y += surf.get_height() + 3
        pygame.draw.line(
            self._surface, HEADER_ACCENT, (x, y), (x + min(40, surf.get_width()), y), 2,
        )
        return y + 6

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
            (self._btn_export, "⇩  Export state"),
            (self._btn_import, "⇧  Import state"),
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
        # (card_id, rect) for every full card block drawn in the panel — the
        # inspected card plus, for a Ritual, the Monster it summons. A
        # RIGHT-click inside one blows that card up full-screen (see
        # CardZoomOverlay); this panel is narrow enough that the card text
        # has to be truncated, so "read it properly" needs its own view.
        self._card_block_rects: list[tuple[str, pygame.Rect]] = []
        # Stage 11: scroll state. The content below the fixed "Card Viewer"
        # header can run taller than the panel (e.g. a Ritual stacked with
        # its summoned Monster's full block) — _scroll_y is a pixel offset
        # applied to everything from the header divider down, clamped to
        # [0, _max_scroll] (recomputed every draw() call from the actual
        # content height). Resets to 0 whenever the viewed card changes so
        # a freshly-opened card always starts at the top.
        self._scroll_y: int = 0
        self._max_scroll: int = 0
        self._content_top: int = 0   # y where scrollable content begins (below the fixed header)
        self._last_card_id: "str | None" = None

    def update_mouse(self, pos: tuple[int, int]) -> None:
        self._mouse_pos = pos

    def is_over(self, mx: int, my: int) -> bool:
        """True if (mx, my) is inside this panel — for routing MOUSEWHEEL."""
        return 0 <= mx <= self._width

    def scroll(self, wheel_ticks: int) -> None:
        """
        Scroll the content by ``wheel_ticks`` (positive = wheel up = scroll
        toward the top, matching pygame's MOUSEWHEEL.y sign — negate it at
        the call site the same way ui/hand_view.py's scroll() expects, if
        "down" should move content up). Clamped to [0, _max_scroll].
        """
        if self._max_scroll <= 0:
            return
        self._scroll_y = max(0, min(self._scroll_y + wheel_ticks * 24, self._max_scroll))

    def handle_ability_click(self, mx: int, my: int) -> str | None:
        """LEFT-click hit-test for the ability buttons. Returns the ability_id."""
        for ability_id, rect in self._ability_btn_rects:
            if rect.collidepoint(mx, my):
                return ability_id
        return None

    def card_at(self, mx: int, my: int) -> str | None:
        """
        RIGHT-click hit-test for the card blocks themselves. Returns the
        card_id of the block under the pixel — the inspected card, or the
        "Summons:" block stacked under a Ritual — so the caller can open
        it in the full-screen CardZoomOverlay.
        """
        for card_id, rect in self._card_block_rects:
            if rect.collidepoint(mx, my):
                return card_id
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
        if card_id != self._last_card_id:
            self._scroll_y = 0
            self._last_card_id = card_id

        full_h = self._surface.get_height()
        h = content_height if content_height is not None else full_h
        rect = pygame.Rect(0, 0, self._width, h)
        draw_panel_gradient(self._surface, rect)
        pygame.draw.line(self._surface, DIALOG_BORDER, (self._width, 0), (self._width, h), 1)

        prev_clip = self._surface.get_clip()
        self._surface.set_clip(pygame.Rect(0, 0, self._width, h))
        try:
            x = self.PADDING
            y = self.PADDING
            header = title_font(TITLE_HEADER_PX).render("Card Viewer", True, HUD_TEXT)
            self._surface.blit(header, (x, y))
            y += header.get_height() + 3
            pygame.draw.line(
                self._surface, HEADER_ACCENT, (x, y), (x + min(40, header.get_width()), y), 2,
            )
            y += 9
            y = self._draw_divider(y)
            y += 8

            # Everything below this point scrolls — the header above stays
            # fixed. content_top is where the (unscrolled) content begins;
            # _max_scroll is recomputed below from the actual content height.
            content_top = y
            self._content_top = content_top
            y -= self._scroll_y

            self._ability_btn_rects = []
            self._card_block_rects = []

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
                block_top = y
                y = self._draw_card_block(x, y, card, ability_ids or [], unit_statuses)
                self._card_block_rects.append((
                    card.id, pygame.Rect(0, block_top, self._width, y - block_top),
                ))

                # Stage 11: a Ritual's own block is followed immediately by
                # a second, stacked block for the Monster it summons — the
                # whole reason to inspect a Ritual is usually "what do I
                # get", so show it without a second right-click.
                from game.cards.card import CardType as _CT
                if card.card_type == _CT.RITUAL and self._registry is not None:
                    summon_id = getattr(card, "summon_monster_id", "")
                    if summon_id and summon_id in self._registry:
                        try:
                            summon_card = self._registry.get(summon_id)
                        except Exception:
                            summon_card = None
                        if summon_card is not None:
                            y += 4
                            y = self._draw_divider(y)
                            y += 6
                            summon_header = self._font_small.render("Summons:", True, HUD_LABEL)
                            self._surface.blit(summon_header, (x, y))
                            y += summon_header.get_height() + 4
                            summon_top = y
                            y = self._draw_card_block(x, y, summon_card, [], ())
                            self._card_block_rects.append((
                                summon_card.id,
                                pygame.Rect(0, summon_top, self._width, y - summon_top),
                            ))

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

            # Recompute the scroll range from the actual content height just
            # drawn (undo the -_scroll_y offset to get the natural height),
            # and re-clamp in case content shrank since the last scroll
            # (e.g. switching from a Ritual+Monster combo to a plain card).
            natural_bottom = y + self._scroll_y
            visible_window = h - content_top
            self._max_scroll = max(0, (natural_bottom - content_top) - visible_window)
            self._scroll_y = min(self._scroll_y, self._max_scroll)

            # Content that got clipped out visually — above the fixed
            # header OR below the panel's bottom edge — must not stay
            # clickable.
            self._ability_btn_rects = [
                (aid, r) for aid, r in self._ability_btn_rects
                if content_top <= r.top and r.bottom <= h
            ]
            self._zone_entry_rects = [
                (cid, r) for cid, r in self._zone_entry_rects
                if content_top <= r.top and r.bottom <= h
            ]
            # A card block is usually taller than the panel, so it only has
            # to OVERLAP the visible window to stay clickable (unlike the
            # buttons above, which must be fully on-screen to be safe).
            visible = pygame.Rect(0, content_top, self._width, max(0, h - content_top))
            self._card_block_rects = [
                (cid, r.clip(visible)) for cid, r in self._card_block_rects
                if r.colliderect(visible)
            ]
        finally:
            self._surface.set_clip(prev_clip)

        # Scrollbar thumb — chrome, drawn unclipped/unscrolled on top.
        if self._max_scroll > 0:
            track_x = self._width - 5
            track_h = h - content_top
            thumb_h = max(20, int(track_h * visible_window / max(1, natural_bottom - content_top)))
            thumb_y = content_top + int((track_h - thumb_h) * (self._scroll_y / self._max_scroll))
            pygame.draw.rect(
                self._surface, DIALOG_BORDER,
                pygame.Rect(track_x, thumb_y, 3, thumb_h), border_radius=2,
            )

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
        elif card.card_type == CardType.KING:
            badge_color = (210, 180, 110)
            title = getattr(card, "title", "")
            arch_line = f"KNG  {title}" if title else "KNG"
        elif card.card_type == CardType.RITUAL:
            badge_color = (180, 90, 210)
            condition_type = getattr(card, "condition_type", "")
            arch_line = f"RIT  {condition_type}" if condition_type else "RIT"
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
                elif status.startswith("challenged_by:"):
                    label = "⚔ challenged"
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


class CardZoomOverlay:
    """
    Full-screen, modal blow-up of ONE card — opened by right-clicking a
    card block in the CardViewer, dismissed by clicking anywhere outside
    the big card (or Escape).

    The left CardViewer is only ~220 px wide, so it has to truncate: four
    effects maximum, six description lines, a 200 px-tall thumbnail. This
    overlay exists purely so a card can be *read* — large artwork, the
    full effect list, the whole description, plus the type-specific data
    the sidebar has no room for (a Monster's vessels, a Ritual's
    condition and payoff, a Trap's trigger and charges).

    Pure rendering + hit-testing; it owns no selection state. The
    AppController holds the card_id being zoomed and clears it when
    ``handle_click`` reports "close".
    """

    MAX_W: int = 980          # widest the panel is drawn WITH artwork
    MAX_W_TEXT: int = 620     # widest it is drawn when the card has no art
    MARGIN: int = 20          # minimum gap to the window edge
    HINT_H: int = 20          # strip under the panel for the dismiss hint
    PADDING: int = 18         # inner padding of the panel
    COL_GAP: int = 18         # gap between the art column and the text column
    ART_MAX_FRACTION: float = 0.58  # most of the panel width the art may take
    ART_FRACTION: float = 0.46      # art's share of the height, single-column

    _BADGES: dict = {
        "monster":  ("MONSTER",  (215, 110, 110)),
        "spell":    ("SPELL",    ( 95, 155, 235)),
        "trap":     ("TRAP",     (175, 115, 235)),
        "building": ("BUILDING", (200, 165,  95)),
        "king":     ("KING",     (225, 195, 120)),
        "ritual":   ("RITUAL",   (195, 105, 225)),
    }

    def __init__(
        self,
        surface: pygame.Surface,
        font_title: Any,
        font_body: Any,
        font_small: Any,
        registry: "object | None" = None,
        images_dir: "Any | None" = None,
    ) -> None:
        self._surface = surface
        self._font_title = font_title
        self._font_body = font_body
        self._font_small = font_small
        self._registry = registry
        self._images_dir = images_dir
        self._image_cache: "dict[str, Any]" = {}
        # The panel rect from the last draw() — everything OUTSIDE it is
        # the dismiss zone.
        self._rect: "pygame.Rect | None" = None

    # ── Interaction ────────────────────────────────────────────────────────

    def handle_click(self, mx: int, my: int) -> str | None:
        """
        Return "close" when the click landed outside the big card (the
        documented way to dismiss it), else None — a click on the card
        itself does nothing, so the player can't lose it by mis-aiming.
        """
        if self._rect is None or not self._rect.collidepoint(mx, my):
            return "close"
        return None

    # ── Rendering ──────────────────────────────────────────────────────────

    def draw(self, card_id: str) -> None:
        card = None
        if self._registry is not None and card_id is not None:
            try:
                if card_id in self._registry:
                    card = self._registry.get(card_id)
            except Exception:
                card = None
        if card is None:
            self._rect = None
            return

        sw, sh = self._surface.get_size()

        # Dim everything behind the card so the eye goes straight to it.
        scrim = pygame.Surface((sw, sh), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 205))
        self._surface.blit(scrim, (0, 0))

        # The card artwork in this game IS the printed card — name, cost,
        # requirements and rules text are all inside the image — so when
        # there is one it gets the panel's full height and the written
        # detail moves into a second column beside it. A card with no image
        # falls back to a narrower, single-column text panel.
        img = self._get_image(card)
        widest = self.MAX_W if img is not None else self.MAX_W_TEXT
        panel_w = min(widest, sw - self.MARGIN * 2)
        panel_h = sh - self.MARGIN * 2 - self.HINT_H
        panel = pygame.Rect(
            (sw - panel_w) // 2, (sh - panel_h - self.HINT_H) // 2, panel_w, panel_h,
        )
        self._rect = panel

        pygame.draw.rect(self._surface, SIDEBAR_BG, panel, border_radius=10)
        pygame.draw.rect(self._surface, DIALOG_BORDER, panel, 2, border_radius=10)

        prev_clip = self._surface.get_clip()
        self._surface.set_clip(panel)
        try:
            self._draw_body(panel, card, img)
        finally:
            self._surface.set_clip(prev_clip)

        hint = self._font_small.render(
            "click anywhere outside to close", True, HUD_LABEL,
        )
        self._surface.blit(hint, (
            panel.centerx - hint.get_width() // 2, panel.bottom + 4,
        ))

    def _draw_body(
        self, panel: pygame.Rect, card: "AnyCard", img: "pygame.Surface | None",
    ) -> None:
        from game.cards.card import CardType

        x = panel.x + self.PADDING
        inner_w = panel.w - self.PADDING * 2
        y = panel.y + self.PADDING
        avail_h = panel.h - self.PADDING * 2

        # ── Artwork ───────────────────────────────────────────────────────
        if img is not None:
            # Fill the panel's height, then cap the width so the text column
            # keeps a usable share of the panel.
            iw, ih = img.get_size()
            art_h = avail_h
            art_w = max(1, int(iw * art_h / max(1, ih)))
            max_art_w = int(panel.w * self.ART_MAX_FRACTION)
            if art_w > max_art_w:
                art_w = max_art_w
                art_h = max(1, int(ih * art_w / max(1, iw)))
            self._draw_art(x, y, art_w, art_h, img)
            x += art_w + self.COL_GAP
            inner_w = panel.right - self.PADDING - x
        else:
            art_h = int(panel.h * self.ART_FRACTION)
            self._draw_art(x, y, inner_w, art_h, None)
            y += art_h + 12

        # ── Name + type badge ─────────────────────────────────────────────
        badge_key = card.card_type.value
        badge_text, badge_color = self._BADGES.get(
            badge_key, (badge_key.upper(), HUD_LABEL),
        )
        name_surf = self._font_title.render(card.name, True, HUD_TEXT)
        if name_surf.get_width() > inner_w:
            name_surf = self._font_body.render(card.name, True, HUD_TEXT)
        self._surface.blit(name_surf, (x, y))
        y += name_surf.get_height() + 4

        subtitle = self._subtitle(card, CardType)
        line = badge_text + (f"   ·   {subtitle}" if subtitle else "")
        badge_surf = self._font_small.render(line, True, badge_color)
        self._surface.blit(badge_surf, (x, y))
        y += badge_surf.get_height() + 8

        pygame.draw.line(
            self._surface, DIALOG_BORDER, (x, y), (x + inner_w, y), 1,
        )
        y += 10

        # ── Type-specific facts the narrow sidebar has no room for ────────
        for label, value in self._detail_rows(card, CardType):
            lab = self._font_small.render(f"{label}:", True, HUD_LABEL)
            self._surface.blit(lab, (x, y))
            y = self._wrap(
                value, self._font_small, HUD_TEXT,
                x + 110, y, inner_w - 110,
            ) + 2

        # ── Full effect list (the sidebar caps it at four) ────────────────
        effects = getattr(card, "effects", ())
        if effects:
            y += 6
            head = self._font_small.render("Effects", True, (150, 210, 150))
            self._surface.blit(head, (x, y))
            y += head.get_height() + 3
            for eff in effects:
                text = f"• {eff.type.replace('_', ' ')}"
                params = getattr(eff, "params", None) or {}
                if params:
                    text += "  (" + ", ".join(
                        f"{k}={v}" for k, v in params.items()
                    ) + ")"
                y = self._wrap(
                    text, self._font_small, (170, 210, 170), x + 6, y, inner_w - 6,
                ) + 1

        # ── Full description (the sidebar caps it at six lines) ───────────
        desc = (getattr(card, "description", "") or "").strip()
        if desc:
            y += 8
            head = self._font_small.render("Description", True, HUD_LABEL)
            self._surface.blit(head, (x, y))
            y += head.get_height() + 3
            self._wrap(desc, self._font_body, HUD_TEXT, x, y, inner_w)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _subtitle(self, card: "AnyCard", CardType: Any) -> str:
        if card.card_type == CardType.MONSTER:
            return getattr(card, "archetype", "") or ""
        if card.card_type == CardType.SPELL:
            st = getattr(card, "spell_type", None)
            return st.value if st else ""
        if card.card_type == CardType.TRAP:
            tr = getattr(card, "trigger", None)
            return tr.value.replace("_", " ") if tr else ""
        if card.card_type == CardType.KING:
            return getattr(card, "title", "") or ""
        if card.card_type == CardType.RITUAL:
            return getattr(card, "condition_type", "") or ""
        if card.card_type == CardType.BUILDING:
            size = getattr(card, "size", None)
            return size.value if size else ""
        return ""

    def _detail_rows(self, card: "AnyCard", CardType: Any) -> list[tuple[str, str]]:
        """The per-type key/value facts worth showing at full size."""
        rows: list[tuple[str, str]] = []
        ct = card.card_type
        if ct == CardType.MONSTER:
            vessels = getattr(card, "supported_vessels", ())
            if vessels:
                rows.append(("Vessels", ", ".join(vessels)))
            if getattr(card, "duel_ability", None):
                rows.append(("Duel", str(card.duel_ability).replace("_", " ")))
            if getattr(card, "ritual_only", False):
                rows.append(("Source", "Ritual summon only"))
        elif ct == CardType.SPELL:
            rows.append(("Target", str(getattr(card, "target_type", "—"))))
            rows.append((
                "Area", f"radius {getattr(card, 'radius', 0)}"
                        f" ({getattr(card, 'shape', 'square')})",
            ))
        elif ct == CardType.TRAP:
            rows.append((
                "Area", f"radius {getattr(card, 'radius', 0)}"
                        f" ({getattr(card, 'shape', 'square')})",
            ))
            charges = getattr(card, "charges", 1)
            rows.append(("Charges", "unlimited" if charges is None else str(charges)))
        elif ct == CardType.BUILDING:
            rows.append(("Cost", f"{getattr(card, 'cost', '?')} pts"))
            rows.append(("Build time", f"{getattr(card, 'construction_turns', '?')} turns"))
            rows.append(("Radius", str(getattr(card, "radius", "?"))))
        elif ct == CardType.KING:
            support = getattr(card, "archetype_support", ())
            if support:
                rows.append(("Supports", ", ".join(support)))
            if getattr(card, "duel_ability", None):
                rows.append(("Duel", str(card.duel_ability).replace("_", " ")))
        elif ct == CardType.RITUAL:
            rows.append(("Condition", str(getattr(card, "condition_type", "—"))))
            vessel = getattr(card, "required_vessel", None)
            rows.append(("Vessel", vessel or "any non-King piece"))
            cond = getattr(card, "condition_type", "")
            if cond == "material":
                rows.append(("Material", str(getattr(card, "min_material", 0))))
            elif cond == "state":
                checks = getattr(card, "state_checks", ())
                if checks:
                    rows.append((
                        "Requires", ", ".join(c.replace("_", " ") for c in checks),
                    ))
            elif cond == "formation":
                rows.append(("Pattern", self._pattern_text(card)))
            rows.append(("Sacrifices", str(getattr(card, "min_sacrifices", 1))))
            rows.append(("Reveal at", f"{getattr(card, 'reveal_progress_threshold', 0)} progress"))
            summon = getattr(card, "summon_monster_id", "")
            if summon:
                rows.append(("Summons", self._card_name(summon)))
        return rows

    def _pattern_text(self, card: "AnyCard") -> str:
        parts = []
        for node in getattr(card, "pattern", ()) or ():
            off = node.get("offset", [0, 0])
            piece = node.get("piece_type", "?")
            tag = " (vessel)" if node.get("anchor") else ""
            parts.append(f"{piece}@{off[0]:+d},{off[1]:+d}{tag}")
        return "; ".join(parts) or "—"

    def _card_name(self, card_id: str) -> str:
        if self._registry is not None:
            try:
                if card_id in self._registry:
                    return self._registry.get(card_id).name
            except Exception:
                pass
        return card_id

    def _draw_art(
        self, x: int, y: int, w: int, h: int, img: "pygame.Surface | None",
    ) -> int:
        box = pygame.Rect(x, y, w, h)
        pygame.draw.rect(self._surface, DIALOG_BG, box, border_radius=6)
        pygame.draw.rect(self._surface, DIALOG_BORDER, box, 1, border_radius=6)

        if img is not None:
            iw, ih = img.get_size()
            if iw > 0 and ih > 0:
                scale = min((w - 10) / iw, (h - 10) / ih)
                nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
                scaled = pygame.transform.smoothscale(img, (nw, nh))
                self._surface.blit(scaled, (x + (w - nw) // 2, y + (h - nh) // 2))
        else:
            msg = self._font_body.render("No Image Available", True, HUD_LABEL)
            self._surface.blit(msg, (
                box.centerx - msg.get_width() // 2,
                box.centery - msg.get_height() // 2,
            ))
        return y + h

    def _get_image(self, card: "AnyCard") -> "pygame.Surface | None":
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

    def _wrap(
        self,
        text: str,
        font: Any,
        color: tuple[int, int, int],
        x: int,
        y: int,
        max_w: int,
    ) -> int:
        """Word-wrap ``text`` at ``max_w`` with no line cap; return the next y."""
        line: list[str] = []
        for word in text.split():
            probe = " ".join(line + [word])
            if line and font.render(probe, True, color).get_width() > max_w:
                surf = font.render(" ".join(line), True, color)
                self._surface.blit(surf, (x, y))
                y += surf.get_height() + 1
                line = [word]
            else:
                line.append(word)
        if line:
            surf = font.render(" ".join(line), True, color)
            self._surface.blit(surf, (x, y))
            y += surf.get_height() + 1
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


class ActivationPopup:
    """
    Stage 14 — a small, non-modal "what just got activated" card popup.

    AppController pushes onto this whenever a Monster is summoned, a
    Spell is cast, a Trap SPRINGS (never when merely placed — a Trap
    stays concealed until it fires), a Ritual completes, or a King is
    Coronated — never from direct user interaction, and only when the
    player has the "Card Reveal" sidebar toggle on. Purely decorative:
    ``draw()`` is the only method that touches the screen, and nothing in
    this class is ever consulted for hit-testing, so it can never eat a
    click the way CardZoomOverlay's modal blow-up deliberately does.

    Only one card is ever shown at a time; further pushes queue behind it
    so a burst of events (e.g. a Ritual's own card followed immediately
    by the Monster it summons) reveals as a short sequence instead of an
    illegible stack.
    """

    W: int = 320
    ART_H: int = 220
    FADE_MS: int = 220
    HOLD_MS: int = 1500

    def __init__(
        self,
        surface: pygame.Surface,
        font_title: Any,
        font_small: Any,
        registry: "object | None" = None,
        images_dir: "Any | None" = None,
    ) -> None:
        self._surface = surface
        self._font_title = font_title
        self._font_small = font_small
        self._registry = registry
        self._images_dir = images_dir
        self._image_cache: "dict[str, Any]" = {}
        self._queue: "list[tuple[str, str]]" = []       # (card_id, kind_label)
        self._current: "tuple[str, str, int] | None" = None  # (card_id, kind_label, shown_since_ms)

    def push(self, card_id: "str | None", kind_label: str) -> None:
        """Queue one card to reveal. Silently ignored if ``card_id`` is falsy
        or unknown — a missing/unresolved card_id should never crash the UI."""
        if card_id:
            self._queue.append((card_id, kind_label))

    def draw(self, center_x: int, center_y: int) -> None:
        """
        Draw the current card (advancing the queue as entries expire),
        centered at ``(center_x, center_y)`` — the board's own center, so
        the reveal lands where the player is already looking.
        """
        now = pygame.time.get_ticks()
        total = self.FADE_MS * 2 + self.HOLD_MS
        if self._current is None:
            if not self._queue:
                return
            card_id, kind_label = self._queue.pop(0)
            self._current = (card_id, kind_label, now)
        card_id, kind_label, shown_since = self._current
        elapsed = now - shown_since
        if elapsed >= total:
            self._current = None
            return

        if elapsed < self.FADE_MS:
            alpha = int(255 * elapsed / self.FADE_MS)
        elif elapsed > total - self.FADE_MS:
            alpha = int(255 * (total - elapsed) / self.FADE_MS)
        else:
            alpha = 255
        alpha = max(0, min(255, alpha))

        card = None
        if self._registry is not None:
            try:
                if card_id in self._registry:
                    card = self._registry.get(card_id)
            except Exception:
                card = None
        name = getattr(card, "name", None) or card_id
        img = self._get_image(card) if card is not None else None

        panel_h = self.ART_H + 62
        panel = pygame.Surface((self.W, panel_h), pygame.SRCALPHA)
        panel_rect = panel.get_rect()
        pygame.draw.rect(panel, (*DIALOG_BG, 235), panel_rect, border_radius=8)
        pygame.draw.rect(panel, (*HEADER_ACCENT, 255), panel_rect, 2, border_radius=8)

        art_rect = pygame.Rect(8, 8, self.W - 16, self.ART_H)
        pygame.draw.rect(panel, (*DIALOG_BORDER, 255), art_rect, border_radius=5)
        if img is not None and img.get_width() > 0 and img.get_height() > 0:
            iw, ih = img.get_size()
            scale = min((art_rect.w - 6) / iw, (art_rect.h - 6) / ih)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            scaled = pygame.transform.smoothscale(img, (nw, nh))
            panel.blit(scaled, (
                art_rect.x + (art_rect.w - nw) // 2, art_rect.y + (art_rect.h - nh) // 2,
            ))

        kind_surf = self._font_small.render(kind_label.upper(), True, HEADER_ACCENT)
        ky = art_rect.bottom + 6
        panel.blit(kind_surf, ((self.W - kind_surf.get_width()) // 2, ky))

        name_surf = self._font_title.render(name, True, HUD_TEXT)
        if name_surf.get_width() > self.W - 16:
            name_surf = self._font_small.render(name, True, HUD_TEXT)
        ny = ky + kind_surf.get_height() + 3
        panel.blit(name_surf, ((self.W - name_surf.get_width()) // 2, ny))

        panel.set_alpha(alpha)
        x = center_x - self.W // 2
        y = center_y - panel_h // 2
        self._surface.blit(panel, (x, y))

    def _get_image(self, card: "AnyCard") -> "pygame.Surface | None":
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


class SidebarActivationOverlay:
    """
    Stage 14b — a "just activated" card overlay, floated on top of the
    left CardViewer sidebar. Companion to ``ActivationPopup`` (the
    transient flash centered on the board) — and, like that one, transient
    too: a card fades out on its own after a couple of seconds
    (FADE_MS/HOLD_MS below). The one difference from ActivationPopup is
    that THIS overlay is never gated by the "Card Reveal" toggle — it
    always shows regardless (see AppController._push_card_reveal) — and it
    can be dismissed early by RIGHT-CLICKing it, which AppController wires
    to two things at once: ``dismiss()`` here, and setting its own
    ``_viewer_card_id`` so the same card opens in the CardViewer proper
    right below.

    Like ``ActivationPopup``, only one card shows at a time; further
    pushes queue behind it so a burst of events (a Ritual completing,
    immediately followed by the Monster it summons) surfaces as a short
    sequence instead of one card silently overwriting the last.

    Purely passive about input — ``hit_test()`` is the only thing an
    outside caller ever consults, and only for right-clicks; a stray
    left-click passes straight through to whatever is underneath.
    """

    W: int = 210
    ART_H: int = 150
    FADE_MS: int = 200
    HOLD_MS: int = 1600   # ~1.8s fully visible+fading — "1 or 2 seconds"

    def __init__(
        self,
        surface: pygame.Surface,
        font_title: Any,
        font_small: Any,
        registry: "object | None" = None,
        images_dir: "Any | None" = None,
    ) -> None:
        self._surface = surface
        self._font_title = font_title
        self._font_small = font_small
        self._registry = registry
        self._images_dir = images_dir
        self._image_cache: "dict[str, Any]" = {}
        self._queue: "list[tuple[str, str]]" = []      # (card_id, kind_label)
        self._current: "tuple[str, str, int] | None" = None  # (card_id, kind_label, shown_since_ms)
        self._rect: "pygame.Rect | None" = None         # last-drawn bounds, for hit_test()

    def push(self, card_id: "str | None", kind_label: str) -> None:
        """Queue one card to reveal. Silently ignored if ``card_id`` is falsy
        — a missing/unresolved card_id should never crash the UI."""
        if card_id:
            self._queue.append((card_id, kind_label))

    def current_card_id(self) -> "str | None":
        """The card_id currently showing, or None if nothing is."""
        return self._current[0] if self._current is not None else None

    def dismiss(self) -> None:
        """Drop whatever is currently showing — an early dismissal (a
        right-click) on top of the automatic fade-out below. The next
        queued card (if any) appears on the following ``draw()`` call."""
        self._current = None
        self._rect = None

    def hit_test(self, mx: int, my: int) -> bool:
        """True if (mx, my) lands inside the panel as last drawn. False
        (never crashes) once nothing is showing."""
        return self._rect is not None and self._rect.collidepoint(mx, my)

    def draw(self, center_x: int, center_y: int) -> None:
        """
        Draw the current card (pulling the next one off the queue if
        nothing is showing yet, fading it in/out over its lifetime),
        centered at ``(center_x, center_y)``. No-op — and clears the
        hit-test rect — when nothing is queued or showing.
        """
        now = pygame.time.get_ticks()
        total = self.FADE_MS * 2 + self.HOLD_MS
        if self._current is None:
            if not self._queue:
                self._rect = None
                return
            card_id, kind_label = self._queue.pop(0)
            self._current = (card_id, kind_label, now)
        card_id, kind_label, shown_since = self._current
        elapsed = now - shown_since
        if elapsed >= total:
            self._current = None
            self._rect = None
            return

        if elapsed < self.FADE_MS:
            alpha = int(255 * elapsed / self.FADE_MS)
        elif elapsed > total - self.FADE_MS:
            alpha = int(255 * (total - elapsed) / self.FADE_MS)
        else:
            alpha = 255
        alpha = max(0, min(255, alpha))

        card = None
        if self._registry is not None:
            try:
                if card_id in self._registry:
                    card = self._registry.get(card_id)
            except Exception:
                card = None
        name = getattr(card, "name", None) or card_id
        img = self._get_image(card) if card is not None else None

        panel_h = self.ART_H + 78
        panel = pygame.Surface((self.W, panel_h), pygame.SRCALPHA)
        panel_rect = panel.get_rect()
        pygame.draw.rect(panel, (*DIALOG_BG, 250), panel_rect, border_radius=8)
        pygame.draw.rect(panel, (*HEADER_ACCENT, 255), panel_rect, 3, border_radius=8)

        art_rect = pygame.Rect(8, 8, self.W - 16, self.ART_H)
        pygame.draw.rect(panel, (*DIALOG_BORDER, 255), art_rect, border_radius=5)
        if img is not None and img.get_width() > 0 and img.get_height() > 0:
            iw, ih = img.get_size()
            scale = min((art_rect.w - 6) / iw, (art_rect.h - 6) / ih)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            scaled = pygame.transform.smoothscale(img, (nw, nh))
            panel.blit(scaled, (
                art_rect.x + (art_rect.w - nw) // 2, art_rect.y + (art_rect.h - nh) // 2,
            ))

        kind_surf = self._font_small.render(kind_label.upper(), True, HEADER_ACCENT)
        ky = art_rect.bottom + 6
        panel.blit(kind_surf, ((self.W - kind_surf.get_width()) // 2, ky))

        name_surf = self._font_title.render(name, True, HUD_TEXT)
        if name_surf.get_width() > self.W - 16:
            name_surf = self._font_small.render(name, True, HUD_TEXT)
        ny = ky + kind_surf.get_height() + 3
        panel.blit(name_surf, ((self.W - name_surf.get_width()) // 2, ny))

        hint_surf = self._font_small.render("right-click to view", True, HUD_LABEL)
        hy = ny + name_surf.get_height() + 6
        panel.blit(hint_surf, ((self.W - hint_surf.get_width()) // 2, hy))

        panel.set_alpha(alpha)
        x = center_x - self.W // 2
        y = center_y - panel_h // 2
        self._surface.blit(panel, (x, y))
        self._rect = pygame.Rect(x, y, self.W, panel_h)

    def _get_image(self, card: "AnyCard") -> "pygame.Surface | None":
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
