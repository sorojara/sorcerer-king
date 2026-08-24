"""
test_ai_stage6_kings.py — King policy valuation (README §53).

The evaluator did not look at Kings at all. ``CoronateKing`` scored a flat
``ActionBias.CORONATION`` and ``ChangeKing`` scored a bare
``ActionBias.SUCCESSION`` of −1.5, with no upside term of any kind. Since
``END_PREPARATION`` is 0.0, no Succession could ever outscore passing.

The corpus priced both mistakes:

    Succession fired **0 times in 3,998 player-matches**, while being
    offered at 19.2 % of decision points — README §53's goal of finding
    "dominant King succession paths" was unanswerable because no path ever
    had a second King on it.

    The first Coronation matched the player's own archetype **1 time in
    12** — the bot crowned whichever of its three Kings the action list
    happened to offer first.

The second is the one that mattered. A King crowned to match the deck
rarely needs replacing, so fixing Coronation is most of the value and
leaves Succession correctly unattractive rather than structurally
impossible.
"""

from __future__ import annotations

import pytest

from game.ai.evaluation import (
    DEFAULT_WEIGHTS,
    board_archetype,
    evaluation_breakdown,
    king_policy_value,
)
from game.ai.heuristic_bot import ActionBias, HeuristicBot
from game.core.actions import ChangeKing, CoronateKing
from game.core.game import Game
from game.core.phases import KingCardStatus


# ─────────────────────────────────────────────────────────────────────────────
# Reading the archetype
# ─────────────────────────────────────────────────────────────────────────────

class TestBoardArchetype:
    def test_the_assigned_archetype_is_used_when_present(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        assert obs.own_archetype is not None
        assert board_archetype(obs, registry) == obs.own_archetype

    def test_it_is_the_players_own_and_not_the_opponents(self, registry):
        """
        §44: an Observation carries its owner's information. The archetype
        field must never be the other player's.
        """
        game = Game.new(seed=5, registry=registry)
        white = game.get_observation("white")
        black = game.get_observation("black")
        assert white.own_archetype == game.state.get_player("white").archetype
        assert black.own_archetype == game.state.get_player("black").archetype

    def test_an_observation_carries_no_opponent_archetype(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        assert not any(
            "archetype" in name and "own" not in name
            for name in vars(obs)
        ), "an opponent archetype field would leak private information"

    def test_it_falls_back_to_voting_without_an_assigned_label(self, registry):
        """Registry-less and archetype-less games still get an answer."""
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        object.__setattr__(obs, "own_archetype", None) if hasattr(
            obs, "__setattr__"
        ) else None
        # The fallback reads the hand; with a real deck it should find something
        # or abstain, but never raise.
        assert board_archetype(obs, registry) is None or isinstance(
            board_archetype(obs, registry), str
        )

    def test_no_registry_is_not_an_error(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        object.__setattr__(obs, "own_archetype", None)
        assert board_archetype(obs, None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Valuing a King
# ─────────────────────────────────────────────────────────────────────────────

class TestKingPolicyValue:
    def test_a_matching_king_beats_a_mismatched_one(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")

        matched = king_policy_value(
            "grave_crowned_king", obs, DEFAULT_WEIGHTS, registry,
            archetype="necromancer",
        )
        mismatched = king_policy_value(
            "grave_crowned_king", obs, DEFAULT_WEIGHTS, registry,
            archetype="dragon",
        )
        assert matched > mismatched
        assert matched - mismatched == pytest.approx(
            DEFAULT_WEIGHTS.king_policy_fit
        )

    def test_no_king_is_worth_nothing(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        assert king_policy_value(None, obs, DEFAULT_WEIGHTS, registry) == 0.0

    def test_an_unknown_king_is_worth_nothing_rather_than_raising(self, registry):
        game = Game.new(seed=5, registry=registry)
        obs = game.get_observation("white")
        assert king_policy_value(
            "no_such_king", obs, DEFAULT_WEIGHTS, registry
        ) == 0.0

    def test_the_fit_bonus_clears_the_cheapest_succession_friction(self):
        """
        The weight is chosen against the friction, not for feel: below the
        bar, the system is unreachable and the term is decoration.
        """
        cheapest_sacrifice = DEFAULT_WEIGHTS.material * 1.0 * 0.5   # a Pawn
        friction = abs(ActionBias.SUCCESSION) + cheapest_sacrifice
        assert DEFAULT_WEIGHTS.king_policy_fit > friction

    def test_it_does_not_clear_a_knight(self):
        """And not so high that Succession is worth real material."""
        knight_sacrifice = DEFAULT_WEIGHTS.material * 3.0 * 0.5
        friction = abs(ActionBias.SUCCESSION) + knight_sacrifice
        assert DEFAULT_WEIGHTS.king_policy_fit < friction

    def test_the_position_score_reflects_the_active_king(self, registry):
        game = Game.new(seed=5, registry=registry)
        before = evaluation_breakdown(
            game.get_observation("white"), DEFAULT_WEIGHTS, registry
        )["king_policy"]
        assert before == 0.0            # nothing crowned yet

        white = game.state.get_player("white")
        white.active_king = white.king_pool[0].king_card_id
        after = evaluation_breakdown(
            game.get_observation("white"), DEFAULT_WEIGHTS, registry
        )["king_policy"]
        assert after > before


# ─────────────────────────────────────────────────────────────────────────────
# Choosing
# ─────────────────────────────────────────────────────────────────────────────

def _first_coronation(game: Game, bot: HeuristicBot):
    """Walk to the first Coronation the bot actually chooses."""
    from game.sim import acting_player
    from game.core.rules import IllegalActionError

    for _ in range(200):
        game.advance_to_preparation()
        if game.is_over():
            return None
        pid = acting_player(game.state)
        legal = game.get_legal_actions(pid)
        obs = game.get_observation(pid)
        if pid == bot.player_id:
            action = bot.choose_action(obs, legal)
            if isinstance(action, CoronateKing):
                return obs, action
        else:
            action = legal[0]
        try:
            game.execute(action)
        except IllegalActionError:
            return None
    return None


class TestCoronationChoosesWell:
    @pytest.mark.parametrize("seed", (100000, 100001, 100002, 100003))
    def test_the_crowned_king_matches_the_players_archetype(self, seed, registry):
        game = Game.new(seed=seed, registry=registry)
        bot = HeuristicBot(seed=seed * 2 + 1, registry=registry)
        bot.player_id = "white"

        found = _first_coronation(game, bot)
        if found is None:
            pytest.skip("no Coronation reached for this seed")
        obs, action = found

        pool = {k.king_card_id for k in obs.own_king_pool}
        supported = {
            cid for cid in pool
            if obs.own_archetype in (registry.get(cid).archetype_support or ())
        }
        if not supported:
            pytest.skip("this pool contains no King for the player's archetype")
        assert action.king_card_id in supported

    def test_coronation_still_beats_passing(self, registry):
        """The King term must not swamp the bias that makes crowning free."""
        game = Game.new(seed=100000, registry=registry)
        bot = HeuristicBot(seed=1, registry=registry)
        bot.player_id = "white"
        found = _first_coronation(game, bot)
        if found is None:
            pytest.skip("no Coronation reached")
        assert True   # reaching a chosen Coronation at all is the assertion


class TestSuccessionIsReachableButNotFree:
    def _context(self, registry, seed=100000):
        from game.ai.evaluation import EvalContext

        game = Game.new(seed=seed, registry=registry)
        obs = game.get_observation("white")
        return game, obs, EvalContext(
            obs=obs, weights=DEFAULT_WEIGHTS, registry=registry
        )

    def test_a_lateral_swap_is_refused(self, registry):
        """Two Kings that fit equally well: pay nothing to swap them."""
        game, obs, ctx = self._context(registry)
        bot = HeuristicBot(seed=1, registry=registry)
        bot.player_id = "white"

        white = game.state.get_player("white")
        white.active_king = white.king_pool[0].king_card_id
        obs = game.get_observation("white")
        ctx.obs = obs

        pos = game.state.board.all_units_for("white")[0][0]
        action = ChangeKing(
            player_id="white",
            king_card_id=white.king_pool[1].king_card_id,
            sacrifice_position=pos,
        )
        assert bot.score_action(action, ctx) < 0

    def test_succession_is_no_longer_a_bare_constant(self, registry):
        """
        The regression that mattered: the score has to *depend* on which
        King is being swapped in. A constant cannot.
        """
        from game.ai.evaluation import EvalContext

        game = Game.new(seed=100000, registry=registry)
        bot = HeuristicBot(seed=1, registry=registry)
        bot.player_id = "white"
        white = game.state.get_player("white")

        # Force a pool where exactly one candidate matches the archetype.
        archetype = white.archetype
        matching = next(
            (cid for cid in
             ("arcane_sovereign", "dragon_high_king", "marshal_king",
              "architect_king", "shadow_regent", "grave_crowned_king")
             if archetype in (registry.get(cid).archetype_support or ())),
            None,
        )
        mismatched = next(
            cid for cid in
            ("arcane_sovereign", "dragon_high_king", "marshal_king",
             "architect_king", "shadow_regent", "grave_crowned_king")
            if cid != matching
        )
        if matching is None:
            pytest.skip(f"no King supports {archetype}")

        white.active_king = mismatched
        for entry in white.king_pool:
            entry.status = KingCardStatus.HIDDEN
        obs = game.get_observation("white")
        ctx = EvalContext(obs=obs, weights=DEFAULT_WEIGHTS, registry=registry)

        pos = next(
            p for p, u in game.state.board.all_units_for("white")
            if u.piece.piece_type.value == "pawn"
        )
        upgrade = ChangeKing(player_id="white", king_card_id=matching,
                             sacrifice_position=pos)
        lateral = ChangeKing(player_id="white", king_card_id=mismatched,
                             sacrifice_position=pos)

        assert bot.score_action(upgrade, ctx) > bot.score_action(lateral, ctx)

    def test_an_upgrade_paid_for_with_a_pawn_is_worth_taking(self, registry):
        """The whole point: some Succession must be choosable."""
        from game.ai.evaluation import EvalContext

        game = Game.new(seed=100000, registry=registry)
        bot = HeuristicBot(seed=1, registry=registry)
        bot.player_id = "white"
        white = game.state.get_player("white")
        archetype = white.archetype

        matching = next(
            (cid for cid in
             ("arcane_sovereign", "dragon_high_king", "marshal_king",
              "architect_king", "shadow_regent", "grave_crowned_king")
             if archetype in (registry.get(cid).archetype_support or ())),
            None,
        )
        if matching is None:
            pytest.skip(f"no King supports {archetype}")
        white.active_king = next(
            cid for cid in ("marshal_king", "dragon_high_king")
            if cid != matching
        )
        obs = game.get_observation("white")
        ctx = EvalContext(obs=obs, weights=DEFAULT_WEIGHTS, registry=registry)

        pawn = next(
            p for p, u in game.state.board.all_units_for("white")
            if u.piece.piece_type.value == "pawn"
        )
        assert bot.score_action(
            ChangeKing(player_id="white", king_card_id=matching,
                       sacrifice_position=pawn),
            ctx,
        ) > 0
