# 🌿 Weed & Trippy Sessions (weed)

A lighthearted party/sesh module for Sopel with countdown animations (solo mode) and gift actions (with a target user). It supports 15 substance categories, inline triggers, and rotation passing.

---

## Setup

**No config needed.** Just drop the script in place and it works.

**1. Place the script:**
```
~/.sopel/scripts/weed.py
```

**Data Persistence:** Bot intoxication states and sobering records are stored in SQLite (`grok_channel_effects` table in `grok.sqlite3`) for seamless cross-plugin integration with `ai-multi.py`. Cooldowns are tracked in memory.

---

## Commands

All commands support an optional target `<nick>` argument. If the target is present in the channel, it sends a gift action. Otherwise, it triggers the channel-wide countdown animation. Target nick arguments take the first word only, ignoring trailing text.

### Substance Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `$weed` | — | Weed session / gift 🌿 |
| `$bong` | — | Bong rip with water bubbling countdown 🫧 |
| `$joint` | — | Roll up and spark a joint 📜 |
| `$jay` | `$doobie` | Joint aliases 📜 |
| `$spliff` | — | Roll and spark a spliff (weed + tobacco) 🚬 |
| `$keef` | `$kief` | Sprinkle some keef ✨ |
| `$trip` | — | DMT breakthrough experience 👽🌀 |
| `$shrooms` | `$mushrooms` | Mushroom trip 🍄 |
| `$acid` | `$lsd` | Acid trip with fractal visuals 🌈 |
| `$peyote` | `$mescaline` | Peyote vision quest 🌵 |
| `$toke` | — | Pack a bowl and take a toke 🌬️ |
| `$edible` | `$edibles` | Eat or gift a THC-infused snack 🍪 |
| `$dab` | `$dabs` | Heat the banger and take a dab 💎🔥 |
| `$blunt` | — | Roll and spark a slow-burning blunt 🍂 |
| `$vape` | — | Discreet vapor hits 🔌☁️ |
| `$hash` | — | Old-world hash crumble 🟤 |
| `$munchies` | — | Raiding the kitchen for snacks 🍕🍿 |

### Rotation & Help Commands

| Command | Arguments | Description | Example |
|---------|-----------|-------------|---------|
| `$pass` | `<nick> [item]` | Take a hit and pass the rotation to someone else (requires target in channel). Passes whatever was last rolled/lit in the channel within the past hour (e.g. after `$blunt` it passes the blunt), or name an item: `joint`, `jay`, `doobie`, `spliff`, `blunt`, `bong`, `toke`/`bowl`, `pipe`, `vape`, `dab`/`dabs`/`dab rig` | `$pass Friend`, `$pass Friend bong` |
| `$sober` | `[nick]` | Sober up the bot in this channel, or splash cold water/coffee on another user | `$sober`, `$sober Glitchy` |
| `$unhigh` | `[nick]` | Alias for `$sober` | `$unhigh` |
| `$weedhelp` | — | PM a complete command reference card to the user | `$weedhelp` |

> **Note:** Serving hot coffee or food to sober up the bot is also available via the bartender plugin (`beer.py`) with `$coffee <botnick>` or `$pizza <botnick>`.

---

## Sesh Modes

### Mode 1: Solo Countdown (No Target)
When you trigger a command **without a target** (or if the target user is offline/not in the channel), it starts a **3-step countdown animation** in a background thread.
* Each step prints a themed, colored message.
* There is a **6-second delay** between each step.
* The final step posts a random colorful outcome from that substance's pool.

### Mode 2: Gift & Pass (With a Target User)
When you specify a target user who is **active in the channel**, the bot posts a single `/me` action message gifting the target a random item from the chosen substance pool or passing the rotation ($pass).
* **Targeting the Bot (AI Intoxication State)**:
  If you pass or gift an item to the bot itself (e.g. `$pass BotNick` or `$weed BotNick`), the bot takes a hit, passes it back, and enters an **altered AI state in that channel for 1 to 2 hours**:
  - **Weed / Bong / Joint / Blunt / Dabs**: The bot becomes **stoned/baked** (`intensity 1 to 5` if passed repeatedly). Responses in that channel become super chill, philosophical, giggly, with occasional typos, slurring, trailing thoughts (`...`), and munchies.
  - **Shrooms / Acid / LSD / Peyote**: The bot starts **tripping**, seeing patterns and fractals, and sharing cosmic revelations.
  - **Stacking**: Passing to the bot again adds +30 minutes (capped at 3 hours).
  - **Sobering Up**: The effect naturally wears off after 1–2 hours, or anyone can hand the bot coffee with `$coffee BotNick` or `$sober` to sober it up instantly.

---

## Cooldowns

* **Channel Cooldown (Countdown mode)**: **20 minutes** (shared by all users and all countdowns in the channel).
* **Per-User Cooldown (Gift & Pass mode)**: **30 seconds** (individual cooldown to prevent spamming gifts).
* Cooldowns are tracked by NickServ account (when registered) or nickname.

---

## Inline Triggering
Countdown commands can be triggered **mid-sentence**:
```
<User> man I could really use a $bong right now
<Glitchy> 🫧 3... Filling the water...
<Glitchy> 🧊 2... Adding ice...
<Glitchy> 🔥 1... Lighting the bowl...
<Glitchy> Bong rip incoming — lean back and ride the clouds 🌊💨
```
* Mid-sentence triggers respect the same 20-minute channel cooldown.
* To prevent duplicate triggers, messages starting with a command prefix (`$`) do not trigger inline rules.

---

## Substance Pools Reference

| Substance | Gift Pool Size | Sample Gifts |
|-----------|---------------|-------------|
| Weed | 30 | joint, bong rip, edible, vape hit, moon rock, rosin press |
| Bong | 10 | ice-catch bong, gravity bong, percolator, beaker |
| Joint | 11 | cross joint, spliff, cone, pinner, kief-dusted |
| Spliff | 8 | Euro-style spliff, Dutch tobacco spliff, hash & tobacco spliff, menthol cone |
| Keef | 8 | bowl topped with keef, keef puck, moonrock, pure keef bowl |
| DMT (trip) | 14 | glass vapor genie, breakthrough, changa joint, ayahuasca brew |
| Shrooms | 14 | golden teachers, penis envy chocolate, blue meanies, lemon tek |
| Acid | 14 | white-on-white, gel tab, blotter art, sugar cube, ten-strip |
| Peyote | 10 | dried button, San Pedro cactus tea, mescaline capsule |
| Toke | 5 | fat bowl of OG Kush, Sour Diesel spoon pipe, Purple Haze pipe |
| Edibles | 14 | canna-brownie, space cake, 100mg gummy worms, space cookie |
| Dab | 10 | live rosin, shatter glob, terp pearl, diamond-and-sauce |
| Blunt | 10 | backwoods, grape Swisher, two-gram torpedo, gold-leaf blunt |
| Vape | 10 | live resin cart, disposable pen, volcano bag |
| Hash | 10 | Nepalese temple ball, Moroccan blonde, Afghani black chunk |
| Munchies | 12 | pepperoni pizza, nacho cheese chips, cold leftovers, ice cream |
