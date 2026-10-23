# 🤖 Plugins for ibot

**Designed for [ibot](https://github.com/lord3nd3r/ibot). Should also work with Sopel.**

The official plugin collection for **ibot**: AI chat, channel moderation, games and a coin economy, and everyday utilities for IRC. These are the plugins that run the `glitchy` bot on Rizon.

> **Quick references:**
> - [📋 Full command reference (`commands.md`)](commands.md)
> - [🧩 Setup guide (`SETUP_GUIDE.md`)](SETUP_GUIDE.md)
> - [📚 Per-plugin documentation (`docs/`)](docs/)
> - [📖 ibot documentation](https://github.com/lord3nd3r/ibot/blob/main/docs/index.md)

---

## Why ibot?

[**ibot**](https://github.com/lord3nd3r/ibot) is a small, modern asyncio IRC bot framework by lord3nd3r (`End3r` on IRC). It runs Sopel-style plugins, these included, without Sopel installed, and adds:

- **Hot reload that doesn't drop you off IRC.** `$reload <plugin>` and `$rehash` import the new code first, so a broken edit leaves the old version running.
- **IRCv3 and solid connection handling.** SASL or NickServ, TLS, automatic reconnect with backoff, nick regain.
- **Safe defaults.** Outgoing flood control, SSRF-safe web fetching, credentials redacted from logs, a bounded handler thread pool.
- **A built-in admin plugin.** Join/part (remembered across restarts), say/act/raw, status, and per-channel plugin disabling.
- **A Sopel-compatible database.** Same SQLAlchemy schema; SQLite by default, with MySQL/PostgreSQL available.

Start with the [ibot README](https://github.com/lord3nd3r/ibot#readme) and the [Quickstart](https://github.com/lord3nd3r/ibot/blob/main/docs/getting-started/quickstart.md).

---

## 🚀 Quick start with ibot

### 1. Install ibot and this collection

```bash
# ibot itself
git clone https://github.com/lord3nd3r/ibot.git ~/ibot
cd ~/ibot
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

# this plugin collection
git clone https://github.com/lord3nd3r/sopel-scripts.git ~/.sopel/scripts

# Python packages used by the plugins (install them in the same venv)
pip install requests beautifulsoup4 defusedxml yfinance
```

You can clone the collection anywhere; `~/.sopel/scripts` is only the path these docs use. `sqlalchemy` comes with ibot. To find out exactly which packages each plugin needs, see [Dependencies](#-dependencies).

### 2. Create a config

```bash
cd ~/ibot
python3 -m ibot --generate-config mybot.cfg
chmod 600 mybot.cfg
```

Edit it so that `[core] extra` points at this repository:

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
# The prefix is a regular expression: \. is the ibot default; these docs use \$
prefix = \$
db_filename = mybot.db

# Load this plugin collection (comma-separated list of directories)
extra = ~/.sopel/scripts

# Optional: skip plugins you don't want
# exclude = antispam, markov

# Optional: SASL login for the bot
# auth_method = sasl
# auth_user = MyBot
# auth_password = YOUR_PASSWORD_HERE
```

> **Note:** Every value above is a placeholder. Keep the file at mode `0600` and never paste it anywhere public, because it will hold passwords and API keys.

### 3. Run it

```bash
cd ~/ibot
python3 -m ibot mybot.cfg -v    # -v = debug logging; leave it off once things work
```

The startup log shows the plugin directories and each plugin it loads:

```text
[INFO] ibot: Plugin directories: ['/home/you/ibot/ibot/plugins', '/home/you/.sopel/scripts']
[INFO] ibot.loader: Loaded plugin: weather (…)
```

To keep the bot running in the background, see [Running as a service](https://github.com/lord3nd3r/ibot/blob/main/docs/getting-started/running-as-a-service.md).

### 4. Try it in IRC

```text
<YourNick> $bstatus           ← ibot core: nick, channel and plugin counts (admins)
<YourNick> $plugins           ← ibot core: list loaded plugins (owner)
<YourNick> $help              ← link to the command reference
<YourNick> $w London           ← weather (needs a PirateWeather key, see docs/weather.md)
<YourNick> $trivia 5          ← start a 5-question trivia game
```

After you edit a plugin, run `$reload <plugin>`. After you edit the config, run `$rehash`. Neither one disconnects the bot. The full list of core commands is in [ibot's admin commands](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/admin-commands.md).

### How ibot loads this repo

- Every `*.py` file in the folder is loaded as a plugin. The plugin name is the file name, so `$reload trivia` reloads `trivia.py`.
- Files whose names start with `_` (`_netguard.py`, `_cli_trivia.py`, `_demo_trivia.py`) are **skipped**. They're shared helpers or command-line tools, not plugins.
- `trivia_game.py` and `trivia_db.py` are support modules for `trivia.py`. They load harmlessly because they register no commands. If you'd rather not see them in `$plugins`, add them to `exclude`.
- `[core] exclude` and `[core] enable` choose which plugins load. Changes to `extra` take effect only after a restart. For details, see [Plugin management](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/plugin-management.md).
- Some plugins write data files to hard-coded paths under `~/.sopel/` (for example `seen.db`, `tell.db` and `autovoice_data.json`). That is expected under ibot as well.

---

## 🔧 Configuring plugins

Plugin settings go in **the same ibot `.cfg` file**, each in its own `[section]`. Most plugins need no settings at all. These are the sections the code reads:

| Plugin | Section | Keys (all optional unless noted) | Docs |
|---|---|---|---|
| `ai-multi.py` | `[ai_multi]` | `api_key` (needed for Grok), `model`, `chat_backend`, `search_backend`, `ollama_url`, `ollama_model`, `openai_api_key`, `openai_base_url`, `openai_model`, `system_prompt`, `blocked_channels`, `nl_actions`, `nl_actions_allow`, `nl_actions_deny`, `intent_check`, `banned_nicks`, `ignored_nicks` | [docs/ai-multi.md](docs/ai-multi.md) |
| `antiflood.py` | `[antiflood]` | `window`, `threshold`, `ban_duration`, `banmask_style`, `exempt_modes`, `enabled` | [docs/antiflood.md](docs/antiflood.md) |
| `jpq.py` | `[jpq]` | `window`, `threshold`, `ban_duration`, `banmask_style`, `exempt_modes`, `enabled` | [docs/jpq.md](docs/jpq.md) |
| `hunt.py` | `[hunt]` | `channels`, `spawn_min`, `spawn_max` | [docs/hunt.md](docs/hunt.md) |
| `markov.py` | `[markov]` | `max_rows_per_channel` | [docs/markov.md](docs/markov.md) |
| `moo.py` | `[moo]` | `moo_cooldown`, `sudo_cooldown`, `legendary_chance`, `max_rate_per_15sec`, `sudo_big_loss_chance`, `leet_moo` | [docs/moo.md](docs/moo.md) |
| `mug.py` | `[mug_game]` | `enabled` | [docs/mug.md](docs/mug.md) |
| `opme.py` | `[promoteme]` | `require_admin`, `require_bot_op`, `success_message`, `modes`, `allow_in_all_channels`, `allowed_channels`, `cooldown_seconds`, `temporary_promotion`, `promotion_duration` | [docs/opme.md](docs/opme.md) |
| `voting.py` | `[voting]` | `db_path`, `message_delay` | [docs/voting.md](docs/voting.md) |
| `monitor.py` | `[core]` | `db_dir` (where `chanstats.db` goes; defaults to the config file's directory) | [docs/monitor.md](docs/monitor.md) |

Example:

```ini
[ai_multi]
api_key = YOUR_XAI_API_KEY_HERE
chat_backend = grok

[antiflood]
threshold = 3
ban_duration = 600

[promoteme]
cooldown_seconds = 60
```

Run `$rehash` after you edit the file. ibot also accepts environment overrides for declared settings, such as `SOPEL_AI_MULTI_API_KEY`. For the general rules, see [ibot plugin configuration](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/plugin-config.md), and for the bot owner and admin settings see [Authentication](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/authentication.md).

---

## 📦 Dependencies

| Package | Needed by |
|---|---|
| `requests` | `ai-multi`, `antispam`, `feeds`, `harambe`, `markov`, `url_titles`, `weather`, `wiki`, `youtube_titles` (and the `_netguard` helper) |
| `beautifulsoup4` | `url_titles` |
| `defusedxml` | `feeds` |
| `yfinance` | `stock` |
| `sqlalchemy` | `feeds`, `karma`, `moo` (already installed with ibot) |

When a plugin fails to import, ibot logs the error at startup and carries on without that plugin.

---

## 📋 Plugin directory

Every plugin has its own page in [`docs/`](docs/) with commands, examples and setup notes. The examples use the `$` prefix.

### 🧠 AI & Moderation

| Plugin | Description | Docs |
|--------|-------------|------|
| **ai-multi** | Multi-backend AI chatbot (xAI Grok, Ollama, OpenAI) with memory, web search, and an intoxication system | [docs/ai-multi.md](docs/ai-multi.md) |
| **antispam** | Multi-mode anti-spam protection, copypasta detection, and Grok AI kick protection | [docs/antispam.md](docs/antispam.md) |
| **antiflood** | Join/part cycle flood protection and user whitelist management | [docs/antiflood.md](docs/antiflood.md) |
| **jpq** | Join/Part/Quit cycle flood protection (enabled by default) | [docs/jpq.md](docs/jpq.md) |
| **operscan** | PM-only scanner that finds network and server IRC operators in a channel | [docs/operscan.md](docs/operscan.md) |

### 🎮 Games & Economy

| Plugin | Description | Docs |
|--------|-------------|------|
| **mug** | IRC economy game with coins, mugging, bounties, casino games (dice, slots, roulette, blackjack, hold'em) and an item shop | [docs/mug.md](docs/mug.md) |
| **beer** | Virtual bartender with drinks, tipping, and the shared coin economy | [docs/beer.md](docs/beer.md) |
| **hunt** | Wildlife hunting game with guns, ammo, steady-aim mechanics, permits and loot (uses mug coins when mug is loaded) | [docs/hunt.md](docs/hunt.md) |
| **trivia** | Multi-player trivia with categories, progressive hints, streaks and persistent stats | [docs/trivia.md](docs/trivia.md) |
| **weed** | Smoke and trip sessions with countdown animations and 15 substances | [docs/weed.md](docs/weed.md) |
| **moo** | Moo counter with legendary moos, power moos (`sudo moo`) and leaderboards | [docs/moo.md](docs/moo.md) |
| **karma** | Inline `++` / `--` karma with per-channel and global leaderboards | [docs/karma.md](docs/karma.md) |

### ⚙️ Bot Administration

| Plugin | Description | Docs |
|--------|-------------|------|
| **botadmin** | `$restart`, `$breload` and `$bothelp`. (`$raw`, `$say`, `$bjoin`, `$rehash` and the rest come from the ibot core admin plugin.) | [docs/botadmin.md](docs/botadmin.md) |
| **opme** | Channel self-promotion command for authorized users (`$promoteme`) | [docs/opme.md](docs/opme.md) |
| **autoop** | Automatic `+o`, `+h` or `+v` on join for configured users | [docs/autoop.md](docs/autoop.md) |
| **autovoice** | Activity-based auto-voice (`+v`) after 50 messages (adjustable per channel), with idle voice removal | [docs/autovoice.md](docs/autovoice.md) |
| **join** | Owner command that makes the bot join public or key-protected channels | [docs/join.md](docs/join.md) |

### 🛠️ Utility & Information

| Plugin | Description | Docs |
|--------|-------------|------|
| **weather** | PirateWeather forecasts, 8-day extended views, weather alerts and space weather | [docs/weather.md](docs/weather.md) |
| **stock** | Stock lookups by ticker symbol or company name through Yahoo Finance | [docs/stock.md](docs/stock.md) |
| **voting** | Multi-option timed channel polls with progress bars | [docs/voting.md](docs/voting.md) |
| **monitor** | Per-channel chatter statistics (lines, words, actions, kicks, joins, parts) | [docs/monitor.md](docs/monitor.md) |
| **wiki** | Search Grokepedia and Wikipedia from IRC | [docs/wiki.md](docs/wiki.md) |
| **quote** | Save and search a channel quote database | [docs/quote.md](docs/quote.md) |
| **rizonhelp** | Quick Rizon network help topics and FAQ | [docs/rizonhelp.md](docs/rizonhelp.md) |
| **tell** | Stores messages for offline users and delivers them by PM when the user next speaks | [docs/tell.md](docs/tell.md) |
| **seen** | Reports when a user was last seen speaking in a channel | [docs/seen.md](docs/seen.md) |
| **feeds** | RSS/Atom feed poster with a global feed list, per-channel subscriptions, de-duplication and link shortening (`$feed`) | [docs/feeds.md](docs/feeds.md) |
| **url_titles** | Fetches and shows the HTML `<title>` of posted links | [docs/url_titles.md](docs/url_titles.md) |
| **youtube_titles** | Detects YouTube links and shows the video title and channel | [docs/youtube_titles.md](docs/youtube_titles.md) |

### 💬 Chat & Fun

| Plugin | Description | Docs |
|--------|-------------|------|
| **markov** | Markov chain chatbot that learns from channel chat and generates weighted sentences | [docs/markov.md](docs/markov.md) |
| **curse** | Demolition Man-style Verbal Morality Statute profanity fines | [docs/curse.md](docs/curse.md) |
| **facepalm** | Reacts to `/me facepalms`, plus `$shrug` | [docs/facepalm.md](docs/facepalm.md) |
| **tableflip** | 4-step table flip ASCII animation (`$flip`) | [docs/tableflip.md](docs/tableflip.md) |
| **fix** | Sed-style typo correction (`s/find/replace/`) | [docs/fix.md](docs/fix.md) |
| **cat** | Random unicode cat (`$cat`) | [docs/cat.md](docs/cat.md) |
| **commands** | `$help` / `$commands` / `$cmds` reply with a link to the command reference | [docs/commands.md](docs/commands.md) |

---

## 🧪 Helper & CLI tools

These files are not plugins. ibot skips anything whose name starts with `_`.

- **`_netguard.py`**: Shared SSRF guard used by the plugins that fetch URLs.
- **`_cli_trivia.py`**: Command-line trivia runner for testing outside IRC (`python3 _cli_trivia.py -i`).
- **`_demo_trivia.py`**: Demo script that exercises hint generation, categories and question loading.

---

## 🐍 Using with Sopel

These plugins use the standard Sopel plugin API, so they should also work on [Sopel](https://sopel.chat/) 8.x, although ibot is the platform they're built and tested on. Some help text and admin integration (`botadmin`, the core `$rehash`/`$bjoin` commands) assume ibot.

1. `pip install sopel`, plus the [dependencies](#-dependencies) above.
2. Put the plugin files in `~/.sopel/scripts/` (Sopel's default plugin folder). Cloning this repo there works, or copy the `*.py` files and `questions.json` (trivia needs it next to `trivia.py`).
3. Put the same `[section]` settings in Sopel's config (`~/.sopel/default.cfg` by default).
4. Restart Sopel, or reload one plugin with Sopel's own `.reload <plugin>` admin command.

For the known differences between the two, see [ibot's Sopel compatibility notes](https://github.com/lord3nd3r/ibot/blob/main/docs/developer-guide/sopel-compatibility.md).

---

## 📝 License

These scripts are provided as-is for personal and community use.

*Made with ❤️ for the IRC community.*
