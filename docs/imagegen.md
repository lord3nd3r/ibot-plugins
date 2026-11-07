# 🎨 Image Generation (imagegen)

> **Built for [ibot](https://github.com/lord3nd3r/ibot).** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

`$img <prompt>` generates an image with **Cloudflare Workers AI** (FLUX.1 schnell by default), uploads it to **[sharey.org](https://sharey.org)** and replies with the link.

---

## Setup

**1. Cloudflare account and token.** A free Cloudflare account with Workers AI is enough; no paid plan is needed.

- **Account ID:** Cloudflare dashboard → *Workers & Pages* (or *AI* → *Workers AI*); it is the 32-character hex ID.
- **API token:** *My Profile* → *API Tokens* → *Create Token* → use the **Workers AI** template, which grants **Workers AI Read and Edit** on your account. A Read-only token cannot run models.

Usage counts against your Cloudflare Workers AI allowance. The plugin's own `daily_cap` limits how many images the bot will make; it does not guarantee how many images the provider's free allowance covers.

**2. Add a section to your ibot `.cfg`** (keep the real values only in your private config, never in this repo):

```ini
[imagegen]
account_id = YOUR_CLOUDFLARE_ACCOUNT_ID
api_token = YOUR_WORKERS_AI_TOKEN
# all optional (defaults shown)
model = @cf/black-forest-labs/flux-1-schnell
steps = 4
daily_cap = 100
user_cooldown = 60
max_concurrent = 2
max_prompt = 400
expiry = 30d
# comma-separated channels; blank = every channel
channels =
```

Instead of the config keys you can set the environment variables `CF_ACCOUNT_ID` and `CF_API_TOKEN`. The plugin re-reads its `[imagegen]` section from the config file whenever the file changes, so new or edited values work without `$rehash`.

**3. Load it** (owner): `$load imagegen`. After updating the code: `$reload imagegen`.

Needs `requests` (already used by other plugins).

---

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `$img <prompt>` | `$image` | Generate an image and reply with a sharey.org link |

```
<User> $img a lighthouse at dusk, oil painting
<Glitchy> User: generating image...
<Glitchy> User: https://sharey.org/AbC123.jpg
```

---

## Limits

| Setting | Default | Meaning |
|---------|---------|---------|
| `daily_cap` | 100 | Images per day for the whole bot; resets 00:00 UTC; stored in the bot database so it survives restarts and reloads |
| `user_cooldown` | 60 | Seconds between requests per nick |
| `max_concurrent` | 2 | Images generated at the same time; extra requests get "busy" |
| `max_prompt` | 400 | Maximum prompt length in characters |
| `steps` | 4 | FLUX steps, clamped to 1–8 |
| `expiry` | `30d` | How long sharey.org keeps the image |

**Quota accounting is conservative.** A slot is given back only when the provider clearly did not run the request (connection never made, HTTP 4xx such as auth, rate limit or a safety refusal). Timeouts, 5xx errors, malformed responses and failed uploads keep the slot used, because the provider allowance may have been spent.

Safety-filter refusals are reported as-is ("that prompt was refused by the provider's safety filter."); the bot never retries or rewords a refused prompt.

---

## How it works

1. `POST https://api.cloudflare.com/client/v4/accounts/<account_id>/ai/run/<model>` with `{"prompt": ..., "steps": ...}`; the base64 image is decoded (responses over 16 MB are rejected).
2. `POST https://sharey.org/api/upload` (public, no key) as multipart `file` plus `expiry`. A `200` or `201` reply with a `urls` array is accepted, and only a clean `https://sharey.org/...` link is posted.

---

*Sopel: the plugin uses the standard plugin API and should also load on Sopel 8.x, but it is only tested on ibot.*
