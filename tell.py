# -*- coding: utf-8 -*-
"""
tell.py — Leave messages for offline users.

Usage:
  .tell <nick> <message>   — leave a message for <nick>
  .showtells               — privately list your pending tells (clears them)

When <nick> next speaks in any channel the bot is in, they receive a PM
per pending message: who sent it, when, and the text.  At most
DELIVER_BATCH messages are sent at once; the rest wait for $showtells or the
next time they speak.  Delivered tells are deleted.

Limits: messages are capped at MAX_MESSAGE_LEN characters, a recipient can
have at most MAX_PENDING_PER_RECIPIENT waiting, and one sender can have at
most MAX_PENDING_PER_SENDER waiting in total.
"""

import logging
import os
import re
import sqlite3
import threading
import time

from sopel import plugin

LOG = logging.getLogger(__name__)

DB_PATH = os.path.expanduser('~/.sopel/tell.db')
_db_lock = threading.Lock()

MAX_MESSAGE_LEN = 350            # characters per tell
MAX_PENDING_PER_RECIPIENT = 20   # undelivered tells one nick can have waiting
MAX_PENDING_PER_SENDER = 30      # undelivered tells one sender can have queued
DELIVER_BATCH = 5                # tells sent per delivery
DELIVER_GAP = 1.0                # seconds between delivered lines
_NICK_RE = re.compile(r'^[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,31}$')

# In-memory set of lowercase nicks that have undelivered tells, so the
# every-line check_tells rule doesn't open the DB for every message.
_pending_nicks = set()
_pending_lock = threading.Lock()
_delivering = set()              # nicks with a delivery in progress

# ─────────────────────────── database ───────────────────────────────

def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def _init_db():
    with _db_lock, _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tells (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient   TEXT    NOT NULL COLLATE NOCASE,
                sender      TEXT    NOT NULL,
                channel     TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                sent_at     INTEGER NOT NULL,
                delivered   INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_tells_recipient '
            'ON tells(recipient COLLATE NOCASE) WHERE delivered = 0'
        )


# ─────────────────────────── helpers ────────────────────────────────

def _store_tell(recipient, sender, channel, message):
    with _db_lock, _get_conn() as conn:
        conn.execute(
            'INSERT INTO tells (recipient, sender, channel, message, sent_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (recipient.lower(), sender, channel, message, int(time.time()))
        )


def _fetch_tells(nick, limit=DELIVER_BATCH):
    """Return up to ``limit`` undelivered tells for nick (oldest first) and
    delete them.  Returns (rows, remaining_count)."""
    nick_lower = nick.lower()
    with _db_lock, _get_conn() as conn:
        rows = conn.execute(
            'SELECT id, sender, channel, message, sent_at '
            'FROM tells WHERE recipient = ? AND delivered = 0 '
            'ORDER BY sent_at ASC, id ASC LIMIT ?',
            (nick_lower, int(limit))
        ).fetchall()
        if rows:
            ids = [r['id'] for r in rows]
            conn.execute(
                'DELETE FROM tells WHERE id IN ({})'.format(
                    ','.join('?' * len(ids))
                ),
                ids
            )
        remaining = conn.execute(
            'SELECT COUNT(*) FROM tells WHERE recipient = ? AND delivered = 0',
            (nick_lower,)
        ).fetchone()[0]
    with _pending_lock:
        if remaining:
            _pending_nicks.add(nick_lower)
        else:
            _pending_nicks.discard(nick_lower)
    return rows, remaining


def _sender_pending(sender):
    with _db_lock, _get_conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) FROM tells WHERE sender = ? COLLATE NOCASE AND delivered = 0',
            (sender,)
        ).fetchone()
    return row[0] if row else 0


def _purge_and_load():
    """Delete already-delivered rows (older versions only marked them) and
    load the set of nicks with pending tells."""
    with _db_lock, _get_conn() as conn:
        purged = conn.execute('DELETE FROM tells WHERE delivered != 0').rowcount
        nicks = {r[0].lower() for r in conn.execute(
            'SELECT DISTINCT recipient FROM tells WHERE delivered = 0')}
    with _pending_lock:
        _pending_nicks.clear()
        _pending_nicks.update(nicks)
    return purged


def _pending_count(nick):
    nick_lower = nick.lower()
    with _db_lock, _get_conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) FROM tells WHERE recipient = ? AND delivered = 0',
            (nick_lower,)
        ).fetchone()
    return row[0] if row else 0


def _fmt_time(epoch):
    """Format epoch as a human-readable UTC string."""
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(epoch))


def _deliver_tells(bot, nick):
    """Deliver up to DELIVER_BATCH pending tells for nick via PM (throttled).
    Returns (delivered, remaining)."""
    key = nick.lower()
    with _pending_lock:
        if key in _delivering:
            return 0, 0
        _delivering.add(key)
    try:
        tells, remaining = _fetch_tells(nick)
        for i, row in enumerate(tells):
            if i:
                time.sleep(DELIVER_GAP)
            msg = (
                f'[Tell from \x02{row["sender"]}\x02 in {row["channel"]} '
                f'on {_fmt_time(row["sent_at"])}]: {row["message"]}'
            )
            bot.say(msg, nick)
        if remaining:
            bot.say(f'You have {remaining} more message(s) waiting. '
                    'Use $showtells to get the next batch.', nick)
        return len(tells), remaining
    finally:
        with _pending_lock:
            _delivering.discard(key)


# ─────────────────────────── lifecycle ──────────────────────────────

def setup(bot):
    _init_db()
    try:
        purged = _purge_and_load()
        if purged:
            LOG.info('tell: purged %d already-delivered tells', purged)
    except Exception:
        LOG.exception('tell: purge/load failed')
    LOG.info('tell: plugin loaded')


# ─────────────────────────── commands ───────────────────────────────

@plugin.commands('tell')
@plugin.example('$tell SomeUser Hey, call me back!')
@plugin.rate(user=5)
def cmd_tell(bot, trigger):
    """Leave a message for a user. Delivered via NOTICE when they next speak."""
    args = (trigger.group(2) or '').strip()
    if not args or ' ' not in args:
        bot.reply('Usage: $tell <nick> <message>')
        return

    recipient, _, message = args.partition(' ')
    message = message.strip()

    if not message:
        bot.reply('Usage: $tell <nick> <message>')
        return

    if recipient.lower() == bot.nick.lower():
        bot.reply("I can't take messages for myself.")
        return

    if recipient.lower() == trigger.nick.lower():
        bot.reply("You can't leave a message for yourself.")
        return

    recipient = recipient.rstrip(',:')
    if not _NICK_RE.match(recipient):
        bot.reply("That doesn't look like a valid nick.")
        return

    if len(message) > MAX_MESSAGE_LEN:
        bot.reply(f'Message too long (max {MAX_MESSAGE_LEN} characters).')
        return

    if _pending_count(recipient) >= MAX_PENDING_PER_RECIPIENT:
        bot.reply(f'\x02{recipient}\x02 already has too many messages waiting.')
        return

    if _sender_pending(str(trigger.nick)) >= MAX_PENDING_PER_SENDER:
        bot.reply('You have too many undelivered messages queued already.')
        return

    channel = str(trigger.sender)
    _store_tell(recipient, str(trigger.nick), channel, message)
    with _pending_lock:
        _pending_nicks.add(recipient.lower())
    bot.reply(f'I will tell \x02{recipient}\x02 that when they next speak.')


@plugin.commands('showtells')
@plugin.rate(user=10)
def cmd_showtells(bot, trigger):
    """Show your pending tells privately (DELIVER_BATCH at a time)."""
    count = _pending_count(str(trigger.nick))
    if count == 0:
        bot.say('You have no pending messages.', trigger.nick)
        return
    delivered, remaining = _deliver_tells(bot, str(trigger.nick))
    if delivered and not remaining:
        bot.say(f'{delivered} message(s) delivered above.', trigger.nick)


# ─────────────────────────── delivery trigger ───────────────────────

@plugin.rule('.*')
@plugin.priority('high')
@plugin.thread(True)
def check_tells(bot, trigger):
    """Deliver pending tells when a user speaks in a channel."""
    if not trigger.sender or trigger.is_privmsg:
        return
    nick = str(trigger.nick)
    if nick.lower() == bot.nick.lower():
        return
    with _pending_lock:
        if nick.lower() not in _pending_nicks or nick.lower() in _delivering:
            return
    if _pending_count(nick) == 0:
        with _pending_lock:
            _pending_nicks.discard(nick.lower())
        return
    _deliver_tells(bot, nick)
