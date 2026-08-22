"""
test_effect_scenarios_cards_info.py — one scenario per CARDS, INFORMATION
and RITUAL effect type.

Covered (data/*.yaml declarations):
    draw_card                    apprentice_mage, alarm_beacon, transformation_alarm
    inspect_top_deck             arcane_archivist, oracle_of_the_last_star
    reorder_top_deck             (resolved by the decision inspect_top_deck opens)
    graveyard_to_deck            grave_scholar
    graveyard_inspect            grave_scholar
    discard_card                 blood_price
    reveal_hidden_info           raven_scout
    reveal_enemy_hand_card       alarm_beacon
    reveal_own_hand_card         forbidden_knowledge
    obscure_influence            veil_conjurer
    ritual_progress_boost        ritual_acolyte
    ritual_pattern_substitute    circle_keeper
    ritual_requirement_reduction forbidden_priest, hasten_the_ritual
    ritual_reveal_tradeoff       omen_reader, oracle_of_the_last_star
    reveal_ritual                ritual_insight, omen_bell
    reveal_enemy_ritual          forbidden_knowledge
    ritual_bluff                 false_prophecy
    interrupt_ritual             profane_interruption
    sacrifice_bonus              blood_seer
    ritual_activation_range      herald_of_the_gate
"""

from __future__ import annotations

import pytest

from game.chess.pieces import Position
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateSpell,
    EndTurn,
    MovePiece,
    ReorderTopDeck,
)
from game.core.events import (
    CardDiscarded,
    CardDrawn,
    DeckInspected,
    EnemyCardRevealed,
    GraveyardInspected,
)
from game.core.phases import DecisionType, Phase, RevelationState
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError
from game.mechanics.monsters import check_capture_traps, check_enter_radius_traps

from tests.effect_harness import (  # noqa: F401
    engine,
    find_at,
    give_ritual,
    new_state,
    place_trap,
    put,
    registry,
    rng,
    spell_reach,
    statuses_with,
)


# ─────────────────────────────────────────────────────────────────────────────
# draw_card
# ─────────────────────────────────────────────────────────────────────────────

def test_apprentice_mage_draws_on_summon(registry):
    state = new_state()
    state.get_player("white").deck = ["stone_golem", "shadow_wolf"]
    events: list = []
    put(state, Position(3, 1), "white", "pawn", "apprentice_mage", registry, events=events)
    assert [e.card_id for e in events if isinstance(e, CardDrawn)] == ["stone_golem"]
    assert state.get_player("white").hand == ["stone_golem"]


def test_alarm_beacon_draws_for_the_trap_owner_not_the_intruder(registry):
    state = new_state(phase=Phase.CHESS)
    state.get_player("black").deck = ["stone_golem"]
    state.get_player("white").deck = ["shadow_wolf"]
    place_trap(state, registry, "alarm_beacon", Position(3, 3), "black")
    intruder = put(state, Position(3, 4), "white", "rook")
    check_enter_radius_traps(state, intruder, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert state.get_player("black").hand == ["stone_golem"]
    assert state.get_player("white").hand == []


def test_transformation_alarm_draws_when_an_enemy_monster_is_summoned_in_range(registry):
    from game.mechanics.monsters import check_summon_traps

    state = new_state()
    state.get_player("black").deck = ["stone_golem"]
    place_trap(state, registry, "transformation_alarm", Position(3, 3), "black")
    summoned = put(state, Position(3, 4), "white", "rook", "stone_golem", registry)
    check_summon_traps(state, summoned, Position(3, 4), [], registry, rng=DeterministicRNG(7))
    assert state.get_player("black").hand == ["stone_golem"]


# ─────────────────────────────────────────────────────────────────────────────
# inspect_top_deck / reorder_top_deck
# ─────────────────────────────────────────────────────────────────────────────

def test_arcane_archivist_inspects_and_opens_a_reorder_decision(registry):
    state = new_state()
    state.get_player("white").deck = ["a_card", "b_card", "c_card", "d_card"]
    events: list = []
    put(state, Position(3, 1), "white", "pawn", "arcane_archivist", registry, events=events)
    inspected = [e for e in events if isinstance(e, DeckInspected)]
    assert inspected and list(inspected[0].card_ids) == ["a_card", "b_card", "c_card"]
    assert state.pending_decision is not None
    assert state.pending_decision.decision_type == DecisionType.REORDER_DECK


def test_reorder_top_deck_applies_the_chosen_order(registry):
    state = new_state()
    state.get_player("white").deck = ["a_card", "b_card", "c_card", "d_card"]
    put(state, Position(3, 1), "white", "pawn", "arcane_archivist", registry)
    engine(registry).execute(
        state,
        ReorderTopDeck(player_id="white", card_ids=["c_card", "a_card", "b_card"]),
        DeterministicRNG(1),
    )
    assert state.get_player("white").deck == ["c_card", "a_card", "b_card", "d_card"]
    assert state.pending_decision is None


# ─────────────────────────────────────────────────────────────────────────────
# graveyard_to_deck / graveyard_inspect
# ─────────────────────────────────────────────────────────────────────────────

def test_grave_scholar_returns_a_graveyard_card_to_the_deck_bottom(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.graveyard = ["fallen_one", "fallen_two"]
    ps.deck = ["top_card"]
    events: list = []
    put(state, Position(3, 1), "white", "bishop", "grave_scholar", registry, events=events)
    assert ps.graveyard == ["fallen_two"]
    assert ps.deck == ["top_card", "fallen_one"]
    assert any(isinstance(e, GraveyardInspected) for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# discard_card
# ─────────────────────────────────────────────────────────────────────────────

def test_blood_price_makes_the_capturing_player_discard(registry):
    state = new_state(phase=Phase.CHESS)
    state.get_player("white").hand = ["dark_magician"]
    place_trap(state, registry, "blood_price", Position(3, 4), "black")
    attacker = put(state, Position(3, 4), "white", "rook")
    events: list = []
    check_capture_traps(state, attacker, "black", Position(3, 4), events, registry, rng=DeterministicRNG(7))
    assert any(isinstance(e, CardDiscarded) for e in events)
    assert state.get_player("white").hand == []
    assert state.get_player("white").graveyard == ["dark_magician"]


# ─────────────────────────────────────────────────────────────────────────────
# reveal_enemy_hand_card / reveal_own_hand_card
# ─────────────────────────────────────────────────────────────────────────────

def test_alarm_beacon_peeks_at_an_enemy_hand_card(registry):
    state = new_state(phase=Phase.CHESS)
    state.get_player("white").hand = ["dark_magician"]
    place_trap(state, registry, "alarm_beacon", Position(3, 3), "black")
    intruder = put(state, Position(3, 4), "white", "rook")
    events: list = []
    check_enter_radius_traps(state, intruder, Position(3, 4), events, registry, rng=DeterministicRNG(7))
    revealed = [e for e in events if isinstance(e, EnemyCardRevealed)]
    assert revealed and revealed[0].player_id == "black"
    assert revealed[0].revealed_card_id == "dark_magician"


def test_forbidden_knowledge_shows_the_opponent_one_of_your_own_cards(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.hand = ["forbidden_knowledge", "dark_magician"]
    give_ritual(state, "black", "rite_of_the_wyrm", RevelationState.SEALED)
    events = engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="forbidden_knowledge", target=None),
        DeterministicRNG(1),
    )
    revealed = [e for e in events if isinstance(e, EnemyCardRevealed)]
    assert revealed and revealed[0].player_id == "black", "the OPPONENT is the one who learns"
    assert revealed[0].revealed_card_id == "dark_magician"
    assert state.get_player("black").ritual_pool[0].revelation == RevelationState.FORETOLD


# ─────────────────────────────────────────────────────────────────────────────
# reveal_hidden_info / reveal_ritual / reveal_enemy_ritual
# ─────────────────────────────────────────────────────────────────────────────

def test_raven_scout_promotes_a_sealed_enemy_ritual(registry):
    state = new_state()
    give_ritual(state, "black", "rite_of_the_wyrm", RevelationState.SEALED)
    put(state, Position(3, 1), "white", "pawn", "raven_scout", registry)
    assert state.get_player("black").ritual_pool[0].revelation == RevelationState.FORETOLD


def test_ritual_insight_reveals_your_own_ritual_and_draws(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.hand = ["ritual_insight"]
    ps.deck = ["stone_golem"]
    give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="ritual_insight", target=None),
        DeterministicRNG(1),
    )
    assert ps.ritual_pool[0].revelation == RevelationState.FORETOLD
    assert ps.hand == ["stone_golem"]


def test_omen_bell_forces_a_sealed_enemy_ritual_to_foretold(registry):
    from game.mechanics.monsters import _fire_trap

    state = new_state(phase=Phase.CHESS)
    give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    trap = place_trap(state, registry, "omen_bell", Position(3, 3), "black")
    unit = put(state, Position(3, 4), "white", "rook")
    _fire_trap(state, trap, unit, Position(3, 4), "ritual_progress", [], registry,
               rng=DeterministicRNG(7))
    assert state.get_player("white").ritual_pool[0].revelation == RevelationState.FORETOLD


# ─────────────────────────────────────────────────────────────────────────────
# ritual_reveal_tradeoff
# ─────────────────────────────────────────────────────────────────────────────

def test_omen_reader_reveals_one_step_and_draws(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.deck = ["stone_golem"]
    give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    put(state, Position(3, 1), "white", "bishop", "omen_reader", registry)
    assert ps.ritual_pool[0].revelation == RevelationState.FORETOLD
    assert ps.hand == ["stone_golem"]


def test_oracle_of_the_last_star_can_trade_a_reveal_for_a_draw(registry):
    """
    oracle_of_the_last_star's ritual_reveal_tradeoff is ``once_per_turn``,
    i.e. an activated ability rather than an on-summon one — but it must
    still be reachable.
    """
    state = new_state(phase=Phase.CHESS)
    ps = state.get_player("white")
    ps.deck = ["a_card", "b_card", "c_card", "d_card", "e_card", "f_card"]
    give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    put(state, Position(3, 1), "white", "queen", "oracle_of_the_last_star", registry)
    state.pending_decision = None       # skip the summon's inspect/reorder decision
    hand_before = len(ps.hand)
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 1),
                               ability_id="ritual_reveal_tradeoff"),
        DeterministicRNG(1),
    )
    assert ps.ritual_pool[0].revelation == RevelationState.FORETOLD
    assert len(ps.hand) == hand_before + 1


# ─────────────────────────────────────────────────────────────────────────────
# ritual_requirement_reduction
# ─────────────────────────────────────────────────────────────────────────────

def test_forbidden_priest_reduces_a_requirement_when_activated(registry):
    state = new_state(phase=Phase.CHESS)
    rs = give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    priest = put(state, Position(3, 1), "white", "bishop", "forbidden_priest", registry)
    assert "req_reduction:available" in priest.statuses
    engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 1),
                               ability_id="ritual_requirement_reduction",
                               target="rite_of_the_wyrm"),
        DeterministicRNG(1),
    )
    assert rs.requirement_reduction == 1
    assert rs.revelation == RevelationState.REVEALED
    assert "req_reduction:available" not in priest.statuses


def test_hasten_the_ritual_fully_reveals_and_discounts_a_ritual(registry):
    """
    hasten_the_ritual: 'Fully reveal one of your Rituals. For its next
    activation attempt, one generic requirement is ignored.'
    """
    state = new_state()
    ps = state.get_player("white")
    ps.hand = ["hasten_the_ritual"]
    rs = give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="hasten_the_ritual", target=None),
        DeterministicRNG(1),
    )
    assert rs.revelation == RevelationState.REVEALED
    assert rs.requirement_reduction == 1


# ─────────────────────────────────────────────────────────────────────────────
# ritual_bluff
# ─────────────────────────────────────────────────────────────────────────────

def test_false_prophecy_arms_a_bluff_without_really_revealing(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.hand = ["false_prophecy"]
    rs = give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="false_prophecy", target=None),
        DeterministicRNG(1),
    )
    assert rs.bluff_turns == 2
    assert rs.revelation == RevelationState.SEALED, "the Ritual itself stays SEALED"


# ─────────────────────────────────────────────────────────────────────────────
# ritual_progress_boost / ritual_pattern_substitute / ritual_activation_range
# ─────────────────────────────────────────────────────────────────────────────

def test_ritual_acolyte_advances_a_ritual_at_end_of_turn(registry):
    state = new_state(phase=Phase.CHESS)
    rs = give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.FORETOLD)
    put(state, Position(3, 1), "white", "bishop", "ritual_acolyte", registry)
    before = rs.progress
    engine(registry).execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert rs.progress > before


def test_circle_keeper_is_armed_as_a_generic_ritual_component(registry):
    state = new_state()
    keeper = put(state, Position(3, 1), "white", "rook", "circle_keeper", registry)
    assert "ritual_substitute" in keeper.statuses


def test_herald_of_the_gate_arms_the_pattern_range_bonus(registry):
    state = new_state()
    herald = put(state, Position(3, 1), "white", "knight", "herald_of_the_gate", registry)
    assert statuses_with(herald, "ritual_range_bonus:") == ["ritual_range_bonus:1"]


def test_blood_seer_arms_its_sacrifice_bonus(registry):
    state = new_state()
    seer = put(state, Position(3, 1), "white", "pawn", "blood_seer", registry)
    assert statuses_with(seer, "sacrifice_ritual_bonus:") == ["sacrifice_ritual_bonus:2"]


# ─────────────────────────────────────────────────────────────────────────────
# obscure_influence
# ─────────────────────────────────────────────────────────────────────────────

def test_veil_conjurer_hides_the_card_id_behind_a_zone(registry):
    from game.core.observation import build_observation

    state = new_state()
    state.board.get_square(Position(3, 3)).add_effect("frozen:2:white:iron_vanguard")
    put(state, Position(3, 2), "white", "bishop", "veil_conjurer", registry)
    obs = build_observation(state, "black", registry)
    tagged = [e for e in obs.board.square_effects if e.position == Position(3, 3)]
    assert tagged, "the zone itself must still be visible"
    assert tagged[0].card_id is None, "but not which card caused it"


def test_without_a_veil_conjurer_the_card_id_is_public(registry):
    from game.core.observation import build_observation

    state = new_state()
    state.board.get_square(Position(3, 3)).add_effect("frozen:2:white:iron_vanguard")
    obs = build_observation(state, "black", registry)
    tagged = [e for e in obs.board.square_effects if e.position == Position(3, 3)]
    assert tagged and tagged[0].card_id == "iron_vanguard"
