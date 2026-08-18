"""
mechanics/effects/ — Effect Category Architecture
===================================================

This package organises all monster (and eventually spell/trap) effects into
eight named categories.  Each category owns a resolver module that implements
the real effects and stubs out the ones not yet ready for the current stage.

Categories
----------
MOVEMENT       alter_movement, burrow, pass_through_units, reposition_unit,
               push_unit
DEFENSE        capture_protection, trap_immunity, intercept, retaliate
BOARD_CONTROL  freeze_square, scorch_square, movement_restriction,
               suppress_spell_zone
CARDS          draw_card, inspect_top_deck, reorder_top_deck,
               graveyard_to_deck
BUILDINGS      construction_speed_bonus, repair_building, building_aura,
               disable_building, territory_expansion
RITUAL         ritual_progress_boost, ritual_pattern_substitute,
               ritual_requirement_reduction, ritual_reveal_tradeoff
KING_DUEL      king_support_bonus, royal_support_suppression, duel_debuff
INFORMATION    reveal_hidden_info, obscure_influence

Public API
----------
    EffectCategory          — enum of the eight categories
    EffectTrigger           — enum of all trigger timing strings
    EFFECT_REGISTRY         — dict mapping effect-type → (category, handler)
    resolve_effect(ctx)     — unified single-entry-point dispatcher
    get_activatable_effects — returns list of on-demand effect types for
                              a given unit (used by the UI to show badges)

The resolver modules follow a strict contract:
    handler(ctx: EffectContext) -> None

    EffectContext bundles state, unit, position, card, events, rng, and
    the raw EffectEntry being resolved.  Handlers mutate ``ctx.state`` and
    append to ``ctx.events``.  They NEVER return a value.

Stubs are implemented as functions that raise ``NotImplementedError`` with a
descriptive message so that accidental trigger of an unimplemented path is
immediately obvious rather than silently skipped.
"""

from __future__ import annotations

from enum import Enum


class EffectCategory(Enum):
    """The eight logical effect categories."""

    MOVEMENT      = "movement"
    DEFENSE       = "defense"
    BOARD_CONTROL = "board_control"
    CARDS         = "cards"
    BUILDINGS     = "buildings"
    RITUAL        = "ritual"
    KING_DUEL     = "king_duel"
    INFORMATION   = "information"


class EffectTrigger(Enum):
    """
    All supported effect-trigger timing strings.

    Triggers are matched against ``EffectEntry.params["trigger"]`` at runtime.
    Multiple triggers may share the same resolver; the effect handler is
    responsible for any trigger-specific branching.
    """

    # Lifecycle
    ON_SUMMON            = "on_summon"
    ON_DISMISS           = "on_dismiss"
    START_OF_TURN        = "start_of_turn"
    END_OF_TURN          = "end_of_turn"

    # Chess events
    ON_CAPTURE           = "on_capture"
    AFTER_CAPTURE        = "after_capture"
    ON_CAPTURE_ATTEMPT   = "on_capture_attempt"
    ON_MOVE              = "on_move"

    # Card / deck events
    AFTER_INSPECT        = "after_inspect"
    WHEN_SACRIFICED      = "when_sacrificed"
    ALLIED_MONSTER_DEST  = "allied_monster_destroyed"

    # Building events
    ADJACENT_END_TURN    = "adjacent_end_turn"
    ADJACENT_KING_TARGETED = "adjacent_king_targeted"

    # Duel events
    FINAL_DUEL_START     = "final_duel_start"

    # Always-on
    PASSIVE              = "passive"

    # Player-activated (requires explicit action)
    ACTIVATED            = "activated"

    # Unknown / future
    UNKNOWN              = "_unknown_"


# Re-export the registry so callers can do:
#   from game.mechanics.effects import EFFECT_REGISTRY, resolve_effect
from game.mechanics.effects.registry import (  # noqa: E402
    EFFECT_REGISTRY,
    EffectContext,
    resolve_effect,
    get_activatable_effects,
    UNRESOLVED_EFFECTS,
)

__all__ = [
    "EffectCategory",
    "EffectTrigger",
    "EffectContext",
    "EFFECT_REGISTRY",
    "UNRESOLVED_EFFECTS",
    "resolve_effect",
    "get_activatable_effects",
]
