"""
mechanics/effects/_meta.py — Miscellaneous / multi-category effects
====================================================================

These effect types appear in monsters.yaml but don't belong cleanly to a
single primary category, or they span multiple systems not yet implemented.

Effect types handled here
--------------------------
copy_effect                 IMPLEMENTED — copy adjacent monster effect (mirror_magus)
vessel_support               IMPLEMENTED — allow extra vessel types in radius
                                  (broodmother; aura scan lives in
                                  mechanics.monsters.get_extra_vessel_types(),
                                  wired into SummonMonster legal-action
                                  generation + validation)
ignore_terrain               IMPLEMENTED (Stage 13) — slide THROUGH the
                                  square of a COMPLETE enemy Building instead
                                  of being stopped by it (sky_serpent); read
                                  by chess.movement._ray_moves
building_damage_bonus       IMPLEMENTED (Stage 13) — grants permission to
                                  besiege, and ``destroy_on_capture`` flattens
                                  a Building in one blow (obsidian_dragon,
                                  sovereign_of_embers); read by
                                  mechanics.buildings.can_attack_building() /
                                  attack_damage()
building_capture_protection IMPLEMENTED (Stage 13) — first layer of
                                  mechanics.buildings.damage_building()'s
                                  defensive stack (castle_keeper)
weakened_target_bonus       IMPLEMENTED — bypasses an adjacent retaliate
                                  effect ONLY when the retaliating unit's own
                                  capture_protection was already spent; see
                                  mechanics.monsters.apply_retaliate()
restore_effect_charge       IMPLEMENTED — restore a spent effect charge to an
                                  adjacent ally at end of the owner's own turn
                                  (battlefield_medic; resolved in
                                  _execute_end_turn)
challenge_unit               IMPLEMENTED — restrict an adjacent enemy to
                                  fight only the duelist for duration_turns;
                                  enforced in chess.movement.get_pseudo_legal_moves
territory_bonus              IMPLEMENTED — extra leap mobility while standing
                                  in enemy Territory (moon_stalker); read by
                                  mechanics.monsters.get_movement_additions()
enemy_territory_mobility    IMPLEMENTED (Stage 13) — same behaviour, other
                                  cards (eclipse_executioner; shadow_regent as
                                  a King policy via mechanics.kings); arms the
                                  same territory_movement_bonus status
graveyard_inspect            IMPLEMENTED — private GraveyardInspected event
                                  revealing the owner's own Graveyard (grave_scholar)
graveyard_counter            IMPLEMENTED — passive counter from destroyed
                                  allies; incremented centrally in
                                  RulesEngine._apply_death_triggered_effects
capture_protection_from_counter IMPLEMENTED — shield charges from the
                                  graveyard_counter threshold (same central hook)
dismiss_monster              IMPLEMENTED — activated dismiss of an ADJACENT
                                  ally to the Graveyard (vessel_reclaimer;
                                  distinct from the player's own DismissMonster
                                  action, which targets self and returns to hand)
graveyard_scaling_movement  IMPLEMENTED — leap bonus once the owner's
                                  Graveyard reaches a threshold (crypt_walker);
                                  read by get_movement_additions()
death_trigger_draw          IMPLEMENTED — draw once per turn on an allied
                                  Monster's death; same central hook as
                                  graveyard_counter
sacrifice_bonus              IMPLEMENTED — extra Ritual progress when this
                                  unit is a pure Ritual sacrifice (blood_seer);
                                  consumed in mechanics.rituals.execute_ritual()
ritual_activation_range     IMPLEMENTED — extends Formation Ritual node
                                  matching by radius_bonus (herald_of_the_gate);
                                  consumed in mechanics.rituals pattern matcher
restore_builder              IMPLEMENTED — on summon, restore the Builder
                                  token to up to ``max_targets`` adjacent
                                  Pawns that already spent it
                                  (guild_foreman 1, worldforge_colossus 2)
capture_then_retreat        IMPLEMENTED — post-capture retreat away from the
                                  enemy King (dusk_reaver; reuses the
                                  REPOSITION PendingDecision machinery)
temporary_vessel_class      IMPLEMENTED — unstable_transmutation (Spell): one
                                  owned piece additionally counts as each of
                                  ``vessel_classes`` for SummonMonster
                                  compatibility only (never gains that
                                  class's movement); consumed in
                                  RulesEngine._execute_summon_monster /
                                  get_legal_actions
spell_radius_bonus          IMPLEMENTED (flag only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.logger import get_logger

_log = get_logger(__name__)

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


def _spell_radius_bonus(ctx: "EffectContext") -> None:
    """IMPLEMENTED — passive flag applied at summon time (dark_magician)."""
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("bonus", 0)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("spell_radius_bonus:")]
    ctx.unit.add_status(f"spell_radius_bonus:{bonus}")


# Effect types that are safe to copy (passive — no on-activation side effects).
# Excludes effects that require their own separate activation logic.
_COPYABLE_PASSIVE_TYPES: frozenset[str] = frozenset({
    "spell_radius_bonus",
    "alter_movement",
    "capture_protection",
    "damage_aura",
    "ignore_terrain",
    "building_damage_bonus",
    "weakened_target_bonus",
    "territory_bonus",
    "graveyard_counter",
    "graveyard_scaling_movement",
    "death_trigger_draw",
    "sacrifice_bonus",
    "ritual_activation_range",
})


def _copy_effect(ctx: "EffectContext") -> None:
    """Copy one copyable passive effect from an adjacent allied monster.

    Scans the 8 squares around the caster for an allied unit that carries
    at least one effect type in _COPYABLE_PASSIVE_TYPES.  The first such
    effect found is stored as ``copied_effect:<type>`` in the caster's
    statuses.  The status is visible in the right-click info panel.

    Raises IllegalActionError when:
      • ctx.unit or ctx.position is missing
      • No adjacent allied monster with a copyable effect exists
      • ctx.registry is unavailable
    """
    from game.cards.card import MonsterCard
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None:
        raise IllegalActionError("copy_effect: missing unit or position.")
    if ctx.registry is None:
        raise IllegalActionError("copy_effect: no card registry available.")
    if ctx.state is None:
        raise IllegalActionError("copy_effect: no game state.")

    pos = ctx.position
    owner = ctx.unit.owner
    caster_id = ctx.unit.piece.id

    _log.debug("COPY     caster=%s  pos=%s  owner=%s  scanning neighbours...",
               caster_id, pos, owner)

    # Scan the 8 neighbouring squares
    found_type: str | None = None
    found_from: str | None = None
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = pos.file + df, pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            from game.chess.pieces import Position as _Pos
            neighbour_pos = _Pos(nf, nr)
            neighbour_unit = ctx.state.board.get_unit(neighbour_pos)
            if neighbour_unit is None:
                _log.debug("COPY       [%d,%d] empty", nf, nr)
                continue
            if neighbour_unit.owner != owner:
                _log.debug("COPY       [%d,%d] %s — wrong owner (%s)",
                           nf, nr, neighbour_unit.piece.id, neighbour_unit.owner)
                continue
            if neighbour_unit.monster_id is None:
                _log.debug("COPY       [%d,%d] %s — no monster",
                           nf, nr, neighbour_unit.piece.id)
                continue
            try:
                neighbour_card = ctx.registry.get(neighbour_unit.monster_id)
            except KeyError:
                _log.debug("COPY       [%d,%d] monster_id=%r not in registry",
                           nf, nr, neighbour_unit.monster_id)
                continue
            if not isinstance(neighbour_card, MonsterCard):
                continue
            _log.debug("COPY       [%d,%d] %s (monster=%s) effects=%s",
                       nf, nr, neighbour_unit.piece.id, neighbour_unit.monster_id,
                       [e.type for e in neighbour_card.effects])
            for eff in neighbour_card.effects:
                if eff.type in _COPYABLE_PASSIVE_TYPES:
                    found_type = eff.type
                    found_from = neighbour_unit.piece.id
                    _log.debug("COPY       found copyable effect %r from %s",
                               found_type, found_from)
                    break
        if found_type:
            break

    if found_type is None:
        _log.warning("COPY     FAILED  caster=%s — no adjacent ally with copyable effect", caster_id)
        raise IllegalActionError(
            "No adjacent allied monster with a copyable passive effect found."
        )

    # Remove any previous copied_effect status, then add the new one
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("copied_effect:")
    ]
    ctx.unit.add_status(f"copied_effect:{found_type}")
    _log.info("COPY     OK  caster=%s  copied=%r  from=%s  statuses=%s",
              caster_id, found_type, found_from, ctx.unit.statuses)


def _vessel_support(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — broodmother (real logic lives elsewhere).

    This is a passive positional aura: it doesn't change anything about
    broodmother's own unit, it extends the set of legal vessel piece types
    for OTHER summons happening nearby.  That can't be expressed as a status
    flag resolved once at summon time — it has to be re-checked every time a
    SummonMonster action is validated or enumerated.

    The actual scan is `mechanics.monsters.get_extra_vessel_types()`, called
    from both legal-action generation and `_execute_summon_monster()` in
    rules.py.  This handler is a no-op placeholder so the effect type is
    recognised by the registry and never logged as unresolved.
    """
    # Real implementation: mechanics.monsters.get_extra_vessel_types()
    pass


def _ignore_terrain(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — sky_serpent, "Ignores movement restrictions
    created by Buildings and Territory".

    Stage 13 made a COMPLETE enemy Building a wall: its square stops rays
    and can never be landed on (chess/movement.py blocks_movement). The
    ``ignore_building_terrain`` status armed here lets this unit SLIDE
    THROUGH such a square instead of being stopped by it. It still cannot
    stop there — the square is physically occupied by the structure, and
    clearing it is what AttackBuilding is for.

    Territory itself still gates only SummonMonster / Trap / Spell
    placement, never movement, so there is nothing further to ignore.
    """
    if ctx.unit is None:
        return
    params = ctx.effect.params
    if params.get("buildings"):
        ctx.unit.add_status("ignore_building_terrain")


def _building_damage_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — obsidian_dragon / sovereign_of_embers.

    Two things now hang off this effect, both read straight from the card
    by mechanics/buildings.py rather than from the status armed here:

      • can_attack_building() — carrying it is one of the three ways a
        unit earns permission to besiege at all (the others being "is a
        Ritual Monster" and "the Building is marked by siege_order").
      • attack_damage() — ``destroy_on_capture: true`` turns one blow into
        an outright demolition, aura durability included ("Buildings
        captured by this monster are destroyed immediately").

    The ``building_destroyer`` status is armed purely as a marker — both
    behaviours above read the CARD, not this flag, so nothing in the engine
    currently consults it. Kept as a ready-made "this piece can siege"
    lookup for a UI badge; it is deliberately not load-bearing.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("building_destroyer")


def _building_capture_protection(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — castle_keeper.

    Arms ``building_capture_protection:<radius>:<uses>``. It is the FIRST
    layer of mechanics.buildings.damage_building()'s defensive stack: a
    hostile action against an allied Building within ``radius`` of this
    Monster is absorbed whole and one charge is spent, before damage,
    vulnerability or durability auras are even considered.
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 1)
    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("building_capture_protection:")
    ]
    ctx.unit.add_status(f"building_capture_protection:{radius}:{uses}")


def _weakened_target_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — executioner.

    Arms ``guaranteed_capture_vs_no_shield`` on summon. Consumed in
    ``mechanics.monsters.apply_retaliate()``, which now also checks the
    card's own condition ("capture_protection_remaining: 0") — the
    bypass only fires when the retaliating unit had no capture_protection
    shield left at the moment it was captured, matching "Excels against
    units whose defensive effects have already been spent" instead of
    unconditionally voiding every retaliate.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("guaranteed_capture_vs_no_shield")


def _restore_effect_charge(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — battlefield_medic (real logic lives elsewhere).

    This only fires at end_of_turn, scanning the board for adjacency —
    not something a summon-time status flag can express.  The actual
    restoration is ``RulesEngine._resolve_restore_effect_charge()``, called
    from ``_execute_end_turn`` for the ending player's own units.  This
    handler is a no-op placeholder so the type is recognised by the
    registry and never logged as unresolved.
    """
    # Real implementation: RulesEngine._resolve_restore_effect_charge()
    pass


def _challenge_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — duelist activated ability.

    ``ctx.extra["target"]`` is an (file, rank) tuple naming an adjacent
    enemy unit (enumerated by RulesEngine.get_legal_actions — one
    ActivateMonsterAbility per adjacent enemy). Tags the TARGET (not the
    duelist) with ``challenged_by:<duelist_piece_id>:<duration_turns>``.

    Enforcement lives in chess.movement.get_pseudo_legal_moves():
      • the challenged unit's own captures are restricted to the duelist,
      • every OTHER unit's captures against the challenged unit are
        illegal — only the duelist may capture it.
    Decays on the challenged unit OWNER's own EndTurn (mirrors
    immobilized/exposed — "until the next turn").
    """
    from game.chess.pieces import Position
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    target = (ctx.extra or {}).get("target")
    if target is None:
        raise IllegalActionError("challenge_unit requires an adjacent enemy target.")
    tf, tr = target
    if max(abs(tf - ctx.position.file), abs(tr - ctx.position.rank)) != 1:
        raise IllegalActionError("challenge_unit target must be adjacent.")
    target_pos = Position(tf, tr)
    target_unit = ctx.state.board.get_unit(target_pos)
    if target_unit is None or target_unit.owner == ctx.unit.owner:
        raise IllegalActionError("challenge_unit target must be an adjacent enemy unit.")

    duration = ctx.effect.params.get("duration_turns", 1)
    target_unit.statuses = [
        s for s in target_unit.statuses if not s.startswith("challenged_by:")
    ]
    target_unit.add_status(f"challenged_by:{ctx.unit.piece.id}:{duration}")


def _enemy_territory_mobility(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — eclipse_executioner (Monster) and, via
    mechanics.kings._apply_enemy_territory_mobility, shadow_regent (King
    policy). Card text: this unit moves ``movement_bonus`` further while
    it stands inside enemy Territory.

    Mechanically identical to moon_stalker's ``territory_bonus`` below —
    two cards, one behaviour — so it arms the SAME
    ``territory_movement_bonus:N`` status rather than a parallel one, and
    inherits its enemy-Territory test in get_movement_additions() for free.
    The ``archetype`` param only matters for the King-policy variant, where
    the effect has to pick out which of the kingdom's Monsters it applies
    to; on the Monster's own card it describes itself, so it is not
    re-checked here.
    """
    if ctx.unit is None:
        return
    params = ctx.effect.params
    if not params.get("condition", {}).get("inside_enemy_territory", True):
        return
    bonus = params.get("movement_bonus", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("territory_movement_bonus:")
    ]
    ctx.unit.add_status(f"territory_movement_bonus:{bonus}")


def _territory_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — moon_stalker. Arms ``territory_movement_bonus:N``, read
    by ``mechanics.monsters.get_movement_additions()`` (called from
    chess.movement.get_pseudo_legal_moves with the GameState/registry it
    needs to test ``territory.is_in_territory`` against the OPPONENT's
    Territory) — adds N cardinal-direction leap squares, same shape as
    alter_movement's add_leap, while the unit currently stands inside
    enemy-controlled Territory.
    """
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("movement_bonus", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("territory_movement_bonus:")
    ]
    ctx.unit.add_status(f"territory_movement_bonus:{bonus}")


def _graveyard_inspect(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — grave_scholar on_summon. Reveals the owner's own
    Graveyard contents via a private GraveyardInspected event (same
    non-persistent-reveal convention as EnemyCardRevealed/DeckInspected —
    Observation's hidden-info filtering is untouched; a human player
    learns it by reading the event / a UI toast).
    """
    from game.core.events import GraveyardInspected

    if ctx.unit is None or ctx.state is None:
        return
    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    ctx.events.append(GraveyardInspected(player_id=owner, card_ids=tuple(ps.graveyard)))


def _graveyard_counter(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — bone_collector passive. Arms ``graveyard_counter:0``;
    incremented centrally in RulesEngine.execute() every time one of the
    owner's OTHER allied Monsters is destroyed (see
    ``RulesEngine._apply_death_triggered_effects`` — the same central
    MonsterDestroyed hook that feeds capture_protection_from_counter and
    death_trigger_draw), mirroring how PlayerState.monsters_lost_count is
    centrally maintained rather than patched into every destruction path.
    """
    if ctx.unit is None:
        return
    if not any(s.startswith("graveyard_counter:") for s in ctx.unit.statuses):
        ctx.unit.add_status("graveyard_counter:0")


def _capture_protection_from_counter(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — bone_collector passive companion to graveyard_counter.
    Arms ``capture_protection_from_counter:<required_counters>:<uses_per_counter>``,
    read by the same central hook that increments graveyard_counter: every
    time the counter reaches a new multiple of ``required_counters``, the
    unit gains ``uses_per_counter`` more capture_protection shield charges.
    """
    if ctx.unit is None:
        return
    required = ctx.effect.params.get("required_counters", 2)
    uses = ctx.effect.params.get("uses_per_counter", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses
        if not s.startswith("capture_protection_from_counter:")
    ]
    ctx.unit.add_status(f"capture_protection_from_counter:{required}:{uses}")


def _dismiss_monster(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — vessel_reclaimer activated ability.

    Two cards share this type, told apart by ``monster_destination``:

    1. vessel_reclaimer (Monster, activated): targets an ADJACENT allied
       Monster (``ctx.extra["target"]``, an (file, rank) tuple enumerated
       by RulesEngine.get_legal_actions) and sends the card to the
       GRAVEYARD — "sending the Monster card to the Graveyard".

    2. sever_the_bond (Spell, target_type "piece"): the targeted piece IS
       ``ctx.unit`` — _resolve_spell_on_piece already resolved and
       ownership-checked it, and supplies no ``target`` at all — and the
       card goes back to HAND. Requiring an adjacent second target here
       made the Spell impossible to cast at all.
    """
    from game.chess.pieces import Position
    from game.core.events import MonsterDismissed
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    params = ctx.effect.params
    destination = params.get("monster_destination", "graveyard")
    target = (ctx.extra or {}).get("target")

    if target is None:
        # sever_the_bond — the Spell's own target is the Monster to dismiss.
        if ctx.unit.monster_id is None:
            raise IllegalActionError("That piece is not hosting a Monster.")
        target_unit, target_pos = ctx.unit, ctx.position
    else:
        tf, tr = target
        if max(abs(tf - ctx.position.file), abs(tr - ctx.position.rank)) != 1:
            raise IllegalActionError("dismiss_monster target must be adjacent.")
        target_pos = Position(tf, tr)
        target_unit = ctx.state.board.get_unit(target_pos)
        if (
            target_unit is None
            or target_unit.owner != ctx.unit.owner
            or target_unit.monster_id is None
        ):
            raise IllegalActionError("dismiss_monster target must be an adjacent allied Monster.")

    card_id = target_unit.monster_id
    target_unit.monster_id = None
    target_unit.statuses = [
        s for s in target_unit.statuses
        if not any(
            s.startswith(prefix) for prefix in (
                "shield:", "spell_radius_bonus:", "stealth",
                "ritual_boost:", "frozen:",
            )
        )
    ]
    ps = ctx.state.get_player(target_unit.owner)
    if destination == "hand":
        ps.hand.append(card_id)
    else:
        ps.graveyard.append(card_id)
    ctx.events.append(MonsterDismissed(
        player_id=target_unit.owner,
        card_id=card_id,
        vessel_piece_id=target_unit.piece.id,
        position=target_pos,
    ))


def _graveyard_scaling_movement(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — crypt_walker. Arms ``graveyard_leap:<threshold>:<bonus_leap>``,
    read by ``mechanics.monsters.get_movement_additions()`` (GameState-aware
    call site — see territory_bonus above) — adds ``bonus_leap`` cardinal
    leap squares once ``len(owner.graveyard) >= threshold``.
    """
    if ctx.unit is None:
        return
    threshold = ctx.effect.params.get("threshold", 5)
    bonus_leap = ctx.effect.params.get("bonus_leap", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("graveyard_leap:")]
    ctx.unit.add_status(f"graveyard_leap:{threshold}:{bonus_leap}")


def _death_trigger_draw(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — mourning_queen passive. Arms ``death_trigger_draw:1``;
    consumed by the same central MonsterDestroyed hook as graveyard_counter
    (RulesEngine._apply_death_triggered_effects), which draws the owner one
    card the first time (per their own turn — PlayerState.
    death_trigger_draw_used_this_turn) another allied Monster is destroyed.
    """
    if ctx.unit is None:
        return
    if not any(s.startswith("death_trigger_draw:") for s in ctx.unit.statuses):
        ctx.unit.add_status("death_trigger_draw:1")


def _sacrifice_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — blood_seer. Arms ``sacrifice_ritual_bonus:N`` on summon;
    consumed in ``mechanics.rituals.execute_ritual()`` — when this unit is
    among the PURE sacrifices (not the Vessel) paid for a Ritual, the
    owner's first not-yet-REVEALED OTHER Ritual gains N progress (reusing
    the same "first not-yet-REVEALED, pool order, threshold-promotes"
    accounting as ritual_acolyte's end-of-turn ritual_progress_boost).
    """
    if ctx.unit is None:
        return
    progress = ctx.effect.params.get("ritual_progress", 2)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("sacrifice_ritual_bonus:")
    ]
    ctx.unit.add_status(f"sacrifice_ritual_bonus:{progress}")


def _ritual_activation_range(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — herald_of_the_gate. Arms ``ritual_range_bonus:N`` on
    summon; consumed by ``mechanics.rituals``' formation pattern matcher
    (_find_formation_candidates / _formation_candidate_ok via
    _ritual_range_bonus()) — a Formation Ritual's non-anchor nodes accept
    any of the owner's own units within Chebyshev N of the node's exact
    offset, not only the exact square, while any of the owner's summoned
    Monsters carries this status ("eligible components within 1 extra
    square as connected").
    """
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("radius_bonus", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("ritual_range_bonus:")]
    ctx.unit.add_status(f"ritual_range_bonus:{bonus}")


def _restore_builder(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — guild_foreman / worldforge_colossus on_summon. Adjacent
    allied Pawns that have already spent their once-per-match Builder token
    (``builder_available == False``) regain it, up to ``max_targets`` of
    them — guild_foreman asks for 1, worldforge_colossus for 2 ("restores
    exhausted builders", plural).
    """
    from game.core.events import BuilderRestored
    from game.core.phases import PieceType

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    pos = ctx.position
    owner = ctx.unit.owner
    remaining = ctx.effect.params.get("max_targets", 1)
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if remaining <= 0:
                return
            if df == 0 and dr == 0:
                continue
            nf, nr = pos.file + df, pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            from game.chess.pieces import Position as _Pos
            neighbour = ctx.state.board.get_unit(_Pos(nf, nr))
            if (
                neighbour is None
                or neighbour.owner != owner
                or neighbour.piece.piece_type != PieceType.PAWN
                or neighbour.builder_available
            ):
                continue
            neighbour.builder_available = True
            remaining -= 1
            ctx.events.append(BuilderRestored(
                player_id=owner,
                piece_id=neighbour.piece.id,
                restored_by_piece_id=ctx.unit.piece.id,
            ))


def _capture_then_retreat(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — dusk_reaver after-capture effect.

    After capturing, the attacker may retreat up to ``max_distance`` squares
    (Chebyshev) provided the destination is empty and, if
    ``must_move_away_from_enemy_king`` is set, no closer to the enemy King
    than the attacker's current position.

    Structurally identical to ``movement._reposition_unit`` (blade_dancer) —
    reuses the same REPOSITION PendingDecision / RepositionUnit resolver —
    with the extra enemy-King-distance filter applied to the option list.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType
    from game.core.state import PendingDecision

    params  = ctx.effect.params
    trigger = params.get("trigger", "")
    if trigger != "after_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    max_dist = params.get("max_distance", 1)
    must_flee = params.get("must_move_away_from_enemy_king", False)
    attacker_pos = ctx.position

    king_dist = None
    if must_flee:
        opponent = "black" if ctx.unit.owner == "white" else "white"
        king_result = ctx.state.board.find_king(opponent)
        if king_result is not None:
            king_pos, _ = king_result
            king_dist = max(
                abs(attacker_pos.file - king_pos.file),
                abs(attacker_pos.rank - king_pos.rank),
            )

    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            if df == 0 and dr == 0:
                continue
            nf = attacker_pos.file + df
            nr = attacker_pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            if ctx.state.board.get_unit(Position(nf, nr)) is not None:
                continue
            if king_dist is not None:
                king_result = ctx.state.board.find_king(
                    "black" if ctx.unit.owner == "white" else "white"
                )
                if king_result is not None:
                    king_pos, _ = king_result
                    new_dist = max(abs(nf - king_pos.file), abs(nr - king_pos.rank))
                    if new_dist < king_dist:
                        continue  # would move closer to the enemy King
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


def _suppress_monster_effects(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — monster_seal (Spell) and nullification_glyph (Trap).

    Arms ``effects_suppressed:<duration_turns>`` on ``ctx.unit`` — see
    mechanics.monsters.is_effects_suppressed() for the full list of
    live-query functions gated on it (damage_aura, movement_restriction,
    vessel_support, suppress_spell_zone, obscure_influence,
    capture_protection, activated abilities), and its scope-limitation
    note (already-banked on_summon statuses aren't retroactively undone).
    Decays on the SUPPRESSED unit's OWNER's own EndTurn (mirrors
    immobilized/exposed — "until its owner's next turn").
    """
    if ctx.unit is None:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("effects_suppressed:")]
    ctx.unit.add_status(f"effects_suppressed:{duration}")


def _temporary_vessel_class(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — unstable_transmutation. Arms
    ``vessel_class_override:<duration>:<class1>|<class2>|...`` on
    ``ctx.unit``. Read in RulesEngine._execute_summon_monster / SummonMonster
    legal-action enumeration: a SummonMonster whose card doesn't support
    the unit's real piece type is still allowed if it supports one of the
    overridden classes instead. Decays on the unit OWNER's own EndTurn
    (immobilized-style — matches "until the end of the turn").
    """
    from game.core.phases import PieceType

    if ctx.unit is None:
        return
    if ctx.unit.piece.piece_type == PieceType.KING:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    classes = ctx.effect.params.get("vessel_classes", [])
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("vessel_class_override:")
    ]
    ctx.unit.add_status(f"vessel_class_override:{duration}:{'|'.join(classes)}")


def _extra_action(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — stolen_moment ("Take an extra action this turn: you may
    cast another Spell or make an additional chess move.").

    Both allowances are plain per-turn latches on PlayerState, so the card
    is exactly "clear the latches the caster has already spent":
    ``preparation_action_used`` gates ActivateSpell (and every other major
    Preparation action), ``chess_move_used`` gates MovePiece.

    Ordering matters and works out for free: _execute_activate_spell calls
    _mark_prep_used BEFORE dispatching effects, so by the time this runs
    the latch it is clearing is the one this very Spell just set. The
    caster ends the turn having spent the Stolen Moment and holding one
    fresh action, which is the card.
    """
    if ctx.state is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return
    params = ctx.effect.params
    ps = ctx.state.get_player(owner)
    if params.get("preparation_action", True):
        ps.preparation_action_used = False
    if params.get("chess_move", True):
        ps.chess_move_used = False


def _resolved_elsewhere(ctx: "EffectContext") -> None:
    """
    INERT — effect types whose real logic lives outside this registry, but
    which CAN still reach it because the card carrying them is a Monster
    (Ritual Monsters reuse several effects originally written as King
    policies or Building auras).

    Registered so a summon-time dispatch resolves quietly instead of
    logging UNRESOLVED. Where each one actually runs:

        formation_support           mechanics.monsters.apply_monster_auras()
                                    (bannerlord_eternal) /
                                    mechanics.kings.apply_king_policy_auras()
                                    (marshal_king)
        graveyard_threshold_bonus   same pair (ossuary_king / grave_crowned_king)
        graveyard_recycle           mechanics.kings.maybe_recycle_destroyed_monster(),
                                    which accepts a Monster source as of Stage 13
        ritual_information_discount mechanics.kings.maybe_ritual_information_discount()
        territory_summon_bonus      mechanics.kings.is_in_territory_with_king_bonus()
        building_territory_bonus    mechanics.territory._building_zone_squares()
        capture_protection_aura     mechanics.buildings.apply_building_auras()
        spell_radius_aura           same
        trap_radius_bonus           mechanics.buildings.trap_radius_bonus()
        trap_territory_bonus        mechanics.territory.trap_zone_squares()
        spell_territory_bonus       mechanics.territory.spell_zone_squares()
        final_duel_guard_bonus      mechanics.duel._gather_building_support()
        final_duel_support_range    mechanics.duel._support_radius_bonus()
    """
    pass


META_HANDLERS: dict[str, object] = {
    # Resolved outside this registry — see _resolved_elsewhere().
    "formation_support":                _resolved_elsewhere,
    "graveyard_threshold_bonus":        _resolved_elsewhere,
    "graveyard_recycle":                _resolved_elsewhere,
    "ritual_information_discount":      _resolved_elsewhere,
    "territory_summon_bonus":           _resolved_elsewhere,
    "building_territory_bonus":         _resolved_elsewhere,
    "capture_protection_aura":          _resolved_elsewhere,
    "spell_radius_aura":                _resolved_elsewhere,
    "trap_radius_bonus":                _resolved_elsewhere,
    "trap_territory_bonus":             _resolved_elsewhere,
    "spell_territory_bonus":            _resolved_elsewhere,
    "final_duel_guard_bonus":           _resolved_elsewhere,
    "final_duel_support_range":         _resolved_elsewhere,
    "spell_radius_bonus":               _spell_radius_bonus,
    "suppress_monster_effects":         _suppress_monster_effects,
    "temporary_vessel_class":           _temporary_vessel_class,
    "copy_effect":                      _copy_effect,
    "vessel_support":                   _vessel_support,
    "ignore_terrain":                   _ignore_terrain,
    "building_damage_bonus":            _building_damage_bonus,
    "building_capture_protection":      _building_capture_protection,
    "weakened_target_bonus":            _weakened_target_bonus,
    "restore_effect_charge":            _restore_effect_charge,
    "challenge_unit":                   _challenge_unit,
    "territory_bonus":                  _territory_bonus,
    "enemy_territory_mobility":         _enemy_territory_mobility,
    "graveyard_inspect":                _graveyard_inspect,
    "graveyard_counter":                _graveyard_counter,
    "capture_protection_from_counter":  _capture_protection_from_counter,
    "dismiss_monster":                  _dismiss_monster,
    "graveyard_scaling_movement":       _graveyard_scaling_movement,
    "death_trigger_draw":               _death_trigger_draw,
    "sacrifice_bonus":                  _sacrifice_bonus,
    "ritual_activation_range":          _ritual_activation_range,
    "restore_builder":                  _restore_builder,
    "capture_then_retreat":             _capture_then_retreat,
    "extra_action":                     _extra_action,
}
