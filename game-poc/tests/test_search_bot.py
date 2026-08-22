"""
AI Stage 2 — SearchBot and the board search (README §47)
==========================================================

Covers:
    • The search position is rebuilt from public state only, and the
      make/unmake forward model is exactly reversible.
    • The leaf evaluation is antisymmetric — negamax is meaningless if it
      is not.
    • The search actually searches: it sees a recapture the Stage 1 static
      threat map cannot see, and it takes free material anyway.
    • Budgets (nodes, time, depth) are respected, and iterative deepening
      always leaves a usable answer behind.
    • The PlayerController contract, reproducibility, and a real game loop.
"""

from __future__ import annotations

import pytest

from game.ai.controller import PlayerController
from game.ai.heuristic_bot import HeuristicBot
from game.ai.search import (
    KING_CAPTURE_VALUE,
    Hazards,
    SearchLimits,
    Searcher,
    board_for_search,
)
from game.ai.search_bot import SearchBot
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import EndTurn, MovePiece
from game.core.game import Game
from game.core.observation import build_observation
from game.core.phases import ConstructionStatus, Phase, PieceType
from game.core.state import BuildingInstance, GameState, TrapInstance


P = Position.from_algebraic


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _obs(state: GameState, player: str = "white", registry=None):
    return build_observation(state, player, registry=registry)


def _put(state: GameState, square: str, owner: str, piece_type: str) -> None:
    piece = ChessPiece(
        id=f"{owner}-{piece_type}-{square}",
        owner=owner,
        piece_type=PieceType(piece_type),
    )
    state.board.place_unit(P(square), UnitInstance(piece=piece))


def _clear(state: GameState) -> None:
    state.board = BoardState()


def _bot(seed: int = 0, player: str = "white", registry=None, **limits) -> SearchBot:
    bot = SearchBot(seed=seed, registry=registry, limits=SearchLimits(**limits)) \
        if limits else SearchBot(seed=seed, registry=registry)
    bot.player_id = player
    return bot


def _searcher(state: GameState, player: str = "white", **limits) -> Searcher:
    obs = _obs(state, player)
    return Searcher(
        board=board_for_search(obs),
        me=player,
        hazards=Hazards.from_observation(obs),
        limits=SearchLimits(**limits) if limits else SearchLimits(),
    )


def _move(source: str, target: str, player: str = "white") -> MovePiece:
    return MovePiece(player_id=player, source=P(source), target=P(target))


# ─────────────────────────────────────────────────────────────────────────────
# Position reconstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestBoardForSearch:
    def test_every_unit_is_reconstructed(self, registry):
        game = Game.new(seed=3, registry=registry)
        board = board_for_search(game.get_observation("white"))
        assert len(board.all_units()) == len(game.state.board.all_units())

    def test_a_complete_building_becomes_a_wall(self, game_state: GameState):
        game_state.buildings.append(BuildingInstance(
            id="b1", owner="black", building_card_id="fortress",
            position=P("d5"), status=ConstructionStatus.COMPLETE,
            integrity=2, max_integrity=2,
        ))
        board = board_for_search(_obs(game_state))
        assert board.get_square(P("d5")).complete_building_owner == "black"

    def test_an_under_construction_building_is_not_a_wall(self, game_state: GameState):
        game_state.buildings.append(BuildingInstance(
            id="b1", owner="black", building_card_id="fortress",
            position=P("d5"), status=ConstructionStatus.UNDER_CONSTRUCTION,
            integrity=2, max_integrity=2,
        ))
        board = board_for_search(_obs(game_state))
        assert board.get_square(P("d5")).complete_building_owner is None

    def test_square_effects_come_across_as_engine_tags(self, game_state: GameState):
        game_state.board.get_square(P("e4")).add_effect("frozen:2:black")
        board = board_for_search(_obs(game_state))
        effects = board.get_square(P("e4")).temporary_effects
        assert any(e.startswith("frozen:") for e in effects)

    def test_reconstruction_does_not_alias_the_engine_board(self, game_state: GameState):
        board = board_for_search(_obs(game_state))
        board.remove_unit(P("e2"))
        assert game_state.board.get_unit(P("e2")) is not None


class TestHazards:
    def test_an_enemy_trap_radius_is_dangerous_to_the_other_side(self, game_state: GameState):
        game_state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="pit_trap", position=P("d4"),
            radius=1, trigger_condition="enter_radius", charges=1,
        ))
        hazards = Hazards.from_observation(_obs(game_state))
        assert P("d4") in hazards.trapped["white"]
        assert P("c3") in hazards.trapped["white"]     # inside the radius
        assert P("d4") not in hazards.trapped["black"]  # its owner walks free
        assert P("a8") not in hazards.trapped["white"]

    def test_a_hostile_zone_is_dangerous_to_the_other_side(self, game_state: GameState):
        game_state.board.get_square(P("e5")).add_effect("cursed:3:black")
        hazards = Hazards.from_observation(_obs(game_state))
        assert P("e5") in hazards.hazardous["white"]
        assert P("e5") not in hazards.hazardous["black"]


# ─────────────────────────────────────────────────────────────────────────────
# The forward model
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeUnmake:
    def test_a_quiet_move_is_exactly_reversible(self, game_state: GameState):
        searcher = _searcher(game_state)
        before = searcher._position_key()
        undo = searcher.make(P("e2"), P("e4"))
        assert searcher.board.get_unit(P("e4")) is not None
        assert searcher.board.get_unit(P("e2")) is None
        searcher.unmake(P("e2"), P("e4"), undo)
        assert searcher._position_key() == before

    def test_a_capture_is_exactly_reversible(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d1", "white", "queen")
        _put(game_state, "d7", "black", "rook")

        searcher = _searcher(game_state)
        before = searcher._position_key()
        undo = searcher.make(P("d1"), P("d7"))
        assert searcher.board.get_unit(P("d7")).owner == "white"
        searcher.unmake(P("d1"), P("d7"), undo)
        assert searcher._position_key() == before
        assert searcher.board.get_unit(P("d7")).owner == "black"

    def test_promotion_is_auto_queen_and_reversible(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a7", "white", "pawn")

        searcher = _searcher(game_state)
        before = searcher._position_key()
        undo = searcher.make(P("a7"), P("a8"))
        assert searcher.board.get_unit(P("a8")).piece.piece_type is PieceType.QUEEN
        searcher.unmake(P("a7"), P("a8"), undo)
        assert searcher._position_key() == before
        assert searcher.board.get_unit(P("a7")).piece.piece_type is PieceType.PAWN

    def test_castling_is_exactly_reversible(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "h1", "white", "rook")
        _put(game_state, "e8", "black", "king")

        searcher = _searcher(game_state)
        before = searcher._position_key()
        undo = searcher.make_castle("white", "kingside")
        assert searcher.board.get_unit(P("g1")).piece.piece_type is PieceType.KING
        assert searcher.board.get_unit(P("f1")).piece.piece_type is PieceType.ROOK
        searcher.unmake_castle(undo)
        assert searcher._position_key() == before

    def test_the_occupancy_map_tracks_the_board(self, game_state: GameState):
        searcher = _searcher(game_state)
        undo = searcher.make(P("e2"), P("e4"))
        assert set(searcher._units) == {pos for pos, _ in searcher.board.all_units()}
        searcher.unmake(P("e2"), P("e4"), undo)
        assert set(searcher._units) == {pos for pos, _ in searcher.board.all_units()}


# ─────────────────────────────────────────────────────────────────────────────
# Leaf evaluation
# ─────────────────────────────────────────────────────────────────────────────

class TestLeafEvaluation:
    def test_evaluation_is_antisymmetric(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "white", "queen")
        _put(game_state, "a7", "black", "rook")
        _put(game_state, "b2", "white", "pawn")

        white_view = _searcher(game_state, "white").evaluate()
        black_view = _searcher(game_state, "black").evaluate()
        assert white_view == pytest.approx(-black_view)

    def test_material_dominates(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "white", "queen")
        assert _searcher(game_state, "white").evaluate() > 8.0

    def test_standing_in_an_enemy_trap_costs_something(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "white", "rook")
        clean = _searcher(game_state, "white").evaluate()

        game_state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="pit_trap", position=P("d4"),
            radius=1, trigger_condition="enter_radius", charges=1,
        ))
        assert _searcher(game_state, "white").evaluate() < clean

    def test_a_lost_king_is_catastrophic(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "white", "queen")
        assert _searcher(game_state, "white").evaluate() <= -KING_CAPTURE_VALUE


# ─────────────────────────────────────────────────────────────────────────────
# The search sees further than the Stage 1 threat map
# ─────────────────────────────────────────────────────────────────────────────

def _poisoned_pawn(state: GameState) -> None:
    """
    White's Queen can take a Pawn on d7 that is "defended" by a Rook the
    Pawn itself is blocking.  A static threat map (AI Stage 1) reads d7 as
    undefended and grabs it; one ply of search sees Rxd7.
    """
    _clear(state)
    _put(state, "e1", "white", "king")
    _put(state, "d1", "white", "queen")
    _put(state, "h8", "black", "king")
    _put(state, "d7", "black", "pawn")
    _put(state, "d8", "black", "rook")


class TestSearchDepthMatters:
    GRAB = _move("d1", "d7")
    QUIET = _move("d1", "d4")

    def test_the_static_bot_grabs_the_poisoned_pawn(self, game_state: GameState):
        _poisoned_pawn(game_state)
        bot = HeuristicBot(seed=0)
        bot.player_id = "white"
        chosen = bot.choose_action(_obs(game_state), [self.GRAB, self.QUIET])
        assert chosen is self.GRAB, (
            "this position only proves anything if the Stage 1 bot falls for it"
        )

    def test_the_search_bot_declines_it(self, game_state: GameState):
        _poisoned_pawn(game_state)
        bot = _bot(max_depth=2, max_nodes=20_000, max_seconds=5.0)
        chosen = bot.choose_action(_obs(game_state), [self.GRAB, self.QUIET])
        assert chosen is self.QUIET

    def test_quiescence_alone_is_enough_to_see_the_recapture(self, game_state: GameState):
        """Depth 1 — the recapture is found by the capture-only extension."""
        _poisoned_pawn(game_state)
        bot = _bot(max_depth=1, quiescence_depth=2, max_nodes=20_000, max_seconds=5.0)
        assert bot.choose_action(_obs(game_state), [self.GRAB, self.QUIET]) is self.QUIET

    def test_without_any_lookahead_it_grabs_the_pawn_too(self, game_state: GameState):
        """The control: no depth and no quiescence collapses to greed."""
        _poisoned_pawn(game_state)
        bot = _bot(max_depth=1, quiescence_depth=0, max_nodes=20_000, max_seconds=5.0)
        assert bot.choose_action(_obs(game_state), [self.GRAB, self.QUIET]) is self.GRAB

    def test_it_still_takes_genuinely_free_material(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "a1", "white", "rook")
        _put(game_state, "h8", "black", "king")
        _put(game_state, "a7", "black", "queen")

        take = _move("a1", "a7")
        quiet = _move("a1", "a4")
        bot = _bot(max_depth=2, max_nodes=20_000, max_seconds=5.0)
        assert bot.choose_action(_obs(game_state), [take, quiet]) is take

    def test_it_goes_for_the_king(self, game_state: GameState):
        """Capturing the King forces the Final Duel (README §22) — always worth it."""
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "a1", "white", "rook")
        _put(game_state, "a8", "black", "king")
        _put(game_state, "h7", "black", "pawn")

        duel = _move("a1", "a8")
        pawn_hunt = _move("a1", "h1")
        bot = _bot(max_depth=2, max_nodes=20_000, max_seconds=5.0)
        assert bot.choose_action(_obs(game_state), [duel, pawn_hunt]) is duel

    def test_moving_beats_passing_when_there_is_something_to_win(self, game_state: GameState):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "a1", "white", "rook")
        _put(game_state, "h8", "black", "king")
        _put(game_state, "a7", "black", "queen")

        take = _move("a1", "a7")
        bot = _bot(max_depth=2, max_nodes=20_000, max_seconds=5.0)
        chosen = bot.choose_action(
            _obs(game_state), [take, EndTurn(player_id="white")]
        )
        assert chosen is take


# ─────────────────────────────────────────────────────────────────────────────
# Budgets
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgets:
    def test_the_node_ceiling_is_respected(self, registry):
        game = Game.new(seed=5, registry=registry)
        game.advance_to_preparation()
        from game.core.actions import EndPreparation
        game.execute(EndPreparation(player_id="white"))
        assert game.state.phase is Phase.CHESS

        bot = _bot(registry=registry, max_depth=6, max_nodes=250, max_seconds=30.0)
        bot.choose_action(game.get_observation("white"), game.get_legal_actions("white"))
        # One node of overshoot per recursion level is inherent to checking
        # the budget on entry; an order of magnitude would not be.
        assert bot.last_search.nodes < 250 * 2
        assert bot.last_search.aborted

    def test_iterative_deepening_always_leaves_an_answer(self, registry):
        game = Game.new(seed=5, registry=registry)
        game.advance_to_preparation()
        from game.core.actions import EndPreparation
        game.execute(EndPreparation(player_id="white"))

        legal = game.get_legal_actions("white")
        bot = _bot(registry=registry, max_depth=9, max_nodes=40, max_seconds=30.0)
        chosen = bot.choose_action(game.get_observation("white"), legal)
        assert any(chosen is a for a in legal)

    def test_a_generous_budget_reaches_the_requested_depth(self, game_state: GameState):
        _poisoned_pawn(game_state)
        bot = _bot(max_depth=3, max_nodes=100_000, max_seconds=30.0)
        bot.choose_action(_obs(game_state), [_move("d1", "d7"), _move("d1", "d4")])
        assert bot.last_search.depth == 3
        assert not bot.last_search.aborted
        assert bot.last_search.nodes > 0


# ─────────────────────────────────────────────────────────────────────────────
# Controller contract
# ─────────────────────────────────────────────────────────────────────────────

class TestControllerContract:
    def test_is_a_player_controller(self):
        assert issubclass(SearchBot, PlayerController)

    def test_empty_legal_list_is_an_engine_bug(self, game_state: GameState):
        with pytest.raises(ValueError):
            _bot().choose_action(_obs(game_state), [])

    def test_a_forced_action_is_returned_untouched(self, game_state: GameState):
        only = EndTurn(player_id="white")
        assert _bot().choose_action(_obs(game_state), [only]) is only

    def test_returns_an_action_from_the_offered_list(self, registry):
        game = Game.new(seed=9, registry=registry)
        legal = game.get_legal_actions("white")
        chosen = _bot(registry=registry).choose_action(
            game.get_observation("white"), legal
        )
        assert any(chosen is a for a in legal)

    def test_the_same_seed_makes_the_same_choice(self, game_state: GameState):
        _poisoned_pawn(game_state)
        actions = [_move("d1", "d7"), _move("d1", "d4"), _move("d1", "d2")]
        first = _bot(seed=4, max_depth=2).choose_action(_obs(game_state), actions)
        second = _bot(seed=4, max_depth=2).choose_action(_obs(game_state), actions)
        assert first is second

    def test_non_chess_phases_fall_back_to_stage_one(self, registry):
        """
        PREPARATION is hidden-information territory (README §47), so
        SearchBot must decide it exactly as HeuristicBot does.
        """
        game = Game.new(seed=13, registry=registry)
        game.advance_to_preparation()
        assert game.state.phase is Phase.PREPARATION
        obs = game.get_observation("white")
        legal = game.get_legal_actions("white")

        searcher_bot = _bot(seed=2, registry=registry)
        static_bot = HeuristicBot(seed=2, registry=registry)
        static_bot.player_id = "white"
        assert searcher_bot.choose_action(obs, legal) is static_bot.choose_action(obs, legal)

    def test_it_plays_a_real_game_without_crashing(self, registry):
        from game.sim import acting_player

        game = Game.new(seed=31, registry=registry)
        bots = {}
        for pid in ("white", "black"):
            bots[pid] = _bot(
                seed=hash(pid) % 100, player=pid, registry=registry,
                max_depth=2, max_nodes=150, max_seconds=1.0,
            )

        for _ in range(30):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            action = bots[pid].choose_action(game.get_observation(pid), legal)
            assert any(action is a for a in legal)
            try:
                game.execute(action)
            except Exception:
                break


# ─────────────────────────────────────────────────────────────────────────────
# Harness wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestHarnessWiring:
    def test_the_sim_factory_knows_the_search_bot(self, registry):
        from game.sim import make_controller

        bot = make_controller("search", seed=3, player_id="black", registry=registry)
        assert isinstance(bot, SearchBot)
        assert bot.player_id == "black"

    def test_the_sim_factory_rejects_nonsense(self):
        from game.sim import make_controller

        with pytest.raises(ValueError):
            make_controller("mcts", seed=0, player_id="white")
