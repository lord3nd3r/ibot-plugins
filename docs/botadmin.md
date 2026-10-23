# 🛠️ Bot Admin (botadmin)

Small owner/admin helper plugin. It provides **`$restart`**, **`$breload`** and **`$bothelp`**. Every other bot-management command (`$rehash`, `$reload`, `$load`, `$unload`, `$plugins`, `$raw`, `$say`, `$act`, `$bjoin`, `$bpart`, `$bnick`, `$bmode`, `$bquit`, `$bstatus`, `$enable`, `$disable`, `$disabled`) comes from the **ibot core admin plugin** (`ibot/plugins/admin.py`), not from this script. See the ibot docs for those.

---

## Setup

**1. Place the script:**
```
~/.sopel/scripts/botadmin.py
```

**2. Owner / admins** come from the bot's `.cfg`:
```ini
[core]
owner = YourNick
admins =
    AdminNick1
```

> If `owner_account` is set, owner checks use the core's account-based `trigger.owner`; the nick fallback is only used when no owner account is configured.

---

## Commands in this plugin

| Command | Who | Description |
|---------|-----|-------------|
| `$restart [message]` | Owner | Restart the bot process. Uses the core's `bot.restart()` when available; otherwise it runs plugin shutdown hooks and re-executes `python3 -m ibot <cfg>` (same flags, from the ibot directory). A second `$restart` while one is in progress is ignored. If the restart can't be started, the bot replies "❌ Restart failed …; bot still running." |
| `$breload <plugin>` | Owner | Reload one plugin — same as the core `$reload <plugin>`. |
| `$breload all` | Owner | Reload config and all plugins — same as the core `$rehash`. |
| `$bothelp` | Admin | List bot admin commands (sent by NOTICE in a channel, by PM otherwise). |

## Core admin commands (ibot, for reference)

| Command | Who | Description |
|---------|-----|-------------|
| `$rehash` | Owner | Reload config and all plugins |
| `$reload <plugin>` / `$load <plugin>` / `$unload <plugin>` | Owner | Manage single plugins |
| `$plugins` | Owner | List loaded plugins |
| `$raw <irc line>` | Owner | Send a raw IRC line |
| `$bnick <nick>` | Owner | Change the bot's nick |
| `$bquit [msg]` / `$die` | Owner | Shut down the bot |
| `$say <target> <msg>` / `$msg` | Admin | Make the bot say something |
| `$act <target> <action>` | Admin | Make the bot do a /me action |
| `$bjoin #chan [key]` / `$bpart #chan [msg]` | Admin | Join / leave a channel |
| `$bmode #chan <modes> [args]` | Admin | Set a channel mode |
| `$bstatus` | Admin | Bot status info |
| `$enable` / `$disable <plugin> <#chan>`, `$disabled [#chan]` | Admin (PM only) | Per-channel plugin toggles |

---

## Examples

```
/msg Glitchy $restart
/msg Glitchy $breload mug
/msg Glitchy $breload all
/msg Glitchy $bothelp
```
