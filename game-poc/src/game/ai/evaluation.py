"""
Weighted State Evaluation — AI Stage 1 (README §46)
=====================================================

README §46 asks for a *weighted state evaluation* over these categories:

    material · king safety · enemy king pressure · board control ·
    monster value · pawn economic value · building value · Territory ·
    Ritual progress · card advantage · hand quality · Royal Support ·
    Final Duel probability

``evaluate_observation`` produces exactly that, as

    score = material + king_safety + enemy_king_pressure + board_control
          + monster_value + pawn_economy + building_value + territory
          + ritual_progress + card_advantage + hand_quality
          + royal_support + duel_probability

Every term is scored from the acting player's point of view: positive is
good for ``observation.player_id``.

Information rules (README §44)
------------------------------
This module reads an **Observation** and nothing else.  It never sees a
GameState, an opponent hand, a hidden King, or a sealed Ritual.

The optional ``registry`` is the *card definitions* — the public rulebook,
the same data a human player can read on the cards.  It contains no match
state, so consulting it is not an information leak.  Everything degrades
gracefully when it is omitted.

Board reconstruction
--------------------
``board_from_observation`` rebuilds a ``BoardState`` from the Observation's
public unit list so the existing movement code can answer "what does the
enemy attack?".  Only public information goes in, so the reconstruction is
exactly what a human staring at the board could work out.  It is
deliberately *not* a game simulator: no traps fire, no effects resolve, and
nothing is ever executed against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import ConstructionStatus, PieceType, RevelationState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.observation import Observation


# ─────────────────────────────────────────────────────────────────────────────
# Piece values
# ─────────────────────────────────────────────────────────────────────────────

PIECE_VALUES: dict[str, float] = {
    "pawn": 1.0,
    "knight": 3.0,
    "bishop": 3.25,
    "rook": 5.0,
    "queen": 9.0,
    # The King is not "material" — losing it triggers a Final Duel rather
    # than ending the match (README §22) — but it must dominate every
    # capture ordering the bot ever considers.
    "king": 100.0,
}


def piece_value(piece_type: str) -> float:
    return PIECE_VALUES.get(piece_type, 1.0)


def centrality(pos: "Position") -> float:
    """0.0 on the rim, 1.0 in the four central squares (d4/d5/e4/e5)."""
    # Distance from the board centre is 0.5 at the centre squares and 3.5
    # at the rim, so normalise over that 3.0-wide span.
    return (3.5 - max(abs(pos.file - 3.5), abs(pos.rank - 3.5))) / 3.0


def chebyshev(a: "Position", b: "Position") -> int:
    return max(abs(a.file - b.file), abs(a.rank - b.rank))


# ─────────────────────────────────────────────────────────────────────────────
# Weights
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvalWeights:
    """
    Tunable coefficients for every README §46 category.

    Defaults are hand-set for the PoC: material dominates, the King terms
    are the next loudest, and the strategic systems (Buildings, Rituals,
    Territory) are worth roughly a minor piece each when fully developed —
    enough to be pursued, not enough to trade a Queen for.  README §42
    telemetry is what these get tuned against.
    """

    material: float = 1.0

    king_safety_check: float = 4.0          # own King in check
    king_safety_attacker: float = 0.5       # per enemy unit near own King
    king_safety_radius: int = 2

    enemy_king_check: float = 4.0           # opponent in check
    enemy_king_attacker: float = 0.6        # per own unit near enemy King
    enemy_king_radius: int = 2

    board_control_square: float = 0.12      # per attacked square, by centrality
    board_control_centrality: float = 0.20  # per own unit, by centrality

    monster_unit: float = 1.5               # per own Monster on the board
    monster_effect: float = 0.25            # per effect that Monster carries
    ritual_monster: float = 3.0             # ritual_only Monsters are the payoff

    pawn_base: float = 0.15                 # per own Pawn (economy, not material)
    pawn_advance: float = 0.12              # per rank advanced toward promotion

    building_complete: float = 2.0
    building_under_construction: float = 0.75
    building_integrity: float = 0.25        # per remaining integrity point

    territory_square: float = 0.05

    ritual_progress: float = 0.30           # per accumulated progress point
    ritual_foretold: float = 0.75
    ritual_revealed: float = 1.5
    ritual_activated: float = 4.0

    card_in_hand: float = 0.30
    card_in_deck: float = 0.04

    hand_summonable: float = 0.35           # a Monster with a vessel on the board
    hand_playable: float = 0.15             # a Spell/Trap in hand

    royal_support: float = 0.35             # per own unit escorting the King
    royal_support_radius: int = 2

    duel_pressure: float = 0.8              # attacker-side Final Duel readiness
    duel_exposure: float = 0.8              # defender-side Final Duel risk

    # Threat awareness (used by the action scorer, kept here so all tuning
    # lives in one object).
    hanging_penalty: float = 0.55           # fraction of value lost when en prise
    defended_discount: float = 0.5          # trade is cheaper when defended
    hazard_penalty: float = 1.0             # entering an enemy zone effect
    trap_penalty: float = 1.5               # entering an enemy Trap radius


DEFAULT_WEIGHTS = EvalWeights()


# ─────────────────────────────────────────────────────────────────────────────
# Board reconstruction (public information only)
# ─────────────────────────────────────────────────────────────────────────────

def board_from_observation(obs: "Observation") -> BoardState:
    """
    Rebuild a ``BoardState`` from the Observation's public unit list.

    Unit positions, types, owners, Monster identities and statuses are all
    public (README §35 "Public": the board, all units, all Monsters), so
    this reconstruction adds no information the observer did not already
    have.  It exists so the bot can reuse ``game.chess.movement`` instead of
    reimplementing chess geometry.
    """
    board = BoardState()
    for info in obs.board.units:
        try:
            piece_type = PieceType(info.piece_type)
        except ValueError:  # pragma: no cover - defensive
            continue
        piece = ChessPiece(id=info.piece_id, owner=info.owner, piece_type=piece_type)
        unit = UnitInstance(piece=piece, monster_id=info.monster_id)
        unit.statuses = list(info.statuses)
        board.place_unit(info.position, unit)
    return board


def _pawn_attack_squares(pos: "Position", owner: str) -> list["Position"]:
    """A Pawn attacks diagonally; its forward push is not a threat."""
    direction = 1 if owner == "white" else -1
    rank = pos.rank + direction
    if not 0 <= rank <= 7:
        return []
    out = []
    for file in (pos.file - 1, pos.file + 1):
        if 0 <= file <= 7:
            out.append(Position(file=file, rank=rank))
    return out


def attacked_squares(
    board: BoardState,
    owner: str,
    registry: "CardRegistry | None" = None,
) -> set["Position"]:
    """
    Every square ``owner`` currently attacks.

    Pseudo-legal (pins and self-check are ignored) — a greedy bot wants the
    threat map, not a perfectly legal move list.  Pawns contribute their
    capture diagonals rather than their pushes.
    """
    from game.chess.movement import get_pseudo_legal_moves

    squares: set[Position] = set()
    for pos, unit in board.all_units_for(owner):
        if unit.piece.piece_type == PieceType.PAWN and unit.monster_id is None:
            squares.update(_pawn_attack_squares(pos, owner))
            continue
        try:
            squares.update(
                get_pseudo_legal_moves(board, pos, unit, registry=registry)
            )
        except Exception:  # noqa: BLE001 - never let evaluation break play
            continue
    return squares


# ─────────────────────────────────────────────────────────────────────────────
# Category scoring
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvalContext:
    """
    Everything derived once per decision and reused by every action score.

    Building it costs one board reconstruction and two threat maps; scoring
    forty candidate actions against it costs almost nothing.
    """

    obs: "Observation"
    weights: EvalWeights = DEFAULT_WEIGHTS
    registry: "CardRegistry | None" = None

    board: BoardState = field(init=False)
    me: str = field(init=False)
    opponent: str = field(init=False)
    my_attacks: set["Position"] = field(init=False)
    enemy_attacks: set["Position"] = field(init=False)
    my_king: "Position | None" = field(init=False)
    enemy_king: "Position | None" = field(init=False)
    units_by_pos: dict["Position", object] = field(init=False)

    def __post_init__(self) -> None:
        obs = self.obs
        self.me = obs.player_id
        self.opponent = "black" if self.me == "white" else "white"
        self.board = board_from_observation(obs)
        self.my_attacks = attacked_squares(self.board, self.me, self.registry)
        self.enemy_attacks = attacked_squares(self.board, self.opponent, self.registry)
        self.units_by_pos = {u.position: u for u in obs.board.units}
        self.my_king = None
        self.enemy_king = None
        for unit in obs.board.units:
            if unit.piece_type != "king":
                continue
            if unit.owner == self.me:
                self.my_king = unit.position
            else:
                self.enemy_king = unit.position

    def unit_at(self, pos: "Position"):
        return self.units_by_pos.get(pos)


def _monster_bonus(
    card_id: str | None,
    weights: EvalWeights,
    registry: "CardRegistry | None",
) -> float:
    """Monster value — README §46's "monster value" category."""
    if card_id is None:
        return 0.0
    score = weights.monster_unit
    if registry is None:
        return score
    try:
        card = registry.get(card_id)
    except (KeyError, AttributeError):
        return score
    score += weights.monster_effect * len(getattr(card, "effects", ()))
    if getattr(card, "ritual_only", False):
        score += weights.ritual_monster
    return score


def evaluation_breakdown(
    obs: "Observation",
    weights: EvalWeights = DEFAULT_WEIGHTS,
    registry: "CardRegistry | None" = None,
    ctx: "EvalContext | None" = None,
) -> dict[str, float]:
    """
    Score the position by README §46 category, from ``obs.player_id``'s
    point of view.  Returned as a breakdown so the weights can be tuned
    against telemetry (and so tests can assert on one category at a time).
    """
    ctx = ctx or EvalContext(obs=obs, weights=weights, registry=registry)
    me, opp = ctx.me, ctx.opponent
    w = weights

    material = 0.0
    monster_value = 0.0
    pawn_economy = 0.0
    board_control = 0.0
    king_safety = 0.0
    enemy_pressure = 0.0
    royal_support = 0.0

    for unit in obs.board.units:
        mine = unit.owner == me
        sign = 1.0 if mine else -1.0

        if unit.piece_type != "king":
            material += sign * w.material * piece_value(unit.piece_type)

        monster_value += sign * _monster_bonus(unit.monster_id, w, registry)

        if unit.piece_type == "pawn":
            # README §46 "pawn economic value": a Pawn is a Vessel, a
            # builder, and Ritual material — worth more than its 1 point.
            # Advancement is measured from that Pawn's OWN start rank
            # (white starts on rank index 1, black on index 6).
            if unit.owner == "white":
                advance = unit.position.rank - 1
            else:
                advance = 6 - unit.position.rank
            pawn_economy += sign * (w.pawn_base + w.pawn_advance * max(advance, 0))

        if mine:
            board_control += w.board_control_centrality * centrality(unit.position)
            if ctx.my_king is not None and unit.piece_type != "king":
                if chebyshev(unit.position, ctx.my_king) <= w.royal_support_radius:
                    royal_support += w.royal_support

        if ctx.my_king is not None and not mine:
            if chebyshev(unit.position, ctx.my_king) <= w.king_safety_radius:
                king_safety -= w.king_safety_attacker
        if ctx.enemy_king is not None and mine:
            if chebyshev(unit.position, ctx.enemy_king) <= w.enemy_king_radius:
                enemy_pressure += w.enemy_king_attacker

    if obs.own_in_check:
        king_safety -= w.king_safety_check
    if obs.opponent_in_check:
        enemy_pressure += w.enemy_king_check

    # Space: squares each side attacks, weighted toward the centre.
    for pos in ctx.my_attacks:
        board_control += w.board_control_square * centrality(pos)
    for pos in ctx.enemy_attacks:
        board_control -= w.board_control_square * centrality(pos)

    # Buildings (README §11–12)
    building_value = 0.0
    for building in obs.board.building_locations:
        sign = 1.0 if getattr(building, "owner", None) == me else -1.0
        if getattr(building, "status", None) == ConstructionStatus.COMPLETE:
            building_value += sign * (
                w.building_complete
                + w.building_integrity * getattr(building, "integrity", 0)
            )
        else:
            building_value += sign * w.building_under_construction

    # Territory (README §13)
    territory = w.territory_square * (
        len(obs.own_territory) - len(obs.opponent_territory)
    )

    # Ritual progress (README §14–15).  Only OWN Rituals are readable in
    # detail; the opponent's are scored from their public revelation state,
    # which is exactly what §15 says leaks.
    ritual_progress = 0.0
    for rs in obs.own_rituals:
        if getattr(rs, "activated", False):
            ritual_progress += w.ritual_activated
            continue
        ritual_progress += w.ritual_progress * getattr(rs, "progress", 0)
        revelation = getattr(rs, "revelation", None)
        if revelation == RevelationState.FORETOLD:
            ritual_progress += w.ritual_foretold
        elif revelation == RevelationState.REVEALED:
            ritual_progress += w.ritual_revealed
    for info in obs.opponent_ritual_info:
        if info.revelation == RevelationState.REVEALED:
            ritual_progress -= w.ritual_revealed
        elif info.revelation == RevelationState.FORETOLD:
            ritual_progress -= w.ritual_foretold

    # Card advantage (README §46).  Only counts are known for the opponent.
    card_advantage = (
        w.card_in_hand * (len(obs.own_hand) - obs.opponent_hand_count)
        + w.card_in_deck * (obs.own_deck_count - obs.opponent_deck_count)
    )

    hand_quality_score = hand_quality(obs, ctx, w, registry)

    # Final Duel probability (README §31): whoever is closer to forcing the
    # Duel on favourable terms.  Approximated from King exposure on both
    # sides, scaled by the Royal Support each King can actually call on.
    duel = 0.0
    if ctx.enemy_king is not None and ctx.enemy_king in ctx.my_attacks:
        duel += w.duel_pressure
    if ctx.my_king is not None and ctx.my_king in ctx.enemy_attacks:
        duel -= w.duel_exposure

    return {
        "material": material,
        "king_safety": king_safety,
        "enemy_king_pressure": enemy_pressure,
        "board_control": board_control,
        "monster_value": monster_value,
        "pawn_economy": pawn_economy,
        "building_value": building_value,
        "territory": territory,
        "ritual_progress": ritual_progress,
        "card_advantage": card_advantage,
        "hand_quality": hand_quality_score,
        "royal_support": royal_support,
        "duel_probability": duel,
    }


def hand_quality(
    obs: "Observation",
    ctx: EvalContext,
    w: EvalWeights,
    registry: "CardRegistry | None",
) -> float:
    """
    README §46 "hand quality": cards you can actually use beat cards you
    cannot.  A Monster only counts when a legal Vessel type is currently on
    the board — otherwise it is a dead card.
    """
    if registry is None:
        return w.hand_playable * len(obs.own_hand)

    from game.cards.card import MonsterCard, SpellCard, TrapCard

    own_vessels = {
        u.piece_type for u in obs.board.units
        if u.owner == ctx.me and u.monster_id is None
    }
    score = 0.0
    for card_id in obs.own_hand:
        try:
            card = registry.get(card_id)
        except (KeyError, AttributeError):
            continue
        if isinstance(card, MonsterCard):
            if own_vessels.intersection(card.supported_vessels):
                score += w.hand_summonable
        elif isinstance(card, (SpellCard, TrapCard)):
            score += w.hand_playable
    return score


def evaluate_observation(
    obs: "Observation",
    weights: EvalWeights = DEFAULT_WEIGHTS,
    registry: "CardRegistry | None" = None,
    ctx: "EvalContext | None" = None,
) -> float:
    """
    README §46's conceptual evaluation, summed:

        score = material_score + king_safety_score + board_control_score
              + building_score + ritual_score + card_advantage_score
              + duel_score + …
    """
    return sum(
        evaluation_breakdown(obs, weights, registry, ctx).values()
    )
