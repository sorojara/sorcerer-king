"""
ui/state_io.py — GameState JSON serialization / deserialization (Stage 3)
=========================================================================

Provides two public functions:

    export_state(game: Game, path: str | Path) -> None
        Serialize the complete game state (board, players, phase, RNG) to
        a human-readable JSON file.

    import_state(path: str | Path) -> Game
        Deserialize a previously exported JSON file back into a live Game.

Design notes:
  • The event_log is NOT serialized (it is replay metadata, not needed for
    resuming play).
  • The RNG state IS serialized so randomness continues deterministically.
  • All enums are stored as their .value strings; restored by name lookup.
  • Position is stored as {"file": f, "rank": r}.
  • The format is intentionally verbose / human-readable for debuggability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from game.chess.board import BoardState, SquareState
from game.chess.chess_state import CastlingRights
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.game import Game
from game.core.phases import (
    ConstructionStatus,
    DecisionType,
    FinalDuelType,
    KingCardStatus,
    Phase,
    PieceType,
    RevelationState,
)
from game.core.rng import DeterministicRNG
from game.core.state import (
    BuildingInstance,
    BuildingPoolEntry,
    DuelState,
    GameState,
    KingCardState,
    PendingDecision,
    PlayerState,
    RitualState,
    TrapInstance,
)

# ── Helpers ────────────────────────────────────────────────────────────────

def _pos_to_d(pos: Position) -> dict:
    return {"file": pos.file, "rank": pos.rank}

def _pos_from_d(d: dict) -> Position:
    return Position(d["file"], d["rank"])

def _pos_or_none_to_d(pos: Position | None) -> dict | None:
    return _pos_to_d(pos) if pos is not None else None

def _pos_or_none_from_d(d: dict | None) -> Position | None:
    return _pos_from_d(d) if d is not None else None


# ── Board serialization ────────────────────────────────────────────────────

def _unit_to_d(unit: UnitInstance) -> dict:
    return {
        "piece_id": unit.piece.id,
        "piece_owner": unit.piece.owner,
        "piece_type": unit.piece.piece_type.value,
        "monster_id": unit.monster_id,
        "statuses": list(unit.statuses),
        "builder_available": unit.builder_available,
    }

def _unit_from_d(d: dict) -> UnitInstance:
    piece = ChessPiece(
        id=d["piece_id"],
        owner=d["piece_owner"],
        piece_type=PieceType(d["piece_type"]),
    )
    unit = UnitInstance(piece=piece, monster_id=d.get("monster_id"))
    unit.statuses = d.get("statuses", [])
    unit.builder_available = d.get("builder_available", piece.piece_type == PieceType.PAWN)
    return unit

def _board_to_d(board: BoardState) -> list[dict]:
    """Serialize only occupied squares (empty squares are reconstructed)."""
    rows = []
    for pos, sq in board.squares.items():
        if sq.unit is not None or sq.trap_ids or sq.building_id or sq.temporary_effects:
            rows.append({
                "pos": _pos_to_d(pos),
                "unit": _unit_to_d(sq.unit) if sq.unit else None,
                "trap_ids": list(sq.trap_ids),
                "building_id": sq.building_id,
                "territory_owner": sq.territory_owner,
                "temporary_effects": list(sq.temporary_effects),
            })
    return rows

def _board_from_d(rows: list[dict]) -> BoardState:
    board = BoardState()  # initializes all 64 empty squares
    for row in rows:
        pos = _pos_from_d(row["pos"])
        sq = board.squares[pos]
        if row.get("unit"):
            sq.unit = _unit_from_d(row["unit"])
        sq.trap_ids = row.get("trap_ids", [])
        sq.building_id = row.get("building_id")
        sq.territory_owner = row.get("territory_owner")
        sq.temporary_effects = row.get("temporary_effects", [])
    return board


# ── Sub-state serialization ────────────────────────────────────────────────

def _castling_to_d(cr: CastlingRights) -> dict:
    return {"kingside": cr.kingside, "queenside": cr.queenside}

def _castling_from_d(d: dict) -> CastlingRights:
    return CastlingRights(kingside=d["kingside"], queenside=d["queenside"])

def _king_card_to_d(kcs: KingCardState) -> dict:
    return {"king_card_id": kcs.king_card_id, "status": kcs.status.value}

def _king_card_from_d(d: dict) -> KingCardState:
    return KingCardState(king_card_id=d["king_card_id"], status=KingCardStatus(d["status"]))

def _ritual_to_d(rs: RitualState) -> dict:
    return {
        "ritual_id": rs.ritual_id,
        "revelation": rs.revelation.value,
        "progress": rs.progress,
        "requirement_reduction": rs.requirement_reduction,
        "activated": rs.activated,
    }

def _ritual_from_d(d: dict) -> RitualState:
    return RitualState(
        ritual_id=d["ritual_id"],
        revelation=RevelationState(d["revelation"]),
        progress=d.get("progress", 0),
        requirement_reduction=d.get("requirement_reduction", 0),
        activated=d.get("activated", False),
    )

def _building_pool_entry_to_d(e: BuildingPoolEntry) -> dict:
    return {"building_card_id": e.building_card_id, "copies_available": e.copies_available}

def _building_pool_entry_from_d(d: dict) -> BuildingPoolEntry:
    return BuildingPoolEntry(building_card_id=d["building_card_id"],
                             copies_available=d["copies_available"])

def _player_to_d(ps: PlayerState) -> dict:
    return {
        "player_id": ps.player_id,
        "deck": list(ps.deck),
        "hand": list(ps.hand),
        "graveyard": list(ps.graveyard),
        "captured_pieces": list(ps.captured_pieces),
        "king_pool": [_king_card_to_d(k) for k in ps.king_pool],
        "active_king": ps.active_king,
        "retired_kings": list(ps.retired_kings),
        "archetype": ps.archetype,
        "ritual_pool": [_ritual_to_d(r) for r in ps.ritual_pool],
        "monsters_lost_count": ps.monsters_lost_count,
        "building_pool": [_building_pool_entry_to_d(b) for b in ps.building_pool],
        "castling_rights": _castling_to_d(ps.castling_rights),
        "preparation_action_used": ps.preparation_action_used,
        "chess_move_used": ps.chess_move_used,
        "king_recycle_used_this_turn": ps.king_recycle_used_this_turn,
        "death_trigger_draw_used_this_turn": ps.death_trigger_draw_used_this_turn,
        "in_check": ps._in_check,
    }

def _player_from_d(d: dict) -> PlayerState:
    ps = PlayerState(
        player_id=d["player_id"],
        deck=d.get("deck", []),
        hand=d.get("hand", []),
        graveyard=d.get("graveyard", []),
        captured_pieces=d.get("captured_pieces", []),
        king_pool=[_king_card_from_d(k) for k in d.get("king_pool", [])],
        active_king=d.get("active_king"),
        retired_kings=d.get("retired_kings", []),
        archetype=d.get("archetype"),
        ritual_pool=[_ritual_from_d(r) for r in d.get("ritual_pool", [])],
        monsters_lost_count=d.get("monsters_lost_count", 0),
        building_pool=[_building_pool_entry_from_d(b) for b in d.get("building_pool", [])],
        castling_rights=_castling_from_d(d["castling_rights"]),
        preparation_action_used=d.get("preparation_action_used", False),
        chess_move_used=d.get("chess_move_used", False),
        king_recycle_used_this_turn=d.get("king_recycle_used_this_turn", False),
        death_trigger_draw_used_this_turn=d.get("death_trigger_draw_used_this_turn", False),
    )
    ps._in_check = d.get("in_check", False)
    return ps

def _pending_decision_to_d(pd: PendingDecision) -> dict:
    # options may contain Position objects (from promotion context)
    def _opt_encode(o: Any) -> Any:
        if isinstance(o, Position):
            return {"__pos__": True, "file": o.file, "rank": o.rank}
        return o
    return {
        "player_id": pd.player_id,
        "decision_type": pd.decision_type.value,
        "options": [_opt_encode(o) for o in pd.options],
        "min_choices": pd.min_choices,
        "max_choices": pd.max_choices,
        "context": pd.context,
    }

def _pending_decision_from_d(d: dict) -> PendingDecision:
    def _opt_decode(o: Any) -> Any:
        if isinstance(o, dict) and o.get("__pos__"):
            return Position(o["file"], o["rank"])
        return o
    return PendingDecision(
        player_id=d["player_id"],
        decision_type=DecisionType(d["decision_type"]),
        options=[_opt_decode(o) for o in d.get("options", [])],
        min_choices=d.get("min_choices", 1),
        max_choices=d.get("max_choices", 1),
        context=d.get("context", {}),
    )

def _trap_to_d(t: TrapInstance) -> dict:
    return {
        "id": t.id, "owner": t.owner, "card_id": t.card_id,
        "position": _pos_to_d(t.position), "radius": t.radius,
        "trigger_condition": t.trigger_condition, "activated": t.activated,
    }

def _trap_from_d(d: dict) -> TrapInstance:
    return TrapInstance(
        id=d["id"], owner=d["owner"], card_id=d["card_id"],
        position=_pos_from_d(d["position"]), radius=d["radius"],
        trigger_condition=d["trigger_condition"], activated=d.get("activated", False),
    )

def _building_to_d(b: BuildingInstance) -> dict:
    return {
        "id": b.id, "owner": b.owner, "building_card_id": b.building_card_id,
        "position": _pos_to_d(b.position), "status": b.status.value,
        "builder_piece_id": b.builder_piece_id, "remaining_turns": b.remaining_turns,
        "disabled_turns": b.disabled_turns,
    }

def _building_from_d(d: dict) -> BuildingInstance:
    return BuildingInstance(
        id=d["id"], owner=d["owner"], building_card_id=d["building_card_id"],
        position=_pos_from_d(d["position"]), status=ConstructionStatus(d["status"]),
        builder_piece_id=d.get("builder_piece_id"), remaining_turns=d.get("remaining_turns", 0),
        disabled_turns=d.get("disabled_turns", 0),
    )

def _duel_to_d(ds: DuelState) -> dict:
    return {
        "duel_type": ds.duel_type.value,
        "attacker": ds.attacker,
        "defender": ds.defender,
        "round_number": ds.round_number,
    }

def _duel_from_d(d: dict) -> DuelState:
    return DuelState(
        duel_type=FinalDuelType(d["duel_type"]),
        attacker=d["attacker"],
        defender=d["defender"],
        round_number=d.get("round_number", 1),
    )


# ── RNG state serialization ────────────────────────────────────────────────
# random.Random.getstate() returns (version, internalstate_tuple, gauss_next)
# JSON can't store tuples of ints natively; we convert to lists.

def _rng_state_to_json(state: Any) -> Any:
    """Recursively convert tuples → lists for JSON serialization."""
    if isinstance(state, tuple):
        return [_rng_state_to_json(x) for x in state]
    if isinstance(state, list):
        return [_rng_state_to_json(x) for x in state]
    return state

def _rng_state_from_json(data: Any) -> Any:
    """
    Restore the raw RNG state.  random.setstate expects specific tuple structure:
        (version:int, internalstate:tuple[int,...], gauss_next:float|None)
    We stored this as nested lists; reconstruct the correct tuple/int types.
    """
    if isinstance(data, list):
        # Top-level is [version, internalstate_list, gauss_next]
        version = int(data[0])
        # internalstate is a list of ints
        internalstate = tuple(int(x) for x in data[1])
        gauss_next = data[2]  # float or None
        return (version, internalstate, gauss_next)
    return data


# ── Top-level export / import ──────────────────────────────────────────────

def export_state(game: Game, path: str | Path) -> None:
    """
    Write the complete game state to a JSON file at ``path``.

    The file can be loaded back with ``import_state(path)`` to resume play
    from exactly this point, including the RNG position.
    """
    state = game._state
    rng = game._rng

    doc: dict[str, Any] = {
        "__version__": 1,
        "game_id": state.game_id,
        "turn_number": state.turn_number,
        "active_player": state.active_player,
        "phase": state.phase.value,
        "winner": state.winner,
        "rng_seed": state.rng_seed,
        "rng_state": _rng_state_to_json(rng.save_state()),
        "en_passant_target": _pos_or_none_to_d(state.en_passant_target),
        "board": _board_to_d(state.board),
        "players": {pid: _player_to_d(ps) for pid, ps in state.players.items()},
        "traps": [_trap_to_d(t) for t in state.traps],
        "buildings": [_building_to_d(b) for b in state.buildings],
        "pending_decision": (
            _pending_decision_to_d(state.pending_decision)
            if state.pending_decision else None
        ),
        "duel": _duel_to_d(state.duel) if state.duel else None,
    }

    Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def import_state(path: str | Path, registry: "Any | None" = None) -> Game:
    """
    Load a previously exported JSON file and return a ready-to-play ``Game``.

    The returned Game is in the exact same state as when it was exported,
    including RNG position, so play continues deterministically.

    ``registry`` — optional CardRegistry; pass the same registry used at game
    start so monster vessel-compatibility checks work after loading.
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))

    board = _board_from_d(doc["board"])
    players = {pid: _player_from_d(pd) for pid, pd in doc["players"].items()}

    state = GameState(
        game_id=doc["game_id"],
        turn_number=doc["turn_number"],
        active_player=doc["active_player"],
        phase=Phase(doc["phase"]),
        board=board,
        players=players,
        traps=[_trap_from_d(t) for t in doc.get("traps", [])],
        buildings=[_building_from_d(b) for b in doc.get("buildings", [])],
        pending_decision=(
            _pending_decision_from_d(doc["pending_decision"])
            if doc.get("pending_decision") else None
        ),
        duel=_duel_from_d(doc["duel"]) if doc.get("duel") else None,
        winner=doc.get("winner"),
        en_passant_target=_pos_or_none_from_d(doc.get("en_passant_target")),
        rng_seed=doc.get("rng_seed", 0),
    )

    rng = DeterministicRNG(seed=doc.get("rng_seed", 0))
    rng.load_state(_rng_state_from_json(doc["rng_state"]))

    return Game(state=state, rng=rng, registry=registry)
