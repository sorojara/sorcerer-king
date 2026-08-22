# Chess × Card Battler — Game Design & PoC Implementation Plan

> Working design document consolidating the mechanics, systems, prototype roadmap, and AI development strategy agreed so far.

---

# 1. Core Concept

The game is a turn-based strategy game built on top of standard chess, mixed with first-generation-style trading card mechanics.

The chessboard remains the primary game state.

Players still move pieces, control squares, create threats, develop positions, and attack the enemy King. However, each player also has a deck containing Monsters, Spells, and Traps that can alter the board and transform chess pieces into stronger or specialized units.

The design principle is:

> **Cards should manipulate chess. Chess should not merely be the board on which a card game happens.**

The game should preserve the strategic clarity of chess while introducing:

- deck construction
- archetypes
- card advantage
- uncertainty
- transformation
- board control
- infrastructure
- rituals
- strategic policy changes
- a climactic Final Duel

The victory objective is ultimately to defeat the opponent's King, but a King is not immediately destroyed by a single lucky card or capture. Reaching the King triggers a Final Duel.

---

# 2. Primary Design Pillars

The game currently revolves around the following major systems.

## 2.1 Chess

Chess provides:

- the 8×8 board
- standard starting armies
- piece movement
- positional play
- material advantage
- checks
- checkmates
- captures
- pawn advancement
- promotion
- board control

Chess remains the deterministic foundation of the game.

---

## 2.2 Main Deck

Each player brings a customizable Main Deck.

The Main Deck contains:

- Monsters
- Spells
- Traps

The Main Deck provides the tactical and uncertain layer of the game.

The exact final deck size should be determined during balance testing.

An early PoC could use approximately 20–30 cards.

---

# 3. Monsters and Vessels

Monsters are not normally summoned onto empty squares.

Instead, a Monster is summoned by transforming one of the player's existing chess pieces.

The chess piece acts as the Monster's **Vessel**.

Each Monster defines the classes of pieces that can serve as its Vessel.

Example:

```yaml
name: Dark Magician
type: monster

supported_vessels:
  - bishop
  - queen
```

This creates a direct relationship between deckbuilding and chess material.

A player cannot summon every Monster using every piece.

---

## 3.1 Vessel Classes

Possible vessel classes include:

- Pawn
- Knight
- Bishop
- Rook
- Queen

The King is not a normal Monster Vessel.

Different Monster archetypes may specialize in different vessel classes.

Example ideas:

| Vessel | Typical Strategic Identity |
|---|---|
| Pawn | cheap Monsters, swarm, economy tradeoffs |
| Knight | mobility, jumping, tactical Monsters |
| Bishop | magic, ranged effects, Ritual support |
| Rook | defense, heavy Monsters, fortification |
| Queen | flexible/high-value Monsters |

---

## 3.2 Vessel Value

Transforming a piece must be a meaningful strategic decision.

Using a Queen as a Vessel may allow access to powerful Monsters, but the player is risking the most valuable conventional chess piece.

Summoning should therefore not automatically be better than preserving the original piece.

---

## 3.3 Monster Movement

The preferred design direction is that Monsters generally inherit movement from their Vessel unless the Monster specifically overrides or modifies it.

Example:

A Dark Magician summoned using a Bishop still moves primarily like a Bishop, but gains the Dark Magician's special effect.

A Monster summoned using a Queen may therefore behave differently from the same Monster summoned using a Bishop.

This gives the same Monster different tactical identities depending on its Vessel.

---

## 3.4 Monster Destruction

Default rule:

> If the Monster is destroyed, its Vessel is also lost.

The Monster and Vessel are treated as a combined battlefield unit.

Some specific effects may allow a Monster to be dismissed and restore its Vessel.

Example:

**Dismiss**
- return Monster card to deck or hand
- restore original Vessel to the board

This should be an exception, not the default.

---

# 4. Combat

The game should avoid reproducing full Yu-Gi-Oh-style numerical ATK/DEF combat.

Chess capture remains the core combat model.

A piece attacks/captures according to its movement and Monster abilities.

Monster effects can alter capture rules, movement, survivability, or range.

Possible future abstraction:

- Monster Power Tiers
- special capture protections
- special retaliation effects

However, the PoC should avoid unnecessary combat arithmetic.

---

# 5. Turn Structure

The exact turn structure must be validated during the PoC.

The preferred direction is that every turn retains a mandatory chess component.

A possible structure:

1. Start Phase
2. Draw Phase
3. Card / Policy Phase
4. Chess Move
5. Reaction Window
6. End Phase

The key design rule is:

> **A player should not be able to ignore chess and spend an entire turn playing cards.**

The game should avoid long combo turns where one player plays many cards while the chessboard becomes irrelevant.

Potential card-action limits should be tested during the PoC.

---

# 6. Spells

Spells modify board rules, positioning, pieces, buildings, or other cards.

Preferred Spell design:

- spatial
- tactical
- temporary
- board-oriented

Avoid excessive numerical bonuses.

Examples of possible Spell effects:

- move a piece
- temporarily block a file/rank
- alter movement
- disable a Building
- change Trap radius
- destroy or move a Trap
- manipulate Vessel compatibility
- accelerate Ritual progress
- reposition a unit
- modify Territory

Some Spells may create temporary areas of influence on the board.

---

# 7. Traps

Traps are **not secret**.

Hidden Trap Cards were rejected because they could create overly powerful and frustrating surprise effects.

Instead:

> Traps are visible battlefield objects with visible activation areas.

The opponent should know:

- where a Trap is located
- which Trap it is
- its activation radius
- the condition under which it can activate

Example:

```text
. . . . .
. X X X .
. X T X .
. X X X .
. . . . .
```

`T` = Trap

`X` = activation radius

This turns Traps into positional threats rather than hidden "gotcha" mechanics.

---

## 7.1 Trap Philosophy

Hidden information should primarily create uncertainty about:

- intentions
- cards in hand
- Rituals
- King policies

It should **not** make the visible battlefield unreliable.

The player should be able to inspect the board and understand known threats.

---

# 8. Spatial Influence Layers

The board may eventually display several overlapping strategic layers:

- normal chess attacks
- Monster ability ranges
- Trap activation radius
- Spell influence
- Building influence
- Territory
- Ritual patterns
- King support range

The UI must make these readable without overwhelming the player.

This will be a major UX challenge during prototyping.

---

# 9. Recompose Hand

Recompose exists to prevent stale endgames where a player has cards that can no longer be used because their required Vessel classes have been eliminated.

However, Recompose should not be an efficient or routine hand-cycling mechanic.

It is intended as a **desperate measure**.

## 9.1 Recompose Rule

The player chooses to activate Recompose.

Only after committing to the action is a random number generated.

That number determines how many cards the player must return to the deck.

Example:

```text
Player activates RECOMPOSE.

Random result: 3

Player must return exactly 3 cards.
Those cards are returned to the deck.
The deck is shuffled.
Player draws 3 replacement cards.
```

The player does not know the required number before choosing Recompose.

This makes the action risky.

The exact probability distribution should be tested.

A weighted 1–4 or 2–4 distribution may be preferable to uniform randomness.

---

## 9.2 Mercenary

Mercenary is a late-game emergency mechanic.

Lore: the player spends resources — Monster cards — to hire mercenaries to
fight for their kingdom.

### Rule

The player pays a cost in Monster cards from their hand.  Those cards are
**removed from the game** (not sent to the graveyard; they are lost forever).

In exchange the player may place a brand-new chess piece of the purchased
type on any **empty square in their own first two ranks**.

### Cost table

| Monsters sacrificed | Piece gained |
|---------------------|-------------|
| 4                   | Pawn        |
| 6                   | Knight or Bishop or Rook |
| 8                   | Queen       |

### Restrictions

- Only Monster cards may be sacrificed (Spells and Traps do not count).
- The player must have exactly the required number of Monster cards in hand.
- The receiving square must be empty and within the player's own first two ranks (ranks 1–2 for white, ranks 7–8 for black).
- Consumes the preparation action for the turn.
- Cannot be used while the preparation action has already been used.

### Design intent

Mercenary is the "nuclear option" for piece recovery in the late game.
Sacrificing 4–8 cards is a severe cost that only makes sense when the player's
piece count has dropped so low that card tempo no longer matters.

---

# 10. Pawns

Pawns are strategically unique.

They should not simply become disposable material for Tributes and Monster summoning.

Pawns are the kingdom's **Builders**.

Only Pawns can construct Buildings.

This gives Pawns several competing uses:

| Pawn Use | Purpose |
|---|---|
| Chess piece | board control and advancement |
| Vessel | summon certain Monsters |
| Tribute | enable major summons |
| Builder | create permanent infrastructure |
| Promotion candidate | gain conventional chess power |
| Ritual component | satisfy Ritual formations |

The player should frequently face the decision:

> Do I sacrifice this Pawn now, or preserve it because I will need infrastructure later?

---

# 11. Buildings

Buildings are a new major mechanic intended to differentiate the game from both chess and traditional card games.

Buildings permanently alter the strategic value of parts of the board.

They:

- do not move
- do not normally capture
- modify squares or nearby pieces
- affect Territory
- support archetypes
- support Rituals
- influence the Final Duel
- create long-term board memory

---

## 11.1 Buildings as Terrain

A **completed** Building is a physical obstacle.

The square it occupies:

- cannot be entered by the opposing side
- blocks a sliding piece's line, exactly like a wall
- never blocks its own owner (the Builder Pawn is standing on it the
  moment construction finishes)

A Building under construction is **not** terrain. It is contested the way
it always was — by capturing the committed Builder Pawn.

Effects that explicitly ignore Building terrain (Sky Serpent) may slide
**through** an occupied square, but still may not stop on it. The
structure is there.

---

## 11.2 Siege

Going around a Building is the normal answer. Knocking it down is the
expensive one.

**Only these may attack a Building:**

| Attacker | Why |
| --- | --- |
| A **Ritual Monster** | The headline rule. Siege capability is the strategic payoff for completing a Ritual. |
| A Monster with **building_damage_bonus** | Cards whose entire text is about tearing down infrastructure (Obsidian Dragon, Sovereign of Embers). |
| **Any unit**, against a Building a Spell has cracked open | Siege Order's `building_vulnerability` breaches the defences for everyone. |

Everything else on the board — every Pawn, Knight, Rook, Queen, and every
ordinary Monster — simply cannot threaten infrastructure.

**Attacking is not moving.** A siege is declared as its own action against
an adjacent-or-reachable Building square. It consumes the attacker's chess
move for the turn, and the attacker does not relocate: the square stays
impassable until the structure actually falls.

A Building on a square where a unit is standing cannot be besieged. Deal
with the garrison first.

---

## 11.3 Building Integrity

Every completed Building has an integrity pool derived from its size:

| Size | Integrity |
| --- | --- |
| Small | 1 |
| Medium | 2 |
| Major | 3 |

One successful hostile action removes one point. At zero, the Building
collapses and its square opens up.

Layers of defence, in the order a blow meets them:

1. **Building Capture Protection** (Castle Keeper) — an allied Monster
   nearby absorbs the entire action and spends a charge.
2. **Building Vulnerability** (Siege Order) — the target is already
   cracked open, so the blow lands harder.
3. **Building Aura** (Siege Captain, Titan of the Foundation) — extra
   effective hit points, re-evaluated on every blow. Kill the Monster
   projecting it and the Building is back to bare integrity.
4. **Temporary Building Protection** (Emergency Fortifications) — the last
   line: a blow that *would* destroy the Building is absorbed and the
   structure is left standing on its final point.

**Repair** (Royal Engineer, Worldforge Colossus) restores integrity at the
owner's end of turn, capped at the Building's maximum.

Some effects bypass the attack rules entirely because they are not a unit
attacking: a Demolition Charge is the ground under the Building
detonating, and it damages adjacent enemy Buildings through the same
defensive stack.

---

# 12. Building Pool

Buildings should **not** be part of the random Main Deck.

Whether a player can construct infrastructure should depend on strategy and deckbuilding, not whether they happened to draw the correct Building.

Instead, players bring a separate **Building Pool**.

The Building Pool is public.

Example:

```text
BUILDING POOL

Fortress       ×2
Shrine         ×1
Watchtower     ×1
Portal         ×2
```

---

## 12.1 Building Budget

A point-based Building Pool is preferred over a flat card count.

Example:

- Small Building = 1 point
- Medium Building = 2 points
- Major Building = 3 points

Example total budget:

```text
Building Budget: 6
```

This allows:

- six small structures
- three medium structures
- one major + several smaller structures
- other combinations

The exact budget should be balance-tested.

---

## 12.2 Construction

Only Pawns may construct Buildings.

Preferred construction flow:

1. Pawn occupies construction square.
2. Player begins construction.
3. Pawn is committed for a period of time.
4. Opponent has an opportunity to disrupt it.
5. If the Pawn survives and conditions remain valid, Building completes.
6. Pawn survives construction and may move later.

Buildings should not consume the Pawn by default.

Otherwise construction becomes another sacrifice mechanic.

---

## 12.3 Builder Limits

A useful balancing rule:

> Each Pawn may construct only one Building per match.

After completing construction, the Pawn loses its unused Builder status.

This can be represented with a token.

The Pawn remains otherwise fully functional.

---

## 12.4 Example Buildings

### Fortress

Purpose:

- protect King area
- improve Final Duel defense
- strengthen Territory

### Shrine

Purpose:

- Ritual support
- Ritual radius
- possibly substitute or augment certain Ritual requirements

### Watchtower

Purpose:

- reveal or interact with information
- extend support/influence range
- support Final Duel participation

### Portal

Purpose:

- link two areas of the board
- provide controlled mobility

### Barracks

Purpose:

- support Pawn or Warrior-like archetypes
- improve nearby summons

### Sanctuary

Purpose:

- limit enemy summoning or hostile effects within its radius

### Citadel

Major Building.

Possible requirements:

- multiple Pawns
- multiple turns
- high Building cost

Provides major King protection.

---

# 13. Territory

Buildings may create or influence **Territory**.

Territory represents developed control of an area of the board.

Possible effects:

- reduced summon costs
- enhanced Trap radius
- improved Rituals
- stronger King policies
- better Final Duel support
- hostile summoning restrictions

Territory should not become a second unrelated board-control game.

It should reinforce chess positioning.

---

# 14. Ritual Summoning

Rituals are one of the game's signature mechanics.

Ritual Monsters exist in a separate Ritual / Extra Pool.

They are not normally drawn.

Rituals are activated when the player achieves a required board state.

The required pieces are then sacrificed to summon a powerful Ritual Monster.

Example:

```text
   Knight
      |
Pawn-Bishop
```

If the exact required formation exists, the Ritual becomes available.

The player sacrifices those pieces and summons the Ritual Monster according to its rules.

---

## 14.1 Ritual Conditions

Ritual requirements do not need to be limited to piece combinations.

Possible Ritual condition types:

### Formation Ritual

Pieces must occupy a specific geometric pattern.

### Control Ritual

Player must control or threaten specified squares.

### Material Ritual

Player must sacrifice a required total material value.

### Tactical Ritual

Example:

- two pieces attacking the same enemy Queen
- King currently in check
- multiple pieces controlling center squares

### State Ritual

Triggered by a unique game condition.

Example:

- Queen has been destroyed
- exactly two Pawns remain
- specified Buildings exist

The strongest Rituals should require visible strategic preparation.

---

# 15. Ritual Information

Rituals begin hidden.

However, they become progressively revealed as the match progresses.

This creates uncertainty without hidden battlefield threats.

Possible Ritual states:

```text
SEALED -> FORETOLD -> REVEALED
```

## Sealed

Opponent knows nothing or almost nothing.

## Foretold

Opponent sees partial information.

Example:

```text
Type: Dragon Ritual
Required Vessel: Rook
Exact formation: ???
```

## Revealed

Full Ritual identity and requirements become public.

---

## 15.1 Ritual Revelation Triggers

Ritual information may be revealed through:

- attempting a Ritual
- paying information as a card cost
- powerful Spell effects
- opponent disruption
- losing major chess material
- losing the Queen
- being checked
- voluntary revelation
- board milestones
- King policies
- Watchtowers or information Buildings

---

## 15.2 Information as a Resource

A player may deliberately reveal a Ritual to gain a benefit.

Example:

```text
Reveal one hidden Ritual:
Draw 1 card.
```

or:

```text
Reveal a Ritual:
Reduce one requirement of that Ritual.
```

This creates a strategic choice:

> Keep the Ritual secret and difficult to complete

versus

> Reveal your plan to the opponent and make it easier to execute

This also enables bluffing.

A player may reveal a Ritual simply to influence the opponent's positioning without intending to summon it.

---

# 16. Kings and King Cards

The physical King is not just a target.

Each player's King represents the current political / strategic doctrine of the kingdom.

Before the game, each player selects a **pool of 3 King Cards**.

These begin hidden.

The King Cards define the player's potential strategic directions.

---

# 17. Coronation

The physical chess King begins without an active King Card.

At some point, the player may reveal one of the three King Cards and activate it.

This is called **Coronation**.

> The first Coronation is free.

Example:

```text
[ ??? ] [ ??? ] [ ??? ]

        ↓ Coronation

[ ACTIVE KING ] [ ??? ] [ ??? ]
```

The active King Card provides passive or strategic effects.

---

# 18. King Design Rules

King effects should:

- support an archetype
- influence Buildings
- influence Rituals
- influence summons
- alter resource efficiency
- affect information
- influence Final Duel support
- change how the kingdom operates

King effects should **not** normally be directly offensive.

Avoid effects like:

> Deal damage to enemy piece.

Prefer effects like:

> Dragon Monsters may use Rooks as additional Vessels.

or:

> Shrines have +1 Ritual influence radius.

The King changes policy, not battlefield attack power.

---

# 19. Succession

The player may change the active King Card during the game.

This represents political succession or a change of policy.

Example:

```text
King A
  ↓
King B
  ↓
King C
```

The first Coronation is free.

Future changes have increasing costs.

Important rule:

> A retired King cannot become active again.

This prevents stance-dancing and makes succession a strategic progression.

---

## 19.1 Possible Succession Costs

Possible costs include:

- sacrifice a piece
- destroy a Building
- discard cards
- reveal Ritual information
- spend a dedicated resource

The PoC should initially prefer costs that reuse existing game resources rather than introduce a new resource system.

---

## 19.2 No Succession Under Check

Preferred rule:

> A player cannot Coronate or perform Succession while their King is currently in check.

The player must anticipate danger rather than instantly switch to a defensive King after being attacked.

---

# 20. Archetypes

King Cards, Monsters, Rituals, and Buildings collectively define an archetype.

Examples:

- Dragon
- Spellcaster
- Warrior
- Fortress / defensive
- Ritual-heavy
- Builder / infrastructure
- Assassin
- Graveyard recursion

A single archetype may support several King policies.

Example Dragon King Pool:

### King of Conquest

Improves territorial summoning.

### King of Rituals

Improves Dragon Ritual access.

### King of Fortification

Improves defensive infrastructure.

The same deck can therefore adapt during the match.

---

# 21. Check

Check remains important.

If a player's King is threatened, the threat must be resolved.

Cards may be used to resolve check if their effect genuinely eliminates the threat.

Possible responses:

- move King
- capture attacker
- block attack
- summon a Monster that blocks
- activate a Spell
- trigger a defensive Trap

The player may not simply ignore the check and continue unrelated development.

---

# 22. King Capture and Checkmate

King capture does **not** immediately end the match.

Otherwise a lucky card draw or unexpected card interaction could produce an unsatisfying instant victory.

Instead:

> **King capture initiates the Final Duel.**

Traditional checkmate also initiates the Final Duel.

These two initiation methods may create different Duel conditions.

---

# 23. Final Duel — Purpose

The Final Duel exists to ensure that:

- the match cannot be decided by one lucky tactical card
- the preceding board state matters
- army preservation matters
- King defense matters
- infrastructure matters
- positional preparation matters
- the match ends with a climactic confrontation

The Final Duel should be short.

Target length:

> approximately 2–5 minutes

It should feel like the consequence of the main game, not a completely separate second game.

---

# 24. Final Duel — Core Principle

When the Final Duel begins:

> **No new Main Deck cards are drawn.**

The Duel should minimize or completely remove randomness.

The Final Duel uses resources created by the board state immediately before the Duel.

---

# 25. Final Duel — Trigger Types

## Assault Duel

Triggered by physically capturing / reaching the enemy King.

Attacker receives standard initiative.

Defender receives normal defensive support.

---

## Siege Duel

Triggered by checkmate.

Because the attacker achieved a stronger positional victory, Siege should provide an additional advantage.

Possible bonuses:

- attacker gets one free positioning action
- defender loses one Guard
- attacker gains initiative advantage
- defender has reduced support access

Exact rule to be determined through testing.

---

## Last Stand

Repeated Final Duels should become progressively more dangerous.

A possible rule:

- First Final Duel: normal
- Second Final Duel: reduced escape protection
- Third Final Duel: Last Stand, no escape

Exact limits should be validated.

---

# 26. Final Duel — Board

Preferred prototype:

A small 3×3 Duel Arena centered around the King.

Example:

```text
┌─────┬─────┬─────┐
│     │  D  │     │
├─────┼─────┼─────┤
│  D  │ KING│  A  │
├─────┼─────┼─────┤
│     │     │     │
└─────┴─────┴─────┘
```

The main chessboard freezes while the Duel is resolved.

The precise arena rules need a dedicated design iteration.

---

# 27. Final Duel — Support

Surviving army size should **not** directly determine victory.

Instead:

> **Army position determines available Duel support.**

A player may have many surviving pieces but still be vulnerable if those pieces are far away from their King.

This creates two distinct late-game resources:

### Material Advantage

How much army is still alive.

### Royal Support

How much of that army is positioned to influence the King's defense or attack.

---

## 27.1 Support Eligibility

Potential rule:

A piece provides Duel Support if:

- it is within a defined radius of the King
- OR its normal movement controls the King's square
- OR it controls one of the squares surrounding the King
- OR a Building/King effect extends its support range

This should be tested.

---

# 28. Final Duel — Support Abilities

Each surviving chess piece may generate a standardized Duel ability.

Initial prototype examples:

### Pawn — Guard

Block one incoming Strike.

### Knight — Charge

Rapid reposition or bypass one defensive square.

### Bishop — Intervention

Redirect or alter an attack.

### Rook — Fortify

Create temporary defense or block a path.

### Queen — Command

Provide a powerful tactical action.

Monsters may replace or enhance their Vessel's standard Duel ability.

---

# 29. Buildings in the Final Duel

Relevant Buildings may contribute Duel abilities.

Example:

### Fortress

Gain an additional Guard.

### Watchtower

Extend Royal Support range.

### Shrine

Allow a Ritual Monster outside normal range to contribute.

### Citadel

Provide a major defensive ability or additional escape option.

This makes infrastructure relevant all the way to the match's conclusion.

---

# 30. King Cards in the Final Duel

The active King Card determines the King's Duel identity.

Example:

### Defensive King

Buildings provide improved defensive support.

### People's King

Surviving Pawns provide additional Guards.

### Arcane King

Completed Rituals provide extra Duel resources.

The King itself remains non-offensive.

The kingdom comes to the King's defense.

---

# 31. Final Duel — Win Condition

Initial preferred prototype:

### Attacker

Land 2 successful Strikes against the King.

### Defender

Survive a fixed number of Duel rounds.

Example:

```text
King Life:

♥ ♥
```

Each successful Strike removes one.

No numerical HP system is required.

---

# 32. Final Duel — Duel Actions

Possible Duel actions:

### Advance

Move within the Duel Arena.

### Strike

Attack King or Duel target.

### Support

Consume a Support ability from the main-board army.

### Building

Activate an eligible Building ability.

### King Policy

Activate an eligible once-per-Duel King effect.

Main Deck cards are not normally available.

No new:

- summons
- Rituals
- Buildings
- Recompose
- random draws

---

# 33. Final Duel — Royal Escape

If the Defender survives the Duel:

> The King escapes and the main game resumes.

The King must not simply return to the exact same losing position.

Possible rule:

- King relocates to a legal nearby square
- attacker remains in the triggering position
- defender gains temporary protection from another Duel

Example:

### Royal Immunity

Another Final Duel cannot be initiated against that King until the end of the defender's next turn.

Repeated escapes should be limited to prevent endless games.

---

# 34. Strategic Paths to Victory

The systems naturally create multiple strategic approaches.

## Conquest

- win material
- destroy infrastructure
- dominate Territory
- corner King
- force an overwhelming Final Duel

## Assassination

- accept material disadvantage
- penetrate defenses
- isolate enemy King
- exploit poor Royal Support
- force Final Duel early

## Ritual Strategy

- create board patterns
- protect required pieces
- reveal information strategically
- summon major Ritual Monsters

## Infrastructure Strategy

- preserve Pawns
- develop Territory
- defend King
- improve long-term board efficiency

These should remain viable without any becoming mandatory.

---

# 35. Information Philosophy

The game deliberately separates deterministic board information from hidden strategic information.

## Public

- board position
- pieces
- active Monsters
- visible Traps
- Trap radius
- Buildings
- Territory
- active King
- revealed Rituals

## Hidden or Partially Hidden

- Main Deck
- hand
- unrevealed King Cards
- unrevealed Rituals
- future intentions

Design principle:

> **Hidden information should create uncertainty about intention, not uncertainty about what the visible board currently does.**

---

# 36. PoC Technology

Recommended initial technology:

- Python
- Pygame
- YAML or JSON card definitions
- pytest or equivalent testing
- deterministic seeded RNG

The first prototype should prioritize rule iteration over graphics.

Possible long-term production engine:

- Godot
- Unity

Godot may be especially suitable because the game is:

- 2D
- grid-based
- UI-heavy
- turn-based

However, the core rules engine should remain independent from Pygame so it can later be ported.

---

# 37. Core Architecture

The game should be playable without a graphical interface.

Recommended architecture:

```text
                Game Core
                    │
              Observation
                    │
          ┌─────────┴─────────┐
          │                   │
       Human UI              AI
          │                   │
          └────── Action ─────┘
                    │
                Game Core
```

The Game Core owns:

- rules
- board
- state
- turns
- validation
- legal actions
- card effects
- RNG
- Final Duel

The UI only visualizes and sends actions.

The AI only receives legal observations and selects actions.

---

# 38. Proposed Source Layout

```text
game/
├── core/
│   ├── game.py
│   ├── state.py
│   ├── actions.py
│   ├── events.py
│   ├── rules.py
│   ├── rng.py
│   └── observation.py
│
├── chess/
│   ├── pieces.py
│   ├── movement.py
│   ├── check.py
│   ├── capture.py
│   └── promotion.py
│
├── cards/
│   ├── card.py
│   ├── deck.py
│   ├── hand.py
│   ├── monster.py
│   ├── spell.py
│   ├── trap.py
│   └── effects.py
│
├── mechanics/
│   ├── vessels.py
│   ├── recompose.py
│   ├── buildings.py
│   ├── territory.py
│   ├── rituals.py
│   ├── kings.py
│   └── duel.py
│
├── ai/
│   ├── controller.py
│   ├── random_bot.py
│   ├── heuristic_bot.py
│   ├── search_bot.py
│   ├── belief.py
│   └── monte_carlo.py
│
├── data/
│   ├── monsters.yaml
│   ├── spells.yaml
│   ├── traps.yaml
│   ├── buildings.yaml
│   ├── rituals.yaml
│   └── kings.yaml
│
├── ui/
│   ├── pygame_app.py
│   ├── board_view.py
│   ├── hand_view.py
│   └── overlays.py
│
├── tests/
│   └── ...
│
└── main.py
```

---

# 39. Data-Driven Card Design

Avoid creating one Python class per card.

Cards should primarily be data.

Example:

```yaml
id: dark_magician
name: Dark Magician
type: monster

supported_vessels:
  - bishop
  - queen

effects:
  - type: spell_radius_bonus
    radius: 1

duel:
  ability: arcane_intervention
```

The engine interprets standardized effects.

This makes:

- card creation faster
- balancing easier
- testing easier
- eventual migration to another engine easier

Custom code should be reserved for effects that truly cannot be represented by generic mechanics.

---

# 40. Deterministic RNG

Random systems include:

- deck shuffle
- draws
- Recompose
- any future random effects

Every match should receive a seed.

Example:

```python
game = Game(seed=938471)
```

Logs should preserve the seed.

This allows exact reproduction of bugs and AI matches.

---

# 41. Implementation Plan

---

## Stage 0 — Formal Rules Specification

Goal:

Turn the design into precise executable rules.

Define:

- turn phases
- legal actions
- move timing
- capture timing
- check rules
- card timing
- Trap activation
- summon timing
- Vessel destruction
- Recompose timing
- Building construction
- Ritual activation
- Coronation / Succession
- Duel triggers
- game termination

Define a canonical Action model.

Examples:

```python
MovePiece(...)
SummonMonster(...)
ActivateSpell(...)
ActivateTrap(...)
BuildStructure(...)
ActivateRitual(...)
CoronateKing(...)
ChangeKing(...)
RecomposeHand(...)
EndTurn(...)
```

Deliverable:

A written rules spec + Python Action model.

---

## Stage 1 — Headless Chess Core

Implement:

- board
- players
- standard pieces
- movement
- captures
- check detection
- checkmate detection
- castling
- promotion
- turns

No Pygame required.

Example:

```python
game.execute(MovePiece("e2", "e4"))
```

Deliverable:

A fully testable chess-derived engine.

---

## Stage 2 — RandomBot + Simulation Harness

Before UI development, add:

```python
RandomBot
```

It selects random legal actions.

Purpose:

- validate action generation
- detect crashes
- expose illegal state transitions
- run hundreds/thousands of automated games

Deliverable:

Headless AI-vs-AI games that terminate correctly.

### Stage 2 completion notes

`game/ai/random_bot.py` plus `game/sim.py` (`play_match`, `run_matches`,
and a `python -m game.sim` CLI). Details in §45; telemetry from those runs
in §42.

**Termination is not free.** RandomBot mirrors wander — most finish inside
50 turns, but some pass 400 and a few never converge, because random play
rarely assembles a mate. `--max-steps` abandons those and the aggregator
counts them as *unfinished* rather than as a loss for either side, so a
hung match can never quietly skew a win rate.

**Simulation is slow, and the cost is in the engine, not the bot.** Profiling
a heuristic-vs-random match puts ~85 % of the time in
`chess/movement.py::get_legal_moves`, which `deepcopy`s the whole board once
per candidate move to test whether it leaves the King in check. That is
fine at UI pace (one decision per click) and expensive at simulation pace.
A make/unmake pair in place of the copy is the obvious fix when bulk
balance runs start to matter.

---

## Stage 3 — Minimal Pygame UI

Implement:

- 8×8 board
- pieces
- clicking
- selected-piece state
- legal square highlighting
- basic turn display

No polish.

No animation requirement.

Deliverable:

Playable basic chess through Pygame.

---

## Stage 4 — Main Deck and Hand

Implement:

- Deck
- Hand
- Graveyard
- card drawing
- Monster cards
- Spells
- Traps

Use only a small test set.

Suggested initial pool:

- ~10 Monsters
- ~5 Spells
- ~5 Traps

Deliverable:

Playable chess + card hand.

---

## Stage 5 — Vessels and Monster Transformation

Implement:

- Vessel compatibility
- Monster transformation
- movement inheritance
- Monster effects
- Monster destruction
- optional Dismiss effects

Deliverable:

The first version of the game's central mechanic.

---

## Stage 6 — Spatial Spells and Visible Traps

Implement:

- Trap placement
- visible Trap identity
- activation radius
- trigger detection
- board overlays
- spatial Spell effects

UI overlays should visualize influence.

Deliverable:

Board-based card interaction.

---

## Stage 7 — Recompose & Mercenary

Implement:

- Recompose action
- random return requirement
- card selection
- deck reinsertion
- shuffle
- replacement draw
- deterministic RNG logging
- Mercenary action
- Monster card sacrifice (removed from game, not graveyard)
- cost table: 4 → Pawn, 6 → Knight/Bishop/Rook, 8 → Queen
- placement on own first two ranks
- UI card-selection and square-placement flows

Deliverable:

Endgame hand recovery mechanic + late-game piece recovery via Mercenary.

---

## Stage 8 — Pawns and Buildings

Implement:

- Building Pool
- Building budget
- Builder tokens
- construction timing
- disruption
- completed Buildings
- destruction
- Building influence

Start with approximately 3 Buildings:

- Fortress
- Shrine
- Watchtower

Deliverable:

First permanent board-development system.

---

## Stage 9 — Territory

Implement:

- influence zones
- Building-generated Territory
- Territory queries
- rules referencing Territory

Keep it simple initially.

Deliverable:

Persistent board-development layer.

---

## Stage 10 — King Pool and Policies

Implement:

- three hidden King Cards
- free first Coronation
- active policy
- Succession
- increasing Succession cost
- retired Kings
- no Succession while in check

Start with approximately 6 Kings supporting two archetypes.

Deliverable:

Dynamic kingdom policy system.

---

## Stage 11 — Ritual Pool

Implement:

- hidden Rituals
- Ritual revelation states
- board-pattern matcher
- sacrifices
- Ritual summoning
- partial revelation
- voluntary revelation costs/benefits

Start with approximately 3 Rituals.

Deliverable:

Strategic board-state objectives.

---

## Stage 12 — Final Duel Prototype

Build Final Duel separately and iterate heavily.

Implement:

- capture/checkmate Duel triggers
- board freeze
- 3×3 Duel Arena
- Support calculation
- piece Support abilities
- Building support
- King policy support
- Strike system
- Duel rounds
- Royal Escape
- repeated Duel escalation

Deliverable:

Complete victory loop.

---

## Stage 13 — First Complete Vertical Slice

A match must support:

1. game start
2. hidden King/Ritual setup
3. Main Deck play
4. Vessel summons
5. visible Traps
6. Recompose
7. Pawn construction
8. Buildings
9. Coronation
10. Succession
11. Rituals
12. checks
13. King capture/checkmate
14. Final Duel
15. victory

Do not add major new mechanics during this stage.

Deliverable:

A complete ugly but playable game.

### Stage 13 completion notes

Two things were finished here.

**Every card effect now resolves.** Each effect type declared anywhere in
`data/*.yaml` dispatches to a real handler — no type in the shipped card
pool is missing one, and `test_stage13_effects.py` asserts that as a
standing invariant. The remainder of the "ARMED, DORMANT" backlog was one
cluster with one shared cause: `building_damage_bonus`, `building_aura`,
`building_capture_protection`, `building_damage`, `building_vulnerability`,
`temporary_building_protection`, `repair_building` and `ignore_terrain`
all described a contest over Buildings that had no contest to join. §11.1
–§11.3 above is that contest; implementing it resolved all eight at once.

The four with no shared cause were implemented individually:
`royal_support_bonus` (Rally the Kingdom), `enemy_territory_mobility`
(Shadow Regent / Eclipse Executioner), `ritual_information_discount`
(Arcane Sovereign) and `ritual_support` (Shrine).

**Siege is what makes Ritual Monsters worth the board investment.** Before
Stage 13 a completed Ritual bought a strong unit and nothing structural.
Now it buys the only routine answer to enemy infrastructure — which is
also why Buildings could safely become hard terrain rather than
decoration.

---

# 42. Telemetry and Balance Instrumentation

The PoC should record match statistics.

Recommended metrics:

```text
match duration
turn count
first-player win rate
cards played
cards remaining
Recomposes used
Pawns captured
Pawns sacrificed
Pawns used as Vessels
Pawns used for construction
Buildings built
Buildings destroyed
Rituals revealed
Rituals attempted
Rituals completed
King Coronation turn
King Successions
first check turn
number of checks
Final Duel trigger type
Royal Support score
Final Duel duration
Final Duel winner
archetype win rate
King policy win rate
```

These metrics will be essential for balancing.

### §42 implementation notes

Implemented in `game/telemetry.py`.

Every `Game` owns a `MatchTelemetry` recorder (`game.telemetry`), fed by
`Game.execute()` with each action and the events it produced. It reads
only — same seed, same match, whether recording is on or off
(`Game.new(..., telemetry=False)` disables it). `game.match_stats()`
returns a `MatchStats` snapshot at any point.

Two layers, because the §42 list mixes two kinds of metric:

| layer | class | metrics |
|---|---|---|
| one match | `MatchStats` / `PlayerMatchStats` | everything countable inside a single game |
| many matches | `TelemetryAggregator` | the *rates* — first-player, archetype, King policy |

Rejected actions are counted too (`illegal_action_count`), which is how
`ActivateRitual` interrupted by an enemy `profane_interruption` Trap stays
visible rather than vanishing.

Export: `MatchStats.to_json()`, `TelemetryAggregator.to_json()`,
`TelemetryAggregator.write_csv(path)` — one row per match.

Bulk collection is `game/sim.py`:

```bash
python -m game.sim --matches 50 --white heuristic --black random --csv telemetry.csv
```

---

# 43. AI Architecture

Human and AI players must use the same game API.

Recommended interface:

```python
class PlayerController:
    def choose_action(
        self,
        observation: Observation,
        legal_actions: list[Action],
    ) -> Action:
        ...
```

Controllers:

```text
HumanController
RandomBot
HeuristicBot
SearchBot
AdvancedBot
```

---

# 44. AI Information Rules

The AI must not receive the full internal `GameState`.

It receives an `Observation`.

## GameState

Contains everything:

- both hands
- deck order
- hidden Rituals
- hidden Kings
- RNG state
- all private data

## Observation

Contains only information legally known to that player.

This prevents accidental AI cheating.

This architectural rule should exist from the first implementation.

### §44 implementation notes

The boundary is `core/observation.py`'s `build_observation`, reached only
through `Game.get_observation(player_id)`. `PlayerController.choose_action`
takes `(observation, legal_actions)` — there is no parameter a GameState
could arrive through, and no controller in the codebase has one.

Two properties make it airtight rather than merely conventional:

1. **Nothing private crosses.** Opponent hand IDs, deck order, hidden King
   identities, sealed Ritual identities and the RNG are all absent from the
   Observation's whole object graph.
2. **Nothing crossing is writable.** The Observation is a frozen dataclass,
   and every mutable engine record it exposes — `KingCardState`,
   `RitualState`, `BuildingPoolEntry`, `TrapInstance`, `BuildingInstance` —
   is handed over as a detached copy. A bot cannot reach through its own
   Observation and set `ritual.activated` or `building.integrity`.

Both are asserted in `tests/test_ai_information_rules.py`, which walks the
Observation graph and re-checks the contract for every shipped bot.

One thing deliberately *not* behind the boundary: the `CardRegistry`. It
holds card definitions — the public rulebook a human reads off the cards —
and no match state. Bots may consult it; every bot also works without it.

---

# 45. AI Stage 0 — RandomBot

Behavior:

```python
return random.choice(legal_actions)
```

Purpose:

- test engine stability
- validate legal-action generation
- fuzz unusual states
- run automated simulations

RandomBot is a testing tool, not intended to be a fun opponent.

### §45 implementation notes

`game/ai/random_bot.py`, with its own seeded `random.Random` so bot choices
and game randomness vary independently.

One deviation from the literal `random.choice(legal_actions)`: during
PREPARATION the legal list is dominated numerically by `SummonMonster`
entries (one per card × vessel), so uniform choice would summon nearly
every turn. The bot picks an action *type* uniformly first, then an action
within it; `DismissMonster` sits outside that pool at a flat 5 % so the bot
doesn't spend the match undoing its own summons.

The four stated purposes are covered by `game/sim.py` (engine stability,
legal-action validation, fuzzing, automated simulation):

```bash
python -m game.sim --matches 100 --white random --black random
```

Any action the engine refuses is logged and retried with that action
removed, and a match that will not terminate is abandoned at `--max-steps`
and reported as unfinished — a fuzz harness reports, it does not hang.

---

# 46. AI Stage 1 — Heuristic / Greedy Bot

Evaluate each legal action using a weighted state evaluation.

Possible evaluation categories:

```text
material
king safety
enemy king pressure
board control
monster value
pawn economic value
building value
Territory
Ritual progress
card advantage
hand quality
Royal Support
Final Duel probability
```

Conceptual evaluation:

```python
score = (
    material_score
    + king_safety_score
    + board_control_score
    + building_score
    + ritual_score
    + card_advantage_score
    + duel_score
)
```

The bot selects the action producing the best immediate score.

Deliverable:

A competent non-searching opponent.

### §46 implementation notes

Two files:

- `game/ai/evaluation.py` — the weighted state evaluation. Every category
  listed above is scored, and `evaluation_breakdown()` returns them
  individually so the weights can be tuned against §42 telemetry.
  `EvalWeights` is a frozen dataclass of coefficients — swapping one in is
  how an AI Personality (§51) or a Difficulty tier (§52) will be built.
- `game/ai/heuristic_bot.py` — `HeuristicBot`, the controller.

Because a controller has no simulator (executing a candidate action to see
the resulting state is §47's job), the bot scores

```text
score(action) = evaluate_observation(obs) + delta(action)
```

where `delta` is a per-action-type estimate built from the same weights:
material captured, the square's safety, the strategic system advanced.

Threat awareness comes from `board_from_observation()`, which rebuilds a
`BoardState` from the Observation's public unit list and asks
`chess/movement.py` what each side attacks. Only public information goes
in, so this is what a human staring at the board could work out — it is a
threat map, not a game simulator. It buys the bot free material, avoidance
of hanging its own pieces, and a path around enemy Traps and hazard zones.

Ties break on the bot's own seeded RNG, and a short memory of its own
recent moves discourages shuffling one piece back and forth forever.

A second memory, added while measuring §47, records which Monster
abilities have already fired **this turn**. Activating an ability does not
consume the chess move and several abilities (`inspect_top_deck`,
`burrow`) stay legal after use, so the flat `MONSTER_ABILITY` bias alone
had the bot re-firing one ability every decision and never playing chess
at all — 173 activations and 3 moves in a 200-action sample. Once per
turn per Monster is the cap; both AI stages inherit it.

Selectable per side from the pygame sidebar: each side's button cycles
**HUMAN → RANDOM AI → HEURISTIC AI → SEARCH AI**.

#### Measured baseline

5 matches per pairing, 400 actions each. Mean pieces captured per match,
and matches won outright inside that budget:

| pairing | white | black | wins |
|---|---|---|---|
| heuristic (W) vs random (B) | **13.0** | 3.8 | heuristic 3 |
| random (W) vs heuristic (B) | 2.8 | **14.0** | heuristic 4 |
| random vs random | 5.0 | 5.2 | — |

The advantage follows the bot, not the colour, which is what "competent"
has to mean before any of these weights are worth tuning.

Re-measured after the once-per-turn ability cap above; the first pass
recorded 9.6 / 7.4 captures and no winner at all, because in every match
where the bot summoned a Monster with a repeatable ability it stopped
playing chess. Same weights, same seeds — the difference is entirely the
bot getting to move.

---

# 47. AI Stage 2 — SearchBot

Introduce tree search.

Concept:

```text
My Action
    ↓
Opponent best response
    ↓
My best response
    ↓
...
```

Potential techniques:

- minimax
- alpha-beta pruning
- iterative deepening
- move ordering
- transposition tables

This works well for deterministic visible portions of the game.

However, the complete game contains hidden information and randomness, so pure minimax will not be the final solution.

### §47 implementation notes

Two files:

- `game/ai/search.py` — the search itself: the position, the forward
  model, the leaf evaluation, and negamax with alpha-beta.
- `game/ai/search_bot.py` — `SearchBot`, the controller. It subclasses
  `HeuristicBot`, so everything Stage 1 already decided well is inherited
  rather than re-derived.

**What gets searched.** The paragraph above is the design brief: search
the deterministic visible portion, and nothing else. That portion is the
chessboard, so `SearchBot` searches the CHESS phase and falls back to the
Stage 1 scorer everywhere else. A Summon, a Ritual or a Recompose turns on
cards nobody can see and draws that have not happened; a minimax that
quietly assumes "the opponent holds nothing" would be *worse* there than
the §46 estimate. Those decisions wait for §48's belief model and §49's
sampler.

**The position.** `board_for_search()` rebuilds a `BoardState` from the
Observation — the §46 reconstruction plus the two things the *movement*
rules read and it left out: COMPLETE Buildings (walls) and square effect
tags (`walled:` stops a ray, `frozen:`/`blocked:` remove destinations).
All public (README §35), so this stays inside §44. Traps and hazard zones
are frozen into a `Hazards` map once per decision, since they never move
during a search.

**The forward model.** Moves come from the engine's own
`chess/movement.py`, so Monster movement additions and Building terrain
behave in the tree exactly as they do in the game. `make`/`unmake` mutate
one board in place — copying 64 squares per node is what separates a
depth-2 search from a depth-4 one — and an occupancy map is kept in step
so no node ever walks empty squares. Promotion is auto-Queen, matching
what the bots already choose in the real game.

Inside the tree the search runs on **pseudo-legal** moves and treats
capturing the King as terminal, rather than paying `get_legal_moves`'
per-move board copy at every node. The bot only ever plays actions from
the engine's legal list, so root legality is guaranteed either way. King
capture scores large but finite and decays with ply: it forces the Final
Duel (§22), it does not win the game.

**Techniques, as listed above.** Negamax with alpha-beta; iterative
deepening (so a budget that runs out mid-iteration costs nothing — the
previous iteration's answer stands); move ordering by MVV-LVA with a
promotion bonus, plus the previous iteration's best move first; a
transposition table with proper bound flags at depth ≥ 2; and a
quiescence extension, without which the bot cheerfully hangs a Queen one
ply beyond the horizon.

**Scoring a candidate.** `score(action) = negamax(position_after(action),
depth-1) + bias(action)`. `MovePiece` and `Castle` change the board;
`EndTurn` is a null move (the opponent simply gets the position), which is
what makes "move" and "pass" comparable at all; `AttackBuilding` leaves
the attacker in place (§11.2) and is valued by its Stage 1 bias on top of
the searched position. Because a bias is added after the search, root
alpha windows are widened by a fixed margin so no pruned candidate could
have been rescued by its bias. A `MovePiece` carries almost no bias —
material and safety already came out of the search, and counting them
twice is exactly the bug this design is meant to avoid.

The leaf evaluation is a board-only, strictly antisymmetric subset of the
§46 weights (`EvalWeights` is shared, so a personality tuned for §51 tunes
both bots at once). The card-system categories — Rituals, Buildings, hand
quality, Territory — are constant across a chess search, so omitting them
changes no ranking and costs nothing.

**Budgets.** `SearchLimits(max_depth, max_nodes, max_seconds,
quiescence_depth)` bounds every decision; the pygame UI asks for its move
on a single frame, so it runs a tighter budget (0.6 s) than a headless run
needs to. On a Monster-free board the move generator is called without the
registry — the `movement_restriction` aura check re-scans the whole board
on every call and can do nothing when there are no Monsters — which is
worth roughly a doubling of search speed in the opening.

Selectable per side from the pygame sidebar: each side's button now cycles
**HUMAN → RANDOM AI → HEURISTIC AI → SEARCH AI**, so any two models can be
matched head-to-head, or against a human. Headless:

```bash
python -m game.sim --matches 20 --white search --black heuristic --search-depth 3
```

#### Measured baseline

Same protocol as §46 — 5 matches per pairing, 400 actions each, default
search budget (depth 3, 2500 nodes, 0.6 s). Mean pieces captured per
match, and matches won outright inside that budget:

| pairing | white | black | wins |
|---|---|---|---|
| search (W) vs heuristic (B) | **11.0** | 10.2 | search 1 |
| heuristic (W) vs search (B) | 9.4 | **14.2** | search 2 |
| search (W) vs random (B) | **12.2** | 0.8 | search 3 of 4 |
| random (W) vs search (B) | 0.6 | **7.0** | search 5 |
| heuristic (W) vs random (B) | 13.0 | 3.8 | heuristic 3 |
| random (W) vs heuristic (B) | 2.8 | 14.0 | heuristic 4 |

Read the two metrics together, because they say different things. Against
the Stage 1 bot the material edge is real but narrow as White (+0.8) and
clear as Black (+4.8) — and across those ten matches SearchBot won three
and HeuristicBot none. Against RandomBot it wins eight of nine and takes
*fewer* pieces than the greedy bot does, which is the expected shape: it
declines the losing trades a random opponent lets you get away with, and
its matches end sooner, so there is less time to accumulate captures.

The margin over Stage 1 is narrower than "adds a search tree" suggests,
for two honest reasons. The engine's move generator costs ~0.3 ms a call,
so the default budget completes depth 2 (plus quiescence) in a crowded
midgame and only reaches depth 3 in quiet ones — the ceiling here is
nodes per second, not the search. And chess is one of several systems: a
match is also Rituals, Buildings and card draws, all of which both bots
play with the same Stage 1 code. §48's belief model is what starts moving
those.

One caveat about the fifth match of `search vs random`: it did not finish,
and not because of the bot — a Trap's `push_unit` effect crashed the
engine (`mechanics/effects/movement.py` moves a unit from the square it
*was* on without checking it is still there). Unrelated to AI, reachable
by any controller, tracked separately.

---

# 48. AI Stage 3 — Belief Model

The AI should reason about information it cannot see.

Example:

```text
Observed:

Opponent played:
- 2 Dragon Monsters
- Ritual support Spell
- Dragon-oriented King

Inference:

Likely archetype: Dragon
Possible hidden Ritual:
- Blue-Eyes Ritual: 55%
- Dragon Emperor: 25%
- Other: 20%
```

The AI maintains probability estimates rather than directly reading hidden state.

Belief models may track:

- probable cards in hand
- archetype
- hidden King possibilities
- hidden Ritual possibilities
- likely strategic intention

---

# 49. AI Stage 4 — Monte Carlo / Sampling

For hidden information, sample plausible game states consistent with the AI's Observation.

Example:

```text
Candidate Action A

Simulation 1:
opponent has Ritual X

Simulation 2:
opponent has Ritual Y

Simulation 3:
opponent hand contains Trap support

...

Estimated outcome:
63% favorable
```

The AI then chooses the action with the best expected outcome.

This is likely to be more appropriate than pure minimax for the mature game.

Potential later techniques:

- Monte Carlo Tree Search
- determinization
- information-set MCTS
- rollout policies

---

# 50. AI Stage 5 — Final Duel AI

The Final Duel is largely deterministic and small.

It can use stronger tactical search than the main board.

Because:

- state space is smaller
- no new Main Deck draws occur
- hidden information is greatly reduced
- Duel duration is short

A dedicated Duel search algorithm may therefore be practical.

---

# 51. AI Personalities

Enemy AI should not only vary by difficulty.

It should also have **play styles**.

Evaluation weights can create personalities.

---

## Conqueror

Priorities:

```text
king pressure       high
material            medium
Buildings           low
Rituals             medium
```

Aggressive positional attacker.

---

## Architect

Priorities:

```text
Buildings           very high
Pawn survival       high
King safety         high
Territory           high
Immediate attack    lower
```

Infrastructure-focused.

---

## Ritualist

Priorities:

```text
Ritual progress     very high
material            lower
formation control   high
King safety         medium
```

Willing to sacrifice conventional material to complete Rituals.

---

## Assassin

Priorities:

```text
enemy King isolation   very high
Final Duel probability very high
material               lower
board conquest         lower
```

Attempts to force Final Duel even while behind in material.

---

## Opportunist

Priorities dynamically change depending on:

- board state
- revealed Rituals
- King policy
- current hand
- opponent weaknesses

This could later become the strongest general-purpose AI.

---

# 52. AI Difficulty

Difficulty should primarily affect reasoning quality.

It should not allow cheating.

## Easy

- shallow/no search
- weaker evaluation
- occasionally selects one of top few actions

## Normal

- heuristic evaluation
- 1–2 ply search

## Hard

- deeper search
- better move ordering
- basic belief modeling

## Master

- belief-aware search
- Monte Carlo evaluation
- strong archetype knowledge
- stronger Final Duel search

All difficulties should operate from the same legal Observation.

---

# 53. AI Stage 6 — Self-Play and Reinforcement Learning

Machine learning should come later.

Do not begin with RL.

First stabilize:

- game rules
- legal actions
- balance
- evaluation
- complete matches
- simulation speed

Once the engine can run many headless games:

```text
AI vs AI
10,000+
100,000+
matches
```

self-play becomes useful.

Potential goals:

- discover unexpected strategies
- evaluate balance
- estimate card strength
- optimize heuristic weights
- learn positional value
- discover Ritual patterns
- identify abusive Building combinations
- identify dominant King succession paths

Possible future approaches:

- reinforcement learning
- evolutionary tuning
- policy/value networks
- AlphaZero-style self-play
- offline model training from simulation data

This is an advanced phase, not part of the initial PoC.

---

# 54. LLM Usage

An LLM is not recommended as the core tactical enemy AI.

LLMs are poorly suited to:

- exhaustive legal move generation
- tactical calculation
- deterministic board evaluation
- deep search
- strict rule enforcement

Potential good uses:

- opponent dialogue
- King personality
- tutorial explanations
- commentary
- explaining why the AI made a move
- dynamic campaign flavor
- narrative content
- deck/archetype recommendations

The actual opponent should rely on the rules engine and search/evaluation algorithms.

---

# 55. AI Development Roadmap Summary

```text
RandomBot
    ↓
HeuristicBot
    ↓
Minimax / Alpha-Beta
    ↓
Belief Model
    ↓
Monte Carlo / Information-Set Search
    ↓
Specialized Final Duel Search
    ↓
AI Personalities
    ↓
Self-Play / RL
```

---

# 56. Recommended First Development Milestone

The first real milestone should stop well before implementing every system.

Build:

### Core

- headless rules engine
- standard chess
- legal actions
- deterministic RNG

### Cards

- Main Deck
- Hand
- approximately 10 Monsters
- approximately 5 Spells
- approximately 5 Traps
- Vessel summoning

### UI

- primitive Pygame board
- hand display
- legal square highlighting
- Trap radius visualization

### AI

- RandomBot
- simple HeuristicBot

Do **not** initially implement:

- full Ritual system
- complete Building system
- Territory
- all Kings
- polished Final Duel
- animations
- multiplayer
- campaign
- online matchmaking
- final artwork

The goal is to answer:

> **Is chess + Vessel Monsters + spatial cards actually fun?**

Only after that is proven should the prototype expand toward the complete ruleset.

---

# 57. Design Questions Intentionally Left Open

These should be answered through playtesting rather than prematurely locked.

- exact Main Deck size
- starting hand size
- draw rate
- card-action limits per turn
- precise Monster movement rules
- whether Power Tiers are needed
- precise Tribute rules
- Building Pool budget
- Building construction duration
- Building stacking rules
- exact Territory mechanics
- Recompose probability distribution
- Ritual pool size
- exact Ritual revelation triggers
- Succession costs
- exact check/card timing
- Final Duel arena size
- Final Duel round count
- Support radius calculation
- exact piece Duel abilities
- number of allowed Royal Escapes
- checkmate advantage during Siege Duel
- first-player balance
- archetype restrictions
- copy limits

These should remain configurable during the PoC.

---

# 58. Current Game Identity

The emerging game can be summarized as:

> **A chess-based tactical card battler where pieces become Monster Vessels, Pawns build a persistent kingdom, hidden Rituals create positional objectives, Kings define changing strategic policies, visible Spells and Traps reshape territory, and attacking the King triggers a Final Duel determined by the kingdom the player managed to preserve.**

The intended strategic layers are:

```text
CHESS
Position, movement, material, threat
        ↓
MONSTERS
Transformation and specialization
        ↓
SPELLS / TRAPS
Temporary spatial rule manipulation
        ↓
BUILDINGS / TERRITORY
Persistent development
        ↓
RITUALS
Long-term positional objectives
        ↓
KING POLICIES
Archetype direction and adaptation
        ↓
FINAL DUEL
Payoff for the entire match state
```

The central design objective is that all of these systems should interact with the chessboard rather than replace it.

---


