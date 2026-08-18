"""
mechanics/effects/board_control.py — BOARD_CONTROL category
============================================================

Effect types in this category
------------------------------
freeze_square          IMPLEMENTED  — lock landing square after capture
scorch_square          STUB         — destroy units entering square (Stage 7+)
movement_restriction   STUB         — limit enemy movement range (Stage 7+)
suppress_spell_zone    STUB         — disable spell effects in radius (Stage 6+)
damage_aura            IMPLEMENTED  — destroy pieces entering radius (dragon_herald)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# freeze_square
# ---------------------------------------------------------------------------

def _freeze_square(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — iron_vanguard on_capture effect.

    After capturing, the landing square is marked ``frozen:<N>:<owner>``
    where N = duration_turns and owner = attacker player_id.

    The freeze is only decremented at the opponent's EndTurn (not the
    capturing player's own EndTurn), so it effectively blocks the opponent
    for exactly N opponent turns.
    """
    from game.core.events import SquareFrozen

    params  = ctx.effect.params
    trigger = params.get("trigger", "on_capture")
    if trigger != "on_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    duration = params.get("duration_turns", 1)
    owner    = ctx.unit.owner
    ctx.state.board.get_square(ctx.position).add_effect(
        f"frozen:{duration}:{owner}"
    )
    ctx.events.append(SquareFrozen(
        position=ctx.position,
        duration_turns=duration,
        caused_by_piece_id=ctx.unit.piece.id,
    ))


# ---------------------------------------------------------------------------
# scorch_square
# ---------------------------------------------------------------------------

def _scorch_square(ctx: "EffectContext") -> None:
    """
    STUB — Stage 7+

    When this monster captures a unit, the landing square becomes
    ``scorched:<N>:<owner>`` for ``duration_turns`` turns.  Any enemy unit
    entering a scorched square is destroyed.

    When implemented this will:
      1. At on_capture: write "scorched:N:owner" to the square's temp effects.
      2. In _execute_move_piece: check target square for "scorched:..." and
         destroy the moving unit if it's an enemy.
      3. Decrement scorched counters at the opponent's EndTurn (same rule as
         frozen squares).
    """
    raise NotImplementedError(
        "scorch_square is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# movement_restriction
# ---------------------------------------------------------------------------

def _movement_restriction(ctx: "EffectContext") -> None:
    """
    STUB — Stage 7+

    Enemy units within ``radius`` squares of this monster cannot move more
    than ``max_distance`` squares per action.

    When implemented this will:
      1. At movement-generation time: for each enemy unit inside the radius,
         filter candidate destinations to those within max_distance (Chebyshev)
         of the unit's current position.
      2. This is a passive aura — no summon-time action required.
    """
    raise NotImplementedError(
        "movement_restriction is not yet implemented (Stage 7+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# suppress_spell_zone
# ---------------------------------------------------------------------------

def _suppress_spell_zone(ctx: "EffectContext") -> None:
    """
    STUB — Stage 6+

    Continuous Spell effects whose affected squares overlap this monster's
    ``radius`` are suppressed while the monster is alive.

    When implemented this will:
      1. During spell-resolution (Stage 6): check all active spell zones for
         overlap with this monster's suppress radius.
      2. Overlapping spell effects are skipped for the duration.
    """
    raise NotImplementedError(
        "suppress_spell_zone is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# damage_aura  (lives here because it's a board-control passive)
# ---------------------------------------------------------------------------

def _damage_aura(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — dragon_herald passive aura.

    Destroys any enemy piece that moves within ``radius`` squares of this
    monster.  Owner's own pieces are immune (``owner_immune: true``).

    This handler is NOT invoked directly via resolve_effect; it is called
    from check_damage_aura() in monsters.py which scans all enemy monster
    units.  This entry exists in the registry so the type is recognised and
    doesn't log to UNRESOLVED_EFFECTS.
    """
    # Actual implementation lives in check_damage_aura() (monsters.py).
    # This handler is a no-op placeholder to acknowledge the type.
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

BOARD_CONTROL_HANDLERS: dict[str, object] = {
    "freeze_square":        _freeze_square,
    "scorch_square":        _scorch_square,
    "movement_restriction": _movement_restriction,
    "suppress_spell_zone":  _suppress_spell_zone,
    "damage_aura":          _damage_aura,
}
