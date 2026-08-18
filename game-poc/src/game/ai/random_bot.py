"""
RandomBot — Stage 0 / Stage 2 / Stage 5
=========================================

RandomBot is the simplest possible AI.  It selects a uniformly random action
from the list of legal actions provided by the engine.

Purpose:
    • Validate legal-action generation (the engine, not the bot, is being tested)
    • Fuzz unusual state transitions
    • Detect crashes and illegal states
    • Run hundreds/thousands of automated games for stability testing
    • Provide the baseline for future bot comparisons

Design contract:
    • Receives only an Observation + list[Action].
    • Never inspects internals.
    • Never uses Python's module-level ``random``.
    • Uses the bot's own seeded RNG for reproducibility (separate from game RNG).

Stage 5 update — two-stage PREPARATION selection:
    During PREPARATION the legal action list can contain many ``SummonMonster``
    entries (one per monster card × valid vessel), plus one ``EndPreparation``
    and a handful of others.  Picking uniformly from the raw list would make the
    bot summon on almost every turn just because summon entries dominate
    numerically.

    Instead, the bot:
      1. Groups legal actions by their *type class*, EXCLUDING ``DismissMonster``.
         Groups: SummonMonster, EndPreparation, DeclareRecompose, CoronateKing, …
      2. Picks one group uniformly — each distinct action type has equal weight.
      3. Picks one action uniformly from within that group.

    ``DismissMonster`` is only chosen independently with a 5 % probability
    BEFORE the two-stage picker runs — this keeps it extremely rare so the AI
    doesn't undo its own summoning every few turns.

    Outside PREPARATION (CHESS, DISCARD, …) the original uniform-random
    behaviour is preserved unchanged.

Usage:
    from game.ai.random_bot import RandomBot

    bot_white = RandomBot(seed=0)
    bot_white.player_id = "white"

    action = bot_white.choose_action(observation, legal_actions)
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from game.ai.controller import PlayerController
from game.core.actions import DismissMonster
from game.core.phases import Phase

if TYPE_CHECKING:
    from game.core.actions import Action
    from game.core.observation import Observation


class RandomBot(PlayerController):
    """
    Uniformly random action selection from the legal action list.

    Uses an internal seeded ``random.Random`` instance for reproducibility.
    The seed is independent of the game's DeterministicRNG so the bot's
    choices can be varied without changing game randomness, and vice versa.

    During Phase.PREPARATION a two-stage selection is used so every distinct
    action *type* (SummonMonster, EndPreparation, …) has equal probability
    regardless of how many variations each type has.  ``DismissMonster`` is
    excluded from the two-stage pool and is only chosen with 5 % probability.
    """

    def __init__(self, seed: int = 0) -> None:
        self._player_id: str = "?"
        self._rng = random.Random(seed)

    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        """
        Return a random action from ``legal_actions``.

        • PREPARATION phase: two-stage selection — first pick an action type
          uniformly, then pick one action of that type uniformly.  This prevents
          the SummonMonster entries from dominating just because there are many
          (card × vessel) combinations.

        • All other phases: uniform random from the full list (original behaviour).

        Raises ValueError if the legal action list is empty — this indicates
        an engine bug (there should always be at least EndTurn).
        """
        if not legal_actions:
            raise ValueError(
                f"RandomBot received an empty legal action list "
                f"(player={self._player_id!r}, phase={observation.phase!r}). "
                "This is an engine bug."
            )

        if observation.phase == Phase.PREPARATION:
            return self._choose_preparation(legal_actions)

        return self._rng.choice(legal_actions)

    # ── Helpers ───────────────────────────────────────────────────────────────

    _DISMISS_PROBABILITY = 0.05  # 5 % chance to even consider dismissing a monster

    def _choose_preparation(self, legal_actions: "list[Action]") -> "Action":
        """
        Two-stage selection for PREPARATION phase.

        DismissMonster is handled separately: with 5 % probability the bot will
        pick a random DismissMonster action (if any exist).  This prevents the
        AI from constantly undoing its own summoning while still allowing it to
        occasionally dismiss a monster.

        For all other action types:
          Stage 1: group by type class, EXCLUDING DismissMonster → pick one
                   group uniformly.
          Stage 2: pick one action uniformly from the chosen group.
        """
        dismiss_actions = [a for a in legal_actions if isinstance(a, DismissMonster)]
        other_actions = [a for a in legal_actions if not isinstance(a, DismissMonster)]

        # 5 % chance to dismiss (only when there is something to dismiss)
        if dismiss_actions and self._rng.random() < self._DISMISS_PROBABILITY:
            return self._rng.choice(dismiss_actions)

        # If all remaining actions are DismissMonster (shouldn't happen), fall back
        if not other_actions:
            return self._rng.choice(dismiss_actions)

        # Two-stage pick from non-dismiss actions
        groups: dict[type, list["Action"]] = {}
        for action in other_actions:
            key = type(action)
            groups.setdefault(key, []).append(action)

        chosen_type = self._rng.choice(list(groups.keys()))
        return self._rng.choice(groups[chosen_type])

    def __repr__(self) -> str:  # pragma: no cover
        return f"RandomBot(player_id={self._player_id!r})"
