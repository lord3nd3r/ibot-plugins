# 📬 Tell — Offline Messages

Leave messages for users who aren't currently active. When they next speak in any channel the bot is in, they receive a PM for each pending message.

---

## Commands

| Command | Description |
|---------|-------------|
| `$tell <nick> <message>` | Leave a message for `<nick>` |
| `$showtells` | Retrieve your pending messages immediately (via PM, 5 at a time) |

---

## How It Works

1. Use `$tell <nick> <message>` in any channel.
2. The bot confirms: `I will tell <nick> that when they next speak.`
3. When `<nick>` next says anything in any channel, the bot sends them a PM:
   ```
   [Tell from End3r in #linux on 2026-05-09 15:43 UTC]: hey, call me back!
   ```
4. Each message is delivered once and not repeated. Up to **5** messages are sent per delivery, about one per second; if more are waiting the bot adds `You have N more message(s) waiting. Use $showtells to get the next batch.`

---

## Examples

**Leaving a message:**
```
<End3r> $tell based hey, you around later?
<devbox> End3r: I will tell based that when they next speak.
```

**Delivery when the target speaks:**
```
(PM from devbox): [Tell from End3r in #linux on 2026-05-09 15:43 UTC]: hey, you around later?
```

**Checking your own pending messages:**
```
<based> $showtells
(PM from devbox): [Tell from End3r in #linux on 2026-05-09 15:43 UTC]: hey, you around later?
(PM from devbox): 1 message(s) delivered above.
```

---

## Notes

- You cannot leave a message for yourself or for the bot.
- Messages are stored in `~/.sopel/tell.db` (SQLite) and survive bot restarts.
- Delivery uses PM so it doesn't clutter the channel.
- Limits: messages are at most **350** characters; a nick can have at most **20** undelivered messages waiting, and one sender at most **30** undelivered messages queued in total. `$tell` can be used once every 5 seconds per user and `$showtells` once every 10 seconds (extra uses are ignored silently).
- Delivered messages are removed from the database (old delivered rows are cleaned up when the plugin loads).
