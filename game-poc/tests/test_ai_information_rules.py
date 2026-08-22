"""
AI Information Rules — README §44
===================================

    "The AI must not receive the full internal GameState. It receives an
     Observation ... This prevents accidental AI cheating. This
     architectural rule should exist from the first implementation."

tests/test_observation.py already covers *what* the Observation hides.
This module covers the *architectural* half of §44 — the boundary itself:

    1. The GameState/Observation split is real: everything §44 lists as
       private is absent from the Observation object graph.
    2. Controllers are handed an Observation and a legal action list, and
       nothing else — enforced by the PlayerController signature.
    3. The Observation is inert: a controller cannot reach through it and
       mutate the engine's state.
    4. Every shipped bot obeys the contract in a real game loop.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError

import pytest

from game.ai.controller import PlayerController
from game.ai.heuristic_bot import HeuristicBot
from game.ai.random_bot import RandomBot
from game.ai.search import SearchLimits
from game.ai.search_bot import SearchBot
from game.core.game import Game
from game.core.observation import Observation, build_observation
from game.core.phases import KingCardStatus, Phase, RevelationState
from game.core.state import GameState


class _BudgetedSearchBot(SearchBot):
    """
    AI Stage 2 on a test-sized search budget.

    What is under test here is the §44 boundary, not playing strength, and
    every one of these cases drives a real game loop — so the default
    per-decision budget would spend the whole suite thinking.
    """

    def __init__(self, seed: int = 0, **kwargs) -> None:
        kwargs.setdefault(
            "limits", SearchLimits(max_depth=2, max_nodes=200, max_seconds=0.25)
        )
        super().__init__(seed=seed, **kwargs)


ALL_BOTS = [RandomBot, HeuristicBot, _BudgetedSearchBot]


@pytest.fixture
def game(registry) -> Game:
    return Game.new(seed=17, registry=registry)


# ─────────────────────────────────────────────────────────────────────────────
# 1. The private/public split
# ─────────────────────────────────────────────────────────────────────────────

class TestPrivateStateNeverCrossesTheBoundary:
    """README §44's GameState column: both hands, deck order, hidden
    Rituals, hidden Kings, RNG state, all private data."""

    def test_observation_carries_no_reference_to_the_game_state(self, game: Game):
        obs = game.get_observation("white")
        seen: set[int] = set()
        for value in _walk(obs, seen):
            assert not isinstance(value, GameState), (
                "an Observation reached the GameState — README §44 forbids it"
            )

    def test_opponent_deck_order_is_absent(self, game: Game):
        black_deck = list(game.state.get_player("black").deck)
        assert black_deck, "fixture precondition: black has a deck"
        obs = game.get_observation("white")
        text = repr(obs)
        # The *order* is what matters: no run of black's deck may appear.
        for i in range(len(black_deck) - 1):
            pair = f"{black_deck[i]!r}, {black_deck[i + 1]!r}"
            assert pair not in text

    def test_opponent_hand_ids_are_absent(self, game: Game):
        obs = game.get_observation("white")
        own = set(obs.own_hand)
        reachable = {v for v in _walk(obs, set()) if isinstance(v, str)}
        for card_id in game.state.get_player("black").hand:
            if card_id in own:
                continue  # both players can hold the same card ID
            assert card_id not in reachable

    def test_hidden_king_identities_are_absent(self, game: Game):
        white, black = (game.state.get_player(p) for p in ("white", "black"))
        # Both pools are drawn from the same King list, so a card white
        # legitimately knows about (its own) can coincide with a black
        # hidden one — those tell white nothing new.
        own = {k.king_card_id for k in white.king_pool}
        hidden = [
            k.king_card_id for k in black.king_pool
            if k.status == KingCardStatus.HIDDEN and k.king_card_id not in own
        ]
        assert hidden, "fixture precondition: black holds unshared hidden Kings"
        reachable = {v for v in _walk(game.get_observation("white"), set())
                     if isinstance(v, str)}
        for king_id in hidden:
            assert king_id not in reachable

    def test_sealed_ritual_identities_are_absent(self, game: Game):
        white, black = (game.state.get_player(p) for p in ("white", "black"))
        own = {r.ritual_id for r in white.ritual_pool}
        sealed = [
            r.ritual_id for r in black.ritual_pool
            if r.revelation == RevelationState.SEALED and r.ritual_id not in own
        ]
        assert sealed, "fixture precondition: black holds unshared sealed Rituals"
        reachable = {v for v in _walk(game.get_observation("white"), set())
                     if isinstance(v, str)}
        for ritual_id in sealed:
            assert ritual_id not in reachable

    def test_rng_state_is_absent(self, game: Game):
        from game.core.rng import DeterministicRNG

        for value in _walk(game.get_observation("white"), set()):
            assert not isinstance(value, DeterministicRNG)

    def test_each_side_sees_its_own_private_data_only(self, game: Game):
        white_obs = game.get_observation("white")
        black_obs = game.get_observation("black")
        assert set(white_obs.own_hand) == set(game.state.get_player("white").hand)
        assert set(black_obs.own_hand) == set(game.state.get_player("black").hand)
        assert white_obs.opponent_hand_count == len(game.state.get_player("black").hand)


# ─────────────────────────────────────────────────────────────────────────────
# 2. The controller contract
# ─────────────────────────────────────────────────────────────────────────────

class TestControllerContract:
    def test_choose_action_takes_an_observation_not_a_state(self):
        params = inspect.signature(PlayerController.choose_action).parameters
        assert list(params) == ["self", "observation", "legal_actions"]
        assert "state" not in params

    @pytest.mark.parametrize("bot_cls", ALL_BOTS)
    def test_bot_is_a_player_controller(self, bot_cls):
        assert issubclass(bot_cls, PlayerController)

    @pytest.mark.parametrize("bot_cls", ALL_BOTS)
    def test_bot_signature_matches_the_abc(self, bot_cls):
        params = inspect.signature(bot_cls.choose_action).parameters
        assert list(params) == ["self", "observation", "legal_actions"]

    @pytest.mark.parametrize("bot_cls", ALL_BOTS)
    def test_bot_returns_an_action_from_the_offered_list(self, bot_cls, game: Game):
        bot = bot_cls(seed=1)
        bot.player_id = "white"
        legal = game.get_legal_actions("white")
        chosen = bot.choose_action(game.get_observation("white"), legal)
        assert any(chosen is a for a in legal)

    @pytest.mark.parametrize("bot_cls", ALL_BOTS)
    def test_bot_cannot_read_the_state_from_a_stub(self, bot_cls, game: Game):
        """
        Hand the bot an Observation whose every attribute access is
        recorded, and assert it never asks for something an Observation
        does not have (which is how a bot would smuggle in a GameState).
        """
        obs = game.get_observation("white")
        legal = game.get_legal_actions("white")
        spy = _AttributeSpy(obs)
        bot = bot_cls(seed=1)
        bot.player_id = "white"
        bot.choose_action(spy, legal)  # type: ignore[arg-type]
        allowed = set(Observation.__dataclass_fields__)
        unexpected = {
            name for name in spy.accessed
            if name not in allowed and not name.startswith("__")
        }
        assert not unexpected, f"bot read non-Observation attributes: {unexpected}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. The Observation is inert
# ─────────────────────────────────────────────────────────────────────────────

class TestObservationIsReadOnly:
    def test_observation_is_frozen(self, game: Game):
        obs = game.get_observation("white")
        with pytest.raises(FrozenInstanceError):
            obs.own_hand = ()  # type: ignore[misc]

    def test_own_hand_is_a_tuple_not_the_live_list(self, game: Game):
        obs = game.get_observation("white")
        assert isinstance(obs.own_hand, tuple)
        assert obs.own_hand is not game.state.get_player("white").hand

    def test_king_pool_entries_are_detached_copies(self, game: Game):
        obs = game.get_observation("white")
        live = game.state.get_player("white").king_pool
        assert obs.own_king_pool[0] is not live[0]
        obs.own_king_pool[0].status = KingCardStatus.ACTIVE
        assert live[0].status == KingCardStatus.HIDDEN

    def test_ritual_entries_are_detached_copies(self, game: Game):
        obs = game.get_observation("white")
        live = game.state.get_player("white").ritual_pool
        assert obs.own_rituals[0] is not live[0]
        obs.own_rituals[0].activated = True
        obs.own_rituals[0].progress = 99
        assert live[0].activated is False
        assert live[0].progress == 0

    def test_building_pool_entries_are_detached_copies(self, game: Game):
        obs = game.get_observation("white")
        live = game.state.get_player("white").building_pool
        before = live[0].copies_available
        obs.own_building_pool[0].copies_available = 99
        assert live[0].copies_available == before

    def test_opponent_building_pool_entries_are_detached_copies(self, game: Game):
        obs = game.get_observation("white")
        live = game.state.get_player("black").building_pool
        before = live[0].copies_available
        obs.opponent_building_pool[0].copies_available = 99
        assert live[0].copies_available == before

    def test_board_instances_are_detached_copies(self, registry):
        from game.chess.pieces import Position
        from game.core.phases import ConstructionStatus
        from game.core.state import BuildingInstance, TrapInstance

        game = Game.new(seed=5, registry=registry)
        game.state.traps.append(TrapInstance(
            id="trap-white-001", owner="white", card_id="pit_trap",
            position=Position.from_algebraic("d4"), radius=1,
            trigger_condition="enter_radius", charges=1,
        ))
        game.state.buildings.append(BuildingInstance(
            id="bld-white-001", owner="white", building_card_id="fortress",
            position=Position.from_algebraic("c3"),
            status=ConstructionStatus.COMPLETE, integrity=2, max_integrity=2,
        ))

        obs = game.get_observation("white")
        obs.board.trap_locations[0].charges = 99
        obs.board.building_locations[0].integrity = 99
        assert game.state.traps[0].charges == 1
        assert game.state.buildings[0].integrity == 2

    def test_detached_copies_keep_their_values(self, game: Game):
        obs = game.get_observation("white")
        live = game.state.get_player("white").king_pool
        assert [k.king_card_id for k in obs.own_king_pool] == \
               [k.king_card_id for k in live]


# ─────────────────────────────────────────────────────────────────────────────
# 4. The boundary holds over a real game loop
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryHoldsInPlay:
    @pytest.mark.parametrize("bot_cls", ALL_BOTS)
    def test_bots_play_without_touching_the_state(self, bot_cls, registry):
        from game.sim import acting_player

        game = Game.new(seed=23, registry=registry)
        bots = {}
        for pid in ("white", "black"):
            bot = bot_cls(seed=hash(pid) % 1000)
            bot.player_id = pid
            bots[pid] = bot

        for _ in range(40):
            if game.is_over():
                break
            game.advance_to_preparation()
            pid = acting_player(game.state)
            legal = game.get_legal_actions(pid)
            if not legal:
                break
            obs = game.get_observation(pid)
            assert isinstance(obs, Observation)
            assert obs.player_id == pid
            # The bot only ever sees its own hand.
            assert set(obs.own_hand) == set(game.state.get_player(pid).hand)
            action = bots[pid].choose_action(obs, legal)
            assert any(action is a for a in legal)
            try:
                game.execute(action)
            except Exception:
                # A rejected action is an engine matter, not a privacy one.
                break

    def test_build_observation_is_the_only_translation_layer(self):
        """
        Game.get_observation must delegate to build_observation — no side
        door that assembles a richer view for a "trusted" caller.
        """
        source = inspect.getsource(Game.get_observation)
        assert "build_observation" in source
        assert build_observation is not None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _walk(value, seen: set[int], depth: int = 0):
    """Yield every object reachable from ``value`` (bounded, cycle-safe)."""
    if depth > 8 or id(value) in seen:
        return
    seen.add(id(value))
    yield value
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(k, seen, depth + 1)
            yield from _walk(v, seen, depth + 1)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _walk(item, seen, depth + 1)
        return
    for name in getattr(value, "__dataclass_fields__", ()):
        yield from _walk(getattr(value, name, None), seen, depth + 1)


class _AttributeSpy:
    """Proxy that records every attribute name a controller reads."""

    def __init__(self, target) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "accessed", set())

    def __getattr__(self, name: str):
        object.__getattribute__(self, "accessed").add(name)
        return getattr(object.__getattribute__(self, "_target"), name)
