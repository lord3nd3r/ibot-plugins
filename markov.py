# markov.py — Markov chain bot plugin for Sopel
# Originally by ComputerTech, updated by End3r
# Learns from channel chat and generates random sentences from word trigrams.
# Trigram data is stored in its own SQLite database (~/.sopel/markov.db).
import importlib.util
import logging
import os
import random
import re
import sqlite3
import sys
import threading

import requests
from sopel import plugin

LOG = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://\S+")
MAX_OUTPUT_LEN = 440  # stay within IRC message limits

NO_MARKOV = "Markov chains are not enabled in this channel."

# $markovlog limits
LOG_MAX_BYTES = 5 * 1024 * 1024   # refuse logs larger than 5 MB
LOG_TIMEOUT = 15                  # per socket operation
LOG_DEADLINE = 60                 # whole download
LOG_BATCH = 500                   # lines per DB transaction while importing

# Optional pruning (OFF by default).  Set in the bot cfg:
#   [markov]
#   max_rows_per_channel = 2000000
# When > 0, an hourly job trims the lowest-frequency trigrams of any channel
# that exceeds the cap, at most PRUNE_BATCH rows per channel per run.
PRUNE_BATCH = 20000

_load_thread = None
_load_lock = threading.Lock()      # guards _load_thread start/finish
_db_path = None
_db_lock = threading.Lock()        # serialises all writes
_write_conn = None                 # one long-lived connection for writes


def _get_db():
    """Return a new sqlite3 connection to the markov database (for reads)."""
    conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _writer():
    """Shared write connection.  Caller must hold _db_lock."""
    global _write_conn
    if _write_conn is None:
        _write_conn = _get_db()
    return _write_conn


def _netguard_import():
    """Load the shared _netguard helper from this plugin's directory."""
    for name in ('ibot_plugins._netguard', '_netguard'):
        if name in sys.modules:
            return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_netguard.py')
    spec = importlib.util.spec_from_file_location('ibot_plugins._netguard', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules['ibot_plugins._netguard'] = mod
    return mod


def setup(bot):
    global _db_path
    _db_path = os.path.join(bot.config.core.homedir, 'markov.db')
    conn = _get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS markov (
            channel     TEXT    NOT NULL,
            first_word  TEXT,
            second_word TEXT,
            third_word  TEXT,
            frequency   INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (channel, first_word, second_word, third_word)
        )
        """
    )
    conn.commit()
    conn.close()


def shutdown(bot):
    global _write_conn
    with _db_lock:
        if _write_conn is not None:
            try:
                _write_conn.close()
            except Exception:
                pass
            _write_conn = None


@plugin.command("markovon")
@plugin.require_chanmsg("This command only works in channels.")
@plugin.require_privilege(plugin.OP, "You must be a channel operator to use this command.")
def cmd_markovon(bot, trigger):
    """$markovon [chance] — Enable markov in this channel. Optional chance 0-100 for auto-trigger."""
    channel = str(trigger.sender)
    bot.db.set_channel_value(channel, "markov", True)
    arg = (trigger.group(2) or "").strip()
    if arg.isdigit():
        chance = max(0, min(100, int(arg)))
        bot.db.set_channel_value(channel, "markov-chance", chance)
        bot.reply("Markov enabled (auto-trigger: %d%%)." % chance)
    else:
        bot.reply("Markov enabled. Use $markovchance <0-100> to set auto-trigger rate.")


@plugin.command("markovoff")
@plugin.require_chanmsg("This command only works in channels.")
@plugin.require_privilege(plugin.OP, "You must be a channel operator to use this command.")
def cmd_markovoff(bot, trigger):
    """$markovoff — Disable markov in this channel."""
    channel = str(trigger.sender)
    bot.db.set_channel_value(channel, "markov", False)
    bot.db.set_channel_value(channel, "markov-chance", 0)
    bot.reply("Markov disabled.")


@plugin.command("markovchance")
@plugin.require_chanmsg("This command only works in channels.")
@plugin.require_privilege(plugin.OP, "You must be a channel operator to use this command.")
def cmd_markovchance(bot, trigger):
    """$markovchance <0-100> — Set auto-trigger percentage."""
    channel = str(trigger.sender)
    if not bot.db.get_channel_value(channel, "markov"):
        return bot.reply(NO_MARKOV)
    arg = (trigger.group(2) or "").strip()
    if not arg.isdigit():
        return bot.reply("Usage: $markovchance <0-100>")
    chance = max(0, min(100, int(arg)))
    bot.db.set_channel_value(channel, "markov-chance", chance)
    bot.reply("Auto-trigger chance set to %d%%." % chance)


@plugin.rule(r".+")
def on_channel_message(bot, trigger):
    if trigger.is_privmsg:
        return

    channel = str(trigger.sender)
    markov_chance = int(bot.db.get_channel_value(channel, "markov-chance") or 0)

    if markov_chance > 0 and random.randint(0, 99) < markov_chance:
        words = trigger.group(0).split()
        random.shuffle(words)
        for word in words:
            out = _generate(bot, channel, [word])
            if out:
                bot.say(out)
                break

    if bot.db.get_channel_value(channel, "markov"):
        _create(bot, channel, trigger.group(0))


@plugin.command("markov")
@plugin.require_chanmsg("This command only works in channels.")
def cmd_markov(bot, trigger):
    channel = str(trigger.sender)
    if not bot.db.get_channel_value(channel, "markov"):
        return bot.reply(NO_MARKOV)

    first_words = trigger.group(2).split() if trigger.group(2) else []
    out = _generate(bot, channel, first_words)
    if out is not None:
        bot.say(out)
    else:
        bot.reply("Failed to generate a Markov chain.")


@plugin.command("markovfor")
def cmd_markovfor(bot, trigger):
    if not trigger.group(2):
        return bot.reply("Usage: !markovfor <#channel> [seed-word]")

    parts = trigger.group(2).split()
    target_name = parts[0]
    first_words = parts[1:]

    if target_name not in bot.channels:
        return bot.reply("Unknown channel.")

    channel = bot.channels[target_name]
    channel_name = str(channel.name)

    # Only members of the target channel may read its chain -- otherwise a
    # secret/private channel's chat could be reconstructed from outside.
    if trigger.nick not in channel.users:
        return bot.reply("You must be in %s to use its Markov chain." % channel_name)

    if not bot.db.get_channel_value(channel_name, "markov"):
        return bot.reply(NO_MARKOV)

    out = _generate(bot, channel_name, first_words)
    if out is None:
        return bot.reply("Failed to generate a Markov chain.")
    if trigger.is_privmsg or str(trigger.sender).lower() == channel_name.lower():
        bot.say(out)
    else:
        # Asked from a different channel: answer privately so one channel's
        # chat isn't replayed into another.
        bot.say("[%s] %s" % (channel_name, out), trigger.nick)


@plugin.command("clearmarkov")
@plugin.require_chanmsg("This command only works in channels.")
@plugin.require_privilege(plugin.OWNER, "You must be the channel owner to use this command.")
def cmd_clearmarkov(bot, trigger):
    channel = str(trigger.sender)

    with _db_lock:
        conn = _writer()
        conn.execute("DELETE FROM markov WHERE channel = ?", (channel,))
        conn.commit()

    bot.reply("Cleared the Markov chain for %s." % channel)


@plugin.command("markovlog")
@plugin.require_chanmsg("This command only works in channels.")
@plugin.require_admin("Only bot admins can import logs.")
def cmd_markovlog(bot, trigger):
    global _load_thread

    channel = str(trigger.sender)
    if not bot.db.get_channel_value(channel, "markov"):
        return bot.reply(NO_MARKOV)

    url = (trigger.group(2) or "").strip()
    if not url:
        return bot.reply("Usage: $markovlog <url>")

    if not re.match(r'^https?://', url, re.IGNORECASE):
        return bot.reply("Only http/https URLs are allowed.")

    with _load_lock:
        if _load_thread is not None and _load_thread.is_alive():
            return bot.reply("A log import is already in progress.")
        _load_thread = threading.Thread(
            target=_fetch_and_load,
            args=(bot, channel, url),
            daemon=True,
        )
        _load_thread.start()
    bot.reply("Fetching log...")


def _fetch_and_load(bot, channel, url):
    """Download (SSRF-guarded, size/time capped) and import a log."""
    global _load_thread
    try:
        ng = _netguard_import()
        try:
            resp = ng.fetch(url, max_bytes=LOG_MAX_BYTES, timeout=LOG_TIMEOUT,
                            deadline=LOG_DEADLINE)
        except ng.BlockedURL:
            bot.say("That URL is not allowed (only public http/https hosts).", channel)
            return
        except ValueError:
            bot.say("Log is too large (max %d MB)." % (LOG_MAX_BYTES // (1024 * 1024)), channel)
            return
        except requests.RequestException:
            LOG.warning("markovlog fetch failed for %s", url, exc_info=True)
            bot.say("Failed to fetch log.", channel)
            return
        if resp.status_code >= 400:
            bot.say("Failed to fetch log (HTTP %d)." % resp.status_code, channel)
            return
        count = _load_loop(bot, channel, resp.text)
        bot.say("Imported %d lines." % count, channel)
    except Exception:
        LOG.exception("markovlog import failed")
        bot.say("Log import failed.", channel)
    finally:
        with _load_lock:
            if _load_thread is threading.current_thread():
                _load_thread = None


def _load_loop(bot, channel, data):
    """Import lines in batches (one transaction per LOG_BATCH lines)."""
    count = 0
    batch = []
    for line in data.split("\n"):
        line = line.strip()
        if line:
            batch.append(line)
            if len(batch) >= LOG_BATCH:
                count += _create_many(channel, batch)
                batch = []
    if batch:
        count += _create_many(channel, batch)
    return count


_INSERT_SQL = """
    INSERT INTO markov
        (channel, first_word, second_word, third_word, frequency)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT (channel, first_word, second_word, third_word)
    DO UPDATE SET frequency = frequency + 1
"""


def _trigrams(line):
    if URL_REGEX.search(line):
        return []

    words = [w.lower() for w in line.split() if w]
    if len(words) <= 2:
        return []

    inserts = [
        (None, None, words[0]),
        (None, words[0], words[1]),
    ]
    for i in range(len(words) - 2):
        inserts.append(tuple(words[i : i + 3]))
    inserts.append((words[-2], words[-1], None))
    return inserts


def _create_many(channel, lines):
    rows = []
    used = 0
    for line in lines:
        t = _trigrams(line)
        if t:
            used += 1
            rows.extend((channel, a, b, c) for a, b, c in t)
    if rows:
        with _db_lock:
            conn = _writer()
            with conn:
                conn.executemany(_INSERT_SQL, rows)
    return used


def _create(bot, channel, line):
    _create_many(channel, [line])


def _prune_cap(bot):
    try:
        val = bot.config.option('markov', 'max_rows_per_channel', '0')
    except Exception:
        val = '0'
    try:
        return max(0, int(str(val or '0').split('#')[0].strip()))
    except ValueError:
        return 0


@plugin.interval(3600)
def prune_markov(bot):
    """Trim oversized chains.  Does nothing unless max_rows_per_channel > 0."""
    cap = _prune_cap(bot)
    if not cap or not _db_path:
        return
    conn = _get_db()
    try:
        counts = conn.execute(
            "SELECT channel, COUNT(*) FROM markov GROUP BY channel").fetchall()
    finally:
        conn.close()
    for channel, n in counts:
        excess = n - cap
        if excess <= 0:
            continue
        with _db_lock:
            w = _writer()
            with w:
                w.execute(
                    """DELETE FROM markov WHERE rowid IN (
                         SELECT rowid FROM markov WHERE channel = ?
                          ORDER BY frequency ASC, rowid ASC LIMIT ?)""",
                    (channel, min(excess, PRUNE_BATCH)),
                )
        LOG.info("markov: pruned %d rows from %s (cap %d)", min(excess, PRUNE_BATCH), channel, cap)


def _choose(pairs):
    items, weights = zip(*pairs)
    return random.choices(items, weights=weights, k=1)[0]


def _generate(bot, channel, first_words):
    conn = _get_db()
    try:
        if not first_words:
            rows = conn.execute(
                """
                SELECT third_word, frequency FROM markov
                 WHERE channel     = ?
                   AND first_word  IS NULL
                   AND second_word IS NULL
                   AND third_word  IS NOT NULL
                """,
                (channel,),
            ).fetchall()
            if not rows:
                return None
            first_word = _choose(rows)

            rows = conn.execute(
                """
                SELECT third_word, frequency FROM markov
                 WHERE channel     = ?
                   AND first_word  IS NULL
                   AND second_word = ?
                   AND third_word  IS NOT NULL
                """,
                (channel, first_word),
            ).fetchall()
            if not rows:
                return None

            second_word = _choose(rows)
            words = [first_word, second_word]

        elif len(first_words) == 1:
            first_word = first_words[0].lower()
            rows = conn.execute(
                """
                SELECT second_word, third_word, frequency FROM markov
                 WHERE channel     = ?
                   AND first_word  = ?
                   AND second_word IS NOT NULL
                   AND third_word  IS NOT NULL
                """,
                (channel, first_word),
            ).fetchall()
            if not rows:
                return None

            second_word, third_word = _choose(
                [((s, t), f) for s, t, f in rows]
            )
            words = [first_word, second_word, third_word]

        else:
            words = [w.lower() for w in first_words]

        for _ in range(30):
            rows = conn.execute(
                """
                SELECT third_word, frequency FROM markov
                 WHERE channel     = ?
                   AND first_word  = ?
                   AND second_word = ?
                """,
                (channel, words[-2], words[-1]),
            ).fetchall()
            if not rows:
                break

            next_word = _choose(rows)
            if next_word is None:
                break

            words.append(next_word)

            # Cap output length to stay within IRC limits
            if len(" ".join(words)) >= MAX_OUTPUT_LEN:
                break
    finally:
        conn.close()

    if words == first_words:
        return None

    out = " ".join(words)
    if len(out) > MAX_OUTPUT_LEN:
        out = out[:MAX_OUTPUT_LEN].rsplit(" ", 1)[0]
    return out
