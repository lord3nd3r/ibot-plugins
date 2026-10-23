# 🧩 Trivia (trivia)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Multi-player trivia with categories, progressive hints, streaks, and stats.

---

## Setup

**1. Place all required files in the scripts directory:**
```
~/.sopel/scripts/trivia.py
~/.sopel/scripts/trivia_game.py      # game engine
~/.sopel/scripts/trivia_db.py        # database layer
~/.sopel/scripts/questions.json      # question bank
```

**No config section needed.** The trivia database (`trivia_stats.db`) is created automatically in the scripts directory on first use. Questions are loaded from `questions.json`.

---

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `$trivia [category] [N]` | — | Start a game with N questions (default 100, max 500), optionally filtered by category |
| `$trivia categories` | — | List all available trivia categories |
| `$tcategories` | `$tcats` | Same list, as its own command |
| `$strivia` | — | Stop the current game |
| `$triviastats [nick]` | `$tstats` | View stats for yourself or another player |
| `$triviatop` | `$ttop` | Top 10 in this channel |
| `$triviatopserver` | `$ttopserver` | Top 10 across the server |

---

## Features

- **431 questions** across 18 categories (Geography, Science, History, Movies, Sports, etc.)
- **Anti-repeat rotation**: Questions answered in the channel's last 3 finished games are automatically deprioritized to the back of the deck on game start so consecutive games stay fresh.
- **Progressive hints**:
  - Text answers reveal letters progressively at 10-second intervals.
  - Purely numeric answers (years, counts) receive progressive meta-hints (number indicator $\to$ magnitude range $\to$ decade or upper/lower half) rather than bare asterisks.
  - Single-character answers are explicitly identified as single letters.
- **Streaks & scoring**: Fast answers award bonus points, and consecutive correct answers build player streaks.
- **Inactivity protection**: Warns after 3 unanswered questions and automatically stops after 5.

---

## Examples

**Start a trivia game:**
```
<User> $trivia
<Glitchy> 🧩 Trivia started! 100 questions. Type your answer in chat!
<Glitchy> 🧩 [1/100] Category: Science — What planet is known as the Red Planet?
<User> mars
<Glitchy> ✅ User got it! The answer was Mars. 🔥 Streak: 1
```

**Start a short game:**
```
<User> $trivia 10
<Glitchy> 🧩 Trivia started! 10 questions. Type your answer in chat!
```

**Stop a game:**
```
<User> $strivia
<Glitchy> 🧩 Trivia stopped! Final scores: User (8) | Friend (6) | Player (3)
```

**Check your stats:**
```
<User> $triviastats
<Glitchy> 🧩 User — Correct: 142 | Games: 12 | Best Streak: 8 | Accuracy: 68%
```

**Check someone else's stats:**
```
<User> $tstats Friend
<Glitchy> 🧩 Friend — Correct: 89 | Games: 7 | Best Streak: 5 | Accuracy: 72%
```

**Channel leaderboard:**
```
<User> $triviatop
<Glitchy> 🧩 #channel Top 10: 1. Brainiac (320) 2. User (142) 3. Friend (89) ...
```

**Server leaderboard:**
```
<User> $ttopserver
<Glitchy> 🧩 Server Top 10: 1. Brainiac (580) 2. Scholar (412) 3. User (142) ...
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
