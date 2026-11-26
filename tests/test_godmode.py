"""Tests for the shared _godmode library (no Sopel required)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _godmode as gm  # noqa: E402


class FakeDB:
    def __init__(self):
        self._store = {}

    def get_plugin_value(self, plugin, key):
        return self._store.get((plugin, key))

    def set_plugin_value(self, plugin, key, value):
        self._store[(plugin, key)] = value


def make_bot(homedir: str, db=None):
    return SimpleNamespace(
        memory={},
        db=db if db is not None else FakeDB(),
        config=SimpleNamespace(core=SimpleNamespace(homedir=homedir)),
    )


class GodmodeLibTests(unittest.TestCase):
    def setUp(self):
        gm.reset_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.homedir = self.td.name
        self.bot = make_bot(self.homedir)

    def tearDown(self):
        gm.reset_for_tests()
        self.td.cleanup()

    def test_normalize_key_folds_case_and_codes(self):
        self.assertEqual(gm.normalize_key('End3r'), 'end3r')
        self.assertEqual(gm.normalize_key('  Jay  '), 'jay')
        self.assertEqual(gm.normalize_key('\x02END3R\x0f'), 'end3r')
        self.assertEqual(gm.normalize_key(''), '')

    def test_set_and_has_via_bot_memory(self):
        gm.set_godmode(self.bot, 'End3r', True)
        self.assertTrue(gm.has_godmode(self.bot, 'end3r'))
        self.assertTrue(gm.has_godmode(self.bot, 'End3r'))
        self.assertIn('end3r', self.bot.memory[gm.MEMORY_KEY])
        self.assertEqual(gm.list_nicks(self.bot), ['end3r'])
        gm.set_godmode(self.bot, 'End3r', False)
        self.assertFalse(gm.has_godmode(self.bot, 'End3r'))
        self.assertEqual(gm.list_nicks(self.bot), [])

    def test_persists_to_db_and_json(self):
        gm.set_godmode(self.bot, 'JayIsProxi', True)
        stored = self.bot.db.get_plugin_value(gm.DB_PLUGIN, gm.DB_KEY)
        self.assertEqual(stored, ['jayisproxi'])
        path = os.path.join(self.homedir, gm.JSON_NAME)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['nicks'], ['jayisproxi'])

    def test_survives_reload_from_db(self):
        gm.set_godmode(self.bot, 'End3r', True)
        gm.set_godmode(self.bot, 'jayisproxi', True)
        gm.reset_for_tests()
        fresh = make_bot(self.homedir, db=self.bot.db)
        # empty memory, like after $rehash, before mug setup
        self.assertTrue(gm.has_godmode(fresh, 'end3r'))
        self.assertTrue(gm.has_godmode(fresh, 'JayIsProxi'))
        self.assertFalse(gm.has_godmode(fresh, 'stranger'))

    def test_survives_restart_from_json_when_db_empty(self):
        gm.set_godmode(self.bot, 'End3r', True)
        gm.reset_for_tests()
        fresh = make_bot(self.homedir, db=FakeDB())  # new process, empty db
        self.assertTrue(gm.has_godmode(fresh, 'End3r'))
        # backfills db
        self.assertEqual(fresh.db.get_plugin_value(gm.DB_PLUGIN, gm.DB_KEY), ['end3r'])

    def test_empty_db_wins_over_stale_json(self):
        # Simulate: enabled, persisted, then disabled (empty list in db)
        gm.set_godmode(self.bot, 'End3r', True)
        gm.set_godmode(self.bot, 'End3r', False)
        self.assertEqual(self.bot.db.get_plugin_value(gm.DB_PLUGIN, gm.DB_KEY), [])
        gm.reset_for_tests()
        fresh = make_bot(self.homedir, db=self.bot.db)
        self.assertFalse(gm.has_godmode(fresh, 'End3r'))

    def test_works_without_bot_after_load(self):
        gm.set_godmode(self.bot, 'End3r', True)
        self.assertTrue(gm.has_godmode(None, 'End3r'))

    def test_works_before_mug_loaded_via_json(self):
        # Write JSON as if mug had saved, then a later plugin checks
        path = os.path.join(self.homedir, gm.JSON_NAME)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'nicks': ['end3r']}, f)
        gm.reset_for_tests()
        bot = make_bot(self.homedir, db=FakeDB())
        self.assertTrue(gm.has_godmode(bot, 'End3r'))

    def test_multiple_nicks_and_disable_one(self):
        gm.set_godmode(self.bot, 'Alice', True)
        gm.set_godmode(self.bot, 'Bob', True)
        self.assertEqual(gm.list_nicks(self.bot), ['alice', 'bob'])
        gm.set_godmode(self.bot, 'Alice', False)
        self.assertEqual(gm.list_nicks(self.bot), ['bob'])
        self.assertFalse(gm.has_godmode(self.bot, 'Alice'))
        self.assertTrue(gm.has_godmode(self.bot, 'Bob'))

    def test_blank_nick_is_false(self):
        self.assertFalse(gm.has_godmode(self.bot, ''))
        self.assertFalse(gm.has_godmode(self.bot, None))  # type: ignore[arg-type]


class HuntLootHelperTests(unittest.TestCase):
    def test_best_loot_picks_highest_value(self):
        table = [('Rabbit Foot', 25), ('Rabbit Pelt', 20), ('Golden Foot', 400)]
        self.assertEqual(max(table, key=lambda it: it[1]), ('Golden Foot', 400))


if __name__ == '__main__':
    unittest.main()
