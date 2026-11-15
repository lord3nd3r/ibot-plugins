"""Fact ranking, search suppression, and save/remember parsing. No network."""

import logging
import threading
import time
from types import SimpleNamespace


def _bulk(n, skip=None):
    facts = [f'unrelated zebra quilt note {i:04d} about nothing in particular' for i in range(n)]
    if skip is not None:
        facts[skip] = 'furiously mashing keys = ranting'
    return facts


def test_ranking_puts_definition_fact_first_among_hundreds(ai):
    facts = _bulk(220, skip=0)
    relevant, recent = ai._select_facts_for_prompt(facts, 'what is ranting?')
    assert relevant[0] == 'furiously mashing keys = ranting'
    assert 'furiously mashing keys = ranting' not in recent
    assert len(recent) <= ai.FACT_CONTEXT_OTHER_MAX


def test_budget_holds_with_five_hundred_facts(ai):
    facts = _bulk(500, skip=3)
    relevant, recent = ai._select_facts_for_prompt(facts, 'what is ranting?')
    assert relevant[0] == 'furiously mashing keys = ranting'
    assert len(recent) <= 25
    used = sum(len(fact) + 3 for fact in relevant + recent)
    assert used <= ai.FACT_CONTEXT_MAX_CHARS
    text = ai._format_profile_for_context(
        'End3r',
        {'location': 'Florida', 'nationality': 'none', 'facts': facts},
        include_facts=True,
        query='what is ranting?',
    )
    assert 'Location: Florida' in text
    assert 'Nationality: none' in text
    assert text.index('Most relevant saved facts') < text.index('furiously mashing keys = ranting')
    assert 'Other recent saved facts' not in text
    chatter = ai._format_profile_for_context(
        'End3r',
        {'facts': facts},
        include_facts=True,
        query='hello there friend',
    )
    assert 'Other recent saved facts' in chatter
    # Mentioned nicks still get the full list when the caller asks for it.
    full = ai._format_profile_for_context('other', {'facts': facts[:40]})
    assert full.count('\n- ') == 40
    assert 'Most relevant saved facts' not in full


def test_definition_match_blocks_search_unless_asked(ai):
    facts = _bulk(30, skip=4)
    assert ai._saved_fact_defines_question('what is ranting?', facts) is True
    assert ai._saved_fact_defines_question("what's ranting", facts) is True
    assert ai._saved_fact_defines_question('what is ranting? please search', facts) is False
    assert ai._saved_fact_defines_question('what is ranting? show sources', facts) is False
    assert ai._saved_fact_defines_question('what is the weather?', facts) is False
    assert ai._saved_fact_defines_question('what about lunch?', facts) is False


def test_save_aliases_store_and_chatter_does_not(ai):
    assert ai._parse_memory_save('save that furiously mashing keys = ranting') == (
        'furiously mashing keys = ranting'
    )
    assert ai._parse_memory_save('save: I live in Florida') == 'I live in Florida'
    assert ai._parse_memory_save('save furiously mashing keys = ranting') == (
        'furiously mashing keys = ranting'
    )
    assert ai._parse_memory_save('remember I live in Florida') == 'I live in Florida'
    assert ai._parse_memory_save('save me a seat') is None
    assert ai._parse_memory_save('save the world') is None
    assert ai._parse_memory_save('save it for later') is None
    assert ai._parse_memory_save('save game') is None
    assert ai._parse_memory_save('remember when we stayed up all night') is None
    assert ai._parse_memory_save('save that when we stayed up all night') is None


def test_definition_restatement_flips_right_hand_term(ai):
    right = ['furiously mashing keys = ranting']
    note = ai._saved_definition_instruction('what is ranting?', right)
    assert note == (
        'The user asked what "ranting" is. '
        'For this user, "ranting" means "furiously mashing keys" (their saved definition). '
        'Answer with that meaning, not a general or dictionary definition.'
    )
    left = ['ranting = furiously mashing keys']
    assert 'means "furiously mashing keys"' in ai._saved_definition_instruction('what is ranting?', left)
    assert 'means "ranting"' not in ai._saved_definition_instruction('what is ranting?', left)
    means = ['furiously mashing keys means ranting']
    assert 'means "furiously mashing keys"' in ai._saved_definition_instruction('what is ranting?', means)
    is_form = ['ranting is furiously mashing keys']
    assert 'means "furiously mashing keys"' in ai._saved_definition_instruction('what is ranting?', is_form)


def test_multiple_definitions_and_late_system_slot(ai):
    facts = [
        'furiously mashing keys = ranting',
        'ranting = also yelling at the screen',
        'unrelated zebra quilt note',
    ]
    note = ai._saved_definition_instruction('what is ranting?', facts)
    assert 'furiously mashing keys' in note
    assert 'also yelling at the screen' in note
    assert note.index('also yelling at the screen') < note.index('furiously mashing keys')
    assert 'zebra' not in note

    messages = [
        {'role': 'system', 'content': 'persona'},
        {'role': 'system', 'content': ai._UNHINGED_REMINDER},
    ]
    ai._append_saved_definition(messages, 'what is ranting?', facts)
    messages.append({'role': 'user', 'content': 'what is ranting?'})
    user_at = next(i for i, msg in enumerate(messages) if msg['role'] == 'user')
    assert messages[user_at - 1]['role'] == 'system'
    assert 'furiously mashing keys' in messages[user_at - 1]['content']
    assert messages[user_at - 2]['content'] == ai._UNHINGED_REMINDER


def test_definition_hit_omits_other_recent_facts(ai):
    facts = _bulk(40, skip=0)
    text = ai._format_profile_for_context(
        'End3r', {'facts': facts}, include_facts=True, query='what is ranting?',
    )
    assert 'Most relevant saved facts' in text
    assert 'furiously mashing keys = ranting' in text
    assert 'Other recent saved facts' not in text
    chat = ai._format_profile_for_context(
        'End3r', {'facts': facts}, include_facts=True, query='hello there friend',
    )
    assert 'Other recent saved facts' in chat


def test_definition_is_in_model_input_not_history(ai, caplog):
    facts = ['furiously mashing keys = ranting']
    original = 'what is ranting?'
    turn = ai._compose_user_turn(original, facts)
    assert turn.startswith('[Saved definition for this user:')
    assert '"ranting" means "furiously mashing keys"' in turn
    assert turn.index('[Saved definition') < turn.index('<user_message>')
    assert 'Saved definition' not in turn.split('<user_message>', 1)[1]
    assert ai._history_user_text(original) == original

    messages = [
        {'role': 'system', 'content': ai._saved_definition_instruction(original, facts)},
        {'role': 'user', 'content': turn},
    ]
    captured = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            if 'responses' in self.url:
                return {'status': 'completed', 'output': []}
            return {'choices': [{'message': {'content': 'ok'}}]}

    class _Session:
        def post(self, url, headers=None, json=None, timeout=None):
            captured.append((url, json))
            resp = _Resp()
            resp.url = url
            return resp

    bot = SimpleNamespace(memory={'grok_session': _Session(), 'grok_chat_session': _Session()})
    ai._call_responses_api(bot, messages, 'grok-test', 0.3, 40, search_mode=False)
    ai._call_chat_completions_api(
        bot, messages, 'gpt-test', 0.3, 40, base_url='https://example.test/v1',
    )
    responses = next(body for url, body in captured if url.endswith('/responses'))
    chat = next(body for url, body in captured if url.endswith('/chat/completions'))
    response_users = [m for m in responses['input'] if m.get('role') != 'system']
    assert 'Saved definition' in response_users[-1]['content']
    assert 'Saved definition' not in responses.get('instructions', '')
    chat_users = [m for m in chat['messages'] if m.get('role') != 'system']
    assert 'Saved definition' in chat_users[-1]['content']

    bot.memory = {}
    ai._remember_prompt(bot, '#Cyberspace', 'End3r', messages)
    stored = bot.memory['grok_last_prompt']['#cyberspace']['messages']
    stored_user = next(m for m in stored if m['role'] == 'user')
    assert 'Saved definition' not in stored_user['content']
    assert 'what is ranting?' in stored_user['content']

    caplog.set_level(logging.INFO)
    with caplog.at_level(logging.INFO, logger='Grok'):
        ai._log_saved_definitions(
            SimpleNamespace(logger=logging.getLogger('Grok')), original, facts,
        )
    assert 'saved definition attached: ranting means furiously mashing keys' in caplog.text


def test_definition_hit_lowers_temperature_only(ai):
    assert ai._request_temperature() == 0.95
    assert ai._request_temperature(unhinged=True) == 1.05
    assert ai._request_temperature(definition_hit=True) == 0.3
    assert ai._request_temperature(unhinged=True, definition_hit=True) == 0.4
    assert ai._request_temperature(review_mode=True) == 0.90
    marked = [{'role': 'user', 'content': ai._compose_user_turn(
        'what is ranting?', ['furiously mashing keys = ranting'],
    )}]
    plain = [{'role': 'user', 'content': ai._wrap_user_text('what is ranting?')}]
    assert ai._messages_have_saved_definition(marked) is True
    assert ai._messages_have_saved_definition(plain) is False


def test_shutdown_warns_if_a_worker_is_still_alive(ai, caplog):
    def _linger():
        time.sleep(0.4)

    thread = threading.Thread(target=_linger, name='grok-test-worker')
    thread.start()
    bot = SimpleNamespace(logger=logging.getLogger('Grok'))
    caplog.set_level(logging.WARNING)
    with caplog.at_level(logging.WARNING, logger='Grok'):
        ai._warn_live_workers(bot, [thread])
    thread.join()
    assert 'Grok worker still alive after join: grok-test-worker' in caplog.text


def test_fact_cap_trims_oldest(ai):
    assert ai.MAX_USER_FACTS == 500
    facts = [f'fact {i:04d} xxxx' for i in range(501)]
    trimmed = ai._bounded_facts(facts)
    assert len(trimmed) == 500
    assert trimmed[0] == 'fact 0001 xxxx'
    assert trimmed[-1] == 'fact 0500 xxxx'
    assert 'fact 0000 xxxx' not in trimmed
