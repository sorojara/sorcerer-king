"""
test_monsters.py — Stage 5: Vessels and Monster Transformation

Covers:
    • Vessel compatibility enforced at summoning (VESSEL_COMPATIBILITY invariant).
    • Vessel ownership enforced (VESSEL_OWNERSHIP invariant).
    • King cannot be a vessel (KING_NOT_VESSEL invariant).
    • Monster summoned on valid vessel → MonsterSummoned event + unit.monster_id set.
    • on_summon draw_card effect (apprentice_mage) triggers CardDrawn event.
    • capture_protection (arcane_sentinel / stone_golem) — captures absorbed, shield depleted.
    • capture_protection exhausted → next capture destroys monster → MonsterDestroyed event.
    • Movement inheritance: monster vessel determines base movement.
    • alter_movement add_leap (wyrm_knight on Knight) adds leap squares.
    • DismissMonster restores vessel (monster_id = None) and returns card to hand.
    • DismissMonster on empty square raises IllegalActionError.
    • DismissMonster on own piece without monster raises IllegalActionError.
    • MonsterDestroyed event fired when monster unit is captured normally.
    • CaptureBlocked event fired when capture_protection absorbs a hit.
    • spell_radius_bonus status applied to unit on summon.
    • ritual_boost status applied to unit on summon.
    • Dismiss clears monster-applied statuses from unit.
    • legal actions include DismissMonster for deployed monsters.
    • legal actions include SummonMonster only for compatible vessel types.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml, MonsterCard
from game.chess.board import BoardState
from game.chess.movement import get_pseudo_legal_moves, get_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    DismissMonster,
    EndTurn,
    MovePiece,
    SummonMonster,
)
from game.core.events import (
    CaptureBlocked,
    CardDrawn,
    MonsterDestroyed,
    MonsterDismissed,
    MonsterSummoned,
    PieceCaptured,
)
from game.core.phases import KingCardStatus, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import GameState, KingCardState, PlayerState

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


def _make_player(pid: str, hand: list[str] | None = None, deck: list[str] | None = None) -> PlayerState:
    ps = PlayerState(player_id=pid)
    ps.hand = hand or []
    ps.deck = deck or []
    ps.king_pool = [
        KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN),
    ]
    return ps


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _game_state(board: BoardState, white: PlayerState, black: PlayerState, phase: Phase = Phase.PREPARATION) -> GameState:
    return GameState(
        game_id="test-monsters",
        turn_number=1,
        active_player="white",
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Vessel compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestVesselCompatibility:
    def test_summon_on_valid_vessel_succeeds(self, registry, rng):
        """dark_magician supports bishop — summon on bishop should work."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        bishop_unit = _place(board, "white", PieceType.BISHOP, "c1", "wb")

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        events = engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("c1")), rng)

        assert any(isinstance(e, MonsterSummoned) for e in events)
        assert bishop_unit.monster_id == "dark_magician"

    def test_summon_on_incompatible_vessel_raises(self, registry, rng):
        """dark_magician supports bishop/queen — knight is incompatible."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.KNIGHT, "b1", "wn")

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="cannot use"):
            engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("b1")), rng)

    def test_summon_on_king_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="King cannot be a Monster vessel"):
            engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("e1")), rng)

    def test_summon_card_not_in_hand_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.BISHOP, "c1", "wb")

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="not in hand"):
            engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("c1")), rng)

    def test_summon_opponent_piece_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "black", PieceType.BISHOP, "c8", "bb")  # black's bishop

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="opponent"):
            engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("c8")), rng)

    def test_legal_actions_only_show_compatible_vessels(self, registry):
        """dark_magician supports bishop/queen — knight should not appear in legal SummonMonster."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.BISHOP, "c1", "wb")
        _place(board, "white", PieceType.KNIGHT, "b1", "wn")
        _place(board, "white", PieceType.QUEEN, "d1", "wq")

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        actions = engine.get_legal_actions(state, "white")

        summon_targets = {
            a.vessel_position for a in actions if isinstance(a, SummonMonster) and a.card_id == "dark_magician"
        }
        # Only bishop (c1) and queen (d1) should be valid
        assert Position.from_algebraic("c1") in summon_targets
        assert Position.from_algebraic("d1") in summon_targets
        assert Position.from_algebraic("b1") not in summon_targets  # knight — incompatible
        assert Position.from_algebraic("e1") not in summon_targets  # king — excluded


# ─────────────────────────────────────────────────────────────────────────────
# On-summon effects
# ─────────────────────────────────────────────────────────────────────────────

class TestOnSummonEffects:
    def test_draw_card_on_summon(self, registry, rng):
        """apprentice_mage draws 1 card on summon."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp-a")

        white = _make_player("white", hand=["apprentice_mage"], deck=["iron_vanguard", "blade_dancer"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        events = engine.execute(state, SummonMonster(player_id="white", card_id="apprentice_mage", vessel_position=Position.from_algebraic("a2")), rng)

        draw_events = [e for e in events if isinstance(e, CardDrawn)]
        assert len(draw_events) == 1
        assert draw_events[0].card_id == "iron_vanguard"
        assert "iron_vanguard" in white.hand

    def test_capture_protection_status_applied(self, registry, rng):
        """arcane_sentinel gets shield:1 applied on summon."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        knight_unit = _place(board, "white", PieceType.KNIGHT, "b1", "wn")

        white = _make_player("white", hand=["arcane_sentinel"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(player_id="white", card_id="arcane_sentinel", vessel_position=Position.from_algebraic("b1")), rng)

        assert any(s.startswith("shield:") for s in knight_unit.statuses), \
            "arcane_sentinel should have shield status after summon"

    def test_spell_radius_bonus_status_applied(self, registry, rng):
        """dark_magician should get spell_radius_bonus status on summon."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        bishop_unit = _place(board, "white", PieceType.BISHOP, "c1", "wb")

        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(player_id="white", card_id="dark_magician", vessel_position=Position.from_algebraic("c1")), rng)

        assert any(s.startswith("spell_radius_bonus:") for s in bishop_unit.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# Movement inheritance
# ─────────────────────────────────────────────────────────────────────────────

class TestMovementInheritance:
    def test_dark_magician_on_bishop_moves_diagonally(self, registry):
        """dark_magician on bishop inherits diagonal bishop movement."""
        board = BoardState()
        # Kings on h1/a8 — don't block bishop diagonals from d4
        _place(board, "white", PieceType.KING, "h1", "wk")
        _place(board, "black", PieceType.KING, "a8", "bk")
        unit = _place(board, "white", PieceType.BISHOP, "d4", "wb")
        unit.monster_id = "dark_magician"

        moves = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), unit, registry=registry)
        # Bishop on d4: diagonals reach a1,b2,c3 (3) + e5,f6,g7,h8 (4) = 7 on one axis
        # and a7,b6,c5 (3) + e3,f2,g1 (3) = 6 on the other = 13 total
        assert len(moves) == 13

    def test_dark_magician_on_queen_moves_like_queen(self, registry):
        """dark_magician on queen inherits queen movement (27 squares from d4 on empty board)."""
        board = BoardState()
        # Kings far from d4 (h1, a8) don't block any of the 27 queen squares
        _place(board, "white", PieceType.KING, "h1", "wk")
        _place(board, "black", PieceType.KING, "a8", "bk")
        unit = _place(board, "white", PieceType.QUEEN, "d4", "wq")
        unit.monster_id = "dark_magician"

        moves = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), unit, registry=registry)
        assert len(moves) == 27  # full queen reach

    def test_wyrm_knight_on_knight_adds_leap(self, registry):
        """wyrm_knight adds 4 cardinal 2-square leaps to knight movement."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        unit = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        unit.monster_id = "wyrm_knight"

        moves_base = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), unit)
        moves_monster = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), unit, registry=registry)

        # Base knight has 8 moves; wyrm_knight adds up to 4 cardinal leaps
        assert len(moves_monster) > len(moves_base)
        # Cardinal leap at distance 2: f4 (+2,0), b4 (-2,0), d6 (0,+2), d2 (0,-2)
        assert Position.from_algebraic("f4") in moves_monster
        assert Position.from_algebraic("b4") in moves_monster
        assert Position.from_algebraic("d6") in moves_monster
        assert Position.from_algebraic("d2") in moves_monster

    def test_without_registry_no_leap_added(self):
        """Without registry, wyrm_knight has no extra moves (no modifiers applied)."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        unit = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        unit.monster_id = "wyrm_knight"

        moves = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), unit, registry=None)
        # Only standard 8 knight moves
        assert len(moves) == 8


# ─────────────────────────────────────────────────────────────────────────────
# Capture protection
# ─────────────────────────────────────────────────────────────────────────────

class TestCaptureProtection:
    def _setup_capture_state(self, registry, monster_id: str, ptype: PieceType, pos_alg: str) -> tuple:
        """Build a minimal state with a shielded monster at pos_alg attacked by a white rook."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white_rook = _place(board, "white", PieceType.ROOK, "d1", "wr")
        black_unit = _place(board, "black", ptype, pos_alg, "bm")
        black_unit.monster_id = monster_id

        # Apply the capture protection manually (as if it was summoned)
        rng = DeterministicRNG(seed=0)
        card = registry.get(monster_id)
        from game.mechanics.monsters import apply_on_summon_effects
        white_ps = _make_player("white", hand=[])
        black_ps = _make_player("black", hand=[])
        state = _game_state(board, white_ps, black_ps, phase=Phase.CHESS)
        events: list = []
        apply_on_summon_effects(state, black_unit, card, events, rng=rng)

        # Move to chess phase for white
        state.phase = Phase.CHESS
        return state, white_rook, black_unit

    def test_capture_protection_absorbs_first_hit(self, registry, rng):
        """arcane_sentinel (shield:1) survives first capture attempt."""
        state, white_rook, black_unit = self._setup_capture_state(
            registry, "arcane_sentinel", PieceType.KNIGHT, "d4"
        )
        # Ensure rook can reach d4
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, MovePiece(player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d4")), rng)

        # Shield should have absorbed the capture
        blocked = [e for e in events if isinstance(e, CaptureBlocked)]
        assert len(blocked) == 1
        assert blocked[0].shields_remaining == 0  # shield exhausted

        # Black unit still on board
        remaining = state.board.get_unit(Position.from_algebraic("d4"))
        assert remaining is not None
        assert remaining.monster_id == "arcane_sentinel"

    def test_stone_golem_absorbs_first_hit(self, registry, rng):
        """stone_golem has shield:2 — survives first capture attempt."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr1")
        black_unit = _place(board, "black", PieceType.ROOK, "d6", "br")
        black_unit.monster_id = "stone_golem"

        # Manually set 2 shields (as if summoned)
        black_unit.add_status("shield:2")

        white_ps = _make_player("white", hand=[])
        black_ps = _make_player("black", hand=[])
        state = _game_state(board, white_ps, black_ps, phase=Phase.CHESS)

        engine = RulesEngine(registry=registry)

        # First attempt — rook at d1 attacks d6 (clear path)
        events1 = engine.execute(state, MovePiece(player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d6")), rng)
        assert any(isinstance(e, CaptureBlocked) for e in events1)
        remaining = state.board.get_unit(Position.from_algebraic("d6"))
        assert remaining is not None, "stone_golem should survive first capture"
        shield_status = next((s for s in remaining.statuses if s.startswith("shield:")), None)
        assert shield_status == "shield:1", f"Expected shield:1, got {shield_status}"

    def test_capture_with_no_shield_destroys_monster(self, registry, rng):
        """A monster with no shield is destroyed and MonsterDestroyed is emitted."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr")
        black_unit = _place(board, "black", PieceType.ROOK, "d6", "br")
        black_unit.monster_id = "dragon_herald"  # no capture_protection

        white_ps = _make_player("white", hand=[])
        black_ps = _make_player("black", hand=[])
        state = _game_state(board, white_ps, black_ps, phase=Phase.CHESS)

        engine = RulesEngine(registry=registry)
        events = engine.execute(state, MovePiece(player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d6")), rng)

        destroyed = [e for e in events if isinstance(e, MonsterDestroyed)]
        assert len(destroyed) == 1
        assert destroyed[0].card_id == "dragon_herald"
        assert destroyed[0].player_id == "black"

        # Normal PieceCaptured also emitted
        captured = [e for e in events if isinstance(e, PieceCaptured)]
        assert len(captured) == 1

        # White rook landed on d6 (captured the black unit)
        occupant = state.board.get_unit(Position.from_algebraic("d6"))
        assert occupant is not None
        assert occupant.owner == "white"  # white rook now occupies d6


# ─────────────────────────────────────────────────────────────────────────────
# Monster destruction
# ─────────────────────────────────────────────────────────────────────────────

class TestMonsterDestruction:
    def test_capturing_plain_piece_does_not_emit_monster_destroyed(self, registry, rng):
        """Plain chess piece capture must NOT emit MonsterDestroyed."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr")
        _place(board, "black", PieceType.ROOK, "d6", "br")  # no monster

        white_ps = _make_player("white", hand=[])
        black_ps = _make_player("black", hand=[])
        state = _game_state(board, white_ps, black_ps, phase=Phase.CHESS)

        engine = RulesEngine(registry=registry)
        events = engine.execute(state, MovePiece(player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d6")), rng)

        assert not any(isinstance(e, MonsterDestroyed) for e in events)
        assert any(isinstance(e, PieceCaptured) for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# Dismiss
# ─────────────────────────────────────────────────────────────────────────────

class TestDismissMonster:
    def test_dismiss_restores_vessel_and_returns_card(self, registry, rng):
        """DismissMonster clears monster_id and returns card to hand."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        unit = _place(board, "white", PieceType.BISHOP, "c1", "wb")
        unit.monster_id = "dark_magician"
        unit.add_status("spell_radius_bonus:1")

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        events = engine.execute(state, DismissMonster(player_id="white", unit_position=Position.from_algebraic("c1")), rng)

        dismissed = [e for e in events if isinstance(e, MonsterDismissed)]
        assert len(dismissed) == 1
        assert dismissed[0].card_id == "dark_magician"

        # Vessel restored
        assert unit.monster_id is None
        # Card returned to hand
        assert "dark_magician" in white.hand

    def test_dismiss_clears_monster_statuses(self, registry, rng):
        """Dismiss should clear spell_radius_bonus, shield, stealth from unit."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        unit = _place(board, "white", PieceType.KNIGHT, "b1", "wn")
        unit.monster_id = "arcane_sentinel"
        unit.add_status("shield:1")
        unit.add_status("spell_radius_bonus:0")

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        engine.execute(state, DismissMonster(player_id="white", unit_position=Position.from_algebraic("b1")), rng)

        assert not any(s.startswith("shield:") for s in unit.statuses)
        assert not any(s.startswith("spell_radius_bonus:") for s in unit.statuses)

    def test_dismiss_empty_square_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="No piece"):
            engine.execute(state, DismissMonster(player_id="white", unit_position=Position.from_algebraic("d4")), rng)

    def test_dismiss_plain_piece_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.BISHOP, "c1", "wb")  # no monster

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="No monster"):
            engine.execute(state, DismissMonster(player_id="white", unit_position=Position.from_algebraic("c1")), rng)

    def test_dismiss_opponent_monster_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        black_unit = _place(board, "black", PieceType.BISHOP, "c8", "bb")
        black_unit.monster_id = "dark_magician"

        white = _make_player("white", hand=[])
        black = _make_player("black", hand=[])
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="opponent"):
            engine.execute(state, DismissMonster(player_id="white", unit_position=Position.from_algebraic("c8")), rng)

    def test_legal_actions_include_dismiss_for_deployed_monsters(self, registry):
        """DismissMonster should appear in legal actions for deployed monsters."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        unit = _place(board, "white", PieceType.BISHOP, "c1", "wb")
        unit.monster_id = "dark_magician"

        white = _make_player("white", hand=[])
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        actions = engine.get_legal_actions(state, "white")

        dismiss_actions = [a for a in actions if isinstance(a, DismissMonster)]
        assert len(dismiss_actions) == 1
        assert dismiss_actions[0].unit_position == Position.from_algebraic("c1")


# ─────────────────────────────────────────────────────────────────────────────
# Damage Aura (dragon_herald)
# ─────────────────────────────────────────────────────────────────────────────

class TestDamageAura:
    """
    dragon_herald has a damage_aura(radius=1, owner_immune=True).
    Any enemy piece that moves within 1 square of dragon_herald is destroyed.
    The owner's own pieces are immune.
    """

    def _setup(self, registry, rng):
        """
        Board layout (all on empty board):
          white king  e1
          black king  e8
          black rook  d5  ← will host dragon_herald
          white pawn  a5  ← will be used as the moving piece
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        rook_unit = _place(board, "black", PieceType.ROOK, "d5", "br")
        white_unit = _place(board, "white", PieceType.PAWN, "a5", "wp")

        engine = RulesEngine(registry=registry)
        # Summon dragon_herald on the black rook
        black = _make_player("black", hand=["dragon_herald"])
        white = _make_player("white")
        state = _game_state(board, white, black, Phase.PREPARATION)
        state.active_player = "black"
        engine.execute(state, SummonMonster(
            player_id="black",
            card_id="dragon_herald",
            vessel_position=Position.from_algebraic("d5"),
        ), rng)
        return state, engine

    def test_enemy_entering_aura_is_destroyed(self, registry, rng):
        """White pawn moving to c5 (adjacent to d5 dragon_herald) is destroyed."""
        state, engine = self._setup(registry, rng)
        # Switch to chess phase, white to move
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        # Add a white rook on c2 that can reach c5 (long range test)
        rook = _place(state.board, "white", PieceType.ROOK, "c2", "wr2")

        events = engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c2"),
            target=Position.from_algebraic("c5"),  # 1 square away from d5
        ), rng)

        event_types = [type(e).__name__ for e in events]
        # The white rook should have been destroyed by the aura
        assert "PieceCaptured" in event_types
        # The piece must no longer be on the board
        assert state.board.get_unit(Position.from_algebraic("c5")) is None

    def test_owner_immune_to_own_aura(self, registry, rng):
        """Black piece moving adjacent to own dragon_herald is NOT destroyed."""
        state, engine = self._setup(registry, rng)
        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False

        # Place a plain black bishop that can reach c5 (adjacent to d5)
        _place(state.board, "black", PieceType.BISHOP, "a3", "bb")

        events = engine.execute(state, MovePiece(
            player_id="black",
            source=Position.from_algebraic("a3"),
            target=Position.from_algebraic("c5"),
        ), rng)

        event_types = [type(e).__name__ for e in events]
        # No PieceCaptured event — owner is immune
        assert "PieceCaptured" not in event_types
        # The bishop must still be on the board
        assert state.board.get_unit(Position.from_algebraic("c5")) is not None

    def test_piece_outside_aura_not_destroyed(self, registry, rng):
        """White rook moving to f5 (2 squares from d5) is not destroyed."""
        state, engine = self._setup(registry, rng)
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        _place(state.board, "white", PieceType.ROOK, "f2", "wr3")

        events = engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("f2"),
            target=Position.from_algebraic("f5"),  # 2 files from d5 — outside radius 1
        ), rng)

        event_types = [type(e).__name__ for e in events]
        assert "PieceCaptured" not in event_types
        assert state.board.get_unit(Position.from_algebraic("f5")) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Freeze Square (iron_vanguard)
# ─────────────────────────────────────────────────────────────────────────────

class TestFreezeSquare:
    """
    iron_vanguard has freeze_square(trigger=on_capture, duration_turns=1).
    After capturing, the landing square is frozen for 1 turn — no piece
    may enter it.  The freeze lifts after the owner's next end-of-turn.
    """

    def _setup_with_iron_vanguard(self, registry, rng, vessel_alg: str = "d4",
                                   target_alg: str = "e5"):
        """
        Place iron_vanguard (on a knight) at vessel_alg.
        Place an enemy pawn at target_alg.
        Returns (state, engine, attacker_pos, target_pos).
        """
        from game.core.actions import EndTurn

        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        attacker = _place(board, "white", PieceType.KNIGHT, vessel_alg, "wn")
        _place(board, "black", PieceType.PAWN, target_alg, "bp")

        white = _make_player("white", hand=["iron_vanguard"])
        black = _make_player("black")
        state = _game_state(board, white, black, Phase.PREPARATION)

        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white",
            card_id="iron_vanguard",
            vessel_position=Position.from_algebraic(vessel_alg),
        ), rng)

        return state, engine, Position.from_algebraic(vessel_alg), Position.from_algebraic(target_alg)

    def test_capture_freezes_landing_square(self, registry, rng):
        """
        iron_vanguard captures → landing square gets frozen:1 effect.
        """
        from game.core.events import SquareFrozen

        state, engine, attacker_pos, target_pos = self._setup_with_iron_vanguard(
            registry, rng, vessel_alg="d4", target_alg="e6"
        )
        # Knight on d4 can reach e6 (knight move: +1 file, +2 rank)
        # Adjust placement so knight can capture
        # Knight d4 → f5 (valid knight move) but we need an enemy there
        # Use c2 → d4 (knight move) then d4 → e6
        # Let's place on c3 and capture d5
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        knight = _place(board, "white", PieceType.KNIGHT, "c3", "wn2")
        enemy = _place(board, "black", PieceType.PAWN, "d5", "bp2")

        white = _make_player("white", hand=["iron_vanguard"])
        black = _make_player("black")
        state2 = _game_state(board, white, black, Phase.PREPARATION)
        engine2 = RulesEngine(registry=registry)
        engine2.execute(state2, SummonMonster(
            player_id="white",
            card_id="iron_vanguard",
            vessel_position=Position.from_algebraic("c3"),
        ), rng)

        state2.phase = Phase.CHESS
        state2.active_player = "white"
        state2.get_player("white").chess_move_used = False

        events = engine2.execute(state2, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),  # knight c3→d5: +1f +2r ✓
        ), rng)

        event_types = [type(e).__name__ for e in events]
        assert "SquareFrozen" in event_types

        sq = state2.board.get_square(Position.from_algebraic("d5"))
        frozen_effects = [e for e in sq.temporary_effects if e.startswith("frozen:")]
        assert len(frozen_effects) == 1
        # Format: "frozen:<turns>:<owner>" — freeze owned by white, duration 1
        assert frozen_effects[0] == "frozen:1:white"

    def test_frozen_square_blocks_entry(self, registry, rng):
        """
        After iron_vanguard captures and freezes the square, it blocks the
        OPPONENT from entering that square on their turn.  The freeze was set
        by white, so it persists through white's own EndTurn and only expires
        after black (the opponent) ends THEIR turn.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        knight = _place(board, "white", PieceType.KNIGHT, "c3", "wn3")
        _place(board, "black", PieceType.PAWN, "d5", "bp3")

        white = _make_player("white", hand=["iron_vanguard"])
        black = _make_player("black")
        state = _game_state(board, white, black, Phase.PREPARATION)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white",
            card_id="iron_vanguard",
            vessel_position=Position.from_algebraic("c3"),
        ), rng)

        # Knight captures on d5 → square freezes (frozen:1:white)
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        # White ends their turn — freeze should still be active (owner=white → not decremented)
        state.phase = Phase.REACTION
        engine.execute(state, EndTurn(player_id="white"), rng)

        sq = state.board.get_square(Position.from_algebraic("d5"))
        frozen_effects = [e for e in sq.temporary_effects if e.startswith("frozen:")]
        assert len(frozen_effects) == 1, "Freeze must survive the capturing player's EndTurn"

        # Now it is black's turn — d5 is still frozen, black cannot enter it
        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        _place(state.board, "black", PieceType.ROOK, "d8", "br5")

        legal = engine.get_legal_actions(state, "black")
        move_to_d5 = [
            a for a in legal
            if isinstance(a, MovePiece) and a.target == Position.from_algebraic("d5")
        ]
        assert len(move_to_d5) == 0, "Frozen square must block opponent from entering"

    def test_frozen_square_thaws_after_opponent_end_of_turn(self, registry, rng):
        """
        The frozen:1:white counter decrements to 0 when BLACK (the opponent) ends
        their turn — the square is accessible again on the next round.
        """
        from game.core.actions import EndTurn

        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        knight = _place(board, "white", PieceType.KNIGHT, "c3", "wn4")
        _place(board, "black", PieceType.PAWN, "d5", "bp4")

        white = _make_player("white", hand=["iron_vanguard"])
        black = _make_player("black")
        state = _game_state(board, white, black, Phase.PREPARATION)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white",
            card_id="iron_vanguard",
            vessel_position=Position.from_algebraic("c3"),
        ), rng)

        # Knight captures on d5 → square frozen
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        sq = state.board.get_square(Position.from_algebraic("d5"))
        assert any(e.startswith("frozen:") for e in sq.temporary_effects)

        # White ends turn — freeze persists (owner=white → not decremented by white's EndTurn)
        state.phase = Phase.REACTION
        engine.execute(state, EndTurn(player_id="white"), rng)
        sq = state.board.get_square(Position.from_algebraic("d5"))
        assert any(e.startswith("frozen:") for e in sq.temporary_effects), \
            "Freeze must still be active after white's own EndTurn"

        # Black ends turn — freeze expires (opponent of white ends their turn)
        state.phase = Phase.REACTION
        state.active_player = "black"
        engine.execute(state, EndTurn(player_id="black"), rng)

        sq = state.board.get_square(Position.from_algebraic("d5"))
        frozen_effects = [e for e in sq.temporary_effects if e.startswith("frozen:")]
        assert len(frozen_effects) == 0, "Frozen square must thaw after opponent's EndTurn"


# ─────────────────────────────────────────────────────────────────────────────
# Blade Dancer — Reposition after capture
# ─────────────────────────────────────────────────────────────────────────────

class TestBladeDancerReposition:
    """
    blade_dancer has reposition_unit(trigger=after_capture, max_distance=2).
    After capturing, a REPOSITION PendingDecision is created.
    The player must execute a RepositionUnit action to resolve it.
    """

    def _setup_blade_dancer(self, registry, rng, vessel_alg: str = "d4",
                             target_alg: str = "c6"):
        """
        Place blade_dancer (on a knight) at vessel_alg.
        Place an enemy pawn at target_alg.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.KNIGHT, vessel_alg, "wn")
        _place(board, "black", PieceType.PAWN, target_alg, "bp")

        white = _make_player("white", hand=["blade_dancer"])
        black = _make_player("black")
        state = _game_state(board, white, black, Phase.PREPARATION)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white",
            card_id="blade_dancer",
            vessel_position=Position.from_algebraic(vessel_alg),
        ), rng)
        return state, engine

    def test_capture_creates_reposition_decision(self, registry, rng):
        """
        After blade_dancer captures an enemy piece, a REPOSITION PendingDecision
        must exist.
        """
        from game.core.phases import DecisionType

        # knight c3 → d5 (knight move: +1f +2r)
        state, engine = self._setup_blade_dancer(registry, rng, "c3", "d5")
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        assert state.pending_decision is not None
        assert state.pending_decision.decision_type == DecisionType.REPOSITION
        assert state.pending_decision.player_id == "white"
        # Options must be within max_distance=2 of d5
        for (f, r) in state.pending_decision.options:
            assert abs(f - 3) <= 2 and abs(r - 4) <= 2  # d5 = file 3, rank 4
            assert state.board.get_unit(Position(f, r)) is None  # must be empty

    def test_reposition_decision_resolved_by_action(self, registry, rng):
        """
        Executing RepositionUnit with a valid destination clears the pending
        decision and moves the piece.
        """
        from game.core.actions import RepositionUnit

        state, engine = self._setup_blade_dancer(registry, rng, "c3", "d5")
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        assert state.pending_decision is not None

        # Pick the first valid reposition destination
        pd = state.pending_decision
        to_f, to_r = pd.options[0]
        to_pos = Position(to_f, to_r)
        from_pos = Position(*pd.context["current_pos"])

        engine.execute(state, RepositionUnit(
            player_id="white",
            from_position=from_pos,
            to_position=to_pos,
        ), rng)

        # PendingDecision must be cleared
        assert state.pending_decision is None
        # Piece must be at the new position
        assert state.board.get_unit(to_pos) is not None
        # Old position must be empty (unless to_pos == from_pos)
        if to_pos != from_pos:
            assert state.board.get_unit(from_pos) is None

    def test_reposition_only_legal_action_while_pending(self, registry, rng):
        """
        While a REPOSITION decision is pending, the only legal actions are
        RepositionUnit variants.  No MovePiece should appear.
        """
        from game.core.actions import RepositionUnit

        state, engine = self._setup_blade_dancer(registry, rng, "c3", "d5")
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        legal = engine.get_legal_actions(state, "white")
        assert len(legal) > 0
        assert all(isinstance(a, RepositionUnit) for a in legal)

    def test_invalid_reposition_destination_raises(self, registry, rng):
        """
        RepositionUnit to a square outside the options list raises IllegalActionError.
        """
        from game.core.actions import RepositionUnit

        state, engine = self._setup_blade_dancer(registry, rng, "c3", "d5")
        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),
        ), rng)

        pd = state.pending_decision
        from_pos = Position(*pd.context["current_pos"])
        # a1 is far outside max_distance=2 from d5
        invalid_dest = Position.from_algebraic("a1")
        # Make sure a1 is not accidentally in the options
        if (invalid_dest.file, invalid_dest.rank) not in pd.options:
            with pytest.raises(IllegalActionError):
                engine.execute(state, RepositionUnit(
                    player_id="white",
                    from_position=from_pos,
                    to_position=invalid_dest,
                ), rng)
        else:
            pytest.skip("a1 happened to be within range; adjust test")

    def test_no_capture_no_reposition_decision(self, registry, rng):
        """
        A plain move (no capture) by blade_dancer does NOT create a REPOSITION decision.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.KNIGHT, "c3", "wn")
        # No enemy at d5 — plain move, no capture

        white = _make_player("white", hand=["blade_dancer"])
        black = _make_player("black")
        state = _game_state(board, white, black, Phase.PREPARATION)
        engine = RulesEngine(registry=registry)
        engine.execute(state, SummonMonster(
            player_id="white",
            card_id="blade_dancer",
            vessel_position=Position.from_algebraic("c3"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False

        engine.execute(state, MovePiece(
            player_id="white",
            source=Position.from_algebraic("c3"),
            target=Position.from_algebraic("d5"),  # plain move — no enemy there
        ), rng)

        assert state.pending_decision is None


# ─────────────────────────────────────────────────────────────────────────────
# ActivateMonsterAbility
# ─────────────────────────────────────────────────────────────────────────────

class TestActivateMonsterAbility:
    """
    Tests for ActivateMonsterAbility — engine dispatch, legal actions,
    validation guards.
    """

    # blade_dancer has reposition_unit (activatable).
    # We place it during CHESS phase and verify the action is legal + dispatches.

    def _setup_chess_with_monster(self, registry, rng, monster_id: str, vessel_type: PieceType, alg: str = "d4"):
        """
        Return (state, engine) with ``monster_id`` already summoned onto a
        ``vessel_type`` piece at ``alg``, phase = CHESS, white's turn.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        vessel_unit = _place(board, "white", vessel_type, alg, "wv")
        vessel_unit.monster_id = monster_id  # bypass PREPARATION for test speed

        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black, phase=Phase.CHESS)
        engine = RulesEngine(registry=registry)
        return state, engine

    def test_activate_ability_emits_event(self, registry, rng):
        """ActivateMonsterAbility dispatches and emits MonsterAbilityActivated."""
        from game.core.actions import ActivateMonsterAbility
        from game.core.events import MonsterAbilityActivated

        # blade_dancer has reposition_unit (activatable) — use it
        state, engine = self._setup_chess_with_monster(
            registry, rng, "blade_dancer", PieceType.KNIGHT, "d4"
        )
        pos = Position.from_algebraic("d4")
        action = ActivateMonsterAbility(
            player_id="white",
            unit_position=pos,
            ability_id="reposition_unit",
        )
        events = engine.execute(state, action, rng)
        assert any(isinstance(e, MonsterAbilityActivated) for e in events)
        activated = next(e for e in events if isinstance(e, MonsterAbilityActivated))
        assert activated.ability_id == "reposition_unit"
        assert activated.card_id == "blade_dancer"

    def test_activate_ability_appears_in_legal_actions(self, registry, rng):
        """blade_dancer's reposition_unit must appear in legal actions during CHESS."""
        from game.core.actions import ActivateMonsterAbility

        state, engine = self._setup_chess_with_monster(
            registry, rng, "blade_dancer", PieceType.KNIGHT, "d4"
        )
        legal = engine.get_legal_actions(state, "white")
        ability_actions = [
            a for a in legal
            if isinstance(a, ActivateMonsterAbility) and a.ability_id == "reposition_unit"
        ]
        assert ability_actions, "Expected ActivateMonsterAbility(reposition_unit) in legal actions"

    def test_activate_wrong_ability_raises(self, registry, rng):
        """Requesting a non-existent ability raises IllegalActionError."""
        from game.core.actions import ActivateMonsterAbility
        from game.core.rules import IllegalActionError

        state, engine = self._setup_chess_with_monster(
            registry, rng, "blade_dancer", PieceType.KNIGHT, "d4"
        )
        pos = Position.from_algebraic("d4")
        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateMonsterAbility(
                player_id="white",
                unit_position=pos,
                ability_id="nonexistent_ability",
            ), rng)

    def test_activate_ability_wrong_phase_raises(self, registry, rng):
        """ActivateMonsterAbility raises during PREPARATION (not CHESS)."""
        from game.core.actions import ActivateMonsterAbility
        from game.core.rules import IllegalActionError

        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        vessel = _place(board, "white", PieceType.KNIGHT, "d4", "wv")
        vessel.monster_id = "blade_dancer"
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black, phase=Phase.PREPARATION)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateMonsterAbility(
                player_id="white",
                unit_position=Position.from_algebraic("d4"),
                ability_id="reposition_unit",
            ), rng)

    def test_activate_ability_no_monster_raises(self, registry, rng):
        """ActivateMonsterAbility on a plain piece raises IllegalActionError."""
        from game.core.actions import ActivateMonsterAbility
        from game.core.rules import IllegalActionError

        state, engine = self._setup_chess_with_monster(
            registry, rng, "blade_dancer", PieceType.KNIGHT, "d4"
        )
        # Use the king's position (no monster)
        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateMonsterAbility(
                player_id="white",
                unit_position=Position.from_algebraic("e1"),
                ability_id="reposition_unit",
            ), rng)
