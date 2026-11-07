"""Local clock, place conversion, and address rules for ai-multi.

Times are frozen. No IRC, no network, no model calls.
"""

import datetime
import logging
from types import SimpleNamespace

import pytest

UTC = datetime.timezone.utc
NOV7_2246 = datetime.datetime(2026, 11, 7, 22, 46, 28, tzinfo=UTC)
NOV7_2248 = datetime.datetime(2026, 11, 7, 22, 48, 39, tzinfo=UTC)
NOV7_2251 = datetime.datetime(2026, 11, 7, 22, 51, 24, tzinfo=UTC)
JULY7_2246 = datetime.datetime(2026, 7, 7, 22, 46, 0, tzinfo=UTC)


class _Bot:
    logger = logging.getLogger('time-query-test')

    def __init__(self):
        self.memory = {}
        self.said = []
        self.notices = []

    def say(self, text, dest=None):
        self.said.append((text, dest))

    def notice(self, text, dest=None):
        self.notices.append((text, dest))


def _trig(nick='End3r', sender='#Cyberspace'):
    return SimpleNamespace(nick=nick, sender=sender, is_relayed=False)


def _prefs(monkeypatch, ai, iana='America/Chicago', label='CDT', fmt='24'):
    monkeypatch.setattr(
        ai, '_db_get_user_pref',
        lambda bot, nick: {'tz_iana': iana, 'tz_label': label, 'time_fmt': fmt},
    )


def test_nov7_home_zone_uses_cst_not_saved_cdt_label(ai, monkeypatch):
    _prefs(monkeypatch, ai, fmt='12')
    bot = _Bot()
    assert ai._answer_local_clock(bot, _trig(), 'what time is it?', now_utc=NOV7_2246)
    text = bot.said[0][0]
    assert text == 'Saturday, November 07, 2026 at 04:46 PM CST'
    assert 'CDT' not in text


def test_frozen_instants_for_utc_japan_and_sydney(ai, monkeypatch):
    _prefs(monkeypatch, ai)
    cases = [
        (NOV7_2246, 'what about in UTC?', 'Saturday, November 07, 2026 at 22:46 UTC'),
        (NOV7_2246, 'and in Japan?', 'Sunday, November 08, 2026 at 07:46 JST'),
        (NOV7_2248, 'in Japan, what is the time and date?', 'Sunday, November 08, 2026 at 07:48 JST'),
        (NOV7_2246, 'what time is it in Sydney Australia?', 'Sunday, November 08, 2026 at 09:46 AEDT'),
        (NOV7_2251, 'what time is it in Sydney, Australia?', 'Sunday, November 08, 2026 at 09:51 AEDT'),
    ]
    for moment, message, expected in cases:
        bot = _Bot()
        assert ai._answer_local_clock(bot, _trig(), message, now_utc=moment) is True
        assert bot.said[0][0] == expected


def test_dst_and_date_rollover(ai, monkeypatch):
    _prefs(monkeypatch, ai, fmt='24')
    bot = _Bot()
    assert ai._answer_local_clock(bot, _trig(), 'what time is it?', now_utc=JULY7_2246)
    # 7 Jul 2026 is CDT (UTC-5). The saved label is irrelevant.
    assert bot.said[0][0] == 'Tuesday, July 07, 2026 at 17:46 CDT'

    winter_sydney = ai._format_clock_in_zone(NOV7_2246, 'Australia/Sydney', False)
    summer_sydney = ai._format_clock_in_zone(JULY7_2246, 'Australia/Sydney', False)
    assert 'AEDT' in winter_sydney and 'November 08' in winter_sydney
    assert 'AEST' in summer_sydney

    # 04:30 UTC 8 Nov is still 7 Nov in Chicago and already 8 Nov in Tokyo.
    split = datetime.datetime(2026, 11, 8, 4, 30, tzinfo=UTC)
    chicago = ai._format_clock_in_zone(split, 'America/Chicago', False)
    tokyo = ai._format_clock_in_zone(split, 'Asia/Tokyo', False)
    assert 'November 07' in chicago and '22:30 CST' in chicago
    assert 'November 08' in tokyo and '13:30 JST' in tokyo


def test_followups_are_clocks_only_when_the_place_is_known(ai):
    assert ai._clock_query('what about in UTC?') == ('zone', 'UTC')
    assert ai._clock_query('and in Japan?') == ('zone', 'Asia/Tokyo')
    assert ai._clock_query('what about in the movie?') is None
    assert ai._clock_query('what about lunch?') is None
    assert ai._clock_query('what about in general?') is None
    assert ai._clock_query('what time was the war') is None
    assert ai._clock_query('what time is sunrise in tokyo') is None


def test_unknown_and_ambiguous_places_do_not_use_home_zone(ai, monkeypatch):
    _prefs(monkeypatch, ai, fmt='12')
    bot = _Bot()
    assert ai._answer_local_clock(
        bot, _trig(), 'what time is it in Narnia?', now_utc=NOV7_2246,
    )
    assert 'CST' not in bot.said[0][0]
    assert 'CDT' not in bot.said[0][0]
    assert "don't know the timezone" in bot.said[0][0]

    bot = _Bot()
    assert ai._answer_local_clock(bot, _trig(), 'what time is it in CST?', now_utc=NOV7_2246)
    assert 'ambiguous' in bot.said[0][0].lower()
    assert '04:46' not in bot.said[0][0]


def test_punctuation_does_not_change_the_zone(ai):
    a = ai._lookup_place('Sydney Australia')
    b = ai._lookup_place('Sydney, Australia')
    c = ai._lookup_place('  sydney,   australia? ')
    assert a == b == c == ('zone', 'Australia/Sydney')
    assert ai._lookup_place('Australia/Sydney') == ('zone', 'Australia/Sydney')


def test_address_direct_lines_and_mere_mentions(ai):
    assert ai._address_verdict('glitchy what time is it?', 'glitchy') == 'yes'
    assert ai._address_verdict('glitchy what about in UTC?', 'glitchy') == 'yes'
    assert ai._address_verdict('glitchy and in Japan?', 'glitchy') == 'yes'
    assert ai._address_verdict('glitchy in Japan, what is the time and date?', 'glitchy') == 'yes'
    assert ai._address_verdict('glitchy what time is it in Sydney Australia?', 'glitchy') == 'yes'
    assert ai._address_verdict('glitchy what time is it in Sydney, Australia?', 'glitchy') == 'yes'
    assert ai._address_verdict('Welcome back glitchy!', 'glitchy') == 'yes'
    assert ai._address_verdict('thanks glitchy', 'glitchy') == 'yes'

    assert ai._address_verdict('so and so is glitchy', 'glitchy') == 'no'
    assert ai._address_verdict('the bot glitchy is acting up', 'glitchy') == 'no'
    assert ai._address_verdict('glitchy and alice were both in here earlier', 'glitchy') == 'no'
    assert ai._address_verdict('alice and glitchy were talking', 'glitchy') == 'no'
    assert ai._address_verdict('is glitchy down again or what', 'glitchy') == 'no'


def test_relay_inner_speaker_place_is_a_clock(ai):
    parsed = ai._parse_relay_line(
        '<End3r/Rizon> glitchy what time is it in Japan?',
        'IglooIRC',
        ['IglooIRC'],
    )
    assert parsed[0] == 'End3r'
    body = parsed[2].split(' ', 1)[1]
    assert ai._clock_query(body) == ('zone', 'Asia/Tokyo')
    key = ai._speaker_key(SimpleNamespace(
        nick='End3r', is_relayed=True, relay_network='Rizon',
    ))
    assert key == 'Rizon:end3r'


def test_direct_drop_notice_is_rate_limited_and_skips_chimeins(ai):
    bot = _Bot()
    trig = _trig()
    ai._direct_drop_notice(bot, trig, 'channel busy', 'busy now')
    ai._direct_drop_notice(bot, trig, 'channel busy', 'busy now')
    assert bot.notices == [('busy now', 'End3r')]
    ai._direct_drop_notice(bot, trig, 'empty reply', 'empty', is_chimein=True)
    assert len(bot.notices) == 1
