"""
test_stage6_traps_spells.py — Stage 6: spatial Spells and visible Traps

Covers the base 5-Trap / 5-Spell PoC set, redesigned per the Stage 6
balance pass (Pit Trap / Counter Strike no longer instant-kill), plus the
canonical area-of-effect + effect-resolution machinery that powers them:

    • PlaceTrap reads real radius/shape/trigger/charges from the card
      (not hardcoded).
    • pit_trap    — enter_radius → destroy_monster_or_piece; charges=1 →
                     the Trap is removed after it fires.
    • ward_of_binding — immobilize_piece; charges default to 1 (single-use,
      like every Trap unless its YAML sets `charges: null`/a higher count).
    • counter_strike  — capture trigger → the CAPTURING piece becomes
                         Exposed (capture_protection bypass) instead of
                         being destroyed.
    • alarm_beacon    — draw_card half of its effect list (reveal_enemy_hand
                         is deliberately deferred — needs Observation reveal
                         plumbing).
    • trap_immunity (ancient_tortoise) skips a Trap's effects once.
    • shatter_trap, cursed_ground, veil_of_stillness, arcane_reposition —
      ActivateSpell resolving through the shared EFFECT_REGISTRY.
    • time_anchor — cancel_move via a MoveSnapshot restore; refuses when
      there's nothing to cancel or the move triggered a Final Duel; single
      charge.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateSpell,
    ActivateTrap,
    EndTurn,
    MovePiece,
    PlaceTrap,
)
from game.core.events import (
    CardDrawn,
    MonsterDestroyed,
    MoveCancelled,
    TrapTriggered,
)
from game.core.phases import KingCardStatus, Phase, PieceType
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
        game_id="test-stage6",
        turn_number=1,
        active_player="white",
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PlaceTrap reads real card data
# ─────────────────────────────────────────────────────────────────────────────

class TestPlaceTrap:
    def test_reads_radius_shape_trigger_charges_from_card(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wr = _place(board, "white", PieceType.ROOK, "d4", "wr")
        wr.monster_id = "stone_golem"
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)

        assert len(state.traps) == 1
        trap = state.traps[0]
        assert trap.radius == 1
        assert trap.shape == "square"
        assert trap.trigger_condition == "enter_radius"
        assert trap.charges == 1


# ─────────────────────────────────────────────────────────────────────────────
# pit_trap
# ─────────────────────────────────────────────────────────────────────────────

class TestPitTrap:
    def _setup(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.ROOK, "d8", "br")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wr = _place(board, "white", PieceType.ROOK, "d4", "wr")
        wr.monster_id = "stone_golem"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)
        return state, engine

    def test_plain_piece_destroyed_outright(self, registry, rng):
        state, engine = self._setup(registry, rng)
        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False

        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, TrapTriggered) for e in events)
        assert state.board.get_unit(Position.from_algebraic("d5")) is None, \
            "a plain (non-Monster) piece is destroyed outright"
        # Pit Trap is single-charge — destroyed after triggering.
        assert len(state.traps) == 0

    def test_monster_destroyed_but_vessel_survives(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        rook = _place(board, "black", PieceType.ROOK, "d8", "br")
        rook.monster_id = "stone_golem"
        rook.add_status("shield:2")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, MonsterDestroyed) for e in events)
        vessel = state.board.get_unit(Position.from_algebraic("d5"))
        assert vessel is not None, "the Vessel survives"
        assert vessel.monster_id is None, "just a plain Rook now"
        assert not any(s.startswith("shield:") for s in vessel.statuses)
        assert "stone_golem" in black.graveyard

    def test_king_is_immune_but_trap_still_consumes_its_charge(self, registry, rng):
        """Kings are immune to Trap effects (README's original pit_trap design)."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        black_king = _place(board, "black", PieceType.KING, "d6", "bk")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d6"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, TrapTriggered) for e in events), "the Trap still fires"
        king = state.board.get_unit(Position.from_algebraic("d5"))
        assert king is not None, "Kings are immune to Trap effects"
        assert len(state.traps) == 0  # charge still consumed

    def test_ward_of_binding_is_consumed_after_triggering(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.ROOK, "d8", "br")
        _place(board, "black", PieceType.ROOK, "a8", "br2")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["ward_of_binding"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="ward_of_binding", position=Position.from_algebraic("d4"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d5"),
        ), rng)
        assert len(state.traps) == 0, (
            "ward_of_binding is single-use by default and is removed from "
            "the field once it triggers"
        )


# ─────────────────────────────────────────────────────────────────────────────
# counter_strike
# ─────────────────────────────────────────────────────────────────────────────

class TestCounterStrike:
    def test_capturing_piece_becomes_exposed_not_destroyed(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.PAWN, "d5", "bp")   # protected by the trap
        _place(board, "white", PieceType.ROOK, "d1", "wr")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster — off the d-file so it doesn't block the
        # white Rook's slide from d1 to d5. c4's radius-1 area still
        # covers d5.
        bn = _place(board, "black", PieceType.KNIGHT, "c4", "bn")
        bn.monster_id = "shadow_wolf"

        white = _make_player("white")
        black = _make_player("black", hand=["counter_strike"])
        state = _game_state(board, white, black)
        state.active_player = "black"
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="black", card_id="counter_strike", position=Position.from_algebraic("c4"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "white"
        state.get_player("white").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, TrapTriggered) for e in events)
        attacker = state.board.get_unit(Position.from_algebraic("d5"))
        assert attacker is not None and attacker.owner == "white", "attacker survives — no instant kill"
        assert any(s.startswith("exposed:") for s in attacker.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# alarm_beacon
# ─────────────────────────────────────────────────────────────────────────────

class TestAlarmBeacon:
    def _setup(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.ROOK, "d8", "br")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["alarm_beacon"])
        white.deck = ["ward_of_binding"]
        black = _make_player("black", hand=["pit_trap", "shatter_trap"])
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="alarm_beacon", position=Position.from_algebraic("d4"),
        ), rng)
        return state, engine, white, black

    def test_enter_radius_draws_a_card(self, registry, rng):
        state, engine, white, black = self._setup(registry, rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d6"),
        ), rng)

        assert any(isinstance(e, CardDrawn) for e in events)
        assert "ward_of_binding" in white.hand

    def test_enter_radius_reveals_an_enemy_hand_card(self, registry, rng):
        from game.core.events import EnemyCardRevealed

        state, engine, white, black = self._setup(registry, rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d6"),
        ), rng)

        revealed = [e for e in events if isinstance(e, EnemyCardRevealed)]
        assert len(revealed) == 1
        assert revealed[0].player_id == "white"           # the trap owner learns it
        assert revealed[0].revealed_card_id in black.hand  # a real card from black's hand


# ─────────────────────────────────────────────────────────────────────────────
# trap_immunity (ancient_tortoise) skips a Trap's effects once
# ─────────────────────────────────────────────────────────────────────────────

class TestTrapImmunity:
    def test_immune_unit_ignores_pit_trap_effects(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        rook = _place(board, "black", PieceType.ROOK, "d8", "br")
        rook.add_status("trap_immune:1")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d8"), target=Position.from_algebraic("d5"),
        ), rng)

        assert any(isinstance(e, TrapTriggered) for e in events), "the Trap still fires"
        moved = state.board.get_unit(Position.from_algebraic("d5"))
        assert moved is not None, "the immune piece survives pit_trap entirely"
        # Charge consumed: "trap_immune:1" -> "trap_immune:0" (same decrement
        # convention as capture_protection's "shield:N" — see apply_capture_protection).
        assert "trap_immune:0" in moved.statuses


# ─────────────────────────────────────────────────────────────────────────────
# Spells: shatter_trap, cursed_ground, veil_of_stillness, arcane_reposition
# ─────────────────────────────────────────────────────────────────────────────

class TestShatterTrap:
    def test_destroys_enemy_trap(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wr = _place(board, "white", PieceType.ROOK, "d4", "wr")
        wr.monster_id = "stone_golem"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black", hand=["shatter_trap"])
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)
        trap_id = state.traps[0].id

        state.active_player = "black"
        engine.execute(state, ActivateSpell(
            player_id="black", card_id="shatter_trap", target=trap_id,
        ), rng)

        assert state.traps == []

    def test_cannot_target_own_trap(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wr = _place(board, "white", PieceType.ROOK, "d4", "wr")
        wr.monster_id = "stone_golem"

        white = _make_player("white", hand=["pit_trap", "shatter_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)
        trap_id = state.traps[0].id
        state.get_player("white").preparation_action_used = False

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateSpell(
                player_id="white", card_id="shatter_trap", target=trap_id,
            ), rng)


class TestCursedGround:
    def test_curses_a_3x3_area(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Stage 9: a Spell may only target a square one of the caster's own
        # non-Pawn pieces could move into — a Rook on d1 reaches d4.
        _place(board, "white", PieceType.ROOK, "d1", "wr")

        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="cursed_ground", target=(3, 3),  # d4
        ), rng)

        # Center + all 8 neighbours cursed.
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                sq = state.board.get_square(Position(3 + df, 3 + dr))
                assert any(e.startswith("cursed:") for e in sq.temporary_effects)
        # Outside the 3x3 area — untouched.
        far = state.board.get_square(Position.from_algebraic("a1"))
        assert not any(e.startswith("cursed:") for e in far.temporary_effects)

    def test_entry_is_legal_but_immobilizes_on_arrival(self, registry, rng):
        """
        Unlike a frozen square, a cursed square may be ENTERED — the piece
        that lands there just becomes immobilized afterward.  Affects
        either side, unlike enemy-only Traps.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.PAWN, "d6", "bp")
        # Stage 9: a Spell may only target a square one of the caster's own
        # non-Pawn pieces could move into — a Rook on d1 reaches d4.
        _place(board, "white", PieceType.ROOK, "d1", "wr")

        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="cursed_ground", target=(3, 3),  # d4, radius 1 -> covers d5
        ), rng)

        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        events = engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d6"), target=Position.from_algebraic("d5"),
        ), rng)

        # The move succeeded — entry into a cursed square is legal.
        moved = state.board.get_unit(Position.from_algebraic("d5"))
        assert moved is not None
        assert any(s.startswith("immobilized:") for s in moved.statuses)


class TestVeilOfStillness:
    def test_blocks_the_whole_column(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Stage 9: a Spell may only target a square one of the caster's own
        # non-Pawn pieces could move into — a Rook on h1 reaches d1.
        _place(board, "white", PieceType.ROOK, "h1", "wr")

        white = _make_player("white", hand=["veil_of_stillness"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="veil_of_stillness", target=(3, 0),  # file d
        ), rng)

        for r in range(8):
            sq = state.board.get_square(Position(3, r))
            assert any(e.startswith("blocked:") for e in sq.temporary_effects)
        # File e (not targeted) is untouched.
        untouched = state.board.get_square(Position(4, 0))
        assert not any(e.startswith("blocked:") for e in untouched.temporary_effects)

    def test_blocked_square_cannot_be_entered(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        rook = _place(board, "black", PieceType.ROOK, "d8", "br")
        # Stage 9: a Spell may only target a square one of the caster's own
        # non-Pawn pieces could move into — a Rook on h1 reaches d1.
        _place(board, "white", PieceType.ROOK, "h1", "wr")

        white = _make_player("white", hand=["veil_of_stillness"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="veil_of_stillness", target=(3, 0),
        ), rng)

        moves = get_pseudo_legal_moves(state.board, Position.from_algebraic("d8"), rook)
        # No move lands on the d-file (d1..d7) — but the rook can still
        # slide sideways along rank 8, which never touches file d.
        assert all(m.file != 3 for m in moves), "no move may land on the blocked d-file"
        assert Position.from_algebraic("g8") in moves  # h8 is occupied by black's own King


class TestArcaneReposition:
    def test_teleports_own_piece_within_range(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.PAWN, "d4", "wp")

        white = _make_player("white", hand=["arcane_reposition"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="arcane_reposition",
            target={"position": (3, 3), "destination": (3, 6)},  # d4 -> d7, 3 squares
        ), rng)

        assert state.board.get_unit(Position.from_algebraic("d4")) is None
        assert state.board.get_unit(Position.from_algebraic("d7")) is not None

    def test_exceeding_max_distance_raises(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.PAWN, "d4", "wp")

        white = _make_player("white", hand=["arcane_reposition"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateSpell(
                player_id="white", card_id="arcane_reposition",
                target={"position": (3, 3), "destination": (3, 7)},  # 4 squares — too far
            ), rng)

    def test_cannot_target_kings(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "d4", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")

        white = _make_player("white", hand=["arcane_reposition"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateSpell(
                player_id="white", card_id="arcane_reposition",
                target={"position": (3, 3), "destination": (3, 4)},
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# time_anchor — snapshot-based cancel_move
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeAnchor:
    def _setup(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.PAWN, "d7", "bp")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "a2", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["time_anchor"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="time_anchor", position=Position.from_algebraic("a2"),
        ), rng)
        return state, engine

    def test_cancels_opponents_last_move(self, registry, rng):
        state, engine = self._setup(registry, rng)
        trap_id = state.traps[0].id

        # Black moves d7-d5.
        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("d7"), target=Position.from_algebraic("d5"),
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d5")) is not None

        # White cancels it during their own next Preparation.
        state.phase = Phase.PREPARATION
        state.active_player = "white"
        state.get_player("white").preparation_action_used = False
        events = engine.execute(state, ActivateTrap(
            player_id="white", trap_instance_id=trap_id,
        ), rng)

        assert any(isinstance(e, MoveCancelled) for e in events)
        assert state.board.get_unit(Position.from_algebraic("d7")) is not None
        assert state.board.get_unit(Position.from_algebraic("d5")) is None
        # Single charge — the Trap is gone after firing.
        assert state.traps == []

    def test_no_move_to_cancel_raises(self, registry, rng):
        state, engine = self._setup(registry, rng)
        trap_id = state.traps[0].id
        state.phase = Phase.PREPARATION
        state.active_player = "white"
        state.get_player("white").preparation_action_used = False

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateTrap(
                player_id="white", trap_instance_id=trap_id,
            ), rng)
        # Failed activation must not consume the Trap or the prep action.
        assert len(state.traps) == 1
        assert state.get_player("white").preparation_action_used is False

    def test_cannot_cancel_a_duel_triggering_move(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        black_king = _place(board, "black", PieceType.KING, "h1", "bk")
        _place(board, "black", PieceType.ROOK, "h8", "br")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster.
        wn = _place(board, "white", PieceType.KNIGHT, "a2", "wn")
        wn.monster_id = "dark_magician"

        white = _make_player("white", hand=["time_anchor"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="time_anchor", position=Position.from_algebraic("a2"),
        ), rng)
        trap_id = state.traps[0].id

        # Black rook captures white's King directly (Stage-0-style simplified
        # "capture the King" rule triggers a Final Duel; legality of moving
        # INTO check is not the point of this test, so place white's King far
        # enough that the rook's move is otherwise legal).
        state.phase = Phase.CHESS
        state.active_player = "black"
        state.get_player("black").chess_move_used = False
        engine.execute(state, MovePiece(
            player_id="black", source=Position.from_algebraic("h8"), target=Position.from_algebraic("a8"),
        ), rng)

        assert state.move_history["black"].triggered_duel is False  # sanity: that move was safe

        # Now trigger a real King capture on a fresh setup instead.
        board2 = BoardState()
        _place(board2, "white", PieceType.KING, "a1", "wk2")
        _place(board2, "black", PieceType.KING, "h8", "bk2")
        _place(board2, "black", PieceType.ROOK, "a8", "br2")
        # Stage 9: a Trap can only be set on a square hosting the caster's
        # own summoned Monster — b2 (not a2) so it doesn't block the black
        # Rook's capture slide down the a-file below.
        wn2 = _place(board2, "white", PieceType.KNIGHT, "b2", "wn2")
        wn2.monster_id = "dark_magician"
        white2 = _make_player("white", hand=["time_anchor"])
        black2 = _make_player("black")
        state2 = _game_state(board2, white2, black2)
        engine2 = RulesEngine(registry=registry)
        engine2.execute(state2, PlaceTrap(
            player_id="white", card_id="time_anchor", position=Position.from_algebraic("b2"),
        ), rng)
        trap_id2 = state2.traps[0].id

        state2.phase = Phase.CHESS
        state2.active_player = "black"
        state2.get_player("black").chess_move_used = False
        engine2.execute(state2, MovePiece(
            player_id="black", source=Position.from_algebraic("a8"), target=Position.from_algebraic("a1"),
        ), rng)
        assert state2.phase == Phase.FINAL_DUEL
        assert state2.move_history["black"].triggered_duel is True

        state2.phase = Phase.PREPARATION  # force back for the test's sake
        state2.active_player = "white"
        state2.get_player("white").preparation_action_used = False
        with pytest.raises(IllegalActionError):
            engine2.execute(state2, ActivateTrap(
                player_id="white", trap_instance_id=trap_id2,
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 9 — targeting restrictions
#   • Spells (target_type "position"/"zone") may be aimed at a square
#     EITHER one of the caster's own non-Pawn pieces could move into right
#     now, OR one covered by one of the caster's own COMPLETE Buildings.
#   • Traps may be set EITHER on a square hosting one of the caster's own
#     summoned Monsters, OR on an empty square covered by one of the
#     caster's own COMPLETE Buildings.
# In both cases only ONE of the two needs to hold — see
# mechanics/territory.py. Building coverage is deliberately NARROWER than
# plain Territory (user correction): the bare home three ranks do NOT
# qualify on their own — only squares an actual Building's radius reaches
# do. These tests use minimal boards with no Buildings at all, so the
# Building-coverage half of the OR is always empty here — only the
# piece-reach / Monster-anchor half is ever exercised.
# Both restrictions are enforced in TWO places: get_legal_actions() (so the
# AI/UI never offers an illegal target) and the executor (so a hand-built
# illegal action is still rejected).
# ─────────────────────────────────────────────────────────────────────────────

class TestSpellTargetingRestriction:
    def test_legal_actions_exclude_unreachable_squares(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        # Only a Rook on d1 — reaches the whole d-file and rank 1, nothing else.
        _place(board, "white", PieceType.ROOK, "d1", "wr")

        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        targets = {
            a.target for a in legal
            if isinstance(a, ActivateSpell) and a.card_id == "cursed_ground"
        }
        assert (3, 3) in targets, "d4 is reachable by the Rook on d1"
        assert (7, 7) not in targets, "h8 is nowhere near the Rook's reach"
        # No Buildings exist on this board, so Building coverage is empty —
        # a1 is occupied by white's own King (not reachable by "a non-Pawn
        # piece could move into") and isn't covered by anything else either.
        assert (0, 0) not in targets, "a1 is unreachable and there is no Building to cover it"
        assert (4, 3) not in targets, "e4 is outside both the Rook's reach and any Building"

    def test_execute_rejects_unreachable_square(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.ROOK, "d1", "wr")

        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateSpell(
                player_id="white", card_id="cursed_ground", target=(7, 7),  # h8 — unreachable
            ), rng)

    def test_a_second_non_pawn_piece_widens_the_reachable_set(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.KNIGHT, "g6", "wn")   # reaches e5, among others

        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        targets = {
            a.target for a in legal
            if isinstance(a, ActivateSpell) and a.card_id == "cursed_ground"
        }
        e5 = Position.from_algebraic("e5")
        assert (e5.file, e5.rank) in targets, "e5 is a Knight move away from g6"

    def test_piece_target_spells_unaffected(self, registry, rng):
        """
        The non-Pawn-movement restriction only gates "position"/"zone"
        Spells — a "piece" target_type Spell like arcane_reposition is
        untouched, even with no non-Pawn piece anywhere on the board.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.PAWN, "d4", "wp")

        white = _make_player("white", hand=["arcane_reposition"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(
            player_id="white", card_id="arcane_reposition",
            target={"position": (3, 3), "destination": (3, 6)},  # d4 -> d7
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d7")) is not None


class TestTrapPlacementRestriction:
    def test_legal_actions_exclude_empty_and_foreign_squares(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        wn = _place(board, "white", PieceType.KNIGHT, "b1", "wn")
        wn.monster_id = "dark_magician"
        bn = _place(board, "black", PieceType.KNIGHT, "g8", "bn")
        bn.monster_id = "shadow_wolf"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        positions = {
            a.position for a in legal
            if isinstance(a, PlaceTrap) and a.card_id == "pit_trap"
        }
        assert Position.from_algebraic("b1") in positions, \
            "white's own summoned Monster is a legal Trap square"
        assert Position.from_algebraic("g8") not in positions, \
            "an enemy Monster is not a legal Trap square"
        assert Position.from_algebraic("d4") not in positions, \
            "an empty square is not a legal Trap square"

    def test_execute_rejects_empty_square(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, PlaceTrap(
                player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
            ), rng)

    def test_execute_rejects_opponents_monster(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        bn = _place(board, "black", PieceType.KNIGHT, "d4", "bn")
        bn.monster_id = "shadow_wolf"

        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, PlaceTrap(
                player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
            ), rng)
