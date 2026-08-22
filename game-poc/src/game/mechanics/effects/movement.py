"""
mechanics/effects/movement.py — MOVEMENT category
==================================================

Effect types in this category
------------------------------
alter_movement          IMPLEMENTED  — adds leap squares / stealth flag at summon
burrow                  IMPLEMENTED  — activated pass-through relocation (tunnel_mole)
pass_through_units      IMPLEMENTED  — phantom_lancer's Pawn double-push
                                       phases through one occupied square
                                       (Knights already ignore intervening
                                       squares); read directly from
                                       card.effects by chess.movement
pull_unit               IMPLEMENTED  — pulls a unit toward a King or Trap
                                       (magnetic_reversal, gravity_well)
move_unit               IMPLEMENTED  — free Pawn advance, no chess move
                                       consumed (forced_march)
swap_units              IMPLEMENTED  — swap two owned units' positions
                                       (exchange_of_fates)
reposition_unit         IMPLEMENTED  — post-capture teleport (blade_dancer) AND
                                       direct-target instant teleport (Stage 6:
                                       arcane_reposition spell — see the
                                       ``ctx.extra["destination"]`` branch)
push_unit               IMPLEMENTED  — push defender instead of capturing
                                       (storm_dragon; resolved in rules.py via
                                       try_push_unit(), BEFORE the normal
                                       capture path runs)
immobilize_piece        IMPLEMENTED  — Stage 6: the unit cannot move for
                                       duration_turns (pit_trap, ward_of_binding).
                                       Enforced by chess.movement returning no
                                       moves while "immobilized:N" is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.chess.movement import blocks_movement

if TYPE_CHECKING:
    from game.chess.pieces import Position
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# Stale-position guard
# ---------------------------------------------------------------------------

def _current_position(ctx: "EffectContext") -> "Position | None":
    """
    Where ``ctx.unit`` ACTUALLY stands right now, or None if it has left
    the board.

    ``ctx.position`` is a snapshot taken when the effect chain was built,
    and every displacement handler below used to trust it blindly.  It
    goes stale whenever something earlier in the same chain already moved
    or removed the unit — most easily when two enemy enter_radius Traps
    cover the same landing square (mechanics.monsters.check_enter_radius_traps
    hands each of them the same pre-displacement ``target_pos``), but also
    between two effects on a single card.  Displacing from the stale
    square either raises out of BoardState.move_unit ("No unit at source
    square") or, for the remove/place handlers, silently duplicates a
    piece onto two squares.
    """
    if ctx.unit is None or ctx.state is None:
        return None
    if ctx.position is not None and ctx.state.board.get_unit(ctx.position) is ctx.unit:
        return ctx.position
    found = ctx.state.board.find_unit_by_piece_id(ctx.unit.piece.id)
    if found is None:
        return None   # captured / destroyed earlier in this same chain
    pos, unit = found
    return pos if unit is ctx.unit else None


# ---------------------------------------------------------------------------
# alter_movement
# ---------------------------------------------------------------------------

def _alter_movement(ctx: "EffectContext") -> None:
    """
    Modify the vessel's movement pattern.

    Currently handled sub-effects (resolved at summon time):
        stealth: true   → flags "stealth" status on the unit
        add_leap: true  → movement additions computed in get_movement_additions()

    The actual leap-square computation lives in get_movement_additions() which
    is called from movement.py during legal-move generation.  This handler only
    applies unit statuses.
    """
    if ctx.unit is None:
        return
    params = ctx.effect.params

    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return

    if params.get("stealth"):
        ctx.unit.add_status("stealth")


# ---------------------------------------------------------------------------
# burrow
# ---------------------------------------------------------------------------

def _burrow(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — tunnel_mole activated ability.

    Move up to ``max_distance`` squares (Chebyshev), through occupied
    squares (``ignore_units: true``); the destination itself must be empty.
    Subject to ``cooldown_turns`` — a "burrow_cooldown:N" status blocks
    reactivation until N of the owner's own EndTurns have passed (ticked
    down in ``_execute_end_turn``, mirroring the frozen-square counters).

    Reuses the same REPOSITION PendingDecision/RepositionUnit machinery as
    ``reposition_unit`` (blade_dancer) — options are enumerated the same
    way and ``_execute_reposition_unit`` performs the actual move.  The
    only addition is a ``post_move_status`` entry in the decision context
    so the resolver applies the cooldown once the player picks a square.

    Raises IllegalActionError if the unit is still on cooldown.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType
    from game.core.rules import IllegalActionError
    from game.core.state import PendingDecision

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    # burrow is an ACTIVATED ability ("once every 2 turns"), not an
    # on-summon one. tunnel_mole's YAML entry carries no ``trigger`` key,
    # so apply_on_summon_effects' default ("on_summon") would otherwise
    # fire it the moment the Monster hits the board and demand a
    # relocation the player never asked for.
    if ctx.trigger != "activated":
        return

    for status in ctx.unit.statuses:
        if status.startswith("burrow_cooldown:"):
            remaining = int(status.split(":")[1])
            if remaining > 0:
                raise IllegalActionError(
                    f"Burrow is on cooldown for {remaining} more of this "
                    "unit's turns."
                )

    params = ctx.effect.params
    max_dist = params.get("max_distance", 2)
    cooldown = params.get("cooldown_turns", 2)
    origin = _current_position(ctx)
    if origin is None:
        return

    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = origin.file + df, origin.rank + dr
            if 0 <= nf <= 7 and 0 <= nr <= 7:
                cand = Position(nf, nr)
                # Stage 13: a COMPLETE enemy Building is terrain — you can't
                # surface under one any more than you can walk onto one.
                if ctx.state.board.get_unit(cand) is None and not blocks_movement(
                    ctx.state.board, cand, ctx.unit.owner
                ):
                    options.append((nf, nr))

    if not options:
        raise IllegalActionError("No empty square available to burrow to.")

    if ctx.state.pending_decision is None:
        ctx.state.pending_decision = PendingDecision(
            player_id=ctx.unit.owner,
            decision_type=DecisionType.REPOSITION,
            options=options,
            min_choices=1,
            max_choices=1,
            context={
                "piece_id": ctx.unit.piece.id,
                "current_pos": (origin.file, origin.rank),
                "post_move_status": f"burrow_cooldown:{cooldown}",
            },
        )


# ---------------------------------------------------------------------------
# pass_through_units
# ---------------------------------------------------------------------------

def _pass_through_units(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — phantom_lancer (real logic lives elsewhere).

    Read directly from ``card.effects`` (not a status flag) by
    chess.movement.get_pseudo_legal_moves(), which looks up the unit's
    MonsterCard once per call and, for a Pawn, lets its double-push phase
    through ONE occupied intermediate square (Knight already ignores
    intervening squares by nature — nothing to change there; see that
    module's docstring for the full reasoning). This handler is a no-op
    placeholder so the type is recognised by the registry and never
    logged as unresolved.
    """
    pass


# ---------------------------------------------------------------------------
# reposition_unit
# ---------------------------------------------------------------------------

_KNIGHT_OFFSETS = frozenset({
    (2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2),
})


def _reposition_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — one effect type, four modes, selected by the params the
    card actually declares:

    1. blade_dancer after-capture (``trigger: after_capture``): creates a
       REPOSITION PendingDecision so the player picks a destination within
       Chebyshev ``max_distance`` of the attacker's current position.
       Called from apply_after_capture_effects() in monsters.py.

    2. Direct-target instant teleport (arcane_reposition): the destination
       comes in via ``ctx.extra["destination"]`` (from
       _resolve_spell_on_piece), so no PendingDecision is needed. Validates
       ``max_distance``, ``must_be_own`` (never the King) and an empty
       destination.

    3. ``movement_pattern: "knight"`` (knightfall): as (2), but the
       destination must be a real Knight hop from the origin and the piece
       itself must be one of ``piece_types`` — "another legal Knight
       destination", not an arbitrary square anywhere on the board.

    4. ``target: "own_king"`` (evacuation_order): the ONE card whose whole
       point is moving the King, so the blanket no-King rule of (2) would
       make it unplayable. ``only_when_not_in_check`` is enforced here
       ("Cannot be activated while in Check"), and
       ``destination_must_be_legal`` additionally rejects a square that
       would leave the King in check.

    5. ``destination: "random_adjacent_empty"`` (displacement_rune): a
       Trap, so no player picks anything — the handler rolls the
       destination itself off ``ctx.rng``, matching the card's "determined
       by seeded game RNG".
    """
    from game.chess.pieces import Position
    from game.core.events import PieceMoved
    from game.core.phases import DecisionType, PieceType
    from game.core.rules import IllegalActionError
    from game.core.state import PendingDecision

    params = ctx.effect.params

    # ── (5) Trap-rolled displacement — displacement_rune ─────────────────
    if params.get("destination") == "random_adjacent_empty":
        if ctx.unit is None or ctx.position is None or ctx.state is None:
            return
        if ctx.unit.piece.piece_type == PieceType.KING:
            return   # Kings are immune to Trap displacement, like pull_unit
        from game.mechanics.buildings import is_committed_builder
        origin = _current_position(ctx)
        if origin is None:
            return
        candidates = []
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if df == 0 and dr == 0:
                    continue
                nf, nr = origin.file + df, origin.rank + dr
                if not (0 <= nf <= 7 and 0 <= nr <= 7):
                    continue
                cand = Position(nf, nr)
                if ctx.state.board.get_unit(cand) is not None:
                    continue
                if blocks_movement(ctx.state.board, cand, ctx.unit.owner):
                    continue
                candidates.append(cand)
        if not candidates:
            return
        dest_pos = ctx.rng.choice(candidates) if ctx.rng is not None else candidates[0]
        ctx.state.board.move_unit(origin, dest_pos)
        ctx.events.append(PieceMoved(
            piece_id=ctx.unit.piece.id, owner=ctx.unit.owner,
            source=origin, target=dest_pos,
        ))
        # Same displacement rule pull_unit/push_unit follow: dragging a
        # committed Builder off its square loses the half-built Building.
        from game.mechanics.buildings import find_committed_building, cancel_construction
        disrupted = find_committed_building(ctx.state, ctx.unit.piece.id)
        if disrupted is not None:
            cancel_construction(ctx.state, disrupted, ctx.events, destroyed_by=None)
        return

    destination = ctx.extra.get("destination") if ctx.extra else None
    if destination is not None:
        if ctx.unit is None or ctx.position is None or ctx.state is None:
            return
        origin = _current_position(ctx)
        if origin is None:
            return

        targets_own_king = params.get("target") == "own_king"
        is_king = ctx.unit.piece.piece_type == PieceType.KING

        # ── (4) evacuation_order ─────────────────────────────────────────
        if targets_own_king:
            if not is_king:
                raise IllegalActionError("This Spell can only target your own King.")
            if params.get("only_when_not_in_check", False):
                from game.chess.movement import is_in_check
                if is_in_check(ctx.state.board, ctx.unit.owner):
                    raise IllegalActionError("Cannot be activated while in Check.")
        elif params.get("must_be_own", True) and is_king:
            raise IllegalActionError("reposition_unit cannot target the King.")

        # ── (3) knightfall's vessel + geometry restrictions ──────────────
        piece_types = params.get("piece_types")
        if piece_types is not None and ctx.unit.piece.piece_type.value not in piece_types:
            raise IllegalActionError(
                f"This Spell can only target: {', '.join(piece_types)}."
            )
        if params.get("movement_pattern") == "knight":
            offset = (destination[0] - origin.file, destination[1] - origin.rank)
            if offset not in _KNIGHT_OFFSETS:
                raise IllegalActionError(
                    "Destination must be a legal Knight destination from the piece's square."
                )

        # Stage 8: a Pawn committed to Building construction cannot be
        # relocated by any means, including this Spell.
        from game.mechanics.buildings import is_committed_builder
        if is_committed_builder(ctx.state, ctx.unit.piece.id):
            raise IllegalActionError("This Pawn is committed to a Building under construction.")
        max_dist = params.get("max_distance")
        if max_dist is not None:
            dist = max(
                abs(destination[0] - origin.file),
                abs(destination[1] - origin.rank),
            )
            if dist > max_dist:
                raise IllegalActionError(
                    f"Destination is {dist} squares away — max_distance is {max_dist}."
                )
        dest_pos = Position(destination[0], destination[1])
        if not (0 <= dest_pos.file <= 7 and 0 <= dest_pos.rank <= 7):
            raise IllegalActionError("Destination is off the board.")
        if ctx.state.board.get_unit(dest_pos) is not None:
            raise IllegalActionError("Destination square is occupied.")
        if blocks_movement(ctx.state.board, dest_pos, ctx.unit.owner):
            raise IllegalActionError("A completed enemy Building stands on that square.")

        ctx.state.board.move_unit(origin, dest_pos)

        # "destination_must_be_legal" — for the King that means the square
        # it lands on must not itself be attacked. Checked after the move
        # so the King's own vacated square doesn't shield the destination.
        if targets_own_king and params.get("destination_must_be_legal", False):
            from game.chess.movement import is_in_check
            if is_in_check(ctx.state.board, ctx.unit.owner):
                ctx.state.board.move_unit(dest_pos, origin)
                raise IllegalActionError("That square would leave your King in check.")

        ctx.events.append(PieceMoved(
            piece_id=ctx.unit.piece.id,
            owner=ctx.unit.owner,
            source=origin,
            target=dest_pos,
        ))
        return

    trigger = params.get("trigger", "")
    if trigger != "after_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    max_dist = params.get("max_distance", 2)
    attacker_pos = _current_position(ctx)
    if attacker_pos is None:
        return
    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            nf = attacker_pos.file + df
            nr = attacker_pos.rank + dr
            if 0 <= nf <= 7 and 0 <= nr <= 7:
                cand = Position(nf, nr)
                if ctx.state.board.get_unit(cand) is None and not blocks_movement(
                    ctx.state.board, cand, ctx.unit.owner
                ):
                    options.append((nf, nr))

    if options and ctx.state.pending_decision is None:
        ctx.state.pending_decision = PendingDecision(
            player_id=ctx.unit.owner,
            decision_type=DecisionType.REPOSITION,
            options=options,
            min_choices=1,
            max_choices=1,
            context={
                "piece_id": ctx.unit.piece.id,
                "current_pos": (attacker_pos.file, attacker_pos.rank),
            },
        )


# ---------------------------------------------------------------------------
# push_unit
# ---------------------------------------------------------------------------

def _push_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — two modes, one per card that declares this type:

    1. storm_dragon (``trigger: on_capture_attempt``, Monster): instead of
       capturing, the target is pushed ``distance`` squares away from the
       attacker. That has to intercept the capture BEFORE the board is
       mutated, so it cannot go through the normal ``resolve_effect``
       dispatch (which only ever runs after a move already landed) — the
       real implementation is ``try_push_unit()`` in mechanics/monsters.py,
       called from ``_execute_move_piece``. This handler leaves that case
       alone.

    2. repulsion_field (``direction: away_from_trap``, Trap): fired by
       mechanics.monsters._fire_trap AFTER the intruder has landed, so
       there IS a "react to it now" moment and it resolves here. Mirrors
       pull_unit's geometry with the sign flipped, including the same
       King-immunity and committed-Builder disruption rules every other
       forced displacement follows.
    """
    from game.chess.pieces import Position
    from game.core.events import PiecePushed
    from game.core.phases import PieceType

    params = ctx.effect.params
    if params.get("direction") != "away_from_trap":
        # storm_dragon's on_capture_attempt variant — see mode 1 above.
        return
    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    if ctx.unit.piece.piece_type == PieceType.KING:
        return   # Kings are immune to Trap displacement (same as pull_unit)

    trap_pos = (ctx.extra or {}).get("trap_position")
    if trap_pos is None:
        return
    ref = Position(trap_pos[0], trap_pos[1])
    cur = _current_position(ctx)
    if cur is None:
        return   # already displaced off the board earlier in this chain
    distance = params.get("distance", 1)

    step_f = (cur.file > ref.file) - (cur.file < ref.file)
    step_r = (cur.rank > ref.rank) - (cur.rank < ref.rank)
    if step_f == 0 and step_r == 0:
        return   # standing exactly on the Trap — no "away" direction exists
    nf, nr = cur.file + step_f * distance, cur.rank + step_r * distance
    if not (0 <= nf <= 7 and 0 <= nr <= 7):
        return
    dest = Position(nf, nr)
    if ctx.state.board.get_unit(dest) is not None:
        return
    if blocks_movement(ctx.state.board, dest, ctx.unit.owner):
        return

    ctx.state.board.move_unit(cur, dest)
    ctx.events.append(PiecePushed(
        piece_id=ctx.unit.piece.id, owner=ctx.unit.owner,
        source=cur, target=dest, pushed_by_piece_id=None,
    ))
    from game.mechanics.buildings import find_committed_building, cancel_construction
    disrupted = find_committed_building(ctx.state, ctx.unit.piece.id)
    if disrupted is not None:
        cancel_construction(ctx.state, disrupted, ctx.events, destroyed_by=None)


# ---------------------------------------------------------------------------
# immobilize_piece  (Stage 6 — pit_trap, ward_of_binding)
# ---------------------------------------------------------------------------

def _immobilize_piece(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 6 Trap effect (pit_trap, ward_of_binding).

    Flags the triggering unit as "immobilized:<duration_turns>" — it has
    zero legal moves until the status expires.  Enforced in
    chess.movement.get_pseudo_legal_moves(), which returns [] immediately
    for an immobilized unit.  It may still be captured normally.

    Decays on the unit OWNER's own EndTurn (mirrors burrow_cooldown) —
    "cannot move on its next turn" counts the victim's own turns, not the
    trap owner's.
    """
    if ctx.unit is None:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("immobilized:")]
    ctx.unit.add_status(f"immobilized:{duration}")


# ---------------------------------------------------------------------------
# pull_unit  (magnetic_reversal spell, gravity_well trap)
# ---------------------------------------------------------------------------

def _pull_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — pulls ``ctx.unit`` one step (``distance`` squares) toward
    a reference point, if the destination is empty:

        toward: "caster_king"  — magnetic_reversal (spell). The reference
            is the CASTER's King (``ctx.extra["caster_owner"]``, supplied
            by _resolve_spell_on_piece; falls back to the opponent of
            ``ctx.unit``'s owner if absent).
        toward: "trap" (default) — gravity_well. The reference is
            ``ctx.extra["trap_position"]`` (supplied by _fire_trap).

    Mirrors push_unit's construction-disruption handling: pulling a
    committed Builder Pawn off its square destroys the half-built
    Building (README §12.2 step 4 applies to ANY forced displacement,
    not just captures).
    """
    from game.chess.pieces import Position
    from game.core.events import PiecePulled
    from game.core.phases import PieceType

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    if ctx.unit.piece.piece_type == PieceType.KING:
        return  # Kings are immune to Trap/Spell displacement effects
    params = ctx.effect.params
    toward = params.get("toward", "trap")
    distance = params.get("distance", 1)

    if toward == "caster_king":
        caster = (ctx.extra or {}).get("caster_owner") or ctx.state.opponent_of(ctx.unit.owner)
        king_result = ctx.state.board.find_king(caster)
        if king_result is None:
            return
        ref_pos, _ = king_result
    else:
        trap_pos = (ctx.extra or {}).get("trap_position")
        if trap_pos is None:
            return
        ref_pos = Position(trap_pos[0], trap_pos[1])

    cur = _current_position(ctx)
    if cur is None:
        return   # already displaced off the board earlier in this chain
    step_f = (ref_pos.file > cur.file) - (ref_pos.file < cur.file)
    step_r = (ref_pos.rank > cur.rank) - (ref_pos.rank < cur.rank)
    if step_f == 0 and step_r == 0:
        return
    nf, nr = cur.file + step_f * distance, cur.rank + step_r * distance
    if not (0 <= nf <= 7 and 0 <= nr <= 7):
        return
    dest = Position(nf, nr)
    if ctx.state.board.get_unit(dest) is not None:
        return
    if blocks_movement(ctx.state.board, dest, ctx.unit.owner):
        return   # Stage 13: can't be dragged into a completed enemy Building

    ctx.state.board.move_unit(cur, dest)
    ctx.events.append(PiecePulled(
        piece_id=ctx.unit.piece.id, owner=ctx.unit.owner, source=cur, target=dest,
    ))
    from game.mechanics.buildings import find_committed_building, cancel_construction
    disrupted = find_committed_building(ctx.state, ctx.unit.piece.id)
    if disrupted is not None:
        cancel_construction(ctx.state, disrupted, ctx.events, destroyed_by=None)


# ---------------------------------------------------------------------------
# move_unit  (forced_march — free Pawn advance)
# ---------------------------------------------------------------------------

def _move_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — forced_march. Advances ``ctx.unit`` (must be an owned
    Pawn per ``piece_types``) ``distance`` squares in its own forward
    direction, without consuming the normal chess move, provided the
    destination is empty ("does not count as a chess move" — this never
    touches ``chess_move_used``).
    """
    from game.chess.pieces import Position
    from game.core.events import PieceMoved
    from game.core.phases import PieceType

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    params = ctx.effect.params
    piece_types = params.get("piece_types", ["pawn"])
    if ctx.unit.piece.piece_type.value not in piece_types:
        return
    distance = params.get("distance", 1)
    origin = _current_position(ctx)
    if origin is None:
        return
    direction = 1 if ctx.unit.owner == "white" else -1
    nr = origin.rank + direction * distance
    if not (0 <= nr <= 7):
        return
    # "Cannot promote a Pawn through this effect" — a free advance onto the
    # promotion rank would hand the player a Queen without spending the
    # chess move that normally pays for it.
    from game.core.rules import IllegalActionError
    promotion_rank = 7 if ctx.unit.owner == "white" else 0
    if ctx.unit.piece.piece_type == PieceType.PAWN and nr == promotion_rank:
        raise IllegalActionError("This Spell cannot promote a Pawn.")
    dest = Position(origin.file, nr)
    if ctx.state.board.get_unit(dest) is not None:
        return
    if blocks_movement(ctx.state.board, dest, ctx.unit.owner):
        return   # Stage 13: a completed enemy Building blocks the advance

    ctx.state.board.move_unit(origin, dest)
    ctx.events.append(PieceMoved(
        piece_id=ctx.unit.piece.id, owner=ctx.unit.owner, source=origin, target=dest,
    ))


# ---------------------------------------------------------------------------
# swap_units  (exchange_of_fates)
# ---------------------------------------------------------------------------

def _swap_units(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — exchange_of_fates. Swaps ``ctx.unit`` with the allied,
    non-King unit at ``ctx.extra["destination"]`` (the Spell's second
    targeted piece — _resolve_spell_on_piece's ``target["destination"]``
    field, repurposed here as a second piece position rather than an
    empty square), provided both are the caster's own, neither is a
    committed Builder, and they're within ``max_distance`` (Chebyshev).
    """
    from game.chess.pieces import Position
    from game.core.events import PieceMoved
    from game.core.phases import PieceType
    from game.core.rules import IllegalActionError
    from game.mechanics.buildings import is_committed_builder

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    destination = ctx.extra.get("destination") if ctx.extra else None
    if destination is None:
        raise IllegalActionError("swap_units requires a second piece to swap with.")

    src = _current_position(ctx)
    if src is None:
        # Displaced or destroyed earlier in this chain — swapping from the
        # stale square would clone the unit onto two squares at once.
        return

    other_pos = Position(destination[0], destination[1])
    other_unit = ctx.state.board.get_unit(other_pos)
    if other_unit is None or other_unit.owner != ctx.unit.owner:
        raise IllegalActionError("swap_units target must be one of your own units.")
    if other_unit is ctx.unit:
        return   # a unit cannot swap with itself
    if ctx.unit.piece.piece_type == PieceType.KING or other_unit.piece.piece_type == PieceType.KING:
        raise IllegalActionError("swap_units cannot target the King.")
    if is_committed_builder(ctx.state, ctx.unit.piece.id) or is_committed_builder(ctx.state, other_unit.piece.id):
        raise IllegalActionError("A Pawn committed to Building construction cannot be swapped.")

    max_dist = ctx.effect.params.get("max_distance")
    if max_dist is not None:
        dist = max(abs(other_pos.file - src.file), abs(other_pos.rank - src.rank))
        if dist > max_dist:
            raise IllegalActionError(f"Target is {dist} squares away — max_distance is {max_dist}.")

    ctx.state.board.remove_unit(src)
    ctx.state.board.remove_unit(other_pos)
    ctx.state.board.place_unit(other_pos, ctx.unit)
    ctx.state.board.place_unit(src, other_unit)
    ctx.events.append(PieceMoved(piece_id=ctx.unit.piece.id, owner=ctx.unit.owner, source=src, target=other_pos))
    ctx.events.append(PieceMoved(piece_id=other_unit.piece.id, owner=other_unit.owner, source=other_pos, target=src))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

MOVEMENT_HANDLERS: dict[str, object] = {
    "alter_movement":    _alter_movement,
    "burrow":            _burrow,
    "pass_through_units": _pass_through_units,
    "reposition_unit":   _reposition_unit,
    "push_unit":         _push_unit,
    "immobilize_piece":  _immobilize_piece,
    "pull_unit":         _pull_unit,
    "move_unit":         _move_unit,
    "swap_units":        _swap_units,
}
