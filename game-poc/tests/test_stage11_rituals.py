"""
test_stage11_rituals.py — Stage 11: Ritual Pool

Covers the Ritual system (README §14-§15):

    • data/rituals.yaml (13 Rituals, spanning every Monster archetype) +
      data/ritual_monsters.yaml (their summon targets, excluded from Main
      Deck sampling).
    • assign_random_ritual_pool: each player draws a random 3-of-13 subset,
      all SEALED — mirrors King Pool assignment.
    • The three condition families: formation, material, state — including
      the generic "<metric>_at_least_N" / "_at_most_N" / "_within_N"
      threshold-predicate dispatcher and the two positional state checks
      (vessel_inside_enemy_territory, enemy_king_within_N).
    • ActivateRitual: sacrifice + Vessel transformation + forced REVEALED.
    • Revelation triggers, each advancing exactly one step: being checked,
      losing the Queen, ritual_progress_boost (ritual_acolyte),
      ritual_reveal_tradeoff — both the on_summon (omen_reader) and
      once_per_turn (oracle_of_the_last_star) variants, reveal_hidden_info
      (raven_scout).
    • ritual_pattern_substitute (circle_keeper) and
      ritual_requirement_reduction (forbidden_priest).
    • PlayerState.monsters_lost_count — the lifetime counter feeding
      allied_monsters_destroyed_at_least_N.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import RitualCard, load_registry_from_yaml
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import ActivateMonsterAbility, ActivateRitual, EndTurn, SummonMonster
from game.core.events import RitualActivated, RitualRevelationChanged
from game.core.phases import ConstructionStatus, KingCardStatus, Phase, PieceType, RevelationState
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.core.state import GameState, KingCardState, PlayerState, RitualState
from game.mechanics.rituals import (
    advance_ritual_progress,
    assign_random_ritual_pool,
    find_ritual_candidates,
    get_ritual_state,
    on_check_detected,
    on_piece_lost,
    promote_one_step,
    reveal_random_sealed,
    validate_ritual,
)

ALL_RITUAL_IDS = {
    "rite_of_the_wyrm", "circle_of_the_arcane", "vow_of_desperation",
    "pyre_of_dominion", "constellation_rite", "sevenfold_circle",
    "march_of_the_last_banner", "rite_of_stone_and_crown", "grand_works",
    "moonless_hunt", "throne_of_remains", "empty_throne_rite",
    "hunt_beneath_the_moon",
}
ALL_RITUAL_MONSTER_IDS = {
    "stormcall_wyrm", "archon_of_the_veil", "vengeance_warlord",
    "sovereign_of_embers", "oracle_of_the_last_star", "high_hierophant_of_the_circle",
    "bannerlord_eternal", "titan_of_the_foundation", "worldforge_colossus",
    "eclipse_executioner", "ossuary_king", "revenant_of_the_empty_throne",
    "primordial_moonbeast",
}

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


@pytest.fixture(scope="module")
def registry():
    return load_registry_from_yaml(_DATA_DIR)


@pytest.fixture
def rng() -> DeterministicRNG:
    return DeterministicRNG(seed=42)


def _make_player(pid: str, hand: list[str] | None = None, deck: list[str] | None = None) -> PlayerState:
    """
    Test helper — carries the FULL Ritual roster (not a random 3-of-N draw;
    see TestAssignRandomRitualPool for that) so any test can target any
    Ritual by id without extra setup.
    """
    ps = PlayerState(player_id=pid)
    ps.hand = hand or []
    ps.deck = deck or []
    ps.king_pool = [KingCardState(king_card_id=f"{pid}-king-a", status=KingCardStatus.HIDDEN)]
    ps.ritual_pool = [RitualState(ritual_id=rid) for rid in sorted(ALL_RITUAL_IDS)]
    return ps


def _place(board: BoardState, owner: str, ptype: PieceType, alg: str, pid: str) -> UnitInstance:
    piece = ChessPiece(id=pid, owner=owner, piece_type=ptype)
    unit = UnitInstance(piece=piece)
    board.place_unit(Position.from_algebraic(alg), unit)
    return unit


def _game_state(board: BoardState, white: PlayerState, black: PlayerState, active: str = "white", phase: Phase = Phase.PREPARATION) -> GameState:
    return GameState(
        game_id="test-stage11",
        turn_number=1,
        active_player=active,
        phase=phase,
        board=board,
        players={"white": white, "black": black},
        rng_seed=42,
    )


# ─────────────────────────────────────────────────────────────────────────────
# rituals.yaml / ritual_monsters.yaml sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestRitualsData:
    def test_thirteen_rituals_loaded(self, registry):
        rituals = registry.all_rituals()
        assert len(rituals) == 13
        assert all(isinstance(r, RitualCard) for r in rituals)
        assert {r.id for r in rituals} == ALL_RITUAL_IDS

    def test_every_archetype_has_at_least_one_ritual(self, registry):
        archetypes = {r.archetype for r in registry.all_rituals()}
        assert archetypes == {
            "dragon", "spellcaster", "warrior", "beast",
            "kingdom", "ritualist", "assassin", "necromancer",
        }

    def test_ritual_monsters_are_ritual_only_and_not_in_main_yaml(self, registry):
        for mid in ALL_RITUAL_MONSTER_IDS:
            card = registry.get(mid)
            assert card.ritual_only is True

        main_yaml_ids = {m.id for m in registry.all_monsters()} - ALL_RITUAL_MONSTER_IDS
        assert "dark_magician" in main_yaml_ids  # sanity: normal roster still there

    def test_every_ritual_summons_a_ritual_only_monster(self, registry):
        for ritual in registry.all_rituals():
            summoned = registry.get(ritual.summon_monster_id)
            assert summoned.ritual_only is True

    def test_ritual_only_monsters_excluded_from_deck_sampling(self, registry, rng):
        from game.core.game import Game
        deck = Game._build_deck_from_registry(registry, rng)
        assert not (set(deck) & ALL_RITUAL_MONSTER_IDS)


# ─────────────────────────────────────────────────────────────────────────────
# assign_random_ritual_pool
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRandomRitualPool:
    def test_pool_has_three_distinct_rituals_from_the_roster(self, registry, rng):
        ps = PlayerState(player_id="white")
        assign_random_ritual_pool(ps, registry, rng)
        ids = [rs.ritual_id for rs in ps.ritual_pool]
        assert len(ids) == 3
        assert len(set(ids)) == 3
        assert set(ids) <= ALL_RITUAL_IDS
        assert all(rs.revelation == RevelationState.SEALED for rs in ps.ritual_pool)

    def test_deterministic_given_seed(self, registry):
        ps1 = PlayerState(player_id="white")
        assign_random_ritual_pool(ps1, registry, DeterministicRNG(seed=7))
        ps2 = PlayerState(player_id="white")
        assign_random_ritual_pool(ps2, registry, DeterministicRNG(seed=7))
        assert [rs.ritual_id for rs in ps1.ritual_pool] == [rs.ritual_id for rs in ps2.ritual_pool]

    def test_different_seeds_can_draw_different_pools(self, registry):
        pools = set()
        for seed in range(10):
            ps = PlayerState(player_id="white")
            assign_random_ritual_pool(ps, registry, DeterministicRNG(seed=seed))
            pools.add(frozenset(rs.ritual_id for rs in ps.ritual_pool))
        assert len(pools) > 1, "expected some variety across 10 different seeds"

    def test_no_registry_leaves_pool_untouched(self, rng):
        ps = _make_player("white")
        original = list(ps.ritual_pool)
        assign_random_ritual_pool(ps, None, rng)
        assert ps.ritual_pool == original


# ─────────────────────────────────────────────────────────────────────────────
# Formation Ritual — rite_of_the_wyrm
# ─────────────────────────────────────────────────────────────────────────────

class TestFormationRitual:
    def _setup(self):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")
        _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
        _place(board, "white", PieceType.KNIGHT, "c2", "white-knight-1")
        white = _make_player("white")
        black = _make_player("black")
        return _game_state(board, white, black)

    def test_full_formation_activates(self, registry, rng):
        state = self._setup()
        engine = RulesEngine(registry=registry)
        positions = [Position.from_algebraic("b1"), Position.from_algebraic("c2"), Position.from_algebraic("c1")]

        events = engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_the_wyrm", sacrifice_positions=positions,
        ), rng)

        assert any(isinstance(e, RitualActivated) for e in events)
        vessel = state.board.get_unit(Position.from_algebraic("c1"))
        assert vessel is not None
        assert vessel.monster_id == "stormcall_wyrm"
        assert state.board.get_unit(Position.from_algebraic("b1")) is None
        assert state.board.get_unit(Position.from_algebraic("c2")) is None

        rstate = get_ritual_state(state, "white", "rite_of_the_wyrm")
        assert rstate.activated is True
        assert rstate.revelation == RevelationState.REVEALED
        assert state.get_player("white").preparation_action_used

    def test_missing_piece_rejected(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")
        _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
        # no knight at c2
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=[Position.from_algebraic("b1"), Position.from_algebraic("c1")],
            ), rng)

    def test_wrong_vessel_type_rejected(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.ROOK, "c1", "white-rook-1")  # not a bishop
        _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
        _place(board, "white", PieceType.KNIGHT, "c2", "white-knight-1")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=[
                    Position.from_algebraic("b1"), Position.from_algebraic("c2"), Position.from_algebraic("c1"),
                ],
            ), rng)

    def test_find_ritual_candidates_matches_the_engine(self, registry):
        state = self._setup()
        ritual = registry.get("rite_of_the_wyrm")
        rstate = get_ritual_state(state, "white", "rite_of_the_wyrm")
        candidates = find_ritual_candidates(state, "white", ritual, rstate)
        assert len(candidates) == 1
        combo = candidates[0]
        assert combo[-1] == Position.from_algebraic("c1")
        assert set(combo[:-1]) == {Position.from_algebraic("b1"), Position.from_algebraic("c2")}

    def test_get_legal_actions_offers_activate_ritual(self, registry, rng):
        state = self._setup()
        engine = RulesEngine(registry=registry)
        legal = engine.get_legal_actions(state, "white", registry=registry)
        ritual_actions = [a for a in legal if isinstance(a, ActivateRitual) and a.ritual_id == "rite_of_the_wyrm"]
        assert len(ritual_actions) == 1

    def test_completed_ritual_cannot_be_repeated(self, registry, rng):
        state = self._setup()
        engine = RulesEngine(registry=registry)
        positions = [Position.from_algebraic("b1"), Position.from_algebraic("c2"), Position.from_algebraic("c1")]
        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_the_wyrm", sacrifice_positions=positions,
        ), rng)

        state.get_player("white").preparation_action_used = False
        with pytest.raises(IllegalActionError, match="already been completed"):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_the_wyrm",
                sacrifice_positions=[Position.from_algebraic("c1")],
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# Material Ritual — circle_of_the_arcane
# ─────────────────────────────────────────────────────────────────────────────

class TestMaterialRitual:
    def test_meets_threshold_activates(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")  # 3, vessel
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")      # 5
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-1")      # 1  → total 9
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="circle_of_the_arcane",
            sacrifice_positions=[
                Position.from_algebraic("a1"), Position.from_algebraic("a2"), Position.from_algebraic("c1"),
            ],
        ), rng)

        vessel = state.board.get_unit(Position.from_algebraic("c1"))
        assert vessel.monster_id == "archon_of_the_veil"

    def test_below_threshold_rejected(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")  # 3
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-1")      # 1 → total 4, short of 9
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="circle_of_the_arcane",
                sacrifice_positions=[Position.from_algebraic("a2"), Position.from_algebraic("c1")],
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# State Ritual — vow_of_desperation
# ─────────────────────────────────────────────────────────────────────────────

class TestStateRitual:
    def test_activates_once_queen_is_gone(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")
        white = _make_player("white")  # no Queen anywhere for white
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="vow_of_desperation",
            sacrifice_positions=[Position.from_algebraic("a1")],
        ), rng)

        vessel = state.board.get_unit(Position.from_algebraic("a1"))
        assert vessel.monster_id == "vengeance_warlord"

    def test_rejected_while_queen_lives(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="vow_of_desperation",
                sacrifice_positions=[Position.from_algebraic("a1")],
            ), rng)

    def test_on_piece_lost_reveals_queen_loser_ritual(self, registry):
        board = BoardState()
        white = _make_player("white")
        events = []
        on_piece_lost(GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.CHESS,
            board=board, players={"white": white, "black": _make_player("black")},
        ), "white", PieceType.QUEEN, events, registry)

        assert any(isinstance(e, RitualRevelationChanged) for e in events)
        promoted = white.ritual_pool[0]
        assert promoted.revelation == RevelationState.FORETOLD

    def test_on_piece_lost_ignores_non_queen(self, registry):
        board = BoardState()
        white = _make_player("white")
        events = []
        state = GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.CHESS,
            board=board, players={"white": white, "black": _make_player("black")},
        )
        on_piece_lost(state, "white", PieceType.PAWN, events, registry)
        assert events == []
        assert all(rs.revelation == RevelationState.SEALED for rs in white.ritual_pool)


# ─────────────────────────────────────────────────────────────────────────────
# Revelation triggers
# ─────────────────────────────────────────────────────────────────────────────

class TestRevelationTriggers:
    def test_promote_one_step_never_skips(self, registry):
        white = _make_player("white")
        board = BoardState()
        state = GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.CHESS,
            board=board, players={"white": white, "black": _make_player("black")},
        )
        events = []
        assert promote_one_step(state, "white", "rite_of_the_wyrm", events) is True
        assert get_ritual_state(state, "white", "rite_of_the_wyrm").revelation == RevelationState.FORETOLD
        events2 = []
        assert promote_one_step(state, "white", "rite_of_the_wyrm", events2) is True
        assert get_ritual_state(state, "white", "rite_of_the_wyrm").revelation == RevelationState.REVEALED
        events3 = []
        assert promote_one_step(state, "white", "rite_of_the_wyrm", events3) is False  # already REVEALED

    def test_check_detected_triggers_reveal(self, registry, rng):
        # King in the corner, Rook delivers check — forces CheckDetected,
        # which must promote white's first Ritual by one step.
        board = BoardState()
        _place(board, "black", PieceType.KING, "h8", "black-king")
        _place(board, "white", PieceType.KING, "a1", "white-king")
        _place(board, "white", PieceType.ROOK, "h1", "white-rook-1")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black, phase=Phase.CHESS)
        engine = RulesEngine(registry=registry)

        from game.core.actions import MovePiece
        engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("h1"), target=Position.from_algebraic("h7"),
        ), rng)

        assert black.ritual_pool[0].revelation == RevelationState.FORETOLD

    def test_advance_ritual_progress_promotes_at_threshold(self, registry):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "b1", "white-bishop-1")
        board.get_unit(Position.from_algebraic("b1")).monster_id = "ritual_acolyte"
        white = _make_player("white")
        state = GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.END,
            board=board, players={"white": white, "black": _make_player("black")},
        )

        events = []
        for _ in range(3):  # reveal_progress_threshold defaults to 3, amount 1/turn
            advance_ritual_progress(state, "white", events, registry)
        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD
        assert white.ritual_pool[0].progress == 0

    def test_reveal_random_sealed_picks_a_sealed_ritual(self, registry):
        white = _make_player("white")
        board = BoardState()
        state = GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.CHESS,
            board=board, players={"white": white, "black": _make_player("black")},
        )
        events = []
        picked = reveal_random_sealed(state, "white", events, rng=DeterministicRNG(seed=1))
        assert picked in ALL_RITUAL_IDS
        assert get_ritual_state(state, "white", picked).revelation == RevelationState.FORETOLD


# ─────────────────────────────────────────────────────────────────────────────
# Ritual-support Monster cards — on-summon integration
# ─────────────────────────────────────────────────────────────────────────────

class TestRitualSupportMonsters:
    def _setup(self, white_hand):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")
        white = _make_player("white", hand=white_hand, deck=["dark_magician", "apprentice_mage"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        return state

    def test_omen_reader_reveal_tradeoff_promotes_and_draws(self, registry, rng):
        state = self._setup(["omen_reader"])
        engine = RulesEngine(registry=registry)
        white = state.get_player("white")
        hand_before = len(white.hand)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="omen_reader", vessel_position=Position.from_algebraic("c1"),
        ), rng)

        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD
        assert len(white.hand) == hand_before - 1 + 1  # -1 (played) +1 (drawn)

    def test_raven_scout_reveals_enemy_ritual(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
        white = _make_player("white", hand=["raven_scout"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="raven_scout", vessel_position=Position.from_algebraic("b1"),
        ), rng)

        assert any(rs.revelation == RevelationState.FORETOLD for rs in black.ritual_pool)
        assert all(rs.revelation == RevelationState.SEALED for rs in white.ritual_pool)

    def test_circle_keeper_substitutes_in_formation(self, registry, rng):
        # circle_keeper stands where the Rite of the Wyrm wants a Knight,
        # but is itself hosted on a Rook — should still satisfy that node.
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")
        _place(board, "white", PieceType.PAWN, "b1", "white-pawn-1")
        _place(board, "white", PieceType.ROOK, "c2", "white-rook-1")
        white = _make_player("white", hand=["circle_keeper"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="circle_keeper", vessel_position=Position.from_algebraic("c2"),
        ), rng)
        assert "ritual_substitute" in state.board.get_unit(Position.from_algebraic("c2")).statuses

        ritual = registry.get("rite_of_the_wyrm")
        rstate = get_ritual_state(state, "white", "rite_of_the_wyrm")
        validate_ritual(state, "white", ritual, rstate, [
            Position.from_algebraic("b1"), Position.from_algebraic("c2"), Position.from_algebraic("c1"),
        ])  # must not raise

        state.get_player("white").preparation_action_used = False  # summoning circle_keeper used it
        events = engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_the_wyrm",
            sacrifice_positions=[
                Position.from_algebraic("b1"), Position.from_algebraic("c2"), Position.from_algebraic("c1"),
            ],
        ), rng)
        assert any(isinstance(e, RitualActivated) for e in events)

    def test_forbidden_priest_reduces_requirement(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "c1", "white-bishop-1")  # vessel, 3
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")      # 5 → total 8, short of 9
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")    # will host forbidden_priest
        white = _make_player("white", hand=["forbidden_priest"])
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, SummonMonster(
            player_id="white", card_id="forbidden_priest", vessel_position=Position.from_algebraic("d1"),
        ), rng)
        state.phase = Phase.CHESS

        engine.execute(state, ActivateMonsterAbility(
            player_id="white", unit_position=Position.from_algebraic("d1"),
            ability_id="ritual_requirement_reduction", target="circle_of_the_arcane",
        ), rng)

        rstate = get_ritual_state(state, "white", "circle_of_the_arcane")
        assert rstate.requirement_reduction == 1
        assert rstate.revelation == RevelationState.REVEALED

        ritual = registry.get("circle_of_the_arcane")
        # Previously rejected (test_below_threshold_rejected uses the same
        # 8-value combo against the un-reduced Ritual) — now legal.
        validate_ritual(state, "white", ritual, rstate, [
            Position.from_algebraic("a1"), Position.from_algebraic("c1"),
        ])  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# The 10 new Rituals (formation/material/state threshold + positional checks)
# ─────────────────────────────────────────────────────────────────────────────

class TestNewRituals:
    def test_pyre_of_dominion_material_and_sacrifice_floor(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")    # vessel, 5
        _place(board, "white", PieceType.ROOK, "h1", "white-rook-2")    # 5
        _place(board, "white", PieceType.KNIGHT, "b1", "white-knight-1")  # 3 → total 13, 3 sacrifices
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="pyre_of_dominion",
            sacrifice_positions=[
                Position.from_algebraic("h1"), Position.from_algebraic("b1"), Position.from_algebraic("a1"),
            ],
        ), rng)
        vessel = state.board.get_unit(Position.from_algebraic("a1"))
        assert vessel.monster_id == "sovereign_of_embers"

    def test_pyre_of_dominion_rejects_below_sacrifice_floor(self, registry, rng):
        # Meets the material threshold (14) with only 2 sacrifices — the
        # Ritual demands at least 3.
        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")   # vessel, 5
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")  # 9 → total 14, but only 2 pieces
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="pyre_of_dominion",
                sacrifice_positions=[Position.from_algebraic("d1"), Position.from_algebraic("a1")],
            ), rng)

    def test_constellation_rite_state(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")   # vessel
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-1")     # extra sacrifice
        white = _make_player("white", hand=["a", "b", "c", "d", "e"])
        # Simulate an already-REVEALED Ritual elsewhere in the pool.
        white.ritual_pool[0].revelation = RevelationState.REVEALED
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="constellation_rite",
            sacrifice_positions=[Position.from_algebraic("a2"), Position.from_algebraic("d1")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d1")).monster_id == "oracle_of_the_last_star"

    def test_constellation_rite_rejected_without_prior_reveal(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        _place(board, "white", PieceType.PAWN, "a2", "white-pawn-1")
        white = _make_player("white", hand=["a", "b", "c", "d", "e"])  # no REVEALED ritual yet
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="constellation_rite",
                sacrifice_positions=[Position.from_algebraic("a2"), Position.from_algebraic("d1")],
            ), rng)

    def test_sevenfold_circle_formation(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.BISHOP, "d4", "white-bishop-1")
        _place(board, "white", PieceType.PAWN, "c4", "white-pawn-1")
        _place(board, "white", PieceType.PAWN, "e4", "white-pawn-2")
        _place(board, "white", PieceType.PAWN, "d5", "white-pawn-3")
        _place(board, "white", PieceType.PAWN, "d3", "white-pawn-4")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="sevenfold_circle",
            sacrifice_positions=[
                Position.from_algebraic("c4"), Position.from_algebraic("e4"),
                Position.from_algebraic("d5"), Position.from_algebraic("d3"),
                Position.from_algebraic("d4"),
            ],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d4")).monster_id == "high_hierophant_of_the_circle"

    def test_march_of_the_last_banner_formation(self, registry, rng):
        # Formation per card image: knight at top, pawns flanking one rank
        # below, rook two ranks below (offsets [-1,-1],[+1,-1],[0,-2]).
        board = BoardState()
        _place(board, "white", PieceType.KNIGHT, "d4", "white-knight-1")
        _place(board, "white", PieceType.PAWN, "c3", "white-pawn-1")   # [-1,-1]
        _place(board, "white", PieceType.PAWN, "e3", "white-pawn-2")   # [+1,-1]
        _place(board, "white", PieceType.ROOK, "d2", "white-rook-1")   # [0,-2]
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="march_of_the_last_banner",
            sacrifice_positions=[
                Position.from_algebraic("c3"), Position.from_algebraic("e3"),
                Position.from_algebraic("d2"), Position.from_algebraic("d4"),
            ],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d4")).monster_id == "bannerlord_eternal"

    def test_rite_of_stone_and_crown_state(self, registry, rng):
        from game.core.state import BuildingInstance

        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")     # vessel
        _place(board, "white", PieceType.KNIGHT, "b1", "white-knight-1")  # extra sacrifice
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        # 2 COMPLETE Buildings; home Territory (ranks 1-3) alone already
        # covers 24 squares, well past the 6-square floor.
        state.buildings = [
            BuildingInstance(id="b1", owner="white", building_card_id="fortress",
                              position=Position.from_algebraic("a3"), status=ConstructionStatus.COMPLETE),
            BuildingInstance(id="b2", owner="white", building_card_id="shrine",
                              position=Position.from_algebraic("h3"), status=ConstructionStatus.COMPLETE),
        ]
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="rite_of_stone_and_crown",
            sacrifice_positions=[Position.from_algebraic("b1"), Position.from_algebraic("a1")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("a1")).monster_id == "titan_of_the_foundation"

    def test_rite_of_stone_and_crown_rejected_with_only_one_building(self, registry, rng):
        from game.core.state import BuildingInstance

        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")
        _place(board, "white", PieceType.KNIGHT, "b1", "white-knight-1")
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings = [
            BuildingInstance(id="b1", owner="white", building_card_id="fortress",
                              position=Position.from_algebraic("a3"), status=ConstructionStatus.COMPLETE),
        ]
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="rite_of_stone_and_crown",
                sacrifice_positions=[Position.from_algebraic("b1"), Position.from_algebraic("a1")],
            ), rng)

    def test_grand_works_state(self, registry, rng):
        from game.core.state import BuildingInstance

        board = BoardState()
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")      # vessel
        _place(board, "white", PieceType.KNIGHT, "b1", "white-knight-1")  # extra sacrifice
        built_1 = _place(board, "white", PieceType.PAWN, "a2", "white-pawn-built-1")
        built_2 = _place(board, "white", PieceType.PAWN, "b2", "white-pawn-built-2")
        built_1.builder_available = False
        built_2.builder_available = False
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        state.buildings = [
            BuildingInstance(id="b1", owner="white", building_card_id="fortress",
                              position=Position.from_algebraic("a3"), status=ConstructionStatus.COMPLETE),
            BuildingInstance(id="b2", owner="white", building_card_id="shrine",
                              position=Position.from_algebraic("h3"), status=ConstructionStatus.DESTROYED),
            BuildingInstance(id="b3", owner="white", building_card_id="watchtower",
                              position=Position.from_algebraic("c3"), status=ConstructionStatus.COMPLETE),
        ]
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="grand_works",
            sacrifice_positions=[Position.from_algebraic("b1"), Position.from_algebraic("a1")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("a1")).monster_id == "worldforge_colossus"

    def test_moonless_hunt_can_never_complete_yet(self, registry, rng):
        """
        enemy_royal_support_at_most_2 references the Final Duel's Royal
        Support tally, which doesn't exist until Stage 12 — this Ritual
        must always be rejected, even when the positional checks
        (vessel_inside_enemy_territory, enemy_king_within_3) hold.
        """
        board = BoardState()
        _place(board, "black", PieceType.KING, "d6", "black-king")
        _place(board, "white", PieceType.KNIGHT, "d5", "white-knight-1")  # deep in black territory, adjacent to King
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="moonless_hunt",
                sacrifice_positions=[Position.from_algebraic("d5")],
            ), rng)

    def test_throne_of_remains_state(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        white = _make_player("white", deck=[f"card-{i}" for i in range(8)])
        white.graveyard = [f"grave-{i}" for i in range(8)]
        white.monsters_lost_count = 3
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="throne_of_remains",
            sacrifice_positions=[Position.from_algebraic("d1")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d1")).monster_id == "ossuary_king"

    def test_empty_throne_rite_state(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KING, "e1", "white-king")
        _place(board, "white", PieceType.ROOK, "a1", "white-rook-1")  # vessel — no Queen anywhere
        white = _make_player("white")
        white.graveyard = [f"grave-{i}" for i in range(6)]
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="empty_throne_rite",
            sacrifice_positions=[Position.from_algebraic("a1")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("a1")).monster_id == "revenant_of_the_empty_throne"

    def test_hunt_beneath_the_moon_positional_state(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KNIGHT, "d6", "white-knight-1")  # rank 6 → inside black's home territory
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        engine.execute(state, ActivateRitual(
            player_id="white", ritual_id="hunt_beneath_the_moon",
            sacrifice_positions=[Position.from_algebraic("d6")],
        ), rng)
        assert state.board.get_unit(Position.from_algebraic("d6")).monster_id == "primordial_moonbeast"

    def test_hunt_beneath_the_moon_rejected_on_home_soil(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.KNIGHT, "d1", "white-knight-1")  # own home territory
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black)
        engine = RulesEngine(registry=registry)

        with pytest.raises(IllegalActionError):
            engine.execute(state, ActivateRitual(
                player_id="white", ritual_id="hunt_beneath_the_moon",
                sacrifice_positions=[Position.from_algebraic("d1")],
            ), rng)


# ─────────────────────────────────────────────────────────────────────────────
# PlayerState.monsters_lost_count + ritual_reveal_tradeoff's once_per_turn path
# ─────────────────────────────────────────────────────────────────────────────

class TestMonstersLostCountAndOncePerTurnReveal:
    def test_monster_destroyed_increments_owner_counter(self, registry, rng):
        board = BoardState()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        _place(board, "black", PieceType.PAWN, "d2", "black-pawn-1")
        board.get_unit(Position.from_algebraic("d2")).monster_id = "shadow_wolf"
        white = _make_player("white")
        black = _make_player("black")
        state = _game_state(board, white, black, phase=Phase.CHESS)
        engine = RulesEngine(registry=registry)

        from game.core.actions import MovePiece
        assert black.monsters_lost_count == 0
        engine.execute(state, MovePiece(
            player_id="white", source=Position.from_algebraic("d1"), target=Position.from_algebraic("d2"),
        ), rng)
        assert black.monsters_lost_count == 1

    def test_advance_ritual_reveal_tradeoff_once_per_turn(self, registry):
        board = BoardState()
        _place(board, "white", PieceType.QUEEN, "d1", "white-queen-1")
        board.get_unit(Position.from_algebraic("d1")).monster_id = "oracle_of_the_last_star"
        white = _make_player("white", deck=["dark_magician"])
        state = GameState(
            game_id="t", turn_number=1, active_player="white", phase=Phase.END,
            board=board, players={"white": white, "black": _make_player("black")},
        )

        events = []
        from game.mechanics.rituals import advance_ritual_reveal_tradeoff
        advance_ritual_reveal_tradeoff(state, "white", events, registry, rng=None)

        assert white.ritual_pool[0].revelation == RevelationState.FORETOLD
        assert "dark_magician" in white.hand
