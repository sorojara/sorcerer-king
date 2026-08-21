"""
mechanics/effects/board_control.py — BOARD_CONTROL category
============================================================

Effect types in this category
------------------------------
freeze_square          IMPLEMENTED  — lock landing square after capture
scorch_square          IMPLEMENTED  — destroy units entering square (ember_drake)
movement_restriction   IMPLEMENTED  — limit enemy movement range (astral_binder)
suppress_spell_zone    IMPLEMENTED  — neutralise continuous Spell zone effects
                                       (blocked:/cursed:) within radius (spellbreaker)
damage_aura            IMPLEMENTED  — destroy pieces entering radius (dragon_herald)
immobilize_zone        IMPLEMENTED  — Stage 6: ANY piece that ends its move
                                       here gets immobilized, unlike
                                       freeze_square which blocks entry
                                       outright (cursed_ground)
destroy_monster_or_piece IMPLEMENTED — Stage 6: destroys just the Monster,
                                       sparing its Vessel, if the triggering
                                       piece hosts one; destroys the whole
                                       piece otherwise (pit_trap)
block_line_of_sight    IMPLEMENTED  — a 4-square line sliding pieces cannot
                                       move onto or through (wall_of_mist);
                                       write side here, read side in
                                       chess.movement._ray_moves
movement_cost_zone     IMPLEMENTED  — units starting their move inside the
                                       zone are capped to max_movement_distance
                                       (fractured_path); write side here, read
                                       side in chess.movement.get_pseudo_legal_moves
prohibit_summoning     IMPLEMENTED  — SummonMonster is illegal on a tagged
                                       square for the affected side(s)
                                       (sanctuary, vessel_lock); write side
                                       here, read side in
                                       RulesEngine._execute_summon_monster /
                                       get_legal_actions
remove_spatial_effects IMPLEMENTED  — dispel_field: strips every
                                       Spell-generated zone tag (identified
                                       by looking up each tag's trailing
                                       card_id in the registry and checking
                                       it's a SpellCard) from the area —
                                       "Buildings and Traps are unaffected"
                                       falls out naturally since their tags'
                                       card_id resolves to a different card type
temporary_territory    IMPLEMENTED  — border_beacon: tags the Trap's own
                                       radius as the owner's Territory for
                                       duration_turns; read side in
                                       mechanics.territory
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
    IMPLEMENTED — iron_vanguard on_capture effect, AND (Stage 6) cursed_ground,
    a spatial Spell that freezes every square in its area (radius=1) rather
    than one capture-landing square.

    _execute_activate_spell resolves area-targeted Spells by calling
    resolve_effect() once per square in the card's expanded area, so this
    handler still only ever touches one square (``ctx.position``) — the
    Spell case supplies ``ctx.extra["caster_owner"]`` in place of
    ``ctx.unit.owner`` since there's no attacking unit involved.

    The freeze is only decremented at the opponent's EndTurn (not the
    setting player's own EndTurn), so it effectively blocks the opponent
    for exactly N opponent turns.
    """
    from game.core.events import SquareFrozen

    params  = ctx.effect.params
    trigger = params.get("trigger", "on_capture")
    if trigger not in ("on_capture", "instant_spell"):
        return

    if ctx.position is None or ctx.state is None:
        return
    owner = ctx.unit.owner if ctx.unit is not None else (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return

    duration = params.get("duration_turns", 1)
    card_id = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(
        f"frozen:{duration}:{owner}:{card_id}"
    )
    ctx.events.append(SquareFrozen(
        position=ctx.position,
        duration_turns=duration,
        caused_by_piece_id=ctx.unit.piece.id if ctx.unit is not None else None,
    ))


# ---------------------------------------------------------------------------
# scorch_square
# ---------------------------------------------------------------------------

def _scorch_square(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — ember_drake on_capture effect.

    When this monster captures a unit, the landing square becomes
    ``scorched:<N>:<owner>`` for ``duration_turns`` turns.  Any enemy unit
    entering a scorched square afterwards is destroyed.

    Mirrors ``_freeze_square`` exactly on the write side.  The read side
    (destroying units that walk into a scorched square) lives in
    ``check_scorch_square()`` in mechanics/monsters.py, called from
    ``_execute_move_piece`` right after the damage-aura check.  Scorched
    counters tick down in ``_execute_end_turn`` alongside frozen squares.
    """
    from game.core.events import SquareScorched

    params  = ctx.effect.params
    trigger = params.get("trigger", "on_capture")
    if trigger != "on_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    duration = params.get("duration_turns", 2)
    owner    = ctx.unit.owner
    card_id  = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(
        f"scorched:{duration}:{owner}:{card_id}"
    )
    ctx.events.append(SquareScorched(
        position=ctx.position,
        duration_turns=duration,
        caused_by_piece_id=ctx.unit.piece.id,
    ))


# ---------------------------------------------------------------------------
# movement_restriction
# ---------------------------------------------------------------------------

def _movement_restriction(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — astral_binder passive aura.

    Enemy units within ``radius`` squares of this monster cannot move more
    than ``max_distance`` squares (Chebyshev) per action.

    This handler only flags the aura on the caster
    (``movement_restriction:<radius>:<max_distance>``); the caster is scanned
    for at legal-move-generation time by
    ``mechanics.monsters.get_movement_cap()``, called from
    ``chess.movement.get_pseudo_legal_moves()`` to filter the enemy's
    candidate destinations.
    """
    if ctx.unit is None:
        return
    radius      = ctx.effect.params.get("radius", 1)
    max_distance = ctx.effect.params.get("max_distance", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("movement_restriction:")
    ]
    ctx.unit.add_status(f"movement_restriction:{radius}:{max_distance}")


# ---------------------------------------------------------------------------
# suppress_spell_zone
# ---------------------------------------------------------------------------

def _suppress_spell_zone(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — spellbreaker passive aura (real logic lives elsewhere).

    Continuous Spell zone effects (``blocked:`` — veil_of_stillness,
    ``cursed:`` — cursed_ground) whose square lies within ``radius`` of a
    spellbreaker are neutralised for BOTH sides, regardless of who cast
    them — spellbreaker "denies" that ground rather than merely defending
    it. Queried live via ``mechanics.monsters.is_spell_zone_suppressed()``
    from chess.movement's blocked-square filter and from
    ``check_immobilize_zone()`` (cursed_ground's read side), matching how
    damage_aura is scanned live rather than armed as a status. This entry
    exists in the registry so the type is recognised and doesn't log to
    UNRESOLVED_EFFECTS.
    """
    # Actual implementation lives in mechanics.monsters.is_spell_zone_suppressed().
    pass


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
# immobilize_zone  (Stage 6 — cursed_ground)
# ---------------------------------------------------------------------------

def _immobilize_zone(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — cursed_ground.

    Unlike ``freeze_square`` (an unenterable square, used by iron_vanguard
    to lock the opponent OUT), cursed_ground's own wording is "Any piece
    that ends its move inside the zone cannot move on its owner's next
    turn" — entry is legal, the piece just gets stuck there afterward, and
    it affects BOTH sides, not just an enemy.

    _execute_activate_spell resolves area-targeted Spells by calling
    resolve_effect() once per square in the card's expanded area (same
    convention as freeze_square/block_zone), so this handler only ever
    touches one square (``ctx.position``), tagging it
    ``cursed:<duration>:<owner>``.  The read side —
    mechanics.monsters.check_immobilize_zone() — is called from
    _execute_move_piece for EVERY completed move (no owner filter), and
    applies the same "immobilized:N" status pit_trap/ward_of_binding use.
    """
    if ctx.state is None or ctx.position is None:
        return
    owner = ctx.unit.owner if ctx.unit is not None else (ctx.extra or {}).get("caster_owner")
    duration = ctx.effect.params.get("duration_turns", 3)
    card_id = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(f"cursed:{duration}:{owner}:{card_id}")


# ---------------------------------------------------------------------------
# destroy_monster_or_piece  (Stage 6 — pit_trap)
# ---------------------------------------------------------------------------

def _destroy_monster_or_piece(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — pit_trap.

    If the triggering piece hosts a Monster, destroy just the Monster —
    its card goes to the owner's Graveyard and the Vessel survives,
    reverted to a plain piece (same status cleanup as DismissMonster, but
    this is a destruction, not a voluntary dismissal, so the card does
    NOT return to hand).  If there's no Monster, the whole piece is
    destroyed outright.

    Kings are already exempt from every Trap effect generically (see
    mechanics.monsters._fire_trap's is_king check) — no special-casing
    needed here.
    """
    from game.core.events import MonsterDestroyed, PieceCaptured

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    unit = ctx.unit
    pos = ctx.position

    if unit.monster_id is not None:
        card_id = unit.monster_id
        unit.monster_id = None
        unit.statuses = [
            s for s in unit.statuses
            if not any(
                s.startswith(prefix) for prefix in (
                    "shield:", "spell_radius_bonus:", "stealth",
                    "ritual_boost:", "exposed:", "immobilized:",
                )
            )
        ]
        ctx.state.get_player(unit.owner).graveyard.append(card_id)
        ctx.events.append(MonsterDestroyed(
            player_id=unit.owner,
            card_id=card_id,
            vessel_piece_id=unit.piece.id,
            position=pos,
            destroyed_by_piece_id=None,
        ))
    else:
        removed = ctx.state.board.remove_unit(pos)
        if removed is not None:
            ctx.state.get_player(removed.owner).captured_pieces.append(removed.piece.id)
            ctx.events.append(PieceCaptured(
                piece_id=removed.piece.id,
                owner=removed.owner,
                captured_at=pos,
                captured_by_piece_id="pit_trap",
            ))


# ---------------------------------------------------------------------------
# block_line_of_sight  (wall_of_mist)
# ---------------------------------------------------------------------------

def _block_line_of_sight(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — wall_of_mist.

    ``_resolve_spell_on_area`` dispatches this once (radius 0 → a single
    anchor square), so this handler expands the whole ``length``-square
    line itself: starting at ``ctx.position``, extending in the +file
    direction (clamped to the board edge — a fixed, deterministic
    orientation rather than a player-chosen one, keeping the target shape
    a plain (file, rank) like every other "position" Spell). Each square
    gets a ``walled:<duration>:<owner>`` tag. Read side: chess.movement.
    _ray_moves refuses to enter OR pass through a walled square for
    sliding pieces (Queen/Rook/Bishop) only — Knights and leap-based
    monster movement additions don't ray-walk at all, so they already
    ignore it, matching "Knights and effects that leap may cross it".
    Blocks BOTH sides equally, same convention as block_zone/veil_of_stillness.
    """
    from game.chess.pieces import Position

    if ctx.state is None or ctx.position is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    params = ctx.effect.params
    length = params.get("length", 4)
    duration = params.get("duration_turns", 2)
    card_id = ctx.card.id if ctx.card is not None else "-"

    for i in range(length):
        f = ctx.position.file + i
        if f > 7:
            break
        ctx.state.board.get_square(Position(f, ctx.position.rank)).add_effect(
            f"walled:{duration}:{owner}:{card_id}"
        )


# ---------------------------------------------------------------------------
# movement_cost_zone  (fractured_path)
# ---------------------------------------------------------------------------

def _movement_cost_zone(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — fractured_path.

    ``_resolve_spell_on_area`` calls this once per square in the resolved
    3×3 area (matches freeze_square/immobilize_zone's convention), tagging
    each with ``cost_zone:<duration>:<owner>:<max_dist>``. Read side:
    chess.movement.get_pseudo_legal_moves caps a unit's move distance to
    ``max_dist`` (Chebyshev) whenever its OWN current square carries this
    tag — "Units beginning their movement inside the area" — affects
    either side, same convention as block_zone.
    """
    if ctx.state is None or ctx.position is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    params = ctx.effect.params
    duration = params.get("duration_turns", 2)
    max_dist = params.get("max_movement_distance", 1)
    card_id = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(
        f"cost_zone:{duration}:{owner}:{max_dist}:{card_id}"
    )


# ---------------------------------------------------------------------------
# prohibit_summoning  (sanctuary spell, vessel_lock trap)
# ---------------------------------------------------------------------------

def _prohibit_summoning(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — sanctuary (Spell, dispatched once per square by
    _resolve_spell_on_area) and vessel_lock (Trap, dispatched once per
    square of its own radius by _execute_activate_trap).

    Tags ``ctx.position`` with ``no_summon:<duration>:<caster_owner>:<blocked>:<card_id>``
    — ``caster_owner`` occupies the usual "owner" slot so decay follows
    the standard convention (ticks down on the CASTER's opponent's own
    EndTurn, blocking for exactly N of their turns), while ``blocked``
    (resolved from ``affected_owner`` at write time: "both"/"opponent"/
    "self") names who is actually barred from summoning there — read in
    RulesEngine._execute_summon_monster / get_legal_actions.
    """
    if ctx.state is None or ctx.position is None:
        return
    caster = (ctx.extra or {}).get("caster_owner")
    params = ctx.effect.params
    affected = params.get("affected_owner", "opponent")
    duration = params.get("duration_turns", 1)
    card_id = ctx.card.id if ctx.card is not None else "-"

    if affected == "both" or caster is None:
        blocked = "both"
    elif affected == "self":
        blocked = caster
    else:  # "opponent" (default)
        blocked = ctx.state.opponent_of(caster)

    ctx.state.board.get_square(ctx.position).add_effect(
        f"no_summon:{duration}:{caster}:{blocked}:{card_id}"
    )


# ---------------------------------------------------------------------------
# remove_spatial_effects  (dispel_field)
# ---------------------------------------------------------------------------

_ZONE_TAG_PREFIXES = ("frozen", "scorched", "blocked", "cursed", "walled", "cost_zone", "no_summon")


def _remove_spatial_effects(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — dispel_field. Strips every zone tag on ``ctx.position``
    whose source card (the trailing ``card_id`` field, always the LAST
    ``:``-separated component in every one of these formats) resolves to
    a SpellCard in the registry — "Spell-generated zones" specifically,
    per the card text; Monster-caused ``frozen:``/``scorched:`` and any
    Trap/Building-authored tag resolve to a different card type and are
    left untouched ("Buildings and Traps are unaffected").
    ``include_own``/``include_enemy`` filter by whether the zone's own
    owner (the caster who originally set it) is the current caster.
    """
    if ctx.state is None or ctx.position is None:
        return
    from game.cards.card import SpellCard

    caster = (ctx.extra or {}).get("caster_owner")
    params = ctx.effect.params
    include_own = params.get("include_own", True)
    include_enemy = params.get("include_enemy", True)

    sq = ctx.state.board.get_square(ctx.position)
    remaining = []
    for eff in sq.temporary_effects:
        parts = eff.split(":")
        if parts[0] not in _ZONE_TAG_PREFIXES:
            remaining.append(eff)
            continue
        owner = parts[2] if len(parts) > 2 else None
        card_id = parts[-1]
        is_spell = False
        if ctx.registry is not None and card_id in ctx.registry:
            card = ctx.registry.get(card_id)
            is_spell = isinstance(card, SpellCard)
        if not is_spell:
            remaining.append(eff)
            continue
        if owner == caster and not include_own:
            remaining.append(eff)
        elif owner != caster and not include_enemy:
            remaining.append(eff)
        # else: dispelled — don't add back
    sq.temporary_effects = remaining


# ---------------------------------------------------------------------------
# temporary_territory  (border_beacon)
# ---------------------------------------------------------------------------

def _temporary_territory(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — border_beacon (Trap, manual trigger — dispatched once
    per square of its own radius by _execute_activate_trap). Tags
    ``ctx.position`` with ``temp_territory:<duration>:<owner>:<card_id>``;
    read by mechanics.territory.territory_squares()/is_in_territory().
    """
    if ctx.state is None or ctx.position is None:
        return
    owner = (ctx.extra or {}).get("caster_owner") or (ctx.extra or {}).get("trap_owner")
    if owner is None:
        return
    duration = ctx.effect.params.get("duration_turns", 2)
    card_id = ctx.card.id if ctx.card is not None else "-"
    ctx.state.board.get_square(ctx.position).add_effect(
        f"temp_territory:{duration}:{owner}:{card_id}"
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

BOARD_CONTROL_HANDLERS: dict[str, object] = {
    "freeze_square":        _freeze_square,
    "scorch_square":        _scorch_square,
    "movement_restriction": _movement_restriction,
    "suppress_spell_zone":  _suppress_spell_zone,
    "destroy_monster_or_piece": _destroy_monster_or_piece,
    "damage_aura":          _damage_aura,
    "immobilize_zone":      _immobilize_zone,
    "block_line_of_sight":  _block_line_of_sight,
    "movement_cost_zone":   _movement_cost_zone,
    "prohibit_summoning":   _prohibit_summoning,
    "remove_spatial_effects": _remove_spatial_effects,
    "temporary_territory":  _temporary_territory,
}
