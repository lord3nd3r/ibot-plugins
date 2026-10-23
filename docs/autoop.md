# 🤖 Auto Op / Auto-Voice Plugin (`autoop.py`)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

A simple but powerful plugin to automatically grant operator (`+o`), half-operator (`+h`), or voice (`+v`) to specific users as soon as they join the channel.

## 🌟 Features
- **Persistent Data:** Users added to the auto-mode lists are saved in the bot's internal database (`bot.db`) and survive bot restarts.
- **Admin-Only Security:** All modification commands require the user to be a bot admin or owner.
- **Immediate Application:** Modes are applied automatically via IRC's `JOIN` event.

## 🛠️ Commands

*Note: All commands must be run inside the channel where you want the modes applied.*

### Operator (`+o`)
| Command | Description |
|---------|-------------|
| `$aop <nick>` | Adds the specified user to the auto-op list. |
| `$dop <nick>` | Removes the user from the auto-op list. |

### Half-Operator (`+h`)
| Command | Description |
|---------|-------------|
| `$ahop <nick>` | Adds the specified user to the auto-halfop list. |
| `$dhop <nick>` | Removes the user from the auto-halfop list. |

### Voice (`+v`)
| Command | Description |
|---------|-------------|
| `$avoice <nick>` | Adds the specified user to the auto-voice list. |
| `$dvoice <nick>` | Removes the user from the auto-voice list. |

### Utility
| Command | Description |
|---------|-------------|
| `$alist` | Lists all users configured for auto-modes in the current channel. |

## 💡 Examples

Adding a trusted user to the auto-op list:
```
<Admin> $aop TrustedFriend
<Bot> ✅ Added TrustedFriend to the auto-op (+o) list for #mychannel.
```

Listing all active auto-modes:
```
<Admin> $alist
<Bot> Auto-modes for #mychannel | +o: TrustedFriend, OtherMod | +v: ChattyUser
```

## ⚙️ Requirements
- The user running the commands must be a configured bot admin (or owner) in the `[core]` section of your ibot `.cfg` file.
- For the bot to successfully apply the modes when users join, the bot itself must have operator privileges (`+o` or higher) in the channel.

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
