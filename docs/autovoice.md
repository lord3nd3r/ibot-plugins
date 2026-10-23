# 🎙️ Auto Voice (autovoice)

Activity-based auto-voicer. Tracks chat activity per user and automatically grants `+v` to active chatters. Removes voice from users who go idle. **Off by default** — must be enabled per-channel.

---

## Setup

**1. Place the script:**
```
~/.sopel/scripts/autovoice.py
```

**2. The bot needs halfop (`%`) or higher** in each channel where autovoice is enabled.

**No config file section needed.** All data is stored in `~/.sopel/autovoice_data.json`, created automatically on first use.

---

## How It Works

1. Every channel message is counted per user per channel (only in enabled channels).
2. Once a user reaches the channel's **message threshold** (default **50 messages**, adjustable per channel with `$autovoice threshold <n>`), they automatically receive `+v`.
3. **Auto-revoice on rejoin** — if a user who already earned `+v` parts and rejoins, they are automatically re-voiced within a few seconds.
4. A background thread sweeps every **15 minutes** to:
   - Voice any users who crossed the threshold since the last check.
   - **Devoice** users who haven't spoken in **7 days**.
   - Clean up stale data entries (users idle for 14+ days are pruned from the data file).
5. Users who **already have a mode** (`+v`, `+h`, `+o`, `+a`, `+q`) are **completely ignored** — the plugin never touches them.
6. If the bot loses halfop, it simply stops making changes until it gets the privilege back.

---

## Commands

| Command | Description |
|---------|-------------|
| `$autovoice on` | Enable autovoice for this channel |
| `$autovoice off` | Disable autovoice for this channel |
| `$autovoice status` | Show on/off state, tracked user count, and threshold info |
| `$autovoice reset <nick>` | Clear a specific user's message count and activity data |
| `$autovoice block <nick>` | Never autovoice this nick in this channel (also resets their count) |
| `$autovoice unblock <nick>` | Remove a nick from the channel's block list |
| `$autovoice blocklist` | List nicks blocked from autovoice in this channel |
| `$autovoice threshold` | Display this channel's message threshold and idle timeout |
| `$autovoice threshold <n>` | Set this channel's message threshold (1–100000; default 50). Saved across restarts |
| `$autovoice #channel threshold <n>` | Set a channel's threshold from PM (any subcommand works from PM with `#channel` first) |
| `$autovoice check [nick]` | Check your (or someone's) progress toward +v |

> **Permission:** every `$autovoice` subcommand (including `threshold` and `check`) requires **halfop+** in that channel or **bot admin**. Anyone can check progress with `$vcheck`.

### Progress Check (`$vcheck`)

Anyone can check autovoice progress — no special permissions needed.

| Command | Description |
|---------|-------------|
| `$vcheck` | Check your own progress in the current channel |
| `$vcheck <nick>` | Check another user's progress |
| `$vcheck #channel [nick]` | Check progress in a specific channel (from PM) |

---

## Examples

**Enable in a channel:**
```
<%Admin> $autovoice on
<Glitchy> Autovoice enabled for this channel.
```

**Check status:**
```
<User> $autovoice status
<Glitchy> Autovoice is ON | Tracking 23 users | Threshold: 50 msgs | Idle timeout: 7d
```

**Check your progress:**
```
<User> $vcheck
<Glitchy> User: 23/50 msgs (46%) — 27 more to go for +v
```

**Check someone else's progress:**
```
<User> $vcheck Boliver
<Glitchy> Boliver: 48/50 msgs (96%) — 2 more to go for +v
```

**User already voiced:**
```
<User> $vcheck ActiveUser
<Glitchy> ActiveUser has reached the threshold (50/50 msgs) ✓ voiced
```

**No data yet:**
```
<User> $vcheck newbie
<Glitchy> No activity recorded for newbie in #chat (need 50 msgs for +v).
```

**From PM:**
```
<User> $vcheck #chat Boliver
<Glitchy> Boliver: 23/50 msgs (46%) — 27 more to go for +v
```

**Change the threshold for this channel:**
```
<%Admin> $autovoice threshold 10
<Glitchy> Autovoice threshold for #chat set to 10 messages.
<%Admin> $autovoice threshold
<Glitchy> Current threshold in #chat: 10 messages to earn +v, 7 days idle to lose it.
```

**Set a threshold from PM:**
```
/msg Glitchy $autovoice #chat threshold 25
<Glitchy> Autovoice threshold for #chat set to 25 messages.
```

**Reset a user's data:**
```
<%Admin> $autovoice reset someuser
<Glitchy> Reset activity data for someuser.
```

---

## Tunables

These constants are at the top of `autovoice.py` and can be adjusted:

| Constant | Default | Description |
|----------|---------|-------------|
| `MSG_THRESHOLD` | `50` | Default messages needed to earn `+v` (override per channel with `$autovoice threshold <n>`) |
| `IDLE_SECONDS` | `604800` (7 days) | Idle time before voice is removed |
| `SWEEP_INTERVAL` | `900` (15 min) | How often the background sweep runs |

---

## Ignored Users

The plugin **never** changes modes for users who already have any privilege:
- `+v` (voice) — already voiced, nothing to do
- `+h` (halfop) — higher than voice, left alone
- `+o` (op) — left alone
- `+a` (admin) — left alone
- `+q` (owner) — left alone

This means ChanServ-assigned modes, access list modes, and manually-set modes are always respected.

---

## Data File

`~/.sopel/autovoice_data.json` stores:
- **Per-channel enabled/disabled state**
- **Per-channel message thresholds** (set with `$autovoice threshold <n>`)
- **Per-channel block lists**
- **Per-user per-channel message counts and last-seen timestamps**

The file is updated periodically and on plugin shutdown. Stale entries are automatically pruned.
