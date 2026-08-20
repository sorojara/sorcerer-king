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

Extended helpers (this pass — see monsters.yaml Stage-5 effect implementation)
-------------------------------------------------------------------------------
    check_scorch_square(state, moving_unit, target_pos, events) → bool
    get_movement_cap(board, pos, owner, registry) → int | None
    get_extra_vessel_types(state, position, owner, archetype, registry) → set[str]
    try_push_unit(state, attacker_unit, attacker_pos, target_pos, card, events) → bool
    apply_retaliate(state, captured_unit, captured_pos, attacker_unit, events) → bool
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
        # Only fire effects whose trigger is summon-compatible.
        # "capture_attempt" is included here because retaliate (thorn_boar)
        # is a passive defensive flag — it must be armed at summon time even
        # though it only *resolves* later, when a capture is attempted
        # against this unit.
        if trigger not in ("on_summon", "passive", "", "capture_attempt"):
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

    Stage 6: a unit with "exposed:N" (capture_vulnerability — pit_trap,
    counter_strike) never has its shield checked at all — it is captured
    normally even if it holds shield charges.
    """
    if any(s.startswith("exposed:") for s in unit.statuses):
        return False
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
                        # Stage 10: grave_crowned_king's graveyard_recycle policy.
                        from game.mechanics.kings import maybe_recycle_destroyed_monster
                        maybe_recycle_destroyed_monster(
                            state, removed.owner, removed.monster_id, events, registry,
                        )
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


# ─────────────────────────────────────────────────────────────────────────────
# Scorch square (fires when any piece enters a scorched square) — ember_drake
# ─────────────────────────────────────────────────────────────────────────────

def check_scorch_square(
    state: "GameState",
    moving_unit: UnitInstance,
    target_pos: Position,
    events: "list[Event]",
) -> bool:
    """
    Read side of ``scorch_square`` (write side: board_control._scorch_square).

    If ``target_pos`` carries a ``scorched:<N>:<owner>`` square effect and
    ``moving_unit`` does not belong to ``owner``, the moving unit is
    destroyed.  Called from ``_execute_move_piece`` right after
    ``check_damage_aura`` — i.e. after the piece has already landed.

    Returns True if the moving unit was destroyed.
    """
    from game.core.events import MonsterDestroyed, PieceCaptured

    sq = state.board.get_square(target_pos)
    for eff in sq.temporary_effects:
        if not eff.startswith("scorched:"):
            continue
        parts = eff.split(":")
        owner = parts[2] if len(parts) > 2 else None
        if owner is not None and moving_unit.owner == owner:
            continue  # owner is immune to their own scorch

        removed = state.board.remove_unit(target_pos)
        if removed is None:
            return False
        opp_ps = state.get_player(removed.owner)
        # Not "captured_pieces" of an opponent — the square itself did this.
        # Still record it so hand/army bookkeeping stays consistent.
        state.get_player(state.opponent_of(removed.owner)).captured_pieces.append(
            removed.piece.id
        )
        if removed.monster_id:
            events.append(MonsterDestroyed(
                player_id=removed.owner,
                card_id=removed.monster_id,
                vessel_piece_id=removed.piece.id,
                position=target_pos,
                destroyed_by_piece_id=None,
            ))
        else:
            events.append(PieceCaptured(
                piece_id=removed.piece.id,
                owner=removed.owner,
                captured_at=target_pos,
                captured_by_piece_id=f"scorch:{target_pos}",
            ))
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Immobilize zone (cursed_ground) — fires for ANY piece landing there
# ─────────────────────────────────────────────────────────────────────────────

def check_immobilize_zone(
    state: "GameState",
    moving_unit: UnitInstance,
    target_pos: Position,
) -> bool:
    """
    Read side of ``immobilize_zone`` (write side:
    board_control._immobilize_zone — cursed_ground).

    If ``target_pos`` carries a ``cursed:<N>:<owner>`` tag, immobilize
    ``moving_unit`` — no owner check, unlike Traps: cursed_ground affects
    "any piece", including the caster's own.  Called from
    _execute_move_piece for every completed move (capture or not).

    Returns True if the moving unit was immobilized.
    """
    sq = state.board.get_square(target_pos)
    for eff in sq.temporary_effects:
        if eff.startswith("cursed:"):
            duration = int(eff.split(":")[1])
            moving_unit.statuses = [
                s for s in moving_unit.statuses if not s.startswith("immobilized:")
            ]
            moving_unit.add_status(f"immobilized:{duration}")
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Movement restriction (passive aura) — astral_binder
# ─────────────────────────────────────────────────────────────────────────────

def get_movement_cap(
    board,
    pos: Position,
    owner: str,
    registry: "object | None",
) -> "int | None":
    """
    Return the strictest ``max_distance`` cap imposed on ``owner``'s unit at
    ``pos`` by any enemy ``movement_restriction`` aura currently in range, or
    None if unrestricted.

    Called from ``chess.movement.get_pseudo_legal_moves()`` to filter out
    candidate destinations farther (Chebyshev) than the cap.
    """
    if registry is None:
        return None

    opponent = "black" if owner == "white" else "white"
    cap: "int | None" = None
    for enemy_pos, enemy_unit in board.all_units_for(opponent):
        for status in enemy_unit.statuses:
            if not status.startswith("movement_restriction:"):
                continue
            _, radius_s, max_dist_s = status.split(":")
            radius = int(radius_s)
            max_dist = int(max_dist_s)
            if (
                abs(pos.file - enemy_pos.file) <= radius
                and abs(pos.rank - enemy_pos.rank) <= radius
            ):
                cap = max_dist if cap is None else min(cap, max_dist)
    return cap


# ─────────────────────────────────────────────────────────────────────────────
# Vessel support (extends Vessel compatibility near allied auras) — broodmother
# ─────────────────────────────────────────────────────────────────────────────

def get_extra_vessel_types(
    state: "GameState",
    position: Position,
    owner: str,
    archetype: str,
    registry: "object | None",
) -> set[str]:
    """
    Return additional vessel piece-type strings allowed for a monster of
    ``archetype`` being summoned at ``position``, granted by any allied unit
    within range that carries a matching ``vessel_support`` aura (broodmother).

    Used both by legal-action generation (SummonMonster enumeration) and by
    ``_execute_summon_monster``'s vessel-compatibility check, so a card whose
    own ``supported_vessels`` doesn't include the piece type can still be
    summoned there if an allied vessel_support aura covers it.
    """
    from game.cards.card import MonsterCard

    if registry is None:
        return set()

    extra: set[str] = set()
    for pos, unit in state.board.all_units_for(owner):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.type != "vessel_support":
                continue
            params = effect.params
            if params.get("archetype") != archetype:
                continue
            radius = params.get("radius", 1)
            if (
                abs(position.file - pos.file) <= radius
                and abs(position.rank - pos.rank) <= radius
            ):
                extra.update(params.get("allow_extra_vessel", []))
    return extra


# ─────────────────────────────────────────────────────────────────────────────
# Push unit (replaces a capture attempt) — storm_dragon
# ─────────────────────────────────────────────────────────────────────────────

def try_push_unit(
    state: "GameState",
    attacker_unit: UnitInstance,
    attacker_pos: Position,
    target_pos: Position,
    card: "MonsterCard",
    events: "list[Event]",
) -> bool:
    """
    If ``card`` carries a ``push_unit`` effect (trigger=on_capture_attempt),
    push the defender at ``target_pos`` one square directly away from
    ``attacker_pos`` instead of letting the normal capture happen, then move
    the attacker onto the vacated square.

    Must be called BEFORE any board mutation for the move, from
    ``_execute_move_piece``.  Returns True if the push occurred (caller
    should treat the move as fully resolved and skip normal capture logic).
    """
    from game.core.events import PieceMoved, PiecePushed

    for effect in card.effects:
        if effect.type != "push_unit":
            continue
        if effect.params.get("trigger", "on_capture_attempt") != "on_capture_attempt":
            continue

        defender = state.board.get_unit(target_pos)
        if defender is None:
            continue

        distance = effect.params.get("distance", 1)
        df = target_pos.file - attacker_pos.file
        dr = target_pos.rank - attacker_pos.rank
        # Normalise to a unit step (attacker/target are adjacent for all
        # current vessel movement patterns relevant to push_unit).
        step_f = (df > 0) - (df < 0)
        step_r = (dr > 0) - (dr < 0)
        push_f = target_pos.file + step_f * distance
        push_r = target_pos.rank + step_r * distance

        if not (0 <= push_f <= 7 and 0 <= push_r <= 7):
            continue  # off-board — fall back to a normal capture
        push_dest = Position(push_f, push_r)
        if state.board.get_unit(push_dest) is not None:
            continue  # blocked — fall back to a normal capture

        state.board.move_unit(target_pos, push_dest)
        events.append(PiecePushed(
            piece_id=defender.piece.id,
            owner=defender.owner,
            source=target_pos,
            target=push_dest,
            pushed_by_piece_id=attacker_unit.piece.id,
        ))
        # Stage 8: forcibly displacing a committed Builder Pawn off its
        # construction square is disruption too — the Building is lost.
        from game.mechanics.buildings import find_committed_building, cancel_construction
        disrupted = find_committed_building(state, defender.piece.id)
        if disrupted is not None:
            cancel_construction(state, disrupted, events, destroyed_by=attacker_unit.piece.id)

        state.board.move_unit(attacker_pos, target_pos)
        events.append(PieceMoved(
            piece_id=attacker_unit.piece.id,
            owner=attacker_unit.owner,
            source=attacker_pos,
            target=target_pos,
        ))
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Retaliate (mutual destruction on capture) — thorn_boar
# ─────────────────────────────────────────────────────────────────────────────

def apply_retaliate(
    state: "GameState",
    captured_unit: UnitInstance,
    captured_pos: Position,
    attacker_unit: UnitInstance,
    events: "list[Event]",
) -> bool:
    """
    If ``captured_unit`` (already removed from the board by the caller) had
    the ``retaliate`` status, destroy ``attacker_unit`` too — it currently
    occupies ``captured_pos`` after completing its capture.

    A card with ``weakened_target_bonus`` (executioner: "guaranteed_capture")
    lets the attacker bypass retaliation — its finishing blow is clean.

    Returns True if the attacker was destroyed.
    """
    from game.core.events import MonsterDestroyed, PieceCaptured, Retaliated

    if "retaliate" not in captured_unit.statuses:
        return False
    if "guaranteed_capture_vs_no_shield" in attacker_unit.statuses:
        return False

    removed = state.board.remove_unit(captured_pos)
    if removed is None:
        return False

    state.get_player(captured_unit.owner).captured_pieces.append(removed.piece.id)
    if removed.monster_id:
        events.append(MonsterDestroyed(
            player_id=removed.owner,
            card_id=removed.monster_id,
            vessel_piece_id=removed.piece.id,
            position=captured_pos,
            destroyed_by_piece_id=captured_unit.piece.id,
        ))
    else:
        events.append(PieceCaptured(
            piece_id=removed.piece.id,
            owner=removed.owner,
            captured_at=captured_pos,
            captured_by_piece_id=captured_unit.piece.id,
        ))
    events.append(Retaliated(
        defender_piece_id=captured_unit.piece.id,
        attacker_piece_id=removed.piece.id,
        position=captured_pos,
    ))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Stage 6 — Trap trigger detection
#
# Canonical effect contract:  TRIGGER → CONDITION → TARGET SELECTION → EFFECT
#   TRIGGER          a piece entered a Trap's area / a capture happened there
#   CONDITION        the trap is armed (charges != 0), it isn't the owner's
#                     own piece, and the triggering unit isn't trap_immune
#   TARGET SELECTION  already resolved by the caller (moving/capturing unit)
#   EFFECT            each of the TrapCard's effects, via the shared
#                      EFFECT_REGISTRY — no Trap-specific effect dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _consume_trap_immunity(unit: UnitInstance) -> bool:
    """
    If ``unit`` carries "trap_immune:N" (N > 0), consume one charge and
    return True — the caller should skip applying the Trap's effects to
    this unit (ancient_tortoise), though the Trap itself still triggers.
    """
    for idx, status in enumerate(unit.statuses):
        if status.startswith("trap_immune:"):
            charges = int(status.split(":")[1])
            if charges > 0:
                unit.statuses[idx] = f"trap_immune:{charges - 1}"
                return True
    return False


def _fire_trap(
    state: "GameState",
    trap,   # TrapInstance
    triggering_unit: UnitInstance,
    triggering_pos: Position,
    trigger_name: str,
    events: "list[Event]",
    registry: "object",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Resolve one already-matched Trap against one already-selected target.

    Kings are immune to Trap effects (README's original pit_trap design:
    "Kings are immune" — reaching the King is handled entirely through
    check/checkmate/Final Duel, not through incidental battlefield
    hazards). The Trap still triggers — charges are consumed and
    TrapTriggered still fires — the King just isn't affected by it.
    """
    from game.core.events import TrapTriggered
    from game.core.phases import PieceType

    card = registry.get(trap.card_id)
    is_king = triggering_unit.piece.piece_type == PieceType.KING
    immune = is_king or _consume_trap_immunity(triggering_unit)
    if not immune:
        for effect in card.effects:
            ctx = _make_ctx(
                state, triggering_unit, triggering_pos, card, effect, events,
                rng=rng, registry=registry, trigger=trigger_name,
                extra={"owner_override": trap.owner},
            )
            try:
                resolve_effect(ctx)
            except NotImplementedError:
                _log.debug("TRAP     NotImplemented for %r — skipped", effect.type)

    events.append(TrapTriggered(
        trap_instance_id=trap.id,
        triggering_piece_id=triggering_unit.piece.id,
    ))

    if trap.charges is not None:
        trap.charges -= 1
        if trap.charges <= 0:
            state.traps = [t for t in state.traps if t.id != trap.id]


def check_enter_radius_traps(
    state: "GameState",
    moving_unit: UnitInstance,
    target_pos: Position,
    events: "list[Event]",
    registry: "object | None",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Fire every enemy-owned ``enter_radius`` Trap whose area now contains
    ``target_pos``.  Called from _execute_move_piece right after a piece
    lands on its destination square (any move, capture or not).
    """
    from game.cards.card import TrapCard, TrapTrigger
    from game.mechanics.area import in_area

    if registry is None:
        return

    for trap in list(state.traps):
        if trap.owner == moving_unit.owner:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.ENTER_RADIUS:
            continue
        # Stage 8: Watchtower extends the owner's Trap influence radius.
        from game.mechanics.buildings import trap_radius_bonus
        radius = card.radius + trap_radius_bonus(state, trap, registry)
        if not in_area(target_pos, trap.position, radius, card.shape):
            continue
        _fire_trap(state, trap, moving_unit, target_pos, "enter_radius", events, registry, rng=rng)


def check_capture_traps(
    state: "GameState",
    capturing_unit: UnitInstance,
    captured_owner: str,
    position: Position,
    events: "list[Event]",
    registry: "object | None",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Fire every ``capture``-trigger Trap belonging to ``captured_owner``
    (the defender being raided) whose area contains ``position``.  Called
    from _execute_move_piece right after a capture resolves — the target
    of the Trap's effects is the CAPTURING piece (counter_strike).
    """
    from game.cards.card import TrapCard, TrapTrigger
    from game.mechanics.area import in_area

    if registry is None:
        return

    for trap in list(state.traps):
        if trap.owner != captured_owner:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.CAPTURE:
            continue
        # Stage 8: Watchtower extends the owner's Trap influence radius.
        from game.mechanics.buildings import trap_radius_bonus
        radius = card.radius + trap_radius_bonus(state, trap, registry)
        if not in_area(position, trap.position, radius, card.shape):
            continue
        _fire_trap(state, trap, capturing_unit, position, "capture", events, registry, rng=rng)
