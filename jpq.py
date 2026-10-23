"""
jpq.py - Join/Part/Quit Flood Protection for Sopel
Detects and bans users who rapidly cycle join/part/quit in channels.

Author: Kristopher Craig
Commands:
    $jpq                    - Show status for the current channel
    $jpq on / off           - Enable/disable JPQ in the current channel
    $jpq ban on / off       - Also ban on kick (off by default)
    $jpq set <param> <val>  - Adjust window, threshold, duration, or banmask
    $jpq whitelist ...      - Manage exempted hostmasks
    $jpq stats              - Show recent ban actions
    $jpq help               - Show help via NOTICE

Config (sopel.cfg):
    [jpq]
    window = 30             # seconds
    threshold = 5           # events within window to trigger ban
    ban_duration = 300      # auto-unban after N seconds (0 = permanent)
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
# \x0f resets color. A bare \x03 eats the next digits, so "4 events" shows up blank.
SEP = "\x0314 · \x0f"

# Maximum announcements per channel within this cooldown (seconds)
_ANNOUNCE_COOLDOWN = 5

# Grace period (seconds) after a bot-initiated kick/ban during which
# JPQ events from that hostmask are ignored, preventing feedback loops.
_KICK_GRACE_PERIOD = 60


# ========================= CONFIG =========================

class JPQSection(StaticSection):
    """Configuration section for JPQ flood protection."""
    window = ValidatedAttribute('window', int, default=30)
    """Time window in seconds to track events."""
    threshold = ValidatedAttribute('threshold', int, default=5)
    """Number of events within the window to trigger action."""
    ban_duration = ValidatedAttribute('ban_duration', int, default=300)
    """Auto-unban after this many seconds. 0 = permanent."""
    banmask_style = ValidatedAttribute('banmask_style', default='host')
    """Banmask style: 'host' = *!*@host, 'ident' = *!user@host."""
    exempt_modes = ValidatedAttribute('exempt_modes', default='vho')
    """Mode chars whose holders are exempt (v=voice, h=halfop, o=op)."""
    enabled = BooleanAttribute('enabled', default=True)
    """Global enable/disable switch."""


# ========================= SETUP / SHUTDOWN =========================

def setup(bot):
    """Initialize the JPQ plugin."""
    bot.config.define_section('jpq', JPQSection)
    bot.memory['jpq_events'] = {}           # (channel, hostmask) -> [timestamps]
    bot.memory['jpq_members'] = {}           # nick_lower -> set(channels)
    bot.memory['jpq_lock'] = threading.Lock()
    bot.memory['jpq_pending_unbans'] = {}    # (channel, banmask) -> Timer
    bot.memory['jpq_last_announce'] = {}     # channel -> timestamp
    bot.memory['jpq_stats'] = []            # list of recent action dicts
    bot.memory['jpq_recent_kicks'] = {}     # (channel, hostmask) -> timestamp of kick
    bot.memory['jpq_staff'] = {}            # channel_lower -> set of nicks at +h or above
    _restore_unbans(bot)
    LOGGER.info("JPQ flood protection initialized")


def shutdown(bot):
    """Cancel pending unban timers on shutdown."""
    for timer in bot.memory.get('jpq_pending_unbans', {}).values():
        timer.cancel()
    LOGGER.info("JPQ flood protection shutdown")


# ========================= SETTINGS HELPERS =========================

def _get_settings(bot):
    """Get JPQ settings — runtime DB overrides take priority over config."""
    defaults = {
        'window': bot.config.jpq.window,
        'threshold': bot.config.jpq.threshold,
        'ban_duration': bot.config.jpq.ban_duration,
        'banmask_style': bot.config.jpq.banmask_style,
    }
    overrides = bot.db.get_plugin_value('jpq', 'settings') or {}
    defaults.update({k: v for k, v in overrides.items() if k in defaults})
    return defaults


def _save_setting(bot, key, value):
    """Persist a runtime setting override to the DB."""
    overrides = bot.db.get_plugin_value('jpq', 'settings') or {}
    overrides[key] = value
    bot.db.set_plugin_value('jpq', 'settings', overrides)


def _is_channel_enabled(bot, channel):
    """Check if JPQ is enabled for a specific channel."""
    if not bot.config.jpq.enabled:
        return False
    disabled = bot.db.get_plugin_value('jpq', 'disabled_channels') or []
    return channel.lower() not in [c.lower() for c in disabled]


def _bans_enabled(bot, channel):
    """Per-channel host bans. Off until someone runs $jpq ban on."""
    flags = bot.db.get_plugin_value('jpq', 'bans') or {}
    return bool(flags.get(str(channel).lower(), False))


def _set_bans(bot, channel, enabled):
    flags = bot.db.get_plugin_value('jpq', 'bans') or {}
    flags[str(channel).lower()] = bool(enabled)
    bot.db.set_plugin_value('jpq', 'bans', flags)


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


def _is_ignored(bot, trigger):
    """Check if the nick or host matches Sopel's built-in block lists."""
    nick = str(trigger.nick)
    host = trigger.host or ''

    # Check nick blocks
    nick_blocks = getattr(bot.config.core, 'nick_blocks', None) or []
    for pattern in nick_blocks:
        try:
            if re.match(pattern, nick, re.IGNORECASE):
                return True
        except re.error:
            pass

    # Check host blocks
    host_blocks = getattr(bot.config.core, 'host_blocks', None) or []
    for pattern in host_blocks:
        try:
            if re.match(pattern, host, re.IGNORECASE):
                return True
        except re.error:
            pass

    return False


# Halfop and everything above it. Not configurable: +h/+o/+a/+q never count.
_STAFF_BITS = (
    plugin.HALFOP | plugin.OP
    | getattr(plugin, 'ADMIN', 8)
    | getattr(plugin, 'OWNER', 16)
)


def _channel_obj(bot, channel):
    channels = getattr(bot, 'channels', None) or {}
    chan = str(channel)
    entry = channels.get(chan)
    if entry is not None:
        return entry
    want = chan.lower()
    for name, obj in channels.items():
        if str(name).lower() == want:
            return obj
    return None


def _privs(bot, nick, channel):
    """Privilege bitmask for nick in channel, or 0."""
    entry = _channel_obj(bot, channel)
    if entry is None:
        return 0
    privs = getattr(entry, 'privileges', None) or {}
    if nick in privs:
        return privs[nick] or 0
    nick_l = str(nick).lower()
    for name, val in privs.items():
        if str(name).lower() == nick_l:
            return val or 0
    return 0


def _remember_staff(bot, channel, nick):
    """Remember that nick holds +h or higher, including across a reconnect."""
    staff = bot.memory.setdefault('jpq_staff', {})
    staff.setdefault(str(channel).lower(), set()).add(str(nick).lower())


def _forget_staff(bot, channel, nick):
    staff = bot.memory.get('jpq_staff') or {}
    nicks = staff.get(str(channel).lower())
    if nicks:
        nicks.discard(str(nick).lower())


def _is_remembered_staff(bot, channel, nick):
    staff = bot.memory.get('jpq_staff') or {}
    return str(nick).lower() in staff.get(str(channel).lower(), ())


def _note_visible_staff(bot):
    """Record everyone who currently holds +h or above.

    Does not forget anyone. A halfop who just rejoined has no mode until
    ChanServ restores it, and that rejoin must stay exempt.
    """
    channels = getattr(bot, 'channels', None) or {}
    for name, chan in channels.items():
        privs = getattr(chan, 'privileges', None) or {}
        for nick, bits in privs.items():
            if isinstance(bits, int) and bits & _STAFF_BITS:
                _remember_staff(bot, name, nick)


def _is_exempt(bot, nick, channel):
    """True for +h and above, or for a configured mode such as +v.

    +h/+o/+a/+q are always exempt, including the quit and the rejoin
    before their mode is restored. Voice stays behind exempt_modes.
    """
    privs = _privs(bot, nick, channel)
    if isinstance(privs, int) and privs & _STAFF_BITS:
        _remember_staff(bot, channel, nick)
        return True
    if _is_remembered_staff(bot, channel, nick):
        return True

    exempt = bot.config.jpq.exempt_modes or ''
    if not privs or 'v' not in exempt:
        return False
    return bool(privs & plugin.VOICE)


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
    last = bot.memory['jpq_last_announce'].get(channel, 0)
    if now - last < _ANNOUNCE_COOLDOWN:
        return False
    bot.memory['jpq_last_announce'][channel] = now
    return True


# --- Persisted temp-ban expiries (survive restarts) -------------------
# Stored in the bot DB as plugin value (jpq, 'pending_unbans'):
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
            data = bot.db.get_plugin_value('jpq', _UNBAN_DB_KEY) or {}
            if expires is None:
                if k not in data:
                    return
                data.pop(k, None)
            else:
                data[k] = {'channel': str(channel), 'mask': banmask,
                           'expires': float(expires), 'extra': extra}
            bot.db.set_plugin_value('jpq', _UNBAN_DB_KEY, data)
    except Exception:
        LOGGER.exception("JPQ: failed to persist pending unban")


def _restore_unbans(bot):
    """Reschedule unbans saved by a previous run."""
    try:
        with _unban_persist_lock:
            data = bot.db.get_plugin_value('jpq', _UNBAN_DB_KEY) or {}
    except Exception:
        LOGGER.exception("JPQ: failed to load pending unbans")
        return
    now = time.time()
    for entry in list(data.values()):
        try:
            delay = max(_RESTORE_MIN_DELAY, float(entry['expires']) - now)
            _schedule_unban(bot, entry['channel'], entry['mask'], delay)
        except Exception:
            LOGGER.exception("JPQ: bad pending unban entry")
    if data:
        LOGGER.info("JPQ: restored %d pending unban(s)", len(data))



def _schedule_unban(bot, channel, banmask, duration):
    """Schedule an automatic unban after duration seconds."""
    if duration <= 0:
        return

    key = (channel.lower(), banmask)

    # Cancel any existing timer for this mask
    old = bot.memory['jpq_pending_unbans'].get(key)
    if old:
        old.cancel()

    def _do_unban():
        bot.write(['MODE', channel, '-b', banmask])
        bot.memory['jpq_pending_unbans'].pop(key, None)
        _persist_unban(bot, channel, banmask, None)
        LOGGER.info("JPQ: Auto-unbanned %s in %s", banmask, channel)

    timer = threading.Timer(duration, _do_unban)
    timer.daemon = True
    timer.start()
    bot.memory['jpq_pending_unbans'][key] = timer
    _persist_unban(bot, channel, banmask, time.time() + duration, None)


def _log_action(bot, nick, channel, banmask, count, window):
    """Record an action for the $jpq stats command."""
    entry = {
        'nick': str(nick),
        'channel': str(channel),
        'banmask': banmask,
        'count': count,
        'window': window,
        'time': time.time(),
    }
    stats = bot.memory['jpq_stats']
    stats.append(entry)
    # Keep only the last 25 actions in memory
    if len(stats) > 25:
        bot.memory['jpq_stats'] = stats[-25:]


# ========================= CORE LOGIC =========================

def _record_event(bot, nick, channel, hostmask, trigger):
    """Record a JPQ event and take action if the threshold is exceeded."""
    channel = str(channel).lower()

    if not _is_channel_enabled(bot, channel):
        return
    if nick.lower() == bot.nick.lower():
        return
    if _is_whitelisted(bot, channel, hostmask, nick):
        return
    if _is_ignored(bot, trigger):
        return
    if _is_exempt(bot, nick, channel):
        return

    # Skip events from users the bot recently kicked/banned (grace period)
    # This prevents the bot's own enforcement from re-triggering the counter.
    kick_key = (channel, hostmask)
    kick_time = bot.memory['jpq_recent_kicks'].get(kick_key)
    if kick_time and (time.time() - kick_time) < _KICK_GRACE_PERIOD:
        LOGGER.debug(
            "JPQ: Ignoring event from %s in %s (within kick grace period)",
            hostmask, channel,
        )
        return

    settings = _get_settings(bot)
    now = time.time()
    key = (channel, hostmask)
    triggered = False

    with bot.memory['jpq_lock']:
        events = bot.memory['jpq_events']

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
    """Kick a user who triggered flood protection, and ban when enabled."""
    do_ban = _bans_enabled(bot, channel)
    banmask = _get_banmask(trigger, style=settings.get('banmask_style', 'host')) if do_ban else None

    if not _bot_has_op(bot, channel):
        LOGGER.warning(
            "JPQ: Flood from %s (%s) in %s — bot not opped, cannot act",
            nick, hostmask, channel,
        )
        return

    # Record the kick so future events from this hostmask are ignored
    # during the grace period (prevents feedback loops)
    bot.memory['jpq_recent_kicks'][(channel, hostmask)] = time.time()

    # Ban first, then kick, so they cannot rejoin ahead of the mode.
    if do_ban:
        bot.write(['MODE', channel, '+b', banmask])
    reason = f"JPQ flood protection ({count} events in {settings['window']}s)"
    bot.write(['KICK', channel, nick, f':{reason}'])

    if do_ban:
        LOGGER.info(
            "JPQ: Banned %s (nick: %s) in %s — %d events in %ds",
            banmask, nick, channel, count, settings['window'],
        )
    else:
        LOGGER.info(
            "JPQ: Kicked %s (%s) in %s — %d events in %ds (bans off)",
            nick, hostmask, channel, count, settings['window'],
        )

    # Record for stats
    _log_action(bot, nick, channel, banmask or 'kick only', count, settings['window'])

    # Announce in channel (rate-limited)
    if _can_announce(bot, channel):
        dur = settings['ban_duration']
        dur_str = f" — auto-unban in {B}{dur}s{B}" if do_ban and dur > 0 else ""
        action = f"Banned {B}{banmask}{B}" if do_ban else f"Kicked {B}{nick}{B}"
        bot.say(
            f"🛡️ {B}JPQ Flood Detected{B}{SEP}"
            f"{action}{SEP}"
            f"{count} events in {settings['window']}s{dur_str}",
            channel,
        )

    # Schedule auto-unban
    dur = settings['ban_duration']
    if do_ban and dur > 0:
        _schedule_unban(bot, channel, banmask, dur)


# ========================= MEMBERSHIP TRACKING =========================
# We maintain our own nick→channels map so the QUIT handler can look up
# which channels a user was in, even after Sopel has removed them from
# bot.channels.

def _track_join(bot, nick, channel):
    """Record that nick is present in channel."""
    nick_lower = nick.lower()
    chan_lower = str(channel).lower()
    with bot.memory['jpq_lock']:
        members = bot.memory['jpq_members']
        if nick_lower not in members:
            members[nick_lower] = set()
        members[nick_lower].add(chan_lower)


def _track_part(bot, nick, channel):
    """Remove nick from a single channel."""
    nick_lower = nick.lower()
    chan_lower = str(channel).lower()
    with bot.memory['jpq_lock']:
        members = bot.memory['jpq_members']
        if nick_lower in members:
            members[nick_lower].discard(chan_lower)
            if not members[nick_lower]:
                del members[nick_lower]


def _track_quit(bot, nick):
    """Remove nick from all channels; return the set of channels they were in."""
    nick_lower = nick.lower()
    with bot.memory['jpq_lock']:
        return bot.memory['jpq_members'].pop(nick_lower, set())


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

    _note_visible_staff(bot)
    _track_join(bot, nick, channel)
    _record_event(bot, nick, channel, hostmask, trigger)


@plugin.thread(True)
@plugin.event('PART')
@plugin.rule('.*')
def on_part(bot, trigger):
    """Handle PART events for flood detection."""
    if trigger.event != 'PART':
        return
    if not str(trigger.sender).startswith('#'):
        return
    nick = trigger.nick
    channel = trigger.sender
    hostmask = _get_hostmask(trigger)

    _note_visible_staff(bot)
    _track_part(bot, nick, channel)
    _record_event(bot, nick, channel, hostmask, trigger)


@plugin.thread(True)
@plugin.event('QUIT')
@plugin.rule('.*')
def on_quit(bot, trigger):
    """Handle QUIT events for flood detection.

    QUIT is server-wide so trigger.sender is NOT a channel. We use our
    own membership tracking to find which channels the user was in.
    """
    if trigger.event != 'QUIT':
        return
    nick = trigger.nick
    if nick.lower() == bot.nick.lower():
        return

    hostmask = _get_hostmask(trigger)
    # Capture +h and above before membership is dropped. A ping timeout
    # from an admin still has to be exempt.
    _note_visible_staff(bot)
    channels = _track_quit(bot, nick)

    for channel in channels:
        _record_event(bot, nick, channel, hostmask, trigger)


def _mode_privilege_nicks(trigger):
    """Return (adding, nick) for +h/+o/+a/+q changes in this MODE."""
    args = [str(arg) for arg in (getattr(trigger, 'args', None) or ())]
    mode = ''
    params = []
    for arg in args:
        if not mode and arg[:1] in '+-' and not arg.startswith('#'):
            mode = arg
        elif mode:
            params.append(arg)
    found = []
    adding = True
    index = 0
    for char in mode:
        if char == '+':
            adding = True
            continue
        if char == '-':
            adding = False
            continue
        takes_arg = char in 'vhoaqbeIkf'
        if char == 'l' and not adding:
            takes_arg = False
        if takes_arg and index < len(params):
            if char in 'hoaq':
                found.append((adding, params[index]))
            index += 1
    return found


@plugin.thread(True)
@plugin.event('MODE')
@plugin.rule('.*')
def on_mode(bot, trigger):
    """Keep the halfop+ list in step with mode changes."""
    if not str(trigger.sender).startswith('#'):
        return
    for adding, nick in _mode_privilege_nicks(trigger):
        if adding or _privs(bot, nick, trigger.sender) & _STAFF_BITS:
            _remember_staff(bot, trigger.sender, nick)
        else:
            _forget_staff(bot, trigger.sender, nick)


# ========================= PERIODIC CLEANUP =========================

@plugin.thread(True)
@plugin.interval(60)
def cleanup(bot):
    """Prune stale event tracking entries and expired kick grace records."""
    _note_visible_staff(bot)
    now = time.time()
    settings = _get_settings(bot)
    cutoff = now - settings['window']

    with bot.memory['jpq_lock']:
        events = bot.memory['jpq_events']
        stale = []
        for key, timestamps in events.items():
            events[key] = [t for t in timestamps if t > cutoff]
            if not events[key]:
                stale.append(key)
        for key in stale:
            del events[key]

    if stale:
        LOGGER.debug("JPQ: Pruned %d stale tracking entries", len(stale))

    # Prune expired kick grace records
    kicks = bot.memory.get('jpq_recent_kicks', {})
    expired = [k for k, t in kicks.items() if (now - t) >= _KICK_GRACE_PERIOD]
    for k in expired:
        del kicks[k]
    if expired:
        LOGGER.debug("JPQ: Pruned %d expired kick grace entries", len(expired))


# ========================= ADMIN COMMANDS =========================

@plugin.command('jpq')
@plugin.require_admin('You need to be a bot admin to manage JPQ.')
def cmd_jpq(bot, trigger):
    """$jpq <status|on|off|set|whitelist|stats|help> — Manage JPQ flood protection."""
    if trigger.is_privmsg:
        return bot.reply("This command must be used in a channel.")

    args = (trigger.group(2) or '').strip().split()
    subcmd = args[0].lower() if args else 'status'

    dispatch = {
        'status':    lambda: _cmd_status(bot, trigger),
        'on':        lambda: _cmd_toggle(bot, trigger, enable=True),
        'off':       lambda: _cmd_toggle(bot, trigger, enable=False),
        'set':       lambda: _cmd_set(bot, trigger, args[1:]),
        'ban':       lambda: _cmd_ban(bot, trigger, args[1:]),
        'whitelist': lambda: _cmd_whitelist(bot, trigger, args[1:]),
        'wl':        lambda: _cmd_whitelist(bot, trigger, args[1:]),
        'stats':     lambda: _cmd_stats(bot, trigger),
        'help':      lambda: _cmd_help(bot, trigger),
    }

    handler = dispatch.get(subcmd)
    if handler:
        handler()
    else:
        bot.reply(
            f"⚠️ Unknown subcommand {B}{subcmd}{B}. "
            f"Try: {B}$jpq help{B}"
        )


def _cmd_status(bot, trigger):
    """Show JPQ status for the current channel."""
    channel = str(trigger.sender).lower()

    settings = _get_settings(bot)
    enabled = _is_channel_enabled(bot, channel)
    icon = "✅" if enabled else "❌"

    with bot.memory['jpq_lock']:
        active = sum(1 for (ch, _) in bot.memory['jpq_events'] if ch == channel)

    pending = sum(
        1 for (ch, _) in bot.memory.get('jpq_pending_unbans', {}) if ch == channel
    )
    whitelist = bot.db.get_plugin_value('jpq', f'whitelist_{channel}') or []
    dur_str = f"{settings['ban_duration']}s" if settings['ban_duration'] > 0 else "permanent"
    style_label = "ident (*!user@host)" if settings.get('banmask_style') == 'ident' else "host (*!*@host)"
    bans_on = _bans_enabled(bot, channel)

    bot.say(
        f"🛡️ {B}JPQ Status{B}{SEP}"
        f"{icon} {'Enabled' if enabled else 'Disabled'}{SEP}"
        f"⏱️ Window: {B}{settings['window']}s{B}{SEP}"
        f"🎯 Threshold: {B}{settings['threshold']}{B} events{SEP}"
        f"🔨 Bans: {B}{'on' if bans_on else 'off'}{B}{SEP}"
        f"⏳ Duration: {B}{dur_str}{B}{SEP}"
        f"🎭 Mask: {B}{style_label}{B}"
    )

    extras = []
    if active:
        extras.append(f"📊 Tracking: {B}{active}{B} host(s)")
    if pending:
        extras.append(f"🔓 Pending unbans: {B}{pending}{B}")
    if whitelist:
        extras.append(f"📋 Whitelist: {B}{len(whitelist)}{B} entries")
    extras.append(f"🔑 Exempt: {B}+h and up{B}")
    if extras:
        bot.say(SEP.join(extras))


def _cmd_toggle(bot, trigger, enable):
    """Enable or disable JPQ for the current channel."""
    channel = str(trigger.sender).lower()

    disabled = bot.db.get_plugin_value('jpq', 'disabled_channels') or []

    if enable:
        disabled = [c for c in disabled if c.lower() != channel]
        bot.db.set_plugin_value('jpq', 'disabled_channels', disabled)
        bot.say(f"✅ JPQ flood protection {B}enabled{B} for {B}{trigger.sender}{B}")
    else:
        if channel not in [c.lower() for c in disabled]:
            disabled.append(channel)
        bot.db.set_plugin_value('jpq', 'disabled_channels', disabled)
        bot.say(f"❌ JPQ flood protection {B}disabled{B} for {B}{trigger.sender}{B}")

    LOGGER.info("JPQ: %s in %s by %s", 'Enabled' if enable else 'Disabled', channel, trigger.nick)


def _cmd_ban(bot, trigger, args):
    """Enable or disable host bans on kick. Off until turned on."""
    channel = str(trigger.sender).lower()
    sub = args[0].lower() if args else 'status'

    if sub in ('status', 'show'):
        state = "enabled" if _bans_enabled(bot, channel) else "disabled"
        bot.say(
            f"🔨 JPQ bans are {B}{state}{B} for {B}{trigger.sender}{B}. "
            f"Use {B}$jpq ban on{B} or {B}$jpq ban off{B}."
        )
        return

    if sub in ('on', 'enable', 'yes'):
        _set_bans(bot, channel, True)
        bot.say(
            f"🔨 JPQ bans {B}enabled{B} for {B}{trigger.sender}{B}. "
            f"Flood kicks will also set a ban."
        )
        LOGGER.info("JPQ: Bans enabled in %s by %s", channel, trigger.nick)
        return

    if sub in ('off', 'disable', 'no'):
        _set_bans(bot, channel, False)
        bot.say(
            f"🔨 JPQ bans {B}disabled{B} for {B}{trigger.sender}{B}. "
            f"Existing bans stay until someone removes them."
        )
        LOGGER.info("JPQ: Bans disabled in %s by %s", channel, trigger.nick)
        return

    bot.reply(f"⚠️ Usage: {B}$jpq ban{B} <on|off>")


def _cmd_set(bot, trigger, args):
    """Adjust a setting. Usage: $jpq set <window|threshold|duration|banmask> <value>"""
    valid_int = {'window': (5, 300), 'threshold': (2, 50), 'duration': (0, 86400)}
    valid_str = {'banmask': ('host', 'ident')}
    all_params = list(valid_int) + list(valid_str)

    if len(args) < 2:
        bot.reply(
            f"Usage: {B}$jpq set{B} <{'|'.join(all_params)}> <value>"
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
        label = f"ident (*!user@host)" if raw_value == 'ident' else "host (*!*@host)"
        bot.say(f"✅ JPQ banmask style set to {B}{label}{B}")
        LOGGER.info("JPQ: %s → %s by %s", param, raw_value, trigger.nick)
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
        label = f"{value}s" if value > 0 else "permanent"
        bot.say(f"✅ JPQ ban duration set to {B}{label}{B}")
    else:
        unit = 's' if param == 'window' else ' events'
        bot.say(f"✅ JPQ {param} set to {B}{value}{unit}{B}")

    LOGGER.info("JPQ: %s → %d by %s", param, value, trigger.nick)


def _cmd_whitelist(bot, trigger, args):
    """Manage whitelist. Usage: $jpq whitelist <add|del|list> [nick|user@host]"""
    channel = str(trigger.sender).lower()

    subcmd = args[0].lower() if args else 'list'
    wl_key = f'whitelist_{channel}'

    if subcmd == 'list':
        whitelist = bot.db.get_plugin_value('jpq', wl_key) or []
        if not whitelist:
            bot.say(f"📋 JPQ whitelist for {B}{trigger.sender}{B} is empty.")
        else:
            entries = ", ".join(f"{B}{w}{B}" for w in whitelist)
            bot.say(f"📋 JPQ whitelist for {B}{trigger.sender}{B}: {entries}")
        return

    if len(args) < 2:
        return bot.reply(f"Usage: {B}$jpq whitelist{B} <add|del> <nick or user@host>")

    mask = args[1].lower()
    if not any(ch.isalnum() for ch in mask):
        return bot.reply(f"⚠️ {B}{mask}{B} would match everyone.")
    whitelist = bot.db.get_plugin_value('jpq', wl_key) or []

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
        bot.db.set_plugin_value('jpq', wl_key, whitelist)
        shown = " and ".join(f"{B}{item}{B}" for item in added)
        bot.say(f"✅ Whitelisted {shown} in {B}{trigger.sender}{B}")
        LOGGER.info("JPQ: Whitelisted %s in %s by %s", ", ".join(added), channel, trigger.nick)

    elif subcmd in ('del', 'remove', 'rm'):
        new_wl = [w for w in whitelist if w.lower() != mask]
        if len(new_wl) == len(whitelist):
            return bot.reply(f"⚠️ {B}{mask}{B} is not in the whitelist.")
        bot.db.set_plugin_value('jpq', wl_key, new_wl)
        bot.say(f"✅ Removed {B}{mask}{B} from whitelist in {B}{trigger.sender}{B}")
        LOGGER.info("JPQ: Un-whitelisted %s in %s by %s", mask, channel, trigger.nick)

    else:
        bot.reply(f"⚠️ Usage: {B}$jpq whitelist{B} <add|del|list> [nick or user@host]")


def _cmd_stats(bot, trigger):
    """Show recent JPQ ban actions."""
    channel = str(trigger.sender).lower()
    stats = bot.memory.get('jpq_stats', [])

    # Filter to current channel
    chan_stats = [s for s in stats if s['channel'] == channel]

    if not chan_stats:
        bot.say(f"📊 No recent JPQ actions in {B}{trigger.sender}{B}.")
        return

    # Show the last 5
    recent = chan_stats[-5:]
    now = time.time()

    bot.say(f"📊 {B}Recent JPQ Actions{B} in {B}{trigger.sender}{B} (last {len(recent)}):")
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
            f" ({entry['count']} events in {entry['window']}s)"
            f" — {ago_str}"
        )


def _cmd_help(bot, trigger):
    """Send the command reference via NOTICE."""
    nick = trigger.nick
    bot.notice(f"🛡️ {B}JPQ Flood Protection — Commands{B}", nick)
    bot.notice(" ", nick)
    bot.notice(f"  {B}$jpq{B}                            — Status for current channel", nick)
    bot.notice(f"  {B}$jpq on{B} / {B}off{B}                    — Enable / disable in this channel", nick)
    bot.notice(f"  {B}$jpq ban{B}                         — Show whether kicks also set a ban", nick)
    bot.notice(f"  {B}$jpq ban on{B} / {B}off{B}                — Ban on kick (off by default)", nick)
    bot.notice(f"  {B}$jpq set window <sec>{B}            — Detection window (5–300s, default 30)", nick)
    bot.notice(f"  {B}$jpq set threshold <n>{B}            — Event count to trigger (2–50, default 5)", nick)
    bot.notice(f"  {B}$jpq set duration <sec>{B}           — Auto-unban delay (0 = permanent, default 300)", nick)
    bot.notice(f"  {B}$jpq set banmask <style>{B}          — host (*!*@host) or ident (*!user@host)", nick)
    bot.notice(f"  {B}$jpq whitelist list{B}               — Show whitelisted nicks and hostmasks", nick)
    bot.notice(f"  {B}$jpq whitelist add <nick|mask>{B}    — Exempt a nick or user@host", nick)
    bot.notice(f"  {B}$jpq whitelist del <nick|mask>{B}    — Remove exemption", nick)
    bot.notice(f"  {B}$jpq stats{B}                        — Recent ban actions in this channel", nick)
    bot.notice(" ", nick)
    bot.notice(
        f"📝 Tracks JOIN/PART/QUIT by hostmask, including ping timeouts and "
        f"dropped connections. If a user exceeds the threshold within the "
        f"window, they are kicked. Bans are off unless $jpq ban on is set. "
        f"Halfops and above are always "
        f"skipped, including the rejoin before their mode comes back. "
        f"Sopel-ignored hosts are skipped.",
        nick,
    )
    bot.say(f"📬 {B}{nick}{B}, check your notices for JPQ command help!")
