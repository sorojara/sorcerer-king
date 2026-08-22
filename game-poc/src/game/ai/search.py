"""
Tree Search — AI Stage 2 (README §47)
=======================================

    "Introduce tree search: my action → opponent best response → my best
     response → ... Potential techniques: minimax, alpha-beta pruning,
     iterative deepening, move ordering, transposition tables.
     This works well for deterministic visible portions of the game."

That last sentence is the whole design brief.  The deterministic, fully
visible portion of this game is the **chessboard**: every unit's position,
type, Monster identity, every Building, every Trap and every square hazard
is public (README §35).  The hidden and random parts — the opponent's hand,
deck order, sealed Rituals, concealed Kings — are Stage 3/4's problem
(belief models and sampling), and §47 explicitly says pure minimax will not
be the final answer for them.

So this module searches the board and nothing else:

    • the position is a ``BoardState`` rebuilt from the Observation
      (``board_for_search``), which adds no information a human staring at
      the board could not work out;
    • moves are the engine's own pseudo-legal move generator
      (``game.chess.movement``), so Monster movement additions, Building
      walls and hazard zones behave exactly as they do in the real game;
    • the leaf evaluation is a board-only, strictly antisymmetric subset of
      the AI Stage 1 weights (``game.ai.evaluation.EvalWeights``), so a
      search score and a HeuristicBot score are in the same units.

Legality inside the tree
------------------------
The search runs on **pseudo-legal** moves and treats capturing the King as
terminal, rather than paying for a full check-filter at every node
(``get_legal_moves`` deep-copies the board once per candidate move, which
is far too expensive to do a few thousand times per decision).  This is the
standard "king-capture" formulation and it is *safe here* because the bot
only ever plays actions from the engine's own legal list — the root moves
are already legal by construction; pseudo-legality only ever affects how
deep lines are valued.

Capturing the King does not win the game (README §22 — it triggers the
Final Duel), so ``KING_CAPTURE_VALUE`` is large but finite, and is scaled
by ply so the search prefers to get there sooner.

Information rules (README §44)
------------------------------
Everything here is derived from an Observation.  The optional ``registry``
is the public card rulebook (definitions, no match state), exactly as in
AI Stage 1.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game.ai.evaluation import (
    DEFAULT_WEIGHTS,
    EvalWeights,
    board_from_observation,
    centrality,
    chebyshev,
    piece_value,
)
from game.chess.board import BoardState
from game.chess.movement import get_pseudo_legal_moves
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import ConstructionStatus, PieceType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.observation import Observation


INF = float("inf")

# Capturing the King forces the Final Duel (README §22) rather than ending
# the match, so this dominates every material term without being infinite.
KING_CAPTURE_VALUE = 250.0

# Transposition-table bounds.
_TT_EXACT, _TT_LOWER, _TT_UPPER = 0, 1, 2

# How often the wall-clock is consulted (checking time on every node costs
# more than the nodes it saves).
_CLOCK_INTERVAL = 64

_PROMOTION_RANK = {"white": 7, "black": 0}

# Centrality is a pure function of the square, and a leaf evaluation asks for
# it once per unit — so it is tabulated once for all 64 squares.
# Piece values keyed by the enum itself — a leaf evaluation would otherwise
# pay for an attribute lookup and a string hash per unit per node.
_PIECE_VALUE: dict = {pt: piece_value(pt.value) for pt in PieceType}

_CENTRALITY: dict = {
    Position(file=f, rank=r): centrality(Position(file=f, rank=r))
    for f in range(8)
    for r in range(8)
}


# ─────────────────────────────────────────────────────────────────────────────
# Position reconstruction (public information only)
# ─────────────────────────────────────────────────────────────────────────────

def board_for_search(obs: "Observation") -> BoardState:
    """
    Rebuild the board the search will play on, from public state only.

    ``evaluation.board_from_observation`` gives the units; this adds the
    two things the *movement* rules care about and it leaves out:

      • COMPLETE Buildings, which are walls (``blocks_movement``);
      • square effects — ``walled:`` stops a sliding ray, ``frozen:`` and
        ``blocked:`` remove destinations.

    Both are public (README §11, §35 and the Observation's own
    ``square_effects`` doc), so the reconstruction stays inside §44.
    """
    board = board_from_observation(obs)

    for building in obs.board.building_locations:
        pos = getattr(building, "position", None)
        if pos is None or pos not in board.squares:
            continue
        square = board.squares[pos]
        square.building_id = getattr(building, "id", None)
        if getattr(building, "status", None) == ConstructionStatus.COMPLETE:
            square.complete_building_owner = getattr(building, "owner", None)

    for effect in obs.board.square_effects:
        pos = effect.position
        if pos not in board.squares:
            continue
        # Re-form the engine's own tag layout ("type:duration:owner"); the
        # movement code only ever reads the prefix and the owner field.
        board.squares[pos].temporary_effects.append(
            f"{effect.effect_type}:{effect.duration_turns}:{effect.owner or '-'}"
        )

    return board


@dataclass(frozen=True)
class Hazards:
    """
    Which squares are dangerous *to whom*, frozen once per decision.

    Traps (README §7.1) and hostile square effects are public and static
    for the length of one search, so they are precomputed here rather than
    re-derived at every leaf.  Both maps are keyed by the player they
    *hurt*: ``trapped["white"]`` is every square inside one of black's Trap
    radii, ``hazardous["white"]`` every square carrying one of black's zone
    effects.
    """

    trapped: dict[str, frozenset] = field(default_factory=dict)
    hazardous: dict[str, frozenset] = field(default_factory=dict)

    @staticmethod
    def from_observation(obs: "Observation") -> "Hazards":
        trapped: dict[str, set] = {"white": set(), "black": set()}
        hazardous: dict[str, set] = {"white": set(), "black": set()}

        for trap in obs.board.trap_locations:
            owner = getattr(trap, "owner", None)
            pos = getattr(trap, "position", None)
            if owner is None or pos is None:
                continue
            radius = getattr(trap, "radius", 1)
            victim = "black" if owner == "white" else "white"
            for file in range(max(0, pos.file - radius), min(7, pos.file + radius) + 1):
                for rank in range(max(0, pos.rank - radius), min(7, pos.rank + radius) + 1):
                    trapped[victim].add(Position(file=file, rank=rank))

        for effect in obs.board.square_effects:
            owner = effect.owner
            if owner is None:
                # An ownerless hazard is bad for whoever stands on it.
                hazardous["white"].add(effect.position)
                hazardous["black"].add(effect.position)
                continue
            victim = "black" if owner == "white" else "white"
            hazardous[victim].add(effect.position)

        return Hazards(
            trapped={k: frozenset(v) for k, v in trapped.items()},
            hazardous={k: frozenset(v) for k, v in hazardous.items()},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Limits and instrumentation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SearchLimits:
    """
    The three budgets that keep a turn-based UI responsive.

    ``max_depth``        — plies, counting the bot's own move as one.
    ``max_nodes``        — hard ceiling on nodes visited per decision.
    ``max_seconds``      — wall-clock ceiling per decision.
    ``quiescence_depth`` — extra capture-only plies past ``max_depth``, so
                           the search is not fooled by a recapture that
                           falls one ply beyond the horizon.
    """

    max_depth: int = 3
    max_nodes: int = 2500
    max_seconds: float = 0.75
    quiescence_depth: int = 2


@dataclass
class SearchStats:
    """What the last decision cost — for telemetry, tuning and tests."""

    depth: int = 0          # deepest iteration that COMPLETED
    nodes: int = 0
    elapsed: float = 0.0
    aborted: bool = False   # budget ran out mid-iteration
    tt_hits: int = 0
    root_moves: int = 0
    best_score: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Searcher
# ─────────────────────────────────────────────────────────────────────────────

class Searcher:
    """
    Negamax + alpha-beta over one reconstructed board.

    The board is mutated in place by ``make``/``unmake`` (copying 64 squares
    per node is the single most expensive thing a naive implementation can
    do), so a Searcher owns its board and is not thread-safe.

    All public values are returned from ``me``'s point of view: positive is
    good for the bot, whoever is to move.
    """

    def __init__(
        self,
        board: BoardState,
        me: str,
        registry: "CardRegistry | None" = None,
        weights: EvalWeights = DEFAULT_WEIGHTS,
        limits: SearchLimits = SearchLimits(),
        hazards: "Hazards | None" = None,
    ) -> None:
        self.board = board
        self.me = me
        self.opponent = "black" if me == "white" else "white"
        self.registry = registry
        self.weights = weights
        self.limits = limits
        hazards = hazards or Hazards()
        # Normalise: the leaf evaluation indexes these by owner on every
        # unit, so both sides must always be present.
        self.hazards = Hazards(
            trapped={
                side: hazards.trapped.get(side, frozenset())
                for side in ("white", "black")
            },
            hazardous={
                side: hazards.hazardous.get(side, frozenset())
                for side in ("white", "black")
            },
        )
        # Most positions carry no Traps and no zone hazards at all; skipping
        # the per-unit set lookups when there is nothing to look up is worth
        # a few per cent of the whole search.
        self._any_hazard = any(self.hazards.trapped.values()) or \
            any(self.hazards.hazardous.values())

        # Occupied squares only, kept in step with the board by make/unmake.
        # Every node walks the pieces at least twice (generate, evaluate);
        # walking 64 SquareStates each time is the difference between a
        # depth-2 and a depth-4 search.
        self._units: dict[Position, UnitInstance] = dict(board.all_units())

        # The engine's move generator consults the registry for Monster
        # movement additions and for enemy movement_restriction auras — and
        # the aura check re-scans the whole board on EVERY call.  None of it
        # can do anything on a board with no Monsters on it, so a
        # Monster-free position (the common case) searches registry-free and
        # several times deeper for the same budget.  Monsters can leave the
        # board mid-search but never arrive, so this is decided once.
        self._move_registry = (
            registry
            if registry is not None
            and any(u.monster_id is not None for u in self._units.values())
            else None
        )

        self.stats = SearchStats()
        # A Searcher used without start() (a one-off evaluate(), say) still
        # has a coherent, unlimited budget.
        self._started = time.monotonic()
        self._deadline = INF
        self._monster_values: dict[str, float] = {}
        self._tt: dict[tuple, tuple[int, float, int]] = {}

    # ── Budget ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the budget for one decision."""
        self.stats = SearchStats()
        self._started = time.monotonic()
        self._deadline = self._started + self.limits.max_seconds
        self._tt.clear()

    def out_of_budget(self) -> bool:
        if self.stats.nodes >= self.limits.max_nodes:
            self.stats.aborted = True
            return True
        if self.stats.nodes % _CLOCK_INTERVAL == 0 and time.monotonic() >= self._deadline:
            self.stats.aborted = True
            return True
        return False

    def finish(self) -> None:
        self.stats.elapsed = time.monotonic() - self._started

    # ── Evaluation (leaf) ─────────────────────────────────────────────────

    def _monster_value(self, monster_id: str) -> float:
        cached = self._monster_values.get(monster_id)
        if cached is not None:
            return cached
        w = self.weights
        value = w.monster_unit
        if self.registry is not None:
            try:
                card = self.registry.get(monster_id)
            except (KeyError, AttributeError):
                card = None
            if card is not None:
                value += w.monster_effect * len(getattr(card, "effects", ()))
                if getattr(card, "ritual_only", False):
                    value += w.ritual_monster
        self._monster_values[monster_id] = value
        return value

    def evaluate(self) -> float:
        """
        Board-only static evaluation, from ``me``'s point of view.

        A strict subset of the README §46 categories — the ones the board
        alone can answer: material, Monster value, Pawn economy, centre
        control, King proximity on both sides, and standing in an enemy
        Trap or hazard.  The card-system categories (Rituals, Buildings,
        hand quality, Territory) are constant across a chess search, so
        leaving them out changes no ranking and costs nothing to omit.

        Antisymmetric by construction: the same position evaluated for the
        other side is exactly this value negated.  Negamax depends on it.
        """
        w = self.weights
        me = self.me
        units = self._units

        my_king = None
        enemy_king = None
        for pos, unit in units.items():
            if unit.piece.piece_type is not PieceType.KING:
                continue
            if unit.owner == me:
                my_king = pos
            else:
                enemy_king = pos

        # A King is only ever missing at a leaf reached through a line the
        # king-capture cut-off did not stop (a promotion-blocked corner case
        # or an aborted search); score it decisively either way.
        if my_king is None and enemy_king is not None:
            return -KING_CAPTURE_VALUE
        if enemy_king is None and my_king is not None:
            return KING_CAPTURE_VALUE

        trapped = self.hazards.trapped
        hazardous = self.hazards.hazardous

        score = 0.0
        for pos, unit in units.items():
            owner = unit.owner
            mine = owner == me
            sign = 1.0 if mine else -1.0
            piece_type = unit.piece.piece_type

            if piece_type is not PieceType.KING:
                score += sign * w.material * _PIECE_VALUE[piece_type]

            if unit.monster_id is not None:
                score += sign * self._monster_value(unit.monster_id)

            if piece_type is PieceType.PAWN:
                advance = pos.rank - 1 if owner == "white" else 6 - pos.rank
                score += sign * (w.pawn_base + w.pawn_advance * max(advance, 0))

            score += sign * w.board_control_centrality * _CENTRALITY[pos]

            if enemy_king is not None and mine and piece_type is not PieceType.KING:
                if chebyshev(pos, enemy_king) <= w.enemy_king_radius:
                    score += w.enemy_king_attacker
            if my_king is not None and not mine and piece_type is not PieceType.KING:
                if chebyshev(pos, my_king) <= w.king_safety_radius:
                    score -= w.king_safety_attacker

            if self._any_hazard:
                if pos in trapped[owner]:      # standing inside an enemy Trap
                    score -= sign * w.trap_penalty
                if pos in hazardous[owner]:
                    score -= sign * w.hazard_penalty

        return score

    # ── Move generation ───────────────────────────────────────────────────

    def moves(self, player: str, captures_only: bool = False) -> list[tuple[float, Position, Position]]:
        """
        Ordered pseudo-legal moves for ``player`` as ``(order, src, dst)``.

        Ordering is MVV-LVA (most valuable victim, least valuable attacker)
        with a promotion bonus and a small centrality tiebreak — the single
        cheapest thing that makes alpha-beta actually prune.
        """
        out: list[tuple[float, Position, Position]] = []
        board = self.board
        for pos, unit in self._units.items():
            if unit.owner != player:
                continue
            try:
                targets = get_pseudo_legal_moves(
                    board, pos, unit, registry=self._move_registry
                )
            except Exception:  # noqa: BLE001 - never let search break play
                continue
            attacker = _PIECE_VALUE[unit.piece.piece_type]
            is_pawn = unit.piece.piece_type is PieceType.PAWN
            promo_rank = _PROMOTION_RANK.get(player)
            for target in targets:
                victim = board.get_unit(target)
                if victim is None:
                    if captures_only:
                        continue
                    order = _CENTRALITY[target]
                else:
                    order = 100.0 + 10.0 * _PIECE_VALUE[victim.piece.piece_type] - attacker
                if is_pawn and target.rank == promo_rank:
                    order += 80.0
                out.append((order, pos, target))
        out.sort(key=lambda entry: entry[0], reverse=True)
        return out

    def king_capture(self, player: str, moves: list[tuple[float, Position, Position]]) -> bool:
        """True if any of ``moves`` takes the enemy King."""
        board = self.board
        for _order, _src, dst in moves:
            victim = board.get_unit(dst)
            if victim is not None and victim.owner != player and \
                    victim.piece.piece_type is PieceType.KING:
                return True
        return False

    # ── make / unmake ─────────────────────────────────────────────────────

    def make(self, src: Position, dst: Position):
        """
        Apply a move in place and return the undo token.

        Promotion is auto-Queen: it is what the bots already choose in the
        real game (ui/pygame_app.py's AI promotion path), and an underpromotion
        search would cost four times the nodes for a case the PoC never needs.
        """
        squares = self.board.squares
        units = self._units
        source_square = squares[src]
        target_square = squares[dst]
        unit = source_square.unit
        captured = target_square.unit
        source_square.unit = None
        units.pop(src, None)

        promoted_from = None
        if (
            unit is not None
            and unit.piece.piece_type is PieceType.PAWN
            and unit.monster_id is None
            and dst.rank == _PROMOTION_RANK.get(unit.owner)
        ):
            promoted_from = unit
            queen = UnitInstance(
                piece=ChessPiece(
                    id=unit.piece.id, owner=unit.owner, piece_type=PieceType.QUEEN
                ),
                monster_id=None,
            )
            queen.statuses = list(unit.statuses)
            target_square.unit = queen
            units[dst] = queen
        else:
            target_square.unit = unit
            units[dst] = unit

        return captured, promoted_from

    def unmake(self, src: Position, dst: Position, undo) -> None:
        captured, promoted_from = undo
        squares = self.board.squares
        units = self._units
        target_square = squares[dst]
        unit = promoted_from if promoted_from is not None else target_square.unit
        squares[src].unit = unit
        units[src] = unit
        target_square.unit = captured
        if captured is None:
            units.pop(dst, None)
        else:
            units[dst] = captured

    def make_castle(self, player: str, side: str):
        """Castling as a compound move; returns an undo token for ``unmake_castle``."""
        rank = 0 if player == "white" else 7
        king_from = Position(file=4, rank=rank)
        if side == "kingside":
            king_to, rook_from, rook_to = (
                Position(file=6, rank=rank),
                Position(file=7, rank=rank),
                Position(file=5, rank=rank),
            )
        else:
            king_to, rook_from, rook_to = (
                Position(file=2, rank=rank),
                Position(file=0, rank=rank),
                Position(file=3, rank=rank),
            )
        squares = self.board.squares
        units = self._units
        king = squares[king_from].unit
        rook = squares[rook_from].unit
        squares[king_from].unit = None
        squares[rook_from].unit = None
        squares[king_to].unit = king
        squares[rook_to].unit = rook
        units.pop(king_from, None)
        units.pop(rook_from, None)
        units[king_to] = king
        units[rook_to] = rook
        return king_from, king_to, rook_from, rook_to, king, rook

    def unmake_castle(self, undo) -> None:
        king_from, king_to, rook_from, rook_to, king, rook = undo
        squares = self.board.squares
        units = self._units
        squares[king_to].unit = None
        squares[rook_to].unit = None
        squares[king_from].unit = king
        squares[rook_from].unit = rook
        units.pop(king_to, None)
        units.pop(rook_to, None)
        units[king_from] = king
        units[rook_from] = rook

    # ── Search ────────────────────────────────────────────────────────────

    def value_to_move(
        self,
        player: str,
        depth: int,
        alpha: float = -INF,
        beta: float = INF,
    ) -> float:
        """
        Value of the current board when ``player`` is to move, from ``me``'s
        point of view.  ``alpha``/``beta`` are in ``me``'s frame too.
        """
        if player == self.me:
            return self._negamax(player, depth, alpha, beta, ply=0)
        # The opponent's frame is this one negated, so the window flips.
        return -self._negamax(player, depth, -beta, -alpha, ply=0)

    def _negamax(
        self,
        player: str,
        depth: int,
        alpha: float,
        beta: float,
        ply: int,
    ) -> float:
        """Standard negamax with alpha-beta; value from ``player``'s POV."""
        self.stats.nodes += 1
        sign = 1.0 if player == self.me else -1.0

        if self.out_of_budget():
            return sign * self.evaluate()

        if depth <= 0:
            return self._quiesce(player, alpha, beta, self.limits.quiescence_depth)

        key = None
        if depth >= 2:
            key = (self._position_key(), player, depth)
            entry = self._tt.get(key)
            if entry is not None:
                stored_depth, value, flag = entry
                if stored_depth >= depth:
                    if flag == _TT_EXACT:
                        self.stats.tt_hits += 1
                        return value
                    if flag == _TT_LOWER and value >= beta:
                        self.stats.tt_hits += 1
                        return value
                    if flag == _TT_UPPER and value <= alpha:
                        self.stats.tt_hits += 1
                        return value

        moves = self.moves(player)
        if not moves:
            # No move at all — the side is buried (every unit immobilised or
            # walled in).  Score the position as it stands.
            return sign * self.evaluate()
        if self.king_capture(player, moves):
            # Taking the King forces the Final Duel (README §22): huge, and
            # better the sooner it happens.
            return KING_CAPTURE_VALUE - ply

        opponent = "black" if player == "white" else "white"
        original_alpha = alpha
        best = -INF
        for _order, src, dst in moves:
            undo = self.make(src, dst)
            value = -self._negamax(opponent, depth - 1, -beta, -alpha, ply + 1)
            self.unmake(src, dst, undo)

            if value > best:
                best = value
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break               # fail-soft beta cut-off
            if self.stats.aborted:
                break

        if key is not None and not self.stats.aborted:
            if best <= original_alpha:
                flag = _TT_UPPER
            elif best >= beta:
                flag = _TT_LOWER
            else:
                flag = _TT_EXACT
            self._tt[key] = (depth, best, flag)

        return best

    def _quiesce(self, player: str, alpha: float, beta: float, depth: int) -> float:
        """
        Capture-only extension.  Without it the search happily hangs a Queen
        on the last ply because the recapture is one move past the horizon.
        """
        self.stats.nodes += 1
        sign = 1.0 if player == self.me else -1.0
        stand_pat = sign * self.evaluate()

        if depth <= 0 or self.out_of_budget():
            return stand_pat
        if stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat

        captures = self.moves(player, captures_only=True)
        if not captures:
            return stand_pat
        if self.king_capture(player, captures):
            return KING_CAPTURE_VALUE

        opponent = "black" if player == "white" else "white"
        best = stand_pat
        for _order, src, dst in captures:
            undo = self.make(src, dst)
            value = -self._quiesce(opponent, -beta, -alpha, depth - 1)
            self.unmake(src, dst, undo)
            if value > best:
                best = value
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
            if self.stats.aborted:
                break
        return best

    # ── Transposition key ─────────────────────────────────────────────────

    def _position_key(self) -> tuple:
        """
        A hashable snapshot of the board.

        Only what the search can change matters: where each unit stands, who
        owns it, what it is, and whether it carries a Monster.  Buildings,
        Traps and hazards are constant for the whole search and so are not
        part of the key.
        """
        return tuple(
            sorted(
                (
                    pos.file * 8 + pos.rank,
                    unit.owner,
                    unit.piece.piece_type.value,
                    unit.monster_id or "",
                )
                for pos, unit in self._units.items()
            )
        )
