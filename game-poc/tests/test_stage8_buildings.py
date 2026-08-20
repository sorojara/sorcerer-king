"""
test_stage8_buildings.py — Stage 8: Pawns and Buildings

Covers the Building system (README §10–§12):

    • BuildingCard / CardType.BUILDING loaded from data/buildings.yaml,
      point cost derived from ``size`` (README §12.1).
    • Game.new() gives both players a default, public Building Pool
      (budget 6, matching README §12.1's illustrative example).
    • StartConstruction — Pawn-only, once-per-match Builder token,
      construction_turns/radius read from the Building's card data,
      Building Pool copies consumed, the square marked immediately.
    • Commitment (README §12.2 step 3): a Pawn mid-construction cannot
      move or be transformed, in both get_legal_actions and execute().
    • Disruption (README §12.2 step 4): capturing a committed Builder
      Pawn destroys its half-built Building; the spent Pool copy is not
      refunded.
    • Completion (README §12.2 step 5 / §12.3): construction ticks down
      on the owner's own end-of-turn; at 0 the Building becomes COMPLETE
      and the Pawn's once-per-match Builder token is spent.
    • Building influence (README §11): Fortress's capture-protection
      aura, Shrine's spell_radius_bonus aura, Watchtower's Trap-radius
      extension.
    • Observation exposes both players' Building Pools (always public).
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import (
    BUILDING_POINT_COST,
    BuildingCard,
    BuildingSize,
    CardType,
    load_registry_from_yaml,
)
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import EndPreparation, EndTurn, MovePiece, StartConstruction, SummonMonster
from game.core.events import BuildingCompleted, BuildingDestroyed, ConstructionStarted
from game.core.game import Game
from game.core.phases import ConstructionStatus, KingCardStatus, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import BuildingPoolEntry, GameState, KingCardState, PlayerState

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


def _make_player(pid: str, hand: list[str] | None = None, pool: list[BuildingPoolEntry] | None = None) -> PlayerState:
    ps = PlayerState(player_id=pid)
    ps.hand = hand or []
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    ps.building_pool = pool if pool is not None else [
        BuildingPoolEntry(building_card_id="fortress", copies_available=1),
        BuildingPoolEntry(building_card_id="shrine", copies_available=1),
        BuildingPoolEntry(building_card_id="watchtower", copies_available=1),
    ]
    return ps


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _game_state(board: BoardState, white: PlayerState, black: PlayerState, phase: Phase = Phase.PREPARATION, active: str = "white") -> GameState:
    return GameState(
        game_id="test-stage8",
        turn_number=1,
        active_player=active,
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Card data
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildingCardData:
    def test_registry_loads_three_starter_buildings(self, registry):
        buildings = {c.id: c for c in registry.all_buildings()}
        assert set(buildings) == {"fortress", "shrine", "watchtower"}
        for card in buildings.values():
            assert isinstance(card, BuildingCard)
            assert card.card_type == CardType.BUILDING

    def test_cost_derived_from_size(self, registry):
        fortress = registry.get("fortress")
        shrine = registry.get("shrine")
        watchtower = registry.get("watchtower")
        assert fortress.size == BuildingSize.MEDIUM
        assert fortress.cost == BUILDING_POINT_COST[BuildingSize.MEDIUM] == 2
        assert shrine.size == BuildingSize.SMALL
        assert shrine.cost == 1
        assert watchtower.size == BuildingSize.SMALL
        assert watchtower.cost == 1

    def test_buildings_not_sampled_into_a_deck(self, registry):
        # Building ids must never leak into Main Deck sampling pools.
        deck_ids = {c.id for c in registry.all_monsters()} \
            | {c.id for c in registry.all_spells()} \
            | {c.id for c in registry.all_traps()}
        assert "fortress" not in deck_ids
        assert "shrine" not in deck_ids
        assert "watchtower" not in deck_ids


class TestDefaultBuildingPool:
    def test_game_new_gives_both_players_a_public_pool(self, registry):
        game = Game.new(seed=1, registry=registry)
        white_pool = game.state.get_player("white").building_pool
        black_pool = game.state.get_player("black").building_pool
        assert white_pool and black_pool
        budget = sum(
            registry.get(e.building_card_id).cost * e.copies_available
            for e in white_pool
        )
        assert budget == 6  # README §12.1 illustrative example

    def test_observation_exposes_both_pools(self, registry):
        game = Game.new(seed=1, registry=registry)
        obs = game.get_observation("white")
        assert len(obs.own_building_pool) > 0
        assert len(obs.opponent_building_pool) > 0


# ─────────────────────────────────────────────────────────────────────────────
# StartConstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestStartConstruction:
    def test_reads_construction_turns_and_consumes_pool_copy(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="fortress",
        ), rng)

        assert any(isinstance(e, ConstructionStarted) for e in events)
        assert len(state.buildings) == 1
        b = state.buildings[0]
        assert b.status == ConstructionStatus.UNDER_CONSTRUCTION
        assert b.remaining_turns == registry.get("fortress").construction_turns == 3
        assert state.board.get_square(Position.from_algebraic("a2")).building_id == b.id

        entry = next(e for e in white.building_pool if e.building_card_id == "fortress")
        assert entry.copies_available == 0

    def test_pool_exhausted_is_illegal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white", pool=[BuildingPoolEntry(building_card_id="fortress", copies_available=0)])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError, match="not available"):
            engine.execute(state, StartConstruction(
                player_id="white", pawn_position=Position.from_algebraic("a2"),
                building_card_id="fortress",
            ), rng)

    def test_cannot_build_twice_on_same_square(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white", pool=[
            BuildingPoolEntry(building_card_id="fortress", copies_available=1),
            BuildingPoolEntry(building_card_id="shrine", copies_available=1),
        ])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        # Note: the Pawn is committed after the first StartConstruction, so
        # the second attempt is rejected for TWO independent reasons — this
        # test only needs one of them to fire.
        engine.execute(state, StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="fortress",
        ), rng)
        with pytest.raises(IllegalActionError):
            engine.execute(state, StartConstruction(
                player_id="white", pawn_position=Position.from_algebraic("a2"),
                building_card_id="shrine",
            ), rng)

    def test_start_construction_appears_in_legal_actions(self, registry):
        game = Game.new(seed=1, registry=registry, white_deck=[], black_deck=[])
        legal = game.get_legal_actions("white")
        matches = [a for a in legal if isinstance(a, StartConstruction)]
        assert matches, "expected at least one StartConstruction in legal actions"
        # Every Pawn on the board × every Pool entry with copies remaining.
        pawn_positions = {a.pawn_position for a in matches}
        assert Position.from_algebraic("a2") in pawn_positions


# ─────────────────────────────────────────────────────────────────────────────
# Commitment — a Pawn under construction cannot move or transform
# ─────────────────────────────────────────────────────────────────────────────

class TestCommitment:
    def _committed_state(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)
        engine.execute(state, StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="fortress",
        ), rng)
        engine.execute(state, EndPreparation(player_id="white"), rng)
        return state, engine

    def test_committed_pawn_cannot_move(self, registry, rng):
        state, engine = self._committed_state(registry, rng)
        with pytest.raises(IllegalActionError, match="committed"):
            engine.execute(state, MovePiece(
                player_id="white",
                source=Position.from_algebraic("a2"),
                target=Position.from_algebraic("a3"),
            ), rng)

    def test_committed_pawn_excluded_from_legal_moves(self, registry, rng):
        state, engine = self._committed_state(registry, rng)
        legal = engine.get_legal_actions(state, "white", registry=registry)
        sources = {a.source for a in legal if isinstance(a, MovePiece)}
        assert Position.from_algebraic("a2") not in sources

    def test_committed_pawn_cannot_be_summon_vessel(self, registry, rng):
        # ritual_acolyte supports the pawn vessel class in the base PoC set.
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white", hand=["ritual_acolyte"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        game = Game(state=state, rng=rng, registry=registry)

        game.execute(StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="shrine",  # construction_turns = 2, still committed after 1 turn
        ))
        game.execute(EndPreparation(player_id="white"))
        game.execute(EndTurn(player_id="white"))          # legal from CHESS with no move made
        game.execute(EndPreparation(player_id="black"))
        game.execute(EndTurn(player_id="black"))
        # White's fresh PREPARATION — the prep-action flag is reset, but the
        # Pawn is still committed (remaining_turns went 2 -> 1, not 0 yet).

        with pytest.raises(IllegalActionError, match="committed"):
            game.execute(SummonMonster(
                player_id="white", card_id="ritual_acolyte",
                vessel_position=Position.from_algebraic("a2"),
            ))


# ─────────────────────────────────────────────────────────────────────────────
# Disruption — capturing the committed Pawn cancels construction
# ─────────────────────────────────────────────────────────────────────────────

class TestDisruption:
    def test_capturing_builder_pawn_destroys_the_building(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        _place(board, "black", PieceType.KNIGHT, "b4", "bn")  # can hop to a2
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        game = Game(state=state, rng=rng, registry=registry)

        game.execute(StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="fortress",
        ))
        building_id = state.buildings[0].id
        entry = next(e for e in white.building_pool if e.building_card_id == "fortress")
        assert entry.copies_available == 0

        game.execute(EndPreparation(player_id="white"))
        game.execute(EndTurn(player_id="white"))          # legal from CHESS with no move made
        game.execute(EndPreparation(player_id="black"))

        result = game.execute(MovePiece(
            player_id="black", source=Position.from_algebraic("b4"), target=Position.from_algebraic("a2"),
        ))
        events = result.events

        assert any(isinstance(e, BuildingDestroyed) and e.building_instance_id == building_id for e in events)
        b = next(bb for bb in state.buildings if bb.id == building_id)
        assert b.status == ConstructionStatus.DESTROYED
        assert state.board.get_square(Position.from_algebraic("a2")).building_id is None
        # The spent Building Pool copy is a permanent loss — not refunded.
        assert entry.copies_available == 0


# ─────────────────────────────────────────────────────────────────────────────
# Completion — ticks down on the owner's own end-of-turn
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletion:
    def test_building_completes_after_construction_turns(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        _place(board, "white", PieceType.PAWN, "a2", "wp")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        game = Game(state=state, rng=rng, registry=registry)

        game.execute(StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a2"),
            building_card_id="shrine",  # construction_turns = 2
        ))
        game.execute(EndPreparation(player_id="white"))

        building = state.buildings[0]
        assert building.remaining_turns == 2

        # First white EndTurn (no chess move needed): 2 -> 1, still building.
        game.execute(EndTurn(player_id="white"))
        assert building.status == ConstructionStatus.UNDER_CONSTRUCTION
        assert building.remaining_turns == 1

        game.execute(EndPreparation(player_id="black"))
        game.execute(EndTurn(player_id="black"))
        game.execute(EndPreparation(player_id="white"))
        result = game.execute(EndTurn(player_id="white"))

        assert building.status == ConstructionStatus.COMPLETE
        assert any(isinstance(e, BuildingCompleted) for e in result.events)
        # Builder token spent (README §12.3).
        pawn_unit = state.board.get_unit(Position.from_algebraic("a2"))
        assert pawn_unit.builder_available is False


# ─────────────────────────────────────────────────────────────────────────────
# Building influence — Fortress / Shrine auras, Watchtower Trap radius
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildingInfluence:
    def test_fortress_shields_nearby_monster_at_start_of_turn(self, registry):
        from game.mechanics.buildings import apply_building_auras
        from game.core.state import BuildingInstance

        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        vessel = _place(board, "white", PieceType.BISHOP, "c3", "wb")
        vessel.monster_id = "dark_magician"
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("a1"),
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        ))

        apply_building_auras(state, "white", registry)

        assert any(s.startswith("shield:") for s in vessel.statuses)

    def test_shrine_grants_spell_radius_flag(self, registry):
        from game.mechanics.buildings import apply_building_auras
        from game.core.state import BuildingInstance

        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "e8", "bk")
        vessel = _place(board, "white", PieceType.BISHOP, "c3", "wb")
        vessel.monster_id = "dark_magician"
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="shrine",
            position=Position.from_algebraic("a1"),
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        ))

        apply_building_auras(state, "white", registry)

        assert any(s.startswith("spell_radius_bonus:") for s in vessel.statuses)

    def test_watchtower_extends_owned_trap_radius(self, registry):
        from game.mechanics.buildings import trap_radius_bonus
        from game.core.state import BuildingInstance, TrapInstance

        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        trap = TrapInstance(
            id="trap-1", owner="white", card_id="pit_trap",
            position=Position.from_algebraic("d4"), radius=1,
            trigger_condition="enter_radius",
        )
        state.traps.append(trap)
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="watchtower",
            position=Position.from_algebraic("d5"),  # within watchtower's radius=3 of d4
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        ))

        assert trap_radius_bonus(state, trap, registry) == 1

    def test_watchtower_bonus_only_for_owner(self, registry):
        from game.mechanics.buildings import trap_radius_bonus
        from game.core.state import BuildingInstance, TrapInstance

        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        trap = TrapInstance(
            id="trap-1", owner="black", card_id="pit_trap",
            position=Position.from_algebraic("d4"), radius=1,
            trigger_condition="enter_radius",
        )
        state.traps.append(trap)
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="watchtower",
            position=Position.from_algebraic("d5"),
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        ))

        assert trap_radius_bonus(state, trap, registry) == 0
