# 🐄 Moo Counter (moo)

Say `moo` in chat to increment your counter. Full network leaderboards. Includes cowsay-style one-liners, linux nerd moos, legendary moos, and sudo moo gambles.

---

## Setup

**1. Install dependencies:**
```bash
pip install sqlalchemy
```
(Usually already installed with Sopel.)

**2. Place the script:**
```
~/.sopel/scripts/moo.py
```

**3. Add to your Sopel `.cfg` file (optional — all have defaults):**
```ini
[moo]
# Cooldown between moos in seconds (default: 6)
moo_cooldown = 6

# Cooldown for sudo moo in seconds (default: 3600 = 1 hour)
sudo_cooldown = 3600

# Chance of a legendary moo, 0.0 to 1.0 (default: 0.02 = 2%)
legendary_chance = 0.02

# Chance of a big loss on sudo moo, 0.0 to 1.0 (default: 0.005)
sudo_big_loss_chance = 0.005

# Enable/disable 1337-speak moo effects (default: true)
leet_moo = true

# Max moo-triggered bot replies per channel in any 15 seconds, all users
# combined (default: 0 = no cap)
max_rate_per_15sec = 0
```

**Data storage:** Moo counts are stored in Sopel's built-in `bot.db` via SQLAlchemy — no extra database setup needed.

---

## Auto-Triggers

| Trigger | Effect |
|---------|--------|
| `moo` (anywhere in message) | +1 moo (per-user cooldown, `moo_cooldown`) |
| `/me moos` (also `moos!`, `moos?`, `moos.`) | +1 moo (shares the same cooldown as a typed moo) |
| `sudo moo` | +10 moos (1/hour/user/channel, `sudo_cooldown`) |

**Cooldowns are silent:** a moo during your cooldown is simply ignored (no channel message). For `sudo moo` you get at most one private NOTICE per cooldown window telling you how long is left. If `max_rate_per_15sec` is set and the channel hits the cap, extra moos are ignored too (a blocked `sudo moo` doesn't use up your hour).

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `$moo` | — | Moo (same as typing moo; same cooldown) |
| `$moo on\|off\|toggle` | `$mooon`, `$moooff` | Enable/disable moo in this channel (ops/admins) |
| `$moo status` | — | Show whether moo is enabled here |
| `$moocount` | `$mymoo` | Your moo count |
| `$mootop` | `$topmoo` | Global moo leaderboard |
| `$mootopchan` | `$chanmootop`, `$topmoochan` | Per-channel leaderboard |
| `$totalmoo` | `$moostats` | Global and channel totals |
| `$moohelp` | `$aboutmoo` | Help (PM) |
| `$mooreset` | — | Reset a user's moos (admin only) |

---

## Examples

**Say moo in chat:**
```
<User> moo
<Glitchy> 🐄 User: moo #42!
```

**Emote moo:**
```
* User moos
<Glitchy> 🐄 User: moo #43!
```

**Sudo moo (10 at once, 1/hour):**
```
<User> sudo moo
<Glitchy> 🐄 User: +10 moos! Total: 53
```

**Check your count:**
```
<User> $moocount
<Glitchy> 🐄 User has mooed 53 times!
```

**Global leaderboard:**
```
<User> $mootop
<Glitchy> 🐄 Top Mooers: 1. CowFan (1,204) 2. MooLord (980) 3. User (53) ...
```

**Channel leaderboard:**
```
<User> $mootopchan
<Glitchy> 🐄 #channel Top: 1. CowFan (450) 2. MooLord (320) 3. User (53)
```

**Total stats:**
```
<User> $totalmoo
<Glitchy> 🐄 Global moos: 15,302 | #channel moos: 3,841
```

**Admin — reset a user:**
```
/msg Glitchy $mooreset SpamMooer
```
