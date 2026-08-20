"""
test_stage9_territory.py — Stage 9: Territory

Covers the Territory system (README §13, refined by the user's design
brief):

    • Base Territory = each side's home three ranks.
    • Building Territory = a radius around every COMPLETE Building, scaled
      by size (small=1, medium=2, major=3 — "increase size of radius over
      size of building").
    • Territory (home + Building) is per-player, not exclusive — squares
      can be contested.
    • SummonMonster — VESSEL_TERRITORY: a vessel must stand in the
      caster's own Territory (home ranks count here). Hard requirement,
      no alternative.
    • PlaceTrap / ActivateSpell(position/zone) — a square qualifies EITHER
      via the pre-existing TRAP_MONSTER_ANCHOR / SPELL_TARGET_REACH rule,
      OR via Building coverage specifically — NOT plain Territory. The
      bare home three ranks do NOT make a square Trap/Spell-placeable on
      their own (user correction: "not available for traps by default").
      A square only needs to satisfy ONE of the two.
    • Watchtower / Shrine push their own Trap-placement / Spell-targeting
      coverage further via trap_territory_bonus / spell_territory_bonus.
    • Observation exposes both players' (home + Building) Territory
      (always public) — used for SummonMonster, not for Trap/Spell zones.
    • RandomBot never attempts an out-of-territory Summon, or a
      Trap/Spell placement satisfying neither condition, because
      get_legal_actions never offers one.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ActivateSpell, PlaceTrap, StartConstruction, SummonMonster
from game.core.game import Game
from game.core.phases import ConstructionStatus, KingCardStatus, Phase, PieceType
from game.core.rules import IllegalActionError, RulesEngine
from game.core.rng import DeterministicRNG
from game.core.state import BuildingInstance, BuildingPoolEntry, GameState, KingCardState, PlayerState, TrapInstance
from game.mechanics.territory import (
    HOME_RANKS,
    is_home_territory,
    is_in_spell_zone,
    is_in_territory,
    is_in_trap_zone,
    spell_zone_squares,
    territory_squares,
    trap_zone_squares,
)

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
        game_id="test-stage9",
        turn_number=1,
        active_player=active,
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


def _complete_building(owner: str, building_card_id: str, alg: str, bid: str = "bld-1") -> BuildingInstance:
    return BuildingInstance(
        id=bid, owner=owner, building_card_id=building_card_id,
        position=Position.from_algebraic(alg),
        status=ConstructionStatus.COMPLETE, remaining_turns=0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Base (home-rank) territory
# ─────────────────────────────────────────────────────────────────────────────

class TestHomeTerritory:
    def test_home_ranks(self):
        assert HOME_RANKS["white"] == (0, 1, 2)
        assert HOME_RANKS["black"] == (5, 6, 7)

    def test_is_home_territory(self):
        assert is_home_territory(Position.from_algebraic("a1"), "white")
        assert is_home_territory(Position.from_algebraic("h3"), "white")
        assert not is_home_territory(Position.from_algebraic("d4"), "white")
        assert not is_home_territory(Position.from_algebraic("d5"), "white")
        assert is_home_territory(Position.from_algebraic("a8"), "black")
        assert is_home_territory(Position.from_algebraic("h6"), "black")
        assert not is_home_territory(Position.from_algebraic("d5"), "black")

    def test_no_buildings_territory_is_just_home_ranks(self):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        squares = territory_squares(state, "white", registry=None)
        assert len(squares) == 24  # 8 files x 3 ranks
        assert all(pos.rank in (0, 1, 2) for pos in squares)


# ─────────────────────────────────────────────────────────────────────────────
# Building territory, scaled by size
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildingTerritory:
    def test_radius_scales_with_size(self, registry):
        assert registry.get("shrine").territory_radius == 1       # small
        assert registry.get("watchtower").territory_radius == 1   # small
        assert registry.get("fortress").territory_radius == 2     # medium

    def test_complete_building_extends_territory(self, registry):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        state.buildings.append(_complete_building("white", "fortress", "e5"))

        # fortress territory_radius=2 -> a 5x5 block centred on e5.
        assert is_in_territory(state, Position.from_algebraic("e5"), "white", registry)
        assert is_in_territory(state, Position.from_algebraic("g5"), "white", registry)   # +2 files
        assert not is_in_territory(state, Position.from_algebraic("h5"), "white", registry)  # +3, out of range

    def test_under_construction_building_does_not_extend_territory(self, registry):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("e5"),
            status=ConstructionStatus.UNDER_CONSTRUCTION, remaining_turns=2,
        ))
        assert not is_in_territory(state, Position.from_algebraic("e5"), "white", registry)

    def test_destroyed_building_does_not_extend_territory(self, registry):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("e5"),
            status=ConstructionStatus.DESTROYED, remaining_turns=0,
        ))
        assert not is_in_territory(state, Position.from_algebraic("e5"), "white", registry)

    def test_no_registry_falls_back_to_home_ranks_only(self):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        state.buildings.append(_complete_building("white", "fortress", "e5"))
        assert not is_in_territory(state, Position.from_algebraic("e5"), "white", registry=None)

    def test_territory_is_not_exclusive_can_be_contested(self, registry):
        board = BoardState()
        state = _game_state(board, _make_player("white"), _make_player("black"))
        state.buildings.append(_complete_building("white", "fortress", "d4", bid="bld-w"))
        state.buildings.append(_complete_building("black", "fortress", "e5", bid="bld-b"))
        # d4 (fortress, radius 2) and e5 (fortress, radius 2) are 1 square
        # apart — plenty of overlap in the middle.
        contested = Position.from_algebraic("e4")
        assert is_in_territory(state, contested, "white", registry)
        assert is_in_territory(state, contested, "black", registry)


# ─────────────────────────────────────────────────────────────────────────────
# VESSEL_TERRITORY — SummonMonster
# ─────────────────────────────────────────────────────────────────────────────

class TestVesselTerritory:
    def test_summon_outside_territory_is_illegal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.BISHOP, "d4", "wb")  # rank 4 — outside territory
        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError, match="Territory"):
            engine.execute(state, SummonMonster(
                player_id="white", card_id="dark_magician",
                vessel_position=Position.from_algebraic("d4"),
            ), rng)

    def test_summon_in_home_territory_is_legal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.BISHOP, "c1", "wb")
        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="dark_magician",
            vessel_position=Position.from_algebraic("c1"),
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("c1")).monster_id == "dark_magician"

    def test_summon_in_building_extended_territory_is_legal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.BISHOP, "e5", "wb")
        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "fortress", "e5"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="dark_magician",
            vessel_position=Position.from_algebraic("e5"),
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("e5")).monster_id == "dark_magician"

    def test_legal_actions_exclude_out_of_territory_vessels(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "white", PieceType.BISHOP, "c1", "wb")   # in territory
        _place(board, "white", PieceType.KNIGHT, "d4", "wn")   # out of territory
        white = _make_player("white", hand=["dark_magician"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        vessels = {
            a.vessel_position for a in legal
            if isinstance(a, SummonMonster) and a.card_id == "dark_magician"
        }
        assert Position.from_algebraic("c1") in vessels
        assert Position.from_algebraic("d4") not in vessels


# ─────────────────────────────────────────────────────────────────────────────
# Trap placement — TRAP_MONSTER_ANCHOR OR Territory
# ─────────────────────────────────────────────────────────────────────────────

class TestTrapZone:
    def test_empty_square_in_bare_home_territory_is_still_illegal(self, registry, rng):
        """
        User correction: bare home-rank Territory does NOT, by itself,
        make a square Trap-placeable — only Building coverage (or hosting
        your own Monster) does.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        # a3 is empty and inside white's home Territory, but there is no
        # Building anywhere on the board, and it hosts no Monster.
        with pytest.raises(IllegalActionError):
            engine.execute(state, PlaceTrap(
                player_id="white", card_id="pit_trap", position=Position.from_algebraic("a3"),
            ), rng)

    def test_empty_square_covered_by_own_building_is_legal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "fortress", "a3"))
        engine = RulesEngine(registry=registry)

        # a3 itself: the Fortress's own square is always within its radius.
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("a3"),
        ), rng)
        assert len(state.traps) == 1

    def test_empty_square_outside_territory_still_illegal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, PlaceTrap(
                player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
            ), rng)

    def test_own_monster_square_outside_territory_still_legal(self, registry, rng):
        """Neither condition needs the OTHER — Monster-anchor alone still works."""
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        wn = _place(board, "white", PieceType.KNIGHT, "d4", "wn")  # outside Territory
        wn.monster_id = "dark_magician"
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=Position.from_algebraic("d4"),
        ), rng)
        assert len(state.traps) == 1

    def test_watchtower_extends_valid_trap_placement_zone(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "watchtower", "e4"))
        engine = RulesEngine(registry=registry)

        # e4 itself: watchtower territory_radius=1 + trap_territory_bonus=2 -> reach 3.
        # e7 is 3 ranks from e4 — inside the Watchtower's Trap zone, but
        # nowhere near home Territory and not hosting any Monster.
        far = Position.from_algebraic("e7")
        assert is_in_trap_zone(state, far, "white", registry)
        engine.execute(state, PlaceTrap(
            player_id="white", card_id="pit_trap", position=far,
        ), rng)
        assert len(state.traps) == 1

    def test_legal_actions_include_both_kinds_of_square(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        wn = _place(board, "white", PieceType.KNIGHT, "d5", "wn")  # outside any Territory/Building
        wn.monster_id = "dark_magician"
        white = _make_player("white", hand=["pit_trap"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "fortress", "a3"))
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        positions = {a.position for a in legal if isinstance(a, PlaceTrap)}
        assert Position.from_algebraic("d5") in positions   # monster-anchor
        assert Position.from_algebraic("a3") in positions   # Building coverage (Fortress itself)
        assert Position.from_algebraic("h1") not in positions  # bare home rank, out of Fortress radius, no Monster
        assert Position.from_algebraic("d4") not in positions  # neither


# ─────────────────────────────────────────────────────────────────────────────
# Spell targeting — SPELL_TARGET_REACH OR Territory
# ─────────────────────────────────────────────────────────────────────────────

class TestSpellZone:
    def test_position_target_in_bare_home_territory_is_still_illegal(self, registry, rng):
        """
        User correction: bare home-rank Territory does NOT, by itself,
        make a square Spell-targetable — only Building coverage (or
        non-Pawn piece reach) does.
        """
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        # h3 is inside white's home Territory but no piece could reach it
        # (only the King exists, which doesn't count for SPELL_TARGET_REACH),
        # and there is no Building anywhere on the board to cover it.
        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateSpell(
                player_id="white", card_id="cursed_ground", target=(7, 2),
            ), rng)

    def test_position_target_covered_by_own_building_is_legal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "shrine", "h3"))
        engine = RulesEngine(registry=registry)

        # h3 is unreachable by any piece, but the Shrine standing there
        # covers its own square.
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="cursed_ground", target=(7, 2),
        ), rng)

    def test_shrine_extends_spell_zone_beyond_reach_and_home(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "a1", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        white = _make_player("white", hand=["cursed_ground"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings.append(_complete_building("white", "shrine", "e4"))
        engine = RulesEngine(registry=registry)

        # shrine territory_radius=1 + spell_territory_bonus=2 -> reach 3.
        far = Position.from_algebraic("e7")
        assert is_in_spell_zone(state, far, "white", registry)
        engine.execute(state, ActivateSpell(
            player_id="white", card_id="cursed_ground", target=(4, 6),
        ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# Observation exposure
# ─────────────────────────────────────────────────────────────────────────────

class TestTerritoryObservation:
    def test_observation_exposes_both_territories(self, registry):
        game = Game.new(seed=1, registry=registry)
        obs = game.get_observation("white")
        assert len(obs.own_territory) == 24
        assert len(obs.opponent_territory) == 24
        assert Position.from_algebraic("a1") in obs.own_territory
        assert Position.from_algebraic("a8") in obs.opponent_territory


# ─────────────────────────────────────────────────────────────────────────────
# AI (RandomBot) never attempts an out-of-territory action
# ─────────────────────────────────────────────────────────────────────────────

class TestAIRespectsTerritory:
    def test_random_bot_never_summons_outside_territory(self, registry):
        from game.ai.random_bot import RandomBot

        game = Game.new(seed=7, registry=registry)
        bot = RandomBot(seed=3)
        bot.player_id = "white"

        for _ in range(60):
            if game.is_over():
                break
            game.advance_to_preparation()
            active = game.state.active_player
            if game.state.phase == Phase.FINAL_DUEL:
                game.resolve_final_duel_immediately()
                continue
            legal = game.get_legal_actions(active)
            if not legal:
                break
            # Every offered SummonMonster must already be in-territory —
            # the invariant this test actually cares about.
            own_territory = territory_squares(game.state, active, registry)
            for a in legal:
                if isinstance(a, SummonMonster):
                    assert a.vessel_position in own_territory
            controller = bot if active == "white" else RandomBot(seed=99)
            controller.player_id = active
            action = controller.choose_action(game.get_observation(active), legal)
            game.execute(action)
