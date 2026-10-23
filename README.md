# 🤖 Sopel IRC Bot Scripts

A collection of custom [Sopel](https://sopel.chat/) IRC bot plugins for fun, games, AI interaction, utility, and community engagement.

> **Quick References:**
> - [📋 Full Command Reference (`commands.md`)](commands.md)
> - [🧩 Trivia Setup Guide (`SETUP_GUIDE.md`)](SETUP_GUIDE.md)
> - [📚 Plugin Documentation Directory (`docs/`)](docs/)

---

## 📋 Plugin Directory

All plugins have detailed documentation, command references, and setup instructions in the [`docs/`](docs/) folder.

### 🧠 AI & Moderation

| Plugin | Description | Docs |
|--------|-------------|------|
| **ai-multi** | Multi-backend AI chatbot (xAI Grok, Ollama, OpenAI) with memory, web search, & intoxication system | [docs/ai-multi.md](docs/ai-multi.md) |
| **antispam** | Multi-mode anti-spam protection, copypasta detection, & Grok AI kick protection | [docs/antispam.md](docs/antispam.md) |
| **antiflood** | Join/part cycle flood protection and user whitelist management | [docs/antiflood.md](docs/antiflood.md) |
| **jpq** | Join/Part/Quit cycle flood protection (enabled by default) | [docs/jpq.md](docs/jpq.md) |
| **operscan** | PM-only scanner to identify network and server IRC operators in a channel | [docs/operscan.md](docs/operscan.md) |

### 🎮 Games & Economy

| Plugin | Description | Docs |
|--------|-------------|------|
| **mug** | IRC economy game with coins, mugging, bounties, casino games (dice, slots, roulette, blackjack, hold'em), & item shop | [docs/mug.md](docs/mug.md) |
| **beer** | Virtual bartender with drinks, tipping, and shared coin economy | [docs/beer.md](docs/beer.md) |
| **hunt** | Wildlife hunting game with guns, ammo, steady aim mechanics, permits, and bounties | [docs/hunt.md](docs/hunt.md) |
| **trivia** | Multi-player trivia game with categories, progressive hints, streaks, and persistent stats | [docs/trivia.md](docs/trivia.md) |
| **weed** | Smoke & trippy sessions with countdown animations and 15 substances | [docs/weed.md](docs/weed.md) |
| **moo** | Moo counter with legendary moos, power moos (`sudo moo`), and leaderboards | [docs/moo.md](docs/moo.md) |
| **karma** | Inline `++` / `--` karma system with per-channel & global leaderboards | [docs/karma.md](docs/karma.md) |

### ⚙️ Bot Administration

| Plugin | Description | Docs |
|--------|-------------|------|
| **botadmin** | Bot owner and admin management commands (`$restart`, `$breload`, `$raw`, `$say`, `$bjoin`, etc.) | [docs/botadmin.md](docs/botadmin.md) |
| **opme** | Channel self-promotion command for authorized users (`$promoteme`) | [docs/opme.md](docs/opme.md) |
| **autoop** | Automatic mode granting (`+o`, `+h`, `+v`) on join for configured users | [docs/autoop.md](docs/autoop.md) |
| **autovoice** | Activity-based auto-voicing (`+v`) after 50 messages (adjustable per channel), with idle voice removal | [docs/autovoice.md](docs/autovoice.md) |
| **join** | Owner command to make the bot join public or key-protected channels | [docs/join.md](docs/join.md) |

### 🛠️ Utility & Information

| Plugin | Description | Docs |
|--------|-------------|------|
| **weather** | PirateWeather forecasts, 8-day extended views, weather alerts, and space weather | [docs/weather.md](docs/weather.md) |
| **stock** | Stock market lookups by ticker symbol or company name via Yahoo Finance | [docs/stock.md](docs/stock.md) |
| **voting** | Multi-option timed channel polls with visual progress bars | [docs/voting.md](docs/voting.md) |
| **monitor** | Per-channel chatter statistics (lines, words, actions, kicks, joins, parts) | [docs/monitor.md](docs/monitor.md) |
| **wiki** | Search Grokepedia and Wikipedia directly from IRC | [docs/wiki.md](docs/wiki.md) |
| **quote** | Save and search channel quote database | [docs/quote.md](docs/quote.md) |
| **rizonhelp** | Quick Rizon IRC network help topics and FAQ | [docs/rizonhelp.md](docs/rizonhelp.md) |
| **tell** | Offline message storage delivered via PM when target speaks | [docs/tell.md](docs/tell.md) |
| **seen** | Track and report when a user was last seen speaking in channel | [docs/seen.md](docs/seen.md) |
| **feeds** | RSS/Atom feed poster: global feed list, per-channel subscriptions, de-duplication, & link shortening (`$feed`) | [docs/feeds.md](docs/feeds.md) |
| **url_titles** | Automatic webpage HTML `<title>` tag fetcher | [docs/url_titles.md](docs/url_titles.md) |
| **youtube_titles** | Auto-detect YouTube links and display video titles and channels | [docs/youtube_titles.md](docs/youtube_titles.md) |

### 💬 Chat & Fun Enhancements

| Plugin | Description | Docs |
|--------|-------------|------|
| **markov** | Markov chain chatbot that learns channel chat and generates weighted sentences | [docs/markov.md](docs/markov.md) |
| **curse** | Demolition Man-style Verbal Morality Statute profanity fine citations | [docs/curse.md](docs/curse.md) |
| **facepalm** | Auto-reaction to `/me facepalms` and `$shrug` output | [docs/facepalm.md](docs/facepalm.md) |
| **tableflip** | 4-step table flip ASCII animation sequence (`$flip`) | [docs/tableflip.md](docs/tableflip.md) |
| **fix** | Sed-style typo correction (`s/find/replace/`) | [docs/fix.md](docs/fix.md) |

---

## 🧪 Helper & CLI Tools

Non-plugin helper tools included in the repository:

- **`cli_trivia.py`**: Command-line interactive trivia runner for testing outside IRC (`python3 cli_trivia.py -i`).
- **`demo_trivia.py`**: Demo script testing progressive hint generation, categories, and question loading.

---

## 📦 Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/lord3nd3r/sopel-scripts.git
   ```

2. **Copy scripts to your Sopel scripts directory:**
   ```bash
   cp sopel-scripts/*.py ~/.sopel/scripts/
   cp sopel-scripts/questions.json ~/.sopel/scripts/
   ```

3. **Install Python dependencies:**
   ```bash
   pip install sopel requests yfinance
   ```

4. **Configure your bot** in `default.cfg` (see [Configuration](#-configuration-overview)).

5. **Restart Sopel:**
   ```bash
   sopel restart
   ```

---

## ⚙️ Configuration Overview

Most plugins work out of the box. For plugins requiring configuration, add settings to your Sopel `.cfg` file (e.g., `default.cfg`):

| Script | Config Section | Notes | Detailed Docs |
|--------|---------------|-------|---------------|
| `ai-multi.py` | `[ai_multi]` | API keys (xAI/OpenAI) and chat backends | [docs/ai-multi.md](docs/ai-multi.md) |
| `mug.py` | `[mug_game]` | Channel toggle & game balance settings | [docs/mug.md](docs/mug.md) |
| `opme.py` | `[promoteme]` | Admin permissions & mode settings | [docs/opme.md](docs/opme.md) |
| `monitor.py` | `[channelstats]` | Channel list & database path | [docs/monitor.md](docs/monitor.md) |
| `moo.py` | `[moo]` | Counter tuning parameters | [docs/moo.md](docs/moo.md) |
| `voting.py` | `[voting]` | Database path tuning | [docs/voting.md](docs/voting.md) |

---

## 📝 License

These scripts are provided as-is for personal and community use.

*Made with ❤️ for the IRC community*
