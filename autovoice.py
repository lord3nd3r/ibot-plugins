"""
autovoice.py - Sopel auto-voice plugin based on chat activity.

Off by default. Enable per-channel with $autovoice on.
Requires the bot to have halfop+ in the channel.

Tracks message counts per user per channel. Once a user hits the
configured threshold they get +v. If they stop talking for a
configurable idle period (default: 7 days) the voice is removed.

Users who already have +v, +h, +o, +a, or +q are ignored entirely.
"""

import json
import logging
import os
import threading
import time

from sopel import module, plugin

LOG = logging.getLogger(__name__)

# ─── tunables ────────────────────────────────────────────────────────
MSG_THRESHOLD = 50           # messages needed to earn +v
IDLE_SECONDS = 7 * 86400     # 7 days without talking → devoice
SWEEP_INTERVAL = 15 * 60     # background sweep every 15 minutes
DATA_FILE = os.path.expanduser('~/.sopel/autovoice_data.json')
# ─────────────────────────────────────────────────────────────────────

# privilege bits used by Sopel internally
_PRIV_VOICE  = 1
_PRIV_HALFOP = 2
_PRIV_OP     = 4
_PRIV_ADMIN  = 8
_PRIV_OWNER  = 16

# runtime state
_data_lock = threading.RLock()
_data = None          # {"channels": {"#chan": {"nick": {"count": N, "last": epoch}}}}
_enabled = None       # {"#chan": bool}
_thresholds = None    # {"#chan": N} per-channel override of MSG_THRESHOLD
_blocked = None       # {"#chan": ["nick1", "nick2", ...]} — never autovoice these
_sweep_stop = threading.Event()
_sweep_thread = None


# ═══════════════════════════════ persistence ═════════════════════════

def _load():
    global _data, _enabled, _blocked, _thresholds
    with _data_lock:
        if _data is not None:
            return
        if os.path.isfile(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    raw = json.load(f)
                _data = raw.get('channels', {})
                _enabled = raw.get('enabled', {})
                _blocked = raw.get('blocked', {})
                _thresholds = raw.get('thresholds', {})
            except Exception:
                LOG.exception('autovoice: failed to load %s', DATA_FILE)
                _data = {}
                _enabled = {}
                _blocked = {}
                _thresholds = {}
        else:
            _data = {}
            _enabled = {}
            _blocked = {}
            _thresholds = {}


def _threshold(channel):
    """Messages needed for +v in this channel (per-channel override or default)."""
    try:
        return int((_thresholds or {}).get(str(channel).lower(), MSG_THRESHOLD))
    except (TypeError, ValueError):
        return MSG_THRESHOLD


def _save():
    with _data_lock:
        if _data is None:
            return
        payload = {'channels': _data, 'enabled': _enabled or {}, 'blocked': _blocked or {},
                   'thresholds': _thresholds or {}}
    tmp = DATA_FILE + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        os.replace(tmp, DATA_FILE)
    except Exception:
        LOG.exception('autovoice: failed to save %s', DATA_FILE)


# ═══════════════════════════════ helpers ═════════════════════════════

def _bot_has_halfop(bot, channel):
    """Return True if the bot has halfop or higher in the channel."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return False
        privs = getattr(chan, 'privileges', None) or {}
        for k, v in privs.items():
            if k.lower() == bot.nick.lower():
                return isinstance(v, int) and v >= _PRIV_HALFOP
        return False
    except Exception:
        return False


def _user_has_mode(bot, channel, nick):
    """Return True if the user already has +v, +h, +o, +a, or +q."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return False
        privs = getattr(chan, 'privileges', None) or {}
        for k, v in privs.items():
            if k.lower() == nick.lower():
                return isinstance(v, int) and v >= _PRIV_VOICE
        return False
    except Exception:
        return False


def _user_privs(bot, channel, nick):
    """Return the privilege bitmask for a nick, 0 if unknown."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return 0
        privs = getattr(chan, 'privileges', None) or {}
        for k, v in privs.items():
            if k.lower() == nick.lower():
                return v if isinstance(v, int) else 0
        return 0
    except Exception:
        return 0


def _nick_in_channel(bot, channel, nick_lower):
    """Check whether a nick is currently in the channel."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return False
        privs = getattr(chan, 'privileges', None) or {}
        return any(k.lower() == nick_lower for k in privs)
    except Exception:
        return False


def _is_enabled(channel):
    """Check if autovoice is enabled for a channel."""
    _load()
    return (_enabled or {}).get(channel.lower(), False)


def _is_blocked(channel, nick_lower):
    """Check if a nick is blocked from autovoice in a channel."""
    _load()
    blocked_list = (_blocked or {}).get(channel.lower(), [])
    return nick_lower in blocked_list


# ═══════════════════════════════ sweep ═══════════════════════════════

def _sweep(bot):
    """Voice users who hit the threshold; devoice users idle > IDLE_SECONDS."""
    _load()
    now = time.time()
    with _data_lock:
        channels = dict(_data) if _data else {}

    for channel, users in channels.items():
        if not _is_enabled(channel):
            continue
        if not _bot_has_halfop(bot, channel):
            continue

        to_voice = []
        to_devoice = []

        for nick_lower, info in list(users.items()):
            if not isinstance(info, dict):
                continue
            count = info.get('count', 0)
            last = info.get('last', 0)

            if not _nick_in_channel(bot, channel, nick_lower):
                continue

            privs = _user_privs(bot, channel, nick_lower)
            has_voice = (privs & _PRIV_VOICE) != 0
            has_higher = privs >= _PRIV_HALFOP  # +h, +o, +a, +q

            # Never touch users with halfop+
            if has_higher:
                continue

            idle = (now - last) > IDLE_SECONDS
            autovoiced = info.get('autovoiced', False)

            if count >= _threshold(channel) and not has_voice and not idle:
                if not _is_blocked(channel, nick_lower):
                    to_voice.append(nick_lower)
            elif has_voice and autovoiced and idle:
                to_devoice.append(nick_lower)

        # batch MODE changes (up to 4 per command to stay safe)
        _batch_mode(bot, channel, '+v', to_voice)
        _batch_mode(bot, channel, '-v', to_devoice)

        # mark newly voiced users and reset devoiced users
        with _data_lock:
            chan_data = _data.get(channel, {})
            for nick_lower in to_voice:
                if nick_lower in chan_data and isinstance(chan_data[nick_lower], dict):
                    chan_data[nick_lower]['autovoiced'] = True
            for nick_lower in to_devoice:
                if nick_lower in chan_data and isinstance(chan_data[nick_lower], dict):
                    chan_data[nick_lower]['count'] = 0
                    chan_data[nick_lower]['autovoiced'] = False

        # clean out idle entries from data so file doesn't grow forever
        with _data_lock:
            chan_data = _data.get(channel, {})
            for nick_lower in list(chan_data):
                info = chan_data[nick_lower]
                if isinstance(info, dict) and (now - info.get('last', 0)) > (IDLE_SECONDS * 2):
                    del chan_data[nick_lower]

    _save()


def _batch_mode(bot, channel, mode_str, nicks):
    """Apply a mode to a list of nicks in batches of 4."""
    if not nicks:
        return
    sign = mode_str[0]   # + or -
    char = mode_str[1]   # v
    for i in range(0, len(nicks), 4):
        batch = nicks[i:i+4]
        flags = sign + (char * len(batch))
        params = ' '.join(batch)
        bot.write(['MODE', channel, flags, params])
        LOG.info('autovoice: %s %s in %s', flags, params, channel)


def _start_sweep(bot):
    global _sweep_thread
    _sweep_stop.clear()

    def _loop():
        _sweep_stop.wait(60)  # let bot join channels first
        while not _sweep_stop.is_set():
            try:
                _sweep(bot)
            except Exception:
                LOG.exception('autovoice: sweep error')
            _sweep_stop.wait(SWEEP_INTERVAL)

    _sweep_thread = threading.Thread(target=_loop, daemon=True)
    _sweep_thread.start()


# ═══════════════════════════════ lifecycle ═══════════════════════════

def setup(bot):
    _load()
    _start_sweep(bot)
    LOG.info('autovoice: plugin loaded')


def shutdown(bot):
    _sweep_stop.set()
    _save()
    LOG.info('autovoice: plugin shutdown')


# ═══════════════════════════════ message tracking ════════════════════

@module.rule('.*')
@module.priority('low')
@plugin.thread(True)
def track_message(bot, trigger):
    """Track every channel message for activity counting."""
    if not trigger.sender or not str(trigger.sender).startswith('#'):
        return
    channel = str(trigger.sender).lower()
    if not _is_enabled(channel):
        return
    nick = str(trigger.nick)
    nick_lower = nick.lower()
    if nick_lower == bot.nick.lower():
        return

    _load()
    now = time.time()
    with _data_lock:
        chan_data = _data.setdefault(channel, {})
        rec = chan_data.setdefault(nick_lower, {'count': 0, 'last': 0, 'nick': nick})
        rec['count'] = rec.get('count', 0) + 1
        rec['last'] = now
        rec['nick'] = nick  # keep display nick fresh
        count = rec['count']

    # Check if this message just pushed them over the threshold
    if count == _threshold(channel):
        if _bot_has_halfop(bot, channel) and not _user_has_mode(bot, channel, nick):
            if not _is_blocked(channel, nick_lower):
                bot.write(['MODE', channel, '+v', nick])
                LOG.info('autovoice: +v %s in %s (hit %d msgs)', nick, channel, _threshold(channel))
                with _data_lock:
                    _data.get(channel, {}).get(nick_lower, {})['autovoiced'] = True

    # Periodic save (every 50 messages across all channels)
    if int(now) % 50 == 0:
        _save()


@module.event('JOIN')
@module.rule('.*')
@module.priority('low')
@plugin.thread(True)
def on_join_revoice(bot, trigger):
    """Re-voice users who already earned autovoice when they rejoin."""
    if not trigger.sender or not str(trigger.sender).startswith('#'):
        return
    channel = str(trigger.sender).lower()
    if not _is_enabled(channel):
        return
    nick = str(trigger.nick)
    nick_lower = nick.lower()
    if nick_lower == bot.nick.lower():
        return
    if not _bot_has_halfop(bot, channel):
        return

    _load()
    with _data_lock:
        rec = (_data or {}).get(channel, {}).get(nick_lower)
        if not rec or not isinstance(rec, dict):
            return
        count = rec.get('count', 0)
        last = rec.get('last', 0)

    # Re-voice if they've earned it and aren't idle
    if count >= _threshold(channel) and (time.time() - last) < IDLE_SECONDS:
        # Check if antispam recently kicked this user — don't re-voice if so
        spam_kicked = bot.memory.get('spam_kicked', {})
        kick_time = spam_kicked.get((channel, nick_lower))
        if kick_time and (time.time() - kick_time) < 1800:  # 30-min cooldown
            LOG.info('autovoice: NOT re-voicing %s in %s — antispam kicked %ds ago',
                     nick, channel, int(time.time() - kick_time))
            return

        # Small delay so the server finishes processing the JOIN
        time.sleep(2)
        if not _user_has_mode(bot, channel, nick) and not _is_blocked(channel, nick_lower):
            bot.write(['MODE', channel, '+v', nick])
            LOG.info('autovoice: re-voiced %s in %s on JOIN', nick, channel)


# ═══════════════════════════════ admin commands ══════════════════════

@module.commands('autovoice')
@module.example('$autovoice on')
@module.example('$autovoice off')
@module.example('$autovoice status')
@module.example('$autovoice reset <nick>')
@module.example('$autovoice block <nick>')
@module.example('$autovoice threshold 10')
def autovoice_cmd(bot, trigger):
    """Manage autovoice. Works in channel or PM ($autovoice #channel <cmd>)."""
    args = (trigger.group(2) or '').strip().split()

    # PM support: first arg must be #channel
    if not trigger.sender or not str(trigger.sender).startswith('#'):
        if not args or not args[0].startswith('#'):
            bot.reply('From PM, specify the channel: $autovoice #channel <subcommand>')
            return
        channel = args.pop(0).lower()
    else:
        channel = str(trigger.sender).lower()

    nick = trigger.nick

    # require halfop+ or admin to manage
    privs = _user_privs(bot, channel, nick)
    if privs < _PRIV_HALFOP and not trigger.admin:
        bot.reply('You need halfop or higher to manage autovoice.')
        return

    # Re-lowercase args after possible pop
    args = [a.lower() for a in args]
    subcmd = args[0] if args else 'status'

    _load()

    if subcmd in ('on', 'enable'):
        with _data_lock:
            _enabled[channel] = True
        _save()
        bot.say(f'Autovoice \x02enabled\x02 for {channel}.')

    elif subcmd in ('off', 'disable'):
        with _data_lock:
            _enabled[channel] = False
        _save()
        bot.say(f'Autovoice \x02disabled\x02 for {channel}.')

    elif subcmd == 'status':
        on = _is_enabled(channel)
        with _data_lock:
            tracked = len((_data or {}).get(channel, {}))
            blocked_count = len((_blocked or {}).get(channel, []))
        state = '\x0303ON\x03' if on else '\x0304OFF\x03'
        bot.say(f'Autovoice is {state} | Tracking \x02{tracked}\x02 users | '
                f'Blocked: \x02{blocked_count}\x02 | '
                f'Threshold: {_threshold(channel)} msgs | Idle timeout: {IDLE_SECONDS // 86400}d')

    elif subcmd == 'reset':
        target = args[1] if len(args) > 1 else None
        if not target:
            bot.reply('Usage: $autovoice reset <nick>')
            return
        target_lower = target.lower()
        with _data_lock:
            chan_data = (_data or {}).get(channel, {})
            if target_lower in chan_data:
                del chan_data[target_lower]
                _save()
                bot.say(f'Reset activity data for \x02{target}\x02 in {channel}.')
            else:
                bot.reply(f'No data found for {target}.')

    elif subcmd == 'block':
        target = args[1] if len(args) > 1 else None
        if not target:
            bot.reply('Usage: $autovoice block <nick>')
            return
        target_lower = target.lower()
        with _data_lock:
            blocked_list = _blocked.setdefault(channel, [])
            if target_lower in blocked_list:
                bot.reply(f'{target} is already blocked.')
                return
            blocked_list.append(target_lower)
            # Also reset their autovoice data
            chan_data = (_data or {}).get(channel, {})
            if target_lower in chan_data:
                chan_data[target_lower]['count'] = 0
                chan_data[target_lower]['autovoiced'] = False
        _save()
        bot.say(f'\x02{target}\x02 is now blocked from autovoice in {channel}.')
        LOG.info('autovoice: blocked %s in %s by %s', target, channel, nick)

    elif subcmd == 'unblock':
        target = args[1] if len(args) > 1 else None
        if not target:
            bot.reply('Usage: $autovoice unblock <nick>')
            return
        target_lower = target.lower()
        with _data_lock:
            blocked_list = _blocked.get(channel, [])
            if target_lower not in blocked_list:
                bot.reply(f'{target} is not blocked.')
                return
            blocked_list.remove(target_lower)
        _save()
        bot.say(f'\x02{target}\x02 is now unblocked from autovoice in {channel}.')
        LOG.info('autovoice: unblocked %s in %s by %s', target, channel, nick)

    elif subcmd == 'blocklist':
        with _data_lock:
            blocked_list = (_blocked or {}).get(channel, [])
        if not blocked_list:
            bot.say(f'No blocked users in {channel}.')
        else:
            bot.say(f'Blocked from autovoice in {channel} ({len(blocked_list)}): '
                    + ', '.join(f'\x02{n}\x02' for n in sorted(blocked_list)))

    elif subcmd == 'threshold':
        if len(args) > 1:
            if not args[1].isdigit() or not 1 <= int(args[1]) <= 100000:
                bot.reply('Usage: $autovoice threshold <number>  (1-100000)')
                return
            n = int(args[1])
            with _data_lock:
                _thresholds[channel] = n
            _save()
            bot.say(f'Autovoice threshold for {channel} set to \x02{n}\x02 messages.')
            LOG.info('autovoice: threshold for %s set to %d by %s', channel, n, nick)
            return
        bot.say(f'Current threshold in {channel}: \x02{_threshold(channel)}\x02 messages to earn +v, '
                f'\x02{IDLE_SECONDS // 86400}\x02 days idle to lose it.')

    elif subcmd == 'check':
        target = args[1] if len(args) > 1 else trigger.nick.lower()
        _vcheck_report(bot, trigger, channel, target)

    else:
        bot.reply('Usage: $autovoice <on|off|status|reset|block|unblock|blocklist|threshold [n]|check> [nick]')


@module.commands('vcheck')
@module.example('$vcheck', 'Check your own autovoice progress')
@module.example('$vcheck Boliver', 'Check another user\'s autovoice progress')
def vcheck_cmd(bot, trigger):
    """Check how far a user is from earning autovoice (+v).

    Usage:
        $vcheck           — check your own progress
        $vcheck <nick>    — check another user's progress
    In PM:
        $vcheck #channel [nick]
    """
    args = (trigger.group(2) or '').strip().split()
    is_pm = not str(trigger.sender).startswith('#')

    if is_pm:
        if not args or not args[0].startswith('#'):
            bot.reply('Usage from PM: $vcheck #channel [nick]')
            return
        channel = args[0].lower()
        target = args[1].lower() if len(args) > 1 else trigger.nick.lower()
    else:
        channel = str(trigger.sender).lower()
        target = args[0].lower() if args else trigger.nick.lower()

    _vcheck_report(bot, trigger, channel, target)


def _vcheck_report(bot, trigger, channel, target_lower):
    """Build and send the autovoice progress report for a nick."""
    _load()

    if not _is_enabled(channel):
        bot.say(f'Autovoice is not enabled in {channel}.')
        return

    with _data_lock:
        chan_data = (_data or {}).get(channel, {})
        rec = chan_data.get(target_lower)

    thr = _threshold(channel)
    display_nick = target_lower
    if rec and isinstance(rec, dict):
        display_nick = rec.get('nick', target_lower)
        count = rec.get('count', 0)
        remaining = max(0, thr - count)
        if remaining == 0:
            bot.say(f'\x02{display_nick}\x02 has reached the threshold '
                    f'({count}/{thr} msgs) \x0303✓ voiced\x03')
        else:
            pct = int((count / thr) * 100)
            bot.say(f'\x02{display_nick}\x02: {count}/{thr} msgs '
                    f'({pct}%) — \x02{remaining}\x02 more to go for +v')
    else:
        bot.say(f'No activity recorded for \x02{target_lower}\x02 in {channel} '
                f'(need {thr} msgs for +v).')
