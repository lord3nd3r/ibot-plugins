# 👁️ Seen — Last Seen Tracker

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Tracks the last time a user spoke in a channel and reports it on demand.

---

## Commands

| Command | Description |
|---------|-------------|
| `$seen <nick>` | Report when `<nick>` was last seen and what they said |

---

## How It Works

- Every channel message and `/me` action is recorded per nick per channel.
- `$seen` checks the current channel first, then falls back to the most recent record across all channels.
- Data is stored in `~/.sopel/seen.db` (SQLite) and survives bot restarts.

---

## Examples

```
<End3r> $seen based
<devbox> based was last seen in #linux 2h 14m ago: hey whats up

<End3r> $seen glitchy
<devbox> glitchy was last seen in #linux 0s ago: * glitchy sets mode +v based
```

---

## Notes

- Asking about yourself or the bot returns a witty reply instead.
- Messages longer than 200 characters are truncated.
- `/me` actions are tracked and displayed as `* nick action`.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
