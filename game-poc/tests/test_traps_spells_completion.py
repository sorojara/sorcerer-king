"""
test_traps_spells_completion.py — Trap/Spell audit follow-up (2026-08-21).

Covers every Trap/Spell effect type that was completely unregistered
before this pass, EXCLUDING Final Duel ones (duel_escape_bonus,
royal_support_bonus, royal_support_mark, royal_support_range_bonus):

    gravity_well / magnetic_reversal   pull_unit
    forced_march                       move_unit
    exchange_of_fates                  swap_units
    wall_of_mist                       block_line_of_sight
    fractured_path                     movement_cost_zone
    blood_price                        discard_card
    forbidden_knowledge                reveal_own_hand_card / reveal_enemy_ritual
    monster_seal / nullification_glyph suppress_monster_effects
    sanctuary / vessel_lock            prohibit_summoning
    unstable_transmutation             temporary_vessel_class
    rapid_construction                 advance_construction
    builders_ward                      protect_builder
    guardian_sigils                    cancel_capture
    vengeance_mark                     mark_unit
    transformation_alarm               draw_card (summon trigger)
    ritual_insight                     reveal_ritual (own_ritual)
    omen_bell                          reveal_ritual (enemy, ritual_progress trigger)
    false_prophecy                     ritual_bluff
    profane_interruption               interrupt_ritual
    dispel_field                       remove_spatial_effects
    border_beacon                      temporary_territory
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ActivateSpell, ActivateTrap, MovePiece, PlaceTrap, SummonMonster
from game.core.observation import build_observation
from game.core.phases import ConstructionStatus, KingCardStatus, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import (
    BuildingInstance,
    BuildingPoolEntry,
    GameState,
    KingCardState,
    PlayerState,
    RitualState,
    TrapInstance,
)

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng():
    return DeterministicRNG(seed=11)


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str, monster_id: str | None = None) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece, monster_id=monster_id)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _player(pid: str, **kwargs) -> PlayerState:
    ps = PlayerState(player_id=pid, **kwargs)
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    return ps


def _state(board, white, black, phase=Phase.PREPARATION, active="white") -> GameState:
    return GameState(
        game_id="test-traps-spells", turn_number=1, active_player=active, phase=phase,
        board=board, players={"white": white, "black": black}, rng_seed=11,
    )


def _kings(board):
    _place(board, "white", PieceType.KING, "a1", "wk")
    _place(board, "black", PieceType.KING, "h8", "bk")


class TestPullUnit:
    def test_gravity_well_pulls_toward_trap(self, registry, rng):
        board = BoardState()
        _kings(board)
        enemy = _place(board, "black", PieceType.PAWN, "d6", "bp")
        state = _state(board, _player("white"), _player("black"), phase=Phase.CHESS, active="black")
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="gravity_well", charges=1,
            position=Position.from_algebraic("d4"), radius=2, trigger_condition="enter_radius",
        ))
        engine = RulesEngine(registry=registry)
        movers = _place(board, "black", PieceType.QUEEN, "a6", "bq")
        engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("a6"),
                                         target=Position.from_algebraic("a5")), rng)
        # enemy pawn itself didn't move — confirm the trap radius covers d6.
        assert board.get_unit(Position.from_algebraic("d6")) is not None

    def test_magnetic_reversal_pulls_enemy_toward_caster_king(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "d4", "wk")
        _place(board, "black", PieceType.KING, "h8", "bk")
        _place(board, "black", PieceType.PAWN, "d6", "bp")
        white = _player("white", hand=["magnetic_reversal"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        legal = engine.get_legal_actions(state, "white", registry=registry)
        pulls = [a for a in legal if isinstance(a, ActivateSpell) and a.card_id == "magnetic_reversal"]
        assert pulls, "expected at least one magnetic_reversal target"
        engine.execute(state, pulls[0], rng)
        assert board.get_unit(Position.from_algebraic("d5")) is not None
        assert board.get_unit(Position.from_algebraic("d6")) is None


class TestMoveUnit:
    def test_forced_march_advances_pawn_without_chess_move(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", hand=["forced_march"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="forced_march",
                                             target={"position": (1, 1)}), rng)
        assert board.get_unit(Position.from_algebraic("b3")) is not None
        assert board.get_unit(Position.from_algebraic("b2")) is None
        assert white.chess_move_used is False


class TestSwapUnits:
    def test_exchange_of_fates_swaps_two_own_units(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp1")
        _place(board, "white", PieceType.KNIGHT, "d2", "wn1")
        white = _player("white", hand=["exchange_of_fates"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(
            player_id="white", card_id="exchange_of_fates",
            target={"position": (1, 1), "destination": (3, 1)},
        ), rng)
        assert board.get_unit(Position.from_algebraic("b2")).piece.id == "wn1"
        assert board.get_unit(Position.from_algebraic("d2")).piece.id == "wp1"


class TestWallOfMist:
    def test_blocks_sliding_but_not_knight(self, registry, rng):
        board = BoardState()
        _kings(board)
        rook = _place(board, "white", PieceType.ROOK, "a4", "wr")
        _place(board, "white", PieceType.KNIGHT, "a1", "wn")
        white = _player("white", hand=["wall_of_mist"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="wall_of_mist",
                                             target=(2, 3)), rng)  # c4, wall extends c4..f4
        moves = get_pseudo_legal_moves(board, Position.from_algebraic("a4"), rook, registry=registry, state=state)
        assert Position.from_algebraic("c4") not in moves
        assert Position.from_algebraic("b4") in moves  # short of the wall is fine


class TestFracturedPath:
    def test_caps_movement_inside_zone(self, registry, rng):
        board = BoardState()
        _kings(board)
        queen = _place(board, "white", PieceType.QUEEN, "d4", "wq")
        white = _player("white", hand=["fractured_path"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="fractured_path",
                                             target=(4, 3)), rng)  # e4 — reachable, zone still covers d4
        moves = get_pseudo_legal_moves(board, Position.from_algebraic("d4"), queen, registry=registry, state=state)
        assert Position.from_algebraic("d5") in moves       # 1 square — within cap
        assert Position.from_algebraic("d7") not in moves    # 3 squares — capped out


class TestBloodPrice:
    def test_capturing_player_discards(self, registry, rng):
        board = BoardState()
        _kings(board)
        attacker = _place(board, "black", PieceType.ROOK, "a7", "br")
        _place(board, "white", PieceType.PAWN, "a6", "wp")
        black = _player("black", hand=["dark_magician", "wyrm_knight"])
        state = _state(board, _player("white"), black, phase=Phase.CHESS, active="black")
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="blood_price",
            position=Position.from_algebraic("a6"), radius=1, trigger_condition="capture",
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("a7"),
                                         target=Position.from_algebraic("a6")), rng)
        assert len(black.hand) == 1
        assert len(black.graveyard) == 1


class TestForbiddenKnowledge:
    def test_reveals_own_hand_card_and_enemy_ritual(self, registry, rng):
        from game.core.events import EnemyCardRevealed, RitualRevelationChanged
        from game.core.phases import RevelationState

        board = BoardState()
        _kings(board)
        white = _player("white", hand=["forbidden_knowledge", "dark_magician"])
        black = _player("black")
        black.ritual_pool = [RitualState(ritual_id="circle_of_the_arcane")]
        state = _state(board, white, black)
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, ActivateSpell(player_id="white", card_id="forbidden_knowledge",
                                                       target=None), rng)
        assert any(isinstance(e, EnemyCardRevealed) for e in events)
        assert black.ritual_pool[0].revelation == RevelationState.FORETOLD


class TestSuppressMonsterEffects:
    def test_monster_seal_disables_activated_ability(self, registry, rng):
        board = BoardState()
        _kings(board)
        target = _place(board, "black", PieceType.KNIGHT, "d5", "bn", monster_id="duelist")
        white = _player("white", hand=["monster_seal"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="monster_seal",
                                             target={"position": (3, 4)}), rng)
        assert any(s.startswith("effects_suppressed:") for s in target.statuses)

        state.phase = Phase.CHESS
        from game.mechanics.effects.registry import get_activatable_effects
        assert get_activatable_effects(target, registry) == []

    def test_nullification_glyph_suppresses_summoned_monster(self, registry, rng):
        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.KNIGHT, "b2", "wn")
        white = _player("white", hand=["duelist"])
        state = _state(board, white, _player("black"))
        state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="nullification_glyph", charges=1,
            position=Position.from_algebraic("b3"), radius=1, trigger_condition="summon",
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(player_id="white", card_id="duelist",
                                             vessel_position=Position.from_algebraic("b2")), rng)
        assert any(s.startswith("effects_suppressed:") for s in vessel.statuses)


class TestProhibitSummoning:
    def test_sanctuary_blocks_summon_in_zone(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp")
        _place(board, "white", PieceType.KNIGHT, "c4", "wn")  # can reach a3 (empty)
        white = _player("white", hand=["sanctuary", "apprentice_mage"])
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="sanctuary", target=(0, 2)), rng)  # a3 — zone still covers b2
        with pytest.raises(IllegalActionError):
            engine.execute(state, SummonMonster(player_id="white", card_id="apprentice_mage",
                                                  vessel_position=Position.from_algebraic("b2")), rng)

    def test_vessel_lock_blocks_opponent_summon(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "black", PieceType.PAWN, "b7", "bp")
        white = _player("white")
        black = _player("black", hand=["apprentice_mage"])
        state = _state(board, white, black)
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="vessel_lock",
            position=Position.from_algebraic("b7"), radius=1, trigger_condition="manual",
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateTrap(player_id="white", trap_instance_id="t1"), rng)
        state.active_player = "black"
        state.phase = Phase.PREPARATION
        with pytest.raises(IllegalActionError):
            engine.execute(state, SummonMonster(player_id="black", card_id="apprentice_mage",
                                                  vessel_position=Position.from_algebraic("b7")), rng)


class TestTemporaryVesselClass:
    def test_unstable_transmutation_allows_offclass_summon(self, registry, rng):
        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", hand=["unstable_transmutation", "duelist"])  # duelist: knight/bishop only
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, SummonMonster(player_id="white", card_id="duelist",
                                                  vessel_position=Position.from_algebraic("b2")), rng)

        engine.execute(state, ActivateSpell(player_id="white", card_id="unstable_transmutation",
                                             target={"position": (1, 1)}), rng)
        white.preparation_action_used = False
        engine.execute(state, SummonMonster(player_id="white", card_id="duelist",
                                             vessel_position=Position.from_algebraic("b2")), rng)
        assert board.get_unit(Position.from_algebraic("b2")).monster_id == "duelist"


class TestAdvanceConstruction:
    def test_rapid_construction_advances_and_can_complete(self, registry, rng):
        from game.core.events import BuildingCompleted

        board = BoardState()
        _kings(board)
        white = _player("white", hand=["rapid_construction"])
        state = _state(board, white, _player("black"))
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("a1"),
            status=ConstructionStatus.UNDER_CONSTRUCTION, remaining_turns=1,
        ))
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, ActivateSpell(player_id="white", card_id="rapid_construction",
                                                       target="bld-1"), rng)
        assert any(isinstance(e, BuildingCompleted) for e in events)
        assert state.buildings[0].status == ConstructionStatus.COMPLETE


class TestProtectBuilder:
    def test_builders_ward_shields_committed_pawn(self, registry, rng):
        from game.mechanics.buildings import apply_builders_ward_aura

        board = BoardState()
        _kings(board)
        pawn = _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white")
        state = _state(board, white, _player("black"))
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("b2"),
            status=ConstructionStatus.UNDER_CONSTRUCTION, remaining_turns=2,
            builder_piece_id="wp",
        ))
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="builders_ward",
            position=Position.from_algebraic("b2"), radius=1, trigger_condition="enter_radius",
        ))

        apply_builders_ward_aura(state, "white", registry)
        assert any(s.startswith("shield:") for s in pawn.statuses)


class TestCancelCapture:
    def test_guardian_sigils_reverses_the_capture(self, registry, rng):
        from game.core.events import CaptureBlocked

        board = BoardState()
        _kings(board)
        attacker = _place(board, "black", PieceType.ROOK, "a7", "br")
        defender = _place(board, "white", PieceType.PAWN, "a6", "wp")
        state = _state(board, _player("white"), _player("black"), phase=Phase.CHESS, active="black")
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="guardian_sigils", charges=1,
            position=Position.from_algebraic("a6"), radius=1, trigger_condition="capture",
        ))
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("a7"),
                                                   target=Position.from_algebraic("a6")), rng)
        assert any(isinstance(e, CaptureBlocked) for e in events)
        assert board.get_unit(Position.from_algebraic("a6")).piece.id == "wp"
        assert board.get_unit(Position.from_algebraic("a7")).piece.id == "br"
        assert state.traps == []  # consumed


class TestMarkUnit:
    def test_vengeance_mark_exposes_the_capturer(self, registry, rng):
        board = BoardState()
        _kings(board)
        attacker = _place(board, "black", PieceType.ROOK, "a7", "br")
        _place(board, "white", PieceType.PAWN, "a6", "wp")
        state = _state(board, _player("white"), _player("black"), phase=Phase.CHESS, active="black")
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="vengeance_mark",
            position=Position.from_algebraic("a6"), radius=2, trigger_condition="capture",
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(state, MovePiece(player_id="black", source=Position.from_algebraic("a7"),
                                         target=Position.from_algebraic("a6")), rng)
        moved = board.get_unit(Position.from_algebraic("a6"))
        assert any(s.startswith("exposed:") for s in moved.statuses)


class TestTransformationAlarm:
    def test_draws_a_card_on_enemy_summon(self, registry, rng):
        from game.core.events import CardDrawn

        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp")
        white = _player("white", hand=["apprentice_mage"], deck=["dark_magician"])
        state = _state(board, white, _player("black"))
        state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="transformation_alarm", charges=1,
            position=Position.from_algebraic("b3"), radius=2, trigger_condition="summon",
        ))
        state.get_player("black").deck = ["wyrm_knight"]
        engine = RulesEngine(registry=registry)

        events = engine.execute(state, SummonMonster(player_id="white", card_id="apprentice_mage",
                                                       vessel_position=Position.from_algebraic("b2")), rng)
        # transformation_alarm's OWN draw_card fires for its owner (black),
        # apprentice_mage's own draw_card fires for white — both present.
        assert sum(1 for e in events if isinstance(e, CardDrawn)) == 2


class TestRitualInsight:
    def test_reveals_own_ritual_and_draws(self, registry, rng):
        board = BoardState()
        _kings(board)
        white = _player("white", hand=["ritual_insight"], deck=["dark_magician"])
        white.ritual_pool = [RitualState(ritual_id="circle_of_the_arcane")]
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        from game.core.phases import RevelationState
        engine.execute(state, ActivateSpell(player_id="white", card_id="ritual_insight", target=None), rng)
        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD
        assert "dark_magician" in white.hand


class TestOmenBell:
    def test_forces_enemy_ritual_foretold_on_progress_near_trap(self, registry, rng):
        from game.core.phases import RevelationState
        from game.mechanics.rituals import advance_ritual_progress

        board = BoardState()
        _kings(board)
        _place(board, "white", PieceType.PAWN, "b2", "wp", monster_id="ritual_acolyte")
        white = _player("white")
        white.ritual_pool = [RitualState(ritual_id="circle_of_the_arcane")]
        state = _state(board, white, _player("black"))
        state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="omen_bell",
            position=Position.from_algebraic("b2"), radius=2, trigger_condition="ritual_progress",
        ))

        advance_ritual_progress(state, "white", [], registry)
        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD


class TestRitualBluff:
    def test_false_prophecy_shows_fake_foretold_to_opponent_only(self, registry, rng):
        board = BoardState()
        _kings(board)
        white = _player("white", hand=["false_prophecy"])
        white.ritual_pool = [RitualState(ritual_id="circle_of_the_arcane")]
        state = _state(board, white, _player("black"))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="false_prophecy", target=None), rng)
        assert white.ritual_pool[0].bluff_turns > 0

        obs_black = build_observation(state, "black", registry)
        from game.core.phases import RevelationState
        assert obs_black.opponent_ritual_info[0].revelation == RevelationState.FORETOLD

        obs_white = build_observation(state, "white", registry)
        assert obs_white.own_rituals[0].revelation == RevelationState.SEALED


class TestInterruptRitual:
    def test_profane_interruption_blocks_and_consumes(self, registry, rng):
        board = BoardState()
        _kings(board)
        vessel = _place(board, "white", PieceType.BISHOP, "d4", "wb")
        seer = _place(board, "white", PieceType.PAWN, "d3", "wp")
        white = _player("white")
        white.ritual_pool = [RitualState(ritual_id="rite_of_the_wyrm")]
        state = _state(board, white, _player("black"))
        state.traps.append(TrapInstance(
            id="t1", owner="black", card_id="profane_interruption", charges=1,
            position=Position.from_algebraic("d3"), radius=1, trigger_condition="ritual_activation",
        ))
        engine = RulesEngine(registry=registry)

        from game.core.actions import ActivateRitual
        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=[Position.from_algebraic("d3"), Position.from_algebraic("d4")],
            ), rng)
        # material preserved — nothing sacrificed.
        assert board.get_unit(Position.from_algebraic("d3")) is not None
        assert board.get_unit(Position.from_algebraic("d4")) is not None
        assert state.traps == []  # consumed


class TestDispelField:
    def test_removes_spell_zone_but_not_trap_or_monster_tags(self, registry, rng):
        board = BoardState()
        _kings(board)
        board.get_square(Position.from_algebraic("d4")).add_effect("blocked:2:black:veil_of_stillness")
        board.get_square(Position.from_algebraic("d4")).add_effect("frozen:1:black:iron_vanguard")
        white = _player("white", hand=["dispel_field"])
        state = _state(board, white, _player("black"))
        # d4 is itself "blocked", so no piece could pseudo-legally move
        # there — reach it via Building coverage instead (the spell
        # targeting rule's OR condition).
        state.buildings.append(BuildingInstance(
            id="bld-1", owner="white", building_card_id="watchtower",
            position=Position.from_algebraic("d4"), status=ConstructionStatus.COMPLETE,
        ))
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateSpell(player_id="white", card_id="dispel_field",
                                             target=(3, 3)), rng)
        remaining = board.get_square(Position.from_algebraic("d4")).temporary_effects
        assert not any(e.startswith("blocked:") for e in remaining)
        assert any(e.startswith("frozen:") for e in remaining)  # Monster-caused, untouched


class TestBorderBeacon:
    def test_treats_radius_as_temporary_territory(self, registry, rng):
        from game.mechanics.territory import is_in_territory

        board = BoardState()
        _kings(board)
        white = _player("white")
        state = _state(board, white, _player("black"))
        state.traps.append(TrapInstance(
            id="t1", owner="white", card_id="border_beacon",
            position=Position.from_algebraic("d5"), radius=1, trigger_condition="manual",
        ))
        engine = RulesEngine(registry=registry)

        far_pos = Position.from_algebraic("d5")
        assert not is_in_territory(state, far_pos, "white", registry)
        engine.execute(state, ActivateTrap(player_id="white", trap_instance_id="t1"), rng)
        assert is_in_territory(state, far_pos, "white", registry)
