"""Offline karma milestone tests. No IRC and no network.

Uses the ibot sopel shim (set IBOT_ROOT if ibot is not a sibling checkout)
and a temporary SQLite bot db.
"""

import importlib.util
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _install_shim():
    if 'sopel' in sys.modules and hasattr(sys.modules['sopel'], 'plugin'):
        return
    ibot_root = Path(os.environ['IBOT_ROOT']) if os.environ.get('IBOT_ROOT') else ROOT.parent / 'ibot'
    sys.path.insert(0, str(ibot_root))
    from ibot.loader import install_sopel_shim
    install_sopel_shim()


def _load(name='karma_plugin'):
    _install_shim()
    spec = importlib.util.spec_from_file_location(name, ROOT / 'karma.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def karma():
    return _load()


@pytest.fixture
def db(tmp_path):
    _install_shim()
    from sopel.db import SopelDB
    database = SopelDB(db_filename=str(tmp_path / 'karma.db'))
    yield database
    database.engine.dispose()


class _Bot:
    def __init__(self, database):
        self.db = database
        self.nick = 'glitchy'
        self.memory = {}
        self.channels = {}
        self.said = []

    def say(self, text, dest=None):
        self.said.append((text, dest))

    def reply(self, text):
        self.said.append(('reply:' + text, None))


class _Trig:
    def __init__(self, nick, text, sender='#chan', args=None, priv=False):
        self.nick = nick
        self.user = 'u'
        self.host = 'h.example'
        self.sender = sender
        self.is_privmsg = priv
        self._text = text
        self._args = args

    def group(self, index=0):
        if index == 2:
            return self._args
        return self._text


def _line(karma, direction, level, score, target='n', kind='new'):
    return karma._format_karma_milestone(target, direction, level, score, kind)


# --- pure transition -------------------------------------------------------

@pytest.mark.parametrize('level', [1, 10, 50, 100, 500, 1000, 2000, 5000, 10000])
def test_positive_threshold_crossing(karma, level):
    event, pos, neg = karma._karma_milestone_transition(level - 1, level, 0, 0)
    assert event == ('positive', level, 'new')
    assert pos == level
    assert neg == 0
    assert 'worshipped by malfunctioning satellites' in karma.POSITIVE_KARMA_MILESTONES[1000]
    assert 'worshipped by malfunctioning satellites' in karma.POSITIVE_KARMA_MILESTONES[10000]


@pytest.mark.parametrize('level', [1, 10, 50, 100, 500, 1000, 2000, 5000, 10000])
def test_negative_threshold_crossing(karma, level):
    event, pos, neg = karma._karma_milestone_transition(-(level - 1), -level, 0, 0)
    assert event == ('negative', level, 'new')
    assert neg == level
    assert pos == 0
    assert 'legally classified as a municipal sewage incident' in karma.NEGATIVE_KARMA_MILESTONES[1000]
    assert 'legally classified as a municipal sewage incident' in karma.NEGATIVE_KARMA_MILESTONES[10000]


def test_recovery_does_not_celebrate_further_decline(karma):
    # Still negative, but the deep rung was already said: climbing is quiet.
    assert karma._karma_milestone_transition(-1000, -1, 0, 1000)[0] is None
    assert karma._karma_milestone_transition(-1200, -1199, 0, 1000)[0] is None
    # Dropping off a positive rung that was already said does not re-celebrate.
    assert karma._karma_milestone_transition(1000, 1, 1000, 0)[0] is None
    assert karma._karma_milestone_transition(999, 1000, 1000, 0)[0] is None
    assert karma._karma_milestone_transition(-999, -1000, 0, 1000)[0] is None
    # Leaving the negatives for zero, or a no-op, says nothing.
    assert karma._karma_milestone_transition(-5, 0, 0, 1)[0] is None
    assert karma._karma_milestone_transition(1200, 1200, 0, 0)[0] is None


def test_catchup_recognizes_the_highest_existing_rung(karma):
    event, pos, neg = karma._karma_milestone_transition(1200, 1201, 0, 0)
    assert event == ('positive', 1000, 'recognition')
    assert pos == 1000 and neg == 0
    event, pos, neg = karma._karma_milestone_transition(-1200, -1201, 0, 0)
    assert event == ('negative', 1000, 'recognition')
    assert neg == 1000 and pos == 0
    # A plus while still negative recognizes the hole they are already in.
    event, pos, neg = karma._karma_milestone_transition(-1200, -1199, 0, 0)
    assert event == ('negative', 1000, 'recognition')
    assert pos == 0 and neg == 1000
    # -261's current rung is -100, not a wait for -500.
    event, _pos, neg = karma._karma_milestone_transition(-261, -260, 0, 0)
    assert event == ('negative', 100, 'recognition')
    assert neg == 100
    # Crossing a higher rung swallows the unannounced lower one.
    event, pos, _neg = karma._karma_milestone_transition(999, 2000, 0, 0)
    assert event == ('positive', 2000, 'new')
    assert pos == 2000


def test_jump_announces_only_highest(karma):
    event, pos, neg = karma._karma_milestone_transition(0, 2500, 0, 0)
    assert event == ('positive', 2000, 'new')
    assert pos == 2000 and neg == 0
    event, pos, neg = karma._karma_milestone_transition(0, -2500, 0, 0)
    assert event == ('negative', 2000, 'new')
    assert neg == 2000 and pos == 0
    event, pos, neg = karma._karma_milestone_transition(400, 1500, 0, 0)
    assert event == ('positive', 1000, 'new')
    assert pos == 1000 and neg == 0


def test_zero_crossing_uses_the_destination_direction(karma):
    event, pos, neg = karma._karma_milestone_transition(-5, 20, 0, 0)
    assert event == ('positive', 10, 'new')
    assert pos == 10
    assert neg == 0  # the old -1 rung was not said, and this score is not negative
    event, pos, neg = karma._karma_milestone_transition(5, -20, 0, 0)
    assert event == ('negative', 10, 'new')
    assert neg == 10
    assert pos == 0
    assert karma._karma_milestone_transition(5, 0, 0, 0)[0] is None
    assert karma._karma_milestone_transition(-5, 0, 0, 0)[0] is None


# --- db-backed behavior ----------------------------------------------------

@pytest.mark.parametrize('level', [1, 10, 50, 100, 500, 1000, 2000, 5000, 10000])
def test_db_positive_and_negative_steps(karma, db, level):
    nick = f'p{level}'
    karma.set_karma(db, nick, level - 1)
    _new, _chan, line = karma._apply_karma_delta(db, nick, '#c', 1)
    assert line == _line(karma, 'positive', level, level, nick)
    assert karma.get_karma(db, nick) == level

    nick = f'n{level}'
    karma.set_karma(db, nick, -(level - 1))
    _new, _chan, line = karma._apply_karma_delta(db, nick, '#c', -1)
    assert line == _line(karma, 'negative', level, -level, nick)
    assert karma.get_karma(db, nick) == -level


def test_repeated_boundary_and_reload(karma, db):
    _new, _chan, line = karma._apply_karma_delta(db, 'Hero', '#c', 1)
    assert line == _line(karma, 'positive', 1, 1, 'Hero')
    _new, _chan, line = karma._apply_karma_delta(db, 'hero', '#c', -1)
    assert line is None
    assert karma.get_karma(db, 'HERO') == 0

    # A second import is a reload: marks live in the db, not process memory.
    other = _load('karma_plugin_reload')
    _new, _chan, line = other._apply_karma_delta(db, 'hero', '#c', 1)
    assert line is None
    assert other.get_karma(db, 'Hero') == 1

    for _ in range(4):
        assert karma._apply_karma_delta(db, 'hero', '#c', -1)[2] is None
        assert karma._apply_karma_delta(db, 'hero', '#c', 1)[2] is None
    assert karma.get_karma(db, 'hero') == 1


def test_casefold_and_channel_do_not_double_announce(karma, db):
    karma.set_karma(db, 'Weird[]', 49)
    karma.add_channel_karma(db, 'Weird[]', '#loud', 10000)
    _new, chan, line = karma._apply_karma_delta(db, 'weird{}', '#quiet', 1)
    assert line == _line(karma, 'positive', 50, 50, 'weird{}')
    assert karma.get_karma(db, 'WEIRD[]') == 50
    assert chan == 1
    assert karma.get_channel_karma(db, 'weird{}', '#loud') == 10000
    # Same global nick, other channel, no new rung.
    _new, chan, line = karma._apply_karma_delta(db, 'Weird{}', '#loud', 1)
    assert line is None
    assert karma.get_karma(db, 'weird[]') == 51
    assert chan == 10001
    assert karma.get_channel_karma(db, 'weird[]', '#quiet') == 1


def test_existing_high_and_low_scores_catch_up_once(karma, db):
    karma.set_karma(db, 'weasel', 1200)
    karma.set_karma(db, 'TorS', -1200)
    _new, _chan, line = karma._apply_karma_delta(db, 'Weasel', '#c', 1)
    assert karma.get_karma(db, 'weasel') == 1201
    assert line == _line(karma, 'positive', 1000, 1201, 'Weasel', 'recognition')
    assert 'already there' in line
    assert 'You are worshipped by malfunctioning satellites.' in line
    assert 'Karma milestone' not in line

    _new, _chan, line = karma._apply_karma_delta(db, 'tors', '#chan', -1)
    assert karma.get_karma(db, 'TorS') == -1201
    assert line == _line(karma, 'negative', 1000, -1201, 'tors', 'recognition')
    assert 'legally classified as a municipal sewage incident.' in line
    assert 'already there' in line

    # Said once. The next award does not repeat it, and +2000 is still ahead.
    _new, _chan, line = karma._apply_karma_delta(db, 'weasel', '#c', 1)
    assert line is None
    assert karma.get_karma(db, 'weasel') == 1202
    _new, line = karma._assign_global_karma(db, 'weasel', 2000)
    assert line == _line(karma, 'positive', 2000, 2000, 'weasel')
    assert 'Orbital choir' in line

    # Recovering toward zero does not dig a deeper negative hole.
    karma.set_karma(db, 'sinking', -1200)
    _new, _chan, line = karma._apply_karma_delta(db, 'sinking', '#chan', 1)
    assert karma.get_karma(db, 'sinking') == -1199
    assert line == _line(karma, 'negative', 1000, -1199, 'sinking', 'recognition')
    assert 'Karma milestone' not in line
    _new, _chan, line = karma._apply_karma_delta(db, 'sinking', '#chan', 1)
    assert line is None
    assert karma.get_channel_karma(db, 'TorS', '#chan') == -1


def test_setkarma_jump_zero_cross_and_no_channel_write(karma, db):
    karma.add_channel_karma(db, 'hero', '#c', 7)
    new, line = karma._assign_global_karma(db, 'hero', 10000)
    assert new == 10000
    assert line == _line(karma, 'positive', 10000, 10000, 'hero')
    assert karma.get_channel_karma(db, 'hero', '#c') == 7
    # Dropping back through positives is not a negative achievement.
    _new, line = karma._assign_global_karma(db, 'hero', 50)
    assert line is None
    # One downward jump across zero announces only the deepest new negative rung.
    _new, line = karma._assign_global_karma(db, 'hero', -10000)
    assert line == _line(karma, 'negative', 10000, -10000, 'hero')
    _new, line = karma._assign_global_karma(db, 'hero', -5)
    assert line is None
    _new, line = karma._assign_global_karma(db, 'hero', 20)
    # Positive watermark was already 10000, so recovery does not re-celebrate.
    assert line is None
    assert karma.get_channel_karma(db, 'hero', '#c') == 7


def test_jump_message_names_the_rung_and_the_live_score(karma, db):
    _new, line = karma._assign_global_karma(db, 'jumper', 1500)
    assert line == _line(karma, 'positive', 1000, 1500, 'jumper')
    assert '(now +1,500)' in line
    assert 'Shopping carts' not in line


def test_startup_is_silent_and_the_next_change_catches_up(karma, db):
    karma.set_karma(db, 'oldtimer', 8000)
    karma.set_karma(db, 'menace', -4000)
    bot = _Bot(db)
    karma.setup(bot)
    assert bot.said == []
    assert db.get_nick_value('oldtimer', karma.KARMA_MILESTONE_KEY) is None
    assert db.get_nick_value('menace', karma.KARMA_MILESTONE_KEY) is None
    _new, _chan, line = karma._apply_karma_delta(db, 'oldtimer', '#c', 1)
    assert line == _line(karma, 'positive', 5000, 8001, 'oldtimer', 'recognition')
    assert karma.get_karma(db, 'oldtimer') == 8001
    assert db.get_nick_value('menace', karma.KARMA_MILESTONE_KEY) is None


def test_flood_rollback_does_not_repeat_the_rung(karma, db):
    _new, _chan, line = karma._apply_karma_delta(db, 'hero', '#c', 1)
    assert line is not None
    events = [{'applied': True, 'direction': '++'}]
    rolled, restored, restored_chan = karma._rollback_applied_karma(db, 'hero', '#c', events)
    assert rolled == 1
    assert restored == 0
    assert restored_chan == 0
    assert events[0]['applied'] is False
    _new, _chan, line = karma._apply_karma_delta(db, 'hero', '#c', 1)
    assert line is None
    assert karma.get_karma(db, 'hero') == 1


def test_concurrent_votes_apply_once_per_rung(karma, db):
    lines = []
    guard = threading.Lock()

    def worker():
        _new, _chan, line = karma._apply_karma_delta(db, 'pile', '#c', 1)
        if line:
            with guard:
                lines.append(line)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert karma.get_karma(db, 'pile') == 20
    assert karma.get_channel_karma(db, 'pile', '#c') == 20
    # +1 and +10 are the only rungs inside 0..20, each at most once.
    assert len(lines) == 2
    assert {_line(karma, 'positive', 1, 1, 'pile'), _line(karma, 'positive', 10, 10, 'pile')} == set(lines)


def test_handler_keeps_boost_line_and_adds_milestone(karma, db):
    bot = _Bot(db)
    karma.setup(bot)
    karma.karma_increment_decrement(bot, _Trig('giver', 'hero++'))
    assert len(bot.said) == 2
    boost, _dest = bot.said[0]
    assert boost == (
        '🆙 Karma boost! hero gained 1 karma ✨ '
        '(🎯 1 in #chan | 🌐 1 global)'
    )
    assert bot.said[1][0] == _line(karma, 'positive', 1, 1, 'hero')

    bot.said.clear()
    karma.karma_increment_decrement(bot, _Trig('other', 'hero--'))
    assert len(bot.said) == 1
    assert bot.said[0][0] == (
        '⬇ Karma drop... hero lost 1 karma 😬 '
        '(🎯 0 in #chan | 🌐 0 global)'
    )
    # Back across +1 does not celebrate again. Use a fresh giver (cooldown).
    bot.said.clear()
    karma.karma_increment_decrement(bot, _Trig('third', 'hero++'))
    assert len(bot.said) == 1
    assert bot.said[0][0].startswith('🆙 Karma boost!')
    assert karma.get_karma(db, 'hero') == 1


def test_setkarma_command_announces_and_help_mentions_it(karma, db):
    bot = _Bot(db)
    karma.add_channel_karma(db, 'hero', '#chan', 4)
    karma.setkarma(bot, _Trig('op', '', sender='#chan', args='hero 1000'))
    assert bot.said[0][0] == "🛠️ hero's karma has been set to 1000."
    assert bot.said[1][0] == _line(karma, 'positive', 1000, 1000, 'hero')
    assert karma.get_channel_karma(db, 'hero', '#chan') == 4

    bot.said.clear()
    karma.cmd_karma(bot, _Trig('op', '', args=None))
    help_text = '\n'.join(text for text, _dest in bot.said)
    assert 'Global milestones' in help_text


def test_v1_seed_is_not_an_announcement_and_v2_is(karma, db):
    karma.set_karma(db, 'seeded', 1200)
    db.set_nick_value('seeded', karma.KARMA_MILESTONE_KEY, {'pos': 1000, 'neg': 0})
    _new, _chan, line = karma._apply_karma_delta(db, 'seeded', '#c', 1)
    assert karma.get_karma(db, 'seeded') == 1201
    assert line == _line(karma, 'positive', 1000, 1201, 'seeded', 'recognition')
    stored = db.get_nick_value('Seeded', karma.KARMA_MILESTONE_KEY)
    assert stored == {'v': 2, 'ann_pos': 1000, 'ann_neg': 0}

    other = _load('karma_plugin_v2_reload')
    _new, _chan, line = other._apply_karma_delta(db, 'seeded', '#c', -1)
    assert line is None
    assert other.get_karma(db, 'seeded') == 1200

    karma.set_karma(db, 'told', 1200)
    db.set_nick_value('told', karma.KARMA_MILESTONE_KEY, {'v': 2, 'ann_pos': 1000, 'ann_neg': 0})
    _new, _chan, line = karma._apply_karma_delta(db, 'told', '#c', 1)
    assert line is None
    assert karma.get_karma(db, 'told') == 1201


def test_noop_and_zero_do_not_announce(karma, db):
    karma.set_karma(db, 'same', 1200)
    new, line = karma._assign_global_karma(db, 'same', 1200)
    assert new == 1200 and line is None
    assert db.get_nick_value('same', karma.KARMA_MILESTONE_KEY) is None
    new, line = karma._assign_global_karma(db, 'same', 0)
    assert new == 0 and line is None
    assert karma.get_karma(db, 'same') == 0
    _new, _chan, line = karma._apply_karma_delta(db, 'same', '#c', 1)
    assert line == _line(karma, 'positive', 1, 1, 'same')


def test_large_jump_is_highest_new_rung_only(karma, db):
    karma.set_karma(db, 'jumper', 400)
    new, line = karma._assign_global_karma(db, 'jumper', 2500)
    assert new == 2500
    assert line == _line(karma, 'positive', 2000, 2500, 'jumper')
    assert 'Shopping carts' not in line
    assert 'Orbital choir' in line
    stored = db.get_nick_value('jumper', karma.KARMA_MILESTONE_KEY)
    assert stored['ann_pos'] == 2000
    new, line = karma._assign_global_karma(db, 'jumper', 2500)
    assert new == 2500 and line is None


def test_private_and_self_karma_do_not_score(karma, db):
    bot = _Bot(db)
    karma.karma_increment_decrement(bot, _Trig('hero', 'hero++', priv=True))
    assert karma.get_karma(db, 'hero') == 0
    assert bot.said and bot.said[0][0].startswith('reply:')
    bot.said.clear()
    karma.karma_increment_decrement(bot, _Trig('hero', 'hero++'))
    assert karma.get_karma(db, 'hero') == 0
    assert bot.said == []
