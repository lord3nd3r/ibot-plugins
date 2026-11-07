"""Unhinged mode session tracking in ai-multi.py: only the bot's own lines sent while
unhinged was on are left out of normal-mode context (per-user history, channel log,
reviews, chime-ins, PM directives, in-flight replies)."""

import datetime
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest

ai = None
TMP = None


@pytest.fixture(autouse=True)
def _bind(request, tmp_path):
    global ai, TMP
    ai = request.getfixturevalue('ai')
    TMP = str(tmp_path)
    while not ai.API_TASK_QUEUE.empty():
        ai.API_TASK_QUEUE.get_nowait()
    yield


class Bot:
    logger = logging.getLogger('t')
    def __init__(self, db):
        self.nick = 'glitchy'
        self.memory = {'grok_db_path': db, 'grok_channel_settings_cache': {}, 'grok_channel_log': {},
                       'grok_say_lock': threading.Lock(), 'grok_history': {}, 'grok_locks': {},
                       'grok_locks_lock': threading.Lock()}
        self.said = []
        self.channels = {}
    def say(self, text, dest=None):
        self.said.append((dest, text)); ai._log_bot_channel_line(self, dest, text)
    def action(self, text, dest=None): self.say(text, dest)
    def reply(self, text): self.said.append((None, text))

def newbot(name='a'):
    b = Bot(os.path.join(TMP, name + '.sqlite3')); ai._init_db(b); ai._load_unhinged_intervals(b); return b
def iso(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None).isoformat()


def test_toggle_intervals_casefold_repeats_persist():
    b = newbot('toggle')
    assert ai._db_set_channel_unhinged(b, '#CyberSpace', True)
    on1 = ai._unhinged_intervals(b, '#cyberspace')[0][0]
    ai._db_set_channel_unhinged(b, '#cyberspace', True)          # on/on no-op
    assert len(ai._unhinged_intervals(b, '#CYBERSPACE')) == 1
    time.sleep(0.01); ai._db_set_channel_unhinged(b, '#Cyberspace', False)
    ai._db_set_channel_unhinged(b, '#cyberspace', False)          # off/off no-op
    ivs = ai._unhinged_intervals(b, '#cyberspace'); assert len(ivs) == 1 and ivs[0][1] > on1
    ai._db_set_channel_unhinged(b, '#cyberspace', True); ai._db_set_channel_unhinged(b, '#cyberspace', False)
    assert len(ai._unhinged_intervals(b, '#cyberspace')) == 2
    assert ai._unhinged_intervals(b, '#other') == []              # isolation
    # restart: fresh memory, same DB
    b2 = Bot(b.memory['grok_db_path']); ai._init_db(b2); ai._load_unhinged_intervals(b2)
    assert ai._unhinged_intervals(b2, '#cyberspace') == ivs + [ai._unhinged_intervals(b, '#cyberspace')[1]]
    assert 'grok_unhinged_off_at' not in b2.memory

def test_boundaries_and_on_mode_no_filter():
    b = newbot('bound')
    ai._unhinged_record(b, '#c', True, now=1000.0); ai._unhinged_record(b, '#c', False, now=2000.0)
    ai._unhinged_record(b, '#c', True, now=3000.0); ai._unhinged_record(b, '#c', False, now=4000.0)
    b.memory['grok_channel_settings_cache']['#c'] = {'unhinged': 0}
    h = ai._unhinged_hidden_intervals(b, '#C')
    for ts, exp in [(999.99, False), (1000.0, True), (1500, True), (2000.0, True), (2000.01, False),
                    (2500, False), (3000, True), (4000.5, False), (iso(1500), True), (iso(2500), False)]:
        assert ai._unhinged_ts_hidden(h, ts) == exp, (ts, exp)
    assert ai._unhinged_hidden_intervals(b, '#d') == []
    b.memory['grok_channel_settings_cache']['#c'] = {'unhinged': 1}
    assert ai._unhinged_hidden_intervals(b, '#c') == []           # on: nothing hidden
    assert ai._unhinged_hidden_intervals(b, 'SomeNick') == []

def test_legacy_migration_no_fabrication():
    db = os.path.join(TMP, 'legacy.sqlite3'); b = Bot(db); ai._init_db(b)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO grok_channel_settings (channel, talkback, enabled, unhinged) VALUES ('#cyberspace',1,1,1)")
    now = time.time()
    b.memory['grok_unhinged_off_at'] = {'#Oldoff': now - 100, '#ancient': now - 90 * 86400}
    ai._load_unhinged_intervals(b)
    assert ai._unhinged_intervals(b, '#cyberspace') == [[None, None]]
    assert ai._unhinged_intervals(b, '#oldoff') == [[None, now - 100]]
    assert ai._unhinged_intervals(b, '#ancient') == []
    ai._load_unhinged_intervals(b)                                # idempotent on reload
    assert ai._unhinged_intervals(b, '#cyberspace') == [[None, None]]
    ai._db_set_channel_unhinged(b, '#cyberspace', False)
    iv = ai._unhinged_intervals(b, '#cyberspace'); assert iv[0][0] is None and iv[0][1] is not None
    h = ai._unhinged_hidden_intervals(b, '#cyberspace')
    assert ai._unhinged_ts_hidden(h, 0.0) and not ai._unhinged_ts_hidden(h, time.time() + 5)
    assert ai._unhinged_recently_off(b, '#cyberspace')

def test_pruning():
    b = newbot('prune'); now = time.time(); keep = ai.UNHINGED_INTERVAL_KEEP_SECS
    ai._unhinged_record(b, '#p', True, now=now - keep - 200); ai._unhinged_record(b, '#p', False, now=now - keep - 100)
    ai._unhinged_record(b, '#p', True, now=now); ai._unhinged_record(b, '#p', False, now=now + 1)
    assert len(ai._unhinged_intervals(b, '#p')) == 1
    b2 = Bot(b.memory['grok_db_path']); ai._load_unhinged_intervals(b2); assert len(ai._unhinged_intervals(b2, '#p')) == 1

def test_span_and_snapshot_marker():
    b = newbot('span')
    ai._unhinged_record(b, '#s', True, now=100.0)
    ai._unhinged_add_span(b, '#s', 150, 160); assert len(ai._unhinged_intervals(b, '#s')) == 1   # covered by open
    ai._unhinged_record(b, '#s', False, now=200.0)
    ai._unhinged_add_span(b, '#s', 205, 210); assert ai._unhinged_intervals(b, '#s')[-1] == [205.0, 210.0]
    ai._unhinged_add_span(b, 'nick', 1, 2); assert ai._unhinged_intervals(b, 'nick') == []
    assert ai._messages_unhinged([{'role': 'system', 'content': 'x\n\n' + ai._UNHINGED_PROMPT}])
    assert not ai._messages_unhinged([{'role': 'system', 'content': 'log: UNHINGED MODE: lol'}])
    assert not ai._messages_unhinged([{'role': 'user', 'content': ai._UNHINGED_PROMPT}])

def test_history_with_ts():
    b = newbot('hist')
    ai._db_add_turn(b, 'End3r', 'user', 'hi', '#c'); ai._db_add_turn(b, 'End3r', 'assistant', 'yo', '#c')
    rows = ai._db_get_recent(b, 'end3r', channel='#c', with_ts=True)
    assert [r[:2] for r in rows] == [('user', 'hi'), ('assistant', 'yo')] and isinstance(rows[0][2], str)
    assert ai._db_get_recent(b, 'end3r', channel='#c') == [('user', 'hi'), ('assistant', 'yo')]
    ts = ai._unhinged_ts_hidden([[time.time() - 60, time.time() + 60]], rows[1][2]); assert ts

def test_directive_context_filters_only_unhinged_bot_lines():
    b = newbot('dir')
    b.config = SimpleNamespace(ai_multi=SimpleNamespace(system_prompt='persona'))
    log = b.memory['grok_channel_log']['#c'] = deque(maxlen=300)
    log.extend([('glitchy', 'tame before', 100.0), ('snOwbLAzE', 'user during', 150.0), ('glitchy', 'VULGAR during', 150.0),
                ('glitchy', 'tame after', 250.0)])
    ai._unhinged_record(b, '#c', True, now=120.0); ai._unhinged_record(b, '#c', False, now=200.0)
    b.memory['grok_channel_settings_cache']['#c'] = {'unhinged': 0}
    ai._enqueue_channel_directive(b, SimpleNamespace(nick='End3r', sender='End3r'), '#c', 'say hi')
    blob = repr(ai.API_TASK_QUEUE.get_nowait()['messages'])
    assert 'tame before' in blob and 'tame after' in blob and 'user during' in blob and 'VULGAR during' not in blob, blob[:400]

def test_worker_inflight_off_transition_marks_reply():
    b = newbot('worker'); b.memory['grok_session'] = object(); b.memory['grok_chat_session'] = None
    b.memory['grok_api_failures'] = {}; b.memory['grok_stats'] = ai._fresh_ai_stats(time.time())
    b.config = SimpleNamespace(ai_multi=SimpleNamespace(chat_backend='ollama', search_backend='none', model='m',
                               ollama_url='http://x', ollama_model='m', system_prompt='p'))
    ai._db_set_channel_unhinged(b, '#w', True)
    msgs = [{'role': 'system', 'content': 'p'}, {'role': 'system', 'content': ai._UNHINGED_PROMPT},
            {'role': 'user', 'content': 'roast me'}]
    def fake_api(bot, messages, *a, **k):
        ai._db_set_channel_unhinged(b, '#w', False)   # op turns it off while the request is in flight
        time.sleep(0.01)
        return 'savage inflight line', []
    o1, o2, o3 = ai._call_chat_completions_api, ai.TYPING_DELAY_MIN, ai.TYPING_DELAY_MAX
    ai._call_chat_completions_api = fake_api; ai.TYPING_DELAY_MIN = ai.TYPING_DELAY_MAX = 0
    try:
        tr = SimpleNamespace(nick='End3r', sender='#w', group=lambda n=0: 'glitchy roast me', tags={}, account=None)
        ai._api_worker(bot=b, trigger=tr, messages=msgs, review_mode=False, is_pm=False, bot_nick='glitchy',
                       chan_lock=threading.Lock(), stop_event=threading.Event())
    finally:
        ai._call_chat_completions_api, ai.TYPING_DELAY_MIN, ai.TYPING_DELAY_MAX = o1, o2, o3
    assert any('savage inflight line' in t for _, t in b.said), b.said
    h = ai._unhinged_hidden_intervals(b, '#w')
    line = [e for e in b.memory['grok_channel_log']['#w'] if 'savage inflight' in e[1]][0]
    assert ai._unhinged_ts_hidden(h, line[2]), (h, line)
    rows = ai._db_get_recent(b, 'end3r', channel='#w', with_ts=True)
    asst = [r for r in rows if r[0] == 'assistant']; assert asst and ai._unhinged_ts_hidden(h, asst[-1][2]), (h, rows)
    # PM directive into a channel (is_pm=True + target_channel) is marked too
    ai._db_set_channel_unhinged(b, '#w2', True)
    ai._call_chat_completions_api = lambda bot, m, *a, **k: (ai._db_set_channel_unhinged(b, '#w2', False), ('directive savage', []))[1]
    ai.TYPING_DELAY_MIN = ai.TYPING_DELAY_MAX = 0
    try:
        tr = SimpleNamespace(nick='End3r', sender='End3r', group=lambda n=0: 'x', tags={}, account=None)
        ai._api_worker(bot=b, trigger=tr, messages=msgs, review_mode=False, is_pm=True, bot_nick='glitchy',
                       chan_lock=threading.Lock(), stop_event=threading.Event(), target_channel='#w2', admin_pm_nick='End3r')
    finally:
        ai._call_chat_completions_api, ai.TYPING_DELAY_MIN, ai.TYPING_DELAY_MAX = o1, o2, o3
    l2 = [e for e in b.memory['grok_channel_log']['#w2'] if 'directive savage' in e[1]][0]
    assert ai._unhinged_ts_hidden(ai._unhinged_hidden_intervals(b, '#w2'), l2[2])
    # a tame reply produced after off is not hidden
    assert not ai._unhinged_ts_hidden(h, time.time() + 1)


class Cfg:
    def __init__(self, **kw): self.__dict__.update(kw)
    def __getattr__(self, name):
        attr = ai.AiMultiSection.__dict__.get(name)
        d = getattr(attr, 'default', None)
        if d is None: raise AttributeError(name)
        return d

def _handle_messages(b, text, nick='End3r', chan='#h'):
    while not ai.API_TASK_QUEUE.empty(): ai.API_TASK_QUEUE.get_nowait()
    tr = SimpleNamespace(nick=nick, sender=chan, group=lambda n=0: text, groups=lambda: (text,), tags={},
                         account=None, hostmask=f'{nick}!u@h', host='h', user='u', ctcp=None, is_privmsg=False,
                         admin=False, owner=False, plain=text, args=[chan, text], event='PRIVMSG', raw='', match=None,
                         time=datetime.datetime.now(datetime.timezone.utc), urls=())
    ai.handle(b, tr, _intent_decided=True)
    tasks = []
    while not ai.API_TASK_QUEUE.empty(): tasks.append(ai.API_TASK_QUEUE.get_nowait())
    assert tasks, ('no task', b.said)
    return repr(tasks[-1].get('messages'))

def test_handle_history_and_log_filtering():
    b = newbot('handle')
    b.memory.update({'grok_session': object(), 'grok_chat_session': None, 'grok_api_disabled': False,
                     'grok_last': ai._BoundedTTLCache(), 'grok_dedup_cache': ai._BoundedTTLCache(),
                     'grok_user_last_cache': ai._BoundedTTLCache(), 'grok_api_failures': {}, 'grok_chimein_last': {},
                     'grok_channel_personality': {}, 'grok_user_personality': {}, 'grok_learn_counters': {},
                     'grok_stats': ai._fresh_ai_stats(time.time()), 'grok_last_prompt': {}, 'grok_fallback_openai': None,
                     'grok_admin_ignored': set()})
    b.config = SimpleNamespace(ai_multi=Cfg(system_prompt='persona'), core=SimpleNamespace(owner='x', admins=[], prefix=r'\$', help_prefix='$'))
    now = time.time()
    # two sessions, tame replies in between and after
    ai._unhinged_record(b, '#h', True, now=now - 600); ai._unhinged_record(b, '#h', False, now=now - 500)
    ai._unhinged_record(b, '#h', True, now=now - 300); ai._unhinged_record(b, '#h', False, now=now - 200)
    b.memory['grok_channel_settings_cache']['#h'] = {'unhinged': 0}
    log = b.memory['grok_channel_log']['#h'] = deque(maxlen=300)
    with sqlite3.connect(b.memory['grok_db_path']) as c:
        for off, role, text in [(700, 'user', 'U-early'), (690, 'assistant', 'TAME-early'), (550, 'user', 'U-s1'),
                                (549, 'assistant', 'VULGAR-s1'), (400, 'user', 'U-mid'), (399, 'assistant', 'TAME-mid'),
                                (250, 'user', 'U-s2'), (249, 'assistant', 'VULGAR-s2'), (100, 'user', 'U-late'), (99, 'assistant', 'TAME-late')]:
            c.execute('INSERT INTO grok_user_history (nick, source, role, text, ts) VALUES (?,?,?,?,?)', ('end3r', '#h', role, text, iso(now - off)))
            log.append(('End3r' if role == 'user' else 'glitchy', 'LOG-' + text, now - off))
    log.append(('glitchy', 'LOG-boundary-on', now - 300)); log.append(('glitchy', 'LOG-boundary-off', now - 200))
    log.append(('Other', 'LOG-other-during', now - 260))
    blob = _handle_messages(b, 'glitchy what is up')
    for keep in ('U-early', 'TAME-early', 'U-s1', 'U-mid', 'TAME-mid', 'U-s2', 'U-late', 'TAME-late',
                 'LOG-TAME-early', 'LOG-TAME-mid', 'LOG-TAME-late', 'LOG-U-s1', 'LOG-other-during'):
        assert keep in blob, ('missing', keep, blob[:3000])
    for drop in ('VULGAR-s1', 'VULGAR-s2', 'LOG-boundary-on', 'LOG-boundary-off'):
        assert drop not in blob, ('leaked', drop)
    assert 'Unhinged mode is now OFF' in blob
    # other channel unaffected by #h intervals
    b.memory['grok_channel_settings_cache']['#z'] = {'unhinged': 0}
    b.memory['grok_channel_log']['#z'] = deque([('glitchy', 'Z-own-line', now - 250)], maxlen=300)
    assert 'Z-own-line' in _handle_messages(b, 'glitchy hey', chan='#z')
    # on mode: unhinged prompt present, own lines kept (unchanged behavior)
    ai._db_set_channel_unhinged(b, '#h', True)
    blob = _handle_messages(b, 'glitchy roast me', nick='Raya')
    assert 'UNHINGED MODE:' in blob and 'LOG-VULGAR-s2' in blob and 'Unhinged mode is now OFF' not in blob

