"""
AI Personalities — README §51
================================

Covers:
    • All five README §51 play styles exist and are distinct.
    • The guardrails hold: a personality is a *tilt*, not a monomania.
      No evaluation category is ever zeroed, and the staple actions —
      Summon, Construction, Ritual, Spell, Trap, Coronation, Castle —
      keep a positive bias whatever the style asked for.
    • Each style actually tilts the way §51 describes it.
    • The Opportunist re-mixes from the board, and its mix never collapses
      onto a single style.
    • PersonalityBot obeys the PlayerController contract and plays a real
      game without crashing — including the card systems its style is
      *not* about.
"""

from __future__ import annotations

import pytest

from game.ai.controller import PlayerController
from game.ai.evaluation import DEFAULT_WEIGHTS, EvalContext, EvalWeights
from game.ai.heuristic_bot import ActionBias, HeuristicBot
from game.ai.personality import (
    ARCHITECT,
    ASSASSIN,
    CONQUEROR,
    DEFAULT_PERSONALITY,
    MIX_MAX_SHARE,
    MIX_MIN_SHARE,
    OPPORTUNIST,
    PERSONALITIES,
    PERSONALITY_KEYS,
    RITUALIST,
    STAPLE_BIASES,
    STAPLE_FLOOR,
    TILT_CEIL,
    TILT_FLOOR,
    ActionBiasSet,
    Personality,
    get_personality,
    opportunist_mix,
    weights_from_tilt,
)
from game.ai.personality_bot import PersonalityBot
from game.ai.search import SearchLimits
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import (
    ActivateRitual,
    EndPreparation,
    StartConstruction,
    SummonMonster,
)
from game.core.game import Game
from game.core.observation import build_observation
from game.core.phases import PieceType, RevelationState
from game.core.state import GameState
from game.sim import CONTROLLER_KINDS, make_controller, play_match


P = Position.from_algebraic

_FIXED = [p for p in PERSONALITIES.values() if not p.is_adaptive]
_FAST = SearchLimits(max_depth=2, max_nodes=800, max_seconds=0.2)


@pytest.fixture
def game(registry) -> Game:
    return Game.new(seed=23, registry=registry)


def _obs(state: GameState, player: str = "white", registry=None):
    return build_observation(state, player, registry=registry)


def _put(state: GameState, square: str, owner: str, piece_type: str) -> None:
    piece = ChessPiece(
        id=f"{owner}-{piece_type}-{square}",
        owner=owner,
        piece_type=PieceType(piece_type),
    )
    state.board.place_unit(P(square), UnitInstance(piece=piece))


# ─────────────────────────────────────────────────────────────────────────────
# The catalogue
# ─────────────────────────────────────────────────────────────────────────────

def test_readme_51_names_all_five_play_styles():
    assert PERSONALITY_KEYS == [
        "conqueror", "architect", "ritualist", "assassin", "opportunist",
    ]
    for key, style in PERSONALITIES.items():
        assert style.key == key
        assert style.name and style.blurb
        assert style.priorities, f"{key} has no README §51 priority table"


def test_personalities_are_distinct():
    """Two play styles that produce identical numbers are one play style."""
    seen: list[tuple] = []
    for style in _FIXED:
        w = style.weights()
        signature = tuple(getattr(w, f) for f in sorted(vars(DEFAULT_WEIGHTS)))
        assert signature not in seen, f"{style.key} duplicates another style"
        seen.append(signature)


def test_only_the_opportunist_is_adaptive():
    assert OPPORTUNIST.is_adaptive
    assert all(not p.is_adaptive for p in _FIXED)


def test_get_personality_accepts_keys_and_instances():
    assert get_personality("ritualist") is RITUALIST
    assert get_personality("RITUALIST") is RITUALIST
    assert get_personality(RITUALIST) is RITUALIST
    assert get_personality(DEFAULT_PERSONALITY) in PERSONALITIES.values()


def test_get_personality_rejects_nonsense():
    with pytest.raises(ValueError, match="Unknown personality"):
        get_personality("necromancer")


def test_tilt_tables_only_name_real_fields():
    """A typo in a tilt table would silently do nothing — so it raises."""
    for style in _FIXED:
        style.weights()          # would raise on an unknown EvalWeights field
        style.biases()           # ... or an unknown ActionBias field
    with pytest.raises(ValueError, match="unknown EvalWeights"):
        weights_from_tilt({"materail": 2.0})
    with pytest.raises(ValueError, match="unknown ActionBias"):
        ActionBiasSet({"SUMMONN": 2.0})


# ─────────────────────────────────────────────────────────────────────────────
# Guardrails — "a tilt, not a monomania"
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("style", _FIXED, ids=lambda s: s.key)
def test_no_evaluation_category_is_ever_zeroed(style: Personality):
    """
    Every §46 category stays inside [TILT_FLOOR, TILT_CEIL] × its default.

    This is what stops an Assassin that "values material lower" from
    ignoring a hanging Queen: lower is 0.6×, never 0×.
    """
    weights = style.weights()
    for name, default in vars(DEFAULT_WEIGHTS).items():
        value = getattr(weights, name)
        if isinstance(default, int):
            assert 1 <= value <= 4, f"{style.key}.{name} = {value}"
            continue
        ratio = value / default
        assert TILT_FLOOR - 1e-9 <= ratio <= TILT_CEIL + 1e-9, (
            f"{style.key}.{name} is {ratio:.2f}× default — outside the guardrails"
        )


@pytest.mark.parametrize("style", _FIXED, ids=lambda s: s.key)
def test_staple_actions_keep_a_positive_bias(style: Personality):
    """
    The user-facing promise: no play style stops playing the game.

    A Ritualist that never summons a Monster, or an Assassin that never
    builds, is a broken bot — so the staples are floored for everyone.
    """
    biases = style.biases()
    for name in STAPLE_BIASES:
        default = getattr(ActionBias, name)
        value = getattr(biases, name)
        assert value > 0.0, f"{style.key} gave up on {name}"
        assert value >= default * STAPLE_FLOOR - 1e-9, (
            f"{style.key}.{name} = {value:.2f}, below the {STAPLE_FLOOR}× floor"
        )


def test_the_staple_floor_actually_bites():
    """A style asking to zero a staple gets the floor, not the zero."""
    starved = ActionBiasSet({"SUMMON": 0.0, "CONSTRUCTION": 0.0, "RITUAL": 0.0})
    for name in ("SUMMON", "CONSTRUCTION", "RITUAL"):
        assert getattr(starved, name) == pytest.approx(
            getattr(ActionBias, name) * STAPLE_FLOOR
        )


def test_non_staple_biases_are_still_free_to_move():
    """The floor covers the staples only — policy biases stay tunable."""
    tilted = ActionBiasSet({"RECOMPOSE": 2.0, "MERCENARY": 0.6})
    assert tilted.RECOMPOSE == pytest.approx(ActionBias.RECOMPOSE * 2.0)
    assert tilted.MERCENARY == pytest.approx(ActionBias.MERCENARY * 0.6)


def test_untilted_bias_set_matches_the_defaults():
    plain = ActionBiasSet()
    for name, value in plain.as_dict().items():
        assert value == pytest.approx(getattr(ActionBias, name))


def test_radius_weights_stay_on_the_board():
    """Radii are square counts: never 0, never board-wide."""
    tiny = weights_from_tilt({"enemy_king_radius": 0.0})
    huge = weights_from_tilt({"enemy_king_radius": 99.0})
    assert tiny.enemy_king_radius >= 1
    assert huge.enemy_king_radius <= 4
    assert isinstance(huge.enemy_king_radius, int)


# ─────────────────────────────────────────────────────────────────────────────
# Each style tilts the way README §51 describes
# ─────────────────────────────────────────────────────────────────────────────

def test_conqueror_presses_the_king_and_shrugs_at_buildings():
    w = CONQUEROR.weights()
    assert w.enemy_king_attacker > DEFAULT_WEIGHTS.enemy_king_attacker
    assert w.enemy_king_check > DEFAULT_WEIGHTS.enemy_king_check
    assert w.enemy_king_radius > DEFAULT_WEIGHTS.enemy_king_radius
    assert w.building_complete < DEFAULT_WEIGHTS.building_complete
    # "material medium" — README §51 leaves it alone.
    assert w.material == pytest.approx(DEFAULT_WEIGHTS.material)


def test_architect_builds_holds_and_declines_the_early_attack():
    w = ARCHITECT.weights()
    assert w.building_complete > DEFAULT_WEIGHTS.building_complete * 2
    assert w.territory_square > DEFAULT_WEIGHTS.territory_square * 2
    assert w.pawn_base > DEFAULT_WEIGHTS.pawn_base
    assert w.king_safety_attacker > DEFAULT_WEIGHTS.king_safety_attacker
    assert w.enemy_king_attacker < DEFAULT_WEIGHTS.enemy_king_attacker
    assert ARCHITECT.biases().CONSTRUCTION > ActionBias.CONSTRUCTION * 2


def test_ritualist_chases_rituals_and_discounts_material():
    w = RITUALIST.weights()
    assert w.ritual_progress > DEFAULT_WEIGHTS.ritual_progress * 2
    assert w.ritual_activated > DEFAULT_WEIGHTS.ritual_activated * 2
    assert w.material < DEFAULT_WEIGHTS.material
    assert w.board_control_centrality > DEFAULT_WEIGHTS.board_control_centrality
    assert RITUALIST.biases().RITUAL > ActionBias.RITUAL


def test_ritualist_summons_MORE_than_the_default_bot():
    """
    The headline requirement: a Ritualist needs bodies to sacrifice, so it
    must not be a bot that skips the Monster system to chase Rituals.
    """
    b = RITUALIST.biases()
    assert b.SUMMON > ActionBias.SUMMON
    assert b.CONSTRUCTION >= ActionBias.CONSTRUCTION * STAPLE_FLOOR
    assert b.DISMISS > ActionBias.DISMISS      # less eager to undo a Summon


def test_assassin_hunts_the_king_and_accepts_the_risk():
    w = ASSASSIN.weights()
    assert w.duel_pressure > DEFAULT_WEIGHTS.duel_pressure * 2
    assert w.enemy_king_attacker > DEFAULT_WEIGHTS.enemy_king_attacker * 2
    assert w.material < DEFAULT_WEIGHTS.material
    assert w.hanging_penalty < DEFAULT_WEIGHTS.hanging_penalty
    assert w.board_control_square < DEFAULT_WEIGHTS.board_control_square


@pytest.mark.parametrize("style", _FIXED, ids=lambda s: s.key)
def test_every_style_still_values_free_material(style: Personality):
    """Damped is not disabled — a free Queen is still a free Queen."""
    assert style.weights().material > 0.5


# ─────────────────────────────────────────────────────────────────────────────
# The Opportunist (README §51)
# ─────────────────────────────────────────────────────────────────────────────

def _shares(mix: dict) -> dict:
    total = sum(mix.values())
    return {k: v / total for k, v in mix.items()}


def test_opportunist_mix_never_collapses_onto_one_style(game, registry):
    shares = _shares(opportunist_mix(_obs(game.state, "white", registry)))
    assert set(shares) == {"conqueror", "architect", "ritualist", "assassin"}
    for key, share in shares.items():
        assert MIX_MIN_SHARE <= share <= MIX_MAX_SHARE, (
            f"{key} took {share:.0%} of the mix"
        )


def test_opportunist_floor_holds_even_when_every_signal_fires(game, registry):
    """
    The invariant, pushed as hard as the board allows: own King in check,
    material down, enemy King swarmed, own and enemy Rituals revealed, a
    half-built Building, a full hand.  Every component is screaming, and no
    component can still take the whole vote.
    """
    state = game.state
    for square in ("a1", "b1", "c1", "h1", "g1", "f1"):
        state.board.remove_unit(P(square))
    _put(state, "e7", "white", "queen")
    _put(state, "d7", "white", "rook")
    _put(state, "f7", "white", "knight")
    _put(state, "e6", "white", "bishop")
    for rs in state.get_player("white").ritual_pool:
        rs.revelation = RevelationState.REVEALED

    shares = _shares(opportunist_mix(_obs(state, "white", registry)))
    for key, share in shares.items():
        assert MIX_MIN_SHARE <= share <= MIX_MAX_SHARE, (
            f"{key} took {share:.0%} of the mix"
        )
    # ... and the loudest signal did move the needle.
    assert max(shares.values()) > min(shares.values())


def test_opportunist_leans_ritualist_when_its_ritual_is_close(game, registry):
    obs = _obs(game.state, "white", registry)
    before = opportunist_mix(obs)

    ritual_pool = game.state.get_player("white").ritual_pool
    if not ritual_pool:
        pytest.skip("no Ritual pool in this deck configuration")
    ritual_pool[0].revelation = RevelationState.REVEALED
    after = opportunist_mix(_obs(game.state, "white", registry))

    assert after["ritualist"] > before["ritualist"]
    assert after["architect"] == before["architect"]


def test_opportunist_leans_architect_when_behind_on_material(game, registry):
    before = opportunist_mix(_obs(game.state, "white", registry))
    for square in ("a1", "h1", "b1"):
        game.state.board.remove_unit(P(square))
    after = opportunist_mix(_obs(game.state, "white", registry))
    assert after["architect"] > before["architect"]


def test_opportunist_leans_assassin_when_the_enemy_king_is_hemmed_in(
    game, registry
):
    before = opportunist_mix(_obs(game.state, "white", registry))
    _put(game.state, "e7", "white", "queen")
    _put(game.state, "d7", "white", "rook")
    after = opportunist_mix(_obs(game.state, "white", registry))
    assert after["assassin"] > before["assassin"]


def test_opportunist_produces_moderate_weights(game, registry):
    """A blend of four styles cannot be more extreme than its components."""
    obs = _obs(game.state, "white", registry)
    w = OPPORTUNIST.weights(obs)
    for name, default in vars(DEFAULT_WEIGHTS).items():
        if isinstance(default, int):
            continue
        ratio = getattr(w, name) / default
        assert TILT_FLOOR - 1e-9 <= ratio <= TILT_CEIL + 1e-9


def test_opportunist_staples_are_floored_too(game, registry):
    biases = OPPORTUNIST.biases(_obs(game.state, "white", registry))
    for name in STAPLE_BIASES:
        assert getattr(biases, name) >= getattr(ActionBias, name) * STAPLE_FLOOR


def test_opportunist_without_an_observation_falls_back_to_neutral():
    """No board to read yet — behave like the default bot, not like nothing."""
    w = OPPORTUNIST.weights()
    assert w == DEFAULT_WEIGHTS


# ─────────────────────────────────────────────────────────────────────────────
# PersonalityBot — the controller
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", PERSONALITY_KEYS)
def test_bot_is_a_player_controller(key, registry):
    bot = PersonalityBot(seed=1, personality=key, registry=registry, limits=_FAST)
    assert isinstance(bot, PlayerController)
    assert isinstance(bot, HeuristicBot)      # inherits Stage 1 + Stage 2
    assert bot.personality.key == key


@pytest.mark.parametrize("key", PERSONALITY_KEYS)
def test_bot_returns_a_legal_action(key, game, registry):
    bot = PersonalityBot(seed=3, personality=key, registry=registry, limits=_FAST)
    bot.player_id = "white"
    legal = game.get_legal_actions("white")
    chosen = bot.choose_action(_obs(game.state, "white", registry), legal)
    assert chosen in legal


def test_bot_carries_its_personality_into_the_weights(registry):
    ritualist = PersonalityBot(seed=1, personality="ritualist", registry=registry)
    architect = PersonalityBot(seed=1, personality="architect", registry=registry)
    assert ritualist._weights.ritual_progress > architect._weights.ritual_progress
    assert architect._weights.building_complete > ritualist._weights.building_complete
    assert ritualist._bias.RITUAL > architect._bias.RITUAL


def test_fixed_personality_reports_a_single_style_mix(registry):
    bot = PersonalityBot(seed=1, personality="assassin", registry=registry)
    assert bot.mix == {"assassin": 1.0}


def test_opportunist_retunes_across_turns(game, registry):
    bot = PersonalityBot(
        seed=1, personality="opportunist", registry=registry, limits=_FAST
    )
    bot.player_id = "white"
    obs = _obs(game.state, "white", registry)
    bot.choose_action(obs, game.get_legal_actions("white"))
    first = bot.mix
    assert sum(first.values()) == pytest.approx(1.0)
    assert len(first) == 4

    # Hand it a materially different board on a later turn.
    for square in ("a1", "h1", "b1", "g1"):
        game.state.board.remove_unit(P(square))
    game.state.turn_number += 1
    later = _obs(game.state, "white", registry)
    bot.choose_action(later, game.get_legal_actions("white"))
    assert bot.mix != first


def test_opportunist_holds_one_mix_for_a_whole_turn(game, registry):
    """
    Coherence: a bot that summoned as an Architect and then moved as an
    Assassin is neither. The mix is recomputed per turn, not per decision.
    """
    bot = PersonalityBot(
        seed=1, personality="opportunist", registry=registry, limits=_FAST
    )
    bot.player_id = "white"
    legal = game.get_legal_actions("white")
    bot.choose_action(_obs(game.state, "white", registry), legal)
    first = bot.mix

    for square in ("a1", "h1", "b1", "g1"):
        game.state.board.remove_unit(P(square))
    bot.choose_action(_obs(game.state, "white", registry), legal)
    assert bot.mix == first


# ─────────────────────────────────────────────────────────────────────────────
# It still plays the whole game
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", PERSONALITY_KEYS)
def test_style_ranks_summon_and_construction_above_passing(key, game, registry):
    """
    The behavioural half of "not 100% personality": whatever the style, a
    Summon and a Construction still beat doing nothing.
    """
    bot = PersonalityBot(seed=5, personality=key, registry=registry, limits=_FAST)
    bot.player_id = "white"
    obs = _obs(game.state, "white", registry)
    if bot.personality.is_adaptive:
        bot._retune(obs)
    ctx = EvalContext(obs=obs, weights=bot._weights, registry=registry)

    legal = game.get_legal_actions("white")
    pass_score = next(
        bot.score_action(a, ctx) for a in legal if isinstance(a, EndPreparation)
    )
    for kind in (SummonMonster, StartConstruction):
        candidates = [a for a in legal if isinstance(a, kind)]
        if not candidates:
            continue
        best = max(bot.score_action(a, ctx) for a in candidates)
        assert best > pass_score, (
            f"{key} would rather pass than play {kind.__name__}"
        )


@pytest.mark.parametrize("key", PERSONALITY_KEYS)
def test_a_full_match_never_crashes(key, registry):
    white = PersonalityBot(
        seed=11, personality=key, registry=registry, limits=_FAST
    )
    black = HeuristicBot(seed=12, registry=registry)
    stats = play_match(
        seed=4, controllers={"white": white, "black": black},
        registry=registry, max_steps=120,
    )
    assert stats.action_count > 0
    assert stats.illegal_action_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Harness wiring
# ─────────────────────────────────────────────────────────────────────────────

def test_personality_is_a_controller_kind():
    assert "personality" in CONTROLLER_KINDS


@pytest.mark.parametrize("key", PERSONALITY_KEYS)
def test_make_controller_builds_each_style(key, registry):
    bot = make_controller(
        "personality", seed=1, player_id="black", registry=registry,
        search_limits=_FAST, personality=key,
    )
    assert isinstance(bot, PersonalityBot)
    assert bot.personality.key == key
    assert bot.player_id == "black"


def test_make_controller_defaults_the_style(registry):
    bot = make_controller(
        "personality", seed=1, player_id="white", registry=registry,
        search_limits=_FAST,
    )
    assert bot.personality.key == DEFAULT_PERSONALITY


def test_make_controller_rejects_an_unknown_style(registry):
    with pytest.raises(ValueError, match="Unknown personality"):
        make_controller(
            "personality", seed=1, player_id="white", registry=registry,
            search_limits=_FAST, personality="warlock",
        )


# ─────────────────────────────────────────────────────────────────────────────
# The other bots keep working with an injected bias table
# ─────────────────────────────────────────────────────────────────────────────

def test_heuristic_bot_accepts_a_personality_bias_table(game, registry):
    bot = HeuristicBot(
        seed=1, weights=RITUALIST.weights(), registry=registry,
        biases=RITUALIST.biases(),
    )
    bot.player_id = "white"
    legal = game.get_legal_actions("white")
    assert bot.choose_action(_obs(game.state, "white", registry), legal) in legal


def test_default_bots_are_unchanged_by_the_refactor(game, registry):
    """HeuristicBot with no bias argument still reads the stock table."""
    bot = HeuristicBot(seed=1, registry=registry)
    assert bot._bias is ActionBias


# ─────────────────────────────────────────────────────────────────────────────
# The pygame player picker
# ─────────────────────────────────────────────────────────────────────────────

class TestPlayerPicker:
    """
    The sidebar selector opens one popup listing every controller: the four
    AI stages by README §52 difficulty, the five §51 play styles, and the
    human.  These assert the roster's shape, not its pixels.
    """

    def _app(self):
        import os

        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from game.ui.pygame_app import AppController

        return AppController()

    def test_the_roster_lists_every_controller_in_order(self):
        from game.ui.pygame_app import AppController

        roster = AppController._ROSTER
        assert [(c.kind, c.personality) for c in roster] == [
            ("random", None),
            ("heuristic", None),
            ("search", None),
            ("montecarlo", None),
            ("personality", "conqueror"),
            ("personality", "architect"),
            ("personality", "ritualist"),
            ("personality", "assassin"),
            ("personality", "opportunist"),
            ("random", None),          # the human row
        ]
        assert roster[-1].mode == "human"
        assert all(c.mode == "ai" for c in roster[:-1])

    def test_the_ai_stages_carry_difficulty_words(self):
        from game.ui.pygame_app import AppController

        tiers = {
            c.kind: c.tier
            for c in AppController._ROSTER
            if c.mode == "ai" and c.kind != "personality"
        }
        assert tiers == {
            "random": "Idiot",
            "heuristic": "Easy",
            "search": "Medium",
            "montecarlo": "Hard",
        }

    def test_play_styles_carry_no_difficulty_word(self):
        """§51 is about how the bot plays; §52 is about how hard it thinks."""
        from game.ui.pygame_app import AppController

        styles = [c for c in AppController._ROSTER if c.kind == "personality"]
        assert len(styles) == len(PERSONALITY_KEYS)
        assert all(c.tier == "" for c in styles)
        assert all(c.subtitle and c.detail for c in styles)

    def test_every_row_can_describe_itself(self):
        from game.ui.pygame_app import AppController

        for c in AppController._ROSTER:
            assert c.name and c.subtitle and c.detail and c.label

    def test_clicking_the_selector_opens_the_picker(self):
        app = self._app()
        assert app._player_picker is None
        app._toggle_mode("white")
        assert app._player_picker is not None
        assert app._player_picker_side == "white"
        # Opening it commits nothing.
        assert app.mode_label("white") == "HUMAN"

    def test_choosing_a_difficulty_switches_the_side(self):
        app = self._app()
        app._open_player_picker("black")
        montecarlo = next(c for c in app._ROSTER if c.kind == "montecarlo")
        app._on_player_choice(montecarlo)
        assert app._player_picker is None
        assert app._player_modes["black"] == "ai"
        assert app._ai_kinds["black"] == "montecarlo"
        assert app.mode_label("black") == "MONTE CARLO AI"

    def test_choosing_a_play_style_switches_the_side_and_names_it(self):
        app = self._app()
        app._open_player_picker("white")
        ritualist = next(
            c for c in app._ROSTER if c.personality == "ritualist"
        )
        app._on_player_choice(ritualist)
        assert app._ai_kinds["white"] == "personality"
        assert app._ai_personalities["white"] == "ritualist"
        assert app.mode_label("white") == "RITUALIST AI"
        assert isinstance(app._bots["white"], PersonalityBot)
        assert app._bots["white"].personality.key == "ritualist"

    def test_choosing_human_hands_the_side_back(self):
        app = self._app()
        app._open_player_picker("white")
        app._on_player_choice(
            next(c for c in app._ROSTER if c.personality == "assassin")
        )
        assert app._player_modes["white"] == "ai"

        app._open_player_picker("white")
        app._on_player_choice(next(c for c in app._ROSTER if c.mode == "human"))
        assert app._player_modes["white"] == "human"
        assert app.mode_label("white") == "HUMAN"

    def test_cancelling_changes_nothing(self):
        app = self._app()
        app._open_player_picker("black")
        app._on_player_choice(next(c for c in app._ROSTER if c.kind == "search"))
        before = (app._player_modes["black"], app._ai_kinds["black"])

        app._open_player_picker("black")
        app._close_player_picker()
        assert app._player_picker is None
        assert (app._player_modes["black"], app._ai_kinds["black"]) == before

    def test_the_current_choice_is_marked(self):
        app = self._app()
        app._open_player_picker("white")
        app._on_player_choice(
            next(c for c in app._ROSTER if c.personality == "architect")
        )
        app._open_player_picker("white")
        assert app._player_picker._current.personality == "architect"

    def test_the_popup_fits_on_screen(self):
        """Ten rows, a heading and a detail panel still have to fit."""
        from game.ui.pygame_app import WIN_H, WIN_W

        app = self._app()
        app._render()
        app._open_player_picker("white")
        app._render()
        rect = app._player_picker._rect
        assert rect.top >= 0 and rect.bottom <= WIN_H
        assert rect.left >= 0 and rect.right <= WIN_W
        assert len(app._player_picker._row_rects) == len(app._ROSTER)

    def test_the_match_is_frozen_while_the_picker_is_open(self):
        """
        Picking a controller is a setup decision, not a game one — the
        board must not move on underneath the player while they read.
        """
        import inspect

        from game.ui.pygame_app import AppController

        src = inspect.getsource(AppController.run)
        assert "if self._player_picker is None:" in src
        assert "self._tick_ai()" in src
