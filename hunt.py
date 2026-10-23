# -*- coding: utf-8 -*-
"""
hunt.py — Persistent IRC Hunting Game for Sopel / ibot
Modeled after IRCHunt v5.4.1 mechanics.

Features:
  - 16 North American wildlife species (Starter, Bird, Medium, Big, Trophy, Legendary, Friendly)
  - 5 Rarity tiers (Common, Uncommon, Rare, Epic, Legendary) with scaled HP, XP, and loot
  - Dynamic Field Conditions / Weather affecting accuracy and rare encounters
  - Shooting engine with Steady Aim (+9% accuracy per miss), ammo, reload, and jams
  - Weapon maintenance (Gun Condition, Lubrication, $grease, $repair)
  - Progression permits (Game Bird, Medium Game, Big Game, Trophy Game, Legendary)
  - Shop with gun upgrades, scopes, silencers, insurance, extra mags, calls, and food
  - Friendly rescue encounters (Stray Cat & Lost Dog: protected species, feed to rescue)
  - Scheduled Legendary Bison encounter
  - Dual-Mode economy: Seamlessly hooks into mug.py (shared coins/inventory) if present,
    or runs 100% self-contained in standalone mode using Spendable XP.

Commands:
  Hunting:   $shoot [animal], $reload, $huntable, $track [animal]
  Gear:      $gun, $shop [buy <id>], $permits [buy <key>], $grease, $repair
  Stats:     $mystats, $bag, $sell <id|all>, $feed [food] [animal], $conditions, $guide [animal]
  Boards:    $top [exp|kills|trophies|friends]
  Admin:     $spawn <animal> [rarity], $rearm [nick]

Database: ~/.sopel/hunt.db (SQLite)
"""
from __future__ import annotations

import os
import contextlib
import sys
import time
import math
import json
import random
import sqlite3
import threading
import datetime
import logging
from typing import Optional, Tuple, Dict, Any, List

from sopel import plugin
from sopel.config.types import StaticSection, ValidatedAttribute

LOG = logging.getLogger('sopel.modules.hunt')
_sysrand = random.SystemRandom()

# ──────────────────────────────────────────────────────────────
# Config Section
# ──────────────────────────────────────────────────────────────
class HuntSection(StaticSection):
    enabled = ValidatedAttribute('enabled', bool, default=True)
    channels = ValidatedAttribute('channels', str, default='')
    spawn_min = ValidatedAttribute('spawn_min', int, default=360)  # seconds (6 min)
    spawn_max = ValidatedAttribute('spawn_max', int, default=720)  # seconds (12 min)


def configure(config):
    config.define_section('hunt', HuntSection)
    config.hunt.configure_setting('enabled', 'Enable the hunting game? (true/false)')
    config.hunt.configure_setting('channels', 'Channels where animals spawn (comma-separated)')


# ──────────────────────────────────────────────────────────────
# Colors & Formatting Constants
# ──────────────────────────────────────────────────────────────
BOLD  = '\x02'
RESET = '\x0f'
GREEN = '\x0303'
RED   = '\x0304'
GOLD  = '\x0307'
CYAN  = '\x0311'
BLUE  = '\x0312'
GREY  = '\x0314'
PURPLE = '\x0306'
PINK  = '\x0313'

RARITY_COLORS = {
    'common':    GREY,
    'uncommon':  GREEN,
    'rare':      CYAN,
    'epic':      PURPLE,
    'legendary': GOLD,
    'friendly':  PINK,
}

RARITIES = {
    'common':    {'label': 'COMMON',    'hp_mul': 1.00, 'xp_mul': 1.00, 'loot_chance': 0.35, 'trophy': 2},
    'uncommon':  {'label': 'UNCOMMON',  'hp_mul': 1.15, 'xp_mul': 1.35, 'loot_chance': 0.55, 'trophy': 4},
    'rare':      {'label': 'RARE',      'hp_mul': 1.35, 'xp_mul': 2.00, 'loot_chance': 0.80, 'trophy': 8},
    'epic':      {'label': 'EPIC',      'hp_mul': 1.75, 'xp_mul': 3.40, 'loot_chance': 1.00, 'trophy': 16},
    'legendary': {'label': 'LEGENDARY', 'hp_mul': 2.35, 'xp_mul': 6.50, 'loot_chance': 1.00, 'trophy': 40},
}

RARITY_ADJECTIVES = {
    'common':    [''],
    'uncommon':  ['Large ', 'Mature '],
    'rare':      ['Old ', 'Prize '],
    'epic':      ['Massive ', 'Giant '],
    'legendary': ['Mythic ', 'Ancient '],
}

CONDITIONS = {
    'clear':       {'name': 'Clear Skies', 'acc_mod': 2,  'rare_bonus': 0},
    'overcast':    {'name': 'Overcast',    'acc_mod': 0,  'rare_bonus': 2},
    'light_rain':  {'name': 'Light Rain',  'acc_mod': -2, 'rare_bonus': 4},
    'dense_fog':   {'name': 'Dense Fog',   'acc_mod': -6, 'rare_bonus': 8},
    'high_wind':   {'name': 'High Wind',   'acc_mod': -4, 'rare_bonus': 3},
    'storm':       {'name': 'Storm Front', 'acc_mod': -7, 'rare_bonus': 12},
    'golden_hour': {'name': 'Golden Hour', 'acc_mod': 4,  'rare_bonus': 10},
}

PERMITS = {
    'game_bird': {
        'name': 'Game Bird Permit', 'cost': 150, 'min_level': 3,
        'desc': 'Covers higher-tier bird hunts such as Canada goose and wild turkey.'
    },
    'medium_game': {
        'name': 'Medium Game Permit', 'cost': 400, 'min_level': 5,
        'desc': 'Covers whitetail deer, wild boar and pronghorn.'
    },
    'big_game': {
        'name': 'Big Game Permit', 'cost': 900, 'min_level': 10,
        'desc': 'Covers black bear, elk and caribou.'
    },
    'trophy_game': {
        'name': 'Trophy Game Permit', 'cost': 1800, 'min_level': 16,
        'desc': 'Covers moose and bison.'
    },
    'legendary': {
        'name': 'Legendary Endorsement', 'cost': 3200, 'min_level': 22,
        'desc': 'Required before firing on any Legendary encounter.'
    },
}

WILDLIFE = {
    # Open small game (no permit required)
    'squirrel': {
        'name': 'Squirrel', 'type': 'hunt', 'min_hp': 1, 'max_hp': 3, 'base_xp': 10,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'epic',
        'stay_min': 240, 'stay_max': 480, 'spawn_weight': 25,
        'loot': [('Squirrel Tail', 15), ('Small Pelt', 20)],
        'spawn_verbs': ['CHITTER! A squirrel darts out from the brush!', 'RUSTLE! A squirrel scrambles into the clearing!']
    },
    'rabbit': {
        'name': 'Rabbit', 'type': 'hunt', 'min_hp': 2, 'max_hp': 4, 'base_xp': 14,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'epic',
        'stay_min': 240, 'stay_max': 480, 'spawn_weight': 25,
        'loot': [('Rabbit Foot', 25), ('Rabbit Pelt', 20)],
        'spawn_verbs': ['THUMP! A rabbit bolts from the grass!', 'A cottontail freezes at the edge of the clearing!', 'RUSTLE! A rabbit hops into view!']
    },
    'duck': {
        'name': 'Duck', 'type': 'hunt', 'min_hp': 3, 'max_hp': 7, 'base_xp': 25,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'epic',
        'stay_min': 300, 'stay_max': 540, 'spawn_weight': 18,
        'loot': [('Duck Feathers', 30), ('Duck Band', 45)],
        'spawn_verbs': ['QUACK! A mallard splashes into the pond!', 'A duck glides down into the reeds!', 'QUACK! QUACK! A duck splashes into the hunting grounds!', 'SPLASH! A noisy duck has landed in the area!']
    },
    'pheasant': {
        'name': 'Pheasant', 'type': 'hunt', 'min_hp': 3, 'max_hp': 6, 'base_xp': 28,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'epic',
        'stay_min': 300, 'stay_max': 540, 'spawn_weight': 18,
        'loot': [('Pheasant Feathers', 35), ('Pheasant Tail', 50)],
        'spawn_verbs': ['WHIRR! A pheasant flushes from the grass!', 'A rooster pheasant cackles and breaks into the open!']
    },
    # Game Bird Permit
    'canada_goose': {
        'name': 'Canada Goose', 'type': 'hunt', 'min_hp': 5, 'max_hp': 10, 'base_xp': 40,
        'min_level': 3, 'min_gun': 1, 'permit': 'game_bird', 'max_rarity': 'legendary',
        'stay_min': 360, 'stay_max': 660, 'spawn_weight': 14,
        'loot': [('Goose Feathers', 45), ('Goose Band', 70)],
        'spawn_verbs': ['HONK! HONK! A Canada goose glides into the area!', 'WINGBEATS thunder as a Canada goose comes into range!']
    },
    'wild_turkey': {
        'name': 'Wild Turkey', 'type': 'hunt', 'min_hp': 6, 'max_hp': 12, 'base_xp': 48,
        'min_level': 4, 'min_gun': 1, 'permit': 'game_bird', 'max_rarity': 'legendary',
        'stay_min': 420, 'stay_max': 720, 'spawn_weight': 12,
        'loot': [('Turkey Feathers', 50), ('Turkey Spur', 80)],
        'spawn_verbs': ['GOBBLE-GOBBLE! A wild turkey struts into view!', 'A distant GOBBLE answers from the timber!']
    },
    # Medium Game Permit
    'whitetail_deer': {
        'name': 'Whitetail Deer', 'type': 'hunt', 'min_hp': 8, 'max_hp': 16, 'base_xp': 65,
        'min_level': 5, 'min_gun': 2, 'permit': 'medium_game', 'max_rarity': 'legendary',
        'stay_min': 480, 'stay_max': 840, 'spawn_weight': 10,
        'loot': [('Deer Hide', 80), ('Deer Antler', 120)],
        'spawn_verbs': ['RUSTLE... a whitetail emerges from the woods!']
    },
    'wild_boar': {
        'name': 'Wild Boar', 'type': 'hunt', 'min_hp': 12, 'max_hp': 22, 'base_xp': 85,
        'min_level': 6, 'min_gun': 2, 'permit': 'medium_game', 'max_rarity': 'legendary',
        'stay_min': 540, 'stay_max': 900, 'spawn_weight': 9,
        'loot': [('Boar Hide', 100), ('Boar Tusk', 150)],
        'spawn_verbs': ['SNORT! A wild boar tears into the clearing!', 'CRASH! A stubborn wild boar bursts through the brush!']
    },
    'pronghorn': {
        'name': 'Pronghorn', 'type': 'hunt', 'min_hp': 10, 'max_hp': 20, 'base_xp': 95,
        'min_level': 7, 'min_gun': 2, 'permit': 'medium_game', 'max_rarity': 'legendary',
        'stay_min': 540, 'stay_max': 900, 'spawn_weight': 8,
        'loot': [('Pronghorn Hide', 110), ('Pronghorn Horn', 160)],
        'spawn_verbs': ['A wary pronghorn stops on the ridge!', 'DUST rises as a pronghorn crosses the hunting grounds!']
    },
    # Big Game Permit
    'black_bear': {
        'name': 'Black Bear', 'type': 'hunt', 'min_hp': 18, 'max_hp': 34, 'base_xp': 135,
        'min_level': 10, 'min_gun': 3, 'permit': 'big_game', 'max_rarity': 'legendary',
        'stay_min': 600, 'stay_max': 1020, 'spawn_weight': 6,
        'loot': [('Bear Hide', 150), ('Bear Claw', 220)],
        'spawn_verbs': ['HEAVY FOOTSTEPS... a powerful black bear pads into view!']
    },
    'elk': {
        'name': 'Elk', 'type': 'hunt', 'min_hp': 24, 'max_hp': 44, 'base_xp': 190,
        'min_level': 12, 'min_gun': 3, 'permit': 'big_game', 'max_rarity': 'legendary',
        'stay_min': 660, 'stay_max': 1080, 'spawn_weight': 5,
        'loot': [('Elk Hide', 200), ('Elk Antler', 280)],
        'spawn_verbs': ['THUD! A heavy elk steps into the open!']
    },
    'caribou': {
        'name': 'Caribou', 'type': 'hunt', 'min_hp': 26, 'max_hp': 48, 'base_xp': 210,
        'min_level': 13, 'min_gun': 3, 'permit': 'big_game', 'max_rarity': 'legendary',
        'stay_min': 720, 'stay_max': 1140, 'spawn_weight': 4,
        'loot': [('Caribou Hide', 220), ('Caribou Antler', 300)],
        'spawn_verbs': ['CLICK-CLACK... a magnificent caribou moves across the tundra!']
    },
    # Trophy Game Permit
    'moose': {
        'name': 'Moose', 'type': 'hunt', 'min_hp': 38, 'max_hp': 70, 'base_xp': 280,
        'min_level': 16, 'min_gun': 4, 'permit': 'trophy_game', 'max_rarity': 'legendary',
        'stay_min': 900, 'stay_max': 1320, 'spawn_weight': 3,
        'loot': [('Moose Hide', 300), ('Moose Antler', 450)],
        'spawn_verbs': ['THUD... a huge moose steps into the clearing!', 'A deep grunt carries across the marsh as a moose appears!']
    },
    'bison': {
        'name': 'Bison', 'type': 'hunt', 'min_hp': 48, 'max_hp': 88, 'base_xp': 360,
        'min_level': 18, 'min_gun': 5, 'permit': 'trophy_game', 'max_rarity': 'epic',
        'stay_min': 960, 'stay_max': 1440, 'spawn_weight': 2,
        'loot': [('Bison Hide', 400), ('Bison Horn', 600)],
        'spawn_verbs': ['THE EARTH SHAKES! A massive bison lumbers into the plain!']
    },
    # Legendary Endgame Event
    'mythic_bison': {
        'name': 'Mythic Bison', 'type': 'hunt', 'min_hp': 141, 'max_hp': 141, 'base_xp': 2340,
        'min_level': 22, 'min_gun': 7, 'permit': 'legendary', 'max_rarity': 'legendary',
        'stay_min': 1500, 'stay_max': 2100, 'spawn_weight': 0,
        'loot': [('Legendary Bison Pelt', 2500), ('Mythic Bison Horn', 3500)],
        'spawn_verbs': ['[SPECIAL ENCOUNTER] THE GROUND TREMBLES... a once-in-hours Legendary Bison has entered the hunting grounds!']
    },
    # Friendly / Protected species
    'stray_cat': {
        'name': 'Stray Cat', 'type': 'friendly', 'min_hp': 1, 'max_hp': 2, 'base_xp': 22,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'rare',
        'stay_min': 480, 'stay_max': 900, 'spawn_weight': 4,
        'loot': [],
        'spawn_verbs': ['MEOW! A friendly stray cat wanders out from behind the trees!']
    },
    'lost_dog': {
        'name': 'Lost Dog', 'type': 'friendly', 'min_hp': 2, 'max_hp': 4, 'base_xp': 30,
        'min_level': 1, 'min_gun': 1, 'permit': None, 'max_rarity': 'rare',
        'stay_min': 540, 'stay_max': 960, 'spawn_weight': 4,
        'loot': [],
        'spawn_verbs': ['WOOF! A friendly lost dog wags its tail at the clearing edge!']
    }
}

SHOP_ITEMS = {
    1: {'name': '1 Round', 'cost': 2, 'min_level': 1, 'desc': 'Adds 1 round to current mag.'},
    2: {'name': 'Full Mag', 'cost': 10, 'min_level': 1, 'desc': 'Refills current mag to max.'},
    3: {'name': 'Xtra Mag', 'cost': 70, 'min_level': 2, 'desc': 'Adds 1 spare mag to carry (max 5).'},
    4: {'name': 'Mag Upg', 'cost': 275, 'min_level': 3, 'desc': '+1 round to mag capacity (max 10 rounds).'},
    5: {'name': 'Gun Return', 'cost': 60, 'min_level': 1, 'desc': 'Returns confiscated gun.'},
    6: {'name': 'Gun Upg', 'cost': 700, 'min_level': 4, 'desc': '+1 Gun Level, boosting damage and accuracy (max L10).'},
    7: {'name': 'Silencer', 'cost': 120, 'min_level': 2, 'desc': 'Prevents gunfire from scaring targets for 24h.'},
    8: {'name': 'Insurance', 'cost': 125, 'min_level': 2, 'desc': 'Protects against gun confiscation for 24h.'},
    9: {'name': 'Food Box', 'cost': 180, 'min_level': 2, 'desc': 'Required container to carry animal food.'},
    10: {'name': 'Bread x15', 'cost': 30, 'min_level': 1, 'desc': '15 feeds at 15% friendship chance (req Food Box).'},
    11: {'name': 'Popcorn x30', 'cost': 70, 'min_level': 2, 'desc': '30 feeds at 30% friendship chance (req Food Box).'},
    12: {'name': 'WildFeed x50', 'cost': 140, 'min_level': 3, 'desc': '50 feeds at 50% friendship chance (req Food Box).'},
    13: {'name': 'Scope Upg', 'cost': 425, 'min_level': 3, 'desc': 'Permanent +3% base accuracy per level (max 5).'},
    14: {'name': 'Action Tune', 'cost': 375, 'min_level': 3, 'desc': 'Permanent jam resistance upgrade (max 5).'},
    15: {'name': 'Gun Grease', 'cost': 15, 'min_level': 1, 'desc': '1 grease tube. Restores 100% gun lubrication and prevents jams.'},
    16: {'name': 'Repair Kit', 'cost': 60, 'min_level': 2, 'desc': '1 repair kit. Restores 35% weapon condition.'},
    17: {'name': 'Tracker Upg', 'cost': 450, 'min_level': 4, 'desc': 'Fieldcraft upgrade: boosts tracking & accuracy.'},
    18: {'name': 'Game Call', 'cost': 60, 'min_level': 2, 'desc': '1 game call. Biases next spawn toward selected species.'},
}

# ──────────────────────────────────────────────────────────────
# Database Layer
# ──────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser('~/.sopel/hunt.db')
_db_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db_lock, _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS hunters (
                nick TEXT PRIMARY KEY COLLATE NOCASE,
                level INTEGER DEFAULT 1,
                total_xp INTEGER DEFAULT 0,
                spendable_xp INTEGER DEFAULT 0,
                total_kills INTEGER DEFAULT 0,
                trophy_score INTEGER DEFAULT 0,
                rare_kills INTEGER DEFAULT 0,
                epic_kills INTEGER DEFAULT 0,
                legendary_kills INTEGER DEFAULT 0,
                friends INTEGER DEFAULT 0,
                tracking_level INTEGER DEFAULT 1,
                tracking_xp INTEGER DEFAULT 0,
                gun_level INTEGER DEFAULT 1,
                mag_capacity INTEGER DEFAULT 6,
                ammo INTEGER DEFAULT 6,
                extra_mags INTEGER DEFAULT 1,
                scope_level INTEGER DEFAULT 0,
                action_tune INTEGER DEFAULT 0,
                tracker_level INTEGER DEFAULT 0,
                gun_condition REAL DEFAULT 100.0,
                gun_lubrication REAL DEFAULT 100.0,
                gun_jammed INTEGER DEFAULT 0,
                gun_confiscated INTEGER DEFAULT 0,
                has_food_box INTEGER DEFAULT 0,
                bread INTEGER DEFAULT 0,
                popcorn INTEGER DEFAULT 0,
                wildfeed INTEGER DEFAULT 0,
                grease_tubes INTEGER DEFAULT 1,
                repair_kits INTEGER DEFAULT 0,
                silencer_until REAL DEFAULT 0.0,
                insurance_until REAL DEFAULT 0.0,
                consecutive_misses INTEGER DEFAULT 0,
                current_streak INTEGER DEFAULT 0,
                registered_at TEXT
            );

            CREATE TABLE IF NOT EXISTS permits (
                nick TEXT COLLATE NOCASE,
                permit_key TEXT,
                purchased_at TEXT,
                PRIMARY KEY (nick, permit_key)
            );

            CREATE TABLE IF NOT EXISTS bag (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nick TEXT COLLATE NOCASE,
                item_name TEXT,
                species TEXT,
                rarity TEXT,
                sell_value INTEGER,
                acquired_at TEXT
            );

            CREATE TABLE IF NOT EXISTS hunting_globals (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        try:
            conn.execute("UPDATE hunters SET extra_mags = 1 WHERE extra_mags = 0 AND total_kills = 0")
        except Exception:
            pass
        conn.commit()


# ──────────────────────────────────────────────────────────────
# Channel Toggle & Permission Management
# ──────────────────────────────────────────────────────────────
PLUGIN_NAME = 'hunt'
_channel_toggles: Optional[Dict[str, bool]] = None


def _get_prefix(bot=None) -> str:
    """Get the command prefix configured in ibot / Sopel (stripping regex escapes)."""
    if bot and hasattr(bot, 'config') and hasattr(bot.config, 'core'):
        p = getattr(bot.config.core, 'prefix', None)
        if p:
            p_str = str(p).strip()
            if p_str.startswith('\\'):
                p_str = p_str[1:]
            if p_str:
                return p_str
        hp = getattr(bot.config.core, 'help_prefix', None)
        if hp:
            return str(hp).strip()
    return '$'


def _load_channel_toggles(bot=None) -> Dict[str, bool]:
    """Lazily load per-channel toggles from bot.db and hunting_globals in sqlite."""
    global _channel_toggles
    if _channel_toggles is not None:
        return _channel_toggles

    toggles: Dict[str, bool] = {}
    if bot and hasattr(bot, 'db') and bot.db:
        try:
            val = bot.db.get_plugin_value(PLUGIN_NAME, 'channel_toggles')
            if isinstance(val, dict):
                toggles = {str(k).lower(): bool(v) for k, v in val.items()}
        except Exception:
            pass

    if not toggles:
        try:
            with _db_lock, _get_conn() as conn:
                row = conn.execute("SELECT value FROM hunting_globals WHERE key = 'channel_toggles'").fetchone()
                if row and row['value']:
                    val = json.loads(row['value'])
                    if isinstance(val, dict):
                        toggles = {str(k).lower(): bool(v) for k, v in val.items()}
        except Exception:
            pass

    _channel_toggles = toggles
    return _channel_toggles


def _set_channel_enabled(bot, channel: str, enabled: bool):
    """Set the per-channel toggle (persisted across restarts in bot.db and hunt.db)."""
    toggles = _load_channel_toggles(bot)
    chan_key = str(channel).lower().strip()
    toggles[chan_key] = bool(enabled)

    if bot and hasattr(bot, 'db') and bot.db:
        try:
            bot.db.set_plugin_value(PLUGIN_NAME, 'channel_toggles', toggles)
        except Exception:
            pass

    try:
        with _db_lock, _get_conn() as conn:
            conn.execute(
                "INSERT INTO hunting_globals (key, value) VALUES ('channel_toggles', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(toggles),)
            )
            conn.commit()
    except Exception as e:
        LOG.exception("Failed to persist channel toggles to DB: %s", e)


def _plugin_enabled(bot, channel: Optional[str] = None) -> bool:
    """Return True if hunting is enabled for *channel*.

    If channel is None or PM context (doesn't start with '#'), return True.
    For IRC channels, hunting is DISABLED BY DEFAULT in ALL channels until explicitly enabled.
    """
    try:
        if hasattr(bot, 'config') and hasattr(bot.config, 'hunt') and not bot.config.hunt.enabled:
            return False
    except Exception:
        pass

    if not channel or not str(channel).startswith('#'):
        return True

    toggles = _load_channel_toggles(bot)
    chan_key = str(channel).lower().strip()
    if chan_key in toggles:
        return toggles[chan_key]

    try:
        if hasattr(bot, 'config') and hasattr(bot.config, 'hunt'):
            cfg_chans = getattr(bot.config.hunt, 'channels', '')
            if cfg_chans:
                allowed = [c.strip().lower() for c in cfg_chans.split(',') if c.strip()]
                if chan_key in allowed:
                    return True
    except Exception:
        pass

    return False


def _get_enabled_channels(bot=None) -> List[str]:
    """Return list of channels where hunting is currently active and enabled."""
    toggles = _load_channel_toggles(bot)
    enabled = set(ch for ch, en in toggles.items() if en and ch.startswith('#'))
    try:
        if bot and hasattr(bot, 'config') and hasattr(bot.config, 'hunt'):
            cfg_chans = getattr(bot.config.hunt, 'channels', '')
            if cfg_chans:
                for c in cfg_chans.split(','):
                    c = c.strip().lower()
                    if c.startswith('#') and toggles.get(c) is not False:
                        enabled.add(c)
    except Exception:
        pass
    return sorted(list(enabled))


def _is_bot_admin(bot, trigger) -> bool:
    """Check if the trigger user is recognized as a bot admin or owner in Sopel / ibot."""
    if getattr(trigger, 'admin', False):
        return True

    nick = (getattr(trigger, 'nick', '') or '').lower().strip()
    account = (getattr(trigger, 'account', None) or '').lower().strip()

    core = getattr(getattr(bot, 'config', None), 'core', None)
    if core:
        owner = getattr(core, 'owner', None)
        if owner:
            owner_str = str(owner).lower().strip()
            if nick == owner_str or (account and account == owner_str):
                return True

        admins = getattr(core, 'admins', None)
        if admins:
            for admin in admins:
                admin_str = str(admin).lower().strip()
                if nick == admin_str or (account and account == admin_str):
                    return True

    return False


def _can_manage_channel(bot, trigger, channel: str) -> bool:
    """Check if trigger user has permission to toggle hunting in channel.
    Authorized if user is a bot admin/owner in Sopel/ibot, or channel operator (+o/+a/+q).
    """
    if _is_bot_admin(bot, trigger):
        return True

    # Channel operator privileges (+o, +a, +q) in Sopel/ibot
    if hasattr(bot, 'channels'):
        target_ch = channel if channel in bot.channels else channel.lower()
        if target_ch in bot.channels:
            chan_obj = bot.channels[target_ch]
            privs = getattr(chan_obj, 'privileges', {}).get(trigger.nick, 0)
            if privs >= 4:  # OP (4), ADMIN (8), OWNER (16)
                return True

    return False


def _check_channel_enabled(bot, trigger) -> bool:
    """Enforce channel enablement for gameplay and channel commands."""
    sender = str(trigger.sender)
    if not sender.startswith('#'):
        return True

    p = _get_prefix(bot)
    if not _plugin_enabled(bot, sender):
        bot.reply(f"🔒 Hunting is {RED}DISABLED{RESET} in {sender}. A channel op or admin can enable it with: {BOLD}{p}hunttoggle on{RESET}")
        return False
    return True


def _get_hunter(nick: str) -> Optional[sqlite3.Row]:
    with _db_lock, _get_conn() as conn:
        return conn.execute("SELECT * FROM hunters WHERE nick=?", (nick,)).fetchone()


def _register_hunter(nick: str) -> bool:
    ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock, _get_conn() as conn:
        try:
            conn.execute("""
                INSERT INTO hunters (nick, registered_at) VALUES (?, ?)
            """, (nick, ts))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def _get_or_create_hunter(nick: str) -> sqlite3.Row:
    h = _get_hunter(nick)
    if not h:
        _register_hunter(nick)
        h = _get_hunter(nick)
    return h


def _get_permits(nick: str) -> List[str]:
    with _db_lock, _get_conn() as conn:
        rows = conn.execute("SELECT permit_key FROM permits WHERE nick=?", (nick,)).fetchall()
        return [r['permit_key'] for r in rows]


def _has_permit(nick: str, permit_key: str) -> bool:
    if not permit_key:
        return True
    with _db_lock, _get_conn() as conn:
        row = conn.execute("SELECT 1 FROM permits WHERE nick=? AND permit_key=?", (nick, permit_key)).fetchone()
        return row is not None


def _grant_permit(nick: str, permit_key: str):
    ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock, _get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO permits (nick, permit_key, purchased_at) VALUES (?, ?, ?)",
                     (nick, permit_key, ts))
        conn.commit()


def _add_to_bag(nick: str, item_name: str, species: str, rarity: str, sell_value: int):
    ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock, _get_conn() as conn:
        conn.execute("""
            INSERT INTO bag (nick, item_name, species, rarity, sell_value, acquired_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nick, item_name, species, rarity, sell_value, ts))
        conn.commit()


# ──────────────────────────────────────────────────────────────
# Economy & Mug Game Bridge
# ──────────────────────────────────────────────────────────────
_MUG_MODULE_NAMES = ('ibot_plugins.mug', 'sopel_modules.mug', 'scripts.mug', 'mug')


def _mug_module():
    """Return the loaded mug.py module (ibot loads it as 'ibot_plugins.mug')."""
    for name in _MUG_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, 'get_user_record') and hasattr(mod, '_save_data'):
            return mod
    return None


def _has_mug(bot) -> bool:
    """Check if mug.py is loaded and available."""
    try:
        return _mug_module() is not None
    except Exception:
        return False


@contextlib.contextmanager
def _mug_locked(bot, mug_mod):
    """Read-modify-write mug data under mug's own lock; mug saves afterwards."""
    if hasattr(mug_mod, 'locked_data'):
        with mug_mod.locked_data(bot):
            yield
    else:
        with mug_mod._data_lock:
            yield
        mug_mod._save_data(bot)


def _get_player_funds(bot, nick: str, hunter: sqlite3.Row) -> Tuple[int, str]:
    """Returns (amount, currency_name). Uses Mug coins if mug is present, else Spendable XP."""
    mug_mod = _mug_module()
    if mug_mod is not None:
        try:
            lock = getattr(mug_mod, '_data_lock', None) or contextlib.nullcontext()
            with lock:
                u = mug_mod.get_user_record(bot, nick)
                return int(u.get('money', 0)), 'coins'
        except Exception as e:
            LOG.debug("Mug get funds failed: %s", e)
    return int(hunter['spendable_xp']), 'XP'


def _deduct_player_funds(bot, nick: str, amount: int) -> bool:
    """Deduct currency from player."""
    mug_mod = _mug_module()
    if mug_mod is not None:
        try:
            ok = False
            with _mug_locked(bot, mug_mod):
                u = mug_mod.get_user_record(bot, nick)
                current = int(u.get('money', 0))
                if current >= amount:
                    u['money'] = current - amount
                    ok = True
            return ok
        except Exception as e:
            LOG.debug("Mug deduct failed: %s", e)
    with _db_lock, _get_conn() as conn:
        row = conn.execute("SELECT spendable_xp FROM hunters WHERE nick=?", (nick,)).fetchone()
        if row and row['spendable_xp'] >= amount:
            conn.execute("UPDATE hunters SET spendable_xp = spendable_xp - ? WHERE nick=?", (amount, nick))
            conn.commit()
            return True
    return False


def _award_player_funds(bot, nick: str, amount: int):
    """Award currency to player."""
    mug_mod = _mug_module()
    if mug_mod is not None:
        try:
            with _mug_locked(bot, mug_mod):
                u = mug_mod.get_user_record(bot, nick)
                u['money'] = int(u.get('money', 0)) + amount
            return
        except Exception as e:
            LOG.debug("Mug award failed: %s", e)
    with _db_lock, _get_conn() as conn:
        conn.execute("UPDATE hunters SET spendable_xp = spendable_xp + ? WHERE nick=?", (amount, nick))
        conn.commit()


# ──────────────────────────────────────────────────────────────
# In-Memory Active Animals & Channel State
# ──────────────────────────────────────────────────────────────
class ActiveAnimal:
    def __init__(self, species_key: str, rarity_key: str, channel: str):
        spec = WILDLIFE[species_key]
        rar = RARITIES[rarity_key]

        self.species_key = species_key
        self.name = spec['name']
        self.type = spec['type']  # 'hunt' or 'friendly'
        self.rarity_key = rarity_key
        self.rarity_label = rar['label']
        if self.type != 'friendly' and rarity_key != 'common':
            if not any(self.name.startswith(p) for p in ('Mythic', 'Legendary', 'Large', 'Mature', 'Old', 'Prize', 'Massive', 'Giant', 'Ancient')):
                adj = _sysrand.choice(RARITY_ADJECTIVES.get(rarity_key, ['']))
                self.name = f"{adj}{self.name}"
        self.color = RARITY_COLORS.get(rarity_key, GREY) if self.type != 'friendly' else PINK
        self.channel = channel

        base_hp = _sysrand.randint(spec['min_hp'], spec['max_hp'])
        self.max_hp = max(1, int(math.ceil(base_hp * rar['hp_mul'])))
        self.hp = self.max_hp

        self.xp = int(math.ceil(spec['base_xp'] * rar['xp_mul']))
        self.trophy_pts = rar['trophy']
        self.min_level = spec['min_level']
        self.min_gun = spec['min_gun']
        self.permit = spec['permit']
        self.loot_table = spec['loot']
        self.loot_chance = rar['loot_chance']

        stay_sec = _sysrand.randint(spec['stay_min'], spec['stay_max'])
        self.spawn_time = time.time()
        self.despawn_time = self.spawn_time + stay_sec

        self.assists: Dict[str, int] = {}  # nick -> damage dealt

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.despawn_time

    @property
    def time_remaining_str(self) -> str:
        rem = max(0, int(self.despawn_time - time.time()))
        mins = rem // 60
        secs = rem % 60
        return f"{mins}m{secs:02d}s"

    @property
    def clearance_str(self) -> str:
        if self.type == 'friendly':
            return f"{PINK}PROTECTED{RESET}"
        if not self.permit and self.min_level == 1 and self.min_gun == 1:
            return f"{GREEN}OPEN{RESET}"
        reqs = []
        if self.min_level > 1:
            reqs.append(f"L{self.min_level}")
        if self.min_gun > 1:
            reqs.append(f"Gun L{self.min_gun}")
        if self.permit:
            p_name = PERMITS.get(self.permit, {}).get('name', self.permit)
            reqs.append(p_name)
        if self.rarity_key == 'legendary' and self.permit != 'legendary':
            reqs.append(PERMITS['legendary']['name'])
        return f"{GOLD}{' + '.join(reqs)}{RESET}"


# {channel: [ActiveAnimal, ...]}
_active_animals: Dict[str, List[ActiveAnimal]] = {}
_channel_condition: Dict[str, str] = {}
_channel_call: Dict[str, str] = {}
_channel_last_spawn: Dict[str, float] = {}
_animal_lock = threading.Lock()

# Daemon control
_spawner_stop_event = threading.Event()
_spawner_thread: Optional[threading.Thread] = None


def _get_spawn_rates(bot=None) -> Tuple[int, int]:
    """Return (spawn_min, spawn_max) in seconds."""
    try:
        with _db_lock, _get_conn() as conn:
            row = conn.execute("SELECT value FROM hunting_globals WHERE key = 'spawn_rates'").fetchone()
            if row and row['value']:
                rates = json.loads(row['value'])
                if isinstance(rates, list) and len(rates) == 2:
                    return int(rates[0]), int(rates[1])
    except Exception:
        pass

    try:
        if bot and hasattr(bot, 'config') and hasattr(bot.config, 'hunt'):
            s_min = getattr(bot.config.hunt, 'spawn_min', None)
            s_max = getattr(bot.config.hunt, 'spawn_max', None)
            if s_min and s_max:
                return int(s_min), int(s_max)
    except Exception:
        pass

    # Default to calm 6 to 12 minutes (360 to 720 seconds)
    return 360, 720


# ──────────────────────────────────────────────────────────────
# Spawner Thread & Logic
# ──────────────────────────────────────────────────────────────
def _roll_rarity(condition_key: str) -> str:
    roll = _sysrand.random() * 100.0
    rare_bonus = CONDITIONS.get(condition_key, {}).get('rare_bonus', 0)
    if roll < (1.0 + rare_bonus * 0.2):
        return 'legendary'
    elif roll < (5.0 + rare_bonus * 0.5):
        return 'epic'
    elif roll < (15.0 + rare_bonus):
        return 'rare'
    elif roll < 40.0:
        return 'uncommon'
    return 'common'


def _spawn_animal(bot, channel: str, forced_species: Optional[str] = None, forced_rarity: Optional[str] = None) -> Optional[ActiveAnimal]:
    with _animal_lock:
        chan_key = channel.lower()
        active = _active_animals.setdefault(chan_key, [])

        # Cap max active animals per channel to 2
        if len(active) >= 2 and not forced_species:
            return None

        cond = _channel_condition.get(chan_key, 'clear')

        # Select species
        if forced_species and forced_species.lower() in WILDLIFE:
            s_key = forced_species.lower()
        elif chan_key in _channel_call:
            s_key = _channel_call.pop(chan_key)
        else:
            candidates = [k for k, v in WILDLIFE.items() if v['spawn_weight'] > 0]
            weights = [WILDLIFE[k]['spawn_weight'] for k in candidates]
            s_key = _sysrand.choices(candidates, weights=weights, k=1)[0]

        spec = WILDLIFE[s_key]

        # Select rarity
        if forced_rarity and forced_rarity.lower() in RARITIES:
            r_key = forced_rarity.lower()
        elif spec['type'] == 'friendly':
            r_key = 'rare' if _sysrand.random() < 0.3 else 'common'
        else:
            r_key = _roll_rarity(cond)
            rarity_ranks = ['common', 'uncommon', 'rare', 'epic', 'legendary']
            max_r = spec.get('max_rarity', 'legendary')
            if rarity_ranks.index(r_key) > rarity_ranks.index(max_r):
                r_key = max_r

        animal = ActiveAnimal(s_key, r_key, channel)
        active.append(animal)
        _channel_last_spawn[chan_key] = time.time()

        # Announcement verb
        verbs = spec['spawn_verbs']
        verb = _sysrand.choice(verbs)

        color = animal.color
        label = animal.rarity_label
        hp_str = f"HP {animal.hp}"
        xp_str = f"{animal.xp} XP"
        clear_str = animal.clearance_str

        msg = f"{color}[{label}]{RESET} {verb} {BOLD}{animal.name}{RESET} {hp_str} | {xp_str} | {clear_str}"
        try:
            bot.say(msg, channel)
        except Exception as e:
            LOG.error("Failed to announce spawn in %s: %s", channel, e)

        return animal


def _spawner_loop(bot):
    """Background loop that spawns wildlife and handles departures."""
    last_weather_change = time.time()

    # Initial delay on bot startup or reload: wait 3 to 6 minutes before first spawn check
    _spawner_stop_event.wait(timeout=_sysrand.randint(180, 360))

    while not _spawner_stop_event.is_set():
        try:
            target_channels = _get_enabled_channels(bot)
            now = time.time()

            # 1. Check expired animals
            with _animal_lock:
                for ch in target_channels:
                    chan_key = ch.lower()
                    active = _active_animals.get(chan_key, [])
                    still_active = []
                    for a in active:
                        if a.is_expired:
                            try:
                                bot.say(f"{GREY}{a.name.lower()} got away.{RESET}", ch)
                            except Exception:
                                pass
                        else:
                            still_active.append(a)
                    _active_animals[chan_key] = still_active

            # 2. Cycle weather every 25-35 minutes
            if now - last_weather_change > 1800:
                last_weather_change = now
                with _animal_lock:
                    for ch in target_channels:
                        new_cond = _sysrand.choice(list(CONDITIONS.keys()))
                        _channel_condition[ch.lower()] = new_cond

            # 3. Attempt spawn per channel respecting per-channel interval
            s_min, s_max = _get_spawn_rates(bot)
            for ch in target_channels:
                chan_key = ch.lower()
                last_spawn = _channel_last_spawn.get(chan_key, 0)
                if (now - last_spawn) < s_min:
                    continue

                with _animal_lock:
                    cur_count = len(_active_animals.get(chan_key, []))

                # If channel is empty: 60% chance to spawn once interval has elapsed
                # If channel already has 1 animal: only 20% chance to spawn a second
                spawn_chance = 0.60 if cur_count == 0 else (0.20 if cur_count == 1 else 0.0)
                if cur_count < 2 and _sysrand.random() < spawn_chance:
                    _spawn_animal(bot, ch)

        except Exception as e:
            LOG.exception("Spawner loop error: %s", e)

        # Sleep 60 to 90 seconds between evaluation loops
        _spawner_stop_event.wait(timeout=_sysrand.randint(60, 90))


# ──────────────────────────────────────────────────────────────
# Sopel Plugin Lifecycle
# ──────────────────────────────────────────────────────────────
def setup(bot):
    _init_db()
    _load_channel_toggles(bot)
    global _spawner_thread, _spawner_stop_event
    _spawner_stop_event.clear()
    _spawner_thread = threading.Thread(target=_spawner_loop, args=(bot,), daemon=True, name="HuntSpawnerThread")
    _spawner_thread.start()
    LOG.info("Hunt plugin initialized; Spawner daemon running.")


def shutdown(bot):
    global _spawner_stop_event
    _spawner_stop_event.set()
    with _animal_lock:
        _active_animals.clear()
    LOG.info("Hunt plugin shut down.")


# ──────────────────────────────────────────────────────────────
# Core Hunting Engine: Helpers & Mechanics
# ──────────────────────────────────────────────────────────────
def _calculate_accuracy(hunter: sqlite3.Row, condition_key: str) -> int:
    """Calculate effective shot accuracy."""
    base = 75
    base += hunter['scope_level'] * 3
    base += max(0, (hunter['gun_level'] - 1))
    base += CONDITIONS.get(condition_key, {}).get('acc_mod', 0)
    steady_aim = min(22, hunter['consecutive_misses'] * 9)
    base += steady_aim
    if hunter['gun_condition'] < 50.0:
        base -= int((50.0 - hunter['gun_condition']) * 0.4)

    return max(15, min(99, base))


def _check_clearance(hunter: sqlite3.Row, permits: List[str], animal: ActiveAnimal, bot=None) -> Tuple[bool, str]:
    """Check if hunter meets level, gun, and permit requirements."""
    p = _get_prefix(bot)
    if animal.type == 'friendly':
        return True, ""

    if hunter['level'] < animal.min_level:
        return False, f"Requires Hunter Level {animal.min_level}+ (you are L{hunter['level']})."

    if hunter['gun_level'] < animal.min_gun:
        return False, f"Requires Gun Level {animal.min_gun}+ (your gun is L{hunter['gun_level']})."

    if animal.permit and animal.permit not in permits:
        p_name = PERMITS.get(animal.permit, {}).get('name', animal.permit)
        return False, f"Requires {p_name}. Buy it with {p}permit buy {animal.permit}."

    if animal.rarity_key == 'legendary' and 'legendary' not in permits:
        return False, f"Requires Legendary Endorsement for Legendary hunts. Buy with {p}permit buy legendary."

    return True, ""


def _level_for_xp(xp: int) -> int:
    """Calculate hunter level from total XP."""
    if xp <= 0:
        return 1
    lvl = 1 + int(math.sqrt(xp / 12.0))
    return min(50, lvl)


# ──────────────────────────────────────────────────────────────
# Commands: Hunting & Shooting
# ──────────────────────────────────────────────────────────────
@plugin.command('huntable')
@plugin.example('$huntable')
def cmd_huntable(bot, trigger):
    """Show all active wildlife in this channel."""
    if not _check_channel_enabled(bot, trigger):
        return

    chan_key = trigger.sender.lower()
    with _animal_lock:
        active = _active_animals.get(chan_key, [])
        valid = [a for a in active if not a.is_expired]

    if not valid:
        bot.say(f"{BLUE}[WILDLIFE]{RESET} The woods are quiet. No wildlife is in the area right now.", trigger.sender)
        return

    if len(valid) > 2:
        for idx, a in enumerate(valid, 1):
            feed_hint = f" | {PINK}Feed with $feed{RESET}" if a.type == 'friendly' else ""
            bot.say(f"{BOLD}[HUNTABLE {idx}]{RESET} {a.color}{a.rarity_label}{RESET} {a.name} HP {a.hp}/{a.max_hp} ({a.xp} XP) {a.time_remaining_str} [{a.clearance_str}]{feed_hint}", trigger.sender)
    else:
        parts = []
        for a in valid:
            parts.append(f"{a.color}{a.rarity_label}{RESET} {a.name} HP {a.hp}/{a.max_hp} ({a.xp} XP) {a.time_remaining_str} [{a.clearance_str}]")
        bot.say(f"{BOLD}[HUNTABLE]{RESET} " + " | ".join(parts), trigger.sender)


@plugin.command('shoot', 'hunt')
@plugin.example('$shoot')
@plugin.example('$shoot rabbit')
def cmd_shoot(bot, trigger):
    """Shoot at active wildlife in the channel. Auto-targets oldest eligible animal if unspecified."""
    channel = trigger.sender
    if not str(channel).startswith('#'):
        bot.reply("Hunting takes place in designated channels, not in private messages!")
        return

    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)
    permits = _get_permits(nick)
    chan_key = channel.lower()

    # 1. Weapon checks
    if hunter['gun_confiscated']:
        bot.notice(f"[CONFISCATED] Your gun was confiscated! Buy it back in the shop with: {p}huntshop buy 5", nick)
        return

    if hunter['gun_jammed']:
        bot.notice(f"[JAMMED] Your gun is jammed! Cycle the action with {p}reloadgun (or {p}unjam).", nick)
        return

    if hunter['ammo'] <= 0:
        bot.notice(f"[AMMO] You are out of rounds. Use {p}reloadgun, {p}huntshop 1 for one round, or {p}huntshop 2 for a full magazine.", nick)
        return

    # 2. Find target animal
    target_name = (trigger.group(2) or '').strip().lower()
    with _animal_lock:
        active = _active_animals.get(chan_key, [])
        valid = [a for a in active if not a.is_expired]

    if not valid:
        bot.notice("[WILDLIFE] The woods are quiet. No wildlife is in the area right now.", nick)
        return

    target: Optional[ActiveAnimal] = None
    if target_name:
        for a in valid:
            if target_name in a.name.lower():
                target = a
                break
        if not target:
            bot.notice(f"No '{target_name}' found here. Type {p}huntable to see active wildlife.", nick)
            return
        cleared, reason = _check_clearance(hunter, permits, target, bot)
        if not cleared:
            bot.notice(f"[CLEARANCE BLOCKED] You cannot hunt {target.name}: {reason}", nick)
            return
    else:
        # Check active engagement first!
        engaged = [a for a in valid if nick in a.assists and a.hp > 0 and a.type != 'friendly']
        if engaged:
            target = engaged[0]
        else:
            for a in valid:
                if a.type != 'friendly':
                    cleared, _ = _check_clearance(hunter, permits, a, bot)
                    if cleared:
                        target = a
                        break
        if not target:
            uncleared = [a for a in valid if a.type != 'friendly']
            if uncleared:
                bot.notice(f"[CLEARANCE] There are animals nearby, but none you are currently cleared to hunt. Use {p}huntable and {p}permits. No ammo or gun wear used.", nick)
                return
            bot.notice(f"There are no huntable animals in the area right now. Type {p}huntable to check for wildlife.", nick)
            return

        cleared, reason = _check_clearance(hunter, permits, target, bot)
        if not cleared:
            bot.notice(f"[CLEARANCE] There are animals nearby, but none you are currently cleared to hunt. Use {p}huntable and {p}permits. No ammo or gun wear used.", nick)
            return

    # 3. Weapon wear & jamming roll
    cond_loss = _sysrand.uniform(0.1, 0.4)
    lube_loss = _sysrand.uniform(0.4, 0.9)
    jam_risk = 0.0
    if hunter['gun_lubrication'] < 20.0:
        jam_risk += (20.0 - hunter['gun_lubrication']) * 0.8
    jam_risk = max(0.0, jam_risk - hunter['action_tune'] * 4.0)

    is_jammed = (_sysrand.random() * 100.0) < jam_risk

    # Deduct ammo
    new_ammo = hunter['ammo'] - 1
    new_cond = max(0.0, hunter['gun_condition'] - cond_loss)
    new_lube = max(0.0, hunter['gun_lubrication'] - lube_loss)

    with _db_lock, _get_conn() as conn:
        conn.execute("""
            UPDATE hunters
            SET ammo=?, gun_condition=?, gun_lubrication=?, gun_jammed=?
            WHERE nick=?
        """, (new_ammo, new_cond, new_lube, 1 if is_jammed else 0, nick))
        conn.commit()

    # 4. Handle Protected / Friendly Species Shot
    if target.type == 'friendly':
        penalty_xp = 50
        with _db_lock, _get_conn() as conn:
            conn.execute("""
                UPDATE hunters
                SET spendable_xp = MAX(0, spendable_xp - ?),
                    consecutive_misses = 0
                WHERE nick=?
            """, (penalty_xp, nick))
            conn.commit()
        with _animal_lock:
            if target in active:
                active.remove(target)
        bot.say(f"{RED}[PENALTY]{RESET} {BOLD}{nick}{RESET} shot at a protected {target.name}! The animal fled in terror. You lost {penalty_xp} XP and a cruelty fine was levied!", channel)
        return

    # 5. Accuracy Roll
    cond_key = _channel_condition.get(chan_key, 'clear')
    accuracy = _calculate_accuracy(hunter, cond_key)
    hit_roll = _sysrand.random() * 100.0
    hit = hit_roll < accuracy

    next_acc = min(99, accuracy + 9)

    if not hit:
        with _db_lock, _get_conn() as conn:
            conn.execute("UPDATE hunters SET consecutive_misses = consecutive_misses + 1 WHERE nick=?", (nick,))
            conn.commit()

        now = time.time()
        silenced = hunter['silencer_until'] > now
        scare_chance = 15 if not silenced else 0
        if _sysrand.random() * 100.0 < scare_chance:
            with _animal_lock:
                if target in active:
                    active.remove(target)
            bot.say(f"{RED}[SCARED]{RESET} {nick} fired at {target.name}, but the gunshot scared it away!", channel)
            return

        bot.say(f"{GREY}[MISS]{RESET} {nick} missed {target.name} | Accuracy {accuracy}% | Steady Aim next shot ~{next_acc}%", channel)
        return

    # 6. Hit Logic & Critical Hit
    base_dmg = max(1, hunter['gun_level'])
    crit_chance = 12.0 + hunter['scope_level'] * 2.0
    is_crit = (_sysrand.random() * 100.0) < crit_chance
    if is_crit:
        damage = base_dmg * 2
        crit_hit_str = f" {GOLD}CRITICAL HIT!{RESET}"
        earned_hit_xp = damage
    else:
        damage = base_dmg
        crit_hit_str = ""
        earned_hit_xp = 1

    target.hp = max(0, target.hp - damage)
    target.assists[nick] = target.assists.get(nick, 0) + damage
    target.despawn_time = max(target.despawn_time, time.time() + 180)

    with _db_lock, _get_conn() as conn:
        conn.execute("UPDATE hunters SET consecutive_misses = 0 WHERE nick=?", (nick,))
        conn.commit()

    if target.hp > 0:
        bot.say(f"{GREEN}[HIT]{RESET} {nick} hit {target.name} for {damage} damage!{crit_hit_str} HP {target.hp}/{target.max_hp} | +{earned_hit_xp} XP | Accuracy {accuracy}%", channel)
        with _db_lock, _get_conn() as conn:
            conn.execute("UPDATE hunters SET total_xp = total_xp + ?, spendable_xp = spendable_xp + ? WHERE nick=?", (earned_hit_xp, earned_hit_xp, nick))
            conn.commit()
        return

    # 7. Animal Harvested!
    with _animal_lock:
        if target in active:
            active.remove(target)

    xp_gain = target.xp
    trophy_gain = target.trophy_pts

    new_streak = hunter['current_streak'] + 1
    streak_bonus = min(25, new_streak * 5)
    total_xp_awarded = xp_gain + streak_bonus

    is_rare = 1 if target.rarity_key in ('rare', 'epic', 'legendary') else 0
    is_epic = 1 if target.rarity_key in ('epic', 'legendary') else 0
    is_leg = 1 if target.rarity_key == 'legendary' else 0

    loot_drop = None
    if target.loot_table and (_sysrand.random() < target.loot_chance):
        loot_item = _sysrand.choice(target.loot_table)
        loot_drop = loot_item[0]
        loot_val = loot_item[1]
        _add_to_bag(nick, loot_drop, target.name, target.rarity_key, loot_val)

    achievement_parts = []
    if hunter['total_kills'] == 0:
        achievement_parts.append(f"{GOLD}Achievement: First Blood +25 XP{RESET}")
        total_xp_awarded += 25

    updated_total_xp = hunter['total_xp'] + total_xp_awarded
    new_level = _level_for_xp(updated_total_xp)

    with _db_lock, _get_conn() as conn:
        conn.execute("""
            UPDATE hunters
            SET total_xp = total_xp + ?,
                spendable_xp = spendable_xp + ?,
                total_kills = total_kills + 1,
                trophy_score = trophy_score + ?,
                rare_kills = rare_kills + ?,
                epic_kills = epic_kills + ?,
                legendary_kills = legendary_kills + ?,
                current_streak = ?,
                level = ?
            WHERE nick=?
        """, (total_xp_awarded, total_xp_awarded, trophy_gain, is_rare, is_epic, is_leg, new_streak, new_level, nick))
        conn.commit()

    if _has_mug(bot):
        coin_payout = int(xp_gain * 2)
        _award_player_funds(bot, nick, coin_payout)

    assist_parts = []
    for helper, dmg in target.assists.items():
        if helper.lower() != nick.lower():
            assist_xp = max(2, int(xp_gain * (dmg / target.max_hp)))
            with _db_lock, _get_conn() as conn:
                conn.execute("UPDATE hunters SET total_xp = total_xp + ?, spendable_xp = spendable_xp + ? WHERE nick=?",
                             (assist_xp, assist_xp, helper))
                conn.commit()
            assist_parts.append(f"{helper} +{assist_xp} XP")

    crit_finish = f" {GOLD}CRITICAL FINISH!{RESET}" if is_crit else ""
    rookie_tag = "rookie " if hunter['level'] <= 5 else ""
    trophy_total = hunter['trophy_score'] + trophy_gain

    loot_str = f" | Loot: {GOLD}{loot_drop}{RESET}" if loot_drop else ""
    assist_str = f" | Assists: {', '.join(assist_parts)}" if assist_parts else ""
    ach_str = f" | {' | '.join(achievement_parts)}" if achievement_parts else ""
    levelup_str = f" | {GOLD}★ LEVEL UP! You are now Level {new_level}!{RESET}" if new_level > hunter['level'] else ""

    bot.say(
        f"{target.color}[{target.rarity_label} KILL]{RESET} {BOLD}{nick}{RESET} harvested {target.name} and earned {total_xp_awarded} XP!{crit_finish} | Trophy +{trophy_gain} ({trophy_total}) | Streak x{new_streak} +{streak_bonus} {rookie_tag}XP!{loot_str}{assist_str}{ach_str}{levelup_str}",
        channel
    )


@plugin.command('reloadgun', 'loadgun', 'rechamber', 'unjam', 'chamber', 'mag')
@plugin.example('$reloadgun')
def cmd_reload(bot, trigger):
    """Reload your gun's magazine or clear a mechanical jam."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    channel = trigger.sender
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)

    if hunter['gun_confiscated']:
        bot.notice(f"Your gun is confiscated! Reclaim it in the shop with {p}huntshop buy 5.", nick)
        return

    if hunter['gun_jammed']:
        with _db_lock, _get_conn() as conn:
            conn.execute("UPDATE hunters SET gun_jammed=0, consecutive_misses=0 WHERE nick=?", (nick,))
            conn.commit()
        bot.notice("[RELOAD] You cycled the action and cleared the jam! Weapon is ready.", nick)
        return

    if hunter['ammo'] >= hunter['mag_capacity']:
        bot.notice(f"[RELOAD] Magazine is already full ({hunter['ammo']}/{hunter['mag_capacity']} rounds).", nick)
        return

    spare_mags = hunter['extra_mags']
    with _db_lock, _get_conn() as conn:
        conn.execute("UPDATE hunters SET ammo = mag_capacity, consecutive_misses=0 WHERE nick=?", (nick,))
        conn.commit()

    bot.notice(f"[RELOAD] Reloaded to {hunter['mag_capacity']} rounds. | {spare_mags} spare mag(s) remain.", nick)


# ──────────────────────────────────────────────────────────────
# Commands: Gear, Permits & Weapon Maintenance
# ──────────────────────────────────────────────────────────────
@plugin.command('gun')
@plugin.example('$gun')
def cmd_gun(bot, trigger):
    """View your weapon accuracy, condition, lubrication, and attachments."""
    if not _check_channel_enabled(bot, trigger):
        return

    args = (trigger.group(2) or '').strip().split()
    target_nick = args[0] if args else str(trigger.nick)
    hunter = _get_hunter(target_nick) if args else _get_or_create_hunter(target_nick)
    if not hunter:
        bot.reply(f"No hunter record found for {target_nick}.", trigger.sender)
        return

    cond_key = _channel_condition.get(trigger.sender.lower(), 'clear') if trigger.sender.startswith('#') else 'clear'
    acc = _calculate_accuracy(hunter, cond_key)

    jam_str = f" {RED}[JAMMED]{RESET}" if hunter['gun_jammed'] else ""
    conf_str = f" {RED}[CONFISCATED]{RESET}" if hunter['gun_confiscated'] else ""
    sil_str = " | Silencer: Active" if hunter['silencer_until'] > time.time() else ""
    ins_str = " | Insurance: Active" if hunter['insurance_until'] > time.time() else ""

    bot.reply(
        f"{BOLD}[GUN STATS: {target_nick}]{RESET} Gun Level {hunter['gun_level']} | Mag: {hunter['ammo']}/{hunter['mag_capacity']} | "
        f"Condition: {hunter['gun_condition']:.1f}% | Lube: {hunter['gun_lubrication']:.1f}% | "
        f"Accuracy: ~{acc}% | Scope: L{hunter['scope_level']} | Action Tune: L{hunter['action_tune']}"
        f"{jam_str}{conf_str}{sil_str}{ins_str}",
        trigger.sender
    )


@plugin.command('grease')
@plugin.example('$grease')
def cmd_grease(bot, trigger):
    """Apply gun grease to restore 100% lubrication and prevent jams."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)

    if hunter['grease_tubes'] <= 0:
        bot.reply(f"You have no gun grease! Buy a tube in the shop with: {p}huntshop buy 15", trigger.sender)
        return

    with _db_lock, _get_conn() as conn:
        conn.execute("UPDATE hunters SET gun_lubrication=100.0, grease_tubes = grease_tubes - 1 WHERE nick=?", (nick,))
        conn.commit()

    bot.reply("Applied gun grease. Lubrication restored to 100%! Gun runs smooth.", trigger.sender)


@plugin.command('repair')
@plugin.example('$repair')
def cmd_repair(bot, trigger):
    """Use a repair kit to restore 35% weapon condition."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)

    if hunter['repair_kits'] <= 0:
        bot.reply(f"You have no repair kits! Buy one in the shop with: {p}huntshop buy 16", trigger.sender)
        return

    new_cond = min(100.0, hunter['gun_condition'] + 35.0)
    with _db_lock, _get_conn() as conn:
        conn.execute("UPDATE hunters SET gun_condition=?, repair_kits = repair_kits - 1 WHERE nick=?", (new_cond, nick))
        conn.commit()

    bot.reply(f"Repaired firearm! Weapon condition restored to {new_cond:.1f}%.", trigger.sender)


@plugin.command('permits', 'permit')
@plugin.example('$permits')
@plugin.example('$permit buy medium_game')
def cmd_permits(bot, trigger):
    """View and purchase hunting permits. Usage: $permits or $permit buy <key>."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)
    owned = _get_permits(nick)

    args = (trigger.group(2) or '').strip().split()
    if args and args[0].lower() in ('buy', 'purchase') and len(args) > 1:
        permit_key = args[1].lower()
        if permit_key not in PERMITS:
            bot.reply(f"Unknown permit '{permit_key}'. Valid: {', '.join(PERMITS.keys())}", trigger.sender)
            return
        if permit_key in owned:
            bot.reply(f"You already own the {PERMITS[permit_key]['name']}!", trigger.sender)
            return

        p_info = PERMITS[permit_key]
        if hunter['level'] < p_info['min_level']:
            bot.reply(f"You need Hunter Level {p_info['min_level']} to buy {p_info['name']} (you are L{hunter['level']}).", trigger.sender)
            return

        funds, cur_name = _get_player_funds(bot, nick, hunter)
        if funds < p_info['cost']:
            bot.reply(f"You need {p_info['cost']} {cur_name} for {p_info['name']} (you have {funds} {cur_name}).", trigger.sender)
            return

        if _deduct_player_funds(bot, nick, p_info['cost']):
            _grant_permit(nick, permit_key)
            bot.reply(f"{GREEN}[PERMIT ISSUED]{RESET} Congratulations! You are now licensed with {BOLD}{p_info['name']}{RESET}! {p_info['desc']}", trigger.sender)
        return

    lines = [f"{BOLD}[HUNTING PERMITS]{RESET} (Buy with {p}permit buy <key>):"]
    for key, p_obj in PERMITS.items():
        status = f"{GREEN}OWNED{RESET}" if key in owned else f"{GOLD}{p_obj['cost']} XP (Req L{p_obj['min_level']}){RESET}"
        lines.append(f"• {BOLD}{p_obj['name']}{RESET} [{key}]: {status} — {p_obj['desc']}")

    for l in lines:
        bot.notice(l, trigger.nick)
    if trigger.sender.startswith('#'):
        bot.reply("Sent permit catalog via private notice.", trigger.sender)


@plugin.command('huntshop', 'hshop', 'shop')
@plugin.example('$huntshop')
@plugin.example('$huntshop buy 1')
def cmd_shop(bot, trigger):
    """Browse the hunt shop or buy supplies. Usage: $huntshop or $huntshop buy <id>."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)
    funds, cur_name = _get_player_funds(bot, nick, hunter)

    args = (trigger.group(2) or '').strip().split()
    item_id = None
    if args:
        if args[0].isdigit():
            item_id = int(args[0])
        elif args[0].lower() in ('buy', 'purchase') and len(args) > 1 and args[1].isdigit():
            item_id = int(args[1])
        elif args[0].lower() in ('buy', 'purchase'):
            bot.reply(f"Usage: {p}huntshop buy <item_id> (or {p}huntshop <item_id>)", trigger.sender)
            return

    if item_id is not None:
        if item_id not in SHOP_ITEMS:
            bot.reply(f"Invalid item ID. Type {p}huntshop to see items.", trigger.sender)
            return

        item = SHOP_ITEMS[item_id]
        if hunter['level'] < item['min_level']:
            bot.reply(f"Requires Hunter Level {item['min_level']} (you are L{hunter['level']}).", trigger.sender)
            return

        if funds < item['cost']:
            bot.reply(f"Insufficient funds! Needs {item['cost']} {cur_name} (you have {funds} {cur_name}).", trigger.sender)
            return

        if not _deduct_player_funds(bot, nick, item['cost']):
            bot.reply("Purchase failed.", trigger.sender)
            return

        with _db_lock, _get_conn() as conn:
            if item_id == 1:
                conn.execute("UPDATE hunters SET ammo = MIN(mag_capacity, ammo + 1) WHERE nick=?", (nick,))
            elif item_id == 2:
                conn.execute("UPDATE hunters SET ammo = mag_capacity WHERE nick=?", (nick,))
            elif item_id == 3:
                conn.execute("UPDATE hunters SET extra_mags = MIN(5, extra_mags + 1) WHERE nick=?", (nick,))
            elif item_id == 4:
                conn.execute("UPDATE hunters SET mag_capacity = MIN(10, mag_capacity + 1) WHERE nick=?", (nick,))
            elif item_id == 5:
                conn.execute("UPDATE hunters SET gun_confiscated = 0 WHERE nick=?", (nick,))
            elif item_id == 6:
                conn.execute("UPDATE hunters SET gun_level = MIN(10, gun_level + 1) WHERE nick=?", (nick,))
            elif item_id == 7:
                conn.execute("UPDATE hunters SET silencer_until = ? WHERE nick=?", (time.time() + 86400, nick))
            elif item_id == 8:
                conn.execute("UPDATE hunters SET insurance_until = ? WHERE nick=?", (time.time() + 86400, nick))
            elif item_id == 9:
                conn.execute("UPDATE hunters SET has_food_box = 1 WHERE nick=?", (nick,))
            elif item_id == 10:
                conn.execute("UPDATE hunters SET bread = bread + 15 WHERE nick=?", (nick,))
            elif item_id == 11:
                conn.execute("UPDATE hunters SET popcorn = popcorn + 30 WHERE nick=?", (nick,))
            elif item_id == 12:
                conn.execute("UPDATE hunters SET wildfeed = wildfeed + 50 WHERE nick=?", (nick,))
            elif item_id == 13:
                conn.execute("UPDATE hunters SET scope_level = MIN(5, scope_level + 1) WHERE nick=?", (nick,))
            elif item_id == 14:
                conn.execute("UPDATE hunters SET action_tune = MIN(5, action_tune + 1) WHERE nick=?", (nick,))
            elif item_id == 15:
                conn.execute("UPDATE hunters SET grease_tubes = grease_tubes + 1 WHERE nick=?", (nick,))
            elif item_id == 16:
                conn.execute("UPDATE hunters SET repair_kits = repair_kits + 1 WHERE nick=?", (nick,))
            conn.commit()

        bot.reply(f"{GREEN}[PURCHASED]{RESET} Bought {BOLD}{item['name']}{RESET} for {item['cost']} {cur_name}! {item['desc']}", trigger.sender)
        return

    lines = [f"{BOLD}[HUNT SHOP]{RESET} | Balance: {GREEN}{funds} {cur_name}{RESET} | Buy: {p}huntshop buy <id>"]
    for i_id, it in SHOP_ITEMS.items():
        lines.append(f"#{i_id} {BOLD}{it['name']}{RESET} ({it['cost']} {cur_name}, Req L{it['min_level']}) — {it['desc']}")

    for l in lines:
        bot.notice(l, trigger.nick)
    if trigger.sender.startswith('#'):
        bot.reply("Sent shop catalog via private notice.", trigger.sender)


# ──────────────────────────────────────────────────────────────
# Commands: Bag, Selling & Friendly Feeding
# ──────────────────────────────────────────────────────────────
@plugin.command('bag')
@plugin.example('$bag')
def cmd_bag(bot, trigger):
    """View harvested animal loot in your hunting bag."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    with _db_lock, _get_conn() as conn:
        rows = conn.execute("SELECT * FROM bag WHERE nick=? ORDER BY id DESC", (nick,)).fetchall()

    if not rows:
        bot.reply("Your hunting bag is empty. Harvest wildlife to gather trophies and loot!", trigger.sender)
        return

    total_val = sum(r['sell_value'] for r in rows)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r['item_name']] = counts.get(r['item_name'], 0) + 1

    parts = [f"{name} x{cnt}" for name, cnt in counts.items()]
    bot.reply(f"{BOLD}[HUNTING BAG]{RESET} {', '.join(parts)} | Total Sell Value: {GOLD}{total_val} XP/coins{RESET} (Sell with {p}sell all)", trigger.sender)


@plugin.command('sell')
@plugin.example('$sell all')
def cmd_sell(bot, trigger):
    """Sell harvested loot for spendable currency. Usage: $sell all."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    args = (trigger.group(2) or '').strip().lower()

    with _db_lock, _get_conn() as conn:
        rows = conn.execute("SELECT * FROM bag WHERE nick=?", (nick,)).fetchall()

    if not rows:
        bot.reply("You have no loot to sell.", trigger.sender)
        return

    if args in ('all', 'loot'):
        total_payout = sum(r['sell_value'] for r in rows)
        with _db_lock, _get_conn() as conn:
            conn.execute("DELETE FROM bag WHERE nick=?", (nick,))
            conn.commit()

        _award_player_funds(bot, nick, total_payout)
        funds, cur_name = _get_player_funds(bot, nick, _get_hunter(nick))
        bot.reply(f"{GREEN}[SOLD]{RESET} Sold all {len(rows)} items for {BOLD}{total_payout} {cur_name}{RESET}! New balance: {funds} {cur_name}.", trigger.sender)
        return

    bot.reply(f"Usage: {p}sell all to sell all held trophies and loot.", trigger.sender)


@plugin.command('feed')
@plugin.example('$feed')
def cmd_feed(bot, trigger):
    """Feed protected animals (Cats and Dogs) to earn friendship points."""
    channel = trigger.sender
    if not str(channel).startswith('#'):
        bot.reply("Animal feed can only be scattered in an active hunting channel!", channel)
        return

    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    nick = str(trigger.nick)
    hunter = _get_or_create_hunter(nick)
    chan_key = channel.lower()

    if not hunter['has_food_box']:
        bot.reply(f"You need a Food Box before carrying animal food! Buy one in the shop: {p}huntshop buy 9", channel)
        return

    with _animal_lock:
        active = _active_animals.get(chan_key, [])
        friendly = [a for a in active if a.type == 'friendly' and not a.is_expired]

    if not friendly:
        bot.reply("There are no stray cats or lost dogs here to feed.", channel)
        return

    target = friendly[0]

    food_used = None
    chance = 0.15
    if hunter['wildfeed'] > 0:
        food_used = 'wildfeed'
        chance = 0.50
    elif hunter['popcorn'] > 0:
        food_used = 'popcorn'
        chance = 0.30
    elif hunter['bread'] > 0:
        food_used = 'bread'
        chance = 0.15
    else:
        bot.reply(f"You have no animal food! Buy Bread, Popcorn, or WildFeed in the shop: {p}huntshop", channel)
        return

    with _db_lock, _get_conn() as conn:
        conn.execute(f"UPDATE hunters SET {food_used} = {food_used} - 1 WHERE nick=?", (nick,))
        conn.commit()

    if _sysrand.random() < chance:
        with _animal_lock:
            if target in active:
                active.remove(target)
        rescue_xp = 35
        with _db_lock, _get_conn() as conn:
            conn.execute("""
                UPDATE hunters
                SET friends = friends + 1,
                    total_xp = total_xp + ?,
                    spendable_xp = spendable_xp + ?
                WHERE nick=?
            """, (rescue_xp, rescue_xp, nick))
            conn.commit()
        bot.say(f"{PINK}[RESCUED]{RESET} {BOLD}{nick}{RESET} fed the {target.name} with {food_used}! The happy animal was befriended and safely rescued! (+{rescue_xp} XP, +1 Friend Credit)", channel)
    else:
        bot.say(f"{GREY}[FEEDING]{RESET} {nick} offered {food_used} to the {target.name}, but it remained shy. Try again!", channel)


# ──────────────────────────────────────────────────────────────
# Commands: Stats, Weather, Guide & Leaderboards
# ──────────────────────────────────────────────────────────────
@plugin.command('mystats', 'huntstats', 'huntprofile')
@plugin.example('$mystats')
@plugin.example('$mystats End3r')
def cmd_mystats(bot, trigger):
    """View your complete hunting career statistics and gear."""
    if not _check_channel_enabled(bot, trigger):
        return

    args = (trigger.group(2) or '').strip().split()
    nick = args[0] if args else str(trigger.nick)
    hunter = _get_hunter(nick) if args else _get_or_create_hunter(nick)
    if not hunter:
        bot.reply(f"No hunter record found for {nick}.", trigger.sender)
        return

    permits = _get_permits(nick)
    funds, cur_name = _get_player_funds(bot, nick, hunter)

    perm_str = ", ".join(permits) if permits else "None"
    bot.reply(
        f"{BOLD}[HUNTER PROFILE: {nick}]{RESET} Level: {BOLD}{hunter['level']}{RESET} | "
        f"Total XP: {hunter['total_xp']} | Funds: {GREEN}{funds} {cur_name}{RESET} | "
        f"Harvests: {hunter['total_kills']} (Rare+: {hunter['rare_kills']}, Leg: {hunter['legendary_kills']}) | "
        f"Trophies: {hunter['trophy_score']} pts | Rescues: {hunter['friends']} | "
        f"Gun Level: {hunter['gun_level']} | Permits: [{perm_str}]",
        trigger.sender
    )


@plugin.command('conditions')
@plugin.example('$conditions')
def cmd_conditions(bot, trigger):
    """View the current hunting field conditions and weather effects."""
    if not _check_channel_enabled(bot, trigger):
        return

    args = (trigger.group(2) or '').strip().split()
    if trigger.sender.startswith('#'):
        chan_key = trigger.sender.lower()
    elif args and args[0].startswith('#'):
        chan_key = args[0].lower()
    else:
        enabled = _get_enabled_channels(bot)
        chan_key = enabled[0].lower() if enabled else 'default'

    cond_key = _channel_condition.get(chan_key, 'clear')
    cond = CONDITIONS.get(cond_key, CONDITIONS['clear'])

    acc_mod = f"+{cond['acc_mod']}%" if cond['acc_mod'] >= 0 else f"{cond['acc_mod']}%"
    rare_mod = f"+{cond['rare_bonus']}%" if cond['rare_bonus'] > 0 else "None"

    bot.reply(f"{BOLD}[FIELD CONDITIONS]{RESET} Weather: {BOLD}{cond['name']}{RESET} | Shot Accuracy: {acc_mod} | Rare Encounter Bonus: {rare_mod}", trigger.sender)


@plugin.command('guide')
@plugin.example('$guide rabbit')
def cmd_guide(bot, trigger):
    """View wildlife field guide data for a species."""
    if not _check_channel_enabled(bot, trigger):
        return

    p = _get_prefix(bot)
    query = (trigger.group(2) or '').strip().lower().replace(' ', '_')
    if not query:
        species_list = [v['name'] for v in WILDLIFE.values()]
        bot.reply(f"Field Guide covers: {', '.join(species_list)}. Use {p}guide <species> for details.", trigger.sender)
        return

    found = None
    for k, v in WILDLIFE.items():
        if query in k or query in v['name'].lower():
            found = v
            break

    if not found:
        bot.reply(f"No entry found for '{query}'.", trigger.sender)
        return

    permit_req = PERMITS.get(found['permit'], {}).get('name', 'None') if found['permit'] else 'OPEN (No permit)'
    loot_names = [l[0] for l in found['loot']] if found['loot'] else ['Rescue XP']
    bot.reply(
        f"{BOLD}[GUIDE: {found['name']}]{RESET} Type: {found['type'].title()} | HP: {found['min_hp']}–{found['max_hp']} | "
        f"Base XP: {found['base_xp']} | Req: L{found['min_level']}+ Gun L{found['min_gun']}+ | "
        f"Permit: {permit_req} | Max Rarity: {found['max_rarity'].title()} | Loot: {', '.join(loot_names)}",
        trigger.sender
    )


@plugin.command('top')
@plugin.example('$top')
@plugin.example('$top trophies')
def cmd_top(bot, trigger):
    """View hunting leaderboards: exp, kills, trophies, or friends."""
    if not _check_channel_enabled(bot, trigger):
        return

    board_type = (trigger.group(2) or 'exp').strip().lower()

    col_map = {
        'exp': ('total_xp', 'Total XP'),
        'xp': ('total_xp', 'Total XP'),
        'kills': ('total_kills', 'Harvests'),
        'harvests': ('total_kills', 'Harvests'),
        'trophies': ('trophy_score', 'Trophy Points'),
        'friends': ('friends', 'Rescues'),
        'rescues': ('friends', 'Rescues'),
    }

    col, label = col_map.get(board_type, ('total_xp', 'Total XP'))

    with _db_lock, _get_conn() as conn:
        rows = conn.execute(f"""
            SELECT nick, level, {col}
            FROM hunters
            WHERE {col} > 0
            ORDER BY {col} DESC
            LIMIT 5
        """).fetchall()

    if not rows:
        bot.reply(f"No hunters on the {label} leaderboard yet.", trigger.sender)
        return

    medals = ['🥇', '🥈', '🥉', '4.', '5.']
    parts = []
    for i, r in enumerate(rows):
        parts.append(f"{medals[i]} {BOLD}{r['nick']}{RESET} (L{r['level']} · {r[col]:,} {label})")

    bot.say(f"{BOLD}[TOP HUNTERS — {label.upper()}]{RESET} " + " | ".join(parts), trigger.sender)


# ──────────────────────────────────────────────────────────────
# Admin & Channel Management Commands
# ──────────────────────────────────────────────────────────────
@plugin.command('spawn')
@plugin.require_admin("Only admins can spawn wildlife.")
@plugin.example('$spawn bison legendary')
def cmd_spawn(bot, trigger):
    """Admin command to force-spawn wildlife in an enabled channel."""
    channel = trigger.sender
    if not str(channel).startswith('#'):
        bot.reply("Spawn command can only be used in a channel.", trigger.sender)
        return

    p = _get_prefix(bot)
    if not _plugin_enabled(bot, channel):
        bot.reply(f"🔒 Hunting is disabled in {channel}. Enable it first with: {p}hunttoggle on", trigger.sender)
        return

    args = (trigger.group(2) or '').strip().split()
    if not args:
        bot.reply(f"Usage: {p}spawn <animal> [rarity]", trigger.sender)
        return

    species = args[0].lower()
    rarity = args[1].lower() if len(args) > 1 else 'common'
    animal = _spawn_animal(bot, channel, forced_species=species, forced_rarity=rarity)
    if not animal:
        bot.reply(f"Failed to spawn {species}.", trigger.sender)


@plugin.command('hunttoggle', 'huntenable', 'huntdisable')
@plugin.example('$hunttoggle on')
@plugin.example('$hunttoggle #channel on')
def cmd_hunttoggle(bot, trigger):
    """Enable or disable hunting in a channel.
    Usage in channel: $hunttoggle [on|off]
    Usage in PM:      $hunttoggle #channel [on|off]
    Aliases: $huntenable, $huntdisable
    """
    p = _get_prefix(bot)
    raw_cmd = (trigger.group(1) or '').lower().strip()
    args = (trigger.group(2) or '').strip().split()
    in_channel = str(trigger.sender).startswith('#')

    target_channel = None
    action = None

    if in_channel:
        target_channel = str(trigger.sender)
        if raw_cmd == 'huntenable':
            action = 'on'
        elif raw_cmd == 'huntdisable':
            action = 'off'
        elif args:
            action = args[0].lower()
    else:
        # PM context: first arg could be #channel
        if raw_cmd == 'huntenable':
            action = 'on'
            if args and args[0].startswith('#'):
                target_channel = args[0]
        elif raw_cmd == 'huntdisable':
            action = 'off'
            if args and args[0].startswith('#'):
                target_channel = args[0]
        elif args:
            if args[0].startswith('#'):
                target_channel = args[0]
                if len(args) > 1:
                    action = args[1].lower()
            else:
                action = args[0].lower()

    if not target_channel:
        # Show all channel statuses
        enabled_list = _get_enabled_channels(bot)
        bot.say("🌲 Hunting Game Channel Statuses:", trigger.nick)
        bot.say("  Default for ALL channels: DISABLED 🔒 (must be explicitly enabled per channel)", trigger.nick)
        if enabled_list:
            for ch in sorted(enabled_list):
                bot.say(f"  {ch}: ENABLED ✅", trigger.nick)
        else:
            bot.say("  No channels are currently enabled.", trigger.nick)
        bot.say(f"Usage: {p}hunttoggle #channel [on|off] (admin only in PM, or channel ops in-channel)", trigger.nick)
        return

    # Check permission using dynamic Sopel / ibot admin and channel op rights
    if not _can_manage_channel(bot, trigger, target_channel):
        bot.reply(f"🚫 You must be a channel operator in {target_channel} or a bot admin to toggle hunting.")
        return

    def _reply(msg):
        if in_channel and target_channel.lower() == str(trigger.sender).lower():
            bot.say(msg)
        else:
            bot.say(msg, trigger.nick)

    if action in ('on', 'enable', '1', 'true'):
        _set_channel_enabled(bot, target_channel, True)
        msg = (
            f"🌲 Hunting is now {GREEN}ENABLED{RESET} in {target_channel}! "
            f"Type {BOLD}{p}hunthelp{RESET} for commands & how to play. "
            f"Grab your gear ({p}gun, {p}huntshop, {p}permits). Wildlife will begin appearing."
        )
        _reply(msg)
        if not in_channel:
            bot.say(msg, target_channel)
    elif action in ('off', 'disable', '0', 'false'):
        _set_channel_enabled(bot, target_channel, False)
        with _animal_lock:
            _active_animals[target_channel.lower()] = []
        msg = f"🔒 Hunting is now {RED}DISABLED{RESET} in {target_channel}. All active animals have scattered."
        _reply(msg)
        if not in_channel:
            bot.say(msg, target_channel)
    else:
        is_en = _plugin_enabled(bot, target_channel)
        st = f"{GREEN}ENABLED ✅{RESET}" if is_en else f"{RED}DISABLED 🔒{RESET}"
        if is_en:
            _reply(f"🌲 Hunting in {target_channel}: {st}. Type {BOLD}{p}hunthelp{RESET} for commands. Toggle with: {p}hunttoggle [on|off]")
        else:
            _reply(f"🌲 Hunting in {target_channel}: {st} (Disabled by default in all channels. Enable with: {p}hunttoggle on)")


@plugin.command('huntrate', 'huntinterval', 'spawnrate')
@plugin.example('$huntrate normal')
@plugin.example('$huntrate slow')
@plugin.example('$huntrate 10 15')
def cmd_huntrate(bot, trigger):
    """View or adjust wildlife spawn rates.
    Usage: $huntrate [slow|normal|fast|<min_minutes> <max_minutes>]
    Presets:
      slow:   10 to 20 minutes
      normal: 6 to 12 minutes (default)
      fast:   3 to 6 minutes
    """
    p = _get_prefix(bot)
    args = (trigger.group(2) or '').strip().split()
    s_min, s_max = _get_spawn_rates(bot)

    if not args:
        bot.reply(
            f"🌲 Critter Spawn Rate: Every {s_min//60} to {s_max//60} minutes. "
            f"Presets: {p}huntrate slow (10–20m), {p}huntrate normal (6–12m), {p}huntrate fast (3–6m).",
            trigger.sender
        )
        return

    # Check admin permission
    if not _is_bot_admin(bot, trigger):
        bot.reply("🚫 Only bot admins can adjust the critter spawn rate.", trigger.sender)
        return

    preset = args[0].lower()
    if preset in ('slow', 'chill', 'calm'):
        new_min, new_max = 600, 1200  # 10 to 20 minutes
    elif preset in ('normal', 'default', 'med', 'medium'):
        new_min, new_max = 360, 720   # 6 to 12 minutes
    elif preset in ('fast', 'rapid', 'quick'):
        new_min, new_max = 180, 360   # 3 to 6 minutes
    elif preset.isdigit():
        new_min = int(preset) * 60
        new_max = int(args[1]) * 60 if len(args) > 1 and args[1].isdigit() else new_min * 2
        new_min = max(60, new_min)
        new_max = max(new_min + 60, new_max)
    else:
        bot.reply(f"Usage: {p}huntrate [slow|normal|fast|<min_minutes> <max_minutes>]", trigger.sender)
        return

    try:
        with _db_lock, _get_conn() as conn:
            conn.execute(
                "INSERT INTO hunting_globals (key, value) VALUES ('spawn_rates', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps([new_min, new_max]),)
            )
            conn.commit()
    except Exception as e:
        LOG.exception("Failed to save spawn rates: %s", e)

    bot.reply(f"🌲 Updated Critter Spawn Rate! Animals will now spawn every {new_min//60} to {new_max//60} minutes.", trigger.sender)


@plugin.command('hunthelp', 'huntinghelp', 'hunt')
@plugin.example('$hunthelp')
@plugin.example('$hunt')
def cmd_hunthelp(bot, trigger):
    """View the hunting game help and quick-start reference."""
    nick = trigger.nick
    p = _get_prefix(bot)
    if trigger.sender.startswith('#'):
        bot.reply(f"📨 {nick}: check your PM for hunting game help.", trigger.sender)

    lines = [
        "🌲 IRC Hunting Game Help (hunt.py) 🦌🎯",
        "",
        "🎯 Hunting & Shooting:",
        f"  • {p}shoot [species] — Shoot at active wildlife in channel (auto-targets oldest eligible if omitted)",
        f"  • {p}huntable — View active wildlife in current channel, HP, and permit clearance",
        f"  • {p}reloadgun — Reload magazine or clear a mechanical jam (aliases: {p}loadgun, {p}unjam, {p}rechamber, {p}mag)",
        f"  • {p}conditions — Current field weather and shot accuracy modifiers",
        "",
        "🔫 Gear & Maintenance:",
        f"  • {p}gun — Inspect firearm level, condition, lubrication, attachments, and accuracy",
        f"  • {p}grease — Apply grease to restore 100% lubrication and prevent jams",
        f"  • {p}repair — Use a repair kit to restore 35% rifle condition",
        f"  • {p}huntshop — Buy ammunition, upgrades (L1–L10), scopes, silencers, food box",
        f"  • {p}permits — View and purchase permits (Game Bird, Medium, Big, Trophy, Legendary)",
        "",
        "🎒 Bag, Economy & Rescues:",
        f"  • {p}bag — View harvested trophies and loot",
        f"  • {p}sell all — Sell held trophies for coins (or spendable XP in standalone)",
        f"  • {p}feed — Feed stray cats & lost dogs to rescue them (+Friend Credit)",
        f"  • {p}mystats — Lifetime harvests, rank, trophies, and XP",
        f"  • {p}guide [species] — Wildlife field guide data",
        f"  • {p}top [exp|kills|trophies|friends] — Top hunter leaderboards",
        "",
        "⚙️ Channel Operators / Admins:",
        f"  • {p}hunttoggle [on|off] — Enable/disable hunting in current channel",
        f"  • {p}hunttoggle #channel [on|off] — Target a specific channel (in PM)",
        "  • Hunting is DISABLED by default in ALL channels until enabled!",
    ]

    for line in lines:
        bot.say(line, nick)
