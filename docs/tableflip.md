# (╯°□°）╯︵ ┻━┻ Table Flip (tableflip)

Play a 4-step table flip animation with timed delays between each frame.

---

## Setup

**No config needed.** Just drop the script in place and it works.

**1. Place the script:**
```
~/.sopel/scripts/tableflip.py
```

**No data storage** — cooldowns are tracked in memory only.

---

## Commands

| Command | Description |
|---------|-------------|
| `$flip` | Play a 4-step table flip animation |

---

## How It Works

1. The bot sends **4 messages** with a **2 second delay** between each one.
2. Only one flip animation runs per channel at a time.
3. Cooldowns: each user has a **60-second cooldown per channel**, and each channel has a **30-second cooldown** shared by everyone. If triggered too soon, the bot sends a private NOTICE telling you how long to wait — at most once per minute; repeat attempts are ignored silently.

### The Animation Sequence

```
Frame 1:  ╭∩╮( º.º )╭∩╮
  (2 second pause)
Frame 2:  ┬─┬ノ( º _ ºノ)
  (2 second pause)
Frame 3:  o(*≧▽≦)ツ┏━┓
  (2 second pause)
Frame 4:  (╯°□°）╯︵ ┻━┻
```

---

## Examples

**Flip a table:**
```
<User> $flip
<Glitchy> ╭∩╮( º.º )╭∩╮
<Glitchy> ┬─┬ノ( º _ ºノ)
<Glitchy> o(*≧▽≦)ツ┏━┓
<Glitchy> (╯°□°）╯︵ ┻━┻
```

**On cooldown:**
```
<User> $flip
-Glitchy- (｡•́︿•̀｡) Whoa! Please wait 45s before flipping again! ⏳✨
```
> Cooldown notices are sent as a private NOTICE, not to the channel.
