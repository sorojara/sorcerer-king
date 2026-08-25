"""
GameState — Stage 0 / Stage 1
==============================

The complete, authoritative, omniscient game state.

Rule: the GameState is NEVER handed directly to an AI or the UI.
      It is translated into an Observation first (see observation.py).

Top-level shape:

    GameState
      ├── board: BoardState
      ├── players: dict[str, PlayerState]
      ├── traps: list[TrapInstance]
      ├── buildings: list[BuildingInstance]
      ├── pending_decision: PendingDecision | None
      ├── duel: DuelState | None
      └── rng_state: stored in DeterministicRNG

PendingDecision exists for two-step actions (Recompose, Promotion, …)
where the engine must wait for a follow-up player input.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from game.chess.board import BoardState
from game.chess.chess_state import CastlingRights
from game.core.phases import (
    ConstructionStatus,
    DecisionType,
    FinalDuelType,
    KingCardStatus,
    Phase,
    PieceType,
    RevelationState,
)


# ─────────────────────────────────────────────────────────────────────────────
# PendingDecision
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PendingDecision:
    """
    Represents a mid-turn decision that must be resolved before the game
    can advance to the next phase.

    Examples:
        • After DeclareRecompose — player must choose exactly N cards.
        • After a Pawn reaches back-rank — player must choose promotion type.
        • After certain Spell effects — player must choose a target.
    """

    player_id: str
    decision_type: DecisionType
    options: list[Any] = field(default_factory=list)  # valid choices
    min_choices: int = 1
    max_choices: int = 1
    context: dict[str, Any] = field(default_factory=dict)  # extra data for resolver


# ─────────────────────────────────────────────────────────────────────────────
# Ritual / King / Building sub-states
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RitualState:
    """
    Tracks one Ritual card in a player's pool and its revelation progress.

    ``progress``               — Stage 11: accumulator fed by
        ``ritual_progress_boost`` (ritual_acolyte, end-of-turn — see
        mechanics/rituals.py advance_ritual_progress). Reaching the
        RitualCard's ``reveal_progress_threshold`` advances ``revelation``
        one step (SEALED→FORETOLD or FORETOLD→REVEALED) and resets to 0.
    ``requirement_reduction``  — Stage 11: forbidden_priest's once-per-
        summon ``ritual_requirement_reduction`` effect. Lowers the
        effective min_material / min_sacrifices / non-anchor pattern-node
        count needed to activate this Ritual by this many (see
        mechanics/rituals.py _effective_requirement). Activating it also
        forces ``revelation`` to REVEALED (card text).
    ``activated``              — True once this Ritual has been summoned
        (ActivateRitual succeeded). A completed Ritual cannot be attempted
        again — mirrors "a retired King cannot become active again"
        (README §19), applied here to a one-shot summon rather than a
        policy switch.
    ``bluff_turns``            — false_prophecy's ``ritual_bluff``. While
        > 0 on a SEALED Ritual, the OPPONENT's Observation reports a
        fabricated FORETOLD requirement for it (see core/observation.py);
        the owner's own view and every engine check still treat it as
        truly SEALED. Ticks down on the opponent's own EndTurn (mirrors
        square-effect durations — blocks their true information for
        exactly N of their own turns).
    """

    ritual_id: str
    revelation: RevelationState = RevelationState.SEALED
    progress: int = 0
    requirement_reduction: int = 0
    activated: bool = False
    bluff_turns: int = 0


@dataclass
class KingCardState:
    """Tracks one King card in a player's pool."""

    king_card_id: str
    status: KingCardStatus = KingCardStatus.HIDDEN


@dataclass
class BuildingPoolEntry:
    """
    One entry in a player's pre-match Building Pool.
    The pool is public information.
    """

    building_card_id: str
    copies_available: int


@dataclass
class TrapInstance:
    """
    A placed Trap on the board.
    Traps are NOT hidden — their identity, position, and radius are public.
    """

    id: str              # unique runtime ID, e.g. "trap-white-001"
    owner: str
    card_id: str         # references a trap definition in the card registry
    position: "Position"  # type: ignore[name-defined]  # forward ref ok
    radius: int          # default 1 = 3×3 area centered on position
    trigger_condition: str  # short tag: "enter_radius", "capture", etc.
    activated: bool = False
    shape: str = "square"        # Stage 6: mirrors TrapCard.shape
    # Stage 6: mirrors TrapCard.charges — None = reusable indefinitely,
    # otherwise decremented by mechanics.monsters._fire_trap() and the Trap
    # is removed from state.traps once it reaches 0 (pit_trap: charges=1).
    charges: int | None = None


@dataclass
class BuildingInstance:
    """A Building placed (or under construction) on the board."""

    id: str
    owner: str
    building_card_id: str
    position: "Position"  # type: ignore[name-defined]
    status: ConstructionStatus = ConstructionStatus.UNDER_CONSTRUCTION
    builder_piece_id: str | None = None
    remaining_turns: int = 0  # construction countdown; 0 when complete
    # saboteur's ``disable_building``: turns remaining before this
    # COMPLETE Building's aura/support effects resume. Ticks down on the
    # OWNER's own EndTurn (mirrors burrow_cooldown/immobilized — "until
    # the start of the next owner turn").
    disabled_turns: int = 0

    # ── Stage 13: siege / durability (mechanics/buildings.py) ───────────
    # ``max_integrity`` is BuildingCard.base_integrity, copied here at
    # construction start so registry-less callers still have a value.
    # ``integrity`` is the current, damageable pool: every successful
    # hostile action (AttackBuilding, demolition_charge's building_damage)
    # removes one point; the Building collapses when it hits 0 with no
    # siege_captain-style ``building_aura`` durability bonus left to
    # absorb the blow. royal_engineer's ``repair_building`` heals it back
    # up to ``max_integrity`` at the owner's end of turn.
    max_integrity: int = 1
    integrity: int = 1
    # emergency_fortifications' ``temporary_building_protection`` — while
    # ``protection_turns`` > 0, the next ``protection_uses`` blows that
    # WOULD destroy this Building are absorbed instead (the Building is
    # left standing at 1 integrity). Both tick on the OWNER's own EndTurn.
    protection_uses: int = 0
    protection_turns: int = 0
    # siege_order's ``building_vulnerability`` — while ``vulnerable_turns``
    # > 0 this Building takes ``vulnerable_amount`` EXTRA damage per hostile
    # action AND is open to being attacked by any enemy unit, not only the
    # Ritual Monsters / building_damage_bonus Monsters that may normally
    # besiege (README-derived rule, see mechanics/buildings.py
    # can_attack_building). Ticks on the OWNER's own EndTurn.
    vulnerable_turns: int = 0
    vulnerable_amount: int = 1
    # True from the moment a hostile action lands until the owner's next
    # EndTurn resolves. royal_engineer-style ``repair_building`` skips a
    # Building carrying this flag: a structure under active bombardment
    # cannot be patched up the same round it was hit. Without it, +1
    # repair per turn exactly cancelled 1 damage per turn, so a single
    # royal_engineer made a Building unkillable by any attacker that was
    # not a one-shot. Cleared in mechanics/buildings.py
    # tick_building_timers, which runs after repair_buildings on the
    # owner's own EndTurn.
    damaged_this_round: bool = False


@dataclass
class DuelSupportItem:
    """
    Stage 12 — one queued Final Duel bonus available to a side.

    A "Duel Support" (README §28), "Building" (§29), or "King Policy" (§30)
    action all resolve the same way underneath: the acting player spends one
    of these queued items and its effect is applied. ``source`` tells the UI
    / action validator which of the three duel_action_type values ("support",
    "building", "king_policy") may spend it.

    ``ability`` is the dispatch key read by mechanics/duel.py's
    ``_ABILITY_HANDLERS`` table — a standardized vessel ability
    ("guard"/"charge"/"intervention"/"fortify"/"command" — README §28) for
    source="piece", or a king-policy key ("king:<duel_effect type>") /
    building key ("building:<building_card_id>") for the other two sources.

    All items are computed ONCE when the Duel begins (README §24 — "no new
    Main Deck cards are drawn"; the Duel spends only resources the board
    state already produced) and never regenerate mid-Duel.
    """

    item_id: str
    owner: str                    # player_id allowed to spend this item
    source: str                   # "piece" | "building" | "king"
    ability: str                  # dispatch key — see mechanics/duel.py
    amount: int = 1                # magnitude fed to the ability handler
    label: str = ""                # human-readable, for UI / event log
    origin_piece_id: str | None = None   # source == "piece" only


@dataclass
class DuelState:
    """
    Stage 12 — Final Duel bookkeeping (README §23–33).

    The Duel Arena itself (README §26's literal 3×3 grid) is intentionally
    abstracted away in this prototype: rather than a second movement engine,
    the Duel is modeled as an alternating round structure (defender acts,
    then attacker acts) driving three deterministic resources — Strikes,
    Guards, and a Bypass counter. This keeps the Duel "short" and close to
    "no randomness" (README §24) while still expressing every rule in
    README §27–33 (Royal Support eligibility, standardized piece/Building/
    King duel abilities, Strikes, Royal Escape, Last Stand escalation).

    ``strikes_landed`` / ``strikes_needed`` — Attacker wins the instant
        ``strikes_landed`` reaches ``strikes_needed`` (README §31, "Land 2
        successful Strikes").
    ``defender_guards`` — each unabsorbed Strike attempt is blocked by
        consuming one Guard (Pawn's standardized "Guard" ability, README
        §28); ``defender_guard_debuff`` (kingsbane/royal_poisoner/
        shadow_regent — applied once at duel start) lowers every future
        Guard grant, floored at 0. ``defender_bonus_guards`` (royal_guard's
        passive king_support_bonus) is added once, automatically, at setup.
    ``attacker_bypass`` — Strikes consume one Bypass token first (before a
        Guard) — granted by the standardized "guard"/"charge" abilities
        when spent by the ATTACKER (README §28's Knight "Charge" reads as
        exactly this: "bypass one defensive square").
    ``max_rounds`` — Defender wins (Royal Escape, README §33) by surviving
        this many rounds without ``strikes_needed`` being reached.
    ``escape_allowed`` — False once this King has survived 2 prior Duels
        this match (README §17/25 "Last Stand, no escape" on the 3rd Duel);
        ``max_rounds`` is then ignored (``hard_round_cap`` becomes the only
        safety valve, guaranteeing the match always terminates).
    ``escape_shield_charges`` — how many capture attempts the King's
        ``shield:N`` status will absorb after a successful Royal Escape
        (README §33's Royal Immunity, implemented by reusing the existing
        capture-protection mechanic — see mechanics/monsters.py
        apply_capture_protection). Boosted by royal_escape_route (README
        Trap "additional_escape_option").
    """

    duel_type: FinalDuelType
    attacker: str
    defender: str

    round_number: int = 1
    max_rounds: int = 5
    hard_round_cap: int = 30          # Last Stand safety valve (no escape)
    strikes_needed: int = 2
    strikes_landed: int = 0

    defender_guards: int = 0
    defender_bonus_guards: int = 0     # applied once at setup, informational
    defender_guard_debuff: int = 0     # applied once at setup; floors future grants
    attacker_bypass: int = 0

    escape_allowed: bool = True
    escape_shield_charges: int = 1

    attacker_support: "list[DuelSupportItem]" = field(default_factory=list)
    defender_support: "list[DuelSupportItem]" = field(default_factory=list)

    defender_acted_this_round: bool = False
    attacker_acted_this_round: bool = False

    # Arena reference square — the defender's King's position at Duel start
    # (README §26). Cached here because an ASSAULT trigger has already
    # REMOVED the King from the board by the time the Duel begins (see
    # ``captured_king_piece_id``), so it can't be re-derived later via
    # BoardState.find_king().
    arena_center: "Position | None" = None
    # ASSAULT only: the captured King's ChessPiece.id, kept so a successful
    # Royal Escape (README §33) can restore it to the board — "King capture
    # does not immediately end the match" (README §22) means a capture is
    # provisional until the Duel resolves.
    captured_king_piece_id: str | None = None

    log: list[str] = field(default_factory=list)


@dataclass
class MoveSnapshot:
    """
    Stage 6 — a reversible-transaction snapshot of one MovePiece, kept so
    ``time_anchor`` can restore the board to how it looked immediately
    before that move.

    This is a pragmatic alternative to a fully generic undoable event log:
    rather than defining an inverse for every Event type, we snapshot the
    handful of mutable pieces of state a MovePiece can touch and restore
    them wholesale.  GameState keeps at most ONE of these per player (the
    player's own most recent move) — a new move for that player overwrites
    the previous snapshot, which is exactly "last move" semantics.

    ``board_before``              — deep copy of the board immediately
                                     before the move (captures piece
                                     positions, monster_id, statuses —
                                     reverting it also un-destroys any
                                     Monster that move captured, for free).
    ``victim_captured_len_before`` — length of the defender's (the
                                     opponent-of-mover's) ``captured_pieces``
                                     list before the move, so truncating
                                     back to it undoes the bookkeeping
                                     append from a capture, if any.
    ``castling_rights_before``    — deep copy of the mover's own castling
                                     rights before the move.
    ``en_passant_before``         — state.en_passant_target before the move.
    ``triggered_duel``            — True if this move triggered a Final
                                     Duel (King capture / checkmate /
                                     stalemate).  time_anchor refuses to
                                     cancel such a move (per its own
                                     design — see traps.yaml).
    """

    player_id: str
    turn_number: int
    source: "Position"       # type: ignore[name-defined]
    target: "Position"       # type: ignore[name-defined]
    board_before: "BoardState"
    victim_captured_len_before: int
    castling_rights_before: CastlingRights
    en_passant_before: "Position | None"  # type: ignore[name-defined]
    triggered_duel: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# PlayerState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    """
    All private and public information belonging to one player.

    ``deck``     — ordered list of card IDs (top of deck = index 0).
    ``hand``     — card IDs currently in hand.
    ``graveyard``— card IDs destroyed / discarded.
    ``king_pool``— the three King cards (hidden until Coronation).
    ``active_king`` — the card_id of the currently active King, or None.
    ``ritual_pool`` — Ritual cards (hidden until revealed).
    ``building_pool`` — Building Pool (public).
    ``preparation_action_used`` — True once a major prep action is taken this turn.
    ``chess_move_used`` — True once a chess move is made this turn.
    """

    player_id: str

    deck: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    graveyard: list[str] = field(default_factory=list)
    # erase_memory's banish_deck_card. Kept SEPARATE from graveyard because
    # the deck-empty reshuffle recycles the graveyard — a banished card must
    # never come back, and nothing reveals it to its owner.
    banished: list[str] = field(default_factory=list)
    # Endgame-pacing fix: Spell/Trap ids that have already made one trip to
    # the graveyard. The SECOND trip banishes the card instead of returning
    # it — see RulesEngine._bury_or_retire. Monster ids are never added
    # here; their attrition already comes from losing their Vessel.
    spent_once: set[str] = field(default_factory=set)

    king_pool: list[KingCardState] = field(default_factory=list)
    active_king: str | None = None
    retired_kings: list[str] = field(default_factory=list)
    # Stage 10: the archetype randomly assigned to this player at setup —
    # guarantees their King Pool contains that archetype's "home" King (see
    # mechanics/kings.py assign_random_king_pool). Exposed to its OWN owner
    # via Observation.own_archetype (AI Stage 6 — a King policy has to be
    # judged against the deck it is meant to support); never to the
    # opponent, and it was already argued to be unexploitable even if it
    # were, since own_king_pool reveals those card IDs to the owner anyway.
    archetype: str | None = None

    ritual_pool: list[RitualState] = field(default_factory=list)
    # Stage 11: lifetime count of this player's OWN Monsters destroyed
    # (across the whole match, never reset) — feeds the Ritual state
    # predicate "allied_monsters_destroyed_at_least_N" (throne_of_remains).
    # Incremented centrally in core/rules.py RulesEngine.execute() for
    # every MonsterDestroyed event, so no destruction path can miss it.
    monsters_lost_count: int = 0

    building_pool: list[BuildingPoolEntry] = field(default_factory=list)

    # Castling rights (Stage 1). Start both sides available.
    castling_rights: CastlingRights = field(default_factory=CastlingRights)

    # Captured enemy pieces (chess piece IDs, e.g. "white-pawn-a2").
    # Kept SEPARATE from graveyard so captured pieces are never recycled into
    # the card deck.  graveyard is cards-only; captured_pieces is pieces-only.
    captured_pieces: list[str] = field(default_factory=list)

    # Turn-scoped flags, reset at start of each player's turn.
    preparation_action_used: bool = False
    chess_move_used: bool = False
    # Stage 10: Grave-Crowned King's graveyard_recycle policy only fires for
    # the FIRST allied Monster destroyed each turn (README-style "once per
    # turn" cap — see mechanics/kings.py maybe_recycle_destroyed_monster).
    king_recycle_used_this_turn: bool = False
    # mourning_queen's ``death_trigger_draw`` (limit_per_turn: 1) — only the
    # FIRST allied Monster destroyed on this player's turn draws a card.
    death_trigger_draw_used_this_turn: bool = False
    # Stage 11 (README §15.2) — RevealRitual is free (it does not consume the
    # preparation action), so the only thing keeping revelation *progressive*
    # is this per-turn cap: one voluntary step per turn. Without it a player
    # could take a Ritual from SEALED to summoned in a single turn and the
    # opponent would never see it coming, which is the whole mechanic.
    ritual_reveal_used_this_turn: bool = False
    # Stage 6 (README §53) — activated Monster abilities already fired this
    # turn, as ``(piece_id, ability_id)`` pairs.  Activating an ability does
    # not consume the chess move and several abilities (``inspect_top_deck``,
    # ``burrow``) stay legal after use, so without a cap the same ability can
    # be re-fired forever and the turn never ends.  README §46 already
    # measured that pathology ("173 activations and 3 moves in a 200-action
    # sample") and capped it *inside the bots* — this is the same cap where
    # it belongs, in the rules, so a human at the UI and a bot that forgets
    # to self-limit are held to it too.  Keyed on the piece ID rather than
    # the square so moving the unit does not buy a second activation.
    abilities_used_this_turn: set[tuple[str, str]] = field(default_factory=set)

    # rally_the_kingdom's ``royal_support_bonus`` — while
    # ``royal_support_bonus_turns`` > 0, every piece of this player's that
    # already qualifies as Royal Support contributes
    # ``royal_support_bonus_amount`` more in a Final Duel (README §27 —
    # "Does not expand support range", so only the per-item amount moves).
    # Ticks down on this player's own EndTurn.
    royal_support_bonus_amount: int = 0
    royal_support_bonus_turns: int = 0

    # arcane_sovereign's ``ritual_information_discount`` — the policy pays
    # out only on the FIRST voluntary Ritual revelation of the match
    # ("first_reveal_each_match: true"), so the payout is latched here.
    # Never reset — "each match", not "each turn".
    ritual_info_discount_used: bool = False

    # Stage 12: count of Final Duels this player has SURVIVED (Royal Escape)
    # as defender this match. Never reset — feeds the Last Stand escalation
    # rule (README §17/25): the 3rd Duel against this King removes the
    # round-limit escape (mechanics/duel.py initialize_duel).
    duels_survived: int = 0

    def reset_turn_flags(self) -> None:
        """Call at the start of this player's turn."""
        self.preparation_action_used = False
        self.chess_move_used = False
        self.king_recycle_used_this_turn = False
        self.death_trigger_draw_used_this_turn = False
        self.ritual_reveal_used_this_turn = False
        self.abilities_used_this_turn.clear()

    def is_in_check(self) -> bool:
        """
        Check status is computed by the RulesEngine, not stored here.
        This flag is a convenience cache; updated by the engine after each move.
        """
        return self._in_check

    _in_check: bool = field(default=False, repr=False)

    def set_check(self, value: bool) -> None:
        self._in_check = value


# ─────────────────────────────────────────────────────────────────────────────
# GameState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchLimits:
    """
    AI Stage 6 (README §53) — the ceilings that guarantee a match ends.

    Nothing in §31's win conditions bounds a match: checkmate and stalemate
    both hand off to the Final Duel rather than ending the game, Royal
    Escape sends a survived Duel back to the board, and there is no
    threefold-repetition or fifty-move rule underneath.  Self-play measured
    the consequence — half of a 12-match RandomBot sample never reached a
    result at all, which is exactly the half that carries no label for
    anything downstream to learn from or measure.

    README §57 keeps numbers like these deliberately open ("these should
    remain configurable during the PoC"), so they live here as data rather
    than as constants buried in the engine, and ``Game.new(limits=...)``
    takes any other set.

    ``repetition_limit``  — how many times one position (piece placement +
        side to move) may recur before the match is called.  3 is chess's
        own threefold rule.
    ``no_progress_plies`` — plies allowed with no *irreversible* change:
        no capture, no pawn move, no Building completed, no Ritual
        progress, no Coronation.  Chess's fifty-move rule counted the same
        way; the game's extra systems are folded into the signature so
        building a kingdom counts as progress even when the pieces stand
        still.
    ``max_turns``         — the backstop, for a match that manages to keep
        making progress forever.

    A match stopped by any of these ends drawn (``winner is None``) with
    the rule named in ``GameOver.reason``.
    """

    repetition_limit: int = 3
    no_progress_plies: int = 80
    max_turns: int = 300


@dataclass
class GameState:
    """
    The single source of truth for the entire game.

    This object is PRIVATE to the engine.  Observers receive an Observation.

    ``game_id``         — unique match identifier (for logging / replay).
    ``turn_number``     — increments after each full round (both players moved).
    ``active_player``   — "white" or "black".
    ``phase``           — current phase of the active player's turn.
    ``board``           — the 8×8 board with all units, traps, buildings.
    ``players``         — dict mapping player_id → PlayerState.
    ``traps``           — all placed TrapInstances (public info).
    ``buildings``       — all BuildingInstances on the board.
    ``pending_decision``— set when an action requires a follow-up response.
    ``duel``            — populated if a Final Duel has been triggered.
    ``winner``          — set to a player_id when the game ends.
    ``rng_seed``        — the original seed (logged for reproduction).
    ``event_log``       — all Events produced this game (for replay / telemetry).
    """

    game_id: str
    turn_number: int
    active_player: str
    phase: Phase

    board: BoardState

    players: dict[str, PlayerState] = field(default_factory=dict)

    traps: list[TrapInstance] = field(default_factory=list)
    buildings: list[BuildingInstance] = field(default_factory=list)

    pending_decision: PendingDecision | None = None
    duel: DuelState | None = None

    winner: str | None = None

    # En passant target square (Stage 1).
    # Set to the square behind a double-pushed pawn immediately after that push.
    # Cleared at the start of every subsequent move.
    en_passant_target: "Position | None" = None  # type: ignore[name-defined]

    # Monotonic counter for TrapInstance IDs. NOT derived from
    # len(state.traps): a Trap is REMOVED from that list once its charges
    # run out, so the length can go back down and re-issue an ID that is
    # still referenced by SquareState.trap_ids — after which
    # ActivateTrap/_fire_trap resolve against whichever duplicate the
    # lookup happens to hit first. Only ever incremented.
    trap_seq: int = 0

    rng_seed: int = 0
    event_log: list = field(default_factory=list)  # list[Event] — avoids circular import

    # ── Stage 6 (README §53): termination bookkeeping ────────────────────
    # Kept on the state (like move_history above) so a copy of the state is
    # a complete description of the match — but see RulesEngine.
    # _check_stagnation for why an AI search copy cannot be fooled by it.
    limits: MatchLimits = field(default_factory=MatchLimits)
    #: position key → how many times it has been seen at a turn boundary.
    position_counts: dict[int, int] = field(default_factory=dict, repr=False)
    #: plies since the progress signature last changed.
    no_progress_plies: int = 0
    #: last progress signature seen, or None before the first turn ends.
    last_progress_key: int | None = field(default=None, repr=False)

    # Stage 6: one MoveSnapshot per player, keyed by player_id — their own
    # most recent MovePiece.  Powers time_anchor's cancel_move effect.
    move_history: "dict[str, MoveSnapshot | None]" = field(default_factory=dict)

    def get_player(self, player_id: str) -> PlayerState:
        if player_id not in self.players:
            raise KeyError(f"Unknown player: {player_id!r}")
        return self.players[player_id]

    def opponent_of(self, player_id: str) -> str:
        """Return the other player's ID (assumes exactly two players)."""
        ids = list(self.players.keys())
        return ids[1] if ids[0] == player_id else ids[0]

    def is_game_over(self) -> bool:
        return self.winner is not None or self.phase == Phase.GAME_OVER

    # ── Stage 6 (README §53): termination signatures ─────────────────────

    def position_key(self) -> int:
        """
        Identity of the current position for repetition purposes: where every
        unit stands, what it is, and whose turn it is.

        Deliberately *not* the whole state.  Hands and decks cycle every
        turn, so folding them in would mean no position ever recurred and
        the repetition rule would never fire.  What this asks is the same
        question chess asks — "have we been exactly here before, with the
        same player to move?" — over a board that also carries Monsters.
        """
        return hash((
            tuple(sorted(
                (pos.file, pos.rank, unit.piece.id, unit.monster_id or "")
                for pos, unit in self.board.all_units()
            )),
            self.active_player,
        ))

    def progress_key(self) -> int:
        """
        Identity of everything that only moves one way.

        A capture cannot be undone, a Pawn cannot walk back, a completed
        Building and an accumulated Ritual and a crowned King all stay
        done.  While this signature holds still, the match is not getting
        closer to any of its endings, however much the pieces shuffle —
        which is what ``MatchLimits.no_progress_plies`` counts.
        """
        return hash((
            len(self.board.all_units()),
            tuple(sorted(
                (pos.file, pos.rank)
                for pos, unit in self.board.all_units()
                if unit.piece.piece_type == PieceType.PAWN
            )),
            tuple(sorted(
                (b.id, b.status.value) for b in self.buildings
            )),
            tuple(sorted(
                (pid, rs.ritual_id, rs.progress, rs.revelation.value, rs.activated)
                for pid, ps in self.players.items()
                for rs in ps.ritual_pool
            )),
            tuple(sorted(
                (pid, ks.king_card_id, ks.status.value)
                for pid, ps in self.players.items()
                for ks in ps.king_pool
            )),
        ))

    def is_final_duel_active(self) -> bool:
        return self.duel is not None and self.phase == Phase.FINAL_DUEL

    def deep_copy(self) -> "GameState":
        """Return a full deep copy suitable for AI tree-search / simulation."""
        return copy.deepcopy(self)
