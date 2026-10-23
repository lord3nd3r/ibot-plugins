# 🛡️ Antiflood Protection (antiflood)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Join/Part flood protection for ibot. Detects and bans users who cycle joins/parts or quit/rejoins too many times in a short window.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/antiflood.py
```

**2. Configure your ibot `.cfg` (optional):**
Default settings can be defined in the `[antiflood]` section:
```ini
[antiflood]
# Time window in seconds to track join events (default: 300)
window = 300
# Number of joins within the window to trigger ban (default: 3)
threshold = 3
# Auto-unban delay in seconds; 0 = permanent (default: 600)
ban_duration = 600
# 'host' (*!*@host) or 'ident' (*!user@host) (default: host)
banmask_style = host
# Channel mode chars exempt from bans (v=voice, h=halfop, o=op, a=admin, q=owner)
exempt_modes = vhoaq
# Global enable/disable switch (default: true)
enabled = true
```

**Note:** Antiflood is **disabled by default per channel**. You must explicitly enable it in each channel using the `$flood on` command.

---

## Commands

All commands require **bot admin** privileges.

| Command | Subcommands / Args | Description | Example |
|---------|-------------------|-------------|---------|
| `$flood` | — | Show antiflood status & parameters for the current channel | `$flood` |
| `$flood` | `on` / `off` | Enable/disable antiflood in the current channel | `$flood on` |
| `$flood` | `set <param> <val>` | Adjust runtime parameters (`window`, `threshold`, `duration`, `banmask`) | `$flood set threshold 5` |
| `$flood` | `whitelist list` | Show this channel's antiflood whitelist | `$flood whitelist list` |
| `$flood` | `whitelist add <nick\|mask>` | Exempt a nick or hostmask from join-flood and from JPQ. A nick also saves `*!*@host` when the bot can see that user. Globs such as `*!*@cloak` match | `$flood whitelist add PokemonTrainer` |
| `$flood` | `whitelist del <nick\|mask>` | Remove one entry from this channel's antiflood list | `$flood whitelist del PokemonTrainer` |
| `$flood` | `stats` | Show recent flood ban actions in this channel | `$flood stats` |
| `$flood` | `top` | Show top 5 most-kicked users in this channel | `$flood top` |
| `$floodtop` | — | Shortcut for `$flood top` | `$floodtop` |
| `$flood` | `help` | Send a command reference guide via NOTICE | `$flood help` |

---

## Behavior

* **Tracking**: Timestamps of `JOIN` events are recorded per hostmask (`user@host`).
* **Triggering**: If a user's join count meets or exceeds the `threshold` within the `window`, they are kicked and banned.
* **Exemptions**: Users with configured exempt channel modes (default: `+v`, `+h`, `+o`, `+a`, `+q`) or who are whitelisted are bypassed. The antiflood list and the JPQ list are both checked, so an entry on either one skips both bans.
* **Grace Period**: After a bot-initiated kick, a 60-second grace period applies to the hostmask to prevent event loops.
* **Auto-Unban**: Banned users are automatically unbanned after `ban_duration` seconds (if duration > 0) via a background timer. Pending unbans are saved, so they still happen after a bot restart (overdue ones run about a minute after the plugin loads).

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
