"""
chess/movement.py — Full legal chess movement (Stage 1)
=========================================================

This module owns all standard-chess movement logic.

The Stage 0 functions ``get_pseudo_legal_moves`` and ``get_legal_moves``
lived inside ``core/rules.py``.  Stage 1 promotes them here so the rules
engine stays thin and the chess layer is independently testable.

What this module adds over Stage 0:
    • En passant pawn capture
    • Kingside castling (O-O)
    • Queenside castling (O-O-O)

Architecture (from phase0.md §7):
    "Define movement independently from cards."

    get_legal_moves(board, pos, unit, game_state) resolves:

        Base vessel movement
              +
        En passant (pawn only)
              +
        Castling (king only)
              =
        Pseudo-legal moves
              –
        Moves that leave own King in check
              =
        Final legal moves

    Monster modifier layer (Stage 5) will sit on top of this.

Public API:
    get_pseudo_legal_moves(board, pos, unit, en_passant_target=None) → list[Position]
    get_legal_moves(board, pos, unit, en_passant_target=None,
                    castling_rights=None, player_id="") → list[Position]
    get_non_pawn_movement_squares(board, player_id, registry=None) → set[Position]
    can_castle_kingside(board, player_id, castling_rights) → bool
    can_castle_queenside(board, player_id, castling_rights) → bool
    is_in_check(board, player_id) → bool
    has_any_legal_move(board, player_id, en_passant_target, castling_rights) → bool
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from game.chess.board import BoardState
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.phases import PieceType

if TYPE_CHECKING:
    from game.chess.chess_state import CastlingRights


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ray_moves(
    board: BoardState,
    pos: Position,
    owner: str,
    deltas: list[tuple[int, int]],
    sliding: bool,
) -> list[Position]:
    """
    Enumerate squares reachable by moving in the given direction vectors.

    ``sliding=True``  → keep sliding until blocked (Queen, Rook, Bishop).
    ``sliding=False`` → one step per direction (King, Knight).
    """
    targets: list[Position] = []
    for df, dr in deltas:
        f, r = pos.file + df, pos.rank + dr
        while 0 <= f <= 7 and 0 <= r <= 7:
            candidate = Position(f, r)
            occupant = board.get_unit(candidate)
            if occupant is None:
                targets.append(candidate)
            elif occupant.owner != owner:
                targets.append(candidate)   # capture — stop ray
                break
            else:
                break                        # blocked by own piece
            if not sliding:
                break
            f += df
            r += dr
    return targets


# ─────────────────────────────────────────────────────────────────────────────
# Public: pseudo-legal moves (no check filtering)
# ─────────────────────────────────────────────────────────────────────────────

def get_pseudo_legal_moves(
    board: BoardState,
    pos: Position,
    unit: UnitInstance,
    en_passant_target: Position | None = None,
    registry: "object | None" = None,
) -> list[Position]:
    """
    Return all candidate destination squares for ``unit`` at ``pos``.

    Includes en passant for pawns.
    Does NOT include castling (handled separately in get_legal_moves).
    Does NOT filter moves that leave the King in check.

    Stage 5: applies monster movement additions (alter_movement / add_leap)
    on top of the base vessel movement pattern.

    Stage 6: an "immobilized:N" unit (pit_trap, ward_of_binding) has zero
    legal moves until the status expires — it may still be captured.
    """
    from game.cards.card import MonsterCard

    if any(s.startswith("immobilized:") for s in unit.statuses):
        return []

    owner = unit.owner
    pt = unit.piece.piece_type
    moves: list[Position] = []

    # ── Sliding / step pieces ─────────────────────────────────────────────

    if pt == PieceType.KING:
        moves = _ray_moves(board, pos, owner, [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),  (1, 0),  (1, 1),
        ], sliding=False)

    elif pt == PieceType.QUEEN:
        moves = _ray_moves(board, pos, owner, [
            (0, 1), (0, -1), (1, 0), (-1, 0),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        ], sliding=True)

    elif pt == PieceType.ROOK:
        moves = _ray_moves(board, pos, owner, [
            (0, 1), (0, -1), (1, 0), (-1, 0),
        ], sliding=True)

    elif pt == PieceType.BISHOP:
        moves = _ray_moves(board, pos, owner, [
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        ], sliding=True)

    elif pt == PieceType.KNIGHT:
        moves = _ray_moves(board, pos, owner, [
            (2, 1), (2, -1), (-2, 1), (-2, -1),
            (1, 2), (1, -2), (-1, 2), (-1, -2),
        ], sliding=False)

    # ── Pawn (most complex: push, double-push, diagonal capture, en passant) ─

    elif pt == PieceType.PAWN:
        direction = 1 if owner == "white" else -1
        start_rank = 1 if owner == "white" else 6

        # Single forward push — guard bounds before constructing Position
        fwd_rank = pos.rank + direction
        if 0 <= fwd_rank <= 7:
            fwd = Position(pos.file, fwd_rank)
            if board.get_unit(fwd) is None:
                moves.append(fwd)
                # Double push only from starting rank and only if single-push clear
                if pos.rank == start_rank:
                    fwd2_rank = pos.rank + 2 * direction
                    if 0 <= fwd2_rank <= 7:
                        fwd2 = Position(pos.file, fwd2_rank)
                        if board.get_unit(fwd2) is None:
                            moves.append(fwd2)

        # Diagonal captures (normal + en passant)
        for df in (-1, 1):
            cf = pos.file + df
            cr = pos.rank + direction
            if not (0 <= cf <= 7 and 0 <= cr <= 7):
                continue
            cap_sq = Position(cf, cr)
            occ = board.get_unit(cap_sq)

            # Normal diagonal capture
            if occ is not None and occ.owner != owner:
                moves.append(cap_sq)

            # En passant capture
            elif (
                occ is None
                and en_passant_target is not None
                and cap_sq == en_passant_target
            ):
                moves.append(cap_sq)

    # ── Stage 5: Monster movement additions ──────────────────────────────
    if unit.monster_id is not None and registry is not None:
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            card = None
        if card is not None and isinstance(card, MonsterCard):
            from game.mechanics.monsters import get_movement_additions
            for df, dr in get_movement_additions(unit, card):
                f, r = pos.file + df, pos.rank + dr
                if 0 <= f <= 7 and 0 <= r <= 7:
                    candidate = Position(f, r)
                    occupant = board.get_unit(candidate)
                    # Can land on empty square or capture enemy
                    if occupant is None or occupant.owner != owner:
                        if candidate not in moves:
                            moves.append(candidate)

    # ── Stage 5/6: frozen / blocked squares cannot be entered ────────────
    # "blocked" is veil_of_stillness's zone (Stage 6) — same square-effect
    # mechanism as "frozen", just a different spatial extent (a whole file
    # via shape: column rather than one landing square).
    moves = [
        m for m in moves
        if not any(
            eff.startswith("frozen:") or eff.startswith("blocked:")
            for eff in board.get_square(m).temporary_effects
        )
    ]

    # ── Stage 5: enemy movement_restriction aura (astral_binder) ─────────
    # Enemy monsters may cap how far this unit can move per action.
    if registry is not None:
        from game.mechanics.monsters import get_movement_cap
        cap = get_movement_cap(board, pos, owner, registry)
        if cap is not None:
            moves = [
                m for m in moves
                if abs(m.file - pos.file) <= cap and abs(m.rank - pos.rank) <= cap
            ]

    return moves


# ─────────────────────────────────────────────────────────────────────────────
# Public: Spell targeting — non-Pawn zone of influence
# ─────────────────────────────────────────────────────────────────────────────

def get_non_pawn_movement_squares(
    board: BoardState,
    player_id: str,
    registry: "object | None" = None,
) -> set[Position]:
    """
    Squares any of ``player_id``'s own non-Pawn pieces could move into right
    now (pseudo-legal: respects blockers, monster movement additions, frozen/
    blocked squares, and movement caps — ignores check/whose-turn-it-is).

    A Spell that targets a board square (target_type "position"/"zone") may
    only be aimed at one of these squares — it reflects the caster's current
    zone of tactical influence rather than the whole board.
    """
    squares: set[Position] = set()
    for pos, unit in board.all_units_for(player_id):
        if unit.piece.piece_type == PieceType.PAWN:
            continue
        squares.update(get_pseudo_legal_moves(board, pos, unit, registry=registry))
    return squares


# ─────────────────────────────────────────────────────────────────────────────
# Public: check detection
# ─────────────────────────────────────────────────────────────────────────────

def is_in_check(board: BoardState, player_id: str) -> bool:
    """
    Return True if ``player_id``'s King is attacked by any enemy unit.

    Only current board threats count — Spells in hand do NOT create check.
    Does not account for castling (castling-through-check handled in movement).

    Reference: phase0.md §8.
    """
    king_result = board.find_king(player_id)
    if king_result is None:
        return False  # King absent — Final Duel logic handles this

    king_pos, _ = king_result
    opponent = "black" if player_id == "white" else "white"

    for pos, unit in board.all_units_for(opponent):
        # Use en_passant_target=None for check detection
        # (en passant cannot create discovered check in standard chess)
        if king_pos in get_pseudo_legal_moves(board, pos, unit, en_passant_target=None):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public: castling legality helpers
# ─────────────────────────────────────────────────────────────────────────────

def _king_start(player_id: str) -> Position:
    rank = 0 if player_id == "white" else 7
    return Position(4, rank)  # e1 / e8


def _squares_empty(board: BoardState, squares: list[Position]) -> bool:
    return all(board.get_unit(sq) is None for sq in squares)


def _squares_safe(
    board: BoardState,
    player_id: str,
    squares: list[Position],
) -> bool:
    """Return True iff none of ``squares`` are attacked by the opponent."""
    opponent = "black" if player_id == "white" else "white"
    attacked: set[Position] = set()
    for pos, unit in board.all_units_for(opponent):
        attacked.update(get_pseudo_legal_moves(board, pos, unit))
    return not any(sq in attacked for sq in squares)


def can_castle_kingside(
    board: BoardState,
    player_id: str,
    castling_rights: "CastlingRights",
) -> bool:
    """
    Kingside castling (O-O) is legal when:
        1. Castling right has not been revoked.
        2. The King is present at its starting square.
        3. The h-file Rook is present at its starting square.
        4. f- and g-file squares on the back rank are empty.
        5. King is not currently in check.
        6. King does not pass through or land on an attacked square.
    """
    if not castling_rights.kingside:
        return False
    rank = 0 if player_id == "white" else 7
    king_pos = _king_start(player_id)
    rook_sq = Position(7, rank)  # h1/h8
    f_sq = Position(5, rank)     # f1/f8
    g_sq = Position(6, rank)     # g1/g8

    # King must be in its starting position
    king_unit = board.get_unit(king_pos)
    if king_unit is None or king_unit.piece.piece_type.value != "king":
        return False
    # Rook must be present on the h-file
    rook_unit = board.get_unit(rook_sq)
    if (
        rook_unit is None
        or rook_unit.piece.piece_type.value != "rook"
        or rook_unit.owner != player_id
    ):
        return False
    # Path between King and Rook must be clear
    if not _squares_empty(board, [f_sq, g_sq]):
        return False
    # King must not be in check AND must not cross an attacked square
    return _squares_safe(board, player_id, [king_pos, f_sq, g_sq])


def can_castle_queenside(
    board: BoardState,
    player_id: str,
    castling_rights: "CastlingRights",
) -> bool:
    """
    Queenside castling (O-O-O) is legal when:
        1. Castling right has not been revoked.
        2. The King is present at its starting square.
        3. The a-file Rook is present at its starting square.
        4. b-, c-, and d-file squares on the back rank are empty.
        5. King does not pass through or land on an attacked square.
    """
    if not castling_rights.queenside:
        return False
    rank = 0 if player_id == "white" else 7
    king_pos = _king_start(player_id)
    rook_sq = Position(0, rank)  # a1/a8
    b_sq = Position(1, rank)     # b1/b8 — rook path, not king path
    c_sq = Position(2, rank)     # c1/c8
    d_sq = Position(3, rank)     # d1/d8

    # King must be in its starting position
    king_unit = board.get_unit(king_pos)
    if king_unit is None or king_unit.piece.piece_type.value != "king":
        return False
    # Rook must be present on the a-file
    rook_unit = board.get_unit(rook_sq)
    if (
        rook_unit is None
        or rook_unit.piece.piece_type.value != "rook"
        or rook_unit.owner != player_id
    ):
        return False
    # Path must be clear
    if not _squares_empty(board, [b_sq, c_sq, d_sq]):
        return False
    # King crosses d- and c-squares (and starts on e)
    return _squares_safe(board, player_id, [king_pos, d_sq, c_sq])


# ─────────────────────────────────────────────────────────────────────────────
# Public: fully legal moves (check-filtered + castling)
# ─────────────────────────────────────────────────────────────────────────────

def get_legal_moves(
    board: BoardState,
    pos: Position,
    unit: UnitInstance,
    en_passant_target: Position | None = None,
    castling_rights: "CastlingRights | None" = None,
    player_id: str = "",
    registry: "object | None" = None,
) -> list[Position]:
    """
    Return all fully-legal destination squares for ``unit`` at ``pos``.

    Filters pseudo-legal moves that leave the own King in check.
    Does NOT include the castling destinations (castle is a separate action).

    Stage 5: ``registry`` is forwarded to get_pseudo_legal_moves so monster
    movement additions participate in check filtering correctly.
    """
    legal: list[Position] = []
    for target in get_pseudo_legal_moves(board, pos, unit, en_passant_target, registry=registry):
        sim = deepcopy(board)

        # En passant: also remove the captured pawn from its real square
        if (
            unit.piece.piece_type == PieceType.PAWN
            and en_passant_target is not None
            and target == en_passant_target
            and board.get_unit(target) is None
        ):
            direction = 1 if unit.owner == "white" else -1
            captured_sq = Position(target.file, target.rank - direction)
            sim.remove_unit(captured_sq)

        sim.move_unit(pos, target)
        if not is_in_check(sim, unit.owner):
            legal.append(target)

    return legal


# ─────────────────────────────────────────────────────────────────────────────
# Public: has_any_legal_move (for checkmate / stalemate detection)
# ─────────────────────────────────────────────────────────────────────────────

def has_any_legal_move(
    board: BoardState,
    player_id: str,
    en_passant_target: Position | None = None,
    castling_rights: "CastlingRights | None" = None,
    registry: "object | None" = None,
) -> bool:
    """
    Return True if ``player_id`` has at least one fully legal chess move
    (including castling).
    """
    from game.chess.chess_state import CastlingRights as CR

    cr = castling_rights or CR()

    for pos, unit in board.all_units_for(player_id):
        if get_legal_moves(board, pos, unit, en_passant_target, registry=registry):
            return True

    # Also check castling
    if can_castle_kingside(board, player_id, cr):
        return True
    if can_castle_queenside(board, player_id, cr):
        return True

    return False
