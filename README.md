<h1 align="center">glitchy</h1>

<p align="center">
  IRC plugins for <a href="https://github.com/lord3nd3r/ibot">ibot</a> — AI chat, games, moderation, and utilities.<br>
  This is the collection that runs the <b>glitchy</b> bot on Rizon.
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.8+" src="https://img.shields.io/badge/python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white"></a>
  <a href="https://github.com/lord3nd3r/ibot"><img alt="ibot" src="https://img.shields.io/badge/platform-ibot-0e7c66?style=flat-square&logo=github&logoColor=white"></a>
  <a href="https://www.rizon.net/"><img alt="IRC Rizon" src="https://img.shields.io/badge/IRC-Rizon-1b6ac6?style=flat-square"></a>
  <a href="#-license"><img alt="License: as-is" src="https://img.shields.io/badge/license-as--is-lightgrey?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://3nd3r.net/paypal"><img alt="Donate with PayPal" src="https://img.shields.io/badge/Donate-PayPal-0070ba?style=flat-square&logo=paypal&logoColor=white"></a>
  <a href="https://3nd3r.net/cashapp"><img alt="Donate with Cash App" src="https://img.shields.io/badge/Donate-Cash%20App-00d632?style=flat-square&logo=cashapp&logoColor=white"></a>
  <a href="https://3nd3r.net/venmo"><img alt="Donate with Venmo" src="https://img.shields.io/badge/Donate-Venmo-008cff?style=flat-square&logo=venmo&logoColor=white"></a>
  <a href="https://3nd3r.net/btc"><img alt="Donate with Bitcoin" src="https://img.shields.io/badge/Donate-BTC-f7931a?style=flat-square&logo=bitcoin&logoColor=white"></a>
</p>

The command prefix used here (and by glitchy) is `$`. ibot's default is `.` — set `prefix = \$` in the ibot config to match these examples. The full command list is in [`commands.md`](commands.md). Plugin details live in [`docs/`](docs/). For a longer walkthrough, see [`SETUP_GUIDE.md`](SETUP_GUIDE.md). Support is in `#glitchy` on Rizon.

## ✨ Highlights

- **AI chat** — talks when addressed, with xAI Grok, local Ollama, or any OpenAI-compatible API, plus memory, web search, and per-channel prompts
- **Games and a coin economy** — mugging, casino games, trivia, hunting, a bartender, karma, and moo
- **Moderation** — antispam, join/part flood protection, auto-voice, and auto-op
- **Channel utilities** — weather, stocks, YouTube and URL titles, RSS feeds, wiki, polls, quotes, and more
- **Hot reload** — on [ibot](https://github.com/lord3nd3r/ibot), `$reload` and `$rehash` pick up changes without dropping the IRC connection

## 🚀 Quick start with ibot

ibot needs Python 3.8 or newer (production runs 3.13).

```bash
# ibot itself
git clone https://github.com/lord3nd3r/ibot.git ~/ibot
cd ~/ibot
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

# this plugin collection (any path is fine; these docs use ~/.sopel/scripts)
git clone https://github.com/lord3nd3r/ibot-plugins.git ~/.sopel/scripts

# packages used by the plugins, in the same venv (`sqlalchemy` already comes with ibot)
pip install requests beautifulsoup4 defusedxml yfinance
```

Generate a config, then point `[core] extra` at the checkout:

```bash
cd ~/ibot
python3 -m ibot --generate-config mybot.cfg
chmod 600 mybot.cfg
```

```ini
[core]
nick = MyBot
host = irc.example.net
port = 6697
use_ssl = true
verify_ssl = true

owner = YourNick
owner_account = YourAccount
# admins = FriendNick

channels = #yourchannel
# prefix is a regular expression: \. is the ibot default; these docs use \$
prefix = \$
db_filename = mybot.db

# comma-separated list of plugin directories
extra = ~/.sopel/scripts

# exclude = antispam, markov

# auth_method = sasl
# auth_user = MyBot
# auth_password = YOUR_PASSWORD_HERE
```

Every value above is a placeholder. Keep the file at mode `0600` and never paste it anywhere public — it will hold passwords and API keys.

```bash
cd ~/ibot
python3 -m ibot mybot.cfg -v    # drop -v once things work
```

Startup should log the plugin directories and each plugin it loads. To keep the bot running in the background, see [Running as a service](https://github.com/lord3nd3r/ibot/blob/main/docs/getting-started/running-as-a-service.md).

In IRC:

```text
<YourNick> $bstatus           # ibot core: nick, channel, and plugin counts (admins)
<YourNick> $plugins           # ibot core: list loaded plugins (owner)
<YourNick> $help              # link to the command reference
<YourNick> $w London          # weather (needs a PirateWeather key; see docs/weather.md)
<YourNick> $trivia 5          # start a 5-question trivia game
```

After you edit a plugin, run `$reload <plugin>`. After you edit the config, run `$rehash`. Neither one disconnects the bot. Core admin commands are documented in [ibot's admin commands](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/admin-commands.md).

<details>
<summary>How ibot loads this repo</summary>

- Every `*.py` file in the folder is loaded as a plugin. The plugin name is the file name, so `$reload trivia` reloads `trivia.py`.
- Files whose names start with `_` (`_netguard.py`, `_cli_trivia.py`, `_demo_trivia.py`) are skipped. They are shared helpers or command-line tools, not plugins.
- `trivia_game.py` and `trivia_db.py` are support modules for `trivia.py`. They load harmlessly because they register no commands. To hide them from `$plugins`, add them to `exclude`.
- `[core] exclude` and `[core] enable` choose which plugins load. Changes to `extra` take effect only after a restart. See [Plugin management](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/plugin-management.md).
- Some plugins write data files to hard-coded paths under `~/.sopel/` (for example `seen.db`, `tell.db`, and `autovoice_data.json`). That is expected under ibot as well.

</details>

## 📦 Plugins

Click a name for the full command list and notes. [`commands.md`](commands.md) is the command reference.

### AI and moderation

| Plugin | What it does | Docs |
|--------|--------------|------|
| **ai-multi** | Multi-backend AI chatbot (xAI Grok, Ollama, OpenAI) with memory, web search, relay support, and rate limits | [docs/ai-multi.md](docs/ai-multi.md) |
| **antispam** | Rate, content, unicode-art, and copypasta spam protection, with optional Grok kick assist | [docs/antispam.md](docs/antispam.md) |
| **antiflood** | Join/part cycle flood protection and a per-channel whitelist | [docs/antiflood.md](docs/antiflood.md) |
| **jpq** | Join/part/quit cycle flood protection (on by default) | [docs/jpq.md](docs/jpq.md) |
| **operscan** | PM-only scanner that finds network and server operators in a channel | [docs/operscan.md](docs/operscan.md) |
| **harambe** | Join and host history lookup, plus mass-join alerts, for authorized operators | — |

### Games and economy

| Plugin | What it does | Docs |
|--------|--------------|------|
| **mug** | Coin economy with mugging, bounties, a shop, and casino games (dice, slots, roulette, blackjack, hold'em) | [docs/mug.md](docs/mug.md) |
| **beer** | Virtual bartender with drinks, tipping, and the shared mug coin economy | [docs/beer.md](docs/beer.md) |
| **hunt** | Wildlife hunting with guns, ammo, permits, and loot (uses mug coins when mug is loaded) | [docs/hunt.md](docs/hunt.md) |
| **trivia** | Multi-player trivia with 431 questions in 18 categories, hints, streaks, and persistent stats | [docs/trivia.md](docs/trivia.md) |
| **weed** | Smoke and trip sessions with countdown animations (including `$hoss`) | [docs/weed.md](docs/weed.md) |
| **moo** | Moo counter with legendary moos, power moos (`sudo moo` / `sudo µ`), and leaderboards | [docs/moo.md](docs/moo.md) |
| **karma** | Inline `++` / `--` karma with per-channel and global leaderboards | [docs/karma.md](docs/karma.md) |

### Bot administration

| Plugin | What it does | Docs |
|--------|--------------|------|
| **botadmin** | `$restart`, `$breload`, and `$bothelp`. Join, say, raw, rehash, and the rest come from the ibot core admin plugin. | [docs/botadmin.md](docs/botadmin.md) |
| **opme** | Channel self-promotion for authorized users (`$promoteme`) | [docs/opme.md](docs/opme.md) |
| **autoop** | Automatic `+o`, `+h`, or `+v` on join for configured users | [docs/autoop.md](docs/autoop.md) |
| **autovoice** | Activity-based auto-voice after a threshold of messages, with idle voice removal | [docs/autovoice.md](docs/autovoice.md) |
| **join** | Owner command that makes the bot join public or keyed channels | [docs/join.md](docs/join.md) |

### Utilities

| Plugin | What it does | Docs |
|--------|--------------|------|
| **weather** | PirateWeather forecasts, alerts, and space weather | [docs/weather.md](docs/weather.md) |
| **stock** | Stock lookups by ticker or company name through Yahoo Finance | [docs/stock.md](docs/stock.md) |
| **voting** | Multi-option timed channel polls | [docs/voting.md](docs/voting.md) |
| **monitor** | Per-channel chatter stats (lines, words, actions, kicks, joins, parts) | [docs/monitor.md](docs/monitor.md) |
| **wiki** | Search Grokepedia, then Wikipedia | [docs/wiki.md](docs/wiki.md) |
| **quote** | Save and search a channel quote database | [docs/quote.md](docs/quote.md) |
| **rizonhelp** | Rizon network help topics and FAQ | [docs/rizonhelp.md](docs/rizonhelp.md) |
| **tell** | Store a message for an offline nick and deliver it by PM when they next speak | [docs/tell.md](docs/tell.md) |
| **seen** | Report when a nick was last seen speaking in a channel | [docs/seen.md](docs/seen.md) |
| **feeds** | RSS/Atom poster with a global feed list, per-channel subscriptions, and de-duplication (`$feed`) | [docs/feeds.md](docs/feeds.md) |
| **imagegen** | `$img <prompt>` generates an image with Cloudflare Workers AI and posts a sharey.org link | [docs/imagegen.md](docs/imagegen.md) |
| **url_titles** | Fetch and show the HTML title of posted links (`$urltitle on` / `off`) | [docs/url_titles.md](docs/url_titles.md) |
| **youtube_titles** | Show the title and channel for posted YouTube links (`$yt on` / `off`) | [docs/youtube_titles.md](docs/youtube_titles.md) |

### Chat and fun

| Plugin | What it does | Docs |
|--------|--------------|------|
| **markov** | Learns from channel chat and generates sentences from word trigrams | [docs/markov.md](docs/markov.md) |
| **curse** | Demolition Man-style Verbal Morality Statute profanity fines | [docs/curse.md](docs/curse.md) |
| **facepalm** | Reacts to `/me facepalms`, plus `$shrug` | [docs/facepalm.md](docs/facepalm.md) |
| **tableflip** | Four-step table flip animation (`$flip`) | [docs/tableflip.md](docs/tableflip.md) |
| **fix** | Sed-style typo correction (`s/find/replace/`) | [docs/fix.md](docs/fix.md) |
| **cat** | Prints a random unicode cat (`$cat`) | [docs/cat.md](docs/cat.md) |
| **commands** | `$help` / `$commands` / `$cmds` link to the command reference and `#glitchy` | [docs/commands.md](docs/commands.md) |

## 🔧 Configuring plugins

Plugin settings go in the same ibot `.cfg` file, each in its own `[section]`. Most plugins need no settings at all. After you edit the file, run `$rehash`. ibot also accepts environment overrides for declared settings, such as `SOPEL_AI_MULTI_API_KEY`. See [ibot plugin configuration](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/plugin-config.md) and [Authentication](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/authentication.md).

```ini
[ai_multi]
api_key = YOUR_XAI_API_KEY_HERE
chat_backend = grok

[youtube]
api_key = YOUR_YOUTUBE_API_KEY

[imagegen]
account_id = YOUR_CF_ACCOUNT_ID
api_token = YOUR_CF_API_TOKEN

[antiflood]
threshold = 3
ban_duration = 600

[promoteme]
cooldown_seconds = 60
```

`[youtube] api_key` is optional. When it is set, YouTube titles fall back to the Data API for videos that oEmbed refuses (for example, embedding disabled). Image generation also accepts `CF_ACCOUNT_ID` and `CF_API_TOKEN` in the environment instead of the `[imagegen]` keys.

<details>
<summary>All config sections and keys</summary>

Keys are optional unless noted. Values shown elsewhere in this file are placeholders only.

| Plugin | Section | Keys | Docs |
|--------|---------|------|------|
| `ai-multi.py` | `[ai_multi]` | `api_key` (needed for Grok), `model`, `chat_backend`, `search_backend`, `ollama_url`, `ollama_model`, `openai_api_key`, `openai_base_url`, `openai_model`, `system_prompt`, `blocked_channels`, `nl_actions`, `nl_actions_allow`, `nl_actions_deny`, `intent_check`, `banned_nicks`, `ignored_nicks`, `relay_nicks`, `ai_bot_nicks`, `fallback_model`, `fallback_openai` | [docs/ai-multi.md](docs/ai-multi.md) |
| `imagegen.py` | `[imagegen]` | `account_id` and `api_token` (needed, or env `CF_ACCOUNT_ID` / `CF_API_TOKEN`), `model`, `steps`, `daily_cap`, `user_cooldown`, `max_concurrent`, `max_prompt`, `expiry`, `channels` | [docs/imagegen.md](docs/imagegen.md) |
| `youtube_titles.py` | `[youtube]` | `api_key` (optional Data API fallback) | [docs/youtube_titles.md](docs/youtube_titles.md) |
| `antiflood.py` | `[antiflood]` | `window`, `threshold`, `ban_duration`, `banmask_style`, `exempt_modes`, `enabled` | [docs/antiflood.md](docs/antiflood.md) |
| `jpq.py` | `[jpq]` | `window`, `threshold`, `ban_duration`, `banmask_style`, `exempt_modes`, `enabled` | [docs/jpq.md](docs/jpq.md) |
| `hunt.py` | `[hunt]` | `enabled`, `channels`, `spawn_min`, `spawn_max` | [docs/hunt.md](docs/hunt.md) |
| `markov.py` | `[markov]` | `max_rows_per_channel` | [docs/markov.md](docs/markov.md) |
| `moo.py` | `[moo]` | `moo_cooldown`, `sudo_cooldown`, `legendary_chance`, `max_rate_per_15sec`, `sudo_big_loss_chance`, `leet_moo` | [docs/moo.md](docs/moo.md) |
| `mug.py` | `[mug_game]` | `enabled` | [docs/mug.md](docs/mug.md) |
| `opme.py` | `[promoteme]` | `require_admin`, `require_bot_op`, `success_message`, `modes`, `allow_in_all_channels`, `allowed_channels`, `cooldown_seconds`, `temporary_promotion`, `promotion_duration` | [docs/opme.md](docs/opme.md) |
| `voting.py` | `[voting]` | `db_path`, `message_delay` | [docs/voting.md](docs/voting.md) |
| `monitor.py` | `[core]` | `db_dir` (where `chanstats.db` goes; defaults to the config file's directory) | [docs/monitor.md](docs/monitor.md) |
| `harambe.py` | `[harambe]` | `oper_name`, `oper_password`, `access_channel`, `access_list`, `db_path`, `max_results`, `flood_window`, `flood_threshold`, `flood_cooldown`, `collect_channels`, `probe_ctcp_version` | — |

Per-channel AI prompts can also be set in `grok_channel_prompts.json` (see `grok_channel_prompts.json.example`).

</details>

## 📚 Dependencies

| Package | Needed by |
|---------|-----------|
| `requests` | `ai-multi`, `antispam`, `feeds`, `harambe`, `imagegen`, `markov`, `url_titles`, `weather`, `wiki`, `youtube_titles` (and the `_netguard` helper) |
| `beautifulsoup4` | `url_titles` |
| `defusedxml` | `feeds` |
| `yfinance` | `stock` |
| `sqlalchemy` | `feeds`, `karma`, `moo` (already installed with ibot) |

## 🧰 Helpers

These files are not plugins. ibot skips anything whose name starts with `_`.

- **`_netguard.py`** — shared SSRF guard used by plugins that fetch URLs
- **`_cli_trivia.py`** — command-line trivia runner for testing outside IRC (`python3 _cli_trivia.py -i`)
- **`_demo_trivia.py`** — demo script that exercises hint generation, categories, and question loading
- **`trivia_game.py`**, **`trivia_db.py`**, **`questions.json`** — engine, stats, and question set for `trivia.py`
- **`grok_channel_prompts.json.example`** — sample per-channel AI prompts

More trivia notes are in [`trivia_README.md`](trivia_README.md) and [`docs/trivia.md`](docs/trivia.md).

## 🐍 Using with Sopel

These plugins use the standard Sopel plugin API, so they should also work on [Sopel](https://sopel.chat/) 8.x. ibot is the platform they are built and tested on. Some help text and admin integration (`botadmin`, the core `$rehash` / `$bjoin` commands) assume ibot.

1. `pip install sopel`, plus the [dependencies](#-dependencies) above.
2. Put the plugin files in `~/.sopel/scripts/` (Sopel's default plugin folder). Cloning this repo there works, or copy the `*.py` files and `questions.json` (trivia needs it next to `trivia.py`).
3. Put the same `[section]` settings in Sopel's config (`~/.sopel/default.cfg` by default).
4. Restart Sopel, or reload one plugin with Sopel's own `.reload <plugin>` admin command.

For the known differences between the two, see [ibot's Sopel compatibility notes](https://github.com/lord3nd3r/ibot/blob/main/docs/developer-guide/sopel-compatibility.md).

## 📄 License

These scripts are provided as-is for personal and community use.
