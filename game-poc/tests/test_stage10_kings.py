"""
test_stage10_kings.py — Stage 10: King Pool and Policies

Covers the King system (README §16-§20):

    • Random King Pool assignment at setup: one archetype chosen per
      player, that archetype's "home" King guaranteed a slot, 2 more
      random Kings filling the pool of 3 (all HIDDEN).
    • Coronation: the first activation is free.
    • Succession: escalating cost — 1st Succession costs a piece sacrifice
      OR a Building destruction (player's choice); 2nd costs BOTH. A
      retired King can never become active again. No Succession while in
      check.
    • The subset of King policy ``effects`` that are actually wired to a
      real system: vessel_support, territory_summon_bonus,
      construction_speed_bonus, building_territory_bonus,
      formation_support, graveyard_threshold_bonus, spell_radius_bonus,
      graveyard_recycle.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import KingCard, load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ChangeKing, CoronateKing, SummonMonster, StartConstruction
from game.core.phases import ConstructionStatus, KingCardStatus, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import (
    BuildingInstance,
    BuildingPoolEntry,
    GameState,
    KingCardState,
    PlayerState,
)
from game.mechanics.kings import (
    apply_construction_speed_bonus,
    apply_king_policy_auras,
    assign_random_king_pool,
    building_territory_radius_bonus,
    get_active_king_card,
    is_in_territory_with_king_bonus,
    king_extra_vessel_types,
    maybe_recycle_destroyed_monster,
)
from game.mechanics.territory import territory_squares

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng() -> DeterministicRNG:
    return DeterministicRNG(seed=42)


def _make_player(pid: str, active_king: str | None = None) -> PlayerState:
    ps = PlayerState(player_id=pid)
    ps.king_pool = [
        KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN),
        KingCardState(king_card_id=f"{pid}-king-b", status=KingCardStatus.HIDDEN),
        KingCardState(king_card_id=f"{pid}-king-c", status=KingCardStatus.HIDDEN),
    ]
    ps.building_pool = [BuildingPoolEntry(building_card_id="fortress", copies_available=1)]
    if active_king is not None:
        for kcs in ps.king_pool:
            if kcs.king_card_id == active_king:
                kcs.status = KingCardStatus.ACTIVE
        ps.active_king = active_king
    return ps


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _game_state(board: BoardState, white: PlayerState, black: PlayerState, active: str = "white") -> GameState:
    return GameState(
        game_id="test-stage10",
        turn_number=1,
        active_player=active,
        phase=Phase.PREPARATION,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# kings.yaml sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestKingsData:
    def test_six_kings_loaded(self, registry):
        kings = registry.all_kings()
        assert len(kings) == 6
        assert all(isinstance(k, KingCard) for k in kings)

    def test_duel_effect_is_a_single_entry_not_a_list(self, registry):
        card = registry.get("dragon_high_king")
        assert card.duel_effect is not None
        assert card.duel_effect.type == "monster_duel_support"


# ─────────────────────────────────────────────────────────────────────────────
# Random King Pool assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRandomKingPool:
    def test_pool_has_three_distinct_kings(self, registry, rng):
        ps = PlayerState(player_id="white")
        assign_random_king_pool(ps, registry, rng)
        ids = [k.king_card_id for k in ps.king_pool]
        assert len(ids) == 3
        assert len(set(ids)) == 3
        assert all(kcs.status == KingCardStatus.HIDDEN for kcs in ps.king_pool)

    def test_home_king_matches_assigned_archetype(self, registry, rng):
        ps = PlayerState(player_id="white")
        assign_random_king_pool(ps, registry, rng)
        assert ps.archetype is not None

        home_king_id = next(
            k.id for k in registry.all_kings()
            if k.archetype_support and k.archetype_support[0] == ps.archetype
        )
        ids = [k.king_card_id for k in ps.king_pool]
        assert home_king_id in ids

    def test_no_registry_leaves_pool_untouched(self, rng):
        ps = _make_player("white")
        original = list(ps.king_pool)
        assign_random_king_pool(ps, None, rng)
        assert ps.king_pool == original
        assert ps.archetype is None

    def test_deterministic_given_seed(self, registry):
        ps1 = PlayerState(player_id="white")
        assign_random_king_pool(ps1, registry, DeterministicRNG(seed=7))
        ps2 = PlayerState(player_id="white")
        assign_random_king_pool(ps2, registry, DeterministicRNG(seed=7))
        assert [k.king_card_id for k in ps1.king_pool] == [k.king_card_id for k in ps2.king_pool]
        assert ps1.archetype == ps2.archetype


# ─────────────────────────────────────────────────────────────────────────────
# Coronation
# ─────────────────────────────────────────────────────────────────────────────

class TestCoronation:
    def test_first_coronation_is_free(self, registry, rng):
        board = BoardState()
        white = _make_player("white")
        white.king_pool[0].king_card_id = "arcane_sovereign"
        black = _make_player("black")
        state = _game_state(board, white, black)

        engine = RulesEngine(registry=registry)
        engine.execute(state, CoronateKing(player_id="white", king_card_id="arcane_sovereign"), rng)

        assert white.active_king == "arcane_sovereign"
        assert white.king_pool[0].status == KingCardStatus.ACTIVE
        assert white.preparation_action_used


# ─────────────────────────────────────────────────────────────────────────────
# Succession cost
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessionCost:
    def _white_with_active_king(self) -> PlayerState:
        white = _make_player("white", active_king="white-king-a")
        white.king_pool[1].king_card_id = "white-king-b"
        white.king_pool[2].king_card_id = "white-king-c"
        return white

    def test_first_succession_requires_exactly_one_cost(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-a2")
        white = self._white_with_active_king()
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError, match="exactly one"):
            engine.execute(state, ChangeKing(player_id="white", king_card_id="white-king-b"), rng)

    def test_first_succession_by_piece_sacrifice(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-a2")
        white = self._white_with_active_king()
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ChangeKing(
            player_id="white", king_card_id="white-king-b",
            sacrifice_position=Position.from_algebraic("a2"),
        ), rng)

        assert white.active_king == "white-king-b"
        assert white.retired_kings == ["white-king-a"]
        assert board.get_unit(Position.from_algebraic("a2")) is None

    def test_first_succession_by_building_destruction(self, registry, rng):
        board = BoardState()
        white = self._white_with_active_king()
        black = _make_player("black")
        state = _game_state(board, white, black)
        bld = BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("e2"),
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        )
        state.buildings.append(bld)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ChangeKing(
            player_id="white", king_card_id="white-king-b", destroy_building_id="bld-1",
        ), rng)

        assert white.active_king == "white-king-b"
        assert bld.status == ConstructionStatus.DESTROYED

    def test_retired_king_can_never_be_chosen_again(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a2", "p1")
        _place(board, "white", PieceType.PAWN, "b2", "p2")
        white = self._white_with_active_king()
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ChangeKing(
            player_id="white", king_card_id="white-king-b",
            sacrifice_position=Position.from_algebraic("a2"),
        ), rng)
        white.reset_turn_flags()

        with pytest.raises(IllegalActionError):
            engine.execute(state, ChangeKing(
                player_id="white", king_card_id="white-king-a",
                sacrifice_position=Position.from_algebraic("b2"),
            ), rng)

    def test_second_succession_requires_both_costs(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a2", "p1")
        white = self._white_with_active_king()
        white.retired_kings = ["some-old-king"]  # pretend a first Succession already happened
        black = _make_player("black")
        state = _game_state(board, white, black)
        bld = BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("e2"),
            status=ConstructionStatus.COMPLETE, remaining_turns=0,
        )
        state.buildings.append(bld)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError, match="BOTH"):
            engine.execute(state, ChangeKing(
                player_id="white", king_card_id="white-king-b",
                sacrifice_position=Position.from_algebraic("a2"),
            ), rng)

        # Providing both succeeds.
        engine.execute(state, ChangeKing(
            player_id="white", king_card_id="white-king-b",
            sacrifice_position=Position.from_algebraic("a2"),
            destroy_building_id="bld-1",
        ), rng)
        assert white.active_king == "white-king-b"
        assert board.get_unit(Position.from_algebraic("a2")) is None
        assert bld.status == ConstructionStatus.DESTROYED

    def test_no_succession_while_in_check(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a2", "p1")
        white = self._white_with_active_king()
        white.set_check(True)
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError, match="check"):
            engine.execute(state, ChangeKing(
                player_id="white", king_card_id="white-king-b",
                sacrifice_position=Position.from_algebraic("a2"),
            ), rng)

    def test_legal_actions_offer_change_king_after_coronation(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "white-king")
        _place(board, "white", PieceType.PAWN, "a2", "p1")
        white = self._white_with_active_king()
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        actions = engine.get_legal_actions(state, "white", registry=registry)
        change_king_actions = [a for a in actions if isinstance(a, ChangeKing)]
        assert change_king_actions  # at least one is offered
        assert all(a.king_card_id != "white-king-a" for a in change_king_actions)  # active king excluded


# ─────────────────────────────────────────────────────────────────────────────
# vessel_support (dragon_high_king) — extends Vessel compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestVesselSupportPolicy:
    def test_dragon_king_allows_pawn_vessel_for_dragon_monster(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="dragon_high_king")
        white.hand = ["wyrm_knight"]  # dragon, supported_vessels = [knight, rook] — no pawn
        black = _make_player("black")
        state = _game_state(board, white, black)

        assert "pawn" not in registry.get("wyrm_knight").supported_vessels
        assert "pawn" in king_extra_vessel_types(state, "white", "dragon", registry)

    def test_no_bonus_for_wrong_archetype(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="dragon_high_king")
        black = _make_player("black")
        state = _game_state(board, white, black)
        assert king_extra_vessel_types(state, "white", "warrior", registry) == set()

    def test_summon_uses_king_extra_vessel(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a3", "p1")  # inside white home Territory
        white = _make_player("white", active_king="dragon_high_king")
        white.hand = ["wyrm_knight"]
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="wyrm_knight", vessel_position=Position.from_algebraic("a3"),
        ), rng)
        assert board.get_unit(Position.from_algebraic("a3")).monster_id == "wyrm_knight"


# ─────────────────────────────────────────────────────────────────────────────
# territory_summon_bonus (dragon_high_king) — extends the Territory boundary
# ─────────────────────────────────────────────────────────────────────────────

class TestTerritorySummonBonus:
    def test_dragon_can_summon_one_square_past_territory(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="dragon_high_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        just_outside = Position.from_algebraic("e4")  # rank index 3, one past white's home (0-2)
        assert just_outside not in territory_squares(state, "white", registry)
        assert is_in_territory_with_king_bonus(state, just_outside, "white", "dragon", registry)

    def test_non_dragon_archetype_gets_no_bonus(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="dragon_high_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        just_outside = Position.from_algebraic("e4")
        assert not is_in_territory_with_king_bonus(state, just_outside, "white", "warrior", registry)

    def test_too_far_outside_still_fails(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="dragon_high_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        too_far = Position.from_algebraic("e5")  # rank index 4, two past home
        assert not is_in_territory_with_king_bonus(state, too_far, "white", "dragon", registry)


# ─────────────────────────────────────────────────────────────────────────────
# construction_speed_bonus / building_territory_bonus (architect_king)
# ─────────────────────────────────────────────────────────────────────────────

class TestArchitectKingPolicy:
    def test_pawn_builder_construction_is_faster(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "a1", "p1")
        white = _make_player("white", active_king="architect_king")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        base_turns = registry.get("fortress").construction_turns
        engine.execute(state, StartConstruction(
            player_id="white", pawn_position=Position.from_algebraic("a1"), building_card_id="fortress",
        ), rng)

        bld = state.buildings[0]
        assert bld.remaining_turns == max(base_turns - 1, 1)

    def test_bonus_clamped_to_minimum_turns(self, registry, rng):
        assert apply_construction_speed_bonus(
            _game_state(BoardState(), _make_player("white", active_king="architect_king"), _make_player("black")),
            "white", "pawn", base_turns=1, registry=registry,
        ) == 1  # minimum_turns clamps the reduction

    def test_building_territory_radius_extended(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="architect_king")
        black = _make_player("black")
        state = _game_state(board, white, black)
        assert building_territory_radius_bonus(state, "white", registry) == 1

        no_king = _make_player("white")
        state2 = _game_state(BoardState(), no_king, _make_player("black"))
        assert building_territory_radius_bonus(state2, "white", registry) == 0


# ─────────────────────────────────────────────────────────────────────────────
# formation_support (marshal_king) — start-of-turn shield aura
# ─────────────────────────────────────────────────────────────────────────────

class TestFormationSupportPolicy:
    def test_warrior_with_adjacent_ally_gets_shield(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.KNIGHT, "d4", "u1")
        u1.monster_id = "blade_dancer"  # warrior
        _place(board, "white", PieceType.PAWN, "d5", "u2")  # adjacent ally
        white = _make_player("white", active_king="marshal_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert any(s.startswith("shield:") for s in u1.statuses)

    def test_warrior_without_adjacent_ally_gets_nothing(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.KNIGHT, "d4", "u1")
        u1.monster_id = "blade_dancer"
        white = _make_player("white", active_king="marshal_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert not any(s.startswith("shield:") for s in u1.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# graveyard_threshold_bonus / graveyard_recycle (grave_crowned_king)
# ─────────────────────────────────────────────────────────────────────────────

class TestGraveCrownedKingPolicy:
    def test_shield_granted_once_graveyard_reaches_threshold(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.PAWN, "d4", "u1")
        u1.monster_id = "grave_scholar"  # necromancer
        white = _make_player("white", active_king="grave_crowned_king")
        white.graveyard = ["a", "b", "c", "d", "e"]  # threshold = 5
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert any(s.startswith("shield:") for s in u1.statuses)

    def test_no_shield_below_threshold(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.PAWN, "d4", "u1")
        u1.monster_id = "grave_scholar"
        white = _make_player("white", active_king="grave_crowned_king")
        white.graveyard = ["a"]
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert not any(s.startswith("shield:") for s in u1.statuses)

    def test_first_destroyed_monster_each_turn_recycled_to_deck(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="grave_crowned_king")
        white.deck = ["existing"]
        black = _make_player("black")
        state = _game_state(board, white, black)
        events = []

        fired = maybe_recycle_destroyed_monster(state, "white", "grave_scholar", events, registry)
        assert fired
        assert white.deck == ["existing", "grave_scholar"]
        assert white.king_recycle_used_this_turn

        # Second destruction this turn does NOT recycle.
        events2 = []
        fired2 = maybe_recycle_destroyed_monster(state, "white", "bone_collector", events2, registry)
        assert not fired2
        assert white.deck == ["existing", "grave_scholar"]

    def test_recycle_resets_next_turn(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="grave_crowned_king")
        black = _make_player("black")
        state = _game_state(board, white, black)

        maybe_recycle_destroyed_monster(state, "white", "grave_scholar", [], registry)
        assert white.king_recycle_used_this_turn
        white.reset_turn_flags()
        assert not white.king_recycle_used_this_turn


# ─────────────────────────────────────────────────────────────────────────────
# spell_radius_bonus (arcane_sovereign) — King-level status aura
# ─────────────────────────────────────────────────────────────────────────────

class TestArcaneSovereignPolicy:
    def test_spellcaster_gets_spell_radius_bonus_status(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.BISHOP, "c1", "u1")
        u1.monster_id = "dark_magician"  # spellcaster
        white = _make_player("white", active_king="arcane_sovereign")
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert any(s.startswith("spell_radius_bonus:") for s in u1.statuses)

    def test_non_spellcaster_unaffected(self, registry):
        board = BoardState()
        u1 = _place(board, "white", PieceType.KNIGHT, "d4", "u1")
        u1.monster_id = "blade_dancer"  # warrior
        white = _make_player("white", active_king="arcane_sovereign")
        black = _make_player("black")
        state = _game_state(board, white, black)

        apply_king_policy_auras(state, "white", registry)
        assert not any(s.startswith("spell_radius_bonus:") for s in u1.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# get_active_king_card
# ─────────────────────────────────────────────────────────────────────────────

class TestGetActiveKingCard:
    def test_none_when_no_active_king(self, registry):
        board = BoardState()
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        assert get_active_king_card(state, "white", registry) is None

    def test_returns_card_when_active(self, registry):
        board = BoardState()
        white = _make_player("white", active_king="marshal_king")
        black = _make_player("black")
        state = _game_state(board, white, black)
        card = get_active_king_card(state, "white", registry)
        assert card is not None
        assert card.id == "marshal_king"
