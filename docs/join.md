# 🚪 Join (join)

Make the bot join a channel. **Owner only** — this is restricted to the single configured bot owner, not admins.

---

## Setup

**No config needed** beyond Sopel's built-in `owner` setting.

**1. Place the script:**
```
~/.sopel/scripts/join.py
```

**2. Make sure your `.cfg` has an owner defined:**
```ini
[core]
owner = YourNick
```

> Only the configured `owner` can use this command. Admins and channel ops **cannot** use it — it is strictly owner-only via Sopel's `@module.require_owner()` decorator.

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
