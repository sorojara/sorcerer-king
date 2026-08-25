"""
test_endgame_attrition.py — Endgame-pacing fix (A + C).

The problem: with the deck-empty reshuffle recycling the WHOLE graveyard
(RulesEngine._auto_draw), Spell/Trap cards circulate forever while Monster
cards quietly go dead once their Vessel classes are captured off the
board (captured_pieces is kept separate from the card graveyard and never
re-enters the deck). Long games degenerate into the same handful of
Spells/Traps being drawn, played, and reshuffled back in on a loop.

A. "Worn card" rule (RulesEngine._bury_or_retire) — a Spell/Trap's SECOND
   trip to the graveyard banishes it instead, giving it real attrition.
   Monsters are exempt; their attrition already comes from losing their
   Vessel.

B. Mercenary's cheapest tier (Pawn) dropped from 4 → 2 Monster cards, so
   a player stuck with dead Monster cards can re-enter Vessel play sooner
   instead of grinding through several all-Spell/Trap turns first.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.core.actions import ActivateSpell, DiscardCard
from game.core.events import CardWornOut
from game.core.phases import Phase
from game.core.rules import RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import GameState

from .conftest import make_player

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def engine(registry) -> RulesEngine:
    return RulesEngine(registry=registry)


@pytest.fixture
def rng() -> DeterministicRNG:
    return DeterministicRNG(seed=7)


def _state_with_hand(standard_board, hand: list[str]) -> GameState:
    white = make_player("white", hand=list(hand), deck=["dark_magician"])
    black = make_player("black", hand=[], deck=["wyrm_knight"])
    return GameState(
        game_id="worn-test",
        turn_number=1,
        active_player="white",
        phase=Phase.PREPARATION,
        board=standard_board,
        players={"white": white, "black": black},
        rng_seed=7,
    )


class TestBuryOrRetire:
    """Unit-level coverage of the new RulesEngine._bury_or_retire helper."""

    def test_spell_banished_on_second_graveyard_trip(self, engine, registry):
        ps = make_player("white")
        events = []

        engine._bury_or_retire(ps, "erase_memory", registry, events)
        assert ps.graveyard == ["erase_memory"]
        assert ps.banished == []
        assert "erase_memory" in ps.spent_once
        assert not any(isinstance(e, CardWornOut) for e in events)

        # Simulate the card being drawn again (deck recycle) and re-played.
        events2 = []
        engine._bury_or_retire(ps, "erase_memory", registry, events2)
        assert ps.graveyard == ["erase_memory"]  # unchanged — did NOT return here
        assert ps.banished == ["erase_memory"]
        assert any(
            isinstance(e, CardWornOut) and e.card_id == "erase_memory"
            for e in events2
        )

    def test_trap_card_follows_the_same_rule_as_spells(self, engine, registry):
        ps = make_player("white")
        engine._bury_or_retire(ps, "pit_trap", registry, [])
        engine._bury_or_retire(ps, "pit_trap", registry, [])
        assert ps.graveyard == ["pit_trap"]
        assert ps.banished == ["pit_trap"]

    def test_monster_cards_are_never_worn_out(self, engine, registry):
        """Monsters already attrite via losing their Vessel — don't tax twice."""
        ps = make_player("white")
        engine._bury_or_retire(ps, "dark_magician", registry, [])
        engine._bury_or_retire(ps, "dark_magician", registry, [])
        engine._bury_or_retire(ps, "dark_magician", registry, [])
        assert ps.graveyard == ["dark_magician", "dark_magician", "dark_magician"]
        assert ps.banished == []
        assert "dark_magician" not in ps.spent_once

    def test_registry_less_fallback_keeps_old_unconditional_behavior(self, engine):
        """Without a registry we can't tell Spell/Trap from Monster — never banish."""
        ps = make_player("white")
        for _ in range(3):
            engine._bury_or_retire(ps, "erase_memory", None, [])
        assert ps.graveyard == ["erase_memory"] * 3
        assert ps.banished == []


class TestActivateSpellIntegration:
    def test_second_activation_of_same_spell_banishes_instead_of_graveyarding(
        self, engine, registry, standard_board, rng
    ):
        # erase_memory's own "draw_cards: 1" effect would otherwise empty
        # white's deck on the very first cast and trigger the (unrelated,
        # pre-existing) deck-empty graveyard recycle mid-test — give white
        # enough filler cards that its deck never runs dry here, so the
        # test isolates _bury_or_retire's behavior specifically.
        state = _state_with_hand(standard_board, ["erase_memory"])
        state.players["white"].deck = ["dark_magician", "dark_magician", "dark_magician"]

        events1 = engine.execute(
            state, ActivateSpell(player_id="white", card_id="erase_memory", target=None), rng
        )
        assert "erase_memory" in state.players["white"].graveyard
        assert not any(isinstance(e, CardWornOut) for e in events1)

        # Card comes back to hand (e.g. via deck-empty reshuffle + draw).
        state.players["white"].hand.append("erase_memory")
        state.phase = Phase.PREPARATION
        state.players["white"].preparation_action_used = False

        events2 = engine.execute(
            state, ActivateSpell(player_id="white", card_id="erase_memory", target=None), rng
        )
        ps = state.players["white"]
        assert ps.graveyard.count("erase_memory") == 1  # did not grow
        assert ps.banished.count("erase_memory") == 1
        assert any(isinstance(e, CardWornOut) for e in events2)


class TestDiscardCardIntegration:
    def test_discarding_the_same_card_twice_eventually_banishes_it(
        self, engine, registry, standard_board
    ):
        state = _state_with_hand(standard_board, ["erase_memory"])
        state.phase = Phase.DISCARD
        ps = state.players["white"]

        engine._execute_discard_card(
            state, DiscardCard(player_id="white", card_id="erase_memory"),
            registry=registry,
        )
        assert ps.graveyard == ["erase_memory"]

        ps.hand.append("erase_memory")
        state.phase = Phase.DISCARD
        engine._execute_discard_card(
            state, DiscardCard(player_id="white", card_id="erase_memory"),
            registry=registry,
        )
        assert ps.graveyard == ["erase_memory"]
        assert ps.banished == ["erase_memory"]


class TestMercenaryCostTable:
    def test_pawn_tier_is_the_new_cheap_floor(self):
        assert RulesEngine._MERCENARY_COST["pawn"] == 2

    def test_bigger_pieces_are_unchanged(self):
        assert RulesEngine._MERCENARY_COST["knight"] == 6
        assert RulesEngine._MERCENARY_COST["bishop"] == 6
        assert RulesEngine._MERCENARY_COST["rook"] == 6
        assert RulesEngine._MERCENARY_COST["queen"] == 8
