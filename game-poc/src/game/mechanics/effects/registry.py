"""
mechanics/effects/registry.py — Effect dispatcher
==================================================

Owns the mapping from effect-type strings → (EffectCategory, handler) and
provides the public ``resolve_effect`` entry point.

``EffectContext`` is the single argument type passed to every handler:

    state       — mutable GameState
    unit        — the UnitInstance that owns the card (may be None for
                  non-board effects e.g. cards in hand)
    position    — current board position of the unit (may be None)
    card        — the MonsterCard (or any card with effects)
    effect      — the specific EffectEntry being resolved
    events      — list to append Event objects to
    rng         — DeterministicRNG (may be None in tests)
    registry    — CardRegistry (may be None)
    trigger     — the timing context that caused resolution (caller hint)
    extra       — optional dict for additional context
                  (e.g. {"target_pos": Position, "captured_unit": UnitInstance})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from game.logger import get_logger

_log = get_logger(__name__)

if TYPE_CHECKING:
    from game.cards.card import AnyCard, EffectEntry
    from game.chess.pieces import Position, UnitInstance
    from game.core.events import Event
    from game.core.rng import DeterministicRNG
    from game.core.state import GameState


@dataclass
class EffectContext:
    """
    Bundle of runtime context passed to every effect handler.

    Handlers read from this object and mutate ``state`` / append to
    ``events``.  They never return a value.
    """

    state:    "GameState"
    effect:   "EffectEntry"
    events:   "list[Event]"
    unit:     "UnitInstance | None"     = None
    position: "Position | None"         = None
    card:     "AnyCard | None"          = None
    rng:      "DeterministicRNG | None" = None
    registry: "Any | None"             = None
    trigger:  str                       = ""
    extra:    dict[str, Any]            = field(default_factory=dict)


# Type alias for handler callables
_Handler = Callable[[EffectContext], None]


# ---------------------------------------------------------------------------
# Handler imports — loaded here to keep the registry central
# ---------------------------------------------------------------------------

from game.mechanics.effects.movement      import MOVEMENT_HANDLERS       # noqa: E402
from game.mechanics.effects.defense       import DEFENSE_HANDLERS        # noqa: E402
from game.mechanics.effects.board_control import BOARD_CONTROL_HANDLERS  # noqa: E402
from game.mechanics.effects.cards         import CARDS_HANDLERS          # noqa: E402
from game.mechanics.effects.buildings     import BUILDINGS_HANDLERS      # noqa: E402
from game.mechanics.effects.ritual        import RITUAL_HANDLERS         # noqa: E402
from game.mechanics.effects.king_duel     import KING_DUEL_HANDLERS      # noqa: E402
from game.mechanics.effects.information   import INFORMATION_HANDLERS    # noqa: E402
from game.mechanics.effects.spells        import SPELLS_HANDLERS         # noqa: E402
from game.mechanics.effects._meta        import META_HANDLERS            # noqa: E402


# ---------------------------------------------------------------------------
# Master registry: effect-type → handler
#
# This ONE registry serves Monsters, Traps, and Spells alike — the
# canonical effect contract (Stage 6):
#
#     TRIGGER → CONDITION → TARGET SELECTION → EFFECT → DURATION → EVENTS
#
# "TARGET SELECTION" happens before resolve_effect() is ever called (trap
# trigger detection in mechanics.monsters, or area expansion in
# _execute_activate_spell via mechanics.area.expand_area) — by the time a
# handler in this registry runs, ctx.unit/ctx.position/ctx.extra already
# name the concrete target(s). Handlers only implement EFFECT + DURATION
# (arming a status/temp-effect) and append to ctx.events.
# ---------------------------------------------------------------------------

EFFECT_REGISTRY: dict[str, _Handler] = {
    **MOVEMENT_HANDLERS,
    **DEFENSE_HANDLERS,
    **BOARD_CONTROL_HANDLERS,
    **CARDS_HANDLERS,
    **BUILDINGS_HANDLERS,
    **RITUAL_HANDLERS,
    **KING_DUEL_HANDLERS,
    **INFORMATION_HANDLERS,
    **SPELLS_HANDLERS,
    **META_HANDLERS,
}


def resolve_effect(ctx: EffectContext) -> None:
    """
    Dispatch ``ctx.effect`` to the appropriate handler.

    Unknown effect types are logged (not raised) so that new YAML entries
    don't crash the engine before their handler is implemented.
    """
    handler = EFFECT_REGISTRY.get(ctx.effect.type)
    if handler is None:
        UNRESOLVED_EFFECTS.add(ctx.effect.type)
        _log.warning(
            "EFFECT   UNRESOLVED type=%r  unit=%s  trigger=%r",
            ctx.effect.type,
            ctx.unit.piece.id if ctx.unit else "None",
            ctx.trigger,
        )
        return

    unit_id = ctx.unit.piece.id if ctx.unit else "None"
    pos_str = str(ctx.position) if ctx.position else "None"
    _log.debug(
        "EFFECT   → %s  unit=%s  pos=%s  trigger=%r  params=%s",
        ctx.effect.type, unit_id, pos_str, ctx.trigger, ctx.effect.params,
    )
    try:
        handler(ctx)
    except Exception as exc:
        _log.warning(
            "EFFECT   FAILED  type=%r  unit=%s  error=%s",
            ctx.effect.type, unit_id, exc,
        )
        raise
    _log.debug("EFFECT   OK      %s  unit=%s", ctx.effect.type, unit_id)


#: Set of effect types that were dispatched but had no registered handler.
#: Useful for test assertions and debugging.
UNRESOLVED_EFFECTS: set[str] = set()


# ---------------------------------------------------------------------------
# Activatable-effects query (used by the UI for ability badges)
# ---------------------------------------------------------------------------

#: Effect types that require an explicit player action (not passive/automatic).
ACTIVATED_EFFECT_TYPES: frozenset[str] = frozenset({
    # MOVEMENT
    "burrow",
    "reposition_unit",
    "push_unit",
    # DEFENSE
    # (all handled passively / on-trigger)
    # BOARD_CONTROL
    # (passive auras — no activation needed)
    # CARDS
    # (all trigger-based)
    # BUILDINGS
    "repair_building",
    "disable_building",
    # RITUAL
    "ritual_reveal_tradeoff",
    "ritual_requirement_reduction",
    # KING_DUEL
    # (passive)
    # INFORMATION
    # (passive)
    # META
    "dismiss_monster",
    "copy_effect",
    "challenge_unit",
    "capture_then_retreat",
})


def get_activatable_effects(unit: "UnitInstance", registry: "Any | None") -> list[str]:
    """
    Return a list of effect types on ``unit`` that require explicit activation.

    Used by the UI to show an "ability available" badge on monster pieces.
    Returns an empty list if the unit has no monster or no activatable effects.
    """
    if unit.monster_id is None or registry is None:
        return []

    from game.cards.card import MonsterCard
    try:
        card = registry.get(unit.monster_id)
    except KeyError:
        return []

    if not isinstance(card, MonsterCard):
        return []

    # monster_seal / nullification_glyph: a suppressed Monster's activated
    # abilities are unavailable too. Deferred import — mechanics.monsters
    # itself imports from this package at module level.
    from game.mechanics.monsters import is_effects_suppressed
    if is_effects_suppressed(unit):
        return []

    return [
        eff.type
        for eff in card.effects
        if eff.type in ACTIVATED_EFFECT_TYPES
    ]
