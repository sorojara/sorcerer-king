"""
effect_harness.py — shared scaffolding for the per-effect scenario tests.

``test_effect_scenarios_*.py`` builds one hand-made board position per
effect type declared in ``data/*.yaml`` and drives it through the REAL
engine entry points (RulesEngine.execute, the trap trigger-detection
pathways, the movement generator) rather than calling handlers directly.

The point is to catch effects that are wired up but never actually
reachable in play — a status nothing reads, a trap whose handler no-ops
because the context it needs is only ever built on a different code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import (
    ConstructionStatus,
    KingCardStatus,
    Phase,
    PieceType,
    RevelationState,
)
from game.core.rng import DeterministicRNG
from game.core.rules import RulesEngine
from game.core.state import (
    BuildingInstance,
    BuildingPoolEntry,
    GameState,
    KingCardState,
    PlayerState,
    RitualState,
    TrapInstance,
)

DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"

_PT = {
    "king": PieceType.KING,
    "queen": PieceType.QUEEN,
    "rook": PieceType.ROOK,
    "bishop": PieceType.BISHOP,
    "knight": PieceType.KNIGHT,
    "pawn": PieceType.PAWN,
}


@pytest.fixture(scope="session")
def registry():
    return load_registry_from_yaml(DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


def engine(registry) -> RulesEngine:
    return RulesEngine(registry=registry)


def new_state(phase: Phase = Phase.PREPARATION, active: str = "white") -> GameState:
    """An EMPTY board with both Kings placed on their home squares."""
    state = GameState(
        game_id="effect-scenario",
        turn_number=1,
        active_player=active,
        phase=phase,
        board=BoardState(),
        players={"white": PlayerState("white"), "black": PlayerState("black")},
        rng_seed=42,
    )
    for pid in ("white", "black"):
        state.get_player(pid).building_pool = [
            BuildingPoolEntry(building_card_id="fortress", copies_available=2),
            BuildingPoolEntry(building_card_id="shrine", copies_available=1),
            BuildingPoolEntry(building_card_id="watchtower", copies_available=1),
        ]
    put(state, Position(4, 0), "white", "king")
    put(state, Position(4, 7), "black", "king")
    return state


def put(
    state: GameState,
    pos: Position,
    owner: str,
    piece_type: str,
    monster: str | None = None,
    registry=None,
    events: list | None = None,
) -> UnitInstance:
    """
    Place a piece, optionally hosting ``monster`` — summon-time effects are
    applied through the same ``apply_on_summon_effects`` the engine uses.
    """
    pt = _PT[piece_type]
    unit = UnitInstance(
        piece=ChessPiece(
            id=f"{owner}-{piece_type}-{pos.file}{pos.rank}", piece_type=pt, owner=owner
        )
    )
    state.board.place_unit(pos, unit)
    if monster is not None:
        unit.monster_id = monster
        if registry is not None:
            from game.mechanics.monsters import apply_on_summon_effects

            apply_on_summon_effects(
                state, unit, registry.get(monster),
                events if events is not None else [],
                rng=DeterministicRNG(seed=42), position=pos, registry=registry,
            )
    return unit


def place_trap(state: GameState, registry, card_id: str, pos: Position, owner: str) -> TrapInstance:
    card = registry.get(card_id)
    trap = TrapInstance(
        id=f"trap-{owner}-{card_id}",
        owner=owner,
        card_id=card_id,
        position=pos,
        radius=card.radius,
        trigger_condition=card.trigger.value,
        shape=card.shape,
        charges=card.charges,
    )
    state.traps.append(trap)
    return trap


def place_building(
    state: GameState,
    card_id: str,
    pos: Position,
    owner: str,
    status: ConstructionStatus = ConstructionStatus.COMPLETE,
    remaining_turns: int = 0,
    integrity: int | None = None,
) -> BuildingInstance:
    b = BuildingInstance(
        id=f"bld-{owner}-{card_id}-{pos.file}{pos.rank}",
        owner=owner,
        building_card_id=card_id,
        position=pos,
        status=status,
        remaining_turns=remaining_turns,
    )
    if integrity is not None:
        b.integrity = integrity
    state.buildings.append(b)
    from game.mechanics.buildings import refresh_building_blocks

    refresh_building_blocks(state)
    return b


def give_ritual(state: GameState, owner: str, ritual_id: str,
                revelation: RevelationState = RevelationState.SEALED) -> RitualState:
    rs = RitualState(ritual_id=ritual_id, revelation=revelation)
    state.get_player(owner).ritual_pool.append(rs)
    return rs


def crown(state: GameState, owner: str, king_card_id: str) -> None:
    ps = state.get_player(owner)
    ps.king_pool = [KingCardState(king_card_id=king_card_id, status=KingCardStatus.ACTIVE)]
    ps.active_king = king_card_id


def spell_reach(state: GameState, owner: str, target: tuple[int, int]) -> Position:
    """
    Satisfy the Stage 9 SPELL_TARGET_REACH gate for ``target``: park one of
    ``owner``'s Queens on the nearest empty square from which a Queen could
    slide onto it. Returns where the Queen ended up so a test can keep that
    square clear of its own pieces.
    """
    tf, tr = target
    for dist in (2, 3, 1, 4, 5, 6, 7):
        for df, dr in (
            (0, 1), (0, -1), (1, 0), (-1, 0),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        ):
            f, r = tf + df * dist, tr + dr * dist
            if not (0 <= f <= 7 and 0 <= r <= 7):
                continue
            cand = Position(f, r)
            if state.board.get_unit(cand) is not None:
                continue
            # Every intervening square must be empty for the ray to reach.
            blocked = False
            for step in range(1, dist):
                between = Position(tf + df * step, tr + dr * step)
                if state.board.get_unit(between) is not None:
                    blocked = True
                    break
            if blocked:
                continue
            put(state, cand, owner, "queen")
            return cand
    raise AssertionError(f"no square found from which {owner} could reach {target}")


def statuses_with(unit: UnitInstance, prefix: str) -> list[str]:
    return [s for s in unit.statuses if s.startswith(prefix)]


def square_effects(state: GameState, pos: Position) -> list[str]:
    return list(state.board.get_square(pos).temporary_effects)


def find_at(state: GameState, unit: UnitInstance) -> Position | None:
    for pos, u in state.board.all_units():
        if u is unit:
            return pos
    return None
