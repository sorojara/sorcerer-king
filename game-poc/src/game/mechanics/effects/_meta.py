"""
mechanics/effects/_meta.py — Miscellaneous / multi-category effects
====================================================================

These effect types appear in monsters.yaml but don't belong cleanly to a
single primary category, or they span multiple systems not yet implemented.

Effect types handled here
--------------------------
copy_effect                 STUB  — copy adjacent monster effect (Stage 7+)
vessel_support              STUB  — allow extra vessel types in radius (Stage 7+)
ignore_terrain              STUB  — skip building/territory movement costs (Stage 8+)
building_damage_bonus       STUB  — destroy buildings on capture (Stage 8+)
building_capture_protection STUB  — absorb building-destroy attempts (Stage 8+)
weakened_target_bonus       STUB  — auto-capture shield-depleted units (Stage 7+)
restore_effect_charge       STUB  — restore spent effect charges nearby (Stage 7+)
challenge_unit              STUB  — restrict adjacent unit to fight only this one (Stage 7+)
territory_bonus             STUB  — extra mobility in enemy territory (Stage 8+)
graveyard_inspect           STUB  — view own graveyard (Stage 6+)
graveyard_counter           STUB  — passive counter from destroyed allies (Stage 7+)
capture_protection_from_counter STUB  — shield charges from counter (Stage 7+)
dismiss_monster             STUB  — dismiss adjacent monster (Stage 7+)
graveyard_scaling_movement  STUB  — leap bonus at graveyard threshold (Stage 7+)
death_trigger_draw          STUB  — draw on ally death (Stage 7+)
sacrifice_bonus             STUB  — extra ritual progress when sacrificed (Stage 11+)
ritual_activation_range     STUB  — extend ritual pattern range (Stage 11+)
restore_builder             STUB  — restore builder token to pawn (Stage 8+)
capture_then_retreat        STUB  — post-capture retreat (Stage 7+)
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
    """STUB — Stage 7+. Allow extra vessel types for dragon monsters in radius."""
    raise NotImplementedError(
        "vessel_support is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


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
    """STUB — Stage 7+. Restore one spent defensive charge to an adjacent ally."""
    raise NotImplementedError(
        "restore_effect_charge is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


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
    """STUB — Stage 7+. After capture, retreat up to max_distance squares away from enemy king."""
    raise NotImplementedError(
        "capture_then_retreat is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
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
