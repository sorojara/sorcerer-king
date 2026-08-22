"""
test_stage13_effects.py — Stage 13: the last pending card effects

Every effect type declared in data/*.yaml now resolves to real behaviour
(or to a documented pointer at the module that owns it). This file covers
the four that had no implementation anywhere before Stage 13, plus the
policy-shaped effects that Ritual Monsters borrowed from King cards:

    royal_support_bonus         rally_the_kingdom
    enemy_territory_mobility    shadow_regent (King) / eclipse_executioner
    ritual_information_discount arcane_sovereign
    ritual_support              Shrine
    formation_support           bannerlord_eternal (Monster-borne)
    graveyard_threshold_bonus   ossuary_king (Monster-borne)
    graveyard_recycle           ossuary_king (Monster-borne)

It also asserts the whole-registry invariant: no effect type shipped in
the card data is missing a handler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ActivateSpell
from game.core.events import CardDrawn
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
    GameState,
    KingCardState,
    PlayerState,
    RitualState,
)

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=42)


def _state() -> GameState:
    return GameState(
        game_id="stage13-effects",
        turn_number=1,
        active_player="white",
        phase=Phase.PREPARATION,
        board=BoardState(),
        players={"white": PlayerState("white"), "black": PlayerState("black")},
    )


def _put(state, pos, owner, pt, monster_id=None, registry=None):
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


def _crown(state, player_id, king_card_id):
    ps = state.get_player(player_id)
    ps.king_pool = [KingCardState(king_card_id=king_card_id, status=KingCardStatus.ACTIVE)]
    ps.active_king = king_card_id


# ─────────────────────────────────────────────────────────────────────────────
# Registry completeness
# ─────────────────────────────────────────────────────────────────────────────

def test_every_declared_effect_type_has_a_handler(registry):
    """No card in data/*.yaml can dispatch to a missing handler."""
    import yaml
    from game.mechanics.effects.registry import EFFECT_REGISTRY

    declared: set[str] = set()
    for path in sorted(_DATA_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "effects" and isinstance(value, list):
                        for entry in value:
                            if isinstance(entry, dict) and "type" in entry:
                                declared.add(str(entry["type"]))
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(doc)

    # The schema-doc pseudo-entries ("none | position | piece | ...") are
    # target_type documentation, not effect types.
    declared = {t for t in declared if "|" not in t}
    missing = sorted(declared - set(EFFECT_REGISTRY))
    assert missing == [], f"effect types with no handler: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# rally_the_kingdom — royal_support_bonus
# ─────────────────────────────────────────────────────────────────────────────

def test_rally_the_kingdom_stores_a_timed_kingdom_bonus(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    state.get_player("white").hand = ["rally_the_kingdom"]

    engine.execute(state, ActivateSpell(
        player_id="white", card_id="rally_the_kingdom", target=None), rng)

    ps = state.get_player("white")
    assert ps.royal_support_bonus_amount == 1
    assert ps.royal_support_bonus_turns == 2
    assert state.get_player("black").royal_support_bonus_amount == 0


def test_rally_amplifies_existing_duel_support_only(registry):
    """'Does not expand support range' — a piece out of range still gives nothing."""
    from game.mechanics.duel import _gather_piece_support

    state = _state()
    _put(state, Position(4, 4), "white", PieceType.KNIGHT)   # in range
    _put(state, Position(0, 0), "white", PieceType.KNIGHT)   # far away

    ids = iter(f"i{i}" for i in range(100))
    before = _gather_piece_support(state, "white", Position(4, 5), 2, lambda: next(ids))
    assert len(before) == 1
    base = before[0].amount

    state.get_player("white").royal_support_bonus_amount = 1
    after = _gather_piece_support(state, "white", Position(4, 5), 2, lambda: next(ids))
    assert len(after) == 1                # range unchanged — still only one piece
    assert after[0].amount == base + 1    # but it counts for one more


def test_rally_decays_on_the_casters_own_end_turn(registry, rng):
    from game.core.actions import EndTurn

    engine = RulesEngine(registry=registry)
    state = _state()
    state.phase = Phase.END
    ps = state.get_player("white")
    ps.royal_support_bonus_amount = 1
    ps.royal_support_bonus_turns = 1

    engine.execute(state, EndTurn(player_id="white"), rng)
    assert ps.royal_support_bonus_turns == 0
    assert ps.royal_support_bonus_amount == 0


# ─────────────────────────────────────────────────────────────────────────────
# enemy_territory_mobility
# ─────────────────────────────────────────────────────────────────────────────

def test_eclipse_executioner_gains_mobility_in_enemy_territory(registry):
    state = _state()
    unit = _put(state, Position(4, 4), "white", PieceType.KNIGHT,
                monster_id="eclipse_executioner", registry=registry)
    assert any(s.startswith("territory_movement_bonus:") for s in unit.statuses)

    from game.mechanics.monsters import get_movement_additions
    card = registry.get("eclipse_executioner")

    # Rank 4 (index 4) is neutral ground — no bonus.
    neutral = get_movement_additions(unit, card, state=state,
                                     position=Position(4, 4), registry=registry)
    # Rank 7 (index 6) is inside black's home Territory — bonus applies.
    state.board.remove_unit(Position(4, 4))
    state.board.place_unit(Position(4, 6), unit)
    inside = get_movement_additions(unit, card, state=state,
                                    position=Position(4, 6), registry=registry)
    assert len(inside) > len(neutral)


def test_shadow_regent_grants_mobility_to_its_assassins(registry):
    from game.mechanics.kings import apply_king_policy_auras

    state = _state()
    _crown(state, "white", "shadow_regent")
    assassin = _put(state, Position(4, 4), "white", PieceType.BISHOP,
                    monster_id="royal_poisoner", registry=registry)
    assert registry.get("royal_poisoner").archetype == "assassin"
    other = _put(state, Position(5, 4), "white", PieceType.ROOK,
                 monster_id="stone_golem", registry=registry)

    assassin.statuses = [s for s in assassin.statuses
                         if not s.startswith("territory_movement_bonus:")]
    apply_king_policy_auras(state, "white", registry)

    assert any(s.startswith("territory_movement_bonus:") for s in assassin.statuses)
    assert not any(s.startswith("territory_movement_bonus:") for s in other.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# arcane_sovereign — ritual_information_discount
# ─────────────────────────────────────────────────────────────────────────────

def test_first_voluntary_reveal_pays_the_information_discount(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _crown(state, "white", "arcane_sovereign")
    ps = state.get_player("white")
    ps.hand = ["ritual_insight"]
    ps.deck = ["stone_golem", "shadow_wolf", "dark_magician"]
    ps.ritual_pool = [RitualState(ritual_id="rite_of_the_ember_crown")]

    events = engine.execute(state, ActivateSpell(
        player_id="white", card_id="ritual_insight", target=None), rng)

    assert ps.ritual_pool[0].revelation == RevelationState.FORETOLD
    assert ps.ritual_info_discount_used is True
    # ritual_insight draws 1 of its own; the King policy adds a second.
    assert sum(1 for e in events if isinstance(e, CardDrawn)) == 2


def test_information_discount_pays_out_once_per_match(registry, rng):
    engine = RulesEngine(registry=registry)
    state = _state()
    _crown(state, "white", "arcane_sovereign")
    ps = state.get_player("white")
    ps.hand = ["ritual_insight", "ritual_insight"]
    ps.deck = ["stone_golem", "shadow_wolf", "dark_magician", "iron_vanguard"]
    ps.ritual_pool = [RitualState(ritual_id="rite_of_the_ember_crown")]

    engine.execute(state, ActivateSpell(
        player_id="white", card_id="ritual_insight", target=None), rng)
    ps.preparation_action_used = False
    events = engine.execute(state, ActivateSpell(
        player_id="white", card_id="ritual_insight", target=None), rng)

    # FORETOLD → REVEALED is a second step, and the latch is already spent:
    # only ritual_insight's own draw happens.
    assert sum(1 for e in events if isinstance(e, CardDrawn)) == 1


def test_involuntary_reveal_pays_nothing(registry):
    """Being checked is not a policy dividend (README §15.1 vs §15.2)."""
    from game.mechanics.rituals import on_check_detected

    state = _state()
    _crown(state, "white", "arcane_sovereign")
    ps = state.get_player("white")
    ps.deck = ["stone_golem", "shadow_wolf"]
    ps.ritual_pool = [RitualState(ritual_id="rite_of_the_ember_crown")]

    events: list = []
    on_check_detected(state, "white", events, registry)

    assert ps.ritual_pool[0].revelation == RevelationState.FORETOLD
    assert ps.ritual_info_discount_used is False
    assert not any(isinstance(e, CardDrawn) for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# Shrine — ritual_support
# ─────────────────────────────────────────────────────────────────────────────

def test_shrine_widens_ritual_formation_tolerance(registry):
    from game.mechanics.rituals import _ritual_range_bonus

    state = _state()
    assert _ritual_range_bonus(state, "white", registry) == 0

    shrine = BuildingInstance(
        id="bld-white-001", owner="white", building_card_id="shrine",
        position=Position(3, 1), status=ConstructionStatus.COMPLETE,
    )
    state.buildings.append(shrine)
    assert _ritual_range_bonus(state, "white", registry) == 1
    # ...and it's the owner's alone.
    assert _ritual_range_bonus(state, "black", registry) == 0


def test_disabled_shrine_gives_no_ritual_support(registry):
    from game.mechanics.rituals import _ritual_range_bonus

    state = _state()
    shrine = BuildingInstance(
        id="bld-white-001", owner="white", building_card_id="shrine",
        position=Position(3, 1), status=ConstructionStatus.COMPLETE,
        disabled_turns=1,
    )
    state.buildings.append(shrine)
    assert _ritual_range_bonus(state, "white", registry) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Monster-borne policy effects
# ─────────────────────────────────────────────────────────────────────────────

def test_bannerlord_shields_nearby_warriors_only(registry):
    from game.mechanics.monsters import apply_monster_auras

    state = _state()
    _put(state, Position(4, 4), "white", PieceType.KNIGHT,
         monster_id="bannerlord_eternal", registry=registry)
    near = _put(state, Position(4, 5), "white", PieceType.ROOK,
                monster_id="siege_captain", registry=registry)   # warrior, adjacent
    far = _put(state, Position(0, 0), "white", PieceType.ROOK,
               monster_id="siege_captain", registry=registry)    # warrior, distant

    for u in (near, far):
        u.statuses = [s for s in u.statuses if not s.startswith("shield:")]
    apply_monster_auras(state, "white", registry)

    assert any(s.startswith("shield:") for s in near.statuses)
    assert not any(s.startswith("shield:") for s in far.statuses)


def test_ossuary_king_shields_necromancers_past_the_graveyard_threshold(registry):
    from game.mechanics.monsters import apply_monster_auras

    state = _state()
    ps = state.get_player("white")
    _put(state, Position(4, 4), "white", PieceType.QUEEN,
         monster_id="ossuary_king", registry=registry)
    necro = _put(state, Position(2, 2), "white", PieceType.BISHOP,
                 monster_id="bone_collector", registry=registry)
    assert registry.get("bone_collector").archetype == "necromancer"
    necro.statuses = [s for s in necro.statuses if not s.startswith("shield:")]

    ps.graveyard = ["stone_golem"] * 5          # below threshold (6)
    apply_monster_auras(state, "white", registry)
    assert not any(s.startswith("shield:") for s in necro.statuses)

    ps.graveyard = ["stone_golem"] * 6          # threshold met
    apply_monster_auras(state, "white", registry)
    assert any(s.startswith("shield:") for s in necro.statuses)


def test_ossuary_king_recycles_a_destroyed_ally(registry):
    from game.mechanics.kings import maybe_recycle_destroyed_monster

    state = _state()
    _put(state, Position(4, 4), "white", PieceType.QUEEN,
         monster_id="ossuary_king", registry=registry)
    ps = state.get_player("white")

    events: list = []
    assert maybe_recycle_destroyed_monster(
        state, "white", "stone_golem", events, registry) is True
    assert ps.deck[-1] == "stone_golem"
    # Once per turn, shared with grave_crowned_king's identical policy.
    assert maybe_recycle_destroyed_monster(
        state, "white", "shadow_wolf", events, registry) is False
