"""
test_rng.py — DeterministicRNG contract tests.

Verifies:
    • Same seed produces identical shuffle results.
    • Different seeds produce different shuffle results.
    • recompose_count returns values in [1, 4].
    • Weighted distribution is approximately correct over many samples.
    • State snapshot/restore works correctly for lookahead.
    • Multiple RNG instances with the same seed are independent.
"""

from __future__ import annotations

import pytest
from game.core.rng import DeterministicRNG


class TestDeterminism:
    def test_same_seed_same_shuffle(self):
        """Two RNGs with the same seed must produce identical shuffles."""
        deck_a = list(range(20))
        deck_b = list(range(20))

        rng_a = DeterministicRNG(seed=12345)
        rng_b = DeterministicRNG(seed=12345)

        rng_a.shuffle(deck_a)
        rng_b.shuffle(deck_b)

        assert deck_a == deck_b

    def test_different_seeds_different_shuffles(self):
        deck_a = list(range(20))
        deck_b = list(range(20))

        rng_a = DeterministicRNG(seed=1)
        rng_b = DeterministicRNG(seed=2)

        rng_a.shuffle(deck_a)
        rng_b.shuffle(deck_b)

        # Probability of collision is astronomically low
        assert deck_a != deck_b

    def test_sequential_shuffles_differ(self):
        rng = DeterministicRNG(seed=99)
        deck_a = list(range(10))
        deck_b = list(range(10))
        rng.shuffle(deck_a)
        rng.shuffle(deck_b)
        assert deck_a != deck_b  # two calls advance the state


class TestRecomposeCount:
    def test_always_in_range(self):
        rng = DeterministicRNG(seed=0)
        for _ in range(1000):
            count = rng.recompose_count()
            assert 1 <= count <= 4

    def test_distribution_weighted_toward_middle(self):
        """P(2) + P(3) > P(1) + P(4) based on weights [1,2,3,2]."""
        rng = DeterministicRNG(seed=0)
        counts = {1: 0, 2: 0, 3: 0, 4: 0}
        n = 10_000
        for _ in range(n):
            c = rng.recompose_count()
            counts[c] += 1
        # P(1) = 12.5%, P(4) = 25%, P(2) = 25%, P(3) = 37.5%
        assert counts[2] + counts[3] > counts[1] + counts[4]
        # All values appear
        for k in (1, 2, 3, 4):
            assert counts[k] > 0


class TestStateSnapshot:
    def test_restore_produces_same_sequence(self):
        rng = DeterministicRNG(seed=777)
        snapshot = rng.save_state()

        results_a = [rng.randint(0, 100) for _ in range(10)]
        rng.load_state(snapshot)
        results_b = [rng.randint(0, 100) for _ in range(10)]

        assert results_a == results_b

    def test_snapshot_does_not_affect_original_after_restore(self):
        rng = DeterministicRNG(seed=55)
        snap = rng.save_state()
        _ = rng.randint(0, 100)  # advance
        rng.load_state(snap)
        val1 = rng.randint(0, 100)
        rng.load_state(snap)
        val2 = rng.randint(0, 100)
        assert val1 == val2


class TestFromSeed:
    def test_from_seed_class_method(self):
        rng = DeterministicRNG.from_seed(42)
        assert rng.seed == 42

    def test_choice_from_non_empty_list(self):
        rng = DeterministicRNG(seed=1)
        items = ["a", "b", "c"]
        result = rng.choice(items)
        assert result in items

    def test_choice_empty_raises(self):
        rng = DeterministicRNG(seed=1)
        with pytest.raises(ValueError):
            rng.choice([])
