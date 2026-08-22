"""
test_effect_scenarios_new_cards.py — the card set written from the artwork.

Each of these cards ships as a finished card FACE in data/images/ (name,
Vessels, rules text, archetype), and each test below asserts the rules
text printed on that art, quoted in the docstring.

    void_stalker        bone_marauder       spirit_hunter      celestial_oracle
    binding_chains      celestial_guidance  arcane_fortitude   return_to_hand
    purify              siphon_of_power     dread_tide         seal_of_lockdown
    stolen_moment       erase_memory        analyze_strategy   ritual_acceleration
    illusory_doubles
"""

from __future__ import annotations

import pytest

from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import Position
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateSpell,
    EndTurn,
    MovePiece,
)
from game.core.events import CardBanished, DeckInspected, PieceExpired, PieceSpawned
from game.core.phases import ConstructionStatus, Phase, RevelationState
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError

from tests.effect_harness import (  # noqa: F401
    engine,
    find_at,
    give_ritual,
    new_state,
    place_building,
    put,
    registry,
    rng,
    spell_reach,
    square_effects,
    statuses_with,
)


# ─────────────────────────────────────────────────────────────────────────────
# MONSTERS
# ─────────────────────────────────────────────────────────────────────────────

def test_void_stalker_moves_over_units(registry):
    """"Can move through enemy territories and over units." (Beast)"""
    state = new_state()
    stalker = put(state, Position(3, 1), "white", "bishop", "void_stalker", registry)
    put(state, Position(4, 2), "black", "pawn")   # normally blocks the diagonal
    moves = get_pseudo_legal_moves(state.board, Position(3, 1), stalker,
                                   registry=registry, state=state)
    assert Position(4, 2) in moves, "it may still capture the blocker"


def test_void_stalker_slides_through_a_building(registry):
    state = new_state()
    place_building(state, "fortress", Position(3, 4), "black", ConstructionStatus.COMPLETE)
    stalker = put(state, Position(3, 3), "white", "knight", "void_stalker", registry)
    assert "ignore_building_terrain" in stalker.statuses


def test_bone_marauder_leaves_a_skeleton_pawn_behind(registry):
    """"When this is destroyed, summon a Skeleton Pawn on an adjacent square.\""""
    state = new_state(phase=Phase.CHESS, active="black")
    put(state, Position(3, 4), "white", "pawn", "bone_marauder", registry)
    put(state, Position(3, 6), "black", "rook")
    events = engine(registry).execute(
        state, MovePiece(player_id="black", source=Position(3, 6), target=Position(3, 4)),
        DeterministicRNG(1),
    )
    spawned = [e for e in events if isinstance(e, PieceSpawned)]
    assert spawned, "the Marauder should leave something behind"
    assert spawned[0].label == "skeleton"
    assert spawned[0].piece_type == "pawn"
    assert spawned[0].player_id == "white", "the Skeleton serves its old master"
    skeleton = state.board.get_unit(spawned[0].position)
    assert skeleton is not None and skeleton.owner == "white"
    assert max(abs(spawned[0].position.file - 3), abs(spawned[0].position.rank - 4)) == 1


def test_spirit_hunter_draws_when_an_enemy_monster_is_summoned_nearby(registry):
    """"When an enemy monster is summoned nearby, draw 1 card." (Warrior)"""
    from game.core.actions import SummonMonster

    state = new_state()
    put(state, Position(3, 3), "black", "bishop", "spirit_hunter", registry)
    ps_black = state.get_player("black")
    ps_black.hand.clear()
    ps_black.deck = ["a_card"]

    # White may only summon inside their own Territory (home ranks 0-2).
    put(state, Position(3, 2), "white", "bishop")
    state.get_player("white").hand.append("dark_magician")
    engine(registry).execute(
        state,
        SummonMonster(player_id="white", card_id="dark_magician",
                      vessel_position=Position(3, 2)),
        DeterministicRNG(1),
    )
    assert ps_black.hand == ["a_card"], "the Hunter reacted to the summon next door"


def test_spirit_hunter_ignores_a_distant_summon(registry):
    from game.core.actions import SummonMonster

    state = new_state()
    put(state, Position(0, 0), "black", "bishop", "spirit_hunter", registry)
    ps_black = state.get_player("black")
    ps_black.hand.clear()
    ps_black.deck = ["a_card"]
    put(state, Position(6, 2), "white", "bishop")
    state.get_player("white").hand.append("dark_magician")
    engine(registry).execute(
        state,
        SummonMonster(player_id="white", card_id="dark_magician",
                      vessel_position=Position(6, 2)),
        DeterministicRNG(1),
    )
    assert ps_black.hand == [], "radius 2 means radius 2"


def test_celestial_oracle_peeks_at_the_top_card_when_activated(registry):
    """"Once per turn, look at the top card of your deck." (Spellcaster)"""
    state = new_state(phase=Phase.CHESS)
    ps = state.get_player("white")
    ps.deck = ["a_card", "b_card"]
    put(state, Position(3, 3), "white", "bishop", "celestial_oracle", registry)
    assert state.pending_decision is None, "it is once-per-turn, not on-summon"
    events = engine(registry).execute(
        state,
        ActivateMonsterAbility(player_id="white", unit_position=Position(3, 3),
                               ability_id="inspect_top_deck"),
        DeterministicRNG(1),
    )
    seen = [e for e in events if isinstance(e, DeckInspected)]
    assert seen and list(seen[0].card_ids) == ["a_card"]
    assert ps.deck == ["a_card", "b_card"], "looking is not drawing"
    assert state.pending_decision is None, "one card has no ordering to choose"


# ─────────────────────────────────────────────────────────────────────────────
# SPELLS — targeted at a piece
# ─────────────────────────────────────────────────────────────────────────────

def test_binding_chains_immobilizes_an_enemy_for_one_turn(registry):
    """"Immobilize target enemy unit for 1 turn (it cannot move).\""""
    state = new_state()
    victim = put(state, Position(3, 4), "black", "rook")
    state.get_player("white").hand.append("binding_chains")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="binding_chains",
                             target={"position": (3, 4)}),
        DeterministicRNG(1),
    )
    assert statuses_with(victim, "immobilized:") == ["immobilized:1"]
    assert get_pseudo_legal_moves(state.board, Position(3, 4), victim,
                                  registry=registry, state=state) == []


def test_binding_chains_cannot_target_your_own_piece(registry):
    state = new_state()
    put(state, Position(3, 4), "white", "rook")
    state.get_player("white").hand.append("binding_chains")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state, ActivateSpell(player_id="white", card_id="binding_chains",
                                 target={"position": (3, 4)}),
            DeterministicRNG(1),
        )


def test_celestial_guidance_moves_an_ally_two_squares_freely(registry):
    """"Move target allied unit up to 2 squares in any direction (ignoring
    normal movement rules).\""""
    state = new_state()
    pawn = put(state, Position(3, 1), "white", "pawn")
    state.get_player("white").hand.append("celestial_guidance")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="celestial_guidance",
                             target={"position": (3, 1), "destination": (5, 2)}),
        DeterministicRNG(1),
    )
    assert find_at(state, pawn) == Position(5, 2), "a Pawn moving diagonally 2 squares"
    assert state.get_player("white").chess_move_used is False


def test_celestial_guidance_stops_at_two_squares(registry):
    state = new_state()
    put(state, Position(3, 1), "white", "pawn")
    state.get_player("white").hand.append("celestial_guidance")
    with pytest.raises(IllegalActionError):
        engine(registry).execute(
            state, ActivateSpell(player_id="white", card_id="celestial_guidance",
                                 target={"position": (3, 1), "destination": (3, 5)}),
            DeterministicRNG(1),
        )


def test_arcane_fortitude_adds_a_shield_on_top_of_an_existing_one(registry):
    """"Give an allied unit +1 Capture Protection." — +1, not "set to 1"."""
    state = new_state()
    unit = put(state, Position(3, 3), "white", "rook", "stone_golem", registry)
    assert statuses_with(unit, "shield:") == ["shield:2"]
    state.get_player("white").hand.append("arcane_fortitude")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="arcane_fortitude",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    assert statuses_with(unit, "shield:") == ["shield:3"]


def test_return_to_hand_puts_the_monster_back_in_hand(registry):
    """"Return target allied non-King unit to your hand. You may summon it
    again later.\""""
    state = new_state()
    unit = put(state, Position(3, 3), "white", "bishop", "dark_magician", registry)
    state.get_player("white").hand.append("return_to_hand")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="return_to_hand",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    assert unit.monster_id is None
    assert find_at(state, unit) == Position(3, 3), "the Vessel stays"
    assert "dark_magician" in state.get_player("white").hand


def test_purify_strips_negative_statuses_but_keeps_your_own_shield(registry):
    """"Remove all negative effects, curses, and Trap auras from target
    allied unit or zone.\""""
    state = new_state()
    unit = put(state, Position(3, 3), "white", "rook", "stone_golem", registry)
    unit.add_status("immobilized:2")
    unit.add_status("exposed:1")
    unit.add_status("effects_suppressed:1")
    state.board.get_square(Position(3, 3)).add_effect("cursed:3:black:cursed_ground")
    state.get_player("white").hand.append("purify")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="purify",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    assert statuses_with(unit, "immobilized:") == []
    assert statuses_with(unit, "exposed:") == []
    assert statuses_with(unit, "effects_suppressed:") == []
    assert statuses_with(unit, "shield:") == ["shield:2"], "its own protection is not an affliction"
    assert not any(e.startswith("cursed:") for e in square_effects(state, Position(3, 3)))


# ─────────────────────────────────────────────────────────────────────────────
# SPELLS — damage
# ─────────────────────────────────────────────────────────────────────────────

def test_siphon_of_power_destroys_an_unshielded_enemy(registry):
    """"Deal 1 damage to an enemy unit. If it survives, you draw 1 card.\""""
    state = new_state()
    victim = put(state, Position(3, 4), "black", "rook")
    ps = state.get_player("white")
    ps.hand.append("siphon_of_power")
    ps.deck = ["a_card"]
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="siphon_of_power",
                             target={"position": (3, 4)}),
        DeterministicRNG(1),
    )
    assert find_at(state, victim) is None
    assert ps.hand == [], "no draw — it did not survive"


def test_siphon_of_power_draws_when_a_shield_absorbs_it(registry):
    state = new_state()
    victim = put(state, Position(3, 4), "black", "rook", "stone_golem", registry)
    ps = state.get_player("white")
    ps.hand.append("siphon_of_power")
    ps.deck = ["a_card"]
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="siphon_of_power",
                             target={"position": (3, 4)}),
        DeterministicRNG(1),
    )
    assert find_at(state, victim) == Position(3, 4), "the shield held"
    assert statuses_with(victim, "shield:") == ["shield:1"], "one charge spent"
    assert ps.hand == ["a_card"], "it survived — you draw"


def test_dread_tide_damages_every_enemy_in_the_area(registry):
    """"Deal 1 damage to all enemy units in a 5x5 area around target square
    and push them 1 square directly away from the center.\""""
    state = new_state()
    spell_reach(state, "white", (3, 3))
    doomed = put(state, Position(2, 2), "black", "rook")
    shielded = put(state, Position(4, 4), "black", "rook", "stone_golem", registry)
    friendly = put(state, Position(2, 4), "white", "rook")
    state.get_player("white").hand.append("dread_tide")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="dread_tide", target=(3, 3)),
        DeterministicRNG(1),
    )
    assert find_at(state, doomed) is None, "unshielded units are swept away"
    assert find_at(state, friendly) == Position(2, 4), "your own units are untouched"
    assert find_at(state, shielded) == Position(5, 5), "a survivor is pushed away from d4"


def test_dread_tide_never_touches_a_king(registry):
    state = new_state()
    spell_reach(state, "white", (4, 6))
    black_king = state.board.get_unit(Position(4, 7))
    state.get_player("white").hand.append("dread_tide")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="dread_tide", target=(4, 6)),
        DeterministicRNG(1),
    )
    assert find_at(state, black_king) == Position(4, 7)


# ─────────────────────────────────────────────────────────────────────────────
# SPELLS — zones and tempo
# ─────────────────────────────────────────────────────────────────────────────

def test_seal_of_lockdown_freezes_enemies_but_not_the_caster(registry):
    """"Seal a 3x3 zone. Enemy units inside cannot move, capture, or be
    targeted by effects. Lasts 2 turns.\""""
    state = new_state()
    spell_reach(state, "white", (3, 3))
    enemy = put(state, Position(3, 3), "black", "rook")
    mine = put(state, Position(2, 2), "white", "rook")
    state.get_player("white").hand.append("seal_of_lockdown")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="seal_of_lockdown", target=(3, 3)),
        DeterministicRNG(1),
    )
    assert get_pseudo_legal_moves(state.board, Position(3, 3), enemy,
                                  registry=registry, state=state) == []
    assert get_pseudo_legal_moves(state.board, Position(2, 2), mine,
                                  registry=registry, state=state), \
        "the sealing player walks through their own seal"


def test_a_sealed_enemy_cannot_be_targeted_by_a_spell(registry):
    state = new_state()
    spell_reach(state, "white", (3, 3))
    put(state, Position(3, 3), "black", "rook")
    ps = state.get_player("white")
    ps.hand += ["seal_of_lockdown", "binding_chains"]
    eng = engine(registry)
    eng.execute(state, ActivateSpell(player_id="white", card_id="seal_of_lockdown",
                                     target=(3, 3)), DeterministicRNG(1))
    ps.preparation_action_used = False
    with pytest.raises(IllegalActionError):
        eng.execute(state, ActivateSpell(player_id="white", card_id="binding_chains",
                                         target={"position": (3, 3)}), DeterministicRNG(1))


def test_stolen_moment_refunds_the_action_it_cost(registry):
    """"Take an extra action this turn: you may cast another Spell or make
    an additional chess move.\""""
    state = new_state()
    ps = state.get_player("white")
    ps.hand.append("stolen_moment")
    ps.chess_move_used = True
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="stolen_moment", target=None),
        DeterministicRNG(1),
    )
    assert ps.preparation_action_used is False, "another Spell may follow"
    assert ps.chess_move_used is False, "and an additional chess move"


def test_stolen_moment_actually_lets_a_second_spell_resolve(registry):
    state = new_state()
    ps = state.get_player("white")
    ps.hand += ["stolen_moment", "analyze_strategy"]
    ps.deck = ["a_card", "b_card", "c_card"]
    eng = engine(registry)
    eng.execute(state, ActivateSpell(player_id="white", card_id="stolen_moment", target=None),
                DeterministicRNG(1))
    eng.execute(state, ActivateSpell(player_id="white", card_id="analyze_strategy", target=None),
                DeterministicRNG(1))
    assert "a_card" in ps.hand


# ─────────────────────────────────────────────────────────────────────────────
# SPELLS — card manipulation
# ─────────────────────────────────────────────────────────────────────────────

def test_erase_memory_banishes_the_opponents_top_card_and_draws(registry):
    """"Banish the top card of your opponent's deck face-down. They cannot
    look at it. Then you draw 1 card.\""""
    state = new_state()
    white, black = state.get_player("white"), state.get_player("black")
    white.hand.append("erase_memory")
    white.deck = ["mine"]
    black.deck = ["theirs_top", "theirs_next"]
    events = engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="erase_memory", target=None),
        DeterministicRNG(1),
    )
    assert black.deck == ["theirs_next"]
    assert black.banished == ["theirs_top"]
    assert "theirs_top" not in black.graveyard, "banished is not the graveyard"
    assert white.hand == ["mine"]
    assert any(isinstance(e, CardBanished) for e in events)


def test_a_banished_card_never_comes_back_on_a_reshuffle(registry):
    state = new_state()
    black = state.get_player("black")
    black.deck = ["doomed"]
    black.graveyard = ["recycled"]
    white = state.get_player("white")
    white.hand.append("erase_memory")
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="erase_memory", target=None),
        DeterministicRNG(1),
    )
    # Black now draws through an empty deck — the graveyard recycles, the
    # banished card does not.
    put(state, Position(3, 6), "black", "pawn", "apprentice_mage", registry)
    assert "doomed" not in black.hand
    assert "doomed" not in black.deck


def test_analyze_strategy_takes_one_and_bottoms_the_rest(registry):
    """"Look at the top 3 cards of your deck. Put 1 into your hand and put
    the rest on the bottom in any order.\""""
    state = new_state()
    ps = state.get_player("white")
    ps.hand.append("analyze_strategy")
    ps.deck = ["one", "two", "three", "four"]
    events = engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="analyze_strategy", target=None),
        DeterministicRNG(1),
    )
    seen = [e for e in events if isinstance(e, DeckInspected)]
    assert seen and list(seen[0].card_ids) == ["one", "two", "three"], "all 3 are looked at"
    assert ps.hand == ["one"], "1 into hand"
    assert ps.deck == ["four", "two", "three"], "the rest go to the BOTTOM"


def test_ritual_acceleration_advances_progress_by_two(registry):
    """"Advance your Ritual progress by 2 (the SEALED -> FORETOLD threshold
    can be reached sooner).\""""
    state = new_state()
    ps = state.get_player("white")
    ps.hand.append("ritual_acceleration")
    rs = give_ritual(state, "white", "rite_of_the_wyrm", RevelationState.SEALED)
    engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="ritual_acceleration", target=None),
        DeterministicRNG(1),
    )
    assert (rs.progress, rs.revelation) != (0, RevelationState.SEALED), \
        "two points of progress must land somewhere"


# ─────────────────────────────────────────────────────────────────────────────
# illusory_doubles
# ─────────────────────────────────────────────────────────────────────────────

def test_illusory_doubles_spawns_a_copy_that_cannot_capture(registry):
    """"Summon an illusion copy of an allied unit in an adjacent empty
    square. Illusions cannot capture and have 1 Health. Lasts 2 turns.\""""
    state = new_state()
    put(state, Position(3, 3), "white", "rook")
    state.get_player("white").hand.append("illusory_doubles")
    events = engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="illusory_doubles",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    spawned = [e for e in events if isinstance(e, PieceSpawned)]
    assert spawned and spawned[0].piece_type == "rook", "a copy of the target"
    illusion = state.board.get_unit(spawned[0].position)
    assert "no_capture" in illusion.statuses
    assert statuses_with(illusion, "expires:") == ["expires:2"]

    enemy_pos = Position(spawned[0].position.file, spawned[0].position.rank + 2)
    put(state, enemy_pos, "black", "pawn")
    moves = get_pseudo_legal_moves(state.board, spawned[0].position, illusion,
                                   registry=registry, state=state)
    assert enemy_pos not in moves, "illusions cannot capture"


def test_an_illusion_has_one_health(registry):
    """"have 1 Health" — no shield, so the first hostile action removes it."""
    state = new_state()
    put(state, Position(3, 3), "white", "rook")
    state.get_player("white").hand.append("illusory_doubles")
    events = engine(registry).execute(
        state, ActivateSpell(player_id="white", card_id="illusory_doubles",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    illusion = state.board.get_unit([e for e in events if isinstance(e, PieceSpawned)][0].position)
    assert statuses_with(illusion, "shield:") == []


def test_an_illusion_expires_after_two_turns(registry):
    state = new_state(phase=Phase.CHESS)
    put(state, Position(3, 3), "white", "rook")
    state.get_player("white").hand.append("illusory_doubles")
    eng = engine(registry)
    state.phase = Phase.PREPARATION
    events = eng.execute(
        state, ActivateSpell(player_id="white", card_id="illusory_doubles",
                             target={"position": (3, 3)}),
        DeterministicRNG(1),
    )
    where = [e for e in events if isinstance(e, PieceSpawned)][0].position

    state.phase = Phase.CHESS
    state.active_player = "white"
    eng.execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert state.board.get_unit(where) is not None, "still there after one turn"

    state.phase = Phase.CHESS
    state.active_player = "white"
    out = eng.execute(state, EndTurn(player_id="white"), DeterministicRNG(1))
    assert state.board.get_unit(where) is None, "gone after two"
    assert any(isinstance(e, PieceExpired) for e in out)


# ─────────────────────────────────────────────────────────────────────────────
# Whole-set invariants
# ─────────────────────────────────────────────────────────────────────────────

_NEW_CARDS = [
    "void_stalker", "bone_marauder", "spirit_hunter", "celestial_oracle",
    "binding_chains", "celestial_guidance", "arcane_fortitude", "return_to_hand",
    "purify", "siphon_of_power", "dread_tide", "seal_of_lockdown",
    "stolen_moment", "erase_memory", "analyze_strategy", "ritual_acceleration",
    "illusory_doubles",
]


@pytest.mark.parametrize("card_id", _NEW_CARDS)
def test_every_new_card_is_in_the_registry_with_effects(registry, card_id):
    card = registry.get(card_id)
    assert card.effects, f"{card_id} would be a blank card"


@pytest.mark.parametrize("card_id", _NEW_CARDS)
def test_every_new_card_has_its_artwork(registry, card_id):
    from tests.effect_harness import DATA_DIR

    card = registry.get(card_id)
    image = card.image_path or f"{card_id}.png"
    assert (DATA_DIR / "images" / image).exists(), f"{card_id} art missing"


def test_new_cards_are_all_main_deck_drawable(registry):
    """None of these is a Ritual payoff — they are ordinary deck cards."""
    drawable = {c.id for c in registry.all_monsters() if not c.ritual_only}
    drawable |= {c.id for c in registry.all_spells()}
    assert set(_NEW_CARDS) <= drawable


# ─────────────────────────────────────────────────────────────────────────────
# Legal-action enumeration
#
# A Spell the engine can EXECUTE but never OFFERS is unreachable in a real
# game — neither the UI nor a bot can find it. Before the generic
# single-piece branch in get_legal_actions, every piece-targeted Spell whose
# effect type wasn't one of the four hardcoded cases fell into that hole,
# sever_the_bond included.
# ─────────────────────────────────────────────────────────────────────────────

_PIECE_TARGET_SPELLS = [
    ("binding_chains", "opponent"),
    ("siphon_of_power", "opponent"),
    ("arcane_fortitude", "self"),
    ("purify", "self"),
    ("illusory_doubles", "self"),
    ("return_to_hand", "self"),
    ("sever_the_bond", "self"),
    ("celestial_guidance", "self"),
]


@pytest.mark.parametrize("card_id,side", _PIECE_TARGET_SPELLS)
def test_piece_targeted_spells_are_offered_by_get_legal_actions(registry, card_id, side):
    from game.core.actions import ActivateSpell

    state = new_state()
    # A monster on each side, so dismiss-style Spells have something to take.
    put(state, Position(3, 2), "white", "bishop", "dark_magician", registry)
    put(state, Position(3, 5), "black", "bishop", "dark_magician", registry)
    state.get_player("white").hand.append(card_id)

    offered = [
        a for a in engine(registry).get_legal_actions(state, "white")
        if isinstance(a, ActivateSpell) and a.card_id == card_id
    ]
    assert offered, f"{card_id} is executable but never offered — unreachable in play"

    expected_owner = "black" if side == "opponent" else "white"
    for action in offered:
        pos = Position(*action.target["position"])
        unit = state.board.get_unit(pos)
        assert unit is not None and unit.owner == expected_owner, (
            f"{card_id} was offered a {unit.owner if unit else 'empty'} target "
            f"but wants {expected_owner}"
        )


@pytest.mark.parametrize("card_id,side", _PIECE_TARGET_SPELLS)
def test_every_offered_piece_spell_actually_executes(registry, card_id, side):
    """Enumeration and validation must agree — an offered action never throws."""
    from game.core.actions import ActivateSpell

    state = new_state()
    put(state, Position(3, 2), "white", "bishop", "dark_magician", registry)
    put(state, Position(3, 5), "black", "bishop", "dark_magician", registry)
    state.get_player("white").hand.append(card_id)

    offered = [
        a for a in engine(registry).get_legal_actions(state, "white")
        if isinstance(a, ActivateSpell) and a.card_id == card_id
    ]
    engine(registry).execute(state, offered[0], DeterministicRNG(1))


def test_sunder_the_walls_is_offered_against_an_enemy_building(registry):
    from game.core.actions import ActivateSpell

    state = new_state()
    b = place_building(state, "fortress", Position(3, 5), "black")
    place_building(state, "fortress", Position(3, 1), "white")
    state.get_player("white").hand.append("sunder_the_walls")
    offered = [
        a for a in engine(registry).get_legal_actions(state, "white")
        if isinstance(a, ActivateSpell) and a.card_id == "sunder_the_walls"
    ]
    assert [a.target for a in offered] == [b.id], "only the ENEMY Building"
