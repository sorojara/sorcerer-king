"""
mechanics/monsters.py — Stage 5: Monster Effect Resolver (refactored)
=======================================================================

This module is the primary call-site used by RulesEngine for all monster
effects.  It delegates implementation to the per-category modules under
``mechanics/effects/``.

Handles all monster-related mechanics:

    1. Movement modifiers  — applied on top of vessel base movement.
    2. On-summon effects   — triggered immediately when SummonMonster executes.
    3. Capture protection  — absorbs N capture attempts before destruction.
    4. Post-capture effects — e.g. reposition_unit after capturing.
    5. Damage aura         — destroys pieces entering the monster's radius.
    6. Freeze square       — locks the landing square after a capture.
    7. Ritual progress     — advances a foretold ritual counter.

Design principle (README §3.3):
    "Monsters generally inherit movement from their Vessel unless the
    Monster specifically overrides or modifies it."

The resolver is stateless — it reads card effects and mutates
GameState directly.  It is called by the RulesEngine at the
appropriate timing hooks.

Public API (unchanged for backwards compatibility)
--------------------------------------------------
    apply_on_summon_effects(state, unit, card, events, rng) → None
    get_movement_additions(unit, card) → list[tuple[int,int]]
    apply_capture_protection(unit) → bool  (True = capture absorbed)
    apply_after_capture_effects(state, attacker_unit, attacker_pos, card, events)
    check_damage_aura(state, moving_unit, target_pos, events, registry) → bool

New helpers (delegated from registry)
--------------------------------------
    get_activatable_effects(unit, registry) → list[str]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.chess.pieces import Position, UnitInstance
from game.core.phases import Phase
from game.logger import get_logger

_log = get_logger(__name__)

if TYPE_CHECKING:
    from game.cards.card import MonsterCard
    from game.core.events import Event
    from game.core.rng import DeterministicRNG
    from game.core.state import GameState


# Re-export the new helpers so callers can import from here
from game.mechanics.effects import (   # noqa: F401
    EffectCategory,
    EffectContext,
    EffectTrigger,
    get_activatable_effects,
    resolve_effect,
    EFFECT_REGISTRY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Internal: build an EffectContext for a given call site
# ─────────────────────────────────────────────────────────────────────────────

def _make_ctx(
    state: "GameState",
    unit: UnitInstance,
    position: Position | None,
    card: "MonsterCard",
    effect,        # EffectEntry
    events: "list[Event]",
    rng: "DeterministicRNG | None" = None,
    registry=None,
    trigger: str = "",
    extra: dict | None = None,
) -> EffectContext:
    return EffectContext(
        state=state,
        unit=unit,
        position=position,
        card=card,
        effect=effect,
        events=events,
        rng=rng,
        registry=registry,
        trigger=trigger,
        extra=extra or {},
    )


# ─────────────────────────────────────────────────────────────────────────────
# On-summon effects
# ─────────────────────────────────────────────────────────────────────────────

def apply_on_summon_effects(
    state: "GameState",
    unit: UnitInstance,
    card: "MonsterCard",
    events: "list[Event]",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Apply all effects from ``card`` that trigger on_summon.

    Delegates each effect to the appropriate handler in the effects registry.
    Stub effects (not yet implemented) are silently skipped unless the effect
    explicitly raises (which only happens in future stages).
    """
    _log.debug("SUMMON   %s  unit=%s  effects=%s",
               card.id, unit.piece.id,
               [e.type for e in card.effects])
    for effect in card.effects:
        trigger = effect.params.get("trigger", "on_summon")
        # Only fire effects whose trigger is summon-compatible
        if trigger not in ("on_summon", "passive", ""):
            _log.debug("SUMMON   skip effect %r (trigger=%r)", effect.type, trigger)
            continue

        ctx = _make_ctx(state, unit, None, card, effect, events, rng,
                        trigger=trigger)
        try:
            resolve_effect(ctx)
        except NotImplementedError:
            # Stub — silently skip during gameplay; logged via UNRESOLVED_EFFECTS
            _log.debug("SUMMON   NotImplemented for %r — skipped", effect.type)


# ─────────────────────────────────────────────────────────────────────────────
# Movement modifiers
# ─────────────────────────────────────────────────────────────────────────────

def get_movement_additions(
    unit: UnitInstance,
    card: "MonsterCard",
) -> list[tuple[int, int]]:
    """
    Return extra movement direction vectors added to the vessel's base moves
    by this monster's ``alter_movement`` effect.

    Currently supported:
        add_leap: true  — adds 4 cardinal leap squares at ``leap_distance``.
    """
    extras: list[tuple[int, int]] = []
    for effect in card.effects:
        if effect.type != "alter_movement":
            continue
        if effect.params.get("add_leap"):
            d = effect.params.get("leap_distance", 2)
            extras.extend([(d, 0), (-d, 0), (0, d), (0, -d)])
    return extras


# ─────────────────────────────────────────────────────────────────────────────
# Capture protection
# ─────────────────────────────────────────────────────────────────────────────

def apply_capture_protection(unit: UnitInstance) -> bool:
    """
    Check whether the unit has a shield and consume one charge.

    Returns True if the capture was absorbed (unit survives).
    Returns False if the unit has no shield — normal capture proceeds.
    """
    for idx, status in enumerate(unit.statuses):
        if status.startswith("shield:"):
            charges = int(status.split(":")[1])
            if charges > 0:
                unit.statuses[idx] = f"shield:{charges - 1}"
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Post-capture effects (fired when THIS unit makes a capture)
# ─────────────────────────────────────────────────────────────────────────────

def apply_after_capture_effects(
    state: "GameState",
    attacker_unit: UnitInstance,
    attacker_pos: Position,
    card: "MonsterCard",
    events: "list[Event]",
) -> None:
    """
    Apply effects that trigger when this monster captures an enemy piece.

    Delegates to the effects registry.  Currently handled:
        reposition_unit (trigger=after_capture) — blade_dancer post-capture teleport
        freeze_square   (trigger=on_capture)    — iron_vanguard landing-square freeze
    """
    _log.debug("CAPTURE  attacker=%s  monster=%s  pos=%s",
               attacker_unit.piece.id, card.id, attacker_pos)
    for effect in card.effects:
        trigger = effect.params.get("trigger", "")
        if trigger not in ("after_capture", "on_capture"):
            continue

        ctx = _make_ctx(state, attacker_unit, attacker_pos, card, effect, events,
                        trigger=trigger)
        try:
            resolve_effect(ctx)
        except NotImplementedError:
            _log.debug("CAPTURE  NotImplemented for %r — skipped", effect.type)


# ─────────────────────────────────────────────────────────────────────────────
# Damage aura (fires when an enemy piece enters the monster's radius)
# ─────────────────────────────────────────────────────────────────────────────

def check_damage_aura(
    state: "GameState",
    moving_unit: UnitInstance,
    target_pos: Position,
    events: "list[Event]",
    registry: "object | None",
) -> bool:
    """
    Check all enemy monsters with a ``damage_aura`` effect.
    If ``moving_unit`` moves to ``target_pos`` and enters one of their auras,
    the moving unit is destroyed and a MonsterDestroyed / PieceCaptured event
    is emitted.

    Returns True if the moving unit was destroyed by an aura.
    """
    from game.cards.card import MonsterCard
    from game.core.events import MonsterDestroyed, PieceCaptured

    if registry is None:
        return False

    destroyer_owner = moving_unit.owner
    opponent = "black" if destroyer_owner == "white" else "white"

    destroyed = False
    for pos, unit in state.board.all_units_for(opponent):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue

        for effect in card.effects:
            if effect.type != "damage_aura":
                continue
            owner_immune = effect.params.get("owner_immune", True)
            if owner_immune and moving_unit.owner == unit.owner:
                continue
            radius = effect.params.get("radius", 1)
            if (
                abs(target_pos.file - pos.file) <= radius
                and abs(target_pos.rank - pos.rank) <= radius
            ):
                removed = state.board.remove_unit(target_pos)
                if removed is not None:
                    opp_ps = state.get_player(destroyer_owner)
                    opp_ps.captured_pieces.append(removed.piece.id)
                    if removed.monster_id:
                        events.append(MonsterDestroyed(
                            player_id=removed.owner,
                            card_id=removed.monster_id,
                            vessel_piece_id=removed.piece.id,
                            position=target_pos,
                            destroyed_by_piece_id=unit.piece.id,
                        ))
                    else:
                        events.append(PieceCaptured(
                            piece_id=removed.piece.id,
                            owner=removed.owner,
                            captured_at=target_pos,
                            captured_by_piece_id=unit.piece.id,
                        ))
                    destroyed = True
                break
    return destroyed
