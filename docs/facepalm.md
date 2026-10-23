# 🤦 Facepalm & Shrug (facepalm)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Auto-triggers: when someone does `/me facepalms`, the bot replies with a random facepalm reaction. When someone does `/me shrugs`, the bot replies with ¯\\\_(ツ)\_/¯. Also includes the `$shrug` command.

---

## Setup

**No config needed.** Just drop the script in place and it works.

**1. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/facepalm.py
```

**No data storage** — cooldowns are tracked in memory only.

---

## Triggers

| Trigger | Description |
|---------|-------------|
| `/me facepalm` | Triggers a random facepalm reaction |
| `/me facepalms` | Triggers a random facepalm reaction |
| `/me facepalmed` | Triggers a random facepalm reaction |
| `/me shrug` | Bot replies with ¯\\\_(ツ)\_/¯ |
| `/me shrugs` | Bot replies with ¯\\\_(ツ)\_/¯ |
| `/me shrugged` | Bot replies with ¯\\\_(ツ)\_/¯ |

> Only responds to `/me` ACTION messages — typing "facepalm" or "shrugs" in regular text does nothing.
>
> **Channel cooldown:** 15 seconds (shared across all triggers in the channel). If on cooldown, the bot simply stays silent.
>
> **Channel only** — does not trigger in private messages.

---

## Facepalm Response Pool

The bot picks a random response from 15 templates. Some sample responses:

```
User facepalms so hard the desk breaks (－‸ლ)
User buries face into hands (ಠ_ಠ) 🤦
User facepalms with both hands 🤦‍♂️🤦‍♀️
User facepalms with the force of a thousand suns ☀️ (－‸ლ) ☀️
User collapses dramatically 🤦 ...and stays there
```

## Shrug Response

The `/me shrugs` trigger always replies with a simple `¯\_(ツ)_/¯` — no random pool.

---

## Examples

**Facepalm in action:**
```
* User facepalms
<Glitchy> User facepalms so hard the desk breaks (－‸ლ)
```

**Different response each time:**
```
* User facepalmed
<Glitchy> User buries face into hands (ಠ_ಠ) 🤦
```

**Shrug in action:**
```
* User shrugs
<Glitchy> ¯\_(ツ)_/¯
```

**On cooldown (no response):**
```
* User facepalms
(15 seconds haven't passed since the last reaction — bot stays silent)
```

**Regular text — no trigger:**
```
<User> I just facepalmed so hard
(no response — only /me actions trigger the bot)
```

---

## Shrug Command

| Command | Description |
|---------|-------------|
| `$shrug` | Output ¯\\\_(ツ)\_/¯ |
| `$shrug <nick>` | Direct the shrug at someone |

**Examples:**
```
<User> $shrug
<Glitchy> ¯\_(ツ)_/¯

<User> $shrug burnout
<Glitchy> burnout: ¯\_(ツ)_/¯
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
