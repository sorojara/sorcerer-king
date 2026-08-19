"""
mechanics/effects/movement.py — MOVEMENT category
==================================================

Effect types in this category
------------------------------
alter_movement          IMPLEMENTED  — adds leap squares / stealth flag at summon
burrow                  IMPLEMENTED  — activated pass-through relocation (tunnel_mole)
pass_through_units      STUB         — move through occupied squares mid-ray
                                       (needs a ray-aware move generator change,
                                       deferred — see module docstring)
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
    STUB — Stage 7+

    Allow the unit to pass through up to ``max_units`` occupied squares
    during its normal chess move.  It cannot end on an occupied square
    (``destination_must_be_empty: true``).

    When implemented this will modify get_pseudo_legal_moves() to allow
    jumping over up to N occupied squares along the movement ray.
    """
    raise NotImplementedError(
        "pass_through_units is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


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
# Export
# ---------------------------------------------------------------------------

MOVEMENT_HANDLERS: dict[str, object] = {
    "alter_movement":    _alter_movement,
    "burrow":            _burrow,
    "pass_through_units": _pass_through_units,
    "reposition_unit":   _reposition_unit,
    "push_unit":         _push_unit,
    "immobilize_piece":  _immobilize_piece,
}
