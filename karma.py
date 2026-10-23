# -*- coding: utf-8 -*-
"""
karma.py - Karma module for Sopel 7/8+
10-minute cooldown per user *per channel* + per-channel & global leaderboards
Now with extra ✨fun✨.
"""

from sopel import module, plugin, tools
from sqlalchemy.sql import text
import re
import time
import threading
import logging
from collections import deque

LOGGER = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────
KARMA_COOLDOWN = 600  # 10 minutes
SELF_KARMA_MESSAGE = "Nice try 😏 but you can't karma yourself."
PRIVATE_KARMA_MESSAGE = "Karma changes belong in public channels. 🗣️"
COOLDOWN_NOTICE = (
    "⏳ Easy there! You can give karma again in {time}. "
    "(Cooldown is per channel.)"
)

# Disallowed karma targets (racial slurs, hate speech, etc.) — case-insensitive exact match
FORBIDDEN_KARMA_TARGETS = {
    "nigger", "nigga", "faggot", "fag", "cunt", "kike", "spic", "chink",
    "gook", "coon", "paki", "raghead", "towelhead", "dyke", "tranny",
    "retard", "homo", "queer", "whore", "slut", "bitch",
}

# Leet-speak normalization map (applied before checking FORBIDDEN_KARMA_TARGETS)
_LEET_MAP = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '6': 'g', '7': 't', '8': 'b', '@': 'a', '$': 's',
    '+': 't', '!': 'i', '|': 'i',
})

# ──────────────────────────────────────────────────────────────
# Karma Flood Protection — Defaults (ON by default, tunable per-channel)
# ──────────────────────────────────────────────────────────────
KARMA_FLOOD_DEFAULTS = {
    'window': 60,          # seconds to look back for coordinated attacks
    'threshold': 3,        # unique givers targeting same nick → triggers protection
    'ban_duration': 600,   # ban length in seconds (10 min)
}
KARMA_FLOOD_ANNOUNCE_CD = 30  # rate-limit flood announcements (seconds)


def _normalize(word: str) -> str:
    """Lowercase + collapse leet substitutions for slur detection."""
    return word.lower().translate(_LEET_MAP)


def is_forbidden(word: str) -> bool:
    """Return True if *word* (after leet normalisation) is a forbidden target."""
    return _normalize(word) in FORBIDDEN_KARMA_TARGETS


def setup(bot):
    """Initialize karma module — flood protection memory + one-time DB cleanup."""
    # ── Karma flood protection in-memory structures ──
    if 'karma_flood_events' not in bot.memory:
        bot.memory['karma_flood_events'] = {}       # {(target, chan): deque}
    if 'karma_flood_unbans' not in bot.memory:
        bot.memory['karma_flood_unbans'] = {}       # {(chan, banmask): Timer}
    if 'karma_flood_last_announce' not in bot.memory:
        bot.memory['karma_flood_last_announce'] = {} # {chan: timestamp}
    if 'karma_flood_banned_hosts' not in bot.memory:
        bot.memory['karma_flood_banned_hosts'] = set()
    bot.memory['karma_flood_settings'] = (
        bot.db.get_plugin_value('karma', 'flood_settings') or {}
    )
    _restore_unbans(bot)

    # ── One-time purge of forbidden karma entries ──
    if bot.memory.get("karma_forbidden_cleaned"):
        return

    if not FORBIDDEN_KARMA_TARGETS:
        return

    # Build safe IN list (hardcoded constants, no user input)
    placeholders = ",".join(f"'{w}'" for w in FORBIDDEN_KARMA_TARGETS)

    global_delete = text(f"""
        DELETE FROM nick_values
        WHERE key = 'karma'
          AND nick_id IN (
            SELECT nick_id FROM nicknames
            WHERE LOWER(COALESCE(canonical, slug)) IN ({placeholders})
          )
    """)

    chan_delete = text(f"""
        DELETE FROM nick_values
        WHERE key LIKE 'karma_channel_%'
          AND nick_id IN (
            SELECT nick_id FROM nicknames
            WHERE LOWER(COALESCE(canonical, slug)) IN ({placeholders})
          )
    """)

    try:
        with bot.db.engine.begin() as conn:
            conn.execute(global_delete)
            conn.execute(chan_delete)
        bot.memory["karma_forbidden_cleaned"] = True
    except Exception:
        # Schema mismatch or permissions — fail silently, allow retry next reload
        bot.memory["karma_forbidden_cleaned"] = False

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def get_karma(db, target):
    """Global karma helper."""
    target_id = tools.Identifier(target)
    karma = db.get_nick_value(target_id, 'karma')
    return int(str(karma).replace('"', '')) if karma is not None else 0


def set_karma(db, target, value):
    db.set_nick_value(tools.Identifier(target), 'karma', str(int(value)))


def add_channel_karma(db, target, channel, delta):
    """Per-channel karma helper."""
    target_id = tools.Identifier(target)
    chan_key = f'karma_channel_{tools.Identifier(channel)}'
    raw = db.get_nick_value(target_id, chan_key)
    try:
        current = int(str(raw).replace('"', '')) if raw is not None else 0
    except (ValueError, TypeError):
        current = 0
    db.set_nick_value(target_id, chan_key, str(current + int(delta)))


def get_channel_karma(db, target, channel):
    """Get per-channel karma (int), stripping old quoted values."""
    target_id = tools.Identifier(target)
    chan_key = f'karma_channel_{tools.Identifier(channel)}'
    raw = db.get_nick_value(target_id, chan_key)
    if raw is None:
        return 0
    try:
        return int(str(raw).replace('"', ''))
    except ValueError:
        return 0


def format_time_remaining(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes} min {secs} sec" if secs else f"{minutes} min"


def get_cooldowns(bot):
    """Return cooldown dict keyed as (giver_identifier, channel_identifier)."""
    if 'karma_cooldown' not in bot.memory:
        bot.memory['karma_cooldown'] = {}
    return bot.memory['karma_cooldown']


# Cleanup old cooldown entries
def cleanup_cooldowns(bot):
    cooldowns = bot.memory.get('karma_cooldown')
    if not cooldowns:
        return
    now = time.time()
    expired = [k for k, t in cooldowns.items()
               if now - t > KARMA_COOLDOWN + 3600]
    for k in expired:
        del cooldowns[k]


@module.interval(7200)
def cooldown_cleanup(bot):
    cleanup_cooldowns(bot)
    _cleanup_flood_events(bot)


# ──────────────────────────────────────────────────────────────
# Karma Flood Protection — Helpers
# ──────────────────────────────────────────────────────────────

def _flood_enabled(bot, channel):
    """Check if karma flood protection is enabled for this channel."""
    disabled = bot.db.get_plugin_value('karma', 'flood_disabled_channels') or []
    return str(channel).lower() not in disabled


def _flood_settings(bot, channel):
    """Get flood protection settings for a channel, with defaults."""
    chan = str(channel).lower()
    custom = bot.memory.get('karma_flood_settings', {}).get(chan, {})
    return {**KARMA_FLOOD_DEFAULTS, **custom}


def _flood_format_duration(seconds):
    """Format seconds into a human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    s = seconds % 60
    if seconds < 3600:
        return f"{m}m {s}s" if s else f"{m}m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _flood_bot_has_op(bot, channel):
    """Check if the bot has operator status in a channel."""
    chan = str(channel)
    if chan in bot.channels:
        privs = bot.channels[chan].privileges.get(bot.nick, 0)
        return bool(privs & plugin.OP)
    return False


# --- Persisted temp-ban expiries (survive restarts) -------------------
# Stored in the bot DB as plugin value (karma, 'pending_unbans'):
#   {"<chan_lower> <banmask>": {"channel", "mask", "expires", "extra"}}
_UNBAN_DB_KEY = 'pending_unbans'
_unban_persist_lock = threading.Lock()
_RESTORE_MIN_DELAY = 60   # overdue unbans run this long after startup
                          # (gives the bot time to reconnect and get ops)


def _persist_unban(bot, channel, banmask, expires, extra=None):
    """Record (expires=epoch) or forget (expires=None) a pending unban."""
    k = f"{str(channel).lower()} {banmask}"
    try:
        with _unban_persist_lock:
            data = bot.db.get_plugin_value('karma', _UNBAN_DB_KEY) or {}
            if expires is None:
                if k not in data:
                    return
                data.pop(k, None)
            else:
                data[k] = {'channel': str(channel), 'mask': banmask,
                           'expires': float(expires), 'extra': extra}
            bot.db.set_plugin_value('karma', _UNBAN_DB_KEY, data)
    except Exception:
        LOGGER.exception("Karma flood: failed to persist pending unban")


def _restore_unbans(bot):
    """Reschedule unbans saved by a previous run."""
    try:
        with _unban_persist_lock:
            data = bot.db.get_plugin_value('karma', _UNBAN_DB_KEY) or {}
    except Exception:
        LOGGER.exception("Karma flood: failed to load pending unbans")
        return
    now = time.time()
    for entry in list(data.values()):
        try:
            delay = max(_RESTORE_MIN_DELAY, float(entry['expires']) - now)
            _schedule_karma_unban(bot, entry['channel'], entry['mask'], entry.get('extra') or '', delay)
            if entry.get('extra'):
                bot.memory['karma_flood_banned_hosts'].add(entry['extra'])
        except Exception:
            LOGGER.exception("Karma flood: bad pending unban entry")
    if data:
        LOGGER.info("Karma flood: restored %d pending unban(s)", len(data))



def _schedule_karma_unban(bot, channel, banmask, giver_host, duration):
    """Schedule an automatic unban after duration seconds."""
    if duration <= 0:
        return

    key = (str(channel).lower(), banmask)

    # Cancel any existing timer for this mask
    old = bot.memory['karma_flood_unbans'].get(key)
    if old:
        old.cancel()

    def _do_unban():
        bot.write(['MODE', channel, '-b', banmask])
        bot.memory['karma_flood_unbans'].pop(key, None)
        _persist_unban(bot, channel, banmask, None)
        bot.memory['karma_flood_banned_hosts'].discard(giver_host)
        LOGGER.info("Karma flood: Auto-unbanned %s in %s", banmask, channel)

    timer = threading.Timer(duration, _do_unban)
    timer.daemon = True
    timer.start()
    bot.memory['karma_flood_unbans'][key] = timer
    _persist_unban(bot, channel, banmask, time.time() + duration, giver_host)


def _can_flood_announce(bot, channel):
    """Rate-limit flood announcements to avoid self-flooding."""
    now = time.time()
    chan = str(channel).lower()
    last = bot.memory['karma_flood_last_announce'].get(chan, 0)
    if now - last < KARMA_FLOOD_ANNOUNCE_CD:
        return False
    bot.memory['karma_flood_last_announce'][chan] = now
    return True


def _record_flood_event(bot, target, channel, giver_host, direction):
    """Record a karma event for flood tracking. Returns the event dict."""
    key = (str(target).lower(), str(channel).lower())
    events = bot.memory['karma_flood_events'].setdefault(key, deque(maxlen=200))
    event = {
        'time': time.time(),
        'giver_host': giver_host,
        'direction': direction,
        'applied': False,
    }
    events.append(event)
    return event


def _check_karma_flood(bot, target, channel, direction):
    """Check if the target is being karma-flooded.

    Returns (is_flood, recent_events) where recent_events are all events
    in the window targeting this nick with the same direction.
    """
    settings = _flood_settings(bot, channel)
    window = settings['window']
    threshold = settings['threshold']

    key = (str(target).lower(), str(channel).lower())
    events = bot.memory['karma_flood_events'].get(key, deque())

    now = time.time()
    cutoff = now - window

    # Events within the window going the same direction
    recent = [e for e in events if e['time'] >= cutoff and e['direction'] == direction]

    # Count unique giver hostmasks
    unique_givers = set(e['giver_host'] for e in recent)

    if len(unique_givers) >= threshold:
        return True, recent

    return False, []


def _handle_karma_flood(bot, target, channel, trigger, flood_events):
    """Handle a detected karma flood — ban attackers, rollback karma, announce."""
    chan = str(channel)
    settings = _flood_settings(bot, channel)
    ban_dur = settings['ban_duration']

    # Current attacker identity
    current_host = f"{trigger.user or '*'}@{trigger.host or '*'}"

    # Collect all unique attacker hostmasks from flood events + current
    all_hosts = set(e['giver_host'] for e in flood_events)
    all_hosts.add(current_host)

    if _flood_bot_has_op(bot, chan):
        # Ban the current attacker first
        current_host_part = trigger.host or '*'
        current_banmask = f"*!*@{current_host_part}"
        bot.write(['MODE', chan, '+b', current_banmask])
        _schedule_karma_unban(bot, chan, current_banmask, current_host, ban_dur)

        # Ban any earlier attackers from this flood not yet banned
        for host_entry in all_hosts:
            if host_entry == current_host:
                continue  # Already handled above
            if host_entry in bot.memory['karma_flood_banned_hosts']:
                continue  # Already banned from a previous detection
            host_part = host_entry.split('@', 1)[-1] if '@' in host_entry else host_entry
            banmask = f"*!*@{host_part}"
            bot.write(['MODE', chan, '+b', banmask])
            _schedule_karma_unban(bot, chan, banmask, host_entry, ban_dur)

        # Mark all hosts as banned
        for host_entry in all_hosts:
            bot.memory['karma_flood_banned_hosts'].add(host_entry)

        # Kick the current attacker (the one whose trigger we have)
        reason = f"Karma flood protection \u2014 banned {_flood_format_duration(ban_dur)}"
        bot.write(['KICK', chan, str(trigger.nick), f':{reason}'])
    else:
        # No op — can't ban/kick, but still block karma and rollback
        for host_entry in all_hosts:
            bot.memory['karma_flood_banned_hosts'].add(host_entry)

    # Rollback all applied karma damage from the flood window
    rolled_back = 0
    target_str = str(target)
    for event in flood_events:
        if event.get('applied'):
            reverse_delta = 1 if event['direction'] == '--' else -1
            new_global = get_karma(bot.db, target_str) + reverse_delta
            set_karma(bot.db, target_str, new_global)
            add_channel_karma(bot.db, target_str, chan, reverse_delta)
            event['applied'] = False
            rolled_back += 1

    # Get restored karma values for the announcement
    restored_global = get_karma(bot.db, target_str)
    restored_chan = get_channel_karma(bot.db, target_str, chan)

    # Announce (rate-limited)
    unique_attackers = len(all_hosts)
    if _can_flood_announce(bot, chan):
        parts = [f"\U0001f6e1\ufe0f Karma flood detected \u2014 protecting {target}."]
        if rolled_back:
            parts.append(
                f"Rolled back {rolled_back} karma change{'s' if rolled_back != 1 else ''} "
                f"(\U0001f3af {restored_chan} in {chan} | \U0001f310 {restored_global} global)."
            )
        parts.append(
            f"Banned {unique_attackers} attacker{'s' if unique_attackers != 1 else ''} "
            f"for {_flood_format_duration(ban_dur)}."
        )
        bot.say(" ".join(parts), chan)

    LOGGER.info(
        "Karma flood: protected %s in %s — rolled back %d, %d attackers banned",
        target, chan, rolled_back, unique_attackers,
    )


def _cleanup_flood_events(bot):
    """Prune stale flood event entries (called periodically)."""
    flood_events = bot.memory.get('karma_flood_events')
    if not flood_events:
        return
    cutoff = time.time() - 300  # keep 5 minutes of history
    empty_keys = []
    for key, events in flood_events.items():
        while events and events[0]['time'] < cutoff:
            events.popleft()
        if not events:
            empty_keys.append(key)
    for key in empty_keys:
        del flood_events[key]


# ──────────────────────────────────────────────────────────────
# ++ / -- handler
# ──────────────────────────────────────────────────────────────
@module.rule(r'^\s*(?:[a-zA-Z0-9_\[\]\\^`{\}\|-]|[^\s\+\-][^\s]*?[a-zA-Z0-9_\[\]\\^`{\}\|-])(?:\+\+|--)\s*$')
def karma_increment_decrement(bot, trigger):
    """Give or take karma inline: type nick++ (+1) or nick-- (-1) as the whole message in a channel.
    Channel only (not in PM); you can't karma yourself; 10-minute cooldown per giver per channel."""
    if trigger.is_privmsg:
        return bot.reply(PRIVATE_KARMA_MESSAGE)

    # Ignore messages from the bot itself to avoid self-triggering
    try:
        if trigger.nick and str(trigger.nick).lower() == str(bot.nick).lower():
            return
    except Exception:
        pass

    # Quick reject: already-banned karma-flood hostmask (silent drop)
    giver_host = f"{trigger.user or '*'}@{trigger.host or '*'}"
    if giver_host in bot.memory.get('karma_flood_banned_hosts', set()):
        return

    # Find all karma patterns in the message (capture nick + sign pair)
    # We'll strip surrounding punctuation from the captured nick below.
    matches = re.findall(
        r'^\s*([a-zA-Z0-9_\[\]\\^`{\}\|-]|[^\s\+\-][^\s]*?[a-zA-Z0-9_\[\]\\^`{\}\|-])(\+\+|--)\s*$',
        trigger.group(0)
    )
    if not matches:
        return

    giver_id = tools.Identifier(trigger.nick)
    chan_id = tools.Identifier(trigger.sender)

    # Check cooldown
    cooldown_key = (giver_id, chan_id)
    cooldowns = get_cooldowns(bot)
    now = time.time()
    last = cooldowns.get(cooldown_key, 0)
    remaining = KARMA_COOLDOWN - (now - last)
    if remaining > 0:
        bot.say(
            COOLDOWN_NOTICE.format(time=format_time_remaining(remaining)),
            trigger.nick,
        )
        return

    processed_targets = set()
    karma_applied = False
    for target_raw, sign in matches:
        # Resolve target nick: if the raw target is present in the channel, keep it.
        # Otherwise, try stripping common surrounding punctuation.
        target_raw_id = tools.Identifier(target_raw)
        if chan_id in bot.channels and target_raw_id in bot.channels[chan_id].users:
            target = target_raw
            target_id = target_raw_id
        else:
            target = target_raw.strip('()[]{}<>"\',:;.!?')
            target_id = tools.Identifier(target)

        # Block abusive/hate speech targets
        if is_forbidden(target):
            continue

        # Only allow karma for nicks actually present in the channel
        if chan_id in bot.channels and target_id not in bot.channels[chan_id].users:
            bot.say(f"⚠️ {target} isn't in the channel — karma only counts for real users here.")
            continue

        # Skip if already processed this target in the message
        if target_id in processed_targets:
            continue
        processed_targets.add(target_id)

        # Self-karma block
        if target_id == giver_id:
            continue

        try:
            if bot.db.get_nick_id(target_id) == bot.db.get_nick_id(giver_id):
                continue
        except ValueError:
            pass

        # ── Karma flood check ──
        flood_event = None
        if _flood_enabled(bot, chan_id):
            flood_event = _record_flood_event(
                bot, target_id, chan_id, giver_host, sign
            )
            is_flood, flood_events = _check_karma_flood(
                bot, target_id, chan_id, sign
            )
            if is_flood:
                _handle_karma_flood(
                    bot, target_id, chan_id, trigger, flood_events
                )
                return  # Block all karma from this message

        # Apply karma (sign is '++' or '--' from the new regex)
        delta = 1 if sign == '++' else -1
        new_global = get_karma(bot.db, target) + delta
        set_karma(bot.db, target, new_global)
        karma_applied = True

        # Mark flood event as applied (for potential rollback later)
        if flood_event:
            flood_event['applied'] = True

        # Friendly message bits
        if delta > 0:
            action_verb = "gained"
            mood = "✨"
            lead = "🆙 Karma boost!"
        else:
            action_verb = "lost"
            mood = "😬"
            lead = "⬇ Karma drop..."

        add_channel_karma(bot.db, target, trigger.sender, delta)
        chan_karma = get_channel_karma(bot.db, target, trigger.sender)
        bot.say(
            f"{lead} {target} {action_verb} {abs(delta)} karma {mood} "
            f"(🎯 {chan_karma} in {trigger.sender} | 🌐 {new_global} global)"
        )

    # Only consume cooldown if karma was actually applied
    if karma_applied:
        cooldowns[cooldown_key] = now


# ──────────────────────────────────────────────────────────────
# Inline == (foo ==)
# ──────────────────────────────────────────────────────────────
@module.rule(r'^\S+\s*==\s*$')
def karma_show_inline(bot, trigger):
    """Check someone's global karma inline: type nick== as the whole message."""
    if trigger.nick and str(trigger.nick).lower() == str(bot.nick).lower():
        return
    target = trigger.group(0).split('==', 1)[0].strip()
    if is_forbidden(target):
        return
    karma = get_karma(bot.db, target)
    bot.say(f"📊 {target} == {karma} karma globally")


# ──────────────────────────────────────────────────────────────
# .karma — stats or command list (PM)
# ──────────────────────────────────────────────────────────────
@module.commands('karma')
def cmd_karma(bot, trigger):
    """
    .karma <nick> → show channel + global karma
    .karma        → PM full command list + cooldown info (multi-line)
    """
    args = (trigger.group(2) or "").strip()

    # No arguments → send help via multi-line PM
    if not args:
        lines = [
            "🤖 Karma Help:",
            "• nick++ / nick-- — Give or remove karma (10-minute per-channel cooldown)",
            "• .karma [nick] — Show channel karma + global karma",
            "• .channeltop / .ctop — Top karma holders in this channel",
            "• .channelbottom / .cbottom — Lowest karma in this channel",
            "• .karmatop / .ktop — Global top karma",
            "• .karmabottom / .kbottom — Global lowest karma",
            "• .setkarma <nick> <value> — OP-only",
            "",
            "⏳ Cooldown: You may give karma once every 10 minutes *per channel*.",
        ]

        for line in lines:
            bot.say(line, trigger.nick)
        return

    # Display karma stats
    target = args.split()[0]
    if is_forbidden(target):
        return
    global_karma = get_karma(bot.db, target)

    if trigger.sender.startswith('#'):
        channel = trigger.sender
        channel_karma = get_channel_karma(bot.db, target, channel)
        bot.say(
            f"📊 {target}: 🎯 {channel_karma} in {channel} | "
            f"🌐 {global_karma} global"
        )
    else:
        bot.say(f"📊 {target} has 🌐 {global_karma} karma globally.")


# ──────────────────────────────────────────────────────────────
# Global leaderboards
# ──────────────────────────────────────────────────────────────
def _global_leaderboard(bot, trigger, descending=True, default_limit=5):
    arg = trigger.group(2)
    limit = default_limit
    if arg and arg.isdigit():
        limit = max(1, min(50, int(arg)))
    order = "DESC" if descending else "ASC"

    # Over-fetch to account for any forbidden entries that get filtered out
    fetch_limit = limit + len(FORBIDDEN_KARMA_TARGETS)

    query = text(f"""
        SELECT COALESCE(nicknames.canonical, nicknames.slug) AS nick,
               CAST(REPLACE(nick_values.value, '"', '') AS INTEGER) AS karma
        FROM nick_values
        JOIN nicknames ON nick_values.nick_id = nicknames.nick_id
        WHERE nick_values.key = 'karma'
        ORDER BY karma {order}
        LIMIT :limit
    """)

    with bot.db.engine.connect() as conn:
        results = conn.execute(query, {"limit": fetch_limit}).fetchall()

    # Filter out forbidden targets, then trim to requested limit
    results = [r for r in results if not is_forbidden(r[0])][:limit]

    if not results:
        return bot.say("🏆 No karma recorded yet.")

    line = " | ".join(f"{nick} == {karma}" for nick, karma in results)
    bot.say(f"🏆 Global karma leaderboard: {line}")


@module.commands('karmatop', 'ktop')
def karmatop(bot, trigger):
    _global_leaderboard(bot, trigger, True, 5)


@module.commands('karmabottom', 'kbottom')
def karmabottom(bot, trigger):
    _global_leaderboard(bot, trigger, False, 5)


# ──────────────────────────────────────────────────────────────
# Channel leaderboards
# ──────────────────────────────────────────────────────────────
def _channel_leaderboard(bot, trigger, descending=True, default_limit=10):
    if not trigger.sender.startswith('#'):
        return bot.say("Channel leaderboards only work in channels. 📺")

    arg = trigger.group(2)
    limit = default_limit
    if arg and arg.isdigit():
        limit = max(1, min(50, int(arg)))
    order = "DESC" if descending else "ASC"

    chan_key = f'karma_channel_{tools.Identifier(trigger.sender)}'

    query = text(f"""
        SELECT COALESCE(nicknames.canonical, nicknames.slug) AS nick,
               CAST(REPLACE(nick_values.value, '"', '') AS INTEGER) AS karma
        FROM nick_values
        JOIN nicknames ON nick_values.nick_id = nicknames.nick_id
        WHERE nick_values.key = :chan_key
        ORDER BY karma {order}
        LIMIT :limit
    """)

    # Over-fetch to account for any forbidden entries that get filtered out
    fetch_limit = limit + len(FORBIDDEN_KARMA_TARGETS)

    with bot.db.engine.connect() as conn:
        results = conn.execute(
            query,
            {"chan_key": chan_key, "limit": fetch_limit},
        ).fetchall()

    # Filter out forbidden targets, then trim to requested limit
    results = [r for r in results if not is_forbidden(r[0])][:limit]

    if not results:
        return bot.say(f"🏆 No karma recorded in {trigger.sender} yet.")

    line = " | ".join(f"{nick} == {karma}" for nick, karma in results)
    bot.say(f"🏆 Karma in {trigger.sender}: {line}")


@module.commands('channeltop', 'ctop')
def channel_top(bot, trigger):
    _channel_leaderboard(bot, trigger, True, 10)


@module.commands('channelbottom', 'cbottom')
def channel_bottom(bot, trigger):
    _channel_leaderboard(bot, trigger, False, 10)


# ──────────────────────────────────────────────────────────────
# .setkarma (OP only)
# ──────────────────────────────────────────────────────────────
@module.commands('setkarma')
@module.require_privilege(module.OP)
@module.require_chanmsg("Setting karma works only in channels.")
def setkarma(bot, trigger):
    text_arg = trigger.group(2)
    if not text_arg:
        return bot.reply("Usage: .setkarma <nick> <value>")

    parts = text_arg.strip().split(None, 2)
    if len(parts) != 2:
        return bot.reply("Usage: .setkarma <nick> <integer>")

    target, value_str = parts
    if is_forbidden(target):
        return bot.reply("That target is not allowed. 🚫")
    try:
        value = int(value_str)
    except ValueError:
        return bot.reply("Karma value must be an integer. 🔢")

    set_karma(bot.db, target, value)
    bot.say(f"🛠️ {target}'s karma has been set to {value}.")


# ──────────────────────────────────────────────────────────────
# $karmaflood — Admin command for flood protection
# ──────────────────────────────────────────────────────────────
@module.commands('karmaflood')
@module.require_chanmsg("This command works only in channels.")
def cmd_karmaflood(bot, trigger):
    """Toggle or configure karma flood protection for this channel."""
    chan = str(trigger.sender)
    chan_lower = chan.lower()

    # Require halfop+ or bot owner
    privs = 0
    if chan in bot.channels:
        privs = bot.channels[chan].privileges.get(trigger.nick, 0)
    is_owner = False
    try:
        is_owner = str(trigger.nick).lower() == str(bot.config.core.owner).lower()
    except Exception:
        pass
    if not (privs >= plugin.HALFOP or is_owner):
        return bot.reply("⚠️ Requires halfop (+h) or above.")

    args = (trigger.group(2) or '').strip().lower().split()

    if not args:
        enabled = _flood_enabled(bot, chan)
        settings = _flood_settings(bot, chan)
        status = "✅ ON" if enabled else "❌ OFF"
        bot.say(
            f"🛡️ Karma flood protection: {status} │ "
            f"Window: {settings['window']}s │ "
            f"Threshold: {settings['threshold']} unique givers │ "
            f"Ban: {_flood_format_duration(settings['ban_duration'])}"
        )
        return

    action = args[0]

    if action == 'on':
        disabled = bot.db.get_plugin_value('karma', 'flood_disabled_channels') or []
        if chan_lower in disabled:
            disabled.remove(chan_lower)
            bot.db.set_plugin_value('karma', 'flood_disabled_channels', disabled)
        bot.say("🛡️ Karma flood protection: ✅ enabled")
        return

    if action == 'off':
        disabled = bot.db.get_plugin_value('karma', 'flood_disabled_channels') or []
        if chan_lower not in disabled:
            disabled.append(chan_lower)
            bot.db.set_plugin_value('karma', 'flood_disabled_channels', disabled)
        bot.say("🛡️ Karma flood protection: ❌ disabled")
        return

    if action == 'set' and len(args) >= 3:
        param = args[1]
        try:
            value = int(args[2])
        except ValueError:
            return bot.reply("⚠️ Value must be a number.")

        valid_params = {
            'window': (10, 300),
            'threshold': (2, 20),
            'ban_duration': (60, 86400),
        }
        if param not in valid_params:
            return bot.reply(
                f"⚠️ Valid params: {', '.join(valid_params.keys())}"
            )

        lo, hi = valid_params[param]
        if not (lo <= value <= hi):
            return bot.reply(f"⚠️ {param} must be between {lo} and {hi}.")

        overrides = bot.db.get_plugin_value('karma', 'flood_settings') or {}
        chan_settings = overrides.get(chan_lower, {})
        chan_settings[param] = value
        overrides[chan_lower] = chan_settings
        bot.db.set_plugin_value('karma', 'flood_settings', overrides)
        bot.memory.setdefault('karma_flood_settings', {})[chan_lower] = chan_settings
        bot.say(f"🛡️ Karma flood {param} → {value} for {chan}")
        return

    bot.reply(
        "Usage: $karmaflood [on|off] │ "
        "$karmaflood set <window|threshold|ban_duration> <value>"
    )


# ──────────────────────────────────────────────────────────────
# Shutdown — clean up timers
# ──────────────────────────────────────────────────────────────
def shutdown(bot):
    """Cancel all pending karma flood unban timers on shutdown."""
    for timer in bot.memory.get('karma_flood_unbans', {}).values():
        timer.cancel()

