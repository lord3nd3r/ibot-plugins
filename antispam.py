"""
antispam.py - Anti-Spam Kick Protection

Detects users who paste rapid-fire multi-line spam (URL dumps, quoted text walls)
and kicks them. Host bans (+b *!*@host) are optional and off unless a channel
op turns them on with $spam ban on.

Four detection modes:
    1. Rate-based:      If a user sends >= `threshold` messages within `window`
                        seconds, they get kicked.
    2. Content-based:   If a message matches any configured trigger phrase,
                        instant kick — regardless of rate or timing.
    3. Unicode art:     Detects braille/block character floods.
    4. Copypasta:       Fingerprints long messages. When a spammer is kicked,
                        their messages are learned. Future messages matching
                        the learned spam get instant-kicked — even with minor
                        word changes.

Author: Kristopher Craig
Permissions:
    Channel ops (@), channel admins (&), channel owners (~), and bot admins.
Commands:
    $spam                               - Show status for the current channel
    $spam on / off                      - Enable/disable in the current channel
    $spam ban on / off                  - Also ban *!*@host on kick (off by default)
    $spam set <param> <val>             - Adjust window or threshold
    $spam trigger add <phrase>          - Add a trigger phrase (instant kick)
    $spam trigger del <phrase>          - Remove a trigger phrase
    $spam trigger list                  - List all trigger phrases
    $spam exempt add <nick>             - Exempt a user from spam detection
    $spam exempt del <nick>             - Remove a user exemption
    $spam exempt list                   - List exempt users
    $spam cmdexempt add <prefix>        - Exempt a command prefix (e.g. !bang)
    $spam cmdexempt del <prefix>        - Remove a command exemption
    $spam cmdexempt list                - List exempt command prefixes
    $spam copypasta status              - Show copypasta DB stats
    $spam copypasta clear               - Wipe the copypasta DB for this channel
    $spam ascii on / off                - Allow figlet bursts (#8chan defaults to on)
    $spam help                          - Show help via NOTICE
"""

from sopel import plugin
import json
import logging
import logging.handlers
import os
import re
import requests
import threading
import time

LOGGER = logging.getLogger('antispam')
LOGGER.setLevel(logging.DEBUG)
# File handler — dedicated log file for antispam
_log_path = os.path.join(os.path.expanduser('~/.sopel'), 'antispam.log')
if not LOGGER.handlers:
    try:
        _fh = logging.handlers.RotatingFileHandler(
            _log_path, maxBytes=5 * 1024 * 1024, backupCount=3,
        )
    except OSError:
        _fh = None
    if _fh is not None:
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
        ))
        LOGGER.addHandler(_fh)

B = "\x02"
COLOR_RESET = "\x03"
SEP = "\x0314 · \x03"

# Default settings
DEFAULT_WINDOW = 8        # seconds — how fast messages must come
DEFAULT_THRESHOLD = 5     # messages within window to trigger kick
DEFAULT_UNICODE_THRESHOLD = 3   # unicode art lines within window to trigger kick
DEFAULT_UNICODE_WINDOW = 30     # seconds — window for unicode art flood detection

# Figlet and ASCII banners are normal in these channels. An explicit
# `ascii` setting in the channel overrides still wins.
ASCII_OK_CHANNELS = {'#8chan'}

# Copypasta detection settings
COPYPASTA_MIN_LENGTH = 60       # minimum normalized chars to fingerprint
COPYPASTA_SHINGLE_SIZE = 3      # words per shingle
COPYPASTA_MATCH_RATIO = 0.4     # 40% shingle overlap = copypasta match
COPYPASTA_MAX_SHINGLES = 2000   # max shingles stored per channel
COPYPASTA_TEXT_WINDOW = 300     # seconds to keep recent texts for learning

# Grok AI spam classification settings
GROK_SPAM_ENABLED = True        # toggle AI classification
GROK_SPAM_MIN_LENGTH = 80       # minimum message length to classify
GROK_SPAM_COOLDOWN = 10         # seconds between AI checks per user
GROK_SPAM_TIMEOUT = 5           # API timeout in seconds
GROK_SPAM_MODEL = 'grok-3-mini-fast'  # cheap/fast model for classification


# ========================= SETUP / SHUTDOWN =========================

def setup(bot):
    """Initialize the antispam plugin."""
    bot.memory['spam_messages'] = {}        # (channel, hostmask) -> [timestamps]
    bot.memory['spam_unicode'] = {}         # (channel, hostmask) -> [timestamps] for unicode art
    bot.memory['spam_kicked'] = {}          # (channel, nick_lower) -> timestamp of last kick
    bot.memory['spam_texts'] = {}           # (channel, hostmask) -> [(timestamp, text)] recent msgs
    bot.memory['spam_grok_last'] = {}       # (channel, hostmask) -> timestamp of last Grok check
    bot.memory['spam_lock'] = threading.Lock()
    LOGGER.info("Antispam protection initialized (with Grok AI classification)")


def shutdown(bot):
    """Clean up on shutdown."""
    LOGGER.info("Antispam protection shutdown")


# ========================= SETTINGS HELPERS =========================

def _get_settings(bot, channel):
    """Get antispam settings for a channel from the DB, with defaults."""
    overrides = bot.db.get_plugin_value('antispam', f'settings_{channel.lower()}') or {}
    return {
        'window': overrides.get('window', DEFAULT_WINDOW),
        'threshold': overrides.get('threshold', DEFAULT_THRESHOLD),
        'unicode_threshold': overrides.get('unicode_threshold', DEFAULT_UNICODE_THRESHOLD),
        'unicode_window': overrides.get('unicode_window', DEFAULT_UNICODE_WINDOW),
        'ban': bool(overrides.get('ban', False)),
        'ascii': overrides['ascii'] if 'ascii' in overrides else channel.lower() in ASCII_OK_CHANNELS,
    }


def _save_setting(bot, channel, key, value):
    """Persist a setting override to the DB."""
    db_key = f'settings_{channel.lower()}'
    overrides = bot.db.get_plugin_value('antispam', db_key) or {}
    overrides[key] = value
    bot.db.set_plugin_value('antispam', db_key, overrides)


def _is_channel_enabled(bot, channel):
    """Check if antispam is enabled for a specific channel."""
    enabled = bot.db.get_plugin_value('antispam', 'enabled_channels') or []
    return channel.lower() in [c.lower() for c in enabled]


def _is_exempt(bot, nick, channel):
    """Check if a user holds halfop (+h) or higher, OR is on the per-channel
    exempt list — exempt from spam kicks.
    Voiced (+v) users are explicitly NOT exempt (unless on the exempt list)."""
    chan = str(channel)

    # Check per-channel user exempt list first
    exempt_users = _get_exempt_users(bot, chan)
    if nick.lower() in [u.lower() for u in exempt_users]:
        LOGGER.debug("Antispam: %s is on the exempt list for %s", nick, chan)
        return True

    if chan not in bot.channels:
        return False
    privs = bot.channels[chan].privileges.get(nick, 0)
    # Only halfop (+h) and above are exempt — voice (+v) is NOT exempt
    exempt = bool(privs & (plugin.HALFOP | plugin.OP | plugin.ADMIN | plugin.OWNER | plugin.OPER))
    if privs and not exempt:
        LOGGER.debug("Antispam: %s has privs=%d in %s but is NOT exempt", nick, privs, chan)
    return exempt


def _bot_has_op(bot, channel):
    """Check if the bot has operator (or halfop) status in a channel."""
    chan = str(channel)
    if chan in bot.channels:
        privs = bot.channels[chan].privileges.get(bot.nick, 0)
        return bool(privs & (plugin.HALFOP | plugin.OP))
    return False


def _bot_can_ban(bot, channel):
    """True when the bot has channel op (@) or higher. Halfop cannot set +b."""
    privs = _lookup_privs(bot, channel, bot.nick)
    if not isinstance(privs, int):
        return False
    return bool(privs & (plugin.OP | plugin.ADMIN | plugin.OWNER))


def _ban_mask(hostmask):
    """Build *!*@host from a user@host mask.

    Returns None when the host is missing or a bare wildcard, so a ban
    cannot be set on everyone.
    """
    if not hostmask or '@' not in str(hostmask):
        return None
    host = str(hostmask).split('@', 1)[1].strip()
    if not host or host == '*' or any(c.isspace() for c in host):
        return None
    return f'*!*@{host}'


def _maybe_ban(bot, channel, nick, hostmask):
    """Set +b *!*@host when this channel has bans enabled.

    Returns the mask that was sent, or None if no ban was set.
    Turning bans off later does not remove bans already set.
    """
    if not _get_settings(bot, channel).get('ban'):
        return None
    mask = _ban_mask(hostmask)
    if not mask:
        LOGGER.warning("Antispam: Bans enabled in %s but no host for %s", channel, nick)
        return None
    if not _bot_can_ban(bot, channel):
        LOGGER.warning(
            "Antispam: Bans enabled in %s but bot is not opped — %s kicked without a ban",
            channel, nick,
        )
        return None
    bot.write(['MODE', channel, '+b', mask])
    LOGGER.info("Antispam: Banned %s in %s (%s)", mask, channel, nick)
    return mask


# Channel op (@) and above may manage settings. Halfop (%) may not.
_MANAGE_PRIVS = plugin.OP | plugin.ADMIN | plugin.OWNER


def _lookup_privs(bot, channel, nick):
    """Return the privilege bitmask for nick in channel, or 0 if unknown."""
    channels = getattr(bot, 'channels', None) or {}
    chan_obj = channels.get(channel) or channels.get(str(channel))
    if chan_obj is None:
        want = str(channel).lower()
        for name, obj in channels.items():
            if str(name).lower() == want:
                chan_obj = obj
                break
    if chan_obj is None:
        return 0
    privs = getattr(chan_obj, 'privileges', None) or {}
    if nick in privs:
        return privs[nick] or 0
    nick_l = str(nick).lower()
    for name, val in privs.items():
        if str(name).lower() == nick_l:
            return val or 0
    return 0


def _is_bot_admin(bot, trigger):
    """True for the bot owner or a configured bot admin."""
    if getattr(trigger, 'owner', False) or getattr(trigger, 'admin', False):
        return True
    try:
        cfg_admins = getattr(bot.config.core, 'admins', None)
        if isinstance(cfg_admins, (list, tuple, set)):
            if trigger.nick.lower() in {a.lower() for a in cfg_admins}:
                return True
        cfg_owner = getattr(bot.config.core, 'owner', None)
        if isinstance(cfg_owner, str) and cfg_owner.strip():
            if trigger.nick.lower() == cfg_owner.strip().lower():
                return True
        if isinstance(cfg_owner, (list, tuple, set)):
            if trigger.nick.lower() in {str(o).lower() for o in cfg_owner}:
                return True
    except Exception:
        pass
    return False


def _can_manage_spam(bot, trigger, channel):
    """Bot admins, or channel ops (@) and above in the target channel.

    Works in-channel and from PM, as long as the bot can see the nick's
    mode in that channel. Voice and halfop are not enough.
    """
    if _is_bot_admin(bot, trigger):
        return True
    privs = _lookup_privs(bot, channel, trigger.nick)
    if not isinstance(privs, int):
        return False
    return bool(privs & _MANAGE_PRIVS)


def _get_triggers(bot, channel):
    """Get the list of trigger phrases for a channel."""
    return bot.db.get_plugin_value('antispam', f'triggers_{channel.lower()}') or []


def _save_triggers(bot, channel, triggers):
    """Save the list of trigger phrases for a channel."""
    bot.db.set_plugin_value('antispam', f'triggers_{channel.lower()}', triggers)


def _check_triggers(bot, channel, text):
    """Check if a message matches any trigger phrase. Returns the matched phrase or None."""
    triggers = _get_triggers(bot, channel)
    if not triggers:
        return None
    text_lower = text.lower()
    for phrase in triggers:
        if phrase.lower() in text_lower:
            return phrase
    return None


# ========================= USER & COMMAND EXEMPTIONS =========================

def _get_exempt_users(bot, channel):
    """Get the list of exempt users (nicks) for a channel."""
    return bot.db.get_plugin_value('antispam', f'exempt_users_{channel.lower()}') or []


def _save_exempt_users(bot, channel, users):
    """Save the list of exempt users for a channel."""
    bot.db.set_plugin_value('antispam', f'exempt_users_{channel.lower()}', users)


def _get_exempt_cmds(bot, channel):
    """Get the list of exempt command prefixes for a channel."""
    return bot.db.get_plugin_value('antispam', f'exempt_cmds_{channel.lower()}') or []


def _save_exempt_cmds(bot, channel, cmds):
    """Save the list of exempt command prefixes for a channel."""
    bot.db.set_plugin_value('antispam', f'exempt_cmds_{channel.lower()}', cmds)


def _is_exempt_command(bot, channel, text):
    """Check if a message starts with an exempt command prefix."""
    exempt_cmds = _get_exempt_cmds(bot, channel)
    if not exempt_cmds:
        return False
    text_stripped = text.strip()
    for prefix in exempt_cmds:
        if text_stripped.lower().startswith(prefix.lower()):
            return True
    return False


# Unicode art character ranges used in braille/block art floods
_UNICODE_ART_RANGES = [
    (0x2500, 0x257F),  # Box Drawing
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2800, 0x28FF),  # Braille Patterns
]


def _is_figlet_line(text):
    """True for one line of figlet or ASCII art, not for a normal sentence.

    A banner is padded with spaces, or it is mostly drawing characters, and
    one letter does the drawing. A sentence, and a repeated word such as
    "hahahahaha", has neither of those shapes.
    """
    s = str(text).strip()
    if len(s) < 8:
        return False
    letters = [c.lower() for c in s if c.isalpha()]
    symbols = sum(1 for c in s if not c.isspace() and not c.isalnum())
    spaces = s.count(' ')
    banner_shape = (
        (spaces / len(s) >= 0.25)
        or (len(letters) < 4 and symbols >= 3)
        or (symbols >= 3 and spaces >= 2)
    )
    if not banner_shape:
        return False
    if len(letters) < 4:
        return symbols >= 3
    counts = {}
    for char in letters:
        counts[char] = counts.get(char, 0) + 1
    top = max(counts.values()) / len(letters)
    real_words = 0
    for word in re.findall(r"[A-Za-z]{3,}", s):
        word_counts = {}
        for char in word.lower():
            word_counts[char] = word_counts.get(char, 0) + 1
        if max(word_counts.values()) / len(word) < 0.7:
            real_words += 1
    if top >= 0.4 and real_words <= 2:
        return True
    if spaces / len(s) >= 0.35 and real_words <= 1 and top >= 0.3:
        return True
    return False


def _burst_is_ascii(texts):
    """True when a flood burst is a figlet banner plus its short leftover lines.

    Braille and box-drawing floods stay on the unicode-art check. Only the
    texts inside the rate window should be passed in.
    """
    if not texts:
        return False
    long_art = 0
    short = 0
    other = 0
    for text in texts:
        if _is_figlet_line(text):
            long_art += 1
        elif len(str(text).strip()) <= 6:
            short += 1
        else:
            other += 1
    if long_art == 0:
        return False
    return (long_art + short) / len(texts) >= 0.6 and other <= long_art


def _is_unicode_art(text, min_length=20, ratio=0.3):
    """Detect messages that are predominantly unicode art (braille, box drawing, etc.).

    Returns True if at least ``ratio`` of non-whitespace characters fall in
    known art-character ranges and the message meets ``min_length``.
    """
    stripped = text.replace(' ', '')
    if len(stripped) < min_length:
        return False
    art_count = sum(
        1 for c in stripped
        if any(lo <= ord(c) <= hi for lo, hi in _UNICODE_ART_RANGES)
    )
    return (art_count / len(stripped)) >= ratio


# ========================= COPYPASTA DETECTION =========================

def _normalize_for_fingerprint(text):
    """Normalize text for copypasta fingerprinting.

    Strips URLs, IRC formatting, punctuation — keeps only lowercase words.
    """
    text = text.lower()
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'[\x02\x03\x0f\x16\x1d\x1f]', '', text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _get_shingles(text, size=COPYPASTA_SHINGLE_SIZE):
    """Extract word-level shingles (n-grams) from normalized text."""
    words = text.split()
    if len(words) < size:
        return set()
    return {' '.join(words[i:i + size]) for i in range(len(words) - size + 1)}


def _get_spam_shingles(bot, channel):
    """Load stored spam shingles for a channel from the DB."""
    stored = bot.db.get_plugin_value('antispam', f'copypasta_{channel}') or []
    return set(stored)


def _save_spam_shingles(bot, channel, shingles):
    """Save spam shingles for a channel to the DB, capped at max."""
    shingle_list = list(shingles)
    if len(shingle_list) > COPYPASTA_MAX_SHINGLES:
        shingle_list = shingle_list[-COPYPASTA_MAX_SHINGLES:]
    bot.db.set_plugin_value('antispam', f'copypasta_{channel}', shingle_list)


def _check_copypasta(bot, channel, text):
    """Check if a message matches known copypasta.

    Returns (ratio, matched_count, total_shingles) or (0, 0, 0) if no match.
    """
    normalized = _normalize_for_fingerprint(text)
    if len(normalized) < COPYPASTA_MIN_LENGTH:
        return 0, 0, 0
    msg_shingles = _get_shingles(normalized)
    if not msg_shingles:
        return 0, 0, 0
    known = _get_spam_shingles(bot, channel)
    if not known:
        return 0, 0, 0
    overlap = msg_shingles & known
    ratio = len(overlap) / len(msg_shingles)
    return ratio, len(overlap), len(msg_shingles)


def _learn_spam_content(bot, channel, texts):
    """Record message texts as known spam for copypasta detection."""
    known = _get_spam_shingles(bot, channel)
    added = 0
    for text in texts:
        normalized = _normalize_for_fingerprint(text)
        if len(normalized) >= COPYPASTA_MIN_LENGTH:
            shingles = _get_shingles(normalized)
            new = shingles - known
            known.update(new)
            added += len(new)
    if added:
        _save_spam_shingles(bot, channel, known)
        LOGGER.info("Antispam: Learned %d new copypasta shingles for %s (total: %d)",
                    added, channel, len(known))


def _track_text(bot, channel, hostmask, text):
    """Track a message's text for copypasta learning when the user gets kicked."""
    now = time.time()
    key = (channel, hostmask)
    with bot.memory['spam_lock']:
        texts = bot.memory['spam_texts']
        if key not in texts:
            texts[key] = []
        texts[key].append((now, text))
        # Prune old entries
        cutoff = now - COPYPASTA_TEXT_WINDOW
        texts[key] = [(t, txt) for t, txt in texts[key] if t > cutoff]


def _get_tracked_texts(bot, channel, hostmask, since=None):
    """Get recently tracked texts for a user.

    ``since`` is the rate-window cutoff. Texts at or before that time stay
    out, so an earlier conversation does not hide a banner pasted just now.
    """
    key = (channel, hostmask)
    with bot.memory['spam_lock']:
        entries = bot.memory['spam_texts'].get(key, [])
        if since is None:
            return [txt for _, txt in entries]
        return [txt for t, txt in entries if t > since]


# ========================= GROK AI CLASSIFICATION =========================

_GROK_SPAM_PROMPT = """You are a spam classifier for the IRC channel #8chan. This channel allows ALL offensive language, racial slurs, trolling, and crude humor — that is NORMAL and must NOT be flagged.

You are looking for ONE specific behavior: a user FLOODING the channel with repetitive copypasta — walls of quoted news articles, URLs, and manifesto-style text about the same topic, posted over and over across multiple messages in rapid succession.

Here are the user's last {count} messages sent in the last {seconds} seconds:
---
{messages}
---

Is this user flooding the channel with repetitive copypasta/spam? Consider:
- Are they posting multiple quoted articles/URLs about the same topic rapidly?
- Is this a wall-of-text flood rather than normal conversation?
- One or two links in conversation is NORMAL. 5+ quoted articles in rapid succession is SPAM.

Reply with ONLY the word SPAM or SAFE. Nothing else."""

GROK_SPAM_MIN_MESSAGES = 5    # need at least this many recent messages to bother checking


def _grok_classify_spam(bot, channel, hostmask, message_text):
    """Ask Grok AI whether a user's recent messages constitute copypasta spam.

    Sends the user's full recent message history so Grok can see the
    flooding pattern, not just one message in isolation.

    Returns True if Grok says it's spam, False otherwise.
    Fails safe (returns False) on any error or timeout.
    Rate-limited per user to avoid API abuse.
    """
    if not GROK_SPAM_ENABLED:
        return False

    # Get the user's recent messages
    recent = _get_tracked_texts(bot, channel, hostmask)
    if len(recent) < GROK_SPAM_MIN_MESSAGES:
        return False

    # Rate limit: one AI check per user per cooldown period
    now = time.time()
    key = (channel, hostmask)
    with bot.memory['spam_lock']:
        last_check = bot.memory['spam_grok_last'].get(key, 0)
        if now - last_check < GROK_SPAM_COOLDOWN:
            return False
        bot.memory['spam_grok_last'][key] = now

    # Use the Grok session from the ai-grok plugin
    session = bot.memory.get('grok_session')
    if not session:
        LOGGER.debug("Antispam: No grok_session available for AI classification")
        return False

    try:
        # Build context: last N messages, truncated to fit
        msg_lines = []
        for i, txt in enumerate(recent[-15:], 1):
            msg_lines.append(f"{i}. {txt[:200]}")
        messages_block = '\n'.join(msg_lines)

        prompt = _GROK_SPAM_PROMPT.format(
            count=len(recent),
            seconds=int(COPYPASTA_TEXT_WINDOW),
            messages=messages_block,
        )
        payload = {
            "model": GROK_SPAM_MODEL,
            "input": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_output_tokens": 10,
        }
        LOGGER.debug("Antispam: Sending %d messages to Grok AI for %s in %s",
                     len(recent), hostmask, channel)
        r = session.post(
            "https://api.x.ai/v1/responses",
            json=payload,
            timeout=(3, GROK_SPAM_TIMEOUT),
        )
        r.raise_for_status()
        data = r.json()

        # Parse response — same format as ai-grok.py
        reply = ''
        output_items = data.get('output', [])
        for item in output_items:
            if isinstance(item, dict) and item.get('type') == 'message':
                for part in (item.get('content') or []):
                    if isinstance(part, dict) and part.get('type') in ('text', 'output_text'):
                        reply += part.get('text', '')

        verdict = reply.strip().upper()
        LOGGER.info("Antispam: Grok AI verdict for %s in %s: %s (%d msgs analyzed)",
                    hostmask, channel, verdict, len(recent))
        return verdict == 'SPAM'

    except requests.Timeout:
        LOGGER.debug("Antispam: Grok AI classification timed out")
        return False
    except Exception:
        LOGGER.debug("Antispam: Grok AI classification failed", exc_info=True)
        return False


# ========================= CORE LOGIC =========================

@plugin.thread(True)
@plugin.rule('.*')
def on_message(bot, trigger):
    """Track every PRIVMSG in enabled channels for spam detection."""
    if trigger.is_privmsg:
        return
    if not str(trigger.sender).startswith('#'):
        return

    channel = str(trigger.sender).lower()
    nick = trigger.nick

    if not _is_channel_enabled(bot, channel):
        return
    if nick.lower() == bot.nick.lower():
        return
    if _is_exempt(bot, nick, channel):
        return

    hostmask = f"{trigger.user or '*'}@{trigger.host or '*'}"
    message_text = str(trigger)

    # --- Command prefix exemption (e.g. !bang, !befriend) ---
    if _is_exempt_command(bot, channel, message_text):
        return

    # --- Content-based detection (instant kick) ---
    matched = _check_triggers(bot, channel, message_text)
    LOGGER.debug(
        "Antispam: [%s] <%s> %s | triggers=%s | matched=%s",
        channel, nick, message_text[:80], bool(_get_triggers(bot, channel)), matched,
    )
    if matched:
        if _bot_has_op(bot, channel):
            reason = f"Matched spam trigger — kick on sight"
            banned = _maybe_ban(bot, channel, nick, hostmask)
            bot.write(['KICK', channel, nick, f':{reason}'])
            LOGGER.info(
                "Antispam: Kicked %s (%s) from %s — trigger match: %s%s",
                nick, hostmask, channel, matched,
                f" (banned {banned})" if banned else "",
            )
        else:
            LOGGER.warning(
                "Antispam: Trigger match from %s in %s — bot not opped",
                nick, channel,
            )
        return

    settings = _get_settings(bot, channel)
    now = time.time()
    key = (channel, hostmask)

    # --- Track message text for copypasta learning ---
    _track_text(bot, channel, hostmask, message_text)

    # --- Copypasta detection (learned spam fingerprints) ---
    # A figlet line in a channel that allows ASCII is not copypasta, even
    # when an earlier kick stored that banner's shingles.
    ratio, matched, total = _check_copypasta(bot, channel, message_text)
    ascii_line = _is_figlet_line(message_text)
    if ratio >= COPYPASTA_MATCH_RATIO and not (settings.get('ascii') and ascii_line):
        LOGGER.info(
            "Antispam: Copypasta match from %s in %s — %.0f%% (%d/%d shingles)",
            nick, channel, ratio * 100, matched, total,
        )
        _kick_spammer(bot, nick, channel, hostmask, settings, 0,
                     reason=f"Copypasta detected ({matched}/{total} fingerprints matched)")
        # Learn any new shingles from this message too
        _learn_spam_content(bot, channel, [message_text])
        return

    # --- Grok AI classification (catches new copypasta not yet fingerprinted) ---
    if not (settings.get('ascii') and ascii_line) and _grok_classify_spam(bot, channel, hostmask, message_text):
        LOGGER.info("Antispam: Grok AI flagged spam from %s in %s", nick, channel)
        _kick_spammer(bot, nick, channel, hostmask, settings, 0,
                     reason="AI detected copypasta spam")
        _learn_spam_content(bot, channel, [message_text])
        return

    # --- Unicode art flood detection (stricter threshold) ---
    # Figlet allowance does not cover braille or box-drawing floods.
    if _is_unicode_art(message_text):
        unicode_triggered = False
        with bot.memory['spam_lock']:
            umsgs = bot.memory['spam_unicode']
            if key not in umsgs:
                umsgs[key] = []
            umsgs[key].append(now)
            cutoff = now - settings['unicode_window']
            umsgs[key] = [t for t in umsgs[key] if t > cutoff]
            ucount = len(umsgs[key])
            if ucount >= settings['unicode_threshold']:
                del umsgs[key]
                unicode_triggered = True

        if unicode_triggered:
            _kick_spammer(bot, nick, channel, hostmask, settings, ucount,
                         reason="Unicode art flood detected — knock it off")
            return

    # --- Rate-based detection ---
    triggered = False

    with bot.memory['spam_lock']:
        msgs = bot.memory['spam_messages']

        if key not in msgs:
            msgs[key] = []
        msgs[key].append(now)

        # Prune outside window
        cutoff = now - settings['window']
        msgs[key] = [t for t in msgs[key] if t > cutoff]
        count = len(msgs[key])

        if count >= settings['threshold']:
            # Reset their counter
            del msgs[key]
            triggered = True

    if triggered:
        window_start = now - settings['window']
        if settings.get('ascii') and _burst_is_ascii(
            _get_tracked_texts(bot, channel, hostmask, since=window_start)
        ):
            LOGGER.info(
                "Antispam: ASCII art burst from %s in %s — allowed (%d msgs)",
                nick, channel, count,
            )
            return
        _kick_spammer(bot, nick, channel, hostmask, settings, count)


def _revoke_autovoice(bot, channel, nick):
    """Reset a user's autovoice data so they don't get re-voiced on rejoin.

    Directly modifies the autovoice module's in-memory data and saves to disk.
    This is the nuclear option — the user has to re-earn voice from zero.
    """
    try:
        import autovoice
        nick_lower = nick.lower()
        autovoice._load()
        with autovoice._data_lock:
            chan_data = (autovoice._data or {}).get(channel, {})
            if nick_lower in chan_data:
                chan_data[nick_lower]['count'] = 0
                chan_data[nick_lower]['autovoiced'] = False
                LOGGER.info("Antispam: Reset autovoice data for %s in %s", nick, channel)
        autovoice._save()
    except Exception:
        LOGGER.debug("Antispam: Could not reset autovoice data (module not loaded?)")


def _kick_spammer(bot, nick, channel, hostmask, settings, count, reason=None):
    """Kick the spammer and revoke their autovoice so they don't get re-voiced on rejoin."""
    if not _bot_has_op(bot, channel):
        LOGGER.warning(
            "Antispam: Spam from %s in %s — bot not opped, cannot kick",
            nick, channel,
        )
        return

    if reason is None:
        reason = f"Spam detected ({count} msgs in {settings['window']}s) — slow down"
    banned = _maybe_ban(bot, channel, nick, hostmask)
    bot.write(['KICK', channel, nick, f':{reason}'])

    # Record the kick so autovoice won't re-voice them on rejoin
    with bot.memory['spam_lock']:
        bot.memory['spam_kicked'][(channel, nick.lower())] = time.time()

    # Learn their recent messages as copypasta for future detection
    recent_texts = _get_tracked_texts(bot, channel, hostmask)
    if recent_texts:
        _learn_spam_content(bot, channel, recent_texts)

    # Nuke their autovoice data so they have to re-earn voice from zero
    _revoke_autovoice(bot, channel, nick)

    LOGGER.info(
        "Antispam: Kicked %s (%s) from %s — %s (autovoice revoked, copypasta learned%s)",
        nick, hostmask, channel, reason,
        f", banned {banned}" if banned else "",
    )


# ========================= PERIODIC CLEANUP =========================

@plugin.thread(True)
@plugin.interval(30)
def cleanup(bot):
    """Prune stale message tracking entries."""
    now = time.time()

    with bot.memory['spam_lock']:
        msgs = bot.memory['spam_messages']
        stale = []
        for key, timestamps in msgs.items():
            channel = key[0]
            settings = _get_settings(bot, channel)
            cutoff = now - settings['window']
            msgs[key] = [t for t in timestamps if t > cutoff]
            if not msgs[key]:
                stale.append(key)
        for key in stale:
            del msgs[key]

        # Prune unicode art tracking
        umsgs = bot.memory.get('spam_unicode', {})
        ustale = []
        for key, timestamps in umsgs.items():
            channel = key[0]
            settings = _get_settings(bot, channel)
            cutoff = now - settings['unicode_window']
            umsgs[key] = [t for t in timestamps if t > cutoff]
            if not umsgs[key]:
                ustale.append(key)
        for key in ustale:
            del umsgs[key]


# ========================= ADMIN COMMANDS =========================

@plugin.command('spam')
def cmd_spam(bot, trigger):
    """$spam <status|on|off|set|trigger|help> — Manage antispam protection.

    Channel ops (@ or higher) and bot admins.
    In a channel:  $spam trigger add <phrase>
    In PM:         $spam #channel trigger add <phrase>
    """
    args = (trigger.group(2) or '').strip().split()

    # Determine channel: from PM require #channel as first arg, in-channel use sender
    if trigger.is_privmsg:
        if not args or not args[0].startswith('#'):
            return bot.reply(
                f"From PM, specify the channel first: "
                f"{B}$spam #channel <subcommand>{B}"
            )
        channel = args.pop(0).lower()
    else:
        channel = str(trigger.sender).lower()

    if not _can_manage_spam(bot, trigger, channel):
        return bot.reply(
            "⛔ You need to be a channel op (@) or a bot admin to manage antispam."
        )

    subcmd = args[0].lower() if args else 'status'

    dispatch = {
        'status':    lambda: _cmd_status(bot, trigger, channel),
        'on':        lambda: _cmd_toggle(bot, trigger, channel, enable=True),
        'off':       lambda: _cmd_toggle(bot, trigger, channel, enable=False),
        'set':       lambda: _cmd_set(bot, trigger, channel, args[1:]),
        'trigger':   lambda: _cmd_trigger(bot, trigger, channel, args[1:]),
        'exempt':    lambda: _cmd_exempt(bot, trigger, channel, args[1:]),
        'cmdexempt': lambda: _cmd_cmdexempt(bot, trigger, channel, args[1:]),
        'copypasta': lambda: _cmd_copypasta(bot, trigger, channel, args[1:]),
        'ban':       lambda: _cmd_ban(bot, trigger, channel, args[1:]),
        'ascii':     lambda: _cmd_ascii(bot, trigger, channel, args[1:]),
        'help':      lambda: _cmd_help(bot, trigger),
    }

    handler = dispatch.get(subcmd)
    if handler:
        handler()
    else:
        bot.reply(
            f"⚠️ Unknown subcommand {B}{subcmd}{B}. "
            f"Try: {B}$spam help{B}"
        )


def _cmd_status(bot, trigger, channel):
    """Show antispam status for the given channel."""
    settings = _get_settings(bot, channel)
    enabled = _is_channel_enabled(bot, channel)
    icon = "✅" if enabled else "❌"
    triggers = _get_triggers(bot, channel)

    with bot.memory['spam_lock']:
        active = sum(1 for (ch, _) in bot.memory['spam_messages'] if ch == channel)

    exempt_users = _get_exempt_users(bot, channel)
    exempt_cmds = _get_exempt_cmds(bot, channel)

    bot.say(
        f"🛡️ {B}Antispam Status{B} for {B}{channel}{B}{SEP}"
        f"{icon} {'Enabled' if enabled else 'Disabled'}{SEP}"
        f"⏱️ Window: {B}{settings['window']}s{B}{SEP}"
        f"🎯 Threshold: {B}{settings['threshold']}{B} msgs{SEP}"
        f"🎨 Unicode: {B}{settings['unicode_threshold']}{B} msgs / {B}{settings['unicode_window']}s{B}{SEP}"
        f"🔨 Bans: {B}{'on' if settings['ban'] else 'off'}{B}{SEP}"
        f"🎨 ASCII: {B}{'on' if settings.get('ascii') else 'off'}{B}{SEP}"
        f"🔑 Triggers: {B}{len(triggers)}{B}{SEP}"
        f"👤 Exempt users: {B}{len(exempt_users)}{B}{SEP}"
        f"🎮 Exempt cmds: {B}{len(exempt_cmds)}{B}{SEP}"
        f"📊 Tracking: {B}{active}{B} user(s)"
    )


def _cmd_toggle(bot, trigger, channel, enable):
    """Enable or disable antispam for a channel."""
    enabled_channels = bot.db.get_plugin_value('antispam', 'enabled_channels') or []

    if enable:
        if channel not in [c.lower() for c in enabled_channels]:
            enabled_channels.append(channel)
        bot.db.set_plugin_value('antispam', 'enabled_channels', enabled_channels)
        bot.say(f"✅ Antispam protection {B}enabled{B} for {B}{channel}{B}")
    else:
        enabled_channels = [c for c in enabled_channels if c.lower() != channel]
        bot.db.set_plugin_value('antispam', 'enabled_channels', enabled_channels)
        bot.say(f"❌ Antispam protection {B}disabled{B} for {B}{channel}{B}")

    LOGGER.info("Antispam: %s in %s by %s", 'Enabled' if enable else 'Disabled', channel, trigger.nick)


def _cmd_ban(bot, trigger, channel, args):
    """Enable or disable host bans on kick. Usage: $spam [#chan] ban [on|off]"""
    sub = args[0].lower() if args else 'status'

    if sub in ('status', 'show'):
        enabled = _get_settings(bot, channel)['ban']
        state = "enabled" if enabled else "disabled"
        bot.say(
            f"🔨 Bans are {B}{state}{B} for {B}{channel}{B}. "
            f"Use {B}$spam ban on{B} or {B}$spam ban off{B}."
        )
        return

    if sub in ('on', 'enable', 'yes'):
        _save_setting(bot, channel, 'ban', True)
        bot.say(
            f"🔨 Antispam bans {B}enabled{B} for {B}{channel}{B}. "
            f"Kicks will also set {B}+b *!*@host{B}."
        )
        LOGGER.info("Antispam: Bans enabled in %s by %s", channel, trigger.nick)
        return

    if sub in ('off', 'disable', 'no'):
        _save_setting(bot, channel, 'ban', False)
        bot.say(
            f"🔨 Antispam bans {B}disabled{B} for {B}{channel}{B}. "
            f"Existing bans stay until someone removes them."
        )
        LOGGER.info("Antispam: Bans disabled in %s by %s", channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$spam ban{B} <on|off>")


def _cmd_ascii(bot, trigger, channel, args):
    """Allow or kick figlet and ASCII bursts. Usage: $spam [#chan] ascii [on|off]"""
    sub = args[0].lower() if args else 'status'
    if sub in ('status', 'show'):
        enabled = _get_settings(bot, channel)['ascii']
        state = "allowed" if enabled else "kicked with other floods"
        bot.say(f"🎨 ASCII art is {B}{state}{B} in {B}{channel}{B}.")
        return
    if sub in ('on', 'enable', 'yes'):
        _save_setting(bot, channel, 'ascii', True)
        bot.say(f"🎨 ASCII art bursts are {B}allowed{B} in {B}{channel}{B}.")
        LOGGER.info("Antispam: ASCII allowed in %s by %s", channel, trigger.nick)
        return
    if sub in ('off', 'disable', 'no'):
        _save_setting(bot, channel, 'ascii', False)
        bot.say(f"🎨 ASCII art bursts are {B}kicked{B} with other floods in {B}{channel}{B}.")
        LOGGER.info("Antispam: ASCII kicks enabled in %s by %s", channel, trigger.nick)
        return
    bot.reply(f"⚠️ Usage: {B}$spam ascii{B} <on|off>")


def _cmd_set(bot, trigger, channel, args):
    """Adjust a setting. Usage: $spam [#chan] set <window|threshold|...> <value>"""
    valid = {
        'window': (3, 120),
        'threshold': (3, 30),
        'unicode_threshold': (2, 10),
        'unicode_window': (10, 120),
    }

    if len(args) < 2:
        bot.reply(
            f"Usage: {B}$spam set{B} <{'|'.join(valid)}> <value>"
        )
        return

    param = args[0].lower()
    if param not in valid:
        bot.reply(
            f"⚠️ Unknown parameter. Valid: "
            + ", ".join(f"{B}{p}{B}" for p in valid)
        )
        return

    try:
        value = int(args[1])
    except ValueError:
        return bot.reply("⚠️ Value must be a number.")

    lo, hi = valid[param]
    if value < lo or value > hi:
        return bot.reply(f"⚠️ {B}{param}{B} must be between {lo} and {hi}.")

    _save_setting(bot, channel, param, value)
    bot.say(f"✅ Antispam {B}{param}{B} set to {B}{value}{B} for {B}{channel}{B}")
    LOGGER.info("Antispam: %s → %d in %s by %s", param, value, channel, trigger.nick)


def _cmd_trigger(bot, trigger, channel, args):
    """Manage trigger phrases. Usage: $spam [#chan] trigger <add|del|list> [phrase]"""
    subcmd = args[0].lower() if args else 'list'

    if subcmd == 'list':
        triggers = _get_triggers(bot, channel)
        if not triggers:
            bot.say(f"📋 No trigger phrases set for {B}{channel}{B}.")
        else:
            bot.say(f"📋 {B}Trigger Phrases{B} for {B}{channel}{B} ({len(triggers)}):")
            for i, phrase in enumerate(triggers, 1):
                bot.say(f"  {i}. {phrase}")
        return

    if subcmd == 'add':
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam trigger add{B} <phrase>")
        phrase = ' '.join(args[1:])
        triggers = _get_triggers(bot, channel)
        # Check for duplicates (case-insensitive)
        if phrase.lower() in [t.lower() for t in triggers]:
            return bot.reply(f"⚠️ Trigger already exists: {B}{phrase}{B}")
        triggers.append(phrase)
        _save_triggers(bot, channel, triggers)
        bot.say(f"✅ Added trigger for {B}{channel}{B}: {B}{phrase}{B}")
        LOGGER.info("Antispam: Added trigger '%s' in %s by %s", phrase, channel, trigger.nick)
        return

    if subcmd in ('del', 'rm', 'remove'):
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam trigger del{B} <phrase or number>")
        raw = ' '.join(args[1:])
        triggers = _get_triggers(bot, channel)

        # Allow deletion by number
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(triggers):
                removed = triggers.pop(idx)
                _save_triggers(bot, channel, triggers)
                bot.say(f"✅ Removed trigger from {B}{channel}{B}: {B}{removed}{B}")
                LOGGER.info("Antispam: Removed trigger '%s' in %s by %s", removed, channel, trigger.nick)
                return
        except ValueError:
            pass

        # Delete by phrase match (case-insensitive)
        new_triggers = [t for t in triggers if t.lower() != raw.lower()]
        if len(new_triggers) == len(triggers):
            return bot.reply(f"⚠️ Trigger not found: {B}{raw}{B}")
        _save_triggers(bot, channel, new_triggers)
        bot.say(f"✅ Removed trigger from {B}{channel}{B}: {B}{raw}{B}")
        LOGGER.info("Antispam: Removed trigger '%s' in %s by %s", raw, channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$spam trigger{B} <add|del|list> [phrase]")


def _cmd_copypasta(bot, trigger, channel, args):
    """Manage copypasta fingerprint DB. Usage: $spam [#chan] copypasta <status|clear>"""
    subcmd = args[0].lower() if args else 'status'

    if subcmd == 'status':
        shingles = _get_spam_shingles(bot, channel)
        ai_status = "✅ ON" if GROK_SPAM_ENABLED else "❌ OFF"
        bot.say(
            f"🧬 {B}Copypasta DB{B} for {B}{channel}{B}{SEP}"
            f"📊 Fingerprints: {B}{len(shingles)}{B} shingles{SEP}"
            f"🤖 AI Classification: {ai_status}{SEP}"
            f"📏 Min length: {B}{COPYPASTA_MIN_LENGTH}{B} chars{SEP}"
            f"🎯 Match ratio: {B}{int(COPYPASTA_MATCH_RATIO * 100)}%{B}"
        )
        return

    if subcmd == 'clear':
        _save_spam_shingles(bot, channel, set())
        bot.say(f"🗑️ Cleared copypasta fingerprint DB for {B}{channel}{B}")
        LOGGER.info("Antispam: Copypasta DB cleared for %s by %s", channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$spam copypasta{B} <status|clear>")


def _cmd_exempt(bot, trigger, channel, args):
    """Manage per-channel user exemptions. Usage: $spam exempt <add|del|list> [nick]"""
    subcmd = args[0].lower() if args else 'list'

    if subcmd == 'list':
        users = _get_exempt_users(bot, channel)
        if not users:
            bot.say(f"📋 No exempt users for {B}{channel}{B}.")
        else:
            bot.say(
                f"📋 {B}Exempt Users{B} for {B}{channel}{B} ({len(users)}): "
                + ", ".join(f"{B}{u}{B}" for u in users)
            )
        return

    if subcmd == 'add':
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam exempt add{B} <nick>")
        nick = args[1]
        users = _get_exempt_users(bot, channel)
        if nick.lower() in [u.lower() for u in users]:
            return bot.reply(f"⚠️ {B}{nick}{B} is already exempt.")
        users.append(nick)
        _save_exempt_users(bot, channel, users)
        bot.say(f"✅ {B}{nick}{B} is now exempt from antispam in {B}{channel}{B}")
        LOGGER.info("Antispam: Exempted user '%s' in %s by %s", nick, channel, trigger.nick)
        return

    if subcmd in ('del', 'rm', 'remove'):
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam exempt del{B} <nick>")
        nick = args[1]
        users = _get_exempt_users(bot, channel)
        new_users = [u for u in users if u.lower() != nick.lower()]
        if len(new_users) == len(users):
            return bot.reply(f"⚠️ {B}{nick}{B} is not on the exempt list.")
        _save_exempt_users(bot, channel, new_users)
        bot.say(f"✅ Removed {B}{nick}{B} from antispam exemptions in {B}{channel}{B}")
        LOGGER.info("Antispam: Un-exempted user '%s' in %s by %s", nick, channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$spam exempt{B} <add|del|list> [nick]")


def _cmd_cmdexempt(bot, trigger, channel, args):
    """Manage per-channel command prefix exemptions. Usage: $spam cmdexempt <add|del|list> [prefix]"""
    subcmd = args[0].lower() if args else 'list'

    if subcmd == 'list':
        cmds = _get_exempt_cmds(bot, channel)
        if not cmds:
            bot.say(f"📋 No exempt command prefixes for {B}{channel}{B}.")
        else:
            bot.say(
                f"📋 {B}Exempt Commands{B} for {B}{channel}{B} ({len(cmds)}): "
                + ", ".join(f"{B}{c}{B}" for c in cmds)
            )
        return

    if subcmd == 'add':
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam cmdexempt add{B} <prefix>")
        prefix = args[1]
        cmds = _get_exempt_cmds(bot, channel)
        if prefix.lower() in [c.lower() for c in cmds]:
            return bot.reply(f"⚠️ {B}{prefix}{B} is already exempt.")
        cmds.append(prefix)
        _save_exempt_cmds(bot, channel, cmds)
        bot.say(f"✅ Commands starting with {B}{prefix}{B} are now exempt in {B}{channel}{B}")
        LOGGER.info("Antispam: Exempted cmd prefix '%s' in %s by %s", prefix, channel, trigger.nick)
        return

    if subcmd in ('del', 'rm', 'remove'):
        if len(args) < 2:
            return bot.reply(f"Usage: {B}$spam cmdexempt del{B} <prefix>")
        prefix = args[1]
        cmds = _get_exempt_cmds(bot, channel)
        new_cmds = [c for c in cmds if c.lower() != prefix.lower()]
        if len(new_cmds) == len(cmds):
            return bot.reply(f"⚠️ {B}{prefix}{B} is not on the exempt list.")
        _save_exempt_cmds(bot, channel, new_cmds)
        bot.say(f"✅ Removed {B}{prefix}{B} from command exemptions in {B}{channel}{B}")
        LOGGER.info("Antispam: Un-exempted cmd prefix '%s' in %s by %s", prefix, channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$spam cmdexempt{B} <add|del|list> [prefix]")


def _cmd_help(bot, trigger):
    """Send the command reference via NOTICE."""
    nick = trigger.nick
    bot.notice(f"🛡️ {B}Antispam Protection — Spam Detection & Auto-Kick{B}", nick)
    bot.notice(" ", nick)
    bot.notice(f"  Channel ops (@ or higher) and bot admins can use these commands.", nick)
    bot.notice(f"  In a channel, the channel is implicit.", nick)
    bot.notice(f"  In PM, prefix with #channel:  {B}$spam #chan trigger add ...\n{B}", nick)
    bot.notice(" ", nick)
    bot.notice(f"  {B}$spam{B}                                  — Status for current channel", nick)
    bot.notice(f"  {B}$spam on{B} / {B}off{B}                          — Enable / disable in this channel", nick)
    bot.notice(f"  {B}$spam ban{B}                              — Show whether kicks also set a ban", nick)
    bot.notice(f"  {B}$spam ban on{B} / {B}off{B}                     — Ban *!*@host on kick (off by default)", nick)
    bot.notice(f"  {B}$spam ascii on{B} / {B}off{B}                   — Allow figlet and ASCII bursts (on in #8chan)", nick)
    bot.notice(f"  {B}$spam set window <sec>{B}                  — Rate detection window (3–120s, default {DEFAULT_WINDOW})", nick)
    bot.notice(f"  {B}$spam set threshold <n>{B}                  — Message count to trigger (3–30, default {DEFAULT_THRESHOLD})", nick)
    bot.notice(f"  {B}$spam set unicode_threshold <n>{B}          — Unicode art lines to trigger (2–10, default {DEFAULT_UNICODE_THRESHOLD})", nick)
    bot.notice(f"  {B}$spam set unicode_window <sec>{B}           — Unicode art window (10–120s, default {DEFAULT_UNICODE_WINDOW})", nick)
    bot.notice(f"  {B}$spam trigger list{B}                       — List trigger phrases", nick)
    bot.notice(f"  {B}$spam trigger add <phrase>{B}               — Add a trigger phrase (instant kick on match)", nick)
    bot.notice(f"  {B}$spam trigger del <phrase|#>{B}             — Remove by phrase or number", nick)
    bot.notice(f"  {B}$spam copypasta status{B}                   — Show copypasta fingerprint DB stats", nick)
    bot.notice(f"  {B}$spam copypasta clear{B}                    — Wipe the copypasta DB", nick)
    bot.notice(f"  {B}$spam help{B}                               — This help message", nick)
    bot.notice(" ", nick)
    bot.notice(
        f"📝 Four detection modes: (1) Rate — kicks if a user sends >= threshold "
        f"msgs within the window. (2) Content — instant kick if a message "
        f"contains any trigger phrase. (3) Copypasta — fingerprints long messages; "
        f"when a spammer is kicked, their text is learned and future matches are "
        f"auto-kicked. (4) Grok AI — classifies long messages as copypasta spam "
        f"vs normal trolling. Users with +h or higher are exempt. "
        f"Per-user and command prefix exemptions available. "
        f"Kicks only, unless $spam ban on is set — then each kick also bans *!*@host. "
        f"Off by default — use $spam on to enable.",
        nick,
    )
    bot.notice(" ", nick)
    bot.notice(f"  {B}$spam exempt list{B}                      — List exempt users", nick)
    bot.notice(f"  {B}$spam exempt add <nick>{B}                — Exempt a user from all spam checks", nick)
    bot.notice(f"  {B}$spam exempt del <nick>{B}                — Remove user exemption", nick)
    bot.notice(f"  {B}$spam cmdexempt list{B}                   — List exempt command prefixes", nick)
    bot.notice(f"  {B}$spam cmdexempt add <prefix>{B}           — Exempt a command prefix (e.g. !bang)", nick)
    bot.notice(f"  {B}$spam cmdexempt del <prefix>{B}           — Remove command exemption", nick)
    bot.say(f"📬 {B}{nick}{B}, check your notices for antispam command help!")

