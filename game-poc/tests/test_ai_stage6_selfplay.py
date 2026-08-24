"""
test_ai_stage6_selfplay.py — AI Stage 6 groundwork (README §53).

§53 gates self-play on the engine first being able to "run many headless
games" and produce "complete matches".  Measurement said it could do
neither: half of a 12-match RandomBot sample ran to the harness's 4000-step
abort with no result, and the matches that did finish took minutes each.

Two rules closed that gap, and this file is what holds them:

    • Once per turn per (unit, ability).  README §46 measured a bot firing
      one Monster ability 173 times in a 200-action sample and capped it
      *inside the bot*; the engine never did, so any caller that forgot to
      self-limit — a human at the UI included — could stall a turn forever.
    • MatchLimits (README §53 / §57): threefold repetition, a no-progress
      ply count that also watches Buildings and Rituals, and a turn
      ceiling.  A match stopped by one of them is drawn, not abandoned.

Also covers the telemetry consequence: decided / drawn / unfinished are a
partition, so a self-play corpus can tell "the rules produced a draw" from
"the harness gave up".
"""

from __future__ import annotations

import pytest

from game.ai.random_bot import RandomBot
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateMonsterAbility,
    EndPreparation,
    EndTurn,
    MovePiece,
    RepositionUnit,
)
from game.core.events import GameOver
from game.core.game import Game
from game.core.phases import Phase, PieceType
from game.core.rules import IllegalActionError
from game.core.state import MatchLimits
from game.sim import play_match
from game.telemetry import MatchStats, TelemetryAggregator


# ─────────────────────────────────────────────────────────────────────────────
# MatchLimits — the numbers are data (README §57)
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchLimits:
    def test_defaults_are_the_documented_ones(self):
        limits = MatchLimits()
        assert limits.repetition_limit == 3      # chess's own threefold rule
        assert limits.no_progress_plies == 80
        assert limits.max_turns == 300

    def test_a_game_carries_the_limits_it_was_given(self, registry):
        limits = MatchLimits(repetition_limit=2, no_progress_plies=4, max_turns=7)
        game = Game.new(seed=1, registry=registry, limits=limits)
        assert game.state.limits is limits

    def test_a_game_given_none_still_has_limits(self, registry):
        game = Game.new(seed=1, registry=registry)
        assert game.state.limits == MatchLimits()


# ─────────────────────────────────────────────────────────────────────────────
# The termination signatures
# ─────────────────────────────────────────────────────────────────────────────

class TestSignatures:
    def test_position_key_ignores_hands_and_decks(self, registry):
        """
        Hands cycle every turn.  If the repetition key looked at them, no
        position would ever recur and the rule could never fire.
        """
        game = Game.new(seed=3, registry=registry)
        before = game.state.position_key()
        ps = game.state.get_player("white")
        ps.hand.append(ps.deck.pop(0))
        assert game.state.position_key() == before

    def test_position_key_tracks_the_side_to_move(self, registry):
        game = Game.new(seed=3, registry=registry)
        before = game.state.position_key()
        game.state.active_player = "black"
        assert game.state.position_key() != before

    def test_progress_key_moves_when_a_piece_is_captured(self, registry):
        game = Game.new(seed=3, registry=registry)
        before = game.state.progress_key()
        pos, _ = game.state.board.all_units_for("black")[0]
        game.state.board.remove_unit(pos)
        assert game.state.progress_key() != before

    def test_progress_key_moves_when_a_ritual_advances(self, registry):
        game = Game.new(seed=3, registry=registry)
        before = game.state.progress_key()
        game.state.get_player("white").ritual_pool[0].progress += 1
        assert game.state.progress_key() != before

    def test_progress_key_holds_still_while_pieces_only_shuffle(self, registry):
        """
        A Knight going out and back is the shape the no-progress rule exists
        to catch: the position changes, nothing irreversible does.
        """
        game = Game.new(seed=3, registry=registry)
        before = game.state.progress_key()
        pos, unit = next(
            (p, u) for p, u in game.state.board.all_units_for("white")
            if u.piece.piece_type.value == "knight"
        )
        empty = next(
            p for p, sq in game.state.board.squares.items() if sq.unit is None
        )
        game.state.board.move_unit(pos, empty)
        assert game.state.progress_key() == before
        assert game.state.position_key() != before


# ─────────────────────────────────────────────────────────────────────────────
# Termination — a drawn match is a result
# ─────────────────────────────────────────────────────────────────────────────

def _dullest(game: Game, legal: list) -> object:
    """
    The most nothing-happening legal action available.

    CHESS always demands a move — EndTurn is only offered there when the
    player has nothing else — so "do nothing" means: pass the phase if the
    phase can be passed, otherwise shuffle a non-Pawn onto an empty square.
    Captures and Pawn moves are what ``progress_key`` watches, so avoiding
    both is what lets these tests hold the match still and see which limit
    fires.
    """
    for kind in (EndTurn, EndPreparation):
        for action in legal:
            if isinstance(action, kind):
                return action

    quiet = [
        a for a in legal
        if isinstance(a, MovePiece)
        and game.state.board.get_unit(a.target) is None
        and (u := game.state.board.get_unit(a.source)) is not None
        and u.piece.piece_type != PieceType.PAWN
    ]
    return quiet[0] if quiet else legal[0]


def _end_turn(game: Game) -> None:
    """Drive whoever is to move through one whole ply, as quietly as possible."""
    mover = game.state.active_player
    for _ in range(200):
        game.advance_to_preparation()
        if game.is_over() or game.state.active_player != mover:
            return
        legal = game.get_legal_actions(mover)
        if not legal:
            return
        try:
            game.execute(_dullest(game, legal))
        except IllegalActionError:
            return


class TestTermination:
    def test_repetition_ends_the_match_in_a_draw(self, registry):
        game = Game.new(
            seed=5, registry=registry, limits=MatchLimits(repetition_limit=2),
        )
        for _ in range(30):
            if game.is_over():
                break
            _end_turn(game)

        assert game.is_over()
        assert game.state.winner is None          # nobody wins a stagnation
        assert game.state.phase == Phase.GAME_OVER
        over = [e for e in game.state.event_log if isinstance(e, GameOver)]
        assert over and over[-1].reason == "repetition"
        assert over[-1].winner is None

    def test_no_progress_ends_the_match(self, registry):
        """Repetition is switched off so only the ply counter can fire."""
        game = Game.new(
            seed=5, registry=registry,
            limits=MatchLimits(repetition_limit=10_000, no_progress_plies=6),
        )
        for _ in range(40):
            if game.is_over():
                break
            _end_turn(game)

        assert game.is_over()
        assert game.state.winner is None
        over = [e for e in game.state.event_log if isinstance(e, GameOver)]
        assert over and over[-1].reason == "no_progress"

    def test_turn_ceiling_is_the_backstop(self, registry):
        game = Game.new(
            seed=5, registry=registry,
            limits=MatchLimits(
                repetition_limit=10_000, no_progress_plies=10_000, max_turns=3,
            ),
        )
        for _ in range(40):
            if game.is_over():
                break
            _end_turn(game)

        assert game.is_over()
        over = [e for e in game.state.event_log if isinstance(e, GameOver)]
        assert over and over[-1].reason == "turn_limit"

    def test_generous_limits_do_not_stop_an_ordinary_opening(self, registry):
        """The rules must not fire on a game that is still going somewhere."""
        game = Game.new(
            seed=5, registry=registry,
            limits=MatchLimits(
                repetition_limit=10_000, no_progress_plies=10_000,
                max_turns=10_000,
            ),
        )
        for _ in range(10):
            _end_turn(game)
        assert not game.is_over()

    def test_a_self_played_match_reaches_a_result(self, registry):
        """
        The end-to-end claim: RandomBot vs RandomBot terminates.  Before the
        cap and the limits, half of these ran out the harness's step budget.
        """
        for seed in (2000, 2007, 2008, 2010, 2011):
            stats = play_match(
                seed=seed,
                controllers={"white": RandomBot(seed * 2 + 1),
                             "black": RandomBot(seed * 2 + 2)},
                registry=registry,
            )
            assert stats.completed, f"seed {seed} did not finish"
            assert stats.end_reason is not None


# ─────────────────────────────────────────────────────────────────────────────
# Once per turn per (unit, ability) — README §46's cap, moved into the rules
# ─────────────────────────────────────────────────────────────────────────────

class TestAbilityCap:
    def test_ledger_starts_empty_and_clears_each_turn(self, registry):
        game = Game.new(seed=7, registry=registry)
        ps = game.state.get_player("white")
        assert ps.abilities_used_this_turn == set()
        ps.abilities_used_this_turn.add(("white-pawn-a", "burrow"))
        ps.reset_turn_flags()
        assert ps.abilities_used_this_turn == set()

    def test_a_spent_ability_is_refused(self, registry):
        """
        Hand-built action, bypassing get_legal_actions entirely: the cap is a
        rule, not a suggestion the action list makes.
        """
        game = Game.new(seed=7, registry=registry)
        game.state.phase = Phase.CHESS
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        unit.monster_id = "tunnel_mole"          # a real card, real ability
        ps = game.state.get_player(pid)

        # Unspent, the same action is accepted.
        game.execute(ActivateMonsterAbility(
            player_id=pid, unit_position=pos, ability_id="burrow",
        ))
        assert (unit.piece.id, "burrow") in ps.abilities_used_this_turn

        # Spent, it is refused — and refused on the cap, not on anything else.
        with pytest.raises(IllegalActionError, match="already been activated"):
            game.execute(ActivateMonsterAbility(
                player_id=pid, unit_position=pos, ability_id="burrow",
            ))

    def test_a_spent_ability_is_no_longer_offered(self, registry):
        game = Game.new(seed=7, registry=registry)
        game.state.phase = Phase.CHESS
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        unit.monster_id = "tunnel_mole"

        def offered() -> list:
            return [
                a for a in game.get_legal_actions(pid)
                if isinstance(a, ActivateMonsterAbility)
                and a.unit_position == pos and a.ability_id == "burrow"
            ]

        assert offered(), "the ability should be on offer before it is spent"
        game.state.get_player(pid).abilities_used_this_turn.add(
            (unit.piece.id, "burrow")
        )
        assert offered() == []

    def test_random_self_play_no_longer_drowns_in_activations(self, registry):
        """
        Seed 2010's signature failure: 3370 ActivateMonsterAbility actions on
        one repeated position, 4000 steps, no result.
        """
        activations = 0

        def count(pid, action):
            nonlocal activations
            if isinstance(action, ActivateMonsterAbility):
                activations += 1

        stats = play_match(
            seed=2010,
            controllers={"white": RandomBot(4021), "black": RandomBot(4022)},
            registry=registry,
            on_step=count,
        )
        assert stats.completed
        assert activations < stats.action_count / 2


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry — a draw is not a missing result
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawAccounting:
    def _stats(self, gid, winner, completed):
        return MatchStats(game_id=gid, seed=0, winner=winner, completed=completed)

    def test_decided_drawn_unfinished_partition_the_run(self):
        agg = TelemetryAggregator()
        agg.extend([
            self._stats("won", "white", True),
            self._stats("drawn", None, True),
            self._stats("abandoned", None, False),
        ])
        assert [m.game_id for m in agg.decided] == ["won"]
        assert [m.game_id for m in agg.drawn] == ["drawn"]
        assert [m.game_id for m in agg.unfinished] == ["abandoned"]
        assert len(agg.decided) + len(agg.drawn) + len(agg.unfinished) == len(agg)

    def test_summary_reports_all_three_and_the_reasons(self):
        agg = TelemetryAggregator()
        won = self._stats("won", "white", True)
        won.end_reason = "final_duel_victory"
        drawn = self._stats("drawn", None, True)
        drawn.end_reason = "repetition"
        agg.extend([won, drawn])

        summary = agg.summary()
        assert summary["decided"] == 1
        assert summary["drawn"] == 1
        assert summary["unfinished"] == 0
        assert summary["end_reasons"] == {
            "final_duel_victory": 1, "repetition": 1,
        }

    def test_a_draw_is_excluded_from_the_win_rate(self):
        agg = TelemetryAggregator()
        agg.extend([
            self._stats("a", "white", True),
            self._stats("b", None, True),
        ])
        assert agg.first_player_win_rate == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# The check filter, before and after
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckFilterEquivalence:
    """
    ``get_legal_moves`` used to answer "does this move leave my King in
    check?" by deep-copying the whole board per candidate destination.  That
    copy was 83 % of the engine's runtime, so it is now make/unmake.

    Make/unmake is only worth having if it is *exactly* the same answer, and
    it is only safe if the board it hands back is the board it was given.
    Both are checked here against the original implementation, over
    positions taken from real self-played games rather than from a fixture —
    the interesting ones (Monsters with altered movement, pinned pieces,
    en passant) are the ones nobody thinks to write down.
    """

    @staticmethod
    def _legal_moves_by_copying(board, pos, unit, en_passant_target, registry):
        """The pre-§53 implementation, kept here as the reference answer."""
        from copy import deepcopy

        from game.chess.movement import get_pseudo_legal_moves, is_in_check

        legal = []
        for target in get_pseudo_legal_moves(
            board, pos, unit, en_passant_target, registry=registry
        ):
            sim = deepcopy(board)
            if (
                unit.piece.piece_type == PieceType.PAWN
                and en_passant_target is not None
                and target == en_passant_target
                and board.get_unit(target) is None
            ):
                direction = 1 if unit.owner == "white" else -1
                sim.remove_unit(
                    Position(target.file, target.rank - direction)
                )
            sim.move_unit(pos, target)
            if not is_in_check(sim, unit.owner):
                legal.append(target)
        return legal

    @staticmethod
    def _board_fingerprint(board):
        return tuple(
            (pos.file, pos.rank, None if sq.unit is None else sq.unit.piece.id)
            for pos, sq in sorted(
                board.squares.items(), key=lambda kv: (kv[0].file, kv[0].rank)
            )
        )

    def test_same_answer_and_an_untouched_board(self, registry):
        from game.chess.movement import get_legal_moves

        game = Game.new(seed=4242, registry=registry)
        compared = 0

        for _ in range(60):
            if game.is_over():
                break
            board = game.state.board
            ep = game.state.en_passant_target

            for pos, unit in board.all_units():
                before = self._board_fingerprint(board)

                expected = self._legal_moves_by_copying(
                    board, pos, unit, ep, registry
                )
                actual = get_legal_moves(
                    board, pos, unit, ep, registry=registry,
                )

                assert actual == expected, (
                    f"legal moves diverged for {unit.piece.id} at {pos}"
                )
                assert self._board_fingerprint(board) == before, (
                    "make/unmake left the board changed"
                )
                compared += 1

            _end_turn(game)

        assert compared > 200, f"only {compared} positions compared"

    def test_en_passant_is_filtered_the_same_way(self, registry):
        """
        The one case where the moved piece and the captured piece stand on
        different squares — and so the one the restore has to get right.
        """
        from game.chess.movement import get_legal_moves

        game = Game.new(seed=11, registry=registry)
        board = game.state.board

        # White pawn on e5, black pawn just double-pushed to d5.
        for pos, _ in list(board.all_units()):
            board.remove_unit(pos)

        def place(owner, ptype, file, rank, pid):
            board.place_unit(
                Position(file, rank),
                UnitInstance(
                    piece=ChessPiece(id=pid, piece_type=ptype, owner=owner)
                ),
            )

        place("white", PieceType.KING, 4, 0, "wk")
        place("black", PieceType.KING, 4, 7, "bk")
        place("white", PieceType.PAWN, 4, 4, "wp")     # e5
        place("black", PieceType.PAWN, 3, 4, "bp")     # d5
        ep_square = Position(3, 5)                      # d6

        pos = Position(4, 4)
        unit = board.get_unit(pos)
        before = self._board_fingerprint(board)

        expected = self._legal_moves_by_copying(board, pos, unit, ep_square, registry)
        actual = get_legal_moves(board, pos, unit, ep_square, registry=registry)

        assert ep_square in actual, "the en passant capture should be legal here"
        assert actual == expected
        assert self._board_fingerprint(board) == before


# ─────────────────────────────────────────────────────────────────────────────
# A decision nobody can resolve
# ─────────────────────────────────────────────────────────────────────────────

class TestStaleRepositionDecision:
    """
    CHESS offers *nothing but* the pending reposition until it is resolved
    (rules.py get_legal_actions), so a REPOSITION decision about a piece
    that has since been captured locks the player out of their own turn for
    the rest of the match.

    Self-play found it as the second shape of non-terminating match, after
    the ability loop: 3791 RepositionUnit attempts against "No piece at a6",
    4000 steps, no result.  Two heuristic matches in a 40-match run were
    still hitting it once the other causes were closed.
    """

    def _pending_reposition(self, game: Game, pos: Position, piece_id: str):
        from game.core.phases import DecisionType
        from game.core.state import PendingDecision

        game.state.phase = Phase.CHESS
        game.state.pending_decision = PendingDecision(
            player_id=game.state.active_player,
            decision_type=DecisionType.REPOSITION,
            options=[(0, 4)],
            min_choices=1,
            max_choices=1,
            context={"piece_id": piece_id, "current_pos": (pos.file, pos.rank)},
        )

    def test_a_live_decision_still_gates_the_turn(self, registry):
        game = Game.new(seed=31, registry=registry)
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        self._pending_reposition(game, pos, unit.piece.id)

        legal = game.get_legal_actions(pid)
        assert legal and all(isinstance(a, RepositionUnit) for a in legal)

    def test_a_captured_subject_does_not_lock_the_player_out(self, registry):
        game = Game.new(seed=31, registry=registry)
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        self._pending_reposition(game, pos, unit.piece.id)
        game.state.board.remove_unit(pos)        # a Trap fires, say

        legal = game.get_legal_actions(pid)
        assert legal, "the player must still have something to do"
        assert not all(isinstance(a, RepositionUnit) for a in legal)

    def test_a_moved_subject_is_stale_too(self, registry):
        """Same square, different piece: the decision was about the other one."""
        from game.core.rules import RulesEngine

        game = Game.new(seed=31, registry=registry)
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        self._pending_reposition(game, pos, "some-other-piece")

        engine = RulesEngine(registry=registry)
        assert engine._reposition_is_stale(game.state, game.state.pending_decision)

    def test_executing_anything_clears_it(self, registry):
        game = Game.new(seed=31, registry=registry)
        pid = game.state.active_player
        pos, unit = game.state.board.all_units_for(pid)[0]
        self._pending_reposition(game, pos, unit.piece.id)
        game.state.board.remove_unit(pos)

        move = next(
            a for a in game.get_legal_actions(pid) if isinstance(a, MovePiece)
        )
        game.execute(move)
        assert game.state.pending_decision is None

    def test_the_matches_it_was_found_in_now_finish(self, registry):
        from game.ai.heuristic_bot import HeuristicBot

        for seed in (9008, 9039):
            stats = play_match(
                seed=seed,
                controllers={"white": HeuristicBot(seed * 2 + 1, registry=registry),
                             "black": HeuristicBot(seed * 2 + 2, registry=registry)},
                registry=registry,
            )
            assert stats.completed, f"seed {seed} still does not finish"


# ─────────────────────────────────────────────────────────────────────────────
# Cards the action generator could never offer legally
# ─────────────────────────────────────────────────────────────────────────────

class TestRepositionSpellTargets:
    """
    ``_reposition_unit`` implements four modes off the card's params, and
    ``get_legal_actions`` used to enumerate only one of them — the plain
    max_distance box.  Two cards were dead as a result:

        knightfall        needs a Knight and a Knight hop; was offered
                          Pawns and adjacent squares, so every offer was
                          refused with "This Spell can only target: knight".
        evacuation_order  exists to move the King; the blanket no-King rule
                          in the enumeration meant the King was never a
                          candidate, so it had no legal target at all.

    README §53's corpus analysis is what found them — both sat at a 0 %
    play rate across ~100 deck appearances each while scoring below
    average, which is what holding a dead card in a 20-card deck does.

    The invariant these tests hold: an offered action is an action the
    handler accepts.
    """

    @staticmethod
    def _offers(game: Game, card_id: str):
        from game.core.actions import ActivateSpell

        pid = game.state.active_player
        game.state.get_player(pid).hand.append(card_id)
        game.advance_to_preparation()
        return pid, [
            a for a in game.get_legal_actions(pid)
            if isinstance(a, ActivateSpell) and a.card_id == card_id
        ]

    def _all_offers_execute(self, registry, card_id: str, setup=None) -> int:
        """Replay each offered action on a fresh game; none may be refused."""
        from game.core.rules import IllegalActionError

        def fresh():
            game = Game.new(seed=77, registry=registry)
            if setup:
                setup(game)
            return game

        _pid, offers = self._offers(fresh(), card_id)
        for action in offers:
            game = fresh()
            game.state.get_player(game.state.active_player).hand.append(card_id)
            game.advance_to_preparation()
            try:
                game.execute(action)
            except IllegalActionError as exc:
                pytest.fail(f"{card_id}: offered an action the rules refuse — {exc}")
        return len(offers)

    @staticmethod
    def _clear_around_king(game: Game) -> None:
        for file in range(3, 6):
            for rank in range(0, 2):
                pos = Position(file, rank)
                unit = game.state.board.get_unit(pos)
                if unit is not None and unit.piece.piece_type != PieceType.KING:
                    game.state.board.remove_unit(pos)

    def test_knightfall_has_legal_targets_and_all_of_them_work(self, registry):
        assert self._all_offers_execute(registry, "knightfall") > 0

    def test_knightfall_only_offers_knights(self, registry):
        game = Game.new(seed=77, registry=registry)
        _pid, offers = self._offers(game, "knightfall")
        assert offers
        for action in offers:
            unit = game.state.board.get_unit(Position(*action.target["position"]))
            assert unit is not None
            assert unit.piece.piece_type == PieceType.KNIGHT

    def test_knightfall_destinations_are_knight_hops(self, registry):
        from game.mechanics.effects.movement import _KNIGHT_OFFSETS

        game = Game.new(seed=77, registry=registry)
        _pid, offers = self._offers(game, "knightfall")
        for action in offers:
            src = action.target["position"]
            dst = action.target["destination"]
            assert (dst[0] - src[0], dst[1] - src[1]) in _KNIGHT_OFFSETS

    def test_evacuation_order_can_move_the_king(self, registry):
        assert self._all_offers_execute(
            registry, "evacuation_order", setup=self._clear_around_king
        ) > 0

    def test_evacuation_order_offers_only_the_king(self, registry):
        game = Game.new(seed=77, registry=registry)
        self._clear_around_king(game)
        _pid, offers = self._offers(game, "evacuation_order")
        assert offers
        for action in offers:
            unit = game.state.board.get_unit(Position(*action.target["position"]))
            assert unit.piece.piece_type == PieceType.KING

    def test_evacuation_order_is_silent_while_in_check(self, registry):
        """The card says so: "Cannot be activated while in Check.\""""
        game = Game.new(seed=77, registry=registry)
        self._clear_around_king(game)

        # Drop an enemy Rook onto the King's file with a clear path.
        for rank in range(1, 7):
            game.state.board.remove_unit(Position(4, rank))
        rook = UnitInstance(
            piece=ChessPiece(id="bq-rook", piece_type=PieceType.ROOK, owner="black")
        )
        game.state.board.place_unit(Position(4, 6), rook)

        from game.chess.movement import is_in_check
        assert is_in_check(game.state.board, "white")

        _pid, offers = self._offers(game, "evacuation_order")
        assert offers == []

    def test_the_ordinary_reposition_spell_is_unchanged(self, registry):
        """arcane_reposition is mode 2 — the one that always worked."""
        game = Game.new(seed=77, registry=registry)
        _pid, offers = self._offers(game, "arcane_reposition")
        assert len(offers) > 50
        for action in offers:
            unit = game.state.board.get_unit(Position(*action.target["position"]))
            assert unit.piece.piece_type != PieceType.KING


# ─────────────────────────────────────────────────────────────────────────────
# En passant, misread as a capture of the mover
# ─────────────────────────────────────────────────────────────────────────────

class TestEnPassantIsACapture:
    """
    En passant detection tested only "is this Pawn landing on the
    en-passant square?", which a *straight push* can also satisfy.

    The corpus turned it up once in a thousand matches, and the setup is as
    unlikely as that suggests: White double-pushes h2→h4, setting the
    en-passant square to h3; tunnel_mole's burrow later puts a White Pawn
    back on h2; White pushes h2→h3.  Target equals the en-passant square,
    so the engine computed the "captured" square as one rank behind — h2,
    the mover's own square — removed the moving Pawn, and then crashed
    moving a piece that was no longer there.

    A capture always changes file.  That is the fix, and this is the test.
    """

    @staticmethod
    def _bare_board(game: Game) -> None:
        for pos, _unit in list(game.state.board.all_units()):
            game.state.board.remove_unit(pos)

    @staticmethod
    def _place(game: Game, owner, ptype, file, rank, pid) -> None:
        game.state.board.place_unit(
            Position(file, rank),
            UnitInstance(piece=ChessPiece(id=pid, piece_type=ptype, owner=owner)),
        )

    def _position(self, registry):
        game = Game.new(seed=3, registry=registry)
        self._bare_board(game)
        self._place(game, "white", PieceType.KING, 4, 0, "wk")
        self._place(game, "black", PieceType.KING, 4, 7, "bk")
        game.state.phase = Phase.CHESS
        game.state.active_player = "white"
        return game

    def test_a_straight_push_onto_the_ep_square_is_an_ordinary_move(self, registry):
        game = self._position(registry)
        self._place(game, "white", PieceType.PAWN, 7, 1, "wp-h")   # h2
        game.state.en_passant_target = Position(7, 2)               # h3

        game.execute(MovePiece(
            player_id="white", source=Position(7, 1), target=Position(7, 2),
        ))

        moved = game.state.board.get_unit(Position(7, 2))
        assert moved is not None and moved.piece.id == "wp-h"
        assert game.state.board.get_unit(Position(7, 1)) is None
        assert len(game.state.board.all_units()) == 3     # nothing was eaten

    def test_a_real_en_passant_still_captures(self, registry):
        """White pawn on e5 takes a black pawn that just double-pushed to d5."""
        game = self._position(registry)
        self._place(game, "white", PieceType.PAWN, 4, 4, "wp-e")   # e5
        self._place(game, "black", PieceType.PAWN, 3, 4, "bp-d")   # d5
        game.state.en_passant_target = Position(3, 5)               # d6

        game.execute(MovePiece(
            player_id="white", source=Position(4, 4), target=Position(3, 5),
        ))

        assert game.state.board.get_unit(Position(3, 5)).piece.id == "wp-e"
        assert game.state.board.get_unit(Position(3, 4)) is None    # victim gone
        assert len(game.state.board.all_units()) == 3

    def test_a_diagonal_move_onto_the_ep_square_with_no_victim_is_not_ep(self, registry):
        """
        The other half of the guard: the square behind the target has to
        actually hold an enemy Pawn.
        """
        game = self._position(registry)
        self._place(game, "white", PieceType.PAWN, 4, 4, "wp-e")   # e5
        self._place(game, "black", PieceType.KNIGHT, 3, 4, "bn")   # d5 — not a Pawn
        game.state.en_passant_target = Position(3, 5)

        game.execute(MovePiece(
            player_id="white", source=Position(4, 4), target=Position(3, 5),
        ))

        assert game.state.board.get_unit(Position(3, 5)).piece.id == "wp-e"
        assert game.state.board.get_unit(Position(3, 4)).piece.id == "bn"  # untouched

    def test_the_match_it_was_found_in_now_finishes(self, registry):
        from game.ai.heuristic_bot import HeuristicBot

        seed = 100121
        stats = play_match(
            seed=seed,
            controllers={"white": HeuristicBot(seed * 2 + 1, registry=registry),
                         "black": HeuristicBot(seed * 2 + 2, registry=registry)},
            registry=registry,
        )
        assert stats.completed


# ─────────────────────────────────────────────────────────────────────────────
# Balance fixes the corpus argued for
# ─────────────────────────────────────────────────────────────────────────────

class TestSealCannotEndTheGame:
    """
    ``seal_zone`` gives a sealed enemy unit zero legal destinations, and
    "zero legal destinations" is exactly what checkmate and stalemate
    detection reads.  A seal laid over the enemy King therefore manufactured
    a game-ending position: with a check it is checkmate (SIEGE), without
    one it is stalemate (LAST_STAND), and either way the sealing player
    picked the moment the Final Duel started.

    README §53's corpus priced it: a player merely *dealt*
    seal_of_lockdown won 80 % of their decided matches (+0.377, 15.5σ) —
    the largest effect in a 117-card pool by half again.

    The King is now exempt, which is the rule the rest of the game already
    followed (``damage_unit``: "Kings are never damaged").  The card keeps
    doing everything else it ever did.
    """

    @staticmethod
    def _sealed_board(registry, *, extra_black_pawn=True):
        game = Game.new(seed=5, registry=registry)
        board = game.state.board
        for pos, _unit in list(board.all_units()):
            board.remove_unit(pos)

        def place(owner, ptype, file, rank, pid):
            board.place_unit(
                Position(file, rank),
                UnitInstance(piece=ChessPiece(id=pid, piece_type=ptype, owner=owner)),
            )

        place("black", PieceType.KING, 4, 7, "bk")
        place("white", PieceType.KING, 4, 0, "wk")
        if extra_black_pawn:
            place("black", PieceType.PAWN, 3, 6, "bp")

        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                file, rank = 4 + df, 7 + dr
                if 0 <= file <= 7 and 0 <= rank <= 7:
                    board.get_square(Position(file, rank)).add_effect("sealed:2:white")
        return game, board

    def test_a_sealed_king_can_still_move(self, registry):
        from game.chess.movement import get_legal_moves

        _game, board = self._sealed_board(registry)
        king_pos = Position(4, 7)
        moves = get_legal_moves(board, king_pos, board.get_unit(king_pos))
        assert moves, "a sealed King must still have somewhere to go"

    def test_the_seal_still_paralyses_everything_else(self, registry):
        """The nerf must not quietly turn the card off."""
        from game.chess.movement import get_legal_moves

        _game, board = self._sealed_board(registry)
        pawn_pos = Position(3, 6)
        assert get_legal_moves(board, pawn_pos, board.get_unit(pawn_pos)) == []

    def test_a_seal_cannot_manufacture_stalemate(self, registry):
        from game.chess.check import is_stalemate

        _game, board = self._sealed_board(registry, extra_black_pawn=False)
        assert not is_stalemate(board, "black")

    def test_a_seal_plus_a_check_is_not_checkmate(self, registry):
        from game.chess.check import is_checkmate
        from game.chess.movement import is_in_check

        _game, board = self._sealed_board(registry, extra_black_pawn=False)
        board.place_unit(
            Position(0, 7),
            UnitInstance(piece=ChessPiece(
                id="wr", piece_type=PieceType.ROOK, owner="white",
            )),
        )
        assert is_in_check(board, "black")
        assert not is_checkmate(board, "black")

    def test_a_sealed_friendly_unit_is_unaffected(self, registry):
        """The seal is directional — it binds the caster's enemies only."""
        from game.chess.movement import get_legal_moves

        _game, board = self._sealed_board(registry)
        board.place_unit(
            Position(5, 6),
            UnitInstance(piece=ChessPiece(
                id="wp", piece_type=PieceType.PAWN, owner="white",
            )),
        )
        assert get_legal_moves(board, Position(5, 6), board.get_unit(Position(5, 6)))


class TestSpellRadiusCeiling:
    """
    dread_tide sat at radius 2 — a 5x5, 39 % of the board — as the pool's
    only mass-removal card and its second-largest measured effect (+0.239,
    8.9σ when dealt).  It was also the only spatial Spell above radius 1 in
    the whole pool, so bringing it down is a return to the game's own
    ceiling rather than a new one imposed on it.
    """

    def test_dread_tide_is_within_the_ceiling(self, registry):
        assert registry.get("dread_tide").radius == 1

    def test_no_spell_exceeds_the_ceiling(self, registry):
        from game.cards.card import SpellCard

        cards = getattr(registry, "_cards", None) or getattr(registry, "cards", {})
        oversized = {
            cid: card.radius
            for cid, card in cards.items()
            if isinstance(card, SpellCard) and (card.radius or 0) > 1
        }
        assert oversized == {}, f"spells above radius 1: {oversized}"
