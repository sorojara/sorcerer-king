"""
ui/hand_view.py — Card hand display (Stage 4 / Stage 5)
=========================================================

Renders the hand strip below the board as individual card thumbnails.

Each thumbnail shows:
    • Card name (truncated if necessary)
    • Colour-coded type badge: MONSTER / SPELL / TRAP
    • Subtle background tint matching card type

Stage 5 changes:
    • Narrower tiles (80 px) so up to 7–8 cards fit in one row.
    • Label sits in the strip background (not before the cards), so all
      card tiles start from the left edge.
    • Selected card gets a bright yellow border highlight.
    • card_from_click() returns the card_id + its index for hit-testing.

When ``opponent_hand`` is provided (a list of card IDs obtained from the
raw game state for the debug "show black hand" feature), a second row is
drawn above the active hand labelled "Black hand (debug)".

When ``discard_mode=True``, the strip label turns red and each card tile
gets a red hover-able border to indicate the player must click one to
discard it.

Layout for a single card tile:
    ┌───────────────┐
    │ [TYPE] name…  │  HEIGHT = 72 px  CARD_W = 84 px
    └───────────────┘
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from game.cards.card import AnyCard, CardType
from game.core.phases import HAND_SIZE_LIMIT
from game.ui.colors import DIALOG_BORDER, HUD_LABEL, HUD_TEXT, SIDEBAR_BG

if TYPE_CHECKING:
    from game.cards.card import CardRegistry
    from game.core.observation import Observation

# ── Per-type accent colours ────────────────────────────────────────────────
_TYPE_COLOR: dict[CardType, tuple[int, int, int]] = {
    CardType.MONSTER: (180,  70,  70),   # muted red
    CardType.SPELL:   ( 70, 120, 200),   # blue
    CardType.TRAP:    (140,  90, 200),   # purple
}
_TYPE_LABEL: dict[CardType, str] = {
    CardType.MONSTER: "MON",
    CardType.SPELL:   "SPC",
    CardType.TRAP:    "TRP",
}
_CARD_BG: dict[CardType, tuple[int, int, int]] = {
    CardType.MONSTER: (50, 25, 25),
    CardType.SPELL:   (20, 30, 55),
    CardType.TRAP:    (35, 20, 50),
}

_UNKNOWN_COLOR = (90, 90, 90)
_UNKNOWN_BG    = (30, 30, 30)

_DISCARD_BORDER = (220, 60, 60)   # red border in discard mode


class HandView:
    """
    Renders a strip showing the active player's hand as card thumbnails.

    Stage 5: narrower tiles (84 px wide) start from the left edge so
    6–8 cards always fit without truncation.  A small label is drawn
    in the top-left corner of the strip background.

    ``selected_card_id`` — when set, that card tile gets a bright gold
    border to indicate it is "held" for summon targeting.

    When ``opponent_cards`` is passed to ``draw()``, a second row is drawn
    above the main one with a debug label.
    """

    HEIGHT: int = 72          # height of ONE hand row in pixels
    PADDING: int = 4
    CARD_W: int = 84          # narrower tiles → more cards visible
    CARD_H: int = 56          # height of each card tile
    CARD_GAP: int = 3         # gap between cards

    _SELECTED_BORDER = (255, 210, 0)   # gold highlight for selected card

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,        # _ft.Font — pygame.font not used (Py 3.14 bug)
        y_offset: int,
        strip_width: int,
        registry: "CardRegistry | None" = None,
        x_offset: int = 0,
    ) -> None:
        self._surface = surface
        self._font = font
        self._y = y_offset
        self._x = x_offset
        self._width = strip_width
        self._registry = registry
        # Cached tile rects for the own-hand row; updated each draw() call.
        self._own_tile_rects: list[tuple[str, pygame.Rect]] = []

    # ── Public hit-testing API ─────────────────────────────────────────────

    def card_from_click(self, mx: int, my: int, card_ids: list[str]) -> str | None:
        """
        Return the card_id at pixel (mx, my) in the own-hand row, or None.
        Uses the cached tile rects recorded by the last draw() call.
        """
        for card_id, rect in self._own_tile_rects:
            if rect.collidepoint(mx, my):
                return card_id
        return None

    # ── Public drawing API ─────────────────────────────────────────────────

    def draw(
        self,
        obs: "Observation",
        opponent_cards: list[str] | None = None,
        discard_mode: bool = False,
        selected_card_id: str | None = None,
        playable_card_ids: "set[str] | None" = None,
    ) -> None:
        """
        Render the hand strip below the board.

        ``opponent_cards``    — if given, an extra debug row is drawn above.
        ``discard_mode``      — when True, cards show red border (must discard).
        ``selected_card_id``  — card held for summon targeting (gold border).
        ``playable_card_ids`` — Stage 6: when given, any own-hand card_id NOT
                                in this set is drawn dimmed/greyed — there's no
                                legal action for it right now.  None = don't dim.
        """
        if opponent_cards is not None:
            opp_y = self._y - self.HEIGHT
            self._draw_row(
                opp_y,
                card_ids=opponent_cards,
                label="Black hand",
                label_color=(200, 120, 120),
            )

        if discard_mode:
            remaining = len(obs.own_hand) - HAND_SIZE_LIMIT
            label = f"DISCARD {remaining} — pick a card"
            label_color = _DISCARD_BORDER
        else:
            label = f"{obs.active_player.capitalize()}  hand  ({len(obs.own_hand)})"
            label_color = HUD_LABEL

        self._own_tile_rects = self._draw_row(
            self._y,
            card_ids=list(obs.own_hand),
            label=label,
            label_color=label_color,
            discard_mode=discard_mode,
            selected_card_id=selected_card_id,
            playable_card_ids=playable_card_ids,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _draw_row(
        self,
        y: int,
        card_ids: list[str],
        label: str,
        label_color: tuple,
        discard_mode: bool = False,
        selected_card_id: str | None = None,
        playable_card_ids: "set[str] | None" = None,
    ) -> list[tuple[str, pygame.Rect]]:
        """
        Draw one hand row at vertical position ``y``.
        Returns a list of (card_id, Rect) pairs for hit-testing.
        """
        tile_rects: list[tuple[str, pygame.Rect]] = []
        x0 = self._x   # left edge of the strip (Stage 6: board may be offset)

        # Background strip
        strip_bg = (45, 20, 20) if discard_mode else SIDEBAR_BG
        strip_rect = pygame.Rect(x0, y, self._width, self.HEIGHT)
        pygame.draw.rect(self._surface, strip_bg, strip_rect)
        border_color = _DISCARD_BORDER if discard_mode else DIALOG_BORDER
        pygame.draw.line(self._surface, border_color, (x0, y), (x0 + self._width, y),
                         2 if discard_mode else 1)

        # Small label in top-left corner of the strip (doesn't push cards right)
        label_surf = self._font.render(label, True, label_color)
        self._surface.blit(label_surf, (x0 + self.PADDING, y + 2))

        # Cards start from the left edge, just below the label
        label_h = label_surf.get_height() + 3
        card_top = y + label_h
        card_area_h = self.HEIGHT - label_h
        # Vertically center cards in the remaining space
        if card_area_h > self.CARD_H:
            card_top += (card_area_h - self.CARD_H) // 2

        x = x0 + self.PADDING

        if not card_ids:
            empty = self._font.render("(empty)", True, HUD_LABEL)
            self._surface.blit(empty, (x, card_top))
            return tile_rects

        for card_id in card_ids:
            # If this card would overflow the strip, show a +N overflow badge
            if x + self.CARD_W > x0 + self._width - self.PADDING:
                remaining_count = len(card_ids) - card_ids.index(card_id)
                more = self._font.render(f"+{remaining_count}", True, HUD_LABEL)
                self._surface.blit(more, (x + 2, card_top + (self.CARD_H - more.get_height()) // 2))
                break
            is_selected = (selected_card_id is not None and card_id == selected_card_id)
            is_playable = playable_card_ids is None or card_id in playable_card_ids
            self._draw_card_tile(x, card_top, card_id,
                                 discard_mode=discard_mode,
                                 selected=is_selected,
                                 playable=is_playable)
            tile_rects.append((card_id, pygame.Rect(x, card_top, self.CARD_W, self.CARD_H)))
            x += self.CARD_W + self.CARD_GAP

        return tile_rects

    def _draw_card_tile(
        self,
        x: int,
        y: int,
        card_id: str,
        discard_mode: bool = False,
        selected: bool = False,
        playable: bool = True,
    ) -> None:
        """Render a single card thumbnail at (x, y).

        Stage 6: ``playable=False`` greys the tile out — there's no legal
        action for this card right now (no valid vessel/square/target).
        """
        card: AnyCard | None = None
        if self._registry is not None and card_id in self._registry:
            card = self._registry.get(card_id)

        ct = card.card_type if card is not None else None
        bg_color    = _CARD_BG.get(ct, _UNKNOWN_BG) if ct else _UNKNOWN_BG
        badge_color = _TYPE_COLOR.get(ct, _UNKNOWN_COLOR) if ct else _UNKNOWN_COLOR
        badge_label = _TYPE_LABEL.get(ct, "???") if ct else "???"
        name        = card.name if card is not None else card_id

        tile = pygame.Rect(x, y, self.CARD_W, self.CARD_H)
        pygame.draw.rect(self._surface, bg_color, tile, border_radius=4)

        if selected:
            border = self._SELECTED_BORDER
            border_w = 3
        elif discard_mode:
            border = _DISCARD_BORDER
            border_w = 2
        else:
            border = badge_color
            border_w = 1
        pygame.draw.rect(self._surface, border, tile, border_w, border_radius=4)

        # Badge (top-left inside tile)
        badge_surf = self._font.render(badge_label, True, badge_color)
        self._surface.blit(badge_surf, (x + 3, y + 2))

        # Name (below badge, truncated to tile width)
        name_surf = self._font.render(
            _truncate(name, self.CARD_W - 6, self._font), True, HUD_TEXT
        )
        name_y = y + 2 + badge_surf.get_height() + 1
        self._surface.blit(name_surf, (x + 3, name_y))

        # Stage 6: grey overlay for unplayable cards, drawn last (on top).
        if not playable:
            dim = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            dim.fill((15, 15, 15, 165))
            self._surface.blit(dim, (x, y))


def _truncate(text: str, max_px: int, font: Any) -> str:
    """Truncate ``text`` so it fits within ``max_px`` pixels, appending '…'."""
    if font.render(text, True, (0, 0, 0)).get_width() <= max_px:
        return text
    while text and font.render(text + "…", True, (0, 0, 0)).get_width() > max_px:
        text = text[:-1]
    return text + "…"
