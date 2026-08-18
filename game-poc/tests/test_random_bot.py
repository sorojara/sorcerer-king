"""
test_random_bot.py — RandomBot and PlayerController interface tests.

Covers:
    • RandomBot implements PlayerController.
    • RandomBot always returns an action from the provided list.
    • RandomBot with same seed produces identical choices.
    • RandomBot raises ValueError for empty legal action list.
    • RandomBot never receives or uses GameState.
    • Two RandomBots with different seeds can produce different choices.
    • Stage 5: two-stage PREPARATION selection — each action type has equal weight.
"""

from __future__ import annotations

import pytest
from game.ai.controller import PlayerController
from game.ai.random_bot import RandomBot
from game.core.actions import (
    DismissMonster,
    EndPreparation,
    EndTurn,
    MovePiece,
    SummonMonster,
)
from game.chess.pieces import Position
from game.core.observation import build_observation
from game.core.phases import Phase


class TestRandomBotInterface:
    def test_is_player_controller(self):
        bot = RandomBot(seed=0)
        assert isinstance(bot, PlayerController)

    def test_player_id_assignment(self):
        bot = RandomBot(seed=0)
        bot.player_id = "white"
        assert bot.player_id == "white"


class TestRandomBotChooseAction:
    def _legal_actions(self) -> list:
        return [
            EndPreparation(player_id="white"),
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            ),
            EndTurn(player_id="white"),
        ]

    def test_returns_action_from_list(self, game_state, rng):
        bot = RandomBot(seed=0)
        bot.player_id = "white"
        obs = build_observation(game_state, "white")
        legal = self._legal_actions()
        chosen = bot.choose_action(obs, legal)
        assert chosen in legal

    def test_same_seed_same_choice(self, game_state):
        obs = build_observation(game_state, "white")
        legal = self._legal_actions()

        bot_a = RandomBot(seed=99)
        bot_b = RandomBot(seed=99)
        bot_a.player_id = "white"
        bot_b.player_id = "white"

        assert bot_a.choose_action(obs, legal) is bot_b.choose_action(
            build_observation(game_state, "white"), legal
        )

    def test_empty_legal_actions_raises(self, game_state):
        bot = RandomBot(seed=0)
        bot.player_id = "white"
        obs = build_observation(game_state, "white")
        with pytest.raises(ValueError, match="engine bug"):
            bot.choose_action(obs, [])

    def test_single_action_always_chosen(self, game_state):
        bot = RandomBot(seed=42)
        bot.player_id = "white"
        obs = build_observation(game_state, "white")
        single = [EndTurn(player_id="white")]
        for _ in range(10):
            chosen = bot.choose_action(obs, single)
            assert chosen is single[0]

    def test_does_not_access_game_state(self, game_state):
        """
        RandomBot must use only Observation + legal_actions.
        We verify it doesn't fail when passed a dummy observation
        (if it tried to access the full game state it would crash).
        """
        bot = RandomBot(seed=7)
        bot.player_id = "white"
        obs = build_observation(game_state, "white")
        # Passing a fresh legal action list without GameState
        legal = [EndPreparation(player_id="white")]
        chosen = bot.choose_action(obs, legal)
        assert chosen is legal[0]


class TestRandomBotPreparationSelection:
    """Stage 5: two-stage PREPARATION selection."""

    def _make_prep_obs(self, game_state):
        """Return an Observation in PREPARATION phase."""
        return build_observation(game_state, "white")

    def _flood_legal(self, n_summons: int = 60) -> list:
        """
        Build a legal action list with many SummonMonster entries and one
        EndPreparation — mimicking the real PREPARATION legal list.
        """
        actions: list = [EndPreparation(player_id="white")]
        for i in range(n_summons):
            actions.append(
                SummonMonster(
                    player_id="white",
                    card_id=f"monster_{i % 3}",
                    vessel_position=Position(i % 8, i % 8),
                )
            )
        return actions

    def test_chosen_action_is_in_legal_list(self, game_state):
        bot = RandomBot(seed=0)
        bot.player_id = "white"
        obs = self._make_prep_obs(game_state)
        legal = self._flood_legal(60)
        chosen = bot.choose_action(obs, legal)
        assert chosen in legal

    def test_end_preparation_chosen_sometimes(self, game_state):
        """
        With 60 SummonMonster entries and 1 EndPreparation, the two-stage
        selection gives EndPreparation a 1/2 chance each call.
        Over 40 trials at least one EndPreparation should be chosen.
        """
        obs = self._make_prep_obs(game_state)
        legal = self._flood_legal(60)
        types_seen: set[type] = set()
        for seed in range(40):
            bot = RandomBot(seed=seed)
            bot.player_id = "white"
            chosen = bot.choose_action(obs, legal)
            types_seen.add(type(chosen))
        assert EndPreparation in types_seen, (
            "EndPreparation never chosen across 40 seeds — two-stage selection broken"
        )

    def test_summon_chosen_sometimes(self, game_state):
        """SummonMonster should also be chosen across seeds."""
        obs = self._make_prep_obs(game_state)
        legal = self._flood_legal(60)
        types_seen: set[type] = set()
        for seed in range(40):
            bot = RandomBot(seed=seed)
            bot.player_id = "white"
            chosen = bot.choose_action(obs, legal)
            types_seen.add(type(chosen))
        assert SummonMonster in types_seen

    def test_three_types_each_seen(self, game_state):
        """With three distinct action types, each should appear across many seeds."""
        obs = self._make_prep_obs(game_state)
        legal = [
            EndPreparation(player_id="white"),
            SummonMonster(player_id="white", card_id="m1",
                          vessel_position=Position(0, 0)),
            SummonMonster(player_id="white", card_id="m1",
                          vessel_position=Position(1, 0)),
            SummonMonster(player_id="white", card_id="m1",
                          vessel_position=Position(2, 0)),
            DismissMonster(player_id="white",
                           unit_position=Position(3, 0)),
        ]
        types_seen: set[type] = set()
        for seed in range(60):
            bot = RandomBot(seed=seed)
            bot.player_id = "white"
            chosen = bot.choose_action(obs, legal)
            types_seen.add(type(chosen))
        assert EndPreparation in types_seen
        assert SummonMonster in types_seen
        assert DismissMonster in types_seen

    def test_non_preparation_phase_unchanged(self, game_state):
        """Outside PREPARATION the original uniform-random behaviour is used."""
        import dataclasses
        chess_obs = dataclasses.replace(
            build_observation(game_state, "white"),
            phase=Phase.CHESS,
        )
        legal = [
            MovePiece(player_id="white",
                      source=Position(0, 1),
                      target=Position(0, 2)),
        ] * 50 + [EndTurn(player_id="white")]
        # With 50× MovePiece and 1 EndTurn, should almost always pick MovePiece
        end_turn_count = 0
        for seed in range(50):
            bot = RandomBot(seed=seed)
            bot.player_id = "white"
            chosen = bot.choose_action(chess_obs, legal)
            if isinstance(chosen, EndTurn):
                end_turn_count += 1
        # Expect roughly 1/51 ≈ 2% EndTurn; allow up to 10 out of 50
        assert end_turn_count < 10, (
            "Two-stage selection incorrectly applied outside PREPARATION"
        )
