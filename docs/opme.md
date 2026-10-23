# 🔑 PromoteMe (opme)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Promote yourself (or a target) to channel operator. Supports configurable modes, temporary promotions, channel restrictions, and cooldowns.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/opme.py
```

**2. Add to your ibot `.cfg` file (optional — all have defaults):**
```ini
[promoteme]
# Only allow admins to use the command (default: true)
require_admin = true

# Require the bot to have op before promoting (default: true)
require_bot_op = true

# IRC modes to apply (default: +o for operator)
modes = +o

# Allow in all channels or restrict to a list (default: true)
allow_in_all_channels = true

# Comma-separated list of allowed channels (only used if allow_in_all_channels = false)
allowed_channels = #channel1, #channel2

# Cooldown between uses per user in seconds (default: 60)
cooldown_seconds = 60

# Custom success message — {nick} and {channel} are replaced
success_message = Promoted {nick} to operator in {channel}.

# Make promotions temporary (default: false)
temporary_promotion = false

# Duration of temporary promotions in seconds (default: 300 = 5 minutes)
promotion_duration = 300
```

---

## Commands

| Command | Description |
|---------|-------------|
| `$promoteme` | Promote yourself to channel op |
| `$promoteme <nick>` | Promote another user to channel op |

---

## How It Works

1. User runs `$promoteme` (or `$promoteme Friend`).
2. Bot checks permissions: is the user an admin? Is the bot opped?
3. Bot applies the configured mode (default `+o`) to the target.
4. If **temporary promotion** is enabled, a timer automatically removes the mode after the configured duration.

### Cooldowns

| Type | Default | Configurable |
|------|---------|-------------|
| **Per-user** | 60 seconds | Yes (`cooldown_seconds`) |
| **Per-channel** | 300 seconds (5 min) | No (hardcoded) |

The per-channel cooldown blocks **all users** in that channel, not just the one who last used it. This prevents mode-change flooding.

### Temporary Promotions

When enabled (`temporary_promotion = true`), the bot automatically reverses the mode after `promotion_duration` seconds.

For example, with `modes = +o` and `promotion_duration = 300`:
- User gets `+o` immediately
- After 5 minutes, bot automatically sets `-o` on the user

---

## Permissions

| Check | Default | Description |
|-------|---------|-------------|
| `require_admin` | true | Only bot admins/owner can use the command |
| `require_bot_op` | true | Bot must have `+o` in the channel |
| `allow_in_all_channels` | true | Set to false + `allowed_channels` to restrict |

> The command **only works in channels** — it does nothing in private messages.

---

## Examples

**Promote yourself:**
```
<Admin> $promoteme
* Glitchy sets mode +o Admin
<Glitchy> Promoted Admin to operator in #channel.
```

**Promote another user:**
```
<Admin> $promoteme Friend
* Glitchy sets mode +o Friend
<Glitchy> Promoted Friend to operator in #channel.
```

**Bot doesn't have op:**
```
<Admin> $promoteme
<Glitchy> I need operator status to promote users!
```

**User cooldown:**
```
<Admin> $promoteme
<Glitchy> Please wait 60 seconds between uses.
```

**Channel cooldown:**
```
<OtherAdmin> $promoteme
<Glitchy> This command is on cooldown in this channel.
```

**Non-admin tries to use it:**
```
<User> $promoteme
<Glitchy> You don't have permission to use this command.
```

**Channel not in allowed list:**
```
<Admin> $promoteme
<Glitchy> This command is not allowed in this channel.
```

**In a private message:**
```
/msg Glitchy $promoteme
<Glitchy> This command can only be used in a channel.
```

**Temporary promotion (auto-reverts):**
```
<Admin> $promoteme
* Glitchy sets mode +o Admin
<Glitchy> Promoted Admin to operator in #channel.
  (5 minutes later...)
* Glitchy sets mode -o Admin
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
