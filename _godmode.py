"""Shared godmode state for glitchy luck/RNG plugins.

Not a plugin (leading underscore, so ibot/sopel will not load it and it
will not register any commands). The $godmode command itself lives in
mug.py (admin, PM-only).

Import and call has_godmode(bot, nick). State is kept in:

  * an in-process cache
  * bot.memory['godmode_nicks'] when a bot is provided
  * Sopel/ibot bot.db (plugin 'godmode', key 'nicks')
  * a JSON file (godmode_nicks.json) in the bot homedir, or
    ~/.sopel/godmode_nicks.json if no bot/homedir is available

so it survives $reload, $rehash, and process restarts. Plugins can call
has_godmode() even if mug is not loaded yet; the first check lazy-loads
from the persisted store.
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from typing import Iterable, Optional

MEMORY_KEY = 'godmode_nicks'
DB_PLUGIN = 'godmode'
DB_KEY = 'nicks'
JSON_NAME = 'godmode_nicks.json'
DEFAULT_FALLBACK_DIR = os.path.expanduser('~/.sopel')

# Same nick folding as mug.normalize_key, kept local so other plugins
# can check godmode without importing mug.
_MIRC_COLOR_RE = re.compile(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?')
_IRC_CTRL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\u200b\u200c\u200d\uFEFF]')
_IRC_FORMAT_RE = re.compile(r'[\x02\x0f\x16\x1d\x1f]')

_lock = threading.RLock()
_cache: set[str] = set()
_loaded = False


def normalize_nick(nick: str) -> str:
    if not nick:
        return ""
    s = str(nick)
    s = unicodedata.normalize('NFKC', s)
    s = _MIRC_COLOR_RE.sub('', s)
    s = _IRC_FORMAT_RE.sub('', s)
    s = _IRC_CTRL_RE.sub('', s)
    return s.strip()


def normalize_key(nick: str) -> str:
    return normalize_nick(nick).lower()


def _json_path(bot=None) -> str:
    if bot is not None:
        try:
            homedir = getattr(getattr(bot, 'config', None), 'core', None)
            homedir = getattr(homedir, 'homedir', None) if homedir is not None else None
            if homedir:
                return os.path.join(str(homedir), JSON_NAME)
        except Exception:
            pass
    return os.path.join(DEFAULT_FALLBACK_DIR, JSON_NAME)


def _read_json(path: str) -> Optional[set[str]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if isinstance(data, dict):
        nicks = data.get('nicks', [])
    elif isinstance(data, list):
        nicks = data
    else:
        return None
    if not isinstance(nicks, (list, tuple, set)):
        return None
    return {normalize_key(n) for n in nicks if n}


def _write_json(path: str, nicks: set[str]) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'nicks': sorted(nicks)}, f, indent=2)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _read_db(bot) -> Optional[set[str]]:
    if bot is None:
        return None
    try:
        db = getattr(bot, 'db', None)
        if db is None:
            return None
        saved = db.get_plugin_value(DB_PLUGIN, DB_KEY)
    except Exception:
        return None
    if saved is None:
        return None
    if isinstance(saved, str):
        try:
            saved = json.loads(saved)
        except Exception:
            saved = [saved]
    if isinstance(saved, dict):
        saved = saved.get('nicks', [])
    if isinstance(saved, (list, tuple, set)):
        return {normalize_key(n) for n in saved if n}
    return None


def _write_db(bot, nicks: set[str]) -> None:
    if bot is None:
        return
    db = getattr(bot, 'db', None)
    if db is None:
        return
    db.set_plugin_value(DB_PLUGIN, DB_KEY, sorted(nicks))


def _write_memory(bot, nicks: Iterable[str]) -> None:
    if bot is None:
        return
    try:
        memory = getattr(bot, 'memory', None)
        if memory is None:
            return
        memory[MEMORY_KEY] = set(nicks)
    except Exception:
        pass


def _read_memory(bot) -> Optional[set[str]]:
    if bot is None:
        return None
    try:
        memory = getattr(bot, 'memory', None)
        if memory is None:
            return None
        mem = memory.get(MEMORY_KEY)
    except Exception:
        return None
    if mem is None:
        return None
    if isinstance(mem, (set, list, tuple)):
        return {normalize_key(n) for n in mem if n}
    return None


def load(bot=None) -> set[str]:
    """Load persisted nicks into the cache and bot.memory.

    Prefer bot.db when it has a stored value (including an empty list).
    Otherwise fall back to the JSON file. Does not overwrite a stored
    empty list with stale JSON.
    """
    global _loaded
    with _lock:
        nicks = _read_db(bot)
        if nicks is None:
            nicks = _read_json(_json_path(bot))
        if nicks is None:
            nicks = set()
        _cache.clear()
        _cache.update(nicks)
        _write_memory(bot, _cache)
        if bot is not None and _read_db(bot) is None and _cache:
            try:
                _write_db(bot, _cache)
            except Exception:
                pass
        _loaded = True
        return set(_cache)


def save(bot=None) -> None:
    """Persist the current cache to memory, bot.db, and the JSON file."""
    with _lock:
        nicks = set(_cache)
        _write_memory(bot, nicks)
        try:
            _write_db(bot, nicks)
        except Exception:
            pass
        try:
            _write_json(_json_path(bot), nicks)
        except Exception:
            pass


def has_godmode(bot, nick: str) -> bool:
    """Return True if nick currently has godmode.

    Checks bot.memory first, then the in-process cache, lazy-loading
    from bot.db / JSON when this process has not loaded state yet.
    """
    key = normalize_key(nick)
    if not key:
        return False
    with _lock:
        mem = _read_memory(bot)
        if mem is not None:
            return key in mem
        if not _loaded:
            load(bot)
        return key in _cache


def set_godmode(bot, nick: str, enabled: bool) -> str:
    """Enable or disable godmode for nick and persist. Returns the key."""
    key = normalize_key(nick)
    if not key:
        return key
    with _lock:
        if not _loaded:
            load(bot)
        if enabled:
            _cache.add(key)
        else:
            _cache.discard(key)
        _write_memory(bot, _cache)
        save(bot)
    return key


def list_nicks(bot=None) -> list[str]:
    """Return sorted normalized nicks that currently have godmode."""
    with _lock:
        mem = _read_memory(bot)
        if mem is not None:
            return sorted(mem)
        if not _loaded:
            load(bot)
        return sorted(_cache)


def reset_for_tests() -> None:
    """Clear in-process state. For unit tests only."""
    global _loaded
    with _lock:
        _cache.clear()
        _loaded = False
