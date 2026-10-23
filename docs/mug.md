# 💰 Coins & Mugging (mug)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

IRC economy game with coins, mugging, bounties, a shop, and gambling.

---

## Setup

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/mug.py
```

**2. Add to your ibot `.cfg` file (optional):**
```ini
[mug_game]
# Enable or disable the mug game (default: true)
enabled = true
```

**Data storage:** Game data (balances, bounties, high scores, etc.) is stored in the bot's built-in `bot.db` — no extra database setup needed.

> **Auto-Voice:** Users with 500+ coins automatically get `+v` in channels where the bot has ops. This is built-in and requires no extra config.

---

## Core Commands

| Command | Description |
|---------|-------------|
| `$coins` | Collect your coins |
| `$balance [nick]` / `$bal` | Check a balance |
| `$give <nick> <amount>` | Give coins to someone |

### Examples

**Collect coins:**
```
<User> $coins
<Glitchy> 💰 User collects 150 coins! Balance: 1,350
```

**Check your balance:**
```
<User> $balance
<Glitchy> 💰 User has 1,350 coins.
```

**Check someone else's balance:**
```
<User> $bal Friend
<Glitchy> 💰 Friend has 2,800 coins.
```

**Give coins:**
```
<User> $give Friend 500
<Glitchy> 💰 User gave 500 coins to Friend!
```

---

## Crime & Combat

| Command | Description |
|---------|-------------|
| `$mug <nick>` / `$rob` | Rob another user |
| `$bounty <nick> <amount>` | Place a bounty |
| `$bounties` | List active bounties |
| `$jail` | Check jail status |

### Examples

**Mug someone:**
```
<User> $mug Victim
<Glitchy> 🔪 User mugged Victim and stole 320 coins!
```

**Failed mugging:**
```
<User> $mug Victim
<Glitchy> 🚔 User tried to mug Victim but got caught! Jailed for 5 minutes.
```

**Place a bounty:**
```
<User> $bounty Enemy 1000
<Glitchy> 🎯 User placed a 1,000 coin bounty on Enemy!
```

**Check bounties:**
```
<User> $bounties
<Glitchy> 🎯 Active Bounties: Enemy (1,000) | Rival (500)
```

**Check jail:**
```
<User> $jail
<Glitchy> 🔒 User is in jail for 3 more minutes.
```

---

## Gambling

### Slots

| Command | Description |
|---------|-------------|
| `$bet <amount>` | Gamble coins (slot machine) |
| `$penny` | 🎰 Penny slot — 1 coin per pull, win up to 5,000! |
| `$dollar` | 💵 Dollar slot — 100 coins per pull, win up to 50,000! |

**Examples:**
```
<User> $bet 500
<Glitchy> 🎰 [🍒 🍒 🍒] User wins 2,500 coins! 💰

<User> $penny
<Glitchy> 🎰 [7️⃣ 🍋 🍒] No match. -1 coin.

<User> $dollar
<Glitchy> 💵 [💎 💎 💎] JACKPOT! User wins 50,000 coins! 🤑
```

### Dice Casino ($roll)

| Command | Description |
|---------|-------------|
| `$roll <amount> [type]` | 🎲 Dice casino |

**Dice Types:**

| Type | How to Win | Payout |
|------|-----------|--------|
| `high` (default) | Roll 2d6, total 7+ wins | 2x |
| `lucky7` | Roll exactly 7 | 4x |
| `snake` | Snake eyes (1+1) | 30x |
| `field` | Roll 2,3,4,9,10,11,12 | 2x (3x on 2 or 12) |
| `hardway` | Doubles (except snake eyes) | 8x |
| `yolo` | Roll 2 or 12 | 15x |

**Examples:**
```
<User> $roll 200
<Glitchy> 🎲 User rolls [4][5] = 9. That's 7+! You win 400 coins! 💰

<User> $roll 100 lucky7
<Glitchy> 🎲 User rolls [3][4] = 7. Lucky 7! You win 400 coins! 🍀

<User> $roll 50 snake
<Glitchy> 🎲 User rolls [1][1] = 2. SNAKE EYES! You win 1,500 coins! 🐍

<User> $roll 100 yolo
<Glitchy> 🎲 User rolls [6][6] = 12. YOLO! You win 1,500 coins! 🤪
```

### Roulette ($roulette)

| Command | Description |
|---------|-------------|
| `$roulette <amount> <bet>` | 🎡 Roulette |

**Bet Types:**

| Bet | Description | Payout |
|-----|-----------|--------|
| `red` / `black` | Color bet | 2x |
| `odd` / `even` | Parity bet | 2x |
| `high` / `low` | 19-36 / 1-18 | 2x |
| `1st` / `2nd` / `3rd` | Dozens (1-12, 13-24, 25-36) | 3x |
| `0`–`36` | Straight number | 36x |

**Examples:**
```
<User> $roulette 500 red
<Glitchy> 🎡 The wheel spins... lands on 23 Red! You win 1,000 coins! 🔴

<User> $roulette 100 17
<Glitchy> 🎡 The wheel spins... lands on 17! Straight hit! You win 3,600 coins! 🎯

<User> $roulette 200 1st
<Glitchy> 🎡 The wheel spins... lands on 8. 1st dozen! You win 600 coins!
```

### Blackjack ($bj / $blackjack)

| Command | Description |
|---------|-------------|
| `$bj <amount>` (alias `$blackjack`) | Deal a hand vs the dealer |
| `$hit` | Draw another card |
| `$stand` | Keep your hand, dealer plays |
| `$dd` | Double down — double bet, one card, auto-stand |

> Natural blackjack pays **2.5x**. Regular win pays **2x**.

**Examples:**
```
<User> $bj 500
<Glitchy> 🃏 User's hand: K♠ 7♥ (17) | Dealer shows: 5♦
<User> $stand
<Glitchy> 🃏 Dealer reveals: 5♦ 10♣ (15). Dealer hits: 8♠ (23). Bust! User wins 1,000 coins!

<User> $bj 300
<Glitchy> 🃏 User's hand: A♠ K♦ (21) BLACKJACK! 🎉 User wins 750 coins!

<User> $bj 200
<Glitchy> 🃏 User's hand: 8♣ 3♦ (11) | Dealer shows: 6♠
<User> $dd
<Glitchy> 🃏 User doubles down! Draws 10♥ (21). Dealer: 6♠ 9♣ (15), hits 7♦ (22). Bust! User wins 800 coins!
```

### Texas Hold'em ($holdem)

| Command | Description |
|---------|-------------|
| `$holdem <amount>` | 🤠 Heads-up vs dealer |

**Payouts:**

| Hand | Payout |
|------|--------|
| Royal Flush | 50x |
| Straight Flush | 25x |
| Four of a Kind | 12x |
| Full House | 6x |
| Flush | 4x |
| Straight | 3x |
| Three/Two/One Pair, High Card | 2x |

**Example:**
```
<User> $holdem 500
<Glitchy> 🤠 User's hole cards: A♠ K♠
<Glitchy> 🤠 Flop: 10♠ J♠ Q♠ | Turn: 3♥ | River: 2♦
<Glitchy> 🤠 User: Royal Flush! Dealer: Two Pair. User wins 25,000 coins! 👑
```

---

## Anti-Spam

> More than **15 commands in 60 seconds** triggers a **30-minute casino lockout**. Admins are exempt and can clear lockouts with `$uncooldown`.

---

## Shop & Items

| Command | Description |
|---------|-------------|
| `$shop` | View the item shop |
| `$buy <item>` | Buy an item (PM) |
| `$inv` / `$inventory` | View your inventory (PM) |
| `$use <item>` | Use a consumable item (PM) |

### Examples

**View the shop:**
```
<User> $shop
<Glitchy> 🛒 Shop: Shield (500) | Lockpick (300) | Lucky Charm (1,000) | ...
```

**Buy an item:**
```
/msg Glitchy $buy shield
<Glitchy> 🛒 You bought a Shield for 500 coins!
```

**Check inventory:**
```
/msg Glitchy $inv
<Glitchy> 🎒 Your inventory: Shield (x1), Lockpick (x2)
```

**Use an item:**
```
/msg Glitchy $use lockpick
<Glitchy> 🔓 You used a Lockpick! Your next mug attempt has a higher success rate.
```

---

## Leaderboards

| Command | Description |
|---------|-------------|
| `$top5` | Top 5 richest users |
| `$top10` | Top 10 richest users |
| `$highscore` | All-time highest balance record |
| `$mughelp` | Full help guide (PM) |

### Examples

**Top 5:**
```
<User> $top5
<Glitchy> 🏆 Top 5: 1. Whale (52,300) 2. Shark (38,100) 3. Dolphin (25,600) 4. Fish (12,400) 5. Minnow (8,200)
```

**All-time high score:**
```
<User> $highscore
<Glitchy> 🏆 All-time highest balance: Whale with 85,000 coins!
```

---

## Admin PM Commands

| Command | Description |
|---------|-------------|
| `$mugadd <nick> <amount>` | Add coins to a user |
| `$mugset <nick> <amount>` | Set a user's balance |
| `$mugtake <nick> <amount>` | Remove coins from a user |
| `$mugreset` | Reset all game data |
| `$mugcleardb confirm` | Delete all DB records |
| `$mugmerge <nick>` | Merge duplicate records |
| `$mugdup <nick>` | List duplicate records |
| `$mugclearbounty <nick>` | Clear all bounties on a nick |
| `$mugtoggle [on\|off]` | Enable/disable per-channel |
| `$godmode <nick>` | Toggle luck override (owner only) |
| `$uncooldown <nick>` | Clear a user's 30-min flood lockout |
| `$mugstats` | Show game statistics |

### Examples

**Add coins:**
```
/msg Glitchy $mugadd User 5000
```

**Set balance:**
```
/msg Glitchy $mugset User 10000
```

**Clear a bounty:**
```
/msg Glitchy $mugclearbounty User
```

**Toggle game in a channel:**
```
/msg Glitchy $mugtoggle off
```

---

## Notes

- **Auto-Voice:** Users with ≥500 coins automatically receive `+v` in configured channels. Drops below 500 = devoiced. Ops/hops/owners are exempt.
- **High Score Topic:** When a player beats the all-time high score, the bot automatically updates the channel topic in configured channels (default: `#mug`). The high score appears at the end of the topic as `┃ 🏆 High Score: nick (X coins) 👑`. If the marker is missing (e.g. after a manual topic change), it self-heals on the next data save.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
