from sopel import plugin, config
import json
import re
import threading
import time
from urllib.parse import quote

import requests

YOUTUBE_RE = r'https?://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/\S*'
DB_KEY = 'youtube_titles_disabled'

CHANNEL_COOLDOWN = 5     # seconds between titles in one channel
USER_COOLDOWN = 15       # seconds between titles for one user's links
_cd_lock = threading.Lock()
_last_chan = {}
_last_user = {}
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f]')


def _cooldown_ok(channel, nick):
    now = time.monotonic()
    c, n = (channel or '').lower(), (nick or '').lower()
    with _cd_lock:
        if now - _last_chan.get(c, -1e9) < CHANNEL_COOLDOWN:
            return False
        if now - _last_user.get(n, -1e9) < USER_COOLDOWN:
            return False
        _last_chan[c] = now
        _last_user[n] = now
        if len(_last_user) > 2000:
            for k in [k for k, t in _last_user.items() if now - t > USER_COOLDOWN]:
                _last_user.pop(k, None)
        if len(_last_chan) > 500:
            for k in [k for k, t in _last_chan.items() if now - t > CHANNEL_COOLDOWN]:
                _last_chan.pop(k, None)
    return True


def _clean(text, limit=250):
    text = _CTRL_RE.sub('', str(text or '')).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _is_disabled(bot, channel):
    """Check if YouTube titles are disabled for this channel."""
    try:
        val = bot.db.get_channel_value(channel, DB_KEY)
        return bool(val)
    except Exception:
        return False


@plugin.command('yt')
@plugin.example('$yt off', '$yt on')
@plugin.require_chanmsg('This command only works in channels.')
def yt_toggle(bot, trigger):
    """Toggle YouTube title fetching on or off for this channel. Requires op or owner."""
    nick = trigger.nick
    channel = trigger.sender

    # Check if the user is a channel op or the bot owner
    is_op = False
    try:
        privs = bot.channels[channel].privileges.get(nick, 0)
        # OP = 2 (halfop=1, op=2, admin=4, owner=8)
        is_op = privs >= 2
    except Exception:
        pass

    is_owner = (nick == bot.settings.core.owner)

    if not is_op and not is_owner:
        bot.say('You need to be a channel op to do that.')
        return

    arg = trigger.group(2)
    if not arg or arg.strip().lower() not in ('on', 'off'):
        # Show current status
        if _is_disabled(bot, channel):
            bot.say('YouTube titles are currently OFF for {}. Use $yt on to enable.'.format(channel))
        else:
            bot.say('YouTube titles are currently ON for {}. Use $yt off to disable.'.format(channel))
        return

    arg = arg.strip().lower()
    if arg == 'off':
        bot.db.set_channel_value(channel, DB_KEY, True)
        bot.say('YouTube titles disabled for {}.'.format(channel))
    elif arg == 'on':
        bot.db.set_channel_value(channel, DB_KEY, False)
        bot.say('YouTube titles enabled for {}.'.format(channel))


@plugin.url(YOUTUBE_RE)
def youtube_title(bot, trigger, match=None):
    # Check if disabled for this channel
    if trigger.sender and _is_disabled(bot, trigger.sender):
        return

    # ibot passes (bot, trigger); Sopel passes (bot, trigger, match)
    if match is not None:
        url = match.group(0)
    else:
        # search the full raw message for a YouTube URL
        raw = str(trigger.group(0)) if trigger.group(0) else ''
        m = re.search(YOUTUBE_RE, raw)
        if not m:
            return
        url = m.group(0)

    # Strip trailing punctuation that got caught by \S*
    url = re.sub(r'[>)\]\'\\"]+$', '', url)

    if not _cooldown_ok(trigger.sender, trigger.nick):
        return

    api = 'https://www.youtube.com/oembed?url={}&format=json'.format(quote(url, safe=''))

    try:
        resp = requests.get(api, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        obj = resp.json()
    except Exception:
        return

    if not isinstance(obj, dict):
        return
    title = _clean(obj.get('title'))
    author = _clean(obj.get('author_name'), 80)

    if not title:
        return

    if author:
        bot.say('YouTube: {} — {}'.format(title, author))
    else:
        bot.say('YouTube: {}'.format(title))
