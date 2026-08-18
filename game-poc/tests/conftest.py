"""
conftest.py — Shared pytest fixtures for Stage 0 tests.

Provides:
    standard_board  — freshly initialised 8×8 starting position
    empty_board     — completely empty board
    game_state      — minimal 2-player GameState in PREPARATION phase
    rng             — DeterministicRNG seeded with 42
    registry        — CardRegistry loaded from the PoC YAML data files
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import (
    ConstructionStatus,
    KingCardStatus,
    Phase,
    PieceType,
)
from game.core.rng import DeterministicRNG
from game.core.state import (
    BuildingPoolEntry,
    GameState,
    KingCardState,
    PlayerState,
    RitualState,
)

# Path to the data directory (relative to this file's package root).
_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture
def rng() -> DeterministicRNG:
    return DeterministicRNG(seed=42)


@pytest.fixture
def standard_board() -> BoardState:
    return BoardState.make_standard_start()


@pytest.fixture
def empty_board() -> BoardState:
    return BoardState()


@pytest.fixture
def registry():
    """Load the PoC card registry from YAML files."""
    from game.cards.card import load_registry_from_yaml
    return load_registry_from_yaml(_DATA_DIR)


def make_player(
    player_id: str,
    hand: list[str] | None = None,
    deck: list[str] | None = None,
) -> PlayerState:
    """Helper: create a minimal PlayerState for testing."""
    ps = PlayerState(player_id=player_id)
    ps.hand = hand or []
    ps.deck = deck or []
    ps.king_pool = [
        KingCardState(king_card_id=f"{player_id}-king-a", status=KingCardStatus.HIDDEN),
        KingCardState(king_card_id=f"{player_id}-king-b", status=KingCardStatus.HIDDEN),
        KingCardState(king_card_id=f"{player_id}-king-c", status=KingCardStatus.HIDDEN),
    ]
    ps.building_pool = [
        BuildingPoolEntry(building_card_id="fortress", copies_available=2),
        BuildingPoolEntry(building_card_id="shrine", copies_available=1),
    ]
    return ps


@pytest.fixture
def game_state(standard_board: BoardState) -> GameState:
    """
    A minimal 2-player GameState in Phase.PREPARATION.
    White to move.  Deck and hand are pre-populated with PoC card IDs.
    """
    white = make_player(
        "white",
        hand=["dark_magician", "arcane_sentinel", "pit_trap"],
        deck=["wyrm_knight", "arcane_reposition", "veil_of_stillness",
              "iron_vanguard", "blade_dancer", "ward_of_binding"],
    )
    black = make_player(
        "black",
        hand=["wyrm_knight", "ritual_insight", "counter_strike"],
        deck=["dragon_herald", "stone_golem", "shadow_wolf",
              "shatter_trap", "cursed_ground", "alarm_beacon"],
    )

    state = GameState(
        game_id="test-game-001",
        turn_number=1,
        active_player="white",
        phase=Phase.PREPARATION,
        board=standard_board,
        players={"white": white, "black": black},
        rng_seed=42,
    )
    return state
