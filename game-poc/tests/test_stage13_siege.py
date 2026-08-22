"""
test_stage13_siege.py — Stage 13: Buildings under siege

Covers the Building durability / attack contest introduced in Stage 13,
and the rule that gives Ritual Monsters their strategic identity:

    • A COMPLETE enemy Building is a WALL — its square can't be entered
      or slid through, and it blocks a Pawn's double push behind it.
      Its own owner is never blocked by it.
    • sky_serpent's ``ignore_terrain`` slides THROUGH such a square but
      still can't land on it.
    • AttackBuilding is a chess-phase action that spends the chess move
      and never relocates the attacker.
    • Only a Ritual Monster, a Monster carrying ``building_damage_bonus``,
      or ANY unit against a Building marked by siege_order's
      ``building_vulnerability`` may attack.
    • The defensive stack, in order: castle_keeper's
      ``building_capture_protection`` → siege_order's extra damage →
      siege_captain / titan ``building_aura`` durability →
      emergency_fortifications' ``temporary_building_protection``.
    • obsidian_dragon / sovereign_of_embers' ``destroy_on_capture``
      flattens a Building in one blow.
    • demolition_charge's ``building_damage`` damages adjacent enemy
      Buildings through the same stack.
    • royal_engineer / worldforge_colossus' ``repair_building`` heals at
      the owner's end of turn, capped at max_integrity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from game.cards.card import BUILDING_BASE_INTEGRITY, BuildingSize, load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.movement import get_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import AttackBuilding, EndTurn, MovePiece
from game.core.events import (
    BuildingAttackBlocked,
    BuildingAttacked,
    BuildingDestroyed,
    BuildingRepaired,
    BuildingVulnerable,
)
from game.core.phases import ConstructionStatus, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import BuildingInstance, GameState, PlayerState
from game.mechanics.buildings import (
    can_attack_building,
    damage_building,
    refresh_building_blocks,
)

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _state() -> GameState:
    return GameState(
        game_id="siege-test",
        turn_number=1,
        active_player="white",
        phase=Phase.CHESS,
        board=BoardState(),
        players={"white": PlayerState("white"), "black": PlayerState("black")},
    )


def _put(state: GameState, pos: Position, owner: str, pt: PieceType,
         monster_id: str | None = None, registry=None) -> UnitInstance:
    """Place a piece, optionally already transformed into ``monster_id``."""
    unit = UnitInstance(
        piece=ChessPiece(id=f"{owner}-{pt.value}-{pos}", piece_type=pt, owner=owner)
    )
    state.board.place_unit(pos, unit)
    if monster_id is not None:
        unit.monster_id = monster_id
        if registry is not None:
            from game.mechanics.monsters import apply_on_summon_effects
            apply_on_summon_effects(state, unit, registry.get(monster_id), [],
                                    position=pos, registry=registry)
    return unit


def _build(state: GameState, pos: Position, owner: str, card_id: str,
           registry=None, complete: bool = True) -> BuildingInstance:
    from game.mechanics.buildings import building_max_integrity

    hp = building_max_integrity(card_id, registry)
    b = BuildingInstance(
        id=f"bld-{owner}-{pos}",
        owner=owner,
        building_card_id=card_id,
        position=pos,
        status=ConstructionStatus.COMPLETE if complete else ConstructionStatus.UNDER_CONSTRUCTION,
        max_integrity=hp,
        integrity=hp,
    )
    state.buildings.append(b)
    state.board.get_square(pos).building_id = b.id
    refresh_building_blocks(state)
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Integrity table
# ─────────────────────────────────────────────────────────────────────────────

def test_integrity_scales_with_building_size(registry):
    fortress = registry.get("fortress")     # medium
    shrine = registry.get("shrine")         # small
    assert fortress.base_integrity == BUILDING_BASE_INTEGRITY[BuildingSize.MEDIUM]
    assert shrine.base_integrity == BUILDING_BASE_INTEGRITY[BuildingSize.SMALL]
    assert fortress.base_integrity > shrine.base_integrity


# ─────────────────────────────────────────────────────────────────────────────
# A finished enemy Building is a wall
# ─────────────────────────────────────────────────────────────────────────────

def test_complete_enemy_building_blocks_landing_and_rays(registry):
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK)
    _build(state, Position(0, 3), "black", "fortress", registry)

    moves = get_legal_moves(state.board, Position(0, 0), rook, player_id="white",
                            registry=registry, state=state)
    assert Position(0, 3) not in moves          # can't land on it
    assert Position(0, 2) in moves              # can approach
    assert Position(0, 4) not in moves          # ray stops at the wall


def test_own_building_never_blocks_its_owner(registry):
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK)
    _build(state, Position(0, 3), "white", "fortress", registry)

    moves = get_legal_moves(state.board, Position(0, 0), rook, player_id="white",
                            registry=registry, state=state)
    assert Position(0, 3) in moves
    assert Position(0, 5) in moves


def test_under_construction_building_does_not_block(registry):
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK)
    _build(state, Position(0, 3), "black", "fortress", registry, complete=False)

    moves = get_legal_moves(state.board, Position(0, 0), rook, player_id="white",
                            registry=registry, state=state)
    assert Position(0, 4) in moves


def test_building_blocks_pawn_push_and_double_push(registry):
    state = _state()
    pawn = _put(state, Position(4, 1), "white", PieceType.PAWN)
    _build(state, Position(4, 2), "black", "shrine", registry)

    moves = get_legal_moves(state.board, Position(4, 1), pawn, player_id="white",
                            registry=registry, state=state)
    assert Position(4, 2) not in moves
    assert Position(4, 3) not in moves


def test_ignore_terrain_slides_through_but_cannot_land(registry):
    """sky_serpent — 'Ignores movement restrictions created by Buildings'."""
    state = _state()
    serpent = _put(state, Position(0, 0), "white", PieceType.ROOK,
                   monster_id="sky_serpent", registry=registry)
    assert "ignore_building_terrain" in serpent.statuses
    _build(state, Position(0, 3), "black", "fortress", registry)

    moves = get_legal_moves(state.board, Position(0, 0), serpent, player_id="white",
                            registry=registry, state=state)
    assert Position(0, 3) not in moves          # still can't stop on the structure
    assert Position(0, 5) in moves              # but the ray carries on past it


# ─────────────────────────────────────────────────────────────────────────────
# Attack permission — the headline rule
# ─────────────────────────────────────────────────────────────────────────────

def test_plain_piece_may_not_attack_a_building(registry):
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK)
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    assert can_attack_building(state, rook, b, registry) is False


def test_ordinary_monster_may_not_attack_a_building(registry):
    state = _state()
    unit = _put(state, Position(0, 0), "white", PieceType.ROOK,
                monster_id="stone_golem", registry=registry)
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    assert can_attack_building(state, unit, b, registry) is False


def test_ritual_monster_may_attack_a_building(registry):
    state = _state()
    titan = _put(state, Position(0, 0), "white", PieceType.ROOK,
                 monster_id="titan_of_the_foundation", registry=registry)
    assert registry.get("titan_of_the_foundation").ritual_only is True
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    assert can_attack_building(state, titan, b, registry) is True


def test_building_damage_bonus_monster_may_attack(registry):
    state = _state()
    dragon = _put(state, Position(0, 0), "white", PieceType.ROOK,
                  monster_id="obsidian_dragon", registry=registry)
    assert registry.get("obsidian_dragon").ritual_only is False
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    assert can_attack_building(state, dragon, b, registry) is True


def test_own_building_is_never_attackable(registry):
    state = _state()
    titan = _put(state, Position(0, 0), "white", PieceType.ROOK,
                 monster_id="titan_of_the_foundation", registry=registry)
    b = _build(state, Position(0, 3), "white", "fortress", registry)
    assert can_attack_building(state, titan, b, registry) is False


def test_siege_order_opens_a_building_to_any_unit(registry):
    """'or spells that allow that' — building_vulnerability breaches the wall."""
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK)
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    assert can_attack_building(state, rook, b, registry) is False

    b.vulnerable_turns = 2
    b.vulnerable_amount = 1
    assert can_attack_building(state, rook, b, registry) is True


# ─────────────────────────────────────────────────────────────────────────────
# AttackBuilding action
# ─────────────────────────────────────────────────────────────────────────────

def test_attack_building_damages_without_moving(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    titan = _put(state, Position(0, 0), "white", PieceType.ROOK,
                 monster_id="titan_of_the_foundation", registry=registry)
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    hp_before = b.integrity

    events = engine.execute(
        state, AttackBuilding(player_id="white", source=Position(0, 0), target=Position(0, 3)), rng,
    )

    assert any(isinstance(e, BuildingAttacked) for e in events)
    assert b.integrity == hp_before - 1
    assert b.status == ConstructionStatus.COMPLETE
    # The attacker stayed put; the chess move is spent.
    assert state.board.get_unit(Position(0, 0)) is titan
    assert state.get_player("white").chess_move_used is True


def test_attack_building_destroys_at_zero_integrity(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    b = _build(state, Position(0, 2), "black", "shrine", registry)   # small → 1 hp

    events = engine.execute(
        state, AttackBuilding(player_id="white", source=Position(0, 0), target=Position(0, 2)), rng,
    )
    assert any(isinstance(e, BuildingDestroyed) for e in events)
    assert b.status == ConstructionStatus.DESTROYED
    assert state.board.get_square(Position(0, 2)).building_id is None
    assert state.board.get_square(Position(0, 2)).complete_building_owner is None


def test_razed_building_stops_blocking_movement(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    rook = _put(state, Position(0, 0), "white", PieceType.ROOK,
                monster_id="titan_of_the_foundation", registry=registry)
    _build(state, Position(0, 2), "black", "shrine", registry)

    engine.execute(state, AttackBuilding(
        player_id="white", source=Position(0, 0), target=Position(0, 2)), rng)

    moves = get_legal_moves(state.board, Position(0, 0), rook, player_id="white",
                            registry=registry, state=state)
    assert Position(0, 2) in moves


def test_attack_building_rejected_for_ineligible_unit(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK)
    _build(state, Position(0, 3), "black", "fortress", registry)

    with pytest.raises(IllegalActionError):
        engine.execute(state, AttackBuilding(
            player_id="white", source=Position(0, 0), target=Position(0, 3)), rng)


def test_attack_building_rejected_when_out_of_reach(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    _build(state, Position(4, 4), "black", "fortress", registry)   # not on a rook line

    with pytest.raises(IllegalActionError):
        engine.execute(state, AttackBuilding(
            player_id="white", source=Position(0, 0), target=Position(4, 4)), rng)


def test_attack_building_rejected_when_garrisoned(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    _build(state, Position(0, 3), "black", "fortress", registry)
    _put(state, Position(0, 3), "black", PieceType.KNIGHT)

    with pytest.raises(IllegalActionError):
        engine.execute(state, AttackBuilding(
            player_id="white", source=Position(0, 0), target=Position(0, 3)), rng)


def test_attack_building_appears_in_legal_actions(registry):
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    _put(state, Position(7, 7), "white", PieceType.KING)
    _put(state, Position(7, 0), "black", PieceType.KING)
    _build(state, Position(0, 3), "black", "fortress", registry)

    actions = engine.get_legal_actions(state, "white")
    sieges = [a for a in actions if isinstance(a, AttackBuilding)]
    assert [a.target for a in sieges] == [Position(0, 3)]
    # ...and the same square is absent from the plain-move list.
    assert Position(0, 3) not in [
        a.target for a in actions if isinstance(a, MovePiece) and a.source == Position(0, 0)
    ]


def test_destroy_on_capture_flattens_in_one_blow(registry, rng):
    """obsidian_dragon — 'Buildings captured by this monster are destroyed immediately'."""
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="obsidian_dragon", registry=registry)
    b = _build(state, Position(0, 3), "black", "fortress", registry)   # medium → 2 hp

    engine.execute(state, AttackBuilding(
        player_id="white", source=Position(0, 0), target=Position(0, 3)), rng)
    assert b.status == ConstructionStatus.DESTROYED


# ─────────────────────────────────────────────────────────────────────────────
# The defensive stack
# ─────────────────────────────────────────────────────────────────────────────

def test_building_aura_adds_one_required_hit(registry):
    """siege_captain — 'require one additional successful hostile action'."""
    state = _state()
    _put(state, Position(1, 3), "black", PieceType.ROOK,
         monster_id="siege_captain", registry=registry)
    b = _build(state, Position(0, 3), "black", "shrine", registry)   # 1 hp + 1 aura

    events: list = []
    assert damage_building(state, b, 1, events, registry) is False
    assert b.status == ConstructionStatus.COMPLETE
    assert damage_building(state, b, 1, events, registry) is True
    assert b.status == ConstructionStatus.DESTROYED


def test_building_aura_lapses_when_its_monster_leaves(registry):
    state = _state()
    guard = _put(state, Position(1, 3), "black", PieceType.ROOK,
                 monster_id="siege_captain", registry=registry)
    b = _build(state, Position(0, 3), "black", "shrine", registry)

    events: list = []
    assert damage_building(state, b, 1, events, registry) is False
    state.board.remove_unit(Position(1, 3))     # the captain is captured
    assert damage_building(state, b, 1, events, registry) is True


def test_castle_keeper_absorbs_the_whole_attack(registry):
    state = _state()
    keeper = _put(state, Position(1, 3), "black", PieceType.ROOK,
                  monster_id="castle_keeper", registry=registry)
    assert any(s.startswith("building_capture_protection:") for s in keeper.statuses)
    b = _build(state, Position(0, 3), "black", "shrine", registry)

    events: list = []
    assert damage_building(state, b, 1, events, registry) is False
    assert b.integrity == b.max_integrity        # no damage at all
    assert any(isinstance(e, BuildingAttackBlocked)
               and e.reason == "building_capture_protection" for e in events)
    # The charge is spent — the next blow lands.
    assert damage_building(state, b, 1, events, registry) is True


def test_temporary_building_protection_survives_a_lethal_blow(registry):
    state = _state()
    b = _build(state, Position(0, 3), "black", "shrine", registry)
    b.protection_uses = 1
    b.protection_turns = 2

    events: list = []
    assert damage_building(state, b, 5, events, registry) is False
    assert b.status == ConstructionStatus.COMPLETE
    assert b.integrity == 1
    assert any(isinstance(e, BuildingAttackBlocked)
               and e.reason == "temporary_building_protection" for e in events)
    assert damage_building(state, b, 1, events, registry) is True


def test_siege_order_makes_every_blow_land_harder(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    ps = state.get_player("white")
    ps.hand = ["siege_order"]
    state.phase = Phase.PREPARATION
    b = _build(state, Position(0, 3), "black", "fortress", registry)   # 2 hp

    from game.core.actions import ActivateSpell
    events = engine.execute(state, ActivateSpell(
        player_id="white", card_id="siege_order", target=b.id), rng)
    assert any(isinstance(e, BuildingVulnerable) for e in events)
    assert b.vulnerable_turns == 2

    # A plain rook can now attack it, and each blow does 1 + 1 = 2.
    state.phase = Phase.CHESS
    _put(state, Position(0, 0), "white", PieceType.ROOK)
    engine.execute(state, AttackBuilding(
        player_id="white", source=Position(0, 0), target=Position(0, 3)), rng)
    assert b.status == ConstructionStatus.DESTROYED


def test_siege_order_mark_decays_on_owner_end_turn(registry):
    from game.mechanics.buildings import tick_building_timers

    state = _state()
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    b.vulnerable_turns = 2
    tick_building_timers(state, "white")        # not the owner — no decay
    assert b.vulnerable_turns == 2
    tick_building_timers(state, "black")
    assert b.vulnerable_turns == 1


# ─────────────────────────────────────────────────────────────────────────────
# demolition_charge + repair
# ─────────────────────────────────────────────────────────────────────────────

def test_demolition_charge_damages_adjacent_enemy_buildings(registry, rng):
    from game.core.state import TrapInstance
    from game.mechanics.monsters import check_enter_radius_traps

    state = _state()
    state.traps.append(TrapInstance(
        id="trap-white-001", owner="white", card_id="demolition_charge",
        position=Position(3, 3), radius=1, trigger_condition="enter_radius", charges=1,
    ))
    b = _build(state, Position(3, 4), "black", "fortress", registry)
    intruder = _put(state, Position(3, 2), "black", PieceType.KNIGHT)

    events: list = []
    check_enter_radius_traps(state, intruder, Position(3, 2), events, registry, rng=rng)
    assert any(isinstance(e, BuildingAttacked) for e in events)
    assert b.integrity == b.max_integrity - 1


def test_repair_building_heals_at_owner_end_of_turn(registry):
    from game.mechanics.buildings import repair_buildings

    state = _state()
    _put(state, Position(1, 3), "black", PieceType.ROOK,
         monster_id="royal_engineer", registry=registry)
    b = _build(state, Position(0, 3), "black", "fortress", registry)
    b.integrity = 0

    events: list = []
    repair_buildings(state, "black", events, registry)
    assert b.integrity == 1
    assert any(isinstance(e, BuildingRepaired) for e in events)

    repair_buildings(state, "black", events, registry)
    assert b.integrity == b.max_integrity
    # Capped — a third pass changes nothing.
    before = len(events)
    repair_buildings(state, "black", events, registry)
    assert b.integrity == b.max_integrity
    assert len(events) == before


def test_nearer_building_shields_the_one_behind_it(registry):
    """A Rook can't see past the first wall to besiege the second."""
    engine = RulesEngine(registry=registry)
    state = _state()
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    _build(state, Position(0, 3), "black", "fortress", registry)
    _build(state, Position(0, 5), "black", "shrine", registry)

    targets = engine._siege_targets(
        state, Position(0, 0),
        state.board.get_unit(Position(0, 0)),
        state.get_player("white"),
    )
    assert targets == {Position(0, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# Forced relocation respects the terrain rule
# ─────────────────────────────────────────────────────────────────────────────

def test_push_cannot_shove_a_defender_into_a_building(registry, rng):
    """storm_dragon's push_unit falls back to a normal capture at a wall."""
    from game.mechanics.monsters import try_push_unit

    state = _state()
    attacker = _put(state, Position(4, 2), "white", PieceType.ROOK,
                    monster_id="storm_dragon", registry=registry)
    _put(state, Position(4, 3), "black", PieceType.KNIGHT)
    _build(state, Position(4, 4), "white", "fortress", registry)   # wall behind

    events: list = []
    pushed = try_push_unit(
        state, attacker, Position(4, 2), Position(4, 3),
        registry.get("storm_dragon"), events,
    )
    assert pushed is False
    assert state.board.get_unit(Position(4, 3)) is not None    # still there


def test_forced_march_stops_at_a_building(registry, rng):
    """forced_march advances a Pawn only onto a square it could occupy."""
    engine = RulesEngine(registry=registry)
    state = _state()
    state.phase = Phase.PREPARATION
    state.get_player("white").hand = ["forced_march"]
    pawn = _put(state, Position(4, 1), "white", PieceType.PAWN)
    _build(state, Position(4, 2), "black", "shrine", registry)

    from game.core.actions import ActivateSpell
    engine.execute(state, ActivateSpell(
        player_id="white", card_id="forced_march",
        target={"position": (4, 1), "destination": None}), rng)

    assert state.board.get_unit(Position(4, 1)) is pawn


def test_razing_a_wall_can_deliver_checkmate(registry, rng):
    """A demolition uncovers lines the same way a move does."""
    from game.core.phases import FinalDuelType

    engine = RulesEngine(registry=registry)
    state = _state()
    # Black king boxed into the corner by its own pieces; white's rook on
    # the a-file is walled off by black's own Shrine at a7.
    _put(state, Position(0, 0), "white", PieceType.ROOK,
         monster_id="titan_of_the_foundation", registry=registry)
    _put(state, Position(1, 0), "white", PieceType.ROOK)
    _put(state, Position(7, 4), "white", PieceType.KING)
    _put(state, Position(0, 7), "black", PieceType.KING)
    _build(state, Position(0, 6), "black", "shrine", registry)

    # The wall is doing real work: black is not in check while it stands.
    from game.chess.movement import is_in_check
    assert is_in_check(state.board, "black") is False

    engine.execute(state, AttackBuilding(
        player_id="white", source=Position(0, 0), target=Position(0, 6)), rng)

    assert is_in_check(state.board, "black") is True
    assert state.duel is not None
    assert state.duel.duel_type == FinalDuelType.SIEGE


def test_blocked_capture_on_the_back_rank_does_not_open_a_promotion(registry, rng):
    """
    A shielded defender on the promotion square survives — and the Pawn is
    back on its own square, so there is nothing to promote.
    """
    from game.core.actions import MovePiece as _Move
    from game.core.phases import DecisionType

    engine = RulesEngine(registry=registry)
    state = _state()
    pawn = _put(state, Position(1, 6), "white", PieceType.PAWN)
    defender = _put(state, Position(0, 7), "black", PieceType.ROOK)
    defender.add_status("shield:1")
    _put(state, Position(7, 0), "white", PieceType.KING)
    _put(state, Position(7, 7), "black", PieceType.KING)

    engine.execute(state, _Move(
        player_id="white", source=Position(1, 6), target=Position(0, 7)), rng)

    assert state.board.get_unit(Position(1, 6)) is pawn      # bounced back
    assert state.board.get_unit(Position(0, 7)) is defender  # still standing
    assert state.pending_decision is None
    assert state.phase != Phase.PROMOTION_SELECTION
