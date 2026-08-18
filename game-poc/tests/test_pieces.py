"""
test_pieces.py — Piece model and board state tests.

Covers:
    • Position creation and algebraic conversion.
    • Position immutability and out-of-range rejection.
    • ChessPiece identity and vessel eligibility.
    • UnitInstance monster wrapping and status management.
    • SquareState occupancy helpers.
    • BoardState: standard start layout, unit placement, movement, capture.
    • BoardState: find_king, find_unit_by_piece_id, all_units_for.
"""

import pytest
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import PieceType


class TestPosition:
    def test_from_algebraic_e4(self):
        pos = Position.from_algebraic("e4")
        assert pos == Position(4, 3)

    def test_to_algebraic_round_trip(self):
        for file in range(8):
            for rank in range(8):
                pos = Position(file, rank)
                assert Position.from_algebraic(pos.to_algebraic()) == pos

    def test_invalid_notation_rejected(self):
        with pytest.raises(ValueError):
            Position.from_algebraic("z9")
        with pytest.raises(ValueError):
            Position.from_algebraic("e")

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            Position(8, 0)
        with pytest.raises(ValueError):
            Position(0, -1)

    def test_frozen(self):
        pos = Position(0, 0)
        with pytest.raises(Exception):
            pos.file = 1  # type: ignore[misc]

    def test_ordering(self):
        assert Position(0, 0) < Position(0, 1)
        assert Position(0, 3) < Position(1, 0)


class TestChessPiece:
    def test_king_not_valid_vessel(self):
        king = ChessPiece(id="white-king-e", owner="white", piece_type=PieceType.KING)
        assert not king.is_valid_vessel
        assert king.is_king

    def test_bishop_is_valid_vessel(self):
        bishop = ChessPiece(id="white-bishop-c", owner="white", piece_type=PieceType.BISHOP)
        assert bishop.is_valid_vessel
        assert not bishop.is_king

    def test_all_non_king_pieces_valid(self):
        for pt in PieceType:
            piece = ChessPiece(id=f"white-{pt.value}", owner="white", piece_type=pt)
            expected = pt != PieceType.KING
            assert piece.is_valid_vessel == expected


class TestUnitInstance:
    def _bishop(self) -> ChessPiece:
        return ChessPiece(id="white-bishop-c", owner="white", piece_type=PieceType.BISHOP)

    def _pawn(self) -> ChessPiece:
        return ChessPiece(id="white-pawn-a", owner="white", piece_type=PieceType.PAWN)

    def test_plain_unit_has_no_monster(self):
        unit = UnitInstance(piece=self._bishop())
        assert not unit.is_monster
        assert unit.monster_id is None

    def test_pawn_has_builder_available(self):
        unit = UnitInstance(piece=self._pawn())
        assert unit.builder_available

    def test_bishop_has_no_builder(self):
        unit = UnitInstance(piece=self._bishop())
        assert not unit.builder_available

    def test_add_remove_status(self):
        unit = UnitInstance(piece=self._bishop())
        unit.add_status("frozen")
        assert unit.has_status("frozen")
        unit.remove_status("frozen")
        assert not unit.has_status("frozen")

    def test_no_duplicate_statuses(self):
        unit = UnitInstance(piece=self._bishop())
        unit.add_status("shielded")
        unit.add_status("shielded")
        assert unit.statuses.count("shielded") == 1

    def test_monster_assignment(self):
        unit = UnitInstance(piece=self._bishop())
        unit.monster_id = "dark_magician"
        assert unit.is_monster


class TestBoardState:
    def test_standard_start_has_32_pieces(self):
        board = BoardState.make_standard_start()
        units = board.all_units()
        assert len(units) == 32

    def test_white_pawns_on_rank_2(self):
        board = BoardState.make_standard_start()
        for file in range(8):
            unit = board.get_unit(Position(file, 1))
            assert unit is not None
            assert unit.owner == "white"
            assert unit.piece.piece_type == PieceType.PAWN

    def test_black_pawns_on_rank_7(self):
        board = BoardState.make_standard_start()
        for file in range(8):
            unit = board.get_unit(Position(file, 6))
            assert unit is not None
            assert unit.owner == "black"

    def test_find_white_king(self):
        board = BoardState.make_standard_start()
        result = board.find_king("white")
        assert result is not None
        pos, unit = result
        assert pos == Position(4, 0)  # e1
        assert unit.piece.piece_type == PieceType.KING

    def test_find_black_king(self):
        board = BoardState.make_standard_start()
        result = board.find_king("black")
        assert result is not None
        pos, unit = result
        assert pos == Position(4, 7)  # e8

    def test_empty_board_has_no_units(self):
        board = BoardState()
        assert board.all_units() == []

    def test_place_and_remove_unit(self):
        board = BoardState()
        piece = ChessPiece(id="w-king", owner="white", piece_type=PieceType.KING)
        unit = UnitInstance(piece=piece)
        pos = Position.from_algebraic("e1")

        board.place_unit(pos, unit)
        assert board.get_unit(pos) is unit

        removed = board.remove_unit(pos)
        assert removed is unit
        assert board.get_unit(pos) is None

    def test_move_unit_returns_captured(self):
        board = BoardState()
        wp = ChessPiece(id="w-rook", owner="white", piece_type=PieceType.ROOK)
        bp = ChessPiece(id="b-pawn", owner="black", piece_type=PieceType.PAWN)
        wunit = UnitInstance(piece=wp)
        bunit = UnitInstance(piece=bp)

        board.place_unit(Position.from_algebraic("a1"), wunit)
        board.place_unit(Position.from_algebraic("a4"), bunit)

        captured = board.move_unit(
            Position.from_algebraic("a1"),
            Position.from_algebraic("a4"),
        )
        assert captured is bunit
        assert board.get_unit(Position.from_algebraic("a4")) is wunit
        assert board.get_unit(Position.from_algebraic("a1")) is None

    def test_all_squares_always_64(self):
        board = BoardState()
        assert len(board.squares) == 64

    def test_find_unit_by_piece_id(self):
        board = BoardState.make_standard_start()
        result = board.find_unit_by_piece_id("white-king-e")
        assert result is not None
        pos, unit = result
        assert pos == Position(4, 0)
