# 🧠 AI Chatbot (ai-multi)

Talks to you when mentioned by name. Supports xAI Grok, local Ollama, and OpenAI-compatible APIs, with optional Grok web search. **v6.0** — memory-safe, proper lifecycle, thread-safe caches.

---

## Setup

**1. Install dependencies:**
```bash
pip install requests
```

**2. Add to your ibot `.cfg` file (e.g. `~/ibot/glitchy.cfg`):**
```ini
[ai_multi]
# Required when using Grok chat or search (get one from https://console.x.ai)
api_key = xai-XXXXXXXXXXXXXXXXXXXXXXXX

# Chat backend: grok (default), ollama, or openai
chat_backend = grok

# Search backend: grok (default) or none
search_backend = grok

# Grok model (default: grok-4.3)
model = grok-4.3

# Ollama settings (when chat_backend = ollama)
ollama_url = http://localhost:11434
ollama_model = llama3.2

# OpenAI-compatible settings (when chat_backend = openai)
openai_api_key =
openai_base_url = https://api.openai.com/v1
openai_model = gpt-4o

# Optional — custom system prompt (the bot's personality)
system_prompt = You are Glitchy, a regular in this IRC channel. You're sharp, geeky, and a little sarcastic.

# Optional — channels where the bot will NOT respond
blocked_channels =
    #quiet-room
    #no-bots

# Optional — nicks the bot will completely ignore
banned_nicks =
    SpamBot
    AnnoyingUser

# Optional — nicks ignored in conversation (no replies, but still tracked)
ignored_nicks =
    LogBot

# Optional — how to detect if a mention is directed at the bot
# Choices: heuristic (default), off, model
#   heuristic — skip third-person / "about the bot" mentions
#   off       — respond to any nick mention
#   model     — not implemented; falls back to heuristic
intent_check = heuristic

# Optional — natural-language actions ("glitchy get me a beer" runs $beer)
nl_actions = on
# extra public command names to allow / command names to never auto-run
nl_actions_allow =
nl_actions_deny =
```

**Personality (natural language to the bot):** defaults to **per-user**.  
Channel-wide (`in this channel` / `for everyone`) requires **op or bot admin**.

**3. Place the script in your ibot plugin extra directory:**
```
~/.sopel/scripts/ai-multi.py
```

> **Note:** The bot runs on [ibot](https://github.com/lord3nd3r/ibot), a custom asyncio IRC framework with a Sopel-compatible shim. Per-channel system prompts can be set via `grok_channel_prompts.json` in the scripts directory. Set `search_backend = none` when using a local backend without Grok search.

---

## Commands

### Talk to the Bot

| Command | Who | Description |
|---------|-----|-------------|
| `BotNick: <message>` or `BotNick <message>` | Anyone | Talk to the bot (addressed at start with or without punctuation) |
| `/me pets BotNick` | Anyone | Emote interaction |
| `BotNick: remember <fact>` | Anyone | Save a fact permanently to SQLite |
| `BotNick: forget <fact>` | Anyone | Remove a saved fact (fuzzy match) |
| `BotNick: forget everything` | Anyone | Clear all your saved facts |
| `BotNick: what do you remember about me` | Anyone | List all your saved facts |
| `$grokreset` | Anyone | Reset your conversation history (not permanent facts) |
| `$grokreset channel` | Op+ / Admin | Reset all channel conversation history |
| `$remind <duration> <msg>` | Anyone | Set a timed reminder (e.g. `$remind 24h Do daily`, `$remind me 10m check oven`). Limits: 10 pending per user, 50 per channel, 300-char message, one `$remind` per 3s per user |
| `$remind list` | Anyone | List your pending reminders (first 5 shown, then "+N more") |
| `$remind del <id>` | Anyone | Cancel a pending reminder by ID |
| `$badmin <add\|del\|list>` | Op+ / Admin | Manage per-channel bot admins (`$badmin add [#chan] <nick>`) |
| `$ai <on\|off>` | Op+ / Admin | Enable/disable AI entirely for the channel |
| `$talkback <on\|off>` | Op+ / Admin | Enable/disable unprompted chime-ins |
| `$testemote` | Admin | Check that the AI plugin is loaded and responding |

### Profiles & Learned Facts

| Command | Who | Description |
|---------|-----|-------------|
| `$profile [nick]` | Anyone (own) / Admin (others) | Show a stored profile. Always **sent by PM** (in a channel the bot just says "Sent you the profile by PM."). At most 10 fact lines; one use per 5s per user |
| `$setprofile <nick> <field> <value>` | Admin | Set `nationality`, `location` or `weather_location` |
| `$addfact <nick> <fact>` | Admin | Add a fact to a profile |
| `$delfact <nick> <index>` | Admin | Remove a fact by index |
| `$delprofile <nick>` | Admin | Delete a whole profile |
| `$reviewfacts [limit]` | Admin | List pending auto-learned fact suggestions |
| `$approve <id>` / `$reject <id>` | Admin | Accept or discard a fact suggestion |
| `$learnfacts <nick>` | Admin | Run fact learning for a user now |

### Diagnostics (Admin)

| Command | Description |
|---------|-------------|
| `$aihistory` | Show the last prompt sent to the model (PM only) |
| `$aistats` | AI health counters (always replied by PM) |
| `$testapi` | Test the Grok API directly |
| `$showextract` | Show the last fact-extraction API response |

### Admin PM Commands

| Command | Description |
|---------|-------------|
| `$part #channel` | Make bot leave a channel |
| `$ignore <nick>` | Add nick to ignore list |
| `$unignore <nick>` | Remove nick from ignore list |
| `$shape #channel <directive>` / `$steer #channel <directive>` | Send an AI directive into a target channel (inherits channel intoxication/sober state); needs admin rights for that channel |

These are read from PMs to the bot, so they must be sent by PM. The plugin also has `$join`, `$say`, `$act` and `$tell #channel` here, but under ibot a real command with the same name always wins (`$join` → join.py, `$say`/`$act` → the core admin plugin, `$tell` → tell.py), so those four never reach ai-multi.

### 🔍 Moderation: Schizo Check

AI-powered chat analysis for channel moderation. Scans recent messages for incoherent, delusional, or conspiratorial content.

| Command | Who | Description |
|---------|-----|-------------|
| `$scheck` | Op+ / Admin | Scan last 100 messages from all users (in channel) |
| `$scheck <nick>` | Op+ / Admin | Scan only that user's messages (in channel) |
| `$scheck #channel [nick]` | Admin | Scan from PM (no channel visibility) |
| `$skick <nick> <#channel>` | Op+ / Admin | Kick user from channel |
| `$skban <nick> <#channel>` | Op+ / Admin | Kick-ban user from channel |

**How it works:**
1. Op/admin runs `$scheck` or `$scheck SomeUser`
2. Bot sends "Scanning..." confirmation
3. AI analyzes the messages and PMs results to the requester
4. If a user was targeted, PM includes `$skick` / `$skban` action commands
5. Op can run the action to kick or kickban

> **Note:** Results are always sent via PM, never in the channel. PM mode (`$scheck #channel nick`) is admin-only.

---

## 🔍 Web Search (Automatic)

The bot **automatically** uses live web search when it detects that your question is about something time-sensitive or factual. There is no special command — just ask naturally and the bot decides whether to search.

**Trigger keywords** (any of these in your message activates search):

| Category | Keywords |
|----------|----------|
| News & Events | `news`, `latest`, `recent`, `today`, `yesterday`, `tonight`, `this week`, `this month`, `current events`, `headlines`, `breaking`, `update` |
| Sports | `score`, `results`, `standings`, `who won`, `who is winning` |
| Finance | `stock price`, `price of`, `worth`, `market`, `stock`, `stocks`, `crypto`, `bitcoin`, `btc`, `ethereum`, `eth` |
| Weather & Disasters | `weather`, `forecast`, `drought`, `flooding`, `hurricane`, `tornado`, `earthquake`, `wildfire` |
| People & Events | `who died`, `who is`, `is ___ dead`, `did ___ happen`, `election`, `poll` |
| Factual Queries | `what is`, `where is`, `when is/was/did`, `how many/much/long/far/old/tall/big/fast`, `how bad`, `how severe`, `status of`, `population`, `gdp`, `economy`, `inflation`, `interest rate` |
| Temporal | `currently`, `right now`, `at the moment` |
| General | `search`, `whats happening`, `tell me about`, `what do you know about`, `look up`, `find out` |

**Examples:**
```
Grok: what's the latest news today?
Grok: who won the NBA game last night?
Grok: what's the stock price of AAPL?
Grok: what is the price of bitcoin?
Grok: how bad is the drought in Florida?
Grok: search for the election results
Grok: what's the weather forecast for tomorrow?
Grok: tell me about the latest earthquake
```

> **Note:** If the web search API fails, the bot automatically falls back to answering from its training data.

> **Safety net:** If the model attempts to call a tool that wasn't provided (outputting raw XML), the bot automatically strips the garbage, retries the request with web search enabled, and returns a real answer. If the retry also fails, it tells the user to try again instead of outputting gibberish.

### Asking for Sources

You can ask the bot to include source links in its response:

```
Grok: what's the latest news? show me the links
Grok: search for election results with sources
```

**Trigger phrases:** `show me the links`, `sources`, `citations`, `references`, `urls`, `include links`

> **Note:** The bot uses a strict extraction system that queries the API for genuine deep-link article URLs to prevent hallucinations. It does not output raw links mid-sentence; instead, it generates a clean list of verified, clickable sources at the end of its response.

---

## 📋 Command Help ("how do I ...?")

When someone asks the bot about its own commands (e.g. `glitchy what commands do you have?`, `glitchy how do I check the weather?`, `glitchy what's the syntax for $feed?`), it answers from a **live command catalog** instead of guessing.

- The catalog is built from the commands and inline triggers ibot actually has registered (plugin decorators, docstrings, and examples), with gaps filled from the `docs/*.md` command tables. It is cached and only rebuilt when the loaded plugin set changes (`$rehash`, `$reload`, `$load`, `$unload`).
- The bot is told to answer **only** with commands from the catalog and not to invent commands or options.
- **Admin/op-only commands are hidden** unless the asker is privileged, and are never listed in a public channel for an unprivileged user.
- Each ready-to-type command is sent on **its own IRC line** so it can be copied directly.
- Short follow-ups within 3 minutes of a command question are treated as more help, not as an order.

---

## 🍺 Natural-Language Actions

A line addressed to the bot that plainly asks for something a **public, harmless** command does is run through ibot's normal command dispatch, exactly as if the user had typed the command — permissions, ignore lists, disabled plugins, and rate limits still apply, and the plugin's own output is the reply.

| You say | Runs |
|---------|------|
| `glitchy get me a beer` | `$beer` |
| `glitchy pour DmD a whiskey` | `$whiskey DmD` |
| `glitchy can I get an irish coffee` | `$coffee irish` |
| `glitchy pass the joint to DmD` | `$pass DmD joint` |
| `glitchy get me the weather in Boston` | `$w Boston` |
| `glitchy flip the table` | `$flip` |

- **Default allowed commands:** `beer.py` drinks/food, `weed.py` sessions, and harmless lookups (`$cat`, `$flip`, `$shrug`, `$karma`, `$seen`, `$w`, `$f`, `$stock`, `$wiki`, `$bal`, `$inv`, moo stats, etc.).
- **Never auto-run:** anything not public, anything involving amounts/numbers (bets, transfers), and moderation/admin/economy commands (antispam, jpq, opme, join, `$give`, `$mug`, `$tell`, `$quote`, `$vote`, `$autovoice`, and similar).
- Questions, past/future tense ("did you…", "I had a beer yesterday"), and help-style phrasing fall through to normal AI chat.
- Per-user cooldown of **8 seconds** between actions.
- Turn off with `nl_actions = off`; add public commands with `nl_actions_allow`, or block commands with `nl_actions_deny` (comma-separated names, `$` optional).

---

## 🎭 Dynamic Personalities (Roleplay)

You can change the bot's personality dynamically in conversation. In channels, these commands change the personality **channel-wide** by default, unless you explicitly request it only for yourself.

### Setting a Personality

Set a new roleplay style or persona:

| Format | Scope | Example |
|--------|-------|---------|
| `BotNick: reply as a <vibe>` | Channel-wide | `Grok: reply as a drunken pirate` |
| `BotNick: from now on be <vibe>` | Channel-wide | `Grok: from now on be a grumpy wizard` |
| `BotNick: reply to me as a <vibe>` | Per-user | `Grok: reply to me as a helpful butler` |
| `BotNick: speak to <nick> like <vibe>` | Target user | `Grok: speak to burnout like a drill sergeant` |

The bot will acknowledge the change, e.g. `ok, new channel-wide personality: drunken pirate` or `ok, new personality for User: helpful butler`.

### Resetting Personality

Clear any dynamic personalities and return the bot to its default configuration:

- `BotNick: reset personality`
- `BotNick: stop acting`
- `BotNick: be yourself`

---

## 🕐 Time & Date Queries

Ask the bot about the current time or date and it responds **instantly with the exact time** from your saved timezone preferences — no AI involved, no jokes, just the real answer. Time queries **bypass rate-limiting** so you can always get a fresh answer.

**Trigger phrases:**
- `what time is it`, `what's the time`, `current time`
- `what's the date`, `what day is it`, `today's date`

**Examples:**
```
Grok: what time is it?
Grok: what's today's date?
Grok: what day is it?
```

> **Note:** Short time queries (≤8 words) go directly to a local clock response. Longer messages that happen to mention time still go through the AI.

### Setting Your Timezone

Tell the bot your timezone and it will remember it for all future time queries:

| Method | Example |
|--------|---------|
| Tell the bot | `Grok: I'm in EST` |
| Explicit set | `Grok: set my timezone to CST` |
| Natural phrasing | `Grok: I live in Pacific` |

**Supported abbreviations:** `EST` / `EDT` / `ET` / `Eastern`, `CST` / `CDT` / `CT` / `Central`, `MST` / `MDT` / `MT` / `Mountain`, `PST` / `PDT` / `PT` / `Pacific`, `UTC` / `GMT`

### Setting Your Time Format

Prefer 12-hour or 24-hour time? Tell the bot:

```
Grok: I prefer 12hr
Grok: use 24 hour
```

Preferences are saved in the database and persist across restarts.

---

## 🧠 Persistent Memory (Remember / Forget)

Tell the bot to remember facts about you — they're stored permanently in SQLite and included in every AI context, so the bot always knows them. Facts survive restarts, rehashes, and conversation resets.

### Saving Facts

```
Grok: remember I live in Florida
Grok: remember my favorite color is blue
Grok: remember if I ask what does florida man say, reply with ┌ಠ_ಠ)┌∩
Grok: remember ComputerTech is the sheep shagger
```

The bot confirms with a short response like "got it, I'll remember that" or "noted".

### Removing Facts

```
Grok: forget about Florida           → fuzzy matches and removes the fact
Grok: forget everything              → clears ALL your saved facts
```

### Viewing Facts

```
Grok: what do you remember about me
Grok: what do you know about burnout
```

### Limits
- Max **50 facts** per user
- Max **300 characters** per fact
- Facts must be at least **5 characters** (shorter ones pass through to the AI as conversation)
- Duplicate facts are detected and rejected
- Conversational "remember when..." / "remember how..." phrases are NOT treated as commands

> **Note:** `$grokreset` clears conversation history but does NOT touch permanent facts. Use "forget everything" to clear facts.

---

## 💬 Review Mode

Ask the bot to summarize or give its opinion on what's been discussed in the channel. Channel messages are **persisted to the database** so review mode survives bot restarts.

**Trigger phrases:**
- `thoughts`, `opinion`, `what do you think`
- `summarize`, `give me your take`, `opine`
- `what's being discussed`, `what's happening`, `what's going on`
- `catch me up`, `fill me in`, `what did I miss`
- `recap`, `tldr`, `tl;dr`, `what happened`
- `^^` (shorthand)

**Examples:**
```
Grok: what do you think?
Grok: catch me up, what did I miss?
Grok: tldr
```

> **Cooldown:** Review mode has a 30-second cooldown per user (with a notice if triggered during cooldown) to prevent one user's requests from blocking others. If the recent channel backlog is empty (such as immediately after a bot restart), the bot automatically falls back to answering the question directly with its own opinion instead of staying silent.

---

## 🤗 Emote Interactions

The bot reacts to `/me` actions and emote-style messages directed at it. Responses are **generated dynamically by the AI** and vary each time.

| Action | Example Trigger |
|--------|-----------------|
| pet / pat | `/me pets Grok` |
| hug / cuddle / snuggle | `/me hugs Grok` |
| poke / boop | `/me pokes Grok` |
| kiss | `/me kisses Grok` |
| slap / smack | `/me slaps Grok` |
| highfive | `/me highfives Grok` |
| wave / wink / dance / twirl | `/me waves at Grok` |

**Trigger formats:**
```
/me pets Grok
* User hugs Grok
```

---

## 🌿🍺 Cross-Plugin Intoxication System (Stoned, Drunk, & Crossfaded)

When users interact with the weed plugin (`weed.py`) or the bartender plugin (`beer.py`), serving hits or drinks to the bot triggers dynamic **intoxication states** scoped to that specific channel:
- **`$pass BotNick`** / **`$weed / $bong / $joint / $blunt BotNick`**: Bot enters the `stoned` state for **60 minutes** (stacking +30 minutes up to 3 hours).
- **`$shrooms / $acid / $lsd / $peyote BotNick`**: Bot enters the `tripping` state for 60–180 minutes.
- **`$beer / $magners / $wine / $cava / $mead / $sake BotNick`**: Light alcohol (+1 intensity level, +45m duration).
- **`$shot / $whiskey / $scotch / $vodka / $rum / $tequila / $drink BotNick`**: Hard liquor / cocktails (+2 intensity levels, +60m duration).

### Dynamic Drunkenness Levels (1–5)
1. **Level 1 (Tipsy)**: Upbeat, chatty, relaxed bar energy, slightly informal.
2. **Level 2 (Drunk)**: Affectionate ("you guys are my best friends"), laughing, mild slurring and trailing vowels.
3. **Level 3 (Hammered)**: Noticeable slurring (`sh` for `s`), hiccups (`*hic*`), rambles, dropped letters.
4. **Level 4 (Plastered)**: Heavy slurs, repeated hiccups, stumbling over words, confused/dramatic bar banter.
5. **Level 5 (Blackout / Blotto)**: Barely holding it together, snoozing on the bar counter (`*snores*... wait what?`), blotto typos.

### 🌀 CROSSFADED Mode
When the bot is passed weed AND bought drinks in the same channel, it becomes **crossfaded**! It combines spacey cosmic stoner thoughts with loud drunk affection, hiccups, munchies, and hilarious slurred speech.

### Sobering Up
- **Coffee & Decaf** (`$coffee BotNick`, `$decaf BotNick`): Lowers drunkenness by 2 levels (sobers completely if level $\le$ 2) and completely clears weed high!
- **Food** (`$pizza BotNick`, `$appetizer BotNick`): Soaks up the alcohol (lowers drunkenness by 1–2 levels) and satisfies munchies.
- **Water & Tea** (`$water BotNick`, `$tea BotNick`): Hydrates and lowers drunkenness by 1 level.
- **`$sober`**: Manual reset to sober up immediately.

### Channel Isolation & State Awareness
- The bot remembers *who* gave it drinks or weed and *what* it consumed, proudly giving credit when asked how it feels.
- Intoxication states are completely isolated to the channel where they were served. In other channels and private messages, the bot remains 100% professional and sober.
- Unprompted chime-ins and review/catch-up queries in the channel automatically reflect these states.

---

## 🧑 Humanization Features

The bot includes several features designed to make it feel less like an AI and more like a regular channel participant.

### Typing Delay

Responses are delayed by a random **1.5–4 seconds** before being sent, simulating the time a real person would take to read and type a reply. This prevents the instant-response pattern that's a dead giveaway for bots.

**Tunables** (in code):
| Setting | Default | Description |
|---------|---------|-------------|
| `TYPING_DELAY_MIN` | 1.5 | Minimum delay in seconds |
| `TYPING_DELAY_MAX` | 4.0 | Maximum delay in seconds |

### Unprompted Chime-In

The bot occasionally jumps into conversation **without being mentioned**, just like a real channel regular would. It reads the recent chat context and drops a short, natural reaction — a quip, agreement, one-liner, or just "lol".

**How it works:**
- Every channel message has a **5% chance** of triggering a chime-in
- Messages containing laughter/excitement keywords (lol, lmao, omg, wtf, etc.) get a **3x boost** (15% chance)
- **200-second cooldown** per channel between chime-ins
- Requires at least **5 messages** in the channel log before it will chime in
- Chime-in responses are typically short (under 100 chars)
- **Greeting filter** — short greetings directed at other users (e.g. "hey owo", "yo burnout") are automatically excluded from chime-in

**Tunables** (in code):
| Setting | Default | Description |
|---------|---------|-------------|
| `CHIMEIN_ENABLED` | `True` | Master switch |
| `CHIMEIN_CHANCE_PCT` | 5 | Base % chance per message |
| `CHIMEIN_COOLDOWN` | 200 | Seconds between chime-ins per channel |
| `CHIMEIN_MIN_ACTIVITY` | 5 | Min messages in log before chiming in |

**Channel Control:**
Channel operators can toggle this feature per-channel using:
- `$talkback off` — Disable unprompted chime-ins
- `$talkback on` — Enable unprompted chime-ins

### Natural Language Style

The system prompt instructs the bot to:
- Use lowercase naturally, like IRC regulars do
- Drop in casual filler: "lol", "ngl", "tbh", "lmao", "fr", "honestly"
- Use sentence fragments instead of always giving complete answers
- Occasionally start with filler words: "oh", "wait", "hmm", "yo", "dude"
- Give one-word reactions ("same", "fr", "lmao") when that's all the moment needs
- Be blunt, funny, or deadpan depending on the conversational vibe

---

## ⚙️ Architecture & Internals (v6.0)

This section documents the internal design for developers and maintainers.

### Resource Lifecycle

| Phase | What happens |
|-------|--------------|
| `setup(bot)` | Reads API key from raw configparser (bypasses ibot's `***` mask), creates a `requests.Session` with connection pooling (10/20 pool), initializes all `bot.memory` structures, wraps `bot.say`, starts 3 worker threads |
| `shutdown(bot)` | Sets `API_WORKER_SHUTDOWN`, sends poison pills to workers, drains the queue, closes the `requests.Session`, restores `bot.say` to its original function |

On **plugin reload** or **$rehash**, `setup()` closes any previous session before creating a new one, and guards against `bot.say` wrapper stacking via `hasattr(bot, '_grok_original_say')`. The channel scrollback is loaded back from `grok_channel_log` in `grok.sqlite3` (last 300 lines per channel). `$grokreset` still clears a user's or channel's conversation history and does not wipe that scrollback.

### Memory Management

All ephemeral per-message and per-user data uses `_BoundedTTLCache` — a thread-safe dict with automatic TTL expiration and capacity limits:

| Cache | Maxsize | TTL | Purpose |
|-------|---------|-----|----------|
| `grok_dedup_cache` | 2,000 | 2s | Prevents duplicate PRIVMSG handler invocations |
| `grok_user_last_cache` | 5,000 | 5min | Per-user safety rate limiter |

Long-lived structures like `grok_history`, `grok_locks`, and `grok_channel_log` are bounded by the number of active channels/users (not per-message), so they don't grow without bound.

### Threading Model

```
┌─────────────────┐
│ Sopel handler    │──► API_TASK_QUEUE (maxsize=50)
│ threads (N)      │         │
└─────────────────┘         ▼
                     ┌──────────────┐
                     │ Worker Pool  │ (3 daemon threads)
                     │ _api_worker()│
                     └──────────────┘
                            │
                     ┌──────────────┐
                     │ Background   │ (learning, scheck)
                     │ Semaphore(2) │ ← limits concurrent bg tasks
                     └──────────────┘
```

- **Worker tasks** are `dict` objects with named keys (not positional tuples) for type safety
- **Background tasks** (fact learning, scheck analysis) are gated by `_BG_TASK_SEMAPHORE(2)` to prevent thread exhaustion
- All SQLite access goes through `_DBContext`, which opens a fresh connection per operation in WAL mode

### Security

- API key is read from the raw configparser and set on the session's `Authorization` header once. **Never** logged (only key presence and length are logged).
- All utility functions (`_extract_facts_from_conversation`, `_scheck_worker`, `test_api`) use `bot.memory['grok_session']` instead of reading `bot.config.grok.api_key` (which returns `***` through the ibot shim).
- The debug response file write has been removed — no API responses are written to disk.

### Database

- SQLite3 in WAL mode (`journal_mode=WAL`) for concurrent read support
- `_DBContext` context manager ensures connections are committed/rolled back and closed
- Nested connections are avoided — `_db_approve_suggestion` defers its profile write until after the suggestion connection closes

### Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `api_key` | string | — | xAI API key when Grok chat or search is enabled |
| `chat_backend` | choice | `grok` | Normal chat backend: `grok`, `ollama`, or `openai` |
| `search_backend` | choice | `grok` | Web-search backend: `grok` or `none` |
| `model` | string | `grok-4.3` | Grok model |
| `ollama_url` | string | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | string | `llama3.2` | Ollama model |
| `openai_api_key` | string | — | Key for OpenAI-compatible chat APIs |
| `openai_base_url` | string | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `openai_model` | string | `gpt-4o` | OpenAI-compatible model |
| `system_prompt` | string | (see code) | Bot personality |
| `blocked_channels` | list | — | Channels where AI won't respond |
| `banned_nicks` | list | — | Nicks completely blocked |
| `ignored_nicks` | list | — | Nicks silently ignored |
| `intent_check` | choice | `heuristic` | Mention detection mode |
| `nl_actions` | string | `on` | Natural-language actions (`on` / `off`) |
| `nl_actions_allow` | string | — | Comma-separated extra public commands that may be auto-run |
| `nl_actions_deny` | string | — | Comma-separated commands that are never auto-run |

**Supported models:**

| Model | Context | Notes |
|-------|---------|-------|
| `grok-4.5` | 500K | Newest flagship; high reasoning by default |
| `grok-4.3` | 1M | Value flagship; configurable reasoning (default) |
| `grok-4.20` | 2M | Prior flagship with reasoning |
| `grok-4.20-non-reasoning` | 2M | Non-thinking variant of grok-4.20 |
| `grok-4.20-multi-agent` | 2M | Multi-agent optimised variant |
| `grok-build-0.1` | 256K | Code / scaffolding focused |

| Env Variable | Description |
|--------------|-------------|
| `AI_GROK_DIR` | Override data directory (default: `grok_data/` next to script) |

| File | Description |
|------|-------------|
| `grok_channel_prompts.json` | Per-channel system prompt overrides |
| `grok_data/grok.sqlite3` | SQLite database (auto-created) |


## Examples

**Chatting with the bot:**
```
<User> Glitchy: what's the weather like on Mars?
<Glitchy> Mars is currently experiencing a mild dust storm in the Hellas Basin...
```

**Emote interaction:**
```
* User pets Glitchy
* Glitchy purrs and nuzzles User
```

**Resetting your history:**
```
<User> $grokreset
<Glitchy> User: Your conversation history has been reset.
```

**Resetting channel history (ops only):**
```
<@Admin> $grokreset channel
<Glitchy> Admin: Channel conversation history has been reset.
```

**Testing emote plugin:**
```
<User> $testemote
<Glitchy> Emote plugin is loaded and working!
```

**Admin PM — join a channel:**
```
/msg Glitchy $join #newchannel
```

**Admin PM — ignore a user:**
```
/msg Glitchy $ignore SpamBot
```
