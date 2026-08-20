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
    MovePiece,
    PlaceMercenaryPiece,
    PlaceTrap,
    PromotePawn,
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
        state.event_log.extend(events)
        return events

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
        if state.active_player != player_id:
            return []

        if state.is_game_over():
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
            actions.append(EndPreparation(player_id=player_id))
            if not ps.preparation_action_used:
                from game.cards.card import MonsterCard
                # Summon Monster: for each monster in hand × valid vessels
                for card_id in ps.hand:
                    if registry and card_id in registry:
                        card = registry.get(card_id)
                        if isinstance(card, MonsterCard):
                            for pos, unit in state.board.all_units_for(player_id):
                                if unit.monster_id is not None or unit.piece.piece_type == PieceType.KING:
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
                # Stage 6: Place Trap — one per Trap card in hand × every
                # empty square on the board.
                from game.cards.card import SpellCard, TrapCard
                if registry is not None:
                    for card_id in ps.hand:
                        if card_id not in registry:
                            continue
                        card = registry.get(card_id)
                        if isinstance(card, TrapCard):
                            occupied_trap_squares = {t.position for t in state.traps}
                            for f in range(8):
                                for r in range(8):
                                    pos = Position(f, r)
                                    if (
                                        state.board.get_unit(pos) is None
                                        and pos not in occupied_trap_squares
                                    ):
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
                ep = state.en_passant_target
                cr = ps.castling_rights
                for pos, unit in state.board.all_units_for(player_id):
                    for target in get_legal_moves(
                        state.board, pos, unit,
                        en_passant_target=ep,
                        castling_rights=cr,
                        player_id=player_id,
                        registry=registry,
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
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.monster_id is None:
                        continue
                    try:
                        card = registry.get(unit.monster_id)
                    except KeyError:
                        continue
                    if not isinstance(card, _MC):
                        continue
                    for eff in card.effects:
                        if eff.type in ACTIVATED_EFFECT_TYPES:
                            actions.append(
                                ActivateMonsterAbility(
                                    player_id=player_id,
                                    unit_position=pos,
                                    ability_id=eff.type,
                                )
                            )

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

        elif card.target_type in ("position", "zone"):
            if card.shape == "column":
                squares = [(f, 0) for f in range(8)]
            elif card.shape == "row":
                squares = [(0, r) for r in range(8)]
            else:
                squares = [(f, r) for f in range(8) for r in range(8)]
            for f, r in squares:
                actions.append(ActivateSpell(
                    player_id=player_id, card_id=card_id, target=(f, r),
                ))

        elif card.target_type == "piece":
            # Only reposition_unit-style spells (arcane_reposition) can be
            # enumerated generically — the destination is part of the
            # target, unlike a Trap/Monster effect where it's chosen later.
            reposition_effect = next(
                (e for e in card.effects if e.type == "reposition_unit"), None
            )
            if reposition_effect is None:
                return actions
            max_dist = reposition_effect.params.get("max_distance", 1)
            must_be_own = reposition_effect.params.get("must_be_own", True)
            for pos, unit in state.board.all_units_for(player_id):
                if must_be_own and unit.piece.piece_type == PieceType.KING:
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

        ep = state.en_passant_target
        cr = ps.castling_rights
        legal = get_legal_moves(
            state.board, action.source, unit,
            en_passant_target=ep,
            castling_rights=cr,
            player_id=action.player_id,
            registry=self._registry,
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
            from game.mechanics.monsters import apply_capture_protection
            target_unit = state.board.get_unit(action.target)
            shield_absorbed = (
                target_unit is not None
                and target_unit.monster_id is not None
                and apply_capture_protection(target_unit)
            )

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
                    # ── Stage 5: monster destruction event ──────────────
                    if captured.monster_id is not None:
                        events.append(MonsterDestroyed(
                            player_id=captured.owner,
                            card_id=captured.monster_id,
                            vessel_piece_id=captured.piece.id,
                            position=action.target,
                            destroyed_by_piece_id=unit.piece.id,
                        ))
                    events.append(PieceCaptured(
                        piece_id=captured.piece.id,
                        owner=captured.owner,
                        captured_at=action.target,
                        captured_by_piece_id=unit.piece.id,
                    ))

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
                        check_immobilize_zone(state, unit, action.target)

            # King capture → Final Duel (never sets winner directly)
            if captured is not None and not shield_absorbed and captured.piece.piece_type == PieceType.KING:
                move_snapshot.triggered_duel = True
                duel = DuelState(
                    duel_type=FinalDuelType.ASSAULT,
                    attacker=action.player_id,
                    defender=captured.owner,
                )
                state.duel = duel
                state.phase = Phase.FINAL_DUEL
                events.append(FinalDuelTriggered(
                    attacker=action.player_id,
                    defender=captured.owner,
                    duel_type=FinalDuelType.ASSAULT,
                    trigger_position=action.target,
                ))
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
            state.duel = DuelState(
                duel_type=FinalDuelType.SIEGE,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.SIEGE,
            ))
        elif is_stalemate(state.board, opp_id, opp_ep, opp_cr):
            move_snapshot.triggered_duel = True
            events.append(StalemateDetected(player_id=opp_id))
            state.duel = DuelState(
                duel_type=FinalDuelType.LAST_STAND,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.LAST_STAND,
            ))

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
            state.duel = DuelState(
                duel_type=FinalDuelType.SIEGE,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.SIEGE,
            ))
        elif is_stalemate(state.board, opp_id, state.en_passant_target, opp_ps.castling_rights):
            events.append(StalemateDetected(player_id=opp_id))
            state.duel = DuelState(
                duel_type=FinalDuelType.LAST_STAND,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.LAST_STAND,
            ))

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

        # ── Stage 5: vessel compatibility check via registry ─────────────
        if registry is not None:
            try:
                card = registry.get(action.card_id)
            except KeyError:
                raise IllegalActionError(f"Unknown card: {action.card_id!r}")
            if not isinstance(card, MonsterCard):
                raise IllegalActionError(
                    f"Card {action.card_id!r} is not a Monster card."
                )
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
                raise IllegalActionError(
                    f"Monster {action.card_id!r} cannot use "
                    f"{unit.piece.piece_type.value!r} as a vessel. "
                    f"Supported: {list(card.supported_vessels)}"
                )  # VESSEL_COMPATIBILITY
        else:
            card = None

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
            apply_on_summon_effects(state, unit, card, events, rng=rng)

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
            "position"  (f, r) — area center; expanded via card.radius/shape
            "zone"      (f, r) — anchor square; shape="column"/"row" expands
                         to the whole file/rank regardless of which square
                         on that line was picked
            "trap"      trap_instance_id (str)
            "none"      None — card has no board target (e.g. ritual_insight)
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
        elif card.target_type == "none":
            self._resolve_spell_untargeted(state, card, events, rng, registry)
        return events

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
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot target an opponent's piece with this Spell.")

        destination = action.target.get("destination")
        for effect in card.effects:
            ctx = EffectContext(
                state=state, unit=unit, position=pos, card=card, effect=effect,
                events=events, registry=registry, trigger="instant_spell",
                extra={"destination": destination} if destination is not None else {},
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
    ) -> None:
        """
        target_type == "none" Spells (e.g. ritual_insight — deferred, needs
        the Ritual system).  Effects that aren't implemented yet are
        silently skipped, same as any other not-yet-implemented handler.
        """
        from game.mechanics.effects.registry import EffectContext, resolve_effect

        for effect in card.effects:
            ctx = EffectContext(
                state=state, unit=None, position=None, card=card, effect=effect,
                events=events, rng=rng, registry=registry, trigger="instant_spell",
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
        """
        from game.cards.card import TrapCard
        from game.core.events import TrapPlaced

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        if state.board.get_unit(action.position) is not None:
            raise IllegalActionError(
                f"Cannot place a Trap on an occupied square ({action.position})."
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

        events: list[Event] = []
        target_player = state.opponent_of(action.player_id)
        for effect in card.effects:
            ctx = EffectContext(
                state=state, unit=None, position=trap.position, card=card, effect=effect,
                events=events, registry=registry, trigger="manual",
                extra={"target_player": target_player, "trap_owner": action.player_id},
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

        building_id = f"bld-{action.player_id}-{len(state.buildings)+1:03d}"
        building = BuildingInstance(
            id=building_id,
            owner=action.player_id,
            building_card_id=action.building_card_id,
            position=action.pawn_position,
            status=ConstructionStatus.UNDER_CONSTRUCTION,
            builder_piece_id=unit.piece.id,
            remaining_turns=2,  # default; overridden by building definition Stage 8
        )
        state.buildings.append(building)

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
        from game.core.events import KingSuccession

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

        # Retire old king
        if old_kcs:
            old_kcs.status = KingCardStatus.RETIRED
            ps.retired_kings.append(ps.active_king)

        new_kcs.status = KingCardStatus.ACTIVE
        old_id = ps.active_king
        ps.active_king = action.king_card_id

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "ChangeKing", events)
        events.append(KingSuccession(
            player_id=action.player_id,
            retired_king_card_id=old_id,
            new_king_card_id=action.king_card_id,
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

        # ── Stage 5/6: tick down frozen / scorched / blocked / cursed squares ─
        # Effect format: "<prefix>:<turns>:<owner_player_id>"
        # "blocked" is veil_of_stillness's zone; "cursed" is cursed_ground's.
        # Decrement only when the player who DIDN'T set the effect ends their
        # turn (i.e., the opponent of the setter calls EndTurn).  This ensures
        # the effect blocks for exactly <turns> full opponent turns.
        ending_player = action.player_id
        for sq in state.board.squares.values():
            remaining = []
            for eff in sq.temporary_effects:
                prefix = eff.split(":")[0]
                if prefix in ("frozen", "scorched", "blocked", "cursed"):
                    # Format: "<prefix>:<turns>:<owner>:<source_card_id>" — the
                    # trailing card_id (Stage 6, for the CardViewer's "Active in
                    # this zone" list) must survive the decrement/rewrite below.
                    parts = eff.split(":")
                    n = int(parts[1])
                    owner = parts[2] if len(parts) > 2 else None
                    card_id = parts[3] if len(parts) > 3 else None
                    # Decrement only if the player ending their turn is NOT the owner
                    if owner is None or owner != ending_player:
                        n -= 1
                    if n > 0:
                        tail = f":{owner}" if owner or card_id else ""
                        tail += f":{card_id}" if card_id else ""
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
                if prefix in ("burrow_cooldown", "immobilized", "exposed"):
                    n = int(status.split(":")[1]) - 1
                    if n > 0:
                        remaining_statuses.append(f"{prefix}:{n}")
                    # else: expired, don't add back
                else:
                    remaining_statuses.append(status)
            u.statuses = remaining_statuses

        # ── Stage 5: restore_effect_charge (battlefield_medic) ───────────
        # At the end of the owner's own turn, restore one spent shield
        # charge to a single adjacent allied monster.
        if self._registry is not None:
            self._resolve_restore_effect_charge(state, ending_player, events, self._registry)

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
            elif was_in_check and not now_in_check:
                events.append(CheckResolved(player_id=pid))
