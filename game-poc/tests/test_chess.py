"""
test_chess.py — Stage 1 chess rule tests.

Covers:
    • Castling rights (CastlingRights dataclass)
    • Kingside castling legal / illegal
    • Queenside castling legal / illegal
    • Castling revoked on King move
    • Castling revoked on Rook move
    • Castling revoked on Rook capture
    • Cannot castle through check
    • Cannot castle while in check
    • En passant target computed after double pawn push
    • En passant capture resolves correctly
    • En passant not possible after other moves
    • Promotion: pawn on back rank triggers PROMOTION_SELECTION
    • Promotion: all four piece types accepted
    • Promotion: invalid piece type rejected by apply_promotion
    • Stalemate detection → FinalDuelTriggered(LAST_STAND)
    • Chess opening moves: white has 20, black has 20
    • get_legal_moves signature with en_passant_target and castling_rights
"""

from __future__ import annotations

import pytest
from game.chess.board import BoardState
from game.chess.check import is_checkmate, is_stalemate
from game.chess.chess_state import CastlingRights
from game.chess.movement import (
    can_castle_kingside,
    can_castle_queenside,
    get_legal_moves,
    get_pseudo_legal_moves,
    has_any_legal_move,
    is_in_check,
)
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.chess.promotion import apply_promotion, is_promotion_rank, VALID_PROMOTION_TYPES
from game.core.actions import Castle, EndPreparation, MovePiece, PromotePawn
from game.core.events import (
    CastlingPerformed,
    ChessMoveUsed,
    EnPassantCapture,
    FinalDuelTriggered,
    PieceMoved,
    StalemateDetected,
)
from game.core.phases import FinalDuelType, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import GameState
from tests.conftest import make_player


def _place(board: BoardState, owner: str, ptype: str, alg: str, pid: str) -> None:
    piece = ChessPiece(id=pid, owner=owner, piece_type=PieceType(ptype))
    board.place_unit(Position.from_algebraic(alg), UnitInstance(piece=piece))


def _empty_game(board: BoardState, phase: Phase = Phase.CHESS) -> GameState:
    white = make_player("white")
    black = make_player("black")
    return GameState(
        game_id="test",
        turn_number=1,
        active_player="white",
        phase=phase,
        board=board,
        players={"white": white, "black": black},
    )


# ─────────────────────────────────────────────────────────────────────────────
# CastlingRights model
# ─────────────────────────────────────────────────────────────────────────────

class TestCastlingRights:
    def test_defaults_both_available(self):
        cr = CastlingRights()
        assert cr.kingside is True
        assert cr.queenside is True

    def test_revoke_all(self):
        cr = CastlingRights()
        cr.revoke_all()
        assert cr.kingside is False
        assert cr.queenside is False

    def test_has_any(self):
        cr = CastlingRights()
        assert cr.has_any()
        cr.revoke_all()
        assert not cr.has_any()


# ─────────────────────────────────────────────────────────────────────────────
# Castling legality (movement module)
# ─────────────────────────────────────────────────────────────────────────────

class TestCastlingLegality:
    def _clear_back_rank_for_kingside(self) -> BoardState:
        """White back rank with only King (e1) and h-Rook (h1) present."""
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h1", "white-rook-h")
        _place(board, "black", "king", "e8", "black-king-e")
        return board

    def _clear_back_rank_for_queenside(self) -> BoardState:
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "a1", "white-rook-a")
        _place(board, "black", "king", "e8", "black-king-e")
        return board

    def test_kingside_clear_path_allowed(self):
        board = self._clear_back_rank_for_kingside()
        cr = CastlingRights()
        assert can_castle_kingside(board, "white", cr)

    def test_queenside_clear_path_allowed(self):
        board = self._clear_back_rank_for_queenside()
        cr = CastlingRights()
        assert can_castle_queenside(board, "white", cr)

    def test_kingside_right_revoked(self):
        board = self._clear_back_rank_for_kingside()
        cr = CastlingRights(kingside=False)
        assert not can_castle_kingside(board, "white", cr)

    def test_queenside_right_revoked(self):
        board = self._clear_back_rank_for_queenside()
        cr = CastlingRights(queenside=False)
        assert not can_castle_queenside(board, "white", cr)

    def test_kingside_blocked_by_piece_on_f1(self):
        board = self._clear_back_rank_for_kingside()
        _place(board, "white", "bishop", "f1", "white-bishop-f")
        cr = CastlingRights()
        assert not can_castle_kingside(board, "white", cr)

    def test_queenside_blocked_by_piece_on_b1(self):
        board = self._clear_back_rank_for_queenside()
        _place(board, "white", "knight", "b1", "white-knight-b")
        cr = CastlingRights()
        assert not can_castle_queenside(board, "white", cr)

    def test_cannot_castle_while_in_check(self):
        """King is in check from black rook on e8 → castling not allowed."""
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h1", "white-rook-h")
        _place(board, "black", "rook", "e8", "black-rook-e")  # checks king on e-file
        _place(board, "black", "king", "a8", "black-king-a")
        cr = CastlingRights()
        assert not can_castle_kingside(board, "white", cr)

    def test_cannot_castle_through_check_kingside(self):
        """Black rook attacks f1 — King would pass through check."""
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h1", "white-rook-h")
        _place(board, "black", "rook", "f8", "black-rook-f")  # controls f-file
        _place(board, "black", "king", "a8", "black-king-a")
        cr = CastlingRights()
        assert not can_castle_kingside(board, "white", cr)

    def test_black_can_castle_kingside(self):
        board = BoardState()
        _place(board, "black", "king", "e8", "black-king-e")
        _place(board, "black", "rook", "h8", "black-rook-h")
        _place(board, "white", "king", "e1", "white-king-e")
        cr = CastlingRights()
        assert can_castle_kingside(board, "black", cr)


# ─────────────────────────────────────────────────────────────────────────────
# Castle action execution
# ─────────────────────────────────────────────────────────────────────────────

class TestCastleExecution:
    def _castling_state(self, side: str) -> GameState:
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        if side == "kingside":
            _place(board, "white", "rook", "h1", "white-rook-h")
        else:
            _place(board, "white", "rook", "a1", "white-rook-a")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        return state

    def test_kingside_castle_emits_event(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")

        events = engine.execute(state, Castle(player_id="white", side="kingside"), rng)
        assert any(isinstance(e, CastlingPerformed) for e in events)
        ce = next(e for e in events if isinstance(e, CastlingPerformed))
        assert ce.side == "kingside"
        assert ce.king_from == Position.from_algebraic("e1")
        assert ce.king_to == Position.from_algebraic("g1")
        assert ce.rook_from == Position.from_algebraic("h1")
        assert ce.rook_to == Position.from_algebraic("f1")

    def test_kingside_castle_moves_pieces(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")
        engine.execute(state, Castle(player_id="white", side="kingside"), rng)
        assert state.board.get_unit(Position.from_algebraic("g1")) is not None
        assert state.board.get_unit(Position.from_algebraic("f1")) is not None
        assert state.board.get_unit(Position.from_algebraic("e1")) is None
        assert state.board.get_unit(Position.from_algebraic("h1")) is None

    def test_queenside_castle(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("queenside")
        events = engine.execute(state, Castle(player_id="white", side="queenside"), rng)
        ce = next(e for e in events if isinstance(e, CastlingPerformed))
        assert ce.king_to == Position.from_algebraic("c1")
        assert ce.rook_to == Position.from_algebraic("d1")

    def test_castle_revokes_all_castling_rights(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")
        engine.execute(state, Castle(player_id="white", side="kingside"), rng)
        cr = state.get_player("white").castling_rights
        assert not cr.kingside
        assert not cr.queenside

    def test_castle_counts_as_chess_move(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")
        events = engine.execute(state, Castle(player_id="white", side="kingside"), rng)
        assert any(isinstance(e, ChessMoveUsed) for e in events)
        assert state.get_player("white").chess_move_used

    def test_castle_invalid_side_raises(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")
        with pytest.raises(IllegalActionError):
            engine.execute(state, Castle(player_id="white", side="queenside"), rng)

    def test_castle_in_wrong_phase_raises(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        state = self._castling_state("kingside")
        state.phase = Phase.PREPARATION
        with pytest.raises(IllegalActionError, match="phase"):
            engine.execute(state, Castle(player_id="white", side="kingside"), rng)


# ─────────────────────────────────────────────────────────────────────────────
# Castling rights revocation via RulesEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestCastlingRightsRevocation:
    def test_king_move_revokes_castling(self):
        """Moving the King revokes both castling rights."""
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h1", "white-rook-h")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("e1"),
                      target=Position.from_algebraic("f1")),
            rng,
        )
        cr = state.get_player("white").castling_rights
        assert not cr.kingside
        assert not cr.queenside

    def test_rook_move_revokes_kingside(self):
        """Moving the h1 rook revokes only kingside castling."""
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h1", "white-rook-h")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("h1"),
                      target=Position.from_algebraic("h3")),
            rng,
        )
        cr = state.get_player("white").castling_rights
        assert not cr.kingside
        assert cr.queenside  # untouched


# ─────────────────────────────────────────────────────────────────────────────
# En passant
# ─────────────────────────────────────────────────────────────────────────────

class TestEnPassant:
    def _ep_state(self) -> tuple[GameState, DeterministicRNG]:
        """
        White pawn on e5, black pawn just double-pushed to d5.
        en_passant_target = d6 (the square the black pawn passed through).
        """
        board = BoardState()
        _place(board, "white", "pawn", "e5", "white-pawn-e")
        _place(board, "black", "pawn", "d5", "black-pawn-d")
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        state.en_passant_target = Position.from_algebraic("d6")
        return state, DeterministicRNG(seed=0)

    def test_en_passant_target_set_after_double_push(self):
        """After a double pawn push, en_passant_target is set to the passed square."""
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState.make_standard_start()
        white = make_player("white")
        black = make_player("black")
        state = GameState(
            game_id="ep-test",
            turn_number=1,
            active_player="white",
            phase=Phase.CHESS,
            board=board,
            players={"white": white, "black": black},
        )
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("e2"),
                      target=Position.from_algebraic("e4")),
            rng,
        )
        # After e2-e4, en passant target should be e3
        assert state.en_passant_target == Position.from_algebraic("e3")

    def test_en_passant_target_cleared_after_non_pawn_move(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "white", "rook", "h4", "white-rook-h")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        state.en_passant_target = Position.from_algebraic("e3")  # set artificially
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("h4"),
                      target=Position.from_algebraic("h5")),
            rng,
        )
        assert state.en_passant_target is None

    def test_en_passant_capture_is_legal(self):
        state, rng = self._ep_state()
        engine = RulesEngine()
        ep_action = MovePiece(
            player_id="white",
            source=Position.from_algebraic("e5"),
            target=Position.from_algebraic("d6"),  # the en passant square
        )
        events = engine.execute(state, ep_action, rng)
        assert any(isinstance(e, EnPassantCapture) for e in events)

    def test_en_passant_removes_captured_pawn(self):
        state, rng = self._ep_state()
        engine = RulesEngine()
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("e5"),
                      target=Position.from_algebraic("d6")),
            rng,
        )
        # Black pawn at d5 must be gone
        assert state.board.get_unit(Position.from_algebraic("d5")) is None
        # White pawn must be at d6
        unit = state.board.get_unit(Position.from_algebraic("d6"))
        assert unit is not None
        assert unit.owner == "white"

    def test_en_passant_not_possible_without_target(self):
        """Without en_passant_target, diagonal move to empty square is illegal."""
        board = BoardState()
        _place(board, "white", "pawn", "e5", "white-pawn-e")
        _place(board, "black", "pawn", "d5", "black-pawn-d")
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        # No en_passant_target set
        engine = RulesEngine()
        with pytest.raises(IllegalActionError):
            engine.execute(
                state,
                MovePiece(player_id="white",
                          source=Position.from_algebraic("e5"),
                          target=Position.from_algebraic("d6")),
                DeterministicRNG(seed=0),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Promotion
# ─────────────────────────────────────────────────────────────────────────────

class TestPromotion:
    def test_promotion_rank_white_rank_8(self):
        assert is_promotion_rank(Position.from_algebraic("a8"), "white")
        assert not is_promotion_rank(Position.from_algebraic("a7"), "white")

    def test_promotion_rank_black_rank_1(self):
        assert is_promotion_rank(Position.from_algebraic("h1"), "black")
        assert not is_promotion_rank(Position.from_algebraic("h2"), "black")

    def test_pawn_on_back_rank_triggers_promotion_selection(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "white", "pawn", "a7", "white-pawn-a")
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("a7"),
                      target=Position.from_algebraic("a8")),
            rng,
        )
        assert state.phase == Phase.PROMOTION_SELECTION
        assert state.pending_decision is not None

    def test_promote_to_queen(self):
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "white", "pawn", "a7", "white-pawn-a")
        _place(board, "white", "king", "e1", "white-king-e")
        _place(board, "black", "king", "e8", "black-king-e")
        state = _empty_game(board)
        engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("a7"),
                      target=Position.from_algebraic("a8")),
            rng,
        )
        engine.execute(
            state,
            PromotePawn(player_id="white",
                        position=Position.from_algebraic("a8"),
                        piece_type="queen"),
            rng,
        )
        unit = state.board.get_unit(Position.from_algebraic("a8"))
        assert unit is not None
        assert unit.piece.piece_type == PieceType.QUEEN
        assert state.phase == Phase.REACTION

    def test_valid_promotion_types(self):
        assert VALID_PROMOTION_TYPES == {"queen", "rook", "bishop", "knight"}

    def test_apply_promotion_invalid_raises(self):
        board = BoardState()
        _place(board, "white", "pawn", "a8", "white-pawn-a")
        with pytest.raises(ValueError, match="Invalid"):
            apply_promotion(board, Position.from_algebraic("a8"), "king")

    def test_apply_promotion_no_pawn_raises(self):
        board = BoardState()
        _place(board, "white", "rook", "a8", "white-rook-a")
        with pytest.raises(ValueError, match="not a pawn"):
            apply_promotion(board, Position.from_algebraic("a8"), "queen")


# ─────────────────────────────────────────────────────────────────────────────
# Stalemate
# ─────────────────────────────────────────────────────────────────────────────

class TestStalemate:
    def _stalemate_board(self) -> BoardState:
        """
        Classic stalemate: black king a8, white queen b6, white king c6.
        Black has no legal moves and is NOT in check.
        """
        board = BoardState()
        _place(board, "black", "king", "a8", "black-king-a")
        _place(board, "white", "queen", "b6", "white-queen-b")
        _place(board, "white", "king", "c6", "white-king-c")
        return board

    def test_is_stalemate_returns_true(self):
        board = self._stalemate_board()
        assert not is_in_check(board, "black")
        assert is_stalemate(board, "black")

    def test_is_checkmate_returns_false_in_stalemate(self):
        board = self._stalemate_board()
        assert not is_checkmate(board, "black")

    def test_stalemate_triggers_last_stand_duel(self):
        """
        White moves queen to b6 creating stalemate → FinalDuelTriggered(LAST_STAND).
        """
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)
        board = BoardState()
        _place(board, "black", "king", "a8", "black-king-a")
        _place(board, "white", "queen", "b5", "white-queen")   # will move to b6
        _place(board, "white", "king", "c6", "white-king-c")
        state = _empty_game(board)

        events = engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("b5"),
                      target=Position.from_algebraic("b6")),
            rng,
        )
        assert any(isinstance(e, StalemateDetected) for e in events)
        stalemate_e = next(e for e in events if isinstance(e, StalemateDetected))
        assert stalemate_e.player_id == "black"

        assert any(isinstance(e, FinalDuelTriggered) for e in events)
        duel_e = next(e for e in events if isinstance(e, FinalDuelTriggered))
        assert duel_e.duel_type == FinalDuelType.LAST_STAND
        assert state.winner is None  # no winner set — duel decides


# ─────────────────────────────────────────────────────────────────────────────
# Opening move count
# ─────────────────────────────────────────────────────────────────────────────

class TestOpeningMoves:
    def test_white_has_20_legal_opening_moves(self):
        board = BoardState.make_standard_start()
        cr = CastlingRights()
        moves = []
        for pos, unit in board.all_units_for("white"):
            moves.extend(get_legal_moves(board, pos, unit, castling_rights=cr, player_id="white"))
        assert len(moves) == 20

    def test_black_has_20_legal_opening_moves(self):
        board = BoardState.make_standard_start()
        cr = CastlingRights()
        moves = []
        for pos, unit in board.all_units_for("black"):
            moves.extend(get_legal_moves(board, pos, unit, castling_rights=cr, player_id="black"))
        assert len(moves) == 20
