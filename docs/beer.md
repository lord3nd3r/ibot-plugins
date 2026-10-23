# 🍺 Bartender (beer)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Virtual bartender with a tip economy integrated directly with the **mug** game's coin system.

---

## Setup

**No config needed.** Just drop the script in place and it works.

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/beer.py
```

**Data Persistence:**
All coin balances are stored in `bot.db` under the `mug_game` plugin. Bartender tip stats are stored in `bot.db` under the `beer` plugin.

---

## Menu & Prices

All prices are in **coins** (🪙). When specifying a target user or the bot (`[nick]`), only the first word is used as the nickname so extra words (e.g. `$beer user again`) are ignored.

| Command | Aliases | Cost | Description |
|---------|---------|------|-------------|
| `$scotch [nick]` | — | 15 🪙 | Premium Scotch whisky 🏴󠁧󠁢󠁳󠁣󠁴󠁿🥃 |
| `$whiskey [nick]` | `$whisky` | 12 🪙 | Fine whiskey 🥃 |
| `$irish [nick]` | `$irishwhiskey` | 10 🪙 | Irish whiskey 🇮🇪🥃 |
| `$beer [nick]` | — | 5 🪙 | Serve a random beer 🍺 |
| `$b33r [nick]` | — | 5 🪙 | Serve a beer in 1337 speak 🍺 |
| `$shot [nick]` | — | 7 🪙 | Serve a random shot 🥃 |
| `$vodka [nick]` | — | 10 🪙 | Premium vodka 🥃 |
| `$rum [nick]` | — | 10 🪙 | Spiced rum 🏴‍☠️🥃 |
| `$tequila [nick]` | — | 10 🪙 | Fine tequila 🇲🇽🥃 |
| `$gin [nick]` | — | 10 🪙 | Botanical gin 🌿🥃 |
| `$brandy [nick]` | `$cognac` | 12 🪙 | Brandy / Cognac 🥃 |
| `$margarita [nick]` | `$marg` | 9 🪙 | Margarita Salt rim! 🧂🍹 |
| `$sake [hot\|cold] [nick]` | — | 9 🪙 | Sake, bartender's choice, hot, or cold 🍶. `warm` counts as hot, `chilled` as cold. Word order does not matter (`$sake Nick hot`) |
| `$liqueur [nick]` | `$cordial` | 8 🪙 | Liqueur 🍯🥃 |
| `$wine [nick]` | — | 8 🪙 | Glass of wine 🍷 |
| `$cava [nick]` | `$prosecco` | 8 🪙 | Sparkling Cava 🍾🥂 |
| `$mead [nick]` | — | 7 🪙 | Horn of mead ⚔️🍯 |
| `$magners [nick]` | — | 6 🪙 | Magners cider 🍎🍺 |
| `$drink [nick]` | — | 10 🪙 | Mixed drink 🍹 |
| `$mocktail [nick]` | `$virgin` | 4 🪙 | Mocktail (non-alcoholic) 🍹 |
| `$coffee [iced\|hot\|irish\|mine] [nick]` | `$caffeine` | 3 🪙 | Coffee ☕. `cold` counts as iced, `warm` as hot. `irish` is a hot whiskey coffee. `mine` is a hot dark roast with a shot of Tullamore Dew. Word order does not matter (`$coffee Nick iced`) |
| `$decaf [nick]` | `$decaffeinated` | 3 🪙 | Decaf coffee ☕😴 |
| `$tea [nick]` | `$cuppa` | 3 🪙 | Tea 🍵 |
| `$water [nick]` | `$hydrate` | Free | Water (Responsible hydration!) 💧 |
| `$pizza [nick]` | — | 15 🪙 | Pizza 🍕 |
| `$appetizer [nick]` | `$snack`, `$food` | 8 🪙 | Appetizer 🍽️ |
| `$surprise [nick]` | `$random` | Varies | Random menu item and price 🎉 |

---

## Economy

* **Starting Tab**: Ordering for the first time opens a tab with **1,000 starting coins**.
* **Daily Bonus**: Users receive a **100 coin credit** once every 24 hours. Credits are applied when you order or check your tab.
* **Shared Balance**: Coins are shared with the **mug** game. It is a single unified economy.

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `$tip <nick> <amount>` | Tip another user for great service (deducts from your coins) | `$tip m0n 50` |
| `$barcash` / `$balance` | Check your current coin balance | `$barcash` |
| `$toptip` | See the top 5 most tipped bartenders | `$toptip` |
| `$barhelp` | Get the full menu and command guide (sent via PM) | `$barhelp` |

### Admin PM Commands

Must be private messaged to the bot. Requires bot admin/owner status.

| Command | Description | Example |
|---------|-------------|---------|
| `$adjbal <nick> <+/-amount>` | Adjust a user's coin balance | `$adjbal m0n +500` |
| `$barreset <nick>` | Reset a user's balance to 1,000 coins and clear their tips | `$barreset m0n` |
| `$barreset all confirm` | Reset ALL users' balances and clear all tips (requires `confirm`) | `$barreset all confirm` |

---

## Examples

**Order a drink for yourself:**
```
<User> $beer
* Glitchy slides a frosty Guinness 🍺 across the bar to User ✨
```
*(You will receive a PM notice: Paid 5 coins - Remaining balance: 995 coins 🪙)*

**Buy someone else a drink:**
```
<User> $shot Friend
* Glitchy lines up a shot of Jägermeister 🦌🥃 for Friend
```

**Tip another user:**
```
<User> $tip Friend 100
<Glitchy> User tips Friend 100 coins! 💰✨
```
*(You will receive a PM notice: New balance: 895 coins 🪙)*

---

## 😵 Bot Intoxication & Sobering

When users buy drinks or food for the bot (e.g. `$beer <botnick>`, `$shot <botnick>`, `$pizza <botnick>`), the bot participates dynamically!

### Getting the Bot Drunk
* **Beers / Ciders / Wines / Meads** (`$beer`, `$magners`, `$wine`, `$cava`, `$mead`, `$sake`):
  * **+1 Intensity Level**, adds **~45 minutes** duration.
* **Shots / Spirits / Cocktails** (`$shot`, `$whiskey`, `$scotch`, `$irish`, `$vodka`, `$rum`, `$tequila`, `$gin`, `$brandy`, `$margarita`, `$liqueur`, `$drink`):
  * **+2 Intensity Levels**, adds **~60 minutes** duration.
* **Intoxication Levels**:
  * **Level 1 (Tipsy)**: Upbeat, warm, chatty, relaxed bar energy.
  * **Level 2 (Drunk)**: Affectionate ("you're my best friend"), laughing, mild slurring/typos.
  * **Level 3 (Hammered)**: Noticeable slurring (`sh` for `s`), hiccups (`*hic*`), rambling tangents.
  * **Level 4 (Plastered)**: Heavy slurs, frequent hiccups, stumbling, dramatic bar banter.
  * **Level 5 (Blackout)**: Slumped on the bar counter, sleeping (`*snores*`), blotto typos.
* **Duration**: Stacks up to a maximum of 3 hours per channel.

### Sobering Up the Bot
* **Coffee & Decaf** (`$coffee <botnick>`, `$coffee hot`, `$coffee iced`, `$decaf <botnick>`):
  * Lowers drunkenness by **2 levels** (sobers up completely if level $\le$ 2).
  * Also completely clears active weed high from `weed.py`!
* **Spiked coffee** (`$coffee irish`, `$coffee mine`):
  * These have whiskey in them, so they count as a spirit (+2), not a sober-up.
* **Food** (`$pizza <botnick>`, `$appetizer <botnick>`):
  * Greasy pizza & snacks soak up the booze! Lowers drunkenness by **1–2 levels**.
  * Satisfies munchies and eases active weed highs.
* **Water & Tea** (`$water <botnick>`, `$tea <botnick>`):
  * Responsible hydration lowers drunkenness by **1 level**.

### Cross-Plugin Synergy (`ai-multi.py` & `weed.py`)
* The bot's AI responses in the channel dynamically reflect its drunkenness level in real-time.
* **Crossfaded Mode**: If the bot is passed weed (`$pass <botnick>` in `weed.py`) AND bought drinks, it becomes **crossfaded** — blending cosmic stoner thoughts with drunk slurs, hiccups, and affection!

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
