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

**A garrison does not shield a Building.** A siege targets the structure,
not whoever is standing on it. Because a completed Building is impassable
to the enemy, no attacker can ever step onto that square to remove a
garrison — so treating a garrison as a defensive layer would make every
Building permanently unbesiegeable simply by leaving the Builder Pawn
where it already stands. Raze the structure; the garrison is left standing
on the open square.

---

## 11.3 Building Integrity

Every completed Building has an integrity pool derived from its size:

| Size | Integrity |
| --- | --- |
| Small | 1 |
| Medium | 2 |
| Major | 3 |

A hostile action removes one point — **two** when the attacker is a Ritual
Monster. Siege is the strategic payoff for completing a Ritual, so a
Ritual Monster has to besiege harder than a card you merely drew: at one
point per blow it hit no harder than a Pawn walking into a Siege Order
mark, and strictly softer than Bane of Structures or Molten Colossus. Two
drops a bare Fortress in a single action. At zero, the Building collapses
and its square opens up.

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
owner's end of turn, capped at the Building's maximum — but **not on a
Building that was hit this round**. Nothing gets patched up while it is
still being battered. Without that rule a single Royal Engineer's point
per turn exactly cancelled a besieger's point per turn, and the Building
simply never fell.

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

### Stage 4 completion notes

Implemented as **determinization** — the second technique on that list, and
the one that fits a controller which owns a *board* model rather than a
full game simulator. `game/ai/determinize.py` samples the worlds;
`game/ai/monte_carlo.py` searches them and averages the results.

**A world.** A `Determinization` is one complete guess at what the observer
cannot see: the opponent's hand, one Ritual per pool slot, one King per
pool slot. It is drawn uniformly from the worlds that do not contradict the
Observation. Every card publicly known to be the opponent's is removed from
the pool first — their graveyard, the Monsters standing on their units, the
Traps they have placed. The observer's *own* cards are deliberately not
removed: each Main Deck is an independent sample from the shared pool
(`Game._build_deck_from_registry`), so both players can hold the same card,
and excluding it would be a wrong constraint rather than a cautious one.
Rituals respect their revelation state (§15) — REVEALED is known outright,
FORETOLD is narrowed to the roster entries matching the leaked archetype and
required Vessel, SEALED is open. `false_prophecy`'s `ritual_bluff` fools the
sampler exactly as it fools a human, which is the card working.

**The decision.**

```text
score(action) = mean over sampled worlds w of
                    search(position_after(action) | w)
              + bias(action)
```

The part of a hidden hand that shows up on a *board* is the Monster it can
summon, so that is what a world applies: the sampled hand is walked and the
Monster that reaches the most useful vessel is put there — constrained the
way the engine constrains a real Summon, including §13's own-Territory
requirement, and capped at one because the engine allows one major
preparation action per turn. The position is then searched with ordinary
Stage 2 machinery. Averaging over worlds is the Monte Carlo; the search
inside each one is what makes the average worth taking.

**Which decisions are sampled.** PREPARATION is Stage 4's reason to exist —
§47 handed every Summon, Ritual and Recompose back to the Stage 1 estimator
precisely because searching them in a world where the opponent holds nothing
would be worse than not searching them at all. Those are now judged by the
board they leave behind. CHESS is sampled too, at a shallower per-world
depth: the board is public, so what sampling adds there is narrower (the
threat map of a Monster not summoned *yet*) and depth is worth more than
breadth, which is why the two budgets are configured separately. Everything
else — Final Duel, forced discards, pending follow-ups — falls through to
Stage 2 and Stage 1 unchanged.

One detail that is easy to get backwards: the turn does not pass at the end
of PREPARATION. `EndPreparation` moves the same player on to CHESS, and the
one preparation action is spent *before* the chess move, so a preparation
candidate is searched with the bot itself still to move. A chess candidate
hands the position to the opponent, exactly as Stage 2 does.

**Biases.** Stage 1's action biases assume nothing has been searched, so
reusing them here would charge twice for the same thing — the search already
sees the Monster a Summon produces and the material a Ritual eats. What is
left is the off-board remainder (`SampledBias`: a card leaving the hand, a
Ritual slot spent for good). Anything the world model does *not* apply —
Traps, Spells, Constructions, Coronations, Recompose, Mercenary — keeps its
full Stage 1 estimate, because for those the search genuinely sees nothing.

**Spending the budget.** Worlds are shared across candidates: every
candidate is scored against the same world before the next is drawn, so the
comparison that decides the move is paired and the noise cancels instead of
accumulating. The field is then cut by successive halving (10 → 5 → 2 at the
default fan-out), with survivors keeping their tallies, so the moves still
in contention are the ones measured on the most worlds. The first world
drawn is always the null one — "the opponent has nothing" — so the ranking
stays anchored to the position as it actually is. A world that runs out of
budget half-way is discarded whole rather than averaged in, and if not one
world completes, the decision falls back to Stage 2.

**Favorable.** Each world is also searched with no action taken. An action
is *favorable* in a world when it comes out ahead of passing, and the
fraction of worlds where it does is what `MonteCarloStats.favorable`
reports — §49's "63 % favorable".

**Honest limits.** This is Perfect-Information Monte Carlo and it inherits
PIMC's two known faults: *strategy fusion* (each world is searched as if its
hidden cards were face-up, so the bot credits itself with plans it could not
actually choose between) and *non-locality* (the opponent is assumed to play
the sampled world rather than to hide information). Sampling is uniform over
consistent worlds; §48's belief model is what replaces that with a weighted
draw, and §49's own list — MCTS, information-set MCTS — is what replaces
PIMC itself.

**Budgets.** `MonteCarloLimits(samples, depth, chess_samples, chess_depth,
max_candidates, max_nodes, max_seconds, …)`. The pygame UI runs a tighter
profile than a headless run, as it does for Stage 2: twelve worlds at two
plies for PREPARATION, eight at two for CHESS, inside 0.9 s. Two plies is
not the concession it looks like — §47's own measurement is that the Stage 2
bot only *completes* depth 2 inside its interactive budget in a crowded
midgame.

Selectable per side from the pygame sidebar: each side's button now cycles
**HUMAN → RANDOM AI → HEURISTIC AI → SEARCH AI → MONTE CARLO AI**. Headless:

```bash
python -m game.sim --matches 10 --white montecarlo --black search --mc-samples 16
```

#### Measured baseline

Same protocol as §46 and §47 — 5 matches per pairing, 400 actions each,
seeds 1000-1004 — with the two bots held to comparable per-decision
wall-clock (`--search-seconds 0.6`, `--mc-seconds 0.9 --mc-chess-depth 2
--mc-samples 12 --mc-chess-samples 8`, i.e. the interactive profile). Mean
pieces captured per match, and matches won outright inside that budget:

| pairing | white | black | wins |
|---|---|---|---|
| montecarlo (W) vs heuristic (B) | **13.4** | 10.4 | montecarlo 3 |
| heuristic (W) vs montecarlo (B) | 10.2 | **12.0** | montecarlo 1 |
| montecarlo (W) vs search (B) | **10.6** | 9.6 | search 1 |
| search (W) vs montecarlo (B) | 8.4 | **10.2** | — |

Against Stage 1 the result is clear and it is clear on both sides of the
board: +3.0 pieces as White, +1.8 as Black, and four wins to nil across the
ten matches. That is a wider margin than §47 measured for Stage 2 over the
same opponent, which is the shape to expect — the decisions Stage 4 changes
are the card decisions, and those are most of what separates two bots that
play the same chess.

Against Stage 2 it is a material edge without a win edge: +1.0 as White,
+1.8 as Black, but one win for SearchBot and none for MonteCarloBot. Read
that as "no separation" rather than a loss — nine of those ten matches hit
the 400-action cap undecided, so the win column has one sample in it. The
honest summary is that Stage 4 is ahead of Stage 2 on material and level on
results at this budget, and that a run long enough to decide most matches is
what would actually settle it.

Two costs are worth naming. Sampling is expensive: a `montecarlo vs search`
match averaged 270 s against 109 s for `montecarlo vs heuristic`, and the
per-decision budget is what caps the worlds drawn, not the sample count. And
Stage 4 is spending that budget on breadth at the price of a ply — the
interactive profile searches two plies per world where Stage 2 nominally
searches three. The material numbers say the trade is paying for itself
against Stage 1; against Stage 2 it is roughly a wash, and §48's belief
model — weighting the worlds instead of drawing them uniformly — is the
thing that would make each world worth more than it currently is.

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

## Chess Purist

Priorities:

```text
chess play          everything
material            high
King safety         high
Preparation         declined
```

Ignores the other Preparation mechanics and tries to win on chess alone.

---

## Rogue

Priorities:

```text
early game          chess, and saving cards
late game           every resource at once
switch              as the endgame arrives
```

Hoards its resources while the game is still being built, then spends all
of them once it decides the endgame has started.

---

### §51 implementation notes

Two files:

- `game/ai/personality.py` — the five play styles. A `Personality` is a
  pair of multiplier tables: one over `EvalWeights` (how the *position* is
  judged) and one over `ActionBias` (which *systems* the bot reaches for).
  `weights()` and `biases()` turn a style into the two objects the bots
  already read.
- `game/ai/personality_bot.py` — `PersonalityBot`, the controller. It
  subclasses `SearchBot`, so §51 changes what the bot *wants* and nothing
  about how hard it thinks — that is §52's job. A Conqueror and an
  Architect calculate chess equally well and simply disagree about which
  position they were aiming for.

**A personality is a tilt, not a monomania.** This is the design
constraint the whole module is built around. A Ritualist that never
summons a Monster is not a Ritualist — it is a broken bot that loses to
everything and teaches the player nothing. So a play style can only *scale*
the defaults, and the scaling is clamped twice:

- every evaluation weight stays inside `[0.6, 3.0] ×` its default, so no
  §46 category is ever switched off. An Assassin that "values material
  lower" (§51) still takes a free Queen — lower means 0.6×, never 0×;
- the **staple actions** — Summon, Construction, Ritual, Spell, Trap,
  Coronation, Castle — additionally cannot fall below `0.6 ×` their
  default bias and cannot go non-positive. Whatever the style, the bot
  keeps summoning, keeps building, and keeps taking a free Coronation.

Inheriting the Stage 2 search is the third guarantee: the chess is
untouched, so no play style can tune itself out of playing. Where a style
would otherwise be tempted to skip a system it needs, the tilt goes the
other way on purpose — the Ritualist's `SUMMON` bias is *raised* to 1.35×,
because a Ritual eats bodies and a Ritualist that will not summon has one
plan and no game.

`tests/test_personality_bots.py` asserts both halves: the numeric
guardrails, and the behavioural consequence — for every style, in a real
opening position, a Summon and a Construction still outscore passing.

**Abstention: the one way past the floor.** The Chess Purist's whole idea
is to *not* play the card game, which the floor above exists to prevent.
Rather than weaken the floor for everyone, a style declares what it
declines in `Personality.abstains`, and those biases are set to
`ABSTAIN_BIAS` outright. The distinction is the point: a style cannot
*drift* into refusing to play by tuning a multiplier down, it has to say so
in one auditable place — and a test asserts the Purist is the only style
that says it.

The exemption is safe here in a way it would not be for the others,
because chess is a complete game on its own. A Ritualist that stops
summoning has one plan and no game; a Purist that stops summoning is
`SearchBot`, which §47 already measured as a competent opponent. It is
giving up real advantages — Monsters make pieces stronger — and that is
the trade the player picked. Coronation is deliberately *not* abstained: a
King is a chess piece, and §17 makes crowning one free.

**Adaptive styles, part two.** The Rogue is written the same way the
Opportunist is — as a blend — but over two private *stances* rather than
over the other archetypes. `_STANCES` is what a blend may name: the play
styles, plus stances like `rogue_hoard` / `rogue_spend` that nobody picks
off a menu. `endgame_pressure()` scores how far into the endgame the board
is, in `[0, 1]`, and the Rogue blends hoarding into spending by it.

That pressure is built only from signals that do not go backwards —
material still on the board, the turn number, the deck draining, Rituals
coming out from under their seals. A King in check is deliberately *not*
one of them: it flickers, and a Rogue that dumped its hand on a check and
then wished it hadn't is the failure this style has to avoid. Monotonic
signals mean the commitment is effectively one-way without needing a latch
to enforce it. And it is a ramp rather than a switch, so the hand starts
opening as the endgame approaches instead of flipping at a threshold
nobody can see.

**The Opportunist** (§51: "priorities dynamically change") is a *blend* of
the other four rather than a table of its own. `opportunist_mix()` reads
the Observation and votes: behind on material it leans Architect, with its
own Ritual REVEALED it leans Ritualist, with the enemy King hemmed in or
the opponent's Ritual revealed it leans Assassin, ahead on material it
leans Conqueror. Every component starts at 1.0 and is capped at 4.0, so
the strongest signal on the board still leaves each other style ~14% of
the vote — the Opportunist adapts, it does not convert. The mix is
recomputed once per **turn**, not per decision, so one turn's Summon,
Ritual and chess move are all decided by the same personality instead of
drifting mid-turn. Information rules (§44) are untouched: the re-mix reads
the same Observation the bot is already deciding from, and nothing else.

Selectable per side from the pygame sidebar. The selector used to cycle one
step per click; with four AI stages, five play styles and the human on the
roster that meant walking a ten-entry loop blind, so the button now opens a
**player picker** listing all of them at once:

```text
Random        (Idiot)     Picks a legal action at random.
Heuristic     (Easy)      Scores every legal action once.
Search        (Medium)    Searches the board a few moves ahead.
Monte Carlo   (Hard)      Guesses your hand, then searches.
── Personalities ──
Conqueror                 Aggressive positional attacker.
Architect                 Builds, holds ground, and outlasts you.
Ritualist                 Sacrifices material to complete Rituals.
Assassin                  Hunts the King to force the Final Duel.
Opportunist               Re-reads the board and re-mixes the other four.
Chess Purist              Wins at chess. Declines the card game.
Rogue                     Hoards its cards, then spends everything late.
Human                     You play this side yourself.
```

The two halves of the list are the two halves of the AI design: the top
group is §52 difficulty — how hard the bot thinks — and the group under the
heading is §51 play style — what it thinks *about*. A bare name carries
neither ("Monte Carlo" does not read as *hard*, "Assassin" does not read as
*goes for your King while losing*), so every row carries a difficulty word
or a one-line description, and the hovered row's behaviour — a §51 priority
table, or how the stage actually plays — is spelled out underneath.

`_PLAYER_ROSTER` is the single source of truth for that list and for the
sidebar button's own label, so adding a controller is one entry and nothing
else. Row height and spacing are computed rather than fixed — twelve rows
plus a detail panel do not fit a 746 px window at comfortable spacing, so
the dialog tightens its rows to fit and drops detail lines last, on the
principle that a row the player cannot see is worse than a description
they have to hover twice for. Opening the picker commits nothing and freezes the match while it is
open; Cancel, ESC and clicking away all leave the side untouched. Once
chosen, the button shows the style rather than the stage ("♛ RITUALIST AI").
Headless:

```bash
python -m game.sim --matches 10 --white personality --white-personality ritualist --black heuristic
```

#### Measured: every style still plays the whole game

The guardrails are only worth having if they show up on the board. 3
matches per style as White against HeuristicBot, 300 actions each, depth-2
budget. **The White side's own numbers**, per match — `player_mean(attr,
"white")`, not the match average, which would fold the opponent's play into
every row:

| style | Vessels summoned | Buildings built | Rituals completed | cards played |
|---|---|---|---|---|
| Conqueror | 4.7 | 4.7 | 0.0 | 27.7 |
| Architect | 2.7 | 5.0 | 0.3 | 26.7 |
| Ritualist | 5.0 | 4.7 | 0.3 | 26.3 |
| Assassin | 6.0 | 4.0 | 0.3 | 25.3 |
| Opportunist | 5.3 | 4.3 | 0.7 | 23.7 |
| Rogue | 2.7 | 4.3 | 0.7 | 26.0 |
| **Chess Purist** | **0.0** | **0.0** | **0.0** | **0.0** |
| *(plain SearchBot)* | *5.7* | *5.0* | *1.0* | *25.3* |

For the six styles that did not declare an abstention, no column has a zero
in it. The Ritualist builds as many Buildings as the Conqueror does and is
among the heaviest summoners — which is the point: it needs the bodies. The
Architect summons the fewest, because its Pawns are worth more standing as
builders than spent as Vessels, and that is a tilt showing up as a play
style rather than as a missing system.

The Chess Purist's row of zeros is not a bug; it is the abstention working,
and it is the reason abstention had to be a declaration rather than
something a multiplier could reach. Its `cards played` of 0.0 against
SearchBot's 25.3 is the whole difference between the two, since everything
else about them is the same search.

The columns are too close together to call this a personality *strength*
measurement, and it is not meant to be one — §51 is about play style, §52
is about strength. What it measures is the constraint the module is built
around: whatever the style, the bot still plays every system on the board
unless it said outright that it does not.

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

### §53 groundwork notes

Not the machine learning. §53 is emphatic that it should not be started
yet — "machine learning should come later", "do not begin with RL" — and
that stands. What is built here is what the section says to build first,
because §53 is the only part of the AI roadmap that opens with a list of
preconditions rather than a design:

> First stabilize: game rules, legal actions, balance, evaluation,
> complete matches, simulation speed.
>
> Once the engine can run many headless games — 10,000+, 100,000+
> matches — self-play becomes useful.

Measured against its own gate, the engine failed both halves of that
sentence. A RandomBot self-play match took **134 seconds**, and **half of
them never produced a result at all**. Ten thousand matches — the smaller
of the two numbers §53 names — was eight days of a four-core machine, and
half of what came out would have carried no label for anything to learn
from or measure.

Four pieces of work closed that. None is a learning algorithm; all of them
are what a learning algorithm would otherwise have starved on.

#### 83 % of the engine's life was spent copying the board

`chess/movement.py` `get_legal_moves` answered "does this move leave my own
King in check?" the obvious way: `deepcopy` the board, play the move on the
copy, ask. Once per candidate destination, for every piece, every time
anything asked for a legal action — and `get_legal_actions` asks for the
whole army.

A profile of 600 headless steps: **348 s total, 287 s of it inside
`copy.deepcopy`, 15.9 million calls.** Legal-action generation was 85 % of
the step loop and the deep copy was 83 % of the entire program. Nothing
else was within an order of magnitude, and it got worse as a match went on —
throughput fell from 31 steps/s in the opening to 11.5 by turn 160, because
a board carrying more Monsters is a more expensive board to copy.

It is now make/unmake (`_leaves_king_in_check`). The copy-based version
performed exactly three mutations — vacate the source square, vacate the
en-passant victim's square, occupy the target — and all three are plain
`SquareState.unit` assignments, so saving those three slots, asking the
question, and putting them back reproduces the identical board. Nothing
reached from the check test mutates: `is_in_check` and
`get_pseudo_legal_moves` only read.

"Identical" is the claim the optimization lives or dies on, so it is tested
as one rather than asserted. `TestCheckFilterEquivalence` keeps the original
deep-copy implementation as the reference answer and compares the two for
every unit of every position of a real self-played game — 200+ positions
per run, including the Monster-altered movement and the pinned pieces
nobody thinks to write a fixture for — checking after each comparison that
the board came back unchanged. En passant, the one case where the captured
piece is not on the target square, gets its own position.

The same match, the same seed, the same 1556 steps: **134.0 s → 12.6 s.**
The test suite went from 538 s to 105 s on the way past.

#### Half of every self-play sample had no result

Twelve RandomBot matches: six reached a winner, six ran out the harness's
4000-step abort. §53's list wants "complete matches", and a corpus where
half the games have no outcome is half a corpus — every use in §53's own
list, from "estimate card strength" to "policy/value networks", needs to
know who won.

Instrumenting the runaways found **three unrelated causes**, two of them
rules bugs that had been sitting in the engine unnoticed because no bot
had been left alone with them for long enough.

**A Monster ability could be fired forever.** The worst offender spent 3370
`ActivateMonsterAbility` actions on a single board position that repeated
3362 times. Activating an ability does not consume the chess move and
several abilities stay legal after use, so the same one can be fired
indefinitely without the turn ever advancing. §46 had already met this
exact pathology — "173 activations and 3 moves in a 200-action sample" —
and fixed it *inside the bot*, with a per-turn memory in `HeuristicBot`.
The rules never had the cap. RandomBot does not self-limit, so it stalled;
and a human at the pygame board could have done the same thing.

The cap now lives where it belongs: once per turn per `(unit, ability)`,
keyed on the piece ID so moving the unit does not buy a second activation,
recorded in `PlayerState.abilities_used_this_turn` and cleared by
`reset_turn_flags` alongside the other per-turn flags. `get_legal_actions`
stops offering a spent ability and `RulesEngine` refuses one built by hand,
so the rule holds against a caller that never consults the action list. The
bots' own memories are now belt-and-braces rather than the only thing
standing there. That match: 4000 steps and no result → **733 steps and a
decisive winner**.

**A pending decision could become unresolvable.** blade_dancer's
after-capture reposition raises a REPOSITION `PendingDecision` about a
specific piece on a specific square, and CHESS then offers *nothing but*
that reposition until it is resolved. If the piece is captured or moved in
between — a Trap firing on the same square will do it — every option points
at an empty square, every attempt is refused, the decision is never
cleared, and the player is locked out of their own turn for the rest of the
match. It showed up as 3791 `RepositionUnit` attempts against "No piece at
a6". A decision about a piece that is not there has nothing left to decide,
so `_reposition_is_stale` now detects it: `get_legal_actions` declines to
offer it and falls through to ordinary play, and `execute` drops it. This
one survived the first two fixes and was still stranding two matches in
forty.

**And nothing bounded a match.** §31's endings are all *events* — a King
captured, a checkmate, a stalemate, a Duel won — and none is guaranteed to
arrive. Checkmate and stalemate hand off to the Final Duel rather than
ending the game, Royal Escape sends a survived Duel back to the board, and
underneath it all there is no threefold repetition and no fifty-move rule.
Two sides that shuffle can decline every ending indefinitely, and the
sample's remaining runaways did: 316, 508, 522 and 530 turns.

`MatchLimits` (in `core/state.py`, per-`Game`, overridable through
`Game.new(limits=...)`) is the floor chess has always had, extended to the
systems this game adds:

- **repetition** — the same piece placement with the same side to move,
  three times. Hands and decks are deliberately *not* in the key: they
  cycle every turn, so folding them in would mean no position ever recurred
  and the rule could never fire.
- **no progress** — 80 plies with nothing irreversible happening. A capture
  cannot be undone, a Pawn cannot walk back, and a completed Building, an
  accumulated Ritual and a crowned King all stay done, so each of them
  counts as progress. This is the fifty-move rule with the kingdom
  included.
- **turn ceiling** — 300, as a backstop behind both.

§57 lists numbers like these as deliberately open, so they are data rather
than constants buried in the engine.

A match stopped by one of them is a **draw** — `winner` stays None, the
phase goes to GAME_OVER, and `GameOver.reason` names which limit fired.
This is the one place the game admits a drawn match, and it is worth being
explicit about why it is a draw rather than an adjudication. §31 routes
every ending through the Final Duel precisely so a match is decided by the
kingdom each player managed to preserve; awarding a stagnation to whoever
happens to lead on material would decide it by a rule the design never
made, and would quietly teach anything trained on the corpus that material
is the tiebreak. A draw records what actually happened: neither player got
there.

Telemetry follows. `decided` / `drawn` / `unfinished` now partition a run,
so "the rules produced a draw" and "the harness gave up" stop being the
same number.

Eighty matches across both bots after all three fixes: **eighty results,
zero unfinished.**

#### The corpus runner

`game/selfplay.py`. `game.sim` already plays a match and gathers §42
telemetry; what it could not do is survive the scale §53 asks for.
`run_matches` is a single-process loop that accumulates in memory and
returns nothing until the last match lands, so a run long enough to matter
is also long enough to be interrupted — and an interrupted run was a total
loss.

    parallel    one worker per core; matches are independent and the GIL is not.
    sharded     each worker appends to its own JSONL file.
    streaming   one line per match, flushed as it completes.
    resumable   a seed already on disk is not replayed.

Rerunning an interrupted command finishes the run instead of restarting it,
and raising `--matches` extends a corpus rather than rebuilding it. Seeds
are dealt round-robin rather than sliced contiguously, because match cost
varies by a factor of five with the seed and dealing stops the long ones
stacking into the last chunk.

Two files come out:

    matches-NNNN.jsonl     one line per match — seed, who played, how it
                           ended, and its full §42 telemetry.
    decisions-NNNN.jsonl   one line per decision (--trajectories) — the
                           position as §46's evaluator saw it, how many
                           actions were available, and which was chosen.

The decision row is deliberately not a serialized Observation. An
Observation is a deep object and there are hundreds of decisions per match;
written out whole it would make a corpus expensive to produce, expensive to
read, and still not in the shape a model wants. §46's
`evaluation_breakdown` is already a fixed-width, named feature vector over
exactly the systems this game is about — material, king safety, board
control, Monsters, Pawn economy, Buildings, territory, Rituals, cards, hand
quality, Royal Support, Duel probability — so that is what a row carries.

`iter_examples()` joins decisions to the outcome of the match they came
from and labels each from the acting player's side: `+1` won, `-1` lost,
`0` drawn. Decisions whose match has no recorded outcome are dropped rather
than labelled with a guess. That is §53's "offline model training from
simulation data", one function call from the corpus on disk.

```bash
python -m game.selfplay --matches 10000 --out corpus/ --white heuristic --black heuristic
python -m game.selfplay --matches 2000  --out corpus/ --white search --black search --trajectories
python -m game.selfplay --out corpus/ --summary-only
```

#### Weights that can leave the process

`game/ai/weights.py`. §53 lists "optimize heuristic weights", and §46's
evaluation was already written as data — `EvalWeights` is a flat dataclass
of floats, `ActionBias` a flat table of them. What was missing was the way
in and out: an optimizer runs *outside* the process that plays the match,
so a candidate vector has to survive a file and a command line.

`load_weights` / `save_weights` read and write JSON, either flat
(`{"material": 1.2}`) or sectioned (`{"weights": {…}, "biases": {…}}`). A
file may be partial — anything it does not mention keeps its default, so a
search over three weights is a three-line file instead of a copy of the
whole table that goes stale the next time a weight is added. `weights=` and
`biases=` now run through `make_controller` to HeuristicBot, SearchBot and
MonteCarloBot, and `--white-weights` / `--black-weights` reach them from
the command line.

These take **absolute values and apply no clamps**, which is the one place
they deliberately differ from §51's `weights_from_tilt` and
`ActionBiasSet`. Those take multipliers and clamp them, for a good reason:
a play style that switched an evaluation category off would stop playing
the game. Tuning is the opposite problem — an optimizer has to be free to
propose the unreasonable, and silently clamping a proposal would mean
scoring a different candidate than the one that was measured. Passing both
a personality and a weight file to the same side is refused rather than
resolved, since both set the same table.

#### Measured: what §53's gate costs now

Same seeds before and after, four cores:

| | before | after |
|---|---|---|
| one RandomBot match, 1 core (seed 1000, 1556 steps) | 134.0 s | 12.6 s |
| 40 RandomBot matches, 4 cores | — | 198 s |
| 40 HeuristicBot matches, 4 cores | — | 147 s |
| matches that reached a result | 6 / 12 | 80 / 80 |
| full test suite | 538 s | 105 s |
| **10,000 matches, 4 cores** | **~8.1 days** | **~10–14 hours** |
| **100,000 matches, 4 cores** | **~81 days** | **~4–6 days** |

RandomBot draws 45 % of its games and HeuristicBot 20 %, which is the
right way round and worth reading as a first result rather than a defect —
a bot that never plans is exactly the one that shuffles into a repetition,
and the gap between the two is §46's evaluation showing up as the ability
to actually finish a game. Mean match length tells the same story: 147
turns for RandomBot, 70 for HeuristicBot. None of this was visible before,
because the games that would have shown it were the ones being abandoned.

10,000 matches is now an overnight run rather than a fortnight, and 100,000
a long weekend rather than a quarter. §53's gate is met.

#### What this does not do

Three gaps, in the order they matter:

- **§48's belief model is still unbuilt**, and §49's determinization still
  says so in its own docstring — sampling is uniform over consistent
  worlds. §55 puts the belief model before self-play; it is skipped here on
  the argument that self-play is a good way to *produce* the priors it
  needs, which is a real argument and also a convenient one.
- **§50's Final Duel search is still unbuilt.** Duel decisions fall back to
  §46's static bias table. The Duel's own round caps are what stop it
  looping, not any judgement about how to play it.
- **The next bottleneck is already visible.** With the deep copy gone,
  `get_pseudo_legal_moves` is 76 % of the remaining runtime, nearly all of
  it called from `is_in_check`, which generates every move of every enemy
  piece to ask whether one of them lands on the King. Asking the question
  from the King's square outward is the standard answer and is worth
  roughly another 2–3×. It is not done here because this game's Monsters
  alter movement patterns, so a reverse-attack test is not obviously
  symmetric and would need its own equivalence proof — the same one
  `TestCheckFilterEquivalence` exists to provide.

And the learning itself, which is still where §53 says it should be: later.

---

### §53 corpus analysis

With the gate met, the first thing worth doing with self-play is the thing
§53 lists first — and it is not learning:

> Potential goals:
> - discover unexpected strategies
> - evaluate balance
> - estimate card strength
> ...
> - discover Ritual patterns
> - identify abusive Building combinations
> - identify dominant King succession paths

Every one of those is a question about a pile of finished matches, and
every one is answerable by counting. `game/analysis.py` is the counting.

```bash
python -m game.selfplay --matches 1000 --out corpus/heuristic-1k \
    --white heuristic --black heuristic --trajectories
python -m game.analysis --corpus corpus/heuristic-1k
```

#### Telemetry was counting cards without naming them

§42's list is thorough about *how many* — cards played, cards remaining,
Rituals completed, Buildings built — and silent about *which*. Every event
already carried the id (`MonsterSummoned.card_id`,
`BuildingCompleted.building_card_id`, `RitualActivated.ritual_id`); the
recorder was throwing it away, because §42 never asked for it and §53 had
not been started.

`PlayerMatchStats` now also keeps `cards_played_by_id`,
`cards_drawn_by_id`, `buildings_built_by_id`, `rituals_completed_by_id` —
and `deck_card_ids`, captured by `MatchTelemetry.record_opening` before the
first event, because the deal happens in `Game.new` and nothing downstream
could reconstruct it.

#### The deal is the denominator

That last field is the whole methodology, and it is worth spelling out
because the obvious alternative is wrong in a way that looks fine.

The tempting way to rate a card is *how often does the side that plays it
win*. That number is close to meaningless: a bot plays a card because §46's
evaluation liked the position it was in, so the card's win rate is
contaminated by every reason the player was already winning. It measures
the bot's taste, not the card.

Each player's 20-card deck is instead sampled at random from the shared
pool, independently of anything either side does — `Game.
_build_deck_from_registry` picks it before the first move. That is a
randomised assignment, and it is the only reason a self-play corpus can
support a causal claim at all. So the primary number is conditioned on
**the card being in the deck**: *players dealt this card scored X, players
not dealt it scored Y.* The play-rate is reported beside it as a
description of the bot, which is the only honest thing it can be.

The sections that follow — Rituals, Buildings, Kings — are labelled
**correlational** in the report itself, because a Building is chosen and a
Ritual is achieved. A Building pair that wins may simply be the pair a
winning player had time to finish, and the report says so rather than
letting the reader assume the card-strength method extends to it.

`tests/test_ai_stage6_analysis.py` plants a card that is inert except that
the winner happens to play it, and asserts the metric refuses to rank it —
a test the contaminated version fails and the means-only version passes.

#### Reading the report

Every rate carries its sample size and standard error; every comparison
carries how many standard errors the difference clears. Effects that do not
clear two are reported as noise rather than quietly ranked, and both
thresholds move from the command line so a reader can check how much of a
finding survives being asked for more evidence.

A drawn match scores ½ for both sides, the convention chess ratings use.
Draws are 20 % of HeuristicBot self-play, and dropping them would discard
exactly the matches where a card most plausibly failed to break a deadlock.

One statistical trap is worth naming because the naive formula gets it
backwards: when both groups have zero spread, the standard error is zero,
and `|Δ| / 0` was being reported as zero significance. Perfect separation
is the *most* certain a comparison can be, not the least. It now reports a
capped maximum — capped rather than infinite so the JSON report stays valid
JSON.

#### First finding: two cards that could never be played

The corpus earned its keep on the first run, and not in the way the goal
list advertises. Two Spells sat at a **0 % play rate across ~100 deck
appearances each**, while scoring measurably below average — which is
exactly what carrying a dead card in a 20-card deck does to you.

    knightfall          Δ-0.104  (2.2σ)   play rate 0 %
    evacuation_order                      play rate 0 %

Neither was a balance problem. Both were unplayable.

`_reposition_unit` (mechanics/effects/movement.py) implements four modes,
selected by the params each card declares, and its docstring describes all
four. `get_legal_actions` enumerated only one of them — the plain
`max_distance` box:

- **knightfall** declares `piece_types: [knight]` and
  `movement_pattern: knight`. The enumeration ignored both, so it offered
  Pawns and adjacent squares. Every offer came back
  *"This Spell can only target: knight."*
- **evacuation_order** declares `target: own_king` and exists to move the
  King one square. The enumeration's blanket "never the King" rule — right
  for every other reposition card — skipped the only piece this one can
  target, so it had no legal action at all, ever.

Both handlers were correct and had been all along; only the offer was
wrong. A card whose effect is implemented perfectly is still dead if
nothing ever proposes a legal way to play it, and no test caught it because
every test drove the handler directly rather than asking what the action
generator would offer.

The enumeration now reads the same params the handler validates against,
and the invariant is stated as a test in
`tests/test_ai_stage6_selfplay.py`: **an offered action is one the rules
accept.** In live self-play the two cards went from 0 % to 45 % and 70 %
play rates. In the 1,000-match corpus below they are ordinary cards:
`knightfall` −0.038 (1.3σ), `evacuation_order` −0.031 (1.0σ), both now
noise, and the corpus's "never played" list is empty.

This is the shape of finding §53 promises and the reason it lists
"estimate card strength" as a *goal* rather than a technique. Nobody was
going to notice by playing: two cards out of a 117-card pool, each drawn
about a third of the time, quietly doing nothing. It took a thousand
matches counting who held what.

#### Measured: 1,000 HeuristicBot self-play matches

`--matches 1000 --white heuristic --black heuristic --seed 100000`, four
workers, 51 minutes, 999 finished and one crash (below). 1,998
player-observations, 557,297 decision rows.

**Balance is better than it had any right to be.** First-player advantage
is +0.005 ±0.020 — 0.2σ, indistinguishable from nothing across two
thousand observations. §57 lists "first-player balance" as an open
question; on this evidence there is no problem to solve. Archetypes span
0.478 to 0.516, every one of them inside two standard errors of even.
Buildings — singly and in pairs — produce nothing above the noise floor at
all.

    draw rate      17.4 %
    match length   mean 60.9, median 50, p10 32, p90 104
    ended by       final_duel_victory 825, repetition 118,
                   no_progress 55, turn_limit 1

**Two cards are not balanced at all.**

| card | dealt | not dealt | Δ | σ |
|---|---|---|---|---|
| `seal_of_lockdown` | **0.826** (215W/16D/39L) | 0.449 | +0.377 | 15.5 |
| `dread_tide` | 0.709 (158W/51D/50L) | 0.469 | +0.239 | 8.9 |
| everything else | — | — | ≤ 0.104 | ≤ 3.9 |

A player who is *dealt* `seal_of_lockdown` — not one who plays it well, one
who is handed it by the shuffle — wins 80 % of their decided matches. The
gap between it and the third-strongest card in the pool is larger than the
gap between the third-strongest and the weakest. Whatever else the balance
numbers say, one card in 117 is deciding a quarter of all matches by
itself, and no amount of play skill on the other side is visible against
it.

`dread_tide` is the same shape, less extreme.

Nothing else clears +0.11. The pool below those two looks healthy.

**One card the bot won't touch.** `vessel_reclaimer` has a 15 % play rate
where every other significant card sits between 82 % and 98 %, and it
scores −0.060 (2.2σ) when dealt. Those two facts together do not say the
card is weak — they say the card is a dead slot *in this bot's hands*,
which is a §46 finding as much as a card finding. Whether a human would
play it is exactly the kind of question self-play cannot answer.

Except it turned out not to be about that card at all. Grouping every
Monster by the Vessels it can ride explains almost the whole spread, and
`game.analysis` now reports it as its own section:

| supported Vessels | cards | play rate |
|---|---|---|
| any group including `pawn` | 26 | 93–95 % |
| `bishop/knight`, `knight/rook`, `queen/rook`, `bishop/rook` | 19 | 28–43 % |
| `bishop/queen` | 6 | 13–17 % |
| `queen` only | 1 | 4 % |

`vessel_reclaimer` at 14.6 % is not an outlier — it is the middle of its
own group. A player has eight Pawns, two Bishops and one Queen, so Vessel
availability decides how often a Monster can be played at all, nearly
independently of what the Monster does. A Queen-only Monster is a dead
card in hand nineteen games in twenty however good it is when it lands.

That is a property of the pool rather than of any card in it, and it is
not something to fix without deciding first whether it is intended — a
restrictive Vessel is a legitimate cost, and six cards priced identically
at 15 % may be exactly the design. It is recorded rather than acted on.

**Rituals split.** `vow_of_desperation` +0.115 (4.6σ), `sevenfold_circle`
−0.117 (3.3σ). Completing any Ritual at all is worth +0.039 (1.6σ) — real
but not yet established, and worth re-measuring on a larger corpus.

**Kings spread by about a tenth.** `grave_crowned_king` 0.549 at the top,
`dragon_high_king` 0.457 at the bottom, with the other four between. Only
the first clears 2σ on its own.

**And a design observation nobody asked for.** All 825 decisive matches
ended `final_duel_victory` — every single one. Not one was decided by
anything else. §31 describes the Final Duel as the payoff for the match
state; in practice it is not a payoff, it is the *only* win condition, and
the 918 duels triggered across 999 matches say most games reach it more
than once (Royal Escape sends the survivor back to the board). Whether
that is the intended shape of the game is a design question, but it is now
a design question with a number attached.

#### The corpus also crashed a match

One seed in a thousand died with `ValueError: No unit at source square
h2` — and the bug under it is a chess rule, not a card.

En passant was detected by asking only "is this Pawn landing on the
en-passant square?" A *straight push* can satisfy that too. White
double-pushes h2→h4, which sets the en-passant square to h3; tunnel_mole's
burrow later puts a White Pawn back on h2; White pushes h2→h3. Target
equals the en-passant square, so the engine computed the captured square
as one rank behind the target — h2, the mover's own square — removed the
moving Pawn, and then crashed trying to move a piece that was no longer
there.

En passant is a capture, so the file always changes. That test is the fix,
along with the matching one from the other side: the square behind the
target has to actually hold an enemy Pawn. Both are now in
`tests/test_ai_stage6_selfplay.py`, together with the real en passant that
must keep working.

A human could have hit this. It needed a Pawn to leave and return to the
square its own double-push had vacated, which is why nobody had — and why
a thousand matches found it in an afternoon.

#### Acting on it: two cards changed, and the same 1,000 matches re-run

Both changes follow a rule the game had already written down somewhere
else, which is the only kind of balance change worth making from a
measurement — a number tells you *something* is wrong, not what the right
answer is, and "make it 0.7× as good" is a guess dressed up as a decision.

**`seal_of_lockdown`: the King is exempt.** The mechanism was not that the
card was strong, it was that the card could *end the game*. `seal_zone`
gives a sealed enemy unit zero legal destinations, and "zero legal
destinations" is precisely the input checkmate and stalemate detection
reads. A seal laid over the enemy King manufactured a terminal position on
demand: with a check that is checkmate (SIEGE), without one it is
stalemate (LAST_STAND), and either way the sealing player chose the moment
the Final Duel began — the moment that decides the match. Sealing a King's
3×3 took it from two legal moves to none.

The exemption is not a special case invented for this card. `damage_unit`
already says "Kings are never damaged", and every other effect that
removes a piece steps around the King. A Spell was never meant to be able
to end the game outright; this was the one that could. The card still
paralyses everything else in its zone, which is what it was for.

**`dread_tide`: radius 2 → 1.** A 5×5 is 39 % of the board, and this was
the pool's only mass-removal card. It was also the only spatial Spell above
radius 1 in the entire pool — every other one is 0 or 1 — so this is a
return to the game's own ceiling rather than a new ceiling imposed on it.

**Re-measured on the same seed**, so both corpora deal the same cards to
the same players and the only difference between them is the rule change:

| | before | after |
|---|---|---|
| `seal_of_lockdown` score when dealt | 0.826 | 0.659 |
| `seal_of_lockdown` Δ | +0.377 (15.5σ) | +0.184 (7.3σ) |
| `dread_tide` score when dealt | 0.709 | 0.646 |
| `dread_tide` Δ | +0.239 (8.9σ) | +0.168 (6.5σ) |
| `siphon_of_power` (untouched control) | +0.104 (3.9σ) | +0.112 (4.4σ) |
| **strongest ÷ third-strongest card** | **3.63×** | **1.65×** |

The last row is the one that matters. Before, one card in 117 was worth
three and a half times the third-best card in the pool; now the top of the
curve looks like a curve. The untouched control moved by 0.008, which is
what "the rest of the pool was not disturbed" looks like.

#### The nerf had a side effect, and it is the more interesting result

| | before | after |
|---|---|---|
| draw rate | 17.4 % | **25.0 %** |
| mean match length | 60.9 turns | 69.7 turns |
| decisive matches | 825 | 750 |
| duels by SIEGE (checkmate) | 108 | 70 |

A quarter of matches now end on a §53 termination limit rather than a
result. The seal was not only winning games for whoever held it — it was
*ending* them, for both players, and removing it took eight turns of
stalling out of the average match along with 40 % of the checkmates.

This lands on the structural finding above rather than on either card. The
Final Duel is the only win condition the engine has — `state.winner` is
assigned in exactly two places and both are the Duel — and reaching one
requires successfully attacking a King. Strip out the card that was forcing
that to happen and HeuristicBot visibly struggles to force it any other
way: more repetitions, longer games, fewer checkmates.

So the honest reading of the first balance pass is not "two cards were too
strong". It is **the game's decisiveness was leaning on a card that could
end it, and underneath that card the engine does not have many ways to
close a match out**. That is a design question, not a tuning one, and it is
the question the next corpus should be aimed at rather than another
multiplier on another card.

`dread_tide` at +0.168 and `seal_of_lockdown` at +0.184 are now within
striking distance of `siphon_of_power` at +0.112, and further trimming
would buy a point of balance at the cost of another point of draw rate.
Stopping here is a judgement, and it is recorded here so the next person
can disagree with it on the same evidence.

#### Third finding: the bot was crowning a King at random

Asking why a quarter of matches now end in a draw sent the corpus at the
question "what does a stuck player have left to try", and one of the
answers came back empty:

    king_successions: {0: 3998}          every player, every match

Every player crowns exactly one King and never succeeds to another. §17–§19
describe a whole system — escalating costs, retired Kings that cannot
return, a three-card pool to choose from — and §53 lists "identify dominant
King succession paths" as a goal. No path in 3,998 player-matches ever had
a second King on it.

It was not a rules gap this time. `ChangeKing` is offered at **19.2 % of
decision points** and refused by nobody. It was two flat constants in §46's
scorer:

- `ChangeKing` returned `ActionBias.SUCCESSION` — a bare −1.5 — and nothing
  else. No upside term of any kind. `END_PREPARATION` is 0.0, so no
  Succession could ever outscore passing; the action was unreachable by
  arithmetic, not by judgement.
- `CoronateKing` returned a flat `CORONATION` of 5.0, so the bot crowned
  whichever of its three Kings the action list happened to offer first.

The second is the one that mattered, and it is the more embarrassing.
Measured: **the first Coronation matched the player's own archetype 1 time
in 12.** The King Pool is built to guarantee the player's home King is in
it (mechanics/kings.py), and the bot was crowning past it five times in
six.

Underneath both: the evaluator did not look at Kings *at all*. Thirteen
categories in `evaluation_breakdown`, not one of them about which King was
wearing the crown. So `king_policy` is now a fourteenth — a King is worth
having, worth more when its `archetype_support` matches the deck it is
supporting, and worth a little per passive policy effect it carries.
Deliberately coarse: most King effects are documented stubs, so scoring
them individually would be scoring the documentation.

Judging fit needs to know the deck's archetype, and two attempts at
inferring it both failed in the same instructive way. Reading the board's
Monsters returns nothing for the whole opening — which is exactly when the
first Coronation happens. Adding the hand's votes gets a tie or a blank
most of the time, five cards being five cards. The archetype was sitting in
`PlayerState.archetype` the entire time, assigned at setup and marked
"not exposed via Observation"; that note was written when nothing needed
it. Telling a player their own deck's archetype leaks nothing — it is the
deck they are holding — so `Observation.own_archetype` now carries it, to
its owner and to nobody else.

Result: **the crowned King matches the player's archetype 12 times out of
12.**

And Succession stayed at zero — which is now the *right* answer rather than
an arithmetic accident. Crown your home King and every remaining candidate
in the pool is a downgrade, so paying a Pawn to swap is correctly refused.
The scoring is a real comparison now (`gained − retired − cost`), and
`king_policy_fit` is set at 2.5 against a friction floor of 2.0 precisely so
that fixing a genuine mismatch is worth a Pawn and is not worth a Knight.

Which leaves a design question where a bug used to be: **Succession is
machinery for a decision the game never presents.** The pool hands you the
King you want, nothing during a match changes which King you want, and so
the escalating costs of §19 price an option nobody has a reason to take.
Either something should be able to make your King the wrong one, or the
pool should not be guaranteed to contain the right one. Both are §57-shaped
questions and neither is a tuning knob.

#### Weight tuning

`game/tuning.py`. §53 asks for "optimize heuristic weights" and
"evolutionary tuning"; `game.ai.weights` made a weight vector portable and
this is the search over them.

A `(1+λ)` evolution strategy — one incumbent, λ mutants per generation,
each played head-to-head against it, promotion only for a challenger that
beats it by more than the noise. No population to maintain and no crossover
to justify, because a single fitness evaluation costs minutes of real match
play and the shape that fits that budget is one clear question per
generation.

Three things in it are worth more than the search algorithm:

**Paired, colour-balanced evaluation.** Every candidate plays the same
seeds as its rivals, and plays each seed once as White and once as Black.
Same deals, same openings, both sides of the first-player edge — so what
survives is the weights. This is the same trick the balance re-measurement
above leaned on, and variance, not speed, is the binding constraint on a
search like this.

**A promotion bar that is honest about noise.** Hill-climbing on noise is
the default failure mode here: over 80 games a candidate needs roughly 8
points of score to be distinguishable, and a search that promotes anything
less is a random walk with extra steps. Promotion requires 2σ of
improvement, and every generation records the margin it needed alongside
the one it got.

Writing the test for that bar caught a bug in it. The rule required
`stderr > 0`, so a candidate that won *every* game — zero variance, the
strongest evidence a sample can carry — would never have been promoted. It
is the same trap `game.analysis` had in `Split.sigmas`, made twice in one
week by the same hand, which is a decent argument for the tests being
where they are.

**Mutation bounds wider than §51's.** Play styles are clamped to
`[0.6, 3.0]×` so a personality cannot tune itself out of playing; a search
gets `[0.1, 5.0]×`, because an optimizer has to be free to propose the
unreasonable — that is how it finds out a weight was wrong. Radius fields
are held fixed by default: a radius changes what the evaluation *looks at*
rather than how much it cares, which is a structural change hiding inside a
numeric one.

```bash
python -m game.tuning --generations 20 --population 6 --matches 40 \
    --export tuned.json
python -m game.selfplay --matches 400 --out corpus/tuned \
    --white heuristic --white-weights tuned.json --black heuristic
```

Checkpointed per generation and resumable, for the same reason the corpus
runner is: a twenty-generation run is hours, and it should not be
all-or-nothing.

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


