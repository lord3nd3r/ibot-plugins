# harambe.py — IRC user search service for Sopel
#
# Collects user data via WHOIS, CTCP VERSION, WHOX sweeps, and oper-privileged WHO replies.
# Stores data in SQLite. Supports wildcard search across: nick, ident, host,
# vhost, ip, name, account, email, server, version, channels.
#
# Commands (replied in the access channel):
#   !help / !commands       — show help
#   !ping / !p              — liveness check
#   !search / !s <field> <pattern>   — case-insensitive wildcard search
#   !csearch / !cs <field> <pattern> — case-sensitive wildcard search
#   !ip <addr|nick>         — IP lookup / geo info
#
# Config (in sopel .cfg):
#   [harambe]
#   oper_name     = myoperaccount
#   oper_password = secret
#   access_channel = #harambe            ; channel where commands are accepted + flood alerts posted
#   access_list   = Nick1, Nick2       ; additional nicks (always allowed)
#   db_path       = ~/.sopel/harambe.db ; SQLite database path for this instance
#   max_results   = 10                 ; max rows per search (default 10)
#   flood_window    = 60               ; mass-join detection window, seconds (default 60)
#   flood_threshold = 5                ; distinct nicks from one host joining the SAME
#                                      ; channel within the window before alert (default 5)
#   flood_cooldown  = 300              ; min seconds between alerts for same host+channel (default 300)
#   collect_channels = all             ; 'all' to WHOIS joiners in every channel, or channel name

from __future__ import annotations

import contextlib
import fnmatch
import ipaddress
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque

import requests
from sopel import plugin
from sopel.config.types import StaticSection, ValidatedAttribute, ListAttribute

LOG = logging.getLogger(__name__)

# ─────────────────────────── config ─────────────────────────────────

class HarambeSection(StaticSection):
    oper_name          = ValidatedAttribute('oper_name',          default='')
    oper_password      = ValidatedAttribute('oper_password',      default='')
    access_channel     = ValidatedAttribute('access_channel',     default='')
    access_list        = ListAttribute('access_list',             default=[])
    db_path            = ValidatedAttribute('db_path',            default='~/.sopel/harambe.db')
    max_results        = ValidatedAttribute('max_results',        default='10')
    flood_window       = ValidatedAttribute('flood_window',        default='60')
    flood_threshold    = ValidatedAttribute('flood_threshold',     default='5')
    flood_cooldown     = ValidatedAttribute('flood_cooldown',      default='300')
    collect_channels   = ValidatedAttribute('collect_channels',    default='all')
    probe_ctcp_version = ValidatedAttribute('probe_ctcp_version', default='true')


def configure(config):
    config.define_section('harambe', HarambeSection)
    config.harambe.configure_setting('oper_name',          'IRC oper account name')
    config.harambe.configure_setting('oper_password',      'IRC oper password')
    config.harambe.configure_setting('access_channel',     'Channel whose members can use search')
    config.harambe.configure_setting('access_list',        'Comma-separated nicks always allowed')
    config.harambe.configure_setting('db_path',            'SQLite database path for this ibot instance')
    config.harambe.configure_setting('max_results',        'Max results per search (default 10)')
    config.harambe.configure_setting('flood_window',        'Mass-join detection window in seconds (default 60)')
    config.harambe.configure_setting('flood_threshold',     'Distinct nicks from one host joining the same channel before alert (default 5)')
    config.harambe.configure_setting('flood_cooldown',      'Min seconds between flood alerts per host+channel (default 300)')
    config.harambe.configure_setting('collect_channels',     "'all' to track joins in every channel, or a channel name")
    config.harambe.configure_setting('probe_ctcp_version', 'Send CTCP VERSION requests during WHOIS enrichment (true/false, default true)')


def setup(bot):
    global _DB_PATH
    bot.config.define_section('harambe', HarambeSection)
    _DB_PATH = os.path.abspath(os.path.expanduser(bot.config.harambe.db_path))
    _init_db(bot)
    # Attempt to oper up immediately if credentials are set
    _oper_up(bot)
    LOG.info('harambe: plugin loaded')


def shutdown(bot):
    _sweeper_stop.set()
    with _sweeper_cv:
        _sweeper_cv.notify_all()
    LOG.info('harambe: shutting down')


# ─────────────────────────── helpers ─────────────────────────────────

SEARCHABLE_FIELDS = ('nick', 'ident', 'host', 'vhost', 'ip', 'name',
                     'account', 'email', 'server', 'version', 'channels')

_DB_PATH = os.path.expanduser('~/.sopel/harambe.db')
_db_lock  = threading.Lock()

# Pending WHOIS state: nick_lower → dict of gathered fields
_whois_pending: dict[str, dict] = {}
_whois_lock = threading.Lock()

# Pending CTCP VERSION replies: nick_lower → monotonic deadline
_ctcp_pending: dict[str, float] = {}
_ctcp_lock = threading.Lock()
_CTCP_TIMEOUT = 30
_CTCP_MAX_PENDING = 1000

# True once the server confirms oper with 381 RPL_YOUREOPER
_is_oper = False

# Channels for which a WHOX sweep was already issued this oper session
_who_sent: set = set()
# Channels that refused WHOX (fell back to plain WHO)
_who_fallback: set = set()
# Global WHO scan state
_global_scan: dict = {}
_global_scan_lock = threading.Lock()
# Global-scan WHOIS enrichment progress
_global_enrichment: dict = {
    'total': 0,
    'completed': 0,
    'started': 0.0,
    'destination': None,
    'who_complete': False,
    'next_report_percent': 20,
}
_global_enrichment_nicks: set = set()
# Outgoing probe queue: ("whois"|"version", nick).  Drained by ONE sweeper
# thread at a fixed pace.  The server won't flood-kick an oper, but the ibot
# core sends at ~1 line/s, so unpaced probes would queue ahead of (and
# delay) every other plugin's output.  The deque is capped.
_whois_queue: deque = deque()
_whois_queued: set = set()          # ("whois"|"version", nick_lower) in queue
_whois_queue_lock = threading.Lock()
_WHOIS_QUEUE_MAX = 50000
_WHOIS_GAP = 1.5                    # seconds between probes
_WHOIS_PENDING_MAX = 5000           # cap on in-flight WHOIS records
_sweeper_cv = threading.Condition(_whois_queue_lock)
_sweeper_stop = threading.Event()
_sweeper_thread = None

# Mass-join flood detection state
_join_events: list = []            # list of (timestamp, host, nick, channel)
_join_events_lock = threading.Lock()
_flood_alert_cooldown: dict = {}   # (host_lower, channel_lower) → last alert timestamp


# NickServ INFO query state
class NickServQuery:
    def __init__(self, nick: str):
        self.nick = nick
        self.nick_lower = nick.lower()
        self.event = threading.Event()
        self.data = {
            'nick': nick,
            'ns_registered': None,
            'ns_last_seen': None,
            'ns_email': None,
            'ns_options': None,
            'is_registered': None,
        }

_ns_queries: dict[str, NickServQuery] = {}
_ns_queries_lock = threading.Lock()
_ns_current_nick: str | None = None
_ns_timer: threading.Timer | None = None
_ns_timer_lock = threading.Lock()


def _is_valid_ip(addr: str) -> bool:
    """Return True if addr is a valid IPv4 or IPv6 address."""
    if not addr or not isinstance(addr, str):
        return False
    try:
        ipaddress.ip_address(addr.strip())
        return True
    except ValueError:
        return False


def _reset_ns_timer(q):
    global _ns_timer
    with _ns_timer_lock:
        if _ns_timer:
            _ns_timer.cancel()
        _ns_timer = threading.Timer(0.5, q.event.set)
        _ns_timer.start()


def _query_nickserv(bot, nick: str, timeout: float = 3.0) -> dict | None:
    nick_lower = nick.lower()
    query = NickServQuery(nick)

    with _ns_queries_lock:
        # Cancel/overwrite any existing query for this nick
        if nick_lower in _ns_queries:
            _ns_queries[nick_lower].event.set()
        _ns_queries[nick_lower] = query
        global _ns_current_nick
        _ns_current_nick = nick_lower

    bot.write(['PRIVMSG', 'NickServ', f'INFO {nick}'])

    # Wait for the event to be set (either by the notice parser, end of info, or a timeout)
    finished = query.event.wait(timeout)

    with _ns_queries_lock:
        _ns_queries.pop(nick_lower, None)
        if _ns_current_nick == nick_lower:
            _ns_current_nick = None

    if finished and query.data['is_registered'] is not None:
        return query.data
    return None


@contextlib.contextmanager
def _get_db():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_db(bot):
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    with _db_lock, _get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                nick_lower  TEXT PRIMARY KEY,
                nick        TEXT,
                ident       TEXT,
                host        TEXT,
                vhost       TEXT,
                ip          TEXT,
                name        TEXT,
                account     TEXT,
                email       TEXT,
                server      TEXT,
                version     TEXT,
                channels    TEXT,
                last_seen   INTEGER
            )
        ''')
        # Ensure indexes exist for fast lookups and clone searching
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_ip ON users(ip)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_host ON users(host)')

        # Check if NickServ columns exist, if not add them
        cursor = conn.execute('PRAGMA table_info(users)')
        columns = [row['name'] for row in cursor.fetchall()]

        new_cols = {
            'ns_registered': 'TEXT',
            'ns_last_seen': 'TEXT',
            'ns_email': 'TEXT',
            'ns_options': 'TEXT'
        }
        for col_name, col_type in new_cols.items():
            if col_name not in columns:
                conn.execute(f'ALTER TABLE users ADD COLUMN {col_name} {col_type}')
                LOG.info('harambe: added column %s to users table', col_name)

    LOG.info('harambe: database ready at %s', _DB_PATH)


def _upsert_user(data: dict):
    """Insert or update a user record, skipping None values (don't overwrite known data with None)."""
    nick_lower = data.get('nick_lower') or (data.get('nick') or '').lower()
    if not nick_lower:
        return
    # Hard guard: never store a record keyed by a channel name — that is
    # always a parsing bug (we've been bitten twice).
    if nick_lower.startswith(('#', '&')) or nick_lower in ('}', '{', '|', '~', '^'):
        LOG.warning('harambe: refusing to upsert bogus nick %r (data keys: %s)',
                    nick_lower, sorted(k for k, v in data.items() if v))
        return

    with _db_lock, _get_db() as conn:
        # Fetch existing row
        existing = conn.execute(
            'SELECT * FROM users WHERE nick_lower = ?', (nick_lower,)
        ).fetchone()

        merged = {
            'nick_lower': nick_lower,
            'nick':      data.get('nick'),
            'ident':     data.get('ident'),
            'host':      data.get('host'),
            'vhost':     data.get('vhost'),
            'ip':        data.get('ip'),
            'name':      data.get('name'),
            'account':   data.get('account'),
            'email':     data.get('email'),
            'server':    data.get('server'),
            'version':   data.get('version'),
            'channels':  data.get('channels'),
            'last_seen': data.get('last_seen', int(time.time())),
            'ns_registered': data.get('ns_registered'),
            'ns_last_seen':  data.get('ns_last_seen'),
            'ns_email':      data.get('ns_email'),
            'ns_options':    data.get('ns_options'),
        }

        if existing:
            # Prefer incoming non-None values, fallback to existing
            for k in merged:
                if merged[k] is None:
                    try:
                        merged[k] = existing[k]
                    except IndexError:
                        # In case columns weren't in existing row yet
                        merged[k] = None

        conn.execute('''
            INSERT INTO users
                (nick_lower, nick, ident, host, vhost, ip, name, account,
                 email, server, version, channels, last_seen,
                 ns_registered, ns_last_seen, ns_email, ns_options)
            VALUES
                (:nick_lower, :nick, :ident, :host, :vhost, :ip, :name, :account,
                 :email, :server, :version, :channels, :last_seen,
                 :ns_registered, :ns_last_seen, :ns_email, :ns_options)
            ON CONFLICT(nick_lower) DO UPDATE SET
                nick     = COALESCE(:nick,    nick),
                ident    = COALESCE(:ident,   ident),
                host     = COALESCE(:host,    host),
                vhost    = COALESCE(:vhost,   vhost),
                ip       = COALESCE(:ip,      ip),
                name     = COALESCE(:name,    name),
                account  = COALESCE(:account, account),
                email    = COALESCE(:email,   email),
                server   = COALESCE(:server,  server),
                version  = COALESCE(:version, version),
                channels = COALESCE(:channels, channels),
                last_seen = :last_seen,
                ns_registered = COALESCE(:ns_registered, ns_registered),
                ns_last_seen  = COALESCE(:ns_last_seen,  ns_last_seen),
                ns_email      = COALESCE(:ns_email,      ns_email),
                ns_options    = COALESCE(:ns_options,    ns_options)
        ''', merged)


def _oper_up(bot):
    """Send OPER command if credentials are configured."""
    try:
        name = bot.config.harambe.oper_name
        pw   = bot.config.harambe.oper_password
    except Exception:
        return
    if name and pw:
        bot.write(['OPER', name, pw])
        LOG.info('harambe: sent OPER %s', name)


def _is_authorized(bot, trigger) -> bool:
    """
    Returns True if the requester is allowed to use search commands.
    Allowed if:
      - Their nick is in the access_list config, OR
      - They are present in the configured access_channel
    """
    try:
        access_list    = [n.strip().lower() for n in bot.config.harambe.access_list]
        access_channel = (bot.config.harambe.access_channel or '').strip()
    except Exception:
        return False

    nick_lower = str(trigger.nick).lower()

    if nick_lower in access_list:
        return True

    if access_channel:
        # ibot's Channel.users is a SopelIdentifierMemory — case-insensitive,
        # IRC case-folding membership test works directly on nicks
        chan_obj = bot.channels.get(access_channel)
        if chan_obj and trigger.nick in chan_obj.users:
            return True

    return False


def _wildcard_to_sql(pattern: str) -> tuple[str, str]:
    """
    Convert an IRC-style wildcard pattern (* and ?) into a SQL LIKE expression.
    Returns (like_pattern, escape_char).
    Escapes literal % and _ with backslash, then maps * → % and ? → _.
    """
    escaped = pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    like    = escaped.replace('*', '%').replace('?', '_')
    return like, '\\'


def _row_to_str(row, exclude_fields: set | None = None) -> str:
    """Format a DB row as a Harambe-style result string."""
    nick    = row['nick']    or row['nick_lower']
    ident   = row['ident']   or '~unknown'
    host    = row['host']    or '*'
    skip    = exclude_fields or set()
    real_ip = row['ip'] if 'ip' not in skip else None
    parts   = [f'{nick}!{ident}@{real_ip or host}']

    if real_ip and host != real_ip:
        parts.append(f'[cloak={host}]')
        skip = skip | {'ip'}

    for field in ('vhost', 'name', 'ip', 'server', 'account', 'version', 'email', 'channels'):
        if field in skip:
            continue
        val = row[field]
        if val:
            parts.append(f'[{field}={val}]')

    return ' '.join(parts)


def _search(field: str, pattern: str, case_sensitive: bool, max_results: int):
    """Run a wildcard search against the DB. Returns list of Row objects, or empty list on SQL/glob error, or None if invalid field."""
    if field not in SEARCHABLE_FIELDS:
        return None  # invalid field

    like, esc = _wildcard_to_sql(pattern)
    col = field  # field name is validated — safe to interpolate

    try:
        if case_sensitive:
            # SQLite LIKE is case-insensitive by default; use GLOB for case sensitivity
            # but GLOB uses * and ? natively so we convert back
            glob_pat = pattern  # keep original wildcards; GLOB uses * and ?
            sql  = f'SELECT * FROM users WHERE {col} GLOB ? LIMIT ?'
            with _db_lock, _get_db() as conn:
                rows = conn.execute(sql, (glob_pat, max_results + 1)).fetchall()
        else:
            sql  = f'SELECT * FROM users WHERE {col} LIKE ? ESCAPE ? LIMIT ?'
            with _db_lock, _get_db() as conn:
                rows = conn.execute(sql, (like, esc, max_results + 1)).fetchall()
        return rows
    except sqlite3.OperationalError as e:
        LOG.warning('harambe: search query error for field %s, pattern %r: %s', field, pattern, e)
        return []


# ─────────────────────────── WHOIS collection ────────────────────────

def _start_whois(bot, nick: str):
    """Issue a WHOIS for a nick and start collecting replies."""
    nick_lower = nick.lower()
    with _whois_lock:
        if nick_lower not in _whois_pending and len(_whois_pending) >= _WHOIS_PENDING_MAX:
            # Drop the oldest half of never-completed WHOIS records
            for k in list(_whois_pending)[:_WHOIS_PENDING_MAX // 2]:
                _whois_pending.pop(k, None)
        # Merge into any in-flight WHOIS for this nick instead of clobbering
        pending = _whois_pending.setdefault(nick_lower, {})
        pending['nick'] = nick
    bot.write(['WHOIS', nick, nick])  # double nick = include idle time


def _finalize_whois(bot, nick_lower: str):
    """Called when WHOIS is complete (318) — flush data to DB."""
    with _whois_lock:
        data = _whois_pending.pop(nick_lower, None)
    if data:
        _upsert_user(data)
        progress_message = None
        progress_destination = None
        is_global_scan_nick = False
        with _global_scan_lock:
            if nick_lower in _global_enrichment_nicks:
                is_global_scan_nick = True
                _global_enrichment_nicks.remove(nick_lower)
                _global_enrichment['completed'] += 1
                total = _global_enrichment['total']
                completed = _global_enrichment['completed']
                percent = completed * 100 // total if total else 100
                if (_global_enrichment['who_complete']
                        and percent >= _global_enrichment['next_report_percent']):
                    milestone = min(100, (percent // 20) * 20)
                    _global_enrichment['next_report_percent'] = milestone + 20
                    progress_destination = _global_enrichment['destination']
                    progress_message = (
                        f'WHOIS enrichment: {completed}/{total} ({percent}%).'
                    )
        if progress_destination and progress_message:
            bot.say(progress_message, progress_destination)
        # Probe for CTCP VERSION if oper configured, allowed, and not part of a mass global scan
        oper_configured = bool((bot.config.harambe.oper_name or '').strip())
        probe_enabled = str(getattr(bot.config.harambe, 'probe_ctcp_version', 'true')).strip().lower() in ('true', '1', 'yes')
        if _is_oper and oper_configured and probe_enabled and not is_global_scan_nick:
            nick = data.get('nick', nick_lower)
            _request_version(bot, nick)


def _request_version(bot, nick: str):
    """Queue a CTCP VERSION probe (sent by the paced sweeper)."""
    _enqueue(bot, 'version', nick)


def _send_version(bot, nick: str):
    nick_lower = nick.lower()
    now = time.monotonic()
    with _ctcp_lock:
        for k in [k for k, dl in _ctcp_pending.items() if dl < now]:
            del _ctcp_pending[k]
        if nick_lower not in _ctcp_pending and len(_ctcp_pending) >= _CTCP_MAX_PENDING:
            return
        _ctcp_pending[nick_lower] = now + _CTCP_TIMEOUT
    bot.write(['PRIVMSG', nick, '\x01VERSION\x01'])


# ─────────────────────────── WHO sweep + flood detection ─────────────

def _cfg_int(bot, name: str, default: int) -> int:
    try:
        return int(getattr(bot.config.harambe, name))
    except Exception:
        return default


def _queue_whois(bot, nick: str):
    """Queue a nick for WHOIS (sent by the paced sweeper thread)."""
    _enqueue(bot, 'whois', nick)


def _enqueue(bot, kind: str, nick: str):
    if not nick:
        return False
    key = (kind, nick.lower())
    with _whois_queue_lock:
        if key in _whois_queued:
            return True
        if len(_whois_queue) >= _WHOIS_QUEUE_MAX:
            LOG.debug('harambe: probe queue full, dropping %s %s', kind, nick)
            return False
        _whois_queue.append((kind, nick))
        _whois_queued.add(key)
        _sweeper_cv.notify()
    _ensure_sweeper(bot)
    return True


def _ensure_sweeper(bot):
    global _sweeper_thread
    with _whois_queue_lock:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            return
        _sweeper_stop.clear()
        _sweeper_thread = threading.Thread(
            target=_sweeper, args=(bot,), daemon=True, name='harambe-sweeper')
        _sweeper_thread.start()


def _sweeper(bot):
    """Single paced worker: WHOIS / VERSION probes, one every _WHOIS_GAP s."""
    while not _sweeper_stop.is_set():
        with _whois_queue_lock:
            while not _whois_queue and not _sweeper_stop.is_set():
                _sweeper_cv.wait(timeout=30)
            if _sweeper_stop.is_set():
                return
            kind, nick = _whois_queue.popleft()
            _whois_queued.discard((kind, nick.lower()))
        try:
            if kind == 'version':
                _send_version(bot, nick)
            else:
                _start_whois(bot, nick)
        except Exception:
            LOG.exception('harambe: probe %s %s failed', kind, nick)
        _sweeper_stop.wait(_WHOIS_GAP)


def _sweep_channels(bot):
    """Send WHO for every joined channel (WHOX where supported)."""
    for chan in list(bot.channels.keys()):
        chan_l = str(chan).lower()
        if chan_l in _who_sent:
            continue
        _who_sent.add(chan_l)
        # WHOX: %c=chan %u=ident %h=host %s=server %n=nick %a=account %r=realname
        bot.write(['WHO', str(chan), '%cuhsnfar,995'])
        LOG.info('harambe: WHO sweep sent for %s', chan)


def _start_global_scan(bot, requester: str) -> bool:
    """Start a network-wide WHO scan. Returns False if one is already running."""
    with _global_scan_lock:
        if _global_scan.get('active'):
            return False
        _global_scan.update({
            'active': True,
            'requester': requester,
            'started': time.time(),
            'count': 0,
        })
        _global_enrichment.update({
            'total': 0,
            'completed': 0,
            'started': time.time(),
            'destination': requester,
            'who_complete': False,
            'next_report_percent': 20,
        })
        _global_enrichment_nicks.clear()
    LOG.info('harambe: network-wide WHO scan started for %s', requester)
    bot.write(['WHO', '*'])
    return True


def _record_join(bot, nick: str, host: str, channel: str):
    """Track joins for mass-join flood detection; alert on threshold.

    Alerts only when DISTINCT nicks from the same host join the SAME channel
    within the window — the botnet/clonetroller signature. A single user
    rejoining many channels after a reconnect does not trip this.
    """
    if not host:
        return
    now = time.time()
    window    = _cfg_int(bot, 'flood_window', 60)
    threshold = _cfg_int(bot, 'flood_threshold', 5)
    cooldown  = _cfg_int(bot, 'flood_cooldown', 300)
    host_l    = host.lower()
    chan_l    = channel.lower()

    with _join_events_lock:
        _join_events.append((now, host_l, nick, channel))
        # Prune old events
        cutoff = now - window
        while _join_events and _join_events[0][0] < cutoff:
            _join_events.pop(0)
        # Distinct nicks from this host in THIS channel within the window
        nicks = {e[2] for e in _join_events
                 if e[1] == host_l and e[3].lower() == chan_l}

        if len(nicks) < threshold:
            return

        cd_key = (host_l, chan_l)
        last_alert = _flood_alert_cooldown.get(cd_key, 0)
        if now - last_alert < cooldown:
            return
        _flood_alert_cooldown[cd_key] = now

    access_channel = (bot.config.harambe.access_channel or '').strip()
    if not access_channel:
        return
    bot.say(
        f'\x02MASS-JOIN ALERT\x02: {len(nicks)} nicks from host {host} '
        f'joined {channel} in {window}s — nicks: {", ".join(sorted(nicks))}',
        access_channel,
    )


@plugin.interval(30)
def check_floods(bot):
    """Prune stale join events and cooldown entries periodically."""
    now = time.time()
    window = _cfg_int(bot, 'flood_window', 60)
    with _join_events_lock:
        cutoff = now - window
        while _join_events and _join_events[0][0] < cutoff:
            _join_events.pop(0)
        for host in list(_flood_alert_cooldown):
            if now - _flood_alert_cooldown[host] > 3600:
                del _flood_alert_cooldown[host]


# ─────────────────────────── IRC event handlers ───────────────────────

@plugin.event('JOIN')
@plugin.priority('low')
@plugin.thread(True)
def on_join(bot, trigger):
    """Collect data on joins: host snapshot, flood tracking, queue WHOIS."""
    nick = str(trigger.nick)
    if nick.lower() == bot.nick.lower():
        return

    channel = str(trigger.sender)
    host    = trigger.host or ''
    ident   = trigger.user or ''
    account = trigger.account  # from extended-join / account-tag (None or '*')

    # Record whatever we know immediately
    data = {'nick': nick, 'host': host, 'ident': ident}
    if account and account != '*':
        data['account'] = account
    if host or ident:
        _upsert_user(data)

    # Mass-join flood detection (all channels)
    _record_join(bot, nick, host, channel)

    # Deep collection (WHOIS + VERSION) is oper-only
    if not _is_oper:
        return

    collect = (bot.config.harambe.collect_channels or 'all').strip().lower()
    if collect not in ('all', '*') and channel.lower() != collect:
        return
    _queue_whois(bot, nick)


@plugin.event('001')  # RPL_WELCOME — we connected
@plugin.priority('low')
@plugin.thread(True)
def on_welcome(bot, trigger):
    """Re-send OPER after connecting (in case the bot restarts mid-session)."""
    global _is_oper
    _is_oper = False  # reset until server confirms with 381
    _who_sent.clear()
    _who_fallback.clear()
    _oper_up(bot)


@plugin.event('381')  # RPL_YOUREOPER — server confirmed oper
@plugin.priority('low')
@plugin.thread(True)
def on_youreoper(bot, trigger):
    """Set oper flag when the server confirms oper status (381)."""
    global _is_oper
    _is_oper = True
    LOG.info('harambe: oper confirmed — CTCP VERSION collection enabled')
    # Bulk-harvest everyone already in our channels (cold-start gap)
    _sweep_channels(bot)


@plugin.event('354')  # RPL_WHOSPCRPL — WHOX reply
@plugin.priority('low')
@plugin.thread(True)
def whox_reply(bot, trigger):
    """Parse WHOX 354 replies from our sweep (query token 995).
    Wire: <me> 995 <chan> <ident> <host> <server> <nick> <account> :<realname>
    ibot keeps the botnick as args[0], so:
    args = [me, 995, chan, ident, host, server, nick, account]
    """
    args = trigger.args
    if len(args) < 8 or args[1] != '995':
        return
    account = args[7]
    _upsert_user({
        'nick':    args[6],
        'ident':   args[3],
        'host':    args[4],
        'server':  args[5],
        'account': None if account in ('0', '*') else account,
        'name':    trigger.text or None,
    })


@plugin.event('352')  # RPL_WHOREPLY — plain WHO fallback
@plugin.priority('low')
@plugin.thread(True)
def who_reply(bot, trigger):
    """Parse plain WHO 352 replies (fallback or network-wide scan).
    Wire: <me> <chan> <ident> <host> <server> <nick> <flags> :<hop> <realname>
    ibot keeps the botnick as args[0], so:
    args = [me, chan, ident, host, server, nick, flags]
    Plain WHO gives no IP, so queue a rate-limited WHOIS for oper visibility.
    """
    args = trigger.args
    if len(args) < 7:
        return
    chan = args[1]
    nick = args[5]
    record = {
        'nick':  nick,
        'ident': args[2],
        'host':  args[3],
        'server': args[4],
        'name':  re.sub(r'^\d+\s+', '', trigger.text or ''),
    }

    with _global_scan_lock:
        if _global_scan.get('active'):
            _global_scan['count'] += 1
            if nick.lower() not in _global_enrichment_nicks:
                _global_enrichment_nicks.add(nick.lower())
                _global_enrichment['total'] += 1
            global_scan_active = True
        else:
            global_scan_active = False

    if global_scan_active:
        _upsert_user(record)
        # The connection can remain oper after a rehash even when _is_oper is
        # stale, so always queue enrichment for an explicit global scan.
        _queue_whois(bot, nick)
        return

    if chan.lower() not in _who_fallback:
        return  # not from our sweep (some other plugin asked for WHO)
    _upsert_user(record)
    if _is_oper:
        _queue_whois(bot, nick)


@plugin.event('315')  # RPL_ENDOFWHO — global scan or channel fallback completed
@plugin.priority('low')
@plugin.thread(True)
def who_end(bot, trigger):
    """Report completion for the network-wide WHO scan."""
    args = trigger.args
    # On IRC, 315 wire format is: <me> <mask> :End of /WHO list.
    # Only complete global scan if this 315 corresponds to the global scan mask ('*')
    if len(args) > 1 and args[1] != '*':
        return

    with _global_scan_lock:
        if not _global_scan.get('active'):
            return
        requester = _global_scan.get('requester')
        count = int(_global_scan.get('count', 0))
        elapsed = max(1, int(time.time() - _global_scan.get('started', time.time())))
        _global_scan.clear()
        _global_enrichment['who_complete'] = True
        total = int(_global_enrichment['total'])
        completed = int(_global_enrichment['completed'])
        percent = completed * 100 // total if total else 0
    if requester:
        bot.say(
            f'Network scan complete: collected {count} user records in {elapsed}s; '
            f'WHOIS enrichment: {completed}/{total} ({percent}%).',
            requester,
        )
    LOG.info('harambe: network-wide WHO scan complete: %d records in %ds', count, elapsed)


@plugin.event('403')  # ERR_NOSUCHCHANNEL — WHOX refused, fall back to plain WHO
@plugin.priority('low')
@plugin.thread(True)
def who_fallback(bot, trigger):
    # args = [me, channel, :error text]
    args = trigger.args
    if len(args) < 2:
        return
    chan = args[1]
    if not chan.startswith(('#', '&')):
        return
    chan_l = chan.lower()
    if chan_l not in _who_sent or chan_l in _who_fallback:
        return
    _who_fallback.add(chan_l)
    LOG.info('harambe: WHOX refused for %s — falling back to plain WHO', chan)
    bot.write(['WHO', chan])


# ── WHOIS reply numerics ──────────────────────────────────────────────

@plugin.event('311')  # RPL_WHOISUSER  :nick!user@host * :realname
@plugin.priority('low')
@plugin.thread(True)
def whois_user(bot, trigger):
    # args: botnick target_nick ident host * :realname
    args = trigger.args
    if len(args) < 4:
        return
    nick_lower = args[1].lower()
    with _whois_lock:
        if nick_lower not in _whois_pending:
            return
        _whois_pending[nick_lower].update({
            'nick':  args[1],
            'ident': args[2],
            'host':  args[3],
            'name':  args[-1],
        })


@plugin.event('312')  # RPL_WHOISSERVER  :nick server :info
@plugin.priority('low')
@plugin.thread(True)
def whois_server(bot, trigger):
    args = trigger.args
    if len(args) < 3:
        return
    nick_lower = args[1].lower()
    with _whois_lock:
        if nick_lower in _whois_pending:
            _whois_pending[nick_lower]['server'] = args[2]


@plugin.event('319')  # RPL_WHOISCHANNELS  :nick :channels
@plugin.priority('low')
@plugin.thread(True)
def whois_channels(bot, trigger):
    args = trigger.args
    if len(args) < 2:
        return
    nick_lower = args[1].lower()
    raw_chans  = (trigger.text or args[-1]).strip()
    with _whois_lock:
        if nick_lower in _whois_pending:
            existing = _whois_pending[nick_lower].get('channels')
            if existing:
                _whois_pending[nick_lower]['channels'] = f'{existing} {raw_chans}'
            else:
                _whois_pending[nick_lower]['channels'] = raw_chans


@plugin.event('330')  # RPL_WHOISACCOUNT  :nick account :is logged in as
@plugin.priority('low')
@plugin.thread(True)
def whois_account(bot, trigger):
    args = trigger.args
    if len(args) < 3:
        return
    nick_lower = args[1].lower()
    with _whois_lock:
        if nick_lower in _whois_pending:
            _whois_pending[nick_lower]['account'] = args[2]


@plugin.event('338')  # RPL_WHOISACTUALLY  :nick real@host :actual IP  (oper-visible)
@plugin.priority('low')
@plugin.thread(True)
def whois_actually(bot, trigger):
    """338 gives us the real host and IP when the bot has oper.
    Format: <me> <nick> <user@realhost> <ip> :... (ip optional on some ircds)
    """
    args = trigger.args
    if len(args) < 3:
        return
    nick_lower = args[1].lower()
    real_host = None
    real_ip = None

    uh = args[2]
    if '@' in uh:
        real_host = uh.split('@', 1)[1]
    elif _is_valid_ip(uh):
        real_ip = uh
    if len(args) > 3:
        candidate = args[3]
        if '@' in candidate:
            real_host = candidate.split('@', 1)[1]
        elif _is_valid_ip(candidate) or re.match(r'^[\d.]+$|^[0-9a-fA-F:]+$', candidate):
            real_ip = candidate

    with _whois_lock:
        if nick_lower not in _whois_pending:
            return
        if real_ip:
            _whois_pending[nick_lower]['ip'] = real_ip
        if real_host:
            _whois_pending[nick_lower]['host'] = real_host


@plugin.event('378')  # RPL_WHOISHOST — "is connecting from user@realhost ip" (oper)
@plugin.priority('low')
@plugin.thread(True)
def whois_host(bot, trigger):
    """378 exposes the real host/IP behind a vhost on UnrealIRCd-family nets."""
    args = trigger.args
    if len(args) < 2:
        return
    nick_lower = args[1].lower()
    text = (trigger.text or '').strip()
    # "is connecting from user@realhost 1.2.3.4"
    m = re.search(r'(\S+)@(\S+)\s+([\d.]+|[0-9a-fA-F:]+)\s*$', text)
    if not m:
        return
    with _whois_lock:
        if nick_lower not in _whois_pending:
            return
        _whois_pending[nick_lower]['ident'] = m.group(1)
        _whois_pending[nick_lower]['host']  = m.group(2)
        _whois_pending[nick_lower]['ip']    = m.group(3)


@plugin.event('671')  # RPL_WHOISSECURE  :nick :is using a secure connection
@plugin.priority('low')
def whois_secure(bot, trigger):
    pass  # We don't need to track this but don't let it be ignored noisily


@plugin.event('318')  # RPL_ENDOFWHOIS
@plugin.priority('low')
@plugin.thread(True)
def whois_end(bot, trigger):
    args = trigger.args
    if len(args) < 2:
        return
    nick_lower = args[1].lower()
    _finalize_whois(bot, nick_lower)


@plugin.event('320')  # RPL_WHOISSPECIAL (email on some networks like Rizon)
@plugin.priority('low')
@plugin.thread(True)
def whois_special(bot, trigger):
    """Rizon sends email in 320 as 'is identified for this nick (email@addr)'."""
    args = trigger.args
    if len(args) < 2:
        return
    nick_lower = args[1].lower()
    text = (trigger.text or '').strip()
    # Try to extract an email address from the text
    m = re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', text)
    if m:
        with _whois_lock:
            if nick_lower in _whois_pending:
                _whois_pending[nick_lower]['email'] = m.group(0)


# ── CTCP VERSION reply ────────────────────────────────────────────────

@plugin.event('NOTICE')
@plugin.priority('low')
@plugin.thread(True)
def ctcp_version_reply(bot, trigger):
    """Catch incoming CTCP VERSION replies.

    Replies arrive as NOTICE with ctcp type VERSION. ibot's @plugin.ctcp
    dispatch only covers PRIVMSG, so we hook NOTICE directly.
    (Note: on_nickserv_notice also hooks NOTICE but returns early for
    non-NickServ senders, so both coexist safely.)
    """
    if trigger.ctcp != 'VERSION':
        return
    # trigger.text keeps the CTCP wrapper: '\x01VERSION <string>\x01'
    version = (trigger.text or '').strip().strip('\x01')
    if version.upper().startswith('VERSION'):
        version = version[7:].strip()
    if not version:
        return

    nick_lower = str(trigger.nick).lower()
    with _ctcp_lock:
        _ctcp_pending.pop(nick_lower, None)

    _upsert_user({'nick': str(trigger.nick), 'version': version})
    LOG.debug('harambe: VERSION from %s: %s', trigger.nick, version)


# ── Track vhost changes (396) ─────────────────────────────────────────

@plugin.event('396')  # RPL_YOURHOSTIS / vhost applied
@plugin.priority('low')
@plugin.thread(True)
def on_vhost_set(bot, trigger):
    """396 is sent when a user mode / vhost is applied."""
    pass


# ── NICK changes — update DB ──────────────────────────────────────────

@plugin.event('NICK')
@plugin.priority('low')
@plugin.thread(True)
def on_nick_change(bot, trigger):
    """When a user changes nick, create/link a new DB entry for the new nick."""
    old_nick = str(trigger.nick)
    new_nick  = str(trigger.args[0]) if trigger.args else ''
    if not new_nick or old_nick.lower() == bot.nick.lower():
        return

    # Copy old record to new nick then re-WHOIS
    with _db_lock, _get_db() as conn:
        row = conn.execute(
            'SELECT * FROM users WHERE nick_lower = ?', (old_nick.lower(),)
        ).fetchone()

    if row:
        data = dict(row)
        data['nick']      = new_nick
        data['nick_lower'] = new_nick.lower()
        _upsert_user(data)

    if _is_oper:
        _queue_whois(bot, new_nick)


# ── NickServ notice parser ────────────────────────────────────────────

@plugin.event('NOTICE')
@plugin.priority('low')
@plugin.thread(True)
def on_nickserv_notice(bot, trigger):
    """Parse incoming NickServ INFO notices across multiple IRC services (Anope, Atheme, etc.)."""
    # Only interested in NickServ
    if str(trigger.nick).lower() != 'nickserv':
        return

    text = str(trigger).strip()
    if not text:
        return

    global _ns_current_nick

    # Clean formatting/colors
    text_clean = re.sub(r'\x03\d{0,2}|\x02|\x0f|\x16|\x1f', '', text)

    # Check for "isn't registered" or "is not registered" or "No such registration"
    m_not_reg = re.match(
        r'^(?:Nick\s+)?[\"\']?(\S+?)[\"\']?\s+(?:is\s+not|isn\'t|is\s+not\s+a)\s+registered'
        r'|^(?:No\s+(?:such\s+registration|info)\s+for\s+(?:nickname\s+)?[\"\']?(\S+?)[\"\']?(?:\.|$))',
        text_clean,
        re.IGNORECASE
    )
    if m_not_reg:
        target_nick = (m_not_reg.group(1) or m_not_reg.group(2)).lower().rstrip(':').strip('()\'"')
        with _ns_queries_lock:
            if target_nick in _ns_queries:
                q = _ns_queries[target_nick]
                q.data['is_registered'] = False
                q.event.set()
            elif _ns_current_nick and _ns_current_nick in _ns_queries:
                q = _ns_queries[_ns_current_nick]
                q.data['is_registered'] = False
                q.event.set()
            if _ns_current_nick == target_nick:
                _ns_current_nick = None
        return

    # Check for End of Info marker (e.g., "*** End of Info ***", "End of INFO")
    if re.match(r'^\*{0,3}\s*End of (?:Info|INFO)\s*\*{0,3}', text_clean, re.IGNORECASE):
        with _ns_queries_lock:
            target_key = _ns_current_nick or (next(iter(_ns_queries.keys())) if len(_ns_queries) == 1 else None)
            if target_key and target_key in _ns_queries:
                q = _ns_queries[target_key]
                if q.data['is_registered'] is None:
                    q.data['is_registered'] = True
                q.event.set()
        return

    # Check for "<nick> is <something>" or "Information on/for <nick>" or "Account: <nick>"
    m_start = re.match(
        r'^(?:Information\s+(?:on|for)|Nick|Account)\s*[:]?\s*[\"\']?(\S+?)[\"\']?(?:\s*\(|\s*:|\s+is|\s*$)'
        r'|^[\"\']?(\S+?)[\"\']?\s+is\s+(?!not\s+|isn\'t)',
        text_clean,
        re.IGNORECASE
    )
    if m_start:
        target_nick = (m_start.group(1) or m_start.group(2)).lower().rstrip(':').strip('()\'"')
        with _ns_queries_lock:
            if target_nick in _ns_queries:
                _ns_current_nick = target_nick
                _ns_queries[target_nick].data['is_registered'] = True
                _reset_ns_timer(_ns_queries[target_nick])
            elif _ns_current_nick and _ns_current_nick in _ns_queries:
                _ns_queries[_ns_current_nick].data['is_registered'] = True
                _reset_ns_timer(_ns_queries[_ns_current_nick])

    # Find the active query object
    with _ns_queries_lock:
        active_key = _ns_current_nick or (next(iter(_ns_queries.keys())) if len(_ns_queries) == 1 else None)
        if not active_key or active_key not in _ns_queries:
            return
        q = _ns_queries[active_key]

    # Parse fields
    m_reg = re.search(r'^(?:Time\s+registered|User\s+reg\.|Registered(?:\s+on)?|Account\s+created)\s*:\s*(.+)', text_clean, re.IGNORECASE)
    if m_reg:
        q.data['ns_registered'] = m_reg.group(1).strip()
        q.data['is_registered'] = True
        _reset_ns_timer(q)
        return

    m_seen = re.search(r'^(?:Last\s+seen(?:\s+time)?|Last\s+quit)\s*:\s*(.+)', text_clean, re.IGNORECASE)
    if m_seen:
        q.data['ns_last_seen'] = m_seen.group(1).strip()
        q.data['is_registered'] = True
        _reset_ns_timer(q)
        return

    m_email = re.search(r'^(?:E-?mail(?:\s+address)?)\s*:\s*(.+)', text_clean, re.IGNORECASE)
    if m_email:
        q.data['ns_email'] = m_email.group(1).strip()
        q.data['is_registered'] = True
        _reset_ns_timer(q)
        return

    m_opts = re.search(r'^(?:Options|Flags|Metadata)\s*:\s*(.+)', text_clean, re.IGNORECASE)
    if m_opts:
        q.data['ns_options'] = m_opts.group(1).strip()
        q.data['is_registered'] = True
        _reset_ns_timer(q)
        return

    # Any other notice line from NickServ during an active query confirms registration and resets timer
    q.data['is_registered'] = True
    _reset_ns_timer(q)



# ─────────────────────────── access control ──────────────────────────

def _reply(bot, dest: str, text: str):
    """Send a message to a channel (or nick if in PM)."""
    bot.say(text, dest)


# ─────────────────────────── commands ────────────────────────────────

# Harambe listens for !commands in PM — trigger on any PRIVMSG matching !<cmd>
# We use a catch-all rule on PRIVMSGs and dispatch manually so we can use
# ! as the prefix without changing the bot's global prefix.

_CMD_RE = re.compile(
    r'^\s*!(?P<cmd>help|commands|ping|p|search|s|csearch|cs|ip|clones|info|last|count|ccount|stats|scan|scanall|scanstatus)(?:\s+(?P<rest>.*))?$',
    re.IGNORECASE
)


@plugin.rule(r'^\s*!(?:help|commands|ping|p|search|s|csearch|cs|ip|clones|info|last|count|ccount|stats|scan|scanall|scanstatus)(?:\s|$)')
@plugin.priority('low')
@plugin.thread(True)
def harambe_dispatch(bot, trigger):
    """Dispatch all Harambe ! commands."""
    raw = str(trigger).strip()
    m = _CMD_RE.match(raw)
    if not m:
        return

    # Only respond in the configured access_channel — ignore everything else
    access_channel = (bot.config.harambe.access_channel or '').strip()
    if str(trigger.sender).lower() != access_channel.lower():
        return

    nick    = str(trigger.nick)
    dest    = str(trigger.sender)  # channel name
    cmd     = m.group('cmd').lower()
    rest    = (m.group('rest') or '').strip()

    if cmd in ('help', 'commands'):
        _cmd_help(bot, nick)  # send help via PM to the requester
        return

    if cmd in ('ping', 'p'):
        _reply(bot, dest, 'PONG! I am alive and watching.')
        return

    if cmd == 'scan':
        if not _is_authorized(bot, trigger):
            _reply(bot, dest, 'Access denied. You are not authorized to use Harambe.')
        else:
            # Do not gate on _is_oper here: that flag is only set by a fresh 381
            # and can be stale after reload while the connection remains oper.
            _who_sent.clear()
            _who_fallback.clear()
            _sweep_channels(bot)
            _reply(bot, dest, 'Started a WHO scan of all joined channels.')
        return

    if cmd == 'scanall':
        if not _is_authorized(bot, trigger):
            _reply(bot, dest, 'Access denied. You are not authorized to use Harambe.')
        elif _start_global_scan(bot, dest):
            _reply(bot, dest, 'Started a network-wide WHO scan. This may take a moment.')
        else:
            _reply(bot, dest, 'A network-wide scan is already running.')
        return

    if cmd == 'scanstatus':
        if not _is_authorized(bot, trigger):
            _reply(bot, dest, 'Access denied. You are not authorized to use Harambe.')
        else:
            _cmd_scanstatus(bot, dest)
        return

    # All search commands require authorization
    if not _is_authorized(bot, trigger):
        _reply(bot, dest, 'Access denied. You are not authorized to use Harambe.')
        return

    if cmd in ('search', 's'):
        _cmd_search(bot, dest, rest, case_sensitive=False)
    elif cmd in ('csearch', 'cs'):
        _cmd_search(bot, dest, rest, case_sensitive=True)
    elif cmd == 'ip':
        _cmd_ip(bot, dest, rest)
    elif cmd == 'clones':
        _cmd_clones(bot, dest, rest)
    elif cmd == 'info':
        _cmd_info(bot, dest, rest)
    elif cmd == 'last':
        _cmd_last(bot, dest, rest)
    elif cmd in ('count', 'ccount'):
        _cmd_count(bot, dest, rest, case_sensitive=(cmd == 'ccount'))
    elif cmd == 'stats':
        _cmd_stats(bot, dest)


def _cmd_help(bot, dest: str):
    lines = [
        'Harambe user search — fields: nick ident host vhost ip name account email server version channels',
        'Wildcards: * = any chars, ? = one char  |  Example: !s nick ex*ple  or  !s host *.example.com',
        '!search <field> <pattern>  (!s)  — case-insensitive search',
        '!csearch <field> <pattern> (!cs) — case-sensitive search',
        '!ip <address|nick>               — geo lookup for an IP, or look up a nick\'s IP',
        '!clones <nick|ip>                — find all nicks sharing the same IP or host/vhost',
        '!info <nick>                     — retrieve full info report (includes live NickServ query)',
        '!last <nick>                     — check when the user was last seen',
        '!count/!ccount <field> <pattern> — count matching records (case-insensitive/sensitive)',
        '!stats                           — show database metrics',
        '!scan                            — scan users in joined channels',
        '!scanall                         — network-wide WHO scan (oper)',
        '!scanstatus                      — show global WHOIS enrichment progress',
        '!ping (!p)                       — liveness check',
        '!help (!commands)                — this message',
    ]
    for line in lines:
        _reply(bot, dest, line)


def _cmd_scanstatus(bot, dest: str):
    """Report progress for the most recent global scan's WHOIS enrichment."""
    with _global_scan_lock:
        total = int(_global_enrichment['total'])
        completed = int(_global_enrichment['completed'])
        active = bool(_global_scan.get('active'))
    with _whois_queue_lock:
        queued = len(_whois_queue)
    with _whois_lock:
        in_flight = len(_whois_pending)

    if not total:
        _reply(bot, dest, 'No global scan enrichment is in progress.')
        return

    percent = completed * 100 // total
    state = 'WHO pass running' if active else 'WHO pass complete'
    _reply(
        bot,
        dest,
        f'Global scan: {completed}/{total} enriched ({percent}%); '
        f'{queued} queued, {in_flight} in flight ({state}).',
    )


def _cmd_search(bot, dest: str, rest: str, case_sensitive: bool):
    """Handle !search / !s / !csearch / !cs."""
    # Parse flags like -version, -email, etc. to exclude fields from output
    exclude_fields = set()
    tokens = rest.split()
    clean_tokens = []
    for tok in tokens:
        if tok.startswith('-') and tok[1:].lower() in SEARCHABLE_FIELDS:
            exclude_fields.add(tok[1:].lower())
        else:
            clean_tokens.append(tok)

    rest = ' '.join(clean_tokens)
    parts = rest.split(None, 1)
    if len(parts) < 2:
        _reply(bot, dest, 'Usage: !s <field> <pattern> [-version]  (fields: nick ident host vhost ip name account email server version channels)')
        return

    field   = parts[0].lower()
    pattern = parts[1]

    if field not in SEARCHABLE_FIELDS:
        _reply(bot, dest,
                f'Unknown field \'{field}\'. Valid fields: {" ".join(SEARCHABLE_FIELDS)}')
        return

    try:
        max_results = int(bot.config.harambe.max_results)
    except Exception:
        max_results = 10

    rows = _search(field, pattern, case_sensitive, max_results)

    if rows is None:
        _reply(bot, dest, f'Invalid field: {field}')
        return

    if not rows:
        _reply(bot, dest, 'No results')
        return

    truncated = len(rows) > max_results
    for i, row in enumerate(rows[:max_results], start=1):
        _reply(bot, dest, f'{i}: {_row_to_str(row, exclude_fields=exclude_fields)}')

    if truncated:
        _reply(bot, dest, f'Results truncated at {max_results}. Refine your search.')
    else:
        _reply(bot, dest, 'End of results')


def _cmd_ip(bot, dest: str, rest: str):
    """Handle !ip — geo lookup for an IP address, CIDR prefix, or nick."""
    target = rest.strip()
    if not target:
        _reply(bot, dest, 'Usage: !ip <address|nick>')
        return

    # Check if target is an IP address vs a nickname
    if _is_valid_ip(target) or re.search(r'[.:]', target):
        ip_target = target
    else:
        # Treat as a nick — look up their IP in the DB
        with _db_lock, _get_db() as conn:
            row = conn.execute(
                'SELECT ip, nick FROM users WHERE nick_lower = ?',
                (target.lower(),)
            ).fetchone()
        if row and row['ip']:
            ip_target = row['ip']
        elif row:
            _reply(bot, dest, f'{target} is known but IP is not available.')
            return
        else:
            _reply(bot, dest, f'No record found for nick: {target}')
            return

    # Validate ip_target contains only valid IP/hostname characters before requesting API
    if not re.match(r'^[a-zA-Z0-9.:_-]+$', ip_target):
        _reply(bot, dest, f'Invalid IP address or target: {ip_target}')
        return

    # Geo lookup via ipinfo.io (matches original Harambe output)
    try:
        resp = requests.get(
            f'https://ipinfo.io/{ip_target}/json',
            timeout=5,
        )
        data = resp.json()
    except Exception as e:
        LOG.warning('harambe: ipinfo request failed for %s: %s', ip_target, e)
        _reply(bot, dest, f'IP lookup failed: {e}')
        return

    if 'error' in data:
        _reply(bot, dest, f'Lookup failed: {data["error"].get("message", "unknown error")}')
        return

    # Output as a single line, e.g.:
    # ip: 1.2.3.4 | city: Albany | region: New York | country: US | ...
    fields = [
        ('ip',       data.get('ip', ip_target)),
        ('city',     data.get('city')),
        ('region',   data.get('region')),
        ('country',  data.get('country')),
        ('loc',      data.get('loc')),
        ('org',      data.get('org')),
        ('postal',   data.get('postal')),
        ('timezone', data.get('timezone')),
    ]
    parts = [f'{name}: {val}' for name, val in fields if val]
    _reply(bot, dest, ' | '.join(parts))


def _cmd_clones(bot, dest: str, rest: str):
    """Handle !clones — find all nicks sharing the same IP, host, or vhost."""
    target = rest.strip()
    if not target:
        _reply(bot, dest, 'Usage: !clones <nick|ip|host>')
        return

    ip_to_search = None
    host_to_search = None
    nick_searched = None

    if _is_valid_ip(target):
        ip_to_search = target
    else:
        # Check if target is a known nick in DB
        with _db_lock, _get_db() as conn:
            row = conn.execute(
                'SELECT ip, host, vhost FROM users WHERE nick_lower = ?',
                (target.lower(),)
            ).fetchone()
        if row:
            nick_searched = target
            ip_to_search = row['ip'] if row['ip'] and row['ip'] not in ('*', '') else None
            host_to_search = row['host'] if row['host'] and row['host'] not in ('*', '') else None
        else:
            # Treat as a hostname / vhost
            if target not in ('*', ''):
                host_to_search = target
            else:
                _reply(bot, dest, f'Invalid target: {target}')
                return

    if not ip_to_search and not host_to_search:
        _reply(bot, dest, f'No IP/host available for {target} to find clones.')
        return

    # Search for other users with matching IP or real host / vhost
    with _db_lock, _get_db() as conn:
        if ip_to_search and host_to_search:
            rows = conn.execute(
                'SELECT * FROM users WHERE (ip = ? OR host = ? OR vhost = ?) AND nick_lower != ?',
                (ip_to_search, host_to_search, host_to_search, (nick_searched or '').lower())
            ).fetchall()
        elif ip_to_search:
            rows = conn.execute(
                'SELECT * FROM users WHERE ip = ? AND nick_lower != ?',
                (ip_to_search, (nick_searched or '').lower())
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM users WHERE (host = ? OR vhost = ?) AND nick_lower != ?',
                (host_to_search, host_to_search, (nick_searched or '').lower())
            ).fetchall()

    if not rows:
        _reply(bot, dest, f'No clones found for {target}.')
        return

    criteria = f'IP {ip_to_search}' if ip_to_search else f'host {host_to_search}'
    if nick_searched:
        _reply(bot, dest, f'Clones for {nick_searched} (sharing {criteria}):')
    else:
        _reply(bot, dest, f'Clones sharing {criteria}:')

    for i, row in enumerate(rows, start=1):
        ns_info = ''
        if row['ns_registered']:
            ns_info = f' [registered={row["ns_registered"]}]'
        _reply(bot, dest, f'{i}: {_row_to_str(row)}{ns_info}')


def _cmd_info(bot, dest: str, rest: str):
    """Handle !info — full dump of WHOIS + NickServ info."""
    target = rest.strip()
    if not target:
        _reply(bot, dest, 'Usage: !info <nick>')
        return

    if re.search(r'[.:/*?]', target):
        _reply(bot, dest, 'Usage: !info <nick> (does not support wildcards or IP addresses)')
        return

    _reply(bot, dest, f'Querying NickServ for {target}...')
    ns_data = _query_nickserv(bot, target)

    if ns_data:
        _upsert_user(ns_data)

    with _db_lock, _get_db() as conn:
        row = conn.execute(
            'SELECT * FROM users WHERE nick_lower = ?',
            (target.lower(),)
        ).fetchone()

    if not row:
        _reply(bot, dest, f'No local database record for {target}.')
        return

    _reply(bot, dest, f'=== Intel Report for {row["nick"] or target} ===')
    userhost = f'{row["nick"]}!{row["ident"] or "~unknown"}@{row["host"] or "*"}'
    _reply(bot, dest, f'Userhost: {userhost}')
    if row['vhost']:
        _reply(bot, dest, f'Vhost:    {row["vhost"]}')
    if row['ip']:
        _reply(bot, dest, f'IP:       {row["ip"]}')
    if row['name']:
        _reply(bot, dest, f'Realname: {row["name"]}')
    if row['account']:
        _reply(bot, dest, f'Account:  {row["account"]}')
    if row['email']:
        _reply(bot, dest, f'Email:    {row["email"]}')
    if row['server']:
        _reply(bot, dest, f'Server:   {row["server"]}')
    if row['version']:
        _reply(bot, dest, f'Version:  {row["version"]}')
    if row['channels']:
        _reply(bot, dest, f'Channels: {row["channels"]}')
    if row['last_seen']:
        seen_str = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(row['last_seen']))
        _reply(bot, dest, f'Seen:     {seen_str}')

    if row['ns_registered']:
        _reply(bot, dest, f'NS Registered: {row["ns_registered"]}')
        if row['ns_last_seen']:
            _reply(bot, dest, f'NS Last Seen:  {row["ns_last_seen"]}')
        if row['ns_email']:
            _reply(bot, dest, f'NS Email:      {row["ns_email"]}')
        if row['ns_options']:
            _reply(bot, dest, f'NS Options:    {row["ns_options"]}')
    else:
        if ns_data and not ns_data.get('is_registered'):
            _reply(bot, dest, 'NickServ:      Nick is not registered.')
        elif row['account']:
            _reply(bot, dest, f'NickServ:      Identified as account {row["account"]}.')
        elif ns_data and ns_data.get('is_registered'):
            _reply(bot, dest, 'NickServ:      Nick is registered (details hidden).')
        else:
            _reply(bot, dest, 'NickServ:      No registration info.')


def _cmd_last(bot, dest: str, rest: str):
    """Handle !last — check when the nick was last seen by the bot."""
    target = rest.strip()
    if not target:
        _reply(bot, dest, 'Usage: !last <nick>')
        return

    with _db_lock, _get_db() as conn:
        row = conn.execute(
            'SELECT nick, last_seen FROM users WHERE nick_lower = ?',
            (target.lower(),)
        ).fetchone()

    if not row or not row['last_seen']:
        _reply(bot, dest, f'No record of {target} ever being seen.')
    else:
        seen_time = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(row['last_seen']))
        diff = int(time.time()) - row['last_seen']
        if diff < 60:
            ago = f'{diff}s ago'
        elif diff < 3600:
            ago = f'{diff // 60}m ago'
        elif diff < 86400:
            ago = f'{diff // 3600}h ago'
        else:
            ago = f'{diff // 86400}d ago'
        _reply(bot, dest, f'{row["nick"] or target} was last seen on {seen_time} ({ago}).')


def _cmd_count(bot, dest: str, rest: str, case_sensitive: bool):
    """Handle !count and !ccount — return match count only."""
    parts = rest.split(None, 1)
    if len(parts) < 2:
        _reply(bot, dest, 'Usage: !count <field> <pattern>  (fields: nick ident host vhost ip name account email server version channels)')
        return

    field   = parts[0].lower()
    pattern = parts[1]

    if field not in SEARCHABLE_FIELDS:
        _reply(bot, dest, f'Unknown field \'{field}\'.')
        return

    like, esc = _wildcard_to_sql(pattern)
    col = field

    try:
        with _db_lock, _get_db() as conn:
            if case_sensitive:
                row = conn.execute(
                    f'SELECT COUNT(*) as cnt FROM users WHERE {col} GLOB ?',
                    (pattern,)
                ).fetchone()
            else:
                row = conn.execute(
                    f'SELECT COUNT(*) as cnt FROM users WHERE {col} LIKE ? ESCAPE ?',
                    (like, esc)
                ).fetchone()
        count = row['cnt'] if row else 0
        _reply(bot, dest, f'Found {count} matching records for {field} = {pattern}')
    except sqlite3.OperationalError as e:
        LOG.warning('harambe: count error for field %s, pattern %r: %s', field, pattern, e)
        _reply(bot, dest, f'Search error: invalid pattern \'{pattern}\'.')


def _cmd_stats(bot, dest: str):
    """Handle !stats — display database summary statistics."""
    with _db_lock, _get_db() as conn:
        total = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if not total:
            _reply(bot, dest, 'Database is empty.')
            return
        has_ip = conn.execute('SELECT COUNT(*) FROM users WHERE ip IS NOT NULL AND ip != ""').fetchone()[0]
        has_acct = conn.execute('SELECT COUNT(*) FROM users WHERE account IS NOT NULL AND account != ""').fetchone()[0]
        has_ver = conn.execute('SELECT COUNT(*) FROM users WHERE version IS NOT NULL AND version != ""').fetchone()[0]
        has_ns = conn.execute('SELECT COUNT(*) FROM users WHERE ns_registered IS NOT NULL').fetchone()[0]

    _reply(bot, dest, f'=== Harambe Database Statistics ===')
    _reply(bot, dest, f'Total users tracked: {total}')
    _reply(bot, dest, f'With IP address:     {has_ip} ({has_ip * 100 // total}%)')
    _reply(bot, dest, f'With registered NS:  {has_ns} ({has_ns * 100 // total}%)')
    _reply(bot, dest, f'With logged account: {has_acct} ({has_acct * 100 // total}%)')
    _reply(bot, dest, f'With client version: {has_ver} ({has_ver * 100 // total}%)')


