"""ASCII-art allowance for antispam. #8chan allows figlet bursts."""

import importlib.util
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    if not ('sopel' in sys.modules and hasattr(sys.modules['sopel'], 'plugin')):
        ibot_root = Path(os.environ['IBOT_ROOT']) if os.environ.get('IBOT_ROOT') else ROOT.parent / 'ibot'
        sys.path.insert(0, str(ibot_root))
        from ibot.loader import install_sopel_shim
        install_sopel_shim()
    spec = importlib.util.spec_from_file_location('antispam_plugin', ROOT / 'antispam.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['antispam_plugin'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def spam():
    return _load()


# The banner reap pasted in #8chan, up to the line that tripped 10 msgs / 10s.
REAP_BURST = [
    '                       .',
    '                       M',
    '                      dM',
    '                      MMr',
    '                     4MMML                  .',
    '                     MMMMM.                xf',
    '     .              "MMMMM               .MM-',
    '      Mh..          +MMMMMM            .MMMM',
    '      .MMM.         .MMMMML.          MMMMMh',
    '       )MMMh.        MMMMMM         MMMMMMM',
]

REAP_WITH_PREFIX = [
    '00:48 reap        )MMMh.        MMMMMM         MMMMMMM',
    '00:48 reap         3MMMMx.     \'MMMMMMf      xnMMMMMM"',
    '00:48 reap            *MMMMMh   "MMMMM"   JMMMMM',
]


class _DB:
    def __init__(self, values=None):
        self.values = values or {}

    def get_plugin_value(self, plugin, key):
        return self.values.get((plugin, key))


class _Bot:
    def __init__(self, values=None):
        self.db = _DB(values)
        self.memory = {
            'spam_lock': threading.Lock(),
            'spam_texts': {},
        }


def test_reap_banner_lines_are_art(spam):
    for line in REAP_BURST:
        if len(line.strip()) <= 6:
            assert not spam._is_figlet_line(line)
        else:
            assert spam._is_figlet_line(line), line
    for line in REAP_WITH_PREFIX:
        assert spam._is_figlet_line(line), line


def test_normal_chat_is_not_art(spam):
    assert not spam._is_figlet_line(
        'buddha said the world is not real and there cannot be enlightenment without suffering'
    )
    assert not spam._is_figlet_line('mikk0: 4you')
    assert not spam._is_figlet_line('hahahahaha')
    assert not spam._is_figlet_line('lolololol')
    assert not spam._is_figlet_line('WWWWWWWW this is fine')
    assert not spam._is_figlet_line('hello there friend')


def test_reap_burst_is_allowed_and_chat_is_not(spam):
    assert spam._burst_is_ascii(REAP_BURST)
    assert spam._burst_is_ascii(['MMMh', 'mikk0: 4you'] + REAP_WITH_PREFIX + REAP_BURST[:3])
    assert not spam._burst_is_ascii(['hello there'] * 10)
    assert not spam._burst_is_ascii(['lol'] * 10)
    assert not spam._burst_is_ascii(['hahahahaha'] * 10)
    assert not spam._burst_is_ascii([])


def test_ascii_defaults_to_8chan_only(spam):
    bot = _Bot()
    assert spam._get_settings(bot, '#8chan')['ascii'] is True
    assert spam._get_settings(bot, '#8CHAN')['ascii'] is True
    assert spam._get_settings(bot, '#tech')['ascii'] is False

    saved = _Bot({('antispam', 'settings_#8chan'): {'ascii': False, 'window': 10}})
    assert spam._get_settings(saved, '#8chan')['ascii'] is False
    assert spam._get_settings(saved, '#8chan')['window'] == 10


def test_rate_window_ignores_older_chat(spam):
    bot = _Bot()
    key = ('#8chan', 'reap@host')
    old = [(100 + i, 'hey what is going on in here today') for i in range(12)]
    fresh = [(200 + i, line) for i, line in enumerate(REAP_BURST)]
    bot.memory['spam_texts'][key] = old + fresh

    full = spam._get_tracked_texts(bot, '#8chan', 'reap@host')
    window = spam._get_tracked_texts(bot, '#8chan', 'reap@host', since=190)
    assert len(full) == 22
    assert window == REAP_BURST
    assert not spam._burst_is_ascii(full)
    assert spam._burst_is_ascii(window)
