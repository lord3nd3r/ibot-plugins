# 🌲 Hunting Game (hunt)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Wildlife hunting mini-game modeled after the authentic IRCHunt v5.5.0 mechanics. Features wildlife encounters, multiple rarity tiers, weapon leveling, ammo and reload management, hunting permits, loot drops, steady aim, and protected-animal rescues.

---

## Setup

**1. Script file:**
```
~/.sopel/scripts/hunt.py
```

**2. Channel Activation (Safe by default):**
The plugin is disabled by default in all channels so it won't spam active discussions. A bot admin or channel operator must enable it per-channel:
```
$hunttoggle on
```
When enabled, the bot announces that hunting is active in the channel and directs users to `$hunthelp` to get started.

**3. Database:**
No configuration needed. The database (`hunt.db`) is automatically created in the scripts directory on first use.

**4. Optional `[hunt]` section in your ibot `.cfg`:**
```ini
[hunt]
# Channels where hunting is on without running $hunttoggle (comma-separated)
channels = #yourchannel, #hunting
# Seconds between animal spawns: random value between these (defaults: 360 and 720)
spawn_min = 360
spawn_max = 720
```
A `$hunttoggle off` in a channel still overrides the `channels` list. Run `$rehash` after you edit the file.

---

## Commands

The command prefix matches your bot's configured prefix (default `$`). Most gameplay commands only work in channels where hunting has been enabled.

### Help & Overview

| Command | Aliases | Description |
|---------|---------|-------------|
| `$hunthelp` | `$huntinghelp`, `$hunt` | Send the full hunting reference to you by PM. |
| `$huntable` | — | Show all active wildlife in this channel, their HP and the permit/level needed. |
| `$conditions` | — | Show the current field conditions (weather) and their effect on accuracy. |
| `$guide <species>` | — | Field-guide data for a species (e.g. `$guide rabbit`). |

> `$hunt` is also registered as an alias of `$shoot`, but the ibot dispatcher only runs the first matching handler, so in practice `$hunt` shows the help. Use `$shoot` to fire.

### Hunting Actions

| Command | Aliases | Description |
|---------|---------|-------------|
| `$shoot [species]` | — | Shoot at active wildlife. With no species it auto-targets the oldest eligible animal. |
| `$reloadgun` | `$loadgun`, `$rechamber`, `$unjam`, `$chamber`, `$mag` | Reload your magazine or clear a jam. |
| `$feed` | — | Feed a protected animal (stray cat / lost dog) to rescue it and earn friendship points. |

### Gear & Equipment

| Command | Aliases | Description |
|---------|---------|-------------|
| `$gun` | — | View weapon level, accuracy, condition, lubrication and attachments. |
| `$grease` | — | Use gun grease to restore 100% lubrication (prevents jams). |
| `$repair` | — | Use a repair kit to restore 35% weapon condition. |

### Shop, Permits & Loot

| Command | Aliases | Description |
|---------|---------|-------------|
| `$huntshop` / `$huntshop buy <id>` | `$hshop`, `$shop` | Browse or buy ammo, gun upgrades, scopes, silencers, grease, repair kits, food. |
| `$permits` / `$permit buy <key>` | `$permit` | View or buy hunting permits. |
| `$bag` | — | View harvested loot in your bag and its sell value. |
| `$sell all` | — | Sell all held loot. |

Permit keys: `game_bird` (150, L3), `medium_game` (400, L5), `big_game` (900, L10), `trophy_game` (1800, L16), `legendary` (3200, L22).

**Currency:** when `mug.py` is loaded, the shop, permits and `$sell` use your **mug coins** (the same wallet as `$mug`/`$beer`). Only if mug is not loaded does hunt fall back to its own "Spendable XP". (Before Oct 2026 the mug bridge never found mug under ibot, so hunt always used Spendable XP; it now finds mug correctly, so existing Spendable XP balances are no longer what the shop spends.)

### Stats & Leaderboards

| Command | Aliases | Description |
|---------|---------|-------------|
| `$mystats [nick]` | `$huntstats`, `$huntprofile` | Career stats: harvests, rank, trophies, XP and gear. |
| `$top [exp\|kills\|trophies\|friends]` | — | Hunting leaderboards (default `exp`). |

### Admin & Channel Management

| Command | Aliases | Who | Description |
|---------|---------|-----|-------------|
| `$hunttoggle [on\|off]` | `$huntenable`, `$huntdisable` | Channel ops or bot admins | Enable/disable hunting in the current channel. In PM: `$hunttoggle #channel on` (bot admins). |
| `$huntrate` | `$huntinterval`, `$spawnrate` | Anyone (view) / bot admins (change) | View or set spawn timing: `slow` (10–20m), `normal` (6–12m, default), `fast` (3–6m), or `<min> <max>` minutes. |
| `$spawn <species> [rarity]` | — | Bot admins | Force-spawn wildlife in an enabled channel (e.g. `$spawn bison legendary`). |

---

## Game Mechanics

### 🎯 Steady Aim & Combat Lock
- **Engagement Lock**: Once you shoot at an animal, `$shoot` keeps targeting that animal (the one you have already hit) until it is harvested or leaves.
- **Stay Time**: Each animal stays for a species-dependent time (about 4–35 minutes) before it leaves.
- **Steady Aim**: Each consecutive miss grants **+9% accuracy** on your next shot (capped at +22%), simulating dialing in your sights.
- **Critical Hits**: Each hit has a 12% critical chance, +2% per scope level, for bonus damage (a critical killing shot is a *Critical Finish*).

### 🦌 Wildlife Rarity & Permits
Animals spawn with a random rarity tier, up to each species' maximum (small game tops out at Epic; permit species can roll Legendary). Any Legendary encounter needs the Legendary Endorsement in addition to the species permit. Species and the permit they need:
- **No permit**: Squirrel, Rabbit, Duck, Pheasant.
- **Game Bird Permit**: Canada Goose, Wild Turkey.
- **Medium Game Permit**: Whitetail Deer, Wild Boar, Pronghorn.
- **Big Game Permit**: Black Bear, Elk, Caribou.
- **Trophy Game Permit**: Moose, Bison.
- **Legendary Endorsement**: Mythic Bison (never spawns naturally; `$spawn` only).
- **Protected (do not shoot — use `$feed`)**: Stray Cat, Lost Dog.

Each species also has a minimum hunter level and gun level (see `$guide <species>`).

### 🤫 Clean Channel Notices
Ammo depletion warnings (`[AMMO]`), empty chamber alerts (`[RELOAD]`), and permit clearance requirements are sent via private IRC notices (`NOTICE`) to keep channel chat clear and uncluttered.

---

## Examples

**Spawning and Hunting:**
```
<Glitchy> [UNCOMMON] A Large Deer steps out from the tree line! Deer HP 15 | 85 XP | L5 + Gun L2 + Big Game Permit
<User> $shoot
<Glitchy> [HIT] User hit Large Deer for 3 damage! HP 12/15 | +1 XP | Accuracy 78%
<User> $shoot
<Glitchy> [MISS] User missed Large Deer | Accuracy 78% | Steady Aim next shot ~87%
<User> $shoot
<Glitchy> [CRITICAL HIT] User scored a critical shot on Large Deer for 12 damage!
<Glitchy> [UNCOMMON KILL] User harvested Large Deer and earned 85 XP! | Trophy +5 (22) | Loot: Venison, Antlers
```

**Reloading:**
```
<User> $shoot
* Glitchy notices: [RELOAD] *CLICK* Out of ammo! Type $reloadgun to reload your Rifle (5/5).
<User> $reloadgun
<Glitchy> User slammed a fresh magazine into Rifle! (5/5 rounds)
```

**Admin Spawn Rate Tuning:**
```
<User> $huntrate
<Glitchy> 🌲 Critter Spawn Rate: Every 6 to 12 minutes. Presets: $huntrate slow (10–20m), $huntrate normal (6–12m), $huntrate fast (3–6m).
<User> $huntrate slow
<User> $huntrate 10 20
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
