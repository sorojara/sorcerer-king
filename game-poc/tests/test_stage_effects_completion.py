"""
test_stage_effects_completion.py — audit follow-up: previously-stub monster
effects, now implemented (2026-08-21 pass).

Covers every monster effect that was either a crashing/no-op stub or a
dead status flag before this pass, EXCLUDING Final Duel effects
(king_support_bonus, royal_support_suppression, duel_debuff, intercept):

    mirror_magus     copy_effect              (bugfix: missing _log in _meta.py)
    executioner      weakened_target_bonus    (conditional retaliate bypass)
    duelist          challenge_unit
    bone_collector   graveyard_counter / capture_protection_from_counter
    mourning_queen   death_trigger_draw
    crypt_walker     graveyard_scaling_movement
    moon_stalker     territory_bonus
    phantom_lancer   pass_through_units
    spellbreaker     suppress_spell_zone
    master_mason     construction_speed_bonus
    frontier_warden  territory_expansion
    saboteur         disable_building
    guild_foreman    restore_builder
    vessel_reclaimer dismiss_monster (activated, adjacent-ally variant)
    arcane_archivist inspect_top_deck / reorder_top_deck
    grave_scholar    graveyard_inspect / graveyard_to_deck
    blood_seer       sacrifice_bonus
    herald_of_the_gate ritual_activation_range
    veil_conjurer    obscure_influence
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateMonsterAbility,
    ReorderTopDeck,
    StartConstruction,
    SummonMonster,
)
from game.core.observation import build_observation
from game.core.phases import ConstructionStatus, DecisionType, KingCardStatus, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import BuildingInstance, GameState, KingCardState, PlayerState

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=7)


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str, monster_id: str | None = None) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece, monster_id=monster_id)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _player(pid: str, **kwargs) -> PlayerState:
    ps = PlayerState(player_id=pid, **kwargs)
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    return ps


def _state(board, white, black, phase=Phase.CHESS, active="white") -> GameState:
    return GameState(
        game_id="test-effects-completion",
        turn_number=1,
        active_player=active,
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=7,
    )


def _kings(board):
    _place(board, "white", PieceType.KING, "a1", "wk")
    _place(board, "black", PieceType.KING, "h8", "bk")


# ─────────────────────────────────────────────────────────────────────────────

class TestMirrorMagusCopyEffect:
    def test_copy_effect_no_longer_crashes(self, registry, rng):
        board = BoardState()
        _kings(board)
        mm = _place(board, "white", PieceType.BISHOP, "d4", "wb1", monster_id="mirror_magus")
        _place(board, "white", PieceType.BISHOP, "e5", "wb2", monster_id="dark_magician")
        state = _state(board, _player("white"), _player("black"))
        engine = RulesEngine(registry=registry)

        events = engine.execute(
            state,
            ActivateMonsterAbility(player_id="white", unit_position=Position.from_algebraic("d4"),
                                    ability_id="copy_effect"),
            rng,
        )
        assert any(s == "copied_effect:spell_radius_bonus" for s in mm.statuses)
        assert events


class TestExecutionerWeakenedTargetBonus:
    def test_bypasses_retaliate_only_when_target_has_no_shield(self, registry, rng):
        from game.mechanics.monsters import apply_retaliate

        board = BoardState()
        _kings(board)
        attacker = _place(board, "white", PieceType.ROOK, "a3", "wr", monster_id="executioner")
        attacker.add_status("guaranteed_capture_vs_no_shield")
        victim = _place(board, "black", PieceType.PAWN, "a4", "bp")
        victim.add_status("retaliate")
        board_state = _state(board, _player("white"), _player("black"))

        # No shield at all → bypass applies, attacker survives.
        destroyed = apply_retaliate(board_state, victim, Position.from_algebraic("a4"), attacker, [])
        assert destroyed is False

    def test_does_not_bypass_when_target_still_had_shield(self, registry):
        from game.mechanics.monsters import apply_retaliate

        board = BoardState()
        _kings(board)
        attacker = _place(board, "white", PieceType.ROOK, "a3", "wr", monster_id="executioner")
        attacker.add_status("guaranteed_capture_vs_no_shield")
        victim = _place(board, "black", PieceType.PAWN, "a4", "bp")
        victim.add_status("retaliate")
        victim.add_status("shield:2")  # exposed bypassed the shield check but it's unspent
        state = _state(board, _player("white"), _player("black"))

        destroyed = apply_retaliate(state, victim, Position.from_algebraic("a4"), attacker, [])
        assert destroyed is True  # executioner does NOT get a free pass here


class TestDuelistChallengeUnit:
    def test_challenge_restricts_captures_both_ways(self, registry, rng):
        board = BoardState()
        _kings(board)
        duelist = _place(board, "white", PieceType.KNIGHT, "d4", "wn", monster_id="duelist")
        target = _place(board, "black", PieceType.QUEEN, "d5", "bn")
        other_attacker = _place(board, "white", PieceType.QUEEN, "d6", "wq")
        state = _state(board, _player("white"), _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(
            state,
            ActivateMonsterAbility(player_id="white", unit_position=Position.from_algebraic("d4"),
                                    ability_id="challenge_unit", target=(3, 4)),
            rng,
        )
        assert any(s.startswith("challenged_by:wn:") for s in target.statuses)

        # A DIFFERENT white unit may no longer capture the challenged target.
        other_moves = get_pseudo_legal_moves(board, Position.from_algebraic("d6"), other_attacker, registry=registry)
        assert Position.from_algebraic("d5") not in other_moves

        # The challenged unit itself may only capture the duelist, not other targets.
        decoy = _place(board, "white", PieceType.PAWN, "e5", "wp_decoy")
        target_moves = get_pseudo_legal_moves(board, Position.from_algebraic("d5"), target, registry=registry)
        assert Position.from_algebraic("e5") not in target_moves  # decoy off-limits
        assert Position.from_algebraic("d4") in target_moves      # duelist still fair game

    def test_challenge_requires_adjacency(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.KNIGHT, "a1", "wn", monster_id="duelist")
        _place(board, "black", PieceType.KNIGHT, "d5", "bn")
        state = _state(board, _player("white"), _player("black"))
        engine = RulesEngine(registry=registry)
        with pytest.raises(IllegalActionError):
            engine.execute(
                state,
                ActivateMonsterAbility(player_id="white", unit_position=Position.from_algebraic("a1"),
                                        ability_id="challenge_unit", target=(3, 4)),
                rng,
            )


class TestBoneCollectorGraveyardCounter:
    def test_counter_increments_and_grants_shield_at_threshold(self, registry, rng):
        board = BoardState()
        _kings(board)
        collector = _place(board, "white", PieceType.PAWN, "b2", "wp1", monster_id="bone_collector")
        collector.add_status("graveyard_counter:0")
        collector.add_status("capture_protection_from_counter:2:1")
        # A second allied monster that will be destroyed.
        _place(board, "white", PieceType.PAWN, "c2", "wp2", monster_id="apprentice_mage")
        _place(board, "black", PieceType.ROOK, "c7", "br")
        white = _player("white")
        black = _player("black")
        state = _state(board, white, black, active="black")
        engine = RulesEngine(registry=registry)

        from game.core.actions import MovePiece
        # black rook captures the apprentice_mage pawn twice (need 2 deaths
        # for the counter to hit required_counters=2) — use two separate
        # allied monsters destroyed on white's own pieces across two moves.
        events = engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("c7"),
                                                   target=Position.from_algebraic("c2")), rng)
        assert any(s == "graveyard_counter:1" for s in collector.statuses)
        assert not any(s.startswith("shield:") for s in collector.statuses)

        # Second death.
        black_rook2 = _place(board, "black", PieceType.ROOK, "b7", "br2")
        _place(board, "white", PieceType.PAWN, "b6", "wp3", monster_id="apprentice_mage")
        state.active_player = "black"
        state.phase = Phase.CHESS
        black.chess_move_used = False
        engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("b7"),
                                         target=Position.from_algebraic("b6")), rng)
        assert any(s == "graveyard_counter:2" for s in collector.statuses)
        assert any(s.startswith("shield:") for s in collector.statuses)


class TestMourningQueenDeathTriggerDraw:
    def test_draws_once_per_turn_on_allied_death(self, registry, rng):
        board = BoardState()
        _kings(board)
        mq = _place(board, "white", PieceType.QUEEN, "d1", "wq", monster_id="mourning_queen")
        mq.add_status("death_trigger_draw:1")
        _place(board, "white", PieceType.PAWN, "c2", "wp", monster_id="apprentice_mage")
        _place(board, "black", PieceType.ROOK, "c7", "br")
        white = _player("white", deck=["dark_magician", "wyrm_knight"])
        black = _player("black")
        state = _state(board, white, black, active="black")
        engine = RulesEngine(registry=registry)

        from game.core.actions import MovePiece
        engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("c7"),
                                         target=Position.from_algebraic("c2")), rng)
        assert "dark_magician" in white.hand
        assert white.death_trigger_draw_used_this_turn is True


class TestCryptWalkerGraveyardScalingMovement:
    def test_no_bonus_below_threshold_bonus_above(self, registry):
        board = BoardState()
        _kings(board)
        cw = _place(board, "white", PieceType.KNIGHT, "d4", "wn", monster_id="crypt_walker")
        cw.add_status("graveyard_leap:5:1")
        white = _player("white", graveyard=["a", "b", "c"])  # below threshold
        state = _state(board, white, _player("black"))

        moves_below = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), cw, registry=registry, state=state)
        assert Position.from_algebraic("d5") not in moves_below  # (0,+1) leap not yet granted

        white.graveyard = ["a", "b", "c", "d", "e"]  # at threshold
        moves_at = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), cw, registry=registry, state=state)
        assert Position.from_algebraic("d5") in moves_at


class TestMoonStalkerTerritoryBonus:
    def test_bonus_leap_only_inside_enemy_territory(self, registry):
        board = BoardState()
        _kings(board)
        ms = _place(board, "white", PieceType.KNIGHT, "d6", "wn", monster_id="moon_stalker")  # black home rank
        ms.add_status("territory_movement_bonus:1")
        state = _state(board, _player("white"), _player("black"))

        moves_in_enemy_territory = get_pseudo_legal_moves(board, Position.from_algebraic("d6"), ms, registry=registry, state=state)
        assert Position.from_algebraic("d7") in moves_in_enemy_territory

        board2 = BoardState()
        _kings(board2)
        ms2 = _place(board2, "white", PieceType.KNIGHT, "d4", "wn2", monster_id="moon_stalker")
        ms2.add_status("territory_movement_bonus:1")
        state2 = _state(board2, _player("white"), _player("black"))
        moves_neutral = get_pseudo_legal_moves(board2, Position.from_algebraic("d4"), ms2, registry=registry, state=state2)
        assert Position.from_algebraic("d5") not in moves_neutral


class TestPhantomLancerPassThroughUnits:
    def test_pawn_double_push_through_one_occupied_square(self, registry):
        board = BoardState()
        _kings(board)
        pawn = _place(board, "white", PieceType.PAWN, "d2", "wp", monster_id="phantom_lancer")
        _place(board, "black", PieceType.PAWN, "d3", "bp_block")  # normally blocks double push
        moves = get_pseudo_legal_moves(board, Position.from_algebraic("d2"), pawn, registry=registry)
        assert Position.from_algebraic("d4") in moves       # phased through d3
        assert Position.from_algebraic("d3") not in moves   # cannot land on occupied square


class TestSpellbreakerSuppressSpellZone:
    def test_neutralises_blocked_zone_in_radius(self, registry):
        board = BoardState()
        _kings(board)
        sb = _place(board, "white", PieceType.KNIGHT, "d4", "wn", monster_id="spellbreaker")
        mover = _place(board, "white", PieceType.ROOK, "e1", "wr")
        board.get_square(Position.from_algebraic("e5")).add_effect("blocked:2:black:veil_of_stillness")
        state = _state(board, _player("white"), _player("black"))

        moves = get_pseudo_legal_moves(board, Position.from_algebraic("e1"), mover, registry=registry, state=state)
        assert Position.from_algebraic("e5") in moves  # suppressed — normally would be blocked

    def test_blocked_zone_outside_radius_still_blocks(self, registry):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.KNIGHT, "a1", "wn", monster_id="spellbreaker")
        mover = _place(board, "white", PieceType.ROOK, "h1", "wr")
        board.get_square(Position.from_algebraic("h5")).add_effect("blocked:2:black:veil_of_stillness")
        state = _state(board, _player("white"), _player("black"))

        moves = get_pseudo_legal_moves(board, Position.from_algebraic("h1"), mover, registry=registry, state=state)
        assert Position.from_algebraic("h5") not in moves


class TestMasterMasonConstructionSpeedBonus:
    def test_reduces_construction_turns(self, registry, rng):
        from game.core.state import BuildingPoolEntry

        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.KNIGHT, "b1", "wn", monster_id="master_mason")
        pawn = _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", building_pool=[BuildingPoolEntry(building_card_id="fortress", copies_available=1)])
        state = _state(board, white, _player("black"), phase=Phase.PREPARATION)
        engine = RulesEngine(registry=registry)

        engine.execute(state, StartConstruction(player_id="white", pawn_position=Position.from_algebraic("b2"),
                                                  building_card_id="fortress"), rng)
        building = state.buildings[0]
        base_card = registry.get("fortress")
        assert building.remaining_turns == max(base_card.construction_turns - 1, 1)


class TestFrontierWardenTerritoryExpansion:
    def test_adjacent_frontier_warden_expands_building_radius(self, registry):
        from game.mechanics.territory import territory_squares

        board = BoardState()
        _kings(board)
        state = _state(board, _player("white"), _player("black"))
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="watchtower",
            position=Position.from_algebraic("d4"), status=ConstructionStatus.COMPLETE,
        ))
        base_card = registry.get("watchtower")
        far_square = Position(
            min(7, 3 + base_card.territory_radius + 1), 3,
        )
        without_bonus = territory_squares(state, "white", registry)
        assert far_square not in without_bonus

        _place(board, "white", PieceType.ROOK, "d5", "wr", monster_id="frontier_warden")
        with_bonus = territory_squares(state, "white", registry)
        assert far_square in with_bonus


class TestSaboteurDisableBuilding:
    def test_disable_suspends_aura_until_owner_end_turn(self, registry, rng):
        from game.mechanics.buildings import apply_building_auras

        board = BoardState()
        _kings(board)
        _place(board, "black", PieceType.PAWN, "e5", "bp", monster_id="saboteur")
        board.get_square(Position.from_algebraic("e4")).building_id = "bld-1"
        white_vessel = _place(board, "white", PieceType.BISHOP, "d4", "wb", monster_id="dark_magician")
        white = _player("white")
        black = _player("black")
        state = _state(board, white, black, active="black")
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("e4"), status=ConstructionStatus.COMPLETE,
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(
            state,
            ActivateMonsterAbility(player_id="black", unit_position=Position.from_algebraic("e5"),
                                    ability_id="disable_building", target="bld-1"),
            rng,
        )
        assert state.buildings[0].disabled_turns >= 1

        apply_building_auras(state, "white", registry)
        assert not any(s.startswith("shield:") for s in white_vessel.statuses)


class TestGuildForemanRestoreBuilder:
    def test_restores_spent_builder_token(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp1")
        spent_pawn = _place(board, "white", PieceType.PAWN, "b3", "wp2")
        spent_pawn.builder_available = False
        white = _player("white", hand=["guild_foreman"])
        state = _state(board, white, _player("black"), phase=Phase.PREPARATION)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(player_id="white", card_id="guild_foreman",
                                             vessel_position=Position.from_algebraic("b2")), rng)
        assert spent_pawn.builder_available is True


class TestVesselReclaimerDismissMonster:
    def test_dismisses_adjacent_ally_to_graveyard(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.QUEEN, "d4", "wq", monster_id="vessel_reclaimer")
        ally = _place(board, "white", PieceType.PAWN, "d5", "wp", monster_id="apprentice_mage")
        white = _player("white")
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(
            state,
            ActivateMonsterAbility(player_id="white", unit_position=Position.from_algebraic("d4"),
                                    ability_id="dismiss_monster", target=(3, 4)),
            rng,
        )
        assert ally.monster_id is None
        assert "apprentice_mage" in white.graveyard
        assert "apprentice_mage" not in white.hand


class TestArcaneArchivistDeckReorder:
    def test_inspect_opens_reorder_decision_and_resolves(self, registry, rng):
        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", hand=["arcane_archivist"], deck=["dark_magician", "wyrm_knight", "stone_golem", "thorn_boar"])
        state = _state(board, white, _player("black"), phase=Phase.PREPARATION)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(player_id="white", card_id="arcane_archivist",
                                             vessel_position=Position.from_algebraic("b2")), rng)
        assert state.pending_decision is not None
        assert state.pending_decision.decision_type == DecisionType.REORDER_DECK
        peeked = list(state.pending_decision.options)
        assert peeked == ["dark_magician", "wyrm_knight", "stone_golem"]

        legal = engine.get_legal_actions(state, "white", registry=registry)
        assert all(isinstance(a, ReorderTopDeck) for a in legal)
        assert len(legal) == 6  # 3! permutations

        new_order = list(reversed(peeked))
        engine.execute(state, ReorderTopDeck(player_id="white", card_ids=new_order), rng)
        assert white.deck[:3] == new_order
        assert state.pending_decision is None


class TestGraveScholarGraveyard:
    def test_inspects_and_returns_first_graveyard_card(self, registry, rng):
        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", hand=["grave_scholar"], graveyard=["dark_magician", "wyrm_knight"])
        state = _state(board, white, _player("black"), phase=Phase.PREPARATION)
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, SummonMonster(player_id="white", card_id="grave_scholar",
                                                       vessel_position=Position.from_algebraic("b2")), rng)
        from game.core.events import GraveyardInspected, CardsReturnedToDeck
        assert any(isinstance(e, GraveyardInspected) and e.card_ids == ("dark_magician", "wyrm_knight") for e in events)
        assert any(isinstance(e, CardsReturnedToDeck) and e.card_ids == ("dark_magician",) for e in events)
        assert white.graveyard == ["wyrm_knight"]
        assert "dark_magician" in white.deck


class TestBloodSeerSacrificeBonus:
    def test_sacrifice_grants_progress_to_other_ritual(self, registry, rng):
        from game.core.state import RitualState
        from game.mechanics.rituals import execute_ritual

        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.BISHOP, "d4", "wb")
        seer = _place(board, "white", PieceType.PAWN, "d3", "wp", monster_id="blood_seer")
        seer.add_status("sacrifice_ritual_bonus:2")
        white = _player("white")
        white.ritual_pool = [RitualState(ritual_id="circle_of_the_arcane", progress=0)]
        state = _state(board, white, _player("black"))

        ritual = registry.get("rite_of_the_wyrm")
        rstate = RitualState(ritual_id="rite_of_the_wyrm")
        events = []
        execute_ritual(
            state, "white", ritual, rstate,
            [Position.from_algebraic("d3"), Position.from_algebraic("d4")],
            events, registry=registry,
        )
        assert white.ritual_pool[0].progress == 2


class TestHeraldOfTheGateRitualActivationRange:
    def test_extends_formation_node_matching_range(self, registry):
        from game.core.state import RitualState
        from game.mechanics.rituals import find_ritual_candidates

        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.BISHOP, "d4", "wb")
        # pawn expected at offset (-1, 0) = c4; place it one extra square away at b4.
        _place(board, "white", PieceType.PAWN, "b4", "wp")
        _place(board, "white", PieceType.KNIGHT, "d5", "wn")  # exact offset (0, 1)
        state = _state(board, _player("white"), _player("black"))
        ritual = registry.get("rite_of_the_wyrm")
        rstate = RitualState(ritual_id="rite_of_the_wyrm")

        without_bonus = find_ritual_candidates(state, "white", ritual, rstate, registry)
        assert without_bonus == []

        herald = _place(board, "white", PieceType.KNIGHT, "a1", "wh", monster_id="herald_of_the_gate")
        herald.add_status("ritual_range_bonus:1")
        with_bonus = find_ritual_candidates(state, "white", ritual, rstate, registry)
        assert len(with_bonus) == 1


class TestVeilConjurerObscureInfluence:
    def test_hides_card_id_from_non_owner_outside_the_area(self, registry):
        board = BoardState()
        _kings(board)
        _place(board, "black", PieceType.BISHOP, "e5", "bb", monster_id="veil_conjurer")
        board.get_square(Position.from_algebraic("e4")).add_effect("blocked:2:black:veil_of_stillness")
        state = _state(board, _player("white"), _player("black"))

        obs_white = build_observation(state, "white", registry)
        eff = next(e for e in obs_white.board.square_effects if e.position == Position.from_algebraic("e4"))
        assert eff.effect_type == "blocked"  # type stays visible
        assert eff.card_id is None            # exact card hidden from the non-owner

        obs_black = build_observation(state, "black", registry)
        eff_black = next(e for e in obs_black.board.square_effects if e.position == Position.from_algebraic("e4"))
        assert eff_black.card_id == "veil_of_stillness"  # owner always sees it fully

    def test_visible_once_observer_occupies_the_square(self, registry):
        board = BoardState()
        _kings(board)
        _place(board, "black", PieceType.BISHOP, "e5", "bb", monster_id="veil_conjurer")
        board.get_square(Position.from_algebraic("e4")).add_effect("blocked:2:black:veil_of_stillness")
        _place(board, "white", PieceType.PAWN, "e4", "wp")
        state = _state(board, _player("white"), _player("black"))

        obs_white = build_observation(state, "white", registry)
        eff = next(e for e in obs_white.board.square_effects if e.position == Position.from_algebraic("e4"))
        assert eff.card_id == "veil_of_stillness"
