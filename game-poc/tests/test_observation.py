"""
test_observation.py — Observation schema and hidden-information enforcement tests.

Verifies (from phase0.md §11, README §44):
    • Observation does not expose opponent hand card IDs.
    • Observation does not expose opponent deck order.
    • Observation does not expose hidden King identities.
    • Observation does not expose SEALED Ritual identities.
    • Observation DOES expose own hand, own deck count, own rituals.
    • Observation correctly reports opponent hand COUNT (not content).
    • PublicBoardState is consistent with GameState board.
    • build_observation is callable and returns an Observation.
"""

from __future__ import annotations

import pytest
from game.core.observation import Observation, build_observation, PublicKingInfo
from game.core.phases import KingCardStatus, Phase, RevelationState
from game.core.state import (
    GameState,
    KingCardState,
    RitualState,
)


class TestObservationPrivacy:
    """Core hidden-information tests.  Invariant: AI must never cheat."""

    def test_observation_hides_opponent_hand_cards(
        self, game_state: GameState
    ):
        obs = build_observation(game_state, "white")
        # We know opponent_hand_count exists but no card IDs
        assert hasattr(obs, "opponent_hand_count")
        assert obs.opponent_hand_count == len(game_state.players["black"].hand)
        # There should be no attribute leaking the actual card IDs
        assert not hasattr(obs, "opponent_hand"), "Observation must not expose opponent_hand"

    def test_observation_hides_opponent_deck_order(
        self, game_state: GameState
    ):
        obs = build_observation(game_state, "white")
        assert obs.opponent_deck_count == len(game_state.players["black"].deck)
        # No deck content accessible
        assert not hasattr(obs, "opponent_deck"), "Observation must not expose opponent_deck"

    def test_observation_exposes_own_hand(
        self, game_state: GameState
    ):
        obs = build_observation(game_state, "white")
        assert set(obs.own_hand) == set(game_state.players["white"].hand)

    def test_observation_exposes_own_deck_count_only(
        self, game_state: GameState
    ):
        obs = build_observation(game_state, "white")
        assert obs.own_deck_count == len(game_state.players["white"].deck)

    def test_observation_hides_sealed_king_identity(
        self, game_state: GameState
    ):
        """Opponent's HIDDEN kings must appear as HIDDEN with no card ID."""
        obs = build_observation(game_state, "white")
        for ki in obs.opponent_king_info:
            if ki.status == KingCardStatus.HIDDEN:
                assert ki.king_card_id is None, (
                    "HIDDEN King must not expose card ID to opponent"
                )

    def test_observation_exposes_active_king_identity(
        self, game_state: GameState
    ):
        """Once a King is coronated (ACTIVE), opponent can see its ID."""
        black_ps = game_state.players["black"]
        black_ps.king_pool[0].status = KingCardStatus.ACTIVE
        black_ps.active_king = black_ps.king_pool[0].king_card_id

        obs = build_observation(game_state, "white")
        active_ki = next(
            (ki for ki in obs.opponent_king_info if ki.status == KingCardStatus.ACTIVE),
            None,
        )
        assert active_ki is not None
        assert active_ki.king_card_id == black_ps.active_king

    def test_observation_hides_sealed_ritual(
        self, game_state: GameState
    ):
        """SEALED ritual must not expose ritual_id to the opponent."""
        black_ps = game_state.players["black"]
        black_ps.ritual_pool = [RitualState(ritual_id="ancient_dragon")]

        obs = build_observation(game_state, "white")
        for ri in obs.opponent_ritual_info:
            if ri.revelation == RevelationState.SEALED:
                assert ri.ritual_id is None, "SEALED ritual must not expose ID"

    def test_observation_exposes_revealed_ritual(
        self, game_state: GameState
    ):
        """REVEALED ritual IS public — opponent should see the ritual ID."""
        black_ps = game_state.players["black"]
        black_ps.ritual_pool = [
            RitualState(ritual_id="ancient_dragon", revelation=RevelationState.REVEALED)
        ]

        obs = build_observation(game_state, "white")
        revealed = next(
            (ri for ri in obs.opponent_ritual_info
             if ri.revelation == RevelationState.REVEALED),
            None,
        )
        assert revealed is not None
        assert revealed.ritual_id == "ancient_dragon"

    def test_own_observation_sees_own_rituals_in_full(
        self, game_state: GameState
    ):
        """Player always has full access to their own ritual pool."""
        black_ps = game_state.players["black"]
        black_ps.ritual_pool = [
            RitualState(ritual_id="secret_summon", revelation=RevelationState.SEALED)
        ]
        # Build observation for black
        obs = build_observation(game_state, "black")
        own_rituals = obs.own_rituals
        assert len(own_rituals) == 1
        assert own_rituals[0].ritual_id == "secret_summon"


class TestObservationStructure:
    def test_returns_observation_instance(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        assert isinstance(obs, Observation)

    def test_player_id_matches(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        assert obs.player_id == "white"

    def test_phase_matches_game_state(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        assert obs.phase == game_state.phase

    def test_turn_number_matches(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        assert obs.turn_number == game_state.turn_number

    def test_board_contains_all_units(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        board_unit_count = len(game_state.board.all_units())
        assert len(obs.board.units) == board_unit_count

    def test_no_pending_decision_when_none(self, game_state: GameState):
        obs = build_observation(game_state, "white")
        assert obs.pending_decision_type is None

    def test_check_flag_reflects_state(self, game_state: GameState):
        # Initially not in check
        obs = build_observation(game_state, "white")
        assert obs.own_in_check is False
        assert obs.opponent_in_check is False
