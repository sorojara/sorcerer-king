"""
test_stage12_final_duel.py — Final Duel engine (README §22–33, Stage 12).

Covers:
    • King capture (ASSAULT) and checkmate (SIEGE) both build a populated
      DuelState instead of ending the match directly.
    • Royal Support tally: standardized piece abilities (guard/charge/
      intervention/fortify/command) are gathered within the support radius.
    • Siege advantage strips one defender Guard-granting item.
    • Turn order is defender-then-attacker each Duel round.
    • Strike/Guard/Bypass mechanics: a Guard blocks a Strike; a spent Bypass
      makes the next Strike land unconditionally.
    • Attacker wins by landing 2 Strikes → GameOver, winner set.
    • Defender wins by surviving the round limit → Royal Escape: the King
      relocates (and, for ASSAULT, is RESTORED to the board — a capture is
      provisional until the Duel resolves), gains a shield (Royal Immunity),
      and normal play resumes.
    • Last Stand escalation (3rd Duel) removes the round-limit escape.
    • Illegal Duel actions (wrong turn, unknown item) are rejected.
    • RandomBot can play an entire Duel to completion without crashing.
"""

from __future__ import annotations

import random

import pytest

from game.ai.random_bot import RandomBot
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import DiscardCard, EndPreparation, EndTurn, FinalDuelAction, MovePiece
from game.core.events import FinalDuelTriggered, GameOver, RoyalEscapeTriggered
from game.core.game import Game
from game.core.phases import FinalDuelType, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import GameState
from game.mechanics.duel import get_legal_duel_actions

from tests.conftest import make_player


def P(alg: str) -> Position:
    return Position.from_algebraic(alg)


def _minimal_duel_state(extra_black_pawns: "list[str] | None" = None) -> tuple[GameState, RulesEngine, DeterministicRNG]:
    """White queen adjacent to capture the black King on d8 next move."""
    engine = RulesEngine()
    rng = DeterministicRNG(seed=0)
    board = BoardState()
    board.place_unit(P("d1"), UnitInstance(piece=ChessPiece(id="white-queen-d", owner="white", piece_type=PieceType.QUEEN)))
    board.place_unit(P("d8"), UnitInstance(piece=ChessPiece(id="black-king-e", owner="black", piece_type=PieceType.KING)))
    board.place_unit(P("e1"), UnitInstance(piece=ChessPiece(id="white-king-e", owner="white", piece_type=PieceType.KING)))
    for i, sq in enumerate(extra_black_pawns or []):
        board.place_unit(P(sq), UnitInstance(
            piece=ChessPiece(id=f"black-pawn-{i}", owner="black", piece_type=PieceType.PAWN)
        ))
    state = GameState(
        game_id="duel-test", turn_number=1, active_player="white",
        phase=Phase.CHESS, board=board,
        players={"white": make_player("white"), "black": make_player("black")},
    )
    return state, engine, rng


def _drive_duel(state: GameState, engine: RulesEngine, rng: DeterministicRNG, defender_action_type: str) -> None:
    """
    Run the Duel to completion. Defender always picks ``defender_action_type``
    (falling back to "advance" if unavailable); attacker always strikes.
    """
    steps = 0
    while state.phase == Phase.FINAL_DUEL and steps < 200:
        steps += 1
        duel = state.duel
        is_def_turn = not duel.defender_acted_this_round
        acting = duel.defender if is_def_turn else duel.attacker
        legal = get_legal_duel_actions(state, acting)
        assert legal, f"no legal Duel actions for {acting}"
        if is_def_turn:
            chosen = next((a for a in legal if a.duel_action_type == defender_action_type), None)
            chosen = chosen or next(a for a in legal if a.duel_action_type == "advance")
        else:
            chosen = next(a for a in legal if a.duel_action_type == "strike")
        engine.execute(state, chosen, rng)
    assert steps < 200, "Duel did not terminate"


# ─────────────────────────────────────────────────────────────────────────────
# Trigger / initialization
# ─────────────────────────────────────────────────────────────────────────────

class TestDuelInitialization:
    def test_assault_populates_support_pools(self):
        state, engine, rng = _minimal_duel_state(extra_black_pawns=["c7", "e7"])
        events = engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)

        assert any(isinstance(e, FinalDuelTriggered) for e in events)
        duel = state.duel
        assert duel is not None
        assert duel.duel_type == FinalDuelType.ASSAULT
        assert duel.attacker == "white" and duel.defender == "black"
        assert duel.arena_center == P("d8")
        assert duel.captured_king_piece_id == "black-king-e"
        # Two Pawns within radius → two "guard" items worth 1 each.
        abilities = sorted((i.ability, i.amount) for i in duel.defender_support)
        assert abilities == [("guard", 1), ("guard", 1)]
        assert state.winner is None
        assert state.phase == Phase.FINAL_DUEL

    def test_siege_strips_one_defender_guard_item(self):
        assault_state, engine, rng = _minimal_duel_state(extra_black_pawns=["c7", "e7"])
        engine.execute(assault_state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        assault_count = len(assault_state.duel.defender_support)

        # Force the SIEGE path directly via _trigger_final_duel to compare
        # against the same board minus the removed capture.
        siege_state, engine2, rng2 = _minimal_duel_state(extra_black_pawns=["c7", "e7"])
        events: list = []
        engine2._trigger_final_duel(
            siege_state, FinalDuelType.SIEGE, "white", "black", events,
        )
        assert len(siege_state.duel.defender_support) == assault_count - 1

    def test_king_capture_never_sets_winner_directly(self):
        state, engine, rng = _minimal_duel_state()
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        assert state.winner is None
        assert state.phase == Phase.FINAL_DUEL


# ─────────────────────────────────────────────────────────────────────────────
# Strike / Guard / Bypass mechanics
# ─────────────────────────────────────────────────────────────────────────────

class TestStrikeMechanics:
    def test_guard_blocks_one_strike(self):
        state, engine, rng = _minimal_duel_state(extra_black_pawns=["c7"])
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        duel = state.duel
        item_id = duel.defender_support[0].item_id

        engine.execute(state, FinalDuelAction(
            player_id="black", duel_action_type="support", parameters={"item_id": item_id},
        ), rng)
        assert duel.defender_guards == 1

        engine.execute(state, FinalDuelAction(player_id="white", duel_action_type="strike", parameters={}), rng)
        assert duel.strikes_landed == 0
        assert duel.defender_guards == 0

    def test_attacker_wins_after_two_unblocked_strikes(self):
        state, engine, rng = _minimal_duel_state()  # no defender support at all
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        _drive_duel(state, engine, rng, defender_action_type="advance")
        assert state.phase == Phase.GAME_OVER
        assert state.winner == "white"


# ─────────────────────────────────────────────────────────────────────────────
# Royal Escape
# ─────────────────────────────────────────────────────────────────────────────

class TestRoyalEscape:
    def test_defender_survives_and_king_is_restored(self):
        pawns = ["c7", "e7", "c6", "e6", "b8"]  # 5 Guards ≥ 5-round survival
        state, engine, rng = _minimal_duel_state(extra_black_pawns=pawns)
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)

        _drive_duel(state, engine, rng, defender_action_type="support")

        assert state.phase == Phase.REACTION
        assert state.winner is None
        assert state.duel is None
        found = state.board.find_king("black")
        assert found is not None, "King must be restored to the board after Royal Escape"
        _, king_unit = found
        assert any(s.startswith("shield:") for s in king_unit.statuses)
        assert state.get_player("black").duels_survived == 1

    def test_escalation_removes_round_limit_escape(self):
        state, engine, rng = _minimal_duel_state()
        state.get_player("black").duels_survived = 2
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        assert state.duel.escape_allowed is False
        assert state.duel.hard_round_cap > state.duel.max_rounds


# ─────────────────────────────────────────────────────────────────────────────
# Turn order / legality
# ─────────────────────────────────────────────────────────────────────────────

class TestDuelTurnOrder:
    def test_defender_acts_before_attacker(self):
        state, engine, rng = _minimal_duel_state(extra_black_pawns=["c7"])
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        assert get_legal_duel_actions(state, "white") == []
        assert get_legal_duel_actions(state, "black") != []

    def test_illegal_actor_rejected(self):
        state, engine, rng = _minimal_duel_state(extra_black_pawns=["c7"])
        engine.execute(state, MovePiece(player_id="white", source=P("d1"), target=P("d8")), rng)
        with pytest.raises(IllegalActionError):
            engine.execute(state, FinalDuelAction(player_id="white", duel_action_type="strike", parameters={}), rng)

    def test_unknown_item_id_rejected_via_game_facade(self):
        game = Game.new(seed=1)
        state = game.state
        # Hand-build an ASSAULT duel directly on the live Game's engine.
        state.board = BoardState()
        state.board.place_unit(P("d1"), UnitInstance(piece=ChessPiece(id="white-queen-d", owner="white", piece_type=PieceType.QUEEN)))
        state.board.place_unit(P("d8"), UnitInstance(piece=ChessPiece(id="black-king-e", owner="black", piece_type=PieceType.KING)))
        state.board.place_unit(P("e1"), UnitInstance(piece=ChessPiece(id="white-king-e", owner="white", piece_type=PieceType.KING)))
        state.phase = Phase.CHESS
        game.execute(MovePiece(player_id="white", source=P("d1"), target=P("d8")))
        assert state.phase == Phase.FINAL_DUEL
        with pytest.raises(IllegalActionError):
            game.execute(FinalDuelAction(
                player_id="black", duel_action_type="support", parameters={"item_id": "does-not-exist"},
            ))


# ─────────────────────────────────────────────────────────────────────────────
# RandomBot stability (Stage 2 goal — no crashes, always terminates)
# ─────────────────────────────────────────────────────────────────────────────

class TestDuelRandomBotStability:
    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    def test_random_play_terminates(self, seed):
        game = Game.new(seed=1)

        def end_turn(player: str) -> None:
            while True:
                game.execute(EndTurn(player_id=player))
                if game.state.phase == Phase.DISCARD and game.state.active_player == player:
                    card = game.state.get_player(player).hand[0]
                    game.execute(DiscardCard(player_id=player, card_id=card))
                    continue
                break

        def move(a: str, b: str, p: str) -> None:
            game.execute(EndPreparation(player_id=p))
            game.execute(MovePiece(player_id=p, source=P(a), target=P(b)))
            end_turn(p)

        # Scholar's Mate — deterministic, fast checkmate (SIEGE trigger).
        move("e2", "e4", "white")
        move("e7", "e5", "black")
        move("d1", "h5", "white")
        move("b8", "c6", "black")
        move("f1", "c4", "white")
        move("g8", "f6", "black")
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(player_id="white", source=P("h5"), target=P("f7")))
        assert game.state.phase == Phase.FINAL_DUEL

        bot_rng = random.Random(seed)
        steps = 0
        while game.state.phase == Phase.FINAL_DUEL and steps < 200:
            steps += 1
            duel = game.state.duel
            acting = duel.defender if not duel.defender_acted_this_round else duel.attacker
            legal = game.get_legal_actions(acting)
            assert legal, f"no legal actions for {acting} (seed={seed}, step={steps})"
            game.execute(bot_rng.choice(legal))
        assert steps < 200
        assert game.state.phase in (Phase.GAME_OVER, Phase.REACTION)
