"""
test_game_facade.py — Game facade (core/game.py) integration tests (Stage 1).

Covers:
    • Game.new() creates a valid starting state.
    • Game.new() deals 5-card opening hand.
    • game.get_legal_actions() delegates correctly.
    • game.get_observation() returns an Observation (never GameState).
    • game.execute() returns an ExecutionResult.
    • game.execute() auto-advances START → DRAW → PREPARATION.
    • A full turn (prep + chess + end) cycles active player.
    • game.is_over() and game.winner() start False/None.
    • game.state is accessible for tests.
    • Two Game.new() with same seed produce identical opening hands.
    • Two Game.new() with different seeds produce different opening hands.
"""

from __future__ import annotations

import pytest
from game.core.game import Game, ExecutionResult
from game.core.actions import EndPreparation, EndTurn, MovePiece
from game.core.observation import Observation
from game.core.phases import Phase
from game.chess.pieces import Position


POC_DECK_WHITE = [
    "dark_magician", "apprentice_mage", "arcane_sentinel",
    "wyrm_knight", "dragon_herald", "iron_vanguard",
    "blade_dancer", "shadow_wolf", "stone_golem", "ritual_acolyte",
    "arcane_reposition", "veil_of_stillness", "cursed_ground",
    "ritual_insight", "shatter_trap", "pit_trap", "ward_of_binding",
    "alarm_beacon", "counter_strike", "time_anchor",
]

POC_DECK_BLACK = list(reversed(POC_DECK_WHITE))


class TestGameNew:
    def test_creates_game(self):
        game = Game.new(seed=0)
        assert game is not None

    def test_active_player_is_white(self):
        game = Game.new(seed=0)
        assert game.state.active_player == "white"

    def test_phase_is_preparation(self):
        game = Game.new(seed=0)
        assert game.state.phase == Phase.PREPARATION

    def test_board_has_32_pieces(self):
        game = Game.new(seed=0)
        assert len(game.state.board.all_units()) == 32

    def test_opening_hand_5_cards(self):
        game = Game.new(seed=0, white_deck=POC_DECK_WHITE, black_deck=POC_DECK_BLACK)
        assert len(game.state.players["white"].hand) == 5
        assert len(game.state.players["black"].hand) == 5

    def test_same_seed_same_hand(self):
        g1 = Game.new(seed=42, white_deck=POC_DECK_WHITE)
        g2 = Game.new(seed=42, white_deck=POC_DECK_WHITE)
        assert list(g1.state.players["white"].hand) == list(g2.state.players["white"].hand)

    def test_different_seeds_different_hands(self):
        g1 = Game.new(seed=1, white_deck=POC_DECK_WHITE)
        g2 = Game.new(seed=2, white_deck=POC_DECK_WHITE)
        assert list(g1.state.players["white"].hand) != list(g2.state.players["white"].hand)

    def test_is_over_starts_false(self):
        game = Game.new(seed=0)
        assert not game.is_over()

    def test_winner_starts_none(self):
        game = Game.new(seed=0)
        assert game.winner() is None

    def test_game_id_auto_generated(self):
        game = Game.new(seed=0)
        assert game.state.game_id is not None
        assert len(game.state.game_id) > 0

    def test_custom_game_id(self):
        game = Game.new(seed=0, game_id="my-test-game")
        assert game.state.game_id == "my-test-game"


class TestGameObservation:
    def test_get_observation_returns_observation(self):
        game = Game.new(seed=0, white_deck=POC_DECK_WHITE, black_deck=POC_DECK_BLACK)
        obs = game.get_observation("white")
        assert isinstance(obs, Observation)

    def test_observation_does_not_expose_opponent_hand(self):
        game = Game.new(seed=0, white_deck=POC_DECK_WHITE, black_deck=POC_DECK_BLACK)
        obs = game.get_observation("white")
        # White should see own hand but only black's count
        assert len(obs.own_hand) == 5
        assert obs.opponent_hand_count == 5
        assert not hasattr(obs, "opponent_hand")

    def test_observation_player_id(self):
        game = Game.new(seed=0)
        obs = game.get_observation("black")
        assert obs.player_id == "black"


class TestGameExecute:
    def test_execute_returns_execution_result(self):
        game = Game.new(seed=0)
        result = game.execute(EndPreparation(player_id="white"))
        assert isinstance(result, ExecutionResult)

    def test_execute_advances_phase(self):
        game = Game.new(seed=0)
        result = game.execute(EndPreparation(player_id="white"))
        assert result.phase == Phase.CHESS

    def test_execute_events_not_empty(self):
        game = Game.new(seed=0)
        result = game.execute(EndPreparation(player_id="white"))
        assert len(result.events) > 0

    def test_execute_chess_move(self):
        game = Game.new(seed=0)
        game.execute(EndPreparation(player_id="white"))
        result = game.execute(
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            )
        )
        from game.core.events import PieceMoved
        assert any(isinstance(e, PieceMoved) for e in result.events)

    def test_full_white_turn_switches_to_black(self):
        """
        White: EndPreparation → MovePiece(e2-e4) → EndTurn
        → active_player should become black.
        """
        game = Game.new(seed=0)
        game.execute(EndPreparation(player_id="white"))
        game.execute(
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            )
        )
        # After move, phase is REACTION; call EndTurn
        game.execute(EndTurn(player_id="white"))
        assert game.state.active_player == "black"


class TestGameAutoStart:
    """Tests that START phase is automatically resolved when execute() is called."""

    def _advance_to_black_start(self, game: Game) -> None:
        """Complete white's first turn."""
        game.execute(EndPreparation(player_id="white"))
        game.execute(
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("d2"),
                target=Position.from_algebraic("d4"),
            )
        )
        game.execute(EndTurn(player_id="white"))

    def test_black_can_play_after_white_ends_turn(self):
        game = Game.new(seed=0, white_deck=POC_DECK_WHITE, black_deck=POC_DECK_BLACK)
        self._advance_to_black_start(game)
        # The game should now be in START or auto-advanced to PREPARATION
        # Calling EndPreparation from black should work (auto-start fires if needed)
        result = game.execute(EndPreparation(player_id="black"))
        assert result.phase == Phase.CHESS

    def test_turn_counter_increments_after_full_round(self):
        """After both white and black complete a turn, turn_number increments."""
        game = Game.new(seed=0)
        assert game.state.turn_number == 1
        self._advance_to_black_start(game)
        # White has gone; black needs to go for counter to increment
        game.execute(EndPreparation(player_id="black"))
        game.execute(
            MovePiece(
                player_id="black",
                source=Position.from_algebraic("e7"),
                target=Position.from_algebraic("e5"),
            )
        )
        game.execute(EndTurn(player_id="black"))
        assert game.state.turn_number == 2


class TestLegalActionsIntegration:
    def test_preparation_legal_actions_include_end_prep(self):
        game = Game.new(seed=0)
        actions = game.get_legal_actions("white")
        assert any(isinstance(a, EndPreparation) for a in actions)

    def test_chess_legal_actions_include_moves(self):
        game = Game.new(seed=0)
        game.execute(EndPreparation(player_id="white"))
        actions = game.get_legal_actions("white")
        move_actions = [a for a in actions if isinstance(a, MovePiece)]
        assert len(move_actions) == 20  # Standard opening

    def test_inactive_player_gets_no_actions(self):
        game = Game.new(seed=0)
        actions = game.get_legal_actions("black")
        assert actions == []
