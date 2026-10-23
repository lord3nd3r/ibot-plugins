"""
monitor.py - Sopel port of arfer's Eggdrop chanstats.tcl (2026 edition)
Author: Kristopher Craig + original by arfer
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from sopel import plugin

LOGGER = logging.getLogger(__name__)

# ========================= CONFIG =========================
SAVE_EVERY_EVENTS = 20      # (unused; idle-prune now runs on the interval below only)
SAVE_EVERY_MINUTES = 10     # also prune on this interval
IDLE_DAYS = 30              # discard nicks unseen for this long
IGNORE_GUESTS = True        # skip guestXXXX nicks
B = "\x02"                  # bold
B_OFF = "\x02"              # bold off (toggle)
COLOR_RESET = "\x03"
SEP = "\x0314 · \x03"       # grey dot separator
# ==========================================================

VALID_FIELDS = frozenset({
    "lines", "words", "actions", "kicks", "bans",
    "joins", "parts", "splits", "quits", "nickchanges",
})

_stats_lock = threading.Lock()

# One shared write connection (sqlite3 objects aren't thread-safe on their
# own, so every use holds _write_lock).  Read-only commands keep using
# short-lived connections from _get_db().
_write_lock = threading.Lock()
_write_conn = None

# Our own view of channel membership: nick_lower -> set(channel_lower).
# The ibot core updates bot.channels *before* dispatching QUIT/NICK, so by
# the time our handler runs the user is already gone from bot.channels.
_members = {}
_members_lock = threading.Lock()


# ====================== DB HELPERS ======================

def setup(bot):
    db_dir = getattr(bot.config.core, "db_dir", None) or os.path.expanduser("~/.sopel")
    db_path = os.path.join(db_dir, "chanstats.db")
    bot.memory["chanstats_db"] = db_path
    bot.memory["chanstats_count"] = 0
    _resync_members(bot)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
                 channel TEXT NOT NULL,
                 nick TEXT NOT NULL,
                 lines INTEGER DEFAULT 0,
                 words INTEGER DEFAULT 0,
                 actions INTEGER DEFAULT 0,
                 kicks INTEGER DEFAULT 0,
                 bans INTEGER DEFAULT 0,
                 joins INTEGER DEFAULT 0,
                 parts INTEGER DEFAULT 0,
                 splits INTEGER DEFAULT 0,
                 quits INTEGER DEFAULT 0,
                 nickchanges INTEGER DEFAULT 0,
                 last_seen INTEGER,
                 PRIMARY KEY (channel, nick))''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_stats_channel ON stats(channel)")
    conn.commit()
    conn.close()
    LOGGER.info("chanstats: database ready at %s", db_path)


def _get_db(bot):
    conn = sqlite3.connect(bot.memory["chanstats_db"])
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _writer(bot):
    """Shared write connection; caller must hold _write_lock."""
    global _write_conn
    if _write_conn is None:
        _write_conn = sqlite3.connect(bot.memory["chanstats_db"], check_same_thread=False)
        _write_conn.execute("PRAGMA journal_mode=WAL")
        _write_conn.execute("PRAGMA synchronous=NORMAL")
    return _write_conn


def _close_writer():
    global _write_conn
    with _write_lock:
        if _write_conn is not None:
            try:
                _write_conn.close()
            except Exception:
                pass
            _write_conn = None


def accrue(bot, nick, channel, field, amount=1):
    """Increment a stat field for nick in channel."""
    accrue_many(bot, nick, [channel], {field: amount})


def accrue_many(bot, nick, channels, fields):
    """Increment several fields for nick in each channel in one transaction."""
    fields = {f: int(a) for f, a in fields.items() if f in VALID_FIELDS and a and a > 0}
    if not fields:
        return
    if IGNORE_GUESTS and nick.lower().startswith("guest") and len(nick) > 5 and nick[-4:].isdigit():
        return
    chans = sorted({str(c).lower() for c in channels if str(c).startswith("#")})
    if not chans:
        return

    nick = nick.lower()
    now = int(time.time())
    names = list(fields)
    # field names are validated against VALID_FIELDS above — safe to format
    sql = (
        f"INSERT INTO stats (channel, nick, last_seen, {', '.join(names)}) "
        f"VALUES (?, ?, ?, {', '.join('?' * len(names))}) "
        "ON CONFLICT(channel, nick) DO UPDATE SET last_seen = excluded.last_seen, "
        + ", ".join(f"{f} = {f} + ?" for f in names)
    )
    vals = [fields[f] for f in names]
    rows = [(c, nick, now, *vals, *vals) for c in chans]
    with _write_lock:
        try:
            conn = _writer(bot)
            with conn:
                conn.executemany(sql, rows)
        except Exception:
            LOGGER.exception("chanstats: accrue error for %s/%s/%s", chans, nick, names)


def _prune_idle(bot, reason="manual"):
    """Remove records for nicks not seen within IDLE_DAYS."""
    cutoff = int(time.time()) - (IDLE_DAYS * 86400)
    with _write_lock:
        try:
            conn = _writer(bot)
            with conn:
                deleted = conn.execute("DELETE FROM stats WHERE last_seen < ?", (cutoff,)).rowcount
        except Exception:
            LOGGER.exception("chanstats: prune error")
            deleted = 0

    if deleted:
        LOGGER.info("chanstats: pruned %d idle records (%s)", deleted, reason)


# ====================== MEMBERSHIP TRACKING ======================

def _resync_members(bot):
    """Rebuild our membership map from bot.channels."""
    snap = {}
    try:
        for chan, ch in list(getattr(bot, "channels", {}).items()):
            for n in list(getattr(ch, "users", {}) or {}):
                snap.setdefault(str(n).lower(), set()).add(str(chan).lower())
    except Exception:
        LOGGER.exception("chanstats: membership resync failed")
        return
    with _members_lock:
        _members.clear()
        _members.update(snap)


def _member_add(nick, chan):
    with _members_lock:
        _members.setdefault(str(nick).lower(), set()).add(str(chan).lower())


def _member_remove(nick, chan):
    n, c = str(nick).lower(), str(chan).lower()
    with _members_lock:
        chans = _members.get(n)
        if chans:
            chans.discard(c)
            if not chans:
                _members.pop(n, None)


def _member_drop_channel(chan):
    c = str(chan).lower()
    with _members_lock:
        for n in list(_members):
            _members[n].discard(c)
            if not _members[n]:
                del _members[n]


@plugin.thread(False)
@plugin.event("366")  # RPL_ENDOFNAMES — the bot's view of a channel is complete
def on_endofnames(bot, trigger):
    _resync_members(bot)


@plugin.thread(True)
@plugin.interval(300)
def resync_members(bot):
    _resync_members(bot)


# ====================== EVENT HANDLERS ======================

@plugin.thread(True)
@plugin.event("PRIVMSG")
def pubmsg(bot, trigger):
    """Track lines, words, and /me actions for channel messages."""
    if not str(trigger.sender).startswith("#"):
        return
    if trigger.nick == bot.nick:
        return

    _member_add(trigger.nick, trigger.sender)
    raw = trigger.raw or ""
    if "\x01ACTION" in raw:
        accrue(bot, trigger.nick, trigger.sender, "actions")
        return

    text = trigger.group(0) or ""
    accrue_many(bot, trigger.nick, [trigger.sender],
                {"lines": 1, "words": len(text.split())})


@plugin.thread(True)
@plugin.event("KICK")
def on_kick(bot, trigger):
    """Track kicks given by the kicker."""
    if not str(trigger.sender).startswith("#"):
        return
    args = list(getattr(trigger, "args", None) or [])
    if len(args) >= 2:
        kicked = args[1]
        if str(kicked).lower() == str(bot.nick).lower():
            _member_drop_channel(trigger.sender)
        else:
            _member_remove(kicked, trigger.sender)
    if trigger.nick != bot.nick:
        accrue(bot, trigger.nick, trigger.sender, "kicks")


@plugin.thread(True)
@plugin.event("MODE")
def on_mode(bot, trigger):
    """Track bans set (+b)."""
    if not str(trigger.sender).startswith("#"):
        return
    # Raw IRC: :nick!u@h MODE #chan +modes [args...]
    # Mode string is the 4th token (index 3); check it starts with + and contains b
    raw_parts = (trigger.raw or "").split()
    # Check each char in the mode string individually to avoid false matches (e.g. +ob)
    if len(raw_parts) >= 4 and raw_parts[3].startswith("+"):
        mode_chars = raw_parts[3][1:]  # strip the leading +
        ban_count = mode_chars.count("b")
        for _ in range(ban_count):
            accrue(bot, trigger.nick, trigger.sender, "bans")


@plugin.thread(True)
@plugin.event("JOIN")
def on_join(bot, trigger):
    if str(trigger.sender).startswith("#"):
        if trigger.nick != bot.nick:
            _member_add(trigger.nick, trigger.sender)
            accrue(bot, trigger.nick, trigger.sender, "joins")


@plugin.thread(True)
@plugin.event("PART")
def on_part(bot, trigger):
    if not str(trigger.sender).startswith("#"):
        return
    if trigger.nick == bot.nick:
        _member_drop_channel(trigger.sender)
        return
    _member_remove(trigger.nick, trigger.sender)
    accrue(bot, trigger.nick, trigger.sender, "parts")


@plugin.thread(True)
@plugin.event("QUIT")
def on_quit(bot, trigger):
    """Track quits. Detect netsplits by quit message format (server1 server2)."""
    if trigger.nick == bot.nick:
        return
    quit_msg = trigger.group(0) or ""
    pieces = quit_msg.strip().split()
    is_split = (
        len(pieces) == 2
        and "." in pieces[0]
        and "." in pieces[1]
    )
    field = "splits" if is_split else "quits"

    # The core removes the user from bot.channels before dispatching QUIT,
    # so use our own membership map: count only channels the nick was in
    # (not every channel it ever had stats in).
    nick_lower = trigger.nick.lower()
    with _members_lock:
        chans = _members.pop(nick_lower, set())
    if chans:
        accrue_many(bot, nick_lower, chans, {field: 1})


@plugin.thread(True)
@plugin.event("NICK")
def on_nickchange(bot, trigger):
    """Track nick changes."""
    old_nick = trigger.nick
    if old_nick == bot.nick:
        return
    args = list(getattr(trigger, "args", None) or [])
    new_nick = str(args[0]).lstrip(":") if args else ""
    with _members_lock:
        chans = _members.pop(old_nick.lower(), set())
        if new_nick and chans:
            _members.setdefault(new_nick.lower(), set()).update(chans)
    if not chans and new_nick:
        # Fallback: the core already renamed the user in bot.channels
        for chan in list(bot.channels):
            try:
                if new_nick in bot.channels[chan].users:
                    chans.add(str(chan).lower())
            except Exception:
                pass
    if chans:
        accrue_many(bot, old_nick, chans, {"nickchanges": 1})


# ====================== HELPER FUNCTIONS ======================

def _fmt_duration(seconds):
    """Format seconds into a compact human-readable string."""
    if seconds < 0:
        seconds = 0
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


# ====================== COMMANDS ======================

@plugin.thread(True)
@plugin.command("stats")
def cmd_stats(bot, trigger):
    """$stats [nick] [#channel] — Show stats for a user."""
    text = (trigger.group(2) or "").strip().split()
    nick = None
    chan = None

    for arg in text:
        if arg.startswith("#"):
            chan = arg.lower()
        elif nick is None:
            nick = arg.lower()

    if nick is None:
        nick = trigger.nick.lower()
    if chan is None:
        chan = str(trigger.sender).lower()

    if not chan.startswith("#"):
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Use this in a channel or specify one.")
        return

    conn = _get_db(bot)
    try:
        row = conn.execute(
            "SELECT lines, words, actions, kicks, bans, joins, parts, splits, quits, nickchanges, last_seen "
            "FROM stats WHERE channel=? AND nick=?",
            (chan, nick),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        bot.say(f"\x0304⚠{COLOR_RESET} No stats for {B}{nick}{B_OFF} in {B}{chan}{B_OFF}.")
        return

    lines, words, actions, kicks, bans, joins, parts, splits, quits, nicks, last_seen = row
    ago = int(time.time()) - (last_seen or 0)

    bot.say(
        f"📊 {B}{nick}{B_OFF} in {B}{chan}{B_OFF}{SEP}"
        f"💬 Lines: {B}{lines:,}{B_OFF}{SEP}"
        f"📝 Words: {B}{words:,}{B_OFF}{SEP}"
        f"🎭 Actions: {B}{actions:,}{B_OFF}{SEP}"
        f"🚪 Joins: {B}{joins:,}{B_OFF}{SEP}"
        f"👋 Parts: {B}{parts:,}{B_OFF}"
    )
    # Second line for less-common stats + last seen
    extras = []
    if kicks:
        extras.append(f"🦵 Kicks: {B}{kicks:,}{B_OFF}")
    if bans:
        extras.append(f"🔨 Bans: {B}{bans:,}{B_OFF}")
    if splits:
        extras.append(f"💥 Splits: {B}{splits:,}{B_OFF}")
    if quits:
        extras.append(f"🚫 Quits: {B}{quits:,}{B_OFF}")
    if nicks:
        extras.append(f"🔄 Nicks: {B}{nicks:,}{B_OFF}")
    extras.append(f"🕐 Last seen: {B}{_fmt_duration(ago)}{B_OFF} ago")
    bot.say(SEP.join(extras))


@plugin.thread(True)
@plugin.command("rank")
def cmd_rank(bot, trigger):
    """$rank [nick|field] [field] [#channel] — Rank a user or show top 10. Default field: lines."""
    text = (trigger.group(2) or "").strip().split()

    field = "lines"
    chan = str(trigger.sender).lower()
    nick = None

    for arg in text:
        if arg.startswith("#"):
            chan = arg.lower()
        elif arg.lower() in VALID_FIELDS:
            field = arg.lower()
        elif nick is None:
            nick = arg.lower()

    if not chan.startswith("#"):
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Use this in a channel or specify one.")
        return

    field_emoji = {
        "lines": "💬", "words": "📝", "actions": "🎭", "kicks": "🦵",
        "bans": "🔨", "joins": "🚪", "parts": "👋", "splits": "💥",
        "quits": "🚫", "nickchanges": "🔄",
    }

    # ── Single-user rank lookup ──────────────────────────────────────────────
    if nick is not None:
        conn = _get_db(bot)
        try:
            # Rank position (1-based) among users with > 0 for that field
            # field is validated against VALID_FIELDS — safe to format
            rank_row = conn.execute(
                f"""SELECT rank, {field} FROM (
                        SELECT nick,
                               {field},
                               ROW_NUMBER() OVER (ORDER BY {field} DESC) AS rank
                        FROM stats
                        WHERE channel=? AND {field} > 0
                    ) WHERE nick=?""",
                (chan, nick),
            ).fetchone()

            stats_row = conn.execute(
                "SELECT lines, words, actions, kicks, bans, joins, parts, splits, quits, nickchanges, last_seen "
                "FROM stats WHERE channel=? AND nick=?",
                (chan, nick),
            ).fetchone()
        finally:
            conn.close()

        if not stats_row:
            bot.say(f"\x0304⚠{COLOR_RESET} No stats for {B}{nick}{B_OFF} in {B}{chan}{B_OFF}.")
            return

        lines, words, actions, kicks, bans, joins, parts, splits, quits, nicks_chg, last_seen = stats_row
        ago = int(time.time()) - (last_seen or 0)

        emoji = field_emoji.get(field, "📊")
        if rank_row:
            rank_pos, rank_val = rank_row
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            medal = medals.get(rank_pos, f"#{rank_pos}")
            rank_str = f"{medal} Rank: {B}{rank_pos}{B_OFF} by {field} ({rank_val:,})"
        else:
            rank_str = f"Rank: {B}unranked{B_OFF} for {field} (0)"

        bot.say(
            f"{emoji} {B}{nick}{B_OFF} in {B}{chan}{B_OFF}{SEP}"
            f"{rank_str}{SEP}"
            f"💬 Lines: {B}{lines:,}{B_OFF}{SEP}"
            f"📝 Words: {B}{words:,}{B_OFF}{SEP}"
            f"🎭 Actions: {B}{actions:,}{B_OFF}"
        )
        return

    # ── Top-10 leaderboard ───────────────────────────────────────────────────
    conn = _get_db(bot)
    try:
        # field is validated against VALID_FIELDS — safe to format
        rows = conn.execute(
            f"SELECT nick, {field} FROM stats "
            f"WHERE channel=? AND {field} > 0 "
            f"ORDER BY {field} DESC LIMIT 10",
            (chan,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        bot.say(f"\x0304⚠{COLOR_RESET} No {B}{field}{B_OFF} stats for {B}{chan}{B_OFF}.")
        return

    emoji = field_emoji.get(field, "📊")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    entries = []
    for i, (nick, value) in enumerate(rows, start=1):
        medal = medals.get(i, f"\x0314{i}.\x03")
        entries.append(f"{medal} {B}{nick}{B_OFF} ({value:,})")

    bot.say(f"{emoji} {B}Top {field.capitalize()}{B_OFF} in {B}{chan}{B_OFF}{SEP}{SEP.join(entries)}")


@plugin.thread(True)
@plugin.command("chanstats")
def cmd_chanstats(bot, trigger):
    """$chanstats [#channel] — Total stats for the entire channel."""
    chan = (trigger.group(2) or str(trigger.sender)).strip().lower()

    if not chan.startswith("#"):
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Use this in a channel or specify one.")
        return

    conn = _get_db(bot)
    try:
        row = conn.execute(
            """SELECT COUNT(DISTINCT nick),
                      COALESCE(SUM(lines), 0),
                      COALESCE(SUM(words), 0),
                      COALESCE(SUM(actions), 0),
                      COALESCE(SUM(kicks), 0),
                      COALESCE(SUM(bans), 0),
                      COALESCE(SUM(joins), 0),
                      COALESCE(SUM(parts), 0),
                      COALESCE(SUM(splits), 0),
                      COALESCE(SUM(quits), 0),
                      COALESCE(SUM(nickchanges), 0)
               FROM stats WHERE channel=?""",
            (chan,),
        ).fetchone()
    finally:
        conn.close()

    if not row or row[0] == 0:
        bot.say(f"\x0304⚠{COLOR_RESET} No stats for {B}{chan}{B_OFF}.")
        return

    users, lines, words, actions, kicks, bans, joins, parts, splits, quits, nicks = row

    parts_list = [
        f"📊 {B}Channel Stats{B_OFF} — {B}{chan}{B_OFF}",
        f"👥 Users: {B}{users:,}{B_OFF}",
        f"💬 Lines: {B}{lines:,}{B_OFF}",
        f"📝 Words: {B}{words:,}{B_OFF}",
        f"🎭 Actions: {B}{actions:,}{B_OFF}",
        f"🚪 Joins: {B}{joins:,}{B_OFF}",
        f"👋 Parts: {B}{parts:,}{B_OFF}",
    ]
    if kicks:
        parts_list.append(f"🦵 Kicks: {B}{kicks:,}{B_OFF}")
    if bans:
        parts_list.append(f"🔨 Bans: {B}{bans:,}{B_OFF}")
    if splits:
        parts_list.append(f"💥 Splits: {B}{splits:,}{B_OFF}")
    if quits:
        parts_list.append(f"🚫 Quits: {B}{quits:,}{B_OFF}")
    if nicks:
        parts_list.append(f"🔄 Nicks: {B}{nicks:,}{B_OFF}")
    bot.say(SEP.join(parts_list))


@plugin.thread(True)
@plugin.command("chanrank")
def cmd_chanrank(bot, trigger):
    """$chanrank — Top 10 channels by activity (total lines)."""
    conn = _get_db(bot)
    try:
        rows = conn.execute(
            """SELECT channel,
                      COUNT(DISTINCT nick) AS users,
                      COALESCE(SUM(lines), 0) AS total_lines,
                      COALESCE(SUM(words), 0) AS total_words
               FROM stats
               GROUP BY channel
               HAVING total_lines > 0
               ORDER BY total_lines DESC
               LIMIT 10""",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        bot.say(f"\x0304⚠{COLOR_RESET} No channel stats recorded yet.")
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    entries = []
    for i, (chan, users, total_lines, total_words) in enumerate(rows, start=1):
        medal = medals.get(i, f"\x0314{i}.\x03")
        entries.append(
            f"{medal} {B}{chan}{B_OFF} — "
            f"💬 {B}{total_lines:,}{B_OFF} lines, "
            f"👥 {B}{users:,}{B_OFF} users"
        )

    bot.say(f"🏆 {B}Top Channels by Activity{B_OFF}")
    # Send in batches to avoid flooding — up to 5 per message
    for start in range(0, len(entries), 5):
        bot.say(SEP.join(entries[start:start + 5]))


@plugin.thread(True)
@plugin.command("zapstats")
@plugin.require_owner("Only the owner can zap stats")
def cmd_zap(bot, trigger):
    """$zapstats [#channel] — Wipe all stats for a channel. Owner only."""
    chan = (trigger.group(2) or str(trigger.sender)).strip().lower()

    if not chan.startswith("#"):
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Specify a channel.")
        return

    conn = _get_db(bot)
    try:
        cur = conn.execute("DELETE FROM stats WHERE channel=?", (chan,))
        deleted = cur.rowcount
        conn.commit()
    except Exception:
        LOGGER.exception("chanstats: zapstats error")
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Failed to zap stats.")
        return
    finally:
        conn.close()

    bot.say(
        f"🗑️ {B}Stats Reset{B_OFF}{SEP}"
        f"{B}{chan}{B_OFF} wiped by {B}{trigger.nick}{B_OFF}{SEP}"
        f"{B}{deleted:,}{B_OFF} records removed"
    )


@plugin.thread(True)
@plugin.command("zapnick")
@plugin.require_privilege(plugin.OP, "You need to be at least an op to use this.")
def cmd_zapnick(bot, trigger):
    """$zapnick <nick> [#channel] — Remove a single nick's stats. Op+ only."""
    text = (trigger.group(2) or "").strip().split()
    nick = None
    chan = str(trigger.sender).lower()

    for arg in text:
        if arg.startswith("#"):
            chan = arg.lower()
        elif nick is None:
            nick = arg.lower()

    if not nick:
        bot.say(f"\x0304⚠ Usage:{COLOR_RESET} {B}!zapnick{B_OFF} <nick> [#channel]")
        return
    if not chan.startswith("#"):
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Specify a channel.")
        return

    conn = _get_db(bot)
    try:
        cur = conn.execute("DELETE FROM stats WHERE channel=? AND nick=?", (chan, nick))
        deleted = cur.rowcount
        conn.commit()
    except Exception:
        LOGGER.exception("chanstats: zapnick error")
        bot.say(f"\x0304⚠ Error:{COLOR_RESET} Failed to remove stats.")
        return
    finally:
        conn.close()

    if deleted:
        bot.say(
            f"🗑️ {B}Stats Removed{B_OFF}{SEP}"
            f"{B}{nick}{B_OFF} cleared from {B}{chan}{B_OFF} by {B}{trigger.nick}{B_OFF}"
        )
    else:
        bot.say(f"\x0304⚠{COLOR_RESET} No stats found for {B}{nick}{B_OFF} in {B}{chan}{B_OFF}.")


@plugin.command("statshelp")
def cmd_statshelp(bot, trigger):
    """$statshelp — PM the user a list of all stats commands."""
    nick = trigger.nick

    bot.notice(f"📊 {B}Channel Stats Commands{B_OFF}", nick)
    bot.notice(" ", nick)
    bot.notice(
        f"  {B}!stats{B_OFF} [nick] [#channel]  —  "
        f"Show stats for a user (defaults to you in the current channel)",
        nick,
    )
    bot.notice(
        f"  {B}!rank{B_OFF} [field] [#channel]  —  "
        f"Top 10 users for a stat field (default: lines)",
        nick,
    )
    bot.notice(
        f"  {B}!chanstats{B_OFF} [#channel]  —  "
        f"Total aggregate stats for the entire channel",
        nick,
    )
    bot.notice(
        f"  {B}!chanrank{B_OFF}  —  "
        f"Top 10 channels ranked by total lines",
        nick,
    )
    bot.notice(
        f"  {B}!zapstats{B_OFF} [#channel]  —  "
        f"Wipe all stats for a channel (owner only)",
        nick,
    )
    bot.notice(
        f"  {B}!zapnick{B_OFF} <nick> [#channel]  —  "
        f"Remove a single nick's stats (op+ only)",
        nick,
    )
    bot.notice(" ", nick)
    bot.notice(
        f"📝 {B}Valid rank fields:{B_OFF} lines, words, actions, kicks, bans, "
        f"joins, parts, splits, quits, nickchanges",
        nick,
    )

    bot.say(f"📬 {B}{nick}{B_OFF}, check your notices for the stats command list!")


# ====================== INTERVAL SAVE ======================

@plugin.thread(True)
@plugin.interval(SAVE_EVERY_MINUTES * 60)
def autosave(bot):
    _prune_idle(bot, "schedule")


def shutdown(bot):
    """Final prune on clean shutdown."""
    _prune_idle(bot, "shutdown")
    _close_writer()
    LOGGER.info("chanstats: shutdown complete")