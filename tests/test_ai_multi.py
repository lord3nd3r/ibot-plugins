"""Tests for the pure helpers and intent decisions in ai-multi.py."""

import logging


class _Trigger:
    def __init__(self, nick='alice'):
        self.nick = nick


class _Bot:
    logger = logging.getLogger('ai-multi-test')


def _addressed(ai, line, nick='glitchy'):
    return ai._heuristic_intent_check(None, None, line, nick)


# --- duration ---------------------------------------------------------------

def test_parse_duration_units(ai):
    assert ai._parse_duration('45s') == 45
    assert ai._parse_duration('10m') == 600
    assert ai._parse_duration('2.5h') == 9000
    assert ai._parse_duration('24h') == 86400
    assert ai._parse_duration('2d') == 172800
    assert ai._parse_duration('1h30m') == 5400
    assert ai._parse_duration('1d2h30m45s') == 95445
    assert ai._parse_duration('  10m  ') == 600


def test_parse_duration_rejects_junk(ai):
    assert ai._parse_duration('') is None
    assert ai._parse_duration(None) is None
    assert ai._parse_duration('abc') is None
    assert ai._parse_duration('10') is None
    assert ai._parse_duration('1h 30m') is None
    assert ai._parse_duration('0s') is None
    assert ai._parse_duration('0h') is None


def test_format_duration_short(ai):
    assert ai._format_duration_short(0) == '0s'
    assert ai._format_duration_short(-5) == '0s'
    assert ai._format_duration_short(45) == '45s'
    assert ai._format_duration_short(90) == '1m 30s'
    assert ai._format_duration_short(5400) == '1h 30m'
    assert ai._format_duration_short(3661) == '1h 1m'
    assert ai._format_duration_short(86400) == '1d'
    assert ai._format_duration_short(90000) == '1d 1h'


# --- sanitize_reply ---------------------------------------------------------

def test_sanitize_strips_function_call_xml(ai):
    only = '<function_call name="web_search">{"q":"x"}</function_call>'
    assert ai.sanitize_reply(_Bot(), _Trigger(), only) == ''

    mixed = 'hello <function_call name="web_search">{"q":"x"}</function_call> there'
    assert ai.sanitize_reply(_Bot(), _Trigger(), mixed) == 'hello  there'


def test_sanitize_strips_code_fences_and_pings(ai):
    fenced = 'see ```print(1)``` please'
    assert ai.sanitize_reply(_Bot(), _Trigger(), fenced) == 'see  (code removed)  please'
    assert ai.sanitize_reply(_Bot(), _Trigger(), 'hey @everyone and @here') == 'hey (nope) and (nope)'


def test_sanitize_strips_block_drawing_runs(ai):
    blocks = 'edge ' + ('\u2588' * 6) + ' edge'
    assert ai.sanitize_reply(_Bot(), _Trigger(), blocks) == 'edge   edge'


def test_sanitize_suppresses_box_drawing_art(ai):
    art = '╔══╗\n║  ║\n╠══╣\n╚══╝\n'
    out = ai.sanitize_reply(_Bot(), _Trigger(), art)
    assert out == "I was gonna draw something cool… but I won't flood the channel"


def test_sanitize_truncates_long_replies(ai):
    reply = 'a' * (ai.MAX_REPLY_LENGTH + 50)
    out = ai.sanitize_reply(_Bot(), _Trigger(), reply)
    assert out.endswith(' […]')
    assert len(out) == ai.TRUNCATED_REPLY_LENGTH + len(' […]')


# --- circuit breaker --------------------------------------------------------

def test_circuit_stays_closed_below_threshold(ai):
    now = 1_000.0
    assert ai._circuit_is_open(None, now) is False
    assert ai._circuit_is_open({'count': 0, 'last': now}, now) is False
    assert ai._circuit_is_open(
        {'count': ai.CIRCUIT_BREAKER_THRESHOLD - 1, 'last': now}, now
    ) is False


def test_circuit_opens_at_threshold_and_probes_after_cooldown(ai):
    now = 1_000.0
    threshold = ai.CIRCUIT_BREAKER_THRESHOLD
    cooldown = ai.CIRCUIT_BREAKER_COOLDOWN
    open_state = {'count': threshold, 'last': now, 'announced': 0.0}
    assert ai._circuit_is_open(open_state, now) is True
    assert ai._circuit_is_open(open_state, now + cooldown - 0.1) is True
    # Exactly one cooldown later the next request is the recovery probe.
    assert ai._circuit_is_open(open_state, now + cooldown) is False
    assert ai._circuit_is_open(
        {'count': threshold + 5, 'last': now - cooldown - 1}, now
    ) is False


def test_record_api_failure_increments_without_opening_alone(ai):
    class Mem(dict):
        pass

    bot = _Bot()
    bot.memory = Mem()
    ai._record_api_failure(bot, '#chan')
    state = bot.memory['grok_api_failures']['#chan']
    assert state['count'] == 1
    assert ai._circuit_is_open(state, state['last']) is False
    for _ in range(ai.CIRCUIT_BREAKER_THRESHOLD - 1):
        ai._record_api_failure(bot, '#chan')
    state = bot.memory['grok_api_failures']['#chan']
    assert state['count'] == ai.CIRCUIT_BREAKER_THRESHOLD
    assert ai._circuit_is_open(state, state['last']) is True


# --- intoxication cleanup ---------------------------------------------------

def test_intoxication_ignores_non_channels(ai, monkeypatch):
    def _fail(bot, channel):
        raise AssertionError('effects lookup should not run')

    monkeypatch.setattr(ai, '_db_get_channel_effects', _fail)
    assert ai._apply_text_intoxication(object(), 'alice', 'hello') == 'hello'
    assert ai._apply_text_intoxication(object(), '#chan', '') == ''


def test_intoxication_tipsy_strips_weed_claims(ai, monkeypatch):
    monkeypatch.setattr(
        ai, '_db_get_channel_effects',
        lambda bot, channel: {'drunk': {'intensity': 1}},
    )
    out = ai._apply_text_intoxication(
        object(), '#chan',
        "I'm crossfaded and stoned, this joint n shot is a joint",
    )
    assert 'crossfaded' not in out.lower()
    assert 'stoned' not in out.lower()
    assert 'joint' not in out.lower()
    assert 'tipsy' in out.lower()
    assert 'this drink' in out.lower()
    assert 'drink' in out.lower()


def test_intoxication_does_not_slur_or_insert_hiccups(ai, monkeypatch):
    """Tone comes from the system prompt. Post-processing leaves the reply alone."""
    monkeypatch.setattr(
        ai, '_db_get_channel_effects',
        lambda bot, channel: {'drunk': {'intensity': 5}},
    )
    text = "I'm talking and this is some serious nonsense"
    assert ai._apply_text_intoxication(object(), '#chan', text) == text

    monkeypatch.setattr(
        ai, '_db_get_channel_effects',
        lambda bot, channel: {'drunk': {'intensity': 3}, 'stoned': {'intensity': 2}},
    )
    crossfaded = "duuude *hic* this is fine"
    assert ai._apply_text_intoxication(object(), '#chan', crossfaded) == crossfaded


def test_intoxication_stoned_strips_drunk_claims(ai, monkeypatch):
    monkeypatch.setattr(
        ai, '_db_get_channel_effects',
        lambda bot, channel: {'stoned': {'intensity': 2}},
    )
    out = ai._apply_text_intoxication(
        object(), '#chan',
        "I'm drunk *hic* and hammered, shots got me",
    )
    assert '*hic*' not in out.lower()
    assert 'drunk' not in out.lower()
    assert 'hammered' not in out.lower()
    assert 'baked' in out.lower()
    assert 'weed got me' in out.lower()


def test_intoxication_sobered_strips_leftover_slur(ai, monkeypatch):
    monkeypatch.setattr(
        ai, '_db_get_channel_effects',
        lambda bot, channel: {'sobered': {'prev_effect': 'drunk'}},
    )
    out = ai._apply_text_intoxication(
        object(), '#chan',
        "still crossfaded *hic* and zooted",
    )
    assert '*hic*' not in out.lower()
    assert 'crossfaded' not in out.lower()
    assert 'zooted' not in out.lower()
    assert 'sober' in out.lower()


# --- addressed-to-the-bot heuristic -----------------------------------------

def test_heuristic_addressed_lines(ai):
    assert _addressed(ai, 'glitchy: hello') is True
    assert _addressed(ai, 'glitchy what do u think about communists') is True
    assert _addressed(ai, 'what do you think glitchy?') is True
    assert _addressed(ai, '* pets glitchy') is True
    assert _addressed(ai, 'yo glitchy') is True


def test_heuristic_talking_about_the_bot(ai):
    assert _addressed(ai, 'he really does go balls deep with that glitchy personality') is False
    assert _addressed(ai, 'that glitchy personality') is False
    assert _addressed(ai, 'glitchy personality is a bit much') is False
    assert _addressed(ai, 'something about glitchy in the log today yeah') is False
    assert _addressed(ai, 'alice: ask glitchy later please') is False
    assert _addressed(ai, 'is glitchy down again or what') is False
    assert _addressed(ai, 'if you say glitchy three times it appears') is False
    assert _addressed(ai, '> glitchy said this yesterday somewhere') is False
    assert _addressed(ai, 'see https://example.com/glitchy for the log') is False
    assert _addressed(ai, 'glitchy and alice were both in here earlier') is False


# --- search / summary / review / time / personality -------------------------

def test_search_intent_hits_and_known_broad_match(ai):
    assert ai._SEARCH_INTENT_RE.search("what's the price of bitcoin")
    assert ai._SEARCH_INTENT_RE.search('who won last night')
    # "what is " is intentionally broad. Keep the hit visible so it is not
    # treated as a clean search request by accident.
    assert ai._SEARCH_INTENT_RE.search('what is your favorite color')
    assert ai._SEARCH_INTENT_RE.search('I bought milk today')
    assert ai._SEARCH_INTENT_RE.search('how are you') is None
    assert ai._SEARCH_INTENT_RE.search('lol nice shot') is None


def test_summary_and_review_modes(ai):
    review, summary = ai._detect_review_mode('catch me up')
    assert review is True and summary is True

    review, summary = ai._detect_review_mode('what do you think')
    assert review is True and summary is False

    review, summary = ai._detect_review_mode('^^')
    assert review is True and summary is False

    # Historical "what happened to X" is a factual question, not a recap.
    review, summary = ai._detect_review_mode('what happened to building 7')
    assert review is False and summary is False

    review, summary = ai._detect_review_mode('what happened')
    assert review is True and summary is True

    # Topic in front of "what's going on" is about that topic, not the channel.
    review, summary = ai._detect_review_mode('the dog is sick what is going on')
    assert review is False and summary is False

    review, summary = ai._detect_review_mode('what is going on')
    assert review is True and summary is True

    # A correction of the previous summary is ordinary chat.
    review, summary = ai._detect_review_mode('revise the summary')
    assert review is False and summary is False
    assert ai._SUMMARY_CORRECTION_RE.search('revise the summary')
    assert ai._SUMMARY_INTENT_RE.search('what happened to building 7') is None


def test_time_question_ignores_historical_wording(ai):
    assert ai._is_time_question('what time is it') is True
    assert ai._is_time_question('what is the date') is True
    # Positive pattern matches "what time"; "was" takes it back out.
    assert ai._TIME_INTENT_RE.search('what time was the war')
    assert ai._is_time_question('what time was the war') is False
    assert ai._is_time_question('what is the date of the earthquake') is False
    assert ai._is_time_question('lol') is False


def test_personality_command_rejects_bare_be(ai):
    pirate = ai._personality_command_match('speak like a pirate')
    assert pirate is not None
    assert pirate.group('desc').strip().lower() == 'pirate'

    leadin = ai._personality_command_match('from now on, be a pirate')
    assert leadin is not None

    # The regex matches bare "be", and the command gate drops it.
    assert ai._PERSONALITY_COMMAND_RE.search('be honest')
    assert ai._personality_command_match('be honest') is None
    assert ai._personality_command_match('talk later') is None
    assert ai._personality_command_match('hello there') is None
