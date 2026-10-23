# ⭐ Karma (karma)

Inline `++` / `--` karma system with per-channel and global tracking.

---

## Setup

**1. Install dependencies:**
```bash
pip install sqlalchemy
```
(Usually already installed with Sopel.)

**2. Place the script:**
```
~/.sopel/scripts/karma.py
```

**No config section needed.** Karma data is stored in Sopel's built-in `bot.db` via SQLAlchemy — no extra database setup required.

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
| `$setkarma <nick> <value>` | — | Set karma (channel ops only) |

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

