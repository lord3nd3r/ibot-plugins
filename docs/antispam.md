# 🛡️ Anti-Spam Protection (antispam)

Anti-Spam Kick Protection for Sopel. It detects and kicks users who post rapid-fire messages, unicode art walls, or repetitive copypasta spam. Host bans are **off by default**. When a channel op turns them on, each kick also sets `+b *!*@host`. The plugin integrates with the **autovoice** plugin to revoke voice privileges from kicked spammers.

---

## Setup

**1. Place the script:**
```
~/.sopel/scripts/antispam.py
```

**2. Logs:**
Anti-spam actions and debug info are written to a dedicated log file:
```
~/.sopel/antispam.log
```

**Note:** Antispam is **disabled by default per channel**. Use `$spam on` to enable.

---

## Detection Modes

The plugin uses five layers of spam detection:

1. **Rate-Based Detection**:
   * Kicks if a user sends $\ge$ `threshold` messages (default: 5) within a `window` of seconds (default: 8).
2. **Content-Based Detection**:
   * Instant kick if a message contains a configured trigger phrase (exact match, case-insensitive).
3. **Unicode Art Detection**:
   * Detects blocks of braille or drawing characters (e.g. text art walls).
   * Kicks if a user sends $\ge$ `unicode_threshold` lines (default: 3) of unicode art within a `unicode_window` (default: 30s).
4. **Copypasta Fingerprinting**:
   * Strips formatting, punctuation, and URLs to create a normalized shingle fingerprint (3 words per shingle).
   * When a user is kicked for rate-based or AI spam, their messages are "learned" in the database.
   * Future messages matching $\ge$ 40% of the learned shingles are instantly kicked.
5. **Grok AI Classification**:
   * Uses `grok-3-mini-fast` to classify a user's recent messages (last 5+ messages in a 300s window) as `SPAM` or `SAFE`.
   * Specifically trained to allow normal offensive language, trolling, and crude humor, while targeting repetitive walls of text, quoted articles, or URL dumps.
   * Rate-limited to one check per user per 10 seconds.

Figlet and ASCII banners are normal in `#8chan`. A burst that is a banner, plus the short leftover lines of that banner, does not trip the rate kick there. Copypasta and the Grok check also skip a figlet line in a channel with ASCII allowed, so a banner learned from an earlier kick does not instant-kick the next one. `$spam ascii off` in `#8chan`, or `$spam ascii on` anywhere else, overrides that. Braille and box-drawing floods still use the unicode-art kick.

---

## Autovoice Revocation

To prevent spammers from exploiting the auto-voice system (which grants `+v` to active users), a kick triggers an automatic reset of the user's autovoice progress. They must re-earn their voice from zero.

---

## Commands

All commands require a **channel op** (`@`), channel admin (`&`), channel owner (`~`), or a **bot admin**.

Commands work in the channel (`$spam trigger add ...`) or in PM when the bot can see your op (`$spam #channel trigger add ...`).

| Command | Subcommands / Args | Description | Example |
|---------|-------------------|-------------|---------|
| `$spam` | — | Show antispam status, thresholds, and tracking stats for the channel | `$spam` |
| `$spam` | `on` / `off` | Enable/disable antispam in the current channel | `$spam on` |
| `$spam` | `ban` | Show whether kicks also set a host ban | `$spam ban` |
| `$spam` | `ban on` / `ban off` | Ban `*!*@host` along with each kick. Off by default. Turning it off leaves bans already set | `$spam ban on` |
| `$spam` | `set <param> <val>` | Adjust thresholds (`window`, `threshold`, `unicode_threshold`, `unicode_window`) | `$spam set window 10` |
| `$spam` | `trigger list` | List all channel-specific trigger phrases | `$spam trigger list` |
| `$spam` | `trigger add <phrase>` | Add a phrase for instant-kick on sight | `$spam trigger add Buy Cheap Bitcoin` |
| `$spam` | `trigger del <phrase\|number>`| Remove a trigger phrase by exact text or index number | `$spam trigger del 1` |
| `$spam` | `exempt list` | List users exempt from spam detection | `$spam exempt list` |
| `$spam` | `exempt add <nick>` | Exempt a user from all spam checks | `$spam exempt add DuckHunt` |
| `$spam` | `exempt del <nick>` | Remove a user exemption | `$spam exempt del DuckHunt` |
| `$spam` | `cmdexempt list` | List exempt command prefixes | `$spam cmdexempt list` |
| `$spam` | `cmdexempt add <prefix>` | Exempt a command prefix from spam detection | `$spam cmdexempt add !bang` |
| `$spam` | `cmdexempt del <prefix>` | Remove a command prefix exemption | `$spam cmdexempt del !bang` |
| `$spam` | `copypasta status` | View copypasta shingles database statistics and settings | `$spam copypasta status` |
| `$spam` | `copypasta clear` | Wipe the learned copypasta shingles database for this channel | `$spam copypasta clear` |
| `$spam` | `ascii on` / `ascii off` | Allow figlet and ASCII bursts through the rate kick. On in `#8chan` unless an op turns it off. Other channels stay off until `$spam ascii on`. Braille floods still kick | `$spam ascii off` |
| `$spam` | `help` | Send a command reference guide via NOTICE | `$spam help` |

---

## Exemptions

### Privilege-Based
Channel halfops (`%`) and above (ops `@`, admins `&`, owners `~`) are **automatically exempt** from all spam kicks. Voiced (`+v`) users are **not** exempt.

### User Exemptions
Add specific users to a per-channel exempt list. Useful for bots that might trigger rate-based detection (e.g. game bots, relay bots):
```
$spam exempt add DuckHunt
$spam exempt add Trivia_Bot
```

### Command Prefix Exemptions
Exempt messages starting with a specific command prefix. Useful for game commands that users may send rapidly (e.g. duckhunt):
```
$spam cmdexempt add !bang
$spam cmdexempt add !befriend
$spam cmdexempt add !duck
```
Messages starting with an exempt prefix are completely skipped by all spam detection modes — they won't count toward rate limits, won't trigger content matches, and won't be fingerprinted as copypasta.

