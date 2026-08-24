"""
event_log.py — Stage 6: human-readable event log panel

Renders every event emitted onto ``GameState.event_log`` (already populated
by ``RulesEngine.execute`` — see core/rules.py) as short, readable lines in
a fixed-height strip pinned to the bottom of the left sidebar, below the
CardViewer.

Two pieces:

    format_event(event, registry=None) -> str
        Turns one Event dataclass instance into a single display line.
        Falls back to a generic "field=value, ..." rendering for any Event
        subclass that doesn't have a dedicated formatter, so new event
        types never crash the UI — they just look a bit less polished
        until a formatter is added for them.

    EventLogPanel
        Pure-rendering widget: draws a header + a divider + as many of the
        most recent formatted lines as fit, newest at the bottom
        (console-style), auto-scrolling as more events accumulate. No
        interaction/hit-testing — this panel is read-only.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pygame

from game.chess.pieces import Position
from game.core import events as ev
from game.ui.colors import DIALOG_BORDER, HEADER_ACCENT, HUD_TEXT
from game.ui.overlays import TITLE_HEADER_PX, draw_panel_gradient, title_font

# Height in pixels reserved at the bottom of the left sidebar for the log.
# CardViewer.draw() is told to stop drawing its own content above this
# boundary (via its ``content_height`` param) so the two panels never
# overlap.
LOG_HEIGHT: int = 200


def _pos(p: "Position | None") -> str:
    if p is None:
        return "?"
    try:
        return p.to_algebraic()
    except Exception:
        return str(p)


def _card_name(registry: "object | None", card_id: "str | None") -> str:
    if card_id is None:
        return "?"
    if registry is not None:
        try:
            if card_id in registry:
                return registry.get(card_id).name
        except Exception:
            pass
    return card_id


def format_event(event: "ev.Event", registry: "object | None" = None) -> str:
    """Render one Event as a single human-readable line."""
    name = lambda cid: _card_name(registry, cid)  # noqa: E731

    if isinstance(event, ev.TurnStarted):
        return f"— Turn {event.turn_number}: {event.player_id} —"
    if isinstance(event, ev.TurnEnded):
        return f"{event.player_id} ended turn {event.turn_number}"
    if isinstance(event, ev.PhaseAdvanced):
        return f"{event.player_id}: {event.from_phase.value} → {event.to_phase.value}"
    if isinstance(event, ev.PrepActionUsed):
        return f"{event.player_id} used a preparation action ({event.action_type})"
    if isinstance(event, ev.ChessMoveUsed):
        return f"{event.player_id} used their chess move"

    if isinstance(event, ev.PieceMoved):
        return f"{event.owner}: {event.piece_id} {_pos(event.source)} → {_pos(event.target)}"
    if isinstance(event, ev.PieceCaptured):
        return f"{event.owner}'s {event.piece_id} captured at {_pos(event.captured_at)}"
    if isinstance(event, ev.PiecePromoted):
        return f"{event.owner}: {event.from_type} promoted to {event.to_type} at {_pos(event.position)}"
    if isinstance(event, ev.CastlingPerformed):
        return f"{event.player_id} castled {event.side} ({_pos(event.king_from)} → {_pos(event.king_to)})"
    if isinstance(event, ev.EnPassantCapture):
        return f"{event.player_id}: en passant {_pos(event.source)} → {_pos(event.target)}"
    if isinstance(event, ev.StalemateDetected):
        return f"Stalemate — {event.player_id} has no legal moves"
    if isinstance(event, ev.CaptureBlocked):
        return f"Capture blocked at {_pos(event.position)} ({event.shields_remaining} shield(s) left)"
    if isinstance(event, ev.PiecePushed):
        return f"{event.owner}'s piece pushed {_pos(event.source)} → {_pos(event.target)}"
    if isinstance(event, ev.Retaliated):
        return f"Retaliation at {_pos(event.position)} destroyed both pieces"
    if isinstance(event, ev.AttackIntercepted):
        return f"Assault on the King intercepted at {_pos(event.position)}"
    if isinstance(event, ev.PieceSpawned):
        return f"{event.player_id} spawned a {event.label} at {_pos(event.position)}"
    if isinstance(event, ev.PieceExpired):
        return f"{event.player_id}'s token faded at {_pos(event.position)}"
    if isinstance(event, ev.CardBanished):
        return f"A card was banished from {event.player_id}'s deck"

    if isinstance(event, ev.MonsterSummoned):
        return f"{event.player_id} summoned {name(event.card_id)} at {_pos(event.position)}"
    if isinstance(event, ev.MonsterDestroyed):
        return f"{event.player_id}'s {name(event.card_id)} was destroyed at {_pos(event.position)}"
    if isinstance(event, ev.MonsterDismissed):
        return f"{event.player_id} dismissed {name(event.card_id)} at {_pos(event.position)}"
    if isinstance(event, ev.MonsterAbilityActivated):
        return f"{event.player_id}: {name(event.card_id)} used {event.ability_id}"
    if isinstance(event, ev.SpellActivated):
        return f"{event.player_id} cast {name(event.card_id)}"
    if isinstance(event, ev.TrapPlaced):
        return f"{event.player_id} placed {name(event.card_id)} at {_pos(event.position)}"
    if isinstance(event, ev.TrapRadiusEntered):
        return f"A piece entered a trap's radius at {_pos(event.square)}"
    if isinstance(event, ev.TrapTriggered):
        return "A trap triggered!"
    if isinstance(event, ev.EnemyCardRevealed):
        return f"{event.player_id} learned the opponent has {name(event.revealed_card_id)}"
    if isinstance(event, ev.MoveCancelled):
        return f"{event.cancelled_player}'s move {_pos(event.source)} → {_pos(event.target)} was cancelled"

    if isinstance(event, ev.CardDrawn):
        return f"{event.player_id} drew {name(event.card_id)}"
    if isinstance(event, ev.CardDiscarded):
        return f"{event.player_id} discarded {name(event.card_id)}"
    if isinstance(event, ev.DeckRecycled):
        return f"{event.player_id}'s graveyard was recycled ({event.card_count} cards)"
    if isinstance(event, ev.CardsReturnedToDeck):
        return f"{event.player_id} returned {len(event.card_ids)} card(s) to the deck"

    if isinstance(event, ev.RecomposeInitiated):
        return f"{event.player_id} must Recompose — return {event.required_count} card(s)"
    if isinstance(event, ev.RecomposeResolved):
        return (
            f"{event.player_id} recomposed: returned {len(event.returned_card_ids)}, "
            f"drew {len(event.drawn_card_ids)}"
        )
    if isinstance(event, ev.MercenaryContractFired):
        return (
            f"{event.player_id} hired a Mercenary {event.piece_type} — "
            f"sacrificed {len(event.sacrificed_card_ids)} Monster(s)"
        )
    if isinstance(event, ev.MercenaryPiecePlaced):
        return (
            f"{event.player_id}'s Mercenary {event.piece_type} placed at {_pos(event.position)}"
        )

    if isinstance(event, ev.ConstructionStarted):
        return f"{event.player_id} started building {name(event.building_card_id)} at {_pos(event.position)}"
    if isinstance(event, ev.BuildingCompleted):
        return f"{event.player_id}'s {name(event.building_card_id)} finished construction"
    if isinstance(event, ev.BuildingDestroyed):
        return f"A building at {_pos(event.position)} was destroyed"
    if isinstance(event, ev.BuildingAttacked):
        return (
            f"Building at {_pos(event.position)} took {event.damage} damage "
            f"({event.integrity_remaining} integrity left)"
        )
    if isinstance(event, ev.BuildingAttackBlocked):
        reason = {
            "building_capture_protection": "a Keeper's ward",
            "temporary_building_protection": "emergency fortifications",
            "building_aura": "reinforced walls",
        }.get(event.reason, event.reason)
        return f"Attack on the building at {_pos(event.position)} was absorbed by {reason}"
    if isinstance(event, ev.BuildingRepaired):
        return (
            f"Building at {_pos(event.position)} repaired +{event.amount} "
            f"(now {event.integrity})"
        )
    if isinstance(event, ev.BuildingVulnerable):
        return (
            f"Building at {_pos(event.position)} marked for destruction "
            f"for {event.duration_turns} turn(s)"
        )
    if isinstance(event, ev.BuildingProtected):
        return (
            f"Building at {_pos(event.position)} fortified — absorbs "
            f"{event.uses} destroying blow(s) for {event.duration_turns} turn(s)"
        )

    if isinstance(event, ev.SquareFrozen):
        return f"Square {_pos(event.position)} frozen for {event.duration_turns} turn(s)"
    if isinstance(event, ev.SquareScorched):
        return f"Square {_pos(event.position)} scorched for {event.duration_turns} turn(s)"

    if isinstance(event, ev.KingCoronated):
        return f"{event.player_id} was crowned with {name(event.king_card_id)}"
    if isinstance(event, ev.KingSuccession):
        return f"{event.player_id}: {name(event.retired_king_card_id)} → {name(event.new_king_card_id)}"

    if isinstance(event, ev.RitualRevelationChanged):
        return f"{event.player_id}'s Ritual: {event.old_state.name} → {event.new_state.name}"
    if isinstance(event, ev.RitualActivated):
        return f"{event.player_id} activated a Ritual, summoning {name(event.summoned_monster_id)}"

    if isinstance(event, ev.CheckDetected):
        return f"{event.player_id} is in check!"
    if isinstance(event, ev.CheckResolved):
        return f"{event.player_id} is no longer in check"
    if isinstance(event, ev.FinalDuelTriggered):
        return f"Final Duel! {event.attacker} vs {event.defender} ({event.duel_type.value})"
    if isinstance(event, ev.GameOver):
        # No winner means a README §53 termination limit called the match
        # (repetition / no progress / turn ceiling) — nobody won it.
        if event.winner is None:
            return f"Game over — draw ({event.reason})"
        return f"Game over — {event.winner} wins ({event.reason})"

    # Stage 12: Final Duel round-by-round actions (see ui/pygame_app.py's
    # dedicated on-screen Duel Log for the primary, more prominent feed —
    # these formatters just keep the sidebar Event Log readable too).
    if isinstance(event, ev.DuelSupportSpent):
        return f"{event.player_id} (Duel): {event.label or event.ability}"
    if isinstance(event, ev.DuelStrikeResolved):
        outcome = f"HIT ({event.strikes_landed}/{event.strikes_needed})" if not event.blocked else "blocked"
        return f"{event.player_id} (Duel): Strike → {outcome}"
    if isinstance(event, ev.DuelRoundAdvanced):
        return f"Duel round {event.round_number}/{event.max_rounds}"
    if isinstance(event, ev.RoyalEscapeTriggered):
        pos = _pos(event.new_position)
        return f"{event.defender}'s King escapes to {pos}! (Royal Escape #{event.duels_survived})"

    # Generic fallback: class name + field=value pairs.
    try:
        parts = ", ".join(f"{f.name}={getattr(event, f.name)!r}" for f in fields(event))
        return f"{type(event).__name__}({parts})"
    except Exception:
        return type(event).__name__


class EventLogPanel:
    """
    Read-only bottom strip of the left sidebar. Shows the tail of
    ``GameState.event_log`` as formatted lines, newest at the bottom.
    """

    PADDING: int = 10

    def __init__(
        self,
        surface: "pygame.Surface",
        font_small: "Any",
        width: int,
        height: int = LOG_HEIGHT,
    ) -> None:
        self._surface = surface
        self._font_small = font_small
        self._width = width
        self._height = height

    def draw(self, events: "list[Any]", registry: "object | None" = None) -> None:
        surf_h = self._surface.get_height()
        y0 = surf_h - self._height
        rect = pygame.Rect(0, y0, self._width, self._height)
        draw_panel_gradient(self._surface, rect)
        pygame.draw.line(self._surface, DIALOG_BORDER, (0, y0), (self._width, y0), 1)
        pygame.draw.line(self._surface, DIALOG_BORDER, (self._width, y0), (self._width, surf_h), 1)

        x = self.PADDING
        y = y0 + self.PADDING
        header = title_font(TITLE_HEADER_PX).render("Event Log", True, HUD_TEXT)
        self._surface.blit(header, (x, y))
        y += header.get_height() + 3
        pygame.draw.line(self._surface, HEADER_ACCENT, (x, y), (x + min(40, header.get_width()), y), 2)
        y += 9

        # FTFont has no get_height(); measure it off a rendered sample
        # (see ui/font.py's FTFont docstring).
        line_h = self._font_small.render("Ag", True, HUD_TEXT).get_height() + 2
        available_h = surf_h - self.PADDING - y
        max_lines = max(0, available_h // line_h)
        if max_lines == 0 or not events:
            return

        prev_clip = self._surface.get_clip()
        self._surface.set_clip(pygame.Rect(0, y, self._width, surf_h - y - self.PADDING))
        try:
            tail = events[-max_lines:]
            inner_w = self._width - self.PADDING * 2
            for event in tail:
                try:
                    line = format_event(event, registry)
                except Exception:
                    line = type(event).__name__
                surf = self._font_small.render(line, True, HUD_TEXT)
                if surf.get_width() > inner_w:
                    # Trim to fit rather than letting it bleed past the border.
                    while line and surf.get_width() > inner_w:
                        line = line[:-1]
                        surf = self._font_small.render(line + "…", True, HUD_TEXT)
                self._surface.blit(surf, (x, y))
                y += line_h
        finally:
            self._surface.set_clip(prev_clip)
