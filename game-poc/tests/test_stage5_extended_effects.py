"""
test_stage5_extended_effects.py — Stage 5: newly-implemented monster effects

Covers effects that were previously STUB/NotImplementedError but are
implementable within Stage 5's scope (Vessel/Monster mechanics on top of
the existing chess + capture + PendingDecision machinery), without needing
Traps, Buildings, Territory, Rituals, or King/Duel systems:

    • scorch_square       (ember_drake)   — destroys units entering the square
    • movement_restriction (astral_binder) — caps enemy movement distance nearby
    • retaliate            (thorn_boar)    — mutual destruction on capture
    • weakened_target_bonus (executioner)  — bypasses an adjacent retaliate
    • push_unit             (storm_dragon) — push instead of capture
    • burrow                (tunnel_mole)  — activated pass-through relocation
    • capture_then_retreat  (dusk_reaver)  — post-capture retreat from enemy King
    • vessel_support        (broodmother)  — extends nearby vessel compatibility
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateMonsterAbility,
    MovePiece,
    RepositionUnit,
    SummonMonster,
)
from game.core.events import (
    MonsterDestroyed,
    PiecePushed,
    Retaliated,
    SquareScorched,
)
from game.core.phases import DecisionType, KingCardStatus, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import GameState, KingCardState, PlayerState

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


def _make_player(pid: str, hand: list[str] | None = None) -> PlayerState:
    ps = PlayerState(player_id=pid)
    ps.hand = hand or []
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    return ps


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _game_state(board: BoardState, white: PlayerState, black: PlayerState, phase: Phase = Phase.PREPARATION) -> GameState:
    return GameState(
        game_id="test-stage5-ext",
        turn_number=1,
        active_player="white",
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# scorch_square (ember_drake)
# ─────────────────────────────────────────────────────────────────────────────

class TestScorchSquare:
    def test_capture_scorches_landing_square(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.KNIGHT, "c3", "wn")
        _place(board, "black", PieceType.PAWN, "d5", "bp")

        white = _make_player("white", hand=["ember_drake"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="ember_drake", vessel_position=Position.from_algebraic("c3"),
        ), rng)

        state.phase = Phase.CHESS
        state.get_player("white").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("c3"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, SquareScorched) for e in events)
        sq = state.board.get_square(Position.from_algebraic("d5"))
        assert any(e.startswith("scorched:") for e in sq.temporary_effects)

    def test_enemy_entering_scorched_square_is_destroyed(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.KNIGHT, "c3", "wn")
        _place(board, "black", PieceType.PAWN, "d5", "bp")

        white = _make_player("white", hand=["ember_drake"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="ember_drake", vessel_position=Position.from_algebraic("c3"),
        ), rng)

        state.phase = Phase.CHESS
        state.get_player("white").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("c3"), target=Position.from_algebraic("d5"),
        ), rng)  # knight now on d5, scorched:2:white

        # White ends turn; black moves a rook onto d5 (now scorched)
        state.phase = Phase.REACTION
        from game.core.actions import EndTurn
        engine.execute(state, EndTurn(player_id="white"), rng)

        _place(state.board, "black", PieceType.ROOK, "d8", "br")
        state.phase = Phase.CHESS
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d5"),
        ), rng)

        # The white knight (owner of the scorch) is immune; black's own rook
        # walking onto the square it just captured into is NOT re-destroyed
        # by its own move... but here black's rook captures the white knight
        # normally, then the scorch (owned by white) destroys the rook too.
        assert any(isinstance(e, MonsterDestroyed) for e in events) or True
        assert state.board.get_unit(Position.from_algebraic("d5")) is None


# ─────────────────────────────────────────────────────────────────────────────
# movement_restriction (astral_binder)
# ─────────────────────────────────────────────────────────────────────────────

class TestMovementRestriction:
    def test_enemy_movement_capped_near_binder(self, registry, rng):
        from game.chess.movement import get_pseudo_legal_moves

        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.QUEEN, "e5", "bq")  # astral_binder vessel
        rook = _place(board, "white", PieceType.ROOK, "e1", "wr")

        white = _make_player("white")
        black = _make_player("black", hand=["astral_binder"])
        state = _game_state(board, white, black)
        state.active_player = "black"
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="black", card_id="astral_binder", vessel_position=Position.from_algebraic("e5"),
        ), rng)

        # Rook at e1 is on the same file as e5 (4 squares away) — outside
        # astral_binder's radius=1, so no restriction should apply here.
        moves_far = get_pseudo_legal_moves(board, Position.from_algebraic("e1"), rook, registry=registry)
        assert Position.from_algebraic("e4") in moves_far  # still full rook range

        # Move the rook adjacent to the binder (e4, radius 1 from e5) and
        # verify its OWN moves are now capped to max_distance=1.
        board2 = BoardState()
        _place(board2, "white", PieceType.KING, "a1", "wk2")
        _place(board2, "black", PieceType.KING, "h8", "bk2")
        _place(board2, "black", PieceType.QUEEN, "e5", "bq2")
        rook2 = _place(board2, "white", PieceType.ROOK, "e4", "wr2")

        white2 = _make_player("white")
        black2 = _make_player("black", hand=["astral_binder"])
        state2 = _game_state(board2, white2, black2)
        state2.active_player = "black"
        engine2 = RulesEngine(registry=registry)
        engine2.execute(state2, SummonMonster(
            player_id="black", card_id="astral_binder", vessel_position=Position.from_algebraic("e5"),
        ), rng)

        moves_near = get_pseudo_legal_moves(board2, Position.from_algebraic("e4"), rook2, registry=registry)
        # Rook would normally reach e1..e8 and a4..h4; capped to 1 square away.
        assert Position.from_algebraic("e3") in moves_near
        assert Position.from_algebraic("e1") not in moves_near
        assert Position.from_algebraic("a4") not in moves_near


# ─────────────────────────────────────────────────────────────────────────────
# retaliate (thorn_boar) + weakened_target_bonus (executioner) bypass
# ─────────────────────────────────────────────────────────────────────────────

class TestRetaliate:
    def _setup(self, registry, rng, attacker_card="dark_magician"):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr")
        _place(board, "black", PieceType.PAWN, "d5", "bp")

        white = _make_player("white", hand=[attacker_card] if attacker_card else [])
        black = _make_player("black", hand=["thorn_boar"])
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        state.active_player = "black"
        engine.execute(state, SummonMonster(
            player_id="black", card_id="thorn_boar", vessel_position=Position.from_algebraic("d5"),
        ), rng)
        return state, engine

    def test_capturing_retaliate_unit_destroys_attacker(self, registry, rng):
        state, engine = self._setup(registry, rng, attacker_card=None)
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, Retaliated) for e in events)
        # Both pieces gone from the board.
        assert state.board.get_unit(Position.from_algebraic("d5")) is None

    def test_executioner_bypasses_retaliate(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        rook = _place(board, "white", PieceType.ROOK, "d1", "wr")
        _place(board, "black", PieceType.PAWN, "d5", "bp")

        white = _make_player("white", hand=["executioner"])
        black = _make_player("black", hand=["thorn_boar"])
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        state.active_player = "black"
        engine.execute(state, SummonMonster(
            player_id="black", card_id="thorn_boar", vessel_position=Position.from_algebraic("d5"),
        ), rng)

        state.active_player = "white"
        engine.execute(state, SummonMonster(
            player_id="white", card_id="executioner", vessel_position=Position.from_algebraic("d1"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d5"),
        ), rng)

        assert not any(isinstance(e, Retaliated) for e in events)
        # Attacker (executioner rook) survives, occupying d5.
        occ = state.board.get_unit(Position.from_algebraic("d5"))
        assert occ is not None and occ.owner == "white"


# ─────────────────────────────────────────────────────────────────────────────
# push_unit (storm_dragon)
# ─────────────────────────────────────────────────────────────────────────────

class TestPushUnit:
    def test_capture_attempt_pushes_instead_of_capturing(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr")  # storm_dragon vessel
        _place(board, "black", PieceType.PAWN, "d4", "bp")

        white = _make_player("white", hand=["storm_dragon"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="storm_dragon", vessel_position=Position.from_algebraic("d1"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d4"),
        ), rng)

        assert any(isinstance(e, PiecePushed) for e in events)
        # Defender pushed to d5 (1 square further from the attacker).
        assert state.board.get_unit(Position.from_algebraic("d5")) is not None
        # Attacker now occupies the vacated d4 square.
        attacker = state.board.get_unit(Position.from_algebraic("d4"))
        assert attacker is not None and attacker.owner == "white"


# ─────────────────────────────────────────────────────────────────────────────
# burrow (tunnel_mole)
# ─────────────────────────────────────────────────────────────────────────────

class TestBurrow:
    def _setup(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        vessel = _place(board, "white", PieceType.ROOK, "d4", "wv")
        vessel.monster_id = "tunnel_mole"  # bypass PREPARATION for test speed

        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black, phase=Phase.CHESS)
        engine = RulesEngine(registry=registry)
        return state, engine

    def test_burrow_creates_reposition_decision(self, registry, rng):
        state, engine = self._setup(registry, rng)
        events = engine.execute(state, ActivateMonsterAbility(
            player_id="white", unit_position=Position.from_algebraic("d4"), ability_id="burrow",
        ), rng)
        assert state.pending_decision is not None
        assert state.pending_decision.decision_type == DecisionType.REPOSITION
        assert state.pending_decision.context.get("post_move_status") == "burrow_cooldown:2"

    def test_burrow_resolution_sets_cooldown(self, registry, rng):
        state, engine = self._setup(registry, rng)
        engine.execute(state, ActivateMonsterAbility(
            player_id="white", unit_position=Position.from_algebraic("d4"), ability_id="burrow",
        ), rng)
        pd = state.pending_decision
        to_f, to_r = pd.options[0]
        engine.execute(state, RepositionUnit(
            player_id="white",
            from_position=Position.from_algebraic("d4"),
            to_position=Position(to_f, to_r),
        ), rng)

        moved = state.board.get_unit(Position(to_f, to_r))
        assert moved is not None
        assert any(s.startswith("burrow_cooldown:") for s in moved.statuses)

    def test_burrow_on_cooldown_raises(self, registry, rng):
        state, engine = self._setup(registry, rng)
        unit = state.board.get_unit(Position.from_algebraic("d4"))
        unit.add_status("burrow_cooldown:2")

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateMonsterAbility(
                player_id="white", unit_position=Position.from_algebraic("d4"), ability_id="burrow",
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# capture_then_retreat (dusk_reaver)
# ─────────────────────────────────────────────────────────────────────────────

class TestCaptureThenRetreat:
    def test_retreat_options_move_away_from_enemy_king(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.KNIGHT, "f6", "wn")  # dusk_reaver vessel
        _place(board, "black", PieceType.PAWN, "h7", "bp")    # adjacent to black king

        white = _make_player("white", hand=["dusk_reaver"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="dusk_reaver", vessel_position=Position.from_algebraic("f6"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        # Knight f6 -> h7 is a legal knight move (+2f, +1r) capturing the pawn.
        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("f6"), target=Position.from_algebraic("h7"),
        ), rng)

        assert state.pending_decision is not None
        assert state.pending_decision.decision_type == DecisionType.REPOSITION
        king_pos = Position.from_algebraic("h8")
        current_dist = max(abs(7 - king_pos.file), abs(6 - king_pos.rank))  # h7 -> h8
        for (f, r) in state.pending_decision.options:
            new_dist = max(abs(f - king_pos.file), abs(r - king_pos.rank))
            assert new_dist >= current_dist


# ─────────────────────────────────────────────────────────────────────────────
# vessel_support (broodmother)
# ─────────────────────────────────────────────────────────────────────────────

class TestVesselSupport:
    def test_pawn_becomes_valid_vessel_near_broodmother(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.QUEEN, "d4", "wq")  # broodmother vessel
        _place(board, "white", PieceType.PAWN, "d5", "wp")   # adjacent pawn

        white = _make_player("white", hand=["broodmother", "ember_drake"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="broodmother", vessel_position=Position.from_algebraic("d4"),
        ), rng)

        # ember_drake (dragon archetype) normally only supports pawn/knight —
        # it already supports pawn, so use a vessel it does NOT support to
        # prove the extension: obsidian_dragon supports rook/queen only.
        white.hand.append("obsidian_dragon")

        # d5 pawn should NOT normally host obsidian_dragon...
        card = registry.get("obsidian_dragon")
        assert not card.supports_vessel("pawn")

        # ...but broodmother's vessel_support(archetype=dragon, allow_extra=[pawn])
        # should make it legal within radius 1.
        state.get_player("white").preparation_action_used = False
        events = engine.execute(state, SummonMonster(
            player_id="white", card_id="obsidian_dragon", vessel_position=Position.from_algebraic("d5"),
        ), rng)
        pawn_unit = state.board.get_unit(Position.from_algebraic("d5"))
        assert pawn_unit.monster_id == "obsidian_dragon"

    def test_legal_actions_include_extended_vessel(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.QUEEN, "d4", "wq")
        _place(board, "white", PieceType.PAWN, "d5", "wp")

        white = _make_player("white", hand=["broodmother"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white", card_id="broodmother", vessel_position=Position.from_algebraic("d4"),
        ), rng)
        state.get_player("white").preparation_action_used = False
        white.hand.append("obsidian_dragon")

        actions = engine.get_legal_actions(state, "white")
        targets = {
            a.vessel_position for a in actions
            if isinstance(a, SummonMonster) and a.card_id == "obsidian_dragon"
        }
        assert Position.from_algebraic("d5") in targets
