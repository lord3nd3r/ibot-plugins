# mug_game.py - Coin, mugging, gambling & item shop for Sopel (FULL EMOJI CHAOS)
# Storage: Sopel bot.db (with merge import from old JSON if present)
from __future__ import annotations

import os
import json
import random
import threading
import time
import atexit

# Use OS-level entropy (/dev/urandom) for all game randomness so consecutive
# calls are truly independent — eliminates the Mersenne Twister streakiness.
_sysrand = random.SystemRandom()
from contextlib import contextmanager
import unicodedata
import re
import logging

from sopel import module
from sopel.config.types import StaticSection, ValidatedAttribute

import os as _gm_os, sys as _gm_sys
_gm_dir = _gm_os.path.dirname(_gm_os.path.abspath(__file__))
if _gm_dir not in _gm_sys.path:
    _gm_sys.path.insert(0, _gm_dir)
import _godmode  # noqa: E402

LOG = logging.getLogger(__name__)


# ============================================================
# ===============  SOPEL CONFIG SECTION  =====================
# ============================================================

class MugGameSection(StaticSection):
    """[mug_game] config section for Sopel's .cfg file.

    Example::

        [mug_game]
        enabled = true
    """
    enabled = ValidatedAttribute('enabled', bool, default=True)


def setup(bot):
    """Called by Sopel when the plugin is loaded."""
    bot.config.define_section('mug_game', MugGameSection)
    # Seed the bot's own wallet if it doesn't have one, and start proactive thread
    if BOT_PLAYER_ENABLED:
        _seed_bot_wallet(bot)
        _start_bot_proactive_thread(bot)
    # Start auto-mode sweep thread
    if VOICE_ENABLED:
        _start_voice_sweep_thread(bot)
    _godmode.load(bot)


def configure(config):
    """Called by ``sopel-plugins configure``."""
    config.define_section('mug_game', MugGameSection)
    config.mug_game.configure_setting(
        'enabled',
        'Enable the mug game plugin? (true/false)',
    )


def shutdown(bot):
    """Called by Sopel when the plugin is unloaded/reloaded."""
    _bot_proactive_stop.set()
    _voice_sweep_stop.set()
    # Persist godmode so it survives $reload / $rehash; do not clear it.
    try:
        _godmode.save(bot)
    except Exception:
        LOG.exception("Failed to persist godmode on shutdown")
    # Clear global caches to avoid state leaks across reloads
    global _channel_toggles, _flood_lockout, _flood_history, _last_cmd, _give_history_loaded, _data
    _channel_toggles = None
    _flood_lockout.clear()
    _flood_history.clear()
    _last_cmd.clear()
    _give_history_loaded = False
    _data = None


# ============================================================
# ===============  CONFIG & TUNING (EDIT ME)  ================
# ============================================================

PLUGIN_NAME = 'mug_game'
OLD_DATA_FILENAME = 'mug_game_coins.json'  # optional legacy import (in Sopel homedir)

# Primary channel where the mug game is played (used in disabled messages)
MUG_HOME_CHANNEL = '#mug'

# PM-admin allowlist (lowercase nicks)
# Add whoever should be able to use $mugadd/$mugset/$mugtake/$mugreset in PM.
ADMIN_NICKS = {"end3r"}

# Cooldowns (seconds)
COINS_COOLDOWN = 10 * 60
MUG_BASE_COOLDOWN = 5 * 60
MUG_EXTRA_FAIL_CD = 2 * 60
JAIL_TIME = 10 * 60
BET_COOLDOWN = 60
BOUNTY_COOLDOWN = 60  # prevents spam-bounty
TOP_CD = 10 * 60          # per-user cooldown for $top5/$top10

# ---- Auto-Mode (coin-based +v / +h / +o) ----
VOICE_ENABLED = True              # Master switch for auto-mode
VOICE_THRESHOLD = 500             # Coins needed to earn +v
HALFOP_THRESHOLD = 10_000_000     # Coins needed to earn +h (10M)
OP_THRESHOLD = 1_000_000_000      # Coins needed to earn +o (1B)
VOICE_CHECK_INTERVAL = 5 * 60     # Periodic sweep every 5 minutes
VOICE_CHANNELS = {'#mug'}         # Channels where auto-mode is active (lowercase)
VOICE_EXEMPT_NICKS = {'end3r', 'glitchy'}  # Nicks to never touch (lowercase)

# ---- Bot Player (Glitchy joins the game!) ----
# ---- High Score Topic Auto-Update ----
HIGHSCORE_TOPIC_ENABLED = True              # Master switch for auto-updating topic
HIGHSCORE_TOPIC_CHANNELS = {'#mug'}         # Channels to update topic in (lowercase)
HIGHSCORE_TOPIC_MARKER = '┃ 🏆'             # Delimiter that marks start of the HS section

# ---- Bot Player (Glitchy joins the game!) ----
BOT_PLAYER_ENABLED = True          # Master switch for bot participation
BOT_SEED_MONEY = 500_000           # Starting balance if bot has no record
BOT_RETALIATE = True               # Counter-mug when someone mugs the bot
BOT_RETALIATE_CHANCE = 60          # % chance bot retaliates after being mugged
BOT_RETALIATE_DELAY = (5, 15)      # Random delay range in seconds before counter-mug
BOT_PROACTIVE = False              # Periodically mug top players
BOT_PROACTIVE_INTERVAL = (30, 90)  # Random interval in minutes between proactive mugs
BOT_PROACTIVE_TARGETS = 5          # Pick from top N players
BOT_SUCCESS_CHANCE = 50            # Bot's mug success chance (slightly below player default)
BOT_STEAL_MIN = 5                  # Bot steal % range (lower than players — it's a bot)
BOT_STEAL_MAX = 15
BOT_FAIL_LOSS_MIN = 10             # Bot loss % range on fail
BOT_FAIL_LOSS_MAX = 25

# $coins base + scaling
COINS_MIN_GAIN = 15
COINS_MAX_GAIN = 75
COINS_SCALE_MIN_PCT = 5        # richer players get 5–15% of current money
COINS_SCALE_MAX_PCT = 15
COINS_SCALE_MAX_EXTRA = 1500   # cap the scaling gain so whales don't instantly go supernova

# Mugging economy
MUG_FEE_MIN = 2                # floor for mug fee
MUG_FEE_PCT = 0.1              # mug fee = 0.1% of attacker balance (min MUG_FEE_MIN)
MUG_MIN_BALANCE_PCT = 1        # attacker must have >= 1% of victim's money to mug them
MUG_MIN_BALANCE_FLOOR = 100    # …but always allow if victim has <= this many coins

# Mugging percent ranges
SUCCESS_STEAL_MIN = 10
SUCCESS_STEAL_MAX = 30
FAIL_LOSS_MIN = 5
FAIL_LOSS_MAX = 15
CRIT_LOSS_MIN = 20
CRIT_LOSS_MAX = 40

# Safeguards: scale the loss cap to the attacker's wealth so it hurts proportionally.
# Cap = max(floor, pct% of attacker money)
MAX_FAIL_LOSS_FLOOR = 100_000  # minimum cap
MAX_FAIL_LOSS_PCT = 5          # 5% of attacker money
MAX_CRIT_LOSS_FLOOR = 250_000  # minimum cap
MAX_CRIT_LOSS_PCT = 10         # 10% of attacker money

# Mugging chances (1–100 roll)
SUCCESS_CHANCE = 60
NORMAL_FAIL_CHANCE = 25
# remaining = critical fail

# Gambling
GAMBLE_MIN_BET = 1
GAMBLE_MAX_BET = 1_000_000_000  # 1B max bet to slow hyperinflation
BET_BASE_WIN_CHANCE = 40

# Anti-bullying / whale protection
RICH_VICTIM_THRESHOLD = 10_000
# Increase max steal pct so very wealthy victims are slightly easier to mug
# (was 10%). Adjust this if muggings against whales feel too weak.
RICH_VICTIM_MAX_STEAL_PCT = 25  # max steal per mug if victim is mega-rich

# Banana Peel item behavior
BANANA_SLIP_PER_ITEM_PCT = 5
BANANA_SLIP_MAX_PCT = 25
BANANA_SLIP_TRIGGERS_CRIT_FAIL = True

# Ultra-rare mug outcomes (turn these up if you want chaos)
MUG_MEGA_STEAL_CHANCE_PCT = 1      # 1% chance: mega steal mode triggers on a "success"
MUG_MEGA_STEAL_BONUS_PCT = 25      # adds +25% steal pct (still subject to caps/vest/etc)
MUG_OOPS_JAIL_CHANCE_PCT = 1       # 1% chance: instant oops-jail (even before roll)

# ============================================================
# ===================== TITLES (SANE STYLE) ==================
# ============================================================
# No title is shown for 0–49 coins.
# Titles are used in narration as: "{nick}, the {title}"
TITLE_THRESHOLDS = [
    (50, "🐀 Street Rat"),
    (200, "🔪 Pickpocket"),
    (750, "🧨 Menace"),
    (2500, "🦹 Thug"),
    (10000, "🕵️ Shadow Operative"),
    (50000, "💼 Crime Boss"),
    (250000, "🦈 Loan Shark"),
    (1000000, "👑 Underworld Kingpin"),
    (5000000, "🏛️ Crime Lord"),
]

# Anti-cheat settings
REQUIRE_IDENTIFIED = False     # Require NickServ identification for economy commands
GLOBAL_CMD_COOLDOWN = 3        # Min seconds between ANY economy command per user

# ---- Flood / spam gate ----
FLOOD_WINDOW = 60              # Rolling window in seconds
FLOOD_MAX_CMDS = 15            # Max commands in that window before lockout
FLOOD_LOCKOUT = 30 * 60        # 30-minute lockout
GIVE_COOLDOWN = 5 * 60         # 5 min cooldown on $give
GIVE_DAILY_MAX = 500_000       # Max coins transferable per day via $give
GIVE_DAILY_WINDOW = 86400      # Rolling window for daily give cap (24h)

# Bounty settings
BOUNTY_MIN = 10
BOUNTY_MAX = 100_000  # sanity cap per bounty placement (pool can exceed)

# -----------------------
# Item shop configuration
# -----------------------
ITEMS = {
    "mask": {
        "name": "Heist Mask",
        "price": 120,
        "desc": "Boosts your mug success chance.",
        "mug_success_bonus": 7,
    },
    "knucks": {
        "name": "Brass Knuckles",
        "price": 250,
        "desc": "Steal more on successful mugs.",
        "mug_steal_bonus_pct": 6,
    },
    "luckycoin": {
        "name": "Lucky Coin",
        "price": 180,
        "desc": "Extra coins from $coins + better $bet odds.",
        "coins_bonus_flat": 3,
        "bet_win_bonus": 7,
    },
    "vest": {
        "name": "Kevlar Vest",
        "price": 220,
        "desc": "Reduces how much others can steal from you.",
        "steal_reduction_pct": 20,
    },
    "cloak": {
        "name": "Shadow Cloak",
        "price": 500,
        "desc": "Chance to dodge a successful mug entirely.",
        "mug_immune_chance": 15,
    },
    "banana": {
        "name": "Banana Peel",
        "price": 50,
        "desc": "Traps muggers: chance they slip into disaster.",
        "banana_slip_chance": BANANA_SLIP_PER_ITEM_PCT,  # per item
    },
    "bail": {
        "name": "Bail Bondsman",
        "price": 5000,
        "desc": "Pays your bail once to spring you out of jail. Use $use bail (PM). 🪓🎉",
    },
}

# ============================================================
# ===================== FUNNY MESSAGE POOLS ==================
# ============================================================

# ONLY for people who try to mug themselves (PM admin tools won't use these)
SELF_MUG_MESSAGES = [
    "😢 You can’t mug yourself. Therapy is cheaper. (Probably.)",
    "🪞 You stare into the mirror and threaten it. The mirror wins.",
    "🤡 You try to rob yourself and immediately call the cops on yourself.",
    "🧠 Galaxy brain move: attempted self-mug. Zero coins gained.",
    "🫠 You aggressively pat your own pockets. Nothing happens.",
    "📉 You almost stole from yourself. Almost.",
    "🚨 Crime rejected. Victim and attacker are the same idiot.",
    "🪙 You reach into your pocket… and feel shame.",
    "🎭 You put on a mask, scare yourself, and run away.",
    "🧘 Inner peace achieved. Crime aborted.",
]

COINS_COOLDOWN_MESSAGES = [
    "⏳ Whoa there, coin goblin! More coins in {time}.",
    "🪙 The coin gods are buffering… try again in {time}.",
    "🐌 Your greed is on cooldown. Return in {time}.",
    "💤 The coins are respawning… give them {time}.",
    "☕ Banker’s on break. Next appointment in {time}.",
    "👀 The IRS noticed you. Lay low for {time}.",
    "🧊 Your wallet needs to chill. Try again in {time}.",
    "🫠 You’re melting the economy. Pause {time}.",
    "🧃 Hydrate first. Coins in {time}.",
    "🧠 Your dopamine is rate-limited. Next hit in {time}.",
]

MUG_COOLDOWN_MESSAGES = [
    "🕵️ Lay low… the cops still remember your face. Try again in {time}.",
    "🚑 You’re still recovering. Mug again in {time}.",
    "👟 Your getaway shoes are untied. Fix them in {time}.",
    "📸 CCTV is still tracking you. Hide {time} longer.",
    "😵 Emotional damage cooldown: {time}.",
    "🎒 Crime backpack is reloading… {time} left.",
    "🧼 You’re still washing fingerprints off. {time}.",
    "🧯 Your criminal aura is cooling down. {time}.",
    "🫵 The neighborhood watch is staring at you. Wait {time}.",
    "🧠 Your evil plan needs another brain cell. {time}.",
]

BET_COOLDOWN_MESSAGES = [
    "🎲 The casino bouncer says “not yet.” Try again in {time}.",
    "🃏 Dealer’s shuffling… slowly. {time} left.",
    "💸 Your wallet is begging for mercy. Wait {time}.",
    "🥴 You’re still dizzy from the last bet. {time}.",
    "🎰 The slot machine overheated. Cooling for {time}.",
    "🤡 The clown dealer dropped all the chips. {time}.",
    "🍀 The four-leaf clover needs a nap — try again in {time}.",
    "🐇 Your rabbit's foot is on strike; luck resumes in {time}.",
    "🧿 The lucky charm's battery is low. Recharge: {time}.",
    "🎲 An RNG gremlin spilled the odds. Cleanup ETA: {time}.",
    "🥠 Fortune cookie production delayed. New cookie in {time}.",
    "🐢 The luck turtle is taking a scenic detour — back in {time}.",
    "🪙 Your lucky coin is under the sofa cushions; retrieval: {time}.",
    "🐈‍⬛ Karma cat is filing your fate; grooming finishes in {time}.",
    "🔮 The crystal ball is foggy. The seer will return in {time}.",
    "👻 Your luck ghost is debugging the haunt — try again in {time}.",
    "👻 Your luck ghost is recharging. {time}.",
    "📉 The economy needs a second after your antics. {time}.",
    "🧨 Your gambling fuse is lit. Wait {time} before you explode again.",
]

BROKE_MESSAGES = [
    "💸 You’re broke. Like, *emotionally* and financially.",
    "🪫 Wallet empty. Dreams empty. Try $coins.",
    "🧻 You can’t afford air right now. Earn coins first.",
    "🥲 Not enough coins. The streets are calling: $coins.",
    "🫠 Your bank account just said 'lol'.",
]

INVALID_MESSAGES = [
    "❓ That ain’t it, chief. Check $mughelp.",
    "🧠 Brain.exe stopped. Try using the command correctly.",
    "🧩 Missing pieces. See $mughelp for syntax.",
    "🤨 What are you *trying* to do? $mughelp explains it.",
    "🫵 Skill issue. Read $mughelp. (I’m kidding… mostly.)",
]

# Optional one-line “vibe prefix” (no names; keeps mug output ONE LINE)
MUG_VIBE_PREFIXES = ["👣", "🧲", "🧤", "🕶️", "🧃", "🌑", "🧿"]

MUG_FAIL_MESSAGES = [
    "💥 {att} slipped mid-mug and yeeted {loss} coins across the street!",
    "🤦 {att} tried to look intimidating but sneezed and dropped {loss} coins!",
    "🩴 {att} tripped over shoelaces and made it rain {loss} coins!",
    "🐈 {att} got ambushed by a random cat and scattered {loss} coins!",
    "🕺 {att} attempted a dramatic pose and breakdanced {loss} coins away!",
    "🐦 A pigeon dive-bombed {att}, causing panic-loss of {loss} coins!",
    "💨 {att} let one rip, panicked, and dropped {loss} coins in shame!",
    "🧼 {att} tried to wipe fingerprints but wiped out and lost {loss} coins!",
    "📦 {att} mugged a mailbox by accident and paid {loss} coins in fines!",
    "🧃 {att} spilled the crime juice and lost {loss} coins buying napkins!",
]

MUG_SUCCESS_MESSAGES = [
    "🦹 {att} jumps {vic} and snatches {steal} coins! 💼",
    "🧤 {att} runs {vic}'s pockets for {steal} coins! 🪙",
    "🏃 {att} hits-and-dips {vic} for {steal} coins! 💨",
    "🧛 {att} drains {vic} for {steal} coins! 🩸🪙",
    "🧲 {att} magnetizes {vic}'s wallet: {steal} coins fly out! 🧲",
    "🪤 {att} sets a trap for {vic} and walks away with {steal} coins! 🪙",
    "📉 {att} commits tax evasion on {vic}: +{steal} coins!",
    "🫳 {att} yoinks {steal} coins off {vic} like it’s casual. 😭",
]

MUG_MEGA_SUCCESS_MESSAGES = [
    "🌟 MEGA HEIST! {att} pulls a legendary swipe on {vic} for {steal} coins!! 🏆💰",
    "🔥 ULTRA MUG! {att} hits {vic} with main-character energy: {steal} coins! 🎬🪙",
    "⚡ GOD TIER YOINK! {att} extracts {steal} coins from {vic}'s soul!! 👻🪙",
]

MUG_CRIT_FAIL_MESSAGES = [
    "🚔 CRITICAL FAIL! {att} faceplants, drops {loss} coins, and gets tossed in jail for {jail}. 🔒",
    "🚨 {att} mugs the air, loses {loss} coins, and the police applauded… then arrested them. Jail: {jail}.",
    "🐕 {att} got tackled by a tiny dog, lost {loss} coins, and got booked. Jail: {jail}.",
    "📛 {att} left fingerprints on EVERYTHING, lost {loss} coins, and got detained. Jail: {jail}.",
    "🧯 {att} set off a silent alarm somehow, dropped {loss} coins, and got hauled away. Jail: {jail}.",
    "🧱 {att} ran into a wall they forgot existed, lost {loss} coins, and went to jail: {jail}.",
]

BOUNTY_CONFIRM_MESSAGES = [
    "🎯 Bounty placed on {vic} for {amt} coins. Somebody is getting *touched*. 😈",
    "🪙 You put {amt} coins on {vic}'s head. That’s… oddly motivational. 🗡️",
    "📛 {vic} is now WANTED for {amt} coins. Channel justice incoming. 🚨",
    "🧨 Bounty armed: {vic} ({amt} coins). Chaos will find them soon. 💥",
]

BOUNTY_CLAIM_MESSAGES = [
    "🎯 BOUNTY CLAIMED! {att} collects {bounty} bonus coins for mugging {vic}! 🏆",
    "💰 Payday! {att} cashes in a {bounty}-coin bounty on {vic}. 🪙🪙🪙",
    "🚨 WANTED DEAD OR POOR: {att} claims {bounty} coins from {vic}'s bounty! 😈",
]

# ---- Bot player messages ----
BOT_RETALIATE_MESSAGES = [
    "🤖 {bot} didn't appreciate that. Time for payback on {vic}!",
    "🤖 {bot} cracks its knuckles. Nobody mugs a bot and walks away.",
    "🤖 {bot} reboots in REVENGE MODE and targets {vic}.",
    "🤖 {bot} says: 'You mugged the wrong bot, {vic}.' 😤",
    "🤖 {bot} initiates Protocol: GET-EVEN against {vic}.",
]

BOT_PROACTIVE_MESSAGES = [
    "🤖 {bot} has been watching {vic}'s fat wallet all day. Time to strike!",
    "🤖 {bot} emerges from the shadows and eyes {vic}'s coins…",
    "🤖 {bot} runs a wealth scan and locks onto {vic}. Target acquired.",
    "🤖 {bot} is feeling chaotic and {vic} is looking too rich.",
    "🤖 {bot} rolls up on {vic}. Nothing personal. Just code.",
]

BOT_MUG_SUCCESS_MESSAGES = [
    "🤖 {bot} snatches {steal} coins from {vic}! Beep boop, pay up. 💰",
    "🤖 {bot} runs {vic}'s pockets for {steal} coins. Cold. Calculated. 🧊",
    "🤖 {bot} yoinks {steal} coins from {vic}. Machine precision. 🎯",
    "🤖 {bot} hacks {vic}'s wallet and extracts {steal} coins. 💾",
]

BOT_MUG_FAIL_MESSAGES = [
    "🤖 {bot} glitches mid-heist and drops {loss} coins. Error 404: stealth not found. 😂",
    "🤖 {bot} trips over its own cables and loses {loss} coins to {vic}. 🔌",
    "🤖 {bot} blue-screens during the robbery! {vic} picks up {loss} coins. 💙💀",
    "🤖 {bot} fumbles the mug. {vic} catches {loss} coins mid-air. 🫳",
]

# ============================================================
# ===================== INTERNAL (DON'T EDIT) =================
# ============================================================

# Per-channel runtime enable/disable toggles (persisted in bot.db)
# {channel_lower: bool}  — missing key = enabled (default)
_channel_toggles: dict[str, bool] | None = None  # None = not loaded yet
_top_cd_cache: dict[tuple, float] = {}  # per-user cooldown for $top5/$top10
_bot_proactive_thread: threading.Thread | None = None  # background mugger thread
_bot_proactive_stop = threading.Event()  # signal to stop the proactive thread
_voice_sweep_stop = threading.Event()    # signal to stop the voice sweep thread

# Re-entrant lock: safe if helpers call helpers while locked
_data_lock = threading.RLock()
_topic_lock = threading.Lock()  # Serialize topic updates to prevent mirror writes
_data = None

# Conservative payload limit so we don't hit IRC's ~512 byte ceiling after prefixes.
SAFE_SAY_MAX_BYTES = 350


@contextmanager
def locked_data(bot):
    """Lock around any read-modify-write updates to avoid lost updates.

    The save runs after the lock is released: _save_data re-acquires the
    lock for the DB write itself, but its side effects (topic updates with
    retry sleeps, MODE sweeps) must not stall other economy commands.
    If the body raises, the save is skipped (same as before).
    """
    with _data_lock:
        data = _load_data(bot)
        yield data
    _save_data(bot)


def _rand(lst):
    return _sysrand.choice(lst)


def _utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


# Normalize nicknames/keys to avoid duplicate records from invisible
# characters, IRC formatting codes, or Unicode variants.
_MIRC_COLOR_RE = re.compile(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?')
_IRC_CTRL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\u200b\u200c\u200d\uFEFF]')
_IRC_FORMAT_RE = re.compile(r'[\x02\x0f\x16\x1d\x1f]')


def normalize_nick(nick: str) -> str:
    if not nick:
        return ""
    s = str(nick)
    # Unicode compatibility decomposition to fold variant glyphs
    s = unicodedata.normalize('NFKC', s)
    # Remove mIRC color codes and other common IRC formatting/control chars
    s = _MIRC_COLOR_RE.sub('', s)
    s = _IRC_FORMAT_RE.sub('', s)
    s = _IRC_CTRL_RE.sub('', s)
    return s.strip()


def normalize_key(nick: str) -> str:
    return normalize_nick(nick).lower()


def _split_for_irc(text: str, max_bytes: int) -> list[str]:
    """
    Split a message into chunks that each fit within max_bytes (UTF-8).
    Prefer splitting on ' | ' (leaderboard/bounty style), then spaces, then hard-cut.
    """
    text = (text or "").strip()
    if not text:
        return []

    chunks: list[str] = []
    remaining = text

    while _utf8_len(remaining) > max_bytes:
        candidate = remaining
        while candidate and _utf8_len(candidate) > max_bytes:
            candidate = candidate[:-1]
        if not candidate:
            break

        cut = candidate.rfind(" | ")
        if cut == -1:
            cut = candidate.rfind(" ")
        if cut == -1 or cut < 10:
            part = candidate
            remaining = remaining[len(part):].lstrip()
        else:
            part = candidate[:cut].rstrip()
            remaining = remaining[cut:].lstrip()
            if remaining.startswith("|"):
                remaining = remaining[1:].lstrip()

        if part and part.strip():
            chunks.append(part)

    if remaining and remaining.strip():
        chunks.append(remaining)

    return chunks


def safe_say(bot, text: str, dest: str | None = None, max_bytes: int = SAFE_SAY_MAX_BYTES):
    """Say a message, splitting into multiple IRC-safe lines if needed."""
    parts = _split_for_irc(text, max_bytes=max_bytes)
    if not parts:
        return
    for part in parts:
        if dest is None:
            bot.say(part)
        else:
            bot.say(part, dest)


def _load_data(bot):
    """Load from bot.db, then merge old JSON balances if found."""
    global _data
    with _data_lock:
        if _data is not None:
            return _data

        data = bot.db.get_plugin_value(PLUGIN_NAME, 'data')
        if not isinstance(data, dict):
            data = {}

        data.setdefault("users", {})
        data.setdefault("bounties", {})         # {nick_lower: amount}
        data.setdefault("last_bounty", {})      # {nick_lower: timestamp}

        users = data["users"]

        # Merge old JSON if exists (only for users/money)
        try:
            json_path = os.path.join(bot.config.core.homedir, OLD_DATA_FILENAME)
            if os.path.isfile(json_path):
                with open(json_path, 'r') as f:
                    old = json.load(f)

                old_users = None
                if isinstance(old, dict):
                    if "users" in old and isinstance(old["users"], dict):
                        old_users = old["users"]
                    else:
                        old_users = old

                if isinstance(old_users, dict):
                    for key, rec in old_users.items():
                        if not isinstance(rec, dict):
                            continue
                        if key in users and isinstance(users[key], dict):
                            if rec.get("money", 0) > users[key].get("money", 0):
                                users[key]["money"] = rec.get("money", 0)
                        else:
                            users[key] = rec

                # Normalize inventory keys (lowercase) for all users after merge
                for u in users.values():
                    inv = u.get("inv")
                    if isinstance(inv, dict):
                        norm = {}
                        for k, v in inv.items():
                            if not k:
                                continue
                            lk = str(k).lower()
                            norm[lk] = norm.get(lk, 0) + int(v)
                        u["inv"] = norm

                bot.db.set_plugin_value(PLUGIN_NAME, 'data', data)

                # Rename the legacy file so it is imported exactly once.
                # Re-merging on every restart would resurrect balances
                # after $mugreset (merge takes the max of JSON vs DB).
                try:
                    os.rename(json_path, json_path + '.imported')
                except OSError:
                    LOG.warning("mug_game: could not rename legacy JSON after import; "
                                "it will re-merge on next restart")
        except (IOError, json.JSONDecodeError, ValueError) as e:
            # JSON is malformed, or file doesn't exist, or old format mismatch
            try:
                bot.logger.warning("mug_game: JSON merge skipped (%s)", type(e).__name__)
            except Exception:
                pass

        _data = data
        return _data


def _update_highscore(data):
    """Scan all users and update the all-time high score record if beaten.
    Returns (changed, nick, amount) where changed is True if a new record was set."""
    old_nick = data.get("highscore_nick", "")
    old_amt = int(data.get("highscore_amount", 0))
    best_nick = old_nick
    best_amt = old_amt
    for key, u in data.get("users", {}).items():
        bal = int(u.get("money", 0))
        if bal > best_amt:
            best_amt = bal
            best_nick = u.get("nick", key)
    data["highscore_nick"] = best_nick
    data["highscore_amount"] = best_amt
    changed = (best_amt > old_amt) or (best_nick != old_nick and best_amt > 0)
    return changed, best_nick, best_amt


def _update_topic_highscore(bot, nick, amount):
    """Update the channel topic with the new high score record (with lock and retry)."""
    if not HIGHSCORE_TOPIC_ENABLED:
        return
    hs_text = f"{HIGHSCORE_TOPIC_MARKER} High Score: {nick} ({fmt_coins(amount)} coins) 👑"
    marker_re = re.compile(re.escape(HIGHSCORE_TOPIC_MARKER) + r'.*$')
    
    with _topic_lock:
        for channel in HIGHSCORE_TOPIC_CHANNELS:
            for attempt in range(1, 3):  # 2 attempts max
                try:
                    chan_obj = bot.channels.get(channel)
                    current_topic = getattr(chan_obj, 'topic', '') or '' if chan_obj else ''
                    if marker_re.search(current_topic):
                        new_topic = marker_re.sub(hs_text, current_topic).strip()
                    else:
                        new_topic = f"{current_topic} {hs_text}".strip() if current_topic else hs_text
                    bot.write(['TOPIC', channel], new_topic)
                    break  # success, exit retry loop
                except RuntimeError:
                    if attempt == 1:
                        LOG.warning('highscore_topic: bot not connected attempt %d in %s', attempt, channel)
                        time.sleep(0.5)  # brief delay before retry
                    else:
                        LOG.warning('highscore_topic: bot not connected, giving up for %s', channel)
                except Exception:
                    if attempt == 1:
                        LOG.exception('highscore_topic: failed attempt %d, retrying %s', attempt, channel)
                        time.sleep(0.5)
                    else:
                        LOG.exception('highscore_topic: failed attempt %d (final), skipping %s', attempt, channel)


def _save_data(bot):
    with _data_lock:
        if _data is None:
            return
        changed, hs_nick, hs_amt = _update_highscore(_data)
        try:
            bot.db.set_plugin_value(PLUGIN_NAME, 'data', _data)
        except (AttributeError, TypeError) as e:
            LOG.warning("mug_game: save_data failed (%s)", type(e).__name__)
            return
    # Auto-update channel topic if high score was beaten or marker is missing
    if hs_nick and hs_amt > 0:
        needs_update = changed
        if not needs_update and HIGHSCORE_TOPIC_ENABLED:
            # Also update if any channel topic is missing the marker
            marker_re = re.compile(re.escape(HIGHSCORE_TOPIC_MARKER))
            for channel in HIGHSCORE_TOPIC_CHANNELS:
                try:
                    chan_obj = bot.channels.get(channel)
                    current_topic = getattr(chan_obj, 'topic', '') or '' if chan_obj else ''
                    if not marker_re.search(current_topic):
                        needs_update = True
                        break
                except (AttributeError, KeyError):
                    pass
        if needs_update:
            try:
                _update_topic_highscore(bot, hs_nick, hs_amt)
            except Exception:
                LOG.exception('highscore_topic: post-save topic update failed')
    # After saving, schedule a voice check for all managed channels
    if VOICE_ENABLED:
        try:
            _voice_sweep_all(bot)
        except Exception:
            LOG.exception('coin_voice: post-save sweep failed')


def get_user_record(bot, nick):
    data = _load_data(bot)
    users = data["users"]
    key = normalize_key(nick)
    if key not in users:
        users[key] = {
            "nick": nick,
            "money": 0,
            "last_coins": 0.0,
            "last_mug": 0.0,
            "jail_until": 0.0,
            "last_bet": 0.0,
            "last_give": 0.0,
            "inv": {},
        }
    else:
        u = users[key]
        u["nick"] = nick
        u.setdefault("money", 0)
        u.setdefault("last_coins", 0.0)
        u.setdefault("last_mug", 0.0)
        u.setdefault("jail_until", 0.0)
        u.setdefault("last_bet", 0.0)
        u.setdefault("last_give", 0.0)
        u.setdefault("inv", {})
    
    # Assign u for both new and existing users so normalization works
    u = users[key]
    
    # Normalize inventory keys to lowercase to avoid casing mismatches
    # Also cap each item at 3 max (migration for legacy users with 100+ stacks)
    # Only do this if the inventory hasn't been marked as normalized yet
    try:
        inv = u.get("inv", {})
        if isinstance(inv, dict) and not u.get("_inv_normalized"):
            norm = {}
            for k, v in inv.items():
                if not k:
                    continue
                lk = str(k).lower()
                count = norm.get(lk, 0) + int(v)
                norm[lk] = min(count, 3)  # cap each item at 3
            u["inv"] = norm
            u["_inv_normalized"] = True  # mark as done to avoid re-normalizing
    except (KeyError, ValueError, AttributeError):
        # Best-effort normalization; don't fail record access
        u["_inv_normalized"] = True
    return users[key]


def fmt_time_remaining(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    sec = seconds % 60
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def fmt_coins(value) -> str:
    """Format coin amounts with commas for readability."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{n:,}"


def coins_cd_remaining(user, now):
    return max(0.0, (user.get("last_coins", 0.0) + COINS_COOLDOWN) - now)


def mug_cd_remaining(user, now):
    return max(0.0, (user.get("last_mug", 0.0) + MUG_BASE_COOLDOWN) - now)


def bet_cd_remaining(user, now):
    return max(0.0, (user.get("last_bet", 0.0) + BET_COOLDOWN) - now)


def give_cd_remaining(user, now):
    return max(0.0, (user.get("last_give", 0.0) + GIVE_COOLDOWN) - now)


def bounty_cd_remaining(data, nick, now):
    last = data["last_bounty"].get(normalize_key(nick), 0.0)
    return max(0.0, (last + BOUNTY_COOLDOWN) - now)


def get_item(key):
    return ITEMS.get(key.lower())


def get_item_count(user, item_key):
    return int(user.get("inv", {}).get(item_key, 0))


def get_item_bonus(user, attr):
    inv = user.get("inv", {})
    total = 0
    for k, count in inv.items():
        item = ITEMS.get(k)
        if not item:
            continue
        total += int(item.get(attr, 0)) * int(count)
    return total


# ---- Anti-cheat: global per-user command throttle ----
_last_cmd: dict[str, float] = {}
# ---- Anti-cheat: flood tracker & lockouts ----
_flood_history: dict[str, list[float]] = {}   # {nick_lower: [timestamps]}
_flood_lockout: dict[str, float] = {}          # {nick_lower: lockout_expiry_ts}
# ---- Anti-cheat: rolling $give tracker {nick_lower: [(timestamp, amount), ...]} ----
_give_history: dict[str, list[tuple[float, int]]] = {}
_give_history_loaded = False  # flag to avoid repeated DB loads

# Items that have active $use behavior; everything else is passive
USABLE_ITEMS = {'bail'}

# God mode: near-guaranteed luck, shared across plugins and persisted
# via _godmode.py (bot.memory + bot.db + JSON). Toggled with $godmode.


def _has_godmode(nick: str, bot=None) -> bool:
    """Return True if the nick currently has god mode enabled."""
    return _godmode.has_godmode(bot, nick)


def _check_identified(bot, trigger) -> bool:
    """Return True if user is identified with NickServ (or check is disabled).

    Tries multiple sources because trigger.account requires IRCv3 account-tag
    capability which many servers don't support:
      1. trigger.account  (IRCv3 account-tag on each PRIVMSG)
      2. bot.users[nick].account  (Sopel's WHO/WHOIS-based tracking)
      3. bot.channels[chan].privileges  (has channel privs → must be identified)

    Fallback behavior (when no account data exists):
      If the server doesn't support IRCv3 account-tag or WHOIS tracking, we 
      cannot reliably detect identification. Returning True allows commands to
      proceed rather than locking everyone out. This is a deliberate choice to
      maximize compat with older IRC networks that don't support identification
      queries. Change REQUIRE_IDENTIFIED=False to disable all ident checks.
    """
    if not REQUIRE_IDENTIFIED:
        return True

    nick = trigger.nick

    # 1) IRCv3 account-tag (best case)
    try:
        account = getattr(trigger, 'account', None)
        if account and account != '*':
            return True
    except AttributeError:
        pass

    # 2) Sopel's internal user tracking (populated via WHO on join)
    try:
        user = bot.users.get(nick)
        if user:
            acct = getattr(user, 'account', None)
            if acct and acct != '*':
                return True
            # If Sopel explicitly recorded None/'*', user is NOT identified
            if acct is not None:
                return False
    except (AttributeError, KeyError):
        pass

    # 3) Channel privilege heuristic: if the user has any channel privileges
    #    (op, voice, etc.) they are almost certainly identified on most networks.
    try:
        chan_name = str(trigger.sender)
        if chan_name.startswith('#'):
            chan = bot.channels.get(chan_name)
            if chan:
                privs = getattr(chan, 'privileges', None)
                if isinstance(privs, dict):
                    v = privs.get(nick) or privs.get(nick.lower())
                    if v is None:
                        for k in privs:
                            if k.lower() == nick.lower():
                                v = privs[k]
                                break
                    if v and (isinstance(v, int) and v > 0):
                        return True
    except (AttributeError, KeyError):
        pass

    # 4) If we have no account data at all (server doesn't support account tracking),
    #    allow the command rather than locking out everyone (fallback compat mode).
    try:
        user = bot.users.get(nick)
        account_attr = getattr(user, 'account', 'UNSET') if user else 'UNSET'
        if account_attr == 'UNSET':
            # Sopel has no account info → server doesn't track accounts
            return True
    except (AttributeError, KeyError):
        pass

    # account is explicitly '*' (not identified) and no privileges
    return False


def _check_global_cooldown(nick: str) -> bool:
    """Return True if the user is allowed to run a command (not flooding)."""
    now = time.time()
    key = normalize_key(nick)
    last = _last_cmd.get(key, 0.0)
    if now - last < GLOBAL_CMD_COOLDOWN:
        return False
    _last_cmd[key] = now
    return True


def _flood_check(bot, trigger) -> bool:
    """Track command usage and enforce flood lockout.
    Returns True if the user is BLOCKED (locked out or just triggered lockout).
    Admins are exempt.
    """
    if _is_admin(bot, trigger.nick):
        return False
    now = time.time()
    key = normalize_key(trigger.nick)

    # Already locked out?
    expiry = _flood_lockout.get(key, 0.0)
    if now < expiry:
        rem = expiry - now
        bot.reply(f"🚫 You're on a 30-minute casino timeout for spamming. {fmt_time_remaining(rem)} remaining.")
        return True

    # Track this command in the rolling window
    history = _flood_history.setdefault(key, [])
    history.append(now)
    # Prune entries outside the window
    cutoff = now - FLOOD_WINDOW
    _flood_history[key] = [t for t in history if t > cutoff]

    if len(_flood_history[key]) > FLOOD_MAX_CMDS:
        _flood_lockout[key] = now + FLOOD_LOCKOUT
        _flood_history[key] = []  # reset history
        bot.reply(
            f"🚫 Whoa there, speed demon! You've been locked out of ALL casino games "
            f"for 30 minutes. Take a walk. 🚶"
        )
        return True

    return False


def _flood_clear(nick: str):
    """Clear a user's flood lockout (admin use)."""
    key = normalize_key(nick)
    _flood_lockout.pop(key, None)
    _flood_history.pop(key, None)


def _load_give_history(bot):
    """Load give history from bot.db on first use (persists across restarts)."""
    global _give_history_loaded
    if _give_history_loaded:
        return
    try:
        saved = bot.db.get_plugin_value(PLUGIN_NAME, 'give_history')
        if isinstance(saved, dict):
            now = time.time()
            cutoff = now - GIVE_DAILY_WINDOW
            for k, entries in saved.items():
                if isinstance(entries, list):
                    try:
                        _give_history[k] = [(float(t), int(a)) for t, a in entries if float(t) > cutoff]
                    except (ValueError, TypeError):
                        # Skip malformed entries
                        continue
    except (AttributeError, TypeError) as e:
        LOG.warning("mug_game: give_history load skipped (%s)", type(e).__name__)
    finally:
        _give_history_loaded = True


def _save_give_history(bot):
    """Persist give history to bot.db."""
    try:
        bot.db.set_plugin_value(PLUGIN_NAME, 'give_history', _give_history)
    except (AttributeError, TypeError) as e:
        LOG.warning("mug_game: give_history save failed (%s)", type(e).__name__)


def _check_give_daily(bot, nick: str, amount: int, now: float) -> tuple[bool, int]:
    """Check if a $give would exceed the daily cap. Returns (allowed, remaining)."""
    _load_give_history(bot)
    key = normalize_key(nick)
    history = _give_history.get(key, [])
    # Prune entries outside the rolling window
    cutoff = now - GIVE_DAILY_WINDOW
    history = [(t, a) for t, a in history if t > cutoff]
    _give_history[key] = history
    total_given = sum(a for _, a in history)
    remaining = max(0, GIVE_DAILY_MAX - total_given)
    return (total_given + amount <= GIVE_DAILY_MAX), remaining


def _record_give(bot, nick: str, amount: int, now: float):
    """Record a successful $give for daily cap tracking (persisted to DB)."""
    _load_give_history(bot)
    key = normalize_key(nick)
    _give_history.setdefault(key, []).append((now, amount))
    _save_give_history(bot)


def _anticheat_gate(bot, trigger, cmd_name: str = "") -> bool:
    """Combined anti-cheat check. Returns True if command should STOP (blocked)."""
    if REQUIRE_IDENTIFIED and not _check_identified(bot, trigger):
        bot.reply("🔐 You must be identified with NickServ to play. Type: /msg NickServ IDENTIFY <password>")
        return True
    if _is_admin(bot, trigger.nick):
        return False  # admins bypass all rate limiting
    if not _check_global_cooldown(trigger.nick):
        return True  # silently drop rapid-fire commands
    if _flood_check(bot, trigger):
        return True  # flood lockout
    return False


def _load_channel_toggles(bot) -> dict[str, bool]:
    """Lazily load per-channel toggles from bot.db."""
    global _channel_toggles
    if _channel_toggles is None:
        try:
            val = bot.db.get_plugin_value(PLUGIN_NAME, 'channel_toggles')
            _channel_toggles = val if isinstance(val, dict) else {}
        except (AttributeError, TypeError):
            _channel_toggles = {}
    return _channel_toggles


def _plugin_enabled(bot, channel: str | None = None) -> bool:
    """Return True if the mug game is enabled for *channel*.

    If *channel* is None (PM context), only the config master switch is checked.
    """
    # Config master switch (hard kill)
    try:
        if not bot.config.mug_game.enabled:
            return False
    except Exception:
        pass  # no config section → default enabled

    # PM-only commands have no channel context → always allowed
    if channel is None:
        return True

    toggles = _load_channel_toggles(bot)
    # Missing key = disabled by default (must be explicitly enabled per channel)
    return toggles.get(channel.lower(), False)


def _set_channel_enabled(bot, channel: str, enabled: bool):
    """Set the per-channel enable/disable toggle (persisted across restarts)."""
    toggles = _load_channel_toggles(bot)
    toggles[channel.lower()] = enabled
    bot.db.set_plugin_value(PLUGIN_NAME, 'channel_toggles', toggles)


def _disabled_msg(bot, trigger):
    """Send a short 'plugin is disabled' notice."""
    chan = str(trigger.sender) if str(trigger.sender).startswith('#') else ''
    if chan:
        bot.reply(f"🔒 The mug game is disabled in {chan}. Join {MUG_HOME_CHANNEL} to play!")
    else:
        bot.reply(f"🔒 The mug game is currently disabled. Join {MUG_HOME_CHANNEL} to play!")


def _pm(bot, nick, text):
    bot.say(text, nick)


def _pm_only(trigger) -> bool:
    return not str(trigger.sender).startswith('#')


def _is_admin(bot, nick: str) -> bool:
    n = normalize_key(nick)

    # plugin allowlist
    if n in {normalize_key(x) for x in ADMIN_NICKS}:
        return True

    # Sopel core owner/admins (best-effort)
    core = getattr(getattr(bot, "config", None), "core", None)
    if core:
        owner = getattr(core, "owner", None)
        if isinstance(owner, str) and n == normalize_key(owner):
            return True
        admins = getattr(core, "admins", None)
        if isinstance(admins, (list, tuple, set)) and any(n == normalize_key(str(a)) for a in admins):
            return True

    return False


def get_title_for_money(money: int) -> str | None:
    """Return a title string (with emoji) for money >= 50, else None."""
    if money is None:
        return None
    title = None
    for threshold, name in TITLE_THRESHOLDS:
        if money >= threshold:
            title = name
        else:
            break
    return title


def tag(nick: str, money: int) -> str:
    """
    Narrative-friendly name:
      - <50: "End3r"
      - >=50: "End3r, the 🐀 Street Rat"
    """
    title = get_title_for_money(money)
    if title:
        return f"{nick}, the {title}"
    return nick


def _is_in_channel(bot, channel_name: str, nick: str) -> bool:
    channel = bot.channels.get(channel_name)
    if channel is None:
        return True  # fail open if unknown (keeps original behavior)
    return nick in channel.users


# ============================================================
# =================== BOT PLAYER LOGIC ======================
# ============================================================

def _seed_bot_wallet(bot):
    """Ensure the bot has a player record with seed money if new."""
    with locked_data(bot):
        rec = get_user_record(bot, bot.nick)
        if rec.get("money", 0) <= 0:
            rec["money"] = BOT_SEED_MONEY


def _bot_mug(bot, channel, target_nick, intro_messages):
    """Execute a bot-initiated mug against target_nick in channel.

    This is a simplified mug that doesn't require a trigger object.
    The bot is never jailed and ignores cooldowns.
    """
    bot_nick = bot.nick
    now = time.time()

    with locked_data(bot) as data:
        bot_rec = get_user_record(bot, bot_nick)
        users = data.get("users", {})
        victim_key = normalize_key(target_nick)
        if victim_key not in users:
            return  # target not a known player

        victim = get_user_record(bot, target_nick)
        vic_money = int(victim.get("money", 0))

        # Announce the attempt
        intro = _rand(intro_messages).format(bot=bot_nick, vic=target_nick)
        bot.say(intro, channel)

        roll = _sysrand.randint(1, 100)

        if roll <= BOT_SUCCESS_CHANCE:
            # Success
            if vic_money <= 0:
                bot.say(f"🤖 {bot_nick} tried to mug {target_nick}, but they're broke! Nothing to take. 🤷", channel)
                return

            pct = _sysrand.randint(BOT_STEAL_MIN, BOT_STEAL_MAX) / 100.0
            steal = max(1, int(vic_money * pct))
            # Apply whale cap
            if vic_money > RICH_VICTIM_THRESHOLD:
                cap = max(1, int(vic_money * (RICH_VICTIM_MAX_STEAL_PCT / 100.0)))
                steal = min(steal, cap)

            victim["money"] = max(0, vic_money - steal)
            bot_rec["money"] = int(bot_rec.get("money", 0)) + steal

            msg = _rand(BOT_MUG_SUCCESS_MESSAGES).format(bot=bot_nick, vic=target_nick, steal=fmt_coins(steal))
            bot.say(f"{msg} | {bot_nick}: {fmt_coins(bot_rec['money'])} | {target_nick}: {fmt_coins(victim['money'])}", channel)
        else:
            # Fail — bot loses coins to the target
            bot_money = int(bot_rec.get("money", 0))
            if bot_money <= 0:
                bot.say(f"🤖 {bot_nick} fumbles the heist but has no coins to drop. Awkward. 🫠", channel)
                return

            pct = _sysrand.randint(BOT_FAIL_LOSS_MIN, BOT_FAIL_LOSS_MAX) / 100.0
            loss = max(1, int(bot_money * pct))
            fail_cap = max(MAX_FAIL_LOSS_FLOOR, int(bot_money * (MAX_FAIL_LOSS_PCT / 100.0)))
            loss = min(loss, bot_money, fail_cap)

            bot_rec["money"] = max(0, bot_money - loss)
            victim["money"] = int(victim.get("money", 0)) + loss

            msg = _rand(BOT_MUG_FAIL_MESSAGES).format(bot=bot_nick, vic=target_nick, loss=fmt_coins(loss))
            bot.say(f"{msg} | {bot_nick}: {fmt_coins(bot_rec['money'])} | {target_nick}: {fmt_coins(victim['money'])}", channel)


def _bot_retaliate(bot, channel, attacker_nick):
    """Delayed retaliation: bot mugs back the player who mugged it."""
    if not BOT_RETALIATE or not BOT_PLAYER_ENABLED:
        return
    if _sysrand.randint(1, 100) > BOT_RETALIATE_CHANCE:
        return  # didn't trigger this time

    delay = _sysrand.randint(BOT_RETALIATE_DELAY[0], BOT_RETALIATE_DELAY[1])

    def _do_retaliate():
        time.sleep(delay)
        if not _plugin_enabled(bot, channel):
            return
        _bot_mug(bot, channel, attacker_nick, BOT_RETALIATE_MESSAGES)

    t = threading.Thread(target=_do_retaliate, daemon=True)
    t.start()


def _start_bot_proactive_thread(bot):
    """Launch the background thread that periodically mugs top players."""
    global _bot_proactive_thread
    _bot_proactive_stop.clear()

    if not BOT_PROACTIVE or not BOT_PLAYER_ENABLED:
        return

    def _proactive_loop():
        # Initial delay so the bot has time to join channels
        _bot_proactive_stop.wait(60)

        while not _bot_proactive_stop.is_set():
            interval = _sysrand.randint(BOT_PROACTIVE_INTERVAL[0], BOT_PROACTIVE_INTERVAL[1]) * 60
            if _bot_proactive_stop.wait(interval):
                break  # stop signal received

            try:
                # Find the game channel (must be enabled)
                channel = MUG_HOME_CHANNEL
                if not _plugin_enabled(bot, channel):
                    continue

                # Pick a target from top N (exclude the bot itself)
                top = _get_leaderboard(bot, BOT_PROACTIVE_TARGETS)
                candidates = [u for u in top if normalize_key(u.get("nick", "")) != normalize_key(bot.nick)]
                if not candidates:
                    continue

                target = _sysrand.choice(candidates)
                target_nick = target.get("nick", "")
                if not target_nick:
                    continue

                _bot_mug(bot, channel, target_nick, BOT_PROACTIVE_MESSAGES)
            except Exception:
                LOG.exception("Bot proactive mug error")

    _bot_proactive_thread = threading.Thread(target=_proactive_loop, daemon=True)
    _bot_proactive_thread.start()


# ============================================================
# ======================= CORE COMMANDS ======================
# ============================================================

@module.commands('coins')
def coins(bot, trigger):
    """$coins - get coins (scaled by wealth)"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'coins'):
        return

    nick = trigger.nick
    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, nick)

        rem = coins_cd_remaining(user, now)
        if rem > 0 and not _is_admin(bot, trigger.nick):
            bot.reply(_rand(COINS_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        current = max(0, int(user.get("money", 0)))
        base_gain = _sysrand.randint(COINS_MIN_GAIN, COINS_MAX_GAIN)

        if current > 0:
            pct = _sysrand.randint(COINS_SCALE_MIN_PCT, COINS_SCALE_MAX_PCT) / 100.0
            scaling_gain = min(int(current * pct), COINS_SCALE_MAX_EXTRA)
        else:
            scaling_gain = 0

        bonus = min(50, get_item_bonus(user, "coins_bonus_flat"))  # cap at +50 coins
        gain = max(1, base_gain + scaling_gain + bonus)

        user["money"] = current + gain
        user["last_coins"] = now

        # Keep the response concise: only report total gain and new balance
        # Avoid titles and mentioning the command; just state what happened.
        bot.say(
            f"💰 {nick} found {fmt_coins(gain)} coins! New balance: {fmt_coins(user['money'])} coins. ✨"
        )


@module.commands('balance', 'bal')
def balance(bot, trigger):
    """$balance [nick] - check coins"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if not _check_global_cooldown(trigger.nick):
        return  # silently drop spam

    arg = (trigger.group(2) or "").strip()
    target = arg.split()[0] if arg else trigger.nick

    # Never create records for other nicks: that would pollute the DB and
    # turn non-players into valid $mug/$bounty targets. Only the caller's
    # own record may be created (under the lock).
    if normalize_key(target) == normalize_key(trigger.nick):
        with _data_lock:
            u = get_user_record(bot, target)
    else:
        data = _load_data(bot)
        u = data.get("users", {}).get(normalize_key(target))
        if not isinstance(u, dict):
            bot.reply("🗃️ That user isn't a known mug player yet.")
            return
    bot.say(f"🧾 {tag(u.get('nick', target), u.get('money', 0))} has {fmt_coins(u.get('money', 0))} coins. 🪙")


@module.commands('give')
def give(bot, trigger):
    """$give <nick> <amount>"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'give'):
        return

    args = (trigger.group(2) or "").split()
    if len(args) < 2:
        bot.reply("📦 Usage: $give <nick> <amount>")
        return

    target_nick = args[0]
    try:
        amt = int(args[1].replace(",", ""))
    except ValueError:
        bot.reply("🔢 Amount must be a whole number.")
        return

    if amt < 1:
        bot.reply("🪙 Amount must be at least 1.")
        return

    if normalize_key(target_nick) == normalize_key(trigger.nick):
        bot.reply("🙃 You can’t give yourself coins. That’s called *having coins*.")
        return

    if not _is_in_channel(bot, trigger.sender, target_nick):
        bot.reply("🕵️ That user doesn’t seem to be in this channel.")
        return

    with locked_data(bot):
        giver = get_user_record(bot, trigger.nick)

        # $give cooldown
        now = time.time()
        rem = give_cd_remaining(giver, now)
        if rem > 0 and not _is_admin(bot, trigger.nick):
            bot.reply(f"⏳ You can give again in {fmt_time_remaining(rem)}.")
            return

        if giver.get("money", 0) < amt:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        # Daily transfer cap (rolling 24h window, admins exempt)
        allowed, remaining = _check_give_daily(bot, trigger.nick, amt, now)
        if not allowed and not _is_admin(bot, trigger.nick):
            bot.reply(
                f"🧢 Daily $give cap is {fmt_coins(GIVE_DAILY_MAX)} coins per 24h. "
                f"You can still give {fmt_coins(remaining)} today."
            )
            return

        recv = get_user_record(bot, target_nick)
        giver["money"] -= amt
        recv["money"] += amt
        giver["last_give"] = now
        _record_give(bot, trigger.nick, amt, now)

        bot.say(
            f"🤝 {tag(trigger.nick, giver['money'])} gave {fmt_coins(amt)} coins to {tag(recv['nick'], recv['money'])}! 🎉"
        )


# ============================================================
# ======================= JAIL STATUS ========================
# ============================================================

@module.commands('jail')
def jail(bot, trigger):
    """$jail - check your jail status"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if not _check_global_cooldown(trigger.nick):
        return  # silently drop spam

    with _data_lock:
        u = get_user_record(bot, trigger.nick)
    now = time.time()
    until = u.get("jail_until", 0.0)
    if until > now:
        rem = fmt_time_remaining(until - now)
        bot.say(f"🚔 {tag(trigger.nick, u.get('money', 0))} is doing time! Free in {rem}. 🔒")
    else:
        bot.say(f"✅ {tag(trigger.nick, u.get('money', 0))} is a free criminal once again. 😈")


# ============================================================
# ========================== BOUNTIES =========================
# ============================================================

@module.commands('bounty')
def bounty(bot, trigger):
    """$bounty <nick> <amount> - place a bounty (costs you coins)"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'bounty'):
        return

    now = time.time()

    with locked_data(bot) as data:
        rem = bounty_cd_remaining(data, trigger.nick, now)
        if rem > 0 and not _is_admin(bot, trigger.nick):
            bot.reply(f"⏳ Slow down, bounty goblin. Try again in {fmt_time_remaining(rem)}.")
            return

        args = (trigger.group(2) or "").split()
        if len(args) < 2:
            bot.reply("🎯 Usage: $bounty <nick> <amount>")
            return

        target_nick = args[0]
        if normalize_key(target_nick) == normalize_key(trigger.nick):
            bot.reply("🫠 You can’t bounty yourself. That’s just therapy with extra steps.")
            return

        if not _is_in_channel(bot, trigger.sender, target_nick):
            bot.reply("🕵️ That user doesn’t seem to be in this channel.")
            return

        # Require target to be a known mug player (prevents dead-end bounties)
        if normalize_key(target_nick) not in data.get('users', {}):
            bot.reply("🗃️ That user isn't a known mug player yet. No point bounty-ing a ghost.")
            return

        try:
            amt = int(args[1].replace(",", ""))
        except ValueError:
            bot.reply("🔢 Amount must be a whole number.")
            return

        if amt < BOUNTY_MIN:
            bot.reply(f"🪙 Minimum bounty is {fmt_coins(BOUNTY_MIN)}. Stop being cheap.")
            return

        if amt > BOUNTY_MAX:
            bot.reply(f"🧯 Max bounty per placement is {fmt_coins(BOUNTY_MAX)}. Relax, Batman.")
            return

        placer = get_user_record(bot, trigger.nick)
        if placer.get("money", 0) < amt:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        placer["money"] -= amt
        key = normalize_key(target_nick)
        data["bounties"][key] = int(data["bounties"].get(key, 0)) + amt
        data["last_bounty"][normalize_key(trigger.nick)] = now

        bot.say(
            _rand(BOUNTY_CONFIRM_MESSAGES).format(vic=target_nick, amt=fmt_coins(amt))
            + f" ({tag(trigger.nick, placer['money'])})"
        )


@module.commands('bounties')
def bounties(bot, trigger):
    """$bounties - one-line list of top bounties"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return

    with _data_lock:
        data = _load_data(bot)
        items = sorted(data.get("bounties", {}).items(), key=lambda kv: -int(kv[1]))[:10]
    if not items:
        bot.say("🎯 No active bounties. Everyone is (unfortunately) safe. 😇")
        return
    parts = [f"🎯 {nick}({fmt_coins(amt)})" for nick, amt in items]
    safe_say(bot, "🔥 Top bounties: " + " | ".join(parts))


# ============================================================
# ======================= MUGGING / ROB ======================
# ============================================================

def _check_mug_allowed(attacker):
    now = time.time()

    jail_until = attacker.get("jail_until", 0.0)
    if jail_until > now:
        return False, f"🚔 You’re still in jail for {fmt_time_remaining(jail_until - now)}. No crimes for now!"

    rem = mug_cd_remaining(attacker, now)
    if rem > 0:
        return False, _rand(MUG_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem))

    if attacker.get("money", 0) < MUG_FEE_MIN:
        return False, _rand(BROKE_MESSAGES)

    return True, None


def _banana_slip_chance(victim):
    count = get_item_count(victim, "banana")
    if count <= 0:
        return 0
    chance = count * BANANA_SLIP_PER_ITEM_PCT
    return min(chance, BANANA_SLIP_MAX_PCT)


@module.commands('mug', 'rob')
def mug(bot, trigger):
    """$mug/$rob <nick> (ONE LINE OUTPUT ONLY)"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel, not PM.")
        return
    if _anticheat_gate(bot, trigger, 'mug'):
        return

    arg = (trigger.group(2) or "").strip()
    if not arg:
        bot.reply("🗡️ Usage: $mug <nick>")
        return

    target_nick = arg.split()[0]
    attacker_nick = trigger.nick

    # Self-mug: random smartass roast; bot.say avoids "nick:" reply prefix
    if normalize_key(target_nick) == normalize_key(attacker_nick):
        bot.say(_rand(SELF_MUG_MESSAGES))
        return

    # Track if the target is the bot (for retaliation after the lock releases)
    _target_is_bot = (normalize_key(target_nick) == normalize_key(bot.nick))
    _mug_happened = False

    now = time.time()
    prefix = _rand(MUG_VIBE_PREFIXES)

    with locked_data(bot) as data:
        attacker = get_user_record(bot, attacker_nick)

        # Allow mugging from anywhere, but only if the target already exists in the mug DB.
        # This prevents creating new victim records just because someone typed a nick.
        users = data.get("users", {})
        victim_key = normalize_key(target_nick)
        if victim_key not in users:
            bot.reply("🗃️ That user isn’t a known mug player yet.")
            return

        victim = get_user_record(bot, target_nick)

        # Snapshot display names ONCE so titles don't change mid-message.
        att_display = tag(attacker_nick, int(attacker.get("money", 0)))
        vic_display = tag(target_nick, int(victim.get("money", 0)))

        allowed, msg = _check_mug_allowed(attacker)
        if not allowed and not _is_admin(bot, trigger.nick):
            bot.reply(msg)
            return

        # Anti-exploit: attacker must have >= MUG_MIN_BALANCE_PCT% of victim's money
        # (skip check for tiny wallets, admins, and godmode users)
        vic_money_check = int(victim.get("money", 0))
        att_money_check = int(attacker.get("money", 0))
        if (vic_money_check > MUG_MIN_BALANCE_FLOOR
                and not _is_admin(bot, trigger.nick)
                and not _has_godmode(attacker_nick)):
            required = max(1, int(vic_money_check * (MUG_MIN_BALANCE_PCT / 100.0)))
            if att_money_check < required:
                bot.reply(f"💸 You need at least {fmt_coins(required)} ({MUG_MIN_BALANCE_PCT}% of their wallet) to attempt this mug. You have {fmt_coins(att_money_check)}.")
                return

        def do_crit_fail(reason_text=None):
            att_money = int(attacker.get("money", 0))
            pct = _sysrand.randint(CRIT_LOSS_MIN, CRIT_LOSS_MAX) / 100.0
            loss = max(1, int(att_money * pct)) if att_money > 0 else 0
            # Scale cap to attacker wealth so rich players feel the sting
            crit_cap = max(MAX_CRIT_LOSS_FLOOR, int(att_money * (MAX_CRIT_LOSS_PCT / 100.0)))
            loss = min(loss, att_money, crit_cap)

            attacker["money"] = att_money - loss
            victim["money"] = int(victim.get("money", 0)) + loss
            attacker["jail_until"] = now + JAIL_TIME
            attacker["last_mug"] = now

            # Check if attacker has bail in inventory - auto-release if they do
            inv = attacker.get("inv", {})
            bail_count = int(inv.get("bail", 0))
            if bail_count > 0:
                inv["bail"] = bail_count - 1
                attacker["jail_until"] = 0.0
                # NOTE: mug cooldown intentionally NOT cleared — bail frees from
                # jail but does not grant an instant re-mug (anti-exploit).
                extra = f" {reason_text}" if reason_text else ""
                msg2 = _rand(MUG_CRIT_FAIL_MESSAGES).format(
                    att=att_display,
                    loss=fmt_coins(loss),
                    jail=fmt_time_remaining(JAIL_TIME),
                )
                bot.say(
                    f"{prefix} {msg2}{extra} 🪓 BUT {attacker_nick} had a Bail Bondsman and got out of jail! (Mug cooldown still active.) | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim['money'])} 💼"
                )
                return

            extra = f" {reason_text}" if reason_text else ""
            msg2 = _rand(MUG_CRIT_FAIL_MESSAGES).format(
                att=att_display,
                loss=fmt_coins(loss),
                jail=fmt_time_remaining(JAIL_TIME),
            )
            bot.say(
                f"{prefix} {msg2}{extra} | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim['money'])} 🪙"
            )
            return

        # Ultra-rare oops-jail (happens even before the roll) — godmode users are immune
        if not _has_godmode(attacker_nick) and _sysrand.randint(1, 100) <= MUG_OOPS_JAIL_CHANCE_PCT:
            do_crit_fail(reason_text="🤡 ULTRA-RARE OOPS: you looked suspicious and got arrested instantly.")
            _mug_happened = True
            # fall through to retaliation check below

        # Charge the mug fee now (scaled to attacker wealth; don't charge if instant-oops jailed)
        if not _mug_happened:
            _att_bal = int(attacker.get("money", 0))
            _fee = max(MUG_FEE_MIN, int(_att_bal * (MUG_FEE_PCT / 100.0)))
            attacker["money"] = max(0, _att_bal - _fee)

        # Mug chance mods
        bonus_success = get_item_bonus(attacker, "mug_success_bonus")
        # Admins with god mode get near-guaranteed success
        if _has_godmode(attacker_nick):
            success_chance = 99
        else:
            success_chance = min(95, SUCCESS_CHANCE + bonus_success)

        roll = _sysrand.randint(1, 100)
        success_max = success_chance
        normal_fail_max = success_max + NORMAL_FAIL_CHANCE

        if _mug_happened:
            pass  # oops-jail already resolved above
        # SUCCESS PATH
        elif roll <= success_max:
            # Banana trap check (victim)
            slip = _banana_slip_chance(victim)
            if not _has_godmode(attacker_nick) and slip > 0 and _sysrand.randint(1, 100) <= slip and BANANA_SLIP_TRIGGERS_CRIT_FAIL:
                do_crit_fail(reason_text=f"🍌 Banana trap! {target_nick} had {slip}% slip chance.")
                _mug_happened = True

            # Cloak dodge check (victim) - cap at 50% max to prevent 100% immunity
            immune = min(50, get_item_bonus(victim, "mug_immune_chance"))
            if not _has_godmode(attacker_nick) and not _mug_happened and immune > 0 and _sysrand.randint(1, 100) <= immune:
                attacker["last_mug"] = now
                bot.say(
                    f"{prefix} 🕶️ {att_display} tries to mug {vic_display}, but {target_nick} vanishes into the shadows. No coins stolen! 👻 | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim.get('money', 0))}"
                )
                _mug_happened = True

            if not _mug_happened:
                vic_money = int(victim.get("money", 0))
            if not _mug_happened and vic_money <= 0:
                attacker["last_mug"] = now
                bot.say(
                    f"{prefix} 🪫 {att_display} tried to mug {vic_display}, but {target_nick} is broke as a joke. Nothing to steal! 🤷 | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim.get('money', 0))}"
                )
                _mug_happened = True

            if not _mug_happened:
                pct_bonus = min(30, get_item_bonus(attacker, "mug_steal_bonus_pct"))  # cap bonus at +30%
                if _has_godmode(attacker_nick):
                    pct_base = SUCCESS_STEAL_MAX
                    mega = True
                else:
                    pct_base = _sysrand.randint(SUCCESS_STEAL_MIN, SUCCESS_STEAL_MAX)
                    mega = (_sysrand.randint(1, 100) <= MUG_MEGA_STEAL_CHANCE_PCT)
                if mega:
                    steal_pct = max(0, pct_base + pct_bonus + MUG_MEGA_STEAL_BONUS_PCT)
                else:
                    steal_pct = max(0, pct_base + pct_bonus)

                # Whale protection: cap max steal
                rich_cap = None
                if vic_money > RICH_VICTIM_THRESHOLD:
                    rich_cap = max(1, int(vic_money * (RICH_VICTIM_MAX_STEAL_PCT / 100.0)))

                steal_raw = max(1, int(vic_money * (steal_pct / 100.0)))
                if rich_cap is not None:
                    steal_raw = min(steal_raw, rich_cap)

                # Vest reduces stolen amount - cap at 60% max reduction
                reduction = min(60, get_item_bonus(victim, "steal_reduction_pct"))
                if reduction > 0:
                    steal = max(1, int(steal_raw * max(0, (100 - reduction)) / 100.0))
                else:
                    steal = steal_raw

                victim["money"] = max(0, vic_money - steal)
                attacker["money"] = int(attacker.get("money", 0)) + steal

                # Bounty claim (explicit mutation)
                bounty_key = normalize_key(target_nick)
                bounty_pool = int(data["bounties"].get(bounty_key, 0))
                bounty_claim = 0
                if bounty_pool > 0:
                    bounty_claim = bounty_pool
                    data["bounties"].pop(bounty_key, None)
                    attacker["money"] += bounty_claim

                attacker["last_mug"] = now

                if mega:
                    msg = _rand(MUG_MEGA_SUCCESS_MESSAGES).format(att=att_display, vic=vic_display, steal=fmt_coins(steal))
                else:
                    msg = _rand(MUG_SUCCESS_MESSAGES).format(att=att_display, vic=vic_display, steal=fmt_coins(steal))

                whale_note = " 🐋(whale-protected)" if rich_cap is not None else ""
                bounty_note = ""
                if bounty_claim:
                    bounty_note = " " + _rand(BOUNTY_CLAIM_MESSAGES).format(
                        att=attacker_nick, vic=target_nick, bounty=fmt_coins(bounty_claim)
                    )

                bot.say(
                    f"{prefix} {msg}{whale_note}{bounty_note} | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim['money'])} 💼"
                )
                _mug_happened = True

        # NORMAL FAIL PATH
        elif roll <= normal_fail_max:
            att_money = int(attacker.get("money", 0))
            pct = _sysrand.randint(FAIL_LOSS_MIN, FAIL_LOSS_MAX) / 100.0
            loss = max(1, int(att_money * pct))
            # Scale cap to attacker wealth so rich players feel the sting
            fail_cap = max(MAX_FAIL_LOSS_FLOOR, int(att_money * (MAX_FAIL_LOSS_PCT / 100.0)))
            loss = min(loss, att_money, fail_cap)

            attacker["money"] = max(0, att_money - loss)
            victim["money"] = int(victim.get("money", 0)) + loss
            # store last_mug in the future to extend cooldown (base + extra)
            attacker["last_mug"] = now + MUG_EXTRA_FAIL_CD

            msg = _rand(MUG_FAIL_MESSAGES).format(att=att_display, loss=fmt_coins(loss))
            bot.say(
                f"{prefix} {msg} | {attacker_nick}: {fmt_coins(attacker['money'])} | {target_nick}: {fmt_coins(victim['money'])} 🤡"
            )
            _mug_happened = True

        # CRITICAL FAIL PATH
        else:
            do_crit_fail()
            _mug_happened = True

    # Bot retaliation (fired outside the lock to avoid deadlock)
    if _target_is_bot and BOT_PLAYER_ENABLED:
        _bot_retaliate(bot, str(trigger.sender), attacker_nick)


# ============================================================
# ========================== BETTING =========================
# ============================================================

# Keep betting as a percentage chance, but use a larger roll range to reduce
# the "small sample" feel and allow finer granularity if needed later.
BET_ROLL_MAX = 10000

@module.commands('bet')
def bet(bot, trigger):
    """$bet <amount>"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'bet'):
        return

    arg = (trigger.group(2) or "").strip()
    if not arg:
        bot.reply("🎲 Usage: $bet <amount>")
        return

    try:
        amount = int(arg.split()[0].replace(",", ""))
    except ValueError:
        bot.reply("🔢 Bet amount must be a whole number.")
        return

    if amount < 1 or amount < GAMBLE_MIN_BET:
        bot.reply(f"💁 Minimum bet is {fmt_coins(GAMBLE_MIN_BET)}.")
        return

    if amount > GAMBLE_MAX_BET:
        bot.reply(f"🚫 Max bet is {fmt_coins(GAMBLE_MAX_BET)}. Easy there, whale.")
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else bet_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(BET_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < amount:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        user["money"] = int(user.get("money", 0)) - amount

        bonus = get_item_bonus(user, "bet_win_bonus")
        # God mode gets near-guaranteed wins
        if _has_godmode(trigger.nick):
            win_chance = 99
        else:
            win_chance = min(95, BET_BASE_WIN_CHANCE + bonus)

        # Roll 1..10000; win_chance is still a percent.
        roll = _sysrand.randint(1, BET_ROLL_MAX)
        win = roll <= (win_chance * (BET_ROLL_MAX // 100))
        user["last_bet"] = now

        if win:
            payout = amount * 2
            user["money"] += payout
            bot.say(
                f"🎲 {tag(trigger.nick, user['money'])} bets {fmt_coins(amount)} and WINS! Payout: {fmt_coins(payout)}. New balance: {fmt_coins(user['money'])} 🤑✨"
            )
        else:
            bot.say(
                f"💀 {tag(trigger.nick, user['money'])} bets {fmt_coins(amount)} and loses it all! New balance: {fmt_coins(user['money'])} 😭🎰"
            )


# ============================================================
# ===================== DICE CASINO 🎲 =======================
# ============================================================
# $roll <amount> [type]
#   Types: high (default), lucky7, snake, field, hardway, yolo
#
# No separate cooldown — shares BET_COOLDOWN with $bet so players
# can choose one or the other while waiting.

DICE_GAMES = {
    'high':    {'desc': 'Roll 2d6 — 7+ wins',        'payout': '2x'},
    'lucky7':  {'desc': 'Roll exactly 7',              'payout': '4x'},
    'snake':   {'desc': 'Roll snake eyes (1+1)',       'payout': '30x'},
    'field':   {'desc': 'Roll 2,3,4,9,10,11,12 wins', 'payout': '2x (3x on 2 or 12)'},
    'hardway': {'desc': 'Roll doubles (not snake)',    'payout': '8x'},
    'yolo':    {'desc': 'Roll 2 or 12 — huge payout',  'payout': '15x'},
}

DICE_COOLDOWN_MESSAGES = [
    "🎲 The dice are still bouncing. Calm down — {time}.",
    "🎰 The table is being swept. Wait {time}.",
    "🎲 The pit boss says slow your roll — {time}.",
    "🎲 Dice are locked in the cage. {time} until they're free.",
    "🎲 Your wrist is cramping from the last throw. Rest {time}.",
    "🕹️ The dice gods demand patience. {time}.",
    "🎲 Security is reviewing the footage… try again in {time}.",
    "🎲 A casino cat stole the dice. Retrieval ETA: {time}.",
]


def _roll_2d6():
    """Roll two six-sided dice. Returns (die1, die2, total)."""
    d1 = _sysrand.randint(1, 6)
    d2 = _sysrand.randint(1, 6)
    return d1, d2, d1 + d2


def _dice_face(n):
    """Return a die-face emoji for 1–6."""
    faces = {1: '⚀', 2: '⚁', 3: '⚂', 4: '⚃', 5: '⚄', 6: '⚅'}
    return faces.get(n, '🎲')


def _eval_dice_bet(game_type, d1, d2, total, amount):
    """Evaluate a dice bet. Returns (won: bool, payout: int, flavor: str)."""
    if game_type == 'high':
        if total >= 7:
            return True, amount * 2, f"Total {total} — that's a winner! 🎉"
        return False, 0, f"Total {total} — under 7, house takes it. 💀"

    if game_type == 'lucky7':
        if total == 7:
            return True, amount * 4, "Lucky 7! 🍀🎰"
        return False, 0, f"Total {total} — needed a 7. 😤"

    if game_type == 'snake':
        if d1 == 1 and d2 == 1:
            return True, amount * 30, "🐍 SNAKE EYES! Legendary roll! 🐍💰"
        return False, 0, f"Rolled {d1}+{d2} — no snakes here. 🐍❌"

    if game_type == 'field':
        field_nums = {2, 3, 4, 9, 10, 11, 12}
        if total in field_nums:
            mult = 3 if total in (2, 12) else 2
            return True, amount * mult, f"Field {total}! {'Triple pay! 🔥' if mult == 3 else 'Double! 💵'}"
        return False, 0, f"Total {total} — not in the field. 😬"

    if game_type == 'hardway':
        if d1 == d2 and not (d1 == 1 and d2 == 1):
            return True, amount * 8, f"Hard {total}! Doubles pay fat! 🎯💰"
        return False, 0, f"Rolled {d1}+{d2} — no doubles. 🫠"

    if game_type == 'yolo':
        if total in (2, 12):
            return True, amount * 15, f"{'Boxcars' if total == 12 else 'Snake eyes'}! YOLO PAYS! 🚀💎🔥"
        return False, 0, f"Total {total} — YOLO didn't pay this time. 🪦"

    # Fallback to high
    if total >= 7:
        return True, amount * 2, f"Total {total} — winner! 🎉"
    return False, 0, f"Total {total} — nope. 💀"


@module.commands('roll')
def roll_dice(bot, trigger):
    """$roll <amount> [type] — Roll the dice! Types: high, lucky7, snake, field, hardway, yolo"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'roll'):
        return

    args = (trigger.group(2) or "").strip().split()
    if not args:
        # Show help
        lines = ["🎲 $roll <amount> [type] — Casino dice! Types:"]
        for k, v in DICE_GAMES.items():
            lines.append(f"  {k}: {v['desc']} → {v['payout']}")
        bot.say(" | ".join(lines))
        return

    try:
        amount = int(args[0].replace(",", ""))
    except ValueError:
        bot.reply("🔢 Bet amount must be a whole number.")
        return

    if amount < 1 or amount < GAMBLE_MIN_BET:
        bot.reply(f"💁 Minimum bet is {fmt_coins(GAMBLE_MIN_BET)}.")
        return

    if amount > GAMBLE_MAX_BET:
        bot.reply(f"🚫 Max bet is {fmt_coins(GAMBLE_MAX_BET)}. Easy there, high roller.")
        return

    game_type = (args[1].lower() if len(args) > 1 else 'high')
    if game_type not in DICE_GAMES:
        bot.reply(f"❓ Unknown type. Pick one: {', '.join(DICE_GAMES.keys())}")
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else bet_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(DICE_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < amount:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        user["money"] = int(user.get("money", 0)) - amount
        user["last_bet"] = now  # shares cooldown with $bet

        # God mode: rig the dice
        if _has_godmode(trigger.nick):
            if game_type == 'snake':
                d1, d2, total = 1, 1, 2
            elif game_type == 'lucky7':
                d1, d2, total = 3, 4, 7
            elif game_type == 'hardway':
                d1, d2, total = 3, 3, 6
            elif game_type == 'yolo':
                d1, d2, total = 6, 6, 12
            elif game_type == 'field':
                d1, d2, total = 6, 6, 12
            else:
                d1, d2, total = _sysrand.choice([(4, 3, 7), (5, 3, 8), (6, 2, 8), (5, 4, 9), (6, 5, 11)])
        else:
            d1, d2, total = _roll_2d6()

        won, payout, flavor = _eval_dice_bet(game_type, d1, d2, total, amount)

        face1 = _dice_face(d1)
        face2 = _dice_face(d2)

        if won:
            user["money"] += payout
            bot.say(
                f"🎲 {face1}{face2} {tag(trigger.nick, user['money'])} rolls {d1}+{d2}={total} on {game_type}! "
                f"{flavor} Payout: {fmt_coins(payout)}. Balance: {fmt_coins(user['money'])} 🪙"
            )
        else:
            bot.say(
                f"🎲 {face1}{face2} {tag(trigger.nick, user['money'])} rolls {d1}+{d2}={total} on {game_type}. "
                f"{flavor} Lost {fmt_coins(amount)}. Balance: {fmt_coins(user['money'])} 🪙"
            )


# ============================================================
# ================== PENNY SLOTS 🎰 ==========================
# ============================================================
# $penny — Drop a coin in the penny slot machine!
# Costs 1 coin per pull. 10 prize tiers + jackpot.
# Own cooldown (PENNY_COOLDOWN) separate from $bet/$roll.

PENNY_COOLDOWN = 15  # seconds between pulls
PENNY_COST = 1       # 1 coin per pull

# (weight, payout, reels, message)
# Higher weight = more common. Weights summed for probability.
PENNY_PRIZES = [
    (35, 0,    '💀💀💀', "Nothing. The machine laughs at you."),
    (20, 0,    '🍋🍒💀', "Two fruits and a skull. Almost."),
    (12, 2,    '🍒🍒🍋', "Two cherries! {coins} back. 🍒"),
    (8,  5,    '🍒🍒🍒', "Triple cherries! 🍒🍒🍒 +{coins}!"),
    (6,  15,   '🍋🍋🍋', "Lemons! 🍋🍋🍋 Sour but sweet — +{coins}!"),
    (5,  50,   '🔔🔔🔔', "DING DING DING! 🔔🔔🔔 +{coins}!"),
    (4,  150,  '💎💎🔔', "Diamonds and a bell! 💎✨ +{coins}!"),
    (3,  500,  '💎💎💎', "TRIPLE DIAMONDS! 💎💎💎 +{coins}! 🤑"),
    (2,  1500, '🔥🔥🔥', "🔥🔥🔥 FIRE SPIN!!! +{coins}! The machine is SMOKING! 🚒"),
    (1,  5000, '👑👑👑', "👑👑👑 JACKPOT!!! 🎰💰🎆🎇🪩🥳 +{coins}!!! THE CROWD GOES WILD!!!"),
]

PENNY_COOLDOWN_MESSAGES = [
    "🎰 The machine needs a second to cool down… {time}.",
    "🎰 Slow down, speed demon — {time}.",
    "🎰 The lever is stuck. Try again in {time}.",
    "🎰 A tiny gremlin is resetting the reels. {time}.",
    "🎰 Penny slots have dignity too. Wait {time}.",
]


def _penny_cd_remaining(user, now):
    return max(0.0, (user.get("last_penny", 0.0) + PENNY_COOLDOWN) - now)


def _penny_spin(godmode=False):
    """Pick a prize tier. Returns (payout, reels, message)."""
    if godmode:
        # Always jackpot
        _, payout, reels, msg = PENNY_PRIZES[-1]
        return payout, reels, msg
    total_weight = sum(w for w, _, _, _ in PENNY_PRIZES)
    roll = _sysrand.randint(1, total_weight)
    cumulative = 0
    for weight, payout, reels, msg in PENNY_PRIZES:
        cumulative += weight
        if roll <= cumulative:
            return payout, reels, msg
    # Fallback
    return 0, '💀💀💀', "The machine ate your coin."


@module.commands('penny')
def penny_slots(bot, trigger):
    """$penny — Pull the penny slot machine! Costs 1 coin per spin."""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'penny'):
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else _penny_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(PENNY_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < PENNY_COST:
            bot.reply("🫗 You don't even have a single coin for the penny slot.")
            return

        user["money"] = int(user.get("money", 0)) - PENNY_COST
        user["last_penny"] = now

        payout, reels, msg = _penny_spin(godmode=_has_godmode(trigger.nick))

        if payout > 0:
            user["money"] += payout
            msg_formatted = msg.format(coins=fmt_coins(payout))
            bot.say(
                f"🎰 {reels} {tag(trigger.nick, user['money'])} "
                f"{msg_formatted} Balance: {fmt_coins(user['money'])} 🪙"
            )
        else:
            msg_formatted = msg.format(coins=fmt_coins(0))
            bot.say(
                f"🎰 {reels} {tag(trigger.nick, user['money'])} "
                f"{msg_formatted} Balance: {fmt_coins(user['money'])} 🪙"
            )


# ============================================================
# ================ DOLLAR SLOTS 💵 ============================
# ============================================================
# $dollar — High-roller slot machine! 100 coins per pull, max 50k jackpot.
# Own cooldown (DOLLAR_COOLDOWN) separate from $bet/$roll.

DOLLAR_COOLDOWN = 30  # seconds between pulls
DOLLAR_COST = 100     # 100 coins per pull

# (weight, payout, reels, message)
DOLLAR_PRIZES = [
    (30, 0,     '☠️☠️☠️', "Dead on arrival. The machine devours your dollar."),
    (18, 0,     '🍇🍊☠️', "Fruit salad of failure. Nothing."),
    (12, 50,    '🍊🍊🍇', "Two oranges! Partial refund — {coins} back. 🍊"),
    (9,  200,   '🍊🍊🍊', "Triple oranges! 🍊🍊🍊 +{coins}!"),
    (7,  500,   '🍇🍇🍇', "Grapes! 🍇🍇🍇 Wine money — +{coins}!"),
    (6,  1500,  '💰💰🍇', "Two money bags! 💰💰 +{coins}!"),
    (5,  3000,  '💰💰💰', "TRIPLE MONEY BAGS! 💰💰💰 +{coins}! 🤑"),
    (4,  8000,  '🌟🌟💰', "Shooting stars! 🌟✨ +{coins}!"),
    (3,  15000, '🌟🌟🌟', "⭐ TRIPLE STARS! ⭐ +{coins}! The machine is GLOWING!"),
    (2,  30000, '💎🔥💎', "💎🔥💎 INFERNO DIAMONDS! +{coins}! Security is on their way! 🚨"),
    (1,  50000, '🏆🏆🏆', "🏆🏆🏆 MEGA JACKPOT!!! 💵🎆🎇🪩💸🥳🎊 +{coins}!!! THE FLOOR ERUPTS!!!"),
]

DOLLAR_COOLDOWN_MESSAGES = [
    "💵 The dollar machine is recalibrating… {time}.",
    "💵 High rollers wait their turn — {time}.",
    "💵 The vault needs to restock. {time}.",
    "💵 A guard is polishing the machine. {time}.",
    "💵 Your luck needs a cooldown. {time}.",
]


def _dollar_cd_remaining(user, now):
    return max(0.0, (user.get("last_dollar", 0.0) + DOLLAR_COOLDOWN) - now)


def _dollar_spin(godmode=False):
    """Pick a prize tier. Returns (payout, reels, message)."""
    if godmode:
        _, payout, reels, msg = DOLLAR_PRIZES[-1]
        return payout, reels, msg
    total_weight = sum(w for w, _, _, _ in DOLLAR_PRIZES)
    roll = _sysrand.randint(1, total_weight)
    cumulative = 0
    for weight, payout, reels, msg in DOLLAR_PRIZES:
        cumulative += weight
        if roll <= cumulative:
            return payout, reels, msg
    return 0, '☠️☠️☠️', "The machine ate your dollar."


@module.commands('dollar')
def dollar_slots(bot, trigger):
    """$dollar — High-roller slot machine! Costs 100 coins per spin."""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'dollar'):
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else _dollar_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(DOLLAR_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < DOLLAR_COST:
            bot.reply(f"💸 You need at least {fmt_coins(DOLLAR_COST)} for the dollar machine.")
            return

        user["money"] = int(user.get("money", 0)) - DOLLAR_COST
        user["last_dollar"] = now

        payout, reels, msg = _dollar_spin(godmode=_has_godmode(trigger.nick))

        if payout > 0:
            user["money"] += payout
            msg_formatted = msg.format(coins=fmt_coins(payout))
            bot.say(
                f"💵 {reels} {tag(trigger.nick, user['money'])} "
                f"{msg_formatted} Balance: {fmt_coins(user['money'])} 🪙"
            )
        else:
            msg_formatted = msg.format(coins=fmt_coins(0))
            bot.say(
                f"💵 {reels} {tag(trigger.nick, user['money'])} "
                f"{msg_formatted} Balance: {fmt_coins(user['money'])} 🪙"
            )


# ============================================================
# ==================== ROULETTE 🎡 ============================
# ============================================================
# $roulette <amount> <bet>
#   Bets: red, black, odd, even, high, low, 1st, 2nd, 3rd, 0-36
# Shares BET_COOLDOWN with $bet/$roll.

ROULETTE_REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BLACKS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}

ROULETTE_COOLDOWN_MESSAGES = [
    "🎡 The wheel is still spinning… {time}.",
    "🎡 No bets while the ball is in play — {time}.",
    "🎡 The croupier says wait — {time}.",
    "🎡 Table's full. Come back in {time}.",
    "🎡 You're sweating on the felt. Chill for {time}.",
]


def _roulette_color(n):
    if n == 0:
        return 'green'
    return 'red' if n in ROULETTE_REDS else 'black'


def _roulette_color_emoji(n):
    c = _roulette_color(n)
    return {'red': '🔴', 'black': '⚫', 'green': '🟢'}[c]


def _roulette_eval(bet_str, number):
    """Evaluate a roulette bet. Returns (won, multiplier, description)."""
    color = _roulette_color(number)

    if bet_str == 'red':
        return color == 'red', 2, 'Red'
    if bet_str == 'black':
        return color == 'black', 2, 'Black'
    if bet_str == 'odd':
        return number != 0 and number % 2 == 1, 2, 'Odd'
    if bet_str == 'even':
        return number != 0 and number % 2 == 0, 2, 'Even'
    if bet_str == 'low':
        return 1 <= number <= 18, 2, 'Low (1-18)'
    if bet_str == 'high':
        return 19 <= number <= 36, 2, 'High (19-36)'
    if bet_str == '1st':
        return 1 <= number <= 12, 3, '1st Dozen'
    if bet_str == '2nd':
        return 13 <= number <= 24, 3, '2nd Dozen'
    if bet_str == '3rd':
        return 25 <= number <= 36, 3, '3rd Dozen'

    # Straight number bet
    try:
        target = int(bet_str)
        if 0 <= target <= 36:
            return number == target, 36, f'Straight {target}'
    except ValueError:
        pass

    return None, 0, None  # invalid bet


@module.commands('roulette')
def roulette(bot, trigger):
    """$roulette <amount> <bet> — Spin the wheel! Bets: red/black/odd/even/high/low/1st/2nd/3rd/0-36"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'roulette'):
        return

    args = (trigger.group(2) or "").strip().split()
    if len(args) < 2:
        bot.say(
            "🎡 $roulette <amount> <bet> — Bets: red, black, odd, even, high, low, 1st, 2nd, 3rd, or 0-36 (straight). "
            "Even money = 2x | Dozens = 3x | Straight number = 36x"
        )
        return

    try:
        amount = int(args[0].replace(",", ""))
    except ValueError:
        bot.reply("🔢 Bet amount must be a number.")
        return

    if amount < 1 or amount < GAMBLE_MIN_BET:
        bot.reply(f"💁 Minimum bet is {fmt_coins(GAMBLE_MIN_BET)}.")
        return
    if amount > GAMBLE_MAX_BET:
        bot.reply(f"🚫 Max bet is {fmt_coins(GAMBLE_MAX_BET)}.")
        return

    bet_str = args[1].lower()

    # Validate bet before taking money
    test_won, test_mult, test_desc = _roulette_eval(bet_str, 0)
    if test_desc is None:
        bot.reply("❓ Invalid bet. Options: red, black, odd, even, high, low, 1st, 2nd, 3rd, or a number 0-36.")
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else bet_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(ROULETTE_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < amount:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        user["money"] = int(user.get("money", 0)) - amount
        user["last_bet"] = now

        if _has_godmode(trigger.nick):
            # Rig it: pick a winning number
            if bet_str == 'red':
                number = _sysrand.choice(list(ROULETTE_REDS))
            elif bet_str == 'black':
                number = _sysrand.choice(list(ROULETTE_BLACKS))
            elif bet_str == 'odd':
                number = _sysrand.choice([n for n in range(1, 37) if n % 2 == 1])
            elif bet_str == 'even':
                number = _sysrand.choice([n for n in range(2, 37) if n % 2 == 0])
            elif bet_str == 'low':
                number = _sysrand.randint(1, 18)
            elif bet_str == 'high':
                number = _sysrand.randint(19, 36)
            elif bet_str == '1st':
                number = _sysrand.randint(1, 12)
            elif bet_str == '2nd':
                number = _sysrand.randint(13, 24)
            elif bet_str == '3rd':
                number = _sysrand.randint(25, 36)
            else:
                try:
                    number = int(bet_str)
                except ValueError:
                    number = _sysrand.randint(0, 36)
        else:
            number = _sysrand.randint(0, 36)

        won, mult, desc = _roulette_eval(bet_str, number)
        color_emoji = _roulette_color_emoji(number)
        color_name = _roulette_color(number).upper()

        if won:
            payout = amount * mult
            user["money"] += payout
            bot.say(
                f"🎡 {color_emoji} The ball lands on {number} ({color_name})! "
                f"{tag(trigger.nick, user['money'])} bet {desc} — WIN! 🎉 "
                f"Payout: {fmt_coins(payout)} ({mult}x). Balance: {fmt_coins(user['money'])} 🪙"
            )
        else:
            bot.say(
                f"🎡 {color_emoji} The ball lands on {number} ({color_name}). "
                f"{tag(trigger.nick, user['money'])} bet {desc} — nope. 💀 "
                f"Lost {fmt_coins(amount)}. Balance: {fmt_coins(user['money'])} 🪙"
            )


# ============================================================
# ==================== BLACKJACK 🃏 ===========================
# ============================================================
# $bj <amount> — Start a hand vs the dealer
# $hit — Draw a card
# $stand — Keep your hand
# $dd — Double down (one more card, auto-stand)
# Shares BET_COOLDOWN with $bet/$roll/$roulette.

_BJ_HANDS = {}  # {nick_lower: {hand, dealer, amount, channel, deck, doubled}}
_BJ_LOCK = threading.Lock()

_SUITS = ['♠️', '♥️', '♦️', '♣️']
_RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

BJ_COOLDOWN_MESSAGES = [
    "🃏 The dealer is shuffling. Chill — {time}.",
    "🃏 Cards are still in the air. Wait {time}.",
    "🃏 You're barred from the table for {time}.",
    "🃏 The pit boss is watching you. {time}.",
]


def _bj_new_deck():
    """Create and shuffle a standard 52-card deck."""
    deck = [(r, s) for s in _SUITS for r in _RANKS]
    _sysrand.shuffle(deck)
    return deck


def _bj_card_str(card):
    """Format a card tuple as a string."""
    return f"{card[0]}{card[1]}"


def _bj_hand_str(hand):
    """Format a list of cards as a string."""
    return ' '.join(_bj_card_str(c) for c in hand)


def _bj_card_value(rank):
    """Value of a single card rank (A=11, face=10)."""
    if rank == 'A':
        return 11
    if rank in ('K', 'Q', 'J'):
        return 10
    return int(rank)


def _bj_hand_value(hand):
    """Calculate best hand value, adjusting aces down from 11 to 1 as needed."""
    total = sum(_bj_card_value(r) for r, s in hand)
    aces = sum(1 for r, s in hand if r == 'A')
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _bj_draw(deck):
    """Draw a card from the deck."""
    return deck.pop()


def _bj_draw_for_player(deck, hand, godmode=False):
    """Draw a card. If godmode, prefer a card that will not bust."""
    if godmode and deck:
        for i, card in enumerate(deck):
            if _bj_hand_value(hand + [card]) <= 21:
                return deck.pop(i)
    return _bj_draw(deck)


def _bj_is_blackjack(hand):
    return len(hand) == 2 and _bj_hand_value(hand) == 21


def _bj_dealer_play(deck, dealer_hand, godmode=False):
    """Dealer draws until 17+. If godmode, dealer busts."""
    if godmode:
        # Rig dealer to bust: keep drawing until bust
        while _bj_hand_value(dealer_hand) < 22:
            dealer_hand.append(_bj_draw(deck))
            if len(deck) == 0:
                break
        return
    while _bj_hand_value(dealer_hand) < 17:
        dealer_hand.append(_bj_draw(deck))


def _bj_resolve(bot, nick, game):
    """Resolve a blackjack hand and pay out."""
    user_key = normalize_key(nick)
    with locked_data(bot):
        user = get_user_record(bot, nick)
        hand_val = _bj_hand_value(game['hand'])
        dealer_val = _bj_hand_value(game['dealer'])
        amount = game['amount']
        chan = game['channel']

        player_bj = _bj_is_blackjack(game['hand'])
        dealer_bj = _bj_is_blackjack(game['dealer'])

        dealer_str = _bj_hand_str(game['dealer'])

        if player_bj and dealer_bj:
            # Push on double blackjack
            user["money"] += amount
            bot.say(
                f"🃏 Dealer: {dealer_str} ({dealer_val}) — Both blackjack! Push. "
                f"{tag(nick, user['money'])} gets {fmt_coins(amount)} back. Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        elif player_bj:
            payout = int(amount * 2.5)
            user["money"] += payout
            bot.say(
                f"🃏 ✨ BLACKJACK! ✨ {_bj_hand_str(game['hand'])} — "
                f"{tag(nick, user['money'])} wins {fmt_coins(payout)} (2.5x)! 🎉🃏💰 "
                f"Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        elif hand_val > 21:
            bot.say(
                f"🃏 BUST! {_bj_hand_str(game['hand'])} ({hand_val}) 💥 "
                f"Dealer: {dealer_str} ({dealer_val}). "
                f"{tag(nick, user['money'])} lost {fmt_coins(amount)}. Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        elif dealer_val > 21:
            payout = amount * 2
            user["money"] += payout
            bot.say(
                f"🃏 Dealer BUSTS! {dealer_str} ({dealer_val}) 💥 "
                f"{tag(nick, user['money'])} wins {fmt_coins(payout)}! 🎉 "
                f"Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        elif hand_val > dealer_val:
            payout = amount * 2
            user["money"] += payout
            bot.say(
                f"🃏 {_bj_hand_str(game['hand'])} ({hand_val}) vs Dealer {dealer_str} ({dealer_val}) — "
                f"{tag(nick, user['money'])} WINS! 🎉 +{fmt_coins(payout)}. Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        elif hand_val < dealer_val:
            bot.say(
                f"🃏 {_bj_hand_str(game['hand'])} ({hand_val}) vs Dealer {dealer_str} ({dealer_val}) — "
                f"Dealer wins. 💀 {tag(nick, user['money'])} lost {fmt_coins(amount)}. Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )
        else:
            # Push
            user["money"] += amount
            bot.say(
                f"🃏 {_bj_hand_str(game['hand'])} ({hand_val}) vs Dealer {dealer_str} ({dealer_val}) — "
                f"Push! {tag(nick, user['money'])} gets {fmt_coins(amount)} back. Balance: {fmt_coins(user['money'])} 🪙",
                chan
            )


@module.commands('bj', 'blackjack')
def blackjack_start(bot, trigger):
    """$bj <amount> — Start a blackjack hand against the dealer."""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'bj'):
        return

    nick_key = normalize_key(trigger.nick)

    with _BJ_LOCK:
        if nick_key in _BJ_HANDS:
            game = _BJ_HANDS[nick_key]
            bot.reply(
                f"🃏 You already have a hand! {_bj_hand_str(game['hand'])} "
                f"({_bj_hand_value(game['hand'])}). Use $hit or $stand."
            )
            return

    arg = (trigger.group(2) or "").strip()
    if not arg:
        bot.say("🃏 $bj <amount> — Play blackjack! Then use $hit, $stand, or $dd (double down).")
        return

    try:
        amount = int(arg.replace(",", ""))
    except ValueError:
        bot.reply("🔢 Bet amount must be a number.")
        return

    if amount < 1 or amount < GAMBLE_MIN_BET:
        bot.reply(f"💁 Minimum bet is {fmt_coins(GAMBLE_MIN_BET)}.")
        return
    if amount > GAMBLE_MAX_BET:
        bot.reply(f"🚫 Max bet is {fmt_coins(GAMBLE_MAX_BET)}.")
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else bet_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(BJ_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < amount:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        user["money"] = int(user.get("money", 0)) - amount
        user["last_bet"] = now

    deck = _bj_new_deck()
    hand = [_bj_draw(deck), _bj_draw(deck)]
    dealer = [_bj_draw(deck), _bj_draw(deck)]

    game = {
        'hand': hand,
        'dealer': dealer,
        'amount': amount,
        'channel': str(trigger.sender),
        'deck': deck,
        'doubled': False,
    }

    # Check for natural blackjack
    if _bj_is_blackjack(hand):
        _bj_dealer_play(deck, dealer, godmode=_has_godmode(trigger.nick))
        _bj_resolve(bot, trigger.nick, game)
        return

    with _BJ_LOCK:
        _BJ_HANDS[nick_key] = game

    dealer_show = _bj_card_str(dealer[0])
    bot.say(
        f"🃏 {tag(trigger.nick, None)} — Your hand: {_bj_hand_str(hand)} ({_bj_hand_value(hand)}) | "
        f"Dealer shows: {dealer_show} | $hit, $stand, or $dd"
    )


@module.commands('hit')
def blackjack_hit(bot, trigger):
    """$hit — Draw another card in blackjack."""
    nick_key = normalize_key(trigger.nick)

    with _BJ_LOCK:
        game = _BJ_HANDS.get(nick_key)
        if not game:
            bot.reply("🃏 You don't have a hand. Start one with $bj <amount>.")
            return
        if game['doubled']:
            bot.reply("🃏 Double down in progress — your hand is resolving.")
            return
        # Draw from the game's deck (godmode: never bust if a safe card remains)
        card = _bj_draw_for_player(game['deck'], game['hand'], godmode=_has_godmode(trigger.nick))
        game['hand'].append(card)
        hand_val = _bj_hand_value(game['hand'])

        if hand_val > 21:
            # Bust — remove game and resolve
            del _BJ_HANDS[nick_key]

    if hand_val > 21:
        _bj_dealer_play(game['deck'], game['dealer'])
        _bj_resolve(bot, trigger.nick, game)
        return

    bot.say(
        f"🃏 {tag(trigger.nick, None)} drew {_bj_card_str(card)} — "
        f"Hand: {_bj_hand_str(game['hand'])} ({hand_val}) | $hit, $stand, or $dd"
    )


@module.commands('stand')
def blackjack_stand(bot, trigger):
    """$stand — Keep your hand, dealer plays."""
    nick_key = normalize_key(trigger.nick)

    with _BJ_LOCK:
        game = _BJ_HANDS.pop(nick_key, None)
    if not game:
        bot.reply("🃏 You don't have a hand. Start one with $bj <amount>.")
        return

    _bj_dealer_play(game['deck'], game['dealer'], godmode=_has_godmode(trigger.nick))
    _bj_resolve(bot, trigger.nick, game)


@module.commands('dd')
def blackjack_double_down(bot, trigger):
    """$dd — Double down: double your bet, draw one card, auto-stand."""
    nick_key = normalize_key(trigger.nick)

    with _BJ_LOCK:
        game = _BJ_HANDS.get(nick_key)
        if not game:
            bot.reply("🃏 You don't have a hand. Start one with $bj <amount>.")
            return
        if len(game['hand']) != 2:
            bot.reply("🃏 You can only double down on your first two cards.")
            return
        if game['doubled']:
            bot.reply("🃏 Already doubled!")
            return
        # Claim the double NOW so a racing $hit/$dd can't double-process
        game['doubled'] = True
        extra = game['amount']

    # Deduct the extra bet
    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)
        if int(user.get("money", 0)) < extra:
            short = True
        else:
            user["money"] = int(user.get("money", 0)) - extra
            short = False
    if short:
        with _BJ_LOCK:
            game['doubled'] = False  # un-claim so they can still $hit/$stand
        bot.reply(f"💸 You need {fmt_coins(extra)} more to double down and you're short.")
        return

    stale = False
    with _BJ_LOCK:
        # A racing $stand may have popped and resolved the hand while we
        # were deducting — if so, refund the extra bet instead of crashing
        # or resolving the hand twice.
        if _BJ_HANDS.pop(nick_key, None) is not game:
            stale = True
        else:
            game['amount'] *= 2
            card = _bj_draw_for_player(game['deck'], game['hand'], godmode=_has_godmode(trigger.nick))
            game['hand'].append(card)

    if stale:
        with locked_data(bot):
            user = get_user_record(bot, trigger.nick)
            user["money"] = int(user.get("money", 0)) + extra
        bot.reply("🃏 That hand just ended — double down cancelled, extra bet refunded.")
        return

    bot.say(
        f"🃏 DOUBLE DOWN! {tag(trigger.nick, None)} drew {_bj_card_str(card)} — "
        f"Hand: {_bj_hand_str(game['hand'])} ({_bj_hand_value(game['hand'])})"
    )

    _bj_dealer_play(game['deck'], game['dealer'], godmode=_has_godmode(trigger.nick))
    _bj_resolve(bot, trigger.nick, game)


# ============================================================
# ================ TEXAS HOLD'EM 🤠 ==========================
# ============================================================
# $holdem <amount> — Instant heads-up vs the dealer.
# 2 hole cards each, 5 community cards, best 5-card hand wins.
# Shares BET_COOLDOWN.

HOLDEM_COOLDOWN_MESSAGES = [
    "🤠 The dealer is still counting chips. {time}.",
    "🤠 Cards haven't been shuffled yet — {time}.",
    "🤠 The table's not ready. {time}.",
    "🤠 Your poker face needs a break. {time}.",
]

_HOLDEM_RANK_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
    '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14
}

_HAND_NAMES = {
    9: "🏆 ROYAL FLUSH",
    8: "🌟 Straight Flush",
    7: "💎 Four of a Kind",
    6: "🏠 Full House",
    5: "🎨 Flush",
    4: "📏 Straight",
    3: "🎯 Three of a Kind",
    2: "👯 Two Pair",
    1: "👫 One Pair",
    0: "👋 High Card",
}

# Payout multipliers based on winning hand rank
_HOLDEM_PAYOUTS = {
    9: 50,  # Royal flush
    8: 25,  # Straight flush
    7: 12,  # Four of a kind
    6: 6,   # Full house
    5: 4,   # Flush
    4: 3,   # Straight
    3: 2,   # Three of a kind
    2: 2,   # Two pair
    1: 2,   # One pair (must beat dealer)
    0: 2,   # High card (must beat dealer)
}


def _holdem_best_hand(hole, community):
    """Find the best 5-card poker hand from 7 cards. Returns (rank, tiebreakers, hand_name, five_cards)."""
    from itertools import combinations
    all_cards = hole + community
    best = None
    best_five = None
    for five in combinations(all_cards, 5):
        score = _holdem_score_five(list(five))
        if best is None or score > best:
            best = score
            best_five = list(five)
    rank = best[0]
    return rank, best, _HAND_NAMES.get(rank, "???"), best_five


def _holdem_score_five(five):
    """Score a 5-card hand. Returns a tuple for comparison (rank, tiebreakers...)."""
    ranks = sorted([_HOLDEM_RANK_VALUES[c[0]] for c in five], reverse=True)
    suits = [c[1] for c in five]

    is_flush = len(set(suits)) == 1
    # Check for straight (including A-low: A,2,3,4,5)
    is_straight = False
    straight_high = 0
    if ranks[0] - ranks[4] == 4 and len(set(ranks)) == 5:
        is_straight = True
        straight_high = ranks[0]
    elif ranks == [14, 5, 4, 3, 2]:
        is_straight = True
        straight_high = 5  # Wheel

    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    groups = sorted(rank_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

    if is_flush and is_straight:
        if straight_high == 14:
            return (9, 14)  # Royal flush
        return (8, straight_high)  # Straight flush
    if groups[0][1] == 4:
        return (7, groups[0][0], groups[1][0])  # Four of a kind
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])  # Full house
    if is_flush:
        return (5,) + tuple(ranks)  # Flush
    if is_straight:
        return (4, straight_high)  # Straight
    if groups[0][1] == 3:
        kickers = sorted([g[0] for g in groups[1:]], reverse=True)
        return (3, groups[0][0]) + tuple(kickers)  # Three of a kind
    if groups[0][1] == 2 and groups[1][1] == 2:
        pairs = sorted([groups[0][0], groups[1][0]], reverse=True)
        kicker = [g[0] for g in groups if g[1] == 1][0]
        return (2, pairs[0], pairs[1], kicker)  # Two pair
    if groups[0][1] == 2:
        kickers = sorted([g[0] for g in groups[1:]], reverse=True)
        return (1, groups[0][0]) + tuple(kickers)  # One pair
    return (0,) + tuple(ranks)  # High card


@module.commands('holdem')
def texas_holdem(bot, trigger):
    """$holdem <amount> — Heads-up Texas Hold'em vs the dealer!"""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if _anticheat_gate(bot, trigger, 'holdem'):
        return

    arg = (trigger.group(2) or "").strip()
    if not arg:
        bot.say(
            "🤠 $holdem <amount> — Heads-up Texas Hold'em vs the dealer! "
            "Best 5-card hand from 2 hole cards + 5 community cards wins. "
            "Payout scales with hand strength: Pair 2x → Royal Flush 50x!"
        )
        return

    try:
        amount = int(arg.replace(",", ""))
    except ValueError:
        bot.reply("🔢 Bet amount must be a number.")
        return

    if amount < 1 or amount < GAMBLE_MIN_BET:
        bot.reply(f"💁 Minimum bet is {fmt_coins(GAMBLE_MIN_BET)}.")
        return
    if amount > GAMBLE_MAX_BET:
        bot.reply(f"🚫 Max bet is {fmt_coins(GAMBLE_MAX_BET)}.")
        return

    now = time.time()

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        rem = 0 if _is_admin(bot, trigger.nick) else bet_cd_remaining(user, now)
        if rem > 0:
            bot.reply(_rand(HOLDEM_COOLDOWN_MESSAGES).format(time=fmt_time_remaining(rem)))
            return

        if int(user.get("money", 0)) < amount:
            bot.reply(_rand(BROKE_MESSAGES))
            return

        user["money"] = int(user.get("money", 0)) - amount
        user["last_bet"] = now

    # Deal
    deck = _bj_new_deck()  # reuse the 52-card deck builder
    player_hole = [_bj_draw(deck), _bj_draw(deck)]
    dealer_hole = [_bj_draw(deck), _bj_draw(deck)]
    community = [_bj_draw(deck) for _ in range(5)]

    p_rank, p_score, p_name, p_best = _holdem_best_hand(player_hole, community)
    d_rank, d_score, d_name, d_best = _holdem_best_hand(dealer_hole, community)

    hole_str = _bj_hand_str(player_hole)
    d_hole_str = _bj_hand_str(dealer_hole)
    comm_str = _bj_hand_str(community)

    # God mode: if player would lose, re-deal player's hole cards to win
    if _has_godmode(trigger.nick) and p_score <= d_score:
        # Give the player a royal flush if possible, otherwise just re-score to win
        for _ in range(50):
            _sysrand.shuffle(deck)
            player_hole = [_bj_draw(deck), _bj_draw(deck)]
            deck.extend(player_hole)  # put them back for reshuffling
            p_rank, p_score, p_name, p_best = _holdem_best_hand(player_hole, community)
            if p_score > d_score:
                hole_str = _bj_hand_str(player_hole)
                break

    won = p_score > d_score
    tied = p_score == d_score

    with locked_data(bot):
        user = get_user_record(bot, trigger.nick)

        if tied:
            user["money"] += amount
            bot.say(
                f"🤠 {hole_str} | Board: {comm_str} | Dealer: {d_hole_str} — "
                f"Both have {p_name}! Push. {tag(trigger.nick, user['money'])} "
                f"gets {fmt_coins(amount)} back. Balance: {fmt_coins(user['money'])} 🪙"
            )
        elif won:
            mult = _HOLDEM_PAYOUTS.get(p_rank, 2)
            payout = amount * mult
            user["money"] += payout
            bot.say(
                f"🤠 {hole_str} | Board: {comm_str} | Dealer: {d_hole_str} — "
                f"{p_name} beats {d_name}! 🎉 {tag(trigger.nick, user['money'])} "
                f"wins {fmt_coins(payout)} ({mult}x)! Balance: {fmt_coins(user['money'])} 🪙"
            )
        else:
            bot.say(
                f"🤠 {hole_str} | Board: {comm_str} | Dealer: {d_hole_str} — "
                f"Dealer's {d_name} beats your {p_name}. 💀 "
                f"{tag(trigger.nick, user['money'])} lost {fmt_coins(amount)}. Balance: {fmt_coins(user['money'])} 🪙"
            )


# ============================================================
# ======================= SHOP & INVENTORY ===================
# ============================================================

@module.commands('shop')
def shop(bot, trigger):
    """$shop (PM or channel)"""
    chan = str(trigger.sender) if str(trigger.sender).startswith('#') else None
    if not _plugin_enabled(bot, chan):
        _disabled_msg(bot, trigger)
        return
    nick = trigger.nick
    if trigger.sender.startswith('#'):
        bot.say(f"🛒 {nick}: check your PM for the shop list.")

    _pm(bot, nick, "🛒 Welcome to the Crime Shop! Use $buy <itemkey> to purchase. 😈")
    for key, item in ITEMS.items():
        _pm(bot, nick, f"{key} → {item['name']} ({fmt_coins(item['price'])} coins) – {item.get('desc', '')}")


@module.commands('buy')
def buy(bot, trigger):
    """$buy <itemkey> (PM)"""
    chan = str(trigger.sender) if str(trigger.sender).startswith('#') else None
    if not _plugin_enabled(bot, chan):
        _disabled_msg(bot, trigger)
        return
    if _anticheat_gate(bot, trigger, 'buy'):
        return

    nick = trigger.nick
    args = (trigger.group(2) or "").strip()
    if not args:
        _pm(bot, nick, "🛒 Usage: $buy <itemkey>  |  Use $shop to see keys.")
        return

    key = args.split()[0].lower()
    item = get_item(key)
    if not item:
        _pm(bot, nick, f"❓ No such item '{key}'. Use $shop to see valid keys.")
        return

    price = int(item.get("price", 0))

    with locked_data(bot):
        user = get_user_record(bot, nick)
        if int(user.get("money", 0)) < price:
            _pm(bot, nick, f"💸 Not enough coins for {item['name']} (costs {fmt_coins(price)}). Go farm $coins like a goblin. 🪙")
            return

        inv = user.setdefault("inv", {})
        current_count = int(inv.get(key, 0))
        
        if current_count >= 3:
            _pm(bot, nick, f"🚫 You already have 3 {item['name']}s. That's the max allowed! (Stack limit: 3)")
            return

        user["money"] = int(user.get("money", 0)) - price
        inv[key] = current_count + 1

        # Auto-consume bail if bought while jailed
        if key == 'bail' and int(user.get('jail_until', 0)) > time.time():
            # consume one bail immediately but keep mug cooldown (anti-exploit)
            inv[key] = max(0, int(inv.get(key, 0)) - 1)
            user['jail_until'] = 0.0
            _pm(bot, nick, f"🪓🎉 {nick} bought {item['name']} for {fmt_coins(price)} coins and got bailed out! Welcome back to freedom. (Mug cooldown still active.) New balance: {fmt_coins(user['money'])} 🧾✨")
        else:
            _pm(bot, nick, f"✅ Bought {item['name']} for {fmt_coins(price)} coins! New balance: {fmt_coins(user['money'])} 🧾✨")


@module.commands('inv', 'inventory')
def inventory(bot, trigger):
    """$inv (PM)"""
    chan = str(trigger.sender) if str(trigger.sender).startswith('#') else None
    if not _plugin_enabled(bot, chan):
        _disabled_msg(bot, trigger)
        return
    nick = trigger.nick
    with _data_lock:
        user = get_user_record(bot, nick)
    inv = user.get("inv", {})

    if not inv:
        _pm(bot, nick, "🎒 Your inventory is empty. You’re basically a civilian. Use $shop. 😭")
        return

    _pm(bot, nick, "🎒 Your inventory:")
    for k, c in inv.items():
        item = get_item(k)
        if not item:
            continue
        _pm(bot, nick, f"- {item['name']} x{c} ({k}) – {item.get('desc', '')}")


# PM: use an item (e.g., bail)
@module.commands('use')
def use_item(bot, trigger):
    """PM: $use <itemkey> — consume an item (e.g., `bail`)"""
    if not _plugin_enabled(bot, None):
        _disabled_msg(bot, trigger)
        return
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if _anticheat_gate(bot, trigger, 'use'):
        return

    nick = trigger.nick
    args = (trigger.group(2) or "").strip()
    if not args:
        _pm(bot, nick, "🧰 Usage: $use <itemkey> — use an item from your inventory.")
        return

    key = args.split()[0].lower()
    item = get_item(key)
    if not item:
        _pm(bot, nick, f"❓ No such item '{key}'. Use $inv to see your items.")
        return

    with locked_data(bot):
        user = get_user_record(bot, nick)
        inv = user.get('inv', {})
        count = int(inv.get(key, 0))
        if count <= 0:
            _pm(bot, nick, f"🪫 You don't have any {item['name']} ({key}). Use $shop to buy one.")
            return

        # Bail behavior: release from jail
        if key == 'bail':
            if int(user.get('jail_until', 0)) > time.time():
                # Consume one and clear jail, but keep mug cooldown (anti-exploit)
                inv[key] = count - 1
                user['jail_until'] = 0.0
                _pm(bot, nick, f"🪓🎉 {nick} used {item['name']} and was BAIL-IFIED! You're free! (Mug cooldown still active.) Stay out of trouble. 💸")
            else:
                _pm(bot, nick, f"🤷 {nick}, you're not in jail. Your {item['name']} stays in your pocket for when you need it. Use it wisely! 🪙")
            return

        # Passive items cannot be consumed — they provide bonuses just by existing in inventory
        if key not in USABLE_ITEMS:
            _pm(bot, nick, f"🛡️ {item['name']} is a passive item — it works automatically from your inventory. No need to use it!")
            return

        # Consume one for other usable items (future-proofed)
        inv[key] = count - 1
        _pm(bot, nick, f"✅ Used {item['name']} x1. ({item.get('desc','')})")


# ============================================================
# =================== LEADERBOARDS (ONE LINE) =================
# ============================================================

def _get_leaderboard(bot, limit):
    with _data_lock:
        data = _load_data(bot)
        users = data.get("users", {})
        sorted_users = sorted(users.values(), key=lambda u: (-int(u.get("money", 0)), u.get("nick", "").lower()))
    return sorted_users[:limit]


def _format_lb(users, start_rank=1):
    """
    Format leaderboard entries with correct global rank.
    🥇🥈🥉 are reserved for ranks 1–3; the rest use #N.
    """
    parts = []
    for i, u in enumerate(users):
        rank = start_rank + i
        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"#{rank}"
        parts.append(f"{prefix} {u.get('nick','?')}({fmt_coins(u.get('money',0))})")
    return " | ".join(parts)


@module.commands('top5')
def top5(bot, trigger):
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not _is_admin(bot, trigger.nick):
        now = time.time()
        key = ('top_cd', normalize_key(trigger.nick))
        last = _top_cd_cache.get(key, 0)
        if now - last < TOP_CD:
            remaining = int(TOP_CD - (now - last))
            bot.notice(f"⏳ Slow down! You can use $top5/$top10 again in {fmt_time_remaining(remaining)}.", trigger.nick)
            return
        _top_cd_cache[key] = now
    top = _get_leaderboard(bot, 5)
    if not top:
        bot.say("📉 No coin data yet. Go earn some with $coins! 💰")
        return
    safe_say(bot, f"🏆 Top 5 coin hoarders: {_format_lb(top, start_rank=1)}")


@module.commands('top10')
def top10(bot, trigger):
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not _is_admin(bot, trigger.nick):
        now = time.time()
        key = ('top_cd', normalize_key(trigger.nick))
        last = _top_cd_cache.get(key, 0)
        if now - last < TOP_CD:
            remaining = int(TOP_CD - (now - last))
            bot.notice(f"⏳ Slow down! You can use $top5/$top10 again in {fmt_time_remaining(remaining)}.", trigger.nick)
            return
        _top_cd_cache[key] = now
    top = _get_leaderboard(bot, 10)
    if not top:
        bot.say("📉 No coin data yet. Go earn some with $coins! 💰")
        return

    safe_say(bot, f"💰 Top 10 coin legends (1–5): {_format_lb(top[:5], start_rank=1)}")
    safe_say(bot, f"💰 Top 10 coin legends (6–10): {_format_lb(top[5:10], start_rank=6)}")


@module.commands('highscore')
def highscore(bot, trigger):
    """$highscore - show the all-time highest balance ever achieved."""
    if not _plugin_enabled(bot, trigger.sender):
        _disabled_msg(bot, trigger)
        return
    if not trigger.sender.startswith('#'):
        bot.reply("🏠 Use this in a channel.")
        return
    if not _check_global_cooldown(trigger.nick):
        return

    data = _load_data(bot)
    hs_nick = data.get("highscore_nick", "")
    hs_amt = int(data.get("highscore_amount", 0))

    if not hs_nick or hs_amt <= 0:
        bot.say("📉 No high score recorded yet. Get out there and earn some coins! 💰")
        return

    bot.say(f"🏆 All-time high score: {hs_nick} with {fmt_coins(hs_amt)} coins! 👑")


# ============================================================
# ===================== PM ADMIN COMMANDS ====================
# ============================================================

@module.commands('mugadd')
def mugadd(bot, trigger):
    """PM: $mugadd <nick> <amount>  (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    args = (trigger.group(2) or "").split()
    if len(args) < 2:
        _pm(bot, trigger.nick, "Usage: $mugadd <nick> <amount>")
        return

    target = args[0]
    try:
        amt = int(args[1].replace(",", ""))
    except ValueError:
        _pm(bot, trigger.nick, "🔢 Amount must be a whole number.")
        return
    if amt <= 0:
        _pm(bot, trigger.nick, "🧠 Amount must be > 0.")
        return

    with locked_data(bot):
        u = get_user_record(bot, target)
        u["money"] = int(u.get("money", 0)) + amt

    _pm(bot, trigger.nick, f"✅ Added {fmt_coins(amt)} coins to {u['nick']}. New balance: {fmt_coins(u['money'])} 🪙")


@module.commands('mugset')
def mugset(bot, trigger):
    """PM: $mugset <nick> <amount>  (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    args = (trigger.group(2) or "").split()
    if len(args) < 2:
        _pm(bot, trigger.nick, "Usage: $mugset <nick> <amount>")
        return

    target = args[0]
    try:
        amt = int(args[1].replace(",", ""))
    except ValueError:
        _pm(bot, trigger.nick, "🔢 Amount must be a whole number.")
        return
    if amt < 0:
        _pm(bot, trigger.nick, "🧠 Amount must be >= 0.")
        return

    with locked_data(bot):
        u = get_user_record(bot, target)
        u["money"] = amt

    _pm(bot, trigger.nick, f"✅ Set {u['nick']}'s balance to {fmt_coins(u['money'])} 🪙")


@module.commands('mugtake')
def mugtake(bot, trigger):
    """PM: $mugtake <nick> <amount>  (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    args = (trigger.group(2) or "").split()
    if len(args) < 2:
        _pm(bot, trigger.nick, "Usage: $mugtake <nick> <amount>")
        return

    target = args[0]
    try:
        amt = int(args[1].replace(",", ""))
    except ValueError:
        _pm(bot, trigger.nick, "🔢 Amount must be a whole number.")
        return
    if amt <= 0:
        _pm(bot, trigger.nick, "🧠 Amount must be > 0.")
        return

    with locked_data(bot):
        u = get_user_record(bot, target)
        cur = int(u.get("money", 0))
        u["money"] = max(0, cur - amt)

    _pm(bot, trigger.nick, f"✅ Took {fmt_coins(amt)} coins from {u['nick']}. New balance: {fmt_coins(u['money'])} 🪙")


@module.commands('mugreset')
def mugreset(bot, trigger):
    """PM: $mugreset  (admin only) - resets ALL users + bounties + cooldowns + inventories"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    with locked_data(bot) as data:
        # reset all user records
        for u in data.get("users", {}).values():
            if not isinstance(u, dict):
                continue
            u["money"] = 0
            u["last_coins"] = 0.0
            u["last_mug"] = 0.0
            u["jail_until"] = 0.0
            u["last_bet"] = 0.0
            u["last_give"] = 0.0
            u["inv"] = {}

        # reset bounties
        data["bounties"] = {}
        data["last_bounty"] = {}

    _pm(bot, trigger.nick, "🧨 FULL RESET DONE. Everyone is broke again. Society restored. ✅")


@module.commands('mugcleardb')
def mugcleardb(bot, trigger):
    """PM: $mugcleardb confirm  (admin only) - deletes ALL user records + bounties from storage"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    args = (trigger.group(2) or "").strip().lower()
    if args != "confirm":
        _pm(bot, trigger.nick, "⚠️ This permanently wipes ALL mug player records (money/cooldowns/inv) and bounties.")
        _pm(bot, trigger.nick, "Usage: $mugcleardb confirm")
        return

    global _data
    with _data_lock:
        # Replace in-memory cache with a clean default structure.
        _data = {"users": {}, "bounties": {}, "last_bounty": {}}

        # Persist wipe to Sopel's DB. Prefer deletion if supported; otherwise overwrite.
        try:
            db = getattr(bot, "db", None)
            if db is not None and hasattr(db, "delete_plugin_value"):
                db.delete_plugin_value(PLUGIN_NAME, 'data')
            else:
                bot.db.set_plugin_value(PLUGIN_NAME, 'data', _data)
        except Exception:
            # Best-effort fallback: overwrite.
            try:
                bot.db.set_plugin_value(PLUGIN_NAME, 'data', _data)
            except Exception:
                pass

    _pm(bot, trigger.nick, "🧹 DB WIPE DONE. All mug player records have been deleted. ✅")


@module.commands('mugmerge')
def mugmerge(bot, trigger):
    """PM: $mugmerge <nick> — merge duplicate records for normalized nick (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return
    raw = (trigger.group(2) or "").strip()
    args = raw.split()
    if not args:
        _pm(bot, trigger.nick, "Usage: $mugmerge <nick> [--dry]")
        return

    # allow flags: --dry or trailing 'dry'
    dry = False
    flags = [a for a in args if a.startswith('--') or a.lower() == 'dry']
    if '--dry' in args or 'dry' in [a.lower() for a in args]:
        dry = True

    # pick first non-flag token as target
    target = next((a for a in args if not a.startswith('--') and a.lower() != 'dry'), None)
    if not target:
        _pm(bot, trigger.nick, "Usage: $mugmerge <nick> [--dry]")
        return

    nk = normalize_key(target)

    with locked_data(bot) as data:
        users = data.get('users', {})
        # Find keys whose stored display name normalizes to the same key
        matches = [k for k, u in users.items() if normalize_key(u.get('nick', '')) == nk]
        if not matches:
            _pm(bot, trigger.nick, f"No records found matching '{target}'.")
            return

        if dry:
            total_money = sum(int(users.get(k, {}).get('money', 0)) for k in matches)
            combined_inv = {}
            for k in matches:
                for itemk, cnt in users.get(k, {}).get('inv', {}).items():
                    combined_inv[itemk] = combined_inv.get(itemk, 0) + int(cnt)

            _pm(bot, trigger.nick, f"Dry-run: would merge {len(matches)} records for normalized key '{nk}':")
            for k in matches:
                u = users.get(k, {})
                _pm(bot, trigger.nick, f" - stored key: {k} | nick: {u.get('nick','?')} | money: {fmt_coins(u.get('money',0))}")

            _pm(bot, trigger.nick, f"Combined total money: {fmt_coins(total_money)}")
            if combined_inv:
                _pm(bot, trigger.nick, "Combined inventory summary:")
                for itemk, cnt in combined_inv.items():
                    _pm(bot, trigger.nick, f" - {itemk}: x{cnt}")
            _pm(bot, trigger.nick, "Run '$mugmerge <nick>' (without --dry) to perform the merge.")
            return

        # Non-dry merge: perform the merge into canonical nk
        # Ensure canonical record exists
        canonical_existed = nk in users
        if not canonical_existed:
            users[nk] = {
                'nick': target,
                'money': 0,
                'last_coins': 0.0,
                'last_mug': 0.0,
                'jail_until': 0.0,
                'last_bet': 0.0,
                'inv': {},
            }

        canonical = users[nk]
        merged_money = 0
        merged_inv = {}
        merged_count = 0

        for k in list(matches):
            if k == nk:
                continue
            u = users.get(k)
            if not isinstance(u, dict):
                continue
            merged_money += int(u.get('money', 0))
            for itemk, cnt in u.get('inv', {}).items():
                merged_inv[itemk] = merged_inv.get(itemk, 0) + int(cnt)

            canonical['last_coins'] = max(canonical.get('last_coins', 0.0), u.get('last_coins', 0.0))
            canonical['last_mug'] = max(canonical.get('last_mug', 0.0), u.get('last_mug', 0.0))
            canonical['last_bet'] = max(canonical.get('last_bet', 0.0), u.get('last_bet', 0.0))
            canonical['jail_until'] = max(canonical.get('jail_until', 0.0), u.get('jail_until', 0.0))

            users.pop(k, None)
            merged_count += 1

        canonical['money'] = int(canonical.get('money', 0)) + merged_money
        inv = canonical.setdefault('inv', {})
        for itemk, cnt in merged_inv.items():
            inv[itemk] = min(3, int(inv.get(itemk, 0)) + int(cnt))  # cap at 3

        _pm(bot, trigger.nick, f"✅ Merged {merged_count} record(s) into {canonical.get('nick', target)}. New balance: {fmt_coins(canonical['money'])} coins.")


@module.commands('mugdup')
def mugdup(bot, trigger):
    """PM: $mugdup <nick> — list stored records that normalize to the same nick (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You’re not an admin.")
        return

    args = (trigger.group(2) or "").split()
    if not args:
        _pm(bot, trigger.nick, "Usage: $mugdup <nick>")
        return

    target = args[0]
    nk = normalize_key(target)

    data = _load_data(bot)
    users = data.get('users', {})
    matches = [k for k, u in users.items() if normalize_key(u.get('nick', '')) == nk]
    if not matches:
        _pm(bot, trigger.nick, f"No stored records normalize to '{target}' (normalized: '{nk}').")
        return

    _pm(bot, trigger.nick, f"Records normalizing to '{nk}':")
    for k in matches:
        u = users.get(k, {})
        _pm(bot, trigger.nick, f" - stored key: {k} | nick: {u.get('nick','?')} | money: {fmt_coins(u.get('money',0))}")
    _pm(bot, trigger.nick, "Use $mugmerge <nick> --dry to preview a merge, or $mugmerge <nick> to merge.")


@module.commands('mugclearbounty')
def mugclearbounty(bot, trigger):
    """PM: $mugclearbounty <nick> — remove a bounty (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You're not an admin.")
        return

    args = (trigger.group(2) or "").split()
    if not args:
        _pm(bot, trigger.nick, "Usage: $mugclearbounty <nick>")
        return

    target = args[0]
    nk = normalize_key(target)

    with locked_data(bot) as data:
        bounties = data.get('bounties', {})
        amt = int(bounties.pop(nk, 0))
        if amt > 0:
            _pm(bot, trigger.nick, f"✅ Removed {fmt_coins(amt)} coin bounty on {target}. Coins returned to the void. 🕳️")
        else:
            _pm(bot, trigger.nick, f"🤷 No active bounty on {target}.")


# ============================================================
# ======================== GOD MODE ==========================
# ============================================================

@module.commands('mugstats')
def mugstats(bot, trigger):
    """$mugstats  (admin only) - economy overview via PM"""
    nick = trigger.nick
    if not _is_admin(bot, trigger.nick):
        if _pm_only(trigger):
            _pm(bot, nick, "🚫 Nice try. You're not an admin.")
        else:
            bot.reply("🚫 Nice try. You're not an admin.")
        return

    if not _pm_only(trigger):
        bot.say(f"📊 {nick}: sending economy stats to your PM.")

    with _data_lock:
        data = _load_data(bot)
        users = data.get('users', {})
        total_users = len(users)
        total_coins = sum(int(u.get('money', 0)) for u in users.values())
        top = sorted(users.values(), key=lambda u: -int(u.get('money', 0)))[:5]

    _pm(bot, nick, "📊 Mug Economy Stats:")
    _pm(bot, nick, f"👥 Users in DB: {total_users}")
    _pm(bot, nick, f"💰 Total coins in economy: {fmt_coins(total_coins)}")
    if top:
        _pm(bot, nick, f"🏆 Top 5: {_format_lb(top, start_rank=1)}")
    else:
        _pm(bot, nick, "🏆 No users yet.")


@module.commands('godmode')
def godmode(bot, trigger):
    """PM: $godmode [on|off] [nick] — toggle global god luck (admin only). Persists across reload/rehash/restart."""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You're not an admin.")
        return

    args = (trigger.group(2) or "").strip().split()

    # No args: show current status for self
    if not args:
        nk = normalize_key(trigger.nick)
        status = "✅ ON" if _godmode.has_godmode(bot, trigger.nick) else "❌ OFF"
        _pm(bot, trigger.nick, f"🔱 God mode for {trigger.nick}: {status}")
        # Also list anyone else with god mode
        others = [n for n in _godmode.list_nicks(bot) if n != nk]
        if others:
            _pm(bot, trigger.nick, f"👑 Others with god mode: {', '.join(others)}")
        return

    action = args[0].lower()
    target = args[1] if len(args) > 1 else str(trigger.nick)

    if action in ('on', 'enable', 'true', '1'):
        _godmode.set_godmode(bot, target, True)
        _pm(bot, trigger.nick, f"🔱 God mode ENABLED for {target}. 99% luck on mugging, casino, hunting, moo, and other luck games. Survives reload. 😈")
    elif action in ('off', 'disable', 'false', '0'):
        _godmode.set_godmode(bot, target, False)
        _pm(bot, trigger.nick, f"🔱 God mode DISABLED for {target}. Back to mortal luck. 💨")
    else:
        _pm(bot, trigger.nick, "Usage: $godmode [on|off] [nick] — no args = check status")


@module.commands('uncooldown')
def uncooldown(bot, trigger):
    """PM: $uncooldown <nick> — remove a user's 30-min flood lockout (admin only)"""
    if not _pm_only(trigger):
        bot.reply("📩 Use this in PM, not in-channel.")
        return
    if not _is_admin(bot, trigger.nick):
        _pm(bot, trigger.nick, "🚫 Nice try. You're not an admin.")
        return
    target = (trigger.group(2) or "").strip()
    if not target:
        _pm(bot, trigger.nick, "Usage: $uncooldown <nick>")
        return
    key = normalize_key(target)
    if key in _flood_lockout:
        _flood_clear(target)
        _pm(bot, trigger.nick, f"✅ Flood lockout cleared for {target}.")
    else:
        _pm(bot, trigger.nick, f"ℹ️ {target} is not currently locked out.")


# ============================================================
# ===================== ENABLE/DISABLE =======================
# ============================================================

@module.commands('mugtoggle')
def mugtoggle(bot, trigger):
    """$mugtoggle [on|off] or $mugtoggle #channel [on|off]  (admin only)"""
    if not _is_admin(bot, trigger.nick):
        bot.reply("🚫 Nice try. You're not an admin.")
        return

    args = (trigger.group(2) or "").strip().split()
    in_channel = str(trigger.sender).startswith('#')

    # Determine target channel and action
    channel = None
    action = None

    if in_channel:
        # Used in-channel: channel is implicit
        channel = str(trigger.sender)
        if args:
            action = args[0].lower()
    else:
        # Used in PM: first arg may be a #channel
        if args and args[0].startswith('#'):
            channel = args[0]
            if len(args) > 1:
                action = args[1].lower()
        elif args:
            action = args[0].lower()

    if not channel:
        _pm(bot, trigger.nick, "🎮 Usage (PM): $mugtoggle #channel [on|off]")
        _pm(bot, trigger.nick, "In-channel: just use $mugtoggle [on|off]")
        # Show all channel statuses
        toggles = _load_channel_toggles(bot)
        if toggles:
            for ch, enabled in sorted(toggles.items()):
                st = "ENABLED ✅" if enabled else "DISABLED 🔒"
                _pm(bot, trigger.nick, f"  {ch}: {st}")
        else:
            _pm(bot, trigger.nick, "  All channels: ENABLED ✅ (default)")
        return

    def _reply(text):
        if in_channel:
            bot.say(text)
        else:
            _pm(bot, trigger.nick, text)

    if action in ('on', 'enable', 'true', '1'):
        _set_channel_enabled(bot, channel, True)
        _reply(f"✅ Mug game ENABLED in {channel}. Let the chaos resume. 😈")
    elif action in ('off', 'disable', 'false', '0'):
        _set_channel_enabled(bot, channel, False)
        _reply(f"🔒 Mug game DISABLED in {channel}. All gameplay commands paused.")
    else:
        status = "ENABLED ✅" if _plugin_enabled(bot, channel) else "DISABLED 🔒"
        _reply(f"🎮 Mug game in {channel}: {status}")
        _reply("Usage: $mugtoggle [on|off]")


# ============================================================
# ========================= HELP (PM) =========================
# ============================================================

@module.commands('mughelp')
def mughelp(bot, trigger):
    nick = trigger.nick
    if trigger.sender.startswith('#'):
        bot.say(f"📨 {nick}: check your PM for mug game help.")

    lines = [
        "📖 Mug Game Help (FULL EMOJI CHAOS) 😈🪙",
        "",
        "💰 Economy:",
        "  • $coins — Get coins (cooldown). Richer players gain more (5–15% bonus, capped).",
        "  • $bal / $balance [nick] — Check coin balance.",
        "  • $give <nick> <amount> — Give coins to someone in the channel (5-min cooldown, 500k/day cap).",
        "  • Amounts accept commas: $bet 1,000,000 works.",
        "",
        "🎯 Bounties:",
        "  • $bounty <nick> <amount> — Put coins on someone’s head (costs you coins).",
        "  • $bounties — Show top bounty targets (auto-splits if too long).",
        "    - If someone mugs a bounty target, they instantly claim the entire bounty pool.",
        "",
        "🗡️ Mugging (ONE LINE):",
        "  • $mug <nick> / $rob <nick> — Attempt to mug someone (costs a small fee).",
        "    - Always outputs ONE line per mug attempt (no setup line).",
        "    - You can mug from anywhere, but the target must already be a known mug player.",
        "    - Success: steal a % of their coins (items can boost).",
        "    - Fail: you drop coins and they pick them up + extra cooldown.",
        "    - Critical fail: you lose a lot + go to jail (no mugging until free).",
        "    - Ultra-rare: mega heists + instant oops-jail events.",
        "    - Whale protection: if victim > 10,000 coins, max steal is 25% per mug.",
        "  • $jail — Check if you’re jailed and for how long.",
        "",
        "🎲 Gambling:",
        "  • $bet <amount> — Slot machine (Lucky Coin improves odds). Win = 2x payout.",
        "  • $roll <amount> [type] — Dice casino. Types: high (default, 2x), lucky7 (4x),",
        "      snake (30x), field (2x, 3x on 2/12), hardway (8x), yolo (15x). 60s cooldown.",
        "  • $penny — Penny slot machine. 1 coin per pull, win up to 5,000! 15s cooldown.",
        "  • $dollar — Dollar slot machine. 100 coins per pull, win up to 50,000! 30s cooldown.",
        "  • $roulette <amount> <bet> — Roulette. Bets: red/black/odd/even/high/low (2x),",
        "      1st/2nd/3rd (3x), or a number 0–36 (36x). 60s cooldown.",
        "  • $bj <amount> — Blackjack vs dealer. Then use $hit, $stand, or $dd (double down).",
        "      Blackjack pays 2.5x, regular win 2x. 60s cooldown.",
        "  • $holdem <amount> — Texas Hold'em heads-up vs dealer. Payouts scale by hand:",
        "      pair/two pair/trips 2x, straight 3x, flush 4x, full house 6x,",
        "      quads 12x, straight flush 25x, royal flush 50x. 60s cooldown.",
        "  ⚠️ Spam protection: 15+ commands in 60s = 30-minute casino lockout.",
        "",
        "🛒 Shop (PM-only):",
        "  • $shop — View shop items (PM).",
        "  • $buy <itemkey> — Buy an item (PM).",
        "  • $inv / $inventory — View inventory (PM).",
        "  • $use <itemkey> — Use a consumable item, e.g. bail (PM). Passive items can't be consumed.",
        "",
        "🧰 Items:",
        "  • mask — +mug success chance",
        "  • knucks — +steal % on success",
        "  • luckycoin — +$coins bonus, +$bet win chance",
        "  • vest — reduces stolen amount",
        "  • cloak — chance to dodge a successful mug",
        "  • banana — trap: chance mugger slips into disaster 🍌",
        "  • bail — Bail Bondsman: costs 5000, instantly frees you when used or bought while jailed. 🪓🎉",
        "",
        "🏆 Leaderboards:",
        "  • $top5 — Top 5 richest (10-min per-user cooldown, admins exempt)",
        "  • $top10 — Top 10 richest (10-min per-user cooldown, admins exempt)",
        "  • $highscore — All-time highest balance ever achieved by any player.",
        "",
        "🤖 Bot Player (glitchy):",
        "  • The bot is a mug game participant with its own wallet (seeded at 500k).",
        "  • Retaliation: 60% chance to counter-mug you 5–15s after you mug it.",
        "  • Proactive: randomly mugs top 5 richest players every 30–90 min in #mug.",
        "  • Bot uses normal odds (50% success, 5–15% steal, 10–25% fail loss).",
        "  • Bot does NOT have god mode — it can lose too!",
        "",
        "📢 Channel Rules:",
        "  • The mug game is disabled by default. An admin must $mugtoggle on.",
        "  • Home channel: #mug — disabled messages direct players there.",
        "  • Auto-mode in #mug: 500 coins = +v, 10M = +h, 1B = +o.",
        "  • Admins are exempt from all cooldown timers.",
        "",
        "🛠️ PM Admin (if you're allowed):",
        "  • $mugadd <nick> <amount> — add coins",
        "  • $mugset <nick> <amount> — set balance",
        "  • $mugtake <nick> <amount> — remove coins",
        "  • $mugreset — FULL RESET (everyone to 0, clears inv/cooldowns/bounties)",
        "  • $mugcleardb confirm — WIPE DB (deletes all player records)",
        "  • $mugmerge <nick> [--dry] — merge duplicate records for normalized nick (admin only; use --dry to preview)",
        "  • $mugdup <nick> — list stored records that normalize to the same nick (admin only)",
        "  • $mugclearbounty <nick> — remove a bounty (coins are NOT refunded) (admin only)",
        "  • $mugstats — PM economy overview: user count, top 5, total coins (admin only)",
        "  • $godmode [on|off] [nick] — toggle global 99% luck (casino, mug, hunt, moo); persists across reload (PM, admin only)",
        "  • $uncooldown <nick> — clear a user's 30-min flood lockout (PM, admin only)",
        "  • $mugtoggle [on|off] — enable/disable the mug game in the current channel (in-channel or PM)",
        "    - In PM: $mugtoggle #channel [on|off] — target a specific channel",
        "    - With no args in PM: shows status of all channels",
        "",
        "🌲 Hunting Game Addon (hunt.py):",
        "  • $shoot [animal] — Fire at active wildlife in channel",
        "  • $huntable — View current wildlife and clearance in channel",
        "  • $reloadgun / $loadgun — Reload magazine / clear mechanical jam",
        "  • $gun — View rifle condition, lubrication, accuracy & attachments",
        "  • $grease / $repair — Restore lubrication and rifle condition",
        "  • $huntshop — Hunting store (ammo, scopes, upgrades, food)",
        "  • $permits — View and buy licenses (Game Bird, Medium, Big, Trophy, Legendary)",
        "  • $bag / $sell all — Check hunting bag and sell trophies for coins",
        "  • $feed — Feed protected animals (stray cats & dogs) to rescue them",
        "  • $mystats — Lifetime harvests, rank, and hunter stats",
        "  • $conditions — Current weather and accuracy modifiers",
        "  • $guide [species] — Wildlife field guide specs and requirements",
        "  • $hunttoggle [on|off] — Enable/disable hunting per channel (ops/admin)",
        "  • $hunthelp — Full in-depth hunting reference",
        "",
        "😎 Pro tip: If the channel is bullying you, buy bananas and place bounties like a supervillain.",
    ]

    for line in lines:
        _pm(bot, nick, line)


# ============================================================
# ===============  AUTO-MODE (COIN-BASED +v/+h/+o)  ==========
# ============================================================

# Privilege bitmask constants (Sopel convention)
_PRIV_VOICE  = 1       # +v
_PRIV_HALFOP = 2       # +h
_PRIV_OP     = 4       # +o
_PRIV_ADMIN  = 8       # +a
_PRIV_OWNER  = 16      # +q


def _mode_get_privs(bot, channel, nick):
    """Return the integer privilege bitmask for nick in channel (0 if absent)."""
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


def _mode_nick_in_channel(bot, channel, nick_lower):
    """Check if a nick is currently in the channel."""
    try:
        chan = bot.channels.get(channel)
        if not chan:
            return False
        privs = getattr(chan, 'privileges', None) or {}
        return any(k.lower() == nick_lower for k in privs)
    except Exception:
        return False


def _mode_get_display_nick(nick_key):
    """Get the display nick from loaded data, falling back to key."""
    try:
        if _data and isinstance(_data, dict):
            rec = _data.get('users', {}).get(nick_key, {})
            if isinstance(rec, dict) and rec.get('nick'):
                return rec['nick']
    except Exception:
        pass
    return nick_key


def _mode_desired(coins):
    """Return the target (mode_char, priv_bit) for a coin balance, or None."""
    if coins >= OP_THRESHOLD:
        return ('o', _PRIV_OP)
    if coins >= HALFOP_THRESHOLD:
        return ('h', _PRIV_HALFOP)
    if coins >= VOICE_THRESHOLD:
        return ('v', _PRIV_VOICE)
    return None


def _mode_sync_nick(bot, channel, nick, coins):
    """Set the correct mode for nick based on coins. Only promotes or strips +v on broke users.
    Never demotes +o/+h that came from the access list."""
    privs = _mode_get_privs(bot, channel, nick)
    # If they have admin (+a) or owner (+q), never touch them
    if privs & (_PRIV_ADMIN | _PRIV_OWNER):
        return
    desired = _mode_desired(coins)
    desired_bit = desired[1] if desired else 0

    # If they already have a mode HIGHER than what coins warrant,
    # it came from the access list / ChanServ — leave them alone.
    if (privs & _PRIV_OP) and desired_bit < _PRIV_OP:
        return
    if (privs & _PRIV_HALFOP) and desired_bit < _PRIV_HALFOP:
        return

    # Managed modes we control: v, h, o
    managed = [('v', _PRIV_VOICE), ('h', _PRIV_HALFOP), ('o', _PRIV_OP)]
    add_modes = ''
    remove_modes = ''
    for char, bit in managed:
        has = (privs & bit) != 0
        want = desired is not None and desired[0] == char
        if want and not has:
            add_modes += char
        elif not want and has:
            remove_modes += char
    mode_str = ''
    if remove_modes:
        mode_str += '-' + remove_modes
    if add_modes:
        mode_str += '+' + add_modes
    if mode_str:
        # Each mode flag needs the nick as a parameter
        count = len(remove_modes) + len(add_modes)
        params = ' '.join([nick] * count)
        bot.write(['MODE', channel, mode_str, params])
        LOG.info('auto-mode: %s %s in %s (coins=%s)', mode_str, nick, channel, coins)


def _voice_sweep_all(bot):
    """Check all players in all managed channels and sync modes."""
    if not VOICE_ENABLED or not _data:
        return
    # Snapshot under the lock; MODE writes below must not hold it
    with _data_lock:
        users = dict(_data.get('users', {}))
    for channel in VOICE_CHANNELS:
        for nick_key, rec in users.items():
            if not isinstance(rec, dict):
                continue
            if nick_key in VOICE_EXEMPT_NICKS:
                continue
            display = _mode_get_display_nick(nick_key)
            if not _mode_nick_in_channel(bot, channel, nick_key):
                continue
            coins = rec.get('money', 0)
            _mode_sync_nick(bot, channel, display, coins)


def _start_voice_sweep_thread(bot):
    """Start the background thread that periodically syncs modes."""
    _voice_sweep_stop.clear()
    t = threading.Thread(target=_voice_sweep_loop, args=(bot,), daemon=True)
    t.start()


def _voice_sweep_loop(bot):
    """Runs in a background thread. Sweeps every VOICE_CHECK_INTERVAL."""
    _voice_sweep_stop.wait(30)  # let the bot join channels first
    while not _voice_sweep_stop.is_set():
        try:
            _voice_sweep_all(bot)
        except Exception:
            LOG.exception('auto-mode: sweep failed')
        _voice_sweep_stop.wait(VOICE_CHECK_INTERVAL)


@module.event('JOIN')
@module.rule('.*')
@module.priority('low')
def voice_on_join(bot, trigger):
    """When someone joins a managed channel, set their mode if they qualify."""
    if not VOICE_ENABLED:
        return
    try:
        if not trigger.sender or not trigger.sender.startswith('#'):
            return
        if trigger.sender.lower() not in VOICE_CHANNELS:
            return
        if trigger.nick.lower() == bot.nick.lower():
            return
        if trigger.nick.lower() in VOICE_EXEMPT_NICKS:
            return
        # Small delay so Sopel's privilege tracking catches up
        time.sleep(2)
        nick_key = normalize_key(trigger.nick)
        data = _load_data(bot)
        rec = data.get('users', {}).get(nick_key, {})
        coins = rec.get('money', 0) if isinstance(rec, dict) else 0
        _mode_sync_nick(bot, trigger.sender, trigger.nick, coins)
    except (AttributeError, KeyError, TypeError):
        pass


# ============================================================
# ============== PLUGIN LIFECYCLE HANDLER =====================
# ============================================================

def _cleanup_on_exit():
    """Graceful cleanup when the process exits."""
    _bot_proactive_stop.set()
    _voice_sweep_stop.set()


# Register cleanup handler for graceful shutdown
atexit.register(_cleanup_on_exit)
