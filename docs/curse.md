# 🚔 Verbal Morality (curse)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Demolition Man-style Verbal Morality Statute. **Disabled by default.** When enabled, the bot monitors every message in the channel and issues fines for profanity with a randomized §X.X citation from the Verbal Morality Statute.

---

## Setup

**No config needed.** Just drop the script in place. Fining is **off by default** per channel — use `$curse on` to enable it.

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/curse.py
```

**Data storage:** Channel toggle states are stored in the bot's built-in `bot.db` and persist across restarts.

---

## Commands

| Command | Who | Description |
|---------|-----|-------------|
| `$curse on` | Halfop+ / Admin | Enable fining in this channel |
| `$curse off` | Halfop+ / Admin | Disable fining in this channel |
| `$curse` | Anyone | Check whether fining is enabled |

> The bot auto-monitors all messages when enabled; no command is needed to trigger a fine.

---

## How It Works

1. When enabled, **every channel message** is checked against a compiled regex of 50+ banned words.
2. Short words like "ass", "hell", "damn" use word-boundary matching to avoid false positives (e.g. "classic", "hello").
3. Longer profane roots match anywhere in a word to catch compounds (e.g. "cluster****", "mother****").
4. `/me` ACTION messages are **ignored** — only regular text triggers fines.
5. The bot picks a random fine message from **7 different style categories** (50+ templates total).

### Fine Message Styles

| Style | Example |
|-------|---------|
| Classic / Booth | `🚔 User, you have been fined 1 credit for violation of the Verbal Morality Statute §4.7.` |
| Department / Authority | `📋 CITATION ISSUED — User, pursuant to VMS §2.3, your language has been noted.` |
| Polite / Sarcastic | `😊 Oh User, such language! That's a fine of 1 credit per VMS §5.1. Have a lovely day!` |
| Stern / Bureaucratic | `⚠️ NOTICE: User — Infraction logged. VMS §3.9. Further violations will be escalated.` |
| Theatrical / Dramatic | `🎭 *gasps* User! The Verbal Morality Statute §6.2 weeps at your transgression!` |
| Robotic / AI Terminal | `🖥️ [VMS v3.7] VIOLATION DETECTED — User — Code §1.4 — Fine: 1 credit — LOGGED.` |
| Pop-Culture / Demolition Man | `🤖 User doesn't know how to use the three seashells! VMS §7.0 — fined!` |

> Fine section numbers (§X.X) are randomized on each violation.

---

## Permissions

Toggling requires **any** of the following:
- Bot owner or admin (configured in `[core]` of your ibot `.cfg`)
- Channel op (`+o` / `@`)
- Channel halfop (`+h` / `%`)
- Channel admin (`+a` / `&`)
- Channel owner (`+q` / `~`)

---

## Examples

**Enable verbal morality:**
```
<%Admin> $curse on
<Glitchy> 🚔 Verbal Morality Statute is now ACTIVE in #channel.
```

**Someone swears (classic style):**
```
<User> What the f***!
<Glitchy> 🚔 User, you have been fined 1 credit for violation of the Verbal Morality Statute §4.7.
```

**Someone swears (robotic style):**
```
<User> That's bs and you know it
<Glitchy> 🖥️ [VMS v3.7] VIOLATION DETECTED — User — Code §1.4 — Fine: 1 credit — LOGGED.
```

**Someone swears (sarcastic style):**
```
<User> Go to hell
<Glitchy> 😊 Oh User, such language! That's a fine of 1 credit per VMS §5.1. Have a lovely day!
```

**Emotes are safe:**
```
* User says something profane
(no response — /me actions are ignored)
```

**Check status:**
```
<User> $curse
<Glitchy> 🚔 Verbal Morality Statute is currently ACTIVE in #channel.
```

**Disable:**
```
<%Admin> $curse off
<Glitchy> 🚔 Verbal Morality Statute has been DEACTIVATED in #channel.
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
