# 🔗 URL Title Fetcher (url_titles)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Automatic URL title fetching plugin for ibot. When a user posts a URL in chat, the bot automatically fetches the webpage, parses the HTML, and displays its `<title>` tag. Channel ops and bot admins can enable or disable title fetching per channel.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/url_titles.py
```

**2. Dependencies:**
* `requests`
* `beautifulsoup4` (BeautifulSoup)

---

## Commands

| Command | Who | Description |
|---------|-----|-------------|
| `$urltitle on` | Halfop+ / Admin | Enable automatic URL title fetching in this channel |
| `$urltitle off` | Halfop+ / Admin | Disable automatic URL title fetching in this channel |
| `$urltitle` | Anyone | Check whether URL title fetching is currently enabled |

* **Aliases:** `$urltitles`

---

## Permissions

* Setting changes (`$urltitle on` / `$urltitle off`) require **halfop or above** (`%`, `@`, `&`, `~`) in the channel, or **bot admin/owner** status.
* Settings are saved per-channel in the bot database and persist across bot restarts.

---

## Behavior

* **Automatic Triggers**: Enabled by default in public channels (starting with `#`). The bot automatically scans messages for URLs matching HTTP/HTTPS patterns.
* **Format**: The title is displayed in bold bracket formatting:
  ```
  [ Example Webpage Title Here ]
  ```
* **Exempted Domains**: Skips domains handled by other specialized plugins. By default, it ignores YouTube links (`youtube.com` and `youtu.be`), which are managed by the `youtube_titles` plugin.
* **Performance Limits**:
  * **Size Guard**: Streams the request and reads at most **64 KB** of data to prevent downloading huge files.
  * **Timeout**: Each request times out after **8 seconds**, and the whole fetch (including redirects) is limited to **10 seconds**.
  * **Cooldowns**: At most one title per channel every **5 seconds** and one per user every **15 seconds**; links posted during a cooldown are skipped silently.
* **Safety**: Only public `http`/`https` addresses are fetched. Links to private, loopback, link-local or other internal IPs (including hostnames that resolve to them) are ignored. Up to 5 redirects are followed, and each hop is checked the same way; the IP is checked again at connect time.
  * **Length Limit**: Title strings are truncated to **200 characters** with an ellipsis (`…`).
  * **Content Type**: Only parses pages with a `Content-Type` header containing `text/html`.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
