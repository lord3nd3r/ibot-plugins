"""
antiflood.py - Join/Part Flood Protection for ibot
Detects and bans users who rejoin a channel too many times in a short window,
catching quit/rejoin cycling and join/part flooding.

Author: Kristopher Craig
Commands:
    $flood                      - Show status for the current channel
    $flood on / off             - Enable/disable in the current channel
    $flood set <param> <val>    - Adjust window, threshold, duration, or banmask
    $flood whitelist ...        - Manage exempted hostmasks
    $flood stats                - Show recent ban actions
    $flood top                  - Show top 5 most-kicked users in this channel
    $flood help                 - Show help via NOTICE

Config (sopel.cfg):
    [antiflood]
    window = 300            # seconds (5 minutes)
    threshold = 3           # joins within window to trigger ban
    ban_duration = 600      # auto-unban after N seconds (0 = permanent)
    banmask_style = host    # host = *!*@host, ident = *!user@host
    exempt_modes = vho      # skip users with +v, +h, or +o
    enabled = true          # global enable/disable
"""

from sopel import plugin
from sopel.config.types import StaticSection, ValidatedAttribute, BooleanAttribute
import fnmatch
import logging
import re
import threading
import time

LOGGER = logging.getLogger(__name__)

B = "\x02"
COLOR_RESET = "\x03"
# \x0f resets color. A bare \x03 eats the next digits, so a count shows up blank.
SEP = "\x0314 · \x0f"

# Maximum announcements per channel within this cooldown (seconds)
_ANNOUNCE_COOLDOWN = 5

# Grace period (seconds) after a bot-initiated kick/ban during which
# flood events from that hostmask are ignored, preventing feedback loops.
_KICK_GRACE_PERIOD = 60


# ========================= CONFIG =========================

class AntiFloodSection(StaticSection):
    """Configuration section for antiflood protection."""
    window = ValidatedAttribute('window', int, default=300)
    """Time window in seconds to track join events."""
    threshold = ValidatedAttribute('threshold', int, default=3)
    """Number of joins within the window to trigger action."""
    ban_duration = ValidatedAttribute('ban_duration', int, default=600)
    """Auto-unban after this many seconds. 0 = permanent."""
    banmask_style = ValidatedAttribute('banmask_style', default='host')
    """Banmask style: 'host' = *!*@host, 'ident' = *!user@host."""
    exempt_modes = ValidatedAttribute('exempt_modes', default='vhoaq')
    """Mode chars whose holders are exempt (v=voice, h=halfop, o=op)."""
    enabled = BooleanAttribute('enabled', default=True)
    """Global enable/disable switch."""


# ========================= SETUP / SHUTDOWN =========================

def setup(bot):
    """Initialize the antiflood plugin."""
    bot.config.define_section('antiflood', AntiFloodSection)
    bot.memory['flood_events'] = {}           # (channel, hostmask) -> [timestamps]
    bot.memory['flood_lock'] = threading.Lock()
    bot.memory['flood_pending_unbans'] = {}   # (channel, banmask) -> Timer
    bot.memory['flood_last_announce'] = {}    # channel -> timestamp
    bot.memory['flood_stats'] = []            # list of recent action dicts
    bot.memory['flood_recent_kicks'] = {}     # (channel, hostmask) -> timestamp
    _restore_unbans(bot)
    LOGGER.info("Antiflood protection initialized")


def shutdown(bot):
    """Cancel pending unban timers on shutdown."""
    for timer in bot.memory.get('flood_pending_unbans', {}).values():
        timer.cancel()
    LOGGER.info("Antiflood protection shutdown")


# ========================= SETTINGS HELPERS =========================

def _get_settings(bot):
    """Get antiflood settings — runtime DB overrides take priority over config."""
    defaults = {
        'window': bot.config.antiflood.window,
        'threshold': bot.config.antiflood.threshold,
        'ban_duration': bot.config.antiflood.ban_duration,
        'banmask_style': bot.config.antiflood.banmask_style,
    }
    overrides = bot.db.get_plugin_value('antiflood', 'settings') or {}
    defaults.update({k: v for k, v in overrides.items() if k in defaults})
    return defaults


def _save_setting(bot, key, value):
    """Persist a runtime setting override to the DB."""
    overrides = bot.db.get_plugin_value('antiflood', 'settings') or {}
    overrides[key] = value
    bot.db.set_plugin_value('antiflood', 'settings', overrides)


def _is_channel_enabled(bot, channel):
    """Check if antiflood is enabled for a specific channel.

    Off by default — channels must be explicitly enabled.
    """
    enabled = bot.db.get_plugin_value('antiflood', 'enabled_channels') or []
    return channel.lower() in [c.lower() for c in enabled]


def _whitelist_entries(bot, channel):
    """Nick and hostmask exemptions from both flood scripts.

    $jpq whitelist and $flood whitelist protect against either ban.
    """
    channel = str(channel).lower()
    entries = []
    for plugin_name in ('jpq', 'antiflood'):
        entries.extend(bot.db.get_plugin_value(plugin_name, f'whitelist_{channel}') or [])
    return entries


def _entry_matches(entry, nick, hostmask):
    """True when a whitelist entry is this nick or this user@host.

    A bare nick matches the nickname. A mask matches user@host, including
    globs such as *!*@cloak.
    """
    entry = str(entry).strip().lower()
    if not entry:
        return False
    nick_l = str(nick or '').lower()
    hostmask_l = str(hostmask or '').lower()
    user, sep, host = hostmask_l.partition('@')
    if not sep:
        user, host = '*', hostmask_l or '*'
    exact = {
        nick_l,
        hostmask_l,
        f"{nick_l}!{user}@{host}",
        f"*!{user}@{host}",
        f"*!*@{host}",
        f"*@{host}",
    }
    if entry in exact:
        return True
    if any(ch in entry for ch in '*?') or '@' in entry:
        for candidate in (f"{nick_l}!{user}@{host}", f"*!{user}@{host}", f"*!*@{host}", hostmask_l):
            if fnmatch.fnmatch(candidate, entry):
                return True
    return False


def _is_whitelisted(bot, channel, hostmask, nick=''):
    """Check if a nick or hostmask is whitelisted in a channel."""
    for entry in _whitelist_entries(bot, channel):
        if _entry_matches(entry, nick, hostmask):
            return True
    return False


def _host_for_nick(bot, nick):
    """Return the host Sopel has seen for nick, if any."""
    users = getattr(bot, 'users', None) or {}
    user = users.get(nick)
    if user is None:
        nick_l = str(nick).lower()
        for key, obj in users.items():
            if str(key).lower() == nick_l:
                user = obj
                break
    host = getattr(user, 'host', None) if user else None
    if host and str(host) not in ('*', ''):
        return str(host)
    return None


def _is_ignored(bot, nick, host):
    """Check if the nick or host matches the bot's built-in block lists."""
    nick_blocks = getattr(bot.config.core, 'nick_blocks', None) or []
    for pattern in nick_blocks:
        try:
            if re.match(pattern, nick, re.IGNORECASE):
                return True
        except re.error:
            pass

    host_blocks = getattr(bot.config.core, 'host_blocks', None) or []
    for pattern in host_blocks:
        try:
            if re.match(pattern, host, re.IGNORECASE):
                return True
        except re.error:
            pass

    return False


def _is_exempt(bot, nick, channel):
    """Check if a user holds an exempt channel mode (+v, +h, +o, etc.)."""
    exempt = bot.config.antiflood.exempt_modes or ''
    chan = str(channel)

    if chan not in bot.channels:
        return False

    privs = bot.channels[chan].privileges.get(nick, 0)
    if not privs:
        return False

    mode_bits = {
        'v': plugin.VOICE,
        'h': plugin.HALFOP,
        'o': plugin.OP,
        'a': plugin.ADMIN if hasattr(plugin, 'ADMIN') else 0,
        'q': plugin.OWNER if hasattr(plugin, 'OWNER') else 0,
    }

    for char in exempt:
        bit = mode_bits.get(char, 0)
        if bit and (privs & bit):
            return True

    return False


# ========================= IRC HELPERS =========================

def _get_hostmask(trigger):
    """Build a tracking identity from user@host."""
    user = trigger.user or '*'
    host = trigger.host or '*'
    return f"{user}@{host}"


def _get_banmask(trigger, style='host'):
    """Build a ban mask from the trigger.

    Styles:
        host  -> *!*@host   (broader, catches ident changes)
        ident -> *!user@host (narrower, preserves ident)
    """
    host = trigger.host or '*'
    if style == 'ident':
        user = trigger.user or '*'
        return f"*!{user}@{host}"
    return f"*!*@{host}"


def _bot_has_op(bot, channel):
    """Check if the bot has operator status in a channel."""
    chan = str(channel)
    if chan in bot.channels:
        privs = bot.channels[chan].privileges.get(bot.nick, 0)
        return bool(privs & plugin.OP)
    return False


def _can_announce(bot, channel):
    """Rate-limit bot announcements to avoid self-flooding."""
    now = time.time()
    last = bot.memory['flood_last_announce'].get(channel, 0)
    if now - last < _ANNOUNCE_COOLDOWN:
        return False
    bot.memory['flood_last_announce'][channel] = now
    return True


# --- Persisted temp-ban expiries (survive restarts) -------------------
# Stored in the bot DB as plugin value (antiflood, 'pending_unbans'):
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
            data = bot.db.get_plugin_value('antiflood', _UNBAN_DB_KEY) or {}
            if expires is None:
                if k not in data:
                    return
                data.pop(k, None)
            else:
                data[k] = {'channel': str(channel), 'mask': banmask,
                           'expires': float(expires), 'extra': extra}
            bot.db.set_plugin_value('antiflood', _UNBAN_DB_KEY, data)
    except Exception:
        LOGGER.exception("Antiflood: failed to persist pending unban")


def _restore_unbans(bot):
    """Reschedule unbans saved by a previous run."""
    try:
        with _unban_persist_lock:
            data = bot.db.get_plugin_value('antiflood', _UNBAN_DB_KEY) or {}
    except Exception:
        LOGGER.exception("Antiflood: failed to load pending unbans")
        return
    now = time.time()
    for entry in list(data.values()):
        try:
            delay = max(_RESTORE_MIN_DELAY, float(entry['expires']) - now)
            _schedule_unban(bot, entry['channel'], entry['mask'], delay)
        except Exception:
            LOGGER.exception("Antiflood: bad pending unban entry")
    if data:
        LOGGER.info("Antiflood: restored %d pending unban(s)", len(data))



def _schedule_unban(bot, channel, banmask, duration):
    """Schedule an automatic unban after duration seconds."""
    if duration <= 0:
        return

    key = (channel.lower(), banmask)

    # Cancel any existing timer for this mask
    old = bot.memory['flood_pending_unbans'].get(key)
    if old:
        old.cancel()

    def _do_unban():
        bot.write(['MODE', channel, '-b', banmask])
        bot.memory['flood_pending_unbans'].pop(key, None)
        _persist_unban(bot, channel, banmask, None)
        LOGGER.info("Antiflood: Auto-unbanned %s in %s", banmask, channel)

    timer = threading.Timer(duration, _do_unban)
    timer.daemon = True
    timer.start()
    bot.memory['flood_pending_unbans'][key] = timer
    _persist_unban(bot, channel, banmask, time.time() + duration, None)


def _log_action(bot, nick, channel, banmask, count, window):
    """Record an action for the $flood stats command."""
    entry = {
        'nick': str(nick),
        'channel': str(channel),
        'banmask': banmask,
        'count': count,
        'window': window,
        'time': time.time(),
    }
    stats = bot.memory['flood_stats']
    stats.append(entry)
    # Keep only the last 25 actions in memory
    if len(stats) > 25:
        bot.memory['flood_stats'] = stats[-25:]


def _increment_kick_count(bot, channel, nick, hostmask):
    """Increment the persistent kick count for a user in a channel (for floodtop)."""
    channel_lower = str(channel).lower()
    db_key = f'floodtop_{channel_lower}'
    top = bot.db.get_plugin_value('antiflood', db_key) or {}

    # Key by hostmask so nick changes don't split counts; store latest nick
    if hostmask not in top:
        top[hostmask] = {'nick': str(nick), 'count': 0}
    top[hostmask]['nick'] = str(nick)  # update to latest nick
    top[hostmask]['count'] += 1

    bot.db.set_plugin_value('antiflood', db_key, top)


def _format_duration(seconds):
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m{s}s" if s else f"{m}m"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m}m" if m else f"{h}h"


# ========================= CORE LOGIC =========================

def _record_event(bot, nick, channel, hostmask, trigger):
    """Record a join event and take action if the threshold is exceeded."""
    channel = str(channel).lower()

    if not _is_channel_enabled(bot, channel):
        return
    if nick.lower() == bot.nick.lower():
        return
    if _is_whitelisted(bot, channel, hostmask, nick):
        return
    if _is_ignored(bot, str(nick), trigger.host or ''):
        return
    if _is_exempt(bot, nick, channel):
        return

    # Skip events from users the bot recently kicked (grace period)
    kick_key = (channel, hostmask)
    kick_time = bot.memory['flood_recent_kicks'].get(kick_key)
    if kick_time and (time.time() - kick_time) < _KICK_GRACE_PERIOD:
        LOGGER.debug(
            "Antiflood: Ignoring event from %s in %s (within kick grace period)",
            hostmask, channel,
        )
        return

    settings = _get_settings(bot)
    now = time.time()
    key = (channel, hostmask)
    triggered = False

    with bot.memory['flood_lock']:
        events = bot.memory['flood_events']

        if key not in events:
            events[key] = []
        events[key].append(now)

        # Prune events outside the window
        cutoff = now - settings['window']
        events[key] = [t for t in events[key] if t > cutoff]
        count = len(events[key])

        if count >= settings['threshold']:
            del events[key]
            triggered = True

    if triggered:
        _take_action(bot, nick, channel, hostmask, trigger, settings, count)


def _take_action(bot, nick, channel, hostmask, trigger, settings, count):
    """Ban and kick a user who triggered flood protection."""
    banmask = _get_banmask(trigger, style=settings.get('banmask_style', 'host'))

    if not _bot_has_op(bot, channel):
        LOGGER.warning(
            "Antiflood: Flood from %s (%s) in %s — bot not opped, cannot act",
            nick, hostmask, channel,
        )
        return

    # Record the kick so future events from this hostmask are ignored
    # during the grace period (prevents feedback loops)
    bot.memory['flood_recent_kicks'][(channel, hostmask)] = time.time()

    # Ban first, then kick
    bot.write(['MODE', channel, '+b', banmask])
    dur = settings['ban_duration']
    dur_str = _format_duration(dur) if dur > 0 else "permanent"
    reason = (
        f"Flood protection ({count} joins in {_format_duration(settings['window'])}) "
        f"— banned for {dur_str}"
    )
    bot.write(['KICK', channel, nick, f':{reason}'])

    LOGGER.info(
        "Antiflood: Banned %s (nick: %s) in %s — %d joins in %ds",
        banmask, nick, channel, count, settings['window'],
    )

    # Record for stats and persistent kick count
    _log_action(bot, nick, channel, banmask, count, settings['window'])
    _increment_kick_count(bot, channel, nick, hostmask)

    # Announce in channel (rate-limited)
    if _can_announce(bot, channel):
        ban_note = f" — auto-unban in {B}{dur_str}{B}" if dur > 0 else ""
        bot.say(
            f"🛡️ {B}Flood Detected{B}{SEP}"
            f"Banned {B}{banmask}{B}{SEP}"
            f"{count} joins in {_format_duration(settings['window'])}{ban_note}",
            channel,
        )

    # Schedule auto-unban
    if dur > 0:
        _schedule_unban(bot, channel, banmask, dur)


# ========================= EVENT HANDLERS =========================

@plugin.thread(True)
@plugin.event('JOIN')
@plugin.rule('.*')
def on_join(bot, trigger):
    """Handle JOIN events for flood detection."""
    if trigger.event != 'JOIN':
        return
    if not str(trigger.sender).startswith('#'):
        return

    nick = trigger.nick
    channel = trigger.sender
    hostmask = _get_hostmask(trigger)

    _record_event(bot, nick, channel, hostmask, trigger)


# ========================= PERIODIC CLEANUP =========================

@plugin.thread(True)
@plugin.interval(60)
def cleanup(bot):
    """Prune stale event tracking entries and expired kick grace records."""
    now = time.time()
    settings = _get_settings(bot)
    cutoff = now - settings['window']

    with bot.memory['flood_lock']:
        events = bot.memory['flood_events']
        stale = []
        for key, timestamps in events.items():
            events[key] = [t for t in timestamps if t > cutoff]
            if not events[key]:
                stale.append(key)
        for key in stale:
            del events[key]

    if stale:
        LOGGER.debug("Antiflood: Pruned %d stale tracking entries", len(stale))

    # Prune expired kick grace records
    kicks = bot.memory.get('flood_recent_kicks', {})
    expired = [k for k, t in kicks.items() if (now - t) >= _KICK_GRACE_PERIOD]
    for k in expired:
        del kicks[k]
    if expired:
        LOGGER.debug("Antiflood: Pruned %d expired kick grace entries", len(expired))


# ========================= ADMIN COMMANDS =========================

@plugin.command('flood')
@plugin.require_admin('You need to be a bot admin to manage antiflood.')
def cmd_flood(bot, trigger):
    """$flood <status|on|off|set|whitelist|stats|top|help> — Manage antiflood protection."""
    if trigger.is_privmsg:
        return bot.reply("This command must be used in a channel.")

    args = (trigger.group(2) or '').strip().split()
    subcmd = args[0].lower() if args else 'status'

    dispatch = {
        'status':    lambda: _cmd_status(bot, trigger),
        'on':        lambda: _cmd_toggle(bot, trigger, enable=True),
        'off':       lambda: _cmd_toggle(bot, trigger, enable=False),
        'set':       lambda: _cmd_set(bot, trigger, args[1:]),
        'whitelist': lambda: _cmd_whitelist(bot, trigger, args[1:]),
        'wl':        lambda: _cmd_whitelist(bot, trigger, args[1:]),
        'stats':     lambda: _cmd_stats(bot, trigger),
        'top':       lambda: _cmd_floodtop(bot, trigger),
        'help':      lambda: _cmd_help(bot, trigger),
    }

    handler = dispatch.get(subcmd)
    if handler:
        handler()
    else:
        bot.reply(
            f"⚠️ Unknown subcommand {B}{subcmd}{B}. "
            f"Try: {B}$flood help{B}"
        )


@plugin.command('floodtop')
@plugin.require_admin('You need to be a bot admin to manage antiflood.')
def cmd_floodtop_alias(bot, trigger):
    """$floodtop — Shortcut for $flood top."""
    if trigger.is_privmsg:
        return bot.reply("This command must be used in a channel.")
    _cmd_floodtop(bot, trigger)


def _cmd_status(bot, trigger):
    """Show antiflood status for the current channel."""
    channel = str(trigger.sender).lower()

    settings = _get_settings(bot)
    enabled = _is_channel_enabled(bot, channel)
    icon = "✅" if enabled else "❌"

    with bot.memory['flood_lock']:
        active = sum(1 for (ch, _) in bot.memory['flood_events'] if ch == channel)

    pending = sum(
        1 for (ch, _) in bot.memory.get('flood_pending_unbans', {}) if ch == channel
    )
    whitelist = bot.db.get_plugin_value('antiflood', f'whitelist_{channel}') or []
    dur = settings['ban_duration']
    dur_str = _format_duration(dur) if dur > 0 else "permanent"
    style_label = "ident (*!user@host)" if settings.get('banmask_style') == 'ident' else "host (*!*@host)"
    window_str = _format_duration(settings['window'])

    bot.say(
        f"🛡️ {B}Antiflood Status{B}{SEP}"
        f"{icon} {'Enabled' if enabled else 'Disabled'}{SEP}"
        f"⏱️ Window: {B}{window_str}{B}{SEP}"
        f"🎯 Threshold: {B}{settings['threshold']}{B} joins{SEP}"
        f"⏳ Ban: {B}{dur_str}{B}{SEP}"
        f"🎭 Mask: {B}{style_label}{B}"
    )

    extras = []
    if active:
        extras.append(f"📊 Tracking: {B}{active}{B} host(s)")
    if pending:
        extras.append(f"🔓 Pending unbans: {B}{pending}{B}")
    if whitelist:
        extras.append(f"📋 Whitelist: {B}{len(whitelist)}{B} entries")
    exempt = bot.config.antiflood.exempt_modes or ''
    if exempt:
        modes = ', '.join(f"+{c}" for c in exempt)
        extras.append(f"🔑 Exempt: {B}{modes}{B}")
    if extras:
        bot.say(SEP.join(extras))


def _cmd_toggle(bot, trigger, enable):
    """Enable or disable antiflood for the current channel."""
    channel = str(trigger.sender).lower()

    enabled_channels = bot.db.get_plugin_value('antiflood', 'enabled_channels') or []

    if enable:
        if channel not in [c.lower() for c in enabled_channels]:
            enabled_channels.append(channel)
        bot.db.set_plugin_value('antiflood', 'enabled_channels', enabled_channels)
        bot.say(f"✅ Antiflood protection {B}enabled{B} for {B}{trigger.sender}{B}")
    else:
        enabled_channels = [c for c in enabled_channels if c.lower() != channel]
        bot.db.set_plugin_value('antiflood', 'enabled_channels', enabled_channels)
        bot.say(f"❌ Antiflood protection {B}disabled{B} for {B}{trigger.sender}{B}")

    LOGGER.info("Antiflood: %s in %s by %s", 'Enabled' if enable else 'Disabled', channel, trigger.nick)


def _cmd_set(bot, trigger, args):
    """Adjust a setting. Usage: $flood set <window|threshold|duration|banmask> <value>"""
    valid_int = {'window': (10, 3600), 'threshold': (2, 50), 'duration': (0, 86400)}
    valid_str = {'banmask': ('host', 'ident')}
    all_params = list(valid_int) + list(valid_str)

    if len(args) < 2:
        bot.reply(
            f"Usage: {B}$flood set{B} <{'|'.join(all_params)}> <value>"
        )
        return

    param = args[0].lower()
    raw_value = args[1].lower()

    # String parameters
    if param in valid_str:
        allowed = valid_str[param]
        if raw_value not in allowed:
            return bot.reply(
                f"⚠️ {B}{param}{B} must be one of: "
                + ", ".join(f"{B}{a}{B}" for a in allowed)
            )
        key = 'banmask_style' if param == 'banmask' else param
        _save_setting(bot, key, raw_value)
        label = "ident (*!user@host)" if raw_value == 'ident' else "host (*!*@host)"
        bot.say(f"✅ Antiflood banmask style set to {B}{label}{B}")
        LOGGER.info("Antiflood: %s → %s by %s", param, raw_value, trigger.nick)
        return

    # Integer parameters
    if param not in valid_int:
        bot.reply(
            f"⚠️ Unknown parameter. Valid: "
            + ", ".join(f"{B}{p}{B}" for p in all_params)
        )
        return

    try:
        value = int(raw_value)
    except ValueError:
        return bot.reply("⚠️ Value must be a number.")

    lo, hi = valid_int[param]
    if value < lo or value > hi:
        return bot.reply(f"⚠️ {B}{param}{B} must be between {lo} and {hi}.")

    # Map 'duration' to the internal key 'ban_duration'
    key = 'ban_duration' if param == 'duration' else param
    _save_setting(bot, key, value)

    if param == 'duration':
        label = _format_duration(value) if value > 0 else "permanent"
        bot.say(f"✅ Antiflood ban duration set to {B}{label}{B}")
    elif param == 'window':
        bot.say(f"✅ Antiflood window set to {B}{_format_duration(value)}{B}")
    else:
        bot.say(f"✅ Antiflood {param} set to {B}{value}{B} joins")

    LOGGER.info("Antiflood: %s → %d by %s", param, value, trigger.nick)


def _cmd_whitelist(bot, trigger, args):
    """Manage whitelist. Usage: $flood whitelist <add|del|list> [nick|user@host]"""
    channel = str(trigger.sender).lower()

    subcmd = args[0].lower() if args else 'list'
    wl_key = f'whitelist_{channel}'

    if subcmd == 'list':
        whitelist = bot.db.get_plugin_value('antiflood', wl_key) or []
        if not whitelist:
            bot.say(f"📋 Antiflood whitelist for {B}{trigger.sender}{B} is empty.")
        else:
            entries = ", ".join(f"{B}{w}{B}" for w in whitelist)
            bot.say(f"📋 Antiflood whitelist for {B}{trigger.sender}{B}: {entries}")
        return

    if len(args) < 2:
        return bot.reply(f"Usage: {B}$flood whitelist{B} <add|del> <nick or user@host>")

    mask = args[1].lower()
    if not any(ch.isalnum() for ch in mask):
        return bot.reply(f"⚠️ {B}{mask}{B} would match everyone.")
    whitelist = bot.db.get_plugin_value('antiflood', wl_key) or []

    if subcmd == 'add':
        existing = [w.lower() for w in whitelist]
        candidates = [mask]
        if '@' not in mask and not any(ch in mask for ch in '*?'):
            host = _host_for_nick(bot, mask)
            if host:
                candidates.append(f"*!*@{host.lower()}")
        added = []
        for item in candidates:
            if item not in existing:
                whitelist.append(item)
                existing.append(item)
                added.append(item)
        if not added:
            return bot.reply(f"⚠️ {B}{mask}{B} is already whitelisted.")
        bot.db.set_plugin_value('antiflood', wl_key, whitelist)
        shown = " and ".join(f"{B}{item}{B}" for item in added)
        bot.say(f"✅ Whitelisted {shown} in {B}{trigger.sender}{B}")
        LOGGER.info("Antiflood: Whitelisted %s in %s by %s", ", ".join(added), channel, trigger.nick)

    elif subcmd in ('del', 'remove', 'rm'):
        new_wl = [w for w in whitelist if w.lower() != mask]
        if len(new_wl) == len(whitelist):
            return bot.reply(f"⚠️ {B}{mask}{B} is not in the whitelist.")
        bot.db.set_plugin_value('antiflood', wl_key, new_wl)
        bot.say(f"✅ Removed {B}{mask}{B} from whitelist in {B}{trigger.sender}{B}")
        LOGGER.info("Antiflood: Un-whitelisted %s in %s by %s", mask, channel, trigger.nick)

    else:
        bot.reply(f"⚠️ Usage: {B}$flood whitelist{B} <add|del|list> [nick or user@host]")


def _cmd_stats(bot, trigger):
    """Show recent antiflood ban actions."""
    channel = str(trigger.sender).lower()
    stats = bot.memory.get('flood_stats', [])

    # Filter to current channel
    chan_stats = [s for s in stats if s['channel'] == channel]

    if not chan_stats:
        bot.say(f"📊 No recent flood actions in {B}{trigger.sender}{B}.")
        return

    # Show the last 5
    recent = chan_stats[-5:]
    now = time.time()

    bot.say(f"📊 {B}Recent Flood Actions{B} in {B}{trigger.sender}{B} (last {len(recent)}):")
    for entry in reversed(recent):
        ago = int(now - entry['time'])
        if ago < 60:
            ago_str = f"{ago}s ago"
        elif ago < 3600:
            ago_str = f"{ago // 60}m ago"
        else:
            ago_str = f"{ago // 3600}h {(ago % 3600) // 60}m ago"

        bot.say(
            f"  {B}{entry['nick']}{B} → {entry['banmask']}"
            f" ({entry['count']} joins in {_format_duration(entry['window'])})"
            f" — {ago_str}"
        )


def _cmd_floodtop(bot, trigger):
    """Show top 5 most-kicked users in this channel."""
    channel = str(trigger.sender).lower()
    db_key = f'floodtop_{channel}'
    top = bot.db.get_plugin_value('antiflood', db_key) or {}

    if not top:
        bot.say(f"📊 No flood kicks recorded in {B}{trigger.sender}{B}.")
        return

    # Sort by count descending, take top 5
    sorted_top = sorted(top.items(), key=lambda x: x[1]['count'], reverse=True)[:5]

    bot.say(f"🏆 {B}Flood Top 5{B} in {B}{trigger.sender}{B}:")
    for rank, (hostmask, data) in enumerate(sorted_top, 1):
        medal = {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, f' {rank}.')
        bot.say(
            f"  {medal} {B}{data['nick']}{B} — "
            f"{B}{data['count']}{B} kick{'s' if data['count'] != 1 else ''}"
            f" ({hostmask})"
        )


def _cmd_help(bot, trigger):
    """Send the command reference via NOTICE."""
    nick = trigger.nick
    bot.notice(f"🛡️ {B}Antiflood Protection — Join/Part Flood Detection{B}", nick)
    bot.notice(" ", nick)
    bot.notice(f"  {B}$flood{B}                            — Status for current channel", nick)
    bot.notice(f"  {B}$flood on{B} / {B}off{B}                    — Enable / disable in this channel", nick)
    bot.notice(f"  {B}$flood set window <sec>{B}            — Detection window (10–3600s, default 300)", nick)
    bot.notice(f"  {B}$flood set threshold <n>{B}            — Join count to trigger (2–50, default 3)", nick)
    bot.notice(f"  {B}$flood set duration <sec>{B}           — Auto-unban delay (0 = permanent, default 600)", nick)
    bot.notice(f"  {B}$flood set banmask <style>{B}          — host (*!*@host) or ident (*!user@host)", nick)
    bot.notice(f"  {B}$flood whitelist list{B}               — Show whitelisted nicks and hostmasks", nick)
    bot.notice(f"  {B}$flood whitelist add <nick|mask>{B}    — Exempt a nick or user@host", nick)
    bot.notice(f"  {B}$flood whitelist del <nick|mask>{B}    — Remove exemption", nick)
    bot.notice(f"  {B}$flood stats{B}                        — Recent ban actions in this channel", nick)
    bot.notice(f"  {B}$flood top{B}                          — Top 5 most-kicked users (all time)", nick)
    bot.notice(f"  {B}$floodtop{B}                           — Shortcut for $flood top", nick)
    bot.notice(" ", nick)
    bot.notice(
        f"📝 Tracks JOIN events per hostmask. If a user rejoins a channel "
        f"more than the threshold within the window, they are banned and "
        f"kicked. Users with exempt modes (default: +v/+h/+o) and "
        f"bot-ignored hosts are skipped. "
        f"Off by default — use $flood on to enable per channel.",
        nick,
    )
    bot.say(f"📬 {B}{nick}{B}, check your notices for antiflood command help!")
