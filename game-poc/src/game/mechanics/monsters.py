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
    position: "Position | None" = None,
    registry: "object | None" = None,
) -> None:
    """
    Apply all effects from ``card`` that trigger on_summon.

    Delegates each effect to the appropriate handler in the effects registry.
    Stub effects (not yet implemented) are silently skipped unless the effect
    explicitly raises (which only happens in future stages).

    ``position`` (optional — the vessel's board square) is forwarded to each
    EffectContext so position-dependent on_summon handlers (e.g.
    guild_foreman's restore_builder, which scans adjacent squares) can
    work; callers that omit it just get those specific effects skipped
    (silently, same as an unregistered/stub type — most on_summon effects
    don't need a position at all).
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
        # against this unit. "adjacent_king_targeted" (royal_guard's
        # intercept) is the same shape: a charge banked on summon, spent
        # only when an assault on the King it escorts actually arrives.
        # "when_sacrificed" (blood_seer's sacrifice_bonus) joins them for the
        # same reason: the handler only banks a status that
        # mechanics.rituals.execute_ritual() reads when the unit is later
        # spent as Ritual material. There is no other moment at which to
        # arm it — by the time the sacrifice happens the unit is being
        # removed, not summoned.
        if trigger not in (
            "on_summon", "passive", "", "capture_attempt",
            "adjacent_king_targeted", "when_sacrificed",
        ):
            _log.debug("SUMMON   skip effect %r (trigger=%r)", effect.type, trigger)
            continue

        ctx = _make_ctx(state, unit, position, card, effect, events, rng,
                        registry=registry, trigger=trigger)
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
    state: "GameState | None" = None,
    position: "Position | None" = None,
    registry: "object | None" = None,
) -> list[tuple[int, int]]:
    """
    Return extra movement direction vectors added to the vessel's base moves
    by this monster's ``alter_movement`` effect.

    Currently supported:
        add_leap: true  — adds 4 cardinal leap squares at ``leap_distance``.

    ``state``/``position``/``registry`` are optional and only needed for the
    two GameState-aware bonuses below (omitted call sites just skip them,
    same as every other optional-registry query in this module):

        territory_movement_bonus:<N>  — moon_stalker's territory_bonus.
            Adds N cardinal leap squares while ``position`` sits inside the
            OPPONENT's Territory (mechanics.territory.is_in_territory).
        graveyard_leap:<threshold>:<bonus>  — crypt_walker's
            graveyard_scaling_movement. Adds ``bonus`` cardinal leap squares
            once ``len(owner.graveyard) >= threshold``.
    """
    extras: list[tuple[int, int]] = []
    for effect in card.effects:
        if effect.type != "alter_movement":
            continue
        if effect.params.get("add_leap"):
            d = effect.params.get("leap_distance", 2)
            extras.extend([(d, 0), (-d, 0), (0, d), (0, -d)])

    if state is not None and position is not None:
        for status in unit.statuses:
            if status.startswith("territory_movement_bonus:"):
                bonus = int(status.split(":")[1])
                from game.mechanics.territory import is_in_territory
                opponent = state.opponent_of(unit.owner)
                if is_in_territory(state, position, opponent, registry):
                    extras.extend([(bonus, 0), (-bonus, 0), (0, bonus), (0, -bonus)])
            elif status.startswith("graveyard_leap:"):
                _, threshold_s, bonus_s = status.split(":")
                threshold, bonus = int(threshold_s), int(bonus_s)
                if len(state.get_player(unit.owner).graveyard) >= threshold:
                    extras.extend([(bonus, 0), (-bonus, 0), (0, bonus), (0, -bonus)])

    return extras


# ─────────────────────────────────────────────────────────────────────────────
# suppress_monster_effects (monster_seal spell, nullification_glyph trap)
# ─────────────────────────────────────────────────────────────────────────────

def is_effects_suppressed(unit: UnitInstance) -> bool:
    """
    True if ``unit`` carries ``effects_suppressed:N`` — its Monster's
    LIVE/passive contributions (damage_aura, movement_restriction,
    vessel_support, suppress_spell_zone, obscure_influence,
    capture_protection, activated abilities) are disabled while this
    holds. "Vessel movement remains intact" per the card text: this never
    touches the vessel's own base movement, only checks scattered across
    the various board-scanning functions below.

    Scope note: statuses already granted at summon time (shield:N,
    spell_radius_bonus:N, ...) are NOT retroactively revoked — there's no
    generic "undo an already-applied on_summon effect" machinery in this
    engine. Suppression stops NEW live contributions; it doesn't erase
    ones already banked before it was applied.
    """
    return any(s.startswith("effects_suppressed:") for s in unit.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# Capture protection
# ─────────────────────────────────────────────────────────────────────────────

def apply_capture_protection(unit: UnitInstance) -> bool:
    """
    Check whether the unit has a shield and consume one charge.

    Returns True if the capture was absorbed (unit survives).
    Returns False if the unit has no shield — normal capture proceeds.

    Stage 6: a unit with "exposed:N" (capture_vulnerability — pit_trap,
    counter_strike; also reused verbatim by vengeance_mark's "Vulnerable"
    status) never has its shield checked at all — it is captured normally
    even if it holds shield charges. Same for a unit whose Monster
    effects are suppressed (monster_seal, nullification_glyph).
    """
    if any(s.startswith("exposed:") for s in unit.statuses):
        return False
    if is_effects_suppressed(unit):
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
        if is_effects_suppressed(unit):
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
    registry: "object | None" = None,
) -> bool:
    """
    Read side of ``immobilize_zone`` (write side:
    board_control._immobilize_zone — cursed_ground).

    If ``target_pos`` carries a ``cursed:<N>:<owner>`` tag, immobilize
    ``moving_unit`` — no owner check, unlike Traps: cursed_ground affects
    "any piece", including the caster's own.  Called from
    _execute_move_piece for every completed move (capture or not).

    Stage: a spellbreaker's ``suppress_spell_zone`` aura neutralises this
    (cursed_ground is a Spell) if ``target_pos`` lies within its radius —
    see is_spell_zone_suppressed().

    Returns True if the moving unit was immobilized.
    """
    if is_spell_zone_suppressed(state, target_pos, registry):
        return False
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
# cancel_capture (guardian_sigils) — checked alongside apply_capture_protection
# ─────────────────────────────────────────────────────────────────────────────

def find_cancel_capture_trap(
    state: "GameState", target_pos: Position, registry: "object | None",
) -> "object | None":
    """
    guardian_sigils: the first armed (charges != 0) ``cancel_capture``
    Trap belonging to the DEFENDER (``protected_owner: trap_owner`` — the
    Trap owner IS the protected side, i.e. the Trap must belong to
    whoever currently occupies ``target_pos``) whose radius covers
    ``target_pos``, or None. Called from _execute_move_piece BEFORE the
    board mutation, alongside apply_capture_protection — same "check
    before you leap" ordering as the shield check.
    """
    if registry is None:
        return None
    target_unit = state.board.get_unit(target_pos)
    if target_unit is None:
        return None
    from game.cards.card import TrapCard, TrapTrigger
    from game.mechanics.area import in_area

    for trap in state.traps:
        if trap.owner != target_unit.owner or trap.charges == 0:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.CAPTURE:
            continue
        for effect in card.effects:
            if effect.type == "cancel_capture" and in_area(target_pos, trap.position, trap.radius, trap.shape):
                return trap
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Summoning prohibition (sanctuary spell, vessel_lock trap)
# ─────────────────────────────────────────────────────────────────────────────

def find_interceptor(
    state: "GameState",
    king_pos: Position,
    king_owner: str,
) -> "tuple[Position, UnitInstance] | None":
    """
    royal_guard's ``intercept`` — "can intercept one attack targeting an
    adjacent King".

    Returns the first allied unit adjacent to ``king_pos`` that still
    carries an unspent ``intercept:N`` charge, or None. The caller
    (RulesEngine._execute_move_piece) redirects the assault onto it: the
    escort dies, the King survives, and no Final Duel is triggered.

    The charge is NOT spent here — ``consume_intercept()`` does that, so a
    caller that decides not to redirect after all (e.g. no room to resolve
    the redirect) leaves the escort's charge intact.
    """
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = king_pos.file + df, king_pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            cand_pos = Position(nf, nr)
            unit = state.board.get_unit(cand_pos)
            if unit is None or unit.owner != king_owner:
                continue
            if is_effects_suppressed(unit):
                continue
            for status in unit.statuses:
                if status.startswith("intercept:") and int(status.split(":")[1]) > 0:
                    return cand_pos, unit
    return None


def consume_intercept(unit: UnitInstance) -> None:
    """Spend one ``intercept:N`` charge on ``unit`` (see find_interceptor)."""
    for idx, status in enumerate(unit.statuses):
        if status.startswith("intercept:"):
            charges = int(status.split(":")[1])
            unit.statuses[idx] = f"intercept:{max(0, charges - 1)}"
            return


def is_summoning_prohibited(board, pos: Position, player_id: str) -> bool:
    """
    True if ``pos`` carries a ``no_summon:`` tag that applies to
    ``player_id`` (either ``blocked == "both"`` or ``blocked ==
    player_id``) — see mechanics.effects.board_control._prohibit_summoning
    for the tag format.
    """
    for eff in board.get_square(pos).temporary_effects:
        if not eff.startswith("no_summon:"):
            continue
        parts = eff.split(":")
        blocked = parts[3] if len(parts) > 3 else "both"
        if blocked == "both" or blocked == player_id:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Spell zone suppression (spellbreaker) — queried live like damage_aura
# ─────────────────────────────────────────────────────────────────────────────

def is_spell_zone_suppressed(
    state: "GameState",
    pos: Position,
    registry: "object | None",
) -> bool:
    """
    True if ``pos`` lies within the radius of ANY monster (either side)
    carrying ``suppress_spell_zone`` — spellbreaker's "Continuous Spell
    effects are suppressed while their affected squares overlap this
    monster's radius". Scoped to the two Spell-authored continuous zone
    tags (``blocked:`` — veil_of_stillness, ``cursed:`` — cursed_ground),
    not to Monster-caused ``frozen:``/``scorched:`` square effects, which
    aren't Spells.
    """
    if registry is None:
        return False
    from game.cards.card import MonsterCard

    for owner in ("white", "black"):
        for spos, unit in state.board.all_units_for(owner):
            if unit.monster_id is None:
                continue
            if is_effects_suppressed(unit):
                continue
            try:
                card = registry.get(unit.monster_id)
            except KeyError:
                continue
            if not isinstance(card, MonsterCard):
                continue
            for effect in card.effects:
                if effect.type != "suppress_spell_zone":
                    continue
                radius = effect.params.get("radius", 1)
                if (
                    abs(pos.file - spos.file) <= radius
                    and abs(pos.rank - spos.rank) <= radius
                ):
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
        if is_effects_suppressed(enemy_unit):
            continue
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
        if is_effects_suppressed(unit):
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
        # Stage 13: a COMPLETE Building the DEFENDER may not enter is a wall
        # for this shove too — fall back to a normal capture.
        from game.chess.movement import blocks_movement
        if blocks_movement(state.board, push_dest, defender.owner):
            continue

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
    lets the attacker bypass retaliation — but only when ``captured_unit``'s
    own capture_protection was already spent (card text: "Excels against
    units whose defensive effects have already been spent" —
    condition.capture_protection_remaining == 0), not unconditionally.

    Returns True if the attacker was destroyed.
    """
    from game.core.events import MonsterDestroyed, PieceCaptured, Retaliated

    if "retaliate" not in captured_unit.statuses:
        return False
    if "guaranteed_capture_vs_no_shield" in attacker_unit.statuses:
        shield_remaining = 0
        for status in captured_unit.statuses:
            if status.startswith("shield:"):
                shield_remaining = int(status.split(":")[1])
                break
        if shield_remaining == 0:
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
                extra={
                    "owner_override": trap.owner,
                    "trap_position": (trap.position.file, trap.position.rank),
                },
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


def check_summon_traps(
    state: "GameState",
    summoned_unit: UnitInstance,
    summon_pos: Position,
    events: "list[Event]",
    registry: "object | None",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Fire every enemy-owned ``summon`` Trap whose area contains
    ``summon_pos`` (transformation_alarm, nullification_glyph). Called
    from ``_execute_summon_monster`` / ``_execute_activate_ritual`` right
    after the Monster is placed — the summon itself is never prevented,
    matching both cards' own text ("The summon is not prevented" /
    "enters normally").
    """
    from game.cards.card import TrapCard, TrapTrigger
    from game.mechanics.area import in_area

    if registry is None:
        return

    for trap in list(state.traps):
        if trap.owner == summoned_unit.owner:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.SUMMON:
            continue
        from game.mechanics.buildings import trap_radius_bonus
        radius = card.radius + trap_radius_bonus(state, trap, registry)
        if not in_area(summon_pos, trap.position, radius, card.shape):
            continue
        _fire_trap(state, trap, summoned_unit, summon_pos, "summon", events, registry, rng=rng)


def check_summon_reactions(
    state: "GameState",
    summoned_unit: UnitInstance,
    summon_pos: Position,
    events: "list[Event]",
    registry: "object | None",
    rng: "DeterministicRNG | None" = None,
) -> None:
    """
    spirit_hunter — "When an enemy monster is summoned nearby, draw 1 card."

    The Monster-borne counterpart to check_summon_traps() above: that one
    scans ``state.traps`` for a SUMMON-trigger Trap, this one scans the
    BOARD for an enemy Monster carrying a ``draw_card`` effect whose
    trigger is ``enemy_summon_nearby``. Same call site
    (RulesEngine._execute_summon_monster, right after the Monster lands),
    same "the summon itself is never prevented" contract.

    ``ctx.extra["owner_override"]`` hands _draw_card the REACTING player,
    since ctx.unit there is the reactor rather than a triggering enemy.
    """
    from game.cards.card import MonsterCard
    from game.mechanics.area import in_area

    if registry is None:
        return

    watcher_owner = state.opponent_of(summoned_unit.owner)
    for pos, unit in state.board.all_units_for(watcher_owner):
        if unit.monster_id is None:
            continue
        if is_effects_suppressed(unit):
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.params.get("trigger") != "enemy_summon_nearby":
                continue
            if not in_area(summon_pos, pos, effect.params.get("radius", 1)):
                continue
            ctx = _make_ctx(
                state, unit, pos, card, effect, events, rng=rng, registry=registry,
                trigger="enemy_summon_nearby",
                extra={"owner_override": unit.owner},
            )
            resolve_effect(ctx)


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


# ─────────────────────────────────────────────────────────────────────────────
# Stage 13 — start-of-turn Monster auras
#
# A handful of Ritual Monsters carry effects that were originally written as
# King POLICIES (bannerlord_eternal's formation_support, ossuary_king's
# graveyard_threshold_bonus). A policy is re-evaluated every turn — the
# Graveyard grows, formations shift — so these can't be armed once at summon
# time the way a flat passive can. This mirror of
# mechanics.kings.apply_king_policy_auras() gives the Monster-borne versions
# the same refresh, from the same call site (core/game.py _auto_start_turn).
# ─────────────────────────────────────────────────────────────────────────────

def apply_monster_auras(state: "GameState", player_id: str, registry: "object | None") -> None:
    """
    Refresh the policy-shaped effects carried by ``player_id``'s own
    summoned Monsters. Idempotent — a unit that already holds the granted
    status is left alone, exactly like the King and Building aura passes.
    """
    if registry is None:
        return
    from game.cards.card import MonsterCard

    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None or is_effects_suppressed(unit):
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.type == "formation_support":
                _apply_monster_formation_support(state, player_id, pos, effect, registry)
            elif effect.type == "graveyard_threshold_bonus":
                _apply_monster_graveyard_threshold(state, player_id, effect, registry)


def _apply_monster_formation_support(
    state: "GameState", player_id: str, source_pos: Position, effect, registry: "object",
) -> None:
    """
    IMPLEMENTED — bannerlord_eternal: "Warriors fighting near the
    Bannerlord gain protection."

    Differs from marshal_king's kingdom-wide ``formation_support`` policy
    in exactly one way, and it's the way the card text differs too: this
    one is anchored to the Bannerlord's own square and only reaches
    ``radius`` from it. The grant itself — a ``shield:N`` charge on allied
    Monsters of ``archetype`` — is identical, so it reuses the same status
    and therefore the same capture-protection machinery.
    """
    from game.cards.card import MonsterCard
    from game.mechanics.area import in_area

    params = effect.params
    archetype = params.get("archetype")
    radius = params.get("radius", 1)
    bonus = params.get("capture_protection_bonus", 1)

    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None or pos == source_pos:
            continue
        if not in_area(pos, source_pos, radius):
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard) or (archetype and card.archetype != archetype):
            continue
        if not any(s.startswith("shield:") for s in unit.statuses):
            unit.add_status(f"shield:{bonus}")


def _apply_monster_graveyard_threshold(
    state: "GameState", player_id: str, effect, registry: "object",
) -> None:
    """
    IMPLEMENTED — ossuary_king's ``graveyard_threshold_bonus``. Identical
    in shape to grave_crowned_king's King-policy version
    (mechanics.kings._apply_graveyard_threshold_bonus): once the owner's
    Graveyard reaches ``threshold``, every allied Monster of
    ``target_archetype`` carries a capture-protection charge.
    """
    from game.cards.card import MonsterCard

    params = effect.params
    ps = state.get_player(player_id)
    if len(ps.graveyard) < params.get("threshold", 5):
        return
    archetype = params.get("target_archetype")
    bonus = params.get("bonus", {}).get("capture_protection_uses", 1)

    for _pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard) or (archetype and card.archetype != archetype):
            continue
        if not any(s.startswith("shield:") for s in unit.statuses):
            unit.add_status(f"shield:{bonus}")


def has_monster_graveyard_recycle(
    state: "GameState", player_id: str, registry: "object | None",
) -> bool:
    """
    IMPLEMENTED — ossuary_king's ``graveyard_recycle``. Reports whether
    ``player_id`` has a summoned Monster carrying the effect, so
    mechanics.kings.maybe_recycle_destroyed_monster() can treat it as an
    additional source alongside grave_crowned_king's King policy — same
    once-per-turn cap, same bottom-of-deck destination, one shared latch
    (a player running both doesn't recycle twice).
    """
    if registry is None:
        return False
    from game.cards.card import MonsterCard

    for _pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None or is_effects_suppressed(unit):
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if isinstance(card, MonsterCard) and any(
            e.type == "graveyard_recycle" for e in card.effects
        ):
            return True
    return False
