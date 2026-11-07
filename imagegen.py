# -*- coding: utf-8 -*-
"""imagegen: $img <prompt> -> Cloudflare Workers AI (FLUX.1 schnell) -> sharey.org link.

Config ([imagegen] in glitchy.cfg, or env CF_ACCOUNT_ID / CF_API_TOKEN):
  account_id, api_token (Workers AI token), model, steps, daily_cap,
  user_cooldown, max_concurrent, max_prompt, expiry, channels (comma list, blank=all)
Daily cap is persisted in bot.db and resets at 00:00 UTC (matches CF free-tier reset).
"""
import base64
import json
import configparser
import datetime
import logging
import os
import re
import threading
import time

import requests
import urllib3
from sopel import plugin
from sopel.config import types

LOGGER = logging.getLogger(__name__)
PLUGIN = 'imagegen'
CF_URL = 'https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}'
SHAREY_URL = 'https://sharey.org/api/upload'
_URL_OK = re.compile(r'https://sharey\.org/(?:files/)?[A-Za-z0-9_-]{3,64}(?:\.[A-Za-z0-9]{2,5})?')
MAX_RESP = 16 * 1024 * 1024
_CTRL = re.compile(r'[\x00-\x20\x7f]')
_ACCT_OK = re.compile(r'^[0-9a-f]{32}$')



class ImageGenSection(types.StaticSection):
    account_id = types.ValidatedAttribute('account_id', default='')
    api_token = types.SecretAttribute('api_token', default='')
    model = types.ValidatedAttribute('model', default='@cf/black-forest-labs/flux-1-schnell')
    steps = types.ValidatedAttribute('steps', int, default=4)
    daily_cap = types.ValidatedAttribute('daily_cap', int, default=100)
    user_cooldown = types.ValidatedAttribute('user_cooldown', int, default=60)
    max_concurrent = types.ValidatedAttribute('max_concurrent', int, default=2)
    max_prompt = types.ValidatedAttribute('max_prompt', int, default=400)
    expiry = types.ValidatedAttribute('expiry', default='30d')
    channels = types.ValidatedAttribute('channels', default='')


def setup(bot):
    bot.config.define_section(PLUGIN, ImageGenSection)
    mem = bot.memory
    if 'imagegen_lock' not in mem:
        mem['imagegen_lock'] = threading.Lock()
    if 'imagegen_cooldowns' not in mem:
        mem['imagegen_cooldowns'] = {}
    if 'imagegen_sem' not in mem:
        mem['imagegen_sem'] = {'n': None, 'sem': None}


_FILE_CACHE = {'key': None, 'vals': {}}


def _file_section(bot):
    """Re-read [imagegen] from the cfg file on disk (cached by mtime) so values
    added after startup work after a plain $load/$reload, no $rehash needed."""
    fn = getattr(bot.config, 'filename', None)
    try:
        st = os.stat(fn)
    except (TypeError, OSError):
        return {}
    key = (fn, st.st_mtime_ns, st.st_size)
    if _FILE_CACHE['key'] != key:
        p = configparser.RawConfigParser(allow_no_value=True)
        try:
            p.read(fn)
            vals = dict(p.items(PLUGIN)) if p.has_section(PLUGIN) else {}
        except configparser.Error:
            vals = {}
        _FILE_CACHE['key'], _FILE_CACHE['vals'] = key, vals
    return _FILE_CACHE['vals']


def _cfg(bot, name, default):
    v = _file_section(bot).get(name)
    if v in (None, ''):
        try:
            v = getattr(bot.config.imagegen, name)
        except Exception:
            v = None
    if v in (None, ''):
        return default
    if isinstance(default, int):
        try:
            return int(str(v).split('#')[0].strip())
        except ValueError:
            return default
    return str(v).strip()


def _creds(bot):
    acct = _cfg(bot, 'account_id', '') or os.environ.get('CF_ACCOUNT_ID', '')
    tok = _cfg(bot, 'api_token', '') or os.environ.get('CF_API_TOKEN', '')
    return acct.strip(), tok.strip()


def _today():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')


def _lock(bot):
    # Shared via bot.memory so it survives $reload (new module objects).
    return bot.memory.setdefault('imagegen_lock', threading.Lock())


def _semaphore(bot, n):
    with _lock(bot):
        st = bot.memory.setdefault('imagegen_sem', {'n': None, 'sem': None})
        if st['n'] != n:
            st['n'], st['sem'] = n, threading.BoundedSemaphore(max(1, n))
        return st['sem']


def _reserve(bot, nick, cap, cooldown, now=None):
    """Atomically check cooldown + daily cap and reserve a slot.
    Returns (ok, message)."""
    now = now if now is not None else time.time()
    with _lock(bot):
        cds = bot.memory.setdefault('imagegen_cooldowns', {})
        last = cds.get(nick, 0)
        if now - last < cooldown:
            return False, 'slow down, try again in %ds.' % int(cooldown - (now - last) + 1)
        day = _today()
        data = bot.db.get_plugin_value(PLUGIN, 'daily') or {}
        if data.get('day') != day:
            data = {'day': day, 'count': 0}
        if data['count'] >= cap:
            return False, 'daily image cap reached, resets 00:00 UTC.'
        data['count'] += 1
        bot.db.set_plugin_value(PLUGIN, 'daily', data)
        cds[nick] = now
        return True, day


def _refund(bot, day):
    """Give back a slot reserved on `day` (no-op if the day already rolled over)."""
    with _lock(bot):
        data = bot.db.get_plugin_value(PLUGIN, 'daily') or {}
        if data.get('day') == day and data.get('count', 0) > 0:
            data['count'] -= 1
            bot.db.set_plugin_value(PLUGIN, 'daily', data)


class GenError(Exception):
    def __init__(self, msg, spent=False):
        super().__init__(msg)
        self.spent = spent  # True if provider allowance may have been used


class _TooBig(Exception):
    pass


def _read_bounded(r, limit):
    try:
        if int(r.headers.get('Content-Length') or 0) > limit:
            raise _TooBig()
    except (TypeError, ValueError, AttributeError):
        pass
    buf = bytearray()
    for chunk in r.iter_content(65536):
        buf.extend(chunk)
        if len(buf) > limit:
            raise _TooBig()
    return bytes(buf)


def _json(raw):
    try:
        return json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None


def _connect_failed(e):
    """True only when the TCP/TLS connection was never established."""
    if isinstance(e, requests.ConnectTimeout):
        return True
    for a in getattr(e, 'args', ()):
        reason = getattr(a, 'reason', a)
        if isinstance(reason, urllib3.exceptions.NewConnectionError):
            return True
    return False


def generate(acct, tok, model, prompt, steps, session=requests):
    try:
        with session.post(CF_URL.format(acct=acct, model=model),
                          headers={'Authorization': 'Bearer ' + tok},
                          json={'prompt': prompt, 'steps': steps},
                          timeout=(10, 90), stream=True) as r:
            status = r.status_code
            raw = _read_bounded(r, MAX_RESP)
    except _TooBig:
        raise GenError('image generation returned an oversized response.', spent=True)
    except requests.RequestException as e:
        if _connect_failed(e):
            raise GenError('image service unreachable, try later.')
        raise GenError('image service connection failed, try later.', spent=True)
    if status == 429:
        raise GenError('image provider quota/rate limit hit, try later.')
    if status in (401, 403):
        raise GenError('image provider auth failed (check config).')
    j = _json(raw)
    if not isinstance(j, dict):
        j = {}
    if status != 200 or j.get('success') is not True:
        errs = j.get('errors')
        errs = errs if isinstance(errs, list) else []
        text = ' '.join(str(e.get('message', '')) for e in errs if isinstance(e, dict)).lower()
        if 'nsfw' in text or 'safety' in text or 'flagged' in text:
            raise GenError("that prompt was refused by the provider's safety filter.")
        if 'neuron' in text or 'quota' in text or 'limit' in text:
            raise GenError('daily provider allowance used up, resets 00:00 UTC.')
        LOGGER.warning('imagegen: CF error status=%s', status)
        # 4xx = rejected before running; 5xx / malformed 200 = may have run
        raise GenError('image generation failed.', spent=not (400 <= status < 500))
    res = j.get('result')
    b64 = res.get('image') if isinstance(res, dict) else None
    try:
        img = base64.b64decode(b64, validate=True) if isinstance(b64, str) else b''
    except (ValueError, TypeError):
        img = b''
    if len(img) < 100:
        raise GenError('image generation returned no image.', spent=True)
    return img


def _valid_url(url):
    return (isinstance(url, str) and not _CTRL.search(url)
            and _URL_OK.fullmatch(url) is not None)


def upload(img, expiry, session=requests):
    url = None
    try:
        with session.post(SHAREY_URL, files={'file': ('image.jpg', img, 'image/jpeg')},
                          data={'expiry': expiry}, timeout=(10, 60), stream=True) as r:
            if r.status_code in (200, 201):  # live API answers 201 Created
                j = _json(_read_bounded(r, 65536))
                urls = j.get('urls') if isinstance(j, dict) else None
                if isinstance(urls, list) and urls:
                    url = urls[0]
    except (requests.RequestException, _TooBig):
        url = None
    if not _valid_url(url):
        LOGGER.warning('imagegen: sharey upload failed')
        raise GenError('image made but upload to sharey failed.', spent=True)
    return url


@plugin.command('img', 'image')
@plugin.example('$img a lighthouse at dusk, oil painting')
def img_cmd(bot, trigger):
    """Generate an image: $img <prompt>"""
    nick = str(trigger.nick)
    chans = [c.strip().lower() for c in _cfg(bot, 'channels', '').split(',') if c.strip()]
    if chans and trigger.sender.lower() not in chans:
        return
    prompt = ' '.join((trigger.group(2) or '').split())
    prompt = re.sub(r'[\x00-\x1f\x7f]', '', prompt)
    if not prompt:
        bot.reply('usage: $img <prompt>')
        return
    maxp = _cfg(bot, 'max_prompt', 400)
    if len(prompt) > maxp:
        bot.reply('prompt too long (max %d chars).' % maxp)
        return
    acct, tok = _creds(bot)
    if not _ACCT_OK.match(acct) or not tok:
        bot.reply('image generation is not configured yet.')
        return
    sem = _semaphore(bot, _cfg(bot, 'max_concurrent', 2))
    if not sem.acquire(blocking=False):
        bot.reply('busy making other images, try again in a bit.')
        return
    try:
        ok, info = _reserve(bot, nick.lower(), _cfg(bot, 'daily_cap', 100),
                           _cfg(bot, 'user_cooldown', 60))
        if not ok:
            bot.reply(info)
            return
        bot.reply('generating image...')
        steps = min(8, max(1, _cfg(bot, 'steps', 4)))
        try:
            img = generate(acct, tok, _cfg(bot, 'model', '@cf/black-forest-labs/flux-1-schnell'),
                           prompt, steps)
        except GenError as e:
            if not e.spent:
                _refund(bot, info)
            bot.reply(str(e))
            return
        try:
            url = upload(img, _cfg(bot, 'expiry', '30d'))
        except GenError as e:
            bot.reply(str(e))
            return
        bot.reply(url)
    finally:
        sem.release()
