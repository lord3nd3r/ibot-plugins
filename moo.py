# -*- coding: utf-8 -*-
"""
Ultimate Moo Plugin for Sopel – v3.8 – Legendary Edition
- Per-user-per-channel cooldowns
- sudo moo: once/hour per user per channel
- Global & per-channel stats and leaderboards
- moohelp PM-only with all commands + aliases listed
- /me moos increments moo count with no cooldown

✨ Prettier, emoji-rich output styled like karma.py. ✨

FIXES APPLIED (Sopel 8.0.4):
✅ Prevent `sudo moo` (and whitespace variants) from triggering generic moo detector (no double count/output)
✅ Legacy DB setup uses IF NOT EXISTS (reload-safe)
✅ Session upserts use `excluded` for better portability
✅ sudo moo uses shared increment logic (milestones still fire) without random moo output
✅ Added 8 more Linux/terminal moos to the `moos` list (total: 16 linux moos)
"""

from sopel import plugin
import random
import logging
import time
import threading
from sqlalchemy import text

logger = logging.getLogger(__name__)
# Bot nick (set at setup)
BOT_NICK_LOWER = None

# Default behavior values (can be overridden from the `moo` config section)
# Cooldowns (seconds)
MOO_COOLDOWN = 6         # moo cooldown
SUDO_COOLDOWN = 3600     # sudo moo cooldown (1 hour)

# Legendary moo chance (0.0 - 1.0)
LEGENDARY_CHANCE = 0.02

# sudo moo big loss chance (0.0 - 1.0)
# (Default is intentionally very low; -100 moos should be rare.)
SUDO_BIG_LOSS_CHANCE = 0.005

# Use monotonic clock for cooldowns
_time = time.monotonic

# Cooldown tracking: (channel, nick) → timestamp
LAST_MOO = {}
LAST_SUDO = {}
SUDO_NOTIFIED = {}        # (channel, nick) -> last cooldown notice timestamp
_CD_LOCK = threading.Lock()

# Per-channel cap on moo-triggered bot replies (cfg: [moo] max_rate_per_15sec).
# 0 = unlimited.  Read leniently (the cfg value may carry an inline comment).
MAX_RATE_PER_15SEC = 0
_CHAN_RATE = {}           # channel -> deque of reply timestamps

# Channel settings cache: channel_lower -> bool (True = enabled, False = disabled)
CHANNEL_SETTINGS = {}
SETTING_LOCK = threading.Lock()


def _is_channel(name):
    """Return True if this looks like a real channel name."""
    return bool(name) and name.startswith(("#", "&", "+", "!"))


def _cooldown_take(store, key, cooldown):
    """Atomically check+set a cooldown.  True if the action may proceed."""
    now = _time()
    with _CD_LOCK:
        if now - store.get(key, -1e9) < cooldown:
            return False
        store[key] = now
        return True


def _channel_rate_ok(chan):
    """Global (all users) per-channel limit on moo replies."""
    if MAX_RATE_PER_15SEC <= 0:
        return True
    from collections import deque
    now = _time()
    with _CD_LOCK:
        dq = _CHAN_RATE.setdefault(chan, deque())
        while dq and now - dq[0] > 15:
            dq.popleft()
        if len(dq) >= MAX_RATE_PER_15SEC:
            return False
        dq.append(now)
        return True


def _lenient_int(val, default):
    try:
        return int(str(val).split('#', 1)[0].split(';', 1)[0].strip())
    except (TypeError, ValueError):
        return default


def _prune_cooldowns(store, max_age):
    """Lightly prune old cooldown entries to prevent unbounded growth."""
    now = _time()
    with _CD_LOCK:
        to_delete = [k for k, t in store.items() if now - t > max_age]
        for k in to_delete:
            store.pop(k, None)


def _load_channel_settings(bot):
    global CHANNEL_SETTINGS
    settings = {}
    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                rows = s.execute(text("SELECT channel, enabled FROM moo_channel_settings")).fetchall()
                settings = {r[0].lower(): bool(r[1]) for r in rows if r and r[0]}
        else:
            conn = bot.db.connect()
            cur = conn.cursor()
            cur.execute("SELECT channel, enabled FROM moo_channel_settings")
            rows = cur.fetchall()
            conn.close()
            settings = {r[0].lower(): bool(r[1]) for r in rows if r and r[0]}
    except Exception:
        pass
    with SETTING_LOCK:
        CHANNEL_SETTINGS = settings


def _is_moo_enabled(bot, channel):
    if not channel or not _is_channel(channel):
        return True
    chan_low = channel.lower()
    with SETTING_LOCK:
        if chan_low in CHANNEL_SETTINGS:
            return CHANNEL_SETTINGS[chan_low]
    try:
        val = bot.db.get_channel_value(chan_low, "moo_enabled")
        if val is not None:
            enabled = bool(val)
            with SETTING_LOCK:
                CHANNEL_SETTINGS[chan_low] = enabled
            return enabled
    except Exception:
        pass
    return True


def _set_moo_enabled(bot, channel, enabled):
    if not channel or not _is_channel(channel):
        return False
    chan_low = channel.lower()
    val = 1 if enabled else 0

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                s.execute(
                    text("""
                        INSERT INTO moo_channel_settings (channel, enabled)
                        VALUES (:c, :e)
                        ON CONFLICT(channel) DO UPDATE SET enabled = excluded.enabled
                    """),
                    {"c": chan_low, "e": val}
                )
                s.commit()
        else:
            conn = bot.db.connect()
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO moo_channel_settings (channel, enabled) VALUES (?, ?)",
                (chan_low, val)
            )
            conn.commit()
            conn.close()
    except Exception:
        logger.exception("Failed to write moo_channel_settings to DB")

    try:
        bot.db.set_channel_value(chan_low, "moo_enabled", bool(enabled))
    except Exception:
        pass

    with SETTING_LOCK:
        CHANNEL_SETTINGS[chan_low] = bool(enabled)
    return True


def _is_op_or_admin(bot, trigger):
    """Check if nick is a bot admin/owner or channel op/admin/owner."""
    if getattr(trigger, 'admin', False) or getattr(trigger, 'owner', False):
        return True
    try:
        cfg_admins = getattr(bot.config.core, 'admins', None)
        if isinstance(cfg_admins, (list, tuple, set)):
            if trigger.nick.lower() in {a.lower() for a in cfg_admins}:
                return True
        elif isinstance(cfg_admins, str) and cfg_admins.strip():
            if trigger.nick.lower() in {a.strip().lower() for a in cfg_admins.split(',') if a.strip()}:
                return True
    except Exception:
        pass

    chan_name = trigger.sender
    if _is_channel(chan_name):
        try:
            chan_obj = getattr(bot, 'channels', {}).get(chan_name)
            if chan_obj:
                if hasattr(chan_obj, 'is_op') and chan_obj.is_op(trigger.nick):
                    return True
                if hasattr(chan_obj, 'is_admin') and chan_obj.is_admin(trigger.nick):
                    return True
                if hasattr(chan_obj, 'is_owner') and chan_obj.is_owner(trigger.nick):
                    return True
                privs = getattr(chan_obj, 'privileges', None) or getattr(chan_obj, 'privs', None)
                if isinstance(privs, dict):
                    v = privs.get(trigger.nick) or privs.get(trigger.nick.lower())
                    if isinstance(v, int):
                        return bool(v & 4) or bool(v & 8) or bool(v & 16)
                    if isinstance(v, (set, list, tuple)):
                        markers = {str(x).lower() for x in v}
                        return bool(markers & {'o', 'op', '@', 'a', 'admin', 'q', 'owner', '~', '&'})
        except Exception:
            pass
    return False


# --------------------------------------------------------------
# Config reader
# --------------------------------------------------------------
def get_config(bot, option, default=None):
    parser = getattr(bot.config, "parser", None)
    if not parser or not parser.has_section("moo"):
        return default
    if not parser.has_option("moo", option):
        return default

    val = parser.get("moo", option).strip().lower()
    if val in ("true", "yes", "on", "1"):
        return True
    if val in ("false", "no", "off", "0"):
        return False
    try:
        return int(val)
    except ValueError:
        return parser.get("moo", option).strip()


# --------------------------------------------------------------
# Setup DB tables
# --------------------------------------------------------------
def setup(bot):
    global BOT_NICK_LOWER
    global MOO_COOLDOWN, SUDO_COOLDOWN, LEGENDARY_CHANCE, SUDO_BIG_LOSS_CHANCE
    global MAX_RATE_PER_15SEC
    BOT_NICK_LOWER = bot.nick.lower()

    parser = getattr(bot.config, "parser", None)
    if parser:
        if not parser.has_section("moo"):
            parser.add_section("moo")
        if not parser.has_option("moo", "leet_moo"):
            parser.set("moo", "leet_moo", "true")

    # Load configurable settings from the config parser (if present).
    try:
        # Use get_config which returns int/bool when possible
        MOO_COOLDOWN = int(get_config(bot, "moo_cooldown", MOO_COOLDOWN))
    except Exception:
        logger.exception("Invalid moo_cooldown in config; using default")
        MOO_COOLDOWN = MOO_COOLDOWN

    try:
        SUDO_COOLDOWN = int(get_config(bot, "sudo_cooldown", SUDO_COOLDOWN))
    except Exception:
        logger.exception("Invalid sudo_cooldown in config; using default")
        SUDO_COOLDOWN = SUDO_COOLDOWN

    try:
        LEGENDARY_CHANCE = float(get_config(bot, "legendary_chance", LEGENDARY_CHANCE))
    except Exception:
        logger.exception("Invalid legendary_chance in config; using default")
        LEGENDARY_CHANCE = LEGENDARY_CHANCE

    MAX_RATE_PER_15SEC = max(0, _lenient_int(get_config(bot, "max_rate_per_15sec", 0), 0))

    try:
        SUDO_BIG_LOSS_CHANCE = float(get_config(bot, "sudo_big_loss_chance", SUDO_BIG_LOSS_CHANCE))
    except Exception:
        logger.exception("Invalid sudo_big_loss_chance in config; using default")
        SUDO_BIG_LOSS_CHANCE = SUDO_BIG_LOSS_CHANCE

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                # Global counts per nick
                s.execute(text("""
                    CREATE TABLE IF NOT EXISTS moo_counts (
                        nick TEXT PRIMARY KEY,
                        count INTEGER DEFAULT 0
                    )
                """))

                # Per-channel counts per nick
                s.execute(text("""
                    CREATE TABLE IF NOT EXISTS moo_counts_chan (
                        nick TEXT,
                        channel TEXT,
                        count INTEGER DEFAULT 0,
                        PRIMARY KEY (nick, channel)
                    )
                """))

                # Per-channel enabled/disabled settings
                s.execute(text("""
                    CREATE TABLE IF NOT EXISTS moo_channel_settings (
                        channel TEXT PRIMARY KEY,
                        enabled INTEGER DEFAULT 1
                    )
                """))

                s.commit()
        else:
            conn = bot.db.connect()
            # Global counts per nick
            conn.execute("""
                CREATE TABLE IF NOT EXISTS moo_counts (
                    nick TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            """)
            # Per-channel counts per nick
            conn.execute("""
                CREATE TABLE IF NOT EXISTS moo_counts_chan (
                    nick TEXT,
                    channel TEXT,
                    count INTEGER DEFAULT 0,
                    PRIMARY KEY (nick, channel)
                )
            """)
            # Per-channel enabled/disabled settings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS moo_channel_settings (
                    channel TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1
                )
            """)
            conn.commit()
            conn.close()

        _load_channel_settings(bot)
    except Exception:
        logger.exception("Moo setup error")


# --------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------
def db_helper(bot, nick, op="get", val=0):
    """Global moo counts (network-wide per nick)."""
    nick = nick.strip().lower()
    bot_nick = (BOT_NICK_LOWER or bot.nick.lower())

    # Never track stats for the bot itself; treat as 0
    if nick == bot_nick:
        return 0

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                if op == "get":
                    row = s.execute(
                        text("SELECT count FROM moo_counts WHERE nick = :n"),
                        {"n": nick}
                    ).fetchone()
                    return row[0] if row else 0

                # increment
                row = s.execute(
                    text("SELECT count FROM moo_counts WHERE nick = :n"),
                    {"n": nick}
                ).fetchone()
                new = (row[0] if row else 0) + val

                s.execute(
                    text("""
                        INSERT INTO moo_counts (nick, count)
                        VALUES (:n, :c)
                        ON CONFLICT(nick) DO UPDATE SET count = excluded.count
                    """),
                    {"n": nick, "c": new}
                )
                s.commit()
                return new

        # Legacy sqlite
        else:
            conn = bot.db.connect()
            cur = conn.cursor()

            if op == "get":
                cur.execute("SELECT count FROM moo_counts WHERE nick = ?", (nick,))
                row = cur.fetchone()
                conn.close()
                return row[0] if row else 0

            cur.execute("SELECT count FROM moo_counts WHERE nick = ?", (nick,))
            row = cur.fetchone()
            new = (row[0] if row else 0) + val
            cur.execute(
                "INSERT OR REPLACE INTO moo_counts (nick, count) VALUES (?, ?)",
                (nick, new)
            )
            conn.commit()
            conn.close()
            return new

    except Exception as e:
        logger.exception("DB error (global)")
        return -1


def db_helper_chan(bot, nick, channel, op="get", val=0):
    """Per-channel moo counts per nick."""
    nick = nick.strip().lower()
    channel = (channel or "").strip().lower()
    if not channel:
        return 0  # no channel context → treat as 0 for per-channel

    bot_nick = (BOT_NICK_LOWER or bot.nick.lower())
    # Never track stats for the bot itself; treat as 0
    if nick == bot_nick:
        return 0

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                if op == "get":
                    row = s.execute(
                        text(
                            "SELECT count FROM moo_counts_chan "
                            "WHERE nick = :n AND channel = :c"
                        ),
                        {"n": nick, "c": channel}
                    ).fetchone()
                    return row[0] if row else 0

                row = s.execute(
                    text(
                        "SELECT count FROM moo_counts_chan "
                        "WHERE nick = :n AND channel = :c"
                    ),
                    {"n": nick, "c": channel}
                ).fetchone()
                new = (row[0] if row else 0) + val

                s.execute(
                    text("""
                        INSERT INTO moo_counts_chan (nick, channel, count)
                        VALUES (:n, :c, :v)
                        ON CONFLICT(nick, channel) DO UPDATE SET count = excluded.count
                    """),
                    {"n": nick, "c": channel, "v": new}
                )
                s.commit()
                return new

        else:
            conn = bot.db.connect()
            cur = conn.cursor()

            if op == "get":
                cur.execute(
                    "SELECT count FROM moo_counts_chan WHERE nick = ? AND channel = ?",
                    (nick, channel)
                )
                row = cur.fetchone()
                conn.close()
                return row[0] if row else 0

            cur.execute(
                "SELECT count FROM moo_counts_chan WHERE nick = ? AND channel = ?",
                (nick, channel)
            )
            row = cur.fetchone()
            new = (row[0] if row else 0) + val
            cur.execute(
                "INSERT OR REPLACE INTO moo_counts_chan (nick, channel, count) "
                "VALUES (?, ?, ?)",
                (nick, channel, new)
            )
            conn.commit()
            conn.close()
            return new

    except Exception as e:
        logger.exception("DB error (channel)")
        return -1


# --------------------------------------------------------------
# Moo responses
# --------------------------------------------------------------
moos = [
    "Moo", "Moooooo", "MOOOOOO", "Moo?", "Moo Moo", "MOOOOO!",
    "mOoOoO", "Moooooooo!", "SuperMoo!", "Moo-calypse now!",
    "Schrödingcow", "sudo moo", "Moo on the rocks",
    "404: Moo not found", "Moo.exe has stopped responding",
    "Live, laugh, moo", "Moo-mentum conserved", "Moo++",
    "m0000000000000000", "MOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO",
    "Moo²", "Moo³", "Moo is love, moo is life",
    "Cowabunga!", "MooCoin to the mooooon", "MooBERT",
    "There is no cloud. It's just someone else's pasture.",
    "ELON JUST TWEETED A COW EMOJI",
    "Kernel panic: not enough moo", "Segmoo fault",
    "docker run moo", "systemctl restart cows",
    "More cowbell!", "Quantum cow",
    "The final moo is not the end",

    # linux nerd moos (original 16)
    "moo@localhost:~$",
    "sudo: moo: command not found",
    "Segmentation moo (core dumped)",
    "Permission denied: /dev/moo",
    "dmesg | grep moo",
    "systemd[1]: moo.service failed",
    "pkill -9 moo",
    "Welcome to Moo GNU/Linux",
    "bash: moo: No such file or pasture",
    "apt install moo",
    "pacman -S moo",
    "make moo && make install",
    "cron[1337]: (root) CMD (moo)",
    "ssh moo@pasture.local",
    "mount: /mnt/moo: bad supercow",
    "OOM killer: killed process moo",

    # 8 more linux nerd moos (added)
    "journalctl -u moo.service -n 50",
    "systemctl status moo.service",
    "tail -f /var/log/moo.log",
    "export MOOFLAGS='--verbose'",
    "chmod +x /usr/bin/moo",
    "ln -s /usr/bin/cowsay /usr/local/bin/moo",
    "grep -R \"moo\" /etc 2>/dev/null",
    "ps aux | awk '/moo/ {print $2}' | xargs kill",

    # cowsay style one-liners
    "< moo > \\ ^__^ (oo)\\_______",
    "( oo ) < deal with it. > 😎",
    "^__^ < have you tried turning the cow off and on again? >",
    "< moo > — brought to you by cowsay(1)",
    "(oo) < I'm not a regular cow, I'm a cool cow >",
    "< this moo is certified organic > 🐄",
    "^__^ < moo is just cow for hello >",
    "< one small moo for man, one giant moo for cowkind > 🚀",
    "(oo) < I was told there would be grass >",
    "< moo > — this is the way 🐄",
    "^__^ < in a world of baas, dare to moo >",
    "< the cow whisperer has entered the chat > (oo)",
    "( oo ) < my other ride is a tractor > 🚜",
    "< moo loud, moo proud > ^__^",
    "(oo)\\_______  < I see you mooing over there >",
    "< excuse me sir, do you have a moment to talk about moo? > 🐄",
]


legendary_moos = [
    "🌈 LEGENDARY MOO DROPS FROM THE SKY 🌈",
    "🔥 MOOCRITICAL HIT! 20x DAMAGE! 🔥",
    "✨ Shiny Golden Moo appears! ✨",
    "🌟 Cosmic Cow bellows across the universe: MOOOOOOOO 🌟",
    "💎 Diamond-encrusted Moo echoes through the pasture 💎",
    "⚡ THUNDERMOO STRIKES! The ground trembles... ⚡",
    "🧬 Genetic Supercow says: MOO+MOO = MOO² 🧬",
    "👑 KING OF COWS DECLARES: This is a LEGENDARY MOO 👑"
]

SUDO_BIG_WIN_MSGS = [
    "🐮 {nick}, the Supercow showers you in clover! +{amt} moos — the herd chants your name!",
    "🎉 {nick} hit the moo jackpot! +{amt} moos! The pasture throws a party.",
    "🍀 Lucky bovine day! {nick} gets +{amt} moos. Don't spend it all on hay.",
]

SUDO_BIG_LOSS_MSGS = [
    "😬 {nick}, the pasture protested and reclaimed {amt} moos. Oof.",
    "💩 Bad barn karma! {nick} lost {amt} moos — even the chickens are laughing.",
    "🕳️ {nick} dropped {amt} moos down a mysterious hole. We're sorry.",
]

MILESTONES = {
    1: "First moo! The herd welcomes you. 🐄",
    10: "Moo Adept achieved! ⭐",
    50: "Certified Mooologist 🎓",
    100: "Moo Master rank up! 🧙‍♂️🐄",
    500: "Legendary Cow status: UNLOCKED 💫",
    1000: "MOO GOD HAS AWAKENED ⚡",
    2000: "The cows are writing fanfics about you now 📚🐄",
    5000: "Global moo shortage declared 🚨",
    10000: "ELON JUST TWEETED: 'this guy moos too much' 🐂🚀"
}


def _handle_moo_increment(bot, nick, chan, legendary=None, say_response=True, inc_override=None):
    """
    Shared increment logic for moo triggers.

    legendary: if None, decide randomly; otherwise force True/False.
    say_response: if True, bot.say() a moo line.
    inc_override: if not None, force increment amount (e.g. sudo moo +10)
    """
    legendary = (random.random() < LEGENDARY_CHANCE) if legendary is None else legendary

    if say_response:
        msg = random.choice(legendary_moos if legendary else moos)
        bot.say(msg)

    if inc_override is not None:
        inc = inc_override
    else:
        inc = 20 if legendary else 1

    # Global count
    g_count = db_helper(bot, nick, "inc", inc)

    # Per-channel count (only if in a real channel)
    if _is_channel(chan):
        db_helper_chan(bot, nick, chan, "inc", inc)

    # Legendary message only for normal moo events (not sudo override)
    if legendary and g_count >= 0 and inc_override is None:
        bot.say(
            f"🌈 LEGENDARY MOO! {nick} gains +{inc} moos "
            f"(🌐 total: {g_count:,})"
        )

    # Only announce milestones on valid (non-error) counts
    if g_count > 0 and g_count in MILESTONES:
        bot.say(f"📈 Milestone unlocked for {nick} ({g_count:,} moos): {MILESTONES[g_count]}")


# --------------------------------------------------------------
# Moo detector (text) — EXCLUDES "sudo moo" (incl whitespace variants)
# --------------------------------------------------------------
@plugin.rule(r"(?i)^(?!\s*sudo\s+moo\s*$).*?\b(m[0o]{2,}s?)\b")
def moo_response(bot, trigger):
    if not trigger.nick or trigger.nick.lower() == bot.nick.lower():
        return
    if not _is_moo_enabled(bot, trigger.sender):
        return

    # CTCP ACTIONs (e.g. /me moos) are handled by moo_action().
    # Sopel can run @rule handlers for ACTION events too; avoid double-firing.
    if getattr(trigger, "event", "").upper() == "ACTION":
        return
    if getattr(trigger, "ctcp", None) == "ACTION":
        return
    try:
        if getattr(trigger, "args", None) and len(trigger.args) > 1:
            if isinstance(trigger.args[1], str) and trigger.args[1].startswith("\x01ACTION"):
                return
    except Exception:
        pass

    chan = (trigger.sender or "").lower()
    nick = trigger.nick
    key = (chan, nick.lower())

    _prune_cooldowns(LAST_MOO, 3600)

    # Ignore zero-moo when leet_moo is OFF
    moo_token = (trigger.group(1) or "")
    if not get_config(bot, "leet_moo", True) and "0" in moo_token:
        return

    if not _cooldown_take(LAST_MOO, key, MOO_COOLDOWN):
        return
    if not _channel_rate_ok(chan):
        return

    _handle_moo_increment(bot, nick, chan, legendary=None, say_response=True)


# --------------------------------------------------------------
# /me moos (ACTION) — shares the text-moo cooldown (silent when on cooldown)
# --------------------------------------------------------------
# Match CTCP ACTIONs like: /me moos  OR  /me moos! (allow simple punctuation)
# Register common punctuation variants to avoid using unsupported decorators
@plugin.action_commands("moos", "moos!", "moos?", "moos.")
def moo_action(bot, trigger):
    """
    Handle /me moos (CTCP ACTION "moos") as a moo.  Uses the same per-user
    cooldown as a typed moo plus the per-channel reply cap.
    """
    if not trigger.nick or trigger.nick.lower() == bot.nick.lower():
        return
    if not _is_moo_enabled(bot, trigger.sender):
        return

    chan = (trigger.sender or "").lower()
    nick = trigger.nick

    _prune_cooldowns(LAST_MOO, 3600)
    if not _cooldown_take(LAST_MOO, (chan, nick.lower()), MOO_COOLDOWN):
        return
    if not _channel_rate_ok(chan):
        return

    _handle_moo_increment(bot, nick, chan, legendary=None, say_response=True)


# --------------------------------------------------------------
# sudo moo (1/hour per user per channel) — uses shared increment logic
# --------------------------------------------------------------
@plugin.rule(r"(?i)^\s*sudo\s+moo\s*$")
def sudo_moo(bot, trigger):
    if not trigger.nick or trigger.nick.lower() == bot.nick.lower():
        return
    if not _is_moo_enabled(bot, trigger.sender):
        return

    chan = (trigger.sender or "").lower()
    nick = trigger.nick
    key = (chan, nick.lower())

    now = _time()
    _prune_cooldowns(LAST_SUDO, 86400)
    _prune_cooldowns(SUDO_NOTIFIED, 86400)

    if not _cooldown_take(LAST_SUDO, key, SUDO_COOLDOWN):
        # Silent in channel.  Tell the user privately, at most once per
        # cooldown window, so repeated attempts can't make the bot spam.
        if _cooldown_take(SUDO_NOTIFIED, key, SUDO_COOLDOWN):
            with _CD_LOCK:
                last = LAST_SUDO.get(key, now)
            remaining = max(0, int(SUDO_COOLDOWN - (now - last)))
            m, s = divmod(remaining, 60)
            left = f"{m}m {s}s" if m > 0 else f"{s}s"
            bot.notice(f"⏳ sudo moo cooldown: {left} left.", nick)
        return

    if not _channel_rate_ok(chan):
        # Don't burn the user's hourly sudo when the channel cap swallowed it.
        with _CD_LOCK:
            LAST_SUDO.pop(key, None)
        return

    # Outcome probabilities (exclusive ranges):
    #  - 5% chance: big win (+30 moos)
    #  - next SUDO_BIG_LOSS_CHANCE: big loss (-100 moos)
    #  - otherwise: normal sudo reward (+10 moos)
    r = random.random()
    if r < 0.05:
        msg = random.choice(SUDO_BIG_WIN_MSGS).format(nick=nick, amt=30)
        bot.say(msg)
        _handle_moo_increment(bot, nick, chan, legendary=False, say_response=False, inc_override=30)
    elif r < 0.05 + max(0.0, min(1.0, SUDO_BIG_LOSS_CHANCE)):
        msg = random.choice(SUDO_BIG_LOSS_MSGS).format(nick=nick, amt=100)
        bot.say(msg)
        _handle_moo_increment(bot, nick, chan, legendary=False, say_response=False, inc_override=-100)
    else:
        bot.say("🐄⚡ Super Cow Powers activated! (+10 moos!)")
        _handle_moo_increment(bot, nick, chan, legendary=False, say_response=False, inc_override=10)


# --------------------------------------------------------------
# .moo / $moo command (on/off/status/toggle per channel)
# --------------------------------------------------------------
@plugin.commands("moo", "mooon", "moooff")
def moo_command(bot, trigger):
    """Channel command to turn moo on or off per channel or check status.

    Usage:
        .moo on        -> Enable moo in this channel (op/admin only)
        .moo off       -> Disable moo in this channel (op/admin only)
        .moo status    -> Check if moo is enabled in this channel
        .moo toggle    -> Toggle moo on/off (op/admin only)
        .moo           -> Trigger a moo (if enabled)
    """
    cmd = (trigger.group(1) or "").strip().lower()
    arg = (trigger.group(2) or "").strip().lower()
    chan = trigger.sender

    if not _is_channel(chan):
        bot.notice("Moo channel settings only work inside a channel.", trigger.nick)
        return

    if cmd == "mooon":
        arg = "on"
    elif cmd == "moooff":
        arg = "off"

    if arg in ("off", "disable", "stop"):
        if not _is_op_or_admin(bot, trigger):
            bot.reply("Only channel operators or admins can disable moo in this channel.")
            return
        _set_moo_enabled(bot, chan, False)
        bot.say(f"🔇 Moo has been DISABLED in {chan}.")
        return

    elif arg in ("on", "enable", "start"):
        if not _is_op_or_admin(bot, trigger):
            bot.reply("Only channel operators or admins can enable moo in this channel.")
            return
        _set_moo_enabled(bot, chan, True)
        bot.say(f"🔔 Moo has been ENABLED in {chan}! 🐄")
        return

    elif arg in ("toggle",):
        if not _is_op_or_admin(bot, trigger):
            bot.reply("Only channel operators or admins can toggle moo in this channel.")
            return
        current = _is_moo_enabled(bot, chan)
        new_state = not current
        _set_moo_enabled(bot, chan, new_state)
        if new_state:
            bot.say(f"🔔 Moo has been ENABLED in {chan}! 🐄")
        else:
            bot.say(f"🔇 Moo has been DISABLED in {chan}.")
        return

    elif arg in ("status", "check"):
        enabled = _is_moo_enabled(bot, chan)
        status_str = "ENABLED 🔔" if enabled else "DISABLED 🔇"
        bot.say(f"🐄 Moo status in {chan}: {status_str}")
        return

    elif not arg:
        # Bare .moo / $moo command
        if not _is_moo_enabled(bot, chan):
            bot.notice(f"Moo is currently disabled in {chan}.", trigger.nick)
            return
        # Same per-user cooldown and per-channel cap as a typed moo; silent
        # while on cooldown.
        lchan = chan.lower()
        _prune_cooldowns(LAST_MOO, 3600)
        if not _cooldown_take(LAST_MOO, (lchan, trigger.nick.lower()), MOO_COOLDOWN):
            return
        if not _channel_rate_ok(lchan):
            return
        _handle_moo_increment(bot, trigger.nick, chan, legendary=None, say_response=True)
    else:
        bot.reply("Usage: .moo [on|off|status|toggle]")


# --------------------------------------------------------------
# .moocount / .mymoo / .moos
# --------------------------------------------------------------
@plugin.commands("moocount", "mymoo")
def moocount(bot, trigger):
    arg = (trigger.group(2) or "").strip()
    target = arg or trigger.nick

    global_count = db_helper(bot, target, "get")

    chan = (trigger.sender or "").lower()
    is_channel = _is_channel(chan)
    if is_channel:
        chan_count = db_helper_chan(bot, target, chan, "get")
        bot.say(
            f"📊 {target}: 🐄 {chan_count:,} moo"
            f"{'' if chan_count == 1 else 's'} in {chan} | "
            f"🌐 {global_count:,} moo"
            f"{'' if global_count == 1 else 's'} total"
        )
    else:
        bot.say(
            f"📊 {target} has 🌐 {global_count:,} moo"
            f"{'' if global_count == 1 else 's'} total."
        )


# --------------------------------------------------------------
# .mootop / .topmoo (global leaderboard)
# --------------------------------------------------------------
@plugin.commands("mootop", "topmoo")
def mootop_global(bot, trigger):
    try:
        limit = int((trigger.group(2) or "10").split()[0])
    except Exception:
        limit = 10

    limit = max(1, min(50, limit))
    query_limit = limit + 1  # in case bot is in list

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                rows = s.execute(
                    text(
                        "SELECT nick, count FROM moo_counts "
                        "ORDER BY count DESC, nick LIMIT :l"
                    ),
                    {"l": query_limit}
                ).fetchall()
        else:
            conn = bot.db.connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT nick, count FROM moo_counts "
                "ORDER BY count DESC, nick LIMIT ?",
                (query_limit,)
            )
            rows = cur.fetchall()
            conn.close()

        botnick = BOT_NICK_LOWER or bot.nick.lower()
        entries = [(n, c) for (n, c) in rows if n.lower() != botnick]

        if not entries:
            bot.say("🏆 No moo legends yet.")
            return

        line = " | ".join(f"{n} == {c:,}" for (n, c) in entries[:limit])
        bot.say(f"🏆 Global Moo Legends: {line}")

    except Exception:
        logger.exception("Moo leaderboard error")
        bot.say("⚠️ Moo leaderboard error.")


# --------------------------------------------------------------
# .mootopchan / .chanmootop / .topmoochan (per-channel leaderboard)
# --------------------------------------------------------------
@plugin.commands("mootopchan", "chanmootop", "topmoochan")
def mootop_channel(bot, trigger):
    chan = (trigger.sender or "").lower()
    if not _is_channel(chan):
        bot.say("📺 Channel-only command. Try this inside a channel.")
        return

    try:
        limit = int((trigger.group(2) or "10").split()[0])
    except Exception:
        limit = 10

    limit = max(1, min(50, limit))
    query_limit = limit + 1

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                rows = s.execute(
                    text(
                        "SELECT nick, count FROM moo_counts_chan "
                        "WHERE channel = :c "
                        "ORDER BY count DESC, nick LIMIT :l"
                    ),
                    {"c": chan, "l": query_limit}
                ).fetchall()
        else:
            conn = bot.db.connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT nick, count FROM moo_counts_chan "
                "WHERE channel = ? "
                "ORDER BY count DESC, nick LIMIT ?",
                (chan, query_limit)
            )
            rows = cur.fetchall()
            conn.close()

        botnick = BOT_NICK_LOWER or bot.nick.lower()
        entries = [(n, c) for (n, c) in rows if n.lower() != botnick]

        if not entries:
            bot.say(f"🏆 No moo legends yet in {chan}.")
            return

        line = " | ".join(f"{n} == {c:,}" for (n, c) in entries[:limit])
        bot.say(f"🏆 Moo leaderboard in {chan}: {line}")

    except Exception:
        logger.exception("Channel moo leaderboard error")
        bot.say("⚠️ Channel moo leaderboard error.")


# --------------------------------------------------------------
# .totalmoo / .moostats
# --------------------------------------------------------------
@plugin.commands("totalmoo", "moostats")
def totalmoo(bot, trigger):
    """Global total & optionally this-channel total (for .moostats)."""
    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                total_global = s.execute(
                    text("SELECT SUM(count) FROM moo_counts")
                ).scalar() or 0
        else:
            conn = bot.db.connect()
            cur = conn.cursor()
            cur.execute("SELECT SUM(count) FROM moo_counts")
            row = cur.fetchone()
            total_global = (row[0] or 0) if row else 0
            conn.close()
    except Exception:
        logger.exception("Failed to calculate total moos")
        bot.say("⚠️ Failed to calculate total moos.")
        return

    cmd = trigger.group(1).lower() if trigger.group(1) else "totalmoo"

    chan = (trigger.sender or "").lower()
    is_channel = _is_channel(chan)

    if cmd == "moostats" and is_channel:
        try:
            if hasattr(bot.db, "session"):
                with bot.db.session() as s:
                    total_chan = s.execute(
                        text(
                            "SELECT SUM(count) FROM moo_counts_chan "
                            "WHERE channel = :c"
                        ),
                        {"c": chan}
                    ).scalar() or 0
            else:
                conn = bot.db.connect()
                cur = conn.cursor()
                cur.execute(
                    "SELECT SUM(count) FROM moo_counts_chan WHERE channel = ?",
                    (chan,)
                )
                row = cur.fetchone()
                total_chan = (row[0] or 0) if row else 0
                conn.close()

            bot.say(
                f"📊 Moo stats — 🌐 total: {total_global:,} | "
                f"📺 in {chan}: {total_chan:,}"
            )
        except Exception:
            bot.say(f"📊 Moo stats — 🌐 total: {total_global:,}")
    else:
        bot.say(f"📊 Total moos (🌐 network-wide): {total_global:,}.")


# --------------------------------------------------------------
# .mooreset (admin only)
# --------------------------------------------------------------
@plugin.commands("mooreset")
@plugin.require_admin()
def mooreset(bot, trigger):
    target = (trigger.group(2) or "").strip() or None

    try:
        if hasattr(bot.db, "session"):
            with bot.db.session() as s:
                if target:
                    low = target.lower()
                    s.execute(
                        text("DELETE FROM moo_counts WHERE nick = :n"),
                        {"n": low}
                    )
                    s.execute(
                        text("DELETE FROM moo_counts_chan WHERE nick = :n"),
                        {"n": low}
                    )
                else:
                    s.execute(text("DELETE FROM moo_counts"))
                    s.execute(text("DELETE FROM moo_counts_chan"))
                s.commit()
        else:
            conn = bot.db.connect()
            if target:
                low = target.lower()
                conn.execute(
                    "DELETE FROM moo_counts WHERE nick = ?",
                    (low,)
                )
                conn.execute(
                    "DELETE FROM moo_counts_chan WHERE nick = ?",
                    (low,)
                )
            else:
                conn.execute("DELETE FROM moo_counts")
                conn.execute("DELETE FROM moo_counts_chan")
            conn.commit()
            conn.close()

        if target:
            bot.say(f"🧹 Moo stats reset for {target}.")
        else:
            bot.say("🧹 All moo stats have been reset.")
    except Exception:
        logger.exception("Moo reset failed")
        bot.say("⚠️ Moo reset failed.")


# --------------------------------------------------------------
# moohelp / aboutmoo (PM-only)
# --------------------------------------------------------------
@plugin.commands("moohelp", "aboutmoo")
def moohelp(bot, trigger):
    """Send help ONLY to user privately (no channel spam)."""
    target = trigger.nick
    leet = "ON" if get_config(bot, "leet_moo", True) else "OFF"

    lines = [
        "🐄 Moo Plugin v3.8 – Legendary Edition",
        f"• Leet-moo: {leet}",
        "",
        "🔔 Automatic moo replies:",
        "   • moo / mooo / m000 → random moo (+1) or LEGENDARY (+20)",
        "",
        "🎭 /me moos (CTCP ACTION):",
        "   • Counts as a moo (+1 or LEGENDARY) with NO cooldown",
        "",
        "⏳ Cooldowns:",
        f"   • moo → {MOO_COOLDOWN}s per user per channel",
        f"   • sudo moo → {SUDO_COOLDOWN // 3600} hour per user per channel",
        "",
        "📊 Stats & Commands:",
        "   • .moo [on|off|status|toggle]",
        "       → 🔔 Enable, disable, or check moo status in this channel (op/admin)",
        "   • .moocount /.mymoo [nick]",
        "       → Show moo count 🎯 in this channel + 🌐 total",
        "   • .mootop /.topmoo [N]",
        "       → 🏆 Top mooers (network-wide)",
        "   • .mootopchan /.chanmootop /.topmoochan [N]",
        "       → 🏆 Top mooers in this channel",
        "   • .totalmoo",
        "       → 📊 Total moos (network-wide)",
        "   • .moostats",
        "       → 📊 Total moos (network-wide + this channel)",
        "   • .mooreset [nick] (admin)",
        "       → 🧹 Reset moo stats (global + per-channel) for one user or everyone",
        "   • .moohelp /.aboutmoo",
        "       → This help message (PM only)",
        "",
        "💥 Extra:",
        "   • sudo moo → 🐄⚡ Super Cow Powers (+10 moos), once/hour per user per channel",
        "",
        "Tip: Start by mooing in any channel. The herd is listening. 🐄✨",
    ]

    for line in lines:
        bot.notice(line, target)



