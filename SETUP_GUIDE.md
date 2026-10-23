# Trivia Bot - Quick Setup Guide

## ✅ Installation Complete!

All files are in place in your Sopel scripts directory:
```
~/.sopel/scripts/
  ├── trivia.py           (Sopel bot plugin)
  ├── trivia_game.py      (Game engine)
  ├── questions.json      (187 questions)
  ├── _cli_trivia.py       (CLI testing tool)
  ├── _demo_trivia.py      (Demo script)
  └── tests/
      └── test_trivia_game.py
```

## 🚀 Starting the Bot

### 1. Restart or Reload Sopel
```bash
# If Sopel is running, reload the module:
/msg YourBotName reload trivia

# Or restart Sopel completely:
sopel restart
```

### 2. Test in IRC Channel
```irc
<YourNick> $trivia 5
<Bot> Starting trivia! 5 questions loaded. Answer in the channel!
<Bot> 1. Geography: What is the capital of France?
<Bot> Hint: p****
<YourNick> paris
<Bot> Winner: YourNick; Answer: Paris; Time: 3.456s; Streak: 1; Points: 3; Total: 3
```

## 📋 Available Commands

| Command | Description | Example |
|---------|-------------|---------|
| `$trivia` | Start with 100 questions (default) | `$trivia` |
| `$trivia 10` | Start with 10 questions | `$trivia 10` |
| `$trivia 500` | Max 500 questions | `$trivia 500` |
| `$strivia` | Stop current game | `$strivia` |
| `$tstats` | Show your own stats | `$tstats` |
| `$tstats Nick` | Show another player's stats | `$tstats CrystalMath` |
| `$ttop` | Show top 10 in current channel | `$ttop` |
| `$ttopserver` | Show top 10 across entire server | `$ttopserver` |

## 🎮 Game Features

- **Multiple channels**: Each channel can have its own game running
- **Progressive hints**: 3 hints revealed every 10 seconds
- **Smart scoring**: Fast answers get more points (3/2/1)
- **Streak tracking**: Consecutive correct answers build streaks
- **Auto-stop**: Game warns at 3 unanswered, stops at 5
- **Persistent statistics**: All stats saved to SQLite database

## 📊 Database & Statistics

All trivia statistics are automatically saved to `trivia_stats.db` and persist across bot restarts!

**What's Tracked:**
- **Per-player**: Total points, wins, answers, longest streak, fastest time
- **Per-channel**: Individual channel leaderboards
- **Per-server**: Server-wide rankings across all channels
- **Per-game**: Complete game history with all answers

**View Stats:**
```irc
<You> $tstats
<Bot> YourNick: 150 points | 50 wins | 5 streak | Fastest: 2.34s

<You> $ttop
<Bot> 🏆 Top Players in #channel:
<Bot> 1. Player1: 500 pts | 180 wins | 8 streak | ⚡1.23s
<Bot> 2. Player2: 450 pts | 150 wins | 6 streak | ⚡2.10s
```

**Database Location:** `~/.sopel/scripts/trivia_stats.db`

## 🧪 Testing Before IRC

Test the game engine locally:
```bash
# Quick test
python3 tests/test_trivia_game.py

# Interactive CLI demo
python3 _cli_trivia.py -i --num 5 --shuffle

# See statistics
python3 _demo_trivia.py
```

## 🔧 Customization

### Change Hint Timing
Edit `trivia.py`, line ~85:
```python
time.sleep(10)  # Change to 5 for faster hints
```

### Adjust Point System
Edit `trivia.py`, lines ~118-122:
```python
if elapsed < 5:
    points = 3  # Fast answer
elif elapsed < 10:
    points = 2  # Medium
```

### Add More Questions
Edit `questions.json` following this format:
```json
{
  "category": "Your Category",
  "question": "Your question?",
  "answer": "your answer"
}
```

## 📊 Question Database

- **187 questions** across 18 categories
- Categories: Geography, Science, History, Movies, Sports, Music, Food, Animals, Literature, Technology, Math, Art, Culture, Languages, Tools, Word Play, TV Film and Stage, Sport and Leisure

## 🐛 Troubleshooting

**Bot doesn't respond to $trivia:**
- Check that trivia.py is in the scripts directory
- Verify command prefix is `$` (check bot config)
- Try: `/msg BotName reload trivia`

**"questions.json not found" error:**
- Ensure questions.json is in same directory as trivia.py
- Check file permissions: `ls -la questions.json`

**Game doesn't stop after inactivity:**
- This is normal - timing may vary based on server load
- Manually stop with `$strivia`

## 📝 Contributing Questions

Want to add questions? Keep this format:
```json
{
  "category": "Category Name",
  "question": "Clear question text?",
  "answer": "exact answer text"
}
```

Tips:
- Use lowercase for answers (bot auto-converts)
- Keep answers concise
- Test with progressive hint generation
- Group similar topics in same category

---

**Ready to play!** Type `$trivia` in any channel where the bot is present.
