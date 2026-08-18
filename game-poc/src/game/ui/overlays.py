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
        self._btn_recompose: pygame.Rect | None = None
        self._mouse_pos: tuple[int, int] = (0, 0)
        # Cached ability button rects: list of (ability_id, Rect)
        self._ability_btn_rects: list[tuple[str, pygame.Rect]] = []

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
            'recompose'         — Trigger DeclareRecompose (PREPARATION only)
            'ability:<id>'      — Activate monster ability button (id = ability_id)
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
        if self._btn_recompose and self._btn_recompose.collidepoint(mx, my):
            return "recompose"
        for ability_id, rect in self._ability_btn_rects:
            if rect.collidepoint(mx, my):
                return f"ability:{ability_id}"
        return None

    def draw(
        self,
        obs: "Observation",
        player_modes: dict | None = None,
        show_black_hand: bool = False,
        show_recompose_btn: bool = False,
        card_info: "AnyCard | None" = None,
        ability_ids: "list[str] | None" = None,
        unit_statuses: "tuple[str, ...] | None" = None,
    ) -> None:
        """
        Render the sidebar based on the current observation.

        ``player_modes``       — optional dict mapping player_id → "human" | "ai".
        ``show_black_hand``    — when True, the black-hand toggle button is shown active.
        ``show_recompose_btn`` — when True, the Recompose button is drawn and clickable.
        ``card_info``          — when set, a card description panel is drawn at the
                                 bottom of the main info block (hovered card or
                                 selected monster).
        ``ability_ids``        — list of activatable ability IDs on the inspected
                                 monster; one clickable button is drawn per ability.
        ``unit_statuses``      — live statuses tuple from the inspected unit; shown
                                 in the card info panel so copied effects are visible.
        """
        modes = player_modes or {}

        # Background
        rect = pygame.Rect(self._x, 0, self.SIDEBAR_WIDTH, self._surface.get_height())
        pygame.draw.rect(self._surface, SIDEBAR_BG, rect)

        y = self.PADDING
        y = self._draw_line("SORCERER KING", self._font, HUD_TEXT, y, center=True)
        y += 8
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

        # Hint
        hint_s = self._font_small.render("(click to toggle)", True, HUD_LABEL)
        self._surface.blit(hint_s, (self._x + self.PADDING, y))
        y += hint_s.get_height() + 8

        y += 4

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

        # Recompose pending notice
        if obs.phase == Phase.RECOMPOSE_SELECTION:
            y = self._draw_line("Recompose: auto-resolving…", self._font_small, HUD_WARN, y, center=True)
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

        # Recompose button — only shown during PREPARATION for a human player
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
        else:
            self._btn_recompose = None

        # ── Card info panel OR controls hint at the bottom ─────────────────
        # _draw_controls_hint always places the Export/Import buttons at a
        # fixed absolute position at the very bottom of the sidebar.
        # When card info is present: draw the card panel first (it fills its
        # region with a solid DIALOG_BG background), then redraw only the
        # buttons on top so they remain visible and clickable.
        # When no card info: draw the full hint strip + buttons.
        self._ability_btn_rects = []
        if card_info is not None:
            y += 12
            y = self._draw_divider(y)
            y += 6
            self._draw_card_info_panel(y, card_info, ability_ids or [], unit_statuses or ())
            # Redraw only the Export/Import buttons on top of the card panel
            self._draw_controls_hint(draw_hints=False)
        else:
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

    def _draw_card_info_panel(
        self,
        y: int,
        card: "AnyCard",
        ability_ids: list[str],
        unit_statuses: "tuple[str, ...]" = (),
    ) -> int:
        """
        Draw a compact card description panel below the main info block.

        Shows:
          • Card name (bold via font_large if it fits, else font_small)
          • Type badge + archetype (monsters only)
          • Effect list (truncated to sidebar width)
          • Description (word-wrapped, max 4 lines)
          • One clickable [⚡ Activate] button per activatable ability
            (CHESS phase only — caller decides when to pass ability_ids)

        Returns the y position after the panel.
        """
        from game.cards.card import CardType, MonsterCard, SpellCard, TrapCard

        inner_w = self.SIDEBAR_WIDTH - self.PADDING * 2
        text_x = self._x + self.PADDING

        # ── Solid background so text doesn't bleed over underlying content ──
        # Stop just above the Export/Import button block so those buttons
        # remain visible when redrawn on top by _draw_controls_hint.
        btn_block = self.BTN_H * 2 + self.PADDING * 3
        panel_bottom = self._surface.get_height() - btn_block
        panel_rect = pygame.Rect(
            self._x, y - 4,
            self.SIDEBAR_WIDTH,
            panel_bottom - (y - 4),
        )
        pygame.draw.rect(self._surface, DIALOG_BG, panel_rect)
        # Thin top border to visually separate from the info block above
        pygame.draw.line(
            self._surface, DIALOG_BORDER,
            (self._x + self.PADDING, y - 3),
            (self._x + self.SIDEBAR_WIDTH - self.PADDING, y - 3),
            1,
        )

        # ── Name ──────────────────────────────────────────────────────────
        name_surf = self._font.render(card.name, True, HUD_TEXT)
        if name_surf.get_width() <= inner_w:
            self._surface.blit(name_surf, (text_x, y))
            y += name_surf.get_height() + 2
        else:
            name_surf = self._font_small.render(card.name, True, HUD_TEXT)
            self._surface.blit(name_surf, (text_x, y))
            y += name_surf.get_height() + 2

        # ── Type badge + archetype ─────────────────────────────────────────
        if card.card_type == CardType.MONSTER:
            badge_color = (200, 100, 100)
            badge = f"MON"
            archetype = getattr(card, "archetype", "")
            arch_line = f"{badge}  {archetype}" if archetype else badge
        elif card.card_type == CardType.SPELL:
            badge_color = (80, 140, 220)
            spell_type = getattr(card, "spell_type", None)
            arch_line = f"SPC  {spell_type.value}" if spell_type else "SPC"
        else:
            badge_color = (160, 100, 220)
            trigger = getattr(card, "trigger", None)
            arch_line = f"TRP  {trigger.value}" if trigger else "TRP"

        badge_surf = self._font_small.render(arch_line, True, badge_color)
        self._surface.blit(badge_surf, (text_x, y))
        y += badge_surf.get_height() + 4

        # ── Effects ───────────────────────────────────────────────────────
        effects = getattr(card, "effects", [])
        if effects:
            eff_header = self._font_small.render("Effects:", True, HUD_LABEL)
            self._surface.blit(eff_header, (text_x, y))
            y += eff_header.get_height() + 1
            for eff in effects[:4]:    # cap at 4 lines
                eff_name = eff.type.replace("_", " ")
                eff_surf = self._font_small.render(f"• {eff_name}", True, (160, 200, 160))
                if eff_surf.get_width() > inner_w:
                    # truncate
                    txt = eff_name
                    while txt and self._font_small.render(f"• {txt}…", True, (0,0,0)).get_width() > inner_w:
                        txt = txt[:-1]
                    eff_surf = self._font_small.render(f"• {txt}…", True, (160, 200, 160))
                self._surface.blit(eff_surf, (text_x + 4, y))
                y += eff_surf.get_height() + 1
            if len(effects) > 4:
                more_surf = self._font_small.render(f"  +{len(effects)-4} more", True, HUD_LABEL)
                self._surface.blit(more_surf, (text_x + 4, y))
                y += more_surf.get_height() + 1
            y += 2

        # ── Live unit statuses (only for board-inspected units) ───────────
        # Filter to statuses a player would care about; skip internal bookkeeping.
        _HIDE_STATUS_PREFIXES = ("builder_available",)
        display_statuses = [
            s for s in unit_statuses
            if not any(s.startswith(p) for p in _HIDE_STATUS_PREFIXES)
        ]
        if display_statuses:
            st_header = self._font_small.render("Active:", True, HUD_LABEL)
            self._surface.blit(st_header, (text_x, y))
            y += st_header.get_height() + 1
            for status in display_statuses:
                # Pretty-print: "copied_effect:alter_movement" → "⟳ alter movement"
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
                self._surface.blit(st_surf, (text_x + 4, y))
                y += st_surf.get_height() + 1
            y += 2

        # ── Description (word-wrap) ────────────────────────────────────────
        desc = getattr(card, "description", "") or ""
        desc = desc.strip()
        if desc:
            desc_header = self._font_small.render("About:", True, HUD_LABEL)
            self._surface.blit(desc_header, (text_x, y))
            y += desc_header.get_height() + 1
            # Simple word-wrap: split on spaces, emit up to 4 lines
            words = desc.split()
            line: list[str] = []
            lines_drawn = 0
            max_lines = 5
            for word in words:
                test = " ".join(line + [word])
                if self._font_small.render(test, True, (0, 0, 0)).get_width() > inner_w:
                    if line:
                        row_surf = self._font_small.render(" ".join(line), True, HUD_LABEL)
                        self._surface.blit(row_surf, (text_x, y))
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
                self._surface.blit(row_surf, (text_x, y))
                y += row_surf.get_height() + 1
            y += 4

        # ── Activatable ability buttons ────────────────────────────────────
        if ability_ids:
            y = self._draw_divider(y)
            y += 4
            act_header = self._font_small.render("Abilities:", True, (80, 220, 220))
            self._surface.blit(act_header, (text_x, y))
            y += act_header.get_height() + 3
            btn_w = inner_w
            for ability_id in ability_ids:
                btn_label = f"⚡ {ability_id.replace('_', ' ')}"
                btn_rect = pygame.Rect(text_x, y, btn_w, self.BTN_H)
                hover = btn_rect.collidepoint(self._mouse_pos)
                pygame.draw.rect(self._surface, DIALOG_HOVER if hover else DIALOG_BG, btn_rect, border_radius=4)
                pygame.draw.rect(self._surface, (80, 220, 220), btn_rect, 1, border_radius=4)
                btn_surf = self._font_small.render(btn_label, True, (80, 220, 220))
                self._surface.blit(btn_surf, (
                    btn_rect.x + (btn_w - btn_surf.get_width()) // 2,
                    btn_rect.y + (self.BTN_H - btn_surf.get_height()) // 2,
                ))
                self._ability_btn_rects.append((ability_id, btn_rect))
                y += self.BTN_H + 3

        return y

    def _draw_controls_hint(self, draw_hints: bool = True) -> None:
        """Draw keyboard hints and the Export/Import buttons at the bottom of the sidebar.

        ``draw_hints`` — when False, only the Export/Import buttons are drawn
        (used when the card info panel is visible so its background doesn't
        cover the buttons).
        """
        h = self._surface.get_height()
        btn_block = self.BTN_H * 2 + self.PADDING * 3

        if draw_hints:
            hints = [
                "Click piece to select",
                "Click square to move",
                "Hover card — see description",
                "Prep: click MON → vessel",
                "D    — dismiss monster",
                "A    — activate ability (CHESS)",
                "ESC  — cancel / deselect",
                "Q    — quit",
            ]
            y = h - len(hints) * 18 - self.PADDING - btn_block
            for hint in hints:
                surf = self._font_small.render(hint, True, HUD_LABEL)
                self._surface.blit(surf, (self._x + self.PADDING, y))
                y += 18

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
