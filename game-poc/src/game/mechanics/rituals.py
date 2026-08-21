"""
mechanics/rituals.py — Stage 11: Ritual Pool, pattern matching, sacrifice & summoning
=======================================================================================

Companion to mechanics/kings.py, for the Ritual system (README §14-§15):
hidden Ritual Monsters, progressive revelation, and the board-pattern /
material / state matcher that decides whether a sacrifice satisfies a
Ritual's requirements.

Lifecycle (state lives on PlayerState.ritual_pool — core/state.py's
RitualState list):

    Game setup (core/game.py Game.new())
        → assign_random_ritual_pool() draws a random 3-of-N subset of
          data/rituals.yaml's roster (13 entries spanning every Monster
          archetype) for each player, all SEALED — mirrors King Pool
          assignment (mechanics/kings.py assign_random_king_pool)'s "pool
          of 3" model.

    ActivateRitual (core/rules.py._execute_activate_ritual)
        → validate_ritual() checks the proposed sacrifice against the
          Ritual's condition (formation / material / state — see below),
          execute_ritual() removes the pure-sacrifice pieces, transforms
          the Vessel (the LAST sacrifice_positions entry) into the summoned
          Monster, and forces the Ritual to REVEALED (its identity is now
          physically on the board — there's nothing left to hide).

    Revelation triggers (README §15.1), all advancing exactly ONE step
    (SEALED→FORETOLD or FORETOLD→REVEALED, never skipping a step):
        • ritual_progress_boost (ritual_acolyte) — advance_ritual_progress(),
          called from core/rules.py._execute_end_turn.
        • "being checked" — on_check_detected(), called from
          core/rules.py._update_check_status.
        • "losing the Queen" — on_piece_lost(), called from
          core/rules.py._execute_move_piece's capture path.
        • Voluntary reveal-for-value (README §15.2) — omen_reader's
          ritual_reveal_tradeoff (mechanics/effects/ritual.py) and
          raven_scout's reveal_hidden_info targeting an ENEMY Ritual
          (mechanics/effects/information.py) both call
          promote_one_step()/reveal_random_sealed() here directly.
        • Successfully completing a Ritual (see above) forces REVEALED
          regardless of its prior state.

Condition families (README §14.1) — the PoC folds "Control Ritual" and
"Tactical Ritual" into "state" (a small named-predicate set), since both
are really "some fact about the current game state must hold":

    formation — pattern: tuple of {"offset": [df, dr], "piece_type": str,
                "anchor": bool} nodes. The anchor node is the Vessel;
                offsets are absolute board deltas from it (not mirrored per
                player color — a deliberate PoC simplification, see
                RitualCard's docstring).
    material  — sacrificed pieces' combined chess value (PIECE_VALUE below)
                must reach RitualCard.min_material.
    state     — every predicate name in RitualCard.state_checks must hold
                (see _STATE_PREDICATES).

In every family, ActivateRitual.sacrifice_positions is read as
[...pure sacrifices..., vessel] — the LAST position survives and hosts the
summoned Monster; every earlier position is destroyed outright. A piece
already hosting a Monster, or the King, is never eligible (mirrors
SummonMonster's own KING_NOT_VESSEL rule, plus a PoC simplification that
avoids double-tribute bookkeeping for an already-summoned Monster).
"""

from __future__ import annotations

import re
from itertools import permutations
from typing import TYPE_CHECKING

from game.chess.pieces import Position
from game.core.phases import ConstructionStatus, PieceType, RevelationState

if TYPE_CHECKING:
    from game.cards.card import RitualCard
    from game.chess.pieces import UnitInstance
    from game.core.events import Event
    from game.core.rng import DeterministicRNG
    from game.core.state import GameState, PlayerState, RitualState


# README §14.1 "Material Ritual" — standard chess relative values.
PIECE_VALUE: dict[PieceType, int] = {
    PieceType.PAWN: 1,
    PieceType.KNIGHT: 3,
    PieceType.BISHOP: 3,
    PieceType.ROOK: 5,
    PieceType.QUEEN: 9,
}


# ─────────────────────────────────────────────────────────────────────────────
# Setup — Ritual Pool assignment
# ─────────────────────────────────────────────────────────────────────────────

def assign_random_ritual_pool(
    player: "PlayerState", registry: "object | None", rng: "DeterministicRNG",
) -> None:
    """
    Draw a random 3-of-N Ritual Pool for ``player`` from ``registry``'s full
    roster (data/rituals.yaml ships 13, spanning every Monster archetype),
    all SEALED — mirrors mechanics/kings.py assign_random_king_pool's "pool
    of 3" model, minus the guaranteed home-archetype slot (Rituals aren't
    tied to the player's drafted King archetype). No-op — the caller's
    existing ritual_pool is left untouched — if ``registry`` is None,
    carries no Ritual cards, or the roster has 3 or fewer entries (draw
    everything), so registry-less callers (unit tests, Game.new() without a
    registry) don't crash.
    """
    from game.core.state import RitualState

    if registry is None:
        return
    rituals = registry.all_rituals()
    if not rituals:
        return
    ids = [r.id for r in rituals]
    rng.shuffle(ids)
    player.ritual_pool = [RitualState(ritual_id=rid) for rid in ids[:3]]


def get_ritual_state(state: "GameState", player_id: str, ritual_id: str) -> "RitualState | None":
    """The RitualState for ``ritual_id`` in ``player_id``'s pool, or None."""
    ps = state.get_player(player_id)
    return next((rs for rs in ps.ritual_pool if rs.ritual_id == ritual_id), None)


# ─────────────────────────────────────────────────────────────────────────────
# Effective requirements — forbidden_priest's requirement_reduction
# ─────────────────────────────────────────────────────────────────────────────

def _effective_min_material(ritual: "RitualCard", rstate: "RitualState") -> int:
    return max(0, ritual.min_material - rstate.requirement_reduction)


def _effective_min_sacrifices(ritual: "RitualCard", rstate: "RitualState") -> int:
    return max(1, ritual.min_sacrifices - rstate.requirement_reduction)


def _effective_pattern_nodes(ritual: "RitualCard", rstate: "RitualState") -> list[dict]:
    """
    Non-anchor pattern nodes still required, after requirement_reduction
    waives that many. Drops from the END of the (tiny, fixed) YAML list —
    an arbitrary but deterministic choice.
    """
    non_anchor = [n for n in ritual.pattern if not n.get("anchor")]
    if rstate.requirement_reduction <= 0:
        return non_anchor
    keep = max(0, len(non_anchor) - rstate.requirement_reduction)
    return non_anchor[:keep]


def _ritual_range_bonus(state: "GameState", player_id: str, registry: "object | None") -> int:
    """
    herald_of_the_gate's ``ritual_activation_range`` — sum of
    ``radius_bonus`` from every owned Monster carrying this effect (any
    that has been summoned — not scoped to proximity to the formation
    itself, matching circle_keeper's ritual_pattern_substitute's own
    "keeps it scoped to this unit, wherever it stands" simplification).
    Used by the Formation Ritual matcher below to accept a candidate
    within Chebyshev distance <= this bonus of a pattern node's exact
    offset, instead of requiring an exact match.
    """
    if registry is None:
        return 0
    from game.cards.card import MonsterCard

    bonus = 0
    for _pos, unit in state.board.all_units_for(player_id):
        for status in unit.statuses:
            if status.startswith("ritual_range_bonus:"):
                bonus += int(status.split(":")[1])
    return bonus


def _anchor_node(ritual: "RitualCard") -> dict:
    return next(
        (n for n in ritual.pattern if n.get("anchor")),
        ritual.pattern[0] if ritual.pattern else {},
    )


def _node_matches(node: dict, unit: "UnitInstance") -> bool:
    """
    True if ``unit`` satisfies pattern ``node``: an exact piece_type match,
    a wildcard ("any"), or circle_keeper's ritual_substitute status
    (mechanics/effects/ritual.py _ritual_pattern_substitute — "counts as
    one compatible generic ritual component").
    """
    wanted = node.get("piece_type", "any")
    if wanted == "any":
        return True
    if unit.piece.piece_type.value == wanted:
        return True
    return any(s.startswith("ritual_substitute") for s in unit.statuses)


# ─────────────────────────────────────────────────────────────────────────────
# Shared sacrifice-eligibility check
# ─────────────────────────────────────────────────────────────────────────────

def _own_sacrificeable_pieces(
    state: "GameState", player_id: str, positions: "list[Position]",
) -> "list[tuple[Position, UnitInstance]] | None":
    """
    Resolve ``positions`` to (Position, UnitInstance) pairs in order, or
    None if any position fails eligibility: must be occupied by the
    player's own piece and not be the King. A piece already hosting a
    Monster IS eligible as a PURE sacrifice (destroyed together with its
    Monster, mirroring README §3.4's "if the Monster is destroyed, its
    Vessel is also lost" — and the one card that cares,
    circle_keeper/ritual_pattern_substitute, explicitly wants to be usable
    as ritual material). Only the VESSEL — the last position, checked
    separately by each condition matcher below — must NOT already host a
    Monster, since it's about to host a new one.
    """
    out: list[tuple[Position, "UnitInstance"]] = []
    for pos in positions:
        unit = state.board.get_unit(pos)
        if unit is None or unit.owner != player_id:
            return None
        if unit.piece.piece_type == PieceType.KING:
            return None
        out.append((pos, unit))
    return out


def _vessel_is_untransformed(vessel_unit: "UnitInstance") -> bool:
    return vessel_unit.monster_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Condition matchers — validation (arbitrary caller-supplied positions)
# ─────────────────────────────────────────────────────────────────────────────

def _formation_candidate_ok(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    positions: "list[Position]", registry: "object | None" = None,
) -> bool:
    resolved = _own_sacrificeable_pieces(state, player_id, positions)
    if not resolved:
        return False
    vessel_pos, vessel_unit = resolved[-1]
    if not _vessel_is_untransformed(vessel_unit):
        return False
    anchor = _anchor_node(ritual)
    if not _node_matches(anchor, vessel_unit):
        return False
    if ritual.required_vessel and vessel_unit.piece.piece_type.value != ritual.required_vessel:
        return False

    needed_nodes = _effective_pattern_nodes(ritual, rstate)
    others = resolved[:-1]
    if len(others) != len(needed_nodes):
        return False
    if not needed_nodes:
        return True

    # herald_of_the_gate's ritual_activation_range: a non-anchor node
    # accepts any candidate within Chebyshev <= range_bonus of its exact
    # offset, not only the exact square ("eligible components within 1
    # extra square as connected").
    range_bonus = _ritual_range_bonus(state, player_id, registry)

    ax, ar = vessel_pos.file, vessel_pos.rank
    for perm in permutations(others):
        if all(
            max(abs(pos.file - (ax + node["offset"][0])), abs(pos.rank - (ar + node["offset"][1]))) <= range_bonus
            and _node_matches(node, unit)
            for node, (pos, unit) in zip(needed_nodes, perm)
        ):
            return True
    return False


def _material_candidate_ok(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    positions: "list[Position]",
) -> bool:
    resolved = _own_sacrificeable_pieces(state, player_id, positions)
    if not resolved:
        return False
    if len(resolved) < _effective_min_sacrifices(ritual, rstate):
        return False
    _vessel_pos, vessel_unit = resolved[-1]
    if not _vessel_is_untransformed(vessel_unit):
        return False
    if ritual.required_vessel and vessel_unit.piece.piece_type.value != ritual.required_vessel:
        return False
    total = sum(PIECE_VALUE.get(u.piece.piece_type, 0) for _, u in resolved)
    return total >= _effective_min_material(ritual, rstate)


# ─────────────────────────────────────────────────────────────────────────────
# State predicates — two shapes:
#
#   FLAG predicates — plain names (queen_lost, king_in_check,
#   ritual_revealed, vessel_inside_enemy_territory). Each takes
#   (state, player_id, positions, registry) → bool. ``positions`` is the
#   candidate ActivateRitual.sacrifice_positions being evaluated (the
#   Vessel is positions[-1]) — needed by the positional checks
#   (vessel_inside_enemy_territory); the non-positional flags ignore it.
#
#   THRESHOLD predicates — "<metric>_at_least_N" / "<metric>_at_most_N" /
#   "<metric>_within_N", parsed by _THRESHOLD_RE. Each metric function has
#   the same (state, player_id, positions, registry) signature and returns
#   an int (or None if the underlying system doesn't exist yet — see
#   enemy_royal_support below — which makes the check permanently False,
#   never raising, matching the codebase's STUB convention).
# ─────────────────────────────────────────────────────────────────────────────

_THRESHOLD_RE = re.compile(r"^(.+)_(at_least|at_most|within)_(\d+)$")


def _flag_queen_lost(state, player_id, positions, registry) -> bool:
    """README §14.1 State Ritual example: "Queen has been destroyed"."""
    return not any(
        u.piece.piece_type == PieceType.QUEEN
        for _, u in state.board.all_units_for(player_id)
    )


def _flag_king_in_check(state, player_id, positions, registry) -> bool:
    """README §14.1 Tactical Ritual example: "King currently in check"."""
    return state.get_player(player_id).is_in_check()


def _flag_ritual_revealed(state, player_id, positions, registry) -> bool:
    """README §15.2 flavor: at least one of the owner's OWN Rituals is REVEALED."""
    return any(
        rs.revelation == RevelationState.REVEALED
        for rs in state.get_player(player_id).ritual_pool
    )


def _flag_vessel_inside_enemy_territory(state, player_id, positions, registry) -> bool:
    """Assassin/beast flavor: the candidate Vessel stands in the OPPONENT's Territory."""
    if not positions or registry is None:
        return False
    from game.mechanics.territory import is_in_territory

    vessel_pos = positions[-1]
    return is_in_territory(state, vessel_pos, state.opponent_of(player_id), registry)


_FLAG_PREDICATES: dict = {
    "queen_lost": _flag_queen_lost,
    "king_in_check": _flag_king_in_check,
    "ritual_revealed": _flag_ritual_revealed,
    "vessel_inside_enemy_territory": _flag_vessel_inside_enemy_territory,
}


def _metric_hand_size(state, player_id, positions, registry) -> int:
    return len(state.get_player(player_id).hand)


def _metric_buildings(state, player_id, positions, registry) -> int:
    return sum(
        1 for b in state.buildings
        if b.owner == player_id and b.status == ConstructionStatus.COMPLETE
    )


def _metric_friendly_territory_squares(state, player_id, positions, registry) -> int:
    if registry is None:
        return 0
    from game.mechanics.territory import territory_squares
    return len(territory_squares(state, player_id, registry))


def _metric_pawns_built(state, player_id, positions, registry) -> int:
    """
    Proxy metric: own Pawns currently on the board that have already spent
    their once-per-match Builder right (UnitInstance.builder_available ==
    False). No dedicated lifetime counter exists for "Pawns that have ever
    completed a Building" — a Pawn that built and was later captured isn't
    counted. Documented simplification, not a bug.
    """
    return sum(
        1 for _pos, u in state.board.all_units_for(player_id)
        if u.piece.piece_type == PieceType.PAWN and not u.builder_available
    )


def _metric_building_destroyed_or_completed(state, player_id, positions, registry) -> int:
    return sum(
        1 for b in state.buildings
        if b.owner == player_id
        and b.status in (ConstructionStatus.COMPLETE, ConstructionStatus.DESTROYED)
    )


def _metric_graveyard_cards(state, player_id, positions, registry) -> int:
    return len(state.get_player(player_id).graveyard)


def _metric_allied_monsters_destroyed(state, player_id, positions, registry) -> int:
    """core/state.py PlayerState.monsters_lost_count — a lifetime counter
    incremented in core/rules.py wherever the player's own Monster is
    destroyed (MonsterDestroyed emission)."""
    return state.get_player(player_id).monsters_lost_count


def _metric_pieces_remaining(state, player_id, positions, registry) -> int:
    return len(state.board.all_units_for(player_id))


def _metric_enemy_king(state, player_id, positions, registry) -> int:
    """Chebyshev (king-move) distance from the candidate Vessel to the enemy King."""
    if not positions:
        return 999
    vessel_pos = positions[-1]
    found = state.board.find_king(state.opponent_of(player_id))
    if found is None:
        return 999
    king_pos, _king_unit = found
    return max(abs(vessel_pos.file - king_pos.file), abs(vessel_pos.rank - king_pos.rank))


def _metric_enemy_royal_support(state, player_id, positions, registry) -> "int | None":
    """
    Royal Support (README §27) doesn't exist until the Final Duel (Stage
    12+) — returns None so any "enemy_royal_support_..." check is always
    False rather than raising. Documented gap: Rituals gated on this
    predicate (moonless_hunt) can't be completed yet.
    """
    return None


_METRICS: dict = {
    "hand_size": _metric_hand_size,
    "buildings": _metric_buildings,
    "friendly_territory_squares": _metric_friendly_territory_squares,
    "pawns_built": _metric_pawns_built,
    "building_destroyed_or_completed": _metric_building_destroyed_or_completed,
    "graveyard_cards": _metric_graveyard_cards,
    "allied_monsters_destroyed": _metric_allied_monsters_destroyed,
    "pieces_remaining": _metric_pieces_remaining,
    "enemy_king": _metric_enemy_king,
    "enemy_royal_support": _metric_enemy_royal_support,
}


def _state_matches(
    state: "GameState", player_id: str, ritual: "RitualCard",
    positions: "list[Position]", registry: "object | None",
) -> bool:
    for check in ritual.state_checks:
        flag = _FLAG_PREDICATES.get(check)
        if flag is not None:
            if not flag(state, player_id, positions, registry):
                return False
            continue

        match = _THRESHOLD_RE.match(check)
        if match is None:
            return False  # unknown check — never satisfiable, not a crash
        metric_name, op, threshold_str = match.groups()
        metric = _METRICS.get(metric_name)
        if metric is None:
            return False
        value = metric(state, player_id, positions, registry)
        if value is None:
            return False  # underlying system doesn't exist yet (see e.g. enemy_royal_support)
        threshold = int(threshold_str)
        if op == "at_least" and value < threshold:
            return False
        if op in ("at_most", "within") and value > threshold:
            return False
    return True


def _state_candidate_ok(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    positions: "list[Position]", registry: "object | None",
) -> bool:
    resolved = _own_sacrificeable_pieces(state, player_id, positions)
    if not resolved:
        return False
    if len(resolved) < _effective_min_sacrifices(ritual, rstate):
        return False
    _vessel_pos, vessel_unit = resolved[-1]
    if not _vessel_is_untransformed(vessel_unit):
        return False
    if ritual.required_vessel and vessel_unit.piece.piece_type.value != ritual.required_vessel:
        return False
    return _state_matches(state, player_id, ritual, positions, registry)


def validate_ritual(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    positions: "list[Position]", registry: "object | None" = None,
) -> None:
    """Raise ValueError(reason) if ``positions`` don't satisfy ``ritual``."""
    if rstate.activated:
        raise ValueError(f"Ritual {ritual.name!r} has already been completed.")
    if not positions:
        raise ValueError("A Ritual requires at least one sacrifice (the Vessel).")
    if len(set(positions)) != len(positions):
        raise ValueError("Sacrifice positions must be distinct.")

    if ritual.condition_type == "formation":
        ok = _formation_candidate_ok(state, player_id, ritual, rstate, positions, registry)
    elif ritual.condition_type == "material":
        ok = _material_candidate_ok(state, player_id, ritual, rstate, positions)
    elif ritual.condition_type == "state":
        ok = _state_candidate_ok(state, player_id, ritual, rstate, positions, registry)
    else:
        ok = False

    if not ok:
        raise ValueError(f"Sacrifice does not satisfy the requirements of {ritual.name!r}.")


# ─────────────────────────────────────────────────────────────────────────────
# Candidate enumeration — for get_legal_actions (bounded, no combinatorial
# explosion: formation is fully determined by its anchor; material/state
# use one greedy combo per candidate Vessel rather than every combination).
# ─────────────────────────────────────────────────────────────────────────────

def _own_untransformed(state: "GameState", player_id: str) -> "list[tuple[Position, UnitInstance]]":
    return [
        (pos, unit) for pos, unit in state.board.all_units_for(player_id)
        if unit.monster_id is None and unit.piece.piece_type != PieceType.KING
    ]


def _assign_distinct(candidate_lists: "list[list[Position]]") -> "list[Position] | None":
    """
    Backtracking search for one assignment of DISTINCT positions, one per
    candidate list (in list order). Pattern node counts are tiny (≤3 in
    every current Ritual), so this is cheap. Returns None if no valid
    assignment exists.
    """
    used: set = set()
    result: list[Position] = []

    def backtrack(i: int) -> bool:
        if i == len(candidate_lists):
            return True
        for cand in candidate_lists[i]:
            if cand in used:
                continue
            used.add(cand)
            result.append(cand)
            if backtrack(i + 1):
                return True
            result.pop()
            used.discard(cand)
        return False

    return result if backtrack(0) else None


def _find_formation_candidates(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    registry: "object | None" = None,
) -> "list[list[Position]]":
    anchor = _anchor_node(ritual)
    wanted_anchor_type = ritual.required_vessel or anchor.get("piece_type")
    needed_nodes = _effective_pattern_nodes(ritual, rstate)
    # herald_of_the_gate's ritual_activation_range — see _ritual_range_bonus.
    range_bonus = _ritual_range_bonus(state, player_id, registry)

    out: list[list[Position]] = []
    for pos, unit in _own_untransformed(state, player_id):
        if wanted_anchor_type and wanted_anchor_type != "any" and unit.piece.piece_type.value != wanted_anchor_type:
            continue

        node_candidates: list[list[Position]] = []
        ok = True
        for node in needed_nodes:
            df, dr = node["offset"]
            ef, er = pos.file + df, pos.rank + dr
            candidates: list[Position] = []
            for nf in range(max(0, ef - range_bonus), min(7, ef + range_bonus) + 1):
                for nr in range(max(0, er - range_bonus), min(7, er + range_bonus) + 1):
                    npos = Position(nf, nr)
                    if npos == pos:
                        continue
                    nunit = state.board.get_unit(npos)
                    # Non-anchor nodes MAY be a Monster-hosting piece (a
                    # pure sacrifice destroys the Monster along with its
                    # Vessel — see _own_sacrificeable_pieces) — only the
                    # King is excluded.
                    if (
                        nunit is None or nunit.owner != player_id
                        or nunit.piece.piece_type == PieceType.KING
                        or not _node_matches(node, nunit)
                    ):
                        continue
                    candidates.append(npos)
            if not candidates:
                ok = False
                break
            node_candidates.append(candidates)
        if not ok:
            continue

        assignment = _assign_distinct(node_candidates)
        if assignment is not None:
            out.append(assignment + [pos])
    return out


def _find_material_candidates(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
) -> "list[list[Position]]":
    threshold = _effective_min_material(ritual, rstate)
    min_sac = _effective_min_sacrifices(ritual, rstate)
    own = _own_untransformed(state, player_id)

    out: list[list[Position]] = []
    for vessel_pos, vessel_unit in own:
        if ritual.required_vessel and vessel_unit.piece.piece_type.value != ritual.required_vessel:
            continue
        others = sorted(
            ((p, u) for p, u in own if p != vessel_pos),
            key=lambda pu: PIECE_VALUE.get(pu[1].piece.piece_type, 0),
            reverse=True,
        )
        total = PIECE_VALUE.get(vessel_unit.piece.piece_type, 0)
        chosen: list[Position] = []
        for pos, unit in others:
            # Keep adding — even past the material threshold — until the
            # sacrifice-count floor (min_sacrifices, e.g. pyre_of_dominion's
            # "at least three pieces") is also met.
            if total >= threshold and len(chosen) + 1 >= min_sac:
                break
            chosen.append(pos)
            total += PIECE_VALUE.get(unit.piece.piece_type, 0)
        if total >= threshold and len(chosen) + 1 >= min_sac:
            out.append(chosen + [vessel_pos])
    return out


def _find_state_candidates(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    registry: "object | None",
) -> "list[list[Position]]":
    """
    Unlike formation/material, state_checks can be POSITIONAL (e.g.
    vessel_inside_enemy_territory, enemy_king_within_N depend on which
    piece is proposed as Vessel) — so _state_matches is re-evaluated per
    candidate combo below, not once up front.
    """
    min_sac = _effective_min_sacrifices(ritual, rstate)
    own = _own_untransformed(state, player_id)

    out: list[list[Position]] = []
    for vessel_pos, vessel_unit in own:
        if ritual.required_vessel and vessel_unit.piece.piece_type.value != ritual.required_vessel:
            continue
        if min_sac <= 1:
            candidate = [vessel_pos]
        else:
            others = [p for p, _u in own if p != vessel_pos][: min_sac - 1]
            if len(others) != min_sac - 1:
                continue
            candidate = others + [vessel_pos]
        if _state_matches(state, player_id, ritual, candidate, registry):
            out.append(candidate)
    return out


def find_ritual_candidates(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    registry: "object | None" = None,
) -> "list[list[Position]]":
    """All legal ActivateRitual.sacrifice_positions combos for ``ritual`` right now."""
    if rstate.activated:
        return []
    if ritual.condition_type == "formation":
        return _find_formation_candidates(state, player_id, ritual, rstate, registry)
    if ritual.condition_type == "material":
        return _find_material_candidates(state, player_id, ritual, rstate)
    if ritual.condition_type == "state":
        return _find_state_candidates(state, player_id, ritual, rstate, registry)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Execution — sacrifice + summon (called after validate_ritual has passed)
# ─────────────────────────────────────────────────────────────────────────────

def execute_ritual(
    state: "GameState", player_id: str, ritual: "RitualCard", rstate: "RitualState",
    positions: "list[Position]", events: "list[Event]",
    registry: "object | None" = None,
) -> "tuple[Position, UnitInstance]":
    """
    Remove every pure-sacrifice position, transform the Vessel (the last
    position) into ``ritual.summon_monster_id``, mark the Ritual activated
    and force it REVEALED, and emit RitualActivated (+ RitualRevelationChanged
    if it wasn't already REVEALED). Returns (vessel_position, vessel_unit)
    so the caller can run on-summon effects for the new Monster.

    ``registry`` (optional) additionally applies blood_seer's
    sacrifice_bonus to another of the owner's Rituals — see below.
    """
    from game.core.events import RitualActivated, RitualRevelationChanged

    sacrificed_ids: list[str] = []
    sacrifice_bonus_progress = 0
    for pos in positions[:-1]:
        unit = state.board.remove_unit(pos)
        if unit is not None:
            sacrificed_ids.append(unit.piece.id)
            # blood_seer's sacrifice_bonus: pure (non-Vessel) sacrifices
            # carrying this status add extra progress toward the owner's
            # OTHER Rituals (applied below, after this Ritual is marked
            # complete, so it can't target itself).
            for status in unit.statuses:
                if status.startswith("sacrifice_ritual_bonus:"):
                    sacrifice_bonus_progress += int(status.split(":")[1])

    vessel_pos = positions[-1]
    vessel_unit = state.board.get_unit(vessel_pos)
    sacrificed_ids.append(vessel_unit.piece.id)
    vessel_unit.monster_id = ritual.summon_monster_id

    rstate.activated = True
    old_revelation = rstate.revelation
    rstate.revelation = RevelationState.REVEALED
    rstate.progress = 0

    events.append(RitualActivated(
        player_id=player_id,
        ritual_id=ritual.id,
        sacrificed_piece_ids=tuple(sacrificed_ids),
        summoned_monster_id=ritual.summon_monster_id,
    ))
    if old_revelation != RevelationState.REVEALED:
        events.append(RitualRevelationChanged(
            player_id=player_id, ritual_id=ritual.id,
            old_state=old_revelation, new_state=RevelationState.REVEALED,
        ))

    # blood_seer's sacrifice_bonus — apply accumulated progress to the
    # owner's first not-yet-REVEALED OTHER Ritual (this one is excluded —
    # it just finished), reusing the same threshold-promotion accounting
    # as advance_ritual_progress (ritual_acolyte's end-of-turn boost).
    if sacrifice_bonus_progress > 0 and registry is not None:
        ps = state.get_player(player_id)
        target = next(
            (rs for rs in ps.ritual_pool
             if rs.revelation != RevelationState.REVEALED and rs.ritual_id != ritual.id),
            None,
        )
        if target is not None:
            try:
                target_ritual = registry.get(target.ritual_id)
            except KeyError:
                target_ritual = None
            if target_ritual is not None:
                target.progress += sacrifice_bonus_progress
                threshold = getattr(target_ritual, "reveal_progress_threshold", 3) or 3
                while target.progress >= threshold and target.revelation != RevelationState.REVEALED:
                    target.progress -= threshold
                    promote_one_step(state, player_id, target.ritual_id, events)

    return vessel_pos, vessel_unit


# ─────────────────────────────────────────────────────────────────────────────
# Revelation — one-step promotion + triggers (README §15.1 / §15.2)
# ─────────────────────────────────────────────────────────────────────────────

def promote_one_step(
    state: "GameState", player_id: str, ritual_id: str, events: "list[Event]",
) -> bool:
    """
    Advance ``ritual_id`` by exactly one revelation step (SEALED→FORETOLD
    or FORETOLD→REVEALED; a no-op if already REVEALED or unknown). Returns
    True if it moved, and appends RitualRevelationChanged when it does.
    """
    from game.core.events import RitualRevelationChanged

    rstate = get_ritual_state(state, player_id, ritual_id)
    if rstate is None or rstate.revelation == RevelationState.REVEALED:
        return False
    old = rstate.revelation
    rstate.revelation = (
        RevelationState.FORETOLD if old == RevelationState.SEALED else RevelationState.REVEALED
    )
    events.append(RitualRevelationChanged(
        player_id=player_id, ritual_id=ritual_id, old_state=old, new_state=rstate.revelation,
    ))
    return True


def _promote_first_unrevealed(state: "GameState", player_id: str, events: "list[Event]") -> None:
    """Promote the player's first not-yet-REVEALED Ritual (pool order) by one step."""
    ps = state.get_player(player_id)
    for rstate in ps.ritual_pool:
        if rstate.revelation != RevelationState.REVEALED:
            promote_one_step(state, player_id, rstate.ritual_id, events)
            return


def on_check_detected(state: "GameState", player_id: str, events: "list[Event]", registry: "object | None") -> None:
    """README §15.1 'being checked' revelation trigger."""
    if registry is None:
        return
    _promote_first_unrevealed(state, player_id, events)


def on_piece_lost(
    state: "GameState", owner: str, piece_type: "PieceType", events: "list[Event]", registry: "object | None",
) -> None:
    """README §15.1 'losing the Queen' revelation trigger."""
    if registry is None or piece_type != PieceType.QUEEN:
        return
    _promote_first_unrevealed(state, owner, events)


def reveal_random_sealed(
    state: "GameState", player_id: str, events: "list[Event]", rng: "DeterministicRNG | None" = None,
) -> "str | None":
    """
    raven_scout's on-summon effect: one random SEALED Ritual of
    ``player_id`` becomes FORETOLD. Returns the ritual_id promoted, or None
    if there was no SEALED Ritual to pick. Falls back to the first SEALED
    entry (pool order) when no RNG is supplied (registry-less tests).
    """
    ps = state.get_player(player_id)
    sealed = [rs for rs in ps.ritual_pool if rs.revelation == RevelationState.SEALED]
    if not sealed:
        return None
    chosen = rng.choice(sealed) if rng is not None else sealed[0]
    promote_one_step(state, player_id, chosen.ritual_id, events)
    return chosen.ritual_id


def advance_ritual_progress(state: "GameState", ending_player: str, events: "list[Event]", registry: "object | None") -> None:
    """
    Stage 11 — end-of-turn ``ritual_progress_boost`` processing
    (ritual_acolyte). Called from core/rules.py._execute_end_turn, mirroring
    _resolve_restore_effect_charge's own direct board-scan pattern rather
    than routing through apply_on_summon_effects (that pipeline only fires
    on_summon-compatible triggers; this effect's trigger is "end_of_turn").

    Sums ``amount`` across every matching effect on the ending player's own
    summoned Monsters, and applies the total to the FIRST of their Rituals
    that isn't yet REVEALED (pool order) — promoting it a revelation step
    each time its accumulated progress reaches the RitualCard's
    ``reveal_progress_threshold`` (it can promote more than once from a
    single large boost).
    """
    if registry is None:
        return
    from game.cards.card import MonsterCard

    ps = state.get_player(ending_player)
    target = next((rs for rs in ps.ritual_pool if rs.revelation != RevelationState.REVEALED), None)
    if target is None:
        return
    try:
        ritual = registry.get(target.ritual_id)
    except KeyError:
        return

    total_amount = 0
    for _pos, unit in state.board.all_units_for(ending_player):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.type != "ritual_progress_boost":
                continue
            if effect.params.get("trigger", "end_of_turn") != "end_of_turn":
                continue
            total_amount += effect.params.get("amount", 1)
    if total_amount <= 0:
        return

    target.progress += total_amount
    threshold = getattr(ritual, "reveal_progress_threshold", 3) or 3
    while target.progress >= threshold and target.revelation != RevelationState.REVEALED:
        target.progress -= threshold
        promote_one_step(state, ending_player, target.ritual_id, events)


def advance_ritual_reveal_tradeoff(
    state: "GameState", ending_player: str, events: "list[Event]",
    registry: "object | None", rng: "DeterministicRNG | None" = None,
) -> None:
    """
    Stage 11 — end-of-turn ``ritual_reveal_tradeoff`` processing for the
    ``trigger: once_per_turn`` variant (oracle_of_the_last_star). Mirrors
    advance_ritual_progress's direct board-scan: the on_summon variant
    (mechanics/effects/ritual.py _ritual_reveal_tradeoff) only fires once,
    at summon time; "once per turn" means repeatable every turn, which
    needs its own scan here rather than the one-shot on_summon pipeline.

    For each of the ending player's own summoned Monsters carrying this
    effect+trigger, reveals one step of their first not-yet-REVEALED
    Ritual and draws ``draw_cards`` — same "one step, never skip" rule as
    every other revelation trigger (mechanics.rituals.promote_one_step).
    Multiple sources each fire independently (no stacking cap — the PoC's
    roster only ever ships one such card).
    """
    if registry is None:
        return
    from game.cards.card import MonsterCard
    from game.core.events import CardDrawn, DeckRecycled

    ps = state.get_player(ending_player)
    for _pos, unit in state.board.all_units_for(ending_player):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.type != "ritual_reveal_tradeoff":
                continue
            if effect.params.get("trigger") != "once_per_turn":
                continue

            target = next(
                (rs for rs in ps.ritual_pool if rs.revelation != RevelationState.REVEALED), None,
            )
            if target is None or not promote_one_step(state, ending_player, target.ritual_id, events):
                continue

            count = effect.params.get("draw_cards", 1)
            for _ in range(count):
                if not ps.deck and ps.graveyard:
                    ps.deck = list(ps.graveyard)
                    ps.graveyard.clear()
                    if rng is not None:
                        rng.shuffle(ps.deck)
                    events.append(DeckRecycled(player_id=ending_player, card_count=len(ps.deck)))
                if ps.deck:
                    card_id = ps.deck.pop(0)
                    ps.hand.append(card_id)
                    events.append(CardDrawn(player_id=ending_player, card_id=card_id))
