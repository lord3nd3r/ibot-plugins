"""Load ai-multi.py for tests.

The plugin file is not an importable module name, and it imports sopel.
This repo runs on iBot, whose loader installs a sopel shim. Point IBOT_ROOT
at an iBot checkout when it is not the sibling of this repo.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_IBOT = ROOT.parent / 'ibot'


def _install_shim():
    if 'sopel' in sys.modules and hasattr(sys.modules['sopel'], 'plugin'):
        return
    ibot_root = Path(os.environ['IBOT_ROOT']) if os.environ.get('IBOT_ROOT') else _DEFAULT_IBOT
    loader = ibot_root / 'ibot' / 'loader.py'
    if not loader.is_file():
        raise RuntimeError(
            f'iBot checkout not found at {ibot_root}. '
            'Set IBOT_ROOT to the iBot repo so tests can import the sopel shim.'
        )
    sys.path.insert(0, str(ibot_root))
    from ibot.loader import install_sopel_shim
    install_sopel_shim()


def load_ai_multi():
    _install_shim()
    spec = importlib.util.spec_from_file_location('ai_multi_plugin', ROOT / 'ai-multi.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['ai_multi_plugin'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def ai():
    return load_ai_multi()
