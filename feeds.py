# -*- coding: utf-8 -*-
"""
feeds.py - RSS/Atom feed poster for ibot (Sopel-compatible plugin API)

Off everywhere by default. A channel only gets posts after a channel op
(or bot admin) runs $feed on there AND subscribes it to at least one feed.

Feeds themselves are a global list managed by bot admins. Channel ops pick
which of those feeds their channel gets.

  Bot admins (anywhere, incl. PM):
    $feed admin add <nick>      make nick a feed admin for this channel
    $feed admin del <nick>      remove them
    $feed admin list            show feed admins for this channel

  Bot admins, or feed admins (any channel they're feed admin of):
    $feed add <name> <url>      add a feed to the global list
    $feed del <name>            remove a feed everywhere
    $feed poll                  check all feeds now

  Channel ops (@ or higher), feed admins of that channel, or bot admins:
    $feed on  / $feed off       enable / disable posting in this channel
    $feed sub <name>            subscribe this channel to a feed
    $feed unsub <name>          unsubscribe

  Channel ops, feed admins of that channel, or bot admins:
    $feed latest                5 newest items across all this channel's feeds
    $feed latest <name> [n]     newest n (max 5) items from one feed

  Anyone:
    $feed list                  all feeds, with * on ones this channel gets
    $feed status                on/off + subscriptions for this channel

  From PM, put the channel first:  $feed #oss-feeds on

Posting limits are hard-coded below and cannot be raised from IRC.
New feeds are "primed" on first fetch: existing items are marked seen and
NOT posted, so adding a feed never dumps its whole backlog.
"""

import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as _stdlib_ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from sopel import plugin
from sqlalchemy.sql import text

try:  # safer XML parsing if available; stdlib fallback otherwise
    import defusedxml.ElementTree as ET
except ImportError:
    ET = _stdlib_ET

LOG = logging.getLogger(__name__)

# ─── tunables (hard caps, not changeable from IRC) ────────────────────
POLL_INTERVAL = 600           # seconds between checks (10 min)
MAX_POSTS_PER_CHAN = 3        # max lines per channel per poll
LINE_GAP = 2.0                # seconds between lines
FETCH_TIMEOUT = 15            # seconds per HTTP request
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_TITLE_LEN = 180
SEEN_KEEP_DAYS = 30           # prune seen-item ids older than this
MAX_FAILS_BEFORE_SKIP = 10    # after this many failures, only retry hourly
LATEST_COOLDOWN = 60          # seconds between $feed latest per channel
LATEST_MAX = 5                # max lines $feed latest will post
NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,32}$')
USER_AGENT = 'Mozilla/5.0 (compatible; glitchy-feeds/1.0; +IRC bot)'
RECENT_KEEP_SECS = 48 * 3600  # same-story dedupe window per channel
RECENT_MAX = 300              # max remembered posted titles per channel
SHORTEN_OVER = 80             # shorten links longer than this via TinyURL
SHORTEN_TIMEOUT = 5           # seconds for the TinyURL call
# ──────────────────────────────────────────────────────────────────────

# ibot stores channel modes as bit flags: VOICE=1 HALFOP=2 OP=4 ADMIN=8 OWNER=16
_OP_OR_HIGHER = 4 | 8 | 16
_poll_lock = threading.Lock()
_latest_last = {}             # channel -> last $feed latest time


# ═══════════════════════════════ database ════════════════════════════

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS feed_list (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE,
        url        TEXT NOT NULL UNIQUE,
        added_by   TEXT,
        added_at   INTEGER,
        primed     INTEGER NOT NULL DEFAULT 0,
        etag       TEXT,
        modified   TEXT,
        fail_count INTEGER NOT NULL DEFAULT 0,
        last_try   INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS feed_channels (
        channel    TEXT PRIMARY KEY,
        enabled    INTEGER NOT NULL DEFAULT 0,
        changed_by TEXT,
        changed_at INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS feed_subs (
        channel TEXT NOT NULL,
        feed_id INTEGER NOT NULL,
        PRIMARY KEY (channel, feed_id)
    )""",
    """CREATE TABLE IF NOT EXISTS feed_admins (
        channel  TEXT NOT NULL,
        nick     TEXT NOT NULL,
        account  TEXT,
        added_by TEXT,
        added_at INTEGER,
        PRIMARY KEY (channel, nick)
    )""",
    """CREATE TABLE IF NOT EXISTS feed_seen (
        feed_id INTEGER NOT NULL,
        item_id TEXT NOT NULL,
        seen_at INTEGER NOT NULL,
        PRIMARY KEY (feed_id, item_id)
    )""",
    """CREATE TABLE IF NOT EXISTS feed_recent (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        channel   TEXT NOT NULL,
        title     TEXT NOT NULL,
        posted_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS feed_recent_chan ON feed_recent (channel, posted_at)",
]


def _q(bot, sql, params=None, fetch=False):
    with bot.db.engine.begin() as conn:
        res = conn.execute(text(sql), params or {})
        return res.fetchall() if fetch else None


def _get_feed(bot, name):
    rows = _q(bot, "SELECT id, name, url FROM feed_list WHERE lower(name) = :n",
              {'n': name.lower()}, fetch=True)
    return rows[0] if rows else None


def _chan_enabled(bot, channel):
    rows = _q(bot, "SELECT enabled FROM feed_channels WHERE channel = :c",
              {'c': channel}, fetch=True)
    return bool(rows and rows[0][0])


def _set_enabled(bot, channel, on, nick):
    _q(bot, """INSERT INTO feed_channels (channel, enabled, changed_by, changed_at)
               VALUES (:c, :e, :n, :t)
               ON CONFLICT(channel) DO UPDATE SET
                 enabled = :e, changed_by = :n, changed_at = :t""",
       {'c': channel, 'e': 1 if on else 0, 'n': nick, 't': int(time.time())})


# ═══════════════════════════════ fetching / parsing ══════════════════

def _local(tag):
    """Strip XML namespace: '{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    return tag.rsplit('}', 1)[-1].lower() if isinstance(tag, str) else ''


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _child_text(el, name):
    c = _child(el, name)
    return ''.join(c.itertext()).strip() if c is not None else ''


# 'Re: ...', 'Fwd: ...', '[list] RE[2]: ...' etc.
_REPLY_RE = re.compile(r'^\s*(\[[^\]]*\]\s*)*(re|fwd?|aw)\s*(\[\d+\])?\s*:', re.I)


def _clean(s):
    """One line, no IRC control codes, no HTML, sane length."""
    s = html.unescape(re.sub(r'<[^>]+>', '', s or ''))
    s = re.sub(r'[\x00-\x1f\x7f]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > MAX_TITLE_LEN:
        s = s[:MAX_TITLE_LEN - 1].rstrip() + '…'
    return s


def _to_ts(s):
    """RFC 822 (RSS pubDate) or ISO 8601 (Atom, dc:date) -> epoch, 0 if unknown."""
    s = (s or '').strip()
    if not s:
        return 0
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _item_ts(e, names):
    for n in names:
        ts = _to_ts(_child_text(e, n))
        if ts:
            return ts
    return 0


def _parse(data):
    """Return list of (item_id, title, link), newest first as the feed lists them."""
    return [(i, t, l) for i, t, l, _ts in _parse_dated(data)]


def _parse_dated(data):
    """Like _parse, but each item also carries a publish timestamp (0 if none)."""
    root = ET.fromstring(data)
    items = []
    root_tag = _local(root.tag)

    if root_tag == 'feed':  # Atom
        for e in root:
            if _local(e.tag) != 'entry':
                continue
            link = ''
            for l in e:
                if _local(l.tag) == 'link':
                    rel = l.get('rel', 'alternate')
                    if rel == 'alternate' or not link:
                        link = l.get('href', '') or link
            title = _child_text(e, 'title')
            iid = _child_text(e, 'id') or link or title
            items.append((iid, title, link, _item_ts(e, ('published', 'updated'))))
    else:  # RSS 2.0 / RSS 1.0 (RDF)
        channel = _child(root, 'channel')
        containers = [root] + ([channel] if channel is not None else [])
        for cont in containers:
            for e in cont:
                if _local(e.tag) != 'item':
                    continue
                title = _child_text(e, 'title')
                link = _child_text(e, 'link')
                if not link:
                    about = e.get('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about')
                    link = about or ''
                iid = _child_text(e, 'guid') or link or title
                items.append((iid, title, link, _item_ts(e, ('pubdate', 'date', 'published', 'updated'))))

    return [(i[:500], t, _clean_link(l), ts) for i, t, l, ts in items if i]


def _fetch(url, etag=None, modified=None):
    """Return (status, body_bytes_or_None, new_etag, new_modified)."""
    headers = {'User-Agent': USER_AGENT,
               'Accept': 'application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5'}
    if etag:
        headers['If-None-Match'] = etag
    if modified:
        headers['If-Modified-Since'] = modified
    # follow redirects by hand so one can't bounce us to an internal address;
    # the guarded session also re-checks the *connected* IP (DNS rebinding).
    if not _valid_url(url):
        raise ValueError('disallowed URL')
    for _hop in range(5):
        r = _guarded_session().get(url, headers=headers, timeout=FETCH_TIMEOUT,
                                   stream=True, allow_redirects=False)
        if not r.is_redirect:
            break
        nxt = urljoin(url, r.headers.get('Location', ''))
        r.close()
        if not _valid_url(nxt):
            raise ValueError('redirect to a disallowed URL')
        url = nxt
    else:
        raise ValueError('too many redirects')
    with r:
        if r.status_code == 304:
            return 304, None, etag, modified
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > MAX_FEED_BYTES:
                raise ValueError('feed larger than %d bytes' % MAX_FEED_BYTES)
        return r.status_code, bytes(buf), r.headers.get('ETag'), r.headers.get('Last-Modified')


import ipaddress
import socket
from urllib.parse import urljoin


def _netguard_import():
    """Load the shared _netguard helper from this plugin's directory."""
    import importlib.util
    import os
    import sys
    for name in ('ibot_plugins._netguard', '_netguard'):
        if name in sys.modules:
            return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_netguard.py')
    spec = importlib.util.spec_from_file_location('ibot_plugins._netguard', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules['ibot_plugins._netguard'] = mod
    return mod


_GUARD = threading.local()


def _guarded_session():
    """Per-thread requests.Session that refuses to connect to non-public
    addresses (the connected peer IP is checked, not just the DNS answer)."""
    sess = getattr(_GUARD, 'session', None)
    if sess is None:
        sess = _GUARD.session = _netguard_import().guarded_session()
    return sess


def _clean_link(s):
    """Strip whitespace/control chars from a link so it can't split an IRC line."""
    return re.sub(r'[\x00-\x20\x7f]', '', s or '')[:400]


def _public_host(host):
    """True only if every address the host resolves to is public."""
    if not host:
        return False
    try:
        return all(ipaddress.ip_address(a[4][0].split('%')[0]).is_global
                   for a in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        return False


def _valid_url(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ('http', 'https') and bool(p.netloc) and _public_host(p.hostname)


# ═══════════════════════════════ same-story dedupe ═══════════════════
# Aggregators (Google News etc.) post one story from many outlets. Two titles
# count as the same story if they share a proper-name bigram ("Tom Skerritt")
# plus >= 2 other meaningful words, or their meaningful-word Jaccard is >= 0.6
# with >= 3 words in common. Titles carrying an advisory ID (CVE-, DSA-, USN-,
# ...) only match a title with the identical ID set.

_SUFFIX_RE = re.compile(r'\s+[-–—|]\s+([^-–—|]{1,60})$')
_LEADTAG_RE = re.compile(r'^\s*(\[[^\]]*\]\s*)+')
_ID_RE = re.compile(
    r'\b(CVE-\d{4}-\d{4,}|GHSA(?:-[a-z0-9]{4}){3}|(?:DSA|DLA)[- ]?\d{3,}(?:-\d+)?'
    r'|USN-\d+-\d+|ASA-\d{6}-\d+|GLSA[- ]?\d{6}-\d+|(?:RH|CE|EL|AL)[SBE]A-\d{4}:\d+'
    r'|(?:open)?SUSE-SU-\d{4}:\d+(?:-\d+)?|FEDORA-\d{4}-[0-9a-f]+|ZDI-\d{2}-\d+'
    r'|SA-\d{8}-\d+|MFSA[- ]?\d{4}-\d+|ALAS2?-\d{4}-\d+|XSA-\d+|VU#\d+)\b', re.I)
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_PUNCT_SPLIT_RE = re.compile(r"[^\w\s'’-]+")

_STOP = set("""a an the and or but nor of in on at to for from by with without as is are
was were be been being it its this that these those into onto over under after before
about than then so not no yes via vs v his her their our your my he she they we you i
who whom whose what which when where why how new after amid says say said will would
can could may might has have had do does did up down out off just more most also all
any some one two three""".split())

# words too generic to identify a story (never part of a name bigram and not
# counted toward a match)
_GENERIC = set("""security advisory advisories update updates updated release released releases
version linux kernel full disclosure google chrome chromium microsoft windows apple mozilla
firefox debian ubuntu fedora gentoo suse arch red hat open source package packages
vulnerability vulnerabilities vuln vulns flaw flaws bug bugs exploit exploits exploited
patch patches patched fix fixes fixed issue issues critical multiple remote code execution
arbitrary injection overflow buffer heap stack use free denial service dos privilege
escalation bypass authentication unauthenticated authenticated xss csrf rce sqli attack
attacks attacker hackers hacker report reports news breaking live latest today week
dies died dead death passes passed away obituary actor actress singer star stars
known famous legendary former ex tv series show film movie dead age aged years old
announces announced launch launches""".split())


def _dd_word(w):
    w = re.sub(r"['’]s$", '', w)
    return w


def _story_sig(title):
    """(ids, name_bigrams, tokens) for a cleaned display title."""
    return _story_sig_cached(title or '')


from functools import lru_cache


@lru_cache(maxsize=4096)
def _story_sig_cached(title):
    t = _SUFFIX_RE.sub(lambda m: '' if len(m.group(1).split()) <= 6 else m.group(0), title)
    ids = frozenset(re.sub(r'\s+', '-', m.group(1).upper()) for m in _ID_RE.finditer(t))
    t = _LEADTAG_RE.sub('', t)
    bigrams = set()
    tokens = set()
    first = True
    for chunk in _PUNCT_SPLIT_RE.split(t):
        words = [_dd_word(w) for w in _WORD_RE.findall(chunk)]
        prev = None
        for w in words:
            lw = w.lower()
            if lw not in _STOP and len(lw) > 1:
                tokens.add(lw)
            cap = (w[:1].isupper() and not w.isupper() and len(w) > 1
                   and lw not in _STOP and lw not in _GENERIC)
            if cap and first and lw in _GENERIC:
                cap = False
            first = False
            if cap and prev is not None:
                bigrams.add((prev.lower(), lw))
            prev = w if cap else None
    core = frozenset(x for x in tokens if x not in _GENERIC)
    return ids, frozenset(bigrams), core


def _same_story(a, b):
    """a, b: signatures from _story_sig."""
    ids_a, bg_a, tok_a = a
    ids_b, bg_b, tok_b = b
    if ids_a or ids_b:
        return ids_a == ids_b
    common = tok_a & tok_b
    for bg in bg_a & bg_b:
        if len(common - set(bg)) >= 2:
            return True
    union = tok_a | tok_b
    return bool(union) and len(common) >= 3 and len(common) / len(union) >= 0.6


def _is_dupe(sig, sigs):
    return any(_same_story(sig, s) for s in sigs)


def _recent_sigs(bot, channel):
    cutoff = int(time.time()) - RECENT_KEEP_SECS
    rows = _q(bot, """SELECT title FROM feed_recent WHERE channel = :c AND posted_at >= :t
                      ORDER BY posted_at DESC LIMIT :n""",
              {'c': channel, 't': cutoff, 'n': RECENT_MAX}, fetch=True)
    return [_story_sig(r[0]) for r in rows]


def _remember(bot, channel, title):
    _q(bot, "INSERT INTO feed_recent (channel, title, posted_at) VALUES (:c, :t, :p)",
       {'c': channel, 't': title, 'p': int(time.time())})


def _prune_recent(bot):
    _q(bot, "DELETE FROM feed_recent WHERE posted_at < :t",
       {'t': int(time.time()) - RECENT_KEEP_SECS})
    _q(bot, """DELETE FROM feed_recent WHERE id IN (
                 SELECT id FROM (SELECT id, ROW_NUMBER() OVER (
                     PARTITION BY channel ORDER BY posted_at DESC, id DESC) AS rn
                   FROM feed_recent) WHERE rn > :n)""", {'n': RECENT_MAX})


# ═══════════════════════════════ link shortening ═════════════════════

_short_cache = {}
_short_lock = threading.Lock()


def _shorten(url):
    """TinyURL for long links; original link on any failure."""
    url = (url or '').strip()
    if len(url) <= SHORTEN_OVER:
        return url
    with _short_lock:
        hit = _short_cache.get(url)
    if hit:
        return hit
    try:
        r = requests.get('https://tinyurl.com/api-create.php', params={'url': url},
                         headers={'User-Agent': USER_AGENT}, timeout=SHORTEN_TIMEOUT,
                         stream=True, allow_redirects=False)
        with r:
            if r.status_code != 200:
                return url
            body = r.raw.read(512, decode_content=True).decode('utf-8', 'replace').strip()
        if not (body.startswith('https://tinyurl.com/') and len(body) <= 100
                and re.fullmatch(r'https://tinyurl\.com/[A-Za-z0-9_-]+', body)):
            return url
    except Exception as exc:
        LOG.info('feeds: tinyurl failed: %s', exc)
        return url
    with _short_lock:
        if len(_short_cache) > 2000:
            _short_cache.clear()
        _short_cache[url] = body
    return body


# ═══════════════════════════════ polling ═════════════════════════════

def _poll_feed(bot, feed):
    """Fetch one feed. Returns list of new (title, link), oldest first."""
    fid, name, url, primed, etag, modified, fails, last_try = feed
    now = int(time.time())

    if fails >= MAX_FAILS_BEFORE_SKIP and now - last_try < 3600:
        return []

    try:
        status, body, etag2, mod2 = _fetch(url, etag, modified)
        if status == 304:
            _q(bot, "UPDATE feed_list SET fail_count = 0, last_try = :t, last_error = NULL WHERE id = :i",
               {'t': now, 'i': fid})
            return []
        items = _parse_dated(body)
    except Exception as exc:
        LOG.warning('feeds: %s (%s) failed: %s', name, url, exc)
        _q(bot, """UPDATE feed_list SET fail_count = fail_count + 1, last_try = :t,
                   last_error = :e WHERE id = :i""",
           {'t': now, 'e': str(exc)[:200], 'i': fid})
        return []

    _q(bot, """UPDATE feed_list SET etag = :et, modified = :m, fail_count = 0,
               last_try = :t, last_error = NULL WHERE id = :i""",
       {'et': etag2, 'm': mod2, 't': now, 'i': fid})

    if not items:
        return []

    ids = [i[0] for i in items]
    seen = set()
    # chunk the IN() lookup to keep the query small
    for k in range(0, len(ids), 200):
        chunk = ids[k:k + 200]
        params = {'f': fid}
        params.update({'i%d' % n: v for n, v in enumerate(chunk)})
        ph = ', '.join(':i%d' % n for n in range(len(chunk)))
        rows = _q(bot, "SELECT item_id FROM feed_seen WHERE feed_id = :f AND item_id IN (%s)" % ph,
                  params, fetch=True)
        seen.update(r[0] for r in rows)

    new = [i for i in items if i[0] not in seen]
    # Record every item still in the feed with a fresh timestamp, not just new
    # ones. Pruning only drops ids that have left the feed, so a feed that
    # keeps old items listed for months never gets them reposted.
    with bot.db.engine.begin() as conn:
        for iid, _t, _l, _ts in items:
            conn.execute(text("INSERT INTO feed_seen (feed_id, item_id, seen_at) "
                              "VALUES (:f, :i, :t) "
                              "ON CONFLICT(feed_id, item_id) DO UPDATE SET seen_at = :t"),
                         {'f': fid, 'i': iid, 't': now})

    if not primed:
        _q(bot, "UPDATE feed_list SET primed = 1 WHERE id = :i", {'i': fid})
        LOG.info('feeds: primed %s with %d items (not posted)', name, len(items))
        return []

    # feeds list newest first; post oldest first; drop list replies/forwards
    out = []
    for _i, t, l, ts in reversed(new):
        t = _clean(t) or '(untitled)'
        if _REPLY_RE.match(t):
            continue
        out.append((t, l.strip(), ts))
    return out


def _run_poll(bot):
    if not _poll_lock.acquire(blocking=False):
        return  # previous poll still running
    try:
        feeds = _q(bot, """SELECT id, name, url, primed, etag, modified, fail_count, last_try
                           FROM feed_list ORDER BY id""", fetch=True)
        if not feeds:
            return

        # which joined + enabled channels want which feed
        subs = _q(bot, """SELECT s.channel, s.feed_id FROM feed_subs s
                          JOIN feed_channels c ON c.channel = s.channel
                          WHERE c.enabled = 1""", fetch=True)
        joined = {str(c).lower() for c in list(bot.channels)}  # snapshot: IRC loop mutates it
        if not joined:
            return  # not connected/joined yet: don't mark items seen we can't post
        want = {}
        for chan, fid in subs:
            if chan in joined:
                want.setdefault(fid, []).append(chan)

        outbox = {}  # channel -> list of (ts, name, title, link)
        queued = {}  # channel -> titles already queued this round
        qsigs = {}   # channel -> story signatures queued this round + recently posted
        for feed in feeds:
            fid, name = feed[0], feed[1]
            # poll every feed (cheap with ETag/304) so the seen list stays
            # current; a channel that subscribes later gets no backlog
            for title, link, ts in _poll_feed(bot, feed):
                tkey = title.lower()
                sig = _story_sig(title)
                for chan in want.get(fid, []):
                    if tkey in queued.setdefault(chan, set()):
                        continue  # same title already queued for this channel
                    if chan not in qsigs:
                        try:
                            qsigs[chan] = _recent_sigs(bot, chan)
                        except Exception:
                            LOG.exception('feeds: recent cache read failed')
                            qsigs[chan] = []
                    if _is_dupe(sig, qsigs[chan]):
                        continue  # same story from another outlet (already marked seen)
                    queued[chan].add(tkey)
                    qsigs[chan].append(sig)
                    outbox.setdefault(chan, []).append((ts, name, title, link))

        for chan, lines in outbox.items():
            extra = len(lines) - MAX_POSTS_PER_CHAN
            lines.sort(key=lambda x: x[0])  # stable: same-time items keep feed order
            for _ts, name, title, link in lines[-MAX_POSTS_PER_CHAN:]:  # newest few
                line = '[\x02%s\x02] %s' % (name, title)
                if link:
                    line += ' – ' + _shorten(link)
                bot.say(line, chan)
                try:
                    _remember(bot, chan, title)
                except Exception:
                    LOG.exception('feeds: recent cache write failed')
                time.sleep(LINE_GAP)
            if extra > 0:
                bot.say('(+%d more feed items skipped)' % extra, chan)
                time.sleep(LINE_GAP)

        cutoff = int(time.time()) - SEEN_KEEP_DAYS * 86400
        _q(bot, "DELETE FROM feed_seen WHERE seen_at < :c", {'c': cutoff})
        _prune_recent(bot)
    except Exception:
        LOG.exception('feeds: poll error')
    finally:
        _poll_lock.release()


# ibot only starts @plugin.interval jobs when it connects to IRC, never on
# $rehash, so a plugin loaded by rehash would never poll. Run our own thread
# instead: started in setup(), stopped in shutdown() (which rehash calls).
_stop = threading.Event()


def _poller_loop(bot, stop):
    stop.wait(60)  # give the bot time to connect and join channels
    while not stop.is_set():
        try:
            _run_poll(bot)
        except Exception:
            LOG.exception('feeds: poller error')
        stop.wait(POLL_INTERVAL)
    LOG.info('feeds: poller stopped')


# ═══════════════════════════════ permissions ═════════════════════════

def _is_op(bot, channel, nick):
    """True if nick has +o, +a or +q in channel (voice/halfop don't count)."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return False
        v = chan.privileges.get(nick, 0)
        return isinstance(v, int) and bool(v & _OP_OR_HIGHER)
    except Exception:
        return False


def _known_account(bot, nick):
    """NickServ account ibot has seen for nick, if the network reports it."""
    try:
        core = getattr(bot, '_bot', bot)
        if hasattr(core, 'get_user_account'):
            return core.get_user_account(nick) or None
    except Exception:
        pass
    return None


def _is_feed_admin(bot, trigger, channel=None):
    """True if trigger.nick is a feed admin of channel (or of any channel
    when channel is None). Nicks are protected by NickServ; if the network
    also reports accounts and one was recorded, it must match too."""
    sql = "SELECT account FROM feed_admins WHERE nick = :n"
    params = {'n': str(trigger.nick).lower()}
    if channel:
        sql += " AND channel = :c"
        params['c'] = channel
    rows = _q(bot, sql, params, fetch=True)
    if not rows:
        return False
    acct = getattr(trigger, 'account', None)
    for (stored,) in rows:
        if not stored or not acct or str(acct).lower() == stored.lower():
            return True
    return False


# ═══════════════════════════════ commands ════════════════════════════

USAGE = ('Usage: $feed <on|off|sub <name>|unsub <name>|latest [name] [n]|list|status>  '
         '| admins: $feed <add <name> <url>|del <name>|poll|admin add/del/list <nick>>  '
         '| from PM: $feed #channel <cmd>')


@plugin.commands('feed', 'feeds')
@plugin.example('$feed on')
@plugin.example('$feed sub lobsters')
@plugin.example('$feed add lobsters https://lobste.rs/rss')
def feed_cmd(bot, trigger):
    """Manage RSS/Atom feeds for a channel. See $feed for usage."""
    args = (trigger.group(2) or '').split()
    in_pm = not str(trigger.sender).startswith('#')
    is_admin = bool(trigger.admin or trigger.owner)

    channel = None
    if args and args[0].startswith('#'):
        channel = args.pop(0).lower()
    elif not in_pm:
        channel = str(trigger.sender).lower()

    sub = args.pop(0).lower() if args else ('status' if channel else 'help')

    # ── bot-admin only: manage feed admins ──
    if sub == 'admin':
        if not is_admin:
            return bot.reply('Only bot admins can manage feed admins.')
        if not channel:
            return bot.reply('From PM, name the channel: $feed #channel admin <add|del|list> [nick]')
        action = args[0].lower() if args else 'list'
        if action == 'list':
            rows = _q(bot, "SELECT nick FROM feed_admins WHERE channel = :c ORDER BY nick",
                      {'c': channel}, fetch=True)
            return bot.say('Feed admins in %s: %s' % (channel, ', '.join(r[0] for r in rows) or 'none'))
        if action not in ('add', 'del', 'rm') or len(args) != 2:
            return bot.reply('Usage: $feed admin <add|del|list> [nick]')
        target = args[1]
        if action == 'add':
            if _q(bot, "SELECT 1 FROM feed_admins WHERE channel = :c AND nick = :n",
                  {'c': channel, 'n': target.lower()}, fetch=True):
                return bot.reply('%s is already a feed admin in %s.' % (target, channel))
            _q(bot, """INSERT INTO feed_admins (channel, nick, account, added_by, added_at)
                       VALUES (:c, :n, :a, :b, :t)""",
               {'c': channel, 'n': target.lower(), 'a': _known_account(bot, target),
                'b': str(trigger.nick), 't': int(time.time())})
            return bot.say('\x02%s\x02 is now a feed admin in %s.' % (target, channel))
        gone = _q(bot, "SELECT 1 FROM feed_admins WHERE channel = :c AND nick = :n",
                  {'c': channel, 'n': target.lower()}, fetch=True)
        if not gone:
            return bot.reply('%s is not a feed admin in %s.' % (target, channel))
        _q(bot, "DELETE FROM feed_admins WHERE channel = :c AND nick = :n",
           {'c': channel, 'n': target.lower()})
        return bot.say('\x02%s\x02 is no longer a feed admin in %s.' % (target, channel))

    # ── global feed list: bot admins or feed admins ──
    if sub in ('add', 'del', 'delete', 'rm', 'poll'):
        if not (is_admin or _is_feed_admin(bot, trigger)):
            return bot.reply('Only bot admins and feed admins can do that.')

        if sub == 'add':
            if len(args) != 2:
                return bot.reply('Usage: $feed add <name> <url>')
            name, url = args
            if not NAME_RE.match(name):
                return bot.reply('Name must be 1-32 chars: letters, digits, . _ -')
            if not _valid_url(url):
                return bot.reply('URL must be http:// or https://')
            if _get_feed(bot, name):
                return bot.reply('A feed named %s already exists.' % name)
            if _q(bot, "SELECT 1 FROM feed_list WHERE url = :u", {'u': url}, fetch=True):
                return bot.reply('That URL is already in the list.')
            try:
                _st, body, _e, _m = _fetch(url)
                n = len(_parse(body))
            except Exception as exc:
                return bot.reply('Could not read that feed: %s' % str(exc)[:150])
            _q(bot, """INSERT INTO feed_list (name, url, added_by, added_at)
                       VALUES (:n, :u, :b, :t)""",
               {'n': name, 'u': url, 'b': str(trigger.nick), 't': int(time.time())})
            return bot.reply('Added \x02%s\x02 (%d items found, existing ones won\'t be posted). '
                             'Ops can now $feed sub %s' % (name, n, name))

        if sub in ('del', 'delete', 'rm'):
            if len(args) != 1:
                return bot.reply('Usage: $feed del <name>')
            f = _get_feed(bot, args[0])
            if not f:
                return bot.reply('No feed named %s.' % args[0])
            with bot.db.engine.begin() as conn:
                for tbl in ('feed_subs', 'feed_seen'):
                    conn.execute(text('DELETE FROM %s WHERE feed_id = :i' % tbl), {'i': f[0]})
                conn.execute(text('DELETE FROM feed_list WHERE id = :i'), {'i': f[0]})
            return bot.reply('Removed \x02%s\x02 from all channels.' % f[1])

        if sub == 'poll':
            bot.reply('Polling feeds…')
            _run_poll(bot)
            return bot.reply('Done.')

    if sub == 'help':
        return bot.reply(USAGE)

    if sub == 'list':
        feeds = _q(bot, "SELECT id, name, fail_count FROM feed_list ORDER BY name", fetch=True)
        if not feeds:
            return bot.say('No feeds yet. A bot admin can add one with $feed add <name> <url>')
        mine = set()
        if channel:
            mine = {r[0] for r in _q(bot, "SELECT feed_id FROM feed_subs WHERE channel = :c",
                                     {'c': channel}, fetch=True)}
        parts = []
        for fid, name, fails in feeds:
            p = ('*' if fid in mine else '') + name
            if fails >= 3:
                p += '(!)'
            parts.append(p)
        tail = ' | * = subscribed in %s, (!) = failing' % channel if channel else ' | (!) = failing'
        return bot.say('Feeds (%d): %s%s' % (len(feeds), ', '.join(parts), tail))

    if not channel:
        return bot.reply('From PM, name the channel: $feed #channel %s' % sub)

    if sub == 'status':
        on = _chan_enabled(bot, channel)
        names = [r[0] for r in _q(bot, """SELECT f.name FROM feed_subs s
                                          JOIN feed_list f ON f.id = s.feed_id
                                          WHERE s.channel = :c ORDER BY f.name""",
                                  {'c': channel}, fetch=True)]
        state = '\x0303ON\x03' if on else '\x0304OFF\x03'
        return bot.say('Feeds in %s: %s | Subscribed: %s | Max %d posts per %d min'
                       % (channel, state, ', '.join(names) or 'none',
                          MAX_POSTS_PER_CHAN, POLL_INTERVAL // 60))

    # ── everything below changes a channel: op there, or bot admin ──
    if not (is_admin or _is_op(bot, channel, trigger.nick)
            or _is_feed_admin(bot, trigger, channel)):
        return bot.reply('You need to be an op (@) or feed admin in %s to do that.' % channel)

    if sub in ('on', 'enable'):
        _set_enabled(bot, channel, True, str(trigger.nick))
        n = _q(bot, "SELECT COUNT(*) FROM feed_subs WHERE channel = :c", {'c': channel}, fetch=True)[0][0]
        msg = 'Feeds \x02enabled\x02 in %s.' % channel
        if not n:
            msg += ' No subscriptions yet: see $feed list, then $feed sub <name>'
        return bot.say(msg)

    if sub in ('off', 'disable'):
        _set_enabled(bot, channel, False, str(trigger.nick))
        return bot.say('Feeds \x02disabled\x02 in %s.' % channel)

    if sub == 'latest':
        return _latest(bot, trigger, channel, args)

    if sub in ('sub', 'unsub'):
        if len(args) != 1:
            return bot.reply('Usage: $feed %s <name>' % sub)
        f = _get_feed(bot, args[0])
        if not f:
            return bot.reply('No feed named %s. See $feed list' % args[0])
        already = bool(_q(bot, "SELECT 1 FROM feed_subs WHERE channel = :c AND feed_id = :f",
                          {'c': channel, 'f': f[0]}, fetch=True))
        if sub == 'sub':
            if already:
                return bot.reply('%s is already subscribed to %s.' % (channel, f[1]))
            _q(bot, "INSERT OR IGNORE INTO feed_subs (channel, feed_id) VALUES (:c, :f)",
               {'c': channel, 'f': f[0]})
            msg = 'Subscribed %s to \x02%s\x02.' % (channel, f[1])
            if not _chan_enabled(bot, channel):
                msg += ' (feeds are OFF here; $feed on to start posting)'
            return bot.say(msg)
        if not already:
            return bot.reply('%s is not subscribed to %s.' % (channel, f[1]))
        _q(bot, "DELETE FROM feed_subs WHERE channel = :c AND feed_id = :f",
           {'c': channel, 'f': f[0]})
        return bot.say('Unsubscribed %s from \x02%s\x02.' % (channel, f[1]))

    return bot.reply(USAGE)


def _format(name, title, link):
    line = '[\x02%s\x02] %s' % (name, _clean(title) or '(untitled)')
    if link:
        line += ' – ' + _shorten(link)
    return line


def _latest(bot, trigger, channel, args):
    """Post the newest item(s) right now. Doesn't touch seen/posted state."""
    now = time.time()
    wait = LATEST_COOLDOWN - (now - _latest_last.get(channel, 0))
    if wait > 0 and not (trigger.admin or trigger.owner):
        return bot.reply('Try again in %ds.' % (int(wait) + 1))

    if args:
        f = _get_feed(bot, args[0])
        if not f:
            return bot.reply('No feed named %s. See $feed list' % args[0])
        n = 1
        if len(args) > 1 and args[1].isdigit():
            n = max(1, min(LATEST_MAX, int(args[1])))
        targets, per_feed = [(f[1], f[2])], n
        _latest_last[channel] = now
        try:
            _st, body, _e, _m = _fetch(f[2])
            items = []
            sigs = []
            for i in _parse(body):
                if _REPLY_RE.match(_clean(i[1])):
                    continue
                sig = _story_sig(_clean(i[1]))
                if _is_dupe(sig, sigs):
                    continue  # same story from another outlet
                sigs.append(sig)
                items.append(i)
                if len(items) >= per_feed:
                    break
        except Exception as exc:
            return bot.reply('Could not fetch %s right now: %s' % (f[1], str(exc)[:100]))
        for _iid, title, link in items:
            bot.say(_format(f[1], title, link), channel)
            time.sleep(LINE_GAP)
        return
    else:
        targets = [tuple(r) for r in _q(bot, """SELECT f.name, f.url FROM feed_subs s
                                               JOIN feed_list f ON f.id = s.feed_id
                                               WHERE s.channel = :c ORDER BY f.name""",
                                        {'c': channel}, fetch=True)]
        if not targets:
            return bot.reply('%s has no subscriptions. See $feed list' % channel)
    # All subscriptions: fetch in parallel, merge, newest LATEST_MAX by date.
    _latest_last[channel] = now

    def grab(target):
        name, url = target
        try:
            _st, body, _e, _m = _fetch(url)
            # all items, not just the first few: some feeds (lobsters, HN)
            # list stories by rank, so the newest one can be far down
            return [(ts, name, t, l) for _i, t, l, ts in _parse_dated(body)
                    if not _REPLY_RE.match(_clean(t))], None
        except Exception as exc:
            LOG.warning('feeds: latest %s failed: %s', name, exc)
            return [], name

    pool = []
    failed = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for items, bad in ex.map(grab, targets):
            pool.extend(items)
            if bad:
                failed.append(bad)

    seen_links = set()
    sigs = []
    picked = []
    for ts, name, title, link in sorted(pool, key=lambda x: x[0], reverse=True):
        key = (link or title).strip().lower()
        if key in seen_links:
            continue  # same story in two feeds
        sig = _story_sig(_clean(title))
        if _is_dupe(sig, sigs):
            continue  # same story from another outlet
        seen_links.add(key)
        sigs.append(sig)
        picked.append((name, title, link))
        if len(picked) >= LATEST_MAX:
            break
    picked = [_format(n, t, l) for n, t, l in picked]  # shortens long links

    if not picked:
        return bot.reply('Could not fetch any feeds right now.')
    for line in picked:
        bot.say(line, channel)
        time.sleep(LINE_GAP)
    if failed:
        bot.say('(could not reach: %s)' % ', '.join(failed), channel)


# ═══════════════════════════════ lifecycle ═══════════════════════════

def setup(bot):
    with bot.db.engine.begin() as conn:
        for stmt in _SCHEMA:
            conn.execute(text(stmt))

    # Stop a poller left over from a previous load, if shutdown() was skipped.
    memory = getattr(getattr(bot, '_bot', bot), 'memory', None)
    if memory is not None:
        old = memory.get('feeds_poller_stop')
        if old is not None:
            old.set()
        memory['feeds_poller_stop'] = _stop
    _stop.clear()
    threading.Thread(target=_poller_loop, args=(bot, _stop),
                     name='feeds-poller', daemon=True).start()
    LOG.info('feeds: plugin loaded, polling every %ds', POLL_INTERVAL)


def shutdown(bot):
    _stop.set()
