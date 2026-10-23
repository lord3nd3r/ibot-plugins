# 🔗 Markov Chain (markov)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Markov chain chatbot plugin — learns from channel messages and generates random sentences by chaining word triplets (trigrams) with weighted probabilities.

Originally by **ComputerTech**, updated by **End3r**.

---

## How It Works

The bot silently reads every message in a Markov-enabled channel and breaks it into overlapping 3-word sequences (trigrams). Each trigram is stored in a database with a frequency count — the more often a word follows a pair, the higher its weight.

When generating, the bot picks a starting word (random or user-provided), then walks the chain: "given words A and B, what word C came next most often?" — weighted by frequency. The result is a sentence that sounds vaguely like the channel's collective vocabulary.

**Example of how a sentence is learned:**

Given the message: `the cat sat on the mat`

The bot stores these trigrams:
```
[NULL, NULL]   → "the"      (sentence start)
[NULL, "the"]  → "cat"
["the", "cat"] → "sat"
["cat", "sat"] → "on"
["sat", "on"]  → "the"
["on", "the"]  → "mat"
["the", "mat"] → END         (sentence end)
```

Each time the same trigram appears, its frequency increases — making common phrases more likely to be generated.

**Things the bot ignores when learning:**
- Messages containing URLs
- Messages with 2 or fewer words
- Private messages (only learns from channels)

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/markov.py
```

**2. No config file changes needed.** Trigram data is stored in its own SQLite database at `~/.sopel/markov.db` (created automatically on first load). Channel settings (enabled, chance) use the bot's built-in `bot.db`.

**3. Load the plugin** (bot owner):
```
$reload markov
```
(`$breload markov` also works.)

**4. Enable it in a channel** (requires channel op):
```
$markovon
```

---

## Admin Commands (Channel Op Required)

| Command | Description |
|---------|-------------|
| `$markovon` | Enable Markov learning in the current channel |
| `$markovon <0-100>` | Enable + set auto-trigger chance (percentage) |
| `$markovoff` | Disable Markov entirely (stops learning and auto-trigger) |
| `$markovchance <0-100>` | Set the auto-trigger percentage (0 = off, 100 = every message) |
| `$clearmarkov` | Wipe the entire Markov brain for this channel (channel owner only) |
| `$markovlog <url>` | Bulk-import a plain text log to bootstrap the brain (**bot admin only**) |

### Examples

**Enable markov with 5% auto-trigger:**
```
<@End3r> $markovon 5
<+glitchy> Markov enabled (auto-trigger: 5%).
```

**Disable markov:**
```
<@End3r> $markovoff
<+glitchy> Markov disabled.
```

**Change auto-trigger rate:**
```
<@End3r> $markovchance 10
<+glitchy> Auto-trigger chance set to 10%.
```

**Wipe the brain and start over:**
```
<@End3r> $clearmarkov
<+glitchy> Cleared the Markov chain for #channel.
```

**Import a chat log to seed the brain:**
```
<@End3r> $markovlog https://example.com/chatlog.txt
<+glitchy> End3r: Fetching log...
<+glitchy> Imported 1234 lines.
```

> **Note:** `$markovlog` is restricted to bot admins and must be used in a channel that has Markov enabled. The log URL must be `http://` or `https://` on a public host — private, loopback and internal addresses are refused, including via redirects. Logs over **5 MB** are refused, and the download has a 60-second limit. Only one import runs at a time. The file should be plain text with one message per line. URLs in the log are automatically skipped during import. Failures show a short message (e.g. `Failed to fetch log (HTTP 404).`); details go to the bot log only.

---

## User Commands

| Command | Description |
|---------|-------------|
| `$markov` | Generate a random sentence from the channel's brain |
| `$markov <word>` | Generate a sentence starting from a specific seed word |
| `$markovfor <#channel>` | Generate using another channel's brain |
| `$markovfor <#channel> <word>` | Generate from another channel with a seed word |

### Examples

**Generate a random sentence:**
```
<User> $markov
<+glitchy> the cat sat on the mat and then exploded
```

**Generate with a seed word:**
```
<User> $markov pizza
<+glitchy> pizza is the only thing that matters in this world
```

**Generate from another channel's brain:**
```
<User> $markovfor #chat
<+glitchy> computers are just rocks we tricked into thinking
```

> **Note:** For `$markovfor`, you must be in the target channel (whether you ask in PM or in a channel). If you ask from a *different* channel, the result is sent to you by private message as `[#chan] sentence` so one channel's chat isn't replayed in another.

---

## Auto-Trigger

When `markov-chance` is set to a value greater than 0, the bot has that percentage chance of spontaneously generating a sentence in response to any channel message. The generated sentence is seeded from a random word in the triggering message, making it loosely topical.

- `$markovon 5` → 5% chance on every message
- `$markovchance 0` → disables auto-trigger (bot still learns)
- `$markovoff` → disables everything (stops learning too)

---

## Data Storage

All trigram data is stored in a separate SQLite database at `~/.sopel/markov.db`, keeping it isolated from the bot's main database. The file is created automatically on first load. WAL journal mode is used for better concurrent read/write performance.

| Column | Type | Description |
|--------|------|-------------|
| `channel` | TEXT | Channel name (e.g. `#chat`) |
| `first_word` | TEXT | First word of the trigram (NULL for sentence start) |
| `second_word` | TEXT | Second word of the trigram |
| `third_word` | TEXT | Third word of the trigram (NULL for sentence end) |
| `frequency` | INTEGER | How many times this trigram has been seen |

The primary key is `(channel, first_word, second_word, third_word)` — so each unique trigram per channel is stored exactly once with a running count.

### Optional size cap (off by default)

The database grows forever unless you set a cap in your ibot `.cfg`:

```ini
[markov]
max_rows_per_channel = 2000000
```

When this is above 0, an hourly job deletes the lowest-frequency trigrams from any channel over the cap, at most 20,000 rows per channel per run. With the default (`0`/unset) nothing is ever deleted.

---

## Tips

- **More chat = better output.** The brain needs hundreds of messages before it generates anything coherent. Be patient.
- **Use `$markovlog`** to bootstrap a new channel by importing an existing chat log.
- **Keep auto-trigger low** (2-5%) to avoid flooding. Higher values make the bot chatty.
- **Each channel has its own brain.** Enabling markov in `#chat` doesn't affect `#help`.
- **Output is capped at 440 characters** to stay within IRC message limits.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
