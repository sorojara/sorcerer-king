"""
mechanics/effects/buildings.py — BUILDINGS category
====================================================

Effect types in this category
------------------------------
construction_speed_bonus    IMPLEMENTED — master_mason; consumed in
                                 RulesEngine._execute_start_construction via
                                 mechanics.buildings.monster_construction_speed_bonus()
repair_building              ARMED, DORMANT — no Building durability/damage
                                 system exists to repair (see building_aura)
building_aura                ARMED, DORMANT — no Building "destroy attempt"
                                 exists to grant durability against (README §11:
                                 Buildings "do not normally capture" — the only
                                 destruction paths are construction-disruption
                                 and the King Succession sacrifice, neither of
                                 which is a contested "attack")
disable_building              IMPLEMENTED — saboteur activated ability; suspends
                                 a COMPLETE enemy Building's auras/support via
                                 BuildingInstance.disabled_turns
territory_expansion          IMPLEMENTED — frontier_warden; consumed in
                                 mechanics.territory._building_zone_squares()
advance_construction         IMPLEMENTED — rapid_construction (Spell,
                                 target_type "building"); real logic in
                                 mechanics.buildings.advance_one_building()
building_vulnerability        ARMED, DORMANT — siege_order. Same blocker as
                                 building_aura/repair_building: no Building
                                 durability system exists to reduce
temporary_building_protection ARMED, DORMANT — emergency_fortifications. Same
                                 blocker: no "destroy attempt" to protect
                                 against
building_damage               ARMED, DORMANT — demolition_charge. Same
                                 blocker: no durability field to damage
protect_builder                IMPLEMENTED — builders_ward (real logic lives
                                 elsewhere: mechanics.buildings.
                                 apply_builders_ward_aura(), refreshed at the
                                 start of the Trap owner's own turn rather
                                 than through its declared ``enter_radius``
                                 trigger — see that function's docstring for
                                 why enter_radius doesn't fit this card).
                                 This entry is a no-op placeholder so the
                                 type is recognised and never logged as
                                 unresolved if check_enter_radius_traps ever
                                 does match it against an enemy piece
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


def _construction_speed_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — master_mason passive (real logic lives elsewhere).

    Applied at construction-start time, not summon time — a status flag
    can't express "reduce THIS building's remaining_turns", so this is a
    no-op placeholder (like damage_aura/vessel_support) so the type is
    recognised. The actual reduction is
    ``mechanics.buildings.monster_construction_speed_bonus()``, called
    from ``RulesEngine._execute_start_construction`` alongside the
    existing architect_king King-policy version of the same bonus.
    """
    pass


def _repair_building(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — royal_engineer. BuildingInstance has no durability/
    damage field and nothing in this engine ever damages a COMPLETE
    Building, so there is nothing to repair yet. Registered as a no-op so
    activating it doesn't crash; flagged to the user as needing a
    Building-durability mechanic that doesn't exist today.
    """
    pass


def _building_aura(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — siege_captain. Same blocker as repair_building: no
    Building durability system and no "destroy attempt" event to grant a
    bonus against. Arms a status for a future durability system to read.
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 1)
    bonus = ctx.effect.params.get("allied_building_bonus", {}).get("durability", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("building_aura:")]
    ctx.unit.add_status(f"building_aura:{radius}:{bonus}")


def _disable_building(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — saboteur activated ability.

    ``ctx.extra["target"]`` is the building_instance_id of an adjacent
    enemy COMPLETE Building (enumerated by RulesEngine.get_legal_actions).
    Sets ``BuildingInstance.disabled_turns``, consulted by
    mechanics.buildings.apply_building_auras() /
    mechanics.buildings.trap_radius_bonus() /
    mechanics.territory._building_zone_squares() to suspend that
    Building's effects until it decays on the OWNER's own EndTurn (same
    convention as burrow_cooldown/immobilized — "until the start of the
    next owner turn").
    """
    from game.core.phases import ConstructionStatus
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    target_id = (ctx.extra or {}).get("target")
    if target_id is None:
        raise IllegalActionError("disable_building requires an adjacent enemy Building target.")

    building = next((b for b in ctx.state.buildings if b.id == target_id), None)
    if (
        building is None
        or building.owner == ctx.unit.owner
        or building.status != ConstructionStatus.COMPLETE
    ):
        raise IllegalActionError("disable_building target must be an adjacent enemy Building.")
    pos = building.position
    if max(abs(pos.file - ctx.position.file), abs(pos.rank - ctx.position.rank)) != 1:
        raise IllegalActionError("disable_building target must be adjacent.")

    duration = ctx.effect.params.get("duration_turns", 1)
    building.disabled_turns = max(building.disabled_turns, duration)

    from game.core.events import BuildingDisabled
    ctx.events.append(BuildingDisabled(
        building_instance_id=building.id,
        position=pos,
        disabled_by_piece_id=ctx.unit.piece.id,
    ))


def _territory_expansion(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — frontier_warden passive (real logic lives elsewhere).

    Like construction_speed_bonus, this can't be expressed as a
    summon-time status on frontier_warden itself — it has to be
    re-evaluated per-Building whenever Territory is computed. No-op
    placeholder here; the actual radius bonus is applied in
    mechanics.territory._building_zone_squares(), which scans for an
    adjacent allied monster carrying this effect for each Building.
    """
    pass


def _advance_construction(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — rapid_construction. ``ctx.extra["building_instance_id"]``
    names the target (supplied by RulesEngine._resolve_spell_on_building).
    Real logic: mechanics.buildings.advance_one_building().
    """
    if ctx.state is None:
        return
    building_id = (ctx.extra or {}).get("building_instance_id")
    if building_id is None:
        return
    building = next((b for b in ctx.state.buildings if b.id == building_id), None)
    if building is None:
        return
    from game.mechanics.buildings import advance_one_building
    turns = ctx.effect.params.get("turns", 1)
    advance_one_building(ctx.state, building, turns, ctx.events)


def _building_vulnerability(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — siege_order. No Building durability field exists to
    reduce; registered as a no-op so activating the Spell doesn't crash.
    """
    pass


def _temporary_building_protection(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — emergency_fortifications. No "destroy attempt" to
    protect against yet; registered as a no-op so activating the Spell
    doesn't crash.
    """
    pass


def _building_damage(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — demolition_charge. No Building durability field
    exists to damage; registered as a no-op so this Trap triggering
    doesn't crash or log unresolved.
    """
    pass


def _protect_builder(ctx: "EffectContext") -> None:
    """No-op placeholder — see module docstring. Real logic lives in
    mechanics.buildings.apply_builders_ward_aura()."""
    pass


BUILDINGS_HANDLERS: dict[str, object] = {
    "construction_speed_bonus": _construction_speed_bonus,
    "repair_building":          _repair_building,
    "building_aura":            _building_aura,
    "disable_building":         _disable_building,
    "territory_expansion":      _territory_expansion,
    "advance_construction":     _advance_construction,
    "building_vulnerability":   _building_vulnerability,
    "temporary_building_protection": _temporary_building_protection,
    "building_damage":          _building_damage,
    "protect_builder":          _protect_builder,
}
