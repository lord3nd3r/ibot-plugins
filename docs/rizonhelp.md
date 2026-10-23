# 🆘 rizonhelp — Rizon Network Help

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

A helper plugin for Rizon IRC network users. Provides quick access to common help topics, FAQ, and staff information.

### Features
- Look up network-specific help topics (VHosts, SASL, ZNC, ChanServ, etc.).
- Fuzzy search for topics.
- Multi-line responses for detailed help.
- Can be enabled/disabled per channel.

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `$rhelp` | List all available help topics | `$rhelp` |
| `$rhelp <topic>` | Show detailed help for a specific topic | `$rhelp vhost` |
| `$rizonhelpon` | Enable Rizon help in the channel (Ops only) | `$rizonhelpon` |
| `$rizonhelpoff` | Disable Rizon help in the channel (Ops only) | `$rizonhelpoff` |

### Common Topics
- `vhost`, `sasl`, `znc`, `register`, `identify`, `ghost`
- `chanserv`, `nickserv`, `memoserv`
- `banned`, `flooded`, `spammers`, `trolls`
- `webchat`, `ip`

---

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
