"""
PersonalityBot — AI Personalities (README §51)
================================================

    "Enemy AI should not only vary by difficulty.  It should also have
     play styles.  Evaluation weights can create personalities."

`personality.py` defines the five play styles and turns each into an
``EvalWeights`` plus an ``ActionBiasSet``.  This file is the controller
that plays with them.

Why it subclasses SearchBot
---------------------------
README §51 is about *what the bot wants*, not about how hard it thinks —
that is §52's job.  So a personality changes the numbers and nothing else:
``PersonalityBot`` inherits Stage 2's tree search whole, and the search
runs over the retuned weights because ``search_bot.py`` already reads
``self._weights``.  A Conqueror and an Architect therefore calculate chess
equally well and simply disagree about which position they were aiming for
— which is the difference a player should be able to feel.

That inheritance is also the strongest guarantee behind the module's "a
tilt, not a monomania" rule: the chess is untouched, so no play style can
tune itself out of playing.  On top of it, `personality.py` clamps every
weight into ``[TILT_FLOOR, TILT_CEIL]`` × default and floors the staple
action biases (Summon, Construction, Ritual, Spell, Trap, Coronation,
Castle).  A Ritualist that valued nothing but Rituals would never summon
the Monsters a Ritual eats; this one summons *more* than the default bot.

Adaptive play styles
--------------------
The Opportunist re-mixes the other four from its Observation.  The mix is
recomputed once per turn rather than once per decision, so one turn's
Summon, Ritual and chess move are all decided by the same personality
instead of drifting mid-turn.

Information rules (README §44) are untouched: the re-mix reads the same
Observation the bot is already deciding from, and nothing else.

Usage:
    from game.ai.personality_bot import PersonalityBot

    bot = PersonalityBot(seed=1, personality="ritualist", registry=registry)
    bot.player_id = "black"
    action = bot.choose_action(observation, legal_actions)

Headless:
    python -m game.sim --white personality --white-personality assassin
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.ai.personality import (
    DEFAULT_PERSONALITY,
    Personality,
    get_personality,
)
from game.ai.search import SearchLimits
from game.ai.search_bot import SearchBot
from game.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.actions import Action
    from game.core.observation import Observation

_log = get_logger(__name__)


class PersonalityBot(SearchBot):
    """
    A README §51 play style, played at Stage 2 strength.

    ``seed``        — the bot's own RNG, for tie-breaking only.
    ``personality`` — a key from ``PERSONALITIES`` ("conqueror",
                      "architect", "ritualist", "assassin", "opportunist")
                      or a ``Personality`` instance.
    ``registry``    — optional public card definitions (README §44).
    ``limits``      — the Stage 2 search budget, unchanged by the play style.
    """

    def __init__(
        self,
        seed: int = 0,
        personality: "str | Personality" = DEFAULT_PERSONALITY,
        registry: "CardRegistry | None" = None,
        limits: SearchLimits = SearchLimits(),
    ) -> None:
        style = get_personality(personality)
        super().__init__(
            seed=seed,
            weights=style.weights(),
            registry=registry,
            limits=limits,
            biases=style.biases(),
        )
        self._personality = style
        # Adaptive styles re-mix once per turn; -1 forces a mix on the
        # first decision of the match.
        self._tuned_turn: int = -1
        self._mix: dict[str, float] = {style.key: 1.0}

    # ── PlayerController ──────────────────────────────────────────────────

    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        if self._personality.is_adaptive:
            self._retune(observation)
        return super().choose_action(observation, legal_actions)

    # ── Adaptive tuning ───────────────────────────────────────────────────

    def _retune(self, observation: "Observation") -> None:
        """
        Re-mix an adaptive play style from the current Observation.

        Once per turn: a mid-turn swing would have the bot summoning as an
        Architect and then moving as an Assassin, which is neither.
        """
        if observation.turn_number == self._tuned_turn:
            return
        self._tuned_turn = observation.turn_number
        self._weights = self._personality.weights(observation)
        self._bias = self._personality.biases(observation)
        blend = self._personality.blend
        if blend is not None:
            raw = blend(observation)
            total = sum(raw.values()) or 1.0
            self._mix = {k: v / total for k, v in raw.items()}
            _log.debug(
                "PERSONALITY player=%s turn=%d mix=%s",
                self._player_id, observation.turn_number,
                ", ".join(f"{k}={v:.2f}" for k, v in sorted(self._mix.items())),
            )

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def personality(self) -> Personality:
        """The play style this bot is running."""
        return self._personality

    @property
    def mix(self) -> "dict[str, float]":
        """
        The current normalised blend over play styles.

        ``{"ritualist": 1.0}`` for a fixed personality; a live mixture for
        the Opportunist, for the event log and §42 telemetry.
        """
        return dict(self._mix)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PersonalityBot(player_id={self._player_id!r}, "
            f"personality={self._personality.key!r})"
        )
