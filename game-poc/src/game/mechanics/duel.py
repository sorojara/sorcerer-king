"""
mechanics/duel.py — Stage 12: Final Duel engine
==================================================

Implements README §22–§33: King capture and checkmate no longer end the
match directly — they open a short, low-randomness Final Duel that the
kingdom's preserved army, infrastructure, and King policy fight to win or
survive.

Design notes (the numbers README §57 leaves "to be tested" — concrete
choices made here, consistent with how earlier stages resolved their own
open questions):

    Arena          README §26's literal 3×3 sub-board is intentionally
                   abstracted away — rather than a second movement engine,
                   the Duel is an alternating round structure (defender
                   acts, then attacker acts each round) driving three
                   resources: Strikes, Guards, and a Bypass counter. This
                   keeps the Duel short and close to deterministic (README
                   §24) while still expressing every rule in §27–§33.
                   "Advance" (README §32) is kept as a real, always-legal
                   action (a no-op pass) rather than literal arena movement.

    Turn order     Defender acts first each round (they can raise Guards
                   before facing the attacker's Strike that same round),
                   then the attacker acts. This gives the defender agency
                   without needing first-mover asymmetry per Duel type.

    Royal Support  Support eligibility (§27.1) is "within radius of the
                   King" — the arena is centered on the DEFENDER's King
                   (where the fight is), so BOTH sides' Duel Support pools
                   are gathered from their own surviving pieces within that
                   radius (default 2) of the arena center.

    Standardized   Every surviving Pawn/Knight/Bishop/Rook/Queen (README
    abilities      §28) generates ONE spendable DuelSupportItem using its
                   VESSEL piece type (a Monster inherits its Vessel's Duel
                   identity here, same as normal movement — README §3.3;
                   per-Monster duel_ability overrides are flavor labels only
                   in this prototype, a documented Stage 12 scope boundary).
                   Building (Fortress) and King Policy bonuses are ALSO
                   queued items — Duel Actions "Building"/"King Policy"
                   (§32) are just a third spendable-item source, resolved
                   through the same five ability handlers as piece items.

    Win condition  Attacker wins the instant 2 Strikes land (§31). Defender
                   wins (Royal Escape, §33) by surviving 5 rounds. The 3rd
                   Duel against the same King removes the round limit
                   (Last Stand, §17/§25) — a large ``hard_round_cap`` is the
                   only remaining safety valve, guaranteeing termination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.events import (
    DuelRoundAdvanced,
    DuelStrikeResolved,
    DuelSupportSpent,
    GameOver,
    RoyalEscapeTriggered,
)
from game.core.phases import ConstructionStatus, FinalDuelType, Phase, PieceType
from game.core.state import DuelState, DuelSupportItem
from game.mechanics.area import in_area

if TYPE_CHECKING:
    from game.core.actions import FinalDuelAction
    from game.core.events import Event
    from game.core.state import GameState


# ─────────────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SUPPORT_RADIUS = 2

# Standardized vessel → ability mapping (README §28).
_VESSEL_ABILITY: dict[PieceType, str] = {
    PieceType.PAWN: "guard",
    PieceType.KNIGHT: "charge",
    PieceType.BISHOP: "intervention",
    PieceType.ROOK: "fortify",
    PieceType.QUEEN: "command",
}

_ABILITY_LABELS: dict[str, str] = {
    "guard": "Guard",
    "charge": "Charge",
    "intervention": "Intervention",
    "fortify": "Fortify",
    "command": "Command",
}

# Amount granted per vessel type before any multiplier — Queen's Command
# is the standout payoff (README §28: "a powerful tactical action").
_VESSEL_AMOUNT: dict[PieceType, int] = {
    PieceType.PAWN: 1,
    PieceType.KNIGHT: 1,
    PieceType.BISHOP: 1,
    PieceType.ROOK: 1,
    PieceType.QUEEN: 2,
}

_SOURCE_FOR_ACTION = {"support": "piece", "building": "building", "king_policy": "king"}
_ACTION_FOR_SOURCE = {v: k for k, v in _SOURCE_FOR_ACTION.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Legal actions
# ─────────────────────────────────────────────────────────────────────────────

def get_legal_duel_actions(state: "GameState", player_id: str) -> "list[FinalDuelAction]":
    """
    Enumerate this player's legal Duel Actions right now (README §32).

    Turn order is defender-then-attacker each round (see module docstring);
    returns an empty list if it isn't ``player_id``'s Duel turn.
    """
    from game.core.actions import FinalDuelAction

    duel = state.duel
    if duel is None:
        return []
    is_defender_turn = not duel.defender_acted_this_round
    acting = duel.defender if is_defender_turn else duel.attacker
    if player_id != acting:
        return []

    actions: "list[FinalDuelAction]" = [
        FinalDuelAction(player_id=player_id, duel_action_type="advance", parameters={})
    ]
    pool = duel.defender_support if is_defender_turn else duel.attacker_support
    for item in pool:
        actions.append(FinalDuelAction(
            player_id=player_id,
            duel_action_type=_ACTION_FOR_SOURCE[item.source],
            parameters={"item_id": item.item_id},
        ))
    if not is_defender_turn:
        actions.append(FinalDuelAction(player_id=player_id, duel_action_type="strike", parameters={}))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Duel Action resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_duel_action(
    state: "GameState",
    action: "FinalDuelAction",
    events: "list[Event]",
    registry: "object | None",
) -> None:
    """
    Apply one already-validated FinalDuelAction. Assumes ``action`` is a
    member of ``get_legal_duel_actions(state, action.player_id)`` — the
    caller (core/rules.py._execute_final_duel_action) is responsible for
    validating that, matching the convention every other mechanics/*.py
    module follows (validation lives in rules.py; mechanics modules trust
    their input).
    """
    duel = state.duel
    if duel is None:
        return

    is_defender_turn = not duel.defender_acted_this_round
    side = "defender" if is_defender_turn else "attacker"
    role = "Defender" if is_defender_turn else "Attacker"
    pool = duel.defender_support if is_defender_turn else duel.attacker_support

    if action.duel_action_type == "advance":
        # README §32 "Move within the Duel Arena" — a no-op pass here.
        duel.log.append(f"{action.player_id} ({role}): Advance (pass)")
    elif action.duel_action_type == "strike":
        _resolve_strike(state, duel, events)
    else:
        item_id = action.parameters.get("item_id")
        item = next((i for i in pool if i.item_id == item_id), None)
        if item is not None:
            pool.remove(item)
            _resolve_support_item(state, duel, side, role, item, events)

    if state.phase != Phase.FINAL_DUEL:
        return  # the action above already ended the Duel (Strike win)

    if is_defender_turn:
        duel.defender_acted_this_round = True
    else:
        duel.attacker_acted_this_round = True

    if duel.defender_acted_this_round and duel.attacker_acted_this_round:
        duel.round_number += 1
        duel.defender_acted_this_round = False
        duel.attacker_acted_this_round = False
        events.append(DuelRoundAdvanced(round_number=duel.round_number, max_rounds=duel.max_rounds))
        duel.log.append(f"— Round {duel.round_number} —")
        _check_round_limit(state, duel, events, registry)


def _resolve_strike(state: "GameState", duel: DuelState, events: "list[Event]") -> None:
    """Attacker attempts to land a Strike (README §31). Bypass is checked before Guards."""
    if duel.attacker_bypass > 0:
        duel.attacker_bypass -= 1
        blocked = False
    elif duel.defender_guards > 0:
        duel.defender_guards -= 1
        blocked = True
    else:
        blocked = False

    if not blocked:
        duel.strikes_landed += 1
    outcome = (
        f"HIT ({duel.strikes_landed}/{duel.strikes_needed})" if not blocked else "blocked by a Guard"
    )
    duel.log.append(f"{duel.attacker} (Attacker): Strike → {outcome}")
    events.append(DuelStrikeResolved(
        player_id=duel.attacker, blocked=blocked,
        strikes_landed=duel.strikes_landed, strikes_needed=duel.strikes_needed,
    ))
    if duel.strikes_landed >= duel.strikes_needed:
        _attacker_wins(state, duel, events)


def _grant_guards(duel: DuelState, amount: int) -> None:
    """Add Guards, netting out any pending defender_guard_debuff first (floored at 0)."""
    if amount <= 0:
        return
    absorbed = min(amount, duel.defender_guard_debuff)
    duel.defender_guard_debuff -= absorbed
    duel.defender_guards += amount - absorbed


# ── Defender-side ability handlers ──────────────────────────────────────────

def _def_guard(duel: DuelState, amount: int) -> None:
    _grant_guards(duel, amount)


def _def_charge(duel: DuelState, amount: int) -> None:
    _grant_guards(duel, amount)


def _def_intervention(duel: DuelState, amount: int) -> None:
    if duel.attacker_bypass > 0:
        duel.attacker_bypass = max(0, duel.attacker_bypass - amount)
    else:
        _grant_guards(duel, amount)


def _def_fortify(duel: DuelState, amount: int) -> None:
    _grant_guards(duel, amount * 2)


def _def_command(duel: DuelState, amount: int) -> None:
    _grant_guards(duel, amount * 2)
    duel.attacker_bypass = 0


_DEFENDER_ABILITY_HANDLERS = {
    "guard": _def_guard,
    "charge": _def_charge,
    "intervention": _def_intervention,
    "fortify": _def_fortify,
    "command": _def_command,
}


# ── Attacker-side ability handlers ──────────────────────────────────────────

def _att_guard(state: "GameState", duel: DuelState, amount: int, events: "list[Event]") -> None:
    duel.attacker_bypass += amount  # banked for a later Strike


def _att_charge(state: "GameState", duel: DuelState, amount: int, events: "list[Event]") -> None:
    duel.attacker_bypass += amount
    _resolve_strike(state, duel, events)


def _att_intervention(state: "GameState", duel: DuelState, amount: int, events: "list[Event]") -> None:
    duel.defender_guards = max(0, duel.defender_guards - amount)


def _att_fortify(state: "GameState", duel: DuelState, amount: int, events: "list[Event]") -> None:
    duel.defender_guards = max(0, duel.defender_guards - amount * 2)


def _att_command(state: "GameState", duel: DuelState, amount: int, events: "list[Event]") -> None:
    duel.attacker_bypass += amount * 2
    _resolve_strike(state, duel, events)


_ATTACKER_ABILITY_HANDLERS = {
    "guard": _att_guard,
    "charge": _att_charge,
    "intervention": _att_intervention,
    "fortify": _att_fortify,
    "command": _att_command,
}


def _resolve_support_item(
    state: "GameState",
    duel: DuelState,
    side: str,
    role: str,
    item: DuelSupportItem,
    events: "list[Event]",
) -> None:
    duel.log.append(f"{item.owner} ({role}): {item.label or item.ability}")
    events.append(DuelSupportSpent(
        player_id=item.owner, item_id=item.item_id, source=item.source,
        ability=item.ability, label=item.label,
    ))
    if side == "defender":
        handler = _DEFENDER_ABILITY_HANDLERS.get(item.ability)
        if handler is not None:
            handler(duel, item.amount)
    else:
        handler = _ATTACKER_ABILITY_HANDLERS.get(item.ability)
        if handler is not None:
            handler(state, duel, item.amount, events)


# ─────────────────────────────────────────────────────────────────────────────
# Win / round-limit resolution
# ─────────────────────────────────────────────────────────────────────────────

def _attacker_wins(state: "GameState", duel: DuelState, events: "list[Event]") -> None:
    duel.log.append(f"{duel.attacker} lands the final Strike — wins the Duel!")
    state.winner = duel.attacker
    state.phase = Phase.GAME_OVER
    events.append(GameOver(winner=duel.attacker, reason="final_duel_victory"))
    state.duel = None


def _check_round_limit(
    state: "GameState",
    duel: DuelState,
    events: "list[Event]",
    registry: "object | None",
) -> None:
    if state.phase != Phase.FINAL_DUEL:
        return
    if duel.escape_allowed:
        if duel.round_number > duel.max_rounds:
            _royal_escape(state, duel, events)
    else:
        # Last Stand (README §17/25) — no round-limit escape. hard_round_cap
        # is a pure safety valve; with finite Guard-granting items it should
        # never actually trigger in ordinary play.
        if duel.round_number > duel.hard_round_cap:
            _attacker_wins(state, duel, events)


def _find_relocation_square(state: "GameState", center: Position, owner: str) -> "Position | None":
    """
    First empty square near ``center`` — preferring one the opponent doesn't
    already attack — expanding outward ring by ring until one is found
    (always terminates: the board is never full).
    """
    from game.mechanics.area import expand_area
    from game.chess.movement import get_pseudo_legal_moves

    opponent = state.opponent_of(owner)
    attacked: set[Position] = set()
    for pos, unit in state.board.all_units_for(opponent):
        try:
            attacked.update(get_pseudo_legal_moves(state.board, pos, unit))
        except Exception:
            continue

    for radius in range(1, 8):
        ring = expand_area(center, radius)
        candidates = [p for p in ring if state.board.get_unit(p) is None]
        if candidates:
            candidates.sort(key=lambda p: (
                p in attacked,
                abs(p.file - center.file) + abs(p.rank - center.rank),
                p.file, p.rank,
            ))
            return candidates[0]
    return None


def _royal_escape(state: "GameState", duel: DuelState, events: "list[Event]") -> None:
    """
    Defender survives the Duel (README §33). The King relocates — for an
    ASSAULT trigger it must first be RESTORED (a capture is provisional
    until the Duel resolves, README §22) — and gains Royal Immunity,
    implemented as a reusable ``shield:N`` (mechanics/monsters.py
    apply_capture_protection already treats any unit's shield uniformly).
    """
    center = duel.arena_center
    new_pos = _find_relocation_square(state, center, duel.defender) if center is not None else None

    if duel.duel_type == FinalDuelType.ASSAULT and duel.captured_king_piece_id is not None:
        if new_pos is not None:
            king_piece = ChessPiece(
                id=duel.captured_king_piece_id, owner=duel.defender, piece_type=PieceType.KING,
            )
            state.board.place_unit(new_pos, UnitInstance(piece=king_piece))
    else:
        found = state.board.find_king(duel.defender)
        if found is not None and new_pos is not None:
            king_pos, king_unit = found
            state.board.remove_unit(king_pos)
            state.board.place_unit(new_pos, king_unit)

    found = state.board.find_king(duel.defender)
    if found is not None:
        _, king_unit = found
        king_unit.add_status(f"shield:{max(1, duel.escape_shield_charges)}")

    ps = state.get_player(duel.defender)
    ps.duels_survived += 1
    duel.log.append(
        f"{duel.defender}'s King escapes to {new_pos.to_algebraic() if new_pos else '?'}! "
        f"(Royal Escape — Duel #{ps.duels_survived})"
    )
    events.append(RoyalEscapeTriggered(
        defender=duel.defender, new_position=new_pos, duels_survived=ps.duels_survived,
    ))

    state.duel = None
    # Resume normal play. state.active_player never changed during the Duel
    # (it's still whoever's chess move triggered it — the attacker), so
    # REACTION continues that player's turn exactly as an ordinary move would.
    state.phase = Phase.REACTION


# ─────────────────────────────────────────────────────────────────────────────
# Duel initialization — Royal Support tally (README §27)
# ─────────────────────────────────────────────────────────────────────────────

def _arena_center(
    state: "GameState",
    duel: DuelState,
    trigger_position: "Position | None",
) -> "Position | None":
    if duel.duel_type == FinalDuelType.ASSAULT and trigger_position is not None:
        return trigger_position
    found = state.board.find_king(duel.defender)
    return found[0] if found else trigger_position


def _support_radius_bonus(state: "GameState", player_id: str, registry: "object | None") -> int:
    """Watchtower's final_duel_support_range (README §12.4 / §29)."""
    if registry is None:
        return 0
    from game.cards.card import BuildingCard

    bonus = 0
    for b in state.buildings:
        if b.owner != player_id or b.status != ConstructionStatus.COMPLETE or b.disabled_turns > 0:
            continue
        try:
            card = registry.get(b.building_card_id)
        except KeyError:
            continue
        if not isinstance(card, BuildingCard):
            continue
        for eff in card.effects:
            if eff.type == "final_duel_support_range":
                bonus += eff.params.get("bonus", 1)
    return bonus


def _apply_final_duel_traps(
    state: "GameState",
    duel: DuelState,
    center: Position,
    def_radius: int,
    registry: "object | None",
) -> int:
    """
    royal_escape_route / muster_bell (traps.yaml "KING / FINAL DUEL TRAPS") —
    only the DEFENDER's own traps qualify (the trap's owner's King must be
    the one inside its radius, and only the defender's King sits at
    ``center``). Returns the (possibly increased) def_radius.
    """
    if registry is None:
        return def_radius
    from game.cards.card import TrapCard, TrapTrigger

    for trap in state.traps:
        if trap.owner != duel.defender:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.FINAL_DUEL_START:
            continue
        if not in_area(center, trap.position, trap.radius, trap.shape):
            continue
        for eff in card.effects:
            if eff.type == "duel_escape_bonus":
                duel.escape_shield_charges += eff.params.get("additional_escape_option", 1)
            elif eff.type == "royal_support_range_bonus":
                def_radius += eff.params.get("amount", 1)
    return def_radius


def _marked_penalty(unit: UnitInstance) -> int:
    """kingslayer_alarm's royal_support_mark — see effects/king_duel.py."""
    for s in unit.statuses:
        if s.startswith("duel_marked:"):
            return int(s.split(":")[1])
    return 0


def _gather_piece_support(
    state: "GameState",
    owner: str,
    center: Position,
    radius: int,
    next_id,
) -> "list[DuelSupportItem]":
    items: "list[DuelSupportItem]" = []
    for pos, unit in state.board.all_units_for(owner):
        if unit.piece.piece_type == PieceType.KING:
            continue
        if not in_area(pos, center, radius):
            continue
        ability = _VESSEL_ABILITY.get(unit.piece.piece_type)
        if ability is None:
            continue
        amount = _VESSEL_AMOUNT.get(unit.piece.piece_type, 1) - _marked_penalty(unit)
        if amount <= 0:
            continue
        # rally_the_kingdom's royal_support_bonus — "allied units that
        # ALREADY qualify as Royal Support count as one additional level".
        # Applied after the qualification checks above (range, marked
        # penalty) precisely so it can only ever amplify existing support,
        # never create it, and never widen ``radius``.
        amount += state.get_player(owner).royal_support_bonus_amount
        items.append(DuelSupportItem(
            item_id=next_id(), owner=owner, source="piece", ability=ability, amount=amount,
            label=f"{_ABILITY_LABELS[ability]} ({unit.piece.id})",
            origin_piece_id=unit.piece.id,
        ))
    return items


def _apply_passive_king_duel_flags(
    state: "GameState",
    duel: DuelState,
    center: Position,
    registry: "object | None",
) -> None:
    """
    Automatic (non-spendable) passive Royal Support adjustments, applied
    once at Duel start:

        king_support_bonus         (royal_guard, defender-owned)   → bonus guards
        royal_support_suppression  (kingsbane, attacker-owned)     → guard debuff
        duel_debuff                (royal_poisoner, attacker-owned)→ guard debuff
    """
    if registry is None:
        return
    from game.cards.card import MonsterCard

    for pos, unit in state.board.all_units_for(duel.defender):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for eff in card.effects:
            if eff.type == "king_support_bonus":
                radius = eff.params.get("radius", DEFAULT_SUPPORT_RADIUS)
                if in_area(pos, center, radius):
                    duel.defender_bonus_guards += eff.params.get("bonus", 1)

    for pos, unit in state.board.all_units_for(duel.attacker):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for eff in card.effects:
            if eff.type == "royal_support_suppression":
                radius = eff.params.get("radius", DEFAULT_SUPPORT_RADIUS)
                if in_area(pos, center, radius):
                    duel.defender_guard_debuff += eff.params.get("amount", 1)
            elif eff.type == "duel_debuff":
                dist = eff.params.get("condition", {}).get("distance_to_enemy_king", 2)
                if in_area(pos, center, dist):
                    duel.defender_guard_debuff += eff.params.get("defender_guard_penalty", 1)


def _gather_building_items(
    state: "GameState",
    duel: DuelState,
    center: Position,
    def_radius: int,
    att_radius: int,
    registry: "object | None",
    next_id,
) -> "list[DuelSupportItem]":
    """Fortress' final_duel_guard_bonus (README §12.4 / §29) as a spendable "guard" item."""
    if registry is None:
        return []
    from game.cards.card import BuildingCard

    items: "list[DuelSupportItem]" = []
    for b in state.buildings:
        if b.status != ConstructionStatus.COMPLETE or b.disabled_turns > 0:
            continue
        if b.owner not in (duel.attacker, duel.defender):
            continue
        radius = def_radius if b.owner == duel.defender else att_radius
        if not in_area(b.position, center, radius):
            continue
        try:
            card = registry.get(b.building_card_id)
        except KeyError:
            continue
        if not isinstance(card, BuildingCard):
            continue
        for eff in card.effects:
            if eff.type != "final_duel_guard_bonus":
                continue
            items.append(DuelSupportItem(
                item_id=next_id(), owner=b.owner, source="building", ability="guard",
                amount=eff.params.get("amount", 1), label=f"{card.name}",
            ))
    return items


def _king_item_amount(
    state: "GameState",
    owner: str,
    duel_effect,
    center: Position,
    radius: int,
    registry: "object | None",
) -> int:
    """Compute the spendable amount for one King's ``duel_effect`` (README §30)."""
    from game.cards.card import MonsterCard

    p = duel_effect.params
    t = duel_effect.type

    if t == "ritual_duel_support":
        completed = sum(1 for r in state.get_player(owner).ritual_pool if r.activated)
        return min(p.get("max_bonus", 99), p.get("support_per_completed_ritual", 1) * completed)

    if t == "monster_duel_support":
        archetype = p.get("archetype")
        nearby_only = p.get("nearby_only", False)
        count = 0
        for pos, unit in state.board.all_units_for(owner):
            if unit.monster_id is None:
                continue
            if nearby_only and not in_area(pos, center, radius):
                continue
            try:
                card = registry.get(unit.monster_id) if registry else None
            except KeyError:
                card = None
            if isinstance(card, MonsterCard) and card.archetype == archetype:
                count += 1
        return p.get("bonus", 1) * min(count, p.get("max_targets", count))

    if t == "royal_support_conversion":
        archetype = p.get("source_archetype")
        count = 0
        for pos, unit in state.board.all_units_for(owner):
            if unit.monster_id is None:
                continue
            try:
                card = registry.get(unit.monster_id) if registry else None
            except KeyError:
                card = None
            if isinstance(card, MonsterCard) and card.archetype == archetype:
                count += 1
        each = max(1, p.get("convert_each", 1))
        return min(p.get("max_guards", 99), count // each)

    if t == "building_duel_support":
        qualifying_radius = p.get("qualifying_radius", radius)
        count = sum(
            1 for b in state.buildings
            if b.owner == owner and b.status == ConstructionStatus.COMPLETE
            and in_area(b.position, center, qualifying_radius)
        )
        return min(p.get("max_bonus", 99), count * p.get("support_per_building", 1))

    if t == "graveyard_duel_support":
        cards = len(state.get_player(owner).graveyard)
        per = max(1, p.get("cards_per_support", 1))
        return min(p.get("max_bonus", 99), cards // per)

    # "defender_support_reduction" (shadow_regent) is a passive attacker-side
    # debuff, not a spendable item — applied separately in initialize_duel().
    return 0


def _gather_king_items(
    state: "GameState",
    duel: DuelState,
    center: Position,
    def_radius: int,
    att_radius: int,
    registry: "object | None",
    next_id,
) -> None:
    """
    King Policy Duel Actions (README §30/§32) — one spendable "fortify"-style
    item per side with an active King carrying a scaling ``duel_effect``.
    Routed through the standardized "fortify" handler (safe: never forces an
    immediate Strike) so King Policy always behaves as a defensive/offensive
    rally regardless of which of the six duel_effect types produced it.
    """
    from game.mechanics.kings import get_active_king_card

    for owner, radius, pool in (
        (duel.defender, def_radius, duel.defender_support),
        (duel.attacker, att_radius, duel.attacker_support),
    ):
        king_card = get_active_king_card(state, owner, registry)
        if king_card is None or king_card.duel_effect is None:
            continue
        if king_card.duel_effect.type == "defender_support_reduction":
            continue  # applied passively below, not spendable
        amount = _king_item_amount(state, owner, king_card.duel_effect, center, radius, registry)
        if amount <= 0:
            continue
        pool.append(DuelSupportItem(
            item_id=next_id(),
            owner=owner, source="king", ability="fortify", amount=amount,
            label=king_card.name,
        ))


def _apply_defender_support_reduction(
    state: "GameState",
    duel: DuelState,
    center: Position,
    radius: int,
    registry: "object | None",
) -> None:
    """shadow_regent's defender_support_reduction (README §30, passive attacker debuff)."""
    from game.cards.card import MonsterCard
    from game.mechanics.kings import get_active_king_card

    king_card = get_active_king_card(state, duel.attacker, registry)
    if king_card is None or king_card.duel_effect is None:
        return
    eff = king_card.duel_effect
    if eff.type != "defender_support_reduction":
        return
    condition = eff.params.get("condition", {})
    if condition.get("attacker_has_nearby_assassin"):
        has_assassin = False
        for pos, unit in state.board.all_units_for(duel.attacker):
            if unit.monster_id is None or not in_area(pos, center, radius):
                continue
            try:
                card = registry.get(unit.monster_id) if registry else None
            except KeyError:
                card = None
            if isinstance(card, MonsterCard) and card.archetype == "assassin":
                has_assassin = True
                break
        if not has_assassin:
            return
    duel.defender_guard_debuff += eff.params.get("amount", 1)


def initialize_duel(
    state: "GameState",
    duel: DuelState,
    registry: "object | None",
    events: "list[Event]",
    trigger_position: "Position | None" = None,
    captured_king_piece_id: str | None = None,
) -> None:
    """
    Populate ``duel`` from the board state at the moment the Final Duel
    begins (README §24 — no new Main Deck resources; only what the board
    already produced). Called once by core/rules.py._trigger_final_duel.
    """
    duel.captured_king_piece_id = captured_king_piece_id
    duel.log.append(
        f"⚔ Final Duel ({duel.duel_type.value}) — "
        f"{duel.attacker} (Attacker) vs {duel.defender} (Defender)"
    )
    duel.log.append(f"— Round {duel.round_number} —")
    center = _arena_center(state, duel, trigger_position)
    duel.arena_center = center
    if center is None:
        # No reachable King position at all (extreme edge case) — the Duel
        # proceeds with empty support pools; the attacker will win almost
        # immediately, which is the sane fallback outcome.
        duel.escape_allowed = state.get_player(duel.defender).duels_survived < 2
        return

    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"duel-item-{counter['n']}"

    def_radius = DEFAULT_SUPPORT_RADIUS + _support_radius_bonus(state, duel.defender, registry)
    att_radius = DEFAULT_SUPPORT_RADIUS + _support_radius_bonus(state, duel.attacker, registry)
    def_radius = _apply_final_duel_traps(state, duel, center, def_radius, registry)

    duel.defender_support = _gather_piece_support(state, duel.defender, center, def_radius, next_id)
    duel.attacker_support = _gather_piece_support(state, duel.attacker, center, att_radius, next_id)

    duel.defender_support += _gather_building_items(
        state, duel, center, def_radius, att_radius, registry, next_id
    )
    _gather_king_items(state, duel, center, def_radius, att_radius, registry, next_id)

    _apply_passive_king_duel_flags(state, duel, center, registry)
    _apply_defender_support_reduction(state, duel, center, att_radius, registry)

    # Siege advantage (README §25) — checkmate is a stronger positional
    # victory than a bare capture, so the attacker strips one of the
    # defender's Guard-granting items (preferring an actual "guard" item).
    if duel.duel_type == FinalDuelType.SIEGE and duel.defender_support:
        idx = next((i for i, it in enumerate(duel.defender_support) if it.ability == "guard"), 0)
        duel.defender_support.pop(idx)

    _grant_guards(duel, duel.defender_bonus_guards)

    # Last Stand escalation (README §17/25) — the 3rd Duel this King has
    # faced removes the round-limit Royal Escape.
    duel.escape_allowed = state.get_player(duel.defender).duels_survived < 2
