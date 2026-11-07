# ⭐ Karma (karma)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Inline `++` / `--` karma system with per-channel and global tracking.

---

## Setup

**1. Install dependencies:**
```bash
pip install sqlalchemy
```
(Already installed as an ibot dependency.)

**2. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/karma.py
```

**No config section needed.** Karma data is stored in the bot's built-in `bot.db` via SQLAlchemy — no extra database setup required.

---

## Inline Usage

| Syntax | Effect |
|--------|--------|
| `nick++` | Give +1 karma (whole message, channel only — not in PM; you can't karma yourself) |
| `nick--` | Give -1 karma (whole message, channel only — not in PM; you can't karma yourself) |
| `nick==` | Check a nick's global karma inline (whole message) |

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `$karma <nick>` | — | Channel + global karma for a user |
| `$karmatop [N]` | `$ktop` | Top N users globally (default 5) |
| `$karmabottom [N]` | `$kbottom` | Bottom N users globally |
| `$channeltop [N]` | `$ctop` | Top N in this channel (default 10) |
| `$channelbottom [N]` | `$cbottom` | Bottom N in this channel |
| `$setkarma <nick> <value>` | — | Set global karma (channel ops only). Channel karma is left alone. Crossing a global milestone adds the extra line below. |

---

## Global milestones

The boost / drop line is unchanged. A real change to **global** karma (a vote or `$setkarma` that actually moves the total) adds at most one extra line: the highest rung the **new** total qualifies for that has not been announced yet. Channel totals never trigger it. There is no rank command and no badge that sticks in `.karma`. Nothing is said at startup, and setting the same value again is silent.

Rungs, both directions: **1, 10, 50, 100, 500, 1000, 2000, 5000, 10000**.

- A positive total announces the highest positive rung at or below it. A negative total does the same on the negative ladder. Zero announces nothing.
- The opposite sign is not celebrated. A `++` that leaves someone negative does not congratulate them for sinking further; if they are still negative, the line is the negative rung they **already** hold.
- **New** means this change crossed that rung (`Karma milestone`). **Recognition** means they were already there and it had never been said (`Karma recognition` … `already there`). Recognition is not worded as fresh damage or a fresh triumph.
- Several missed rungs, or a jump that also reaches a higher one, produce only the **highest** line. Lower ones are not read out and are not repeated later.
- What was said is stored per nick (`karma_milestones`, schema v2: `ann_pos` / `ann_neg`). Reloading, flood rollback, or bouncing on the boundary does not repeat it. The record only moves upward.
- `$setkarma` uses the previous global total and the value you set. It still does not change channel karma.
- **Already-high nicks:** the next real change catches up the highest rung they already qualify for. Global +1,200 hears the +1,000 line on the way to +1,201, not silence until +2,000. Global -1,200 hears the -1,000 line. Global -261 hears -100, until a later drop actually crosses -500.
- **Older v1 records** (`pos` / `neg` with no `v`) were silent seeds, not a log of lines that were said. They are ignored, so the next change can still catch up. If a v1 process both stored a seed and spoke a line, that one line can be said a second time. There is no separate history to tell those apart.

| Global | Positive (score going up) | Negative (score going down) |
|--------|---------------------------|-----------------------------|
| +1 / -1 | First positive karma. A moth filed you under 'possibly nice' and then forgot. | First negative karma. A stapler sighed and opened a file it hopes to lose. |
| 10 | Karma Adept. A vending machine bowed and refunded a coin nobody inserted. | Petty nuisance. The office plants voted to look slightly the other way. |
| 50 | Certified Benign Oddity. The county fair pinned a ribbon to a cloud shaped like your nick. | Officially inconvenient. A middle manager stamped your aura 'see attached' and left for lunch. |
| 100 | Civic Darling. Shopping carts are returning themselves and claiming it was your idea. | Civic grumble. Three pigeons and a parking meter co-signed a complaint in triplicate. |
| 500 | Regional Delight. Wayward balloons rewrote their flight plans to swing past your nick. | Municipal headache. Your nick now requires a traffic cone, a pamphlet, and one bored clerk. |
| 1000 | You are worshipped by malfunctioning satellites. | You have been legally classified as a municipal sewage incident. |
| 2000 | Orbital choir. The malfunctioning satellites formed a choir and the only lyric is your nick. | Regional apologies desk. An intern was hired solely to regret you, with carbon copies. |
| 5000 | Minor celestial appointment. A spare moon listed you as a reference and put gravity under hobbies. | Cartographic warning. Mapmakers added you as a smell on the windward side of the county. |
| 10000 | Karma supernova. You are worshipped by malfunctioning satellites, and the sun requested an autograph in ultraviolet. | Planetary liability. You are legally classified as a municipal sewage incident, now with extraterritorial jurisdiction and hold music. |

```
<User> Friend++
<Glitchy> 🆙 Karma boost! Friend gained 1 karma ✨ (🎯 10 in #channel | 🌐 1000 global)
<Glitchy> 🎉 Karma milestone for Friend (+1,000 global): You are worshipped by malfunctioning satellites.
```

Already at +1,200, first karma after this behavior (no prior announcement):

```
<User> Friend++
<Glitchy> 🆙 Karma boost! Friend gained 1 karma ✨ (🎯 11 in #channel | 🌐 1201 global)
<Glitchy> 🎉 Karma recognition for Friend (+1,000 global, already there; now +1,201): You are worshipped by malfunctioning satellites.
```

```
<User> Troll--
<Glitchy> ⬇ Karma drop... Troll lost 1 karma 😬 (🎯 -12 in #channel | 🌐 -500 global)
<Glitchy> 📉 Karma milestone for Troll (-500 global): Municipal headache. Your nick now requires a traffic cone, a pamphlet, and one bored clerk.
```

---

## Cooldowns

- **10 minutes** per user per channel between karma changes
- Arrow-like patterns (`<--`, `-->`, `<++`) are **not treated** as karma changes — the `++` / `--` operator must immediately follow a word character (letter, digit, or underscore)

---

## Examples

**Give karma:**
```
<User> Friend++
<Glitchy> ⭐ Friend's karma is now 15 (#channel) / 42 (global)
```

**Remove karma:**
```
<User> Troll--
<Glitchy> ⭐ Troll's karma is now -3 (#channel) / -8 (global)
```

**Quick check with ==:**
```
<User> Friend==
<Glitchy> ⭐ Friend: 15 (#channel) / 42 (global)
```

**Detailed check:**
```
<User> $karma Friend
<Glitchy> ⭐ Friend — #channel: 15 | Global: 42
```

**Global leaderboard:**
```
<User> $karmatop
<Glitchy> ⭐ Top 5 Karma: 1. Helper (120) 2. Friend (42) 3. Guru (38) 4. Sage (25) 5. Mentor (19)
```

**Bottom karma:**
```
<User> $karmabottom 3
<Glitchy> ⭐ Bottom 3 Karma: 1. Troll (-8) 2. Grump (-5) 3. Meanie (-2)
```

**Channel leaderboard:**
```
<User> $ctop 5
<Glitchy> ⭐ #channel Top 5: 1. Helper (45) 2. Friend (15) 3. User (8) ...
```

**Set karma (ops only):**
```
<@Admin> $setkarma Troll 0
<Glitchy> 🛠️ Troll's karma has been set to 0.
```

---

## Karma Flood Protection

Built-in protection against coordinated karma bombing attacks (bot swarms sending `nick--` or `nick++` from many accounts at once). **Enabled by default — no setup needed.**

### How It Works

When **3 or more unique accounts** target the same nick with karma changes in the **same direction** (`++` or `--`) within a **60-second window**, the system:

1. **Blocks** the karma change
2. **Bans** all attacker hostmasks (`*!*@host`)
3. **Kicks** the current attacker
4. **Rolls back** all karma damage from the flood window
5. **Auto-unbans** after 10 minutes (pending unbans are saved and still happen after a bot restart)
6. **Announces** the protection with restored karma values

Subsequent attackers from the same wave are silently dropped and banned on arrival.

### Admin Commands

Requires **halfop** (`+h`) or above, or **bot owner**.

| Command | Description | Example |
|---------|-------------|---------|
| `$karmaflood` | Show protection status and current settings | `$karmaflood` |
| `$karmaflood on` | Enable protection (on by default) | `$karmaflood on` |
| `$karmaflood off` | Disable protection | `$karmaflood off` |
| `$karmaflood set window <N>` | Detection window in seconds (10–300, default 60) | `$karmaflood set window 30` |
| `$karmaflood set threshold <N>` | Unique givers to trigger (2–20, default 3) | `$karmaflood set threshold 2` |
| `$karmaflood set ban_duration <N>` | Ban length in seconds (60–86400, default 600) | `$karmaflood set ban_duration 1800` |

> **Note:** Even if the bot doesn't have channel operator status, karma changes are still blocked and rolled back — the bot just can't ban/kick without ops.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
