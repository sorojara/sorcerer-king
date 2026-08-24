"""
Stage 11 — README §15.2: revelation as a player decision.

Two rules land together here:

  1. A Ritual must be REVEALED before ActivateRitual will touch it. The
     summon comes off the top of the revelation ladder, never instead of
     climbing it, so the opponent always sees the threat before it lands.

  2. RevealRitual lets the owner climb that ladder deliberately — one step
     at a time, free of the preparation action, at most once per turn.

The pair is what makes §15 a decision rather than a passive drip: the
involuntary triggers of §15.1 (being checked, losing the Queen, an enemy's
omen_bell) still push a player up the ladder, but the player chooses when
to pay the information themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ActivateRitual, RevealRitual
from game.core.events import RitualRevelationChanged
from game.core.phases import KingCardStatus, Phase, PieceType, RevelationState
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import GameState, KingCardState, PlayerState, RitualState

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng() -> DeterministicRNG:
    return DeterministicRNG(seed=42)


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> None:
    board.place_unit(
        Position.from_algebraic(alg),
        UnitInstance(piece=ChessPiece(id=pid, owner=owner, piece_type=ptype)),
    )


def _player(pid: str, rituals: list[RitualState] | None = None) -> PlayerState:
    ps = PlayerState(player_id=pid)
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    ps.ritual_pool = rituals if rituals is not None else [
        RitualState(ritual_id="rite_of_the_wyrm"),
    ]
    return ps


def _wyrm_board() -> BoardState:
    """The exact formation rite_of_the_wyrm asks for: pawn b1, knight c2, bishop c1."""
    board = BoardState()
    _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")
    _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
    _place(board, "white", PieceType.KNIGHT, "c2", "white-knight-1")
    return board


def _state(board: BoardState, white: PlayerState, black: PlayerState) -> GameState:
    return GameState(
        game_id="test-voluntary-reveal",
        turn_number=1,
        active_player="white",
        phase=Phase.PREPARATION,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


_WYRM_SACRIFICE = [
    Position.from_algebraic("b1"),
    Position.from_algebraic("c2"),
    Position.from_algebraic("c1"),
]


# ─────────────────────────────────────────────────────────────────────────────
# The activation gate
# ─────────────────────────────────────────────────────────────────────────────

class TestActivationRequiresRevealed:
    @pytest.mark.parametrize(
        "revelation", [RevelationState.SEALED, RevelationState.FORETOLD],
    )
    def test_an_unrevealed_ritual_cannot_be_activated(self, registry, rng, revelation):
        state = _state(
            _wyrm_board(),
            _player("white", [RitualState(ritual_id="rite_of_the_wyrm", revelation=revelation)]),
            _player("black"),
        )
        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError, match="REVEALED"):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=list(_WYRM_SACRIFICE),
            ), rng)

    def test_a_rejected_attempt_costs_nothing(self, registry, rng):
        """No material, no preparation action — the attempt never happened."""
        state = _state(_wyrm_board(), _player("white"), _player("black"))
        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=list(_WYRM_SACRIFICE),
            ), rng)
        for alg in ("b1", "c2", "c1"):
            assert state.board.get_unit(Position.from_algebraic(alg)) is not None
        assert not state.get_player("white").preparation_action_used

    def test_a_revealed_ritual_activates(self, registry, rng):
        state = _state(
            _wyrm_board(),
            _player("white", [RitualState(
                ritual_id="rite_of_the_wyrm", revelation=RevelationState.REVEALED,
            )]),
            _player("black"),
        )
        engine = RulesEngine(registry=registry)
        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_the_wyrm",
            sacrifice_positions=list(_WYRM_SACRIFICE),
        ), rng)
        vessel = state.board.get_unit(Position.from_algebraic("c1"))
        assert vessel is not None and vessel.monster_id == "stormcall_wyrm"

    def test_unrevealed_rituals_are_never_offered_as_legal(self, registry):
        state = _state(_wyrm_board(), _player("white"), _player("black"))
        engine = RulesEngine(registry=registry)
        legal = engine.get_legal_actions(state, "white")
        assert not [a for a in legal if isinstance(a, ActivateRitual)]

        state.get_player("white").ritual_pool[0].revelation = RevelationState.REVEALED
        legal = engine.get_legal_actions(state, "white")
        assert [a for a in legal if isinstance(a, ActivateRitual)]


# ─────────────────────────────────────────────────────────────────────────────
# RevealRitual
# ─────────────────────────────────────────────────────────────────────────────

class TestRevealRitual:
    def _engine_state(self, revelation=RevelationState.SEALED):
        return (
            RulesEngine(registry=load_registry_from_yaml(_DATA_DIR)),
            _state(
                _wyrm_board(),
                _player("white", [RitualState(
                    ritual_id="rite_of_the_wyrm", revelation=revelation,
                )]),
                _player("black"),
            ),
        )

    def test_one_step_sealed_to_foretold(self, rng):
        engine, state = self._engine_state()
        events = engine.execute(
            state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng,
        )
        assert state.get_player("white").ritual_pool[0].revelation == RevelationState.FORETOLD
        change = next(e for e in events if isinstance(e, RitualRevelationChanged))
        assert change.old_state == RevelationState.SEALED
        assert change.new_state == RevelationState.FORETOLD

    def test_it_never_skips_a_step(self, rng):
        engine, state = self._engine_state()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert state.get_player("white").ritual_pool[0].revelation == RevelationState.FORETOLD

    def test_it_does_not_consume_the_preparation_action(self, rng):
        engine, state = self._engine_state()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert not state.get_player("white").preparation_action_used

    def test_only_one_step_per_turn(self, rng):
        engine, state = self._engine_state()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        with pytest.raises(IllegalActionError, match="already revealed a Ritual this turn"):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng,
            )

    def test_the_cap_is_per_turn_not_per_match(self, rng):
        engine, state = self._engine_state()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        state.get_player("white").reset_turn_flags()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert state.get_player("white").ritual_pool[0].revelation == RevelationState.REVEALED

    def test_the_cap_covers_the_whole_pool_not_one_ritual(self, rng):
        """Two different Rituals still share the one step per turn."""
        engine = RulesEngine(registry=load_registry_from_yaml(_DATA_DIR))
        state = _state(
            _wyrm_board(),
            _player("white", [
                RitualState(ritual_id="rite_of_the_wyrm"),
                RitualState(ritual_id="sevenfold_circle"),
            ]),
            _player("black"),
        )
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        with pytest.raises(IllegalActionError):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="sevenfold_circle"), rng,
            )

    def test_an_already_revealed_ritual_is_rejected(self, rng):
        engine, state = self._engine_state(RevelationState.REVEALED)
        with pytest.raises(IllegalActionError, match="already fully revealed"):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng,
            )

    def test_a_completed_ritual_is_rejected(self, rng):
        engine, state = self._engine_state()
        state.get_player("white").ritual_pool[0].activated = True
        with pytest.raises(IllegalActionError, match="already been completed"):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng,
            )

    def test_a_ritual_outside_your_pool_is_rejected(self, rng):
        engine, state = self._engine_state()
        with pytest.raises(IllegalActionError, match="not in your pool"):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="grand_works"), rng,
            )

    def test_it_is_preparation_only(self, rng):
        engine, state = self._engine_state()
        state.phase = Phase.CHESS
        with pytest.raises(IllegalActionError):
            engine.execute(
                state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Legal-action offering
# ─────────────────────────────────────────────────────────────────────────────

class TestRevealRitualIsOffered:
    def _state_with(self, revelation=RevelationState.SEALED):
        return _state(
            _wyrm_board(),
            _player("white", [RitualState(
                ritual_id="rite_of_the_wyrm", revelation=revelation,
            )]),
            _player("black"),
        )

    def test_offered_for_every_unrevealed_ritual(self, registry):
        engine = RulesEngine(registry=registry)
        state = self._state_with()
        offered = [
            a for a in engine.get_legal_actions(state, "white")
            if isinstance(a, RevealRitual)
        ]
        assert [a.ritual_id for a in offered] == ["rite_of_the_wyrm"]

    def test_not_offered_once_revealed(self, registry):
        engine = RulesEngine(registry=registry)
        state = self._state_with(RevelationState.REVEALED)
        assert not [
            a for a in engine.get_legal_actions(state, "white")
            if isinstance(a, RevealRitual)
        ]

    def test_not_offered_twice_in_one_turn(self, registry, rng):
        engine = RulesEngine(registry=registry)
        state = self._state_with()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert not [
            a for a in engine.get_legal_actions(state, "white")
            if isinstance(a, RevealRitual)
        ]

    def test_still_offered_after_the_preparation_action_is_spent(self, registry, rng):
        """
        The whole point of it being free: summoning first must not lock the
        player out of advancing a Ritual on the same turn.
        """
        engine = RulesEngine(registry=registry)
        board = _wyrm_board()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        white = _player("white")
        white.hand = ["dark_magician"]
        state = _state(board, white, _player("black"))
        state.get_player("white").preparation_action_used = True

        offered = [
            a for a in engine.get_legal_actions(state, "white")
            if isinstance(a, RevealRitual)
        ]
        assert [a.ritual_id for a in offered] == ["rite_of_the_wyrm"]
        engine.execute(state, offered[0], rng)
        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD


# ─────────────────────────────────────────────────────────────────────────────
# The ladder end to end
# ─────────────────────────────────────────────────────────────────────────────

class TestTheLadder:
    def test_sealed_to_summoned_takes_three_turns(self, registry, rng):
        """
        SEALED → FORETOLD → REVEALED → activate, one rung per turn. The
        opponent sees the archetype a full turn before the Monster lands.
        """
        engine = RulesEngine(registry=registry)
        state = _state(_wyrm_board(), _player("white"), _player("black"))
        white = state.get_player("white")

        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD

        white.reset_turn_flags()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        assert white.ritual_pool[0].revelation == RevelationState.REVEALED

        white.reset_turn_flags()
        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_the_wyrm",
            sacrifice_positions=list(_WYRM_SACRIFICE),
        ), rng)
        assert white.ritual_pool[0].activated is True
        assert state.board.get_unit(
            Position.from_algebraic("c1"),
        ).monster_id == "stormcall_wyrm"

    def test_the_opponent_learns_exactly_one_rung_at_a_time(self, registry, rng):
        """The Observation boundary moves in step with the ladder, no faster."""
        from game.core.observation import build_observation

        engine = RulesEngine(registry=registry)
        state = _state(_wyrm_board(), _player("white"), _player("black"))
        white = state.get_player("white")

        seen = build_observation(state, "black", registry=registry).opponent_ritual_info[0]
        assert seen.revelation == RevelationState.SEALED
        assert seen.archetype is None

        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        seen = build_observation(state, "black", registry=registry).opponent_ritual_info[0]
        assert seen.revelation == RevelationState.FORETOLD
        assert seen.archetype == "dragon"
        assert seen.required_vessel == "bishop"
        assert seen.ritual_id is None          # identity still withheld

        white.reset_turn_flags()
        engine.execute(state, RevealRitual(player_id="white", ritual_id="rite_of_the_wyrm"), rng)
        seen = build_observation(state, "black", registry=registry).opponent_ritual_info[0]
        assert seen.revelation == RevelationState.REVEALED
        assert seen.ritual_id == "rite_of_the_wyrm"
