# Trivia Game Framework

IRC-style trivia game with categories, progressive hints, and inactivity detection.

## Features

- **465 questions** across 18+ categories (Geography, Science, History, Movies, Sports, Tech, etc.)
- **Category filtering & listing** (`$trivia [category] [number]`, `$trivia categories`)
- **Anti-repeat rotation** - answers from recent games are deprioritized so consecutive games draw fresh questions
- **Progressive masked hints** (letters gradually unmasked, numeric range/decade hints, single-letter markers)
- **Persistent SQLite stats & leaderboards** (`trivia_stats.db`) for channels and server-wide
- **Per-channel game state** - multiple channels can play simultaneously
- **Streak tracking** and scoring with point bonuses for fast answers
- **Inactivity detection** - warns after 3 unanswered, stops after 5
- **IRC-bot-style output** matching traditional trivia bot format

## Sopel Bot Commands

### In IRC Channel
```
$trivia [category] [number]  - Start trivia (default 100 questions, max 500)
$trivia categories           - List all available question categories
$strivia                     - Stop current trivia game
$triviastats [nick]          - View stats for yourself or another player ($tstats)
$triviatop                   - Channel top 10 leaderboard ($ttop)
$triviatopserver             - Server-wide top 10 leaderboard ($ttopserver)
```

Just type your answer in the channel - the bot will check all messages automatically!

### Example IRC Session
```
<User> $trivia 5
<Bot> Starting trivia! 5 questions loaded. Answer in the channel!
<Bot> 1. Geography: What is the capital of France?
<Bot> Hint: p****
<Bot> Hint: par**
<User> paris
<Bot> Winner: User; Answer: Paris; Time: 5.234s; Streak: 1; Points: 2; Total: 2
<Bot> 2. Science: Which planet is known as the Red Planet?
...
<User> $strivia
<Bot> Trivia stopped. '.trivia [number]' to start playing again.
<Bot> Total Questions: 2
<Bot> User (2 points)
```

## Standalone CLI Usage

### Interactive Mode (show answers immediately)
```bash
python3 _cli_trivia.py -i --num 20 --shuffle
```

### Demo Mode (timed hints, like IRC bot)
```bash
python3 _cli_trivia.py --num 10 --hint-delay 2 --shuffle
```

### Category Mode
```bash
python3 _cli_trivia.py --category Geography -i --num 15
```

### Options
- `--file, -f` - Questions JSON file (default: questions.json)
- `--num, -n` - Number of questions (default: 10)
- `--shuffle, -s` - Randomize question order
- `--category, -c` - Filter by category
- `--interactive, -i` - Show answers immediately (good for testing)
- `--hint-delay, -d` - Seconds between hints in demo mode (default: 5)
- `--inactivity-limit` - Stop after N unanswered questions (default: 5)

## Installation

The trivia bot is already installed in your Sopel scripts directory. Sopel should auto-load it when the bot starts.

If you need to reload:
```
/msg YourBot reload trivia
```

## Files

- **`trivia.py`** — Sopel IRC bot plugin (main module)
- **`trivia_game.py`** — Core game engine with hint generation
- **`trivia_db.py`** — SQLite database layer for stats, leaderboards, and game history
- **`questions.json`** — 465 categorized questions
- **`_cli_trivia.py`** — Standalone CLI runner for testing
- **`tests/test_trivia_game.py`** — Unit tests
- **`_demo_trivia.py`** — Demo script showing features

## Question Format

```json
{
  "category": "Geography",
  "question": "What is the capital of France?",
  "choices": ["Paris", "Berlin", "Rome", "Madrid"],
  "answer_index": 0
}
```

Or for text answers:
```json
{
  "category": "History",
  "question": "Who was the first US President?",
  "answer": "george washington"
}
```

## Game Behavior

### Hints & Timing
- Question is displayed
- 3 progressive hints shown at 10-second intervals
- Final 10-second wait before timeout (40 seconds total per question)
- Answer any time before "Time's up!" message

### Scoring
- **Fast answer** (< 5 seconds): 3 points
- **Medium answer** (5-10 seconds): 2 points  
- **Slow answer** (10+ seconds): 1 point
- **Streak bonus**: Same player answering consecutively increases streak counter

### Inactivity Protection
- After **3 unanswered questions**: Warning message displayed
- After **5 unanswered questions**: Game automatically stops
- Answering any question resets the inactivity counter

## Next Steps

- Add more questions to `questions.json`
- Implement persistent leaderboards (SQLite database)
- Add difficulty levels or category selection commands
- Implement weekly/monthly championship tracking
- Add custom time limits per category
- Create question submission system for users
