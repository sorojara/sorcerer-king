"""
mechanics/effects/_meta.py — Miscellaneous / multi-category effects
====================================================================

These effect types appear in monsters.yaml but don't belong cleanly to a
single primary category, or they span multiple systems not yet implemented.

Effect types handled here
--------------------------
copy_effect                 IMPLEMENTED — copy adjacent monster effect (mirror_magus)
vessel_support               IMPLEMENTED — allow extra vessel types in radius
                                  (broodmother; aura scan lives in
                                  mechanics.monsters.get_extra_vessel_types(),
                                  wired into SummonMonster legal-action
                                  generation + validation)
ignore_terrain               STUB  — skip building/territory movement costs
                                  (needs the Building/Territory systems)
building_damage_bonus       STUB  — destroy buildings on capture (needs the
                                  Building system)
building_capture_protection STUB  — absorb building-destroy attempts (needs
                                  the Building system)
weakened_target_bonus       IMPLEMENTED (flag only) — bypasses an adjacent
                                  retaliate effect; see
                                  mechanics.monsters.apply_retaliate()
restore_effect_charge       IMPLEMENTED — restore a spent effect charge to an
                                  adjacent ally at end of the owner's own turn
                                  (battlefield_medic; resolved in
                                  _execute_end_turn)
challenge_unit               STUB (flag only) — restrict an adjacent unit to
                                  fight only this one; the flag is armed but
                                  capture-legality enforcement is deferred
                                  (needs two-sided move-generation changes)
territory_bonus              STUB  — extra mobility in enemy territory (needs
                                  the Territory system)
graveyard_inspect            STUB  — view own graveyard (needs private/public
                                  Observation plumbing for deck contents)
graveyard_counter            STUB (flag only) — passive counter from
                                  destroyed allies (needs a generic
                                  event-triggered passive-effect scanner)
capture_protection_from_counter STUB  — shield charges from counter (depends
                                  on graveyard_counter above)
dismiss_monster              STUB  — activated dismiss of an ADJACENT ally
                                  (distinct from the player's own
                                  DismissMonster action, which already
                                  works); needs a target-selection
                                  PendingDecision flow not yet built
graveyard_scaling_movement  STUB (flag only) — leap bonus at graveyard
                                  threshold (get_movement_additions() has no
                                  GameState/graveyard access at its call site)
death_trigger_draw          STUB (flag only) — draw on ally death (needs the
                                  same event-triggered scanner as
                                  graveyard_counter)
sacrifice_bonus              STUB (flag only) — extra ritual progress when
                                  sacrificed (needs the Ritual system)
ritual_activation_range     STUB (flag only) — extend ritual pattern range
                                  (needs the Ritual system)
restore_builder              STUB  — restore builder token to pawn (needs the
                                  Building system)
capture_then_retreat        IMPLEMENTED — post-capture retreat away from the
                                  enemy King (dusk_reaver; reuses the
                                  REPOSITION PendingDecision machinery)
spell_radius_bonus          IMPLEMENTED (flag only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


def _spell_radius_bonus(ctx: "EffectContext") -> None:
    """IMPLEMENTED — passive flag applied at summon time (dark_magician)."""
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("bonus", 0)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("spell_radius_bonus:")]
    ctx.unit.add_status(f"spell_radius_bonus:{bonus}")


# Effect types that are safe to copy (passive — no on-activation side effects).
# Excludes effects that require their own separate activation logic.
_COPYABLE_PASSIVE_TYPES: frozenset[str] = frozenset({
    "spell_radius_bonus",
    "alter_movement",
    "capture_protection",
    "damage_aura",
    "ignore_terrain",
    "building_damage_bonus",
    "weakened_target_bonus",
    "territory_bonus",
    "graveyard_counter",
    "graveyard_scaling_movement",
    "death_trigger_draw",
    "sacrifice_bonus",
    "ritual_activation_range",
})


def _copy_effect(ctx: "EffectContext") -> None:
    """Copy one copyable passive effect from an adjacent allied monster.

    Scans the 8 squares around the caster for an allied unit that carries
    at least one effect type in _COPYABLE_PASSIVE_TYPES.  The first such
    effect found is stored as ``copied_effect:<type>`` in the caster's
    statuses.  The status is visible in the right-click info panel.

    Raises IllegalActionError when:
      • ctx.unit or ctx.position is missing
      • No adjacent allied monster with a copyable effect exists
      • ctx.registry is unavailable
    """
    from game.cards.card import MonsterCard
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None:
        raise IllegalActionError("copy_effect: missing unit or position.")
    if ctx.registry is None:
        raise IllegalActionError("copy_effect: no card registry available.")
    if ctx.state is None:
        raise IllegalActionError("copy_effect: no game state.")

    pos = ctx.position
    owner = ctx.unit.owner
    caster_id = ctx.unit.piece.id

    _log.debug("COPY     caster=%s  pos=%s  owner=%s  scanning neighbours...",
               caster_id, pos, owner)

    # Scan the 8 neighbouring squares
    found_type: str | None = None
    found_from: str | None = None
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = pos.file + df, pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            from game.chess.pieces import Position as _Pos
            neighbour_pos = _Pos(nf, nr)
            neighbour_unit = ctx.state.board.get_unit(neighbour_pos)
            if neighbour_unit is None:
                _log.debug("COPY       [%d,%d] empty", nf, nr)
                continue
            if neighbour_unit.owner != owner:
                _log.debug("COPY       [%d,%d] %s — wrong owner (%s)",
                           nf, nr, neighbour_unit.piece.id, neighbour_unit.owner)
                continue
            if neighbour_unit.monster_id is None:
                _log.debug("COPY       [%d,%d] %s — no monster",
                           nf, nr, neighbour_unit.piece.id)
                continue
            try:
                neighbour_card = ctx.registry.get(neighbour_unit.monster_id)
            except KeyError:
                _log.debug("COPY       [%d,%d] monster_id=%r not in registry",
                           nf, nr, neighbour_unit.monster_id)
                continue
            if not isinstance(neighbour_card, MonsterCard):
                continue
            _log.debug("COPY       [%d,%d] %s (monster=%s) effects=%s",
                       nf, nr, neighbour_unit.piece.id, neighbour_unit.monster_id,
                       [e.type for e in neighbour_card.effects])
            for eff in neighbour_card.effects:
                if eff.type in _COPYABLE_PASSIVE_TYPES:
                    found_type = eff.type
                    found_from = neighbour_unit.piece.id
                    _log.debug("COPY       found copyable effect %r from %s",
                               found_type, found_from)
                    break
        if found_type:
            break

    if found_type is None:
        _log.warning("COPY     FAILED  caster=%s — no adjacent ally with copyable effect", caster_id)
        raise IllegalActionError(
            "No adjacent allied monster with a copyable passive effect found."
        )

    # Remove any previous copied_effect status, then add the new one
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("copied_effect:")
    ]
    ctx.unit.add_status(f"copied_effect:{found_type}")
    _log.info("COPY     OK  caster=%s  copied=%r  from=%s  statuses=%s",
              caster_id, found_type, found_from, ctx.unit.statuses)


def _vessel_support(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — broodmother (real logic lives elsewhere).

    This is a passive positional aura: it doesn't change anything about
    broodmother's own unit, it extends the set of legal vessel piece types
    for OTHER summons happening nearby.  That can't be expressed as a status
    flag resolved once at summon time — it has to be re-checked every time a
    SummonMonster action is validated or enumerated.

    The actual scan is `mechanics.monsters.get_extra_vessel_types()`, called
    from both legal-action generation and `_execute_summon_monster()` in
    rules.py.  This handler is a no-op placeholder so the effect type is
    recognised by the registry and never logged as unresolved.
    """
    # Real implementation: mechanics.monsters.get_extra_vessel_types()
    pass


def _ignore_terrain(ctx: "EffectContext") -> None:
    """STUB — Stage 8+. Ignore building/territory movement restrictions."""
    if ctx.unit is None:
        return
    params = ctx.effect.params
    if params.get("buildings"):
        ctx.unit.add_status("ignore_building_terrain")
    # NOTE: movement generation must check this status (Stage 8+).


def _building_damage_bonus(ctx: "EffectContext") -> None:
    """STUB — Stage 8+. Buildings captured by this monster are destroyed immediately."""
    if ctx.unit is None:
        return
    ctx.unit.add_status("building_destroyer")
    # NOTE: building capture must check this status (Stage 8+).


def _building_capture_protection(ctx: "EffectContext") -> None:
    """STUB — Stage 8+. First destroy-attempt on nearby allied building fails."""
    raise NotImplementedError(
        "building_capture_protection is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _weakened_target_bonus(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Guaranteed capture of shield-depleted units."""
    if ctx.unit is None:
        return
    ctx.unit.add_status("guaranteed_capture_vs_no_shield")
    # NOTE: capture validation must check this status (Stage 7+).


def _restore_effect_charge(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — battlefield_medic (real logic lives elsewhere).

    This only fires at end_of_turn, scanning the board for adjacency —
    not something a summon-time status flag can express.  The actual
    restoration is ``RulesEngine._resolve_restore_effect_charge()``, called
    from ``_execute_end_turn`` for the ending player's own units.  This
    handler is a no-op placeholder so the type is recognised by the
    registry and never logged as unresolved.
    """
    # Real implementation: RulesEngine._resolve_restore_effect_charge()
    pass


def _challenge_unit(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Restrict adjacent enemy to fight only this unit for 1 turn.

    Records a status to mark that a challenge was issued.  The movement-
    restriction logic that enforces the challenge will be wired up in Stage 7.
    """
    if ctx.unit is None:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    ctx.unit.add_status(f"challenge_issued:{duration}")


def _territory_bonus(ctx: "EffectContext") -> None:
    """STUB — Stage 8+. Extra movement in enemy territory."""
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("movement_bonus", 1)
    ctx.unit.add_status(f"territory_movement_bonus:{bonus}")
    # NOTE: movement generation must check this status (Stage 8+).


def _graveyard_inspect(ctx: "EffectContext") -> None:
    """STUB — Stage 6+. View own graveyard contents on summon."""
    raise NotImplementedError(
        "graveyard_inspect is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _graveyard_counter(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Passive counter that increments when allied units die."""
    if ctx.unit is None:
        return
    ctx.unit.add_status("graveyard_counter:0")
    # NOTE: the counter is incremented by MonsterDestroyed/PieceCaptured hook (Stage 7+).


def _capture_protection_from_counter(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Grant shield charges based on graveyard counter."""
    raise NotImplementedError(
        "capture_protection_from_counter is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _dismiss_monster(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Activated ability to dismiss an adjacent allied monster."""
    raise NotImplementedError(
        "dismiss_monster (as an activated effect) is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _graveyard_scaling_movement(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Bonus leap when graveyard reaches threshold."""
    if ctx.unit is None:
        return
    threshold = ctx.effect.params.get("threshold", 5)
    bonus_leap = ctx.effect.params.get("bonus_leap", 1)
    ctx.unit.add_status(f"graveyard_leap:{threshold}:{bonus_leap}")
    # NOTE: movement generation checks this status against graveyard size (Stage 7+).


def _death_trigger_draw(ctx: "EffectContext") -> None:
    """STUB — Stage 7+. Draw a card once per turn when an allied monster dies."""
    if ctx.unit is None:
        return
    ctx.unit.add_status("death_trigger_draw:1")
    # NOTE: draw hook on MonsterDestroyed event (Stage 7+).


def _sacrifice_bonus(ctx: "EffectContext") -> None:
    """STUB — Stage 11+. Extra ritual progress when this unit is sacrificed."""
    if ctx.unit is None:
        return
    progress = ctx.effect.params.get("ritual_progress", 2)
    ctx.unit.add_status(f"sacrifice_ritual_bonus:{progress}")
    # NOTE: ritual sacrifice logic reads this status (Stage 11+).


def _ritual_activation_range(ctx: "EffectContext") -> None:
    """STUB — Stage 11+. Extend ritual pattern matching range."""
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("radius_bonus", 1)
    ctx.unit.add_status(f"ritual_range_bonus:{bonus}")
    # NOTE: ritual pattern matching reads this status (Stage 11+).


def _restore_builder(ctx: "EffectContext") -> None:
    """STUB — Stage 8+. On summon, restore builder token to adjacent pawn."""
    raise NotImplementedError(
        "restore_builder is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _capture_then_retreat(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — dusk_reaver after-capture effect.

    After capturing, the attacker may retreat up to ``max_distance`` squares
    (Chebyshev) provided the destination is empty and, if
    ``must_move_away_from_enemy_king`` is set, no closer to the enemy King
    than the attacker's current position.

    Structurally identical to ``movement._reposition_unit`` (blade_dancer) —
    reuses the same REPOSITION PendingDecision / RepositionUnit resolver —
    with the extra enemy-King-distance filter applied to the option list.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType
    from game.core.state import PendingDecision

    params  = ctx.effect.params
    trigger = params.get("trigger", "")
    if trigger != "after_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    max_dist = params.get("max_distance", 1)
    must_flee = params.get("must_move_away_from_enemy_king", False)
    attacker_pos = ctx.position

    king_dist = None
    if must_flee:
        opponent = "black" if ctx.unit.owner == "white" else "white"
        king_result = ctx.state.board.find_king(opponent)
        if king_result is not None:
            king_pos, _ = king_result
            king_dist = max(
                abs(attacker_pos.file - king_pos.file),
                abs(attacker_pos.rank - king_pos.rank),
            )

    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            if df == 0 and dr == 0:
                continue
            nf = attacker_pos.file + df
            nr = attacker_pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            if ctx.state.board.get_unit(Position(nf, nr)) is not None:
                continue
            if king_dist is not None:
                king_result = ctx.state.board.find_king(
                    "black" if ctx.unit.owner == "white" else "white"
                )
                if king_result is not None:
                    king_pos, _ = king_result
                    new_dist = max(abs(nf - king_pos.file), abs(nr - king_pos.rank))
                    if new_dist < king_dist:
                        continue  # would move closer to the enemy King
            options.append((nf, nr))

    if options and ctx.state.pending_decision is None:
        ctx.state.pending_decision = PendingDecision(
            player_id=ctx.unit.owner,
            decision_type=DecisionType.REPOSITION,
            options=options,
            min_choices=1,
            max_choices=1,
            context={
                "piece_id": ctx.unit.piece.id,
                "current_pos": (attacker_pos.file, attacker_pos.rank),
            },
        )


META_HANDLERS: dict[str, object] = {
    "spell_radius_bonus":               _spell_radius_bonus,
    "copy_effect":                      _copy_effect,
    "vessel_support":                   _vessel_support,
    "ignore_terrain":                   _ignore_terrain,
    "building_damage_bonus":            _building_damage_bonus,
    "building_capture_protection":      _building_capture_protection,
    "weakened_target_bonus":            _weakened_target_bonus,
    "restore_effect_charge":            _restore_effect_charge,
    "challenge_unit":                   _challenge_unit,
    "territory_bonus":                  _territory_bonus,
    "graveyard_inspect":                _graveyard_inspect,
    "graveyard_counter":                _graveyard_counter,
    "capture_protection_from_counter":  _capture_protection_from_counter,
    "dismiss_monster":                  _dismiss_monster,
    "graveyard_scaling_movement":       _graveyard_scaling_movement,
    "death_trigger_draw":               _death_trigger_draw,
    "sacrifice_bonus":                  _sacrifice_bonus,
    "ritual_activation_range":          _ritual_activation_range,
    "restore_builder":                  _restore_builder,
    "capture_then_retreat":             _capture_then_retreat,
}
