# 📰 RSS/Atom Feeds (feeds)

Posts new items from RSS and Atom feeds into IRC channels. **Off everywhere by default** — a channel only gets posts after a channel op (or bot admin) runs `$feed on` there **and** subscribes it to at least one feed.

Feeds are a **global list** managed by bot admins (and feed admins). Channel ops choose which of those feeds their channel receives.

---

## Setup

**No config section needed.** Feed lists, subscriptions, feed admins, and seen-item state are stored in the bot's built-in `bot.db` (tables `feed_list`, `feed_channels`, `feed_subs`, `feed_admins`, `feed_seen`, `feed_recent`), created automatically on load.

**1. Place the script:**
```
~/.sopel/scripts/feeds.py
```

**2. Dependencies:** `requests` and `sqlalchemy` (already used by the bot). `defusedxml` is used for safer XML parsing if installed; otherwise the stdlib parser is used.

---

## Commands

`$feeds` is an alias for `$feed`. From PM, put the channel first: `$feed #channel <cmd>`. Running `$feed` with no arguments in a channel shows its status; in PM it shows usage.

### Anyone

| Command | Description |
|---------|-------------|
| `$feed list` | List all feeds; `*` marks ones this channel is subscribed to, `(!)` marks feeds that are failing |
| `$feed status` | On/off state, subscriptions, and posting limits for this channel |

### Channel Ops (@ or higher), Feed Admins of the Channel, or Bot Admins

| Command | Description |
|---------|-------------|
| `$feed on` | Enable feed posting in this channel (`enable` also works) |
| `$feed off` | Disable feed posting in this channel (`disable` also works) |
| `$feed sub <name>` | Subscribe this channel to a feed |
| `$feed unsub <name>` | Unsubscribe this channel from a feed |
| `$feed latest` | Post the 5 newest items across all of this channel's feeds |
| `$feed latest <name> [n]` | Post the newest `n` items (default 1, max 5) from one feed |

> `$feed latest` has a **60-second cooldown per channel** (bot admins are exempt) and doesn't change what counts as already posted.

### Bot Admins, or Feed Admins (of any channel)

| Command | Description |
|---------|-------------|
| `$feed add <name> <url>` | Add a feed to the global list (name: 1–32 chars, letters/digits/`.`/`_`/`-`) |
| `$feed del <name>` | Remove a feed from the list and from every channel (`delete` / `rm` also work) |
| `$feed poll` | Check all feeds right now instead of waiting for the next poll |

### Bot Admins Only

| Command | Description |
|---------|-------------|
| `$feed admin add <nick>` | Make a nick a feed admin for this channel |
| `$feed admin del <nick>` | Remove a feed admin (`rm` also works) |
| `$feed admin list` | Show the feed admins for this channel |

> **Feed admins** are stored per channel by nick. If the network reports NickServ accounts and one was known when the admin was added, the account must match too.

---

## How It Works

- Feeds are polled every **10 minutes** (cheap with ETag / Last-Modified, so unchanged feeds return `304`).
- **Priming:** when a feed is added, its existing items are marked as seen and **not posted**, so adding a feed never dumps its backlog. A channel that subscribes later also gets no backlog.
- At most **3 lines per channel per poll**, posted 2 seconds apart; if more items arrived, the newest 3 are posted followed by `(+N more feed items skipped)`.
- Post format: `[feedname] Title – link`

### Filtering & De-duplication

- **Mailing-list replies are skipped:** titles starting with `Re:`, `Fwd:`, `Fw:`, `AW:` (including after `[list]` tags, e.g. `[oss-security] Re: ...`).
- **Duplicate titles** queued for the same channel in one poll are posted once.
- **Same story from different outlets** (e.g. Google News aggregations) is skipped if a matching story was posted in that channel in the last **48 hours**. Titles match when they share a proper-name pair plus two other meaningful words, or have heavy word overlap. Titles carrying an **advisory ID** (`CVE-`, `GHSA-`, `DSA-`/`DLA-`, `USN-`, `RHSA-`, `GLSA`, `SUSE-SU-`, `FEDORA-`, `XSA-`, etc.) only match a title with the **identical set of IDs**, so different advisories are never merged.
- `$feed latest` applies the same reply filter and same-story check.

### Links

- Links longer than **80 characters** are shortened via **TinyURL** (the original link is used if TinyURL fails or returns anything unexpected).
- Titles are stripped of HTML and IRC control codes and capped at 180 characters.

### Safety Limits (hard-coded, not changeable from IRC)

| Limit | Value |
|-------|-------|
| Poll interval | 10 minutes |
| Max posts per channel per poll | 3 |
| Feed size limit | 2 MB |
| HTTP timeout | 15 seconds |
| Redirects | Max 5, followed manually |
| Failing feeds | After 10 consecutive failures, retried only hourly |
| Seen-item history | 30 days |

- **SSRF protection:** only `http://` / `https://` URLs whose host resolves exclusively to **public** IP addresses are accepted. Every redirect is re-checked, so a feed can't bounce the bot to an internal address.

---

## Examples

**Add a feed (bot admin / feed admin):**
```
<Admin> $feed add lobsters https://lobste.rs/rss
<Glitchy> Admin: Added lobsters (25 items found, existing ones won't be posted). Ops can now $feed sub lobsters
```

**Turn on feeds in a channel and subscribe:**
```
<@Op> $feed on
<Glitchy> Feeds enabled in #oss-feeds. No subscriptions yet: see $feed list, then $feed sub <name>
<@Op> $feed sub lobsters
<Glitchy> Subscribed #oss-feeds to lobsters.
```

**Check status:**
```
<User> $feed status
<Glitchy> Feeds in #oss-feeds: ON | Subscribed: lobsters | Max 3 posts per 10 min
```

**Newest 3 items from one feed:**
```
<@Op> $feed latest lobsters 3
```

**From PM:**
```
/msg Glitchy $feed #oss-feeds on
/msg Glitchy $feed #oss-feeds admin add SomeNick
```
