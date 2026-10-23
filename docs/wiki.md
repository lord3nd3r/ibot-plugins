# 🔍 wiki — Wiki Search

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

An ibot plugin that provides a `wiki` command to search Grokepedia first, then Wikipedia if not found.

### Features
- Searches **Grokepedia** (`grokepedia.org`) by default.
- Falls back to **Wikipedia** if no results are found on Grokepedia.
- Returns a summary and a link to the article.

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `$wiki <term>` | Search for a term on Grokepedia/Wikipedia | `$wiki Turing test` |

**Limits:** one `$wiki` per user every 10 seconds and one per channel every 4 seconds (extra requests are ignored silently; admins are exempt). Search terms are cut to 200 characters, and replies are one line of at most 400 characters with control characters removed.

---

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
