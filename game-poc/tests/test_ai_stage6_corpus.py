"""
test_ai_stage6_corpus.py — AI Stage 6 tooling (README §53).

Two modules:

    game.ai.weights   — the evaluation table, on disk.  §53 wants heuristic
                        weights optimized; an optimizer runs outside the
                        process that plays the match, so the weight vector
                        has to survive a file and a command line.
    game.selfplay     — the corpus runner: parallel, sharded, resumable,
                        streaming.  §53's gate is 10,000+ and 100,000+
                        matches, and a run that long will be interrupted.

The matches here are deliberately tiny (``max_turns=4``) — what is under
test is the corpus machinery, not the game.
"""

from __future__ import annotations

import json

import pytest

from game.ai.evaluation import DEFAULT_WEIGHTS, EvalWeights
from game.ai.heuristic_bot import ActionBias
from game.ai.weights import (
    BIAS_FIELDS,
    WEIGHT_FIELDS,
    BiasTable,
    biases_from_dict,
    biases_to_dict,
    load_weights,
    save_weights,
    weights_from_dict,
    weights_to_dict,
)
from game.selfplay import (
    CorpusSpec,
    _partition,
    completed_seeds,
    iter_decisions,
    iter_examples,
    iter_matches,
    run_corpus,
    summarize,
)
from game.sim import make_controller


# ─────────────────────────────────────────────────────────────────────────────
# Weights on disk
# ─────────────────────────────────────────────────────────────────────────────

class TestWeightsRoundTrip:
    def test_every_field_is_tunable(self):
        assert "material" in WEIGHT_FIELDS
        assert "ritual_progress" in WEIGHT_FIELDS
        assert len(WEIGHT_FIELDS) == len(weights_to_dict())

    def test_dict_round_trip_is_lossless(self):
        assert weights_from_dict(weights_to_dict()) == DEFAULT_WEIGHTS

    def test_a_partial_file_keeps_every_other_default(self):
        """A search over one weight should be a one-line file."""
        tuned = weights_from_dict({"material": 3.5})
        assert tuned.material == 3.5
        assert tuned.ritual_progress == DEFAULT_WEIGHTS.ritual_progress
        assert tuned.card_in_hand == DEFAULT_WEIGHTS.card_in_hand

    def test_radius_fields_stay_integers(self):
        """A radius is a square count; a float would break the geometry."""
        tuned = weights_from_dict({"king_safety_radius": 3.4})
        assert tuned.king_safety_radius == 3
        assert isinstance(tuned.king_safety_radius, int)

    def test_an_unknown_field_is_an_error_not_a_no_op(self):
        with pytest.raises(ValueError, match="unknown EvalWeights field"):
            weights_from_dict({"materal": 2.0})

    def test_values_are_absolute_not_multipliers(self):
        """
        §51's tables take multipliers and clamp them; tuning must not, or the
        optimizer is scored on a candidate it did not propose.
        """
        tuned = weights_from_dict({"material": 0.0})
        assert tuned.material == 0.0


class TestBiasTable:
    def test_defaults_match_the_action_bias_class(self):
        table = BiasTable()
        for name in BIAS_FIELDS:
            assert getattr(table, name) == getattr(ActionBias, name)

    def test_named_biases_override(self):
        table = biases_from_dict({"SUMMON": 9.0})
        assert table.SUMMON == 9.0
        assert table.END_TURN == float(ActionBias.END_TURN)

    def test_an_unknown_bias_is_an_error(self):
        with pytest.raises(ValueError, match="unknown ActionBias field"):
            biases_from_dict({"SUMMONN": 1.0})

    def test_as_dict_round_trips(self):
        assert biases_from_dict(biases_to_dict()).as_dict() == biases_to_dict()


class TestWeightFiles:
    def test_flat_file_is_weights_only(self, tmp_path):
        path = tmp_path / "flat.json"
        path.write_text(json.dumps({"material": 2.0}), encoding="utf-8")
        weights, biases = load_weights(path)
        assert weights.material == 2.0
        assert biases is None                      # leave the bot's table alone

    def test_sectioned_file_carries_both(self, tmp_path):
        path = tmp_path / "both.json"
        path.write_text(
            json.dumps({"weights": {"material": 2.0}, "biases": {"SUMMON": 7.0}}),
            encoding="utf-8",
        )
        weights, biases = load_weights(path)
        assert weights.material == 2.0
        assert biases is not None and biases.SUMMON == 7.0

    def test_save_then_load(self, tmp_path):
        path = tmp_path / "saved.json"
        save_weights(weights_from_dict({"material": 1.75}), path, biases=ActionBias)
        weights, biases = load_weights(path)
        assert weights.material == 1.75
        assert biases is not None and biases.SUMMON == float(ActionBias.SUMMON)

    def test_a_non_object_file_is_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="expected a JSON object"):
            load_weights(path)


class TestControllerWiring:
    """The point of the file: it has to reach the bot that plays the match."""

    @pytest.mark.parametrize("kind", ["heuristic", "search", "montecarlo"])
    def test_weights_reach_the_scoring_bots(self, kind, registry):
        tuned = weights_from_dict({"material": 4.25})
        bot = make_controller(kind, 1, "white", registry, weights=tuned)
        assert bot._weights.material == 4.25

    def test_biases_reach_the_scoring_bots(self, registry):
        table = biases_from_dict({"SUMMON": 8.5})
        bot = make_controller("heuristic", 1, "white", registry, biases=table)
        assert bot._bias.SUMMON == 8.5

    def test_random_bot_ignores_them(self, registry):
        """RandomBot scores nothing — handing it weights must not break it."""
        bot = make_controller(
            "random", 1, "white", registry,
            weights=weights_from_dict({"material": 4.0}),
        )
        assert bot is not None

    def test_defaults_are_untouched_when_nothing_is_passed(self, registry):
        bot = make_controller("heuristic", 1, "white", registry)
        assert bot._weights is DEFAULT_WEIGHTS


# ─────────────────────────────────────────────────────────────────────────────
# Corpus spec
# ─────────────────────────────────────────────────────────────────────────────

class TestCorpusSpec:
    def test_seeds_are_contiguous_from_the_base(self):
        assert CorpusSpec(matches=4, base_seed=100).seeds() == [100, 101, 102, 103]

    def test_worker_count_defaults_to_the_machine(self):
        assert CorpusSpec(matches=1).worker_count() >= 1
        assert CorpusSpec(matches=1, workers=3).worker_count() == 3

    def test_limits_fall_back_to_the_defaults(self):
        from game.core.state import MatchLimits

        assert CorpusSpec(matches=1).limits() == MatchLimits()
        assert CorpusSpec(matches=1, max_turns=7).limits().max_turns == 7

    def test_partition_deals_round_robin(self):
        """
        Contiguous slices would stack the long matches together; dealing
        spreads them, so no core is left holding the tail of the run.
        """
        assert _partition([0, 1, 2, 3, 4, 5], 3) == [[0, 3], [1, 4], [2, 5]]

    def test_partition_never_makes_empty_chunks(self):
        assert _partition([0, 1], 8) == [[0], [1]]
        assert _partition([0, 1, 2], 1) == [[0, 1, 2]]


# ─────────────────────────────────────────────────────────────────────────────
# Running a corpus
# ─────────────────────────────────────────────────────────────────────────────

def _tiny(tmp_path, **kwargs) -> CorpusSpec:
    """A corpus whose matches are over almost immediately."""
    defaults = dict(
        matches=4,
        out_dir=str(tmp_path / "corpus"),
        white="random",
        black="random",
        base_seed=500,
        workers=1,
        max_turns=4,
    )
    defaults.update(kwargs)
    return CorpusSpec(**defaults)


class TestRunCorpus:
    def test_a_run_writes_one_record_per_match(self, tmp_path):
        spec = _tiny(tmp_path)
        result = run_corpus(spec, progress=False)

        assert result.played == 4
        assert result.failed == 0
        records = list(iter_matches(spec.out_dir))
        assert len(records) == 4
        assert {r["seed"] for r in records} == set(spec.seeds())

    def test_every_record_carries_an_outcome(self, tmp_path):
        spec = _tiny(tmp_path)
        run_corpus(spec, progress=False)
        for record in iter_matches(spec.out_dir):
            assert record["completed"] is True
            assert record["end_reason"] is not None
            assert "telemetry" in record

    def test_a_second_run_replays_nothing(self, tmp_path):
        spec = _tiny(tmp_path)
        run_corpus(spec, progress=False)
        again = run_corpus(spec, progress=False)

        assert again.played == 0
        assert again.already_present == 4
        assert len(list(iter_matches(spec.out_dir))) == 4

    def test_extending_a_corpus_only_plays_the_new_seeds(self, tmp_path):
        first = _tiny(tmp_path, matches=2)
        run_corpus(first, progress=False)
        second = _tiny(tmp_path, matches=4)
        result = run_corpus(second, progress=False)

        assert result.already_present == 2
        assert result.played == 2
        assert len(list(iter_matches(second.out_dir))) == 4

    def test_completed_seeds_is_what_resume_reads(self, tmp_path):
        spec = _tiny(tmp_path)
        run_corpus(spec, progress=False)
        assert completed_seeds(spec.out_dir) == set(spec.seeds())

    def test_a_torn_line_does_not_stop_a_reader(self, tmp_path):
        """A run killed mid-write leaves at most one incomplete line."""
        spec = _tiny(tmp_path, matches=2)
        run_corpus(spec, progress=False)
        shard = sorted((tmp_path / "corpus").glob("matches-*.jsonl"))[0]
        with open(shard, "a", encoding="utf-8") as fh:
            fh.write('{"seed": 999, "winner": "wh')

        assert len(list(iter_matches(spec.out_dir))) == 2

    def test_the_pool_path_works_too(self, tmp_path):
        """workers=1 skips multiprocessing entirely; this exercises the pool."""
        spec = _tiny(tmp_path, matches=4, workers=2)
        result = run_corpus(spec, progress=False)
        assert result.played == 4
        assert len(list(iter_matches(spec.out_dir))) == 4

    def test_summary_reads_the_corpus_back_off_disk(self, tmp_path):
        spec = _tiny(tmp_path)
        run_corpus(spec, progress=False)
        summary = summarize(spec.out_dir)

        assert summary["matches"] == 4
        assert summary["unfinished"] == 0
        assert summary["decided"] + summary["drawn"] == 4
        assert summary["shards"] >= 1
        assert sum(summary["end_reasons"].values()) == 4


class TestTrajectories:
    def test_no_decision_rows_unless_asked(self, tmp_path):
        spec = _tiny(tmp_path)
        run_corpus(spec, progress=False)
        assert list(iter_decisions(spec.out_dir)) == []

    def test_decision_rows_carry_the_position_as_the_evaluator_saw_it(self, tmp_path):
        spec = _tiny(tmp_path, matches=2, trajectories=True)
        run_corpus(spec, progress=False)

        rows = list(iter_decisions(spec.out_dir))
        assert rows, "trajectories were requested"
        row = rows[0]
        assert set(row) >= {
            "seed", "step", "player", "turn", "phase", "action", "n_legal", "eval",
        }
        assert row["player"] in ("white", "black")
        assert row["n_legal"] >= 1
        assert "material" in row["eval"]

    def test_features_can_be_switched_off(self, tmp_path):
        spec = _tiny(tmp_path, matches=1, trajectories=True, features=False)
        run_corpus(spec, progress=False)
        rows = list(iter_decisions(spec.out_dir))
        assert rows and all("eval" not in r for r in rows)

    def test_examples_are_labelled_from_the_acting_players_side(self, tmp_path):
        spec = _tiny(tmp_path, matches=4, trajectories=True)
        run_corpus(spec, progress=False)

        outcomes = {m["seed"]: m for m in iter_matches(spec.out_dir)}
        examples = list(iter_examples(spec.out_dir))
        assert examples

        for row in examples:
            winner = outcomes[row["seed"]]["winner"]
            expected = 0 if winner is None else (1 if winner == row["player"] else -1)
            assert row["result"] == expected
            assert row["result"] in (-1, 0, 1)

    def test_decisions_without_a_recorded_outcome_are_dropped(self, tmp_path):
        """Better no example than one labelled with a guess."""
        spec = _tiny(tmp_path, matches=1, trajectories=True)
        run_corpus(spec, progress=False)

        shard = sorted((tmp_path / "corpus").glob("decisions-*.jsonl"))[0]
        with open(shard, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seed": 999_999, "player": "white", "step": 0}) + "\n")

        assert all(r["seed"] != 999_999 for r in iter_examples(spec.out_dir))


class TestDecisionCounting:
    """
    ``summarize`` wants a row count, not the rows.  A 100,000-match corpus
    with trajectories holds tens of millions of them, so the count reads
    newlines rather than parsing JSON — but it has to agree with the parser
    it replaced, including on the torn last line a killed run can leave.
    """

    def test_it_agrees_with_parsing_every_row(self, tmp_path):
        from game.selfplay import count_decisions

        spec = _tiny(tmp_path, matches=3, trajectories=True)
        run_corpus(spec, progress=False)

        assert count_decisions(spec.out_dir) == len(list(iter_decisions(spec.out_dir)))

    def test_an_empty_corpus_counts_zero(self, tmp_path):
        from game.selfplay import count_decisions

        spec = _tiny(tmp_path, matches=1)
        run_corpus(spec, progress=False)
        assert count_decisions(spec.out_dir) == 0

    def test_summarize_uses_it(self, tmp_path):
        spec = _tiny(tmp_path, matches=2, trajectories=True)
        run_corpus(spec, progress=False)
        assert summarize(spec.out_dir)["decision_rows"] == len(
            list(iter_decisions(spec.out_dir))
        )
