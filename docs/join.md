# 🚪 Join (join)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Make the bot join a channel. **Owner only** — this is restricted to the single configured bot owner, not admins.

---

## Setup

**No config needed** beyond the `[core] owner` setting in your ibot `.cfg`.

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/join.py
```

**2. Make sure your ibot `.cfg` has an owner defined:**
```ini
[core]
owner = YourNick
```

> Only the configured `owner` can use this command. Admins and channel ops **cannot** use it — it is strictly owner-only via the `@module.require_owner()` decorator.

---

## Commands

| Command | Who | Description |
|---------|-----|-------------|
| `$join #channel [key]` | Owner only | Make the bot join a channel for this session only (not saved — use `$bjoin` to also auto-rejoin on restart) |

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `#channel` | Yes | The channel to join (must start with `#`) |
| `key` | No | Optional channel password/key for +k channels |

---

## Examples

**Join a channel:**
```
/msg Glitchy $join #newchannel
<Glitchy> Joining #newchannel
* Glitchy has joined #newchannel
```

**Join a channel with a key (password-protected):**
```
/msg Glitchy $join #secretroom mypassword
<Glitchy> Joining #secretroom
* Glitchy has joined #secretroom
```

**Missing channel name:**
```
/msg Glitchy $join
<Glitchy> Usage: $join #channel [key]
```

**Non-owner tries to use it:**
```
<User> $join #somewhere
(no response — silently ignored for non-owners)
```

> **Tip:** For admin-level join/part, use the ibot core `$bjoin` / `$bpart` commands (listed in [Bot Admin](botadmin.md)), which allow configured admins.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
