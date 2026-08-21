"""
ui/hand_view.py — Card hand display (Stage 4 / Stage 5)
=========================================================

Renders the hand strip below the board as individual card thumbnails.

Each thumbnail shows:
    • Card artwork (scaled to fill the tile), with card name in a bottom bar.
    • Fallback to colour-coded type badge + name when artwork is missing.
    • Colour-coded type badge: MONSTER / SPELL / TRAP.

Stage 5 changes:
    • Narrower tiles (80 px) so up to 7–8 cards fit in one row.
    • Label sits in the strip background (not before the cards), so all
      card tiles start from the left edge.
    • Selected card gets a bright yellow border highlight.
    • card_from_click() returns the card_id + its index for hit-testing.

Scrollable hand (current):
    • Larger tiles (110 × 90 px) — readable artwork.
    • When more cards exist than fit in the strip, left/right chevron
      arrows appear on the strip edges; scrolling is also triggered by the
      mouse wheel when the cursor is over the hand area.
    • HandView.scroll(delta) / reset_scroll() are called by AppController.
    • The strip still clips cards correctly so nothing bleeds outside the
      board width.

When ``opponent_hand`` is provided (a list of card IDs obtained from the
raw game state for the debug "show black hand" feature), a second row is
drawn above the active hand labelled "Black hand (debug)".

When ``discard_mode=True``, the strip label turns red and each card tile
gets a red hover-able border to indicate the player must click one to
discard it.

Layout for a single card tile:
    ┌───────────────┐
    │ [artwork]     │  HEIGHT = 106 px  CARD_W = 110 px
    │               │
    │ name…         │
    └───────────────┘
"""

from __future__ import annotations

from pathlib import Path
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

_DISCARD_BORDER  = (220, 60, 60)   # red border in discard mode
_RECOMPOSE_SELECTED_BORDER = (60, 220, 200)   # teal border — card chosen for recompose
_RECOMPOSE_IDLE_BORDER     = (80, 140, 130)   # muted teal border — card not yet chosen
_MERCENARY_CHOSEN_BORDER   = (200, 160, 80)   # gold border — Monster card queued for sacrifice
_MERCENARY_IDLE_BORDER     = (120, 90,  40)   # muted gold — valid but not yet chosen
_MERCENARY_INVALID_BORDER  = (60,  60,  60)   # grey — card cannot be sacrificed (not a Monster)


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

    HEIGHT: int = 106         # height of ONE hand row in pixels
    PADDING: int = 4
    CARD_W: int = 110         # wider tiles — readable artwork
    CARD_H: int = 88          # height of each card tile
    CARD_GAP: int = 4         # gap between cards

    # Scroll chevron
    _CHEVRON_W: int = 18      # width of the left/right scroll arrow zones

    _SELECTED_BORDER = (255, 210, 0)   # gold highlight for selected card

    def __init__(
        self,
        surface: pygame.Surface,
        font: Any,        # _ft.Font — pygame.font not used (Py 3.14 bug)
        y_offset: int,
        strip_width: int,
        registry: "CardRegistry | None" = None,
        x_offset: int = 0,
        images_dir: "Path | None" = None,
    ) -> None:
        self._surface = surface
        self._font = font
        self._y = y_offset
        self._x = x_offset
        self._width = strip_width
        self._registry = registry
        self._images_dir = images_dir
        # image_path -> loaded Surface, or None if load failed/missing
        self._image_cache: "dict[str, Any]" = {}
        # Cached tile rects for the own-hand row; updated each draw() call.
        self._own_tile_rects: list[tuple[str, pygame.Rect]] = []
        # How many card-widths the hand is scrolled to the right (0 = leftmost).
        self._scroll_offset: int = 0

    # ── Scroll API ─────────────────────────────────────────────────────────

    def scroll(self, delta: int, total_cards: int) -> None:
        """
        Scroll the hand by ``delta`` cards (positive = right, negative = left).
        Clamped so the last card is never scrolled out of view.
        ``total_cards`` is the current hand size, used to compute the maximum offset.
        """
        visible = max(1, (self._width - self.PADDING * 2) // (self.CARD_W + self.CARD_GAP))
        max_offset = max(0, total_cards - visible)
        self._scroll_offset = max(0, min(max_offset, self._scroll_offset + delta))

    def reset_scroll(self) -> None:
        """Reset scroll position to the leftmost card."""
        self._scroll_offset = 0

    def is_over_hand(self, mx: int, my: int) -> bool:
        """Return True if the pixel (mx, my) is within the own-hand strip."""
        return (
            self._x <= mx < self._x + self._width
            and self._y <= my < self._y + self.HEIGHT
        )

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
        recompose_mode: bool = False,
        recompose_selected_ids: "set[str] | None" = None,
        mercenary_mode: bool = False,
        mercenary_selected_ids: "set[str] | None" = None,
        mercenary_valid_ids: "set[str] | None" = None,
    ) -> None:
        """
        Render the hand strip below the board.

        ``opponent_cards``       — if given, an extra debug row is drawn above.
        ``discard_mode``         — when True, cards show red border (must discard).
        ``selected_card_id``     — card held for summon targeting (gold border).
        ``playable_card_ids``    — Stage 6: when given, any own-hand card_id NOT
                                   in this set is drawn dimmed/greyed — there's no
                                   legal action for it right now.  None = don't dim.
        ``recompose_mode``       — Stage 7: True while the human is picking cards
                                   to return during RECOMPOSE_SELECTION.
        ``recompose_selected_ids`` — card IDs the player has toggled for return;
                                   selected cards get a bright teal border, others
                                   a muted teal border.
        ``mercenary_mode``       — Stage 7: True while picking Monster cards to sacrifice.
        ``mercenary_selected_ids`` — card IDs chosen for sacrifice (gold border).
        ``mercenary_valid_ids``  — card IDs that are valid Monster cards to sacrifice;
                                   others are shown greyed out.
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
        elif recompose_mode:
            n = obs.pending_decision_min if hasattr(obs, "pending_decision_min") else 0
            already = len(recompose_selected_ids) if recompose_selected_ids else 0
            label = f"RECOMPOSE — pick {n} card(s) to return  ({already}/{n})"
            label_color = _RECOMPOSE_SELECTED_BORDER
        elif mercenary_mode:
            n = obs.pending_decision_min if hasattr(obs, "pending_decision_min") else 0
            already = len(mercenary_selected_ids) if mercenary_selected_ids else 0
            label = f"MERCENARY — sacrifice {n} Monster(s)  ({already}/{n})"
            label_color = _MERCENARY_CHOSEN_BORDER
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
            recompose_mode=recompose_mode,
            recompose_selected_ids=recompose_selected_ids,
            mercenary_mode=mercenary_mode,
            mercenary_selected_ids=mercenary_selected_ids,
            mercenary_valid_ids=mercenary_valid_ids,
            scroll_offset=self._scroll_offset,
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
        recompose_mode: bool = False,
        recompose_selected_ids: "set[str] | None" = None,
        mercenary_mode: bool = False,
        mercenary_selected_ids: "set[str] | None" = None,
        mercenary_valid_ids: "set[str] | None" = None,
        scroll_offset: int = 0,
    ) -> list[tuple[str, pygame.Rect]]:
        """
        Draw one hand row at vertical position ``y``.

        ``scroll_offset`` — number of cards scrolled off the left edge.

        Returns a list of (card_id, Rect) pairs for hit-testing of the
        currently VISIBLE cards only.
        """
        tile_rects: list[tuple[str, pygame.Rect]] = []
        x0 = self._x   # left edge of the strip (Stage 6: board may be offset)
        strip_w = self._width

        # Background strip
        if discard_mode:
            strip_bg = (45, 20, 20)
        elif recompose_mode:
            strip_bg = (15, 40, 40)
        elif mercenary_mode:
            strip_bg = (40, 30, 10)
        else:
            strip_bg = SIDEBAR_BG
        strip_rect = pygame.Rect(x0, y, strip_w, self.HEIGHT)
        pygame.draw.rect(self._surface, strip_bg, strip_rect)
        if discard_mode:
            border_color = _DISCARD_BORDER
        elif recompose_mode:
            border_color = _RECOMPOSE_SELECTED_BORDER
        elif mercenary_mode:
            border_color = _MERCENARY_CHOSEN_BORDER
        else:
            border_color = DIALOG_BORDER
        pygame.draw.line(self._surface, border_color, (x0, y), (x0 + strip_w, y),
                         2 if (discard_mode or recompose_mode or mercenary_mode) else 1)

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

        if not card_ids:
            empty = self._font.render("(empty)", True, HUD_LABEL)
            self._surface.blit(empty, (x0 + self.PADDING, card_top))
            return tile_rects

        # ── Scroll geometry ────────────────────────────────────────────────
        # Left/right chevron zones are only reserved when there are cards
        # that actually overflow in that direction.
        can_scroll_left  = scroll_offset > 0
        # Compute how many cards fit in the available width (after reserving
        # chevron space on whichever sides are needed).
        chevron_left_w  = self._CHEVRON_W if can_scroll_left else 0
        # We don't know yet if we need a right chevron, so do a quick probe.
        visible_area = strip_w - self.PADDING * 2 - chevron_left_w
        cards_that_fit = max(1, visible_area // (self.CARD_W + self.CARD_GAP))
        can_scroll_right = (scroll_offset + cards_that_fit) < len(card_ids)
        chevron_right_w = self._CHEVRON_W if can_scroll_right else 0
        # Recompute with both chevrons known.
        visible_area = strip_w - self.PADDING * 2 - chevron_left_w - chevron_right_w
        cards_that_fit = max(1, visible_area // (self.CARD_W + self.CARD_GAP))
        can_scroll_right = (scroll_offset + cards_that_fit) < len(card_ids)

        # Cards to render
        visible_ids = card_ids[scroll_offset: scroll_offset + cards_that_fit]

        # Clip rendering to the card area so nothing bleeds under chevrons.
        cards_area_rect = pygame.Rect(
            x0 + self.PADDING + chevron_left_w,
            y,
            strip_w - self.PADDING * 2 - chevron_left_w - chevron_right_w,
            self.HEIGHT,
        )
        prev_clip = self._surface.get_clip()
        self._surface.set_clip(cards_area_rect)

        x = x0 + self.PADDING + chevron_left_w

        for card_id in visible_ids:
            is_selected = (selected_card_id is not None and card_id == selected_card_id)
            is_playable = playable_card_ids is None or card_id in playable_card_ids
            is_recompose_chosen = recompose_mode and (
                recompose_selected_ids is not None and card_id in recompose_selected_ids
            )
            is_merc_chosen = mercenary_mode and (
                mercenary_selected_ids is not None and card_id in mercenary_selected_ids
            )
            is_merc_valid = (not mercenary_mode) or (
                mercenary_valid_ids is not None and card_id in mercenary_valid_ids
            )
            self._draw_card_tile(x, card_top, card_id,
                                 discard_mode=discard_mode,
                                 selected=is_selected,
                                 playable=is_playable,
                                 recompose_mode=recompose_mode,
                                 recompose_chosen=is_recompose_chosen,
                                 mercenary_mode=mercenary_mode,
                                 mercenary_chosen=is_merc_chosen,
                                 mercenary_valid=is_merc_valid)
            tile_rects.append((card_id, pygame.Rect(x, card_top, self.CARD_W, self.CARD_H)))
            x += self.CARD_W + self.CARD_GAP

        self._surface.set_clip(prev_clip)

        # ── Scroll chevrons ────────────────────────────────────────────────
        chevron_cy = y + self.HEIGHT // 2
        if can_scroll_left:
            self._draw_chevron(x0 + self.PADDING, chevron_cy, left=True)
        if can_scroll_right:
            self._draw_chevron(x0 + strip_w - self.PADDING - self._CHEVRON_W, chevron_cy, left=False)

        return tile_rects

    def _draw_chevron(self, x: int, cy: int, left: bool) -> None:
        """Draw a small ‹ or › arrow chevron for scroll indication."""
        w = self._CHEVRON_W
        h = 14
        color = (180, 180, 200)
        if left:
            pts = [(x + w - 3, cy - h // 2), (x + 3, cy), (x + w - 3, cy + h // 2)]
        else:
            pts = [(x + 3, cy - h // 2), (x + w - 3, cy), (x + 3, cy + h // 2)]
        pygame.draw.polygon(self._surface, color, pts)

    def _get_card_image(self, card: "AnyCard") -> "pygame.Surface | None":
        """Load and cache a card's artwork, or return None if unavailable."""
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

    def _draw_card_tile(
        self,
        x: int,
        y: int,
        card_id: str,
        discard_mode: bool = False,
        selected: bool = False,
        playable: bool = True,
        recompose_mode: bool = False,
        recompose_chosen: bool = False,
        mercenary_mode: bool = False,
        mercenary_chosen: bool = False,
        mercenary_valid: bool = True,
    ) -> None:
        """Render a single card thumbnail at (x, y).

        When the card has artwork available the tile shows the image scaled
        to fill the tile with the name in a small bar at the bottom.
        Otherwise falls back to the text-only badge + name layout.

        Stage 6: ``playable=False`` greys the tile out — there's no legal
        action for this card right now (no valid vessel/square/target).
        Stage 7: ``recompose_mode=True`` overrides the border colour —
        ``recompose_chosen=True`` → bright teal (card queued for return),
        ``recompose_chosen=False`` → muted teal (card not yet chosen).
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
        elif recompose_chosen:
            border = _RECOMPOSE_SELECTED_BORDER
            border_w = 3
        elif recompose_mode:
            border = _RECOMPOSE_IDLE_BORDER
            border_w = 1
        elif mercenary_chosen:
            border = _MERCENARY_CHOSEN_BORDER
            border_w = 3
        elif mercenary_mode and mercenary_valid:
            border = _MERCENARY_IDLE_BORDER
            border_w = 1
        elif mercenary_mode:
            border = _MERCENARY_INVALID_BORDER
            border_w = 1
        elif discard_mode:
            border = _DISCARD_BORDER
            border_w = 2
        else:
            border = badge_color
            border_w = 1

        img = self._get_card_image(card) if card is not None else None
        if img is not None:
            # Scale image to fill the tile (cover, centered) then clip to tile.
            iw, ih = img.get_size()
            scale = max(self.CARD_W / iw, self.CARD_H / ih)
            new_w = max(1, int(iw * scale))
            new_h = max(1, int(ih * scale))
            scaled = pygame.transform.smoothscale(img, (new_w, new_h))
            blit_x = x + (self.CARD_W - new_w) // 2
            blit_y = y + (self.CARD_H - new_h) // 2
            prev_clip = self._surface.get_clip()
            self._surface.set_clip(tile)
            self._surface.blit(scaled, (blit_x, blit_y))
            self._surface.set_clip(prev_clip)

            # Thin semi-transparent name bar at the bottom of the tile.
            bar_h = self._font.render(name, True, (0, 0, 0)).get_height() + 4
            bar_surf = pygame.Surface((self.CARD_W, bar_h), pygame.SRCALPHA)
            bar_surf.fill((0, 0, 0, 170))
            self._surface.blit(bar_surf, (x, y + self.CARD_H - bar_h))
            name_surf = self._font.render(
                _truncate(name, self.CARD_W - 6, self._font), True, HUD_TEXT
            )
            name_x = x + (self.CARD_W - name_surf.get_width()) // 2
            name_y = y + self.CARD_H - bar_h + (bar_h - name_surf.get_height()) // 2
            self._surface.blit(name_surf, (name_x, name_y))
        else:
            # Text-only fallback: badge + name.
            badge_surf = self._font.render(badge_label, True, badge_color)
            self._surface.blit(badge_surf, (x + 3, y + 2))
            name_surf = self._font.render(
                _truncate(name, self.CARD_W - 6, self._font), True, HUD_TEXT
            )
            name_y = y + 2 + badge_surf.get_height() + 1
            self._surface.blit(name_surf, (x + 3, name_y))

        pygame.draw.rect(self._surface, border, tile, border_w, border_radius=4)

        # Stage 6: grey overlay for unplayable cards, drawn last (on top).
        if not playable:
            dim = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            dim.fill((15, 15, 15, 165))
            self._surface.blit(dim, (x, y))

        # Stage 7: dim unchosen cards during recompose selection so chosen ones pop.
        if recompose_mode and not recompose_chosen:
            dim = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 100))
            self._surface.blit(dim, (x, y))

        # Stage 7: in mercenary mode, heavily dim invalid cards (not Monsters).
        if mercenary_mode and not mercenary_valid:
            dim = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 160))
            self._surface.blit(dim, (x, y))
        elif mercenary_mode and not mercenary_chosen:
            dim = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 80))
            self._surface.blit(dim, (x, y))


def _truncate(text: str, max_px: int, font: Any) -> str:
    """Truncate ``text`` so it fits within ``max_px`` pixels, appending '…'."""
    if font.render(text, True, (0, 0, 0)).get_width() <= max_px:
        return text
    while text and font.render(text + "…", True, (0, 0, 0)).get_width() > max_px:
        text = text[:-1]
    return text + "…"
