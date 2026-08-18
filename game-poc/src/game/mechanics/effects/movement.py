"""
mechanics/effects/movement.py — MOVEMENT category
==================================================

Effect types in this category
------------------------------
alter_movement          IMPLEMENTED  — adds leap squares / stealth flag at summon
burrow                  STUB         — pass-through movement (Stage 7+)
pass_through_units      STUB         — move through occupied squares (Stage 7+)
reposition_unit         IMPLEMENTED  — post-capture teleport (blade_dancer)
push_unit               STUB         — push enemy piece on capture (Stage 7+)
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
    STUB — Stage 7+

    Move up to ``max_distance`` squares through occupied squares.
    Destination must be empty.  Subject to ``cooldown_turns``.

    When implemented this will:
      1. Check that the unit has no active "burrow_cooldown:N" status.
      2. Generate valid burrowing destinations (ignore units, destination empty).
      3. Place a BURROW PendingDecision so the player picks a destination.
      4. Set a "burrow_cooldown:N" status on the unit.
    """
    raise NotImplementedError(
        "burrow is not yet implemented (Stage 7+). "
        "Effect params: "  + repr(ctx.effect.params)
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
    IMPLEMENTED — blade_dancer after-capture effect.

    Creates a REPOSITION PendingDecision so the player picks a destination
    within Chebyshev distance ``max_distance`` of the attacker's current
    position.  Options exclude occupied squares.

    This handler is called from apply_after_capture_effects() in monsters.py.
    The trigger must be ``after_capture``.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType
    from game.core.state import PendingDecision

    params   = ctx.effect.params
    trigger  = params.get("trigger", "")
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
    STUB — Stage 7+

    On capture attempt (``trigger: on_capture_attempt``): instead of
    capturing, push the target piece ``distance`` squares in
    ``direction`` (e.g. ``away_from_source``) if that destination is
    legal and empty.

    When implemented this will:
      1. Intercept the normal capture logic via a pre-capture hook.
      2. Compute the push destination.
      3. If valid and empty: move the target there; no capture occurs.
      4. Emit a PiecePushed event (new event type to add to events.py).
    """
    raise NotImplementedError(
        "push_unit is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

MOVEMENT_HANDLERS: dict[str, object] = {
    "alter_movement":    _alter_movement,
    "burrow":            _burrow,
    "pass_through_units": _pass_through_units,
    "reposition_unit":   _reposition_unit,
    "push_unit":         _push_unit,
}
