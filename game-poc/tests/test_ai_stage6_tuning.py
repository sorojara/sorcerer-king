"""
test_ai_stage6_tuning.py — weight search (README §53).

§53 asks for "optimize heuristic weights" and "evolutionary tuning".
``game.ai.weights`` made a weight vector portable; ``game.tuning`` is the
search over them.

Fitness evaluation costs minutes of real match play, so almost everything
here tests the search *logic* — mutation bounds, the promotion rule,
checkpointing, resume — against a stubbed evaluation. The one end-to-end
test plays a handful of real games and is kept deliberately tiny.

The rule worth testing hardest is the promotion bar. Hill-climbing on noise
is the default failure mode of a search like this: with 80 games a candidate
needs roughly 8 points of score to be distinguishable, and a search that
promotes anything less is a random walk with extra steps.
"""

from __future__ import annotations

import json
import random

import pytest

from game.ai.evaluation import DEFAULT_WEIGHTS
from game.ai.weights import weights_from_dict, weights_to_dict
from game import tuning
from game.tuning import (
    MAX_SCALE,
    MIN_SCALE,
    STRUCTURAL_FIELDS,
    TUNABLE_FIELDS,
    Result,
    TuningSpec,
    load_latest,
    mutate,
    run,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mutation
# ─────────────────────────────────────────────────────────────────────────────

class TestMutation:
    def test_only_the_named_fields_move(self):
        mutated = mutate(DEFAULT_WEIGHTS, ("material",), random.Random(1), 0.3)
        assert mutated.material != DEFAULT_WEIGHTS.material
        assert mutated.ritual_progress == DEFAULT_WEIGHTS.ritual_progress
        assert mutated.card_in_hand == DEFAULT_WEIGHTS.card_in_hand

    def test_steps_are_multiplicative_so_small_weights_still_move(self):
        """
        Weights span two orders of magnitude — territory_square is 0.05 and
        king_safety_check is 4.0 — so one additive step size would be a
        rounding error for one and a rewrite of the other.
        """
        rng = random.Random(7)
        small = [
            mutate(DEFAULT_WEIGHTS, ("territory_square",), rng, 0.3).territory_square
            for _ in range(20)
        ]
        relative = [abs(v - DEFAULT_WEIGHTS.territory_square)
                    / DEFAULT_WEIGHTS.territory_square for v in small]
        assert max(relative) > 0.05, "a small weight must still be able to move"

    def test_exploration_bounds_are_respected(self):
        rng = random.Random(3)
        weights = DEFAULT_WEIGHTS
        for _ in range(200):
            weights = mutate(weights, ("material",), rng, 1.5)
        assert DEFAULT_WEIGHTS.material * MIN_SCALE <= weights.material
        assert weights.material <= DEFAULT_WEIGHTS.material * MAX_SCALE

    def test_bounds_are_wider_than_a_personality_would_allow(self):
        """
        §51 clamps play styles to [0.6, 3.0]× so a personality cannot tune
        itself out of playing. A search has the opposite requirement.
        """
        from game.ai.personality import clamp_tilt

        assert MIN_SCALE < 0.6
        assert MAX_SCALE > 3.0
        assert clamp_tilt(MIN_SCALE) > MIN_SCALE   # the style clamp would bite

    def test_structural_fields_are_held_fixed_by_default(self):
        """
        A radius changes what the evaluation *looks at*, not how much it
        cares — a structural change hiding inside a numeric one.
        """
        assert STRUCTURAL_FIELDS
        assert not (STRUCTURAL_FIELDS & TUNABLE_FIELDS)
        mutated = mutate(
            DEFAULT_WEIGHTS, tuple(TUNABLE_FIELDS), random.Random(1), 0.5
        )
        for name in STRUCTURAL_FIELDS:
            assert getattr(mutated, name) == getattr(DEFAULT_WEIGHTS, name)

    def test_a_radius_can_still_be_tuned_when_asked_for_explicitly(self):
        mutated = mutate(
            DEFAULT_WEIGHTS, ("king_safety_radius",), random.Random(9), 0.9
        )
        assert isinstance(mutated.king_safety_radius, int)


# ─────────────────────────────────────────────────────────────────────────────
# Spec
# ─────────────────────────────────────────────────────────────────────────────

class TestSpec:
    def test_default_tunable_set_excludes_structural(self):
        assert set(TuningSpec().tunable()) == set(TUNABLE_FIELDS)

    def test_named_fields_are_honoured(self):
        assert TuningSpec(fields=("material",)).tunable() == ("material",)

    def test_an_unknown_field_is_rejected(self):
        with pytest.raises(ValueError, match="unknown weight field"):
            TuningSpec(fields=("materal",)).tunable()

    def test_workers_default_to_the_machine(self):
        assert TuningSpec().worker_count() >= 1
        assert TuningSpec(workers=3).worker_count() == 3


# ─────────────────────────────────────────────────────────────────────────────
# The promotion rule
# ─────────────────────────────────────────────────────────────────────────────

class TestResult:
    def test_a_draw_counts_half(self):
        result = Result()
        for value in (1.0, 0.0, 0.5, 0.5):
            result.add(value)
        assert result.mean == 0.5

    def test_error_shrinks_with_games(self):
        small, large = Result(), Result()
        for i in range(10):
            small.add(float(i % 2))
        for i in range(1000):
            large.add(float(i % 2))
        assert large.stderr < small.stderr / 5


class _StubEvaluate:
    """Replaces match play with a scripted score per generation."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = 0

    def __call__(self, challengers, incumbent, spec, generation, pool=None):
        target = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        results = []
        for index, _candidate in enumerate(challengers):
            result = Result()
            # Give candidate 0 the target score, the rest parity.
            value = target if index == 0 else 0.5
            for _ in range(spec.matches * 2):
                result.add(value)
            results.append(result)
        return results


class TestPromotion:
    def _spec(self, tmp_path, **kwargs):
        defaults = dict(
            generations=1, population=2, matches=20,
            fields=("material",), out_dir=str(tmp_path / "tune"),
        )
        defaults.update(kwargs)
        return TuningSpec(**defaults)

    def test_a_clean_sweep_is_promoted(self, tmp_path, monkeypatch):
        """
        A candidate that scores 1.0 every game has zero variance, so the
        naive ``margin > SIGMA * stderr`` divides by a zero error and
        refuses it. A sweep is the strongest evidence a sample can carry.
        """
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([1.0]))
        result = run(self._spec(tmp_path), progress=False)
        assert result["promotions"] == 1
        assert result["changed"], "the winning weights should have been adopted"

    def test_a_sweep_over_too_few_games_is_not_promoted(self, tmp_path, monkeypatch):
        """Three games agreeing with each other is not a sweep."""
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([1.0]))
        result = run(self._spec(tmp_path, matches=1), progress=False)
        assert result["promotions"] == 0

    def test_parity_is_not_promoted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([0.5]))
        result = run(self._spec(tmp_path), progress=False)
        assert result["promotions"] == 0
        assert result["changed"] == {}

    def test_a_noisy_small_edge_is_not_promoted(self, tmp_path, monkeypatch):
        """0.52 over 40 games is indistinguishable from parity."""
        class Noisy(_StubEvaluate):
            def __call__(self, challengers, incumbent, spec, generation, pool=None):
                results = []
                rng = random.Random(4)
                for _ in challengers:
                    result = Result()
                    for _ in range(spec.matches * 2):
                        result.add(rng.choice([0.0, 0.5, 1.0]))
                    results.append(result)
                return results

        monkeypatch.setattr(tuning, "evaluate", Noisy([]))
        result = run(self._spec(tmp_path, matches=20), progress=False)
        assert result["promotions"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Checkpointing
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckpoints:
    def _spec(self, tmp_path, **kwargs):
        defaults = dict(
            generations=3, population=2, matches=10,
            fields=("material",), out_dir=str(tmp_path / "tune"),
        )
        defaults.update(kwargs)
        return TuningSpec(**defaults)

    def test_every_generation_is_written_as_it_completes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([0.5, 0.5, 0.5]))
        spec = self._spec(tmp_path)
        run(spec, progress=False)
        written = sorted((tmp_path / "tune").glob("generation-*.json"))
        assert len(written) == 3

    def test_a_checkpoint_carries_the_incumbent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([0.5]))
        spec = self._spec(tmp_path, generations=1)
        run(spec, progress=False)
        data = json.loads(
            next((tmp_path / "tune").glob("generation-*.json")).read_text()
        )
        assert set(data["incumbent"]) == set(weights_to_dict())

    def test_load_latest_finds_the_newest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tuning, "evaluate", _StubEvaluate([0.5, 0.5, 0.5]))
        spec = self._spec(tmp_path)
        run(spec, progress=False)
        found = load_latest(spec.out_dir)
        assert found is not None
        assert found[0] == 3

    def test_load_latest_on_an_empty_dir(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert load_latest(tmp_path / "empty") is None

    def test_a_resumed_run_does_not_redo_finished_generations(
        self, tmp_path, monkeypatch,
    ):
        stub = _StubEvaluate([0.5, 0.5, 0.5, 0.5])
        monkeypatch.setattr(tuning, "evaluate", stub)
        run(self._spec(tmp_path, generations=2), progress=False)
        first_pass = stub.calls

        run(self._spec(tmp_path, generations=3), progress=False)
        assert stub.calls == first_pass + 1, "only the missing generation"


# ─────────────────────────────────────────────────────────────────────────────
# End to end
# ─────────────────────────────────────────────────────────────────────────────

class TestRealMatches:
    def test_a_tiny_search_completes_and_checkpoints(self, tmp_path):
        """
        Real match play, kept to the minimum that exercises the plumbing:
        one generation, one mutant, two seeds — four games.
        """
        spec = TuningSpec(
            generations=1, population=1, matches=2,
            fields=("material",), out_dir=str(tmp_path / "tune"),
            workers=1, base_seed=500, max_steps=400,
        )
        result = run(spec, progress=False)

        assert result["generations"] == 1
        assert set(result["incumbent"]) == set(weights_to_dict())
        assert (tmp_path / "tune" / "generation-0001.json").exists()
        history = result["history"][0]
        assert history["best"]["games"] == 2       # two seeds, averaged per seed
