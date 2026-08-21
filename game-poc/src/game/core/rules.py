"""
RulesEngine — Stage 0 / Stage 1
=================================

The single authoritative source of game rules.

Responsibilities:
    1. Validate that an Action is legal in the current GameState.
    2. Execute the action: advance the state and produce Events.
    3. Detect check / checkmate / Final Duel triggers.
    4. Enumerate all legal actions for the current player.

Invariants enforced (from phase0.md §18):

    TURN_OWNERSHIP       — Only active_player may submit voluntary actions.
    PHASE_OWNERSHIP      — MovePiece only in CHESS phase.
    CHESS_MOVE_LIMIT     — Max one chess move per turn.
    PREPARATION_LIMIT    — Max one major preparation action per turn.
    VESSEL_OWNERSHIP     — Cannot transform an opponent's piece.
    VESSEL_COMPATIBILITY — Monster can only use supported vessel classes.
    KING_NOT_VESSEL      — King cannot be a Monster vessel.
    BUILDER_IS_PAWN      — Only Pawns may build.
    BUILDER_ONCE         — Pawn that already built cannot build again.
    HIDDEN_INFO          — Observation must never expose opponent's private cards.
    FINAL_DUEL_CAPTURE   — King capture never directly sets winner.
    SPELL_TARGET_REACH    — A "position"/"zone" Spell may target a square
                            EITHER one of the caster's own non-Pawn pieces
                            could move into right now, OR one covered by
                            one of the caster's own Buildings (Spell zone —
                            mechanics/territory.py; NOT plain Territory —
                            the bare home ranks don't qualify on their
                            own). Only one of the two needs to hold
                            (Stage 9).
    TRAP_MONSTER_ANCHOR   — A Trap may be set on a square EITHER hosting
                            one of the caster's own summoned Monsters, OR
                            covered by one of the caster's own Buildings
                            (Trap zone — NOT plain Territory; the bare
                            home ranks don't qualify on their own). Only
                            one of the two needs to hold (Stage 9).
    VESSEL_TERRITORY      — A Monster may only be summoned onto a vessel
                            standing inside the caster's own Territory —
                            a hard requirement, no alternative (Stage 9;
                            user brief: "summoning monsters into vessels
                            can only happen in your territory").

Stage 0 chess rules (simple subset, full movement in Stage 1):
    • Pieces move according to standard chess movement.
    • Captures are detected and produce PieceCaptured events.
    • Check is detected after every move.
    • Checkmate triggers FinalDuelTriggered instead of GameOver.
    • Promotion is detected when a Pawn reaches the back rank.

Stage 1 upgrades (this file):
    • MovePiece now uses chess.movement for fully-legal moves.
    • Castle action supported (kingside + queenside).
    • En passant capture detected and resolved.
    • Stalemate triggers FinalDuelTriggered(LAST_STAND).
    • Castling rights revoked on King/Rook move or Rook capture.
    • En passant target updated after every double pawn push.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.logger import get_logger

_log = get_logger(__name__)

from game.chess.board import BoardState
from game.chess.check import is_checkmate, is_stalemate
from game.chess.movement import (
    can_castle_kingside,
    can_castle_queenside,
    get_legal_moves,
    get_non_pawn_movement_squares,
    get_pseudo_legal_moves,
    has_any_legal_move,
    is_in_check,
)
from game.chess.pieces import Position, UnitInstance
from game.chess.promotion import apply_promotion, is_promotion_rank
from game.core.actions import (
    Action,
    ActivateMonsterAbility,
    ActivateRitual,
    ActivateSpell,
    ActivateTrap,
    Castle,
    ChangeKing,
    CoronateKing,
    DeclareMercenary,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    FinalDuelAction,
    MovePiece,
    PlaceMercenaryPiece,
    PlaceTrap,
    PromotePawn,
    ReorderTopDeck,
    RepositionUnit,
    SelectMercenaryCards,
    SelectRecomposeCards,
    StartConstruction,
    SummonMonster,
)
from game.core.events import (
    CardDiscarded,
    CardDrawn,
    CastlingPerformed,
    CheckDetected,
    CheckResolved,
    ChessMoveUsed,
    DeckRecycled,
    EnPassantCapture,
    Event,
    FinalDuelTriggered,
    MercenaryContractFired,
    MercenaryPiecePlaced,
    MonsterAbilityActivated,
    MonsterDestroyed,
    MonsterDismissed,
    PhaseAdvanced,
    PieceCaptured,
    PieceMoved,
    PiecePromoted,
    PrepActionUsed,
    RecomposeInitiated,
    StalemateDetected,
    TurnEnded,
    TurnStarted,
)
from game.core.phases import (
    DecisionType,
    FinalDuelType,
    HAND_SIZE_LIMIT,
    KingCardStatus,
    Phase,
    PieceType,
    VALID_VESSEL_TYPES,
)
from game.core.rng import DeterministicRNG
from game.core.state import (
    BuildingPoolEntry,
    DuelState,
    GameState,
    KingCardState,
    PendingDecision,
    PlayerState,
    TrapInstance,
)

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Validation errors
# ─────────────────────────────────────────────────────────────────────────────

class IllegalActionError(Exception):
    """Raised when an action violates a game rule."""


# ─────────────────────────────────────────────────────────────────────────────
# Phase transition helper
# ─────────────────────────────────────────────────────────────────────────────

_NORMAL_PHASE_ORDER = [
    Phase.START,
    Phase.DRAW,
    Phase.PREPARATION,
    Phase.CHESS,
    Phase.REACTION,
    Phase.END,
]


def next_phase(current: Phase) -> Phase:
    """Return the next phase in the normal turn sequence."""
    try:
        idx = _NORMAL_PHASE_ORDER.index(current)
        if idx + 1 < len(_NORMAL_PHASE_ORDER):
            return _NORMAL_PHASE_ORDER[idx + 1]
    except ValueError:
        pass
    raise ValueError(f"No automatic next phase for {current!r}")


# ─────────────────────────────────────────────────────────────────────────────
# RulesEngine
# ─────────────────────────────────────────────────────────────────────────────

class RulesEngine:
    """
    The engine that owns and enforces all game rules.

    Public API:
        engine = RulesEngine(registry=registry)   # Stage 5: registry optional
        engine.execute(state, action, rng) → list[Event]  + mutates state

    Internal helpers produce events and mutate state atomically.

    Stage 5 note: pass a CardRegistry at construction time to enable:
        • vessel compatibility enforcement (VESSEL_COMPATIBILITY invariant)
        • on-summon effect resolution
        • monster movement additions in legal-move enumeration
    """

    def __init__(self, registry: "object | None" = None) -> None:
        self._registry = registry

    # ── Public entry point ───────────────────────────────────────────────

    def execute(
        self,
        state: GameState,
        action: Action,
        rng: DeterministicRNG,
    ) -> list[Event]:
        """
        Validate and execute ``action`` against ``state``.
        Mutates ``state`` and returns the list of Events produced.

        Raises ``IllegalActionError`` if the action is invalid.
        """
        _log.debug("DISPATCH %s  player=%s  phase=%s",
                   type(action).__name__, action.player_id, state.phase.value)
        try:
            self._validate_ownership(state, action)
        except IllegalActionError as exc:
            _log.warning("OWNERSHIP FAIL  %s: %s", type(action).__name__, exc)
            raise

        events: list[Event] = []

        # Dispatch
        try:
            if isinstance(action, MovePiece):
                events = self._execute_move_piece(state, action, rng)
            elif isinstance(action, Castle):
                events = self._execute_castle(state, action)
            elif isinstance(action, SummonMonster):
                events = self._execute_summon_monster(state, action, rng, registry=self._registry)
            elif isinstance(action, RepositionUnit):
                events = self._execute_reposition_unit(state, action)
            elif isinstance(action, ReorderTopDeck):
                events = self._execute_reorder_top_deck(state, action)
            elif isinstance(action, DismissMonster):
                events = self._execute_dismiss_monster(state, action, registry=self._registry)
            elif isinstance(action, ActivateMonsterAbility):
                events = self._execute_activate_monster_ability(state, action, rng, registry=self._registry)
            elif isinstance(action, ActivateSpell):
                events = self._execute_activate_spell(state, action, rng, registry=self._registry)
            elif isinstance(action, PlaceTrap):
                events = self._execute_place_trap(state, action, registry=self._registry)
            elif isinstance(action, ActivateTrap):
                events = self._execute_activate_trap(state, action, registry=self._registry)
            elif isinstance(action, StartConstruction):
                events = self._execute_start_construction(state, action)
            elif isinstance(action, ActivateRitual):
                events = self._execute_activate_ritual(state, action, rng, registry=self._registry)
            elif isinstance(action, CoronateKing):
                events = self._execute_coronate_king(state, action)
            elif isinstance(action, ChangeKing):
                events = self._execute_change_king(state, action)
            elif isinstance(action, DeclareRecompose):
                events = self._execute_declare_recompose(state, action, rng)
            elif isinstance(action, SelectRecomposeCards):
                events = self._execute_select_recompose(state, action, rng)
            elif isinstance(action, DeclareMercenary):
                events = self._execute_declare_mercenary(state, action)
            elif isinstance(action, SelectMercenaryCards):
                events = self._execute_select_mercenary_cards(state, action)
            elif isinstance(action, PlaceMercenaryPiece):
                events = self._execute_place_mercenary_piece(state, action)
            elif isinstance(action, PromotePawn):
                events = self._execute_promote_pawn(state, action)
            elif isinstance(action, DiscardCard):
                events = self._execute_discard_card(state, action)
            elif isinstance(action, EndPreparation):
                events = self._execute_end_preparation(state, action)
            elif isinstance(action, EndTurn):
                events = self._execute_end_turn(state, action)
            elif isinstance(action, FinalDuelAction):
                events = self._execute_final_duel_action(state, action, registry=self._registry)
            else:
                raise IllegalActionError(
                    f"Unrecognized or not-yet-implemented action: {type(action).__name__}"
                )
        except IllegalActionError as exc:
            _log.warning("ILLEGAL  %s: %s", type(action).__name__, exc)
            raise
        except Exception as exc:
            _log.error("UNEXPECTED ERROR in %s: %s", type(action).__name__, exc, exc_info=True)
            raise

        _log.debug("DONE     %s  → events=[%s]",
                   type(action).__name__,
                   ", ".join(type(e).__name__ for e in events))

        # Stage 11: PlayerState.monsters_lost_count — a lifetime counter
        # feeding the "allied_monsters_destroyed_at_least_N" Ritual
        # predicate (mechanics/rituals.py). Centralized here (rather than
        # patched into every MonsterDestroyed call site — the primary
        # capture path in this file plus three in mechanics/monsters.py)
        # so no future destruction path can silently miss it.
        extra_events: list[Event] = []
        for event in events:
            if isinstance(event, MonsterDestroyed):
                state.get_player(event.player_id).monsters_lost_count += 1
                # Stage: bone_collector's graveyard_counter /
                # capture_protection_from_counter and mourning_queen's
                # death_trigger_draw — every surviving allied Monster of
                # the DESTROYED unit's owner gets a chance to react.
                self._apply_death_triggered_effects(state, event.player_id, extra_events, rng)
        events.extend(extra_events)

        state.event_log.extend(events)
        return events

    def _apply_death_triggered_effects(
        self,
        state: GameState,
        destroyed_owner: str,
        new_events: list[Event],
        rng: DeterministicRNG,
    ) -> None:
        """
        Central MonsterDestroyed reaction hook (see execute()). Scans
        ``destroyed_owner``'s SURVIVING summoned Monsters — the just-
        destroyed unit is already off the board by the time this runs, so
        it never reacts to its own death.
        """
        if self._registry is None:
            return
        from game.cards.card import MonsterCard
        from game.core.events import CardDrawn, DeckRecycled

        ps = state.get_player(destroyed_owner)
        for pos, unit in state.board.all_units_for(destroyed_owner):
            if unit.monster_id is None:
                continue
            try:
                card = self._registry.get(unit.monster_id)
            except KeyError:
                continue
            if not isinstance(card, MonsterCard):
                continue
            effect_types = {e.type for e in card.effects}

            # ── bone_collector: graveyard_counter + capture_protection_from_counter ──
            if "graveyard_counter" in effect_types:
                for idx, status in enumerate(unit.statuses):
                    if not status.startswith("graveyard_counter:"):
                        continue
                    count = int(status.split(":")[1]) + 1
                    unit.statuses[idx] = f"graveyard_counter:{count}"
                    for eff in card.effects:
                        if eff.type != "capture_protection_from_counter":
                            continue
                        required = eff.params.get("required_counters", 2)
                        bonus_uses = eff.params.get("uses_per_counter", 1)
                        if required > 0 and count % required == 0:
                            shield_idx = next(
                                (i for i, s in enumerate(unit.statuses) if s.startswith("shield:")),
                                None,
                            )
                            if shield_idx is not None:
                                current = int(unit.statuses[shield_idx].split(":")[1])
                                unit.statuses[shield_idx] = f"shield:{current + bonus_uses}"
                            else:
                                unit.add_status(f"shield:{bonus_uses}")
                    break

            # ── mourning_queen: death_trigger_draw (limit_per_turn: 1) ──
            if "death_trigger_draw" in effect_types and not ps.death_trigger_draw_used_this_turn:
                ps.death_trigger_draw_used_this_turn = True
                if not ps.deck and ps.graveyard:
                    ps.deck = list(ps.graveyard)
                    ps.graveyard.clear()
                    rng.shuffle(ps.deck)
                    new_events.append(DeckRecycled(player_id=destroyed_owner, card_count=len(ps.deck)))
                if ps.deck:
                    drawn = ps.deck.pop(0)
                    ps.hand.append(drawn)
                    new_events.append(CardDrawn(player_id=destroyed_owner, card_id=drawn))

    # ── Legal action generation ──────────────────────────────────────────

    def get_legal_actions(
        self,
        state: GameState,
        player_id: str,
        registry: "object | None" = None,
    ) -> list[Action]:
        """
        Enumerate all actions the player may legally take right now.

        Stage 1 scope (adds over Stage 0):
            • CHESS → Castle actions (kingside / queenside when legal)
            • CHESS → En passant moves included in MovePiece candidates
            • Empty CHESS → stalemate / checkmate handled automatically

        Stage 5 scope:
            • PREPARATION → DismissMonster for each deployed monster
            • PREPARATION → SummonMonster uses registry for vessel compatibility
            • CHESS → movement additions from monster alter_movement effects
        """
        # Use per-call registry if given, else fall back to engine-level registry
        registry = registry or self._registry

        if state.is_game_over():
            return []

        # Stage 12: Final Duel turn order is defender-then-attacker each
        # round, independent of state.active_player — see mechanics/duel.py.
        if state.phase == Phase.FINAL_DUEL:
            if state.duel is None or player_id not in (state.duel.attacker, state.duel.defender):
                return []
            from game.mechanics.duel import get_legal_duel_actions
            return get_legal_duel_actions(state, player_id)

        if state.active_player != player_id:
            return []

        phase = state.phase
        ps = state.get_player(player_id)
        actions: list[Action] = []

        if phase == Phase.DRAW:
            # Automatic — no player choice.
            return []

        if phase == Phase.DISCARD:
            # Player must discard cards until hand ≤ HAND_SIZE_LIMIT.
            # Each DiscardCard is a separate action; player picks one card to discard.
            for card_id in ps.hand:
                actions.append(DiscardCard(player_id=player_id, card_id=card_id))
            return actions

        if phase == Phase.RECOMPOSE_SELECTION:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                # Player must choose exactly pd.min_choices cards.
                from itertools import combinations
                n = pd.min_choices
                hand = ps.hand
                for combo in combinations(hand, n):
                    actions.append(
                        SelectRecomposeCards(
                            player_id=player_id,
                            card_ids=list(combo),
                        )
                    )
            return actions

        if phase == Phase.MERCENARY_SELECTION:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                from itertools import combinations
                n = pd.min_choices
                monster_ids = [cid for cid in ps.hand if cid in pd.options]
                for combo in combinations(monster_ids, n):
                    actions.append(
                        SelectMercenaryCards(
                            player_id=player_id,
                            card_ids=list(combo),
                        )
                    )
            return actions

        if phase == Phase.MERCENARY_PLACEMENT:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                piece_type = pd.context.get("piece_type", "pawn")
                own_ranks = (0, 1) if player_id == "white" else (6, 7)
                for f in range(8):
                    for r in own_ranks:
                        pos = Position(f, r)
                        if state.board.get_unit(pos) is None:
                            actions.append(
                                PlaceMercenaryPiece(
                                    player_id=player_id,
                                    position=pos,
                                )
                            )
            return actions

        if phase == Phase.PROMOTION_SELECTION:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                pos_tuple = pd.context.get("position")
                if pos_tuple:
                    ppos = Position(*pos_tuple)
                    for pt in ("queen", "rook", "bishop", "knight"):
                        actions.append(
                            PromotePawn(
                                player_id=player_id,
                                position=ppos,
                                piece_type=pt,
                            )
                        )
            return actions

        if phase == Phase.PREPARATION:
            # arcane_archivist's inspect_top_deck opens a REORDER_DECK
            # PendingDecision mid-PREPARATION (after the SummonMonster that
            # triggered it already consumed the preparation action) — only
            # ReorderTopDeck permutations of the peeked cards are legal
            # until it's resolved, mirroring CHESS's REPOSITION gating.
            pd = state.pending_decision
            if pd is not None and pd.decision_type == DecisionType.REORDER_DECK and pd.player_id == player_id:
                from itertools import permutations
                for perm in set(permutations(pd.options)):
                    actions.append(ReorderTopDeck(player_id=player_id, card_ids=list(perm)))
                return actions

            actions.append(EndPreparation(player_id=player_id))
            if not ps.preparation_action_used:
                from game.cards.card import MonsterCard
                from game.mechanics.buildings import is_committed_builder
                from game.mechanics.territory import territory_squares
                # Stage 9: a vessel must stand in the caster's own Territory
                # (user brief: "summoning monsters into vessels can only
                # happen in your territory" — a hard requirement, unlike
                # the OR-satisfiable Trap/Spell zones below).
                own_territory = territory_squares(state, player_id, registry)
                # Summon Monster: for each monster in hand × valid vessels
                for card_id in ps.hand:
                    if registry and card_id in registry:
                        card = registry.get(card_id)
                        if isinstance(card, MonsterCard):
                            for pos, unit in state.board.all_units_for(player_id):
                                if unit.monster_id is not None or unit.piece.piece_type == PieceType.KING:
                                    continue
                                # Stage 8: a Pawn mid-construction is committed —
                                # it cannot be transformed until it completes,
                                # is disrupted, or the Building is destroyed.
                                if is_committed_builder(state, unit.piece.id):
                                    continue
                                if pos not in own_territory:
                                    continue
                                # sanctuary / vessel_lock: prohibit_summoning zone.
                                from game.mechanics.monsters import is_summoning_prohibited
                                if is_summoning_prohibited(state.board, pos, player_id):
                                    continue
                                pt = unit.piece.piece_type.value
                                # Stage 5: vessel_support (broodmother) can extend
                                # the compatible vessel set near allied auras.
                                compatible = card.supports_vessel(pt)
                                if not compatible and registry is not None:
                                    from game.mechanics.monsters import get_extra_vessel_types
                                    compatible = pt in get_extra_vessel_types(
                                        state, pos, player_id, card.archetype, registry
                                    )
                                if not compatible:
                                    # unstable_transmutation.
                                    for status in unit.statuses:
                                        if status.startswith("vessel_class_override:"):
                                            _, _n, classes = status.split(":", 2)
                                            if any(card.supports_vessel(c) for c in classes.split("|")):
                                                compatible = True
                                            break
                                if compatible:
                                    actions.append(
                                        SummonMonster(
                                            player_id=player_id,
                                            card_id=card_id,
                                            vessel_position=pos,
                                        )
                                    )
                # Stage 5: Dismiss Monster (return card to hand, restore vessel)
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.monster_id is not None:
                        actions.append(
                            DismissMonster(
                                player_id=player_id,
                                unit_position=pos,
                            )
                        )
                # Declare Recompose (always available, risky)
                if ps.hand:
                    actions.append(DeclareRecompose(player_id=player_id))
                # Declare Mercenary — only if the player has enough Monster cards
                # AND at least one empty square in their first two ranks.
                _mercenary_costs = {
                    "pawn": 4, "knight": 6, "bishop": 6, "rook": 6, "queen": 8,
                }
                if registry is not None:
                    from game.cards.card import MonsterCard
                    monster_count = sum(
                        1 for cid in ps.hand
                        if cid in registry and isinstance(registry.get(cid), MonsterCard)
                    )
                    own_ranks = (0, 1) if player_id == "white" else (6, 7)
                    has_empty_placement = any(
                        state.board.get_unit(Position(f, r)) is None
                        for f in range(8)
                        for r in own_ranks
                    )
                    if has_empty_placement:
                        for piece_type, cost in _mercenary_costs.items():
                            if monster_count >= cost:
                                actions.append(DeclareMercenary(
                                    player_id=player_id,
                                    piece_type=piece_type,
                                ))
                # Stage 8: Start Construction — one per (available builder
                # Pawn) × (Building Pool entry with copies remaining). A
                # Pawn already committed to another Building, or standing
                # on a square that already has one, is not a candidate.
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.piece.piece_type != PieceType.PAWN or not unit.builder_available:
                        continue
                    if is_committed_builder(state, unit.piece.id):
                        continue
                    if state.board.get_square(pos).building_id is not None:
                        continue
                    for entry in ps.building_pool:
                        if entry.copies_available > 0:
                            actions.append(StartConstruction(
                                player_id=player_id,
                                pawn_position=pos,
                                building_card_id=entry.building_card_id,
                            ))
                # Coronate King (if no active king and not in check)
                if ps.active_king is None and not ps.is_in_check():
                    for kcs in ps.king_pool:
                        if kcs.status == KingCardStatus.HIDDEN:
                            actions.append(
                                CoronateKing(
                                    player_id=player_id,
                                    king_card_id=kcs.king_card_id,
                                )
                            )
                # Stage 10: Succession (if an active king exists, not in
                # check, and a HIDDEN king remains). Cost escalates with the
                # succession number (README §19.1) — see ChangeKing docstring.
                elif ps.active_king is not None and not ps.is_in_check():
                    from game.core.phases import ConstructionStatus
                    hidden_kings = [
                        kcs.king_card_id for kcs in ps.king_pool
                        if kcs.status == KingCardStatus.HIDDEN
                    ]
                    if hidden_kings:
                        succession_number = len(ps.retired_kings) + 1
                        own_pieces = [
                            pos for pos, u in state.board.all_units_for(player_id)
                            if u.piece.piece_type != PieceType.KING
                        ]
                        own_buildings = [
                            b.id for b in state.buildings
                            if b.owner == player_id and b.status == ConstructionStatus.COMPLETE
                        ]
                        for king_card_id in hidden_kings:
                            if succession_number == 1:
                                # OR cost: either a piece sacrifice or a Building.
                                for pos in own_pieces:
                                    actions.append(ChangeKing(
                                        player_id=player_id, king_card_id=king_card_id,
                                        sacrifice_position=pos,
                                    ))
                                for bld_id in own_buildings:
                                    actions.append(ChangeKing(
                                        player_id=player_id, king_card_id=king_card_id,
                                        destroy_building_id=bld_id,
                                    ))
                            elif succession_number == 2:
                                # AND cost: both a piece sacrifice and a Building.
                                for pos in own_pieces:
                                    for bld_id in own_buildings:
                                        actions.append(ChangeKing(
                                            player_id=player_id, king_card_id=king_card_id,
                                            sacrifice_position=pos, destroy_building_id=bld_id,
                                        ))
                            # succession_number >= 3 is structurally
                            # impossible (the King Pool only holds 3 cards).
                # Stage 6/9: Place Trap — one per Trap card in hand × every
                # square that qualifies EITHER way (TRAP_MONSTER_ANCHOR OR
                # Trap-zone Building coverage — see the RulesEngine
                # docstring; plain home-rank Territory does NOT qualify).
                from game.cards.card import SpellCard, TrapCard
                if registry is not None:
                    from game.mechanics.territory import trap_zone_squares
                    occupied_trap_squares = {t.position for t in state.traps}
                    own_trap_zone = trap_zone_squares(state, player_id, registry)
                    trap_candidates: set[Position] = set()
                    # TRAP_MONSTER_ANCHOR: any square hosting the player's
                    # own summoned Monster (occupied is required here).
                    for pos, unit in state.board.all_units_for(player_id):
                        if unit.monster_id is not None:
                            trap_candidates.add(pos)
                    # Building coverage: any EMPTY square inside the
                    # player's Trap zone (the original Stage 6 "empty
                    # square" rule, scoped to Building radius, NOT the
                    # whole board and NOT bare home-rank Territory).
                    for pos in own_trap_zone:
                        if state.board.get_unit(pos) is None:
                            trap_candidates.add(pos)
                    trap_candidates -= occupied_trap_squares

                    for card_id in ps.hand:
                        if card_id not in registry:
                            continue
                        card = registry.get(card_id)
                        if isinstance(card, TrapCard):
                            for pos in trap_candidates:
                                actions.append(PlaceTrap(
                                    player_id=player_id, card_id=card_id, position=pos,
                                ))
                        elif isinstance(card, SpellCard):
                            actions.extend(
                                self._legal_spell_targets(state, player_id, card_id, card, registry)
                            )
                # Stage 6: Activate Trap — one per owned manual-trigger Trap.
                if registry is not None:
                    from game.cards.card import TrapTrigger
                    for trap in state.traps:
                        if trap.owner != player_id:
                            continue
                        try:
                            tcard = registry.get(trap.card_id)
                        except KeyError:
                            continue
                        if isinstance(tcard, TrapCard) and tcard.trigger == TrapTrigger.MANUAL:
                            actions.append(ActivateTrap(
                                player_id=player_id, trap_instance_id=trap.id,
                            ))
                # Stage 11: Activate Ritual — one per legal sacrifice combo
                # per not-yet-activated Ritual in the player's pool (see
                # mechanics/rituals.find_ritual_candidates; bounded per
                # condition type, no combinatorial explosion).
                if registry is not None:
                    from game.cards.card import RitualCard
                    from game.mechanics.rituals import find_ritual_candidates
                    for rstate in ps.ritual_pool:
                        if rstate.activated or rstate.ritual_id not in registry:
                            continue
                        ritual = registry.get(rstate.ritual_id)
                        if not isinstance(ritual, RitualCard):
                            continue
                        for combo in find_ritual_candidates(state, player_id, ritual, rstate, registry):
                            actions.append(ActivateRitual(
                                player_id=player_id,
                                ritual_id=rstate.ritual_id,
                                sacrifice_positions=combo,
                            ))
            return actions

        if phase == Phase.CHESS:
            # Stage 5: if a REPOSITION decision is pending, only reposition actions
            # are legal until the player resolves it.
            pd = state.pending_decision
            if pd is not None and pd.decision_type == DecisionType.REPOSITION and pd.player_id == player_id:
                piece_id = pd.context.get("piece_id")
                cur_tuple = pd.context.get("current_pos")
                if piece_id and cur_tuple:
                    from_pos = Position(*cur_tuple)
                    for opt in pd.options:
                        to_pos = Position(*opt)
                        actions.append(RepositionUnit(
                            player_id=player_id,
                            from_position=from_pos,
                            to_position=to_pos,
                        ))
                return actions

            if not ps.chess_move_used:
                from game.mechanics.buildings import is_committed_builder
                ep = state.en_passant_target
                cr = ps.castling_rights
                for pos, unit in state.board.all_units_for(player_id):
                    # Stage 8: a Pawn committed to an active Building
                    # construction cannot move (README §12.2 — "the Pawn
                    # is committed for a period of time").
                    if is_committed_builder(state, unit.piece.id):
                        continue
                    for target in get_legal_moves(
                        state.board, pos, unit,
                        en_passant_target=ep,
                        castling_rights=cr,
                        player_id=player_id,
                        registry=registry,
                        state=state,
                    ):
                        actions.append(
                            MovePiece(
                                player_id=player_id,
                                source=pos,
                                target=target,
                            )
                        )
                # Castling actions
                if can_castle_kingside(state.board, player_id, cr):
                    actions.append(Castle(player_id=player_id, side="kingside"))
                if can_castle_queenside(state.board, player_id, cr):
                    actions.append(Castle(player_id=player_id, side="queenside"))

            # Stage 5: activated monster abilities (available any time during CHESS)
            if registry is not None:
                from game.mechanics.effects.registry import ACTIVATED_EFFECT_TYPES
                from game.cards.card import MonsterCard as _MC
                from game.core.phases import ConstructionStatus as _CS, RevelationState as _RS

                # Ability types that need a specific target enumerated,
                # rather than a single target=None action.
                _TARGETED_ABILITIES = {
                    "challenge_unit", "dismiss_monster",
                    "disable_building", "ritual_requirement_reduction",
                }

                from game.mechanics.monsters import is_effects_suppressed

                for pos, unit in state.board.all_units_for(player_id):
                    if unit.monster_id is None:
                        continue
                    # monster_seal / nullification_glyph: no activated
                    # abilities while suppressed.
                    if is_effects_suppressed(unit):
                        continue
                    try:
                        card = registry.get(unit.monster_id)
                    except KeyError:
                        continue
                    if not isinstance(card, _MC):
                        continue
                    for eff in card.effects:
                        if eff.type not in ACTIVATED_EFFECT_TYPES:
                            continue
                        if eff.type not in _TARGETED_ABILITIES:
                            actions.append(ActivateMonsterAbility(
                                player_id=player_id, unit_position=pos, ability_id=eff.type,
                            ))
                            continue

                        if eff.type == "challenge_unit":
                            for df in (-1, 0, 1):
                                for dr in (-1, 0, 1):
                                    if df == 0 and dr == 0:
                                        continue
                                    nf, nr = pos.file + df, pos.rank + dr
                                    if not (0 <= nf <= 7 and 0 <= nr <= 7):
                                        continue
                                    npos = Position(nf, nr)
                                    nunit = state.board.get_unit(npos)
                                    if nunit is not None and nunit.owner != player_id:
                                        actions.append(ActivateMonsterAbility(
                                            player_id=player_id, unit_position=pos,
                                            ability_id=eff.type, target=(nf, nr),
                                        ))
                        elif eff.type == "dismiss_monster":
                            for df in (-1, 0, 1):
                                for dr in (-1, 0, 1):
                                    if df == 0 and dr == 0:
                                        continue
                                    nf, nr = pos.file + df, pos.rank + dr
                                    if not (0 <= nf <= 7 and 0 <= nr <= 7):
                                        continue
                                    npos = Position(nf, nr)
                                    nunit = state.board.get_unit(npos)
                                    if (
                                        nunit is not None and nunit.owner == player_id
                                        and nunit.monster_id is not None
                                    ):
                                        actions.append(ActivateMonsterAbility(
                                            player_id=player_id, unit_position=pos,
                                            ability_id=eff.type, target=(nf, nr),
                                        ))
                        elif eff.type == "disable_building":
                            for df in (-1, 0, 1):
                                for dr in (-1, 0, 1):
                                    if df == 0 and dr == 0:
                                        continue
                                    nf, nr = pos.file + df, pos.rank + dr
                                    if not (0 <= nf <= 7 and 0 <= nr <= 7):
                                        continue
                                    bld_id = state.board.get_square(Position(nf, nr)).building_id
                                    if bld_id is None:
                                        continue
                                    bld = next((b for b in state.buildings if b.id == bld_id), None)
                                    if (
                                        bld is not None and bld.owner != player_id
                                        and bld.status == _CS.COMPLETE
                                    ):
                                        actions.append(ActivateMonsterAbility(
                                            player_id=player_id, unit_position=pos,
                                            ability_id=eff.type, target=bld_id,
                                        ))
                        elif eff.type == "ritual_requirement_reduction":
                            if "req_reduction:available" not in unit.statuses:
                                continue
                            for rstate in ps.ritual_pool:
                                if rstate.revelation != _RS.REVEALED:
                                    actions.append(ActivateMonsterAbility(
                                        player_id=player_id, unit_position=pos,
                                        ability_id=eff.type, target=rstate.ritual_id,
                                    ))

            if not actions:
                # No moves available — checkmate or stalemate handled by engine
                actions.append(EndTurn(player_id=player_id))
            return actions

        if phase == Phase.REACTION:
            # Stage 0: no reaction actions yet; automatic pass
            return [EndTurn(player_id=player_id)]

        if phase in (Phase.END, Phase.REACTION):
            return [EndTurn(player_id=player_id)]

        return []

    def _legal_spell_targets(
        self,
        state: GameState,
        player_id: str,
        card_id: str,
        card,   # SpellCard
        registry: "object",
    ) -> list:
        """
        Stage 6 — enumerate legal ActivateSpell actions for one Spell card,
        based on its ``target_type``.  See _execute_activate_spell for the
        matching ``action.target`` payload shapes.
        """
        actions: list = []

        if card.target_type == "none":
            actions.append(ActivateSpell(player_id=player_id, card_id=card_id, target=None))

        elif card.target_type == "trap":
            for trap in state.traps:
                if trap.owner != player_id:   # e.g. shatter_trap: target_owner=opponent
                    actions.append(ActivateSpell(
                        player_id=player_id, card_id=card_id, target=trap.id,
                    ))

        elif card.target_type == "building":
            # rapid_construction/emergency_fortifications default to the
            # caster's own Building; siege_order (target_owner: opponent)
            # targets an enemy one.
            effect = card.effects[0] if card.effects else None
            target_owner = effect.params.get("target_owner", "self") if effect is not None else "self"
            for building in state.buildings:
                owns_it = building.owner == player_id
                if (target_owner == "opponent") == owns_it:
                    continue
                actions.append(ActivateSpell(
                    player_id=player_id, card_id=card_id, target=building.id,
                ))

        elif card.target_type in ("position", "zone"):
            # Stage 9: a Spell may be aimed at a square EITHER one of the
            # caster's own non-Pawn pieces could move into right now, OR
            # one covered by one of the caster's own Buildings (possibly
            # extended further by a Shrine) — only one needs to hold; bare
            # home-rank Territory does NOT qualify on its own. Either way
            # only the anchor square is checked; the zone that expands
            # from it (column/row/radius) is unrestricted.
            from game.mechanics.territory import spell_zone_squares
            reachable = get_non_pawn_movement_squares(state.board, player_id, registry)
            reachable = reachable | spell_zone_squares(state, player_id, registry)
            if card.shape == "column":
                squares = [(f, 0) for f in range(8)]
            elif card.shape == "row":
                squares = [(0, r) for r in range(8)]
            else:
                squares = [(f, r) for f in range(8) for r in range(8)]
            for f, r in squares:
                if Position(f, r) not in reachable:
                    continue
                actions.append(ActivateSpell(
                    player_id=player_id, card_id=card_id, target=(f, r),
                ))

        elif card.target_type == "piece":
            from game.mechanics.buildings import is_committed_builder

            effect = card.effects[0] if card.effects else None
            if effect is None:
                return actions

            if effect.type == "reposition_unit":
                max_dist = effect.params.get("max_distance", 1)
                must_be_own = effect.params.get("must_be_own", True)
                for pos, unit in state.board.all_units_for(player_id):
                    if must_be_own and unit.piece.piece_type == PieceType.KING:
                        continue
                    # Stage 8: a Pawn committed to construction cannot be
                    # relocated by any means, including this Spell.
                    if is_committed_builder(state, unit.piece.id):
                        continue
                    for df in range(-max_dist, max_dist + 1):
                        for dr in range(-max_dist, max_dist + 1):
                            nf, nr = pos.file + df, pos.rank + dr
                            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                                continue
                            if state.board.get_unit(Position(nf, nr)) is not None:
                                continue
                            actions.append(ActivateSpell(
                                player_id=player_id, card_id=card_id,
                                target={"position": (pos.file, pos.rank), "destination": (nf, nr)},
                            ))

            elif effect.type == "move_unit":
                # forced_march: advance one owned Pawn ``distance`` squares
                # forward without consuming the chess move.
                piece_types = effect.params.get("piece_types", ["pawn"])
                distance = effect.params.get("distance", 1)
                direction = 1 if player_id == "white" else -1
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.piece.piece_type.value not in piece_types:
                        continue
                    if is_committed_builder(state, unit.piece.id):
                        continue
                    nr = pos.rank + direction * distance
                    if not (0 <= nr <= 7):
                        continue
                    if state.board.get_unit(Position(pos.file, nr)) is not None:
                        continue
                    actions.append(ActivateSpell(
                        player_id=player_id, card_id=card_id,
                        target={"position": (pos.file, pos.rank)},
                    ))

            elif effect.type == "swap_units":
                # exchange_of_fates: swap two of the caster's own units
                # within max_distance of each other.
                max_dist = effect.params.get("max_distance", 3)
                candidates = [
                    (pos, unit) for pos, unit in state.board.all_units_for(player_id)
                    if unit.piece.piece_type != PieceType.KING
                    and not is_committed_builder(state, unit.piece.id)
                ]
                for i, (pos1, _u1) in enumerate(candidates):
                    for pos2, _u2 in candidates[i + 1:]:
                        dist = max(abs(pos1.file - pos2.file), abs(pos1.rank - pos2.rank))
                        if dist > max_dist:
                            continue
                        actions.append(ActivateSpell(
                            player_id=player_id, card_id=card_id,
                            target={"position": (pos1.file, pos1.rank), "destination": (pos2.file, pos2.rank)},
                        ))
                        actions.append(ActivateSpell(
                            player_id=player_id, card_id=card_id,
                            target={"position": (pos2.file, pos2.rank), "destination": (pos1.file, pos1.rank)},
                        ))

            elif effect.type == "pull_unit":
                # magnetic_reversal: pull an ENEMY unit toward the caster's
                # own King. Kings are immune (matches every Trap-effect
                # convention — see mechanics.monsters._fire_trap).
                distance = effect.params.get("distance", 1)
                king_result = state.board.find_king(player_id)
                if king_result is not None:
                    king_pos, _ = king_result
                    for pos, unit in state.board.all_units_for(state.opponent_of(player_id)):
                        if unit.piece.piece_type == PieceType.KING:
                            continue
                        step_f = (king_pos.file > pos.file) - (king_pos.file < pos.file)
                        step_r = (king_pos.rank > pos.rank) - (king_pos.rank < pos.rank)
                        if step_f == 0 and step_r == 0:
                            continue
                        nf, nr = pos.file + step_f * distance, pos.rank + step_r * distance
                        if not (0 <= nf <= 7 and 0 <= nr <= 7):
                            continue
                        if state.board.get_unit(Position(nf, nr)) is not None:
                            continue
                        actions.append(ActivateSpell(
                            player_id=player_id, card_id=card_id,
                            target={"position": (pos.file, pos.rank)},
                        ))

        return actions

    # ── Validation helpers ───────────────────────────────────────────────

    def _validate_ownership(self, state: GameState, action: Action) -> None:
        """Invariant: only active_player may submit voluntary actions."""
        # Follow-up actions during interrupt phases must match the pending player.
        if state.phase in (
            Phase.RECOMPOSE_SELECTION,
            Phase.MERCENARY_SELECTION,
            Phase.MERCENARY_PLACEMENT,
            Phase.PROMOTION_SELECTION,
        ):
            pd = state.pending_decision
            if pd and action.player_id != pd.player_id:
                raise IllegalActionError(
                    f"Decision belongs to {pd.player_id!r}, "
                    f"not {action.player_id!r}."
                )
            return
        # Stage 12: inside a Final Duel, turn order is defender-then-attacker
        # each round (mechanics/duel.py) — NOT state.active_player, which
        # still names whoever's normal-turn move triggered the Duel.
        if state.phase == Phase.FINAL_DUEL and state.duel is not None:
            expected = (
                state.duel.defender if not state.duel.defender_acted_this_round
                else state.duel.attacker
            )
            if action.player_id != expected:
                raise IllegalActionError(
                    f"It is {expected!r}'s Duel action, not {action.player_id!r}'s."
                )
            return
        if action.player_id != state.active_player:
            raise IllegalActionError(
                f"It is {state.active_player!r}'s turn, "
                f"not {action.player_id!r}'s."
            )

    def _require_phase(self, state: GameState, *phases: Phase) -> None:
        if state.phase not in phases:
            raise IllegalActionError(
                f"Action not allowed in phase {state.phase!r}. "
                f"Expected one of: {[p.value for p in phases]}"
            )

    def _require_no_prep_used(self, state: GameState, player_id: str) -> None:
        if state.get_player(player_id).preparation_action_used:
            raise IllegalActionError("Preparation action already used this turn.")

    def _mark_prep_used(
        self,
        state: GameState,
        player_id: str,
        action_name: str,
        events: list[Event],
    ) -> None:
        state.get_player(player_id).preparation_action_used = True
        events.append(PrepActionUsed(player_id=player_id, action_type=action_name))

    def _advance_phase(
        self,
        state: GameState,
        player_id: str,
        events: list[Event],
    ) -> None:
        old = state.phase
        new = next_phase(old)
        state.phase = new
        events.append(PhaseAdvanced(player_id=player_id, from_phase=old, to_phase=new))
        # Auto-execute DRAW phase
        if new == Phase.DRAW:
            self._auto_draw(state, player_id, events)
        if new == Phase.START:
            pass  # start-of-turn effects (Stage 1+)

    # ── Phase auto-executors ─────────────────────────────────────────────

    def _auto_start_turn(
        self,
        state: GameState,
        player_id: str,
        rng: DeterministicRNG,
        events: list[Event],
    ) -> None:
        """Called at the beginning of a player's turn."""
        ps = state.get_player(player_id)
        ps.reset_turn_flags()
        events.append(TurnStarted(player_id=player_id, turn_number=state.turn_number))
        # Advance through START → DRAW → PREPARATION automatically
        old = state.phase
        state.phase = Phase.DRAW
        events.append(PhaseAdvanced(player_id=player_id, from_phase=old, to_phase=Phase.DRAW))
        self._auto_draw(state, player_id, events, rng=rng)

    def _auto_draw(
        self,
        state: GameState,
        player_id: str,
        events: list[Event],
        rng: "DeterministicRNG | None" = None,
    ) -> None:
        """
        Draw 1 card from deck to hand.

        If the deck is empty, recycle the graveyard: shuffle it back into a
        fresh deck (requires ``rng``; if None the graveyard order is preserved)
        before drawing.  If both deck and graveyard are empty, no card is drawn.
        """
        ps = state.get_player(player_id)
        if not ps.deck and ps.graveyard:
            # Recycle: graveyard → new deck
            ps.deck = list(ps.graveyard)
            ps.graveyard.clear()
            if rng is not None:
                rng.shuffle(ps.deck)
            events.append(DeckRecycled(
                player_id=player_id,
                card_count=len(ps.deck),
            ))
        if ps.deck:
            card_id = ps.deck.pop(0)
            ps.hand.append(card_id)
            events.append(CardDrawn(player_id=player_id, card_id=card_id))
        # Advance to PREPARATION
        state.phase = Phase.PREPARATION
        events.append(PhaseAdvanced(
            player_id=player_id,
            from_phase=Phase.DRAW,
            to_phase=Phase.PREPARATION,
        ))

    # ── Action executors ─────────────────────────────────────────────────

    def _execute_move_piece(
        self,
        state: GameState,
        action: MovePiece,
        rng: DeterministicRNG,
    ) -> list[Event]:
        self._require_phase(state, Phase.CHESS)
        ps = state.get_player(action.player_id)
        if ps.chess_move_used:
            raise IllegalActionError("Chess move already used this turn.")

        unit = state.board.get_unit(action.source)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.source}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot move an opponent's piece.")

        from game.mechanics.buildings import is_committed_builder
        if is_committed_builder(state, unit.piece.id):
            raise IllegalActionError(
                "This Pawn is committed to a Building under construction and cannot move."
            )

        ep = state.en_passant_target
        cr = ps.castling_rights
        legal = get_legal_moves(
            state.board, action.source, unit,
            en_passant_target=ep,
            castling_rights=cr,
            player_id=action.player_id,
            registry=self._registry,
            state=state,
        )
        if action.target not in legal:
            raise IllegalActionError(
                f"Illegal move: {action.source} → {action.target}."
            )

        # ── Stage 6: snapshot for time_anchor's cancel_move ────────────────
        # Captured BEFORE any mutation. Overwrites this player's previous
        # snapshot — GameState.move_history only ever needs "their most
        # recent move". triggered_duel is patched to True below if this
        # move turns out to trigger a Final Duel.
        import copy as _copy
        from game.core.state import MoveSnapshot
        move_snapshot = MoveSnapshot(
            player_id=action.player_id,
            turn_number=state.turn_number,
            source=action.source,
            target=action.target,
            board_before=_copy.deepcopy(state.board),
            victim_captured_len_before=len(
                state.get_player(state.opponent_of(action.player_id)).captured_pieces
            ),
            castling_rights_before=_copy.deepcopy(ps.castling_rights),
            en_passant_before=ep,
        )
        state.move_history[action.player_id] = move_snapshot

        events: list[Event] = []

        # ── En passant detection ──────────────────────────────────────────
        is_ep = (
            unit.piece.piece_type == PieceType.PAWN
            and ep is not None
            and action.target == ep
            and state.board.get_unit(action.target) is None
        )
        if is_ep:
            direction = 1 if unit.owner == "white" else -1
            captured_sq = Position(action.target.file, action.target.rank - direction)
            ep_captured_unit = state.board.remove_unit(captured_sq)
            state.board.move_unit(action.source, action.target)
            events.append(PieceMoved(
                piece_id=unit.piece.id,
                owner=unit.owner,
                source=action.source,
                target=action.target,
            ))
            if ep_captured_unit is not None:
                opp = state.get_player(state.opponent_of(action.player_id))
                opp.captured_pieces.append(ep_captured_unit.piece.id)
                # Stage 8: disruption — capturing a committed Builder Pawn
                # (even via en passant) destroys its half-built Building.
                from game.mechanics.buildings import find_committed_building, cancel_construction
                disrupted = find_committed_building(state, ep_captured_unit.piece.id)
                if disrupted is not None:
                    cancel_construction(state, disrupted, events, destroyed_by=unit.piece.id)
                events.append(EnPassantCapture(
                    player_id=action.player_id,
                    pawn_piece_id=unit.piece.id,
                    source=action.source,
                    target=action.target,
                    captured_pawn_piece_id=ep_captured_unit.piece.id,
                    captured_pawn_at=captured_sq,
                ))
        else:
            # ── Normal move ───────────────────────────────────────────────
            # Stage 5: push_unit (storm_dragon) may replace a capture attempt
            # entirely — must be checked before any board mutation.
            target_unit = state.board.get_unit(action.target)
            if (
                target_unit is not None
                and target_unit.owner != action.player_id
                and unit.monster_id is not None
                and self._registry is not None
            ):
                from game.cards.card import MonsterCard as _MC
                try:
                    attacker_card = self._registry.get(unit.monster_id)
                except KeyError:
                    attacker_card = None
                if attacker_card is not None and isinstance(attacker_card, _MC):
                    from game.mechanics.monsters import try_push_unit
                    if try_push_unit(
                        state, unit, action.source, action.target,
                        attacker_card, events,
                    ):
                        self._update_castling_rights(state, action.player_id, unit, action.source)
                        state.en_passant_target = None
                        ps.chess_move_used = True
                        events.append(ChessMoveUsed(player_id=action.player_id))
                        self._update_check_status(state, events)
                        if state.phase == Phase.CHESS:
                            old = state.phase
                            state.phase = Phase.REACTION
                            events.append(PhaseAdvanced(
                                player_id=action.player_id,
                                from_phase=old,
                                to_phase=Phase.REACTION,
                            ))
                        return events

            # Stage 5: check capture protection BEFORE the board mutation.
            # Not gated to Monster units — builders_ward grants a plain
            # committed Builder Pawn a shield too; apply_capture_protection
            # itself already just reads/consumes the "shield:" status
            # regardless of what granted it.
            from game.mechanics.monsters import apply_capture_protection, find_cancel_capture_trap
            target_unit = state.board.get_unit(action.target)
            shield_absorbed = target_unit is not None and apply_capture_protection(target_unit)

            # guardian_sigils: cancels the capture outright (distinct from
            # a shield — no charge is spent on the DEFENDER, the TRAP is
            # consumed instead). Only checked if the shield didn't already
            # handle it, matching "first" layer-of-defense priority.
            cancel_trap = None
            if not shield_absorbed and target_unit is not None:
                cancel_trap = find_cancel_capture_trap(state, action.target, self._registry)
                if cancel_trap is not None:
                    shield_absorbed = True  # reuse the same restore-and-report branch below

            captured = state.board.move_unit(action.source, action.target)

            if shield_absorbed and captured is not None:
                # The capture was blocked by the shield.
                # Restore: move attacker back to source, defender back to target.
                attacker = state.board.remove_unit(action.target)
                if attacker is not None:
                    state.board.place_unit(action.source, attacker)
                state.board.place_unit(action.target, captured)
                from game.core.events import CaptureBlocked
                shields_left = next(
                    (int(s.split(":")[1]) for s in captured.statuses
                     if s.startswith("shield:")),
                    0,
                )
                events.append(CaptureBlocked(
                    attacker_piece_id=unit.piece.id,
                    defender_piece_id=captured.piece.id,
                    position=action.target,
                    shields_remaining=shields_left,
                ))
                if cancel_trap is not None:
                    from game.core.events import TrapTriggered
                    events.append(TrapTriggered(
                        trap_instance_id=cancel_trap.id, triggering_piece_id=unit.piece.id,
                    ))
                    if cancel_trap.charges is not None:
                        cancel_trap.charges -= 1
                        if cancel_trap.charges <= 0:
                            state.traps = [t for t in state.traps if t.id != cancel_trap.id]
            else:
                events.append(PieceMoved(
                    piece_id=unit.piece.id,
                    owner=unit.owner,
                    source=action.source,
                    target=action.target,
                ))
                if captured is not None:
                    opp = state.get_player(state.opponent_of(action.player_id))
                    opp.captured_pieces.append(captured.piece.id)
                    # ── Stage 8: disruption ───────────────────────────────
                    # Capturing a committed Builder Pawn destroys its
                    # half-built Building (README §12.2 step 4).
                    from game.mechanics.buildings import find_committed_building, cancel_construction
                    disrupted = find_committed_building(state, captured.piece.id)
                    if disrupted is not None:
                        cancel_construction(state, disrupted, events, destroyed_by=unit.piece.id)
                    # ── Stage 5: monster destruction event ──────────────
                    if captured.monster_id is not None:
                        events.append(MonsterDestroyed(
                            player_id=captured.owner,
                            card_id=captured.monster_id,
                            vessel_piece_id=captured.piece.id,
                            position=action.target,
                            destroyed_by_piece_id=unit.piece.id,
                        ))
                        # Stage 10: grave_crowned_king's graveyard_recycle policy.
                        from game.mechanics.kings import maybe_recycle_destroyed_monster
                        maybe_recycle_destroyed_monster(
                            state, captured.owner, captured.monster_id, events, self._registry,
                        )
                    events.append(PieceCaptured(
                        piece_id=captured.piece.id,
                        owner=captured.owner,
                        captured_at=action.target,
                        captured_by_piece_id=unit.piece.id,
                    ))

                    # ── Stage 11: "losing the Queen" Ritual-revelation
                    # trigger (README §15.1) ──────────────────────────────
                    from game.mechanics.rituals import on_piece_lost
                    on_piece_lost(
                        state, captured.owner, captured.piece.piece_type,
                        events, self._registry,
                    )

                    # ── Stage 5: retaliate (thorn_boar) ──────────────────
                    # If the captured unit had "retaliate", the attacker is
                    # destroyed too — skip after-capture effects / damage
                    # aura below since the attacker no longer exists.
                    from game.mechanics.monsters import apply_retaliate
                    attacker_destroyed = apply_retaliate(
                        state, captured, action.target, unit, events
                    )

                    # ── Stage 5: after-capture effects on the attacker ───
                    # (freeze_square for iron_vanguard, reposition for blade_dancer)
                    if (
                        not attacker_destroyed
                        and unit.monster_id is not None
                        and self._registry is not None
                    ):
                        try:
                            attacker_card = self._registry.get(unit.monster_id)
                        except KeyError:
                            attacker_card = None
                        from game.cards.card import MonsterCard as _MC
                        if attacker_card is not None and isinstance(attacker_card, _MC):
                            from game.mechanics.monsters import apply_after_capture_effects
                            apply_after_capture_effects(
                                state, unit, action.target, attacker_card, events
                            )

                    # ── Stage 6: capture-trigger Traps (counter_strike) ──
                    # Fires against the CAPTURING piece if it landed inside
                    # a Trap the defender owns.
                    if not attacker_destroyed and self._registry is not None:
                        from game.mechanics.monsters import check_capture_traps
                        check_capture_traps(
                            state, unit, captured.owner, action.target, events, self._registry, rng=rng
                        )

                    if attacker_destroyed:
                        # Attacker is gone — nothing more can happen at this
                        # square (no damage aura / promotion / King-capture
                        # duel; the attacker isn't there to have captured a King).
                        self._update_castling_rights(state, action.player_id, unit, action.source)
                        state.en_passant_target = None
                        ps.chess_move_used = True
                        events.append(ChessMoveUsed(player_id=action.player_id))
                        self._update_check_status(state, events)
                        if state.phase == Phase.CHESS:
                            old = state.phase
                            state.phase = Phase.REACTION
                            events.append(PhaseAdvanced(
                                player_id=action.player_id,
                                from_phase=old,
                                to_phase=Phase.REACTION,
                            ))
                        return events

                # ── Stage 5: damage aura check after any (non-blocked) move ─
                # Check if the moving piece entered an enemy damage aura.
                # Must run AFTER the piece has landed at action.target.
                from game.mechanics.monsters import check_damage_aura
                aura_destroyed = check_damage_aura(state, unit, action.target, events, self._registry)

                # ── Stage 5: scorch_square check (ember_drake) ───────────
                # Any unit that walks onto (or is left standing on) a
                # scorched square is destroyed — including the attacker
                # that just captured there.
                if not aura_destroyed:
                    from game.mechanics.monsters import check_scorch_square
                    scorch_destroyed = check_scorch_square(state, unit, action.target, events)

                    # ── Stage 6: enter_radius Traps (pit_trap, ward_of_binding) ─
                    # Fires against the MOVING piece if it survived — checked
                    # for every move, capture or not.
                    if not scorch_destroyed and self._registry is not None:
                        from game.mechanics.monsters import check_enter_radius_traps
                        check_enter_radius_traps(
                            state, unit, action.target, events, self._registry, rng=rng
                        )

                    # ── Stage 6: immobilize_zone (cursed_ground) ─────────
                    # Applies to ANY piece, either side — not gated on
                    # owner like Traps are.
                    if not scorch_destroyed:
                        from game.mechanics.monsters import check_immobilize_zone
                        check_immobilize_zone(state, unit, action.target, registry=self._registry)

            # King capture → Final Duel (never sets winner directly)
            if captured is not None and not shield_absorbed and captured.piece.piece_type == PieceType.KING:
                move_snapshot.triggered_duel = True
                self._trigger_final_duel(
                    state, FinalDuelType.ASSAULT, action.player_id, captured.owner,
                    events, trigger_position=action.target,
                    captured_king_piece_id=captured.piece.id,
                )
                self._revoke_castling_rights_for_captured_rook(
                    state, captured, action.target
                )
                return events

        # ── Update castling rights ────────────────────────────────────────
        self._update_castling_rights(state, action.player_id, unit, action.source)

        # ── Update en passant target ──────────────────────────────────────
        state.en_passant_target = self._compute_en_passant_target(
            unit, action.source, action.target
        )

        # ── Promotion ─────────────────────────────────────────────────────
        if is_promotion_rank(action.target, action.player_id):
            if unit.piece.piece_type == PieceType.PAWN:
                state.pending_decision = PendingDecision(
                    player_id=action.player_id,
                    decision_type=DecisionType.PROMOTION,
                    options=["queen", "rook", "bishop", "knight"],
                    min_choices=1,
                    max_choices=1,
                    context={"position": (action.target.file, action.target.rank)},
                )
                state.phase = Phase.PROMOTION_SELECTION

        ps.chess_move_used = True
        events.append(ChessMoveUsed(player_id=action.player_id))

        # ── Check status update ───────────────────────────────────────────
        self._update_check_status(state, events)

        # ── Checkmate / stalemate detection ──────────────────────────────
        opp_id = state.opponent_of(action.player_id)
        opp_ps = state.get_player(opp_id)
        opp_ep = state.en_passant_target   # already cleared above (en passant consumed)
        opp_cr = opp_ps.castling_rights

        if is_checkmate(state.board, opp_id, opp_ep, opp_cr):
            move_snapshot.triggered_duel = True
            self._trigger_final_duel(state, FinalDuelType.SIEGE, action.player_id, opp_id, events)
        elif is_stalemate(state.board, opp_id, opp_ep, opp_cr):
            move_snapshot.triggered_duel = True
            events.append(StalemateDetected(player_id=opp_id))
            self._trigger_final_duel(state, FinalDuelType.LAST_STAND, action.player_id, opp_id, events)

        # If not in an interrupt phase, advance to REACTION.
        # Exception: if a REPOSITION decision is pending (blade_dancer after-capture),
        # stay in CHESS so the player can execute RepositionUnit before REACTION.
        if state.phase == Phase.CHESS:
            if (state.pending_decision is not None
                    and state.pending_decision.decision_type == DecisionType.REPOSITION):
                pass  # stay in CHESS; REACTION will fire after RepositionUnit resolves
            else:
                old = state.phase
                state.phase = Phase.REACTION
                events.append(PhaseAdvanced(
                    player_id=action.player_id,
                    from_phase=old,
                    to_phase=Phase.REACTION,
                ))
        return events

    def _execute_castle(
        self,
        state: GameState,
        action: Castle,
    ) -> list[Event]:
        """
        Execute a castling move.  Both King and Rook are moved atomically.
        Castling rights are fully revoked for the player afterwards.
        """
        self._require_phase(state, Phase.CHESS)
        ps = state.get_player(action.player_id)
        if ps.chess_move_used:
            raise IllegalActionError("Chess move already used this turn.")

        cr = ps.castling_rights
        if action.side == "kingside":
            if not can_castle_kingside(state.board, action.player_id, cr):
                raise IllegalActionError(
                    f"Kingside castling is not legal for {action.player_id!r}."
                )
        elif action.side == "queenside":
            if not can_castle_queenside(state.board, action.player_id, cr):
                raise IllegalActionError(
                    f"Queenside castling is not legal for {action.player_id!r}."
                )
        else:
            raise IllegalActionError(f"Unknown castling side: {action.side!r}")

        # Stage 6: time_anchor only supports undoing MovePiece (see
        # state.MoveSnapshot). Clear any stale snapshot rather than let
        # time_anchor silently restore the wrong, older move.
        state.move_history[action.player_id] = None

        rank = 0 if action.player_id == "white" else 7
        king_from = Position(4, rank)  # e-file

        if action.side == "kingside":
            king_to = Position(6, rank)   # g-file
            rook_from = Position(7, rank)  # h-file
            rook_to = Position(5, rank)    # f-file
        else:
            king_to = Position(2, rank)   # c-file
            rook_from = Position(0, rank)  # a-file
            rook_to = Position(3, rank)    # d-file

        # Move both pieces
        state.board.move_unit(king_from, king_to)
        state.board.move_unit(rook_from, rook_to)

        # Revoke all castling rights
        cr.revoke_all()

        # Clear en passant
        state.en_passant_target = None

        ps.chess_move_used = True
        events: list[Event] = [
            CastlingPerformed(
                player_id=action.player_id,
                side=action.side,
                king_from=king_from,
                king_to=king_to,
                rook_from=rook_from,
                rook_to=rook_to,
            ),
            ChessMoveUsed(player_id=action.player_id),
        ]

        self._update_check_status(state, events)

        # Check for checkmate/stalemate after castling
        opp_id = state.opponent_of(action.player_id)
        opp_ps = state.get_player(opp_id)
        if is_checkmate(state.board, opp_id, state.en_passant_target, opp_ps.castling_rights):
            self._trigger_final_duel(state, FinalDuelType.SIEGE, action.player_id, opp_id, events)
        elif is_stalemate(state.board, opp_id, state.en_passant_target, opp_ps.castling_rights):
            events.append(StalemateDetected(player_id=opp_id))
            self._trigger_final_duel(state, FinalDuelType.LAST_STAND, action.player_id, opp_id, events)

        if state.phase == Phase.CHESS:
            old = state.phase
            state.phase = Phase.REACTION
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.REACTION,
            ))
        return events

    def _execute_summon_monster(
        self,
        state: GameState,
        action: SummonMonster,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — full vessel compatibility check + on-summon effects.

        Invariants enforced:
            VESSEL_OWNERSHIP      — only own pieces
            KING_NOT_VESSEL       — King excluded
            VESSEL_COMPATIBILITY  — piece type must be in supported_vessels
        """
        from game.cards.card import MonsterCard
        from game.core.events import MonsterSummoned
        from game.mechanics.monsters import apply_on_summon_effects

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        unit = state.board.get_unit(action.vessel_position)
        if unit is None:
            raise IllegalActionError(
                f"No piece at vessel position {action.vessel_position}."
            )
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot transform opponent's piece.")  # VESSEL_OWNERSHIP
        if unit.piece.piece_type == PieceType.KING:
            raise IllegalActionError("King cannot be a Monster vessel.")    # KING_NOT_VESSEL
        if unit.monster_id is not None:
            raise IllegalActionError("Piece is already hosting a Monster.")

        from game.mechanics.buildings import is_committed_builder
        if is_committed_builder(state, unit.piece.id):
            raise IllegalActionError("This Pawn is committed to a Building under construction.")

        # ── Stage 5: vessel compatibility check via registry ─────────────
        # (looked up before the Territory check so a Stage 10 King's
        # territory_summon_bonus can be scoped to this card's archetype.)
        if registry is not None:
            try:
                card = registry.get(action.card_id)
            except KeyError:
                raise IllegalActionError(f"Unknown card: {action.card_id!r}")
            if not isinstance(card, MonsterCard):
                raise IllegalActionError(
                    f"Card {action.card_id!r} is not a Monster card."
                )
        else:
            card = None

        # ── Stage 9/10: VESSEL_TERRITORY — summoning only in own Territory,
        # extended by a Stage 10 King's territory_summon_bonus policy
        # (dragon_high_king: Dragons may summon slightly past the boundary
        # while inside friendly Territory).
        from game.mechanics.kings import is_in_territory_with_king_bonus
        archetype = card.archetype if card is not None else None
        if not is_in_territory_with_king_bonus(
            state, action.vessel_position, action.player_id, archetype, registry
        ):
            raise IllegalActionError(
                f"Vessel at {action.vessel_position} is outside your Territory."
            )  # VESSEL_TERRITORY

        # sanctuary / vessel_lock: prohibit_summoning zone.
        from game.mechanics.monsters import is_summoning_prohibited
        if is_summoning_prohibited(state.board, action.vessel_position, action.player_id):
            raise IllegalActionError(
                f"Summoning is prohibited at {action.vessel_position} right now."
            )

        if registry is not None:
            pt = unit.piece.piece_type.value
            compatible = card.supports_vessel(pt)
            if not compatible:
                # Stage 5: vessel_support (broodmother) — an allied aura
                # nearby may extend the compatible vessel set.
                from game.mechanics.monsters import get_extra_vessel_types
                compatible = pt in get_extra_vessel_types(
                    state, action.vessel_position, action.player_id,
                    card.archetype, registry,
                )
            if not compatible:
                # Stage 10: vessel_support (King policy) — a matching
                # archetype King may extend the compatible vessel set too.
                from game.mechanics.kings import king_extra_vessel_types
                compatible = pt in king_extra_vessel_types(
                    state, action.player_id, card.archetype, registry,
                )
            if not compatible:
                # unstable_transmutation's temporary_vessel_class — this
                # unit additionally COUNTS AS each overridden class for
                # compatibility purposes only.
                for status in unit.statuses:
                    if status.startswith("vessel_class_override:"):
                        _, _n, classes = status.split(":", 2)
                        if any(card.supports_vessel(c) for c in classes.split("|")):
                            compatible = True
                        break
            if not compatible:
                raise IllegalActionError(
                    f"Monster {action.card_id!r} cannot use "
                    f"{unit.piece.piece_type.value!r} as a vessel. "
                    f"Supported: {list(card.supported_vessels)}"
                )  # VESSEL_COMPATIBILITY

        unit.monster_id = action.card_id
        ps.hand.remove(action.card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "SummonMonster", events)
        events.append(MonsterSummoned(
            player_id=action.player_id,
            card_id=action.card_id,
            vessel_piece_id=unit.piece.id,
            position=action.vessel_position,
        ))

        # ── Stage 5: on-summon effects ────────────────────────────────────
        if card is not None and isinstance(card, MonsterCard):
            apply_on_summon_effects(
                state, unit, card, events, rng=rng,
                position=action.vessel_position, registry=registry,
            )

        # transformation_alarm / nullification_glyph — SUMMON-trigger Traps.
        from game.mechanics.monsters import check_summon_traps
        check_summon_traps(state, unit, action.vessel_position, events, registry, rng=rng)

        return events

    def _execute_dismiss_monster(
        self,
        state: GameState,
        action: DismissMonster,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — Dismiss a monster from a vessel.

        The monster card is returned to the owner's hand.
        The vessel piece is restored (monster_id cleared, statuses wiped).
        This is a preparation action.
        """
        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        unit = state.board.get_unit(action.unit_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.unit_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot dismiss an opponent's monster.")
        if unit.monster_id is None:
            raise IllegalActionError("No monster on this piece to dismiss.")

        card_id = unit.monster_id

        # Remove monster from vessel
        unit.monster_id = None
        # Clear all monster-applied statuses (shield, spell_radius_bonus, stealth, etc.)
        unit.statuses = [
            s for s in unit.statuses
            if not any(
                s.startswith(prefix) for prefix in (
                    "shield:", "spell_radius_bonus:", "stealth",
                    "ritual_boost:", "frozen:",
                )
            )
        ]

        # Return card to hand (optional dismiss effect: return to deck instead
        # is reserved for future YAML-driven dismiss_effect field)
        ps = state.get_player(action.player_id)
        ps.hand.append(card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "DismissMonster", events)
        events.append(MonsterDismissed(
            player_id=action.player_id,
            card_id=card_id,
            vessel_piece_id=unit.piece.id,
            position=action.unit_position,
        ))
        return events

    def _execute_activate_monster_ability(
        self,
        state: GameState,
        action: ActivateMonsterAbility,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — Fire a monster's activated ability.

        Validates that:
          • The action is in Phase.CHESS.
          • The acting player owns the unit.
          • The unit has a monster.
          • The requested ability_id is an activatable effect on that monster.

        Then resolves the effect via the EffectContext/resolve_effect pipeline
        and emits a MonsterAbilityActivated event.
        """
        from game.cards.card import MonsterCard
        from game.mechanics.effects.registry import (
            ACTIVATED_EFFECT_TYPES,
            EffectContext,
            resolve_effect,
        )

        self._require_phase(state, Phase.CHESS)

        unit = state.board.get_unit(action.unit_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.unit_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot activate an opponent's monster ability.")
        if unit.monster_id is None:
            raise IllegalActionError("No monster on this piece.")

        from game.mechanics.monsters import is_effects_suppressed
        if is_effects_suppressed(unit):
            raise IllegalActionError("This Monster's effects are suppressed.")

        if registry is None:
            raise IllegalActionError("No card registry available to resolve abilities.")

        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            raise IllegalActionError(f"Unknown monster card: {unit.monster_id!r}")

        if not isinstance(card, MonsterCard):
            raise IllegalActionError(f"Card {unit.monster_id!r} is not a Monster card.")

        # Find the matching effect
        matching = [
            eff for eff in card.effects
            if eff.type == action.ability_id and eff.type in ACTIVATED_EFFECT_TYPES
        ]
        if not matching:
            raise IllegalActionError(
                f"Monster {unit.monster_id!r} has no activatable ability {action.ability_id!r}."
            )
        effect_entry = matching[0]

        events: list[Event] = []

        ctx = EffectContext(
            state=state,
            effect=effect_entry,
            events=events,
            unit=unit,
            position=action.unit_position,
            card=card,
            rng=rng,
            registry=registry,
            trigger="activated",
            extra={"target": action.target},
        )
        resolve_effect(ctx)

        events.append(MonsterAbilityActivated(
            player_id=action.player_id,
            card_id=unit.monster_id,
            ability_id=action.ability_id,
            unit_position=action.unit_position,
            target=action.target,
        ))
        return events

    def _execute_reposition_unit(
        self,
        state: GameState,
        action: RepositionUnit,
    ) -> list[Event]:
        """
        Stage 5 — resolve a REPOSITION PendingDecision (blade_dancer after-capture).

        Moves the piece from ``from_position`` to ``to_position`` (must be in the
        pending options list).  Clears the PendingDecision afterwards.
        """
        self._require_phase(state, Phase.CHESS)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.REPOSITION:
            raise IllegalActionError("No pending Reposition decision.")
        if (action.to_position.file, action.to_position.rank) not in pd.options:
            raise IllegalActionError(
                f"Position {action.to_position} not in reposition options."
            )
        unit = state.board.get_unit(action.from_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.from_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot reposition an opponent's piece.")

        state.board.move_unit(action.from_position, action.to_position)

        # Stage 5: burrow (tunnel_mole) piggybacks this same PendingDecision
        # flow and tags a cooldown status to apply once the move resolves.
        post_move_status = pd.context.get("post_move_status")
        if post_move_status:
            unit.statuses = [
                s for s in unit.statuses
                if s.split(":")[0] != post_move_status.split(":")[0]
            ]
            unit.add_status(post_move_status)

        state.pending_decision = None

        from game.core.events import PhaseAdvanced
        events: list[Event] = [PieceMoved(
            piece_id=unit.piece.id,
            owner=unit.owner,
            source=action.from_position,
            target=action.to_position,
        )]

        # Reposition resolves the CHESS sub-turn — now advance to REACTION.
        state.phase = Phase.REACTION
        events.append(PhaseAdvanced(
            player_id=action.player_id,
            from_phase=Phase.CHESS,
            to_phase=Phase.REACTION,
        ))
        return events

    def _execute_reorder_top_deck(
        self,
        state: GameState,
        action: ReorderTopDeck,
    ) -> list[Event]:
        """
        Resolve a REORDER_DECK PendingDecision (arcane_archivist's
        inspect_top_deck). ``action.card_ids`` must be a permutation of
        ``pd.options`` — replaces the top ``len(card_ids)`` cards of the
        owner's deck with that order (index 0 = new top). Stays in
        Phase.PREPARATION (SummonMonster already consumed the preparation
        action) so the player can continue normally afterwards.
        """
        from game.core.events import DeckReordered

        self._require_phase(state, Phase.PREPARATION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.REORDER_DECK:
            raise IllegalActionError("No pending deck-reorder decision.")
        if pd.player_id != action.player_id:
            raise IllegalActionError("This reorder decision belongs to the other player.")
        if sorted(action.card_ids) != sorted(pd.options):
            raise IllegalActionError(
                "card_ids must be a permutation of the inspected cards "
                f"{pd.options!r}."
            )

        ps = state.get_player(action.player_id)
        n = len(action.card_ids)
        ps.deck[:n] = list(action.card_ids)
        state.pending_decision = None

        return [DeckReordered(player_id=action.player_id, card_ids=tuple(action.card_ids))]

    def _execute_activate_spell(
        self,
        state: GameState,
        action: ActivateSpell,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 6 — play a Spell and resolve its effects.

        Canonical effect contract:
            TRIGGER           the player activates the Spell
            CONDITION         card is in hand; ``action.target`` matches the
                               shape ``target_type`` expects (validated below)
            TARGET SELECTION  mechanics.area.expand_area(card.radius, card.shape)
                               around ``action.target`` for spatial Spells, OR
                               a single named piece/Trap
            EFFECT            each of the card's effects, via the SAME
                               EFFECT_REGISTRY monsters/traps use
            DURATION/EVENTS   handled inside each individual handler

        ``action.target`` shape depends on ``card.target_type``:
            "piece"     {"position": (f, r), "destination": (f, r) | None}
                         — "position" is the caster's own piece for most
                         effects, but the TARGET enemy piece for pull_unit
                         (target_owner: opponent — see _resolve_spell_on_piece);
                         "destination" is unused for move_unit, an empty
                         square for reposition_unit, and a SECOND owned
                         piece's position for swap_units
            "position"  (f, r) — area center; expanded via card.radius/shape
            "zone"      (f, r) — anchor square; shape="column"/"row" expands
                         to the whole file/rank regardless of which square
                         on that line was picked
            "trap"      trap_instance_id (str)
            "building"  building_instance_id (str)
            "none"      None — card has no board target (e.g. ritual_insight,
                         false_prophecy, forbidden_knowledge)

        Stage 9 — SPELL_TARGET_REACH *or* Building coverage: for
        "position"/"zone" Spells, the anchor square is legal if EITHER a
        non-Pawn piece the caster currently controls could move into it
        (pseudo-legal geometry; see chess.movement.
        get_non_pawn_movement_squares), OR it is covered by one of the
        caster's own Buildings (possibly extended further by a Shrine —
        mechanics.territory.spell_zone_squares) — only one needs to hold.
        Bare home-rank Territory does NOT qualify on its own. Only the
        anchor is checked — the zone it expands into (column/row/radius)
        is not.
        """
        from game.cards.card import SpellCard
        from game.core.events import SpellActivated

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        card = None
        if registry is not None:
            try:
                card = registry.get(action.card_id)
            except KeyError:
                raise IllegalActionError(f"Unknown card: {action.card_id!r}")
            if not isinstance(card, SpellCard):
                raise IllegalActionError(f"Card {action.card_id!r} is not a Spell card.")

            # Stage 9: a Spell may be aimed at a square EITHER one of the
            # caster's own non-Pawn pieces could move into right now, OR
            # one covered by one of the caster's own Buildings (the anchor
            # square only — the zone it expands into is unrestricted; bare
            # home-rank Territory does NOT qualify on its own).
            if card.target_type in ("position", "zone"):
                if not (isinstance(action.target, (tuple, list)) and len(action.target) == 2):
                    raise IllegalActionError(f"{card.name!r} needs a (file, rank) target square.")
                anchor = Position(*action.target)
                from game.mechanics.territory import is_in_spell_zone
                reachable = get_non_pawn_movement_squares(state.board, action.player_id, registry)
                if anchor not in reachable and not is_in_spell_zone(state, anchor, action.player_id, registry):
                    raise IllegalActionError(
                        f"{card.name!r} can only target a square one of your "
                        "non-Pawn pieces could move into, or one covered by one of your Buildings."
                    )

        ps.hand.remove(action.card_id)
        ps.graveyard.append(action.card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "ActivateSpell", events)
        events.append(SpellActivated(
            player_id=action.player_id,
            card_id=action.card_id,
            target=action.target,
        ))

        if card is None:
            return events

        if card.target_type == "piece":
            self._resolve_spell_on_piece(state, action, card, events, registry)
        elif card.target_type in ("position", "zone"):
            self._resolve_spell_on_area(state, action, card, events, registry)
        elif card.target_type == "trap":
            self._resolve_spell_on_trap(state, action, card, events)
        elif card.target_type == "building":
            self._resolve_spell_on_building(state, action, card, events, registry)
        elif card.target_type == "none":
            self._resolve_spell_untargeted(state, card, events, rng, registry, action.player_id)
        return events

    def _resolve_spell_on_building(
        self,
        state: GameState,
        action: ActivateSpell,
        card,   # SpellCard
        events: list[Event],
        registry: "object | None",
    ) -> None:
        """
        rapid_construction / siege_order / emergency_fortifications
        (target_type "building"). ``action.target`` is a
        building_instance_id (str) — mirrors _resolve_spell_on_trap.
        """
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        building_id = action.target
        if not isinstance(building_id, str):
            raise IllegalActionError(f"{card.name!r} needs a building_instance_id target.")
        building = next((b for b in state.buildings if b.id == building_id), None)
        if building is None:
            raise IllegalActionError(f"No Building with id {building_id!r} on the board.")

        for effect in card.effects:
            target_owner = effect.params.get("target_owner", "self")
            if target_owner == "opponent" and building.owner == action.player_id:
                raise IllegalActionError(f"{card.name!r} can only target an enemy Building.")
            if target_owner != "opponent" and building.owner != action.player_id:
                raise IllegalActionError(f"{card.name!r} can only target your own Building.")
            ctx = EffectContext(
                state=state, unit=None, position=building.position, card=card, effect=effect,
                events=events, registry=registry, trigger="instant_spell",
                extra={"building_instance_id": building_id, "caster_owner": action.player_id},
            )
            try:
                resolve_effect(ctx)
            except NotImplementedError:
                _log.debug("SPELL    NotImplemented for %r — skipped", effect.type)

    def _resolve_spell_on_piece(
        self,
        state: GameState,
        action: ActivateSpell,
        card,   # SpellCard
        events: list[Event],
        registry: "object | None",
    ) -> None:
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        if not isinstance(action.target, dict) or "position" not in action.target:
            raise IllegalActionError(
                f"{card.name!r} needs a "
                "{'position': (file, rank), 'destination': (file, rank) | None} target."
            )
        pos = Position(*action.target["position"])
        unit = state.board.get_unit(pos)
        if unit is None:
            raise IllegalActionError(f"No piece at {pos}.")

        # Ownership requirement is effect-driven, not a blanket "must be
        # own" — pull_unit (magnetic_reversal) targets an OPPONENT piece
        # (``target_owner: opponent``); every other "piece"-targeted Spell
        # today defaults to requiring the caster's own piece.
        primary = card.effects[0] if card.effects else None
        target_owner = primary.params.get("target_owner") if primary is not None else None
        if target_owner == "opponent":
            if unit.owner == action.player_id:
                raise IllegalActionError(f"{card.name!r} can only target an opponent's piece.")
            if unit.piece.piece_type == PieceType.KING:
                raise IllegalActionError(f"{card.name!r} cannot target the King.")
        elif unit.owner != action.player_id:
            raise IllegalActionError("Cannot target an opponent's piece with this Spell.")

        destination = action.target.get("destination")
        caster_owner = action.player_id
        for effect in card.effects:
            ctx = EffectContext(
                state=state, unit=unit, position=pos, card=card, effect=effect,
                events=events, registry=registry, trigger="instant_spell",
                extra={"destination": destination, "caster_owner": caster_owner},
            )
            try:
                resolve_effect(ctx)
            except NotImplementedError:
                _log.debug("SPELL    NotImplemented for %r — skipped", effect.type)

    def _resolve_spell_on_area(
        self,
        state: GameState,
        action: ActivateSpell,
        card,   # SpellCard
        events: list[Event],
        registry: "object | None",
    ) -> None:
        from game.mechanics.area import expand_area
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        if not isinstance(action.target, (tuple, list)) or len(action.target) != 2:
            raise IllegalActionError(f"{card.name!r} needs a (file, rank) target square.")
        center = Position(*action.target)
        area = expand_area(center, card.radius, card.shape)

        for square in area:
            for effect in card.effects:
                ctx = EffectContext(
                    state=state, unit=None, position=square, card=card, effect=effect,
                    events=events, registry=registry, trigger="instant_spell",
                    extra={"caster_owner": action.player_id},
                )
                try:
                    resolve_effect(ctx)
                except NotImplementedError:
                    _log.debug("SPELL    NotImplemented for %r — skipped", effect.type)

    def _resolve_spell_on_trap(
        self,
        state: GameState,
        action: ActivateSpell,
        card,   # SpellCard
        events: list[Event],
    ) -> None:
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        trap_id = action.target
        if not isinstance(trap_id, str):
            raise IllegalActionError(f"{card.name!r} needs a trap_instance_id target.")
        trap = next((t for t in state.traps if t.id == trap_id), None)
        if trap is None:
            raise IllegalActionError(f"No Trap with id {trap_id!r} on the board.")

        for effect in card.effects:
            target_owner = effect.params.get("target_owner", "opponent")
            if target_owner == "opponent" and trap.owner == action.player_id:
                raise IllegalActionError(f"{card.name!r} can only target an enemy Trap.")
            ctx = EffectContext(
                state=state, unit=None, position=trap.position, card=card, effect=effect,
                events=events, trigger="instant_spell",
                extra={"trap_instance_id": trap_id},
            )
            try:
                resolve_effect(ctx)
            except NotImplementedError:
                _log.debug("SPELL    NotImplemented for %r — skipped", effect.type)

    def _resolve_spell_untargeted(
        self,
        state: GameState,
        card,   # SpellCard
        events: list[Event],
        rng: DeterministicRNG,
        registry: "object | None",
        caster_owner: str,
    ) -> None:
        """
        target_type == "none" Spells (ritual_insight, false_prophecy,
        hasten_the_ritual, forbidden_knowledge). ``caster_owner`` is
        forwarded via ctx.extra since there's no ``ctx.unit`` to read an
        owner from. Effects that aren't implemented yet are silently
        skipped, same as any other not-yet-implemented handler.
        """
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        for effect in card.effects:
            ctx = EffectContext(
                state=state, unit=None, position=None, card=card, effect=effect,
                events=events, rng=rng, registry=registry, trigger="instant_spell",
                extra={"caster_owner": caster_owner},
            )
            try:
                resolve_effect(ctx)
            except NotImplementedError:
                _log.debug("SPELL    NotImplemented for %r — skipped", effect.type)

    def _execute_place_trap(
        self,
        state: GameState,
        action: PlaceTrap,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 6 — place a Trap card from hand onto the board.

        The TrapInstance's radius/shape/trigger/charges are read from the
        card definition (not hardcoded) so trigger detection
        (mechanics.monsters.check_enter_radius_traps /
        check_capture_traps) sees the Trap's real area of effect.

        Stage 9 — TRAP_MONSTER_ANCHOR *or* Building coverage: the target
        square is legal if EITHER it hosts one of the caster's own
        summoned Monsters, OR it is an empty square covered by one of the
        caster's own Buildings (possibly extended further by a Watchtower)
        — only one needs to hold. Bare home-rank Territory does NOT
        qualify on its own. It must not already have another Trap on it
        either way.
        """
        from game.cards.card import TrapCard
        from game.core.events import TrapPlaced
        from game.mechanics.territory import is_in_trap_zone

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        target_unit = state.board.get_unit(action.position)
        anchored_to_own_monster = (
            target_unit is not None
            and target_unit.monster_id is not None
            and target_unit.owner == action.player_id
        )
        in_own_trap_zone = target_unit is None and is_in_trap_zone(
            state, action.position, action.player_id, registry
        )
        if not (anchored_to_own_monster or in_own_trap_zone):
            raise IllegalActionError(
                f"A Trap can only be placed on a square hosting one of your "
                f"own summoned Monsters, or on an empty square covered by "
                f"one of your Buildings ({action.position})."
            )
        if any(t.position == action.position for t in state.traps):
            raise IllegalActionError(
                f"A Trap is already placed at {action.position}."
            )

        card = None
        radius, shape, trigger_name, charges = 1, "square", "enter_radius", None
        if registry is not None:
            try:
                card = registry.get(action.card_id)
            except KeyError:
                raise IllegalActionError(f"Unknown card: {action.card_id!r}")
            if not isinstance(card, TrapCard):
                raise IllegalActionError(f"Card {action.card_id!r} is not a Trap card.")
            radius, shape = card.radius, card.shape
            trigger_name, charges = card.trigger.value, card.charges

        ps.hand.remove(action.card_id)

        trap_id = f"trap-{action.player_id}-{len(state.traps)+1:03d}"
        trap = TrapInstance(
            id=trap_id,
            owner=action.player_id,
            card_id=action.card_id,
            position=action.position,
            radius=radius,
            shape=shape,
            trigger_condition=trigger_name,
            charges=charges,
        )
        state.traps.append(trap)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "PlaceTrap", events)
        events.append(TrapPlaced(
            player_id=action.player_id,
            card_id=action.card_id,
            trap_instance_id=trap_id,
            position=action.position,
            radius=trap.radius,
        ))
        return events

    def _execute_activate_trap(
        self,
        state: GameState,
        action: ActivateTrap,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 6 — manually fire a ``trigger: manual`` Trap (time_anchor).

        Simplification: the README's flavor text has these "activated
        during the Reaction phase" (the owner reacting to the opponent's
        move) but the engine currently only lets active_player act outside
        the two special interrupt phases (RECOMPOSE_SELECTION /
        PROMOTION_SELECTION). Mechanically equivalent and much simpler:
        the owner activates it during their OWN next PREPARATION phase —
        which, since it follows the opponent's move and EndTurn, is the
        very next moment the owner can act anyway. state.move_history is
        unaffected by whose turn it is, so undo semantics come out
        identical either way; only the UI framing differs.
        """
        from game.cards.card import TrapCard, TrapTrigger
        from game.core.events import TrapTriggered
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        trap = next((t for t in state.traps if t.id == action.trap_instance_id), None)
        if trap is None:
            raise IllegalActionError(f"No Trap with id {action.trap_instance_id!r} on the board.")
        if trap.owner != action.player_id:
            raise IllegalActionError("Cannot activate an opponent's Trap.")

        card = None
        if registry is not None:
            try:
                card = registry.get(trap.card_id)
            except KeyError:
                card = None
        if not isinstance(card, TrapCard) or card.trigger != TrapTrigger.MANUAL:
            raise IllegalActionError(f"Trap {trap.card_id!r} cannot be manually activated.")

        from game.mechanics.area import expand_area

        events: list[Event] = []
        target_player = state.opponent_of(action.player_id)
        # Expand over the Trap's own area (radius/shape) so a zone-tagging
        # effect like vessel_lock's prohibit_summoning covers its whole
        # radius, not just the anchor square — mirrors
        # _resolve_spell_on_area's convention. Effects that only care
        # about ``trap.position`` itself (time_anchor's cancel_move,
        # radius 0) are unaffected — expand_area(radius=0) is just [pos].
        area = expand_area(trap.position, card.radius, card.shape)
        for effect in card.effects:
            for square in area:
                ctx = EffectContext(
                    state=state, unit=None, position=square, card=card, effect=effect,
                    events=events, registry=registry, trigger="manual",
                    extra={
                        "target_player": target_player, "trap_owner": action.player_id,
                        "caster_owner": action.player_id,
                    },
                )
                resolve_effect(ctx)   # let IllegalActionError propagate — nothing consumed yet

        self._mark_prep_used(state, action.player_id, "ActivateTrap", events)
        if trap.charges is not None:
            trap.charges -= 1
            if trap.charges <= 0:
                state.traps = [t for t in state.traps if t.id != trap.id]

        events.append(TrapTriggered(trap_instance_id=trap.id, triggering_piece_id=None))
        return events

    def _execute_start_construction(
        self,
        state: GameState,
        action: StartConstruction,
    ) -> list[Event]:
        from game.core.events import ConstructionStarted
        from game.core.phases import ConstructionStatus
        from game.core.state import BuildingInstance
        from game.mechanics.buildings import is_committed_builder

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        unit = state.board.get_unit(action.pawn_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.pawn_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot use opponent's Pawn to build.")
        if unit.piece.piece_type != PieceType.PAWN:
            raise IllegalActionError("Only Pawns can build.")              # BUILDER_IS_PAWN
        if not unit.builder_available:
            raise IllegalActionError("This Pawn has already completed a building.")  # BUILDER_ONCE
        if is_committed_builder(state, unit.piece.id):
            raise IllegalActionError("This Pawn is already committed to another Building.")

        sq = state.board.get_square(action.pawn_position)
        if sq.building_id is not None:
            raise IllegalActionError("This square already has a Building on it.")

        ps = state.get_player(action.player_id)
        entry = next(
            (e for e in ps.building_pool if e.building_card_id == action.building_card_id),
            None,
        )
        if entry is None or entry.copies_available <= 0:
            raise IllegalActionError(
                f"Building {action.building_card_id!r} not available in pool."
            )
        entry.copies_available -= 1

        # Stage 8: construction_turns comes from the Building's data
        # definition when a registry is available; falls back to the
        # original Stage 7 placeholder (2) for registry-less unit tests.
        construction_turns = 2
        if self._registry is not None:
            from game.cards.card import BuildingCard
            try:
                card = self._registry.get(action.building_card_id)
            except KeyError:
                card = None
            if isinstance(card, BuildingCard):
                construction_turns = card.construction_turns

        # Stage 10: architect_king's construction_speed_bonus policy.
        from game.mechanics.kings import apply_construction_speed_bonus
        construction_turns = apply_construction_speed_bonus(
            state, action.player_id, unit.piece.piece_type.value,
            construction_turns, self._registry,
        )
        # Stage 8: master_mason's construction_speed_bonus (adjacent Monster).
        from game.mechanics.buildings import monster_construction_speed_bonus
        construction_turns = monster_construction_speed_bonus(
            state, action.player_id, action.pawn_position,
            construction_turns, self._registry,
        )

        building_id = f"bld-{action.player_id}-{len(state.buildings)+1:03d}"
        building = BuildingInstance(
            id=building_id,
            owner=action.player_id,
            building_card_id=action.building_card_id,
            position=action.pawn_position,
            status=ConstructionStatus.UNDER_CONSTRUCTION,
            builder_piece_id=unit.piece.id,
            remaining_turns=construction_turns,
        )
        state.buildings.append(building)
        sq.building_id = building_id  # visible on the board immediately (under construction)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "StartConstruction", events)
        events.append(ConstructionStarted(
            player_id=action.player_id,
            building_card_id=action.building_card_id,
            building_instance_id=building_id,
            position=action.pawn_position,
            builder_piece_id=unit.piece.id,
        ))
        return events

    def _execute_activate_ritual(
        self,
        state: GameState,
        action: ActivateRitual,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 11 — attempt a Ritual summon (README §14).

        ``action.sacrifice_positions`` is read as [...pure sacrifices...,
        Vessel] — see mechanics/rituals.py's module docstring. Validation
        (mechanics.rituals.validate_ritual) covers the condition-specific
        checks (formation / material / state); this method only handles
        the generic PREPARATION-action bookkeeping and the actual board
        mutation (mechanics.rituals.execute_ritual), then runs the
        summoned Monster's on-summon effects exactly like SummonMonster.
        """
        from game.cards.card import RitualCard
        from game.mechanics.monsters import apply_on_summon_effects
        from game.mechanics.rituals import execute_ritual, get_ritual_state, validate_ritual

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        if registry is None:
            raise IllegalActionError("No card registry available to resolve Rituals.")

        try:
            ritual = registry.get(action.ritual_id)
        except KeyError:
            raise IllegalActionError(f"Unknown Ritual: {action.ritual_id!r}")
        if not isinstance(ritual, RitualCard):
            raise IllegalActionError(f"Card {action.ritual_id!r} is not a Ritual card.")

        rstate = get_ritual_state(state, action.player_id, action.ritual_id)
        if rstate is None:
            raise IllegalActionError(f"Ritual {action.ritual_id!r} is not in your pool.")

        # profane_interruption: an enemy Trap covering any of the proposed
        # sacrifice squares blocks this attempt outright — checked BEFORE
        # validate_ritual so nothing is sacrificed either way (README text:
        # "The material is not sacrificed"). Consumes the Trap like any
        # other single-charge Trap.
        from game.mechanics.rituals import find_interrupting_trap
        interrupting_trap = find_interrupting_trap(
            state, action.player_id, list(action.sacrifice_positions), registry,
        )
        if interrupting_trap is not None:
            from game.core.events import TrapTriggered
            events: list[Event] = [TrapTriggered(
                trap_instance_id=interrupting_trap.id, triggering_piece_id=None,
            )]
            if interrupting_trap.charges is not None:
                interrupting_trap.charges -= 1
                if interrupting_trap.charges <= 0:
                    state.traps = [t for t in state.traps if t.id != interrupting_trap.id]
            state.event_log.extend(events)
            raise IllegalActionError(
                "This Ritual attempt is interrupted by an enemy Trap covering "
                "the sacrifice material — try again next turn."
            )

        try:
            validate_ritual(state, action.player_id, ritual, rstate, list(action.sacrifice_positions), registry)
        except ValueError as exc:
            raise IllegalActionError(str(exc))

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "ActivateRitual", events)

        _vessel_pos, vessel_unit = execute_ritual(
            state, action.player_id, ritual, rstate, list(action.sacrifice_positions), events,
            registry=registry,
        )

        summoned_card = registry.get(ritual.summon_monster_id)
        from game.cards.card import MonsterCard
        if isinstance(summoned_card, MonsterCard):
            apply_on_summon_effects(
                state, vessel_unit, summoned_card, events, rng=rng,
                position=_vessel_pos, registry=registry,
            )

        # transformation_alarm / nullification_glyph — SUMMON-trigger Traps
        # (a Ritual summon is a summon too).
        from game.mechanics.monsters import check_summon_traps
        check_summon_traps(state, vessel_unit, _vessel_pos, events, registry, rng=rng)

        return events

    def _execute_coronate_king(
        self,
        state: GameState,
        action: CoronateKing,
    ) -> list[Event]:
        from game.core.events import KingCoronated

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if ps.is_in_check():
            raise IllegalActionError("Cannot Coronate while in check.")

        kcs = next(
            (k for k in ps.king_pool if k.king_card_id == action.king_card_id),
            None,
        )
        if kcs is None:
            raise IllegalActionError(f"King card {action.king_card_id!r} not in pool.")
        if kcs.status != KingCardStatus.HIDDEN:
            raise IllegalActionError(f"King card {action.king_card_id!r} is already active or retired.")

        kcs.status = KingCardStatus.ACTIVE
        ps.active_king = action.king_card_id

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "CoronateKing", events)
        events.append(KingCoronated(player_id=action.player_id, king_card_id=action.king_card_id))
        return events

    def _execute_change_king(
        self,
        state: GameState,
        action: ChangeKing,
    ) -> list[Event]:
        from game.core.events import BuildingDestroyed, KingSuccession
        from game.core.phases import ConstructionStatus

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if ps.is_in_check():
            raise IllegalActionError("Cannot perform Succession while in check.")
        if ps.active_king is None:
            raise IllegalActionError("No active King to succeed. Use CoronateKing first.")

        old_kcs = next(
            (k for k in ps.king_pool if k.king_card_id == ps.active_king), None
        )
        new_kcs = next(
            (k for k in ps.king_pool if k.king_card_id == action.king_card_id), None
        )
        if new_kcs is None or new_kcs.status != KingCardStatus.HIDDEN:
            raise IllegalActionError(f"King card {action.king_card_id!r} unavailable for succession.")

        # ── Stage 10: escalating Succession cost (README §19.1) ──────────
        # 1st Succession: piece sacrifice OR Building destruction (exactly
        # one). 2nd Succession: BOTH. The King Pool only ever holds 3 cards,
        # so 2 is also the highest possible succession number.
        succession_number = len(ps.retired_kings) + 1
        if succession_number == 1:
            provided = (
                (action.sacrifice_position is not None)
                + (action.destroy_building_id is not None)
            )
            if provided != 1:
                raise IllegalActionError(
                    "1st Succession costs exactly one of: a piece sacrifice OR "
                    "a Building destruction."
                )
        elif succession_number == 2:
            if action.sacrifice_position is None or action.destroy_building_id is None:
                raise IllegalActionError(
                    "2nd Succession costs BOTH a piece sacrifice AND a Building destruction."
                )
        else:
            raise IllegalActionError("No further Succession is possible — King Pool exhausted.")

        sacrificed_piece_id: str | None = None
        if action.sacrifice_position is not None:
            victim = state.board.get_unit(action.sacrifice_position)
            if victim is None or victim.owner != action.player_id:
                raise IllegalActionError("Succession sacrifice must be your own piece.")
            if victim.piece.piece_type == PieceType.KING:
                raise IllegalActionError("The King cannot be sacrificed for Succession.")
            sacrificed_piece_id = victim.piece.id
            state.board.remove_unit(action.sacrifice_position)

        destroyed_building_id: str | None = None
        events: list[Event] = []
        if action.destroy_building_id is not None:
            building = next(
                (b for b in state.buildings if b.id == action.destroy_building_id), None
            )
            if (
                building is None
                or building.owner != action.player_id
                or building.status != ConstructionStatus.COMPLETE
            ):
                raise IllegalActionError(
                    "Succession Building cost must be one of your own COMPLETE Buildings."
                )
            building.status = ConstructionStatus.DESTROYED
            sq = state.board.get_square(building.position)
            if sq.building_id == building.id:
                sq.building_id = None
            destroyed_building_id = building.id
            events.append(BuildingDestroyed(
                building_instance_id=building.id,
                position=building.position,
                destroyed_by=action.player_id,
            ))

        # Retire old king
        if old_kcs:
            old_kcs.status = KingCardStatus.RETIRED
            ps.retired_kings.append(ps.active_king)

        new_kcs.status = KingCardStatus.ACTIVE
        old_id = ps.active_king
        ps.active_king = action.king_card_id

        self._mark_prep_used(state, action.player_id, "ChangeKing", events)
        events.append(KingSuccession(
            player_id=action.player_id,
            retired_king_card_id=old_id,
            new_king_card_id=action.king_card_id,
            sacrificed_piece_id=sacrificed_piece_id,
            destroyed_building_id=destroyed_building_id,
        ))
        return events

    def _execute_declare_recompose(
        self,
        state: GameState,
        action: DeclareRecompose,
        rng: DeterministicRNG,
    ) -> list[Event]:
        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if not ps.hand:
            raise IllegalActionError("Cannot Recompose with empty hand.")

        n = rng.recompose_count()
        n = min(n, len(ps.hand))  # can't return more than you have

        state.pending_decision = PendingDecision(
            player_id=action.player_id,
            decision_type=DecisionType.RECOMPOSE_CARDS,
            options=list(ps.hand),
            min_choices=n,
            max_choices=n,
        )
        state.phase = Phase.RECOMPOSE_SELECTION

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "DeclareRecompose", events)
        events.append(RecomposeInitiated(
            player_id=action.player_id,
            required_count=n,
        ))
        return events

    def _execute_select_recompose(
        self,
        state: GameState,
        action: SelectRecomposeCards,
        rng: DeterministicRNG,
    ) -> list[Event]:
        from game.core.events import CardsReturnedToDeck, RecomposeResolved

        self._require_phase(state, Phase.RECOMPOSE_SELECTION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.RECOMPOSE_CARDS:
            raise IllegalActionError("No pending Recompose decision.")

        n = pd.min_choices
        if len(action.card_ids) != n:
            raise IllegalActionError(
                f"Must return exactly {n} cards; got {len(action.card_ids)}."
            )

        ps = state.get_player(action.player_id)
        for cid in action.card_ids:
            if cid not in ps.hand:
                raise IllegalActionError(f"Card {cid!r} not in hand.")

        # Return cards to deck, shuffle, draw replacements
        for cid in action.card_ids:
            ps.hand.remove(cid)
            ps.deck.append(cid)
        rng.shuffle(ps.deck)

        drawn: list[str] = []
        for _ in range(n):
            if ps.deck:
                card = ps.deck.pop(0)
                ps.hand.append(card)
                drawn.append(card)

        state.pending_decision = None
        state.phase = Phase.CHESS  # Advance to chess phase after recompose

        events: list[Event] = [
            CardsReturnedToDeck(
                player_id=action.player_id,
                card_ids=tuple(action.card_ids),
            ),
            RecomposeResolved(
                player_id=action.player_id,
                returned_card_ids=tuple(action.card_ids),
                drawn_card_ids=tuple(drawn),
            ),
        ]
        return events

    # ── Mercenary executors (Stage 7) ────────────────────────────────────

    # Cost table: piece_type → number of Monster cards required.
    _MERCENARY_COST: dict[str, int] = {
        "pawn": 4, "knight": 6, "bishop": 6, "rook": 6, "queen": 8,
    }

    def _execute_declare_mercenary(
        self,
        state: GameState,
        action: DeclareMercenary,
    ) -> list[Event]:
        """
        Validate the piece type and that enough Monster cards are in hand,
        then set up MERCENARY_SELECTION so the player picks which ones to sacrifice.
        """
        from game.cards.card import MonsterCard

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        pt = action.piece_type.lower()
        if pt not in self._MERCENARY_COST:
            raise IllegalActionError(
                f"Invalid piece type {action.piece_type!r}. "
                f"Must be one of: {list(self._MERCENARY_COST)}"
            )

        ps = state.get_player(action.player_id)
        cost = self._MERCENARY_COST[pt]

        registry = self._registry
        monster_ids_in_hand = [
            cid for cid in ps.hand
            if registry and cid in registry and isinstance(registry.get(cid), MonsterCard)
        ]
        if len(monster_ids_in_hand) < cost:
            raise IllegalActionError(
                f"Mercenary {pt} costs {cost} Monster card(s); "
                f"you only have {len(monster_ids_in_hand)} Monster card(s) in hand."
            )

        # Ensure at least one empty square exists in the player's first two ranks.
        own_ranks = (0, 1) if action.player_id == "white" else (6, 7)
        has_empty = any(
            state.board.get_unit(Position(f, r)) is None
            for f in range(8)
            for r in own_ranks
        )
        if not has_empty:
            raise IllegalActionError(
                "No empty squares in your first two ranks to place a Mercenary piece."
            )

        state.pending_decision = PendingDecision(
            player_id=action.player_id,
            decision_type=DecisionType.MERCENARY_CARDS,
            options=monster_ids_in_hand,
            min_choices=cost,
            max_choices=cost,
            context={"piece_type": pt},
        )
        state.phase = Phase.MERCENARY_SELECTION

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "DeclareMercenary", events)
        return events

    def _execute_select_mercenary_cards(
        self,
        state: GameState,
        action: SelectMercenaryCards,
    ) -> list[Event]:
        """
        Validate the sacrificed cards, remove them from game (not graveyard),
        then advance to MERCENARY_PLACEMENT.
        """
        from game.cards.card import MonsterCard

        self._require_phase(state, Phase.MERCENARY_SELECTION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.MERCENARY_CARDS:
            raise IllegalActionError("No pending Mercenary card-selection decision.")

        n = pd.min_choices
        if len(action.card_ids) != n:
            raise IllegalActionError(
                f"Must sacrifice exactly {n} Monster card(s); got {len(action.card_ids)}."
            )

        ps = state.get_player(action.player_id)
        registry = self._registry
        for cid in action.card_ids:
            if cid not in ps.hand:
                raise IllegalActionError(f"Card {cid!r} is not in hand.")
            if not (registry and cid in registry and isinstance(registry.get(cid), MonsterCard)):
                raise IllegalActionError(f"Card {cid!r} is not a Monster card.")

        # Remove from game — do NOT add to graveyard.
        for cid in action.card_ids:
            ps.hand.remove(cid)

        piece_type = pd.context.get("piece_type", "pawn")

        # Set up MERCENARY_PLACEMENT pending decision.
        state.pending_decision = PendingDecision(
            player_id=action.player_id,
            decision_type=DecisionType.MERCENARY_PLACE,
            options=[],        # placement squares enumerated by get_legal_actions
            min_choices=1,
            max_choices=1,
            context={"piece_type": piece_type},
        )
        state.phase = Phase.MERCENARY_PLACEMENT

        return [
            MercenaryContractFired(
                player_id=action.player_id,
                sacrificed_card_ids=tuple(action.card_ids),
                piece_type=piece_type,
            )
        ]

    def _execute_place_mercenary_piece(
        self,
        state: GameState,
        action: PlaceMercenaryPiece,
    ) -> list[Event]:
        """
        Place the new piece on the chosen empty square in the player's first two ranks.
        """
        from game.chess.pieces import ChessPiece

        self._require_phase(state, Phase.MERCENARY_PLACEMENT)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.MERCENARY_PLACE:
            raise IllegalActionError("No pending Mercenary placement decision.")

        player_id = action.player_id
        pos = action.position
        own_ranks = (0, 1) if player_id == "white" else (6, 7)
        if pos.rank not in own_ranks:
            raise IllegalActionError(
                f"Mercenary piece must be placed in {player_id}'s first two ranks."
            )
        if state.board.get_unit(pos) is not None:
            raise IllegalActionError(f"Square {pos.to_algebraic()!r} is occupied.")

        piece_type_str = pd.context.get("piece_type", "pawn")
        pt_enum = PieceType(piece_type_str)

        # Generate a unique piece ID (e.g. "white-merc-knight-3")
        existing_merc = [
            sq.unit.piece.id
            for sq in state.board.squares.values()
            if sq.unit is not None and sq.unit.owner == player_id
            and sq.unit.piece.id.startswith(f"{player_id}-merc-")
        ]
        piece_id = f"{player_id}-merc-{piece_type_str}-{len(existing_merc) + 1}"

        piece = ChessPiece(id=piece_id, owner=player_id, piece_type=pt_enum)
        unit = UnitInstance(piece=piece)
        state.board.place_unit(pos, unit)

        state.pending_decision = None
        state.phase = Phase.CHESS   # advance to chess phase

        return [
            MercenaryPiecePlaced(
                player_id=player_id,
                piece_type=piece_type_str,
                position=pos,
                piece_id=piece_id,
            )
        ]

    def _execute_promote_pawn(
        self,
        state: GameState,
        action: PromotePawn,
    ) -> list[Event]:
        self._require_phase(state, Phase.PROMOTION_SELECTION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.PROMOTION:
            raise IllegalActionError("No pending promotion decision.")

        unit = state.board.get_unit(action.position)
        if unit is None:
            raise IllegalActionError(f"No unit at {action.position} to promote.")

        old_id, new_id = apply_promotion(state.board, action.position, action.piece_type)

        events: list[Event] = [
            PiecePromoted(
                piece_id=old_id,
                owner=unit.owner,
                position=action.position,
                from_type="pawn",
                to_type=action.piece_type,
                new_piece_id=new_id,
            )
        ]
        state.pending_decision = None
        state.phase = Phase.REACTION
        return events

    def _execute_end_preparation(
        self,
        state: GameState,
        action: EndPreparation,
    ) -> list[Event]:
        self._require_phase(state, Phase.PREPARATION)
        events: list[Event] = []
        old = state.phase
        state.phase = Phase.CHESS
        events.append(PhaseAdvanced(
            player_id=action.player_id,
            from_phase=old,
            to_phase=Phase.CHESS,
        ))
        return events

    def _execute_discard_card(
        self,
        state: GameState,
        action: DiscardCard,
    ) -> list[Event]:
        """Discard one card from hand to graveyard during Phase.DISCARD."""
        self._require_phase(state, Phase.DISCARD)
        ps = state.get_player(action.player_id)
        if action.card_id not in ps.hand:
            raise IllegalActionError(
                f"Card {action.card_id!r} is not in {action.player_id}'s hand."
            )
        ps.hand.remove(action.card_id)
        ps.graveyard.append(action.card_id)
        events: list[Event] = [
            CardDiscarded(player_id=action.player_id, card_id=action.card_id)
        ]
        # If hand is now within limit, leave DISCARD phase → END
        if len(ps.hand) <= HAND_SIZE_LIMIT:
            old = state.phase
            state.phase = Phase.END
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.END,
            ))
        return events

    def _execute_end_turn(
        self,
        state: GameState,
        action: EndTurn,
    ) -> list[Event]:
        # Allow from END, REACTION, CHESS (no moves), or DISCARD (after discarding to ≤ limit)
        self._require_phase(state, Phase.END, Phase.REACTION, Phase.CHESS, Phase.DISCARD)
        ps = state.get_player(action.player_id)
        events: list[Event] = []

        # Hand-size enforcement: if hand > limit, redirect to DISCARD phase
        if len(ps.hand) > HAND_SIZE_LIMIT:
            old = state.phase
            state.phase = Phase.DISCARD
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.DISCARD,
            ))
            return events

        events.append(TurnEnded(
            player_id=action.player_id,
            turn_number=state.turn_number,
        ))

        # ── Stage 5/6: tick down frozen / scorched / blocked / cursed / walled /
        # cost_zone squares ──────────────────────────────────────────────────
        # Effect format: "<prefix>:<turns>:<owner_player_id>[:<extra>...]"
        # "blocked" is veil_of_stillness's zone; "cursed" is cursed_ground's;
        # "walled" is wall_of_mist's; "cost_zone" is fractured_path's (carries
        # an extra max_dist field after owner, in addition to the usual
        # trailing source_card_id — both are preserved verbatim below, not
        # just a single "card_id" slot).
        # Decrement only when the player who DIDN'T set the effect ends their
        # turn (i.e., the opponent of the setter calls EndTurn).  This ensures
        # the effect blocks for exactly <turns> full opponent turns.
        ending_player = action.player_id
        for sq in state.board.squares.values():
            remaining = []
            for eff in sq.temporary_effects:
                prefix = eff.split(":")[0]
                if prefix in ("frozen", "scorched", "blocked", "cursed", "walled", "cost_zone", "no_summon", "temp_territory"):
                    # Format: "<prefix>:<turns>:<owner>:<...trailing fields>" —
                    # everything after <owner> (source_card_id, and for
                    # cost_zone also max_dist) must survive the
                    # decrement/rewrite below, in whatever order it was set.
                    parts = eff.split(":")
                    n = int(parts[1])
                    owner = parts[2] if len(parts) > 2 else None
                    rest = parts[3:]
                    # Decrement only if the player ending their turn is NOT the owner
                    if owner is None or owner != ending_player:
                        n -= 1
                    if n > 0:
                        tail = f":{owner}" if owner or rest else ""
                        if rest:
                            tail += ":" + ":".join(rest)
                        remaining.append(f"{prefix}:{n}{tail}")
                    # else: expired, don't add back
                else:
                    remaining.append(eff)
            sq.temporary_effects = remaining

        # ── Stage 5/6: tick down the OWNER's own per-unit timers ─────────
        # burrow_cooldown / immobilized / exposed all count the unit
        # OWNER's own turns (not the opponent's), unlike frozen/scorched/
        # blocked squares above — "cannot move on ITS next turn" means the
        # victim's own turn, not the trap owner's.
        for pos, u in state.board.all_units_for(ending_player):
            remaining_statuses = []
            for status in u.statuses:
                prefix = status.split(":")[0]
                if prefix in ("burrow_cooldown", "immobilized", "exposed", "effects_suppressed"):
                    n = int(status.split(":")[1]) - 1
                    if n > 0:
                        remaining_statuses.append(f"{prefix}:{n}")
                    # else: expired, don't add back
                elif prefix == "vessel_class_override":
                    # unstable_transmutation — "vessel_class_override:<N>:<classes>"
                    _, n_s, classes = status.split(":", 2)
                    n = int(n_s) - 1
                    if n > 0:
                        remaining_statuses.append(f"vessel_class_override:{n}:{classes}")
                elif prefix == "challenged_by":
                    # duelist's challenge_unit — "challenged_by:<piece_id>:<N>"
                    # decays on the CHALLENGED unit's own EndTurn, same
                    # convention as immobilized/exposed above.
                    _, challenger_id, n_s = status.split(":")
                    n = int(n_s) - 1
                    if n > 0:
                        remaining_statuses.append(f"challenged_by:{challenger_id}:{n}")
                else:
                    remaining_statuses.append(status)
            u.statuses = remaining_statuses

        # ── Stage 5: restore_effect_charge (battlefield_medic) ───────────
        # At the end of the owner's own turn, restore one spent shield
        # charge to a single adjacent allied monster.
        if self._registry is not None:
            self._resolve_restore_effect_charge(state, ending_player, events, self._registry)

        # ── Stage 8: Building construction ticks down on the owner's own
        # end of turn (same "counts the owner's own turns" convention as
        # burrow_cooldown/immobilized/exposed above) — completing when it
        # reaches 0 (README §12.2).
        from game.mechanics.buildings import tick_construction, tick_disabled_buildings
        tick_construction(state, ending_player, events)
        # Stage 8: saboteur's disable_building decays on the disabled
        # Building's OWNER's own EndTurn ("until the start of the next
        # owner turn" — mirrors burrow_cooldown/immobilized).
        tick_disabled_buildings(state, ending_player)

        # ── Stage 11: ritual_progress_boost (ritual_acolyte) — advances
        # the ending player's Ritual revelation progress (README §15.1).
        from game.mechanics.rituals import advance_ritual_progress, advance_ritual_reveal_tradeoff
        advance_ritual_progress(state, ending_player, events, self._registry)
        # Stage 11: ritual_reveal_tradeoff's "once_per_turn" variant
        # (oracle_of_the_last_star) — the on_summon path only fires once.
        advance_ritual_reveal_tradeoff(state, ending_player, events, self._registry, rng=None)

        # false_prophecy's ritual_bluff — the flag lives on the CASTER's
        # own RitualState (it names one of THEIR SEALED Rituals), but
        # decays on the DECEIVED opponent's own EndTurn (same convention
        # as square effects — blocks their true info for exactly N of
        # their own turns), so it's read off the ending player's OPPONENT.
        for rs in state.get_player(state.opponent_of(ending_player)).ritual_pool:
            if rs.bluff_turns > 0:
                rs.bluff_turns -= 1

        # Switch to other player
        opponent = state.opponent_of(action.player_id)
        state.active_player = opponent

        # Advance turn counter after both players have moved
        players = list(state.players.keys())
        if action.player_id == players[-1]:
            state.turn_number += 1

        # Begin next player's START → DRAW → PREPARATION sequence
        state.phase = Phase.START
        # (The auto-start is triggered by the next call from the game loop)

        return events

    def _resolve_restore_effect_charge(
        self,
        state: GameState,
        ending_player: str,
        events: list[Event],
        registry: "object | None",
    ) -> None:
        """
        Stage 5 — battlefield_medic's ``restore_effect_charge``.

        At the end of ``ending_player``'s own turn, every allied unit
        carrying this effect restores one spent shield charge
        (``shield:0`` → ``shield:1``) to a single adjacent allied monster
        that has previously had a shield.  Only one target per medic per
        turn (``max_targets: 1`` in the current data set).
        """
        from game.cards.card import MonsterCard

        for pos, medic_unit in state.board.all_units_for(ending_player):
            if medic_unit.monster_id is None:
                continue
            try:
                card = registry.get(medic_unit.monster_id)
            except KeyError:
                continue
            if not isinstance(card, MonsterCard):
                continue

            for effect in card.effects:
                if effect.type != "restore_effect_charge":
                    continue
                if effect.params.get("trigger", "end_of_turn") != "end_of_turn":
                    continue
                radius = effect.params.get("radius", 1)
                amount = effect.params.get("amount", 1)

                restored = False
                for df in (-1, 0, 1):
                    if restored:
                        break
                    for dr in (-1, 0, 1):
                        if df == 0 and dr == 0:
                            continue
                        if abs(df) > radius or abs(dr) > radius:
                            continue
                        nf, nr = pos.file + df, pos.rank + dr
                        if not (0 <= nf <= 7 and 0 <= nr <= 7):
                            continue
                        ally_pos = Position(nf, nr)
                        ally = state.board.get_unit(ally_pos)
                        if ally is None or ally.owner != ending_player or ally is medic_unit:
                            continue
                        for idx, status in enumerate(ally.statuses):
                            if status.startswith("shield:"):
                                charges = int(status.split(":")[1])
                                ally.statuses[idx] = f"shield:{charges + amount}"
                                restored = True
                                break
                        if restored:
                            break
                break  # only the first matching effect entry per medic

    # ── Final Duel (Stage 12) ────────────────────────────────────────────

    def _trigger_final_duel(
        self,
        state: GameState,
        duel_type: FinalDuelType,
        attacker: str,
        defender: str,
        events: list[Event],
        trigger_position: "Position | None" = None,
        captured_king_piece_id: str | None = None,
    ) -> None:
        """
        Single entry point for entering Phase.FINAL_DUEL (README §22 — King
        capture and checkmate both trigger the Duel instead of an instant
        win). Builds the DuelState via mechanics/duel.initialize_duel (Royal
        Support tally — README §27) and appends FinalDuelTriggered.

        Called from every capture/checkmate/stalemate detection site instead
        of constructing DuelState inline, so all five sites share one
        initialization path.
        """
        from game.mechanics.duel import initialize_duel

        duel = DuelState(duel_type=duel_type, attacker=attacker, defender=defender)
        state.duel = duel
        state.phase = Phase.FINAL_DUEL
        initialize_duel(
            state, duel, self._registry, events,
            trigger_position=trigger_position,
            captured_king_piece_id=captured_king_piece_id,
        )
        events.append(FinalDuelTriggered(
            attacker=attacker,
            defender=defender,
            duel_type=duel_type,
            trigger_position=trigger_position,
        ))

    def _execute_final_duel_action(
        self,
        state: GameState,
        action: FinalDuelAction,
        registry: "object | None" = None,
    ) -> list[Event]:
        """Dispatch a Duel Action (README §32) to mechanics/duel.py."""
        self._require_phase(state, Phase.FINAL_DUEL)
        if state.duel is None:
            raise IllegalActionError("No active Final Duel.")
        from game.mechanics.duel import get_legal_duel_actions, resolve_duel_action

        item_id = action.parameters.get("item_id")
        legal = get_legal_duel_actions(state, action.player_id)
        if not any(
            a.duel_action_type == action.duel_action_type
            and a.parameters.get("item_id") == item_id
            for a in legal
        ):
            raise IllegalActionError(
                f"Illegal Duel action {action.duel_action_type!r} "
                f"(item_id={item_id!r}) for {action.player_id!r}."
            )

        events: list[Event] = []
        resolve_duel_action(state, action, events, registry)
        return events

    # ── Castling rights helpers (Stage 1) ────────────────────────────────

    def _update_castling_rights(
        self,
        state: GameState,
        player_id: str,
        unit: UnitInstance,
        source: Position,
    ) -> None:
        """
        Revoke castling rights after a King or Rook move.
        Called after every successful MovePiece.
        """
        ps = state.get_player(player_id)
        cr = ps.castling_rights
        rank = 0 if player_id == "white" else 7

        if unit.piece.piece_type == PieceType.KING:
            cr.revoke_all()
        elif unit.piece.piece_type == PieceType.ROOK:
            if source == Position(0, rank):   # a-file rook
                cr.queenside = False
            elif source == Position(7, rank): # h-file rook
                cr.kingside = False

    def _revoke_castling_rights_for_captured_rook(
        self,
        state: GameState,
        captured_unit: UnitInstance,
        captured_at: Position,
    ) -> None:
        """
        If a Rook was captured, revoke the opponent's castling right for that side.
        Called when a piece capture event is detected.
        """
        if captured_unit.piece.piece_type != PieceType.ROOK:
            return
        opp_id = captured_unit.owner
        opp_ps = state.get_player(opp_id)
        rank = 0 if opp_id == "white" else 7
        if captured_at == Position(0, rank):
            opp_ps.castling_rights.queenside = False
        elif captured_at == Position(7, rank):
            opp_ps.castling_rights.kingside = False

    @staticmethod
    def _compute_en_passant_target(
        unit: UnitInstance,
        source: Position,
        target: Position,
    ) -> "Position | None":
        """
        Return the en passant target square if the move was a double pawn push,
        otherwise None.  Clears en passant for any other move type.
        """
        if unit.piece.piece_type != PieceType.PAWN:
            return None
        if abs(target.rank - source.rank) == 2:
            # Double push — en passant target is the square the pawn passed through
            ep_rank = (source.rank + target.rank) // 2
            return Position(source.file, ep_rank)
        return None

    # ── Check status update ──────────────────────────────────────────────

    def _update_check_status(
        self,
        state: GameState,
        events: list[Event],
    ) -> None:
        """Recompute and cache check flags after a board change."""
        for pid in state.players:
            ps = state.get_player(pid)
            was_in_check = ps.is_in_check()
            now_in_check = is_in_check(state.board, pid)
            ps.set_check(now_in_check)
            if not was_in_check and now_in_check:
                events.append(CheckDetected(player_id=pid))
                # Stage 11: "being checked" Ritual-revelation trigger (README §15.1).
                from game.mechanics.rituals import on_check_detected
                on_check_detected(state, pid, events, self._registry)
            elif was_in_check and not now_in_check:
                events.append(CheckResolved(player_id=pid))
