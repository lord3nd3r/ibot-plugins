# 💬 quote — Channel Quote Database

A comprehensive quote management system for Sopel. Save and search funny or memorable things said in the channel.

### Features
- Per-channel quote databases.
- Subcommands for adding, searching, and managing quotes.
- Stats for top quoted users.
- ID-based retrieval and random quotes.

### Commands

The bot command prefix is **`$`**.

| Command | Description | Example |
|---------|-------------|---------|
| `$quote` (alias `$q`) | Get a random quote from the current channel. Every `$quote …` form below also works as `$q …` | `$q` |
| `$quote <id>` | Get a specific quote by its ID | `$quote 42` |
| `$quote add <nick> <text>` | Add a new quote | `$quote add Ender Hello world!` |
| `$quote search <term>` | Search quotes for a specific keyword | `$quote search funny` |
| `$quote by <nick>` | Get a random quote attributed to a specific nick | `$quote by Ender` |
| `$quote last` | Show the most recently added quote | `$quote last` |
| `$quote count` | Show the total number of quotes in the channel | `$quote count` |
| `$quote info <id>` | Show metadata (who added it and when) for a quote | `$quote info 42` |
| `$quote top` | Show the top 5 most-quoted users in the channel | `$quote top` |
| `$quote del <id>` | Delete a quote (Admins only) | `$quote del 42` |

---
