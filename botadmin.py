# botadmin.py — Owner-only bot management commands (supplements built-in admin)
# Commands here are unique to this plugin and DO NOT duplicate the built-in
# admin plugin's commands (bjoin, bpart, bmode, say, act, raw, bnick, bquit,
# rehash, reload, load, unload, plugins, bstatus, enable, disable, disabled).
from __future__ import annotations

import os
import sys
import time
import logging
import threading

from sopel import module

LOG = logging.getLogger(__name__)

# Owner nicks (lowercase) — hardcoded fallback if config parsing fails
OWNER_NICKS = {'end3r'}


def _is_owner(bot, trigger):
    """Check if the trigger is from the bot owner.

    Prefer the core's owner flag (which honours owner_account when set);
    fall back to nick matching only when the trigger has no such flag.
    """
    if getattr(trigger, 'owner', False):
        return True
    try:
        if getattr(bot.config.core, 'owner_account', None):
            return False
    except Exception:
        pass
    nick = trigger.nick.lower()
    if nick in OWNER_NICKS:
        return True
    try:
        cfg_owner = bot.config.core.owner
        if isinstance(cfg_owner, (list, tuple, set)):
            if nick in {o.lower() for o in cfg_owner}:
                return True
        elif cfg_owner and nick == str(cfg_owner).lower():
            return True
    except Exception:
        pass
    return getattr(trigger, 'owner', False)


def _is_admin(bot, trigger):
    """Check if the trigger nick is an admin (or owner)."""
    if _is_owner(bot, trigger):
        return True
    if getattr(trigger, 'admin', False):
        return True
    try:
        cfg_admins = getattr(bot.config.core, 'admins', None)
        if isinstance(cfg_admins, (list, tuple, set)):
            return trigger.nick.lower() in {a.lower() for a in cfg_admins}
        if isinstance(cfg_admins, str) and cfg_admins.strip():
            import re
            admins = {a.strip().lower() for a in re.split(r'[,\\s]+', cfg_admins) if a.strip()}
            return trigger.nick.lower() in admins
    except Exception:
        pass
    return False


def _deny(bot, trigger):
    bot.reply('⛔ You are not authorized to use this command.')


def _pm_reply(bot, trigger, msg):
    """Reply to PM or channel appropriately."""
    if trigger.sender.startswith('#'):
        bot.say(msg, trigger.sender)
    else:
        bot.say(msg, trigger.nick)


# ------------------------------------------------------------------
# $restart — restart the bot process (clean restart)
# ------------------------------------------------------------------
_restart_lock = threading.Lock()
_restarting = False
IBOT_HOME_FALLBACK = '/home/ender/ibot'


def _ibot_home():
    """Directory that contains the ``ibot`` package (cwd for ``-m ibot``)."""
    mod = sys.modules.get('ibot')
    path = getattr(mod, '__file__', None) if mod else None
    if path:
        return os.path.dirname(os.path.dirname(os.path.abspath(path)))
    return IBOT_HOME_FALLBACK


def _restart_argv(cfg_path):
    """argv for re-exec: keep the original flags (e.g. -v) when possible."""
    orig = list(sys.argv[1:])
    if any(os.path.abspath(a) == cfg_path for a in orig if not a.startswith('-')):
        args = [cfg_path if (not a.startswith('-') and os.path.abspath(a) == cfg_path) else a
                for a in orig]
    else:
        args = [cfg_path] + [a for a in orig if a.startswith('-')]
    return [sys.executable, '-m', 'ibot'] + args


def _run_shutdowns(bot, timeout=5.0):
    """Best effort: run plugin shutdown hooks so state is flushed."""
    core = getattr(bot, '_bot', None)
    plugins = list(getattr(getattr(core, 'dispatcher', None), 'plugins', []) or [])

    def _go():
        for p in plugins:
            fn = getattr(p, 'shutdown_func', None)
            if fn:
                try:
                    fn(bot)
                except Exception:
                    LOG.exception('shutdown hook for %s failed', getattr(p, 'name', '?'))

    t = threading.Thread(target=_go, daemon=True, name='botadmin-restart-shutdown')
    t.start()
    t.join(timeout)


def _exec_restart(bot, message):
    """Fallback restart: run shutdown hooks, then re-exec
    ``python -m ibot <cfg>`` from the ibot directory."""
    cfg = getattr(getattr(bot, 'config', None), 'filename', None)
    if not cfg:
        raise RuntimeError('config filename unknown')
    cfg_path = os.path.abspath(cfg)
    home = _ibot_home()
    if not os.path.isfile(cfg_path):
        raise RuntimeError('config file not found')
    if not os.path.isfile(os.path.join(home, 'ibot', '__main__.py')):
        raise RuntimeError('ibot package not found')
    argv = _restart_argv(cfg_path)
    LOG.warning('restart: re-exec %s (cwd %s)', argv[:3], home)
    # No QUIT first: if the core treats the server closing the link as
    # "stop", the process could exit before we get to exec.  The IRC socket
    # is non-inheritable, so exec closes it and the server drops us at once.
    time.sleep(1.0)            # let the "Restarting" reply flush
    _run_shutdowns(bot)
    logging.shutdown()
    os.chdir(home)
    os.execv(sys.executable, argv)


@module.commands('restart')
def restart(bot, trigger):
    """$restart — Restart the bot (owner only)."""
    global _restarting
    if not _is_owner(bot, trigger):
        _deny(bot, trigger)
        return
    with _restart_lock:
        if _restarting:
            _pm_reply(bot, trigger, '⏳ Restart already in progress.')
            return
        _restarting = True
    msg = 'Restarting — be right back!'
    _pm_reply(bot, trigger, '🔄 Restarting bot process...')
    LOG.info('RESTART requested by %s', trigger.nick)
    try:
        # Preferred: the core's own restart (ibot shim, when available).
        if callable(getattr(bot, 'restart', None)):
            bot.restart(msg)
            return
        _exec_restart(bot, msg)
    except Exception as e:
        LOG.exception('restart failed')
        with _restart_lock:
            _restarting = False
        _pm_reply(bot, trigger, f'❌ Restart failed ({type(e).__name__}); bot still running.')


# ------------------------------------------------------------------
# $breload <module|all> — alias for the core $reload / $rehash
# ------------------------------------------------------------------
def _core_handler(bot, command):
    """Find another plugin's handler for ``command`` (e.g. ibot core admin)."""
    core = getattr(bot, '_bot', None)
    handlers = getattr(getattr(core, 'dispatcher', None), '_command_handlers', None) or []
    for entry in handlers:
        try:
            _compiled, func, pname = entry
        except (TypeError, ValueError):
            continue
        if pname == __name__.rsplit('.', 1)[-1] or pname == 'botadmin':
            continue
        if command in (getattr(func, '_commands', None) or ()):
            return func
    return None


@module.commands('breload')
def reload_plugin(bot, trigger):
    """$breload <module|all> — Reload one plugin (same as $reload) or all
    plugins (same as $rehash). Owner only."""
    if not _is_owner(bot, trigger):
        _deny(bot, trigger)
        return
    arg = (trigger.group(2) or '').strip()
    if not arg:
        _pm_reply(bot, trigger, '❓ Usage: $breload <module_name|all>')
        return
    if arg.lower() == 'all':
        func = _core_handler(bot, 'rehash')
        if func is None:
            _pm_reply(bot, trigger, '⚠️ Core $rehash not found; use $rehash.')
            return
        func(bot, trigger)
        return
    if callable(getattr(bot, 'reload_plugin', None)):
        try:
            bot.reload_plugin(arg)
            _pm_reply(bot, trigger, f'✅ Reloaded plugin: {arg}')
        except Exception as e:
            LOG.exception('Failed to reload plugin %s', arg)
            _pm_reply(bot, trigger, f'❌ Failed to reload {arg} ({type(e).__name__}).')
        return
    func = _core_handler(bot, 'reload')
    if func is None:
        _pm_reply(bot, trigger, '⚠️ Core $reload not found; use $reload <name>.')
        return
    # The core handler reads the plugin name from trigger.group(2), which is
    # the same argument $breload received.
    func(bot, trigger)


# ------------------------------------------------------------------
# $bothelp — list available admin commands
# ------------------------------------------------------------------
@module.commands('bothelp')
def bothelp(bot, trigger):
    """$bothelp — List admin bot commands."""
    if not _is_admin(bot, trigger):
        _deny(bot, trigger)
        return
    lines = [
        '🛠️ Bot Admin Commands:',
        '  $restart — Restart the bot process (owner)',
        '  $rehash — Hot-reload config and all plugins (owner)',
        '  $breload <module|all> — Same as $reload <module> / $rehash (owner)',
        '  $reload <module> — Reload a single plugin (owner)',
        '  $bquit [msg] — Shut down the bot (owner)',
        '  $say <target> <msg> — Say something (admin)',
        '  $act <target> <action> — /me action (admin)',
        '  $raw <irc line> — Raw IRC command (owner)',
        '  $bnick <nick> — Change bot nick (owner)',
        '  $bjoin #chan [key] — Join channel (admin)',
        '  $bpart #chan [msg] — Leave channel (admin)',
        '  $bmode #chan <mode> [nick] — Set mode (admin)',
        '  $enable <plugin> <#chan> — Enable plugin in channel (admin, PM only)',
        '  $disable <plugin> <#chan> — Disable plugin in channel (admin, PM only)',
        '  $disabled [#chan] — List disabled plugins (admin, PM only)',
        '  $bstatus — Show bot status info (admin)',
    ]
    for line in lines:
        if trigger.sender.startswith('#'):
            bot.notice(line, trigger.nick)
        else:
            bot.say(line, trigger.nick)
