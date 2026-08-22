"""
HeuristicBot — AI Stage 1 (README §46)
========================================

    "Evaluate each legal action using a weighted state evaluation ...
     The bot selects the action producing the best immediate score.
     Deliverable: a competent non-searching opponent."

How it works
------------
The bot is handed an Observation and a list of legal actions.  It has no
simulator — a controller cannot execute a candidate action to look at the
resulting state (that is AI Stage 2's job, README §47) — so it scores each
action as an *estimated delta* on top of the current position:

    score(action) = position_score + delta(action)

``position_score`` is ``evaluation.evaluate_observation`` — the full README
§46 weighted category sum.  It is identical for every candidate in one
decision, so it never changes the ranking; it is included because §46 asks
for a state evaluation and because it makes a logged score comparable
across turns.

``delta(action)`` is where the ranking happens.  Each action type gets an
estimate built from the same weights: what it captures, what it exposes,
what strategic system it advances.  Threat awareness comes from
reconstructing the public board and asking ``game.chess.movement`` what
each side attacks — so the bot takes free material, prefers not to hang
pieces, and walks around enemy Traps and hazard zones.

Information rules (README §44)
------------------------------
The bot reads its Observation and the legal action list.  Nothing else.
The optional ``registry`` is the public card rulebook (definitions, not
match state) and everything degrades gracefully without it.

Determinism
-----------
Ties are broken with the bot's own seeded RNG, independent of the game
RNG, so two HeuristicBots with different seeds do not lock into the same
repeated line.  A short memory of its own recent moves discourages
shuffling one piece back and forth forever, and a per-turn memory of the
Monster abilities it has already fired stops it re-activating the same
one instead of playing chess (activating an ability does not consume the
chess move, and several abilities stay legal after use).

Usage:
    from game.ai.heuristic_bot import HeuristicBot

    bot = HeuristicBot(seed=1, registry=registry)
    bot.player_id = "black"
    action = bot.choose_action(observation, legal_actions)
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from game.ai.controller import PlayerController
from game.ai.evaluation import (
    DEFAULT_WEIGHTS,
    EvalContext,
    EvalWeights,
    centrality,
    chebyshev,
    evaluate_observation,
    piece_value,
)
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateRitual,
    ActivateSpell,
    ActivateTrap,
    AttackBuilding,
    Castle,
    ChangeKing,
    CoronateKing,
    DeclareMercenary,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    FinalDuelAction,
    MovePiece,
    PlaceMercenaryPiece,
    PlaceTrap,
    PromotePawn,
    ReorderTopDeck,
    RepositionUnit,
    SelectMercenaryCards,
    SelectRecomposeCards,
    StartConstruction,
    SummonMonster,
)
from game.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.actions import Action
    from game.core.observation import Observation

_log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Action-level tuning
# ─────────────────────────────────────────────────────────────────────────────

class ActionBias:
    """
    Flat biases applied per action *type*, on top of the material and
    positional deltas the scorer computes.

    These express strategic policy, not board maths: "a Coronation is free
    value, take it early", "Recompose is a last resort", "never dismiss a
    Monster you just paid for".
    """

    END_PREPARATION = 0.0        # the neutral baseline every other action beats
    END_TURN = 0.0

    SUMMON = 1.2                 # a Vessel becomes a stronger unit
    SUMMON_KING_ADJACENT = 0.6   # ... near the enemy King, better still
    DISMISS = -3.0               # undoing your own summon is almost never right

    SPELL = 0.8
    TRAP = 0.6
    ACTIVATE_TRAP = 1.0
    MONSTER_ABILITY = 0.9
    ABILITY_REPEAT = -6.0        # ... but only once per turn, per Monster

    RITUAL = 6.0                 # the payoff the whole Ritual system exists for
    CONSTRUCTION = 1.5
    CORONATION = 5.0             # free action, pure upside (README §17)
    SUCCESSION = -1.5            # costs a piece and/or a Building (README §19.1)

    RECOMPOSE = -1.0             # random, and you don't know N before committing
    RECOMPOSE_EMPTY_HAND = 2.5   # ... unless the hand is genuinely dead
    MERCENARY = 0.5

    CASTLE = 1.5                 # King safety, cheaply
    PROMOTION = 0.0              # handled by the promoted piece's value
    REPETITION = -0.8            # discourage shuffling the same piece forever

    # Final Duel (README §32)
    DUEL_STRIKE = 4.0
    DUEL_SUPPORT = 2.0
    DUEL_BUILDING = 1.5
    DUEL_KING_POLICY = 1.5
    DUEL_ADVANCE = 0.5


_PROMOTION_VALUE = {"queen": 9.0, "rook": 5.0, "bishop": 3.25, "knight": 3.0}


class HeuristicBot(PlayerController):
    """
    A competent non-searching opponent (README §46).

    ``seed``     — the bot's own RNG, for tie-breaking only.  Independent of
                   the game's DeterministicRNG.
    ``weights``  — the README §46 category weights; swap in an
                   ``EvalWeights`` to build an AI personality (README §51).
    ``registry`` — optional public card definitions.  Improves Monster and
                   hand-quality scoring; never required.
    """

    _HISTORY_LEN = 6

    def __init__(
        self,
        seed: int = 0,
        weights: EvalWeights = DEFAULT_WEIGHTS,
        registry: "CardRegistry | None" = None,
    ) -> None:
        self._player_id: str = "?"
        self._rng = random.Random(seed)
        self._weights = weights
        self._registry = registry
        # The bot's own memory of the moves IT made — not game state.
        self._recent_moves: list[tuple[object, object]] = []
        # Monster abilities already fired this turn, same idea.  Activating
        # one does NOT consume the chess move, and several of them (e.g.
        # inspect_top_deck) stay legal after they have been used, so a flat
        # positive bias alone would have the bot re-firing the same ability
        # for the rest of the game instead of ever playing chess.
        self._used_abilities: set[tuple[object, str]] = set()
        self._ability_turn: int = -1
        self._last_score: float = 0.0

    # ── PlayerController ──────────────────────────────────────────────────

    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        if not legal_actions:
            raise ValueError(
                f"HeuristicBot received an empty legal action list "
                f"(player={self._player_id!r}, phase={observation.phase!r}). "
                "This is an engine bug."
            )
        self._note_turn(observation)
        if len(legal_actions) == 1:
            self._remember(legal_actions[0])
            return legal_actions[0]

        ctx = EvalContext(
            obs=observation, weights=self._weights, registry=self._registry
        )
        position_score = evaluate_observation(
            observation, self._weights, self._registry, ctx
        )

        best: list["Action"] = []
        best_score = float("-inf")
        for action in legal_actions:
            score = position_score + self.score_action(action, ctx)
            if score > best_score + 1e-9:
                best_score, best = score, [action]
            elif score > best_score - 1e-9:
                best.append(action)

        chosen = best[0] if len(best) == 1 else self._rng.choice(best)
        self._last_score = best_score
        self._remember(chosen)
        _log.debug(
            "HEURISTIC player=%s phase=%s candidates=%d chose=%s score=%.3f",
            self._player_id, observation.phase.value, len(legal_actions),
            type(chosen).__name__, best_score,
        )
        return chosen

    # ── Scoring ───────────────────────────────────────────────────────────

    def score_action(self, action: "Action", ctx: EvalContext) -> float:
        """
        Estimated change in the README §46 evaluation if ``action`` is played.

        Public so the weights can be inspected and tuned from tests and
        tooling; ``ctx`` is the per-decision cache from ``EvalContext``.
        """
        w = ctx.weights
        b = ActionBias

        if isinstance(action, MovePiece):
            return self._score_move(action, ctx)

        if isinstance(action, AttackBuilding):
            return self._score_siege(action, ctx)

        if isinstance(action, Castle):
            return b.CASTLE

        if isinstance(action, SummonMonster):
            return self._score_summon(action, ctx)

        if isinstance(action, DismissMonster):
            return b.DISMISS

        if isinstance(action, ActivateSpell):
            return b.SPELL + self._target_bias(getattr(action, "target", None), ctx)

        if isinstance(action, PlaceTrap):
            return b.TRAP + self._trap_placement_bias(action, ctx)

        if isinstance(action, ActivateTrap):
            return b.ACTIVATE_TRAP

        if isinstance(action, ActivateMonsterAbility):
            if (action.unit_position, action.ability_id) in self._used_abilities:
                return b.MONSTER_ABILITY + b.ABILITY_REPEAT
            return b.MONSTER_ABILITY

        if isinstance(action, ActivateRitual):
            return b.RITUAL + self._ritual_cost(action, ctx)

        if isinstance(action, StartConstruction):
            return b.CONSTRUCTION

        if isinstance(action, CoronateKing):
            return b.CORONATION

        if isinstance(action, ChangeKing):
            return b.SUCCESSION

        if isinstance(action, DeclareRecompose):
            return self._score_recompose(ctx)

        if isinstance(action, SelectRecomposeCards):
            # Follow-up: the count is forced; any selection is equivalent.
            return 0.0

        if isinstance(action, DeclareMercenary):
            return b.MERCENARY + w.material * piece_value(action.piece_type)

        if isinstance(action, SelectMercenaryCards):
            return 0.0

        if isinstance(action, PlaceMercenaryPiece):
            return w.board_control_centrality * centrality(action.position) * 4.0

        if isinstance(action, PromotePawn):
            return b.PROMOTION + w.material * _PROMOTION_VALUE.get(
                action.piece_type, 1.0
            )

        if isinstance(action, RepositionUnit):
            return self._square_risk(action.to_position, ctx)

        if isinstance(action, DiscardCard):
            return self._score_discard(action, ctx)

        if isinstance(action, ReorderTopDeck):
            return 0.0

        if isinstance(action, FinalDuelAction):
            return self._score_duel(action)

        if isinstance(action, EndPreparation):
            return b.END_PREPARATION

        if isinstance(action, EndTurn):
            return b.END_TURN

        return 0.0

    # ── Chess ─────────────────────────────────────────────────────────────

    def _score_move(self, action: MovePiece, ctx: EvalContext) -> float:
        w = ctx.weights
        score = 0.0

        mover = ctx.unit_at(action.source)
        mover_value = piece_value(mover.piece_type) if mover is not None else 1.0

        # Capture — the single biggest term a greedy bot has.
        victim = ctx.unit_at(action.target)
        if victim is not None and victim.owner != ctx.me:
            score += w.material * piece_value(victim.piece_type)
            if victim.monster_id is not None:
                score += w.monster_unit
            if victim.piece_type == "king":
                # Capturing the King triggers the Final Duel (README §22/25),
                # it does not win outright — but it is always worth going for.
                score += w.duel_pressure * 5.0

        # Positional: centre, and pressure on the enemy King.
        score += w.board_control_centrality * (
            centrality(action.target) - centrality(action.source)
        )
        if ctx.enemy_king is not None:
            closer = (
                chebyshev(action.source, ctx.enemy_king)
                - chebyshev(action.target, ctx.enemy_king)
            )
            score += w.enemy_king_attacker * 0.4 * closer

        # Pawn advancement (README §10 — promotion is a real plan).
        if mover is not None and mover.piece_type == "pawn":
            direction = 1 if ctx.me == "white" else -1
            score += w.pawn_advance * (action.target.rank - action.source.rank) * direction

        # Safety: do not walk into the enemy's threat map for free.
        score += self._square_risk(action.target, ctx, mover_value)
        # ... and getting OUT of an attacked square is worth something.
        if action.source in ctx.enemy_attacks and action.target not in ctx.enemy_attacks:
            score += w.hanging_penalty * mover_value * 0.5

        if (action.source, action.target) in self._recent_moves or \
                (action.target, action.source) in self._recent_moves:
            score += ActionBias.REPETITION

        return score

    def _square_risk(
        self,
        target: object,
        ctx: EvalContext,
        mover_value: float = 1.0,
    ) -> float:
        """Penalty for occupying ``target``: enemy threats, Traps, hazards."""
        w = ctx.weights
        risk = 0.0

        if target in ctx.enemy_attacks:
            defended = target in ctx.my_attacks
            factor = w.defended_discount if defended else 1.0
            risk -= w.hanging_penalty * mover_value * factor

        for trap in ctx.obs.board.trap_locations:
            if getattr(trap, "owner", None) == ctx.me:
                continue
            if chebyshev(target, trap.position) <= getattr(trap, "radius", 1):
                risk -= w.trap_penalty
                break

        for effect in ctx.obs.board.square_effects:
            if effect.position != target:
                continue
            if effect.owner == ctx.me:
                continue
            risk -= w.hazard_penalty

        return risk

    def _score_siege(self, action: AttackBuilding, ctx: EvalContext) -> float:
        """
        Stage 13 siege (README §11.2).  Worth the chess move when the
        Building is close to falling — Buildings are hard terrain, so
        removing one opens the position as well as denying its aura.
        """
        w = ctx.weights
        for building in ctx.obs.board.building_locations:
            if getattr(building, "position", None) != action.target:
                continue
            if getattr(building, "owner", None) == ctx.me:
                return -w.building_complete       # never besiege your own
            integrity = max(getattr(building, "integrity", 1), 1)
            # A one-hit kill is worth the whole Building; a long grind less so.
            return w.building_complete / integrity + w.building_integrity
        return 0.0

    # ── Preparation ───────────────────────────────────────────────────────

    def _score_summon(self, action: SummonMonster, ctx: EvalContext) -> float:
        score = ActionBias.SUMMON
        w = ctx.weights

        if self._registry is not None:
            try:
                card = self._registry.get(action.card_id)
                score += w.monster_effect * len(getattr(card, "effects", ()))
            except (KeyError, AttributeError):
                pass

        # Prefer a Vessel that is safe and useful: a Pawn is the cheap
        # Vessel (README §10), a hanging Queen is a terrible one.
        vessel = ctx.unit_at(action.vessel_position)
        if vessel is not None:
            if action.vessel_position in ctx.enemy_attacks:
                score -= w.hanging_penalty * piece_value(vessel.piece_type)
            if vessel.piece_type == "pawn":
                score += w.pawn_base

        if ctx.enemy_king is not None:
            if chebyshev(action.vessel_position, ctx.enemy_king) <= w.enemy_king_radius:
                score += ActionBias.SUMMON_KING_ADJACENT

        return score

    def _ritual_cost(self, action: ActivateRitual, ctx: EvalContext) -> float:
        """The Ritual bonus is flat; the material it eats is not."""
        cost = 0.0
        for pos in getattr(action, "sacrifice_positions", ()) or ():
            unit = ctx.unit_at(pos)
            if unit is not None:
                cost -= ctx.weights.material * piece_value(unit.piece_type) * 0.5
        return cost

    def _trap_placement_bias(self, action: PlaceTrap, ctx: EvalContext) -> float:
        """Traps are public (README §7.1) — put them where they deter."""
        w = ctx.weights
        score = w.board_control_centrality * centrality(action.position)
        if ctx.my_king is not None:
            if chebyshev(action.position, ctx.my_king) <= w.king_safety_radius:
                score += w.king_safety_attacker
        return score

    def _target_bias(self, target: object, ctx: EvalContext) -> float:
        """A Spell aimed at something specific beats one fired at nothing."""
        from game.chess.pieces import Position

        if isinstance(target, Position):
            unit = ctx.unit_at(target)
            if unit is not None and unit.owner != ctx.me:
                return ctx.weights.material * piece_value(unit.piece_type) * 0.15
            return ctx.weights.board_control_centrality * centrality(target)
        return 0.0

    def _score_recompose(self, ctx: EvalContext) -> float:
        """
        README §9: Recompose trades an unknown number of cards for fresh
        ones.  Only attractive when the hand cannot do anything — which the
        hand-quality term already measures.
        """
        obs = ctx.obs
        if not obs.own_hand:
            return ActionBias.RECOMPOSE_EMPTY_HAND
        from game.ai.evaluation import hand_quality

        quality = hand_quality(obs, ctx, ctx.weights, self._registry)
        if quality <= ctx.weights.hand_playable:
            return ActionBias.RECOMPOSE_EMPTY_HAND
        return ActionBias.RECOMPOSE

    def _score_discard(self, action: DiscardCard, ctx: EvalContext) -> float:
        """Forced discard (README §5): shed the least useful card."""
        if self._registry is None:
            return 0.0
        from game.cards.card import MonsterCard

        try:
            card = self._registry.get(action.card_id)
        except (KeyError, AttributeError):
            return 0.0
        if isinstance(card, MonsterCard):
            own_vessels = {
                u.piece_type for u in ctx.obs.board.units
                if u.owner == ctx.me and u.monster_id is None
            }
            if not own_vessels.intersection(card.supported_vessels):
                return ctx.weights.hand_summonable      # dead card — pitch it
            return -ctx.weights.hand_summonable
        return -ctx.weights.hand_playable

    # ── Final Duel (README §32) ───────────────────────────────────────────

    def _score_duel(self, action: FinalDuelAction) -> float:
        kind = getattr(action, "duel_action_type", "")
        return {
            "strike": ActionBias.DUEL_STRIKE,
            "support": ActionBias.DUEL_SUPPORT,
            "building": ActionBias.DUEL_BUILDING,
            "king_policy": ActionBias.DUEL_KING_POLICY,
            "advance": ActionBias.DUEL_ADVANCE,
        }.get(kind, 0.0)

    # ── Memory ────────────────────────────────────────────────────────────

    def _note_turn(self, observation: "Observation") -> None:
        """A new turn wipes the per-turn ability memory."""
        if observation.turn_number != self._ability_turn:
            self._ability_turn = observation.turn_number
            self._used_abilities.clear()

    def _remember(self, action: "Action") -> None:
        if isinstance(action, MovePiece):
            self._recent_moves.append((action.source, action.target))
            if len(self._recent_moves) > self._HISTORY_LEN:
                self._recent_moves.pop(0)
        elif isinstance(action, ActivateMonsterAbility):
            self._used_abilities.add((action.unit_position, action.ability_id))

    @property
    def last_score(self) -> float:
        """Score of the most recent decision — for logging and tuning."""
        return self._last_score

    def __repr__(self) -> str:  # pragma: no cover
        return f"HeuristicBot(player_id={self._player_id!r})"
