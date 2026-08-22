"""
AI Stage 4 — determinization and MonteCarloBot (README §49)
=============================================================

Covers:
    • A sampled world is *consistent*: it never contains a card the
      Observation already accounts for, it respects a Ritual's revelation
      state, and it is the right size.
    • A sampled world is *only a guess*: nothing hidden leaks into it, and
      it stays inside README §44 in a real game loop.
    • The board model for preparation actions (Summon, Dismiss, Ritual) is
      exactly reversible — worlds are searched on one shared board.
    • Sampling changes decisions: the bot summons where a Stage 1 bias
      cannot see, and prices a Ritual by what the board looks like after it.
    • Budgets, the PlayerController contract, reproducibility, harness and
      UI wiring.
"""

from __future__ import annotations

import pytest

from game.ai.controller import PlayerController
from game.ai.determinize import NULL_WORLD, Determinization, Determinizer
from game.ai.monte_carlo import (
    MonteCarloBot,
    MonteCarloLimits,
    MonteCarloStats,
    SampledBias,
)
from game.ai.search import Hazards, SearchLimits, Searcher, board_for_search
from game.ai.search_bot import SearchBot
from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateRitual,
    DismissMonster,
    EndPreparation,
    EndTurn,
    MovePiece,
    SummonMonster,
)
from game.core.game import Game
from game.core.observation import build_observation
from game.core.phases import KingCardStatus, Phase, PieceType, RevelationState
from game.core.state import GameState, RitualState, TrapInstance


P = Position.from_algebraic


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _obs(state: GameState, player: str = "white", registry=None):
    return build_observation(state, player, registry=registry)


def _put(state: GameState, square: str, owner: str, piece_type: str,
         monster_id: str | None = None) -> None:
    piece = ChessPiece(
        id=f"{owner}-{piece_type}-{square}",
        owner=owner,
        piece_type=PieceType(piece_type),
    )
    state.board.place_unit(P(square), UnitInstance(piece=piece, monster_id=monster_id))


def _clear(state: GameState) -> None:
    state.board = BoardState()


def _bot(seed: int = 0, player: str = "white", registry=None, **mc) -> MonteCarloBot:
    """A Stage 4 bot on a test-sized budget unless the caller says otherwise."""
    mc.setdefault("samples", 4)
    mc.setdefault("chess_samples", 4)
    mc.setdefault("chess_depth", 2)
    mc.setdefault("max_seconds", 2.0)
    bot = MonteCarloBot(
        seed=seed,
        registry=registry,
        limits=SearchLimits(max_depth=2, max_nodes=300, max_seconds=1.0),
        mc_limits=MonteCarloLimits(**mc),
    )
    bot.player_id = player
    return bot


def _searcher(state: GameState, player: str = "white", registry=None) -> Searcher:
    obs = _obs(state, player, registry=registry)
    return Searcher(
        board=board_for_search(obs),
        me=player,
        registry=registry,
        hazards=Hazards.from_observation(obs),
        monsters_expected=True,
    )


def _snapshot(searcher: Searcher) -> list[tuple]:
    """A comparable picture of everything the board model can change."""
    return sorted(
        (pos.file, pos.rank, unit.owner, unit.piece.piece_type.value,
         unit.monster_id or "")
        for pos, unit in searcher.board.all_units()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Determinization — consistency
# ─────────────────────────────────────────────────────────────────────────────

class TestSampledWorldsAreConsistent:
    def test_the_sampled_hand_is_the_size_the_observation_reports(
        self, game_state: GameState, registry
    ):
        obs = _obs(game_state, "white", registry=registry)
        world = Determinizer(obs, registry).sample()
        assert len(world.opponent_hand) == obs.opponent_hand_count

    def test_the_sampled_hand_holds_distinct_cards(
        self, game_state: GameState, registry
    ):
        obs = _obs(game_state, "white", registry=registry)
        world = Determinizer(obs, registry).sample()
        assert len(set(world.opponent_hand)) == len(world.opponent_hand)

    def test_a_card_in_the_opponent_graveyard_is_never_sampled(
        self, game_state: GameState, registry
    ):
        game_state.get_player("black").graveyard = ["wyrm_knight", "stone_golem"]
        obs = _obs(game_state, "white", registry=registry)
        determinizer = Determinizer(obs, registry)
        assert "wyrm_knight" not in determinizer.hand_pool
        assert "stone_golem" not in determinizer.hand_pool

    def test_a_monster_standing_on_an_opponent_unit_is_never_sampled(
        self, game_state: GameState, registry
    ):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d7", "black", "pawn", monster_id="wyrm_knight")
        obs = _obs(game_state, "white", registry=registry)
        assert "wyrm_knight" not in Determinizer(obs, registry).hand_pool

    def test_a_trap_the_opponent_has_placed_is_never_sampled(
        self, game_state: GameState, registry
    ):
        game_state.traps.append(TrapInstance(
            id="trap-black-001", owner="black", card_id="pit_trap",
            position=P("d4"), radius=1, trigger_condition="enter_radius",
        ))
        obs = _obs(game_state, "white", registry=registry)
        assert "pit_trap" not in Determinizer(obs, registry).hand_pool

    def test_the_observers_own_cards_stay_in_the_pool(
        self, game_state: GameState, registry
    ):
        """
        Each deck is an independent sample from the shared pool
        (``Game._build_deck_from_registry``), so both players can hold the
        same card.  Removing the observer's own cards would be a *wrong*
        constraint, not a conservative one.
        """
        game_state.get_player("white").hand = ["wyrm_knight"]
        game_state.get_player("white").graveyard = ["stone_golem"]
        obs = _obs(game_state, "white", registry=registry)
        pool = Determinizer(obs, registry).hand_pool
        assert "wyrm_knight" in pool
        assert "stone_golem" in pool

    def test_ritual_only_monsters_are_never_in_a_hand(
        self, game_state: GameState, registry
    ):
        obs = _obs(game_state, "white", registry=registry)
        pool = set(Determinizer(obs, registry).hand_pool)
        ritual_only = {c.id for c in registry.all_monsters() if c.ritual_only}
        assert ritual_only
        assert not (pool & ritual_only)


class TestSampledRitualsRespectRevelation:
    def test_a_revealed_ritual_is_taken_as_it_stands(
        self, game_state: GameState, registry
    ):
        target = registry.all_rituals()[0]
        game_state.get_player("black").ritual_pool = [
            RitualState(ritual_id=target.id, revelation=RevelationState.REVEALED)
        ]
        obs = _obs(game_state, "white", registry=registry)
        determinizer = Determinizer(obs, registry)
        for _ in range(8):
            assert determinizer.sample().opponent_rituals == (target.id,)

    def test_a_foretold_ritual_is_narrowed_to_what_it_leaked(
        self, game_state: GameState, registry
    ):
        target = registry.all_rituals()[0]
        game_state.get_player("black").ritual_pool = [
            RitualState(ritual_id=target.id, revelation=RevelationState.FORETOLD)
        ]
        obs = _obs(game_state, "white", registry=registry)
        determinizer = Determinizer(obs, registry)
        for _ in range(12):
            (sampled,) = determinizer.sample().opponent_rituals
            card = registry.get(sampled)
            assert card.archetype == target.archetype
            assert card.required_vessel == target.required_vessel

    def test_a_sealed_ritual_is_open_but_the_pool_stays_distinct(
        self, game_state: GameState, registry
    ):
        game_state.get_player("black").ritual_pool = [
            RitualState(ritual_id=r.id) for r in registry.all_rituals()[:3]
        ]
        obs = _obs(game_state, "white", registry=registry)
        determinizer = Determinizer(obs, registry)
        for _ in range(12):
            sampled = determinizer.sample().opponent_rituals
            assert len(sampled) == 3
            assert len(set(sampled)) == 3


class TestSampledKings:
    def test_a_face_up_king_slot_is_taken_as_it_stands(
        self, game_state: GameState, registry
    ):
        known = registry.all_kings()[0].id
        pool = game_state.get_player("black").king_pool
        pool[0].king_card_id = known
        pool[0].status = KingCardStatus.ACTIVE
        obs = _obs(game_state, "white", registry=registry)
        for _ in range(6):
            assert Determinizer(obs, registry).sample().opponent_kings[0] == known

    def test_hidden_king_slots_are_sampled_distinctly_from_the_roster(
        self, game_state: GameState, registry
    ):
        obs = _obs(game_state, "white", registry=registry)
        roster = {k.id for k in registry.all_kings()}
        determinizer = Determinizer(obs, registry)
        for _ in range(10):
            kings = determinizer.sample().opponent_kings
            assert len(kings) == len(obs.opponent_king_info)
            assert len(set(kings)) == len(kings)
            assert set(kings) <= roster


class TestDeterminizerEdges:
    def test_no_registry_means_nothing_to_sample(self, game_state: GameState):
        obs = _obs(game_state, "white")
        determinizer = Determinizer(obs, None)
        assert determinizer.sample() is NULL_WORLD
        assert determinizer.is_informative is False

    def test_an_empty_opponent_hand_with_no_hidden_pools_is_uninformative(
        self, game_state: GameState, registry
    ):
        black = game_state.get_player("black")
        black.hand = []
        black.ritual_pool = []
        for slot in black.king_pool:
            slot.status = KingCardStatus.RETIRED
        obs = _obs(game_state, "white", registry=registry)
        assert Determinizer(obs, registry).is_informative is False

    def test_the_same_rng_seed_samples_the_same_world(
        self, game_state: GameState, registry
    ):
        import random

        obs = _obs(game_state, "white", registry=registry)
        first = Determinizer(obs, registry, random.Random(11)).sample()
        second = Determinizer(obs, registry, random.Random(11)).sample()
        assert first == second

    def test_different_seeds_disagree(self, game_state: GameState, registry):
        import random

        obs = _obs(game_state, "white", registry=registry)
        hands = {
            Determinizer(obs, registry, random.Random(s)).sample().opponent_hand
            for s in range(6)
        }
        assert len(hands) > 1

    def test_the_null_world_is_empty(self):
        assert NULL_WORLD.is_null
        assert not Determinization(opponent_hand=("wyrm_knight",)).is_null


# ─────────────────────────────────────────────────────────────────────────────
# The board model
# ─────────────────────────────────────────────────────────────────────────────

class TestBoardEdits:
    def test_setting_and_clearing_a_monster_is_reversible(
        self, game_state: GameState, registry
    ):
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        previous = searcher.set_monster(P("d2"), "wyrm_knight")
        assert searcher.board.get_unit(P("d2")).monster_id == "wyrm_knight"
        searcher.set_monster(P("d2"), previous)
        assert _snapshot(searcher) == before

    def test_lifting_and_restoring_a_unit_is_reversible(
        self, game_state: GameState, registry
    ):
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        lifted = searcher.lift(P("d2"))
        assert searcher.board.get_unit(P("d2")) is None
        assert lifted is not None
        searcher.restore(P("d2"), lifted)
        assert _snapshot(searcher) == before

    def test_a_lifted_square_leaves_the_move_generator_alone(
        self, game_state: GameState, registry
    ):
        """The occupancy map must track the board, or ``moves()`` invents pieces."""
        searcher = _searcher(game_state, "white", registry=registry)
        searcher.lift(P("d2"))
        assert all(src != P("d2") for _o, src, _d in searcher.moves("white"))

    def test_monsters_expected_turns_on_registry_aware_generation(
        self, game_state: GameState, registry
    ):
        obs = _obs(game_state, "white", registry=registry)
        plain = Searcher(board=board_for_search(obs), me="white", registry=registry)
        expectant = Searcher(
            board=board_for_search(obs), me="white", registry=registry,
            monsters_expected=True,
        )
        assert plain._move_registry is None
        assert expectant._move_registry is registry


class TestPreparationModel:
    def test_a_summon_puts_the_monster_on_the_vessel_and_takes_it_back(
        self, game_state: GameState, registry
    ):
        bot = _bot(registry=registry)
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        action = SummonMonster(
            player_id="white", card_id="wyrm_knight", vessel_position=P("d2")
        )
        token = bot._apply_preparation(searcher, action)
        assert searcher.board.get_unit(P("d2")).monster_id == "wyrm_knight"
        bot._undo_preparation(searcher, token)
        assert _snapshot(searcher) == before

    def test_a_dismiss_clears_the_monster_and_takes_it_back(
        self, game_state: GameState, registry
    ):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d2", "white", "pawn", monster_id="wyrm_knight")
        bot = _bot(registry=registry)
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        token = bot._apply_preparation(
            searcher, DismissMonster(player_id="white", unit_position=P("d2"))
        )
        assert searcher.board.get_unit(P("d2")).monster_id is None
        bot._undo_preparation(searcher, token)
        assert _snapshot(searcher) == before

    def test_a_ritual_eats_its_sacrifices_and_crowns_its_vessel(
        self, game_state: GameState, registry
    ):
        ritual = next(
            r for r in registry.all_rituals() if r.summon_monster_id
        )
        bot = _bot(registry=registry)
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        action = ActivateRitual(
            player_id="white",
            ritual_id=ritual.id,
            sacrifice_positions=[P("a2"), P("b2"), P("c2")],
        )
        token = bot._apply_preparation(searcher, action)
        assert searcher.board.get_unit(P("a2")) is None
        assert searcher.board.get_unit(P("b2")) is None
        assert searcher.board.get_unit(P("c2")).monster_id == ritual.summon_monster_id
        bot._undo_preparation(searcher, token)
        assert _snapshot(searcher) == before

    def test_an_unmodelled_action_changes_nothing(
        self, game_state: GameState, registry
    ):
        bot = _bot(registry=registry)
        searcher = _searcher(game_state, "white", registry=registry)
        before = _snapshot(searcher)
        assert bot._apply_preparation(
            searcher, EndPreparation(player_id="white")
        ) is None
        assert _snapshot(searcher) == before


class TestSampledSummons:
    def _obs_with_black_pawn(self, game_state: GameState, registry):
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d7", "black", "pawn")
        return _obs(game_state, "white", registry=registry)

    def test_the_sampled_hand_lands_on_a_vessel_it_supports(
        self, game_state: GameState, registry
    ):
        obs = self._obs_with_black_pawn(game_state, registry)
        pawn_monster = next(
            c for c in registry.all_monsters()
            if not c.ritual_only and c.supports_vessel("pawn")
        )
        bot = _bot(registry=registry)
        planned = bot._plan_summons(
            obs, Determinization(opponent_hand=(pawn_monster.id,))
        )
        assert planned == ((P("d7"), pawn_monster.id),)

    def test_a_monster_with_no_legal_vessel_is_not_summoned(
        self, game_state: GameState, registry
    ):
        obs = self._obs_with_black_pawn(game_state, registry)
        no_pawn_monster = next(
            c for c in registry.all_monsters()
            if not c.ritual_only and not c.supports_vessel("pawn")
        )
        bot = _bot(registry=registry)
        assert bot._plan_summons(
            obs, Determinization(opponent_hand=(no_pawn_monster.id,))
        ) == ()

    def test_summons_stay_inside_the_opponents_own_territory(
        self, game_state: GameState, registry
    ):
        """README §13 — the engine requires it, so a sampled world must too."""
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d4", "black", "pawn")     # no-man's land
        obs = _obs(game_state, "white", registry=registry)
        assert P("d4") not in set(obs.opponent_territory)
        pawn_monster = next(
            c for c in registry.all_monsters()
            if not c.ritual_only and c.supports_vessel("pawn")
        )
        bot = _bot(registry=registry)
        assert bot._plan_summons(
            obs, Determinization(opponent_hand=(pawn_monster.id,))
        ) == ()

    def test_only_one_monster_lands_per_world(
        self, game_state: GameState, registry
    ):
        """One major preparation action per turn (``rules`` §PREPARATION_LIMIT)."""
        obs = _obs(game_state, "white", registry=registry)
        pawn_monsters = [
            c.id for c in registry.all_monsters()
            if not c.ritual_only and c.supports_vessel("pawn")
        ][:4]
        assert len(pawn_monsters) >= 2
        bot = _bot(registry=registry)
        planned = bot._plan_summons(
            obs, Determinization(opponent_hand=tuple(pawn_monsters))
        )
        assert len(planned) == 1

    def test_the_sampled_summon_reaches_the_searched_board(
        self, game_state: GameState, registry
    ):
        import time

        obs = self._obs_with_black_pawn(game_state, registry)
        pawn_monster = next(
            c for c in registry.all_monsters()
            if not c.ritual_only and c.supports_vessel("pawn")
        )
        bot = _bot(registry=registry)
        world = bot._build_world(
            obs,
            Determinization(opponent_hand=(pawn_monster.id,)),
            depth=2,
            deadline=time.monotonic() + 5.0,
            monsters_expected=False,
        )
        assert world.searcher.board.get_unit(P("d7")).monster_id == pawn_monster.id

    def test_the_null_world_leaves_the_board_alone(
        self, game_state: GameState, registry
    ):
        import time

        obs = self._obs_with_black_pawn(game_state, registry)
        bot = _bot(registry=registry)
        world = bot._build_world(
            obs, NULL_WORLD, depth=2,
            deadline=time.monotonic() + 5.0, monsters_expected=False,
        )
        assert world.determinization is NULL_WORLD
        assert world.summons == ()
        assert world.searcher.board.get_unit(P("d7")).monster_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Sampling changes decisions
# ─────────────────────────────────────────────────────────────────────────────

class TestSamplingChangesTheAnswer:
    def test_it_still_takes_free_material(self, game_state: GameState, registry):
        """
        Sanity: whatever the sampler imagines, an undefended Queen is an
        undefended Queen.
        """
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d1", "white", "rook")
        _put(game_state, "d7", "black", "queen")
        obs = _obs(game_state, "white", registry=registry)
        actions = [
            MovePiece(player_id="white", source=P("d1"), target=P("d7")),
            MovePiece(player_id="white", source=P("d1"), target=P("d4")),
            EndTurn(player_id="white"),
        ]
        chosen = _bot(seed=1, registry=registry).choose_action(obs, actions)
        assert isinstance(chosen, MovePiece)
        assert chosen.target == P("d7")

    def _doomed_vessel(self, game_state: GameState, registry):
        """
        Two candidate Vessels in white's own Territory: a2 is quiet, h2 is
        on an open file in front of a Rook and cannot be saved — advancing
        it stays on the file, and nothing defends it.
        """
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "a2", "white", "pawn")     # quiet vessel
        _put(game_state, "h2", "white", "pawn")     # doomed vessel
        _put(game_state, "h7", "black", "rook")     # ... because of this
        obs = _obs(game_state, "white", registry=registry)
        card = next(
            c for c in registry.all_monsters()
            if not c.ritual_only and c.supports_vessel("pawn")
        )
        return (
            obs,
            SummonMonster(player_id="white", card_id=card.id, vessel_position=P("a2")),
            SummonMonster(player_id="white", card_id=card.id, vessel_position=P("h2")),
        )

    def test_a_doomed_vessel_is_refuted_by_the_search_not_by_a_discount(
        self, game_state: GameState, registry
    ):
        """
        Stage 1 prices a hanging Vessel with a flat discount.  Stage 4
        searches the position the Summon produces, so a Monster placed
        where it simply gets taken is worth *nothing* — the sampled value
        collapses back to the value of not summoning at all.
        """
        import time

        obs, safe, doomed = self._doomed_vessel(game_state, registry)
        bot = _bot(seed=2, registry=registry)
        # The null world alone settles this, so the assertion carries no
        # sampling noise.
        world = bot._build_world(
            obs, NULL_WORLD, depth=2,
            deadline=time.monotonic() + 10.0, monsters_expected=True,
        )
        passing = bot._pass_value(world, obs, 2)
        quiet = bot._value_in_world(safe, world, obs, 2)
        lost = bot._value_in_world(doomed, world, obs, 2)

        assert quiet > lost
        assert lost == pytest.approx(passing)

    def test_it_summons_onto_the_vessel_that_survives(
        self, game_state: GameState, registry
    ):
        obs, safe, doomed = self._doomed_vessel(game_state, registry)
        for seed in range(4):
            chosen = _bot(seed=seed, registry=registry, samples=6).choose_action(
                obs, [doomed, safe]
            )
            assert chosen is safe

    def test_the_bias_table_does_not_re_charge_for_what_was_searched(
        self, game_state: GameState, registry
    ):
        """
        The searched board already shows the Monster a Summon produces, so
        Stage 4's Summon bias must be small — reusing Stage 1's +1.2 would
        pay for the same Monster twice.
        """
        from game.ai.heuristic_bot import ActionBias

        assert abs(SampledBias.SUMMON) < ActionBias.SUMMON
        assert SampledBias.RITUAL < ActionBias.RITUAL


# ─────────────────────────────────────────────────────────────────────────────
# Budgets and instrumentation
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgets:
    def test_it_draws_the_worlds_it_was_asked_for(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        bot = _bot(seed=1, registry=registry, samples=8, max_seconds=30.0)
        bot.choose_action(
            game.get_observation("white"), game.get_legal_actions("white")
        )
        assert bot.last_sampling.worlds == 8
        assert not bot.last_sampling.aborted

    def test_a_starved_budget_still_answers(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        legal = game.get_legal_actions("white")
        bot = _bot(seed=1, registry=registry, samples=64, max_seconds=0.05)
        chosen = bot.choose_action(game.get_observation("white"), legal)
        assert any(chosen is a for a in legal)
        assert bot.last_sampling.worlds < 64

    def test_the_node_ceiling_is_respected(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        bot = _bot(
            seed=1, registry=registry, samples=64, max_nodes=1500, max_seconds=30.0
        )
        bot.choose_action(
            game.get_observation("white"), game.get_legal_actions("white")
        )
        assert bot.last_sampling.aborted
        assert 0 < bot.last_sampling.worlds < 64

    def test_a_world_is_given_the_whole_node_ceiling_not_a_slice(self, registry):
        """
        Regression: a world that runs out of budget half-way is discarded
        whole, so a per-world *slice* of the node ceiling starves every
        world at once and leaves the decision with nothing to average.
        """
        import time

        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        bot = _bot(seed=1, registry=registry, samples=32, max_nodes=5000)
        world = bot._build_world(
            game.get_observation("white"), NULL_WORLD, depth=2,
            deadline=time.monotonic() + 10.0, monsters_expected=False,
        )
        assert world.searcher.limits.max_nodes == 5000

    def test_the_interactive_budget_completes_whole_worlds(self, registry):
        """
        The pygame profile is the tightest budget the bot ships with; a
        decision taken under it has to come back with worlds it can average,
        not with an empty tally.
        """
        from game.ui.pygame_app import AppController

        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        bot = MonteCarloBot(
            seed=1, registry=registry, mc_limits=AppController._UI_MC_LIMITS
        )
        bot.player_id = "white"
        bot.choose_action(
            game.get_observation("white"), game.get_legal_actions("white")
        )
        assert bot.last_sampling.worlds > 0
        assert bot.last_sampling.evaluations > 0

    def test_a_hopeless_budget_falls_back_to_stage_two(self, game_state: GameState, registry):
        """
        No world survived, so the biases alone are not a ranking — Stage 2
        takes over, and an undefended Queen is still taken.
        """
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d1", "white", "rook")
        _put(game_state, "d7", "black", "queen")
        game_state.phase = Phase.CHESS
        obs = _obs(game_state, "white", registry=registry)
        actions = [
            MovePiece(player_id="white", source=P("d1"), target=P("d7")),
            MovePiece(player_id="white", source=P("d1"), target=P("d4")),
            EndTurn(player_id="white"),
        ]
        bot = _bot(seed=1, registry=registry, max_nodes=1, max_seconds=30.0)
        chosen = bot.choose_action(obs, actions)
        assert bot.last_sampling.worlds == 0
        assert isinstance(chosen, MovePiece) and chosen.target == P("d7")

    def test_the_root_fan_out_is_cut_before_any_world_is_drawn(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        legal = game.get_legal_actions("white")
        assert len(legal) > 6
        bot = _bot(seed=1, registry=registry, max_candidates=6, max_seconds=30.0)
        bot.choose_action(game.get_observation("white"), legal)
        assert bot.last_sampling.candidates == 6

    def test_the_stats_describe_the_decision(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        bot = _bot(seed=1, registry=registry, samples=6, max_seconds=30.0)
        bot.choose_action(
            game.get_observation("white"), game.get_legal_actions("white")
        )
        stats = bot.last_sampling
        assert isinstance(stats, MonteCarloStats)
        assert stats.worlds > 0
        assert stats.evaluations >= stats.worlds
        assert stats.elapsed > 0.0
        assert 0.0 <= stats.favorable <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Controller contract
# ─────────────────────────────────────────────────────────────────────────────

class TestControllerContract:
    def test_is_a_player_controller(self):
        assert issubclass(MonteCarloBot, PlayerController)
        assert issubclass(MonteCarloBot, SearchBot)

    def test_empty_legal_list_is_an_engine_bug(self, game_state: GameState):
        with pytest.raises(ValueError):
            _bot().choose_action(_obs(game_state), [])

    def test_a_forced_action_is_returned_untouched(self, game_state: GameState):
        only = EndTurn(player_id="white")
        assert _bot().choose_action(_obs(game_state), [only]) is only

    def test_returns_an_action_from_the_offered_list(self, registry):
        game = Game.new(seed=9, registry=registry)
        legal = game.get_legal_actions("white")
        chosen = _bot(registry=registry).choose_action(
            game.get_observation("white"), legal
        )
        assert any(chosen is a for a in legal)

    def test_the_same_seed_makes_the_same_choice(self, registry):
        game = Game.new(seed=9, registry=registry)
        game.advance_to_preparation()
        obs = game.get_observation("white")
        legal = game.get_legal_actions("white")
        first = _bot(seed=7, registry=registry, max_seconds=30.0).choose_action(
            obs, legal
        )
        second = _bot(seed=7, registry=registry, max_seconds=30.0).choose_action(
            obs, legal
        )
        assert first is second

    def test_without_a_registry_it_falls_back_to_stage_two(
        self, game_state: GameState
    ):
        """No card pool means no world to sample; Stage 2 still has a board."""
        _clear(game_state)
        _put(game_state, "e1", "white", "king")
        _put(game_state, "e8", "black", "king")
        _put(game_state, "d1", "white", "rook")
        _put(game_state, "d7", "black", "queen")
        game_state.phase = Phase.CHESS
        obs = _obs(game_state, "white")
        actions = [
            MovePiece(player_id="white", source=P("d1"), target=P("d7")),
            MovePiece(player_id="white", source=P("d1"), target=P("d4")),
        ]
        bot = _bot(seed=1)
        assert bot.choose_action(obs, actions).target == P("d7")
        assert bot.last_sampling.worlds == 0

    def test_an_unsampled_phase_falls_through_to_the_earlier_stages(
        self, game_state: GameState, registry
    ):
        """
        A forced discard has no board consequence and no hidden state worth
        guessing at, so Stage 4 must decide it exactly as Stage 2 does.
        """
        from game.core.actions import DiscardCard

        game_state.phase = Phase.DISCARD
        obs = _obs(game_state, "white", registry=registry)
        actions = [
            DiscardCard(player_id="white", card_id=cid)
            for cid in game_state.get_player("white").hand
        ]
        sampling_bot = _bot(seed=3, registry=registry)
        searching_bot = SearchBot(seed=3, registry=registry)
        searching_bot.player_id = "white"
        assert sampling_bot.choose_action(obs, actions) is \
            searching_bot.choose_action(obs, actions)

    def test_a_decision_no_world_could_change_is_handed_back(
        self, game_state: GameState, registry
    ):
        """
        Every candidate here is searched as the same null move, so the
        worlds cancel and the ranking is the bias table alone — Stage 1's
        answer at Stage 4 prices.  It must not spend a world on it.
        """
        from game.core.actions import AttackBuilding

        game_state.phase = Phase.CHESS
        obs = _obs(game_state, "white", registry=registry)
        actions = [
            EndTurn(player_id="white"),
            AttackBuilding(
                player_id="white", source=P("d2"), target=P("d5")
            ),
        ]
        bot = _bot(seed=1, registry=registry)
        chosen = bot.choose_action(obs, actions)
        assert any(chosen is a for a in actions)
        assert bot.last_sampling.worlds == 0

    def test_it_plays_a_real_game_without_crashing(self, registry):
        from game.sim import acting_player

        game = Game.new(seed=31, registry=registry)
        bots = {
            pid: _bot(
                seed=hash(pid) % 100, player=pid, registry=registry,
                samples=2, chess_samples=2, max_nodes=250, max_seconds=1.0,
            )
            for pid in ("white", "black")
        }

        for _ in range(30):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            action = bots[pid].choose_action(game.get_observation(pid), legal)
            assert any(action is a for a in legal)
            try:
                game.execute(action)
            except Exception:
                break


# ─────────────────────────────────────────────────────────────────────────────
# Harness and UI wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestWiring:
    def test_the_sim_factory_knows_the_monte_carlo_bot(self, registry):
        from game.sim import CONTROLLER_KINDS, make_controller

        assert "montecarlo" in CONTROLLER_KINDS
        bot = make_controller(
            "montecarlo", seed=3, player_id="black", registry=registry
        )
        assert isinstance(bot, MonteCarloBot)
        assert bot.player_id == "black"

    def test_the_sim_factory_honours_a_custom_sampling_budget(self, registry):
        from game.sim import make_controller

        limits = MonteCarloLimits(samples=3, depth=1)
        bot = make_controller(
            "montecarlo", seed=3, player_id="white", registry=registry,
            mc_limits=limits,
        )
        assert bot.mc_limits is limits

    def test_the_cli_accepts_the_sampling_flags(self):
        from game.sim import main

        with pytest.raises(SystemExit):
            main(["--white", "montecarlo", "--mc-samples", "4", "--help"])

    def test_the_ui_selector_offers_it(self):
        from game.ui.pygame_app import AppController

        roster = AppController._ROSTER
        montecarlo = next(
            (c for c in roster if c.mode == "ai" and c.kind == "montecarlo"), None
        )
        assert montecarlo is not None
        assert montecarlo.label == "MONTE CARLO AI"

    def test_the_ui_names_it_as_the_hard_difficulty(self):
        """
        Stage 4 is what the player picks when they want a hard game, and
        the picker has to say so — "Monte Carlo" does not read as *hard*.
        """
        from game.ui.pygame_app import AppController

        montecarlo = next(
            c for c in AppController._ROSTER
            if c.mode == "ai" and c.kind == "montecarlo"
        )
        assert montecarlo.tier == "Hard"
        # ... and it is the hardest tier offered: nothing after it in the
        # roster is another difficulty step, only play styles and the human.
        tiers = [c.tier for c in AppController._ROSTER if c.kind != "personality"]
        assert tiers == ["Idiot", "Easy", "Medium", "Hard", ""]

    def test_the_ui_budget_is_tighter_than_the_headless_default(self):
        from game.ui.pygame_app import AppController

        ui = AppController._UI_MC_LIMITS
        default = MonteCarloLimits()
        assert ui.max_seconds < default.max_seconds
        assert ui.chess_depth <= default.chess_depth
