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

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


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
    origin = ctx.position

    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = origin.file + df, origin.rank + dr
            if 0 <= nf <= 7 and 0 <= nr <= 7:
                if ctx.state.board.get_unit(Position(nf, nr)) is None:
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

def _reposition_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — two independent modes sharing one effect type:

    1. blade_dancer after-capture (params.trigger == "after_capture"):
       creates a REPOSITION PendingDecision so the player picks a
       destination within Chebyshev distance ``max_distance`` of the
       attacker's current position.  Called from
       apply_after_capture_effects() in monsters.py.

    2. Stage 6 direct-target instant teleport (arcane_reposition spell):
       when ``ctx.extra["destination"]`` is supplied (by
       _execute_activate_spell), the unit is moved there immediately —
       no PendingDecision needed since the player already chose the
       destination as part of the spell's target.  Validates
       ``max_distance`` (from the caster's current position),
       ``must_be_own`` (never targets the King), and that the
       destination is empty.  Raises IllegalActionError on violation.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType, PieceType
    from game.core.rules import IllegalActionError
    from game.core.state import PendingDecision

    params = ctx.effect.params

    destination = ctx.extra.get("destination") if ctx.extra else None
    if destination is not None:
        if ctx.unit is None or ctx.position is None or ctx.state is None:
            return
        if params.get("must_be_own", True) and ctx.unit.piece.piece_type == PieceType.KING:
            raise IllegalActionError("reposition_unit cannot target the King.")
        # Stage 8: a Pawn committed to Building construction cannot be
        # relocated by any means, including this Spell.
        from game.mechanics.buildings import is_committed_builder
        if is_committed_builder(ctx.state, ctx.unit.piece.id):
            raise IllegalActionError("This Pawn is committed to a Building under construction.")
        max_dist = params.get("max_distance")
        if max_dist is not None:
            dist = max(
                abs(destination[0] - ctx.position.file),
                abs(destination[1] - ctx.position.rank),
            )
            if dist > max_dist:
                raise IllegalActionError(
                    f"Destination is {dist} squares away — max_distance is {max_dist}."
                )
        dest_pos = Position(destination[0], destination[1])
        if ctx.state.board.get_unit(dest_pos) is not None:
            raise IllegalActionError("Destination square is occupied.")
        from game.core.events import PieceMoved
        ctx.state.board.move_unit(ctx.position, dest_pos)
        ctx.events.append(PieceMoved(
            piece_id=ctx.unit.piece.id,
            owner=ctx.unit.owner,
            source=ctx.position,
            target=dest_pos,
        ))
        return

    trigger = params.get("trigger", "")
    if trigger != "after_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    max_dist = params.get("max_distance", 2)
    attacker_pos = ctx.position
    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            nf = attacker_pos.file + df
            nr = attacker_pos.rank + dr
            if 0 <= nf <= 7 and 0 <= nr <= 7:
                if ctx.state.board.get_unit(Position(nf, nr)) is None:
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
    IMPLEMENTED — storm_dragon (real logic lives elsewhere).

    On capture attempt (``trigger: on_capture_attempt``), instead of
    capturing, the target is pushed ``distance`` squares in ``direction``
    if that destination is legal and empty.

    This must intercept the capture BEFORE the board is mutated, so it
    cannot be resolved through the normal ``resolve_effect`` dispatch
    (which only ever runs after a move already landed).  The actual
    implementation is ``try_push_unit()`` in mechanics/monsters.py, called
    directly from ``_execute_move_piece`` right before the capture-
    protection / shield check.  This handler is a no-op placeholder so the
    type is recognised by the registry and never logged as unresolved.
    """
    # Real implementation: mechanics.monsters.try_push_unit()
    pass


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

    cur = ctx.position
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
    direction = 1 if ctx.unit.owner == "white" else -1
    nr = ctx.position.rank + direction * distance
    if not (0 <= nr <= 7):
        return
    dest = Position(ctx.position.file, nr)
    if ctx.state.board.get_unit(dest) is not None:
        return

    ctx.state.board.move_unit(ctx.position, dest)
    ctx.events.append(PieceMoved(
        piece_id=ctx.unit.piece.id, owner=ctx.unit.owner, source=ctx.position, target=dest,
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

    other_pos = Position(destination[0], destination[1])
    other_unit = ctx.state.board.get_unit(other_pos)
    if other_unit is None or other_unit.owner != ctx.unit.owner:
        raise IllegalActionError("swap_units target must be one of your own units.")
    if ctx.unit.piece.piece_type == PieceType.KING or other_unit.piece.piece_type == PieceType.KING:
        raise IllegalActionError("swap_units cannot target the King.")
    if is_committed_builder(ctx.state, ctx.unit.piece.id) or is_committed_builder(ctx.state, other_unit.piece.id):
        raise IllegalActionError("A Pawn committed to Building construction cannot be swapped.")

    max_dist = ctx.effect.params.get("max_distance")
    if max_dist is not None:
        dist = max(abs(other_pos.file - ctx.position.file), abs(other_pos.rank - ctx.position.rank))
        if dist > max_dist:
            raise IllegalActionError(f"Target is {dist} squares away — max_distance is {max_dist}.")

    src = ctx.position
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
