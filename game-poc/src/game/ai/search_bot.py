"""
SearchBot — AI Stage 2 (README §47)
=====================================

    "Introduce tree search ... This works well for deterministic visible
     portions of the game.  However, the complete game contains hidden
     information and randomness, so pure minimax will not be the final
     solution."

SearchBot is AI Stage 1 with a tree bolted onto the part of the game a tree
actually fits: the chess phase.

    CHESS phase   →  minimax / alpha-beta over the public board
                     (game/ai/search.py), with iterative deepening, move
                     ordering, a transposition table and a quiescence
                     extension.
    every other   →  HeuristicBot's Stage 1 scoring, unchanged.
    phase

The split is deliberate and is the README's own reasoning.  A Summon, a
Ritual or a Recompose turns on cards the bot cannot see and on draws that
have not happened yet; searching them with a minimax that quietly assumes
"the opponent has nothing in hand" would be *worse* than the Stage 1
estimate, not better.  Those decisions wait for the belief model (§48) and
the Monte-Carlo sampler (§49).  The board, by contrast, is fully public and
fully deterministic, so a tree over it is pure gain — and it is where the
material actually changes hands.

How a chess decision is made
----------------------------
Every candidate action is turned into the position it leads to, and that
position is searched with the opponent to move::

    score(action) = negamax(position_after(action), depth - 1) + bias(action)

    MovePiece      → the move is applied to the search board
    Castle         → King and Rook are both relocated
    EndTurn        → a null move: the opponent simply gets the position
    AttackBuilding → the board is unchanged (the attacker never relocates,
                     README §11.2), so the siege is valued by its Stage 1
                     bias on top of the searched position
    anything else  → Stage 1 bias on top of the searched position

``bias`` carries the handful of things the board search cannot see — the
value of knocking a Building down, and the bot's own anti-shuffling memory.
Because a bias is applied *after* the search, root alpha-beta windows are
widened by ``_BIAS_MARGIN`` so a pruned candidate can never have been
rescued by its bias.

Information rules (README §44)
------------------------------
Unchanged from Stage 1: an Observation, a legal action list, and the public
card registry.  The search position is rebuilt from the Observation's public
board (``search.board_for_search``) — it is not the engine's GameState, and
no card, deck or hidden pool is ever consulted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.ai.evaluation import DEFAULT_WEIGHTS, EvalContext, EvalWeights
from game.ai.heuristic_bot import ActionBias, HeuristicBot
from game.ai.search import (
    Hazards,
    SearchLimits,
    SearchStats,
    Searcher,
    board_for_search,
)
from game.core.actions import (
    AttackBuilding,
    Castle,
    EndTurn,
    MovePiece,
)
from game.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.actions import Action
    from game.core.observation import Observation

_log = get_logger(__name__)


# Widest bias any root candidate can carry, used to keep root alpha-beta
# windows honest (see the module docstring).  AttackBuilding is the loudest:
# building_complete + building_integrity ≈ 2.25 with the default weights.
_BIAS_MARGIN = 4.0


class SearchBot(HeuristicBot):
    """
    A searching opponent (README §47).

    ``seed``     — the bot's own RNG, for tie-breaking only.
    ``weights``  — the README §46 evaluation weights, shared by the Stage 1
                   scorer and the search's leaf evaluation.
    ``registry`` — the public card definitions (never match state).
    ``limits``   — search budget; the default keeps one decision well under
                   a second so the pygame UI stays responsive.

    Reproducibility: identical for a given seed and *node* budget.  The
    wall-clock ceiling is not reproducible by nature — a loaded machine
    stops the search earlier — so pin ``max_seconds`` high and drive the
    budget with ``max_nodes`` when an experiment has to repeat exactly.
    """

    def __init__(
        self,
        seed: int = 0,
        weights: EvalWeights = DEFAULT_WEIGHTS,
        registry: "CardRegistry | None" = None,
        limits: SearchLimits = SearchLimits(),
        biases: "type[ActionBias] | ActionBias" = ActionBias,
    ) -> None:
        super().__init__(
            seed=seed, weights=weights, registry=registry, biases=biases
        )
        self._limits = limits
        self._last_search = SearchStats()

    # ── PlayerController ──────────────────────────────────────────────────

    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        if not legal_actions:
            raise ValueError(
                f"SearchBot received an empty legal action list "
                f"(player={self._player_id!r}, phase={observation.phase!r}). "
                "This is an engine bug."
            )
        self._note_turn(observation)
        if len(legal_actions) == 1:
            self._remember(legal_actions[0])
            return legal_actions[0]

        if any(isinstance(a, (MovePiece, Castle)) for a in legal_actions):
            return self._choose_by_search(observation, legal_actions)

        # Preparation, Discard, Final Duel, pending decisions: hidden
        # information and card systems — Stage 1 territory (README §47).
        return super().choose_action(observation, legal_actions)

    # ── Searched decision ─────────────────────────────────────────────────

    def _choose_by_search(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        me = observation.player_id
        opponent = "black" if me == "white" else "white"

        ctx = EvalContext(
            obs=observation, weights=self._weights, registry=self._registry
        )
        searcher = Searcher(
            board=board_for_search(observation),
            me=me,
            registry=self._registry,
            weights=self._weights,
            limits=self._limits,
            hazards=Hazards.from_observation(observation),
        )
        searcher.start()

        # Root ordering: the Stage 1 estimate is a good first guess, and a
        # good first guess is what makes alpha-beta prune on the deeper
        # iterations.
        candidates = sorted(
            legal_actions,
            key=lambda a: self.score_action(a, ctx),
            reverse=True,
        )

        # If even the first iteration runs out of budget, the Stage 1
        # ordering's favourite stands and the position's own score is
        # reported for it.
        best_action = candidates[0]
        best_score = searcher.evaluate()

        # Iterative deepening: every iteration produces a complete answer,
        # so running out of budget half way through a deeper one costs
        # nothing — the previous iteration's choice still stands.
        for depth in range(1, self._limits.max_depth + 1):
            iteration_best: list["Action"] = []
            iteration_score = -float("inf")
            for action in candidates:
                # Alpha at the root is the best score so far, dropped by the
                # widest bias any candidate can carry, so pruning here can
                # never discard a move that its bias would have rescued.
                floor = (
                    -float("inf") if iteration_score == -float("inf")
                    else iteration_score - _BIAS_MARGIN
                )
                value = self._value_of(
                    action, searcher, ctx, opponent, depth, floor
                )
                if searcher.stats.aborted:
                    break
                if value > iteration_score + 1e-9:
                    iteration_score, iteration_best = value, [action]
                elif value > iteration_score - 1e-9:
                    iteration_best.append(action)

            if searcher.stats.aborted:
                break

            best_score = iteration_score
            best_action = (
                iteration_best[0] if len(iteration_best) == 1
                else self._rng.choice(iteration_best)
            )
            searcher.stats.depth = depth
            # Search the winner first next time round.
            candidates.sort(key=lambda a: a is best_action, reverse=True)

        searcher.finish()
        searcher.stats.root_moves = len(legal_actions)
        searcher.stats.best_score = best_score
        self._last_search = searcher.stats
        self._last_score = best_score
        self._remember(best_action)

        _log.debug(
            "SEARCH   player=%s phase=%s candidates=%d depth=%d nodes=%d "
            "%.3fs%s chose=%s score=%.3f",
            self._player_id, observation.phase.value, len(legal_actions),
            searcher.stats.depth, searcher.stats.nodes, searcher.stats.elapsed,
            " (budget)" if searcher.stats.aborted else "",
            type(best_action).__name__, best_score,
        )
        return best_action

    def _value_of(
        self,
        action: "Action",
        searcher: Searcher,
        ctx: EvalContext,
        opponent: str,
        depth: int,
        alpha_floor: float = -float("inf"),
    ) -> float:
        """
        Search the position ``action`` leads to and add its bias.

        ``alpha_floor`` prunes candidates that cannot beat the best one
        found so far.  Fail-soft alpha-beta only ever returns a value ABOVE
        the floor when that value is trustworthy, so a pruned candidate is
        always genuinely worse — see the module docstring on the margin.
        """
        bias = self._root_bias(action, ctx)

        if isinstance(action, MovePiece):
            undo = searcher.make(action.source, action.target)
            value = searcher.value_to_move(opponent, depth - 1, alpha_floor)
            searcher.unmake(action.source, action.target, undo)
        elif isinstance(action, Castle):
            undo = searcher.make_castle(action.player_id, action.side)
            value = searcher.value_to_move(opponent, depth - 1, alpha_floor)
            searcher.unmake_castle(undo)
        else:
            # EndTurn, AttackBuilding, monster abilities: the board is
            # unchanged, so this is the null move — the opponent gets the
            # position as it stands.
            value = searcher.value_to_move(opponent, depth - 1, alpha_floor)

        return value + bias

    def _root_bias(self, action: "Action", ctx: EvalContext) -> float:
        """
        What the board search cannot see.

        Material, safety and King pressure all come out of the search, so a
        MovePiece must NOT be re-scored with the Stage 1 delta (that would
        count every capture twice).  What survives is the anti-shuffling
        memory, the siege valuation, and — for anything else that is legal
        during CHESS — the Stage 1 estimate.
        """
        if isinstance(action, MovePiece):
            if (action.source, action.target) in self._recent_moves or \
                    (action.target, action.source) in self._recent_moves:
                return self._bias.REPETITION
            return 0.0
        if isinstance(action, Castle):
            return self._bias.CASTLE
        if isinstance(action, AttackBuilding):
            return self._score_siege(action, ctx)
        if isinstance(action, EndTurn):
            return self._bias.END_TURN
        return self.score_action(action, ctx)

    # ── Instrumentation ───────────────────────────────────────────────────

    @property
    def last_search(self) -> SearchStats:
        """Cost of the most recent searched decision (README §42 tuning)."""
        return self._last_search

    @property
    def limits(self) -> SearchLimits:
        return self._limits

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SearchBot(player_id={self._player_id!r}, "
            f"depth={self._limits.max_depth})"
        )
