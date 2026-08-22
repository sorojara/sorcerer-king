"""
test_effect_scenarios_board_control.py — one scenario per BOARD_CONTROL /
zone effect type.

Covered (data/*.yaml declarations):
    freeze_square            iron_vanguard
    scorch_square            ember_drake
    damage_aura              dragon_herald
    movement_restriction     astral_binder (aura) / labyrinth_rune (debuff)
    suppress_spell_zone      spellbreaker
    immobilize_zone          cursed_ground
    destroy_monster_or_piece pit_trap
    block_line_of_sight      wall_of_mist
    movement_cost_zone       fractured_path
    prohibit_summoning       sanctuary, vessel_lock
    remove_spatial_effects   dispel_field
    temporary_territory      border_beacon
    block_zone               veil_of_stillness
    destroy_trap             shatter_trap
    cancel_move              time_anchor
    ignore_terrain           sky_serpent
"""

from __future__ import annotations

import pytest

from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import Position
from game.core.actions import ActivateSpell, ActivateTrap, MovePiece, SummonMonster
from game.core.events import SquareFrozen, SquareScorched
from game.core.phases import ConstructionStatus, Phase
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError
from game.mechanics.monsters import (
    check_enter_radius_traps,
    is_summoning_prohibited,
)
from game.mechanics.territory import is_in_territory

from tests.effect_harness import (  # noqa: F401
    engine,
    find_at,
    new_state,
    place_building,
    place_trap,
    put,
    registry,
    rng,
    spell_reach,
    square_effects,
    statuses_with,
)


# ─────────────────────────────────────────────────────────────────────────────
# freeze_square / scorch_square
# ─────────────────────────────────────────────────────────────────────────────

def test_iron_vanguard_freezes_its_landing_square(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "knight", "iron_vanguard", registry)
    put(state, Position(4, 5), "black", "pawn")
    events = engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(4, 5)),
        DeterministicRNG(1),
    )
    assert any(isinstance(e, SquareFrozen) for e in events)
    assert any(e.startswith("frozen:") for e in square_effects(state, Position(4, 5)))


def test_ember_drake_scorches_and_the_scorch_kills_the_next_visitor(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "knight", "ember_drake", registry)
    put(state, Position(4, 5), "black", "pawn")
    eng = engine(registry)
    events = eng.execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(4, 5)),
        DeterministicRNG(1),
    )
    assert any(isinstance(e, SquareScorched) for e in events)
    # The drake moves off, a black piece walks in and burns.
    state.board.move_unit(Position(4, 5), Position(0, 5))
    victim = put(state, Position(4, 7), "black", "rook")
    state.get_player("black").chess_move_used = False
    state.phase = Phase.CHESS
    state.active_player = "black"
    eng.execute(state, MovePiece(player_id="black", source=Position(4, 7), target=Position(4, 5)),
                DeterministicRNG(1))
    assert find_at(state, victim) is None, "a scorched square destroys the unit that enters it"


# ─────────────────────────────────────────────────────────────────────────────
# damage_aura
# ─────────────────────────────────────────────────────────────────────────────

def test_dragon_herald_destroys_an_enemy_entering_its_aura(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "black", "rook", "dragon_herald", registry)
    intruder = put(state, Position(2, 7), "white", "rook")
    engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(2, 7), target=Position(2, 3)),
        DeterministicRNG(1),
    )
    assert find_at(state, intruder) is None


def test_damage_aura_spares_the_auras_own_side(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "rook", "dragon_herald", registry)
    friend = put(state, Position(2, 7), "white", "rook")
    engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(2, 7), target=Position(2, 3)),
        DeterministicRNG(1),
    )
    assert find_at(state, friend) == Position(2, 3)


# ─────────────────────────────────────────────────────────────────────────────
# movement_restriction
# ─────────────────────────────────────────────────────────────────────────────

def test_astral_binder_caps_adjacent_enemy_movement(registry):
    state = new_state()
    put(state, Position(3, 3), "black", "queen", "astral_binder", registry)
    victim = put(state, Position(3, 4), "white", "rook")
    moves = get_pseudo_legal_moves(state.board, Position(3, 4), victim, registry=registry, state=state)
    assert all(
        max(abs(m.file - 3), abs(m.rank - 4)) <= 1 for m in moves
    ), "astral_binder should cap an adjacent enemy to 1 square"


def test_labyrinth_rune_limits_the_victim_not_the_trap_owner(registry):
    """
    labyrinth_rune: 'An enemy entering the area has ITS movement limited to
    1 square on its next turn.' The debuff belongs on the intruder.
    """
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "labyrinth_rune", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "rook")
    bystander = put(state, Position(2, 4), "black", "rook")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))

    victim_moves = get_pseudo_legal_moves(state.board, Position(3, 4), victim, registry=registry, state=state)
    assert victim_moves, "the victim should still be able to move — just not far"
    assert all(max(abs(m.file - 3), abs(m.rank - 4)) <= 1 for m in victim_moves), \
        "the intruder is the one whose movement is limited"

    bystander_moves = get_pseudo_legal_moves(state.board, Position(2, 4), bystander, registry=registry, state=state)
    assert any(max(abs(m.file - 2), abs(m.rank - 4)) > 1 for m in bystander_moves), \
        "the Trap owner's own pieces must not be caught by their own rune"


def test_labyrinth_rune_debuff_expires_on_the_victims_own_turn(registry):
    from game.core.actions import EndTurn

    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "labyrinth_rune", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "rook")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(victim, "movement_limit:")
    engine(registry).execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert statuses_with(victim, "movement_limit:") == []


# ─────────────────────────────────────────────────────────────────────────────
# immobilize_zone / block_zone / movement_cost_zone / block_line_of_sight
# ─────────────────────────────────────────────────────────────────────────────

def test_cursed_ground_immobilizes_a_piece_that_ends_its_move_inside(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    eng = engine(registry)
    state.get_player("white").hand.append("cursed_ground")
    eng.execute(state, ActivateSpell(player_id="white", card_id="cursed_ground", target=(3, 3)),
                DeterministicRNG(1))
    assert any(e.startswith("cursed:") for e in square_effects(state, Position(3, 3)))

    state.phase = Phase.CHESS
    walker = put(state, Position(0, 3), "white", "rook")
    eng.execute(state, MovePiece(player_id="white", source=Position(0, 3), target=Position(3, 3)),
                DeterministicRNG(1))
    assert statuses_with(walker, "immobilized:"), "cursed_ground catches BOTH sides"


def test_veil_of_stillness_blocks_the_whole_file(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    state.get_player("white").hand.append("veil_of_stillness")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="veil_of_stillness", target=(3, 3)),
        DeterministicRNG(1),
    )
    tagged = [r for r in range(8) if any(e.startswith("blocked:") for e in square_effects(state, Position(3, r)))]
    assert tagged == list(range(8)), "shape 'column' should cover the whole file"

    mover = put(state, Position(2, 5), "white", "rook")
    moves = get_pseudo_legal_moves(state.board, Position(2, 5), mover, registry=registry, state=state)
    assert Position(3, 5) not in moves


def test_fractured_path_caps_a_unit_starting_inside_it(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    state.get_player("white").hand.append("fractured_path")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="fractured_path", target=(3, 3)),
        DeterministicRNG(1),
    )
    mover = put(state, Position(3, 3), "white", "rook")
    moves = get_pseudo_legal_moves(state.board, Position(3, 3), mover, registry=registry, state=state)
    assert moves and all(max(abs(m.file - 3), abs(m.rank - 3)) <= 1 for m in moves)


def test_wall_of_mist_stops_a_rook_but_not_a_knight(registry):
    state = new_state()
    spell_reach(state, "white", (2, 4))
    state.get_player("white").hand.append("wall_of_mist")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="wall_of_mist", target=(2, 4)),
        DeterministicRNG(1),
    )
    assert any(e.startswith("walled:") for e in square_effects(state, Position(2, 4)))

    rook = put(state, Position(2, 2), "white", "rook")
    rook_moves = get_pseudo_legal_moves(state.board, Position(2, 2), rook, registry=registry, state=state)
    assert Position(2, 4) not in rook_moves
    assert Position(2, 6) not in rook_moves, "a sliding piece cannot cross the wall either"

    knight = put(state, Position(1, 2), "white", "knight")
    knight_moves = get_pseudo_legal_moves(state.board, Position(1, 2), knight, registry=registry, state=state)
    assert Position(2, 4) in knight_moves, "'Knights and effects that leap may cross it'"


# ─────────────────────────────────────────────────────────────────────────────
# suppress_spell_zone
# ─────────────────────────────────────────────────────────────────────────────

def test_spellbreaker_neutralises_a_blocked_zone_in_range(registry):
    state = new_state()
    state.board.get_square(Position(3, 3)).add_effect("blocked:2:black:veil_of_stillness")
    put(state, Position(3, 2), "white", "knight", "spellbreaker", registry)
    mover = put(state, Position(2, 3), "white", "rook")
    moves = get_pseudo_legal_moves(state.board, Position(2, 3), mover, registry=registry, state=state)
    assert Position(3, 3) in moves, "spellbreaker should suppress the adjacent blocked square"


# ─────────────────────────────────────────────────────────────────────────────
# destroy_monster_or_piece
# ─────────────────────────────────────────────────────────────────────────────

def test_pit_trap_destroys_the_monster_and_spares_the_vessel(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "pit_trap", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "bishop", "dark_magician", registry)
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert victim.monster_id is None
    assert find_at(state, victim) == Position(3, 4), "the Vessel survives"
    assert "dark_magician" in state.get_player("white").graveyard


def test_pit_trap_destroys_a_plain_piece_outright(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "pit_trap", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "bishop")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert find_at(state, victim) is None


def test_pit_trap_never_touches_a_king(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "pit_trap", Position(4, 1), "black")
    king = state.board.get_unit(Position(4, 0))
    check_enter_radius_traps(state, king, Position(4, 0), [], registry, rng=DeterministicRNG(7))
    assert find_at(state, king) == Position(4, 0)


# ─────────────────────────────────────────────────────────────────────────────
# prohibit_summoning
# ─────────────────────────────────────────────────────────────────────────────

def test_sanctuary_bars_both_sides_from_summoning_inside(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    state.get_player("white").hand.append("sanctuary")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="sanctuary", target=(3, 3)),
        DeterministicRNG(1),
    )
    assert is_summoning_prohibited(state.board, Position(3, 3), "white")
    assert is_summoning_prohibited(state.board, Position(3, 3), "black")


def test_vessel_lock_bars_only_the_opponent(registry):
    state = new_state()
    trap = place_trap(state, registry, "vessel_lock", Position(3, 3), "white")
    engine(registry).execute(
        state, ActivateTrap(player_id="white", trap_instance_id=trap.id),
        DeterministicRNG(1),
    )
    assert is_summoning_prohibited(state.board, Position(3, 3), "black")
    assert not is_summoning_prohibited(state.board, Position(3, 3), "white")


# ─────────────────────────────────────────────────────────────────────────────
# remove_spatial_effects
# ─────────────────────────────────────────────────────────────────────────────

def test_dispel_field_strips_spell_zones_but_leaves_a_monsters_freeze(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    state.board.get_square(Position(4, 4)).add_effect("blocked:2:black:veil_of_stillness")
    state.board.get_square(Position(4, 4)).add_effect("frozen:2:black:iron_vanguard")
    state.get_player("white").hand.append("dispel_field")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="dispel_field", target=(3, 3)),
        DeterministicRNG(1),
    )
    remaining = square_effects(state, Position(4, 4))
    assert not any(e.startswith("blocked:") for e in remaining)
    assert any(e.startswith("frozen:") for e in remaining), "a Monster-caused zone is not a Spell zone"


# ─────────────────────────────────────────────────────────────────────────────
# temporary_territory
# ─────────────────────────────────────────────────────────────────────────────

def test_border_beacon_claims_its_radius_as_territory(registry):
    state = new_state()
    trap = place_trap(state, registry, "border_beacon", Position(3, 3), "white")
    assert not is_in_territory(state, Position(3, 3), "white", registry)
    engine(registry).execute(
        state, ActivateTrap(player_id="white", trap_instance_id=trap.id),
        DeterministicRNG(1),
    )
    assert is_in_territory(state, Position(3, 3), "white", registry)
    assert is_in_territory(state, Position(4, 4), "white", registry), "the whole 3x3 radius"


# ─────────────────────────────────────────────────────────────────────────────
# destroy_trap
# ─────────────────────────────────────────────────────────────────────────────

def test_shatter_trap_removes_an_enemy_trap(registry):
    state = new_state()
    trap = place_trap(state, registry, "pit_trap", Position(3, 3), "black")
    state.get_player("white").hand.append("shatter_trap")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="shatter_trap", target=trap.id),
        DeterministicRNG(1),
    )
    assert trap not in state.traps


def test_shatter_trap_refuses_your_own_trap(registry):
    state = new_state()
    trap = place_trap(state, registry, "pit_trap", Position(3, 3), "white")
    state.get_player("white").hand.append("shatter_trap")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state, ActivateSpell(player_id="white", card_id="shatter_trap", target=trap.id),
            DeterministicRNG(1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# cancel_move
# ─────────────────────────────────────────────────────────────────────────────

def test_time_anchor_undoes_the_opponents_last_move(registry):
    state = new_state(phase=Phase.CHESS, active="black")
    trap = place_trap(state, registry, "time_anchor", Position(0, 0), "white")
    mover = put(state, Position(3, 5), "black", "rook")
    eng = engine(registry)
    eng.execute(state, MovePiece(player_id="black", source=Position(3, 5), target=Position(3, 2)),
                DeterministicRNG(1))
    assert state.board.get_unit(Position(3, 2)) is not None
    state.active_player = "white"
    state.phase = Phase.PREPARATION
    eng.execute(state, ActivateTrap(player_id="white", trap_instance_id=trap.id), DeterministicRNG(1))
    assert state.board.get_unit(Position(3, 2)) is None
    assert state.board.get_unit(Position(3, 5)) is not None


# ─────────────────────────────────────────────────────────────────────────────
# ignore_terrain
# ─────────────────────────────────────────────────────────────────────────────

def test_sky_serpent_slides_through_a_building(registry):
    state = new_state()
    place_building(state, "fortress", Position(3, 4), "black", ConstructionStatus.COMPLETE)
    serpent = put(state, Position(3, 3), "white", "queen", "sky_serpent", registry)
    moves = get_pseudo_legal_moves(state.board, Position(3, 3), serpent, registry=registry, state=state)
    assert Position(3, 5) in moves, "sky_serpent phases past the structure"
    assert Position(3, 4) not in moves, "but it still cannot stop on top of it"


def test_a_plain_rook_is_stopped_by_a_building(registry):
    state = new_state()
    place_building(state, "fortress", Position(3, 4), "black", ConstructionStatus.COMPLETE)
    rook = put(state, Position(3, 3), "white", "rook")
    moves = get_pseudo_legal_moves(state.board, Position(3, 3), rook, registry=registry, state=state)
    assert Position(3, 5) not in moves
