"""
test_effect_scenarios_defense.py — one scenario per DEFENSE effect type.

Covered (data/*.yaml declarations):
    capture_protection              arcane_sentinel, stone_golem, ancient_tortoise, ...
    capture_protection_from_counter bone_collector
    capture_protection_aura         fortress (Building)
    trap_immunity                   ancient_tortoise
    intercept                       royal_guard
    retaliate                       thorn_boar
    weakened_target_bonus           executioner
    capture_vulnerability           counter_strike
    mark_unit                       vengeance_mark
    cancel_capture                  guardian_sigils
    suppress_monster_effects        monster_seal, nullification_glyph
"""

from __future__ import annotations

import pytest

from game.chess.pieces import Position
from game.core.actions import ActivateSpell, MovePiece
from game.core.events import (
    CaptureBlocked,
    MonsterDestroyed,
    PieceCaptured,
    Retaliated,
)
from game.core.phases import Phase
from game.core.rng import DeterministicRNG
from game.mechanics.monsters import (
    check_capture_traps,
    check_enter_radius_traps,
    is_effects_suppressed,
)

from tests.effect_harness import (  # noqa: F401
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
# capture_protection
# ─────────────────────────────────────────────────────────────────────────────

def test_capture_protection_absorbs_the_first_capture(registry):
    state = new_state(phase=Phase.CHESS)
    defender = put(state, Position(3, 4), "black", "knight", "arcane_sentinel", registry)
    put(state, Position(3, 3), "white", "rook")
    assert statuses_with(defender, "shield:") == ["shield:1"]
    events = engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert any(isinstance(e, CaptureBlocked) for e in events)
    assert find_at(state, defender) == Position(3, 4)


def test_capture_protection_uses_two_for_stone_golem(registry):
    state = new_state()
    golem = put(state, Position(3, 4), "black", "rook", "stone_golem", registry)
    assert statuses_with(golem, "shield:") == ["shield:2"]


def test_capture_protection_from_counter_grants_a_shield_at_the_threshold(registry):
    """
    bone_collector: 'Gains defensive value as allied units are destroyed'
    — required_counters 2, uses_per_counter 1.
    """
    state = new_state(phase=Phase.CHESS)
    collector = put(state, Position(0, 4), "black", "pawn", "bone_collector", registry)
    assert statuses_with(collector, "graveyard_counter:") == ["graveyard_counter:0"]
    eng = engine(registry)
    # Two allied Monsters die → the counter reaches 2 → one shield charge.
    for rank, file_ in ((4, 6), (5, 6)):
        put(state, Position(file_, rank), "black", "pawn", "apprentice_mage", registry)
        put(state, Position(file_, rank - 1), "white", "rook")
        state.get_player("white").chess_move_used = False
        state.phase = Phase.CHESS
        eng.execute(
            state,
            MovePiece(player_id="white", source=Position(file_, rank - 1), target=Position(file_, rank)),
            DeterministicRNG(1),
        )
    assert statuses_with(collector, "graveyard_counter:") == ["graveyard_counter:2"]
    assert statuses_with(collector, "shield:") == ["shield:1"]


# ─────────────────────────────────────────────────────────────────────────────
# trap_immunity
# ─────────────────────────────────────────────────────────────────────────────

def test_trap_immunity_absorbs_the_first_trap(registry):
    """ancient_tortoise: 'Ignores the first Trap effect that would affect it'."""
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "ward_of_binding", Position(3, 3), "black")
    tortoise = put(state, Position(3, 4), "white", "rook", "ancient_tortoise", registry)
    assert statuses_with(tortoise, "trap_immune:") == ["trap_immune:1"]
    check_enter_radius_traps(state, tortoise, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(tortoise, "immobilized:") == [], "the Trap should have been ignored"
    assert statuses_with(tortoise, "trap_immune:") == ["trap_immune:0"], "the charge should be spent"


# ─────────────────────────────────────────────────────────────────────────────
# intercept
# ─────────────────────────────────────────────────────────────────────────────

def test_intercept_is_armed_on_summon(registry):
    state = new_state()
    guard = put(state, Position(3, 0), "white", "pawn", "royal_guard", registry)
    assert statuses_with(guard, "intercept:") == ["intercept:1"]


def test_intercept_absorbs_an_attack_on_the_adjacent_king(registry):
    """
    royal_guard: 'can intercept one attack targeting an adjacent King'.
    The Guard dies in the King's place — no Final Duel is triggered.
    """
    state = new_state(phase=Phase.CHESS, active="black")
    guard = put(state, Position(3, 0), "white", "pawn", "royal_guard", registry)
    put(state, Position(4, 3), "black", "rook")
    events = engine(registry).execute(
        state, MovePiece(player_id="black", source=Position(4, 3), target=Position(4, 0)),
        DeterministicRNG(1),
    )
    assert state.duel is None, "the King's escort should have absorbed the assault"
    assert state.board.get_unit(Position(4, 0)) is not None, "the King must still be standing"
    assert find_at(state, guard) is None, "the interceptor pays with its life"
    assert any(isinstance(e, MonsterDestroyed) for e in events)


def test_intercept_only_works_once(registry):
    state = new_state(phase=Phase.CHESS, active="black")
    put(state, Position(3, 0), "white", "pawn", "royal_guard", registry)
    put(state, Position(4, 3), "black", "rook")
    eng = engine(registry)
    eng.execute(state, MovePiece(player_id="black", source=Position(4, 3), target=Position(4, 0)),
                DeterministicRNG(1))
    state.get_player("black").chess_move_used = False
    state.phase = Phase.CHESS
    eng.execute(state, MovePiece(player_id="black", source=Position(4, 3), target=Position(4, 0)),
                DeterministicRNG(1))
    assert state.duel is not None, "the second assault has no escort left to absorb it"


# ─────────────────────────────────────────────────────────────────────────────
# retaliate
# ─────────────────────────────────────────────────────────────────────────────

def test_retaliate_destroys_the_attacker(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 4), "black", "pawn", "thorn_boar", registry)
    attacker = put(state, Position(3, 3), "white", "rook")
    events = engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert any(isinstance(e, Retaliated) for e in events)
    assert find_at(state, attacker) is None


def test_weakened_target_bonus_bypasses_retaliate_on_an_unshielded_target(registry):
    """executioner: 'Excels against units whose defensive effects have
    already been spent' — only then does it dodge the thorns."""
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 4), "black", "pawn", "thorn_boar", registry)
    attacker = put(state, Position(3, 3), "white", "rook", "executioner", registry)
    engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert find_at(state, attacker) == Position(3, 4), "executioner should have survived"


# ─────────────────────────────────────────────────────────────────────────────
# capture_vulnerability / mark_unit
# ─────────────────────────────────────────────────────────────────────────────

def test_counter_strike_exposes_the_capturing_piece(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "counter_strike", Position(3, 4), "black")
    attacker = put(state, Position(3, 4), "white", "rook", "arcane_sentinel", registry)
    check_capture_traps(state, attacker, "black", Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(attacker, "exposed:") == ["exposed:1"]


def test_exposed_bypasses_a_shield(registry):
    state = new_state(phase=Phase.CHESS)
    defender = put(state, Position(3, 4), "black", "knight", "arcane_sentinel", registry)
    defender.add_status("exposed:1")
    put(state, Position(3, 3), "white", "rook")
    engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert find_at(state, defender) is None, "an Exposed unit's shield must not save it"


def test_vengeance_mark_marks_the_capturing_piece(registry):
    state = new_state(phase=Phase.CHESS)
    place_trap(state, registry, "vengeance_mark", Position(3, 4), "black")
    attacker = put(state, Position(3, 4), "white", "rook")
    check_capture_traps(state, attacker, "black", Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert statuses_with(attacker, "exposed:") == ["exposed:1"]


# ─────────────────────────────────────────────────────────────────────────────
# cancel_capture
# ─────────────────────────────────────────────────────────────────────────────

def test_guardian_sigils_cancels_the_capture_and_is_consumed(registry):
    state = new_state(phase=Phase.CHESS)
    trap = place_trap(state, registry, "guardian_sigils", Position(3, 4), "black")
    defender = put(state, Position(3, 4), "black", "pawn")
    attacker = put(state, Position(3, 3), "white", "rook")
    events = engine(registry).execute(
        state, MovePiece(player_id="white", source=Position(3, 3), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    assert any(isinstance(e, CaptureBlocked) for e in events)
    assert find_at(state, defender) == Position(3, 4)
    assert find_at(state, attacker) == Position(3, 3)
    assert trap not in state.traps, "guardian_sigils consumes itself"


# ─────────────────────────────────────────────────────────────────────────────
# suppress_monster_effects
# ─────────────────────────────────────────────────────────────────────────────

def test_monster_seal_suppresses_an_enemy_monsters_effects(registry):
    state = new_state()
    victim = put(state, Position(3, 4), "black", "rook", "dragon_herald", registry)
    spell_reach(state, "white", (3, 4))
    state.get_player("white").hand.append("monster_seal")
    engine(registry).execute(
        state,
        ActivateSpell(player_id="white", card_id="monster_seal", target={"position": (3, 4)}),
        DeterministicRNG(1),
    )
    assert is_effects_suppressed(victim)


def test_nullification_glyph_suppresses_a_monster_summoned_in_range(registry):
    from game.mechanics.monsters import check_summon_traps

    state = new_state()
    place_trap(state, registry, "nullification_glyph", Position(3, 3), "black")
    summoned = put(state, Position(3, 4), "white", "rook", "dragon_herald", registry)
    check_summon_traps(state, summoned, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert is_effects_suppressed(summoned)
