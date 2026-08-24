"""
AI Personalities — README §51
===============================

    "Enemy AI should not only vary by difficulty.  It should also have
     play styles.  Evaluation weights can create personalities."

README §51 names five: **Conqueror**, **Architect**, **Ritualist**,
**Assassin** and the dynamic **Opportunist**.  This module defines them and
turns each one into the two tuning tables the bots already read:

    Personality ──► EvalWeights   (how the *position* is judged)
                └─► ActionBiasSet (which *systems* the bot reaches for)

Nothing else changes.  A personality bot is an ordinary
``PlayerController`` running the ordinary Stage 2 search over retuned
numbers, so it obeys the same information rules (README §44) and the same
legality contract as every other bot.

A personality is a tilt, not a monomania
----------------------------------------
The one thing a play style must not do is stop playing the game.  A
Ritualist that never summons a Monster is not a Ritualist — it is a broken
bot that loses to everything and teaches the player nothing.  So a
personality here is expressed as a **multiplier on the defaults**, and the
multipliers are clamped:

    • every evaluation weight stays within ``[TILT_FLOOR, TILT_CEIL]`` ×
      its default — no category is ever zeroed, so an Assassin that "values
      material lower" still takes a free Queen, it just will not go out of
      its way for a pawn;

    • the **staple actions** — Summon, Construction, Ritual, Spell, Trap,
      Coronation, Castle — additionally cannot fall below
      ``STAPLE_FLOOR`` × their default bias, and cannot go non-positive.
      Whatever the style, the bot keeps summoning Monsters, keeps starting
      Buildings, and keeps taking a free Coronation.

The result is a bot that is recognisably *itself* — a Ritualist really does
sacrifice material to finish a Ritual — while still playing every system on
the board.  ``tests/test_personality_bots.py`` asserts both halves.

The Opportunist (README §51) is different by design: "priorities
dynamically change depending on board state".  It is defined as a *blend*
of the other four, re-mixed from the Observation on every turn — behind in
material it leans Architect, with a Ritual close to landing it leans
Ritualist, with the enemy King exposed it leans Assassin.  Each component
keeps a floor share of the mix, so the Opportunist is never a pure copy of
one of the others either.

Usage:
    from game.ai.personality import PERSONALITIES, get_personality

    ritualist = get_personality("ritualist")
    weights   = ritualist.weights()          # an EvalWeights
    biases    = ritualist.biases()           # an ActionBiasSet
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Callable

from game.ai.evaluation import DEFAULT_WEIGHTS, EvalWeights
from game.ai.heuristic_bot import ActionBias
from game.core.phases import ConstructionStatus, RevelationState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.core.observation import Observation


# ─────────────────────────────────────────────────────────────────────────────
# Guardrails — "a tilt, not a monomania"
# ─────────────────────────────────────────────────────────────────────────────

#: No evaluation weight may drop below this fraction of its default …
TILT_FLOOR: float = 0.6
#: … nor rise above this multiple of it.
TILT_CEIL: float = 3.0

#: Radius weights are square counts, not coefficients; they get their own
#: bounds so a personality cannot ask for a 0-square or board-wide radius.
RADIUS_MIN: int = 1
RADIUS_MAX: int = 4

#: The action biases that keep the bot playing the whole game.  However a
#: personality is tuned, these stay at or above ``STAPLE_FLOOR`` × default
#: and stay positive, so every personality still summons, builds, casts,
#: crowns and completes Rituals.
STAPLE_BIASES: tuple[str, ...] = (
    "SUMMON",
    "SPELL",
    "TRAP",
    "ACTIVATE_TRAP",
    "MONSTER_ABILITY",
    "RITUAL",
    "CONSTRUCTION",
    "CORONATION",
    "CASTLE",
)
STAPLE_FLOOR: float = 0.6

#: The one way past that floor.  A play style whose whole idea is to skip a
#: system — the Chess Purist declining the card game — names it in
#: ``Personality.abstains`` and gets this bias instead of the floor.  It is
#: a *declaration*, not a multiplier: a style cannot drift below the floor
#: by tuning, only by saying outright which systems it does not play.  The
#: value sits below every board-derived bonus a Summon or a Ritual can pick
#: up, so an abstaining bot passes PREPARATION rather than half-playing it.
ABSTAIN_BIAS: float = -4.0


def clamp_tilt(multiplier: float) -> float:
    """Force a personality multiplier inside ``[TILT_FLOOR, TILT_CEIL]``."""
    return min(max(multiplier, TILT_FLOOR), TILT_CEIL)


# ─────────────────────────────────────────────────────────────────────────────
# ActionBiasSet — a retuned copy of the ActionBias table
# ─────────────────────────────────────────────────────────────────────────────

class ActionBiasSet:
    """
    A mutable copy of ``ActionBias`` with personality multipliers applied.

    The bots read their bias table as ``self._bias.SUMMON``, so anything
    exposing the same upper-case attributes is a drop-in replacement for the
    ``ActionBias`` class itself.
    """

    def __init__(
        self,
        tilt: "dict[str, float] | None" = None,
        base: "type[ActionBias] | ActionBias" = ActionBias,
        abstains: "frozenset[str] | None" = None,
    ) -> None:
        tilt = tilt or {}
        abstains = abstains or frozenset()
        fields = {n for n in dir(base) if n.isupper()}
        unknown = (set(tilt) | set(abstains)) - fields
        if unknown:
            raise ValueError(
                f"unknown ActionBias field(s): {', '.join(sorted(unknown))}"
            )
        for name in fields:
            default = getattr(base, name)
            if name in abstains:
                # A declared abstention (see ABSTAIN_BIAS): this style does
                # not play this system at all.
                setattr(self, name, ABSTAIN_BIAS)
                continue
            value = default * clamp_tilt(tilt.get(name, 1.0))
            if name in STAPLE_BIASES:
                # The staple floor is what stops a play style from *drifting*
                # into a refusal to play: whatever the tilt asked for, a
                # Summon stays worth summoning unless the style said outright
                # that it abstains.
                value = max(value, default * STAPLE_FLOOR)
            setattr(self, name, value)

    def as_dict(self) -> "dict[str, float]":
        """Every bias, by name — for tests, tuning and telemetry."""
        return {k: v for k, v in vars(self).items() if k.isupper()}

    def __repr__(self) -> str:  # pragma: no cover
        return f"ActionBiasSet({self.as_dict()})"


# ─────────────────────────────────────────────────────────────────────────────
# EvalWeights construction
# ─────────────────────────────────────────────────────────────────────────────

_RADIUS_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(EvalWeights)
    if isinstance(getattr(DEFAULT_WEIGHTS, f.name), int)
)
_WEIGHT_FIELDS: frozenset[str] = frozenset(f.name for f in fields(EvalWeights))


def weights_from_tilt(
    tilt: "dict[str, float]",
    base: EvalWeights = DEFAULT_WEIGHTS,
) -> EvalWeights:
    """
    Apply per-field multipliers to ``base``, clamped by the guardrails.

    Unknown field names are a programming error, not a silent no-op — a
    typo in a personality table would otherwise ship as "that priority does
    nothing".
    """
    unknown = set(tilt) - _WEIGHT_FIELDS
    if unknown:
        raise ValueError(
            f"unknown EvalWeights field(s): {', '.join(sorted(unknown))}"
        )
    values: dict[str, float | int] = {}
    for name in _WEIGHT_FIELDS:
        default = getattr(base, name)
        scaled = default * clamp_tilt(tilt.get(name, 1.0))
        if name in _RADIUS_FIELDS:
            values[name] = max(RADIUS_MIN, min(RADIUS_MAX, round(scaled)))
        else:
            values[name] = scaled
    return EvalWeights(**values)


# ─────────────────────────────────────────────────────────────────────────────
# Personality
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Personality:
    """
    One README §51 play style.

    ``weight_tilt`` / ``bias_tilt`` are multipliers on ``EvalWeights`` and
    ``ActionBias`` fields; both are clamped when applied (see the module
    docstring).

    ``abstains`` names the staple actions this style declines outright —
    the Chess Purist's whole idea.  It is the only way past the staple
    floor, and it is deliberately a declaration rather than a tuning knob:
    a style cannot *drift* into refusing to play, it has to say so.

    ``blend`` is the adaptive styles' escape hatch: given an Observation it
    returns a mixture over *stance* names, which ``tilts()`` averages into
    a single tilt for that turn.  A stance is any entry in ``_STANCES`` —
    the five §51 archetypes, plus the private stances an adaptive style
    switches between.  A blended style's ``abstains`` is its own; stances
    do not contribute abstentions, because "sometimes refuses to play a
    system" is not a thing this design wants to be able to express.
    """

    key: str
    name: str
    blurb: str
    priorities: tuple[str, ...] = ()
    weight_tilt: "dict[str, float]" = field(default_factory=dict)
    bias_tilt: "dict[str, float]" = field(default_factory=dict)
    abstains: frozenset[str] = frozenset()
    blend: "Callable[[Observation], dict[str, float]] | None" = None

    @property
    def is_adaptive(self) -> bool:
        """True for a personality whose priorities depend on the board."""
        return self.blend is not None

    def tilts(
        self, obs: "Observation | None" = None
    ) -> "tuple[dict[str, float], dict[str, float]]":
        """The (weight, bias) multipliers this personality wants right now."""
        if self.blend is None or obs is None:
            return dict(self.weight_tilt), dict(self.bias_tilt)
        mix = _normalise(self.blend(obs))
        return (
            _blend_tilts(mix, "weight_tilt"),
            _blend_tilts(mix, "bias_tilt"),
        )

    def weights(self, obs: "Observation | None" = None) -> EvalWeights:
        """The README §46 weights for this play style."""
        return weights_from_tilt(self.tilts(obs)[0])

    def biases(self, obs: "Observation | None" = None) -> ActionBiasSet:
        """The action-bias table for this play style."""
        return ActionBiasSet(self.tilts(obs)[1], abstains=self.abstains)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Personality({self.key!r})"


def _normalise(mix: "dict[str, float]") -> "dict[str, float]":
    total = sum(max(v, 0.0) for v in mix.values())
    if total <= 0:
        return {k: 1.0 / len(mix) for k in mix}
    return {k: max(v, 0.0) / total for k, v in mix.items()}


def _blend_tilts(mix: "dict[str, float]", attr: str) -> "dict[str, float]":
    """
    Average the named tilt table across a normalised personality mixture.

    A field absent from a component's table is that component voting for
    1.0 (leave it alone), which is what makes a partial blend moderate
    rather than extreme.
    """
    keys: set[str] = set()
    for pkey in mix:
        keys.update(getattr(_STANCES[pkey], attr))
    return {
        name: sum(
            share * getattr(_STANCES[pkey], attr).get(name, 1.0)
            for pkey, share in mix.items()
        )
        for name in keys
    }


# ─────────────────────────────────────────────────────────────────────────────
# The Opportunist's mixer (README §51)
# ─────────────────────────────────────────────────────────────────────────────

#: Every component starts here, so no single style can take the whole mix.
MIX_BASE: float = 1.0
#: … and none can be pushed past this, so the floor share really is a floor.
MIX_CAP: float = 4.0

#: The bounds those two constants imply on any component's share of the
#: normalised mix, with four components in play.  The Opportunist adapts
#: between these; it never converts to a single style.
MIX_MIN_SHARE: float = MIX_BASE / (MIX_BASE + 3 * MIX_CAP)
MIX_MAX_SHARE: float = MIX_CAP / (MIX_CAP + 3 * MIX_BASE)

_PIECE_POINTS = {"pawn": 1.0, "knight": 3.0, "bishop": 3.25, "rook": 5.0, "queen": 9.0}


def _material_edge(obs: "Observation") -> float:
    """Own material minus the opponent's, in pawns.  Kings excluded."""
    edge = 0.0
    for unit in obs.board.units:
        value = _PIECE_POINTS.get(unit.piece_type, 0.0)
        edge += value if unit.owner == obs.player_id else -value
    return edge


def opportunist_mix(obs: "Observation") -> "dict[str, float]":
    """
    README §51's Opportunist: "priorities dynamically change depending on
    board state, revealed Rituals, King policy, current hand, opponent
    weaknesses".

    Returns an un-normalised mixture over the four fixed personalities.
    Each starts at ``MIX_BASE`` and is capped at ``MIX_CAP``, so every
    component's share of the normalised mix is bounded by
    ``MIX_MIN_SHARE`` / ``MIX_MAX_SHARE`` no matter what the board says —
    the Opportunist adapts, it does not convert.
    """
    mix = {
        "conqueror": MIX_BASE,
        "architect": MIX_BASE,
        "ritualist": MIX_BASE,
        "assassin": MIX_BASE,
    }

    # ── Board state: who is winning the material race ────────────────────
    edge = _material_edge(obs)
    if edge >= 3.0:
        mix["conqueror"] += 1.5          # press an advantage
    elif edge <= -3.0:
        mix["architect"] += 1.5          # consolidate and out-build instead

    # ── Opponent weaknesses: an exposed enemy King ───────────────────────
    if obs.opponent_in_check:
        mix["assassin"] += 1.2
    enemy_king = next(
        (u.position for u in obs.board.units
         if u.piece_type == "king" and u.owner != obs.player_id),
        None,
    )
    if enemy_king is not None:
        from game.ai.evaluation import chebyshev

        attackers = sum(
            1 for u in obs.board.units
            if u.owner == obs.player_id
            and u.piece_type != "king"
            and chebyshev(u.position, enemy_king) <= 2
        )
        if attackers >= 2:
            mix["assassin"] += 0.8
        if attackers >= 4:
            mix["conqueror"] += 0.6

    # ── Own King under pressure: turtle up ───────────────────────────────
    if obs.own_in_check:
        mix["architect"] += 1.0

    # ── Revealed Rituals — own (finish it) and theirs (race it) ──────────
    for rs in obs.own_rituals:
        if getattr(rs, "activated", False):
            continue
        revelation = getattr(rs, "revelation", None)
        if revelation == RevelationState.REVEALED:
            mix["ritualist"] += 2.0
        elif revelation == RevelationState.FORETOLD:
            mix["ritualist"] += 1.0
        mix["ritualist"] += 0.3 * min(getattr(rs, "progress", 0), 3)
    for info in obs.opponent_ritual_info:
        if info.revelation == RevelationState.REVEALED:
            mix["assassin"] += 1.0       # end it before the payoff lands
            mix["conqueror"] += 0.6
        elif info.revelation == RevelationState.FORETOLD:
            mix["assassin"] += 0.4

    # ── The infrastructure already paid for ──────────────────────────────
    own_buildings = [
        b for b in obs.board.building_locations
        if getattr(b, "owner", None) == obs.player_id
    ]
    if any(getattr(b, "status", None) != ConstructionStatus.COMPLETE
           for b in own_buildings):
        mix["architect"] += 0.8          # finish what is standing half-built
    if sum(1 for b in own_buildings
           if getattr(b, "status", None) == ConstructionStatus.COMPLETE) >= 2:
        mix["architect"] += 0.5
    if len(obs.own_territory) >= len(obs.opponent_territory) + 6:
        mix["conqueror"] += 0.5

    # ── Current hand: cards are Ritual material ──────────────────────────
    if len(obs.own_hand) >= 4:
        mix["ritualist"] += 0.5
    if len(obs.own_hand) + 2 <= obs.opponent_hand_count:
        mix["architect"] += 0.4          # out of cards — play the long game

    return {k: min(v, MIX_CAP) for k, v in mix.items()}


# ─────────────────────────────────────────────────────────────────────────────
# The Rogue's clock
# ─────────────────────────────────────────────────────────────────────────────

#: A full board is two armies of 39 points.  Below ``_ENDGAME_MATERIAL`` of
#: the total still standing, the game is being decided rather than built.
_OPENING_MATERIAL: float = 60.0
_ENDGAME_MATERIAL: float = 24.0

_EARLY_TURN: float = 10.0
_LATE_TURN: float = 28.0

_DECK_COMFORTABLE: float = 10.0
_DECK_EMPTY: float = 2.0


def _ramp(value: float, start: float, full: float) -> float:
    """
    0.0 at ``start``, 1.0 at ``full``, linear between, clamped outside.

    ``start`` may be either side of ``full``, so a signal that *falls* as
    the game progresses (material, deck size) reads the same way as one
    that rises (turn number).
    """
    if start == full:
        return 1.0 if value == full else 0.0
    return max(0.0, min(1.0, (value - start) / (full - start)))


def endgame_pressure(obs: "Observation") -> float:
    """
    How much the board says the endgame has arrived, in ``[0, 1]``.

    Built only from signals that do not go backwards — material on the
    board, the turn number, the deck draining, Rituals coming out from
    under their seals.  That matters: the Rogue's whole idea is that it
    commits *once*.  A signal that flickers (a King in check for one turn)
    would have it dumping its hand and then wishing it hadn't, so none are
    used, and no latch is needed to paper over it.
    """
    material = sum(
        _PIECE_POINTS.get(unit.piece_type, 0.0) for unit in obs.board.units
    )
    revealed = sum(
        1 for rs in obs.own_rituals
        if getattr(rs, "revelation", None) in (
            RevelationState.FORETOLD, RevelationState.REVEALED
        )
    ) + sum(
        1 for info in obs.opponent_ritual_info
        if info.revelation in (
            RevelationState.FORETOLD, RevelationState.REVEALED
        )
    )

    signals = (
        _ramp(material, _OPENING_MATERIAL, _ENDGAME_MATERIAL),
        _ramp(float(obs.turn_number), _EARLY_TURN, _LATE_TURN),
        _ramp(float(obs.own_deck_count), _DECK_COMFORTABLE, _DECK_EMPTY),
        _ramp(float(revealed), 0.0, 3.0),
    )
    return sum(signals) / len(signals)


def rogue_mix(obs: "Observation") -> "dict[str, float]":
    """
    The Rogue's stance for this turn: hoarding, spending, or on the way
    between the two.

    A hard switch would make the Rogue's one interesting moment depend on a
    threshold nobody can see, and would flip back and forth around it.  A
    ramp means the hand starts opening as the endgame approaches and is
    fully open by the time it arrives — which is what "feels like the end
    game starts" actually looks like from the other side of the board.
    """
    pressure = endgame_pressure(obs)
    return {"rogue_hoard": 1.0 - pressure, "rogue_spend": pressure}


# ─────────────────────────────────────────────────────────────────────────────
# The play styles
# ─────────────────────────────────────────────────────────────────────────────

CONQUEROR = Personality(
    key="conqueror",
    name="Conqueror",
    blurb="Aggressive positional attacker.",
    priorities=(
        "King pressure  high",
        "Material       medium",
        "Buildings      low",
        "Rituals        medium",
    ),
    weight_tilt={
        # King pressure: high.
        "enemy_king_check": 1.7,
        "enemy_king_attacker": 1.9,
        "enemy_king_radius": 1.5,        # 2 → 3 squares
        "duel_pressure": 1.4,
        # Board conquest is how it gets there.
        "board_control_square": 1.3,
        "board_control_centrality": 1.25,
        "pawn_advance": 1.3,
        # Material: medium — left at the default.
        # Buildings: low.
        "building_complete": 0.7,
        "building_under_construction": 0.7,
        "building_integrity": 0.7,
        "territory_square": 0.8,
        # Rituals: medium — also default.
        # An attacker accepts sharper positions than a builder would.
        "hanging_penalty": 0.85,
    },
    bias_tilt={
        "SUMMON_KING_ADJACENT": 1.8,
        "CONSTRUCTION": 0.7,             # floored at 0.6× — it still builds
        "MERCENARY": 1.4,                # more bodies, faster
        "DUEL_STRIKE": 1.2,
    },
)

ARCHITECT = Personality(
    key="architect",
    name="Architect",
    blurb="Builds, holds ground, and outlasts you.",
    priorities=(
        "Buildings        very high",
        "Pawn survival    high",
        "King safety      high",
        "Territory        high",
        "Immediate attack lower",
    ),
    weight_tilt={
        # Buildings: very high.
        "building_complete": 2.6,
        "building_under_construction": 2.4,
        "building_integrity": 2.2,
        # Pawn survival: high — worth more standing than racing.
        "pawn_base": 2.2,
        "pawn_advance": 0.75,
        # King safety: high.
        "king_safety_check": 1.6,
        "king_safety_attacker": 1.7,
        "king_safety_radius": 1.5,       # 2 → 3 squares
        "royal_support": 1.6,
        "duel_exposure": 1.4,
        # Territory: high.
        "territory_square": 2.8,
        # Immediate attack: lower.
        "enemy_king_check": 0.85,
        "enemy_king_attacker": 0.7,
        "duel_pressure": 0.75,
        # It does not give pieces away to get there.
        "hanging_penalty": 1.35,
        "trap_penalty": 1.2,
    },
    bias_tilt={
        "CONSTRUCTION": 2.5,
        "TRAP": 1.6,
        "ACTIVATE_TRAP": 1.3,
        "CASTLE": 1.5,
        "RECOMPOSE": 0.8,
        # Summoning is untouched: a fortress still needs a garrison.
    },
)

RITUALIST = Personality(
    key="ritualist",
    name="Ritualist",
    blurb="Sacrifices material to complete Rituals.",
    priorities=(
        "Ritual progress   very high",
        "Material          lower",
        "Formation control high",
        "King safety       medium",
    ),
    weight_tilt={
        # Ritual progress: very high.
        "ritual_progress": 2.8,
        "ritual_foretold": 2.2,
        "ritual_revealed": 2.4,
        "ritual_activated": 2.6,
        "ritual_monster": 2.2,
        # Material: lower — but floored, so free captures are still free.
        "material": 0.7,
        "hanging_penalty": 0.8,
        # Formation control: high.  Rituals are pattern-shaped (README §14),
        # so where the units stand matters more than what they are.
        "board_control_centrality": 1.6,
        "board_control_square": 1.35,
        "royal_support": 1.35,
        # The engine that feeds the Ritual: Monsters on the board, and the
        # cards in hand that put them there.
        "monster_unit": 1.4,
        "monster_effect": 1.3,
        "hand_summonable": 1.5,
        "card_in_hand": 1.25,
        # King safety: medium — left at the default.
    },
    bias_tilt={
        "RITUAL": 2.2,
        # Explicitly UP, not down.  A Ritualist needs bodies on the board to
        # sacrifice, so it summons *more* than the default bot, not less —
        # and Construction stays untouched, because a Ritualist that refuses
        # to build is just a bot with one plan and no game.
        "SUMMON": 1.35,
        "DISMISS": 0.7,                  # a Monster is Ritual material
        "MERCENARY": 1.3,
        "RECOMPOSE": 0.7,                # dig for the pieces it needs
    },
)

ASSASSIN = Personality(
    key="assassin",
    name="Assassin",
    blurb="Hunts the King to force the Final Duel.",
    priorities=(
        "Enemy King isolation   very high",
        "Final Duel probability very high",
        "Material               lower",
        "Board conquest         lower",
    ),
    weight_tilt={
        # Enemy King isolation: very high.
        "enemy_king_check": 2.2,
        "enemy_king_attacker": 2.6,
        "enemy_king_radius": 1.5,        # 2 → 3 squares
        # Final Duel probability: very high.
        "duel_pressure": 2.8,
        "duel_exposure": 1.3,
        # Material: lower — README §51 says it goes for the King while
        # behind, so the material term is damped and risk is cheap.
        "material": 0.7,
        "hanging_penalty": 0.65,
        # Board conquest: lower.
        "board_control_square": 0.65,
        "board_control_centrality": 0.7,
        "territory_square": 0.6,
        "building_complete": 0.7,
        "building_under_construction": 0.65,
        "pawn_base": 0.75,
    },
    bias_tilt={
        "SUMMON_KING_ADJACENT": 2.6,
        "DUEL_STRIKE": 1.5,
        "DUEL_SUPPORT": 1.3,
        "DUEL_ADVANCE": 0.7,
        "CONSTRUCTION": 0.7,             # floored — it still builds
        "SUCCESSION": 1.3,               # a fresh King is a fresh weapon
    },
)

CHESS_PURIST = Personality(
    key="purist",
    name="Chess Purist",
    blurb="Wins at chess. Declines the card game.",
    priorities=(
        "Chess play    everything",
        "Material      high",
        "King safety   high",
        "Preparation   declined",
    ),
    weight_tilt={
        # Everything that shows up on a chessboard, turned up.
        "material": 1.35,
        "board_control_square": 1.5,
        "board_control_centrality": 1.5,
        "pawn_advance": 1.4,
        "king_safety_check": 1.4,
        "king_safety_attacker": 1.35,
        "royal_support": 1.3,
        "enemy_king_check": 1.4,
        "enemy_king_attacker": 1.3,
        "hanging_penalty": 1.4,          # it is playing for the pieces
        # Everything that does not, turned down to the floor.  It cannot go
        # to zero — the Purist still *sees* an enemy Building as terrain and
        # an enemy Monster as a stronger piece, because both are on the board
        # and pretending otherwise would be blindness, not purity.
        "building_complete": 0.6,
        "building_under_construction": 0.6,
        "building_integrity": 0.6,
        "territory_square": 0.6,
        "ritual_progress": 0.6,
        "ritual_foretold": 0.6,
        "ritual_revealed": 0.6,
        "ritual_activated": 0.6,
        "ritual_monster": 0.6,
        "card_in_hand": 0.6,
        "card_in_deck": 0.6,
        "hand_summonable": 0.6,
        "hand_playable": 0.6,
    },
    bias_tilt={
        "CASTLE": 1.6,                   # the one preparation-ish thing it loves
        "MERCENARY": 0.6,
        "RECOMPOSE": 0.6,
    },
    # The declaration.  Everything the card game asks it to do, it declines
    # — and because this is stated rather than tuned, the staple floor stays
    # intact for every other style (see ABSTAIN_BIAS).  Coronation is NOT on
    # the list: a King is a chess piece, and §17 makes crowning one free.
    abstains=frozenset({
        "SUMMON", "RITUAL", "CONSTRUCTION",
        "SPELL", "TRAP", "ACTIVATE_TRAP", "MONSTER_ABILITY",
    }),
)

# ── The Rogue's two stances (README §51-style, but private) ──────────────
# Not roster entries: nobody picks "Hoarding" from the menu.  They exist so
# the Rogue can be expressed the same way the Opportunist is — as a blend —
# rather than as a second adaptive mechanism.

_ROGUE_HOARD = Personality(
    key="rogue_hoard",
    name="Rogue (hoarding)",
    blurb="Plays chess and keeps its powder dry.",
    weight_tilt={
        # Cards in hand are the whole point of the stance.
        "card_in_hand": 2.2,
        "card_in_deck": 1.5,
        "hand_summonable": 1.6,
        "hand_playable": 1.5,
        # Meanwhile: play solid, unspectacular chess.
        "material": 1.25,
        "board_control_square": 1.25,
        "board_control_centrality": 1.2,
        "king_safety_attacker": 1.2,
        "hanging_penalty": 1.3,
        # Don't chase the long games yet.
        "ritual_progress": 0.7,
        "building_complete": 0.75,
        "territory_square": 0.75,
        "duel_pressure": 0.8,
    },
    bias_tilt={
        # Floored, not abstained — the Rogue *saves* its resources, it does
        # not swear off them.  A free Ritual is still a free Ritual.
        "SUMMON": 0.6,
        "RITUAL": 0.6,
        "CONSTRUCTION": 0.6,
        "SPELL": 0.6,
        "TRAP": 0.6,
        "MERCENARY": 0.6,
        "RECOMPOSE": 2.0,                # a negative bias — churn the hand less
    },
)

_ROGUE_SPEND = Personality(
    key="rogue_spend",
    name="Rogue (spending)",
    blurb="Empties the hand and closes the game out.",
    weight_tilt={
        # A card still in hand at the end is a card that did nothing.
        "card_in_hand": 0.6,
        "card_in_deck": 0.6,
        # Everything it was saving for.
        "ritual_progress": 2.2,
        "ritual_revealed": 2.0,
        "ritual_activated": 2.4,
        "ritual_monster": 2.0,
        "monster_unit": 1.8,
        "monster_effect": 1.6,
        "building_complete": 1.4,
        # ... spent on ending it.
        "enemy_king_check": 1.8,
        "enemy_king_attacker": 1.8,
        "duel_pressure": 2.2,
        "hanging_penalty": 0.8,          # no time left to be careful
    },
    bias_tilt={
        "SUMMON": 2.0,
        "RITUAL": 2.0,
        "SPELL": 1.8,
        "TRAP": 1.5,
        "ACTIVATE_TRAP": 1.5,
        "MONSTER_ABILITY": 1.5,
        "MERCENARY": 1.8,
        "CONSTRUCTION": 1.2,
        "RECOMPOSE": 0.6,                # now worth digging for the last card
        "DUEL_STRIKE": 1.3,
    },
)

ROGUE = Personality(
    key="rogue",
    name="Rogue",
    blurb="Hoards its cards, then spends everything late.",
    priorities=(
        "Early    chess, and saving cards",
        "Late     every resource at once",
        "Switch   as the endgame arrives",
        "Reads    material, turn, deck, Rituals",
    ),
    blend=lambda obs: rogue_mix(obs),
)

OPPORTUNIST = Personality(
    key="opportunist",
    name="Opportunist",
    blurb="Re-reads the board and re-mixes the other four.",
    priorities=(
        "Adapts to  board state",
        "           revealed Rituals",
        "           King policy",
        "           current hand",
        "           opponent weaknesses",
    ),
    blend=opportunist_mix,
)


#: Every play style, README §51's five in the order it lists them, then the
#: two the game added after it.
PERSONALITIES: "dict[str, Personality]" = {
    p.key: p for p in (
        CONQUEROR, ARCHITECT, RITUALIST, ASSASSIN, OPPORTUNIST,
        CHESS_PURIST, ROGUE,
    )
}

#: What a ``blend`` may name: every play style, plus the private stances an
#: adaptive style switches between.  Stances are not selectable — nobody
#: picks "Rogue (hoarding)" off a menu — they are how an adaptive style is
#: written down.
_STANCES: "dict[str, Personality]" = {
    **PERSONALITIES,
    _ROGUE_HOARD.key: _ROGUE_HOARD,
    _ROGUE_SPEND.key: _ROGUE_SPEND,
}

PERSONALITY_KEYS: list[str] = list(PERSONALITIES)

DEFAULT_PERSONALITY: str = "conqueror"


def get_personality(key: "str | Personality") -> Personality:
    """Look a play style up by key.  Accepts a ``Personality`` unchanged."""
    if isinstance(key, Personality):
        return key
    try:
        return PERSONALITIES[key.lower()]
    except (KeyError, AttributeError):
        raise ValueError(
            f"Unknown personality {key!r} "
            f"(expected one of {', '.join(PERSONALITY_KEYS)})"
        ) from None
