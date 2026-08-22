"""
Simulation Harness — README Stage 2 / AI Stage 0
==================================================

The harness is the delivery vehicle for AI Stage 0's stated purpose:
run automated matches, fuzz the engine, and gather README §42 telemetry
in bulk.  These tests keep it honest without running full matches (a
RandomBot mirror can take hundreds of turns), so every case is capped.
"""

from __future__ import annotations

import json

import pytest

from game.ai.heuristic_bot import HeuristicBot
from game.ai.random_bot import RandomBot
from game.core.game import Game
from game.core.phases import Phase
from game.sim import (
    SimulationResult,
    acting_player,
    make_controller,
    play_match,
    run_matches,
)
from game.telemetry import MatchStats


class TestMakeController:
    def test_random(self):
        bot = make_controller("random", seed=1, player_id="white")
        assert isinstance(bot, RandomBot)
        assert bot.player_id == "white"

    def test_heuristic(self):
        bot = make_controller("heuristic", seed=1, player_id="black")
        assert isinstance(bot, HeuristicBot)
        assert bot.player_id == "black"

    def test_is_case_insensitive(self):
        assert isinstance(make_controller("HEURISTIC", 1, "white"), HeuristicBot)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown controller kind"):
            make_controller("mcts", seed=1, player_id="white")


class TestActingPlayer:
    def test_normal_phase_uses_the_active_player(self):
        game = Game.new(seed=1)
        assert acting_player(game.state) == game.state.active_player

    def test_final_duel_defender_acts_first(self):
        from game.core.phases import FinalDuelType
        from game.core.state import DuelState

        game = Game.new(seed=1)
        game.state.phase = Phase.FINAL_DUEL
        game.state.duel = DuelState(
            duel_type=FinalDuelType.SIEGE, attacker="white", defender="black",
        )
        assert acting_player(game.state) == "black"
        game.state.duel.defender_acted_this_round = True
        assert acting_player(game.state) == "white"


class TestPlayMatch:
    def test_returns_match_stats(self):
        controllers = {
            "white": RandomBot(seed=1),
            "black": RandomBot(seed=2),
        }
        stats = play_match(seed=3, controllers=controllers, max_steps=40)
        assert isinstance(stats, MatchStats)
        assert stats.seed == 3
        assert stats.action_count > 0
        assert set(stats.players) == {"white", "black"}

    def test_assigns_player_ids_to_the_controllers(self):
        white, black = RandomBot(seed=1), RandomBot(seed=2)
        play_match(seed=3, controllers={"white": white, "black": black},
                   max_steps=5)
        assert white.player_id == "white"
        assert black.player_id == "black"

    def test_max_steps_is_respected(self):
        stats = play_match(
            seed=3,
            controllers={"white": RandomBot(seed=1), "black": RandomBot(seed=2)},
            max_steps=12,
        )
        assert stats.action_count <= 12
        assert stats.completed is False

    def test_on_step_sees_every_action(self):
        seen: list[tuple[str, object]] = []
        play_match(
            seed=3,
            controllers={"white": RandomBot(seed=1), "black": RandomBot(seed=2)},
            max_steps=10,
            on_step=lambda pid, action: seen.append((pid, action)),
        )
        assert len(seen) == 10
        assert all(pid in ("white", "black") for pid, _ in seen)

    def test_same_seeds_replay_identically(self):
        def run() -> tuple:
            stats = play_match(
                seed=9,
                controllers={"white": RandomBot(seed=1), "black": RandomBot(seed=2)},
                max_steps=30,
            )
            return (stats.action_count, stats.turn_count, stats.ply_count,
                    stats.players["white"].cards_played)

        assert run() == run()

    def test_heuristic_against_random(self, registry):
        stats = play_match(
            seed=4,
            controllers={
                "white": HeuristicBot(seed=1, registry=registry),
                "black": RandomBot(seed=2),
            },
            registry=registry,
            max_steps=25,
        )
        assert stats.action_count > 0


class TestRunMatches:
    def test_aggregates_every_match(self):
        result = run_matches(count=3, base_seed=100, max_steps=15)
        assert isinstance(result, SimulationResult)
        assert len(result.aggregator) == 3
        assert result.errors == []
        assert len({m.seed for m in result.matches}) == 3

    def test_summary_is_json_serialisable(self):
        result = run_matches(count=2, base_seed=200, max_steps=15)
        parsed = json.loads(result.aggregator.to_json())
        assert parsed["matches"] == 2

    def test_writes_a_csv_row_per_match(self, tmp_path):
        result = run_matches(count=2, base_seed=300, max_steps=15)
        out = tmp_path / "run.csv"
        result.aggregator.write_csv(str(out))
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_a_run_is_reproducible_from_its_base_seed(self):
        def digest():
            result = run_matches(count=2, base_seed=400, max_steps=20)
            return [(m.seed, m.action_count, m.turn_count) for m in result.matches]

        assert digest() == digest()
