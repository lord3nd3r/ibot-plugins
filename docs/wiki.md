# 🔍 wiki — Wiki Search

A Sopel script that provides a `wiki` command to search Grokepedia first, then Wikipedia if not found.

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
