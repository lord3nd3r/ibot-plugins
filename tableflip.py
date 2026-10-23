from sopel import module

import time
import threading

MESSAGES = [
    "╭∩╮( º.º )╭∩╮",
    "┬─┬ノ( º _ ºノ)",
    "o(*≧▽≦)ツ┏━┓",
    "(╯°□°）╯︵ ┻━┻"
]

COOLDOWN_SECONDS = 60          # per user, per channel
CHANNEL_COOLDOWN_SECONDS = 30  # any user, per channel
STEP_DELAY = 2

# Cooldown state: {(channel, user): last_time}, {channel: last_time}
cooldowns = {}
channel_cooldowns = {}
_notified = {}                 # (channel, user) -> last cooldown notice
_lock = threading.Lock()
_running = set()               # channels with a sequence in progress


def _prune(store, max_age, now):
    if len(store) > 1000:
        for k in [k for k, t in store.items() if now - t > max_age]:
            store.pop(k, None)


@module.commands('flip')
def tableflip(bot, trigger):
    channel = str(trigger.sender).lower()
    user = str(trigger.nick).lower()
    key = (channel, user)
    now = time.time()
    with _lock:
        _prune(cooldowns, COOLDOWN_SECONDS, now)
        _prune(_notified, COOLDOWN_SECONDS, now)
        user_left = COOLDOWN_SECONDS - (now - cooldowns.get(key, 0))
        chan_left = CHANNEL_COOLDOWN_SECONDS - (now - channel_cooldowns.get(channel, 0))
        busy = channel in _running
        if busy or user_left > 0 or chan_left > 0:
            # Tell the user once per window; otherwise stay quiet.
            notify = now - _notified.get(key, 0) >= COOLDOWN_SECONDS
            if notify:
                _notified[key] = now
        else:
            notify = False
            cooldowns[key] = now
            channel_cooldowns[channel] = now
            _running.add(channel)
    if busy or user_left > 0 or chan_left > 0:
        if notify:
            remaining = int(max(user_left, chan_left, 1))
            bot.notice(f"(｡•́︿•̀｡) Whoa! Please wait {remaining}s before flipping again! ⏳✨",
                       trigger.nick)
        return
    # Handlers already run in their own thread, so play the sequence here
    # rather than spawning a new thread per call.
    try:
        for i, msg in enumerate(MESSAGES):
            if i:
                time.sleep(STEP_DELAY)
            bot.say(msg, trigger.sender)
    finally:
        with _lock:
            _running.discard(channel)
