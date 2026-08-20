"""
Formal Turn Sequence — Stage 0 Specification
=============================================

Every normal turn follows exactly this sequence:

    START TURN
       │
       ├── 1. START      — Resolve start-of-turn effects
       │
       ├── 2. DRAW       — Draw 1 card
       │
       ├── 3. PREPARATION — ONE major preparation action:
       │      • Summon Monster
       │      • Activate Spell
       │      • Place/Activate Trap
       │      • Start Building
       │      • Perform Ritual
       │      • Coronate / Succession
       │      • Declare Recompose
       │      • Pass (no action)
       │      OR respond to a PendingDecision
       │
       ├── 4. CHESS      — Perform ONE chess move
       │
       ├── 5. REACTION   — Resolve triggered traps / effects
       │
       └── 6. END        — Resolve end-of-turn effects

    SPECIAL STATES (not a normal turn phase)
       ├── RECOMPOSE_SELECTION    — Player choosing which cards to return
       ├── MERCENARY_SELECTION    — Player choosing which Monster cards to sacrifice
       ├── MERCENARY_PLACEMENT    — Player choosing the square for the new piece
       ├── PROMOTION_SELECTION    — Pawn reached back-rank, picking piece type
       ├── FINAL_DUEL             — King capture / checkmate triggered the Duel
       └── GAME_OVER              — A winner has been determined

Invariants (locked for v0.1):
  • At most ONE major preparation action per normal turn.
  • At most ONE chess move per normal turn.
  • A player cannot act outside their own phases.
  • King capture never directly sets winner — triggers FINAL_DUEL.
"""

from enum import Enum, auto


class Phase(Enum):
    """All possible game phases, including special non-turn states."""

    # ── Normal turn phases (in order) ──────────────────────────────────────
    START = "start"
    DRAW = "draw"
    PREPARATION = "preparation"
    CHESS = "chess"
    REACTION = "reaction"
    END = "end"

    # ── Special interrupt / sub-states ─────────────────────────────────────
    DISCARD = "discard"            # hand > HAND_SIZE_LIMIT: player must discard
    RECOMPOSE_SELECTION = "recompose_selection"
    MERCENARY_SELECTION = "mercenary_selection"   # Stage 7: choosing cards to sacrifice
    MERCENARY_PLACEMENT = "mercenary_placement"   # Stage 7: choosing square for new piece
    PROMOTION_SELECTION = "promotion_selection"

    # ── Terminal / macro states ─────────────────────────────────────────────
    FINAL_DUEL = "final_duel"
    GAME_OVER = "game_over"


class PieceType(Enum):
    """Standard chess piece types. King is never a valid Monster vessel."""

    KING = "king"
    QUEEN = "queen"
    ROOK = "rook"
    BISHOP = "bishop"
    KNIGHT = "knight"
    PAWN = "pawn"


# Pieces that may serve as Monster vessels (King is excluded).
VALID_VESSEL_TYPES: frozenset[PieceType] = frozenset(
    {
        PieceType.QUEEN,
        PieceType.ROOK,
        PieceType.BISHOP,
        PieceType.KNIGHT,
        PieceType.PAWN,
    }
)


class FinalDuelType(Enum):
    """How the Final Duel was triggered."""

    ASSAULT = "assault"  # King was captured on the board
    SIEGE = "siege"      # Checkmate reached with no legal resolution
    LAST_STAND = "last_stand"  # Repeated escalation / special condition


class RevelationState(Enum):
    """Information state of a hidden Ritual card."""

    SEALED = 1    # Opponent knows nothing
    FORETOLD = 2  # Opponent sees archetype + required vessel, not full pattern
    REVEALED = 3  # Full Ritual identity is public


class KingCardStatus(Enum):
    """Lifecycle state of a King card in a player's pool."""

    HIDDEN = 1    # Not yet revealed
    ACTIVE = 2    # Currently active policy
    RETIRED = 3   # Used and can never become active again


class ConstructionStatus(Enum):
    """Lifecycle state of a BuildingInstance on the board."""

    UNDER_CONSTRUCTION = 1
    COMPLETE = 2
    DESTROYED = 3


# Maximum hand size before the player must discard.
HAND_SIZE_LIMIT: int = 6


class DecisionType(Enum):
    """Types of pending decisions that require a player response."""

    RECOMPOSE_CARDS = "recompose_cards"
    MERCENARY_CARDS = "mercenary_cards"      # Stage 7: which Monster cards to sacrifice
    MERCENARY_PLACE = "mercenary_place"      # Stage 7: which square to place the piece
    PROMOTION = "promotion"
    CHOOSE_TARGET = "choose_target"
    CHOOSE_SACRIFICE = "choose_sacrifice"
    REPOSITION = "reposition"          # Stage 5: move a piece to a chosen square
