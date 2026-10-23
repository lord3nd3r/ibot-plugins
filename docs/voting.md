# 🗳️ Voting (voting)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Create timed polls with multiple options. Requires halfop (`%`) or above to create.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/voting.py
```

**2. Add to your ibot `.cfg` file (optional — all have defaults):**
```ini
[voting]
# Path to the SQLite database for storing votes (default: voting.db)
db_path = voting.db

# Delay between multi-line messages in seconds (default: 0.5)
message_delay = 0.5
```

**Data storage:** Votes are stored in a SQLite database (default: `voting.db` in the working directory or resolved relative to the bot's home directory). Created automatically on first use.

---

## Commands

| Command | Description |
|---------|-------------|
| `$vote <question>` | Create a Yes/No poll with 24h default duration (Halfop+ only) |
| `$vote Q:<question> A1:<opt> A2:<opt> [A3:...] [T:<duration>]` | Create a poll with custom options and duration (Halfop+ only) |
| `$v <number>` / `$castvote` | Cast or change your vote |
| `$voting` / `$votestats` / `$vstats` / `$voteresults` | Show current poll results / status |
| `$endvote` | End the poll early (creator or halfop+) |
| `$votehelp` | Full help guide (sent via PM) |

> **Aliases:** `$vote`, `$voting`, `$poll`.
> **Duration formats:** `30s` (seconds), `15m` (minutes), `24h` (hours), `7d` (days). Default duration is `24h`. Options default to `1: Yes, 2: No` if none are specified. Durations must be between **10 seconds** and **30 days**, and a poll can have at most **10** options.

---

## Examples

**Create a poll:**
```
<%Admin> $vote Q:Best programming language? A1:Python A2:JavaScript A3:Rust A4:Go T:1h
<Glitchy> 📊 ═══════════════════════════════════════
<Glitchy> 🗳️  NEW VOTE by Admin
<Glitchy> ❓ Best programming language?
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 1️⃣ Option 1: Python
<Glitchy> 2️⃣ Option 2: JavaScript
<Glitchy> 3️⃣ Option 3: Rust
<Glitchy> 4️⃣ Option 4: Go
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> ⏰ Vote ends in 1h
<Glitchy> 💡 Vote with: .v 1 or 2 or 3 or 4
<Glitchy> 📊 ═══════════════════════════════════════
```

**Cast a vote:**
```
<User> $v 3
<Glitchy> User: ✅ Vote recorded for 3️⃣ Option 3!
```

**Check results mid-poll:**
```
<User> $votestats
<Glitchy> 📊 ═══════════════════════════════════════
<Glitchy> 📈 VOTE STATISTICS
<Glitchy> ❓ Best programming language?
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 🗳️  Total Votes: 1
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 1️⃣ Option 1: Python
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy> 2️⃣ Option 2: JavaScript
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy> 3️⃣ Option 3: Rust
<Glitchy>    [████████████████████] 1 votes (100.0%)
<Glitchy> 4️⃣ Option 4: Go
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> ⏰ Time remaining: 59m 45s
<Glitchy> 📊 ═══════════════════════════════════════
```

**End poll early:**
```
<%Admin> $endvote
<Glitchy> 🏁 ═══════════════════════════════════════
<Glitchy> 🎉 VOTE ENDED - FINAL RESULTS
<Glitchy> ❓ Best programming language?
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 🗳️  Total Votes: 1
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 🏆 3️⃣ Option 3: Rust
<Glitchy>    [████████████████████] 1 votes (100.0%)
<Glitchy>    1️⃣ Option 1: Python
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy>    2️⃣ Option 2: JavaScript
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy>    4️⃣ Option 4: Go
<Glitchy>    [░░░░░░░░░░░░░░░░░░░░] 0 votes (0.0%)
<Glitchy> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Glitchy> 🏆 WINNER: 3️⃣ Option 3 - Rust
<Glitchy> 🏁 ═══════════════════════════════════════
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
