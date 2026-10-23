# 📊 Channel Stats (monitor)

Tracks lines, words, actions, kicks, bans, joins, parts, quits, splits, and nick changes per user per channel.

---

## Setup

**1. Place the script:**
```
~/.sopel/scripts/monitor.py
```

**No config section needed.** The stats database (`chanstats.db`) is created automatically in Sopel's `db_dir` (usually `~/.sopel/`). Tracking begins immediately once the script is loaded.

---

## Commands

| Command | Description |
|---------|-------------|
| `$stats [nick] [#channel]` | Stats for a user |
| `$rank [field] [#channel]` | Top 10 for a specific stat (default: lines) |
| `$chanstats [#channel]` | Aggregate stats for a channel |
| `$chanrank` | Top 10 channels by activity |
| `$statshelp` | Full command reference (PM) |

## Admin Commands

| Command | Privilege | Description |
|---------|-----------|-------------|
| `$zapstats [#channel]` | Owner | Wipe all stats for a channel |
| `$zapnick <nick> [#channel]` | Op+ | Remove a single nick's stats |

---

## Trackable Fields

`lines`, `words`, `actions`, `kicks`, `bans`, `joins`, `parts`, `quits`, `splits`, `nicks`

---

### How events are counted

- **Quits / splits** are counted only for the channels the user was actually in at the time (the plugin keeps its own membership list from NAMES, JOIN, PART, KICK and NICK).
- **Nick changes** are counted in each channel the user shares with the bot.
- Nicks like `Guest1234` are not tracked.
- Records for nicks not seen for 30 days are pruned every 10 minutes and at shutdown.

## Examples

**Your own stats:**
```
<User> $stats
<Glitchy> 📊 User in #channel — Lines: 1,204 | Words: 8,932 | Actions: 45 | Joins: 89 | Parts: 82
```

**Stats for another user:**
```
<User> $stats Friend
<Glitchy> 📊 Friend in #channel — Lines: 3,456 | Words: 22,100 | Actions: 120 | Joins: 200 | Parts: 195
```

**Stats in a specific channel:**
```
<User> $stats Friend #otherchannel
<Glitchy> 📊 Friend in #otherchannel — Lines: 890 | Words: 5,400 | Actions: 30 | Joins: 45 | Parts: 40
```

**Top 10 by lines (default):**
```
<User> $rank
<Glitchy> 📊 #channel Top 10 (Lines): 1. Chatter (5,200) 2. Friend (3,456) 3. User (1,204) ...
```

**Top 10 by a specific field:**
```
<User> $rank actions
<Glitchy> 📊 #channel Top 10 (Actions): 1. Friend (120) 2. Emotive (98) 3. User (45) ...
```

**Channel aggregate stats:**
```
<User> $chanstats
<Glitchy> 📊 #channel — Total Lines: 45,200 | Words: 312,000 | Users: 42 | Kicks: 15 | Bans: 3
```

**Top channels:**
```
<User> $chanrank
<Glitchy> 📊 Top Channels: 1. #general (45,200) 2. #random (32,100) 3. #dev (18,900) ...
```

**Admin — wipe channel stats:**
```
/msg Glitchy $zapstats #oldchannel
```

**Admin — remove a nick's stats:**
```
<@Admin> $zapnick SpamBot
```
