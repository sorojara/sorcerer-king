"""
test_effect_scenarios_buildings_duel.py — one scenario per BUILDINGS,
KING/DUEL and remaining META effect type.

Covered (data/*.yaml declarations):
    construction_speed_bonus      master_mason, worldforge_colossus, architect_king
    repair_building               royal_engineer, worldforge_colossus
    building_aura                 siege_captain, titan_of_the_foundation
    building_capture_protection   castle_keeper
    building_damage_bonus         obsidian_dragon, sovereign_of_embers
    disable_building              saboteur
    territory_expansion           frontier_warden, titan_of_the_foundation
    advance_construction          rapid_construction
    building_vulnerability        siege_order
    temporary_building_protection emergency_fortifications
    building_damage               demolition_charge
    protect_builder               builders_ward
    capture_protection_aura       fortress
    spell_radius_aura             shrine
    trap_radius_bonus             watchtower
    king_support_bonus            royal_guard, bannerlord_eternal, marshal_king
    royal_support_suppression     kingsbane, eclipse_executioner, ...
    duel_debuff                   royal_poisoner
    royal_support_mark            kingslayer_alarm
    royal_support_bonus           rally_the_kingdom
    royal_support_range_bonus     muster_bell
    duel_escape_bonus             royal_escape_route
    final_duel_guard_bonus        fortress
    copy_effect                   mirror_magus
    challenge_unit                duelist
    dismiss_monster               vessel_reclaimer, sever_the_bond
    restore_builder               guild_foreman
    restore_effect_charge         battlefield_medic
    capture_then_retreat          dusk_reaver
    temporary_vessel_class        unstable_transmutation
    vessel_support                broodmother
    spell_radius_bonus            dark_magician
    territory_bonus               moon_stalker
    enemy_territory_mobility      eclipse_executioner
    graveyard_scaling_movement    crypt_walker
"""

from __future__ import annotations

import pytest

from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import Position
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateSpell,
    AttackBuilding,
    EndTurn,
    MovePiece,
    StartConstruction,
)
from game.core.phases import ConstructionStatus, FinalDuelType, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError
from game.mechanics.buildings import can_attack_building, damage_building
from game.mechanics.monsters import check_enter_radius_traps
from game.mechanics.territory import is_in_territory

from tests.effect_harness import (  # noqa: F401
    crown,
    engine,
    find_at,
    new_state,
    place_building,
    place_trap,
    put,
    registry,
    rng,
    spell_reach,
    statuses_with,
)


# ─────────────────────────────────────────────────────────────────────────────
# construction_speed_bonus / advance_construction
# ─────────────────────────────────────────────────────────────────────────────

def test_master_mason_speeds_up_an_adjacent_pawns_construction(registry):
    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    put(state, Position(3, 2), "white", "pawn", "master_mason", registry)
    engine(registry).execute(
        state,
        StartConstruction(player_id="white", building_card_id="fortress",
                          pawn_position=Position(3, 1)),
        DeterministicRNG(1),
    )
    b = state.buildings[-1]
    assert b.remaining_turns == 2, "fortress takes 3 turns; the mason shaves one off"


def test_construction_without_a_mason_takes_the_full_time(registry):
    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    engine(registry).execute(
        state,
        StartConstruction(player_id="white", building_card_id="fortress",
                          pawn_position=Position(3, 1)),
        DeterministicRNG(1),
    )
    assert state.buildings[-1].remaining_turns == 3


def test_rapid_construction_advances_a_building_by_one_turn(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 1), "white",
                       ConstructionStatus.UNDER_CONSTRUCTION, remaining_turns=3)
    state.get_player("white").hand.append("rapid_construction")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="rapid_construction", target=b.id),
        DeterministicRNG(1),
    )
    assert b.remaining_turns == 2


# ─────────────────────────────────────────────────────────────────────────────
# repair_building
# ─────────────────────────────────────────────────────────────────────────────

def test_royal_engineer_repairs_a_nearby_damaged_building(registry):
    state = new_state(phase=Phase.CHESS)
    b = place_building(state, "fortress", Position(3, 1), "white")
    b.max_integrity, b.integrity = 3, 1
    put(state, Position(3, 2), "white", "pawn", "royal_engineer", registry)
    engine(registry).execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert b.integrity == 2


def test_a_damaged_building_stays_damaged_without_an_engineer(registry):
    state = new_state(phase=Phase.CHESS)
    b = place_building(state, "fortress", Position(3, 1), "white")
    b.max_integrity, b.integrity = 3, 1
    engine(registry).execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert b.integrity == 1


# ─────────────────────────────────────────────────────────────────────────────
# building_aura / building_capture_protection / temporary_building_protection
# ─────────────────────────────────────────────────────────────────────────────

def test_siege_captain_arms_its_durability_aura(registry):
    state = new_state()
    captain = put(state, Position(3, 1), "white", "rook", "siege_captain", registry)
    assert statuses_with(captain, "building_aura:") == ["building_aura:1:1"]


def test_siege_captain_makes_an_allied_building_take_one_more_blow(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 1), "white")
    b.max_integrity = b.integrity = 1
    put(state, Position(3, 2), "white", "rook", "siege_captain", registry)
    damage_building(state, b, 1, [], registry, attacker_piece_id="attacker")
    assert b.status == ConstructionStatus.COMPLETE, "the aura should have absorbed the blow"


def test_castle_keeper_absorbs_the_first_attempt_entirely(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 1), "white")
    b.max_integrity = b.integrity = 1
    keeper = put(state, Position(3, 2), "white", "rook", "castle_keeper", registry)
    assert statuses_with(keeper, "building_capture_protection:") == ["building_capture_protection:1:1"]
    damage_building(state, b, 1, [], registry, attacker_piece_id="attacker")
    assert b.status == ConstructionStatus.COMPLETE
    assert b.integrity == 1, "the whole hostile action was absorbed, not just its damage"


def test_emergency_fortifications_saves_a_building_from_a_lethal_blow(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 1), "white")
    b.max_integrity = b.integrity = 1
    state.get_player("white").hand.append("emergency_fortifications")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="emergency_fortifications", target=b.id),
        DeterministicRNG(1),
    )
    assert b.protection_uses == 1
    damage_building(state, b, 5, [], registry, attacker_piece_id="attacker")
    assert b.status == ConstructionStatus.COMPLETE
    assert b.integrity == 1


# ─────────────────────────────────────────────────────────────────────────────
# building_vulnerability / building_damage_bonus / building_damage
# ─────────────────────────────────────────────────────────────────────────────

def test_siege_order_opens_a_building_to_any_attacker(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 4), "black")
    plain = put(state, Position(3, 3), "white", "rook")
    assert not can_attack_building(state, plain, b, registry), "a plain Rook cannot normally besiege"
    spell_reach(state, "white", (3, 4))
    state.get_player("white").hand.append("siege_order")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="siege_order", target=b.id),
        DeterministicRNG(1),
    )
    assert b.vulnerable_turns == 2
    assert can_attack_building(state, plain, b, registry), "siege_order IS the 'or spells that allow that' clause"


def test_obsidian_dragon_flattens_a_building_in_one_blow(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 4), "black")
    b.max_integrity = b.integrity = 3
    dragon = put(state, Position(3, 3), "white", "rook", "obsidian_dragon", registry)
    assert can_attack_building(state, dragon, b, registry)
    damage_building(state, b, 99, [], registry, attacker_piece_id=dragon.piece.id)
    assert b.status == ConstructionStatus.DESTROYED


def test_demolition_charge_damages_an_adjacent_enemy_building(registry):
    state = new_state(phase=Phase.CHESS)
    b = place_building(state, "fortress", Position(3, 4), "white")
    b.max_integrity = b.integrity = 3
    place_trap(state, registry, "demolition_charge", Position(3, 3), "black")
    intruder = put(state, Position(2, 3), "white", "rook")
    check_enter_radius_traps(state, intruder, Position(2, 3), [], registry, rng=DeterministicRNG(7))
    assert b.integrity == 2


# ─────────────────────────────────────────────────────────────────────────────
# disable_building
# ─────────────────────────────────────────────────────────────────────────────

def test_saboteur_disables_an_adjacent_enemy_building(registry):
    state = new_state(phase=Phase.CHESS)
    b = place_building(state, "watchtower", Position(3, 4), "black")
    put(state, Position(3, 3), "white", "pawn", "saboteur", registry)
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                               ability_id="disable_building", target=b.id),
        DeterministicRNG(1),
    )
    assert b.disabled_turns == 1


def test_saboteur_cannot_disable_a_distant_building(registry):
    state = new_state(phase=Phase.CHESS)
    b = place_building(state, "watchtower", Position(6, 6), "black")
    put(state, Position(3, 3), "white", "pawn", "saboteur", registry)
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                                   ability_id="disable_building", target=b.id),
            DeterministicRNG(1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# territory_expansion / building auras
# ─────────────────────────────────────────────────────────────────────────────

def test_frontier_warden_pushes_a_buildings_territory_one_square_further(registry):
    state = new_state()
    place_building(state, "fortress", Position(3, 3), "white")
    edge = Position(3, 6)   # fortress radius 2 → normally out of reach
    assert not is_in_territory(state, edge, "white", registry)
    put(state, Position(3, 4), "white", "rook", "frontier_warden", registry)
    assert is_in_territory(state, edge, "white", registry)


def test_fortress_shields_allied_monsters_in_its_shadow(registry):
    from game.mechanics.buildings import apply_building_auras

    state = new_state()
    place_building(state, "fortress", Position(3, 3), "white")
    unit = put(state, Position(3, 4), "white", "rook")
    unit.monster_id = "stone_golem"
    unit.statuses = []
    apply_building_auras(state, "white", registry)
    assert statuses_with(unit, "shield:"), "fortress' capture_protection_aura"


def test_shrine_widens_a_nearby_monsters_spell_radius(registry):
    from game.mechanics.buildings import apply_building_auras

    state = new_state()
    place_building(state, "shrine", Position(3, 3), "white")
    unit = put(state, Position(3, 4), "white", "bishop")
    unit.monster_id = "dark_magician"
    unit.statuses = []
    apply_building_auras(state, "white", registry)
    assert statuses_with(unit, "spell_radius_bonus:")


def test_watchtower_extends_a_nearby_traps_radius(registry):
    from game.mechanics.buildings import trap_radius_bonus

    state = new_state()
    place_building(state, "watchtower", Position(3, 3), "white")
    trap = place_trap(state, registry, "pit_trap", Position(3, 4), "white")
    assert trap_radius_bonus(state, trap, registry) == 1


def test_builders_ward_shields_a_committed_builder(registry):
    from game.mechanics.buildings import apply_builders_ward_aura

    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    b = place_building(state, "fortress", Position(3, 1), "white",
                       ConstructionStatus.UNDER_CONSTRUCTION, remaining_turns=3)
    b.builder_piece_id = pawn.piece.id
    place_trap(state, registry, "builders_ward", Position(3, 2), "white")
    apply_builders_ward_aura(state, "white", registry)
    assert statuses_with(pawn, "shield:")


# ─────────────────────────────────────────────────────────────────────────────
# KING / DUEL
# ─────────────────────────────────────────────────────────────────────────────

class _Duel:
    """Thin view over DuelState so a test can talk about "all support"."""

    def __init__(self, duel):
        self._duel = duel

    def __getattr__(self, name):
        return getattr(self._duel, name)

    @property
    def all_support(self):
        return list(self._duel.attacker_support) + list(self._duel.defender_support)


def _start_duel(state, registry, attacker="black", defender="white",
                trigger_position=None):
    eng = engine(registry)
    events: list = []
    # ASSAULT rather than SIEGE: a SIEGE deliberately strips one of the
    # defender's support items (README §25's siege advantage), which would
    # silently eat whichever item the test is actually about.
    eng._trigger_final_duel(
        state, FinalDuelType.ASSAULT, attacker, defender, events,
        trigger_position=trigger_position,
    )
    return _Duel(state.duel)


def test_royal_guard_adds_a_defender_guard_at_duel_start(registry):
    state = new_state()
    put(state, Position(3, 0), "white", "pawn", "royal_guard", registry)
    duel = _start_duel(state, registry)
    assert duel.defender_bonus_guards >= 1


def test_kingsbane_debuffs_the_defenders_guards(registry):
    state = new_state()
    put(state, Position(3, 1), "black", "knight", "kingsbane", registry)
    duel = _start_duel(state, registry)
    assert duel.defender_guard_debuff >= 1


def test_royal_poisoner_debuffs_when_it_is_close_to_the_king(registry):
    state = new_state()
    put(state, Position(3, 1), "black", "bishop", "royal_poisoner", registry)
    duel = _start_duel(state, registry)
    assert duel.defender_guard_debuff >= 1


def test_kingslayer_alarm_marks_a_unit_and_the_duel_honours_the_mark(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "kingslayer_alarm", Position(3, 1), "white")
    marked = put(state, Position(3, 2), "black", "pawn")
    check_enter_radius_traps(state, marked, Position(3, 2), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(marked, "duel_marked:") == ["duel_marked:1"]

    duel = _start_duel(state, registry, attacker="black", defender="white",
                       trigger_position=Position(4, 0))
    attacker_items = [i for i in duel.all_support if i.origin_piece_id == marked.piece.id]
    assert not attacker_items, "a marked Pawn's single point of support is cancelled out"


def test_rally_the_kingdom_raises_every_qualifying_supporters_amount(registry):
    state = new_state()
    put(state, Position(3, 1), "white", "rook")
    state.get_player("white").hand.append("rally_the_kingdom")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="rally_the_kingdom", target=None),
        DeterministicRNG(1),
    )
    ps = state.get_player("white")
    assert ps.royal_support_bonus_amount == 1
    assert ps.royal_support_bonus_turns == 2

    duel = _start_duel(state, registry, trigger_position=Position(4, 0))
    rook_items = [i for i in duel.all_support if i.owner == "white" and i.source == "piece"]
    assert rook_items and rook_items[0].amount >= 2


def test_muster_bell_widens_the_defenders_support_range(registry):
    state = new_state()
    place_trap(state, registry, "muster_bell", Position(4, 0), "white")
    far = put(state, Position(4, 3), "white", "rook")
    duel = _start_duel(state, registry, trigger_position=Position(4, 0))
    assert any(i.origin_piece_id == far.piece.id for i in duel.all_support), \
        "muster_bell should pull a piece just outside the default radius into range"


def test_royal_escape_route_grants_an_extra_escape(registry):
    state = new_state()
    place_trap(state, registry, "royal_escape_route", Position(4, 0), "white")
    duel = _start_duel(state, registry, trigger_position=Position(4, 0))
    assert duel.escape_shield_charges >= 1


def test_fortress_contributes_a_guard_item_to_the_duel(registry):
    state = new_state()
    place_building(state, "fortress", Position(4, 1), "white")
    duel = _start_duel(state, registry, trigger_position=Position(4, 0))
    assert any(i.source == "building" and i.owner == "white" for i in duel.all_support)


def test_watchtower_extends_final_duel_support_range(registry):
    from game.mechanics.duel import _support_radius_bonus

    state = new_state()
    place_building(state, "watchtower", Position(4, 1), "white")
    assert _support_radius_bonus(state, "white", registry) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# META — activated abilities
# ─────────────────────────────────────────────────────────────────────────────

def test_mirror_magus_copies_an_adjacent_allys_passive(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "bishop", "mirror_magus", registry)
    put(state, Position(3, 4), "white", "bishop", "dark_magician", registry)
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                               ability_id="copy_effect"),
        DeterministicRNG(1),
    )
    magus = state.board.get_unit(Position(3, 3))
    assert statuses_with(magus, "copied_effect:") == ["copied_effect:spell_radius_bonus"]


def test_duelist_challenge_restricts_the_targets_captures(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "knight", "duelist", registry)
    target = put(state, Position(3, 4), "black", "rook")
    bystander = put(state, Position(2, 5), "white", "pawn")
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                               ability_id="challenge_unit", target=(3, 4)),
        DeterministicRNG(1),
    )
    assert statuses_with(target, "challenged_by:")
    moves = get_pseudo_legal_moves(state.board, Position(3, 4), target, registry=registry, state=state)
    assert Position(2, 5) not in moves, "a challenged unit may only fight its challenger"
    assert Position(3, 3) in moves, "but it may still fight the Duelist"


def test_vessel_reclaimer_dismisses_an_adjacent_ally_to_the_graveyard(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "bishop", "vessel_reclaimer", registry)
    ally = put(state, Position(3, 4), "white", "bishop", "dark_magician", registry)
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                               ability_id="dismiss_monster", target=(3, 4)),
        DeterministicRNG(1),
    )
    assert ally.monster_id is None
    assert find_at(state, ally) == Position(3, 4), "the Vessel survives"
    assert state.get_player("white").graveyard == ["dark_magician"]


def test_sever_the_bond_returns_your_own_monster_to_hand(registry):
    """
    sever_the_bond: 'Dismiss one of your Monsters. Restore its original
    chess Vessel and return the Monster card to your hand.'
    """
    state = new_state()
    unit = put(state, Position(3, 3), "white", "bishop", "dark_magician", registry)
    state.get_player("white").hand.append("sever_the_bond")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="sever_the_bond", target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    assert unit.monster_id is None
    assert find_at(state, unit) == Position(3, 3)
    assert "dark_magician" in state.get_player("white").hand
    assert "dark_magician" not in state.get_player("white").graveyard


def test_guild_foreman_restores_an_adjacent_pawns_builder_token(registry):
    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    pawn.builder_available = False
    put(state, Position(3, 2), "white", "pawn", "guild_foreman", registry)
    assert pawn.builder_available is True


def test_battlefield_medic_restores_a_spent_shield_charge(registry):
    state = new_state(phase=Phase.CHESS)
    ally = put(state, Position(3, 1), "white", "knight", "arcane_sentinel", registry)
    ally.statuses = [s for s in ally.statuses if not s.startswith("shield:")]
    ally.add_status("shield:0")
    put(state, Position(3, 2), "white", "pawn", "battlefield_medic", registry)
    engine(registry).execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert statuses_with(ally, "shield:") == ["shield:1"]


def test_dusk_reaver_opens_a_retreat_decision_after_capturing(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "knight", "dusk_reaver", registry)
    put(state, Position(4, 5), "black", "pawn")
    engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(4, 5)),
        DeterministicRNG(1),
    )
    assert state.pending_decision is not None
    options = state.pending_decision.options
    assert options, "dusk_reaver should be offered somewhere to fall back to"
    # Every option must be no closer to the enemy King on e8 than d5 already is.
    current = max(abs(4 - 4), abs(5 - 7))
    assert all(max(abs(f - 4), abs(r - 7)) >= current for f, r in options)


def test_unstable_transmutation_grants_a_temporary_vessel_class(registry):
    state = new_state()
    unit = put(state, Position(3, 3), "white", "rook")
    state.get_player("white").hand.append("unstable_transmutation")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="unstable_transmutation",
                      target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    assert statuses_with(unit, "vessel_class_override:")


def test_broodmother_lets_a_nearby_dragon_use_a_pawn_vessel(registry):
    from game.mechanics.monsters import get_extra_vessel_types

    state = new_state()
    put(state, Position(3, 3), "white", "queen", "broodmother", registry)
    extra = get_extra_vessel_types(state, Position(3, 4), "white", "dragon", registry)
    assert "pawn" in extra


def test_dark_magician_arms_its_spell_radius_bonus(registry):
    state = new_state()
    dm = put(state, Position(3, 3), "white", "bishop", "dark_magician", registry)
    assert statuses_with(dm, "spell_radius_bonus:") == ["spell_radius_bonus:1"]


def test_moon_stalker_gains_leaps_only_inside_enemy_territory(registry):
    state = new_state()
    stalker = put(state, Position(3, 3), "white", "knight", "moon_stalker", registry)
    assert statuses_with(stalker, "territory_movement_bonus:") == ["territory_movement_bonus:1"]
    neutral = get_pseudo_legal_moves(state.board, Position(3, 3), stalker,
                                     registry=registry, state=state)
    assert Position(4, 3) not in neutral, "no bonus outside enemy Territory"

    # Black's home rank IS black Territory.
    state.board.move_unit(Position(3, 3), Position(3, 7))
    inside = get_pseudo_legal_moves(state.board, Position(3, 7), stalker,
                                    registry=registry, state=state)
    assert Position(2, 7) in inside or Position(3, 6) in inside


def test_crypt_walker_gains_a_leap_once_the_graveyard_is_deep_enough(registry):
    state = new_state()
    walker = put(state, Position(3, 3), "white", "pawn", "crypt_walker", registry)
    assert statuses_with(walker, "graveyard_leap:") == ["graveyard_leap:5:1"]
    before = get_pseudo_legal_moves(state.board, Position(3, 3), walker,
                                    registry=registry, state=state)
    state.get_player("white").graveyard = ["a", "b", "c", "d", "e"]
    after = get_pseudo_legal_moves(state.board, Position(3, 3), walker,
                                   registry=registry, state=state)
    assert len(after) > len(before)


def test_eclipse_executioner_moves_further_inside_enemy_territory(registry):
    state = new_state()
    exe = put(state, Position(3, 3), "white", "knight", "eclipse_executioner", registry)
    assert statuses_with(exe, "territory_movement_bonus:") == ["territory_movement_bonus:2"]


# ─────────────────────────────────────────────────────────────────────────────
# King policies — effects that live on a King card rather than a unit
# ─────────────────────────────────────────────────────────────────────────────

def test_mourning_queen_draws_when_an_allied_monster_dies(registry):
    state = new_state(phase=Phase.CHESS, active="black")
    ps = state.get_player("white")
    put(state, Position(0, 1), "white", "queen", "mourning_queen", registry)
    put(state, Position(3, 4), "white", "pawn", "apprentice_mage", registry)
    ps.hand.clear()
    ps.deck = ["a_card"]     # stocked AFTER the mage's own on-summon draw
    put(state, Position(3, 5), "black", "rook")
    engine(registry).execute(
        state, MovePiece(player_id="black", source=Position(3, 5), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert ps.hand == ["a_card"]


def test_mourning_queen_draws_only_once_per_turn(registry):
    state = new_state(phase=Phase.CHESS, active="black")
    ps = state.get_player("white")
    put(state, Position(0, 1), "white", "queen", "mourning_queen", registry)
    put(state, Position(3, 4), "white", "pawn", "apprentice_mage", registry)
    put(state, Position(5, 4), "white", "pawn", "apprentice_mage", registry)
    ps.hand.clear()
    ps.graveyard.clear()
    ps.deck = ["a_card", "b_card"]
    put(state, Position(3, 5), "black", "rook")
    put(state, Position(5, 5), "black", "rook")
    eng = engine(registry)
    eng.execute(state, MovePiece(player_id="black", source=Position(3, 5), target=Position(3, 4)),
                DeterministicRNG(1))
    state.get_player("black").chess_move_used = False
    state.phase = Phase.CHESS
    eng.execute(state, MovePiece(player_id="black", source=Position(5, 5), target=Position(5, 4)),
                DeterministicRNG(1))
    assert ps.hand == ["a_card"], "limit_per_turn: 1"


def test_grave_crowned_king_recycles_the_first_monster_lost_each_turn(registry):
    from game.mechanics.kings import maybe_recycle_destroyed_monster

    state = new_state()
    crown(state, "white", "grave_crowned_king")
    ps = state.get_player("white")
    assert maybe_recycle_destroyed_monster(state, "white", "stone_golem", [], registry)
    assert ps.deck == ["stone_golem"], "returned to the BOTTOM of the deck"
    assert not maybe_recycle_destroyed_monster(state, "white", "shadow_wolf", [], registry), \
        "only the first each turn"


def test_grave_crowned_king_shields_necromancers_once_the_graveyard_is_deep(registry):
    from game.mechanics.kings import apply_king_policy_auras

    state = new_state()
    crown(state, "white", "grave_crowned_king")
    unit = put(state, Position(3, 1), "white", "pawn", "bone_collector", registry)
    unit.statuses = [s for s in unit.statuses if not s.startswith("shield:")]
    state.get_player("white").graveyard = ["a", "b", "c", "d", "e"]
    apply_king_policy_auras(state, "white", registry)
    assert statuses_with(unit, "shield:"), "graveyard_threshold_bonus"


def test_arcane_sovereign_rebates_the_first_voluntary_reveal(registry):
    state = new_state()
    crown(state, "white", "arcane_sovereign")
    ps = state.get_player("white")
    ps.deck = ["a_card", "b_card", "c_card"]
    ps.hand = ["ritual_insight"]
    from tests.effect_harness import give_ritual
    from game.core.phases import RevelationState

    give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="ritual_insight", target=None),
        DeterministicRNG(1),
    )
    assert len(ps.hand) == 2, "ritual_insight's own draw PLUS the Sovereign's rebate"
    assert ps.ritual_info_discount_used is True, "once per match"


def test_dragon_high_king_extends_dragon_summon_range_in_territory(registry):
    from game.mechanics.kings import is_in_territory_with_king_bonus

    state = new_state()
    crown(state, "white", "dragon_high_king")
    beyond = Position(3, 3)   # one rank past white's home Territory (ranks 0-2)
    assert not is_in_territory_with_king_bonus(state, beyond, "white", "warrior", registry)
    assert is_in_territory_with_king_bonus(state, beyond, "white", "dragon", registry)


def test_worldforge_colossus_restores_two_builders(registry):
    """worldforge_colossus declares ``max_targets: 2``, guild_foreman 1."""
    state = new_state()
    a = put(state, Position(2, 1), "white", "pawn")
    b = put(state, Position(3, 1), "white", "pawn")
    a.builder_available = False
    b.builder_available = False
    put(state, Position(3, 2), "white", "rook", "worldforge_colossus", registry)
    assert [a.builder_available, b.builder_available] == [True, True]


def test_guild_foreman_restores_only_one_builder(registry):
    state = new_state()
    a = put(state, Position(2, 1), "white", "pawn")
    b = put(state, Position(3, 1), "white", "pawn")
    a.builder_available = False
    b.builder_available = False
    put(state, Position(3, 2), "white", "pawn", "guild_foreman", registry)
    assert [a.builder_available, b.builder_available].count(True) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Non-Ritual siege cards — bane_of_structures / molten_colossus /
# sunder_the_walls. Before these, the only way to threaten a Building
# without a Ritual Monster was obsidian_dragon, siege_order or a
# demolition_charge the enemy had to walk into.
# ─────────────────────────────────────────────────────────────────────────────

def test_bane_of_structures_may_besiege_without_a_ritual(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    bane = put(state, Position(3, 3), "white", "rook", "bane_of_structures", registry)
    assert not registry.get("bane_of_structures").ritual_only, "this is a Main Deck Monster"
    assert can_attack_building(state, bane, b, registry)


def test_bane_of_structures_hits_for_two(registry):
    from game.mechanics.buildings import attack_damage

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    bane = put(state, Position(3, 3), "white", "rook", "bane_of_structures", registry)
    assert attack_damage(bane, b, registry) == 2, "base 1 + the card's amount: 1"
    damage_building(state, b, attack_damage(bane, b, registry), [], registry,
                    attacker_piece_id=bane.piece.id)
    assert b.integrity == 1
    assert b.status == ConstructionStatus.COMPLETE, "a Fortress survives one blow"


def test_bane_of_structures_levels_a_fortress_in_two_blows(registry):
    from game.mechanics.buildings import attack_damage

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    bane = put(state, Position(3, 3), "white", "rook", "bane_of_structures", registry)
    for _ in range(2):
        damage_building(state, b, attack_damage(bane, b, registry), [], registry,
                        attacker_piece_id=bane.piece.id)
    assert b.status == ConstructionStatus.DESTROYED


def test_molten_colossus_flattens_a_fortress_in_one_blow(registry):
    from game.mechanics.buildings import attack_damage

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    colossus = put(state, Position(3, 3), "white", "rook", "molten_colossus", registry)
    assert attack_damage(colossus, b, registry) == 3, "base 1 + the card's amount: 2"
    damage_building(state, b, attack_damage(colossus, b, registry), [], registry,
                    attacker_piece_id=colossus.piece.id)
    assert b.status == ConstructionStatus.DESTROYED


def test_molten_colossus_is_still_blunted_by_a_durability_aura(registry):
    """
    Unlike obsidian_dragon's ``destroy_on_capture`` overwhelming blow, the
    Colossus deals ordinary damage — defenders still get their say.
    """
    from game.mechanics.buildings import attack_damage

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    put(state, Position(3, 6), "black", "rook", "siege_captain", registry)
    colossus = put(state, Position(3, 3), "white", "rook", "molten_colossus", registry)
    damage_building(state, b, attack_damage(colossus, b, registry), [], registry,
                    attacker_piece_id=colossus.piece.id)
    assert b.status == ConstructionStatus.COMPLETE, "the Captain's aura held the wall"


def test_molten_colossus_survives_the_first_capture_attempt(registry):
    state = new_state()
    colossus = put(state, Position(3, 3), "white", "rook", "molten_colossus", registry)
    assert statuses_with(colossus, "shield:") == ["shield:1"]


def test_sunder_the_walls_damages_a_building_with_no_unit_involved(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    state.get_player("white").hand.append("sunder_the_walls")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="sunder_the_walls", target=b.id),
        DeterministicRNG(1),
    )
    assert b.integrity == 1


def test_sunder_the_walls_refuses_your_own_building(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 1), "white")
    state.get_player("white").hand.append("sunder_the_walls")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state, ActivateSpell(player_id="white", card_id="sunder_the_walls", target=b.id),
            DeterministicRNG(1),
        )


def test_sunder_the_walls_is_absorbed_by_castle_keeper(registry):
    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    put(state, Position(3, 6), "black", "rook", "castle_keeper", registry)
    state.get_player("white").hand.append("sunder_the_walls")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="sunder_the_walls", target=b.id),
        DeterministicRNG(1),
    )
    assert b.integrity == 3, "the Keeper ate the whole hostile action"


def test_sunder_the_walls_and_a_besieger_finish_a_fortress_together(registry):
    from game.mechanics.buildings import attack_damage

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    b.max_integrity = b.integrity = 3
    bane = put(state, Position(3, 3), "white", "rook", "bane_of_structures", registry)
    state.get_player("white").hand.append("sunder_the_walls")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="sunder_the_walls", target=b.id),
        DeterministicRNG(1),
    )
    damage_building(state, b, attack_damage(bane, b, registry), [], registry,
                    attacker_piece_id=bane.piece.id)
    assert b.status == ConstructionStatus.DESTROYED


def test_the_new_siege_cards_are_all_main_deck_drawable(registry):
    """None of the three may be a Ritual payoff — that was the whole point."""
    main_deck_monsters = {c.id for c in registry.all_monsters() if not c.ritual_only}
    assert {"bane_of_structures", "molten_colossus"} <= main_deck_monsters
    assert "sunder_the_walls" in {c.id for c in registry.all_spells()}
