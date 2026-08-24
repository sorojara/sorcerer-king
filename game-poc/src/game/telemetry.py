"""
Telemetry and Balance Instrumentation — README §42
====================================================

The PoC records match statistics so balance work has numbers behind it
rather than impressions.

Two layers:

    MatchTelemetry   — one live recorder per ``Game``.  Fed by
                       ``Game.execute()`` (actions + the events they
                       produced) and by ``Game._auto_start_turn``.
                       Produces a ``MatchStats`` snapshot on demand.

    TelemetryAggregator — accumulates many ``MatchStats`` and computes the
                       rate-style metrics README §42 asks for, which only
                       exist across a *set* of matches:
                       first-player win rate, archetype win rate,
                       King policy win rate.

Every metric named in README §42 is covered:

    match duration              → MatchStats.duration_seconds
    turn count                  → MatchStats.turn_count / ply_count
    first-player win rate       → TelemetryAggregator.first_player_win_rate
    cards played                → PlayerMatchStats.cards_played (+ breakdown)
    cards remaining             → PlayerMatchStats.cards_remaining
    Recomposes used             → PlayerMatchStats.recomposes_used
    Pawns captured              → PlayerMatchStats.pawns_captured
    Pawns sacrificed            → PlayerMatchStats.pawns_sacrificed
    Pawns used as Vessels       → PlayerMatchStats.pawns_used_as_vessels
    Pawns used for construction → PlayerMatchStats.pawns_used_for_construction
    Buildings built             → PlayerMatchStats.buildings_built
    Buildings destroyed         → PlayerMatchStats.buildings_destroyed
    Rituals revealed            → PlayerMatchStats.rituals_revealed
    Rituals attempted           → PlayerMatchStats.rituals_attempted
    Rituals completed           → PlayerMatchStats.rituals_completed
    King Coronation turn        → PlayerMatchStats.king_coronation_turn
    King Successions            → PlayerMatchStats.king_successions
    first check turn            → MatchStats.first_check_turn
    number of checks            → MatchStats.total_checks
    Final Duel trigger type     → MatchStats.final_duel_trigger
    Royal Support score         → PlayerMatchStats.royal_support_score
    Final Duel duration         → MatchStats.final_duel_rounds
    Final Duel winner           → MatchStats.final_duel_winner
    archetype win rate          → TelemetryAggregator.archetype_win_rates()
    King policy win rate        → TelemetryAggregator.king_policy_win_rates()

Design rules:

    • Telemetry NEVER influences the game.  It only reads.
    • Telemetry reads the full ``GameState`` — it is engine-side
      instrumentation, not a player.  It is emphatically NOT an Observation
      consumer and must never be handed to an AI (README §44).
    • Recording is cheap: counters incremented per event, no deep copies.

Usage:

    game = Game.new(seed=42, registry=registry)
    ...play...
    stats = game.telemetry.snapshot(game.state)
    print(stats.to_dict())

    agg = TelemetryAggregator()
    for stats in many_matches:
        agg.add(stats)
    print(agg.summary())
"""

from __future__ import annotations

import csv
import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from game.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.core.actions import Action
    from game.core.events import Event
    from game.core.state import GameState

_log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PIECE_TYPE_TOKENS = frozenset(
    {"king", "queen", "rook", "bishop", "knight", "pawn"}
)


def piece_type_from_id(piece_id: str | None) -> str | None:
    """
    Recover a piece type from a canonical piece ID (``"white-pawn-a2"``,
    ``"black-queen"``, ``"white-merc-knight-1"``).

    Returns None when the ID carries no recognisable piece token — the
    caller treats that as "unknown", never as a silent "pawn".
    """
    if not piece_id:
        return None
    for token in piece_id.split("-"):
        if token in _PIECE_TYPE_TOKENS:
            return token
    return None


def _is_pawn(piece_id: str | None) -> bool:
    return piece_type_from_id(piece_id) == "pawn"


# ─────────────────────────────────────────────────────────────────────────────
# Per-player statistics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerMatchStats:
    """Everything README §42 asks for that is scoped to one player."""

    player_id: str

    # Identity (for archetype / King-policy win rates)
    archetype: str | None = None
    active_king: str | None = None
    kings_played: list[str] = field(default_factory=list)

    # ── Card identity (README §53) ───────────────────────────────────────
    # §42 counts cards; §53 wants to "estimate card strength", which needs
    # to know *which* ones.  Every event already carries the id — only the
    # recorder was throwing it away.
    #
    # ``deck_card_ids`` is the honest denominator.  Each player's 20-card
    # deck is sampled at random from the shared pool (Game.
    # _build_deck_from_registry), so "was this card in the deck" is a
    # randomised assignment and the win rate conditioned on it estimates
    # the card's contribution rather than how often bots like playing it.
    # Conditioning on *played* instead measures the card and the bot's
    # taste in cards together, which is a different and much weaker claim.
    deck_card_ids: list[str] = field(default_factory=list)
    cards_played_by_id: dict[str, int] = field(default_factory=dict)
    cards_drawn_by_id: dict[str, int] = field(default_factory=dict)
    buildings_built_by_id: dict[str, int] = field(default_factory=dict)
    rituals_completed_by_id: dict[str, int] = field(default_factory=dict)

    # Cards
    cards_played: int = 0            # monsters + spells + traps actually resolved
    monsters_summoned: int = 0
    spells_activated: int = 0
    traps_placed: int = 0
    cards_drawn: int = 0
    cards_discarded: int = 0
    cards_remaining: int = 0         # hand + deck at snapshot time
    hand_remaining: int = 0
    deck_remaining: int = 0
    graveyard_size: int = 0

    # Recompose / Mercenary (README §9)
    recomposes_used: int = 0
    recompose_cards_returned: int = 0
    mercenaries_hired: int = 0

    # Pawn economy (README §10)
    pawns_captured: int = 0                # own Pawns lost to the opponent
    pawns_sacrificed: int = 0              # spent on Rituals / Succession costs
    pawns_used_as_vessels: int = 0
    pawns_used_for_construction: int = 0

    # Buildings (README §11–12)
    buildings_started: int = 0
    buildings_built: int = 0               # reached COMPLETE
    buildings_destroyed: int = 0           # own Buildings lost
    buildings_razed: int = 0               # enemy Buildings this player destroyed

    # Rituals (README §14–15)
    rituals_revealed: int = 0
    rituals_attempted: int = 0
    rituals_completed: int = 0

    # Kings (README §17–19)
    king_coronation_turn: int | None = None
    king_successions: int = 0

    # Check (README §21)
    checks_received: int = 0
    checks_delivered: int = 0
    first_check_received_turn: int | None = None

    # Material
    pieces_captured: int = 0               # own pieces lost (all types)
    pieces_taken: int = 0                  # enemy pieces this player captured
    monsters_lost: int = 0

    # Final Duel (README §27)
    royal_support_score: int = 0
    duel_support_items: int = 0
    duel_support_spent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Match-level statistics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchStats:
    """An immutable-by-convention snapshot of one match's telemetry."""

    game_id: str
    seed: int

    duration_seconds: float = 0.0
    turn_count: int = 0
    ply_count: int = 0               # completed player turns (TurnEnded count)
    action_count: int = 0
    illegal_action_count: int = 0

    completed: bool = False
    winner: str | None = None
    first_player: str = "white"
    end_reason: str | None = None

    # Check (match-wide)
    first_check_turn: int | None = None
    total_checks: int = 0

    # Final Duel
    final_duel_triggered: bool = False
    final_duel_trigger: str | None = None      # FinalDuelType.value
    final_duel_turn: int | None = None
    final_duel_rounds: int = 0
    final_duel_winner: str | None = None
    final_duel_attacker: str | None = None
    final_duel_defender: str | None = None

    players: dict[str, PlayerMatchStats] = field(default_factory=dict)

    # ── Derived ──────────────────────────────────────────────────────────

    @property
    def first_player_won(self) -> bool | None:
        """None for an unfinished / drawn match — excluded from the rate."""
        if self.winner is None:
            return None
        return self.winner == self.first_player

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["players"] = {pid: ps.to_dict() for pid, ps in self.players.items()}
        d["first_player_won"] = self.first_player_won
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def flat_row(self) -> dict[str, Any]:
        """
        Flatten to a single CSV-friendly row: match-level keys as-is, each
        player's keys prefixed with ``white_`` / ``black_``.
        """
        row: dict[str, Any] = {
            k: v for k, v in self.to_dict().items() if k != "players"
        }
        for pid, ps in self.players.items():
            for k, v in ps.to_dict().items():
                if k == "player_id":
                    continue
                row[f"{pid}_{k}"] = (
                    ";".join(map(str, v)) if isinstance(v, list) else v
                )
        return row


# ─────────────────────────────────────────────────────────────────────────────
# Live recorder
# ─────────────────────────────────────────────────────────────────────────────

class MatchTelemetry:
    """
    Live per-match recorder.

    Owned by ``Game``.  ``Game.execute()`` calls ``record()`` after every
    successful action and ``record_illegal()`` when one is rejected, so the
    recorder sees every action attempted *and* every event produced.

    The recorder is deliberately tolerant: an unknown event type is ignored
    rather than raising, so adding a new Event never breaks a running match.
    """

    def __init__(self, game_id: str, seed: int, enabled: bool = True) -> None:
        self.game_id = game_id
        self.seed = seed
        self.enabled = enabled

        self._start_time = time.monotonic()
        self._end_time: float | None = None

        self._players: dict[str, PlayerMatchStats] = {}
        self._action_count = 0
        self._illegal_action_count = 0
        self._ply_count = 0
        self._turn_count = 1

        self._first_check_turn: int | None = None
        self._total_checks = 0

        self._duel_triggered = False
        self._duel_trigger: str | None = None
        self._duel_turn: int | None = None
        self._duel_attacker: str | None = None
        self._duel_defender: str | None = None
        self._duel_rounds = 0
        self._duel_winner: str | None = None

        self._winner: str | None = None
        self._end_reason: str | None = None

        # BuildingDestroyed carries only an instance ID (the BuildingInstance
        # is gone from the state by then), so ownership is remembered from the
        # ConstructionStarted / BuildingCompleted event that created it.
        self._building_owner: dict[str, str] = {}

    # ── Internal ──────────────────────────────────────────────────────────

    def _player(self, player_id: str | None) -> PlayerMatchStats | None:
        if not player_id:
            return None
        ps = self._players.get(player_id)
        if ps is None:
            ps = PlayerMatchStats(player_id=player_id)
            self._players[player_id] = ps
        return ps

    @staticmethod
    def _bump(ledger: "dict[str, int]", card_id: "str | None") -> None:
        """Count one use of ``card_id``, ignoring a missing id."""
        if card_id:
            ledger[card_id] = ledger.get(card_id, 0) + 1

    # ── Public recording API ──────────────────────────────────────────────

    def record_opening(self, state: "GameState") -> None:
        """
        Snapshot what each player was dealt, before anything is played.

        Called once, from ``Game.__init__``.  The deal is the randomisation
        every card-strength number leans on, and it happens before the first
        event, so nothing downstream could reconstruct it.
        """
        if not self.enabled:
            return
        for player_id, player in state.players.items():
            ps = self._player(player_id)
            if ps is not None:
                ps.deck_card_ids = list(player.hand) + list(player.deck)

    def record_illegal(self, action: "Action", turn_number: int) -> None:
        """An action was rejected by the RulesEngine."""
        if not self.enabled:
            return
        self._illegal_action_count += 1
        # A rejected ActivateRitual is still an *attempt* (README §42
        # distinguishes "Rituals attempted" from "Rituals completed").
        self._count_ritual_attempt(action)

    def record(
        self,
        action: "Action | None",
        events: "Sequence[Event]",
        state: "GameState",
        turn_number: int | None = None,
    ) -> None:
        """
        Record one executed action and the events it produced.

        ``turn_number`` is the turn the action was issued on (captured
        BEFORE execution, since EndTurn can increment the counter).  Falls
        back to the state's current turn when omitted.
        """
        if not self.enabled:
            return

        turn = turn_number if turn_number is not None else state.turn_number
        self._turn_count = max(self._turn_count, state.turn_number)

        if action is not None:
            self._action_count += 1
            self._count_ritual_attempt(action)

        for event in events:
            self._record_event(event, state, turn)

    # ── Event dispatch ────────────────────────────────────────────────────

    def _count_ritual_attempt(self, action: "Action | None") -> None:
        from game.core.actions import ActivateRitual

        if isinstance(action, ActivateRitual):
            ps = self._player(getattr(action, "player_id", None))
            if ps is not None:
                ps.rituals_attempted += 1

    def _record_event(
        self, event: "Event", state: "GameState", turn: int
    ) -> None:
        from game.core import events as E
        from game.core.phases import RevelationState

        # ── Turn lifecycle ────────────────────────────────────────────────
        if isinstance(event, E.TurnEnded):
            self._ply_count += 1
            return

        # ── Cards ─────────────────────────────────────────────────────────
        if isinstance(event, E.CardDrawn):
            if (ps := self._player(event.player_id)) is not None:
                ps.cards_drawn += 1
                self._bump(ps.cards_drawn_by_id, event.card_id)
            return

        if isinstance(event, E.CardDiscarded):
            if (ps := self._player(event.player_id)) is not None:
                ps.cards_discarded += 1
            return

        if isinstance(event, E.MonsterSummoned):
            if (ps := self._player(event.player_id)) is not None:
                ps.monsters_summoned += 1
                ps.cards_played += 1
                self._bump(ps.cards_played_by_id, event.card_id)
                if _is_pawn(event.vessel_piece_id):
                    ps.pawns_used_as_vessels += 1
            return

        if isinstance(event, E.SpellActivated):
            if (ps := self._player(event.player_id)) is not None:
                ps.spells_activated += 1
                ps.cards_played += 1
                self._bump(ps.cards_played_by_id, event.card_id)
            return

        if isinstance(event, E.TrapPlaced):
            if (ps := self._player(event.player_id)) is not None:
                ps.traps_placed += 1
                ps.cards_played += 1
                self._bump(ps.cards_played_by_id, event.card_id)
            return

        if isinstance(event, E.MonsterDestroyed):
            if (ps := self._player(event.player_id)) is not None:
                ps.monsters_lost += 1
            return

        # ── Recompose / Mercenary ─────────────────────────────────────────
        if isinstance(event, E.RecomposeResolved):
            if (ps := self._player(event.player_id)) is not None:
                ps.recomposes_used += 1
                ps.recompose_cards_returned += len(event.returned_card_ids)
            return

        if isinstance(event, E.MercenaryPiecePlaced):
            if (ps := self._player(event.player_id)) is not None:
                ps.mercenaries_hired += 1
            return

        # ── Material ──────────────────────────────────────────────────────
        if isinstance(event, E.PieceCaptured):
            victim = self._player(event.owner)
            if victim is not None:
                victim.pieces_captured += 1
                if _is_pawn(event.piece_id):
                    victim.pawns_captured += 1
            taker = self._player(self._other(state, event.owner))
            if taker is not None:
                taker.pieces_taken += 1
            return

        # ── Buildings ─────────────────────────────────────────────────────
        if isinstance(event, E.ConstructionStarted):
            self._building_owner[event.building_instance_id] = event.player_id
            if (ps := self._player(event.player_id)) is not None:
                ps.buildings_started += 1
                if _is_pawn(event.builder_piece_id):
                    ps.pawns_used_for_construction += 1
            return

        if isinstance(event, E.BuildingCompleted):
            self._building_owner[event.building_instance_id] = event.player_id
            if (ps := self._player(event.player_id)) is not None:
                ps.buildings_built += 1
                self._bump(ps.buildings_built_by_id, event.building_card_id)
            return

        if isinstance(event, E.BuildingDestroyed):
            owner = self._building_owner.get(event.building_instance_id)
            if owner is None:
                owner = self._lookup_building_owner(state, event.building_instance_id)
            if (ps := self._player(owner)) is not None:
                ps.buildings_destroyed += 1
            # A player can destroy their OWN Building (a Succession cost —
            # README §19.1), which is not a raze.  Credit the razer only when
            # the destroyer is identifiably the other side.
            destroyer = self._owner_of_piece_id(state, event.destroyed_by)
            if destroyer is not None and owner is not None and destroyer != owner:
                if (razer := self._player(destroyer)) is not None:
                    razer.buildings_razed += 1
            return

        # ── Rituals ───────────────────────────────────────────────────────
        if isinstance(event, E.RitualRevelationChanged):
            if event.new_state == RevelationState.REVEALED:
                if (ps := self._player(event.player_id)) is not None:
                    ps.rituals_revealed += 1
            return

        if isinstance(event, E.RitualActivated):
            if (ps := self._player(event.player_id)) is not None:
                ps.rituals_completed += 1
                self._bump(ps.rituals_completed_by_id, event.ritual_id)
                ps.pawns_sacrificed += sum(
                    1 for pid in event.sacrificed_piece_ids if _is_pawn(pid)
                )
            return

        # ── Kings ─────────────────────────────────────────────────────────
        if isinstance(event, E.KingCoronated):
            if (ps := self._player(event.player_id)) is not None:
                if ps.king_coronation_turn is None:
                    ps.king_coronation_turn = turn
                ps.active_king = event.king_card_id
                ps.kings_played.append(event.king_card_id)
            return

        if isinstance(event, E.KingSuccession):
            if (ps := self._player(event.player_id)) is not None:
                ps.king_successions += 1
                ps.active_king = event.new_king_card_id
                ps.kings_played.append(event.new_king_card_id)
                if _is_pawn(event.sacrificed_piece_id):
                    ps.pawns_sacrificed += 1
            return

        # ── Check ─────────────────────────────────────────────────────────
        if isinstance(event, E.CheckDetected):
            self._total_checks += 1
            if self._first_check_turn is None:
                self._first_check_turn = turn
            if (ps := self._player(event.player_id)) is not None:
                ps.checks_received += 1
                if ps.first_check_received_turn is None:
                    ps.first_check_received_turn = turn
            if (opp := self._player(self._other(state, event.player_id))) is not None:
                opp.checks_delivered += 1
            return

        # ── Final Duel ────────────────────────────────────────────────────
        if isinstance(event, E.FinalDuelTriggered):
            self._duel_triggered = True
            self._duel_trigger = event.duel_type.value
            self._duel_turn = turn
            self._duel_attacker = event.attacker
            self._duel_defender = event.defender
            self._capture_royal_support(state)
            return

        if isinstance(event, E.DuelRoundAdvanced):
            self._duel_rounds = max(self._duel_rounds, event.round_number)
            return

        if isinstance(event, E.DuelSupportSpent):
            if (ps := self._player(event.player_id)) is not None:
                ps.duel_support_spent += 1
            return

        if isinstance(event, E.GameOver):
            self._winner = event.winner
            self._end_reason = event.reason
            if self._duel_triggered:
                self._duel_winner = event.winner
            return

    @staticmethod
    def _other(state: "GameState", player_id: str | None) -> str | None:
        if player_id is None or player_id not in state.players:
            return None
        return state.opponent_of(player_id)

    @staticmethod
    def _owner_of_piece_id(
        state: "GameState", piece_id: str | None
    ) -> str | None:
        """Canonical piece IDs are ``"<owner>-<type>-<square>"``."""
        if not piece_id:
            return None
        owner = piece_id.split("-", 1)[0]
        return owner if owner in state.players else None

    @staticmethod
    def _lookup_building_owner(
        state: "GameState", instance_id: str
    ) -> str | None:
        """
        Fallback for a Building this recorder never saw created (e.g. one
        placed directly into the state by a test fixture).
        """
        for building in state.buildings:
            if getattr(building, "id", None) == instance_id:
                return getattr(building, "owner", None)
        return None

    def _capture_royal_support(self, state: "GameState") -> None:
        """
        README §27 — the Royal Support score is computed once, when the Duel
        begins, from the board state the match produced.  Snapshot it here
        because the support lists are consumed as the Duel plays out.
        """
        duel = state.duel
        if duel is None:
            return
        for owner, items in (
            (duel.attacker, duel.attacker_support),
            (duel.defender, duel.defender_support),
        ):
            ps = self._player(owner)
            if ps is None:
                continue
            ps.duel_support_items = len(items)
            ps.royal_support_score = sum(
                getattr(item, "amount", 1) for item in items
            )

    # ── Snapshot ──────────────────────────────────────────────────────────

    def finish(self) -> None:
        """Stop the duration clock.  Idempotent."""
        if self._end_time is None:
            self._end_time = time.monotonic()

    def snapshot(self, state: "GameState") -> MatchStats:
        """
        Build a ``MatchStats`` from everything recorded so far plus the
        end-of-match resources still sitting in ``state``.

        Safe to call mid-match (produces a running snapshot).
        """
        end = self._end_time if self._end_time is not None else time.monotonic()

        players: dict[str, PlayerMatchStats] = {}
        for pid in state.players:
            ps = self._player(pid)
            assert ps is not None  # _player only returns None for a None id
            player_state = state.get_player(pid)
            ps.hand_remaining = len(player_state.hand)
            ps.deck_remaining = len(player_state.deck)
            ps.cards_remaining = ps.hand_remaining + ps.deck_remaining
            ps.graveyard_size = len(player_state.graveyard)
            ps.archetype = player_state.archetype
            if player_state.active_king is not None:
                ps.active_king = player_state.active_king
            players[pid] = ps

        # Anything recorded for a player no longer in state (shouldn't happen)
        for pid, ps in self._players.items():
            players.setdefault(pid, ps)

        duel_rounds = self._duel_rounds
        if state.duel is not None:
            duel_rounds = max(duel_rounds, state.duel.round_number)

        winner = self._winner if self._winner is not None else state.winner
        duel_winner = self._duel_winner
        if self._duel_triggered and duel_winner is None:
            duel_winner = winner

        return MatchStats(
            game_id=self.game_id,
            seed=self.seed,
            duration_seconds=round(end - self._start_time, 4),
            turn_count=max(self._turn_count, state.turn_number),
            ply_count=self._ply_count,
            action_count=self._action_count,
            illegal_action_count=self._illegal_action_count,
            completed=state.is_game_over(),
            winner=winner,
            first_player="white",
            end_reason=self._end_reason,
            first_check_turn=self._first_check_turn,
            total_checks=self._total_checks,
            final_duel_triggered=self._duel_triggered,
            final_duel_trigger=self._duel_trigger,
            final_duel_turn=self._duel_turn,
            final_duel_rounds=duel_rounds,
            final_duel_winner=duel_winner,
            final_duel_attacker=self._duel_attacker,
            final_duel_defender=self._duel_defender,
            players=players,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cross-match aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _rate(wins: int, total: int) -> float | None:
    return None if total == 0 else round(wins / total, 4)


@dataclass
class WinRecord:
    """Wins / decided-matches for one bucket (an archetype, a King, …)."""

    wins: int = 0
    matches: int = 0

    @property
    def win_rate(self) -> float | None:
        return _rate(self.wins, self.matches)


class TelemetryAggregator:
    """
    Accumulates ``MatchStats`` across a simulation run.

    The README §42 metrics that are *rates* only exist at this level:
    first-player win rate, archetype win rate, King policy win rate.
    Matches with no winner (unfinished / hard-capped) are counted in
    ``matches`` but excluded from every win-rate denominator, so an
    aborted match can never look like a loss for one side.
    """

    def __init__(self) -> None:
        self.matches: list[MatchStats] = []

    # ── Ingest ────────────────────────────────────────────────────────────

    def add(self, stats: MatchStats) -> None:
        self.matches.append(stats)

    def extend(self, many: "Iterable[MatchStats]") -> None:
        for stats in many:
            self.add(stats)

    def __len__(self) -> int:
        return len(self.matches)

    # ── Basic counts ──────────────────────────────────────────────────────

    @property
    def decided(self) -> list[MatchStats]:
        return [m for m in self.matches if m.winner is not None]

    @property
    def drawn(self) -> list[MatchStats]:
        """
        Matches that finished with no winner — stopped by one of the README
        §53 ``MatchLimits`` (repetition, no progress, turn ceiling).

        Distinct from ``unfinished``, which is a match the *harness* gave up
        on at ``max_steps``.  A draw is a result the rules produced and can
        be learned from; an unfinished match is missing data.

        ``decided`` / ``drawn`` / ``unfinished`` partition the run: every
        match is in exactly one of them.
        """
        return [m for m in self.matches if m.winner is None and m.completed]

    @property
    def unfinished(self) -> list[MatchStats]:
        """Matches abandoned before the rules produced any result."""
        return [m for m in self.matches if m.winner is None and not m.completed]

    @property
    def first_player_win_rate(self) -> float | None:
        decided = self.decided
        return _rate(sum(1 for m in decided if m.first_player_won), len(decided))

    def mean(self, attr: str) -> float | None:
        values = [
            getattr(m, attr) for m in self.matches if getattr(m, attr) is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def player_mean(self, attr: str, player_id: str | None = None) -> float | None:
        """Mean of a ``PlayerMatchStats`` field, over one side or both."""
        values: list[float] = []
        for m in self.matches:
            for pid, ps in m.players.items():
                if player_id is not None and pid != player_id:
                    continue
                value = getattr(ps, attr, None)
                if value is not None:
                    values.append(value)
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    # ── Win-rate buckets ──────────────────────────────────────────────────

    def _bucket_win_rates(self, key: str) -> dict[str, WinRecord]:
        buckets: dict[str, WinRecord] = {}
        for m in self.decided:
            for pid, ps in m.players.items():
                value = getattr(ps, key, None)
                if value is None:
                    continue
                rec = buckets.setdefault(value, WinRecord())
                rec.matches += 1
                if m.winner == pid:
                    rec.wins += 1
        return buckets

    def archetype_win_rates(self) -> dict[str, WinRecord]:
        """README §42 "archetype win rate" — keyed by King archetype."""
        return self._bucket_win_rates("archetype")

    def king_policy_win_rates(self) -> dict[str, WinRecord]:
        """README §42 "King policy win rate" — keyed by the ACTIVE King card."""
        return self._bucket_win_rates("active_king")

    def duel_trigger_counts(self) -> dict[str, int]:
        return dict(
            Counter(
                m.final_duel_trigger
                for m in self.matches
                if m.final_duel_trigger is not None
            )
        )

    # ── Reporting ─────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """A JSON-serialisable digest of the whole run."""
        decided = self.decided
        return {
            "matches": len(self.matches),
            "decided": len(decided),
            "drawn": len(self.drawn),
            "unfinished": len(self.unfinished),
            "end_reasons": dict(
                Counter(m.end_reason for m in self.matches if m.end_reason)
                .most_common()
            ),
            "first_player_win_rate": self.first_player_win_rate,
            "wins_by_player": dict(
                Counter(m.winner for m in decided)
            ),
            "mean_turn_count": self.mean("turn_count"),
            "mean_ply_count": self.mean("ply_count"),
            "mean_duration_seconds": self.mean("duration_seconds"),
            "mean_total_checks": self.mean("total_checks"),
            "mean_first_check_turn": self.mean("first_check_turn"),
            "final_duels_triggered": sum(
                1 for m in self.matches if m.final_duel_triggered
            ),
            "duel_trigger_counts": self.duel_trigger_counts(),
            "mean_final_duel_rounds": self.mean("final_duel_rounds"),
            "mean_cards_played": self.player_mean("cards_played"),
            "mean_cards_remaining": self.player_mean("cards_remaining"),
            "mean_recomposes_used": self.player_mean("recomposes_used"),
            "mean_pawns_captured": self.player_mean("pawns_captured"),
            "mean_pawns_sacrificed": self.player_mean("pawns_sacrificed"),
            "mean_pawns_used_as_vessels": self.player_mean("pawns_used_as_vessels"),
            "mean_pawns_used_for_construction": self.player_mean(
                "pawns_used_for_construction"
            ),
            "mean_buildings_built": self.player_mean("buildings_built"),
            "mean_buildings_destroyed": self.player_mean("buildings_destroyed"),
            "mean_rituals_revealed": self.player_mean("rituals_revealed"),
            "mean_rituals_attempted": self.player_mean("rituals_attempted"),
            "mean_rituals_completed": self.player_mean("rituals_completed"),
            "mean_king_coronation_turn": self.player_mean("king_coronation_turn"),
            "mean_king_successions": self.player_mean("king_successions"),
            "mean_royal_support_score": self.player_mean("royal_support_score"),
            "archetype_win_rates": {
                k: {"wins": v.wins, "matches": v.matches, "win_rate": v.win_rate}
                for k, v in sorted(self.archetype_win_rates().items())
            },
            "king_policy_win_rates": {
                k: {"wins": v.wins, "matches": v.matches, "win_rate": v.win_rate}
                for k, v in sorted(self.king_policy_win_rates().items())
            },
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.summary(), indent=indent, default=str)

    def write_csv(self, path: str) -> None:
        """One row per match — the raw data balance work actually needs."""
        rows = [m.flat_row() for m in self.matches]
        if not rows:
            _log.warning("TELEMETRY  write_csv: no matches recorded, skipping %s", path)
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        _log.info("TELEMETRY  wrote %d match rows to %s", len(rows), path)
