"""
mechanics/effects/king_duel.py — KING / DUEL category
======================================================

Effect types in this category
------------------------------
king_support_bonus          IMPLEMENTED — the EFFECT ENTRY is read off the
                             card by mechanics/duel.py at Final Duel start;
                             the on_summon status is a display marker only.
royal_support_suppression   IMPLEMENTED — same shape as king_support_bonus:
                             resolved from the card at Duel start, status is
                             a display marker only.
duel_debuff                 not fired on_summon (trigger=final_duel_start);
                             mechanics/duel.py reads the effect straight off
                             the card at Duel start instead (Stage 12).
royal_support_mark          IMPLEMENTED — enter_radius Trap effect
                             (kingslayer_alarm); tags the triggering piece,
                             read by mechanics/duel.py at Duel start.
royal_support_bonus         IMPLEMENTED (Stage 13) — rally_the_kingdom
                             (Spell, target_type "none"). Stores a timed,
                             kingdom-wide bonus on the caster's PlayerState;
                             mechanics/duel.py._gather_piece_support() adds it
                             to every qualifying piece's support amount.
royal_support_range_bonus   Not dispatched through this registry —
                             muster_bell is a ``final_duel_start`` Trap, read
                             straight off the card by mechanics/duel.py.
                             _apply_final_duel_traps(). Registered below as an
                             inert entry so a stray dispatch logs nothing.
duel_escape_bonus           Same as royal_support_range_bonus
                             (royal_escape_route).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# king_support_bonus
# ---------------------------------------------------------------------------

def _king_support_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — royal_guard / bannerlord_eternal (and marshal_king as a
    King policy). Within ``radius`` squares of the allied King, this
    monster grants ``bonus`` additional defender Guards.

    The resolution reads the EFFECT ENTRY straight off the card in
    mechanics/duel.py._apply_passive_king_duel_flags() at Final Duel start
    — it needs the live distance to the arena centre, which summon time
    cannot know. The status armed here is therefore a marker for display,
    not the load-bearing path.
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 2)
    bonus  = ctx.effect.params.get("bonus", 1)
    ctx.unit.add_status(f"king_support_bonus:{radius}:{bonus}")


# ---------------------------------------------------------------------------
# royal_support_suppression
# ---------------------------------------------------------------------------

def _royal_support_suppression(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — kingsbane / eclipse_executioner / revenant_of_the_empty_throne
    (and shadow_regent as a King policy). An attacker-owned monster near the
    defending King strips ``amount`` Guards from the defender when a Final
    Duel begins.

    Like king_support_bonus above, the resolution reads the EFFECT ENTRY off
    the card in mechanics/duel.py._apply_passive_king_duel_flags(); the
    ``royal_suppression`` status armed here is a display marker with no
    reader in the engine.
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 2)
    amount = ctx.effect.params.get("amount", 1)
    ctx.unit.add_status(f"royal_suppression:{radius}:{amount}")


# ---------------------------------------------------------------------------
# duel_debuff
# ---------------------------------------------------------------------------

def _duel_debuff(ctx: "EffectContext") -> None:
    """
    Not on_summon-compatible (params.trigger == "final_duel_start" — see
    apply_on_summon_effects' allowed-trigger list), so this handler is never
    actually dispatched: mechanics/duel.py.initialize_duel() reads the
    ``duel_debuff`` EffectEntry straight off the card at Final Duel start
    instead (it needs the live distance-to-arena-center at that moment,
    which on_summon time can't know). Kept only so an unexpected dispatch
    (e.g. a future generic on_summon sweep) fails soft instead of logging
    UNRESOLVED.

    Card text: if this monster is within
    ``condition.distance_to_enemy_king`` squares of the enemy King when the
    Duel begins, the defender starts with ``defender_guard_penalty`` fewer
    Guards.
    """
    if ctx.unit is None:
        return
    penalty = ctx.effect.params.get("defender_guard_penalty", 1)
    ctx.unit.add_status(f"duel_debuff:{penalty}")


# ---------------------------------------------------------------------------
# royal_support_mark
# ---------------------------------------------------------------------------

def _royal_support_mark(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 12. kingslayer_alarm (traps.yaml): an enemy unit
    entering the Trap's radius (``trigger: enter_radius``) is tagged
    ``duel_marked:<penalty>``. mechanics/duel.py._gather_piece_support()
    reduces that unit's own generated DuelSupportItem amount by
    ``penalty`` (floored at 0 — the item is simply dropped) when a Final
    Duel later begins with the marked unit still on the board.

    ``params.target`` is already resolved to ``ctx.unit`` by the enter_radius
    trigger-detection pathway (mechanics/monsters.py check_enter_radius_traps)
    before this handler ever runs — see registry.py's TARGET SELECTION note.
    """
    if ctx.unit is None:
        return
    penalty = ctx.effect.params.get("duel_penalty", 1)
    ctx.unit.add_status(f"duel_marked:{penalty}")


# ---------------------------------------------------------------------------
# royal_support_bonus
# ---------------------------------------------------------------------------

def _royal_support_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — rally_the_kingdom: "For 2 turns, allied units
    that already qualify as Royal Support count as one additional level of
    support. Does not expand support range."

    A kingdom-wide, timed modifier with no board anchor — there is no unit
    or square to hang it on — so it lives on PlayerState
    (``royal_support_bonus_amount`` / ``royal_support_bonus_turns``),
    alongside the other per-player latches. Two consequences follow
    directly from the card text:

      • Only the per-item AMOUNT moves. The support RADIUS is untouched,
        so a piece outside range still contributes nothing — "does not
        expand support range".
      • It is applied in mechanics/duel.py._gather_piece_support(), which
        already skips pieces whose amount falls to 0, so a Rally can never
        conjure support out of a piece that had none.

    The timer decays on the CASTER's own EndTurn (core/rules.py
    _execute_end_turn) — a persistent Spell on your own kingdom, not an
    opponent-facing zone.
    """
    if ctx.state is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return
    params = ctx.effect.params
    ps = ctx.state.get_player(owner)
    ps.royal_support_bonus_amount = max(
        ps.royal_support_bonus_amount, params.get("amount", 1)
    )
    ps.royal_support_bonus_turns = max(
        ps.royal_support_bonus_turns, params.get("duration_turns", 2)
    )


# ---------------------------------------------------------------------------
# royal_support_range_bonus / duel_escape_bonus — inert registry entries
# ---------------------------------------------------------------------------

def _final_duel_trap_marker(ctx: "EffectContext") -> None:
    """
    INERT — muster_bell's ``royal_support_range_bonus`` and
    royal_escape_route's ``duel_escape_bonus``.

    Both live on ``final_duel_start`` Traps, which no trigger-detection
    pathway ever fires: mechanics/duel.py._apply_final_duel_traps() scans
    state.traps directly when the Duel begins, because the bonus depends on
    the arena centre — a position that doesn't exist until that moment.
    Registered here purely so the effect types are recognised and never
    logged as UNRESOLVED if a future generic sweep dispatches them.
    """
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

KING_DUEL_HANDLERS: dict[str, object] = {
    "king_support_bonus":        _king_support_bonus,
    "royal_support_suppression": _royal_support_suppression,
    "duel_debuff":               _duel_debuff,
    "royal_support_mark":        _royal_support_mark,
    "royal_support_bonus":       _royal_support_bonus,
    "royal_support_range_bonus": _final_duel_trap_marker,
    "duel_escape_bonus":         _final_duel_trap_marker,
}
