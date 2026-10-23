# 🛡️ JPQ Flood Protection (jpq)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Join/Part/Quit cycle flood protection for ibot. It detects and bans users who cycle joins, parts, or quits in channels to flood the chat or server logs.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/jpq.py
```

**2. Configure your ibot `.cfg` (optional):**
Default settings can be defined in the `[jpq]` section:
```ini
[jpq]
# Time window in seconds to track cycling events (default: 30)
window = 30
# Number of events within the window to trigger ban (default: 5)
threshold = 5
# Auto-unban delay in seconds; 0 = permanent (default: 300)
ban_duration = 300
# 'host' (*!*@host) or 'ident' (*!user@host) (default: host)
banmask_style = host
# Only "v" is read. +h and above are always exempt
exempt_modes = vho
# Global enable/disable switch (default: true)
enabled = true
```

**Note:** Unlike `antiflood`, JPQ is **enabled by default in all channels**. Channels must be explicitly disabled using the `$jpq off` command if desired.

`exempt_modes` only adds voice. `h`, `o`, `a`, and `q` in that string do nothing, because halfop and above are always skipped.

---

## Commands

All commands require **bot admin** privileges and must be used in a channel. `$jpq wl` is an alias for `$jpq whitelist`.

| Command | Subcommands / Args | Description | Example |
|---------|-------------------|-------------|---------|
| `$jpq` | — | Show JPQ status and parameters for the current channel | `$jpq` |
| `$jpq` | `on` / `off` | Enable or disable JPQ in the current channel | `$jpq off` |
| `$jpq` | `ban` | Show whether flood kicks also set a ban | `$jpq ban` |
| `$jpq` | `ban on` / `ban off` | Ban along with each kick. Off by default. Turning it off leaves bans already set | `$jpq ban on` |
| `$jpq` | `set window <sec>` | Detection window, 5–300 seconds (default 30) | `$jpq set window 300` |
| `$jpq` | `set threshold <n>` | Events inside the window that trigger a ban, 2–50 (default 5) | `$jpq set threshold 4` |
| `$jpq` | `set duration <sec>` | Auto-unban delay, 0–86400 seconds. 0 keeps the ban (default 300) | `$jpq set duration 600` |
| `$jpq` | `set banmask <host\|ident>` | `host` is `*!*@host`. `ident` is `*!user@host` | `$jpq set banmask host` |
| `$jpq` | `whitelist list` | Show this channel's JPQ whitelist | `$jpq whitelist list` |
| `$jpq` | `whitelist add <nick\|mask>` | Exempt a nick or hostmask from JPQ and from join-flood. A nick also saves `*!*@host` when the bot can see that user. Globs such as `*!*@cloak` match. A mask with no letters or digits is rejected | `$jpq whitelist add PokemonTrainer` |
| `$jpq` | `whitelist del <nick\|mask>` | Remove one entry from this channel's JPQ list | `$jpq whitelist del PokemonTrainer` |
| `$jpq` | `stats` | Show the last 25 bans this process has made in this channel. The list is memory only and clears on restart | `$jpq stats` |
| `$jpq` | `help` | Send the command reference by NOTICE | `$jpq help` |

---

## Behavior

* **Tracking**: The plugin tracks `JOIN`, `PART`, and `QUIT` events, including ping timeouts and dropped connections. Since `QUIT` is server-wide, the plugin keeps an in-memory channel membership map so it knows which channels the quitting user was in.
* **Triggering**: If a user's combined event count meets or exceeds the `threshold` within the `window`, the bot kicks them. The kick reason is `JPQ flood protection (N events in Ws)`. The bot has to be opped. If it is not, the flood is logged and nobody is kicked.
* **Bans**: Off until `$jpq ban on` in that channel. When on, the bot sets the ban before the kick. `$jpq ban off` stops new bans and leaves ones already set. Auto-unban applies only when bans are on and `ban_duration` is greater than 0.
* **Announcement**: The channel notice is limited to one every 5 seconds. The event count is printed in normal text so IRC color codes do not swallow the digits.
* **Exemptions**: Halfops and above (`+h`, `+o`, `+a`, `+q`) are always skipped. That includes the quit and the rejoin before ChanServ restores the mode. A mode change that leaves them below halfop removes that exemption. Voice is skipped only when `exempt_modes` contains `v`.
* **Whitelist**: A nick matches that nickname. A hostmask matches `user@host`, including `*` and `?` globs. The JPQ list and the antiflood list are both checked, so `$jpq whitelist add` and `$flood whitelist add` each protect against both bans. Removing an entry only deletes it from the list it was added to.
* **Other skips**: users on the core `nick_blocks` and `host_blocks` lists are ignored.
* **Grace Period**: After a bot kick, that hostmask is ignored for 60 seconds so the kick and ban do not count as more flood events.
* **Auto-Unban**: When bans are on and `ban_duration` is greater than 0, a timer removes the ban. Pending timers are cancelled when the bot shuts down, but the pending unbans are saved and rescheduled when the plugin loads again (overdue ones run about a minute after loading).

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
