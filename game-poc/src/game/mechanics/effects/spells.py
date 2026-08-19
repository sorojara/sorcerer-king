"""
mechanics/effects/spells.py — SPELLS category (Stage 6)
==========================================================

Effect types that belong to the Spell/Trap card family specifically —
board-zone management and Trap destruction — rather than to a Monster's
own combat/movement kit.

Effect types in this category
------------------------------
block_zone      IMPLEMENTED — Stage 6: mark every square in the resolved
                               area as impassable for duration_turns
                               (veil_of_stillness — shape: column)
destroy_trap    IMPLEMENTED — Stage 6: remove a TrapInstance from play
                               (shatter_trap)
cancel_move     IMPLEMENTED — Stage 6: restore the board to a pre-move
                               snapshot, undoing the opponent's last chess
                               move (time_anchor). See state.MoveSnapshot
                               for why this uses a snapshot/restore
                               strategy rather than a generic undoable
                               event log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# block_zone
# ---------------------------------------------------------------------------

def _block_zone(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — veil_of_stillness.

    _execute_activate_spell resolves area-targeted Spells by calling
    resolve_effect() once PER SQUARE in the card's expanded area (same
    convention freeze_square already uses for cursed_ground) — so this
    handler only ever touches ``ctx.position``, a single square.  It gets
    a ``blocked:<duration_turns>:<owner>`` temporary effect; chess.movement
    refuses to move INTO a blocked square, mirroring "frozen".  It decays
    like frozen/scorched squares: only on the CASTER's opponent's EndTurn,
    so it blocks for exactly duration_turns full opponent turns.

    Simplification: only blocks moves landing IN the zone, not sliding
    "through" it (a full ray-interruption model is deferred, matching the
    same trade-off documented for push_unit/pass_through_units).
    """
    if ctx.state is None or ctx.position is None:
        return
    owner = ctx.extra.get("caster_owner") if ctx.extra else None
    duration = ctx.effect.params.get("duration_turns", 2)
    card_id = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(f"blocked:{duration}:{owner}:{card_id}")


# ---------------------------------------------------------------------------
# destroy_trap
# ---------------------------------------------------------------------------

def _destroy_trap(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — shatter_trap.

    Removes the TrapInstance named by ``ctx.extra["trap_instance_id"]``
    from ``state.traps``.  Validated (existence + ownership per
    ``target_owner``) in _execute_activate_spell before this handler runs.
    """
    if ctx.state is None or ctx.extra is None:
        return
    trap_id = ctx.extra.get("trap_instance_id")
    if trap_id is None:
        return
    ctx.state.traps = [t for t in ctx.state.traps if t.id != trap_id]


# ---------------------------------------------------------------------------
# cancel_move
# ---------------------------------------------------------------------------

def _cancel_move(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — time_anchor (manual Trap, activated via ActivateTrap).

    ``ctx.extra["target_player"]`` names whose move to undo (always the
    Trap owner's opponent — "the enemy's most recent chess move").
    Restores state.board from that player's MoveSnapshot wholesale, then
    reverts the handful of side-tables a MovePiece can touch (castling
    rights, en_passant_target, the defender's captured_pieces bookkeeping).

    Raises IllegalActionError if there's no snapshot to restore, or if
    that move triggered a Final Duel — time_anchor explicitly does not
    reach into a Duel already in progress.
    """
    from game.core.events import MoveCancelled
    from game.core.rules import IllegalActionError

    if ctx.state is None or ctx.extra is None:
        return
    target_player = ctx.extra.get("target_player")
    snapshot = ctx.state.move_history.get(target_player)
    if snapshot is None:
        raise IllegalActionError(f"{target_player} has no move to cancel.")
    if snapshot.triggered_duel:
        raise IllegalActionError(
            "Cannot cancel a move that triggered a Final Duel."
        )

    ctx.state.board = snapshot.board_before
    ctx.state.en_passant_target = snapshot.en_passant_before
    ctx.state.get_player(snapshot.player_id).castling_rights = snapshot.castling_rights_before

    defender_id = ctx.state.opponent_of(snapshot.player_id)
    defender_ps = ctx.state.get_player(defender_id)
    defender_ps.captured_pieces = defender_ps.captured_pieces[:snapshot.victim_captured_len_before]

    ctx.state.move_history[target_player] = None

    ctx.events.append(MoveCancelled(
        cancelled_player=snapshot.player_id,
        source=snapshot.source,
        target=snapshot.target,
        turn_number=snapshot.turn_number,
    ))


SPELLS_HANDLERS: dict[str, object] = {
    "block_zone":   _block_zone,
    "destroy_trap": _destroy_trap,
    "cancel_move":  _cancel_move,
}
