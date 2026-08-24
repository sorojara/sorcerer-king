"""
ui/ritual_bar.py — Stage 11: the Ritual status strip above the board
======================================================================

A two-row band pinned above the chess board that answers, at a glance,
"what Rituals are in play and how much does each side know about them?"

    ┌ BLACK ─┬───────────────┬───────────────┬───────────────┐
    │        │ Sealed Ritual │ Dragon rite…  │ Rite of the…  │
    │  (them)│ ◆ SEALED      │ ◈ FORETOLD    │ ◉ REVEALED    │
    ├ WHITE ─┼───────────────┼───────────────┼───────────────┤
    │  (you) │ Rite of the…  │ Circle of th… │ Chant of Ash  │
    │        │ ◆ SEALED 2/3  │ ◈ FORETOLD    │ ✦ SUMMONED    │
    └────────┴───────────────┴───────────────┴───────────────┘

Row order matches the board underneath it: black on top, white below —
so a side's Rituals sit on the same edge as its pieces.

Information model (the whole point of this widget):
  • The VIEWER's own row is drawn from ``Observation.own_rituals``
    (RitualState — full information: identity, revelation, progress,
    activated) and is therefore always fully legible to them.
  • The rival's row is drawn from ``Observation.opponent_ritual_info``
    (PublicRitualInfo — already redacted by core/observation.py, including
    false_prophecy's fabricated FORETOLD bluff). This widget NEVER reaches
    past that projection, so it cannot leak hidden information even by
    accident: a SEALED rival Ritual simply has no name to draw.
  • Which row is "own" flips with the Observation the AppController hands
    in, so the panel swaps perspective as the turn passes.

Pure rendering + hit-testing, like every other ui/ widget: it never
mutates game state. ``ritual_at()`` reports the card_id under a pixel
(None for a chip whose identity the viewer isn't entitled to), so the
AppController can route a right-click into the CardViewer.
"""

from __future__ import annotations

from typing import Any

import pygame

from game.core.phases import RevelationState
from game.ui.archetype_colors import aura_color_for
from game.ui.board_view import BOARD_OFFSET_Y
from game.ui.colors import (
    DIALOG_BG,
    DIALOG_BORDER,
    HUD_LABEL,
    HUD_TEXT,
    SIDEBAR_BG,
)

# ── State palette ──────────────────────────────────────────────────────────
# One color per RevelationState, plus "activated" (the Ritual has fired
# and its Monster is on the board — nothing left to reveal).
_SEALED_COLOR: tuple[int, int, int] = (130, 130, 150)
_FORETOLD_COLOR: tuple[int, int, int] = (230, 175, 60)
_REVEALED_COLOR: tuple[int, int, int] = (215, 110, 235)
_DONE_COLOR: tuple[int, int, int] = (90, 220, 150)

_STATE_BADGE: dict[RevelationState, tuple[str, tuple[int, int, int]]] = {
    RevelationState.SEALED: ("◆ SEALED", _SEALED_COLOR),
    RevelationState.FORETOLD: ("◈ FORETOLD", _FORETOLD_COLOR),
    RevelationState.REVEALED: ("◉ REVEALED", _REVEALED_COLOR),
}

_OWN_ROW_BG: tuple[int, int, int] = (38, 40, 54)
_RIVAL_ROW_BG: tuple[int, int, int] = (30, 30, 40)
_TURN_MARKER: tuple[int, int, int] = (255, 210, 0)   # gold — matches SUMMON_VESSEL_DOT

# Height of the whole band, in pixels — by definition the board's own top
# offset, since the band is exactly the space above the squares. Board
# geometry lives in ui/board_view.py (see the note on BOARD_OFFSET_Y);
# this alias just gives the widget a name for its own height.
BAR_HEIGHT: int = BOARD_OFFSET_Y   # 132


class RitualBar:
    """
    Renders both players' Ritual pools as a band of chips above the board.

    Construct once with the window surface and the board's x/width so the
    band lines up with the squares beneath it; call ``draw()`` every frame
    with the Observation whose owner is the viewer.
    """

    PAD: int = 6           # outer padding inside the band
    ROW_H: int = 56        # height of one player's row
    ROW_GAP: int = 4       # vertical gap between the two rows
    LABEL_W: int = 70      # width of the "WHITE"/"BLACK" gutter column
    CHIP_GAP: int = 5      # horizontal gap between chips
    CHIP_PAD: int = 6      # inner padding of one chip
    ACCENT_W: int = 4      # width of a chip's left archetype accent bar

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,          # FTFont — chip titles
        font_small: Any,    # FTFont — sub-lines / badges
        x_offset: int,
        width: int,
        registry: "object | None" = None,
    ) -> None:
        self._surface = surface
        self._font = font
        self._font_small = font_small
        self._x = x_offset
        self._width = width
        self._registry = registry
        self._mouse_pos: tuple[int, int] = (0, 0)
        # (card_id | None, rect) per chip — refreshed every draw() call.
        # card_id is None when the viewer isn't entitled to the identity
        # (a rival's SEALED or FORETOLD Ritual), which makes the chip
        # un-inspectable rather than a hidden-info leak.
        self._chip_rects: list[tuple["str | None", pygame.Rect]] = []

    # ── Interaction ────────────────────────────────────────────────────────

    def update_mouse(self, pos: tuple[int, int]) -> None:
        self._mouse_pos = pos

    def ritual_at(self, mx: int, my: int) -> "str | None":
        """
        The card_id of the chip under (mx, my), or None — either because
        no chip is there or because that chip's identity is hidden from
        the viewer. Uses the rects cached by the last ``draw()``.
        """
        for card_id, rect in self._chip_rects:
            if rect.collidepoint(mx, my):
                return card_id
        return None

    # ── Rendering ──────────────────────────────────────────────────────────

    def draw(self, obs: "Any", active_player: "str | None" = None) -> None:
        """
        ``obs``            — the Observation of the player currently looking
                             at the screen. Their own pool is drawn in full;
                             the other side's comes from the redacted
                             ``opponent_ritual_info`` projection.
        ``active_player``  — "white"/"black" whose turn it is, used only to
                             mark the row with a gold "to move" caret.
                             Defaults to ``obs.active_player``.
        """
        viewer = getattr(obs, "player_id", None) or obs.active_player
        to_move = active_player or obs.active_player

        band = pygame.Rect(self._x, 0, self._width, BAR_HEIGHT)
        pygame.draw.rect(self._surface, SIDEBAR_BG, band)
        pygame.draw.line(
            self._surface, DIALOG_BORDER,
            (self._x, BAR_HEIGHT - 1), (self._x + self._width, BAR_HEIGHT - 1), 1,
        )

        self._chip_rects = []

        # Row order follows the board beneath: black on top, white below,
        # so each side's Rituals sit on the same edge as its pieces.
        y = self.PAD
        for side in ("black", "white"):
            is_viewer = side == viewer
            entries = (
                list(obs.own_rituals) if is_viewer
                else list(obs.opponent_ritual_info)
            )
            self._draw_row(
                y=y,
                side=side,
                entries=entries,
                is_viewer=is_viewer,
                to_move=(side == to_move),
                reveal_spent=bool(getattr(obs, "own_ritual_reveal_used", False)),
            )
            y += self.ROW_H + self.ROW_GAP

    # ── Internals ──────────────────────────────────────────────────────────

    def _draw_row(
        self,
        y: int,
        side: str,
        entries: list[Any],
        is_viewer: bool,
        to_move: bool,
        reveal_spent: bool = False,
    ) -> None:
        row = pygame.Rect(self._x + self.PAD, y, self._width - self.PAD * 2, self.ROW_H)
        pygame.draw.rect(
            self._surface, _OWN_ROW_BG if is_viewer else _RIVAL_ROW_BG,
            row, border_radius=5,
        )
        pygame.draw.rect(
            self._surface,
            _TURN_MARKER if to_move else DIALOG_BORDER,
            row, 1, border_radius=5,
        )

        # ── Gutter: side name + who the viewer is ─────────────────────────
        lx = row.x + 7
        ly = row.y + 7
        caret = "▶ " if to_move else ""
        name_surf = self._font_small.render(
            f"{caret}{side.upper()}", True,
            _TURN_MARKER if to_move else HUD_TEXT,
        )
        self._surface.blit(name_surf, (lx, ly))
        ly += name_surf.get_height() + 2
        who_surf = self._font_small.render(
            "you" if is_viewer else "rival", True, HUD_LABEL,
        )
        self._surface.blit(who_surf, (lx, ly))
        ly += who_surf.get_height() + 2
        if is_viewer:
            # README §15.2 — one voluntary revelation step per turn. Showing
            # it here is the difference between "I could advance a Ritual"
            # being a thing the player remembers and a thing they can see.
            has_room = any(
                getattr(e, "revelation", None) != RevelationState.REVEALED
                and not getattr(e, "activated", False)
                for e in entries
            )
            if not has_room:
                budget, budget_color = "—", HUD_LABEL
            elif reveal_spent:
                budget, budget_color = "reveal ✗", HUD_LABEL
            else:
                budget, budget_color = "reveal ✓", _TURN_MARKER
        else:
            budget, budget_color = f"{len(entries)} rit.", HUD_LABEL
        self._surface.blit(
            self._font_small.render(budget, True, budget_color), (lx, ly),
        )

        chips_x = row.x + self.LABEL_W
        chips_w = row.right - chips_x - 6
        pygame.draw.line(
            self._surface, DIALOG_BORDER,
            (chips_x - 5, row.y + 5), (chips_x - 5, row.bottom - 5), 1,
        )

        if not entries:
            hint = self._font_small.render("— no Rituals in play —", True, HUD_LABEL)
            self._surface.blit(hint, (
                chips_x + 6, row.y + (self.ROW_H - hint.get_height()) // 2,
            ))
            return

        n = len(entries)
        chip_w = max(60, (chips_w - self.CHIP_GAP * (n - 1)) // n)
        cx = chips_x
        for entry in entries:
            chip = pygame.Rect(cx, row.y + 4, chip_w, self.ROW_H - 8)
            card_id = (
                self._draw_own_chip(chip, entry) if is_viewer
                else self._draw_rival_chip(chip, entry)
            )
            self._chip_rects.append((card_id, chip))
            cx += chip_w + self.CHIP_GAP

    def _card(self, ritual_id: "str | None") -> "Any | None":
        if not ritual_id or self._registry is None:
            return None
        try:
            if ritual_id in self._registry:
                return self._registry.get(ritual_id)
        except Exception:
            pass
        return None

    def _chip_frame(
        self,
        chip: pygame.Rect,
        accent: tuple[int, int, int],
        border: tuple[int, int, int],
        inspectable: bool,
    ) -> tuple[int, int, int]:
        """Draw a chip's background, accent bar and border; return (x, y, inner_w)."""
        hover = inspectable and chip.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, DIALOG_BG, chip, border_radius=4)
        pygame.draw.rect(
            self._surface, accent,
            pygame.Rect(chip.x + 1, chip.y + 1, self.ACCENT_W, chip.h - 2),
            border_top_left_radius=4, border_bottom_left_radius=4,
        )
        pygame.draw.rect(
            self._surface, HUD_TEXT if hover else border, chip, 1, border_radius=4,
        )
        x = chip.x + self.ACCENT_W + self.CHIP_PAD
        return x, chip.y + 4, chip.right - x - self.CHIP_PAD

    def _draw_own_chip(self, chip: pygame.Rect, rstate: Any) -> "str | None":
        """
        One of the viewer's OWN Rituals — full information, always. Shows
        its true revelation state so the player can see how much the rival
        has already learned about it.
        """
        card = self._card(getattr(rstate, "ritual_id", None))
        revelation = getattr(rstate, "revelation", RevelationState.SEALED)
        activated = bool(getattr(rstate, "activated", False))
        archetype = getattr(card, "archetype", "") if card else ""
        accent = aura_color_for(archetype or None)

        badge_text, badge_color = _STATE_BADGE.get(
            revelation, ("◆ SEALED", _SEALED_COLOR),
        )
        if activated:
            badge_text, badge_color = "✦ SUMMONED", _DONE_COLOR

        x, y, inner_w = self._chip_frame(chip, accent, badge_color, card is not None)

        title = getattr(card, "name", None) or getattr(rstate, "ritual_id", "?")
        y = self._blit_clipped(self._font, title, HUD_TEXT, x, y, inner_w) + 1

        vessel = getattr(card, "required_vessel", None) if card else None
        sub = " · ".join(p for p in (archetype, f"vessel: {vessel}" if vessel else "") if p)
        y = self._blit_clipped(
            self._font_small, sub or "—", HUD_LABEL, x, y, inner_w,
        ) + 1

        # Progress toward the NEXT revelation step (ritual_acolyte's
        # ritual_progress_boost) — meaningless once fully revealed/fired.
        tail = badge_text
        threshold = getattr(card, "reveal_progress_threshold", 0) if card else 0
        progress = int(getattr(rstate, "progress", 0) or 0)
        if (
            not activated
            and revelation != RevelationState.REVEALED
            and threshold
        ):
            tail = f"{badge_text}  {progress}/{threshold}"
        if int(getattr(rstate, "bluff_turns", 0) or 0) > 0:
            tail += "  ✜bluff"
        self._blit_clipped(self._font_small, tail, badge_color, x, y, inner_w)

        return getattr(rstate, "ritual_id", None) if card is not None else None

    def _draw_rival_chip(self, chip: pygame.Rect, info: Any) -> "str | None":
        """
        One of the RIVAL's Rituals, straight from the redacted
        PublicRitualInfo — SEALED shows nothing but the fact a slot exists,
        FORETOLD shows archetype + required Vessel, REVEALED shows the card.
        """
        revelation = getattr(info, "revelation", RevelationState.SEALED)
        badge_text, badge_color = _STATE_BADGE.get(
            revelation, ("◆ SEALED", _SEALED_COLOR),
        )

        card_id: "str | None" = None
        card = None
        if revelation == RevelationState.REVEALED:
            card_id = getattr(info, "ritual_id", None)
            card = self._card(card_id)

        archetype = (
            getattr(card, "archetype", "") if card
            else (getattr(info, "archetype", None) or "")
        )
        accent = aura_color_for(archetype or None) if archetype else _SEALED_COLOR

        x, y, inner_w = self._chip_frame(chip, accent, badge_color, card is not None)

        if revelation == RevelationState.SEALED:
            title, title_color = "Sealed Ritual", HUD_LABEL
            sub = "identity unknown"
        elif revelation == RevelationState.FORETOLD:
            title, title_color = "Foretold Ritual", HUD_TEXT
            vessel = getattr(info, "required_vessel", None)
            sub = " · ".join(
                p for p in (archetype or "?", f"vessel: {vessel}" if vessel else "") if p
            )
        else:
            title = getattr(card, "name", None) or (card_id or "Revealed Ritual")
            title_color = HUD_TEXT
            vessel = getattr(card, "required_vessel", None) if card else None
            sub = " · ".join(
                p for p in (archetype or "?", f"vessel: {vessel}" if vessel else "") if p
            )

        y = self._blit_clipped(self._font, title, title_color, x, y, inner_w) + 1
        y = self._blit_clipped(self._font_small, sub, HUD_LABEL, x, y, inner_w) + 1
        self._blit_clipped(self._font_small, badge_text, badge_color, x, y, inner_w)

        # Only a REVEALED Ritual has an identity to open in the CardViewer.
        return card_id if card is not None else None

    def _blit_clipped(
        self,
        font: Any,
        text: str,
        color: tuple[int, int, int],
        x: int,
        y: int,
        max_w: int,
    ) -> int:
        """Blit ``text`` truncated with an ellipsis to ``max_w``; return the next y."""
        surf = font.render(text, True, color)
        if surf.get_width() > max_w:
            trimmed = text
            while trimmed and font.render(trimmed + "…", True, color).get_width() > max_w:
                trimmed = trimmed[:-1]
            surf = font.render(trimmed + "…", True, color)
        self._surface.blit(surf, (x, y))
        return y + surf.get_height()
