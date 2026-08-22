"""
MonteCarloBot — AI Stage 4 (README §49)
=========================================

    "For hidden information, sample plausible game states consistent with
     the AI's Observation ... Estimated outcome: 63 % favorable.  The AI
     then chooses the action with the best expected outcome.  This is
     likely to be more appropriate than pure minimax for the mature game."

Stage 2 searches one world: the board as it stands, with the opponent's
hand quietly assumed to be empty.  That is why ``SearchBot`` hands every
Summon, Ritual and Recompose back to the Stage 1 estimator — searching a
card decision inside a world where the opponent holds no cards is worse
than not searching it at all.

Stage 4 searches *many* worlds instead::

    score(action) = mean over sampled worlds w of
                        search(position_after(action) | w)
                  + bias(action)

Each world is a ``Determinization`` (``game.ai.determinize``): a complete,
consistent guess at the opponent's hand, Rituals and Kings.  The guess is
turned into board facts — the Monster the sampled hand could most usefully
summon is put on the vessel it would most usefully take — and the position
is then searched with the ordinary Stage 2 machinery.  Averaging over the
worlds is the Monte Carlo; the search inside each world is what keeps the
estimate sharp enough to be worth averaging.  This is determinization,
the second technique §49 lists, and it is the one that fits a controller
that owns a *board* model rather than a full game simulator.

Which decisions get sampled
---------------------------
    PREPARATION  →  sampled.  This is Stage 4's reason to exist: a Summon
                    or a Ritual is judged by what the board looks like
                    afterwards, against an opponent who might hold
                    anything, instead of by a flat "+1.2 for summoning".
    CHESS        →  sampled, at a shallower per-world depth.  The board is
                    public, so what sampling adds here is narrower — the
                    threat map of a Monster the opponent has not summoned
                    *yet* — and depth is worth more than breadth, so the
                    two budgets are configured separately.
    anything     →  Stage 2 / Stage 1, unchanged (``SearchBot``).
    else

Because the turn does not pass at the end of PREPARATION — EndPreparation
moves the same player on to CHESS, and the one major preparation action
per turn is spent *before* the chess move (``rules._execute_end_preparation``)
— a preparation candidate is searched with the bot itself still to move.
A chess candidate hands the position to the opponent, exactly as Stage 2
does.

Spending the budget
-------------------
Worlds are shared across candidates: every candidate is scored against the
*same* sampled world before the next one is drawn.  That is common random
numbers — the comparison that decides the move is paired, so the noise
that matters cancels instead of accumulating.

Candidates are then cut by **successive halving**: the worse half of the
field is dropped between rounds, and survivors keep their tallies, so the
moves still in contention are the ones measured on the most worlds and the
budget is not spread evenly over forty Summons that are obviously bad.  The
first world drawn is always the null one — "the opponent has nothing" — so
the ranking stays anchored to the position as it actually is.

What ``favorable`` means
------------------------
Each world is also searched with no action taken at all (the null move).
An action counts as favorable in that world when it comes out ahead of
doing nothing, and the fraction of worlds where it does is reported as
``MonteCarloStats.favorable`` — README §49's "63 % favorable".

Honest limits
-------------
This is Perfect-Information Monte Carlo, and it inherits PIMC's two known
faults: *strategy fusion* (each world is searched as if its hidden cards
were face-up, so the bot credits itself with plans it could not actually
choose between) and *non-locality* (the opponent is assumed to play the
sampled world, not to hide information).  Sampling is uniform over
consistent worlds; README §48's belief model is what replaces that with a
weighted draw, and §49's own list of later techniques — MCTS,
information-set MCTS — is what replaces PIMC itself.

Information rules (README §44)
------------------------------
Unchanged: an Observation, the legal action list, and the public card
registry.  Everything a world contains is a *guess* built from public
information; the sampler cannot read a hidden card, and is wrong about
most of them by construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game.ai.determinize import NULL_WORLD, Determinization, Determinizer
from game.ai.evaluation import DEFAULT_WEIGHTS, EvalContext, EvalWeights, chebyshev
from game.ai.heuristic_bot import ActionBias
from game.ai.search import Hazards, SearchLimits, Searcher, board_for_search
from game.ai.search_bot import SearchBot
from game.core.actions import (
    ActivateRitual,
    AttackBuilding,
    Castle,
    DismissMonster,
    EndTurn,
    MovePiece,
    SummonMonster,
)
from game.core.phases import Phase
from game.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.chess.pieces import Position
    from game.core.actions import Action
    from game.core.observation import Observation

_log = get_logger(__name__)


# The phases whose decisions turn on cards the bot cannot see.  Everything
# else (Final Duel, forced discards, pending follow-ups) is left to Stage 2
# and Stage 1 — sampling a world there would cost budget and change nothing.
_SAMPLED_PHASES = (Phase.PREPARATION, Phase.CHESS)

# The action types whose consequence a world can actually show.  When a
# decision offers none of them every candidate is searched as the same null
# move, so the worlds cancel out and the ranking is the bias table alone —
# which is Stage 1's answer, reached at Stage 4 prices.  Better to say so up
# front and hand the decision back.
_MODELLED_ACTIONS = (
    MovePiece, Castle, SummonMonster, DismissMonster, ActivateRitual,
)


class SampledBias:
    """
    What a *searched* world still cannot show.

    Stage 1's biases assume nothing has been searched, so reusing them here
    would count the same thing twice: the search already sees the Monster a
    Summon puts on the board, the material a Ritual eats, and the piece a
    Dismiss gives up.  What survives is the part that never touches the
    board — a card leaving the hand, a Ritual slot being spent for good.

    Anything the world model does *not* apply keeps its full Stage 1
    estimate instead (see ``MonteCarloBot._sampled_bias``); there is
    nothing to double count when the search sees nothing.
    """

    SUMMON = -0.30      # the card leaves the hand (README §46 card advantage)
    DISMISS = -1.00     # the card comes back, but undoing a Summon rarely pays
    RITUAL = 1.00       # a one-shot payoff; the board shows the Monster, not
                        # the fact that the slot is now permanently spent


@dataclass(frozen=True)
class MonteCarloLimits:
    """
    The budget for one sampled decision.

    ``samples`` / ``depth``             — worlds drawn, and plies searched
                                          inside each, for a PREPARATION
                                          decision.
    ``chess_samples`` / ``chess_depth`` — the same pair for a CHESS
                                          decision, where the board is
                                          public and depth buys more than
                                          breadth does.
    ``max_candidates``                  — widest root fan-out kept; the rest
                                          are cut by the Stage 1 ordering
                                          before any world is drawn.  A
                                          PREPARATION turn can offer forty
                                          Summons, and most of them differ
                                          only in which Pawn is used.
    ``max_nodes`` / ``max_seconds``     — hard ceilings across all worlds.
    ``halving``                         — successive halving between rounds.
    ``sampled_summons``                 — how many Monsters a sampled hand is
                                          allowed to put on the board.  One,
                                          because the engine allows one major
                                          preparation action per turn
                                          (``rules._require_no_prep_used``).

    Reproducibility matches Stage 2's: identical for a given seed and node
    budget; the wall-clock ceiling is not reproducible by nature, so pin
    ``max_seconds`` high and drive an experiment with ``max_nodes``.
    """

    samples: int = 16
    depth: int = 2
    chess_samples: int = 8
    chess_depth: int = 3
    max_candidates: int = 10
    max_nodes: int = 20000
    max_seconds: float = 1.5
    quiescence_depth: int = 2
    halving: bool = True
    sampled_summons: int = 1


@dataclass
class MonteCarloStats:
    """What the last sampled decision cost — telemetry, tuning and tests."""

    worlds: int = 0         # determinizations actually drawn
    evaluations: int = 0    # (candidate, world) pairs searched
    nodes: int = 0
    elapsed: float = 0.0
    aborted: bool = False   # budget ran out before the sample plan finished
    candidates: int = 0     # root actions considered (after the fan-out cut)
    best_score: float = 0.0
    favorable: float = 0.0  # fraction of worlds the chosen action beat passing in
                            # — README §49's "63 % favorable"


@dataclass
class _Candidate:
    """One root action and its running tally across the worlds drawn so far."""

    action: "Action"
    bias: float
    total: float = 0.0
    worlds: int = 0
    favorable: int = 0

    @property
    def mean(self) -> float:
        """Expected outcome: the average across worlds, plus the flat bias."""
        if self.worlds == 0:
            return self.bias
        return self.bias + self.total / self.worlds

    def record(self, value: float, baseline: float) -> None:
        self.total += value
        self.worlds += 1
        # Favorable means "better than passing", so the bias belongs in the
        # comparison: an action the board model does not apply is searched
        # AS a pass, and its whole case for existing is its bias.
        if value + self.bias > baseline + 1e-9:
            self.favorable += 1


@dataclass
class _World:
    """A sampled world, set up and ready to be searched."""

    searcher: Searcher
    determinization: Determinization
    summons: tuple[tuple["Position", str], ...] = field(default_factory=tuple)


class MonteCarloBot(SearchBot):
    """
    A sampling opponent (README §49).

    ``seed``      — the bot's own RNG.  Drives both the world sampler and
                    tie-breaking, and is independent of the game RNG.
    ``weights``   — the README §46 evaluation weights, shared with Stage 1
                    and Stage 2.
    ``registry``  — the public card definitions.  **Required in practice**:
                    with no registry there is no pool to sample worlds from,
                    and the bot degrades to Stage 2 behaviour.
    ``limits``    — the Stage 2 search budget, used for the phases Stage 4
                    does not sample.
    ``mc_limits`` — the Stage 4 sampling budget.
    """

    def __init__(
        self,
        seed: int = 0,
        weights: EvalWeights = DEFAULT_WEIGHTS,
        registry: "CardRegistry | None" = None,
        limits: SearchLimits = SearchLimits(),
        mc_limits: MonteCarloLimits = MonteCarloLimits(),
        biases: "type[ActionBias] | ActionBias" = ActionBias,
    ) -> None:
        super().__init__(
            seed=seed, weights=weights, registry=registry, limits=limits,
            biases=biases,
        )
        self._mc_limits = mc_limits
        self._last_sampling = MonteCarloStats()

    # ── PlayerController ──────────────────────────────────────────────────

    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        if not legal_actions:
            raise ValueError(
                f"MonteCarloBot received an empty legal action list "
                f"(player={self._player_id!r}, phase={observation.phase!r}). "
                "This is an engine bug."
            )
        self._note_turn(observation)
        if len(legal_actions) == 1:
            self._remember(legal_actions[0])
            return legal_actions[0]

        if (
            observation.phase in _SAMPLED_PHASES
            and self._registry is not None
            and any(isinstance(a, _MODELLED_ACTIONS) for a in legal_actions)
        ):
            determinizer = Determinizer(observation, self._registry, self._rng)
            if determinizer.is_informative:
                return self._choose_by_sampling(
                    observation, legal_actions, determinizer
                )

        # Nothing hidden left to guess at, nothing a world could change, or a
        # phase Stage 4 does not model: Stage 2 searches the board, Stage 1
        # handles the rest.
        return super().choose_action(observation, legal_actions)

    # ── Sampled decision ──────────────────────────────────────────────────

    def _choose_by_sampling(
        self,
        obs: "Observation",
        legal_actions: "list[Action]",
        determinizer: Determinizer,
    ) -> "Action":
        limits = self._mc_limits
        started = time.monotonic()
        deadline = started + limits.max_seconds
        preparation = obs.phase == Phase.PREPARATION
        target_worlds = limits.samples if preparation else limits.chess_samples
        depth = limits.depth if preparation else limits.chess_depth

        ctx = EvalContext(
            obs=obs, weights=self._weights, registry=self._registry
        )
        # The Stage 1 estimate is the root ordering AND the fan-out cut: a
        # world costs a search per surviving candidate, so the field has to
        # be narrowed before the first one is drawn.
        ordered = sorted(
            legal_actions, key=lambda a: self.score_action(a, ctx), reverse=True
        )[: max(2, limits.max_candidates)]
        candidates = [
            _Candidate(action=a, bias=self._sampled_bias(a, ctx)) for a in ordered
        ]

        # A candidate Summon or Ritual puts a Monster on a board that may
        # not have one yet, and the move generator has to be told before it
        # starts caching its answer (see Searcher.__init__).
        monsters_expected = any(
            isinstance(c.action, (SummonMonster, ActivateRitual)) for c in candidates
        )

        stats = MonteCarloStats(candidates=len(candidates))
        survivors = list(candidates)
        # Never eliminate on the evidence of a single world — that is a coin
        # toss wearing a bandit algorithm's clothes.  Two worlds per round is
        # the floor, so a small budget simply runs fewer rounds.
        rounds = min(
            self._round_count(len(survivors), limits.halving),
            max(1, target_worlds // 2),
        )

        for round_index in range(rounds):
            # Spread what is left of the world budget over the rounds that
            # are left, so a count that does not divide evenly spends its
            # remainder on the last round instead of losing it.
            rounds_left = rounds - round_index
            this_round = max(
                1,
                -(-(target_worlds - stats.worlds) // rounds_left),  # ceil div
            )
            for _ in range(this_round):
                if stats.worlds >= target_worlds:
                    break
                world = self._build_world(
                    obs,
                    # Always start from the world Stage 2 would have searched.
                    NULL_WORLD if stats.worlds == 0 else determinizer.sample(),
                    depth,
                    deadline,
                    monsters_expected,
                )
                # "What happens if I do nothing here?" — the reference every
                # candidate in this world is measured against.
                baseline = self._pass_value(world, obs, depth)
                values = [
                    self._value_in_world(c.action, world, obs, depth)
                    for c in survivors
                ]
                stats.nodes += world.searcher.stats.nodes

                if world.searcher.stats.aborted:
                    # A world that ran out of budget part-way through has
                    # values for some candidates and stand-pat guesses for
                    # the rest, which is exactly the kind of unpaired
                    # comparison the shared-world design exists to avoid.
                    # Throw it away whole.
                    stats.aborted = True
                    break

                for candidate, value in zip(survivors, values):
                    candidate.record(value, baseline)
                stats.evaluations += len(values)
                stats.worlds += 1

                if time.monotonic() >= deadline or stats.nodes >= limits.max_nodes:
                    stats.aborted = True
                    break

            if stats.aborted or len(survivors) <= 1 or round_index == rounds - 1:
                break
            # Successive halving: drop the worse half.  The survivors keep
            # their tallies, so the field that is still in contention is the
            # field measured on the most worlds.
            survivors.sort(key=lambda c: c.mean, reverse=True)
            survivors = survivors[: max(1, len(survivors) // 2)]
            if len(survivors) == 1:
                # Nothing left to tell apart; more worlds cannot change the
                # answer, so stop rather than spend the rest of the budget
                # measuring one candidate against itself.
                break

        if stats.worlds == 0:
            # Not one world completed inside the budget, so there is nothing
            # to average and the biases alone are not a ranking.  Stage 2
            # still has a board to search, and it is strictly better than
            # guessing — README §47 remains the floor Stage 4 stands on.
            self._last_sampling = stats
            _log.debug(
                "MONTECARLO player=%s phase=%s no world completed inside the "
                "budget (%d nodes, %.3fs) — falling back to Stage 2",
                self._player_id, obs.phase.value, stats.nodes,
                time.monotonic() - started,
            )
            return super().choose_action(obs, legal_actions)

        best = self._pick(survivors)
        stats.elapsed = time.monotonic() - started
        stats.best_score = best.mean
        stats.favorable = (
            best.favorable / best.worlds if best.worlds else 0.0
        )
        self._last_sampling = stats
        self._last_score = best.mean
        self._remember(best.action)

        _log.debug(
            "MONTECARLO player=%s phase=%s candidates=%d worlds=%d evals=%d "
            "nodes=%d %.3fs%s chose=%s score=%.3f favorable=%.0f%%",
            self._player_id, obs.phase.value, stats.candidates, stats.worlds,
            stats.evaluations, stats.nodes, stats.elapsed,
            " (budget)" if stats.aborted else "",
            type(best.action).__name__, stats.best_score, stats.favorable * 100.0,
        )
        return best.action

    def _pick(self, survivors: "list[_Candidate]") -> _Candidate:
        """Best expected outcome; ties broken by the bot's own RNG."""
        best_mean = max(c.mean for c in survivors)
        tied = [c for c in survivors if c.mean > best_mean - 1e-9]
        return tied[0] if len(tied) == 1 else self._rng.choice(tied)

    @staticmethod
    def _round_count(candidates: int, halving: bool) -> int:
        """
        How many rounds ``candidates`` supports — the number of halvings
        that take the field down to one, so the *last* round still has a
        real comparison in it and no round is spent on a single survivor.

        Two candidates take one round (there is nothing to eliminate before
        the comparison that decides the move), ten take three: 10 → 5 → 2.
        """
        if not halving or candidates <= 2:
            return 1
        rounds = 0
        while candidates > 1:
            candidates //= 2
            rounds += 1
        return max(1, rounds)

    # ── Worlds ────────────────────────────────────────────────────────────

    def _build_world(
        self,
        obs: "Observation",
        determinization: Determinization,
        depth: int,
        deadline: float,
        monsters_expected: bool,
    ) -> _World:
        """
        Turn a sampled hidden state into a board that can be searched.

        The only part of a hidden hand that shows up on a *board* is the
        Monster it can summon, so that is what is applied: the sampled hand
        is walked, and the Monster that can reach the most useful vessel is
        put there.  Everything else the world contains (Rituals, Kings) is
        carried on the ``Determinization`` for the record — it shapes no
        board fact the search can read, and inventing one would be fiction
        rather than sampling.
        """
        board = board_for_search(obs)
        summons = self._plan_summons(obs, determinization)
        for pos, card_id in summons:
            unit = board.get_unit(pos)
            if unit is not None:
                unit.monster_id = card_id

        limits = self._mc_limits
        searcher = Searcher(
            board=board,
            me=obs.player_id,
            registry=self._registry,
            weights=self._weights,
            limits=SearchLimits(
                max_depth=depth,
                # The decision's whole ceiling, not a slice of it.  A world
                # that runs out of budget part-way through is thrown away
                # (see ``_choose_by_sampling``), so slicing the nodes per
                # world buys nothing and costs everything: too thin a slice
                # and every world is discarded, leaving the decision with no
                # samples at all.  What stops the loop is the running total,
                # checked once a world is complete.
                max_nodes=limits.max_nodes,
                max_seconds=max(0.01, deadline - time.monotonic()),
                quiescence_depth=limits.quiescence_depth,
            ),
            hazards=Hazards.from_observation(obs),
            monsters_expected=monsters_expected or bool(summons),
        )
        searcher.start()
        return _World(
            searcher=searcher,
            determinization=determinization,
            summons=summons,
        )

    def _plan_summons(
        self, obs: "Observation", determinization: Determinization
    ) -> "tuple[tuple[Position, str], ...]":
        """
        What the sampled hand most plausibly puts on the board next turn.

        Constrained the way the engine constrains a real Summon: the card
        must be a Monster, the vessel must be one it supports, the vessel
        must not already carry a Monster or be a King, and it must stand in
        its owner's own Territory (README §13 — public, and a hard
        requirement in ``rules._legal_preparation_actions``).  Among the
        legal vessels the one nearest the observer's King is chosen: a
        sampled opponent that summons where it hurts is the opponent worth
        preparing for.
        """
        registry = self._registry
        if registry is None or not determinization.opponent_hand:
            return ()

        from game.cards.card import MonsterCard

        opponent = "black" if obs.player_id == "white" else "white"
        territory = set(obs.opponent_territory)
        vessels = [
            (u.position, u.piece_type)
            for u in obs.board.units
            if u.owner == opponent
            and u.monster_id is None
            and u.piece_type != "king"
            and u.position in territory
        ]
        if not vessels:
            return ()

        my_king = next(
            (
                u.position for u in obs.board.units
                if u.owner == obs.player_id and u.piece_type == "king"
            ),
            None,
        )

        planned: list[tuple["Position", str]] = []
        taken: set["Position"] = set()
        for card_id in determinization.opponent_hand:
            if len(planned) >= self._mc_limits.sampled_summons:
                break
            try:
                card = registry.get(card_id)
            except (KeyError, AttributeError):
                continue
            if not isinstance(card, MonsterCard):
                continue
            options = [
                pos for pos, piece_type in vessels
                if pos not in taken and card.supports_vessel(piece_type)
            ]
            if not options:
                continue
            if my_king is not None:
                options.sort(key=lambda p: chebyshev(p, my_king))
            planned.append((options[0], card_id))
            taken.add(options[0])

        return tuple(planned)

    # ── Valuing one candidate in one world ────────────────────────────────

    def _next_to_move(self, obs: "Observation") -> str:
        """
        Who moves once this decision is taken.

        A preparation action does not pass the turn — EndPreparation moves
        the same player on to CHESS — so the bot is still to move.  A chess
        action hands the position over.
        """
        if obs.phase == Phase.PREPARATION:
            return obs.player_id
        return "black" if obs.player_id == "white" else "white"

    def _pass_value(self, world: _World, obs: "Observation", depth: int) -> float:
        """Value of this world with no action taken — the null move."""
        return world.searcher.value_to_move(self._next_to_move(obs), depth - 1)

    def _value_in_world(
        self,
        action: "Action",
        world: _World,
        obs: "Observation",
        depth: int,
    ) -> float:
        """
        Search ``action``'s consequence inside one sampled world.

        Full alpha-beta windows on purpose: the *value* is what gets
        averaged, so a fail-soft bound from a narrowed root window would
        poison the mean.  Stage 2's root pruning is safe because it only
        ever needs the argmax; Stage 4 needs the numbers.
        """
        searcher = world.searcher
        mover = self._next_to_move(obs)

        if isinstance(action, MovePiece):
            undo = searcher.make(action.source, action.target)
            value = searcher.value_to_move(mover, depth - 1)
            searcher.unmake(action.source, action.target, undo)
            return value

        if isinstance(action, Castle):
            undo = searcher.make_castle(action.player_id, action.side)
            value = searcher.value_to_move(mover, depth - 1)
            searcher.unmake_castle(undo)
            return value

        undo_prep = self._apply_preparation(searcher, action)
        value = searcher.value_to_move(mover, depth - 1)
        if undo_prep is not None:
            self._undo_preparation(searcher, undo_prep)
        return value

    # ── The board model for preparation actions ───────────────────────────

    def _apply_preparation(self, searcher: Searcher, action: "Action"):
        """
        Apply the board consequence of a preparation action, if it has one.

        Modelled:
            SummonMonster  — the vessel gains the Monster.
            DismissMonster — the vessel loses it.
            ActivateRitual — every sacrifice except the last leaves the
                             board; the last is the Ritual's Vessel and
                             gains the summoned Monster (README §14, and
                             ``cards.card.RitualCard``).

        Not modelled — the board does not change, so the candidate is
        searched as a null move and keeps its full Stage 1 estimate as its
        bias: Traps and Spells (their effect is a rules interaction, not a
        piece move), Constructions, Coronations, Successions, Recompose,
        Mercenary, EndPreparation.

        Returns an undo token, or None when there was nothing to apply.
        """
        if isinstance(action, SummonMonster):
            previous = searcher.set_monster(action.vessel_position, action.card_id)
            return ("monster", action.vessel_position, previous)

        if isinstance(action, DismissMonster):
            previous = searcher.set_monster(action.unit_position, None)
            return ("monster", action.unit_position, previous)

        if isinstance(action, ActivateRitual):
            positions = list(getattr(action, "sacrifice_positions", ()) or ())
            if not positions:
                return None
            vessel = positions[-1]
            lifted = [(pos, searcher.lift(pos)) for pos in positions[:-1]]
            summoned = ""
            if self._registry is not None:
                try:
                    summoned = getattr(
                        self._registry.get(action.ritual_id), "summon_monster_id", ""
                    )
                except (KeyError, AttributeError):
                    summoned = ""
            previous = searcher.set_monster(vessel, summoned or None)
            return ("ritual", vessel, previous, lifted)

        return None

    @staticmethod
    def _undo_preparation(searcher: Searcher, token) -> None:
        kind = token[0]
        if kind == "monster":
            _, pos, previous = token
            searcher.set_monster(pos, previous)
            return
        _, vessel, previous, lifted = token
        searcher.set_monster(vessel, previous)
        for pos, unit in lifted:
            searcher.restore(pos, unit)

    # ── Bias ──────────────────────────────────────────────────────────────

    def _sampled_bias(self, action: "Action", ctx: EvalContext) -> float:
        """
        What the sampled search cannot see, per action type.

        Chess actions reuse Stage 2's own rule (``SearchBot._root_bias``) —
        the reasoning is identical, and it already avoids counting a
        capture twice.  For the preparation actions Stage 4 *does* model,
        only the off-board remainder is added.  Everything else falls
        through to the full Stage 1 estimate, because for those the search
        genuinely sees nothing.
        """
        if isinstance(action, (MovePiece, Castle, AttackBuilding, EndTurn)):
            return self._root_bias(action, ctx)
        if isinstance(action, SummonMonster):
            return SampledBias.SUMMON
        if isinstance(action, DismissMonster):
            return SampledBias.DISMISS
        if isinstance(action, ActivateRitual):
            # The sacrificed material is already off the searched board, so
            # Stage 1's ``_ritual_cost`` must NOT be added on top of it.
            return SampledBias.RITUAL
        return self.score_action(action, ctx)

    # ── Instrumentation ───────────────────────────────────────────────────

    @property
    def last_sampling(self) -> MonteCarloStats:
        """Cost and shape of the most recent sampled decision (README §42)."""
        return self._last_sampling

    @property
    def mc_limits(self) -> MonteCarloLimits:
        return self._mc_limits

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MonteCarloBot(player_id={self._player_id!r}, "
            f"samples={self._mc_limits.samples}, depth={self._mc_limits.depth})"
        )
