# 🧩 Setup Guide

**Designed for [ibot](https://github.com/lord3nd3r/ibot). Should also work with Sopel.**

This guide takes you from nothing to an ibot running this plugin collection, and uses the trivia game as the first thing to test. If you're on Sopel, the main steps still apply in spirit, and [Appendix: Sopel](#appendix-sopel) at the end lists the differences.

---

## 1. Install ibot

ibot needs Python 3.8 or newer (production runs 3.13).

```bash
git clone https://github.com/lord3nd3r/ibot.git ~/ibot
cd ~/ibot
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

For more detail (optional MySQL/PostgreSQL drivers, upgrading), see [ibot: Installation](https://github.com/lord3nd3r/ibot/blob/main/docs/getting-started/installation.md).

## 2. Get the plugins

```bash
git clone https://github.com/lord3nd3r/sopel-scripts.git ~/.sopel/scripts
pip install requests beautifulsoup4 defusedxml yfinance   # in the same venv as ibot
```

The checkout looks like this (trivia files shown):

```
~/.sopel/scripts/
  ├── trivia.py           (plugin: IRC commands)
  ├── trivia_game.py      (game engine)
  ├── trivia_db.py        (SQLite stats layer)
  ├── questions.json      (431 questions in 18 categories)
  ├── _cli_trivia.py      (CLI tester, not a plugin)
  ├── _demo_trivia.py     (demo script, not a plugin)
  └── … every other plugin
```

ibot skips files that start with `_`, so the CLI and demo scripts never load as plugins.

## 3. Configure ibot

```bash
cd ~/ibot
python3 -m ibot --generate-config mybot.cfg
chmod 600 mybot.cfg
```

Set at least these keys (all values are placeholders):

```ini
[core]
nick = MyBot
host = irc.example.net
port = 6697
use_ssl = true
owner = YourNick
channels = #yourchannel
prefix = \$
db_filename = mybot.db
extra = ~/.sopel/scripts
```

- `prefix` is a regular expression. ibot's default is `\.` (`.trivia`), and these docs use `\$` (`$trivia`).
- `extra` is a comma-separated list of plugin directories. Changing it requires a restart.
- Plugin settings go in their own sections in this same file. See [Configuring plugins](README.md#-configuring-plugins).

For every option, see [ibot: Core reference](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/core-reference.md); for SASL/NickServ and owner accounts, see [ibot: Authentication](https://github.com/lord3nd3r/ibot/blob/main/docs/configuration/authentication.md).

## 4. Start the bot

```bash
cd ~/ibot
python3 -m ibot mybot.cfg -v
```

Check the log for `Plugin directories: [..., '/home/you/.sopel/scripts']` and a `Loaded plugin: trivia` line. To run it permanently, see [ibot: Running as a service](https://github.com/lord3nd3r/ibot/blob/main/docs/getting-started/running-as-a-service.md).

## 5. Test in IRC

```irc
<YourNick> $trivia 5
<Bot> Starting trivia! 5 questions loaded. Answer in the channel!
<Bot> 1. Geography: What is the capital of France?
<Bot> Hint: p****
<YourNick> paris
<Bot> Winner: YourNick; Answer: Paris; Time: 3.456s; Streak: 1; Points: 3; Total: 3
```

### Reloading after changes

| You changed… | Do this (owner) |
|---|---|
| One plugin file | `$reload trivia` |
| Added a new plugin file | `$load <name>` |
| The `.cfg` file, or several plugins | `$rehash` |
| `extra`, database or flood settings | Restart the bot |

None of these except the restart disconnect the bot. A reload that fails keeps the old version running. See [ibot: Plugin management](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/plugin-management.md).

> **Note:** `$reload trivia` re-imports only `trivia.py`. If you change `trivia_game.py` or `trivia_db.py`, restart the bot.

---

## 📋 Trivia commands

| Command | Description | Example |
|---------|-------------|---------|
| `$trivia [category] [number]` | Start a game (default 100 questions, max 500) | `$trivia 10` |
| `$trivia categories` / `$tcategories` | List the question categories | `$tcats` |
| `$strivia` | Stop the current game | `$strivia` |
| `$tstats [nick]` | Show your stats or another player's | `$tstats CrystalMath` |
| `$ttop` | Top 10 in the current channel | `$ttop` |
| `$ttopserver` | Top 10 across the whole server | `$ttopserver` |

## 🎮 Game features

- **Multiple channels:** each channel can run its own game.
- **Progressive hints:** 3 hints, one every 10 seconds.
- **Scoring:** faster answers earn more points (3/2/1).
- **Streaks:** consecutive correct answers build a streak.
- **Auto-stop:** the game warns after 3 unanswered questions and stops after 5.
- **Persistent statistics:** everything is saved to SQLite.

## 📊 Statistics

Stats are saved to `trivia_stats.db` **next to `trivia.py`** (for example `~/.sopel/scripts/trivia_stats.db`) and survive restarts. Per-player, per-channel, server-wide and per-game history are all tracked.

```irc
<You> $tstats
<Bot> YourNick: 150 points | 50 wins | 5 streak | Fastest: 2.34s
```

## 🧪 Testing outside IRC

```bash
cd ~/.sopel/scripts
python3 _cli_trivia.py -i --num 5 --shuffle   # interactive CLI game
python3 _demo_trivia.py                       # hint/category demo
```

## ➕ Adding questions

Add entries to `questions.json`:

```json
{
  "category": "Your Category",
  "question": "Your question?",
  "answer": "your answer"
}
```

Keep answers short and lowercase. Then start a new game; the file is read when each game starts. For multiple-choice entries and the details of hints and scoring, see [trivia_README.md](trivia_README.md) and [docs/trivia.md](docs/trivia.md).

## 🐛 Troubleshooting

**The bot doesn't respond to `$trivia`:**
- Run `$plugins` and check that `trivia` is in the list. If it isn't, look in the startup log for `Failed to load plugin 'trivia'`.
- Check that `[core] extra` points at the folder that contains `trivia.py`, and restart if you changed it.
- Check your prefix: with `prefix = \.` the command is `.trivia`.
- After fixing the problem, run `$reload trivia` (or `$load trivia`).

**"questions.json not found":**
- `questions.json` must be in the same directory as `trivia.py`.
- Check permissions with `ls -la questions.json`.

**The game doesn't stop after inactivity:**
- Timing can vary with server load. Stop it by hand with `$strivia`.

For more help, see [ibot: Troubleshooting](https://github.com/lord3nd3r/ibot/blob/main/docs/troubleshooting.md).

---

## Appendix: Sopel

The plugins use the standard Sopel plugin API, so they should also work on [Sopel](https://sopel.chat/), but ibot is the supported platform.

1. `pip install sopel requests beautifulsoup4 defusedxml yfinance`
2. Put the plugin files (and `questions.json`) in `~/.sopel/scripts/`, Sopel's default plugin folder.
3. Add any plugin sections to Sopel's config (`~/.sopel/default.cfg` by default).
4. Restart Sopel, or use Sopel's own `.reload trivia`.

The ibot-only admin commands (`$rehash`, `$bjoin`, `$bstatus` and so on) aren't available on Sopel. See [Using with Sopel](README.md#-using-with-sopel).

---

**Ready to play!** Type `$trivia` in any channel the bot is in.
