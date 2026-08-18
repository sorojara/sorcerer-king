I would start Stage 0 by **not designing individual cards yet**. First create the contract that every future card must obey.

The first milestone should be:

> `GameState + TurnState + Action + Event + Observation + RulesValidator`

Once those six concepts are stable, almost everything else can plug into them.

## 1. Lock the fundamental game loop

For the first PoC, I would use this turn structure:

```text
START TURN
   │
   ├── 1. Start Phase
   │      Resolve start-of-turn effects
   │
   ├── 2. Draw Phase
   │      Draw 1 card
   │
   ├── 3. Preparation Phase
   │      ONE major preparation action:
   │
   │      • Summon Monster
   │      • Activate Spell
   │      • Place/activate Trap
   │      • Start Building
   │      • Perform Ritual
   │      • Coronate / Succession
   │      • Recompose
   │      • Pass
   │
   ├── 4. Chess Phase
   │      Perform ONE chess move
   │
   ├── 5. Reaction Resolution
   │      Resolve triggered traps/effects
   │
   └── 6. End Phase
          Resolve end-of-turn effects
```

This gives us a very important invariant:

> **Every normal turn contains at most one major card/system action and one chess move.**

That prevents the card layer from swallowing the chess game.

I would make this configurable later, but use it for v0.1.

---

# 2. Define Actions

Everything a human or AI can do must become an `Action`.

I would start with these:

```python
class Action:
    player_id: str


class MovePiece(Action):
    source: Position
    target: Position


class SummonMonster(Action):
    card_id: str
    vessel_position: Position


class ActivateSpell(Action):
    card_id: str
    target: object | None


class PlaceTrap(Action):
    card_id: str
    position: Position


class ActivateTrap(Action):
    trap_instance_id: str
    target: object | None


class StartConstruction(Action):
    pawn_position: Position
    building_id: str


class ActivateRitual(Action):
    ritual_id: str
    sacrifice_positions: list[Position]


class CoronateKing(Action):
    king_card_id: str


class ChangeKing(Action):
    king_card_id: str


class RecomposeHand(Action):
    card_ids: list[str]


class PromotePawn(Action):
    position: Position
    piece_type: str


class EndPreparation(Action):
    pass


class EndTurn(Action):
    pass
```

Later we can introduce things such as:

```python
DismissMonster
ActivateMonsterAbility
DestroyBuilding
FinalDuelAction
```

But don't put them in v0.1 unless needed.

---

# 3. Actions must produce Events

This distinction will save us trouble later.

An **Action** is what the player attempts.

An **Event** is what actually happened.

Example:

```python
MovePiece(
    source="e2",
    target="e4"
)
```

could produce:

```python
[
    PieceMoved(
        piece_id="white-pawn-e",
        source="e2",
        target="e4"
    ),
    TrapRadiusEntered(
        trap_id="trap-123",
        piece_id="white-pawn-e"
    ),
    TrapTriggered(
        trap_id="trap-123"
    )
]
```

So:

```text
PLAYER
   │
   ▼
Action
   │
   ▼
Validator
   │
   ▼
Game Rules
   │
   ▼
Events
   │
   ▼
New GameState
```

This will later make:

* animations
* replay
* save games
* AI analysis
* debugging
* game logs

much easier.

---

# 4. First `GameState`

I'd make the top-level state roughly:

```python
@dataclass
class GameState:
    game_id: str
    turn_number: int
    active_player: str
    phase: Phase

    board: BoardState

    players: dict[str, PlayerState]

    traps: list[TrapInstance]
    buildings: list[BuildingInstance]

    duel: DuelState | None

    rng_seed: int
    rng_state: object

    winner: str | None
```

And:

```python
@dataclass
class PlayerState:
    player_id: str

    deck: list[str]
    hand: list[str]
    graveyard: list[str]

    king_pool: list[KingCardState]
    active_king: str | None
    retired_kings: list[str]

    ritual_pool: list[RitualState]

    building_pool: list[BuildingPoolEntry]

    preparation_action_used: bool
    chess_move_used: bool
```

---

# 5. Board State

Don't make the board just:

```python
board[x][y] = piece
```

because squares themselves now matter.

Use something closer to:

```python
@dataclass
class SquareState:
    position: Position

    unit: UnitInstance | None

    trap_ids: list[str]
    building_id: str | None

    temporary_effects: list[str]
```

Then:

```python
@dataclass
class BoardState:
    squares: dict[Position, SquareState]
```

Why?

Because eventually:

```text
E4
├── Monster
├── Fortress
├── Trap radius affecting it
├── Territory owner
└── temporary Spell effect
```

can all be relevant simultaneously.

---

# 6. Separate `Piece` from `Unit`

This is particularly important because of Vessels.

I'd model:

```python
@dataclass
class ChessPiece:
    id: str
    owner: str
    type: PieceType
```

Then:

```python
@dataclass
class UnitInstance:
    chess_piece: ChessPiece

    monster_card_id: str | None = None

    statuses: list[str] = field(default_factory=list)
```

So a Bishop:

```text
ChessPiece:
    bishop
```

can become:

```text
Unit:
    Vessel = Bishop
    Monster = Dark Magician
```

without destroying the identity of the Bishop.

That makes things like:

> "Dismiss this Monster and restore its Vessel."

very easy.

---

# 7. Define movement independently from cards

Don't put movement code into each piece/card.

Have:

```python
get_legal_moves(
    state,
    unit
)
```

internally resolve:

```text
Base Vessel movement
        +
Monster modifiers
        +
King policy
        +
Buildings
        +
Spells
        +
Statuses
        =
Final legal movement
```

For example:

```python
movement = movement_rules.for_piece(unit.chess_piece)

movement = apply_monster_modifiers(
    movement,
    unit.monster_card_id
)

movement = apply_board_effects(
    movement,
    state
)
```

That avoids `DarkMagicianBishopMovement`, `DarkMagicianQueenMovement`, etc.

---

# 8. Define Check now

This needs to be explicit before cards exist.

I would define:

> A King is **in Check** when at least one currently active enemy unit has a legal capture path to the King's square.

That capture may result from:

* standard chess movement
* Monster-modified movement
* a persistent board effect

But I would **not** say:

> "The opponent has a Spell in hand that could kill my King, therefore I'm in check."

Only current board threats count.

So:

```python
is_in_check(player_id, game_state)
```

only evaluates battlefield state.

---

# 9. What happens when you're in Check

Here's the rule I would lock for Stage 0:

> If the King begins the player's Preparation Phase in Check, every action taken before the Chess Move must contribute to resolving the Check.

And:

> The player's Chess Phase cannot end while their King remains in Check.

For the engine, the simpler invariant is actually:

```text
After the player's full turn action sequence:
King must not remain in ordinary unresolved Check,
unless the state has transitioned into Final Duel.
```

Later we'll refine timing.

---

# 10. Checkmate

Do not end the game.

Instead:

```python
if king_is_in_check
and no_legal_resolution_exists:
    trigger_final_duel(
        type=FinalDuelType.SIEGE
    )
```

Likewise King capture:

```python
trigger_final_duel(
    type=FinalDuelType.ASSAULT
)
```

This should be encoded now even if the actual Duel isn't implemented until much later.

For Stage 1, you could temporarily return:

```text
FINAL_DUEL_REQUIRED
```

and stop the simulation.

---

# 11. Observation is critical because of AI

This should exist in Stage 0, not when we eventually build the AI.

Never do:

```python
bot.choose_action(game_state)
```

Do:

```python
bot.choose_action(
    observation,
    legal_actions
)
```

For White:

```python
@dataclass
class Observation:
    player_id: str

    board: PublicBoardState

    own_hand: list[str]
    own_deck_count: int
    opponent_hand_count: int
    opponent_deck_count: int

    own_king_pool: list[KingCardState]
    opponent_king_public_info: list[PublicKingInfo]

    own_rituals: list[RitualState]
    opponent_ritual_public_info: list[PublicRitualInfo]

    buildings: list[BuildingInstance]
    traps: list[TrapInstance]

    phase: Phase
    turn_number: int
```

The AI **never receives**:

```text
Opponent hand
Opponent deck order
Hidden King identity
Hidden Ritual identity
Future RNG
```

Even on Master difficulty.

---

# 12. Ritual information state

I would formally establish this enum now:

```python
class RevelationState(Enum):
    SEALED = 1
    FORETOLD = 2
    REVEALED = 3
```

Then:

```python
@dataclass
class RitualState:
    ritual_id: str
    revelation: RevelationState
```

An opponent observation might translate:

```text
SEALED
    ↓
{ "ritual": "unknown" }

FORETOLD
    ↓
{
  "archetype": "dragon",
  "required_vessel": "rook"
}

REVEALED
    ↓
{
  "name": "Ancient Dragon Ritual",
  "pattern": ...
}
```

This ensures hidden information is enforced by architecture rather than UI convention.

---

# 13. King states

Similar:

```python
class KingCardStatus(Enum):
    HIDDEN = 1
    ACTIVE = 2
    RETIRED = 3
```

Player starts:

```text
King Piece: active

King Cards:

[Hidden]
[Hidden]
[Hidden]
```

After Coronation:

```text
[ACTIVE]
[Hidden]
[Hidden]
```

After succession:

```text
[RETIRED]
[ACTIVE]
[Hidden]
```

No returning to retired policies.

---

# 14. Construction state

I'd define Buildings as their own entity.

```python
class ConstructionStatus(Enum):
    UNDER_CONSTRUCTION = 1
    COMPLETE = 2
    DESTROYED = 3
```

Example:

```python
@dataclass
class BuildingInstance:
    id: str
    owner: str
    building_card_id: str
    position: Position

    status: ConstructionStatus

    builder_piece_id: str | None
    remaining_turns: int
```

And Pawn:

```python
builder_available: bool
```

Default:

```python
True
```

After completing one building:

```python
False
```

---

# 15. Recompose should be a two-step action

Because the player does **not** know how many cards must be returned before committing.

So don't implement:

```python
RecomposeHand(cards=[...])
```

immediately.

Make:

```text
DeclareRecompose
      ↓
RNG determines requirement
      ↓
Game enters RECOMPOSE_SELECTION state
      ↓
Player chooses exactly N cards
      ↓
ResolveRecompose
```

Which suggests actions:

```python
DeclareRecompose()
```

and:

```python
SelectRecomposeCards(card_ids)
```

That's much cleaner.

---

# 16. This introduces `PendingDecision`

Very useful.

Some actions require player input after something happens.

For example:

```python
@dataclass
class PendingDecision:
    player_id: str
    decision_type: DecisionType
    options: list
    min_choices: int
    max_choices: int
```

Recompose:

```text
decision_type = RECOMPOSE_CARDS
min_choices = 3
max_choices = 3
```

Promotion:

```text
decision_type = PROMOTION
options = [Queen, Rook, Bishop, Knight]
```

Future card:

> Choose one enemy Trap.

Same infrastructure.

This will be extremely useful.

---

# 17. Legal Actions are generated by the engine

Never have the UI decide what's legal.

Expose:

```python
legal_actions = game.get_legal_actions(player_id)
```

The UI renders them.

The AI evaluates them.

The game validates again upon execution.

So:

```text
GameState
   ↓
RulesEngine
   ↓
Legal Actions
   ├──── Human UI
   └──── AI
```

This is one of the most important architectural choices in the entire project.

---

# 18. Make the rules engine enforce invariants

I would write these immediately as tests.

### Turn ownership

```text
Only active player may perform voluntary actions.
```

### Phase ownership

```text
MovePiece cannot happen during Draw Phase.
```

### Chess move limit

```text
Maximum one normal chess move per turn.
```

### Preparation limit

```text
Maximum one major preparation action per normal turn.
```

### Vessel ownership

```text
Cannot transform opponent piece.
```

### Vessel compatibility

```text
Monster can only use supported Vessel classes.
```

### King

```text
King cannot be used as Monster Vessel.
```

### Builder

```text
Only Pawn can build.
```

### Builder usage

```text
Pawn that already completed a Building cannot build again.
```

### Hidden information

```text
Observation must not expose opponent private cards.
```

### Final Duel

```text
King capture never directly sets winner.
```

These tests become your game's constitution.

---

# 19. Stage 0 repository

I'd actually create this immediately:

```text
game-poc/
├── README.md
├── pyproject.toml
│
├── docs/
│   ├── GAME_DESIGN.md
│   └── RULES.md
│
├── src/
│   └── game/
│       ├── core/
│       │   ├── actions.py
│       │   ├── events.py
│       │   ├── state.py
│       │   ├── observation.py
│       │   ├── phases.py
│       │   └── rules.py
│       │
│       ├── chess/
│       │   ├── board.py
│       │   └── pieces.py
│       │
│       └── cards/
│           └── definitions.py
│
└── tests/
    ├── test_actions.py
    ├── test_turns.py
    ├── test_observation.py
    └── test_rules.py
```

No Pygame yet.

---

# 20. First code I would write

Not movement.

Not cards.

Start here:

```python
from dataclasses import dataclass
from enum import Enum


class Phase(Enum):
    START = "start"
    DRAW = "draw"
    PREPARATION = "preparation"
    CHESS = "chess"
    REACTION = "reaction"
    END = "end"
    FINAL_DUEL = "final_duel"


class PieceType(Enum):
    KING = "king"
    QUEEN = "queen"
    ROOK = "rook"
    BISHOP = "bishop"
    KNIGHT = "knight"
    PAWN = "pawn"


@dataclass(frozen=True)
class Position:
    file: int
    rank: int
```

Then:

```python
@dataclass
class ChessPiece:
    id: str
    owner: str
    piece_type: PieceType


@dataclass
class UnitInstance:
    piece: ChessPiece
    monster_id: str | None = None
```

Then:

```python
@dataclass
class GameState:
    turn: int
    active_player: str
    phase: Phase
```

Only after these objects feel clean would I start implementing chess movement.

---

# 21. The actual Stage 0 checklist

I would do it in this order:

* [ ] Create repository structure.
* [ ] Define `Phase`.
* [ ] Define `Position`.
* [ ] Define `PieceType`.
* [ ] Define `ChessPiece`.
* [ ] Define `UnitInstance`.
* [ ] Define `SquareState`.
* [ ] Define `BoardState`.
* [ ] Define `PlayerState`.
* [ ] Define `GameState`.
* [ ] Define `Action` hierarchy.
* [ ] Define `Event` hierarchy.
* [ ] Define `PendingDecision`.
* [ ] Define `Observation`.
* [ ] Define Ritual revelation states.
* [ ] Define King states.
* [ ] Define Building states.
* [ ] Define Final Duel trigger states.
* [ ] Write rules invariants.
* [ ] Write observation/privacy tests.
* [ ] Write turn-state tests.
* [ ] Only then implement chess movement.

## One design decision I'd intentionally postpone

Don't decide whether a Spell activation happens strictly **before or after** the chess move forever.

Implement:

```python
Phase.PREPARATION
Phase.CHESS
```

for now.

If playtesting later shows that spells need both a pre-move and post-move window, we can change it to:

```text
Preparation
Move
Tactics
Reaction
```

without breaking the whole engine.

The thing we **should** lock now is the action/state architecture, not every balance/timing detail.

If I were starting the repository today, the first functional target would be extremely small:

```python
game = Game.new()

print(game.state.phase)
# PREPARATION

print(game.get_legal_actions("white"))
# [...]

result = game.execute(action)

print(result.events)
```

Once that API exists, we have the foundation for **Pygame, RandomBot, replay, automated tests, and eventually the production engine** all at once.
