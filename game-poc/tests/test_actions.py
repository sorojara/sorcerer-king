"""
test_actions.py — Action model and RulesEngine action-execution tests.

Covers:
    • All Action subclasses are instantiable with correct fields.
    • Turn ownership invariant: wrong player → IllegalActionError.
    • Phase ownership invariant: MovePiece in PREPARATION → IllegalActionError.
    • Chess move limit: second MovePiece in same turn → IllegalActionError.
    • Preparation limit: second prep action → IllegalActionError.
    • Vessel ownership invariant: cannot transform opponent's piece.
    • King-not-vessel invariant: cannot summon Monster on King.
    • Pawn-builder invariant: non-pawn cannot StartConstruction.
    • Builder-once invariant: used builder cannot build again.
    • EndPreparation advances phase to CHESS.
    • EndTurn switches active player and emits TurnEnded.
    • MovePiece advances phase to REACTION.
    • King capture triggers FinalDuelTriggered (not GameOver).
"""

from __future__ import annotations

import pytest
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateSpell,
    CoronateKing,
    DeclareRecompose,
    EndPreparation,
    EndTurn,
    MovePiece,
    PlaceTrap,
    SummonMonster,
    StartConstruction,
)
from game.core.events import (
    ChessMoveUsed,
    FinalDuelTriggered,
    PieceCaptured,
    PieceMoved,
    PhaseAdvanced,
    PrepActionUsed,
    TurnEnded,
)
from game.core.phases import FinalDuelType, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine, get_legal_moves, is_in_check
from game.core.rng import DeterministicRNG
from game.core.state import GameState


# ─────────────────────────────────────────────────────────────────────────────
# Action instantiation
# ─────────────────────────────────────────────────────────────────────────────

class TestActionInstantiation:
    def test_move_piece(self):
        a = MovePiece(player_id="white",
                      source=Position.from_algebraic("e2"),
                      target=Position.from_algebraic("e4"))
        assert a.player_id == "white"

    def test_summon_monster(self):
        a = SummonMonster(player_id="white", card_id="dark_magician",
                          vessel_position=Position.from_algebraic("c1"))
        assert a.card_id == "dark_magician"

    def test_end_turn(self):
        a = EndTurn(player_id="black")
        assert a.player_id == "black"

    def test_declare_recompose(self):
        a = DeclareRecompose(player_id="white")
        assert a.player_id == "white"


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: turn ownership
# ─────────────────────────────────────────────────────────────────────────────

class TestTurnOwnership:
    def test_wrong_player_raises(self, game_state: GameState, rng: DeterministicRNG):
        engine = RulesEngine()
        action = EndPreparation(player_id="black")  # it's white's turn
        with pytest.raises(IllegalActionError, match="white"):
            engine.execute(game_state, action, rng)


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: phase ownership
# ─────────────────────────────────────────────────────────────────────────────

class TestPhaseOwnership:
    def test_move_piece_in_preparation_raises(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        action = MovePiece(
            player_id="white",
            source=Position.from_algebraic("e2"),
            target=Position.from_algebraic("e4"),
        )
        with pytest.raises(IllegalActionError, match="phase"):
            engine.execute(game_state, action, rng)

    def test_end_preparation_in_preparation_ok(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        events = engine.execute(game_state, EndPreparation(player_id="white"), rng)
        assert any(isinstance(e, PhaseAdvanced) for e in events)
        assert game_state.phase == Phase.CHESS


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: preparation limit (max 1 per turn)
# ─────────────────────────────────────────────────────────────────────────────

class TestPreparationLimit:
    def test_second_prep_action_raises(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        # First prep action: place trap
        engine.execute(
            game_state,
            PlaceTrap(
                player_id="white",
                card_id="pit_trap",
                position=Position.from_algebraic("d4"),
            ),
            rng,
        )
        # Second prep action must fail
        with pytest.raises(IllegalActionError, match="already used"):
            engine.execute(
                game_state,
                ActivateSpell(player_id="white", card_id="arcane_sentinel"),
                rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: chess move limit (max 1 per turn)
# ─────────────────────────────────────────────────────────────────────────────

class TestChessMoveLimit:
    def test_second_chess_move_raises(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        # Advance to CHESS phase
        engine.execute(game_state, EndPreparation(player_id="white"), rng)
        assert game_state.phase == Phase.CHESS

        # First chess move
        engine.execute(
            game_state,
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            ),
            rng,
        )
        # Force back to CHESS phase for the test
        game_state.phase = Phase.CHESS
        # Second chess move must fail
        with pytest.raises(IllegalActionError, match="already used"):
            engine.execute(
                game_state,
                MovePiece(
                    player_id="white",
                    source=Position.from_algebraic("d2"),
                    target=Position.from_algebraic("d4"),
                ),
                rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: vessel ownership
# ─────────────────────────────────────────────────────────────────────────────

class TestVesselOwnership:
    def test_cannot_transform_opponent_piece(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        # White tries to summon monster on black's pawn at a7
        with pytest.raises(IllegalActionError, match="opponent"):
            engine.execute(
                game_state,
                SummonMonster(
                    player_id="white",
                    card_id="dark_magician",
                    vessel_position=Position.from_algebraic("a7"),
                ),
                rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: King not a vessel
# ─────────────────────────────────────────────────────────────────────────────

class TestKingNotVessel:
    def test_king_cannot_be_vessel(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        with pytest.raises(IllegalActionError, match="King"):
            engine.execute(
                game_state,
                SummonMonster(
                    player_id="white",
                    card_id="dark_magician",
                    vessel_position=Position.from_algebraic("e1"),  # white king
                ),
                rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: only pawns build
# ─────────────────────────────────────────────────────────────────────────────

class TestBuilderIsPawn:
    def test_non_pawn_cannot_build(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        with pytest.raises(IllegalActionError, match="Pawn"):
            engine.execute(
                game_state,
                StartConstruction(
                    player_id="white",
                    pawn_position=Position.from_algebraic("e1"),  # king, not pawn
                    building_card_id="fortress",
                ),
                rng,
            )

    def test_pawn_can_build(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        events = engine.execute(
            game_state,
            StartConstruction(
                player_id="white",
                pawn_position=Position.from_algebraic("a2"),  # white pawn
                building_card_id="fortress",
            ),
            rng,
        )
        from game.core.events import ConstructionStarted
        assert any(isinstance(e, ConstructionStarted) for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: builder once
# ─────────────────────────────────────────────────────────────────────────────

class TestBuilderOnce:
    def test_used_builder_cannot_build_again(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        # Mark pawn's builder as used
        pawn_pos = Position.from_algebraic("a2")
        pawn_unit = game_state.board.get_unit(pawn_pos)
        assert pawn_unit is not None
        pawn_unit.builder_available = False

        with pytest.raises(IllegalActionError, match="already completed"):
            engine.execute(
                game_state,
                StartConstruction(
                    player_id="white",
                    pawn_position=pawn_pos,
                    building_card_id="fortress",
                ),
                rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Chess move produces correct events
# ─────────────────────────────────────────────────────────────────────────────

class TestChessMoveEvents:
    def test_move_piece_emits_piece_moved(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        engine.execute(game_state, EndPreparation(player_id="white"), rng)

        events = engine.execute(
            game_state,
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            ),
            rng,
        )
        assert any(isinstance(e, PieceMoved) for e in events)
        assert any(isinstance(e, ChessMoveUsed) for e in events)

    def test_chess_move_advances_to_reaction(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        engine.execute(game_state, EndPreparation(player_id="white"), rng)
        engine.execute(
            game_state,
            MovePiece(
                player_id="white",
                source=Position.from_algebraic("e2"),
                target=Position.from_algebraic("e4"),
            ),
            rng,
        )
        assert game_state.phase == Phase.REACTION

    def test_capture_produces_piece_captured_event(self):
        """Set up a board where white can capture a black piece."""
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)

        board = BoardState()
        wp = ChessPiece(id="white-queen-d", owner="white", piece_type=PieceType.QUEEN)
        bp = ChessPiece(id="black-pawn-d", owner="black", piece_type=PieceType.PAWN)
        wking = ChessPiece(id="white-king-e", owner="white", piece_type=PieceType.KING)
        bking = ChessPiece(id="black-king-e", owner="black", piece_type=PieceType.KING)

        board.place_unit(Position.from_algebraic("d1"), UnitInstance(piece=wp))
        board.place_unit(Position.from_algebraic("d5"), UnitInstance(piece=bp))
        board.place_unit(Position.from_algebraic("e1"), UnitInstance(piece=wking))
        board.place_unit(Position.from_algebraic("e8"), UnitInstance(piece=bking))

        from game.core.state import PlayerState, GameState
        from game.core.phases import Phase
        from tests.conftest import make_player

        white = make_player("white")
        black = make_player("black")

        state = GameState(
            game_id="test",
            turn_number=1,
            active_player="white",
            phase=Phase.CHESS,
            board=board,
            players={"white": white, "black": black},
        )

        events = engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("d1"),
                      target=Position.from_algebraic("d5")),
            rng,
        )
        assert any(isinstance(e, PieceCaptured) for e in events)
        captured = next(e for e in events if isinstance(e, PieceCaptured))
        assert captured.piece_id == "black-pawn-d"


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: King capture triggers Final Duel, not GameOver
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalDuelInvariant:
    def test_king_capture_triggers_final_duel_not_game_over(self):
        """
        White queen captures black king →
        FinalDuelTriggered must be emitted and winner must NOT be set.
        Phase must become FINAL_DUEL.
        """
        engine = RulesEngine()
        rng = DeterministicRNG(seed=0)

        board = BoardState()
        wq = ChessPiece(id="white-queen-d", owner="white", piece_type=PieceType.QUEEN)
        bk = ChessPiece(id="black-king-e", owner="black", piece_type=PieceType.KING)
        wk = ChessPiece(id="white-king-e", owner="white", piece_type=PieceType.KING)

        board.place_unit(Position.from_algebraic("d1"), UnitInstance(piece=wq))
        board.place_unit(Position.from_algebraic("d8"), UnitInstance(piece=bk))
        board.place_unit(Position.from_algebraic("e1"), UnitInstance(piece=wk))

        from game.core.state import PlayerState, GameState
        from tests.conftest import make_player

        state = GameState(
            game_id="test-duel",
            turn_number=1,
            active_player="white",
            phase=Phase.CHESS,
            board=board,
            players={"white": make_player("white"), "black": make_player("black")},
        )

        events = engine.execute(
            state,
            MovePiece(player_id="white",
                      source=Position.from_algebraic("d1"),
                      target=Position.from_algebraic("d8")),
            rng,
        )

        assert any(isinstance(e, FinalDuelTriggered) for e in events)
        duel_event = next(e for e in events if isinstance(e, FinalDuelTriggered))
        assert duel_event.duel_type == FinalDuelType.ASSAULT
        assert duel_event.attacker == "white"
        assert duel_event.defender == "black"

        # CRITICAL invariant: winner must NOT be set
        assert state.winner is None, "King capture must never directly set winner"
        assert state.phase == Phase.FINAL_DUEL


# ─────────────────────────────────────────────────────────────────────────────
# EndTurn switches active player
# ─────────────────────────────────────────────────────────────────────────────

class TestEndTurn:
    def test_end_turn_switches_player(
        self, game_state: GameState, rng: DeterministicRNG
    ):
        engine = RulesEngine()
        # Get to END phase
        game_state.phase = Phase.END
        events = engine.execute(game_state, EndTurn(player_id="white"), rng)
        assert any(isinstance(e, TurnEnded) for e in events)
        assert game_state.active_player == "black"
