"""
test_effect_scenarios_movement.py — one scenario per MOVEMENT effect type.

Covered (data/*.yaml declarations):
    alter_movement          wyrm_knight, shadow_wolf
    burrow                  tunnel_mole
    pass_through_units      phantom_lancer
    reposition_unit         blade_dancer, arcane_reposition, knightfall,
                            evacuation_order, displacement_rune
    push_unit               storm_dragon, repulsion_field
    pull_unit               magnetic_reversal, gravity_well
    move_unit               forced_march
    swap_units              exchange_of_fates
    immobilize_piece        pit_trap, ward_of_binding
"""

from __future__ import annotations

import pytest

from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import Position
from game.core.actions import ActivateSpell, MovePiece
from game.core.phases import Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError
from game.mechanics.monsters import check_enter_radius_traps

from tests.effect_harness import (  # noqa: F401  (fixtures)
    engine,
    find_at,
    new_state,
    place_trap,
    put,
    registry,
    rng,
    spell_reach,
    statuses_with,
)


# ─────────────────────────────────────────────────────────────────────────────
# alter_movement
# ─────────────────────────────────────────────────────────────────────────────

def test_alter_movement_add_leap_grants_cardinal_leaps(registry):
    """wyrm_knight: 'adds a 2-square leap in the four cardinal directions'."""
    state = new_state()
    knight = put(state, Position(3, 3), "white", "knight", "wyrm_knight", registry)
    moves = get_pseudo_legal_moves(state.board, Position(3, 3), knight, registry=registry, state=state)
    for dest in (Position(5, 3), Position(1, 3), Position(3, 5), Position(3, 1)):
        assert dest in moves, f"wyrm_knight should reach {dest} via its 2-square cardinal leap"


def test_alter_movement_stealth_flags_the_unit(registry):
    """shadow_wolf: stealth is armed on summon."""
    state = new_state()
    wolf = put(state, Position(3, 3), "white", "knight", "shadow_wolf", registry)
    assert "stealth" in wolf.statuses


# ─────────────────────────────────────────────────────────────────────────────
# burrow
# ─────────────────────────────────────────────────────────────────────────────

def test_burrow_does_not_fire_on_summon(registry):
    """
    tunnel_mole's burrow is an ACTIVATED ability ('once every 2 turns') —
    summoning it must not immediately demand a relocation choice.
    """
    state = new_state()
    put(state, Position(3, 3), "white", "pawn", "tunnel_mole", registry)
    assert state.pending_decision is None, (
        "burrow opened a REPOSITION decision at summon time — it is an "
        "activated ability, not an on-summon one"
    )


def test_burrow_activated_offers_squares_through_occupied_ones(registry):
    from game.core.actions import ActivateMonsterAbility

    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "pawn", "tunnel_mole", registry)
    put(state, Position(3, 4), "black", "pawn")     # a body to tunnel under
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3), ability_id="burrow"),
        DeterministicRNG(1),
    )
    assert state.pending_decision is not None
    assert (3, 5) in state.pending_decision.options, "burrow should reach past an occupied square"
    assert (3, 4) not in state.pending_decision.options, "burrow cannot END on an occupied square"


# ─────────────────────────────────────────────────────────────────────────────
# pass_through_units
# ─────────────────────────────────────────────────────────────────────────────

def test_pass_through_units_lets_a_pawn_double_push_over_a_body(registry):
    state = new_state()
    lancer = put(state, Position(3, 1), "white", "pawn", "phantom_lancer", registry)
    put(state, Position(3, 2), "black", "pawn")     # normally blocks the double push
    moves = get_pseudo_legal_moves(state.board, Position(3, 1), lancer, registry=registry, state=state)
    assert Position(3, 3) in moves


# ─────────────────────────────────────────────────────────────────────────────
# reposition_unit
# ─────────────────────────────────────────────────────────────────────────────

def test_reposition_unit_after_capture_opens_a_decision(registry):
    """blade_dancer: 'after capturing she may immediately reposition up to 2'."""
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "knight", "blade_dancer", registry)
    put(state, Position(4, 5), "black", "pawn")
    engine(registry).execute(
        state,
        MovePiece(player_id="white", source=Position(3, 3), target=Position(4, 5)),
        DeterministicRNG(1),
    )
    assert state.pending_decision is not None
    assert state.pending_decision.context["piece_id"].startswith("white-knight")


def test_arcane_reposition_teleports_within_three(registry):
    state = new_state()
    unit = put(state, Position(3, 3), "white", "bishop")
    state.get_player("white").hand.append("arcane_reposition")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="arcane_reposition",
                      target={"position": (3, 3), "destination": (5, 4)}),
        DeterministicRNG(1),
    )
    assert find_at(state, unit) == Position(5, 4)


def test_arcane_reposition_rejects_a_destination_out_of_range(registry):
    state = new_state()
    put(state, Position(3, 3), "white", "bishop")
    state.get_player("white").hand.append("arcane_reposition")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="arcane_reposition",
                          target={"position": (3, 3), "destination": (7, 7)}),
            DeterministicRNG(1),
        )


def test_knightfall_only_reaches_knight_destinations(registry):
    """
    knightfall: 'Move one allied Knight ... to another legal KNIGHT
    destination'. An arbitrary empty square is not one.
    """
    state = new_state()
    put(state, Position(3, 3), "white", "knight")
    state.get_player("white").hand.append("knightfall")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="knightfall",
                          target={"position": (3, 3), "destination": (7, 7)}),
            DeterministicRNG(1),
        )


def test_knightfall_accepts_a_real_knight_hop(registry):
    state = new_state()
    knight = put(state, Position(3, 3), "white", "knight")
    state.get_player("white").hand.append("knightfall")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="knightfall",
                      target={"position": (3, 3), "destination": (4, 5)}),
        DeterministicRNG(1),
    )
    assert find_at(state, knight) == Position(4, 5)
    assert state.get_player("white").chess_move_used is False, "knightfall does not consume the chess move"


def test_knightfall_rejects_a_non_knight_piece(registry):
    state = new_state()
    put(state, Position(3, 3), "white", "bishop")
    state.get_player("white").hand.append("knightfall")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="knightfall",
                          target={"position": (3, 3), "destination": (4, 5)}),
            DeterministicRNG(1),
        )


def test_evacuation_order_moves_the_king_one_square(registry):
    """evacuation_order explicitly targets 'your King' — it must not be
    rejected by reposition_unit's generic no-King rule."""
    state = new_state()
    king = state.board.get_unit(Position(4, 0))
    state.get_player("white").hand.append("evacuation_order")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="evacuation_order",
                      target={"position": (4, 0), "destination": (5, 0)}),
        DeterministicRNG(1),
    )
    assert find_at(state, king) == Position(5, 0)
    assert state.get_player("white").chess_move_used is False


def test_evacuation_order_is_illegal_while_in_check(registry):
    state = new_state()
    put(state, Position(4, 5), "black", "rook")   # checks the white King down the e-file
    state.get_player("white").hand.append("evacuation_order")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="evacuation_order",
                          target={"position": (4, 0), "destination": (5, 0)}),
            DeterministicRNG(1),
        )


def test_displacement_rune_displaces_the_triggering_piece(registry):
    """
    displacement_rune: 'An enemy entering the area is displaced to a random
    adjacent empty square.'
    """
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "displacement_rune", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "rook")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert find_at(state, victim) != Position(3, 4), "displacement_rune left the victim in place"


# ─────────────────────────────────────────────────────────────────────────────
# push_unit
# ─────────────────────────────────────────────────────────────────────────────

def test_storm_dragon_pushes_instead_of_capturing(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "rook", "storm_dragon", registry)
    victim = put(state, Position(3, 4), "black", "pawn")
    engine(registry).execute(
        state,
        MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert find_at(state, victim) == Position(3, 5), "the defender should have been shoved, not captured"


def test_repulsion_field_pushes_the_triggering_piece_away(registry):
    """
    repulsion_field: 'push it 1 square directly away from the Trap if that
    square is available.'
    """
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "repulsion_field", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "rook")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert find_at(state, victim) == Position(3, 5), "repulsion_field did not push the intruder away"


# ─────────────────────────────────────────────────────────────────────────────
# pull_unit
# ─────────────────────────────────────────────────────────────────────────────

def test_magnetic_reversal_pulls_an_enemy_toward_the_casters_king(registry):
    state = new_state()
    victim = put(state, Position(4, 4), "black", "rook")
    spell_reach(state, "white", (4, 4))
    state.get_player("white").hand.append("magnetic_reversal")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="magnetic_reversal",
                      target={"position": (4, 4)}),
        DeterministicRNG(1),
    )
    assert find_at(state, victim) == Position(4, 3), "should be dragged one rank toward the white King on e1"


def test_gravity_well_pulls_the_triggering_piece_toward_the_trap(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "gravity_well", Position(3, 3), "black")
    victim = put(state, Position(3, 5), "white", "rook")
    check_enter_radius_traps(state, victim, Position(3, 5), [], registry, rng=DeterministicRNG(7))
    assert find_at(state, victim) == Position(3, 4)


# ─────────────────────────────────────────────────────────────────────────────
# move_unit
# ─────────────────────────────────────────────────────────────────────────────

def test_forced_march_advances_a_pawn_without_the_chess_move(registry):
    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    state.get_player("white").hand.append("forced_march")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="forced_march", target={"position": (3, 1)}),
        DeterministicRNG(1),
    )
    assert find_at(state, pawn) == Position(3, 2)
    assert state.get_player("white").chess_move_used is False


def test_forced_march_cannot_promote_a_pawn(registry):
    """forced_march: 'Cannot promote a Pawn through this effect.'"""
    state = new_state()
    pawn = put(state, Position(3, 6), "white", "pawn")
    state.get_player("white").hand.append("forced_march")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="forced_march", target={"position": (3, 6)}),
            DeterministicRNG(1),
        )
    assert find_at(state, pawn) == Position(3, 6)


# ─────────────────────────────────────────────────────────────────────────────
# swap_units
# ─────────────────────────────────────────────────────────────────────────────

def test_exchange_of_fates_swaps_two_allied_units(registry):
    state = new_state()
    a = put(state, Position(3, 3), "white", "rook")
    b = put(state, Position(5, 4), "white", "bishop")
    state.get_player("white").hand.append("exchange_of_fates")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="exchange_of_fates",
                      target={"position": (3, 3), "destination": (5, 4)}),
        DeterministicRNG(1),
    )
    assert find_at(state, a) == Position(5, 4)
    assert find_at(state, b) == Position(3, 3)


def test_exchange_of_fates_rejects_a_king(registry):
    state = new_state()
    put(state, Position(4, 1), "white", "rook")
    state.get_player("white").hand.append("exchange_of_fates")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state,
            ActivateSpell(player_id="white", card_id="exchange_of_fates",
                          target={"position": (4, 1), "destination": (4, 0)}),
            DeterministicRNG(1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# immobilize_piece
# ─────────────────────────────────────────────────────────────────────────────

def test_ward_of_binding_immobilizes_the_intruder(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "ward_of_binding", Position(3, 3), "black")
    victim = put(state, Position(3, 4), "white", "rook")
    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(victim, "immobilized:") == ["immobilized:2"]
    assert get_pseudo_legal_moves(state.board, Position(3, 4), victim, registry=registry, state=state) == []


# ─────────────────────────────────────────────────────────────────────────────
# Stale ctx.position — two enter_radius Traps covering the same landing square
#
# check_enter_radius_traps hands EVERY matching Trap the same pre-displacement
# ``target_pos``. Once the first Trap displaces the intruder, that snapshot is
# stale, and a second displacement Trap used to try to move a piece off an
# empty square — BoardState.move_unit raised "No unit at source square".
# ─────────────────────────────────────────────────────────────────────────────

def test_push_trap_after_an_earlier_trap_already_displaced_the_victim(registry):
    """
    gravity_well fires first and drags the intruder off its landing square;
    repulsion_field then fires with the now-stale position and must push the
    victim from where it ACTUALLY stands instead of crashing.
    """
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "gravity_well", Position(3, 3), "black")
    place_trap(state, registry, "repulsion_field", Position(2, 5), "black")
    victim = put(state, Position(3, 5), "white", "rook")

    check_enter_radius_traps(state, victim, Position(3, 5), [], registry, rng=DeterministicRNG(7))

    # gravity_well: d6 → d5 (one step toward d4). repulsion_field at c6 then
    # pushes it away from c6 along the d5→e4 diagonal.
    assert find_at(state, victim) == Position(4, 3)
    assert state.board.get_unit(Position(3, 5)) is None, "the landing square must not be re-occupied"


def test_pull_trap_after_an_earlier_trap_already_displaced_the_victim(registry):
    """The mirror case — pull_unit has the same stale-position hole as push_unit."""
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "repulsion_field", Position(3, 3), "black")
    place_trap(state, registry, "gravity_well", Position(2, 4), "black")
    victim = put(state, Position(3, 4), "white", "rook")

    check_enter_radius_traps(state, victim, Position(3, 4), [], registry, rng=DeterministicRNG(7))

    # repulsion_field: d5 → d6 (away from d4). gravity_well at c5 then pulls
    # it from d6 back to c5.
    assert find_at(state, victim) == Position(2, 4)


def test_push_trap_is_a_no_op_when_the_victim_left_the_board(registry):
    """
    If an earlier effect in the chain removed the unit entirely, the push
    handler has nothing to move and must not touch the board.
    """
    from game.mechanics.effects.movement import _push_unit
    from game.mechanics.effects.registry import EffectContext

    state = new_state(phase=Phase.CHESS)
    victim = put(state, Position(3, 4), "white", "rook")
    card = registry.get("repulsion_field")
    state.board.remove_unit(Position(3, 4))   # captured earlier in the chain

    _push_unit(EffectContext(
        state=state,
        unit=victim,
        position=Position(3, 4),
        card=card,
        effect=card.effects[0],
        events=[],
        extra={"trap_position": (3, 3)},
    ))

    assert state.board.find_unit_by_piece_id(victim.piece.id) is None
