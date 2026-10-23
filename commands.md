# 📋 Bot Command Reference

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** To load these plugins, see the [Quick start](README.md#-quick-start-with-ibot).

Click any plugin below to see its full command list with examples.

The examples use the **`$`** prefix (what the `glitchy` bot uses, `prefix = \$` in its ibot `.cfg`). ibot's default prefix is `.`, so use whatever yours is set to.

**Core commands:** bot management commands such as `$rehash`, `$reload <plugin>`, `$load`, `$unload`, `$plugins`, `$bjoin`, `$bpart`, `$say`, `$bstatus` and `$disable`/`$enable` are built into ibot. See [ibot admin commands](https://github.com/lord3nd3r/ibot/blob/main/docs/administration/admin-commands.md).

---

| Script | Description |
|--------|-------------|
| [🧠 AI Chatbot](docs/ai-multi.md) | Talk to the bot via Grok, Ollama, or an OpenAI-compatible API |
| [🍺 Bartender](docs/beer.md) | Virtual bar with drinks, tips, and a unified coin economy |
| [🌿 Weed & Trippy](docs/weed.md) | Themed smoke/trip messages with countdown animations |
| [💰 Coins & Mugging](docs/mug.md) | IRC economy — coins, mugging, bounties, shop, gambling |
| [🛠️ Bot Admin](docs/botadmin.md) | `$restart`, `$breload`, `$bothelp` (other admin commands are in the ibot core) |
| [🔍 IRC Oper Scanner](docs/operscan.md) | PM-only scanner to identify network/server IRC operators |
| [🐄 Moo Counter](docs/moo.md) | Say moo to increment your counter, network leaderboards |
| [⭐ Karma](docs/karma.md) | Inline `++` / `--` karma with per-channel & global tracking |
| [🧩 Trivia](docs/trivia.md) | Multi-player trivia with categories, hints, and streaks |
| [🌤️ Weather](docs/weather.md) | Current weather, forecasts, alerts, and space weather |
| [📈 Stocks](docs/stock.md) | Look up stocks by ticker or company name |
| [🗳️ Voting](docs/voting.md) | Create timed polls with multiple options |
| [📊 Channel Stats](docs/monitor.md) | Track lines, words, actions, kicks, and more per user |
| [🚔 Verbal Morality](docs/curse.md) | Demolition Man-style profanity fines |
| [🤦 Facepalm](docs/facepalm.md) | Auto-react to `/me facepalms` |
| [(╯°□°）╯︵ ┻━┻ Table Flip](docs/tableflip.md) | 4-step table flip animation |
| [🔑 PromoteMe](docs/opme.md) | Promote yourself or a target to channel op |
| [🚪 Join](docs/join.md) | Make the bot join a channel (owner only) |
| [🎬 YouTube Titles](docs/youtube_titles.md) | Auto-detect YouTube links and show video info |
| [📰 RSS/Atom Feeds](docs/feeds.md) | Post new RSS/Atom feed items to subscribed channels (`$feed`) |
| [🔗 URL Titles](docs/url_titles.md) | Automatic webpage title fetching (`$urltitle on/off`) |
| [🎙️ Auto Voice](docs/autovoice.md) | Activity-based auto-voicing for active chatters (per-channel threshold) |
| [🤖 Auto Op](docs/autoop.md) | Auto-mode list for +o, +h, and +v on join |
| [🛡️ Antiflood](docs/antiflood.md) | Join/part cycle flood protection |
| [🛡️ Antispam](docs/antispam.md) | Multi-mode anti-spam and Grok AI kick protection |
| [🛡️ JPQ Flood Protection](docs/jpq.md) | Join/Part/Quit cycle flood protection (enabled by default) |
| [🔗 Markov Chain](docs/markov.md) | Learns from chat and generates random sentences from word trigrams |
| [🔍 Wiki Search](docs/wiki.md) | Search Grokepedia and Wikipedia |
| [💬 Quotes](docs/quote.md) | Save and search funny channel quotes |
| [🔧 Typo Correction](docs/fix.md) | Sed-style typo correction (`s/find/replace/`) |
| [🆘 Rizon Help](docs/rizonhelp.md) | Rizon network help and FAQ |
| [📬 Tell](docs/tell.md) | Leave offline messages for users, delivered when they next speak (`$tell`) |
| [👁️ Seen](docs/seen.md) | Report when a user was last seen in the channel |
| [🌲 Hunting Game](docs/hunt.md) | Wildlife hunting game with guns, permits, ammo, and steady aim mechanics |
| [ᓚᘏᗢ Cat](docs/cat.md) | Print a random unicode cat (`$cat`) |
| [📋 Help Link](docs/commands.md) | `$help` / `$commands` / `$cmds` link to this page |

---

*On Sopel, the core commands above aren't available; use Sopel's own admin commands instead. See [Using with Sopel](README.md#-using-with-sopel).*
