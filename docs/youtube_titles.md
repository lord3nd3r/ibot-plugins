# 🎬 YouTube Titles (youtube_titles)

Automatic YouTube video metadata fetching for Sopel. When a user posts a YouTube URL in chat, the bot automatically fetches and displays the video title and channel/author name. It includes a command to enable/disable this feature per channel.

---

## Setup

**No config needed.** Just drop the script in place and it works.

**1. Place the script:**
```
~/.sopel/scripts/youtube_titles.py
```

**No API key required.** Uses the public YouTube oEmbed endpoint (`youtube.com/oembed`) to fetch metadata — no quotas, no authentication.

---

## Commands

Requires **channel operator** (`+o`, `%` halfop or above, privilege level $\ge$ 2) or **bot owner** status.

| Command | Arguments | Description | Example |
|---------|-----------|-------------|---------|
| `$yt` | — | Display the current status of YouTube title fetching in the channel | `$yt` |
| `$yt` | `on` / `off` | Enable/disable YouTube title fetching in the current channel | `$yt off` |

---

## Triggers

When enabled in a channel, the bot automatically scans all messages for YouTube URLs.

### Supported URL Formats

| Format | Example |
|--------|--------|
| Standard watch URL | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| Short URL | `https://youtu.be/dQw4w9WgXcQ` |
| Embed URL | `https://www.youtube.com/embed/dQw4w9WgXcQ` |
| With subdomain | `https://m.youtube.com/watch?v=dQw4w9WgXcQ` |
| With extra params | `https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120` |

### Output Format

```
YouTube: Video Title — Channel Name
```

If the channel/author name is unavailable, only the title is shown:

```
YouTube: Video Title
```

> **Fail-silent:** If the oEmbed API call fails or the video is private/deleted, the bot simply doesn't respond — no error message is shown.
>
> **Cooldowns:** at most one YouTube title per channel every **5 seconds** and one per user every **15 seconds**; links during a cooldown are skipped silently. Titles and channel names are stripped of IRC control/formatting characters.
