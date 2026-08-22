"""
AI Stage 1 — HeuristicBot and the weighted evaluation (README §46)
====================================================================

Covers:
    • The evaluation covers every README §46 category and reacts to the
      board in the right direction.
    • Board reconstruction from an Observation is faithful and read-only.
    • The bot obeys the PlayerController contract.
    • It plays greedily and sensibly: takes free material, avoids hanging
      pieces, walks around Traps, prefers Coronation and Rituals.
    • It is reproducible and never crashes across a real game.
"""

from __future__ import annotations

import pytest

from game.ai.evaluation import (
    DEFAULT_WEIGHTS,
    EvalContext,
    EvalWeights,
    attacked_squares,
    board_from_observation,
    centrality,
    chebyshev,
    evaluate_observation,
    evaluation_breakdown,
    piece_value,
)
from game.ai.heuristic_bot import ActionBias, HeuristicBot
from game.ai.controller import PlayerController
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateMonsterAbility,
    CoronateKing,
    EndPreparation,
    EndTurn,
    MovePiece,
    PromotePawn,
    SummonMonster,
)
from game.core.game import Game
from game.core.observation import build_observation
from game.core.phases import PieceType, Phase
from game.core.state import GameState


P = Position.from_algebraic


@pytest.fixture
def game(registry) -> Game:
    return Game.new(seed=11, registry=registry)


def _obs(state: GameState, player: str = "white", registry=None):
    return build_observation(state, player, registry=registry)


def _ctx(state: GameState, player: str = "white", registry=None) -> EvalContext:
    return EvalContext(obs=_obs(state, player, registry), registry=registry)


def _put(state: GameState, square: str, owner: str, piece_type: str,
         piece_id: str | None = None) -> None:
    piece = ChessPiece(
        id=piece_id or f"{owner}-{piece_type}-{square}",
        owner=owner,
        piece_type=PieceType(piece_type),
    )
    state.board.place_unit(P(square), UnitInstance(piece=piece))


def _clear(state: GameState) -> None:
    state.board = BoardState()


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_piece_values_are_ordered(self):
        assert piece_value("pawn") < piece_value("knight") < piece_value("rook")
        assert piece_value("rook") < piece_value("queen") < piece_value("king")

    def test_unknown_piece_type_is_worth_a_pawn(self):
        assert piece_value("wyrm") == 1.0

    def test_centrality_peaks_in_the_middle(self):
        assert centrality(P("d4")) == pytest.approx(1.0)
        assert centrality(P("a1")) == pytest.approx(0.0)
        assert centrality(P("c3")) > centrality(P("b2"))

    def test_chebyshev(self):
        assert chebyshev(P("a1"), P("a1")) == 0
        assert chebyshev(P("a1"), P("b2")) == 1
        assert chebyshev(P("a1"), P("h8")) == 7


# ─────────────────────────────────────────────────────────────────────────────
# Board reconstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestBoardReconstruction:
    def test_reconstructs_every_unit(self, game: Game):
        obs = game.get_observation("white")
        board = board_from_observation(obs)
        assert len(board.all_units()) == len(game.state.board.all_units())

    def test_positions_types_and_owners_match(self, game: Game):
        board = board_from_observation(game.get_observation("white"))
        for pos, unit in game.state.board.all_units():
            mirror = board.get_unit(pos)
            assert mirror is not None
            assert mirror.piece.piece_type == unit.piece.piece_type
            assert mirror.owner == unit.owner
            assert mirror.piece.id == unit.piece.id

    def test_reconstruction_does_not_alias_the_engine_board(self, game: Game):
        board = board_from_observation(game.get_observation("white"))
        board.remove_unit(P("e2"))
        assert game.state.board.get_unit(P("e2")) is not None

    def test_attacked_squares_from_the_opening(self, game: Game):
        board = board_from_observation(game.get_observation("white"))
        attacks = attacked_squares(board, "white")
        # Pawns attack diagonally, not straight ahead.
        assert P("d3") in attacks          # covered by the c2 and e2 pawns
        assert P("f3") in attacks          # g1 knight
        assert P("a3") in attacks          # b2 pawn
        # Nothing white owns reaches the 4th rank in the opening — which it
        # would if pawn pushes were being counted as attacks.
        assert P("e4") not in attacks


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation — README §46 categories
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluationCategories:
    README_CATEGORIES = [
        "material", "king_safety", "enemy_king_pressure", "board_control",
        "monster_value", "pawn_economy", "building_value", "territory",
        "ritual_progress", "card_advantage", "hand_quality", "royal_support",
        "duel_probability",
    ]

    def test_every_readme_category_is_scored(self, game: Game):
        breakdown = evaluation_breakdown(game.get_observation("white"))
        assert set(breakdown) == set(self.README_CATEGORIES)

    def test_score_is_the_sum_of_the_breakdown(self, game: Game):
        obs = game.get_observation("white")
        assert evaluate_observation(obs) == pytest.approx(
            sum(evaluation_breakdown(obs).values())
        )

    def test_starting_position_is_symmetric(self, game: Game):
        white = evaluation_breakdown(game.get_observation("white"))
        black = evaluation_breakdown(game.get_observation("black"))
        assert white["material"] == pytest.approx(0.0)
        assert white["material"] == pytest.approx(black["material"])
        assert white["card_advantage"] == pytest.approx(black["card_advantage"])

    def test_material_advantage_is_positive(self, game_state: GameState):
        game_state.board.remove_unit(P("d8"))          # black loses its queen
        breakdown = evaluation_breakdown(_obs(game_state, "white"))
        assert breakdown["material"] == pytest.approx(piece_value("queen"))
        assert evaluation_breakdown(_obs(game_state, "black"))["material"] < 0

    def test_check_dominates_king_safety(self, game_state: GameState):
        before = evaluation_breakdown(_obs(game_state, "white"))["king_safety"]
        game_state.get_player("white").set_check(True)
        after = evaluation_breakdown(_obs(game_state, "white"))["king_safety"]
        assert after == pytest.approx(before - DEFAULT_WEIGHTS.king_safety_check)

    def test_enemy_check_is_pressure(self, game_state: GameState):
        game_state.get_player("black").set_check(True)
        breakdown = evaluation_breakdown(_obs(game_state, "white"))
        assert breakdown["enemy_king_pressure"] >= DEFAULT_WEIGHTS.enemy_king_check

    def test_units_near_the_enemy_king_are_pressure(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        base = evaluation_breakdown(_obs(game_state, "white"))["enemy_king_pressure"]
        _put(game_state, "e7", "white", "rook")
        after = evaluation_breakdown(_obs(game_state, "white"))["enemy_king_pressure"]
        assert after > base

    def test_advanced_pawns_are_worth_more(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a2", "white", "pawn")
        low = evaluation_breakdown(_obs(game_state, "white"))["pawn_economy"]
        game_state.board.move_unit(P("a2"), P("a6"))
        high = evaluation_breakdown(_obs(game_state, "white"))["pawn_economy"]
        assert high > low

    def test_black_pawn_advancement_is_measured_from_its_own_side(
        self, game_state: GameState
    ):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a7", "black", "pawn")
        low = evaluation_breakdown(_obs(game_state, "black"))["pawn_economy"]
        game_state.board.move_unit(P("a7"), P("a3"))
        high = evaluation_breakdown(_obs(game_state, "black"))["pawn_economy"]
        assert high > low

    def test_own_building_is_positive_enemy_building_negative(
        self, game_state: GameState, registry
    ):
        from game.core.phases import ConstructionStatus
        from game.core.state import BuildingInstance

        game_state.buildings.append(BuildingInstance(
            id="b1", owner="white", building_card_id="fortress",
            position=P("c3"), status=ConstructionStatus.COMPLETE,
            integrity=2, max_integrity=2,
        ))
        white = evaluation_breakdown(_obs(game_state, "white", registry))
        black = evaluation_breakdown(_obs(game_state, "black", registry))
        assert white["building_value"] > 0
        assert black["building_value"] == pytest.approx(-white["building_value"])

    def test_card_advantage_follows_hand_size(self, game_state: GameState):
        base = evaluation_breakdown(_obs(game_state, "white"))["card_advantage"]
        game_state.get_player("white").hand.append("dark_magician")
        more = evaluation_breakdown(_obs(game_state, "white"))["card_advantage"]
        assert more == pytest.approx(base + DEFAULT_WEIGHTS.card_in_hand)

    def test_ritual_progress_counts(self, game_state: GameState):
        from game.core.state import RitualState

        game_state.get_player("white").ritual_pool = [
            RitualState(ritual_id="r1", progress=2)
        ]
        breakdown = evaluation_breakdown(_obs(game_state, "white"))
        assert breakdown["ritual_progress"] == pytest.approx(
            2 * DEFAULT_WEIGHTS.ritual_progress
        )

    def test_activated_ritual_is_the_biggest_ritual_term(self, game_state: GameState):
        from game.core.state import RitualState

        game_state.get_player("white").ritual_pool = [
            RitualState(ritual_id="r1", activated=True)
        ]
        breakdown = evaluation_breakdown(_obs(game_state, "white"))
        assert breakdown["ritual_progress"] == pytest.approx(
            DEFAULT_WEIGHTS.ritual_activated
        )

    def test_monster_value_rewards_transformed_units(
        self, game_state: GameState, registry
    ):
        base = evaluation_breakdown(_obs(game_state, "white", registry))["monster_value"]
        unit = game_state.board.get_unit(P("d1"))
        unit.monster_id = "dark_magician"
        after = evaluation_breakdown(_obs(game_state, "white", registry))["monster_value"]
        assert after > base

    def test_hand_quality_without_a_registry_falls_back_to_hand_size(
        self, game_state: GameState
    ):
        breakdown = evaluation_breakdown(_obs(game_state, "white"))
        expected = DEFAULT_WEIGHTS.hand_playable * len(
            game_state.get_player("white").hand
        )
        assert breakdown["hand_quality"] == pytest.approx(expected)

    def test_weights_are_tunable(self, game_state: GameState):
        game_state.board.remove_unit(P("d8"))
        loud = EvalWeights(material=10.0)
        assert (
            evaluation_breakdown(_obs(game_state, "white"), loud)["material"]
            == pytest.approx(10.0 * piece_value("queen"))
        )


class TestEvaluationIsReadOnly:
    def test_evaluating_does_not_change_the_game(self, game: Game):
        before = repr(game.state.board.all_units())
        evaluate_observation(game.get_observation("white"))
        assert repr(game.state.board.all_units()) == before


# ─────────────────────────────────────────────────────────────────────────────
# Controller contract
# ─────────────────────────────────────────────────────────────────────────────

class TestControllerContract:
    def test_is_a_player_controller(self):
        assert issubclass(HeuristicBot, PlayerController)

    def test_player_id_assignment(self):
        bot = HeuristicBot()
        bot.player_id = "black"
        assert bot.player_id == "black"

    def test_empty_legal_actions_raises(self, game: Game):
        bot = HeuristicBot()
        bot.player_id = "white"
        with pytest.raises(ValueError, match="empty legal action list"):
            bot.choose_action(game.get_observation("white"), [])

    def test_single_action_is_returned_unchanged(self, game: Game):
        bot = HeuristicBot()
        bot.player_id = "white"
        only = EndPreparation(player_id="white")
        assert bot.choose_action(game.get_observation("white"), [only]) is only

    def test_returns_an_action_from_the_list(self, game: Game):
        bot = HeuristicBot(seed=4)
        bot.player_id = "white"
        legal = game.get_legal_actions("white")
        chosen = bot.choose_action(game.get_observation("white"), legal)
        assert any(chosen is a for a in legal)

    def test_same_seed_same_choice(self, game: Game):
        obs = game.get_observation("white")
        legal = game.get_legal_actions("white")
        picks = []
        for _ in range(2):
            bot = HeuristicBot(seed=9)
            bot.player_id = "white"
            picks.append(repr(bot.choose_action(obs, legal)))
        assert picks[0] == picks[1]

    def test_works_without_a_registry(self, game: Game):
        bot = HeuristicBot(seed=1)          # no registry
        bot.player_id = "white"
        legal = game.get_legal_actions("white")
        assert bot.choose_action(game.get_observation("white"), legal) in legal


# ─────────────────────────────────────────────────────────────────────────────
# Monster abilities fire once per turn
# ─────────────────────────────────────────────────────────────────────────────

class TestAbilityRepetition:
    """
    Activating a Monster ability does not consume the chess move, and
    several abilities (inspect_top_deck, burrow) stay legal after use — so
    a flat positive bias alone had the bot re-firing the same ability every
    decision and never playing chess at all.
    """

    def _ability(self) -> ActivateMonsterAbility:
        return ActivateMonsterAbility(
            player_id="white", unit_position=P("c2"), ability_id="inspect_top_deck",
        )

    def test_the_first_activation_is_preferred(self, game_state: GameState):
        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ability = self._ability()
        chosen = bot.choose_action(
            _obs(game_state), [ability, EndTurn(player_id="white")]
        )
        assert chosen is ability

    def test_the_same_ability_is_not_fired_twice_in_one_turn(self, game_state: GameState):
        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ability = self._ability()
        end = EndTurn(player_id="white")
        assert bot.choose_action(_obs(game_state), [ability, end]) is ability
        assert bot.choose_action(_obs(game_state), [ability, end]) is end

    def test_a_new_turn_clears_the_memory(self, game_state: GameState):
        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ability = self._ability()
        end = EndTurn(player_id="white")
        assert bot.choose_action(_obs(game_state), [ability, end]) is ability

        game_state.turn_number += 1
        assert bot.choose_action(_obs(game_state), [ability, end]) is ability


# ─────────────────────────────────────────────────────────────────────────────
# Greedy behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestGreedyBehaviour:
    def _bot(self, registry=None, seed: int = 0) -> HeuristicBot:
        bot = HeuristicBot(seed=seed, registry=registry)
        bot.player_id = "white"
        return bot

    def test_takes_the_free_queen(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a1", "white", "rook")
        _put(game_state, "a7", "black", "queen")
        _put(game_state, "h7", "black", "pawn")

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        take_queen = MovePiece(player_id="white", source=P("a1"), target=P("a7"))
        quiet = MovePiece(player_id="white", source=P("a1"), target=P("a4"))
        assert bot.score_action(take_queen, ctx) > bot.score_action(quiet, ctx)

    def test_prefers_the_bigger_capture(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "white", "queen")
        _put(game_state, "d7", "black", "rook")
        _put(game_state, "a4", "black", "pawn")

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        rook = MovePiece(player_id="white", source=P("d4"), target=P("d7"))
        pawn = MovePiece(player_id="white", source=P("d4"), target=P("a4"))
        assert bot.score_action(rook, ctx) > bot.score_action(pawn, ctx)

    def test_avoids_hanging_a_piece(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d1", "white", "queen")
        _put(game_state, "a7", "black", "rook")        # covers the whole 7th rank

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        into_danger = MovePiece(player_id="white", source=P("d1"), target=P("d7"))
        safe = MovePiece(player_id="white", source=P("d1"), target=P("d5"))
        assert bot.score_action(safe, ctx) > bot.score_action(into_danger, ctx)

    def test_avoids_an_enemy_trap_radius(self, game_state: GameState):
        from game.core.state import TrapInstance

        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a1", "white", "rook")
        game_state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="pit_trap", position=P("a5"),
            radius=1, trigger_condition="enter_radius", charges=1,
        ))

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        into_trap = MovePiece(player_id="white", source=P("a1"), target=P("a4"))
        clear_of_it = MovePiece(player_id="white", source=P("a1"), target=P("a3"))
        assert bot.score_action(clear_of_it, ctx) > bot.score_action(into_trap, ctx)

    def test_ignores_its_own_traps(self, game_state: GameState):
        from game.core.state import TrapInstance

        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a1", "white", "rook")
        game_state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="pit_trap", position=P("a5"),
            radius=1, trigger_condition="enter_radius", charges=1,
        ))

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        near_own_trap = MovePiece(player_id="white", source=P("a1"), target=P("a4"))
        elsewhere = MovePiece(player_id="white", source=P("a1"), target=P("a3"))
        assert bot.score_action(near_own_trap, ctx) == pytest.approx(
            bot.score_action(elsewhere, ctx), abs=0.4
        )

    def test_coronation_beats_ending_preparation(self, game: Game, registry):
        bot = HeuristicBot(seed=0, registry=registry)
        bot.player_id = "white"
        ctx = EvalContext(obs=game.get_observation("white"), registry=registry)
        coronate = CoronateKing(
            player_id="white",
            king_card_id=game.state.get_player("white").king_pool[0].king_card_id,
        )
        assert bot.score_action(coronate, ctx) > bot.score_action(
            EndPreparation(player_id="white"), ctx
        )

    def test_promotes_to_a_queen(self, game: Game):
        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ctx = EvalContext(obs=game.get_observation("white"))
        queen = PromotePawn(player_id="white", position=P("a8"), piece_type="queen")
        knight = PromotePawn(player_id="white", position=P("a8"), piece_type="knight")
        assert bot.score_action(queen, ctx) > bot.score_action(knight, ctx)

    def test_dismissing_a_monster_is_discouraged(self, game: Game):
        from game.core.actions import DismissMonster

        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ctx = EvalContext(obs=game.get_observation("white"))
        dismiss = DismissMonster(player_id="white", unit_position=P("d1"))
        assert bot.score_action(dismiss, ctx) < bot.score_action(
            EndPreparation(player_id="white"), ctx
        )

    def test_strike_is_the_best_duel_action(self, game: Game):
        from game.core.actions import FinalDuelAction

        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        ctx = EvalContext(obs=game.get_observation("white"))
        strike = FinalDuelAction(player_id="white", duel_action_type="strike")
        advance = FinalDuelAction(player_id="white", duel_action_type="advance")
        assert bot.score_action(strike, ctx) > bot.score_action(advance, ctx)

    def test_repeating_a_move_is_penalised(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a1", "white", "rook")

        bot = self._bot()
        ctx = _ctx(game_state, "white")
        move = MovePiece(player_id="white", source=P("a1"), target=P("a3"))
        fresh = bot.score_action(move, ctx)
        bot._remember(MovePiece(player_id="white", source=P("a3"), target=P("a1")))
        assert bot.score_action(move, ctx) == pytest.approx(
            fresh + ActionBias.REPETITION
        )


# ─────────────────────────────────────────────────────────────────────────────
# Plays a real game
# ─────────────────────────────────────────────────────────────────────────────

class TestPlaysARealGame:
    def test_mirror_match_runs_without_crashing(self, registry):
        from game.sim import acting_player

        game = Game.new(seed=31, registry=registry)
        bots = {}
        for i, pid in enumerate(("white", "black")):
            bot = HeuristicBot(seed=i, registry=registry)
            bot.player_id = pid
            bots[pid] = bot

        for _ in range(60):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            action = bots[pid].choose_action(game.get_observation(pid), legal)
            try:
                game.execute(action)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"HeuristicBot produced an unusable action: {exc}")

        assert game.state.turn_number >= 2

    def test_it_actually_captures_material(self, registry):
        """A greedy bot should take pieces — that is the whole deliverable."""
        from game.sim import acting_player

        game = Game.new(seed=41, registry=registry)
        white = HeuristicBot(seed=1, registry=registry)
        white.player_id = "white"
        from game.ai.random_bot import RandomBot
        black = RandomBot(seed=2)
        black.player_id = "black"
        bots = {"white": white, "black": black}

        for _ in range(120):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            action = bots[pid].choose_action(game.get_observation(pid), legal)
            try:
                game.execute(action)
            except Exception:
                break

        stats = game.match_stats()
        assert stats.players["white"].pieces_taken > 0

    def test_never_chooses_an_action_outside_the_offered_list(self, registry):
        from game.sim import acting_player

        game = Game.new(seed=53, registry=registry)
        bot = HeuristicBot(seed=3, registry=registry)

        for _ in range(40):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            bot.player_id = pid
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            action = bot.choose_action(game.get_observation(pid), legal)
            assert any(action is a for a in legal)
            try:
                game.execute(action)
            except Exception:
                break

    def test_handles_the_discard_phase(self, registry):
        """DISCARD offers only DiscardCard actions — the scorer must rank them."""
        from game.core.actions import DiscardCard

        game = Game.new(seed=61, registry=registry)
        bot = HeuristicBot(seed=1, registry=registry)
        bot.player_id = "white"
        obs = game.get_observation("white")
        hand = list(obs.own_hand)
        actions = [DiscardCard(player_id="white", card_id=c) for c in hand]
        chosen = bot.choose_action(obs, actions)
        assert chosen in actions
