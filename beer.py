"""
Sopel Bartender Module - Your friendly virtual bartender!
Serves up random beers, shots, whiskeys, cocktails, and pizza.
Uses unified bot.db for persistence (same as mug.py).
Deducts from mug game coins instead of maintaining separate balance.
"""
import functools
import random
import sys
import time
import logging
from contextlib import contextmanager
import atexit
import threading
import unicodedata
import os
import sqlite3
import json
from sopel import module
import re


LOG = logging.getLogger(__name__)
PLUGIN_NAME = 'beer'
MUG_GAME_PLUGIN = 'mug_game'
NEW_USER_STARTING_COINS = 1000  # Starting coins for users on first drink purchase
DAILY_BONUS_COINS = 100  # Bonus coins given once per 24 hours

# IRC format/control regex patterns (from mug.py)
_MIRC_COLOR_RE = re.compile(r'\x03(?:\d{1,2})?(?:,\d{1,2})?')
_IRC_FORMAT_RE = re.compile(r'[\x02\x1d\x1f\x11\x16]')
_IRC_CTRL_RE = re.compile(r'[\x00-\x08\x0b-\x0c\x0e-\x1a]')


def _normalize_nick(nick: str) -> str:
    """Normalize nickname following mug.py standards."""
    if not nick:
        return ""
    s = str(nick)
    s = unicodedata.normalize('NFKC', s)
    s = _MIRC_COLOR_RE.sub('', s)
    s = _IRC_FORMAT_RE.sub('', s)
    s = _IRC_CTRL_RE.sub('', s)
    return s.strip()


def _user_key(user):
    """Normalize user identifier for storage (use lowercase strings)."""
    return _normalize_nick(user).lower()


def _to_1337(text):
    """Convert text to 1337 speak while preserving IRC formatting codes and emojis."""
    if not text:
        return text
    table = {
        'a': '4', 'A': '4',
        'b': '8', 'B': '8',
        'e': '3', 'E': '3',
        'g': '9', 'G': '9',
        'i': '1', 'I': '1',
        'l': '1', 'L': '1',
        'o': '0', 'O': '0',
        's': '5', 'S': '5',
        't': '7', 'T': '7',
        'z': '2', 'Z': '2',
    }
    pattern = r'(\x03\d{0,2}(?:,\d{1,2})?|\x02|\x0f|\x16|\x1d|\x1f)'
    parts = re.split(pattern, text)
    res = []
    for part in parts:
        if not part:
            continue
        if part.startswith('\x03') or part in ('\x02', '\x0f', '\x16', '\x1d', '\x1f'):
            res.append(part)
        else:
            res.append(''.join(table.get(c, c) for c in part))
    return ''.join(res)



# ---------------------------------------------------------------------------
# Mug-game coin storage
#
# Balances belong to mug.py, which keeps them in an in-memory cache and saves
# that cache to bot.db.  Writing bot.db directly from here (as this plugin used
# to) raced with mug.py: either side could overwrite the other's update.  When
# mug.py is loaded we now read/write ITS live data under ITS lock (mug is the
# single owner); only when mug.py isn't loaded do we fall back to bot.db.
# ---------------------------------------------------------------------------

_MUG_MODULE_NAMES = ('ibot_plugins.mug', 'sopel_modules.mug', 'scripts.mug', 'mug')
_MUG_FALLBACK_LOCK = threading.RLock()
_mug_tls = threading.local()


def _mug_module():
    for name in _MUG_MODULE_NAMES:
        mod = sys.modules.get(name)
        if (mod is not None and hasattr(mod, '_data_lock')
                and hasattr(mod, '_load_data') and hasattr(mod, '_save_data')):
            return mod
    return None


@contextmanager
def _mug_txn(bot):
    """Hold the coin lock for a read-modify-write.  Saves requested through
    _save_mug_data() inside the block are deferred until the lock is released
    (mug's saver has slow side effects: topic updates and voice sweeps)."""
    mod = _mug_module()
    lock = mod._data_lock if mod is not None else _MUG_FALLBACK_LOCK
    depth = getattr(_mug_tls, 'depth', 0)
    with lock:
        _mug_tls.depth = depth + 1
        if depth == 0:
            _mug_tls.dirty = False
        try:
            yield
        finally:
            _mug_tls.depth = depth
    if depth == 0 and getattr(_mug_tls, 'dirty', False):
        _mug_tls.dirty = False
        if mod is not None:
            try:
                mod._save_data(bot)
            except Exception:
                LOG.exception('beer: mug save failed')


def _mug_locked(func):
    @functools.wraps(func)
    def wrapper(bot, *args, **kwargs):
        with _mug_txn(bot):
            return func(bot, *args, **kwargs)
    return wrapper


def _load_mug_data(bot):
    """Return the mug game data dict (mug.py's live copy when loaded)."""
    mod = _mug_module()
    if mod is not None:
        return mod._load_data(bot)
    try:
        data = bot.db.get_plugin_value(MUG_GAME_PLUGIN, 'data')
        if isinstance(data, dict):
            return data
    except (AttributeError, KeyError, TypeError) as e:
        LOG.warning(f"Failed to load mug game data: {e}")
    return {'users': {}}


def _save_mug_data(bot, data):
    """Persist mug game data.  With mug.py loaded this delegates to mug's own
    saver (deferred until the surrounding _mug_txn releases the lock)."""
    mod = _mug_module()
    if mod is not None:
        if getattr(_mug_tls, 'depth', 0) > 0:
            _mug_tls.dirty = True
            return True
        try:
            mod._save_data(bot)
        except Exception as e:
            LOG.error(f"ERROR saving mug game data via mug.py: {e}")
            return False
        return True
    try:
        bot.db.set_plugin_value(MUG_GAME_PLUGIN, 'data', data)
    except (AttributeError, TypeError, ValueError) as e:
        LOG.error(f"ERROR saving mug game data: {e}")
        return False
    return True


def _load_tip_data(bot):
    """Load tip leaderboard data from bot.db.

    Only 'tips_received' is used; balances live in mug game coins.
    """
    data = None
    try:
        data = bot.db.get_plugin_value(PLUGIN_NAME, 'tips')
    except (AttributeError, KeyError, TypeError) as e:
        LOG.warning(f"Failed to load tip data: {e}")
    if not isinstance(data, dict):
        data = {}
    data.setdefault('tips_received', {})
    return data


def _save_tip_data(bot, data):
    """Save tip data to bot.db atomically."""
    try:
        bot.db.set_plugin_value(PLUGIN_NAME, 'tips', data)
    except (AttributeError, TypeError, ValueError) as e:
        LOG.error(f"ERROR saving tip data: {e}")
        return False
    return True


@_mug_locked
def _get_user_coins(bot, user):
    """Get user's current mug game coin balance."""
    data = _load_mug_data(bot)
    users = data.get('users', {})
    user_key = _user_key(user)
    
    if user_key in users and isinstance(users[user_key], dict):
        return int(users[user_key].get('money', 0))
    return 0


def _touch_mug_user(data, user, current_time):
    """Get or create a user's mug game record, applying the daily bonus if due.

    Returns (user_rec, was_new, got_daily).
    """
    users = data.setdefault('users', {})
    user_key = _user_key(user)

    if user_key not in users or not isinstance(users[user_key], dict):
        # New user: give them starting coins
        users[user_key] = {
            'nick': user,
            'money': NEW_USER_STARTING_COINS,
            'inv': {},
            'last_bar_credit': current_time
        }
        LOG.debug(f"New user {user_key} initialized with {NEW_USER_STARTING_COINS} starting coins")
        return users[user_key], True, False

    user_rec = users[user_key]

    # Check for daily bonus (24 hour cooldown)
    if current_time - user_rec.get('last_bar_credit', 0) >= 86400:
        user_rec['money'] = int(user_rec.get('money', 0)) + DAILY_BONUS_COINS
        user_rec['last_bar_credit'] = current_time
        LOG.debug(f"User {user_key} received daily bonus of {DAILY_BONUS_COINS} coins")
        return user_rec, False, True

    return user_rec, False, False


@_mug_locked
def _deduct_mug_coins(bot, user, amount):
    """Deduct coins from user's mug game balance.
    Returns (new_balance, success, was_new_user, got_daily_bonus) where:
    - new_balance: resulting balance or None if insufficient
    - success: True if deduction succeeded
    - was_new_user: True if this was their first time (got starting coins)
    - got_daily_bonus: True if they received daily bonus coins
    """
    if amount <= 0:
        balance = _get_user_coins(bot, user)
        return balance, True, False, False

    data = _load_mug_data(bot)
    user_key = _user_key(user)
    user_rec, was_new, got_daily = _touch_mug_user(data, user, time.time())
    current_balance = int(user_rec.get('money', 0))

    # Check if user has enough
    if current_balance < amount:
        # Save any daily bonus that was just added
        if got_daily:
            _save_mug_data(bot, data)
        return None, False, was_new, got_daily  # Insufficient funds
    
    # Deduct the amount
    new_balance = current_balance - amount
    user_rec['money'] = new_balance
    
    # Save back to bot.db
    if _save_mug_data(bot, data):
        LOG.debug(f"Deducted {amount} coins from {user_key}: {current_balance} -> {new_balance}")
        return new_balance, True, was_new, got_daily
    
    LOG.error(f"Failed to save mug data after deducting {amount} from {user_key}")
    return None, False, was_new, got_daily

# Prices for items

PRICES = {
    'scotch': 15,
    'beer': 5,
    'b33r': 5,
    'shot': 7,
    'whiskey': 12,
    'irish': 10,
    'vodka': 10,
    'rum': 10,
    'tequila': 10,
    'gin': 10,
    'brandy': 12,
    'mixed_drink': 10,
    'margarita': 9,
    'sake': 9,
    'liqueur': 8,
    'mead': 7,
    'wine': 8,
    'cava': 8,
    'magners': 6,
    'mocktail': 4,
    'coffee': 3,
    'decaf': 3,
    'tea': 3,
    'water': 0,  # Water is free!
    'pizza': 15,
    'appetizer': 8,
}


def deduct_price(bot, user, item_type):
    """Deduct price from user's mug game coins.
    Returns (new_balance, price, was_new_user, got_daily_bonus)
    """
    price = PRICES.get(item_type, 0)
    
    # Free items don't require deduction
    if price <= 0:
        balance = _get_user_coins(bot, user)
        return balance, price, False, False
    
    # Deduct from mug game coins
    new_balance, success, was_new, got_daily = _deduct_mug_coins(bot, user, price)
    
    if not success:
        return None, price, was_new, got_daily
    
    return new_balance, price, was_new, got_daily


# Item lookup: maps item type to (item_list, message_template_list, placeholder_key)
DRINK_REGISTRY = {}
_last_served_items = {}
_last_served_lock = threading.Lock()


def _choose_item(item_type, item_list):
    """Choose an item without repeating the previous item of this type."""
    with _last_served_lock:
        previous_item = _last_served_items.get(item_type)
        choices = [item for item in item_list if item != previous_item]
        chosen_item = random.choice(choices or item_list)
        _last_served_items[item_type] = chosen_item
        return chosen_item


BOT_DRINK_RECEPTION_MESSAGES = [
    "happily accepts {drink} from {sender}, raises a glass 🍻 and cheers!",
    "thanks {sender}, takes a nice big sip of {drink} 🍺 and smiles!",
    "catches {drink} slid down the bar by {sender}, takes a gulp 🍻 and nods in thanks!",
    "sparks up a smile, toasts {sender} with {drink} 🥃 and takes a smooth sip!",
    "pours a bit of {drink} out for the channel, thanks {sender}, and drinks up! 🍻",
]

BOT_FOOD_RECEPTION_MESSAGES = [
    "happily accepts {food} from {sender}, takes a big bite 🍕 and says thanks!",
    "grabs {food} courtesy of {sender}, munches away 🍿 and gives a thumbs up!",
    "thanks {sender} for {food}, takes a delicious bite 🍔 and grins!",
]


HARD_ALCOHOL_TYPES = {
    'shot', 'whiskey', 'scotch', 'irish', 'vodka', 'rum',
    'tequila', 'gin', 'brandy', 'margarita', 'liqueur', 'mixed_drink',
    'irish_coffee',
}
LIGHT_ALCOHOL_TYPES = {
    'beer', 'magners', 'wine', 'cava', 'mead', 'sake'
}
SOBERING_COFFEE_TYPES = {'coffee', 'decaf'}
SOBERING_FOOD_TYPES = {'pizza', 'appetizer'}
SOBERING_HYDRATION_TYPES = {'water', 'tea', 'mocktail'}

DRUNK_TITLES = {
    1: 'tipsy',
    2: 'drunk',
    3: 'hammered',
    4: 'plastered',
    5: 'blackout wasted'
}


def _format_dur(minutes: int) -> str:
    """Format minutes into human-readable duration."""
    if minutes >= 60:
        h = minutes // 60
        m = minutes % 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def _get_grok_db_path(bot):
    """Retrieve the grok.sqlite3 path from bot.memory or standard location (ibot-safe)."""
    core = getattr(bot, '_bot', bot)
    if hasattr(core, 'memory') and core.memory.get('grok_db_path'):
        return core.memory.get('grok_db_path')
    if hasattr(bot, 'memory') and bot.memory.get('grok_db_path'):
        return bot.memory.get('grok_db_path')
    base_dir = os.environ.get('AI_GROK_DIR') or os.path.join(os.path.dirname(__file__), 'grok_data')
    return os.path.join(base_dir, 'grok.sqlite3')


def _get_effects_cache(bot):
    """Retrieve or create the grok_channel_effects dict across Sopel / ibot."""
    core = getattr(bot, '_bot', bot)
    if hasattr(core, 'memory'):
        return core.memory.setdefault('grok_channel_effects', {})
    if hasattr(bot, 'memory'):
        return bot.memory.setdefault('grok_channel_effects', {})
    return {}


def _apply_bot_alcohol(bot, channel, giver, item_name, is_hard_liquor=False):
    """Apply or increase bot alcohol intoxication for the channel."""
    if not channel or not str(channel).startswith('#'):
        return 0, 1, 'tipsy'

    chan_key = str(channel).lower()
    now = time.time()
    add_duration = 3600 if is_hard_liquor else 2700  # 60m for hard liquor, 45m for beer/wine
    add_intensity = 2 if is_hard_liquor else 1

    cache = _get_effects_cache(bot)
    chan_cache = cache.setdefault(chan_key, {})
    existing = chan_cache.get('drunk')

    if existing and existing.get('expires_at', 0) > now:
        new_expires = min(existing['expires_at'] + add_duration, now + 10800)  # max 3 hours total
        new_intensity = min(existing.get('intensity', 1) + add_intensity, 5)
    else:
        new_expires = min(now + add_duration, now + 10800)
        new_intensity = min(add_intensity, 5)

    effect_data = {
        'effect': 'drunk',
        'expires_at': new_expires,
        'started_at': now,
        'giver': giver,
        'item_name': item_name,
        'intensity': new_intensity,
    }
    chan_cache['drunk'] = effect_data
    chan_cache.pop('sobered', None)

    db_path = _get_grok_db_path(bot)
    try:
        if os.path.exists(os.path.dirname(db_path)):
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS grok_channel_effects (
                        channel TEXT NOT NULL,
                        effect TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        started_at REAL NOT NULL,
                        giver TEXT,
                        item_name TEXT,
                        intensity INTEGER DEFAULT 1,
                        PRIMARY KEY (channel, effect)
                    )
                ''')
                conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect = ?', (chan_key, 'sobered'))
                conn.execute('''
                    INSERT OR REPLACE INTO grok_channel_effects
                    (channel, effect, expires_at, started_at, giver, item_name, intensity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (chan_key, 'drunk', new_expires, now, giver, item_name, new_intensity))
                conn.commit()
    except Exception as e:
        LOG.exception("Failed to write drunk effect to DB: %s", e)

    remaining_mins = max(1, int((new_expires - now) // 60))
    title = DRUNK_TITLES.get(new_intensity, 'drunk')
    return remaining_mins, new_intensity, title


def _sober_bot(bot, channel, giver, item_name, item_type):
    """Sober up the bot using coffee, food, water, etc.
    
    Returns: (action_type, details)
    action_type can be:
      - 'sobered_both'
      - 'sobered_drunk'
      - 'sobered_stoned'
      - 'reduced_drunk'
      - 'none' (wasn't intoxicated)
    """
    if not channel or not str(channel).startswith('#'):
        return 'none', {}

    chan_key = str(channel).lower()
    now = time.time()
    cache = _get_effects_cache(bot)
    chan_cache = cache.get(chan_key)
    if chan_cache is None:
        chan_cache = {}
        cache[chan_key] = chan_cache

    # If cache is missing effects, try loading active effects from DB
    if not chan_cache:
        db_path = _get_grok_db_path(bot)
        if os.path.exists(db_path):
            try:
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    conn.execute('DELETE FROM grok_channel_effects WHERE expires_at <= ?', (now,))
                    cur = conn.execute(
                        'SELECT effect, expires_at, started_at, giver, item_name, intensity '
                        'FROM grok_channel_effects WHERE channel = ? AND expires_at > ?',
                        (chan_key, now)
                    )
                    for row in cur.fetchall():
                        eff_name = row[0]
                        chan_cache[eff_name] = {
                            'effect': eff_name,
                            'expires_at': row[1],
                            'started_at': row[2],
                            'giver': row[3],
                            'item_name': row[4],
                            'intensity': row[5],
                        }
            except Exception:
                pass

    had_drunk = 'drunk' in chan_cache and chan_cache['drunk'].get('expires_at', 0) > now
    had_stoned = 'stoned' in chan_cache and chan_cache['stoned'].get('expires_at', 0) > now
    had_tripping = 'tripping' in chan_cache and chan_cache['tripping'].get('expires_at', 0) > now

    if not had_drunk and not had_stoned and not had_tripping:
        return 'none', {}

    # Capture details of what the bot was on before altering
    prev_drunk = chan_cache.get('drunk') if had_drunk else None
    prev_stoned = chan_cache.get('stoned') if had_stoned else None
    prev_tripping = chan_cache.get('tripping') if had_tripping else None

    if had_drunk and had_stoned:
        prev_effect = 'crossfaded (both baked and drunk)'
        prev_giver = f"{prev_stoned.get('giver', 'someone')} and {prev_drunk.get('giver', 'someone')}"
        prev_item = f"{prev_stoned.get('item_name', 'weed')} and {prev_drunk.get('item_name', 'drinks')}"
    elif had_stoned:
        prev_effect = 'baked and high on weed'
        prev_giver = prev_stoned.get('giver', 'someone')
        prev_item = prev_stoned.get('item_name', 'weed')
    elif had_drunk:
        prev_effect = f"{DRUNK_TITLES.get(prev_drunk.get('intensity', 1), 'drunk')}"
        prev_giver = prev_drunk.get('giver', 'someone')
        prev_item = prev_drunk.get('item_name', 'alcohol')
    else:
        prev_effect = 'tripping on psychedelics'
        prev_giver = prev_tripping.get('giver', 'someone')
        prev_item = prev_tripping.get('item_name', 'psychedelics')

    sober_levels = 2 if (item_type in SOBERING_COFFEE_TYPES or item_type == 'pizza') else 1

    cleared_drunk = False
    new_drunk_intensity = 0
    new_drunk_mins = 0
    new_drunk_title = ''
    cleared_stoned = False
    cleared_tripping = False

    # Handle weed high: coffee clears it completely; food satisfies munchies and reduces/clears it
    if had_stoned:
        if item_type in SOBERING_COFFEE_TYPES:
            cleared_stoned = True
            chan_cache.pop('stoned', None)
        elif item_type in (SOBERING_FOOD_TYPES | SOBERING_HYDRATION_TYPES):
            st_intensity = chan_cache['stoned'].get('intensity', 1)
            if st_intensity <= 1:
                cleared_stoned = True
                chan_cache.pop('stoned', None)
            else:
                chan_cache['stoned']['intensity'] = st_intensity - 1

    # Handle psychedelics: coffee or hydration helps sober up
    if had_tripping and (item_type in SOBERING_COFFEE_TYPES or item_type in SOBERING_FOOD_TYPES or item_type in SOBERING_HYDRATION_TYPES):
        cleared_tripping = True
        chan_cache.pop('tripping', None)

    # Handle alcohol
    if had_drunk:
        current_intensity = chan_cache['drunk'].get('intensity', 1)
        if current_intensity <= sober_levels:
            cleared_drunk = True
            chan_cache.pop('drunk', None)
        else:
            new_drunk_intensity = current_intensity - sober_levels
            chan_cache['drunk']['intensity'] = new_drunk_intensity
            curr_exp = chan_cache['drunk']['expires_at']
            new_exp = max(now + 600, curr_exp - (1800 * sober_levels))
            chan_cache['drunk']['expires_at'] = new_exp
            new_drunk_mins = max(1, int((new_exp - now) // 60))
            new_drunk_title = DRUNK_TITLES.get(new_drunk_intensity, 'tipsy')

    # Check if completely sober now
    is_now_completely_sober = (
        ('drunk' not in chan_cache or chan_cache['drunk'].get('expires_at', 0) <= now) and
        ('stoned' not in chan_cache or chan_cache['stoned'].get('expires_at', 0) <= now) and
        ('tripping' not in chan_cache or chan_cache['tripping'].get('expires_at', 0) <= now)
    )

    # Update DB
    db_path = _get_grok_db_path(bot)
    try:
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                if cleared_drunk:
                    conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect = ?', (chan_key, 'drunk'))
                elif had_drunk:
                    conn.execute('UPDATE grok_channel_effects SET intensity = ?, expires_at = ? WHERE channel = ? AND effect = ?',
                                 (new_drunk_intensity, chan_cache['drunk']['expires_at'], chan_key, 'drunk'))

                if cleared_stoned:
                    conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect = ?', (chan_key, 'stoned'))
                elif had_stoned and item_type in (SOBERING_FOOD_TYPES | SOBERING_HYDRATION_TYPES):
                    conn.execute('UPDATE grok_channel_effects SET intensity = ? WHERE channel = ? AND effect = ?',
                                 (chan_cache['stoned']['intensity'], chan_key, 'stoned'))

                if cleared_tripping:
                    conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect = ?', (chan_key, 'tripping'))

                if is_now_completely_sober:
                    sober_payload = {
                        'prev_effect': prev_effect,
                        'prev_giver': prev_giver,
                        'prev_item': prev_item,
                        'sober_giver': giver,
                        'sober_item': item_name,
                    }
                    sober_json = json.dumps(sober_payload)
                    chan_cache['sobered'] = {
                        'effect': 'sobered',
                        'expires_at': now + 3600,
                        'started_at': now,
                        'giver': giver,
                        'item_name': sober_json,
                        'intensity': 0,
                        **sober_payload
                    }
                    conn.execute('''
                        INSERT OR REPLACE INTO grok_channel_effects
                        (channel, effect, expires_at, started_at, giver, item_name, intensity)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (chan_key, 'sobered', now + 3600, now, giver, sober_json, 0))

                conn.commit()
    except Exception as e:
        LOG.exception("Failed to update DB while sobering bot: %s", e)

    if cleared_drunk and cleared_stoned:
        return 'sobered_both', {}
    elif is_now_completely_sober:
        if had_drunk:
            return 'sobered_drunk', {}
        elif had_stoned:
            return 'sobered_stoned', {}
        else:
            return 'sobered_both', {}
    elif had_drunk and not cleared_drunk:
        return 'reduced_drunk', {'intensity': new_drunk_intensity, 'title': new_drunk_title, 'mins': new_drunk_mins}
    elif cleared_stoned:
        return 'sobered_stoned', {}

    return 'none', {}


def _serve_item(bot, trigger, item_type, item_list, message_list, placeholder_key='drink', target_override=None, is_1337=False, effect_type=None):
    """Generic handler for serving any drink or food item.
    
    Args:
        bot: Sopel bot
        trigger: IRC trigger
        item_type: Key in PRICES dict
        item_list: List of drink/food options
        message_list: List of message templates
        placeholder_key: 'drink' or 'food' for message formatting
        target_override: Optional explicit target user
    """
    try:
        # Parse target user (may be empty) - ibot safe
        if target_override:
            target_user = target_override
        else:
            try:
                raw_target = trigger.group(2)
            except (IndexError, AttributeError):
                raw_target = None

            if not raw_target:
                target_user = trigger.nick
            else:
                target_user = raw_target.strip().split()[0]
        
        # Deduct price
        sender = trigger.account or trigger.nick
        new_balance, price, was_new_user, got_daily_bonus = deduct_price(bot, sender, item_type)
        
        if new_balance is None:
            current_balance = _get_user_coins(bot, sender)
            bot.say(f"{trigger.nick}: You don't have enough coins! {item_type.capitalize()} costs {price} coins. You have {current_balance} coins.")
            return
        
        # Select random item and message
        chosen_item = _choose_item(item_type, item_list)
        if item_type == 'sake':
            message_list = _sake_messages_for(chosen_item)
        elif item_type in ('coffee', 'decaf', 'irish_coffee'):
            message_list = _coffee_messages_for(chosen_item)

        effect = effect_type or item_type
        
        # Format and send message
        bot_nick = (getattr(bot, 'nick', '') or '').lower()
        if target_user.lower() == bot_nick:
            if placeholder_key == 'food':
                giving_message = random.choice(BOT_FOOD_RECEPTION_MESSAGES)
            else:
                giving_message = random.choice(BOT_DRINK_RECEPTION_MESSAGES)
            base_message = giving_message.format(**{placeholder_key: chosen_item, 'sender': trigger.nick, 'user': trigger.nick})

            channel = trigger.sender
            status_tag = ""
            if channel and str(channel).startswith('#'):
                if effect in HARD_ALCOHOL_TYPES:
                    mins, intensity, title = _apply_bot_alcohol(bot, channel, trigger.nick, chosen_item, is_hard_liquor=True)
                    status_tag = f" ({bot.nick} is {title} in {channel} ~{_format_dur(mins)})"
                elif effect in LIGHT_ALCOHOL_TYPES:
                    mins, intensity, title = _apply_bot_alcohol(bot, channel, trigger.nick, chosen_item, is_hard_liquor=False)
                    status_tag = f" ({bot.nick} is {title} in {channel} ~{_format_dur(mins)})"
                elif effect in (SOBERING_COFFEE_TYPES | SOBERING_FOOD_TYPES | SOBERING_HYDRATION_TYPES):
                    action_type, details = _sober_bot(bot, channel, trigger.nick, chosen_item, effect)
                    if action_type == 'sobered_both':
                        status_tag = f" ({bot.nick} is completely sober and down to earth in {channel})"
                    elif action_type == 'sobered_drunk':
                        status_tag = f" ({bot.nick} has sobered up in {channel})"
                    elif action_type == 'sobered_stoned':
                        status_tag = f" ({bot.nick} sobered up and the high wore off in {channel})"
                    elif action_type == 'reduced_drunk':
                        status_tag = f" ({bot.nick} soaked up some booze, down to {details['title']} in {channel} ~{_format_dur(details['mins'])})"

            message = base_message + status_tag
        else:
            giving_message = random.choice(message_list)
            message = giving_message.format(**{placeholder_key: chosen_item, 'user': target_user, 'sender': trigger.nick})
        
        if is_1337 or item_type == 'b33r':
            message = _to_1337(message)

        bot.action(message)
        
        # Send balance update via PM
        if price > 0:
            if was_new_user:
                bot.notice(f"Welcome to the bar! 🍺 You received {NEW_USER_STARTING_COINS} starting coins.", trigger.nick)
                bot.notice(f"Paid {price} coins - Remaining balance: {new_balance} coins 🪙", trigger.nick)
            elif got_daily_bonus:
                bot.notice(f"Daily bonus! +{DAILY_BONUS_COINS} coins 💰", trigger.nick)
                bot.notice(f"Paid {price} coins - Remaining balance: {new_balance} coins 🪙", trigger.nick)
            else:
                bot.notice(f"Paid {price} coins - Remaining balance: {new_balance} coins 🪙", trigger.nick)
    
    except Exception as e:
        LOG.error(f"Error in _serve_item for {item_type}: {e}")
        try:
            bot.say(f"{trigger.nick}: An error occurred. Sorry!")
        except Exception:
            pass

# List of fun beers to give out
BEERS = [
    "a frosty Guinness 🍺",
    "an ice-cold Heineken 🍻",
    "a refreshing Corona with lime 🍋🍺",
    "a crisp Pilsner Urquell 🍺",
    "a hoppy IPA 🌿🍺",
    "a smooth Stella Artois 🍺",
    "a Belgian Trappist ale 🍺✨",
    "a fruity Lambic 🍓🍺",
    "a dark porter 🖤🍺",
    "a craft wheat beer 🌾🍺",
    "a chilled Budweiser 🧊🍺",
    "a golden lager 💛🍺",
    "a tasty amber ale 🍺",
    "a strong Belgian triple 💪🍺",
    "a refreshing pale ale 🍺",
    "a German weissbier 🇩🇪🍺",
    "a Japanese Asahi Super Dry 🇯🇵🍺",
    "a Mexican Modelo Especial 🇲🇽🍺",
    "a smooth stout 🍺",
    "a citrusy session IPA 🍊🍺",
    "a hazy New England IPA 🌫️🍺",
    "a crisp Czech Pilsner 🇨🇿🍺",
    "a smooth Irish red ale 🇮🇪🍺",
    "a funky farmhouse saison 🌾🍺",
    "a rich milk stout 🥛🖤🍺",
    "a juicy double IPA 🍊💪🍺",
    "a refreshing Kölsch 🇩🇪🍺",
    "a chocolatey oatmeal stout 🍫🍺",
    "a zesty gose with sea salt 🧂🍺",
    "a bold Russian imperial stout 🖤💪🍺",
    "a tart Berliner Weisse 🍋🍺",
    "a barrel-aged imperial porter 🛢️🍺",
    "a smooth Vienna lager 🇦🇹🍺",
    "a classic British ESB 🇬🇧🍺",
    "a velvety nitro stout 🖤☁️🍺",
]

# List of fun shots to give out
SHOTS = [
    "a shot of Jägermeister 🦌🥃",
    "a tequila shot with salt & lime 🧂🍋🥃",
    "a flaming Sambuca 🔥🥃",
    "a smooth vodka shot 🧊🥃",
    "a shot of Fireball 🔥🌶️🥃",
    "a B-52 shooter 💣🥃",
    "a kamikaze shot ✈️🥃",
    "a lemon drop shot 🍋✨🥃",
    "a buttery nipple 🧈🥃",
    "a mind eraser 🧠💥🥃",
    "a Vegas bomb 🎰💣🥃",
    "a shot of Patrón Silver 💎🥃",
    "a pickle back 🥒🥃",
    "a Jägerbomb 🦌💣🥃",
    "a Washington apple shot 🍎🥃",
    "a pineapple upside down shot 🍍🥃",
    "a royal flush shot 👑🥃",
    "a brain hemorrhage 🧠🩸🥃",
    "a liquid cocaine shot ⚡🥃",
    "a prairie fire 🔥🥃",
]

# List of Magners ciders to give out
MAGNERS = [
    "a crisp Magners Original Irish Cider 🍎🍺",
    "an ice-cold Magners Original over ice 🧊🍎",
    "a refreshing Magners Pear Cider 🍐✨",
    "a chilled Magners Berry Cider 🫐🍓🍺",
    "a frosty Magners Dark Fruit 🖤🍒🍺",
    "a smooth Magners Rosé Cider 🌹🍎",
    "a golden Magners on the rocks 🧊🍎🍺",
    "a crisp Magners straight from the tap 🍺🍎",
    "a perfectly chilled Magners 🌡️🍎",
    "a sweet Magners with extra ice 🧊🧊🍎",
]

# List of whiskeys to give out
WHISKEYS = [
    "a glass of smooth Jameson 🇮🇪🥃",
    "a dram of Glenfiddich 🏴🥃",
    "a pour of Jack Daniel's 🇺🇸🥃",
    "a shot of Maker's Mark 🔴🥃",
    "a glass of peaty Laphroaig 🏴💨🥃",
    "a tumbler of Bulleit Bourbon 🤠🥃",
    "a measure of Talisker 🌊🥃",
    "a dram of Macallan 12 Year 💎🥃",
    "a glass of Wild Turkey 🦃🥃",
    "a pour of Bushmills 🇮🇪🥃",
    "a shot of Crown Royal 👑🥃",
    "a glass of Johnnie Walker Black ⚫🥃",
    "a dram of Highland Park 🏔️🥃",
    "a measure of Ardbeg 🏴💚🥃",
    "a pour of Four Roses 🌹🥃",
    "a tumbler of Knob Creek 🌽🥃",
    "a glass of Redbreast 12 🇮🇪🔴🥃",
    "a shot of Woodford Reserve 🐴🥃",
    "a dram of Oban 14 🏴🥃",
    "a pour of Buffalo Trace 🦬🥃",
]

# List of high-end Scotch whiskies to give out
SCOTCHES = [
    "a dram of The Macallan 25 Year Old Sherry Oak 🏴󠁧󠁢󠁳󠁣󠁴󠁿💎👑🥃",
    "a measure of Glenfiddich 30 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿✨🥃",
    "a glass of Lagavulin 16 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🔥🥃",
    "a dram of The Balvenie 21 Year Old PortWood 🏴󠁧󠁢󠁳󠁣󠁴󠁿🪵🥃",
    "a measure of Highland Park 18 Year Old Viking Pride 🏴󠁧󠁢󠁳󠁣󠁴󠁿🛡️🥃",
    "a dram of Oban 14 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🌊🥃",
    "a pour of Laphroaig 25 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🔥💎🥃",
    "a measure of Talisker 18 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🌊🥃",
    "a glass of Ardbeg Uigeadail 🏴󠁧󠁢󠁳󠁣󠁴󠁿🔥💪🥃",
    "a dram of Glenmorangie Signet 🏴󠁧󠁢󠁳󠁣󠁴󠁿☕✨🥃",
    "a pour of Aberlour A'bunadh 🏴󠁧󠁢󠁳󠁣󠁴󠁿🍷💪🥃",
    "a dram of Springbank 18 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🏴🥃",
    "a glass of Johnnie Walker Blue Label 🏴󠁧󠁢󠁳󠁣󠁴󠁿🔵👑🥃",
    "a dram of The Macallan 18 Year Old Double Cask 🏴󠁧󠁢󠁳󠁣󠁴󠁿💎🥃",
    "a measure of Glenfarclas 25 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🍷🥃",
    "a dram of Bowmore 25 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🌊🔥🥃",
    "a pour of Dalmore 18 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🦌🥃",
    "a dram of Mortlach 20 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿🦁🥃",
    "a tumbler of Bruichladdich Octomore 🏴󠁧󠁢󠁳󠁣󠁴󠁿💨🔥🥃",
    "a dram of Royal Salute 21 Year Old 🏴󠁧󠁢󠁳󠁣󠁴󠁿👑🥃",
]

# List of Irish whiskies to give out
IRISH_WHISKEYS = [
    "a glass of smooth Jameson Irish Whiskey 🇮🇪🥃",
    "a dram of Tullamore D.E.W. 🇮🇪☘️🥃",
    "a shot of Paddy Old Irish Whiskey 🇮🇪🥃",
    "a measure of Redbreast 12 Year Old Single Pot Still 🇮🇪🐦🥃",
    "a pour of Bushmills 16 Year Old Single Malt 🇮🇪🪵🥃",
    "a dram of Teeling Small Batch Irish Whiskey 🇮🇪🔥🥃",
    "a glass of Green Spot Single Pot Still 🇮🇪🟢🥃",
    "a dram of Yellow Spot 12 Year Old 🇮🇪🟡🥃",
    "a measure of Redbreast 21 Year Old 🇮🇪🐦👑🥃",
    "a luxurious pour of Midleton Very Rare 🇮🇪💎👑🥃",
    "a tumbler of Bushmills Black Bush 🇮🇪🖤🥃",
    "a shot of Powers Gold Label 🇮🇪🏅🥃",
    "a glass of Writer's Tears Copper Pot 🇮🇪✍️🥃",
    "a dram of Slane Irish Whiskey 🇮🇪🏰🥃",
    "a pour of The Sexton Single Malt 🇮🇪💀🥃",
    "a glass of Connemara Peated Single Malt 🇮🇪🔥☘️🥃",
    "a dram of Kilbeggan Single Grain 🇮🇪🥃",
    "a measure of Glendalough Double Cask 🇮🇪🏔️🥃",
    "a pour of Roe & Co Irish Whiskey 🇮🇪🍸🥃",
    "a dram of Egan's Fortitude 🇮🇪⚜️🥃",
]

# List of vodkas to give out
VODKAS = [
    "a chilled Belvedere on the rocks 🇵🇱🧊🥃",
    "an ice-cold Grey Goose 🪿🧊🥃",
    "a smooth Ketel One 🇳🇱🥃",
    "a crisp Stolichnaya 🇷🇺🥃",
    "a premium Ciroc 🍇🥃",
    "a clean Absolut 🇸🇪🥃",
    "a silky Chopin 🎹🥃",
    "a refined Beluga Noble 🐋🥃",
    "a classic Smirnoff 🧊🥃",
    "a Polish Żubrówka Bison Grass 🦬🌿🥃",
    "a French Cîroc Pineapple 🍍🥃",
    "a crystal-clear Reyka from Iceland 🇮🇸🧊🥃",
    "a handcrafted Tito's 🇺🇸🥃",
    "a velvety Russian Standard 🇷🇺🥃",
    "a botanical Hangar 1 🌿🥃",
    "a crisp Finlandia 🇫🇮🥃",
    "a smooth Skyy 🔵🥃",
    "a premium Purity 34 🇸🇪✨🥃",
    "a fiery Absolut Peppar 🌶️🥃",
    "a sweet Absolut Vanilia 🍦🥃",
]

# List of rums to give out
RUMS = [
    "a smooth Diplomático Reserva Exclusiva 🇻🇪🥃",
    "a dark Kraken Black Spiced 🐙🏴🥃",
    "a golden Appleton Estate 🇯🇲🥃",
    "a classic Bacardi Superior 🇨🇺🥃",
    "a rich Ron Zacapa 23 🇬🇹🥃",
    "a spiced Captain Morgan 🏴‍☠️🥃",
    "a sipping Mount Gay Eclipse 🇧🇧🥃",
    "an aged Flor de Caña 18 🇳🇮🥃",
    "a smooth Havana Club 7 🇨🇺🥃",
    "a premium Plantation XO 🌴🥃",
    "a funky Smith & Cross 🇯🇲💪🥃",
    "a velvety El Dorado 15 🇬🇾✨🥃",
    "a coconut Malibu 🥥🌴🥃",
    "a dark Myers's Original 🇯🇲🖤🥃",
    "a cask-aged Pusser's 🏴‍☠️🥃",
    "a sipping Doorly's 12 🇧🇧🥃",
    "a crisp Banks 5 Island 🏝️🥃",
    "a bold Gosling's Black Seal 🇧🇲🥃",
    "a tropical Rhum J.M VSOP 🇲🇶🥃",
    "an overproof Wray & Nephew 🇯🇲🔥🥃",
]

# List of tequilas to give out
TEQUILAS = [
    "a smooth Patrón Silver 💎🥃",
    "a premium Don Julio 1942 🇲🇽👑🥃",
    "a crisp Casamigos Blanco 🇲🇽🥃",
    "a golden Herradura Reposado 🐴🥃",
    "a rich Clase Azul Reposado 🇲🇽🏺🥃",
    "a smoky Fortaleza Añejo 🔥🥃",
    "a classic Jose Cuervo Reserva 🇲🇽🥃",
    "a sipping El Tesoro Añejo 🏺🥃",
    "a fiery Espolòn Blanco 🐓🥃",
    "a velvety Ocho Reposado 🇲🇽✨🥃",
    "a top-shelf Casa Noble Crystal 💎🥃",
    "a bold Tapatio 110 🇲🇽💪🥃",
    "a smooth Avión Silver 🇲🇽✈️🥃",
    "a premium Tears of Llorona Extra Añejo 💧👑🥃",
    "a crisp Olmeca Altos Plata 🌵🥃",
    "a aged Gran Centenario Añejo 🇲🇽🥃",
    "a crystalline Código 1530 Rosa 🌹🥃",
    "a artisanal Siete Leguas Blanco 🇲🇽🌿🥃",
    "a smoky mezcal Del Maguey Vida 🔥🌵🥃",
    "a legendary Mezcal Vago Elote 🌽🔥🥃",
]

# List of gins to give out
GINS = [
    "a classic Tanqueray 🌿🥃",
    "a floral Hendrick's 🥒🌸🥃",
    "a crisp Bombay Sapphire 💎🔵🥃",
    "a bold Beefeater 🇬🇧🥃",
    "a smooth Monkey 47 🐒🌿🥃",
    "a refined Sipsmith London Dry 🇬🇧🥃",
    "a aromatic The Botanist 🏴󠁧󠁢󠁳󠁣󠁴󠁿🌿🥃",
    "a premium Aviation 🇺🇸✈️🥃",
    "a juniper-forward Plymouth 🌲🥃",
    "a citrusy Malfy Con Limone 🇮🇹🍋🥃",
    "a Spanish Gin Mare 🇪🇸🫒🥃",
    "a Japanese Roku 🇯🇵🌸🥃",
    "a pink Gordon's Pink 🩷🥃",
    "a barrel-aged Few 🇺🇸🪵🥃",
    "a summery Whitley Neill Rhubarb & Ginger 🫚🥃",
    "a classic Bols Genever 🇳🇱🥃",
    "a herbaceous St. George Terroir 🌿🏔️🥃",
    "a elegant No. 3 London Dry 🇬🇧👑🥃",
    "a tropical Tarquin's Cornish Gin 🇬🇧🌊🥃",
    "a floral Empress 1908 💜🥃",
]

# List of brandies/cognacs to give out
BRANDIES = [
    "a luxurious Hennessy XO 🇫🇷👑🥃",
    "a smooth Rémy Martin VSOP 🇫🇷🥃",
    "a refined Courvoisier VS 🇫🇷🥃",
    "a premium Martell Cordon Bleu 🇫🇷💙🥃",
    "a aged Hine Rare VSOP 🇫🇷🥃",
    "a elegant Pierre Ferrand 1840 🇫🇷✨🥃",
    "a velvety Camus XO 🇫🇷🥃",
    "a classic Torres 10 🇪🇸🥃",
    "a German Asbach Uralt 🇩🇪🥃",
    "a smooth Metaxa 7 Star 🇬🇷⭐🥃",
    "a fruity calvados Père Magloire VSOP 🍎🇫🇷🥃",
    "a rich Delamain XO 🇫🇷👑🥃",
    "a sipping Hardy VSOP 🇫🇷🥃",
    "a South African KWV 10 🇿🇦🥃",
    "a Peruvian Pisco Portón 🇵🇪🥃",
    "a Armenian Ararat 10 Year 🇦🇲🥃",
    "a Georgian Sarajishvili VS 🇬🇪🥃",
    "a Italian Vecchia Romagna 🇮🇹🥃",
    "a warming Brandy de Jerez Solera 🇪🇸🥃",
    "a exquisite Louis XIII 🇫🇷💎👑🥃",
]

# List of margaritas to give out
MARGARITAS = [
    "a classic Margarita with a salt rim 🍹🧂",
    "a frozen Strawberry Margarita 🍓🍹",
    "a spicy Jalapeño Margarita 🌶️🍹",
    "a tangy Mango Margarita 🥭🍹",
    "a top-shelf Cadillac Margarita 🍊👑🍹",
    "a smoky Mezcal Margarita 🔥🍹",
    "a refreshing Watermelon Margarita 🍉🍹",
    "a zesty lime Margarita on the rocks 🍋🧊🍹",
    "a frozen Blue Margarita 💙🍹",
    "a sweet Peach Margarita 🍑🍹",
    "a tart Prickly Pear Margarita 🌵💗🍹",
    "a coconut Margarita with a toasted rim 🥥🍹",
    "a fiery Habanero Margarita 🔥🌶️🍹",
    "a Tommy's Margarita with agave nectar 🍯🍹",
    "a blood orange Margarita 🍊❤️🍹",
    "a pineapple-cilantro Margarita 🍍🌿🍹",
    "a skinny Margarita 🍋💚🍹",
    "a black cherry Margarita 🍒🖤🍹",
    "a golden Grand Marnier Margarita 🏆🍹",
    "a Margarita flight - one of each! ✈️🍹🍹🍹",
]

# List of sakes to give out (bartender's choice — hot and cold mixed)
SAKES = [
    "a smooth Junmai Daiginjo 🍶✨",
    "a chilled Dassai 39 🍶🧊",
    "a warm Honjozo sake 🍶♨️",
    "a fragrant Kubota Manju 🍶🌸",
    "a crisp Hakkaisan Junmai 🍶🏔️",
    "a cloudy Nigori sake 🍶☁️",
    "a sparkling Mio sake 🍶🫧",
    "a legendary Juyondai 🍶👑",
    "a dry Kikusui Junmai Ginjo 🍶💧",
    "a smooth Otokoyama 🍶🗻",
    "a fruity Dewazakura Oka Ginjo 🍶🌸",
    "an aged amber Koshu sake 🍶🏺",
]

# Hot sake (atsukan / nurukan)
HOT_SAKES = [
    "a steaming atsukan Junmai 🍶♨️",
    "a warm Honjozo from the tokkuri 🍶♨️",
    "a gently warmed nurukan Ginjo 🍶🔥",
    "a piping hot flask of house sake 🍶♨️",
    "a hot cup of Otokoyama 🍶🗻♨️",
    "a toasty warmed Kikusui 🍶♨️",
    "a hot aged Koshu, amber in the cup 🍶🏺♨️",
    "a warm cloudy Nigori 🍶☁️♨️",
]

# Cold sake (reishu / hiya)
COLD_SAKES = [
    "an ice-cold Junmai Daiginjo 🍶🧊✨",
    "a frosty Dassai 39 straight from the fridge 🍶🧊",
    "a crisp chilled Hakkaisan Junmai 🍶🏔️🧊",
    "a cold Dewazakura Oka Ginjo 🍶🌸🧊",
    "a chilled sparkling Mio 🍶🫧🧊",
    "a cold cloudy Nigori over ice 🍶☁️🧊",
    "a refrigerator-cold Kubota Manju 🍶🧊",
    "a crisp cold Juyondai 🍶👑🧊",
]

# List of liqueurs & cordials to give out
LIQUEURS = [
    "a creamy Baileys Irish Cream 🥛🥃🇮🇪",
    "a nutty Disaronno Amaretto 🌰🥃",
    "an orangey Grand Marnier 🍊🥃",
    "an herbaceous green Chartreuse 💚🥃",
    "a sunny Limoncello 🍋✨",
    "a smooth Kahlúa on the rocks 🇲🇽🧊🥃",
    "a cherry Luxardo Maraschino 🍒🥃",
    "an elderflower St-Germain 🌼🥃",
    "a honeyed Drambuie 🏴🍯🥃",
    "a bittersweet Campari 🇮🇹❤️🥃",
    "a velvety Frangelico 🌰🥃",
    "a rich Chambord 🫐👑🥃",
    "a minty Crème de Menthe 🌿🥃",
    "a creamy Amarula 🐘🥛🥃",
    "a fiery Goldschläger with gold flakes ✨🥃",
    "a spiced Fernet-Branca 🇮🇹🌿🥃",
]

# List of meads to give out
MEADS = [
    "a golden traditional Mead 🍯🍺",
    "a spiced Viking Mead ⚔️🍯",
    "a tart cherry Melomel 🍒🍯",
    "a crisp apple Cyser 🍎🍯",
    "a warming Metheglin with cinnamon 🍯🔥",
    "a sparkling session Mead 🍯🫧",
    "a bold caramelized Bochet 🍯🔥",
    "a berry blackberry Mead 🫐🍯",
    "a smooth vanilla-oak Mead 🍯🪵",
    "a legendary Mead of the Gods 🍯⚡✨",
]

# List of popular pizzas to give out
PIZZAS = [
    "a classic Margherita pizza 🍕",
    "a pepperoni pizza with extra cheese 🧀🍕",
    "a classic cheese pizza 🧀🍕",
    "a Hawaiian pizza with pineapple 🍍🍕",
    "a veggie supreme pizza 🥦🍅🍕",
    "a meat lover's pizza 🥩🍕",
    "a BBQ chicken pizza 🍗🍕",
    "a buffalo chicken pizza 🐔🌶️🍕",
    "a white pizza with ricotta and spinach 🌱🍕",
    "a four cheese pizza 🧀🧀🧀🧀🍕",
]

# List of popular mixed drinks to give out
MIXED_DRINKS = [
    "a classic Margarita with salt rim 🍹🧂",
    "a refreshing Mojito with fresh mint 🌿🍹",
    "an Old Fashioned with a twist 🍊🥃",
    "a sophisticated Manhattan 🍒🥃",
    "a dry Martini - shaken, not stirred 🍸",
    "a pink Cosmopolitan 🍸💕",
    "a tropical Mai Tai 🍹🌺",
    "a creamy Piña Colada 🍍🥥🍹",
    "a potent Long Island Iced Tea 🍹💪",
    "a Moscow Mule in a copper mug 🍹",
    "a spicy Bloody Mary with celery 🍅🌶️🍹",
    "a tangy Whiskey Sour 🍋🥃",
    "a fizzy Tom Collins 🍋🍹",
    "a bitter Negroni 🍊🍹",
    "a classic Daiquiri 🍹",
    "a minty Mint Julep 🌿🥃",
    "a simple Gin and Tonic 🍋🍹",
    "a Rum and Coke with lime 🥃🥤",
    "a fruity Sex on the Beach 🍑🍹",
    "a smooth White Russian ☕🥃",
    "a bold Espresso Martini ☕🍸",
    "an Aperol Spritz with prosecco 🍊🥂",
    "a warm Irish Coffee ☕🥃🇮🇪",
    "a tropical Blue Hawaiian 🍹💙",
    "a sweet Amaretto Sour 🍋🥃",
    "a zesty Paloma 🍊🥤",
    "a Brazilian Caipirinha 🇧🇷🍋🍹",
    "a classy French 75 🥂✨",
    "a Dark 'n' Stormy ⛈️🥃",
    "a tropical Painkiller 🍍🥥🍹",
    "a New Orleans Sazerac ⚜️🥃",
    "a spicy Michelada 🌶️🇲🇽🍹",
    "a fruity Sangria 🍇🍊🇪🇸🍷",
    "a brunch-ready Mimosa 🥂🍊",
    "a peachy Bellini 🍑🥂",
    "a Hurricane from the French Quarter 🌀🍹",
    "a smoky Penicillin 🍯💊🥃",
    "a Cuba Libre with extra lime 🇨🇺🍋🥤",
    "a tiki Zombie - limit two! 🧟🍹",
    "a classic Singapore Sling 🇸🇬🍹",
]

# List of wines to give out
WINES = [
    "a bold Cabernet Sauvignon 🍷",
    "a smooth Merlot 🍷",
    "a rich Pinot Noir 🍷❤️",
    "a full-bodied Malbec 🍷🇦🇷",
    "a elegant Syrah/Shiraz 🍷",
    "a crisp Sauvignon Blanc 🥂",
    "a buttery Chardonnay 🥂",
    "a refreshing Pinot Grigio 🥂",
    "a zesty Riesling 🥂🍋",
    "a aromatic Moscato 🥂✨",
    "a delicate Rosé 🌹🍷",
    "a sparkling Champagne 🍾✨",
    "a bubbly Prosecco 🍾🇮🇹",
    "a festive Cava 🍾🇪🇸",
    "a sweet Dessert Wine 🍷🍯",
    "a fortified Port 🍷🇵🇹",
    "a nutty Sherry 🍷🥜",
    'a complex Chianti 🍷🇮🇹',
    'a smooth Rioja 🍷🇪🇸',
    'a premium Barolo 🍷👑',
]

# List of Spanish Cavas & sparkling wines to give out
CAVAS = [
    "a bottle of Freixenet Cordón Negro Brut 🍾🇪🇸",
    "a glass of Segura Viudas Brut Reserva 🥂🇪🇸",
    "a bottle of Codorníu Clásico Brut 🍾✨",
    "a crisp Gramona Imperial Corpinnat 🥂🇪🇸✨",
    "a chilled Juvé & Camps Reserva de la Familia 🍾🧊🇪🇸",
    "a glass of Llopart Brut Nature 🥂🍾🇪🇸",
    "a bottle of Recaredo Terrers Brut Nature 🍾🇪🇸",
    "a refreshing Cava Rosé 🥂🌹🇪🇸",
    "a glass of Bohigas Brut Reserva 🥂🇪🇸",
    "a bottle of Raventós i Blanc de Nit 🍾🌸🇪🇸",
    "a frosty glass of vintage Cava 🥂✨🇪🇸",
    "a sparkling Cava Brut Nature 🍾🥂🇪🇸",
    "a glass of Roger Goulart Gran Reserva 🥂👑🇪🇸",
    "a festive bottle of Spanish Cava 🍾🎉🇪🇸",
    "a chilled Cava semi-seco 🥂🍊🇪🇸",
]

# List of mocktails (non-alcoholic)
MOCKTAILS = [
    "a virgin Piña Colada 🍍🥥",
    "a refreshing Virgin Mojito 🌿💚",
    "a fruity Shirley Temple 🍒✨",
    "a zesty Virgin Mary 🍅",
    "a tropical Roy Rogers 🥤🍒",
    "a sparkling Arnold Palmer 🍋⛳",
    "a minty Cucumber Cooler 🥒🌿",
    "a sweet Strawberry Lemonade 🍓🍋",
    "a fizzy Italian Soda 🍓🥤",
    "a creamy Virgin Mudslide 🍫☕",
    "a tangy Citrus Punch 🍊🍋",
    "a exotic Mango Lassi 🥭🥛",
    "a refreshing Watermelon Agua Fresca 🍉💧",
    "a spiced Ginger Beer Float 🥤✨",
    "a fruity Paradise Punch 🍹🌴",
    "a berry Blueberry Lemonade 🫐🍋",
    "a tropical Passion Fruit Spritzer 💛🥤",
    "a fancy Mock Champagne 🍾✨",
    "a cool Mint Lime Cooler 🌿🍋",
    "a sweet Peach Iced Tea 🍑🧊",
]

# List of coffee drinks to give out
COFFEES = [
    # Classic Espresso Drinks
    "a strong Espresso ☕💪",
    "a smooth Americano ☕",
    "a creamy Cappuccino ☕🥛",
    "a frothy Latte ☕✨",
    "a sweet Mocha ☕🍫",
    "a caramel Macchiato ☕🍯",
    "a bold Cortado ☕",
    "a Flat White ☕🇦🇺",
    "a fancy Affogato ☕🍨",
    "a classic Café au Lait ☕🥛",
    "a rich Ristretto ☕💎",
    "a smooth Lungo ☕",
    "a layered Latte Macchiato ☕🥛",
    "a traditional Espresso Con Panna ☕🍦",
    "a double-shot Doppio ☕☕",
    
    # Iced Coffee Varieties
    "an iced Cold Brew ☕🧊",
    "a frothy Nitro Cold Brew ☕💨",
    "a Vietnamese Iced Coffee ☕🥛🇻🇳",
    "an Iced Americano ☕🧊",
    "an Iced Latte ☕🧊🥛",
    "an Iced Mocha ☕🧊🍫",
    "an Iced Caramel Macchiato ☕🧊🍯",
    "a refreshing Iced Coffee ☕🧊",
    "a Japanese Iced Coffee ☕🇯🇵🧊",
    "a Freddo Espresso ☕🇬🇷🧊",
    "a Freddo Cappuccino ☕🇬🇷🧊🥛",
    "a smooth Cold Foam Cold Brew ☕🧊☁️",
    "a Sweet Cream Cold Brew ☕🧊🥛",
    
    # Flavored Lattes & Mochas
    "a sweet Vanilla Latte ☕🌼",
    "a nutty Hazelnut Latte ☕🌰",
    "a sweet Cinnamon Dolce Latte ☕✨",
    "a smooth White Chocolate Mocha ☕🤍🍫",
    "a minty Peppermint Mocha ☕🌿🍫",
    "a festive Pumpkin Spice Latte 🎃☕",
    "a Gingerbread Latte ☕🍪",
    "a Salted Caramel Mocha ☕🧂🍫",
    "a Toffee Nut Latte ☕🍬",
    "a Coconut Milk Latte ☕🥥",
    "a Lavender Latte ☕💜",
    "a Rose Latte ☕🌹",
    "a Honey Vanilla Latte ☕🍯",
    "a Brown Sugar Oat Milk Latte ☕🌾",
    "a Maple Cinnamon Latte ☕🍁",
    "a Pistachio Latte ☕💚",
    "a Toasted Marshmallow Latte ☕🍭",
    "a Cookie Butter Latte ☕🍪",
    "an Almond Joy Latte ☕🥥🍫",
    "a Snickerdoodle Latte ☕🍪",
    
    # International Coffee Styles
    "a rich Turkish Coffee ☕🇹🇷",
    "a strong Greek Coffee ☕🇬🇷",
    "a traditional Cuban Coffee ☕🇨🇺",
    "a creamy Italian Coffee ☕🇮🇹",
    "a spiced Arabic Coffee ☕🌶️",
    "a sweet Thai Iced Coffee ☕🇹🇭🧊",
    "a Spanish Café Bombón ☕🇪🇸",
    "a Brazilian Cafezinho ☕🇧🇷",
    "a Mexican Café de Olla ☕🇲🇽",
    "an Ethiopian Coffee ☕🇪🇹",
    "a Portuguese Galão ☕🇵🇹",
    "an Australian Magic ☕🇦🇺✨",
    "a New Zealand Long Black ☕🇳🇿",
    "a Hong Kong Yuanyang ☕🇭🇰",
    "a Malaysian Kopi ☕🇲🇾",
    "an Indian Filter Coffee ☕🇮🇳",
    
    # Specialty Teas & Tea Lattes
    "a spiced Chai Latte ☕🌶️",
    "a London Fog ☕🇬🇧☁️",
    "a Matcha Latte ☕🍵💚",
    "a Dirty Chai ☕🌶️💪",
    "a Turmeric Latte ☕💛",
    "a Beetroot Latte ☕💗",
    "a Blue Butterfly Pea Latte ☕💙",
    
    # Coffee with Alcohol
    "a warm Irish Coffee ☕🥃🇮🇪",
    "a Spanish Carajillo ☕🥃🇪🇸",
    "an Italian Caffè Corretto ☕🥃🇮🇹",
    "a French Café Royale ☕🥃🇫🇷",
    "a Baileys Coffee ☕🍫🥃",
    "a Rum Coffee ☕🥃",
    "an Amaretto Coffee ☕🥃🌰",
    
    # Specialty Brewing Methods
    "a Pour Over Coffee ☕💧",
    "a French Press Coffee ☕🇫🇷",
    "a Chemex Coffee ☕⚗️",
    "an AeroPress Coffee ☕✈️",
    "a Siphon Coffee ☕🔬",
    "a Moka Pot Coffee ☕🇮🇹",
    "a Percolated Coffee ☕",
    "a Cowboy Coffee ☕🤠",
    
    # Modern & Designer Coffees
    "an Espresso Martini ☕🍸",
    "a Dalgona Coffee ☕☁️",
    "a Whipped Coffee ☕☁️",
    "a Coffee Frappuccino ☕🧊🥤",
    "a Protein Coffee ☕💪",
    "a Bulletproof Coffee ☕🧈",
    "a Mushroom Coffee ☕🍄",
    "a CBD Coffee ☕🌿",
    "a Collagen Coffee ☕✨",
    "a Charcoal Latte ☕🖤",
    "a Unicorn Latte ☕🦄",
    "a Galaxy Latte ☕🌌",
    "a Cloud Macchiato ☕☁️",
    "a Cascara Latte ☕🍒",
]

# Hot coffee. No ice, no liquor.
HOT_COFFEES = [
    "a hot dark roast ☕",
    "a steaming Americano ☕",
    "a hot Cappuccino ☕🥛",
    "a hot Latte ☕✨",
    "a hot Mocha ☕🍫",
    "a hot Flat White ☕",
    "a hot Pour Over ☕💧",
    "a hot Turkish Coffee ☕🇹🇷",
    "a hot Café au Lait ☕🥛",
    "a hot double Espresso ☕💪",
]

# Iced coffee.
ICED_COFFEES = [
    "an iced Cold Brew ☕🧊",
    "a frothy Nitro Cold Brew ☕💨",
    "an Iced Americano ☕🧊",
    "an Iced Latte ☕🧊🥛",
    "an Iced Mocha ☕🧊🍫",
    "a Vietnamese Iced Coffee ☕🥛🇻🇳",
    "a Japanese Iced Coffee ☕🇯🇵🧊",
    "a Sweet Cream Cold Brew ☕🧊🥛",
]

# Irish coffee and the close cousins.
IRISH_COFFEES = [
    "a hot Irish Coffee with Tullamore Dew and cream ☕🥃☘️",
    "a hot Irish Coffee with Jameson ☕🥃🇮🇪",
    "a hot Irish Coffee, brown sugar and thick cream ☕🥃",
    "a hot Baileys Coffee ☕🍫🥃",
    "a hot Irish Coffee with Bushmills ☕🥃",
    "a hot Irish Coffee with Redbreast ☕🥃",
    "a hot whiskey coffee under a collar of cream ☕🥃☁️",
    "a hot Irish Coffee with Powers Gold Label ☕🥃",
]

# End3r's coffee: hot dark roast and a shot of Tullamore Dew.
MINE_COFFEE = "a hot dark roast coffee with a shot of Tullamore Dew ☕🥃☘️"

# List of decaf & alternative coffees to give out
DECAFS = [
    "a Decaf Latte ☕😴",
    "a Decaf Americano ☕💤",
    "a Decaf Cappuccino ☕😴🥛",
    "a Decaf Mocha ☕💤🍫",
    "a Decaf Espresso ☕😴",
    "a Decaf Cold Brew ☕🧊💤",
    "a Decaf Iced Latte ☕🧊😴",
    "a Half-Caf Coffee ☕½",
    "a Half-Caf Latte ☕½🥛",
    "a Chicory Coffee ☕🌿",
    "a Barley Coffee ☕🌾",
    "a Dandelion Coffee ☕🌼",
    "a Carob Latte ☕🍫😴",
    "a Roasted Fig Latte ☕😴",
    "a Mushroom Decaf ☕🍄💤",
    "a Golden Milk Decaf Latte ☕💛😴",
    "a Decaf Flat White ☕😴🇦🇺",
    "a Swiss Water Decaf ☕🇨🇭💧",
    "a Decaf Pumpkin Spice Latte 🎃☕😴",
    "an Herbal Rooibos 'Coffee' ☕🌿😴",
]

# List of teas to give out
TEAS = [
    # Classic Black Teas
    "a proper English Breakfast tea 🫖🇬🇧",
    "a bold Assam tea 🫖🌿",
    "a fragrant Earl Grey 🫖💜",
    "a delicate Darjeeling 🫖🏔️",
    "a smoky Lapsang Souchong 🫖💨",
    "a rich Ceylon tea 🫖🇱🇰",
    "a classic Lady Grey 🫖✨",
    "a malty Irish Breakfast tea 🫖🇮🇪",

    # Green Teas
    "a serene Japanese Sencha 🍵🇯🇵",
    "a ceremonial Matcha 🍵💚",
    "a fresh Gyokuro 🍵✨",
    "a toasty Genmaicha 🍵🌾",
    "a floral Jasmine Green tea 🍵🌸",
    "a sweet Chinese Dragon Well 🍵🐉",
    "a grassy Biluochun 🍵🌿",
    "a smoky Gunpowder tea 🍵💣",

    # White & Oolong Teas
    "a delicate Silver Needle white tea 🍵🌙",
    "a light White Peony tea 🍵🌸",
    "a complex Oolong 🍵🔀",
    "a roasted Tieguanyin 🍵🏵️",
    "a floral Oriental Beauty Oolong 🍵🦋",

    # Herbal & Tisanes
    "a soothing Chamomile tea 🌼🫖",
    "a refreshing Peppermint tea 🌿🫖",
    "a calming Lavender tea 💜🫖",
    "a tart Hibiscus tea 🌺🫖",
    "a warming Ginger tea 🫚🫖",
    "a gentle Lemon Balm tea 🍋🫖",
    "a spiced Rooibos 🍂🫖🇿🇦",
    "a woody Yerba Maté 🧉🇦🇷",
    "a floral Rose hip tea 🌹🫖",
    "a earthy Nettle tea 🌿🫖",
    "a tropical Hibiscus Lemonade tea 🌺🍋",
    "a warming Turmeric Ginger tea 💛🫖",
    "a soothing Valerian Root tea 🌸😴",
    "a tangy Lemon Verbena tea 🍋🌿",

    # Chai & Spiced Teas
    "a spiced Masala Chai 🌶️🫖🇮🇳",
    "a creamy Chai Latte 🫖✨",
    "a warming Kashmiri Pink Chai 🌸🫖",
    "a bold Adrak Chai (Ginger Chai) 🫚🫖",
    "a festive Mulled Tea 🍊🌿🫖",

    # Iced & Cold Teas
    "a refreshing Iced Black Tea 🧊🫖",
    "a classic Sweet Tea 🍋🧊🫖",
    "a fruity Iced Peach Tea 🍑🧊",
    "a tropical Iced Mango Tea 🥭🧊",
    "a zesty Iced Lemon Tea 🍋🧊",
    "a sparkling Iced Green Tea 🍵🧊✨",
    "a refreshing Arnold Palmer 🍋⛳🧊",
    "a smooth Taiwanese Milk Tea 🥛🫖🇹🇼",
    "a classic Bubble Tea 🧋🟤",
    "a taro Bubble Tea 🟣🧋",
    "a strawberry Bubble Tea 🍓🧋",
    "a matcha Bubble Tea 💚🧋",

    # International & Specialty Teas
    "a traditional Moroccan Mint Tea 🌿🇲🇦",
    "a sweet Hong Kong Milk Tea 🥛☕🇭🇰",
    "a rich Teh Tarik 🇲🇾☕",
    "a ceremonial Japanese Hojicha 🍵🔥🇯🇵",
    "a tangy Kombucha 🍵🦠✨",
    "a light Korean Barley Tea 🌾🫖🇰🇷",
    "a refreshing Tibetan Butter Tea 🧈🫖",
    "a warming Russian Zavarka 🫖🇷🇺",
]

# List of hydration options (fun!)
WATERS = [
    "a crystal clear glass of water 💧",
    "a fancy sparkling water 💧✨",
    "a refreshing ice water 🧊💧",
    "a healthy coconut water 🥥💧",
    "a trendy alkaline water 💧⚗️",
    "a bougie Fiji water 💧🏝️",
    "a hydrating electrolyte water 💧⚡",
    "a cucumber infused water 🥒💧",
    "a lemon water 🍋💧",
    "a responsible glass of H2O 💧😇",
    "a bubbly Perrier 💧🇫🇷",
    "a classy San Pellegrino 💧🇮🇹",
    "a smart choice - water 💧🧠",
    "a mint infused water 🌿💧",
    "a strawberry water 🍓💧",
]

# List of appetizers/snacks
APPETIZERS = [
    "some crispy Buffalo Wings 🍗🔥",
    "a plate of loaded Nachos 🧀🌶️",
    "some golden Mozzarella Sticks 🧀✨",
    "a basket of seasoned Fries 🍟",
    "some crunchy Onion Rings 🧅⭕",
    "a platter of Chicken Tenders 🍗",
    "some spicy Jalapeño Poppers 🌶️🧀",
    "a bowl of Chips and Salsa 🌮🍅",
    "some cheesy Quesadillas 🧀🌮",
    "a plate of Sliders 🍔",
    "some crispy Calamari 🦑",
    "a serving of Spinach Artichoke Dip 🌿🧀",
    "some loaded Potato Skins 🥔🧀🥓",
    "a basket of Pretzel Bites 🥨",
    "some BBQ Ribs 🍖🔥",
    "a plate of Poutine 🍟🧀🇨🇦",
    "some Garlic Bread 🧄🍞",
    "a charcuterie board 🧀🥖🍇",
    "some Boneless Wings 🍗",
    "a bowl of Guacamole with chips 🥑🌮",
    "some Fried Pickles 🥒",
    "a plate of Bruschetta 🍅🍞🇮🇹",
    "some Tater Tots 🥔",
    "a serving of Hummus and Pita 🫓",
    "some Mac and Cheese Bites 🧀🍝",
]

# Beer/Cider giving messages (can crack open, chill, etc)
BEER_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "cracks open {drink} for {user} *pssshhht* 💨",
    "tosses {drink} to {user} *CATCH!* 🎯",
    "serves {user} {drink} - enjoy! 🍻",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} into a frosty glass for {user} 🍺",
    "rolls {drink} down the bar to {user} 🎳",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pulls {drink} from the cooler and hands it to {user} 🧊",
]

# Whiskey/Spirit giving messages (pour, serve neat or on rocks)
WHISKEY_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} and hands it to {user} with a smile 😊",
    "serves {user} {drink} - enjoy! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} neat for {user} 🥃",
    "pours {drink} on the rocks for {user} 🧊🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "carefully measures out {drink} for {user} 🥃",
    "pours {drink} for {user} 🥃",
]

# Scotch giving messages
SCOTCH_MESSAGES = [
    "slides {drink} across the bar to {user} 🏴󠁧󠁢󠁳󠁣󠁴󠁿✨",
    "pours {drink} for {user} - Slàinte mhath! 🥃🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "serves {user} {drink} - savor every drop! 🥃✨",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} neat in a Glencairn glass for {user} 🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "carefully measures out {drink} for {user} 🥃",
    "pours {drink} for {user} 🥃🏴󠁧󠁢󠁳󠁣󠁴󠁿",
]

# Irish whiskey giving messages
IRISH_MESSAGES = [
    "slides {drink} across the bar to {user} 🇮🇪✨",
    "pours {drink} for {user} - Sláinte! 🇮🇪🥃",
    "serves {user} {drink} with a touch of Irish warmth! ☘️🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} for {user} 🇮🇪🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "hands {user} {drink} ☘️🥃",
]

# Vodka giving messages
VODKA_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} ice-cold for {user} 🧊🥃",
    "serves {user} {drink} - na zdorovie! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "chills {drink} to perfection and hands it to {user} 🧊",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} for {user} 🥃",
    "pulls {drink} from the freezer for {user} 🧊🥃",
    "serves {user} {drink} chilled to perfection 🥶🥃",
]

# Rum giving messages
RUM_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} and hands it to {user} 🏴‍☠️🥃",
    "serves {user} {drink} - yo ho ho! 🏴‍☠️",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} neat for {user} 🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} for {user} 🥃",
    "serves {user} {drink} island style 🌴🥃",
    "sets sail with {drink} for {user} ⛵🥃",
]

# Tequila giving messages
TEQUILA_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} and hands it to {user} - ¡salud! 🇲🇽🥃",
    "serves {user} {drink} with salt & lime 🧂🍋🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} smooth for {user} 🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "lines up {drink} for {user} - arriba! 🥃🎉",
    "carefully pours {drink} for {user} to sip 🥃",
    "delivers {drink} to {user} - one tequila, two tequila... 🥃🥃",
]

# Gin giving messages
GIN_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} and hands it to {user} 🌿🥃",
    "serves {user} {drink} - cheers! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} with a flourish for {user} 🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "carefully measures {drink} for {user} 🥃",
    "serves {user} {drink} botanically blessed 🌿✨",
    "garnishes {drink} and passes it to {user} 🥒🥃",
]

# Brandy/Cognac giving messages
BRANDY_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} into a snifter for {user} 🥃",
    "serves {user} {drink} - santé! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "warms {drink} gently and hands it to {user} 🥃🔥",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} into a snifter for {user} 🥃",
    "serves {user} {drink} in fine style 🎩🥃",
    "presents {drink} to {user} with a knowing nod 🥃😌",
]

# Margarita giving messages
MARGARITA_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "shakes up {drink} and serves it to {user} - ¡salud! 🇲🇽🍹",
    "salts the rim and hands {drink} to {user} 🧂🍹",
    "blends {drink} to perfection for {user} 🌀🍹",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "garnishes {drink} with a lime wheel for {user} 🍋🍹",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "serves {user} {drink} - it's five o'clock somewhere! 🕔🍹",
    "cranks up the blender and pours {drink} for {user} 🎉🍹",
]

# Sake giving messages. No temperature claims — a warm pour must not
# be described as chilled, and the other way around.
SAKE_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} into an ochoko cup for {user} - kanpai! 🍶🇯🇵",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} for {user} with both hands 🙏🍶",
]

HOT_SAKE_MESSAGES = [
    "warms {drink} and sets the tokkuri in front of {user} ♨️🍶",
    "pours steaming {drink} into an ochoko for {user} - kanpai! 🍶🇯🇵",
    "slides a hot flask of {drink} down the bar to {user} ♨️",
    "cups {drink} until it steams, then serves {user} 🔥🍶",
    "ceremoniously presents {user} with {drink} 🎊♨️",
    "pours {drink} for {user} with both hands - drink it while it's hot 🙏🍶",
    "teleports steaming {drink} into {user}'s hands 🚀♨️",
    "sets {drink} down for {user} with a warm towel ♨️🍶",
]

COLD_SAKE_MESSAGES = [
    "serves {user} {drink} perfectly chilled 🧊🍶",
    "pulls {drink} from the ice well for {user} 🧊",
    "pours {drink} into a chilled ochoko for {user} - kanpai! 🍶🇯🇵",
    "slides frosty {drink} across the bar to {user} ✨🧊",
    "sets {drink} over ice in front of {user} 🧊🍶",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports ice-cold {drink} into {user}'s hand 🚀🧊",
    "pours {drink} for {user} with both hands 🙏🍶",
]

# hot/warm and cold/chilled. Anything else is a nickname.
_SAKE_TEMPS = {
    'hot': 'hot',
    'warm': 'hot',
    'cold': 'cold',
    'chilled': 'cold',
}


def _parse_sake_args(raw):
    """Split a sake order into (temperature or None, nick or None).

    Accepts ``hot``, ``cold``, a nick, or either order of the two.
    Only the first two words are read.
    """
    if not raw or not str(raw).strip():
        return None, None
    temp = None
    nick = None
    for part in str(raw).strip().split()[:2]:
        kind = _SAKE_TEMPS.get(part.lower())
        if kind:
            if temp is None:
                temp = kind
            continue
        if nick is None:
            nick = part
    return temp, nick


def _sake_menu(temp):
    """Return (item_list, message_list) for hot, cold, or bartender's choice."""
    if temp == 'hot':
        return HOT_SAKES, HOT_SAKE_MESSAGES
    if temp == 'cold':
        return COLD_SAKES, COLD_SAKE_MESSAGES
    return SAKES, SAKE_MESSAGES


_COFFEE_STYLES = {
    'iced': 'iced',
    'ice': 'iced',
    'cold': 'iced',
    'hot': 'hot',
    'warm': 'hot',
    'irish': 'irish',
    'mine': 'mine',
}


def _parse_coffee_args(raw):
    """Split a coffee order into (style or None, nick or None).

    Styles are iced, hot, irish, and mine. A nick can sit on either side.
    Only the first two words are read.
    """
    if not raw or not str(raw).strip():
        return None, None
    style = None
    nick = None
    for part in str(raw).strip().split()[:2]:
        kind = _COFFEE_STYLES.get(part.lower())
        if kind:
            if style is None:
                style = kind
            continue
        if nick is None:
            nick = part
    return style, nick


def _coffee_menu(style):
    """Return (item_list, message_list, effect_type or None) for a coffee style.

    Irish and mine are spiked, so they count as a spirit instead of a sober-up.
    """
    if style == 'hot':
        return HOT_COFFEES, HOT_COFFEE_MESSAGES, None
    if style == 'iced':
        return ICED_COFFEES, ICED_COFFEE_MESSAGES, None
    if style == 'irish':
        return IRISH_COFFEES, IRISH_COFFEE_MESSAGES, 'irish_coffee'
    if style == 'mine':
        return [MINE_COFFEE], MINE_COFFEE_MESSAGES, 'irish_coffee'
    return COFFEES, COFFEE_MESSAGES, None


def _coffee_messages_for(item):
    """Pick a serving line that matches the cup: iced, hot, Irish, or End3r's."""
    low = item.lower()
    if 'tullamore' in low and 'dark roast' in low:
        return MINE_COFFEE_MESSAGES
    if any(mark in low for mark in ('irish', 'baileys', 'jameson', 'tullamore', 'bushmills', 'redbreast', 'powers', 'whiskey', 'whisky')):
        return IRISH_COFFEE_MESSAGES
    if any(mark in low for mark in ('iced', 'cold brew', 'nitro', 'freddo', 'frappuccino', '🧊')):
        return ICED_COFFEE_MESSAGES
    if any(mark in low for mark in ('hot', 'steam', 'warm')):
        return HOT_COFFEE_MESSAGES
    return COFFEE_MESSAGES


def _coffee_is_spiked(item):
    """True when the cup has whiskey in it, including End3r's usual."""
    return _coffee_messages_for(item) in (IRISH_COFFEE_MESSAGES, MINE_COFFEE_MESSAGES)


def _sake_messages_for(item):
    """Pick serving lines that match the bottle's temperature.

    Plain ``$sake`` draws from a mixed list, so the action has to follow
    the bottle that was actually chosen.
    """
    text = item.lower()
    if any(mark in text for mark in ('warm', 'hot', 'steam', 'atsukan', 'nurukan', 'toasty', '♨️', '🔥')):
        return HOT_SAKE_MESSAGES
    if any(mark in text for mark in ('chill', 'cold', 'frost', 'ice', '🧊')):
        return COLD_SAKE_MESSAGES
    return SAKE_MESSAGES

# Liqueur giving messages
LIQUEUR_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} over ice for {user} 🧊🥃",
    "serves {user} {drink} - sip slowly! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} for {user} 🍯🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "serves {user} {drink} as a nightcap 🌙🥃",
]

# Mead giving messages
MEAD_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} into a drinking horn for {user} ⚔️🍯",
    "serves {user} {drink} - SKÅL! 🛡️🍯",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "raises a horn of {drink} to {user} 🍻⚔️",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "fills {user}'s goblet with {drink} 🏰🍯",
]

# Shot giving messages (quick, energetic)
SHOT_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "lines up {drink} for {user} 🥃",
    "serves {user} {drink} - bottoms up! 🥃",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} and hands it to {user} 🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "sets down {drink} in front of {user} 🥃",
    "prepares {drink} for {user} 🥃",
    "quickly pours {drink} for {user} - cheers! 🥃",
]

# Cocktail/Mixed drink giving messages (mix, shake, stir)
COCKTAIL_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "shakes up {drink} and serves it to {user} 🍸",
    "mixes {drink} and hands it to {user} with a smile 😊",
    "serves {user} {drink} - enjoy! 🍹",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "carefully crafts {drink} for {user} 🍸",
    "stirs {drink} and passes it to {user} 🥄",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "muddles and mixes {drink} for {user} 🍹",
]

# Wine giving messages (pour, swirl, serve)
WINE_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pours {drink} and hands it to {user} 🍷",
    "serves {user} {drink} - cheers! 🍷",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "uncorks and pours {drink} for {user} 🍷",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} for {user} 🍷",
    "decants {drink} and serves it to {user} 🍷",
    "swirls and serves {drink} to {user} 🍷✨",
]

# Cava giving messages (sparkling Spanish wine)
CAVA_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "pops the cork on {drink} for {user} *POP!* 🍾🥂",
    "pours {drink} for {user} - ¡Salud! 🥂🇪🇸",
    "serves {user} {drink} - cheers! 🥂",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "uncorks {drink} with a loud pop for {user} 🍾✨",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "pours {drink} for {user} 🥂✨",
    "chills and pours {drink} for {user} 🧊🥂",
]

# Coffee giving messages
COFFEE_MESSAGES = [
    "slides {drink} across the counter to {user} ✨",
    "brews {drink} fresh for {user} ☕",
    "serves {user} {drink} - enjoy! ☕",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "steams and pours {drink} for {user} ☕",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "carefully crafts {drink} for {user} ☕✨",
    "froths {drink} and hands it to {user} ☕🥛",
    "pulls a perfect shot for {drink} and gives it to {user} ☕",
]

HOT_COFFEE_MESSAGES = [
    "brews {drink} fresh and slides it to {user} ☕",
    "steams {drink} and sets it in front of {user} ☕",
    "pours {drink} for {user} ☕",
    "serves {user} {drink} while it's still steaming ☕",
    "carefully crafts {drink} for {user} ☕✨",
    "slides {drink} across the counter to {user} ✨",
    "ceremoniously presents {user} with {drink} 🎊",
    "hands {user} {drink} - careful, it's hot ☕",
]

ICED_COFFEE_MESSAGES = [
    "pours {drink} over ice for {user} 🧊☕",
    "pulls {drink} from the ice well for {user} 🧊",
    "slides frosty {drink} across the counter to {user} ✨🧊",
    "serves {user} {drink} perfectly cold 🧊☕",
    "shakes {drink} with ice and hands it to {user} 🧊",
    "sets {drink} down in front of {user} 🧊☕",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports ice-cold {drink} into {user}'s hand 🚀🧊",
]

IRISH_COFFEE_MESSAGES = [
    "builds {drink} under a collar of cream for {user} ☕🥃",
    "pours {drink} and slides it to {user} - sláinte ☘️",
    "floats cream on {drink} and serves {user} ☕🥃☁️",
    "sets {drink} down in front of {user} ☕🥃",
    "ceremoniously presents {user} with {drink} 🎊",
    "hands {user} {drink} - whiskey in the coffee 🥃☕",
    "warms {drink} and serves it to {user} ♨️☕",
    "slides {drink} across the counter to {user} ✨",
]

MINE_COFFEE_MESSAGES = [
    "brews a hot dark roast, pours in a shot of Tullamore Dew, and sets it in front of {user} ☕🥃☘️",
    "slides {drink} across the counter to {user} ☕🥃",
    "pours {drink} for {user} - sláinte ☘️",
    "builds {drink} and hands it to {user} ☕🥃",
    "sets {drink} down in front of {user}, hot and spiked ☕🥃",
    "ceremoniously presents {user} with {drink} 🎊",
]

# Tea giving messages
TEA_MESSAGES = [
    "slides {drink} across the counter to {user} ✨",
    "steeps {drink} to perfection for {user} 🫖",
    "serves {user} {drink} - enjoy! 🍵",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "carefully pours {drink} for {user} 🫖",
    "ceremoniously presents {user} with {drink} 🎊",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "brews {drink} for {user} 🫖✨",
    "hands {user} {drink} 🍵",
    "sets {drink} down in front of {user} with a smile 🫖😊",
]

# Water giving messages (responsible hydration!)
WATER_MESSAGES = [
    "slides {drink} across the bar to {user} ✨",
    "hands {drink} to {user} - stay hydrated! 💧",
    "serves {user} {drink} - good choice! 💧👍",
    "conjures {drink} out of thin air for {user} ✨🎩",
    "pours {drink} and passes it to {user} 💧",
    "teleports {drink} directly into {user}'s hand 🚀✨",
    "responsibly serves {user} {drink} 💧😇",
    "serves {drink} to {user} 💧",
    "cracks open {drink} for {user} 💧",
    "reminds {user} to hydrate and hands them {drink} 💧❤️",
]

# Food giving messages
FOOD_MESSAGES = [
    "slides {food} across the counter to {user} ✨",
    "serves {user} {food} - enjoy! 🍽️",
    "brings {food} fresh from the kitchen to {user} 🔥",
    "conjures {food} out of thin air for {user} ✨🎩",
    "plates up {food} and hands it to {user} 🍽️",
    "ceremoniously presents {user} with {food} 🎊",
    "teleports {food} directly to {user} 🚀✨",
    "tosses {food} to {user} *CATCH!* 🎯",
    "delivers {food} hot and ready to {user} 🔥",
    "sets down {food} in front of {user} 🍽️",
]

# Pizza-specific giving messages
PIZZA_MESSAGES = [
    "slides {food} across the counter to {user} ✨",
    "hands {food} to {user} with a smile 😊",
    "tosses {food} to {user} *CATCH!* 🎯",
    "serves {user} {food} - enjoy! 🍕",
    "conjures {food} out of thin air for {user} ✨🎩",
    "passes {food} to {user} 🍕",
    "delivers {food} hot and fresh to {user} 🔥",
    "rolls {food} down the counter to {user} 🎳",
    "ceremoniously presents {user} with {food} 🎊",
    "teleports {food} directly into {user}'s hand 🚀✨",
    "boxes up {food} and hands it to {user} 📦",
    "brings {user} a fresh {food} straight from the oven 🔥",
]


@module.commands('beer')
@module.example('$beer username', 'Give a user a random beer')
def beer(bot, trigger):
    """Give someone a refreshing beer! 🍺"""
    _serve_item(bot, trigger, 'beer', BEERS, BEER_MESSAGES)


@module.commands('b33r')
@module.example('$b33r username', 'Give a user a random beer in 1337 speak')
def b33r(bot, trigger):
    """Give someone a beer in 1337 speak! 🍺"""
    _serve_item(bot, trigger, 'beer', BEERS, BEER_MESSAGES, is_1337=True)


@module.commands('shot')
@module.example('$shot username', 'Give a user a random shot')
def shot(bot, trigger):
    """Give someone a shot! 🥃"""
    _serve_item(bot, trigger, 'shot', SHOTS, SHOT_MESSAGES)


@module.commands('magners')
@module.example('$magners username', 'Give a user a random Magners cider')
def magners(bot, trigger):
    """Give someone a refreshing Magners! 🍎🍺"""
    _serve_item(bot, trigger, 'magners', MAGNERS, BEER_MESSAGES)


@module.commands('whiskey', 'whisky')
@module.example('$whiskey username', 'Give a user a random whiskey')
def whiskey(bot, trigger):
    """Give someone a fine whiskey! 🥃"""
    _serve_item(bot, trigger, 'whiskey', WHISKEYS, WHISKEY_MESSAGES)


@module.commands('scotch')
@module.example('$scotch username', 'Give a user a premium Scotch whisky')
def scotch(bot, trigger):
    """Give someone a fine single malt or blend Scotch whisky! 🏴󠁧󠁢󠁳󠁣󠁴󠁿🥃"""
    _serve_item(bot, trigger, 'scotch', SCOTCHES, SCOTCH_MESSAGES)


@module.commands('irish', 'irishwhiskey')
@module.example('$irish username', 'Give a user an Irish whiskey')
def irish(bot, trigger):
    """Give someone a fine Irish whiskey! Sláinte! 🇮🇪🥃"""
    _serve_item(bot, trigger, 'irish', IRISH_WHISKEYS, IRISH_MESSAGES)


@module.commands('vodka')
@module.example('$vodka username', 'Give a user a random vodka')
def vodka(bot, trigger):
    """Give someone a fine vodka! 🥃"""
    _serve_item(bot, trigger, 'vodka', VODKAS, VODKA_MESSAGES)


@module.commands('rum')
@module.example('$rum username', 'Give a user a random rum')
def rum(bot, trigger):
    """Give someone a fine rum! 🏴‍☠️🥃"""
    _serve_item(bot, trigger, 'rum', RUMS, RUM_MESSAGES)


@module.commands('tequila')
@module.example('$tequila username', 'Give a user a random tequila')
def tequila(bot, trigger):
    """Give someone a fine tequila! 🇲🇽🥃"""
    _serve_item(bot, trigger, 'tequila', TEQUILAS, TEQUILA_MESSAGES)


@module.commands('gin')
@module.example('$gin username', 'Give a user a random gin')
def gin(bot, trigger):
    """Give someone a fine gin! 🌿🥃"""
    _serve_item(bot, trigger, 'gin', GINS, GIN_MESSAGES)


@module.commands('brandy', 'cognac')
@module.example('$brandy username', 'Give a user a random brandy/cognac')
def brandy(bot, trigger):
    """Give someone a fine brandy or cognac! 🥃"""
    _serve_item(bot, trigger, 'brandy', BRANDIES, BRANDY_MESSAGES)


@module.commands('margarita', 'marg')
@module.example('$margarita username', 'Give a user a random margarita')
def margarita(bot, trigger):
    """Give someone a refreshing margarita! 🧂🍹"""
    _serve_item(bot, trigger, 'margarita', MARGARITAS, MARGARITA_MESSAGES)


@module.commands('sake')
@module.example('$sake', 'Give yourself a random sake')
@module.example('$sake hot', 'Give yourself a hot sake')
@module.example('$sake cold username', 'Give a user a cold sake')
def sake(bot, trigger):
    """Give someone sake. Hot, cold, or bartender's choice. Kanpai! 🍶"""
    try:
        raw_target = trigger.group(2)
    except (IndexError, AttributeError):
        raw_target = None
    temp, nick = _parse_sake_args(raw_target)
    item_list, message_list = _sake_menu(temp)
    _serve_item(
        bot, trigger, 'sake', item_list, message_list,
        target_override=nick or trigger.nick,
    )


@module.commands('liqueur', 'cordial')
@module.example('$liqueur username', 'Give a user a random liqueur')
def liqueur(bot, trigger):
    """Give someone a sweet liqueur! 🍯🥃"""
    _serve_item(bot, trigger, 'liqueur', LIQUEURS, LIQUEUR_MESSAGES)


@module.commands('mead')
@module.example('$mead username', 'Give a user a random mead')
def mead(bot, trigger):
    """Give someone a horn of mead! SKÅL! ⚔️🍯"""
    _serve_item(bot, trigger, 'mead', MEADS, MEAD_MESSAGES)


@module.commands('pizza')
@module.example('$pizza', 'Give yourself a random pizza')
@module.example('$pizza username', 'Give a user a random pizza')
def pizza(bot, trigger):
    """Give someone a delicious pizza! 🍕"""
    _serve_item(bot, trigger, 'pizza', PIZZAS, PIZZA_MESSAGES, placeholder_key='food')


@module.commands('drink')
@module.example('$drink', 'Give yourself a random mixed drink')
@module.example('$drink username', 'Give a user a random mixed drink')
def drink(bot, trigger):
    """Give someone a delicious mixed drink! 🍹"""
    _serve_item(bot, trigger, 'mixed_drink', MIXED_DRINKS, COCKTAIL_MESSAGES)


@module.commands('wine')
@module.example('$wine', 'Give yourself a random wine')
@module.example('$wine username', 'Give a user a random wine')
def wine(bot, trigger):
    """Give someone a fine wine! 🍷"""
    _serve_item(bot, trigger, 'wine', WINES, WINE_MESSAGES)


@module.commands('cava', 'prosecco')
@module.example('$cava', 'Give yourself a random Cava')
@module.example('$cava username', 'Give a user a random Cava')
def cava(bot, trigger):
    """Give someone a bottle/glass of sparkling Cava! ¡Salud! 🍾🥂"""
    _serve_item(bot, trigger, 'cava', CAVAS, CAVA_MESSAGES)


@module.commands('mocktail', 'virgin')
@module.example('$mocktail', 'Give yourself a random non-alcoholic drink')
@module.example('$mocktail username', 'Give a user a random non-alcoholic drink')
def mocktail(bot, trigger):
    """Give someone a refreshing mocktail! 🍹"""
    _serve_item(bot, trigger, 'mocktail', MOCKTAILS, COCKTAIL_MESSAGES)


@module.commands('coffee', 'caffeine')
@module.example('$coffee', 'Give yourself a random coffee')
@module.example('$coffee iced', 'Give yourself an iced coffee')
@module.example('$coffee hot username', 'Give a user a hot coffee')
@module.example('$coffee irish', 'Give yourself an Irish coffee')
@module.example('$coffee mine', 'Hot dark roast with a shot of Tullamore Dew')
def coffee(bot, trigger):
    """Give someone coffee: iced, hot, Irish, or End3r's usual. ☕"""
    try:
        raw_target = trigger.group(2)
    except (IndexError, AttributeError):
        raw_target = None
    style, nick = _parse_coffee_args(raw_target)
    item_list, message_list, effect = _coffee_menu(style)
    _serve_item(
        bot, trigger, 'coffee', item_list, message_list,
        target_override=nick or trigger.nick,
        effect_type=effect,
    )


@module.commands('decaf', 'decaffeinated')
@module.example('$decaf', 'Give yourself a random decaf coffee')
@module.example('$decaf username', 'Give a user a random decaf coffee')
def decaf(bot, trigger):
    """Give someone a decaf coffee - all the flavor, none of the jitters! ☕😴"""
    _serve_item(bot, trigger, 'decaf', DECAFS, COFFEE_MESSAGES)


@module.commands('tea', 'cuppa')
@module.example('$tea', 'Give yourself a random tea')
@module.example('$tea username', 'Give a user a random tea')
def tea(bot, trigger):
    """Give someone a soothing tea! 🍵"""
    _serve_item(bot, trigger, 'tea', TEAS, TEA_MESSAGES)


@module.commands('water', 'hydrate')
@module.example('$water', 'Give yourself water')
@module.example('$water username', 'Give a user water - stay hydrated!')
def water(bot, trigger):
    """Give someone water! Stay hydrated! 💧"""
    _serve_item(bot, trigger, 'water', WATERS, WATER_MESSAGES)


@module.commands('appetizer', 'snack', 'food')
@module.example('$appetizer', 'Give yourself a random appetizer')
@module.example('$appetizer username', 'Give a user a random appetizer')
def appetizer(bot, trigger):
    """Give someone a tasty appetizer! 🍽️"""
    _serve_item(bot, trigger, 'appetizer', APPETIZERS, FOOD_MESSAGES, placeholder_key='food')


@module.commands('surprise', 'random')
@module.example('$surprise', 'Give yourself something random from the entire menu!')
@module.example('$surprise username', 'Give a user something random')
def surprise(bot, trigger, target_override=None):
    """Give someone a random surprise from everything available! 🎉"""
    
    # Use sender's nick if no user specified - ibot safe
    if target_override:
        target_user = target_override
    else:
        try:
            raw_target = trigger.group(2)
        except (IndexError, AttributeError):
            raw_target = None

        if not raw_target:
            target_user = trigger.nick
        else:
            target_user = raw_target.strip().split()[0]
    
    # Combine all drink lists
    all_drinks = BEERS + SHOTS + MAGNERS + WHISKEYS + SCOTCHES + IRISH_WHISKEYS + VODKAS + RUMS + TEQUILAS + GINS + BRANDIES + MARGARITAS + SAKES + LIQUEURS + MEADS + MIXED_DRINKS + WINES + CAVAS + MOCKTAILS + COFFEES + DECAFS + TEAS + WATERS
    all_foods = PIZZAS + APPETIZERS
    
    # Randomly decide if it's a drink or food
    if random.choice([True, False]):
        # It's a drink
        chosen_item = random.choice(all_drinks)
        # Pick appropriate message based on item characteristics
        if any(word in chosen_item.lower() for word in ['cava', 'champagne', 'prosecco']):
            giving_message = random.choice(CAVA_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['wine']):
            giving_message = random.choice(WINE_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['coffee', 'espresso', 'latte', 'cappuccino']):
            giving_message = random.choice(_coffee_messages_for(chosen_item))
        elif any(word in chosen_item.lower() for word in ['tea', 'chai', 'matcha', 'herbal', 'tisane', 'rooibos', 'chamomile', 'bubble']):
            giving_message = random.choice(TEA_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['water', 'h2o']):
            giving_message = random.choice(WATER_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['shot', 'bomber']):
            giving_message = random.choice(SHOT_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['beer', 'lager', 'ale', 'stout', 'ipa', 'magners', 'cider']):
            giving_message = random.choice(BEER_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['scotch', 'macallan', 'glenfiddich', 'lagavulin', 'balvenie', 'laphroaig', 'talisker', 'ardbeg', 'glenmorangie', 'springbank', 'royal salute']):
            giving_message = random.choice(SCOTCH_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['irish', 'jameson', 'tullamore', 'paddy', 'paddies', 'redbreast', 'bushmills', 'teeling', 'green spot', 'midleton', 'slane']):
            giving_message = random.choice(IRISH_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['whiskey', 'whisky', 'bourbon']):
            giving_message = random.choice(WHISKEY_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['vodka', 'belvedere', 'grey goose', 'ketel', 'absolut', 'smirnoff', 'stolichnaya']):
            giving_message = random.choice(VODKA_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['rum', 'bacardi', 'captain morgan', 'malibu', 'kraken']):
            giving_message = random.choice(RUM_MESSAGES)
        elif 'margarita' in chosen_item.lower():
            giving_message = random.choice(MARGARITA_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['sake', 'junmai', 'ginjo', 'nigori', 'daiginjo', 'honjozo']):
            giving_message = random.choice(_sake_messages_for(chosen_item))
        elif any(word in chosen_item.lower() for word in ['mead', 'melomel', 'cyser', 'metheglin', 'bochet']):
            giving_message = random.choice(MEAD_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['baileys', 'disaronno', 'grand marnier', 'chartreuse', 'limoncello', 'kahlúa', 'maraschino', 'st-germain', 'drambuie', 'campari', 'frangelico', 'chambord', 'crème de menthe', 'amarula', 'goldschläger', 'fernet']):
            giving_message = random.choice(LIQUEUR_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['tequila', 'mezcal', 'patrón', 'don julio']):
            giving_message = random.choice(TEQUILA_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['gin', 'tanqueray', 'hendrick', 'bombay', 'beefeater']):
            giving_message = random.choice(GIN_MESSAGES)
        elif any(word in chosen_item.lower() for word in ['brandy', 'cognac', 'hennessy', 'rémy', 'courvoisier', 'calvados', 'pisco']):
            giving_message = random.choice(BRANDY_MESSAGES)
        else:
            giving_message = random.choice(COCKTAIL_MESSAGES)
        
        bot_nick = (getattr(bot, 'nick', '') or '').lower()
        if target_user.lower() == bot_nick:
            giving_message = random.choice(BOT_DRINK_RECEPTION_MESSAGES)
            base_message = giving_message.format(drink=chosen_item, sender=trigger.nick, user=trigger.nick)
            status_tag = ""
            channel = trigger.sender
            if channel and str(channel).startswith('#'):
                item_lower = chosen_item.lower()
                atype = 'none'
                d = {}
                if any(w in item_lower for w in ['coffee', 'latte', 'espresso', 'cappuccino', 'decaf']) and _coffee_is_spiked(chosen_item):
                    mins, intensity, title = _apply_bot_alcohol(bot, channel, trigger.nick, chosen_item, is_hard_liquor=True)
                    status_tag = f" ({bot.nick} is {title} in {channel} ~{_format_dur(mins)})"
                elif any(w in item_lower for w in ['coffee', 'latte', 'espresso', 'cappuccino', 'decaf']):
                    atype, d = _sober_bot(bot, channel, trigger.nick, chosen_item, 'coffee')
                elif any(w in item_lower for w in ['tea', 'chai', 'matcha', 'tisane', 'water', 'h2o', 'mocktail', 'virgin']):
                    atype, d = _sober_bot(bot, channel, trigger.nick, chosen_item, 'water')
                elif any(w in item_lower for w in ['beer', 'ale', 'stout', 'lager', 'ipa', 'pilsner', 'cider', 'magners', 'wine', 'cava', 'champagne', 'prosecco', 'sake', 'mead']):
                    mins, intensity, title = _apply_bot_alcohol(bot, channel, trigger.nick, chosen_item, is_hard_liquor=False)
                    status_tag = f" ({bot.nick} is {title} in {channel} ~{_format_dur(mins)})"
                else:
                    mins, intensity, title = _apply_bot_alcohol(bot, channel, trigger.nick, chosen_item, is_hard_liquor=True)
                    status_tag = f" ({bot.nick} is {title} in {channel} ~{_format_dur(mins)})"

                if not status_tag:
                    if atype == 'sobered_both':
                        status_tag = f" ({bot.nick} is completely sober and down to earth in {channel})"
                    elif atype == 'sobered_drunk':
                        status_tag = f" ({bot.nick} has sobered up in {channel})"
                    elif atype == 'sobered_stoned':
                        status_tag = f" ({bot.nick} sobered up and the high wore off in {channel})"
                    elif atype == 'reduced_drunk':
                        status_tag = f" ({bot.nick} soaked up some booze, down to {d['title']} in {channel} ~{_format_dur(d['mins'])})"

            message = base_message + status_tag
        else:
            message = giving_message.format(drink=chosen_item, user=target_user)
    else:
        # It's food
        chosen_item = random.choice(all_foods)
        if 'pizza' in chosen_item.lower():
            giving_message = random.choice(PIZZA_MESSAGES)
        else:
            giving_message = random.choice(FOOD_MESSAGES)
        
        bot_nick = (getattr(bot, 'nick', '') or '').lower()
        if target_user.lower() == bot_nick:
            giving_message = random.choice(BOT_FOOD_RECEPTION_MESSAGES)
            base_message = giving_message.format(food=chosen_item, sender=trigger.nick, user=trigger.nick)
            status_tag = ""
            channel = trigger.sender
            if channel and str(channel).startswith('#'):
                food_type = 'pizza' if 'pizza' in chosen_item.lower() else 'appetizer'
                atype, d = _sober_bot(bot, channel, trigger.nick, chosen_item, food_type)
                if atype == 'sobered_both':
                    status_tag = f" ({bot.nick} is completely sober and down to earth in {channel})"
                elif atype == 'sobered_drunk':
                    status_tag = f" ({bot.nick} has sobered up in {channel})"
                elif atype == 'sobered_stoned':
                    status_tag = f" ({bot.nick} sobered up and the high wore off in {channel})"
                elif atype == 'reduced_drunk':
                    status_tag = f" ({bot.nick} soaked up some booze, down to {d['title']} in {channel} ~{_format_dur(d['mins'])})"

            message = base_message + status_tag
        else:
            message = giving_message.format(food=chosen_item, user=target_user)
    
    # Send it!
    bot.action(message)


# =======================
# INLINE DRINK TRIGGERS
# =======================
_INLINE_DRINK_MAP = {
    'beer': ('beer', BEERS, BEER_MESSAGES, 'drink'),
    'b33r': ('beer', BEERS, BEER_MESSAGES, 'drink'),
    'shot': ('shot', SHOTS, SHOT_MESSAGES, 'drink'),
    'magners': ('magners', MAGNERS, BEER_MESSAGES, 'drink'),
    'whiskey': ('whiskey', WHISKEYS, WHISKEY_MESSAGES, 'drink'),
    'whisky': ('whiskey', WHISKEYS, WHISKEY_MESSAGES, 'drink'),
    'scotch': ('scotch', SCOTCHES, SCOTCH_MESSAGES, 'drink'),
    'irish': ('irish', IRISH_WHISKEYS, IRISH_MESSAGES, 'drink'),
    'irishwhiskey': ('irish', IRISH_WHISKEYS, IRISH_MESSAGES, 'drink'),
    'vodka': ('vodka', VODKAS, VODKA_MESSAGES, 'drink'),
    'rum': ('rum', RUMS, RUM_MESSAGES, 'drink'),
    'tequila': ('tequila', TEQUILAS, TEQUILA_MESSAGES, 'drink'),
    'gin': ('gin', GINS, GIN_MESSAGES, 'drink'),
    'brandy': ('brandy', BRANDIES, BRANDY_MESSAGES, 'drink'),
    'cognac': ('brandy', BRANDIES, BRANDY_MESSAGES, 'drink'),
    'margarita': ('margarita', MARGARITAS, MARGARITA_MESSAGES, 'drink'),
    'marg': ('margarita', MARGARITAS, MARGARITA_MESSAGES, 'drink'),
    'sake': ('sake', SAKES, SAKE_MESSAGES, 'drink'),
    'liqueur': ('liqueur', LIQUEURS, LIQUEUR_MESSAGES, 'drink'),
    'cordial': ('liqueur', LIQUEURS, LIQUEUR_MESSAGES, 'drink'),
    'mead': ('mead', MEADS, MEAD_MESSAGES, 'drink'),
    'pizza': ('pizza', PIZZAS, PIZZA_MESSAGES, 'food'),
    'drink': ('mixed_drink', MIXED_DRINKS, COCKTAIL_MESSAGES, 'drink'),
    'wine': ('wine', WINES, WINE_MESSAGES, 'drink'),
    'cava': ('cava', CAVAS, CAVA_MESSAGES, 'drink'),
    'prosecco': ('cava', CAVAS, CAVA_MESSAGES, 'drink'),
    'mocktail': ('mocktail', MOCKTAILS, COCKTAIL_MESSAGES, 'drink'),
    'virgin': ('mocktail', MOCKTAILS, COCKTAIL_MESSAGES, 'drink'),
    'coffee': ('coffee', COFFEES, COFFEE_MESSAGES, 'drink'),
    'caffeine': ('coffee', COFFEES, COFFEE_MESSAGES, 'drink'),
    'decaf': ('decaf', DECAFS, COFFEE_MESSAGES, 'drink'),
    'decaffeinated': ('decaf', DECAFS, COFFEE_MESSAGES, 'drink'),
    'tea': ('tea', TEAS, TEA_MESSAGES, 'drink'),
    'cuppa': ('tea', TEAS, TEA_MESSAGES, 'drink'),
    'water': ('water', WATERS, WATER_MESSAGES, 'drink'),
    'hydrate': ('water', WATERS, WATER_MESSAGES, 'drink'),
    'appetizer': ('appetizer', APPETIZERS, FOOD_MESSAGES, 'food'),
    'snack': ('appetizer', APPETIZERS, FOOD_MESSAGES, 'food'),
    'food': ('appetizer', APPETIZERS, FOOD_MESSAGES, 'food'),
}

_INLINE_CMDS_SORTED = sorted(list(_INLINE_DRINK_MAP.keys()) + ['surprise', 'random'], key=len, reverse=True)
_INLINE_BEER_PATTERN = (
    r'^(?!\$).+\$(?P<incmd>'
    + '|'.join(_INLINE_CMDS_SORTED)
    + r')\b(?:\s+(?P<intarget>[a-zA-Z0-9_\[\]\\`^{}|-]+))?'
)

_INLINE_BEER_LAST = {}
_INLINE_BEER_LOCK = threading.Lock()


@module.rule(_INLINE_BEER_PATTERN)
def beer_inline(bot, trigger):
    """Serve a drink or food item when $command appears mid-sentence."""
    if not str(trigger.sender).startswith('#'):
        return

    cmd = trigger.match.group('incmd').lower()
    channel = str(trigger.sender).lower()
    sender = trigger.nick

    # Debounce check (2 seconds per channel & user)
    now = time.time()
    key = (channel, sender.lower(), cmd)
    with _INLINE_BEER_LOCK:
        last = _INLINE_BEER_LAST.get(key, 0)
        if now - last < 2.0:
            return
        _INLINE_BEER_LAST[key] = now

    # Determine target if valid channel member.
    # "$sake hot" and "$coffee iced" mid-sentence pick a style
    # and pour it for the speaker.
    raw_target = trigger.match.group('intarget')
    sake_temp = None
    coffee_style = None
    target = None
    if raw_target and cmd == 'sake':
        sake_temp = _SAKE_TEMPS.get(raw_target.lower())
    elif raw_target and cmd in ('coffee', 'caffeine'):
        coffee_style = _COFFEE_STYLES.get(raw_target.lower())
    if raw_target and not sake_temp and not coffee_style:
        chan_obj = bot.channels.get(str(trigger.sender))
        if chan_obj is not None:
            chan_users = [u.lower() for u in chan_obj.users.keys()]
            if raw_target.lower() in chan_users:
                target = raw_target

    if not target:
        target = sender

    if cmd in ('surprise', 'random'):
        surprise(bot, trigger, target_override=target)
    elif cmd == 'sake' and sake_temp:
        item_list, message_list = _sake_menu(sake_temp)
        _serve_item(
            bot, trigger, 'sake', item_list, message_list,
            target_override=target,
        )
    elif cmd in ('coffee', 'caffeine') and coffee_style:
        item_list, message_list, effect = _coffee_menu(coffee_style)
        _serve_item(
            bot, trigger, 'coffee', item_list, message_list,
            target_override=target,
            effect_type=effect,
        )
    elif cmd in _INLINE_DRINK_MAP:
        item_type, item_list, message_list, placeholder_key = _INLINE_DRINK_MAP[cmd]
        is_1337 = (cmd == 'b33r')
        _serve_item(bot, trigger, item_type, item_list, message_list, placeholder_key=placeholder_key, target_override=target, is_1337=is_1337)


@module.commands('barhelp')
@module.example('$barhelp', 'Get a list of all bartender commands')
def barhelp(bot, trigger):
    """Get help for all bartender commands! 📋"""
    
    user = trigger.nick
    
    # Notify in channel
    bot.say(f"{user}: Check your PM for the bartender menu! 📬")
    
    # Send detailed help via PM
    help_messages = [
        "🍺 BARTENDER MENU 🍺",
        "=" * 40,
        "ECONOMY:",
        f"  • Your first order opens a tab with {NEW_USER_STARTING_COINS} starting coins",
        f"  • You receive a {DAILY_BONUS_COINS} coin credit every 24 hours",
        "  • Credits are applied when you order or run $barcash; $tip does not trigger credits",
        "  • Coins are shared with the mug game - one balance for everything",
        "  • Water is always FREE!",
        "",
        "BOT INTOXICATION:",
        "  • Buying drinks for the bot makes it progressively drunk in chat! 😵🍺",
        "  • Shots & spirits increase drunkenness faster (+2) than beer & wine (+1).",
        "  • Food ($pizza, $appetizer) and coffee/water sober the bot back up! 🍕☕",
        "",
        "DRINKS:",

        "  $scotch [user] .......... 15 coins",
        "  $whiskey [user] ......... 12 coins",
        "  $irish [user] ........... 10 coins",
        "  $beer [user] ............ 5 coins",
        "  $b33r [user] ............ 5 coins (1337 speak)",
        "  $shot [user] ............ 7 coins",
        "  $vodka [user] ........... 10 coins",
        "  $rum [user] ............. 10 coins",
        "  $tequila [user] ......... 10 coins",
        "  $gin [user] ............. 10 coins",
        "  $brandy [user] .......... 12 coins",
        "  $drink [user] ........... 10 coins",
        "  $margarita [user] ....... 9 coins",
        "  $sake [hot|cold] [user] . 9 coins",
        "  $liqueur [user] ......... 8 coins",
        "  $wine [user] ............ 8 coins",
        "  $cava [user] ............ 8 coins",
        "  $mead [user] ............ 7 coins",
        "  $magners [user] ......... 6 coins",
        "",
        "NON-ALCOHOLIC:",
        "  $mocktail [user] ........ 4 coins",
        "  $coffee [iced|hot|irish|mine] [user] ... 3 coins",
        "    mine = hot dark roast + a shot of Tullamore Dew",
        "  $decaf [user] ........... 3 coins",
        "  $tea [user] ............. 3 coins",
        "  $water [user] ........... FREE",
        "",
        "FOOD:",
        "  $pizza [user] ........... 15 coins",
        "  $appetizer [user] ....... 8 coins",
        "",
        "SPECIAL:",
        "  $surprise [user] ........ (Random Price)",
        "  Inline $command mid-sentence also orders items (e.g. 'time for $beer')",
        "",
        "COMMANDS:",
        "  $tip <user> <amount> - Tip another user (comes from your coins)",
        "  $barcash - Check your coin balance (alias: $balance)",
        "  $toptip - See the top 5 most tipped users",
        "  $barhelp - Show this menu",
        "  $adjbal <nick> <+amount|-amount|amount> - Admin PM only: adjust a user's balance",
        "  $barreset <nick> | all confirm - Admin PM only: reset a user's balance or all balances (requires confirm)",
        "",
        "🍻 Enjoy responsibly! 🍻"
    ]
    
    # Send each line as a separate PM
    for line in help_messages:
        bot.say(line, user)


@module.commands('tip')
@module.example('$tip username 50', 'Tip a user 50 coins')
def tip_user(bot, trigger):
    """Tip another user for great service! 💰"""

    tipper = str(trigger.account or trigger.nick)
    tipper_display = trigger.nick

    # Check if user specified who to tip and amount
    if not trigger.group(2):
        bot.say(f"{tipper_display}: Usage: $tip <user> <amount>")
        return

    args = trigger.group(2).strip().split()
    if len(args) < 2:
        bot.say(f"{tipper_display}: Usage: $tip <user> <amount>")
        return

    recipient = args[0]
    try:
        amount = int(args[1])
    except ValueError:
        bot.say(f"{tipper_display}: Amount must be a number!")
        return

    tipper_key = _user_key(tipper)
    recipient_key = _user_key(recipient)

    # Can't tip yourself (compare normalized keys so format codes can't dodge it)
    if recipient_key in (tipper_key, _user_key(tipper_display)):
        bot.say(f"{tipper_display}: You can't tip yourself! 🙄")
        return

    # Amount must be positive
    if amount <= 0:
        bot.say(f"{tipper_display}: Tip amount must be positive!")
        return

    # Tips come out of mug game coins - the same balance drinks are bought with
    with _mug_txn(bot):
        data = _load_mug_data(bot)
        users = data.get('users', {})

        if tipper_key not in users or not isinstance(users[tipper_key], dict):
            bot.say(f"{tipper_display}: You need to buy something from the bar first to open a tab!")
            return

        tipper_rec = users[tipper_key]
        balance = int(tipper_rec.get('money', 0))

        # Check if tipper has enough balance
        if balance < amount:
            bot.notice(f"You don't have enough! Balance: {balance} coins", tipper_display)
            return

        recipient_rec, _, _ = _touch_mug_user(data, recipient, time.time())

        # Transfer the coins
        tipper_rec['money'] = balance - amount
        recipient_rec['money'] = int(recipient_rec.get('money', 0)) + amount

        if not _save_mug_data(bot, data):
            bot.say(f"{tipper_display}: Something went wrong saving the tip. Sorry!")
            return

        # Track the tip leaderboard
        tips = _load_tip_data(bot)
        tips['tips_received'][recipient_key] = tips['tips_received'].get(recipient_key, 0) + amount
        _save_tip_data(bot, tips)

    bot.say(f"{tipper_display} tips {recipient} {amount} coins! 💰✨")
    bot.notice(f"New balance: {tipper_rec['money']} coins 🪙", tipper_display)


@module.commands('barcash', 'balance')
@module.example('$barcash', 'Check your current balance')
def barcash(bot, trigger):
    """Check your current bar coin balance! 💵"""

    user = str(trigger.account or trigger.nick)
    user_display = trigger.nick

    with _mug_txn(bot):
        data = _load_mug_data(bot)
        user_rec, was_new, got_daily = _touch_mug_user(data, user, time.time())
        if was_new or got_daily:
            _save_mug_data(bot, data)

    balance = int(user_rec.get('money', 0))
    if was_new:
        bot.notice(f"Welcome to the bar! 🍺 You received {NEW_USER_STARTING_COINS} starting coins. Balance: {balance} coins 🪙", user_display)
    elif got_daily:
        bot.notice(f"Daily bonus! +{DAILY_BONUS_COINS} coins 💰 Balance: {balance} coins 🪙", user_display)
    else:
        bot.notice(f"Your balance is {balance} coins 🪙", user_display)


@module.commands('toptip')
@module.example('$toptip', 'See the top 5 most tipped users')
def toptip(bot, trigger):
    """See the top 5 most tipped bartenders! 🏆"""
    
    data = _load_tip_data(bot)
    tips = data.get('tips_received', {})
    
    if not tips:
        bot.say("No tips have been given yet! Be the first to tip someone! 💰")
        return
    
    # Sort by tips received
    sorted_tips = sorted(tips.items(), key=lambda x: x[1], reverse=True)[:5]
    
    bot.say("🏆 TOP 5 BARTENDERS 🏆")
    for i, (user, amount) in enumerate(sorted_tips, 1):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i-1]
        bot.say(f"{medal} {user}: ${amount} in tips")


@module.commands('adjbal')
@module.example('$adjbal username +100', 'Adjust a user\'s balance by +100 (admin PM only)')
def adjbal(bot, trigger):
    """Admin-only PM command to adjust a user's balance.

    Usage (PM to bot): $adjbal <nick> <+amount| -amount | amount>
    Example: $adjbal m0n +100  (adds $100)
             $adjbal m0n -50   (subtracts $50)
    """
    # Require PM
    try:
        is_pm = getattr(trigger, 'is_privmsg', False) or (not trigger.sender.startswith('#'))
    except Exception:
        is_pm = getattr(trigger, 'is_privmsg', False)

    if not is_pm:
        try:
            bot.reply('Please PM me this admin command.')
        except Exception:
            pass
        return

    # Authorization: Sopel may set trigger.admin/owner for privileged users
    if not (getattr(trigger, 'admin', False) or getattr(trigger, 'owner', False)):
        # Also allow configured core admins if present
        try:
            cfg_admins = getattr(bot.config.core, 'admins', None)
            allowed = False
            if isinstance(cfg_admins, (list, tuple, set)):
                allowed = trigger.nick.lower() in {a.lower() for a in cfg_admins}
            elif isinstance(cfg_admins, str) and cfg_admins.strip():
                allowed = trigger.nick.lower() in {a.strip().lower() for a in re.split(r'[,\s]+', cfg_admins) if a.strip()}
            if not allowed:
                bot.reply('You are not authorized to use this command.')
                return
        except Exception:
            bot.reply('You are not authorized to use this command.')
            return

    if not trigger.group(2):
        bot.reply('Usage: $adjbal <nick> <+amount|-amount|amount>')
        return

    parts = trigger.group(2).strip().split()
    if len(parts) < 2:
        bot.reply('Usage: $adjbal <nick> <+amount|-amount|amount>')
        return

    nick = parts[0]
    amt_s = parts[1]
    # Allow +100, -50, or plain number
    try:
        amt = int(amt_s)
    except ValueError:
        bot.reply('Amount must be an integer like +100 or -50.')
        return

    with _mug_txn(bot):
        data = _load_mug_data(bot)
        user_rec, _, _ = _touch_mug_user(data, nick, time.time())

        old = int(user_rec.get('money', 0))
        new = old + amt
        if new < 0:
            bot.reply(f'Operation would make {nick}\'s balance negative ({new}). Operation cancelled.')
            return

        user_rec['money'] = new
        _save_mug_data(bot, data)

        bot.reply(f"Adjusted {nick}'s balance: {old} -> {new} coins")



@module.commands('barreset')
@module.example('$barreset username', "Reset a user's balance to starting coins (admin PM only)")
@module.example('$barreset all confirm', 'Reset all balances to starting coins and clear tips (admin PM only, requires confirm)')
def barreset(bot, trigger):
    """Admin-only PM command to reset balances.

    Balances are mug game coins, so this also resets mug game money.

    Usage (PM to bot):
      $barreset <nick>            # reset a single user's balance and clear their tips
      $barreset all confirm      # reset ALL balances and clear all tips (requires confirm)
    """
    # Require PM
    try:
        is_pm = getattr(trigger, 'is_privmsg', False) or (not trigger.sender.startswith('#'))
    except Exception:
        is_pm = getattr(trigger, 'is_privmsg', False)

    if not is_pm:
        try:
            bot.reply('Please PM me this admin command.')
        except Exception:
            pass
        return

    # Authorization: same checks as adjbal
    if not (getattr(trigger, 'admin', False) or getattr(trigger, 'owner', False)):
        try:
            cfg_admins = getattr(bot.config.core, 'admins', None)
            allowed = False
            if isinstance(cfg_admins, (list, tuple, set)):
                allowed = trigger.nick.lower() in {a.lower() for a in cfg_admins}
            elif isinstance(cfg_admins, str) and cfg_admins.strip():
                allowed = trigger.nick.lower() in {a.strip().lower() for a in re.split(r'[,\s]+', cfg_admins) if a.strip()}
            if not allowed:
                bot.reply('You are not authorized to use this command.')
                return
        except Exception:
            bot.reply('You are not authorized to use this command.')
            return

    if not trigger.group(2):
        bot.reply('Usage: $barreset <nick> OR $barreset all confirm')
        return

    parts = trigger.group(2).strip().split()
    target = parts[0]

    with _mug_txn(bot):
        data = _load_mug_data(bot)
        tips = _load_tip_data(bot)
        now = time.time()

        if target.lower() == 'all':
            # require explicit confirmation to prevent accidents
            if len(parts) < 2 or parts[1].lower() != 'confirm':
                bot.reply('To reset ALL balances, PM me: $barreset all confirm')
                return

            # Reset everything
            for rec in data.get('users', {}).values():
                if isinstance(rec, dict):
                    rec['money'] = NEW_USER_STARTING_COINS
                    rec['last_bar_credit'] = now
            tips['tips_received'] = {}

            _save_mug_data(bot, data)
            _save_tip_data(bot, tips)
            bot.reply(f'All balances reset to {NEW_USER_STARTING_COINS} coins and all tips cleared.')
            return

        # Single user reset
        nick = target
        nick_key = _user_key(nick)
        user_rec, was_new, _ = _touch_mug_user(data, nick, now)
        user_rec['money'] = NEW_USER_STARTING_COINS
        user_rec['last_bar_credit'] = now
        tips['tips_received'].pop(nick_key, None)

        _save_mug_data(bot, data)
        _save_tip_data(bot, tips)
        if was_new:
            bot.reply(f"{nick} did not have an account; initialized to {NEW_USER_STARTING_COINS} coins.")
        else:
            bot.reply(f"Reset {nick}'s balance to {NEW_USER_STARTING_COINS} coins and cleared tips.")


def _cleanup():
    """Cleanup on shutdown (empty for bot.db approach)."""
    pass


atexit.register(_cleanup)
