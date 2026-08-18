"""
Deterministic RNG Contract — Stage 0
======================================

All randomness in the game flows through a single DeterministicRNG instance
stored in the GameState.

Contract:
    1. Every match receives a seed at creation time: ``Game(seed=938471)``.
    2. RNG state is advanced only through ``DeterministicRNG`` methods.
    3. The seed is stored in the match log so any match can be reproduced exactly.
    4. AI or UI code must never call random.* directly.

Random operations in the game:
    • deck shuffle      (at game start and after Recompose)
    • card draw         (top of shuffled deck)
    • Recompose count   (weighted 1–4 distribution)
    • future random effects

Seeding strategy:
    The RNG wraps Python's ``random.Random`` (MT19937) with an explicit seed.
    The internal state can be snapshotted and restored for AI tree-search.

Usage:
    rng = DeterministicRNG(seed=42)
    rng.shuffle(deck)                 # mutates in place
    count = rng.recompose_count()     # 1–4 (weighted)
    rng.save_state() / rng.load_state(snapshot)  # for MCTS
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


# Recompose distribution: weighted 1-4.
# Heavier on 2-3 (costly but not always devastating).
# Adjust weights during balance testing.
_RECOMPOSE_WEIGHTS = [1, 2, 3, 2]   # weights for [1, 2, 3, 4]
_RECOMPOSE_VALUES  = [1, 2, 3, 4]


@dataclass
class DeterministicRNG:
    """
    A seedable, snapshotable, deterministic random number generator.

    All game random events must call methods on this object.
    The engine stores one instance per GameState.
    """

    seed: int
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    # ── Core operations ──────────────────────────────────────────────────

    def shuffle(self, deck: list[Any]) -> None:
        """Shuffle ``deck`` in-place deterministically."""
        self._rng.shuffle(deck)

    def choice(self, population: list[Any]) -> Any:
        """Return a uniformly random element from ``population``."""
        if not population:
            raise ValueError("choice() from empty population")
        return self._rng.choice(population)

    def randint(self, a: int, b: int) -> int:
        """Return a random integer N such that a <= N <= b."""
        return self._rng.randint(a, b)

    def recompose_count(self) -> int:
        """
        Draw the Recompose requirement (how many cards must be returned).

        Uses a weighted distribution biased toward 2–3 to make Recompose
        costly but not always crippling.  Weights can be tuned:
            value:  1  2  3  4
            weight: 1  2  3  2   → P(1)=12.5%, P(2)=25%, P(3)=37.5%, P(4)=25%
        """
        return self._rng.choices(_RECOMPOSE_VALUES, weights=_RECOMPOSE_WEIGHTS, k=1)[0]

    # ── State snapshot/restore (for MCTS / AI lookahead) ─────────────────

    def save_state(self) -> Any:
        """
        Return an opaque snapshot of the current RNG state.
        Pass this to ``load_state`` to restore to this point.
        """
        return self._rng.getstate()

    def load_state(self, snapshot: Any) -> None:
        """Restore RNG to a previously saved state."""
        self._rng.setstate(snapshot)

    # ── Convenience ──────────────────────────────────────────────────────

    @classmethod
    def from_seed(cls, seed: int) -> "DeterministicRNG":
        return cls(seed=seed)
