"""
mechanics/effects/buildings.py — BUILDINGS category
====================================================

Effect types in this category
------------------------------
construction_speed_bonus    IMPLEMENTED — master_mason; consumed in
                                 RulesEngine._execute_start_construction via
                                 mechanics.buildings.monster_construction_speed_bonus()
repair_building              IMPLEMENTED — royal_engineer /
                                 worldforge_colossus. Real logic lives in
                                 mechanics.buildings.repair_buildings(), an
                                 end-of-turn board scan (an ``end_of_turn``
                                 repeating effect can't be expressed as a
                                 one-shot summon-time status). Stage 13.
building_aura                IMPLEMENTED — siege_captain /
                                 titan_of_the_foundation. Arms
                                 ``building_aura:<radius>:<bonus>``, read on
                                 every blow by
                                 mechanics.buildings.aura_durability_bonus()
                                 ("require one additional successful hostile
                                 action to destroy"). Stage 13.
disable_building              IMPLEMENTED — saboteur activated ability; suspends
                                 a COMPLETE enemy Building's auras/support via
                                 BuildingInstance.disabled_turns
territory_expansion          IMPLEMENTED — frontier_warden; consumed in
                                 mechanics.territory._building_zone_squares()
advance_construction         IMPLEMENTED — rapid_construction (Spell,
                                 target_type "building"); real logic in
                                 mechanics.buildings.advance_one_building()
building_vulnerability        IMPLEMENTED — siege_order. Stage 13: marks a
                                 COMPLETE enemy Building so every blow lands
                                 harder AND so ANY unit may besiege it, not
                                 only Ritual Monsters
building_damage               IMPLEMENTED — demolition_charge (Trap, adjacent
                                 enemy Buildings) and sunder_the_walls
                                 (Spell, one named Building); both go through
                                 the same defensive stack a siege goes through
temporary_building_protection IMPLEMENTED — emergency_fortifications. Stage
                                 13: absorbs the next N blows that would
                                 destroy an allied Building
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

Stage 13 note: every "ARMED, DORMANT" entry that used to sit in this list
was blocked on the same missing mechanic — a Building had no durability and
nothing could attack one. mechanics/buildings.py now owns that contest
(damage_building / can_attack_building), so the whole family resolves for
real.
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
    IMPLEMENTED — royal_engineer / worldforge_colossus (real logic lives
    elsewhere).

    ``trigger: end_of_turn`` means this repeats every turn, which a
    summon-time status can't express — mechanics.buildings.repair_buildings()
    rescans the board at the owner's EndTurn instead (the same shape as
    _resolve_restore_effect_charge). No-op placeholder here so the type is
    recognised by the registry and never logged as unresolved.
    """
    pass


def _building_aura(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — siege_captain / titan_of_the_foundation.

    Arms ``building_aura:<radius>:<bonus>`` on the Monster itself.
    mechanics.buildings.aura_durability_bonus() reads it fresh on every
    blow, so the shield follows the Monster around the board and vanishes
    the moment it is captured — "Allied Buildings within N squares require
    one additional successful hostile action to destroy".
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
    IMPLEMENTED — siege_order ("Mark one enemy Building for destruction.
    Its defensive protection is reduced for 2 turns.").

    ``ctx.extra["building_instance_id"]`` names the target (supplied by
    RulesEngine._resolve_spell_on_building). "Protection reduced" is read
    as two things at once, both consumed by
    mechanics.buildings.damage_building():

      • every blow against it lands ``protection_reduction`` harder, and
      • the Building is open to attack by ANY enemy unit for the duration,
        not just the Ritual Monsters / building_damage_bonus Monsters that
        may normally besiege — this Spell IS the "or spells that allow
        that" clause of the siege rule.

    The mark decays on the target OWNER's own EndTurn
    (mechanics.buildings.tick_building_timers).
    """
    from game.core.events import BuildingVulnerable
    from game.core.phases import ConstructionStatus

    if ctx.state is None:
        return
    building_id = (ctx.extra or {}).get("building_instance_id")
    if building_id is None:
        return
    building = next((b for b in ctx.state.buildings if b.id == building_id), None)
    if building is None or building.status != ConstructionStatus.COMPLETE:
        return

    # Ownership ("target_owner: opponent") is already enforced by
    # RulesEngine._resolve_spell_on_building before dispatch.
    params = ctx.effect.params
    duration = params.get("duration_turns", 2)
    amount = params.get("protection_reduction", 1)
    building.vulnerable_turns = max(building.vulnerable_turns, duration)
    building.vulnerable_amount = max(building.vulnerable_amount, amount)
    ctx.events.append(BuildingVulnerable(
        building_instance_id=building.id,
        position=building.position,
        duration_turns=duration,
        extra_damage=amount,
    ))


def _temporary_building_protection(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — emergency_fortifications ("Give one allied Building
    protection from the next effect that would destroy it during the next
    2 turns.").

    ``ctx.extra["building_instance_id"]`` names the target. Stores
    ``protection_uses`` charges for ``duration_turns``; the last line of
    mechanics.buildings.damage_building()'s defensive stack spends one to
    convert a lethal blow into a Building left standing on 1 integrity.
    """
    from game.core.events import BuildingProtected
    from game.core.phases import ConstructionStatus

    if ctx.state is None:
        return
    building_id = (ctx.extra or {}).get("building_instance_id")
    if building_id is None:
        return
    building = next((b for b in ctx.state.buildings if b.id == building_id), None)
    if building is None or building.status != ConstructionStatus.COMPLETE:
        return

    # Ownership ("target_owner" defaults to "self") is already enforced by
    # RulesEngine._resolve_spell_on_building before dispatch.
    params = ctx.effect.params
    uses = params.get("uses", 1)
    duration = params.get("duration_turns", 2)
    building.protection_uses = max(building.protection_uses, uses)
    building.protection_turns = max(building.protection_turns, duration)
    ctx.events.append(BuildingProtected(
        building_instance_id=building.id,
        position=building.position,
        uses=uses,
        duration_turns=duration,
    ))


def _building_damage(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — two delivery mechanisms, one payload.

    1. demolition_charge (Trap, "adjacent enemy Buildings", amount 1).
       Fired by mechanics.monsters._fire_trap, which supplies
       ``ctx.extra["trap_position"]`` (the charge's own square) and
       ``ctx.extra["owner_override"]`` (the Trap owner); ``ctx.unit`` is
       the piece that sprang it. Every COMPLETE Building adjacent to the
       charge and belonging to the triggering side takes ``amount``
       damage. ``condition.triggering_owner: opponent`` is already
       guaranteed by check_enter_radius_traps (a Trap only ever fires
       against the other side) and is re-asserted below so a
       manual-activation path can never smuggle in a self-hit.

    2. sunder_the_walls (Spell, target_type "building"). ``ctx.unit`` is
       None and ``ctx.extra["building_instance_id"]`` names the single
       target outright — no radius scan, no triggering piece. Ownership
       ("target_owner: opponent") is already enforced by
       RulesEngine._resolve_spell_on_building before dispatch.

    Either way the damage goes through the same defensive stack a siege
    goes through (mechanics.buildings.damage_building), so castle_keeper's
    shield, a durability aura and emergency_fortifications each still get
    their say. Neither path needs attack PERMISSION the way AttackBuilding
    does: a charge under the foundations is not a unit trying to besiege.
    """
    from game.chess.pieces import Position
    from game.core.phases import ConstructionStatus
    from game.mechanics.area import in_area
    from game.mechanics.buildings import damage_building

    if ctx.state is None:
        return

    params = ctx.effect.params

    # ── (2) Spell: one named Building, no geometry ───────────────────────
    building_id = (ctx.extra or {}).get("building_instance_id")
    if building_id is not None:
        target = next((b for b in ctx.state.buildings if b.id == building_id), None)
        if target is None or target.status != ConstructionStatus.COMPLETE:
            return
        damage_building(
            ctx.state, target, params.get("amount", 1), ctx.events, ctx.registry,
            attacker_piece_id=None,
        )
        return

    # ── (1) Trap: every adjacent Building on the triggering side ─────────
    if ctx.unit is None:
        return

    extra = ctx.extra or {}
    trap_pos = extra.get("trap_position")
    center = Position(*trap_pos) if trap_pos is not None else ctx.position
    if center is None:
        return

    victim = ctx.unit.owner
    trap_owner = extra.get("owner_override")
    if params.get("condition", {}).get("triggering_owner") == "opponent":
        if trap_owner is not None and victim == trap_owner:
            return

    amount = params.get("amount", 1)
    radius = 1 if params.get("adjacent_enemy_buildings", True) else params.get("radius", 1)
    for b in list(ctx.state.buildings):
        if b.owner != victim or b.status != ConstructionStatus.COMPLETE:
            continue
        if not in_area(b.position, center, radius):
            continue
        damage_building(
            ctx.state, b, amount, ctx.events, ctx.registry,
            attacker_piece_id=None,
        )


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
