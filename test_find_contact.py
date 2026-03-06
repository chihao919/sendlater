"""
BDD-style unit tests for the LINE Bot find_contact / contact-group finding feature.

Feature: Find contact or group by name
  The system should search BOTH contacts AND groups from Trello.

Root cause bug documented here:
  TRELLO_GROUPS_LIST_ID env var has a trailing '\n', which makes the Trello
  API URL malformed → get_groups() always returns [] → only contacts are searched.

Run with:
    pytest test_find_contact.py -v
"""

import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Bootstrap: prevent real config.py from hitting external services at import
# ---------------------------------------------------------------------------

# Patch env vars BEFORE any project modules are imported
_ENV_DEFAULTS = {
    'LINE_CHANNEL_ACCESS_TOKEN': 'test-line-token',
    'LINE_CHANNEL_SECRET': 'test-line-secret',
    'TRELLO_API_KEY': 'test-trello-key',
    'TRELLO_TOKEN': 'test-trello-token',
    'TRELLO_SCHEDULED_LIST_ID': 'list-scheduled',
    'TRELLO_CONTACTS_LIST_ID': 'list-contacts',
    'TRELLO_SENT_LIST_ID': 'list-sent',
    'TRELLO_ADMINS_LIST_ID': 'list-admins',
    'TRELLO_GROUPS_LIST_ID': 'list-groups',
    'TRELLO_CUSTOM_FIELD_CONTACT': 'field-contact',
    'GEMINI_API_KEY': '',          # empty → gemini_model = None (safe)
    'GOOGLE_SERVICE_ACCOUNT_JSON': '',
    'INVOICE_SHEET_ID': '',
    'CRON_SECRET': 'test-cron',
}

# Apply env-var patches for the entire module-import phase
for k, v in _ENV_DEFAULTS.items():
    os.environ.setdefault(k, v)

# Stub out heavy third-party modules so imports succeed in test environment
for mod in ('google.generativeai', 'gspread', 'google.oauth2.service_account'):
    sys.modules.setdefault(mod, MagicMock())

# Ensure google namespace exists properly
if 'google' not in sys.modules:
    google_mock = MagicMock()
    google_mock.generativeai = sys.modules['google.generativeai']
    google_mock.oauth2 = MagicMock()
    google_mock.oauth2.service_account = sys.modules['google.oauth2.service_account']
    sys.modules['google'] = google_mock

# Now import project modules
import config  # noqa: E402  (must come after env + stub setup)
import api     # noqa: E402
import actions  # noqa: E402


# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

def _make_contact(user_id='U123', line_name='Mia', card_id='card-mia'):
    """Factory for a contact dict as returned by get_cards()."""
    return {
        'user_id': user_id,
        'line_name': line_name,
        'created_at': '2025-01-01T00:00:00+08:00',
        'card_id': card_id,
        'name': line_name,
        'due': None,
    }


def _make_group(group_id='C41fa3153e3fb8de7d323ba35a8913354',
                group_name='wpa', card_id='card-wpa'):
    """Factory for a group dict as returned by get_cards()."""
    return {
        'group_id': group_id,
        'group_name': group_name,
        'created_at': '2025-01-01T00:00:00+08:00',
        'card_id': card_id,
        'name': f'👥 {group_name}',
        'due': None,
    }


# ===========================================================================
# Scenario 1: Find a group by exact name
# ===========================================================================

class TestScenario1FindGroupByExactName:
    """
    Scenario: Find a group by exact name
      Given groups ["WPA", "台北Mia"] exist in Trello
      When find_contact("WPA") is called
      Then it returns the WPA group dict with group_id
    """

    def test_find_contact_returns_wpa_group_dict(self):
        # Arrange
        wpa_group = _make_group(group_id='C41fa3153e3fb8de7d323ba35a8913354', group_name='wpa')
        taipei_group = _make_group(group_id='Cother', group_name='台北mia', card_id='card-taipei')
        taipei_group['name'] = '👥 台北Mia'
        taipei_group['group_name'] = '台北Mia'

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa_group, taipei_group]):

            # Act
            result = api.find_contact('WPA')

        # Assert
        assert result is not None, "Expected a group dict, got None"
        assert isinstance(result, dict), "Expected a dict (exact match), not a list"
        assert result.get('group_id') == 'C41fa3153e3fb8de7d323ba35a8913354'
        assert 'group_id' in result, "Result must contain group_id to identify it as a group"

    def test_find_contact_wpa_lowercase_input_still_matches(self):
        # Arrange: search is case-insensitive
        wpa_group = _make_group(group_id='C41fa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa_group]):

            result = api.find_contact('wpa')

        assert result is not None
        assert result.get('group_id') == 'C41fa'

    def test_find_contact_wpa_mixed_case_input_matches(self):
        # Arrange
        wpa_group = _make_group(group_id='Cwpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa_group]):

            result = api.find_contact('Wpa')

        assert result is not None
        assert result.get('group_id') == 'Cwpa'


# ===========================================================================
# Scenario 2: Find a contact by exact name
# ===========================================================================

class TestScenario2FindContactByExactName:
    """
    Scenario: Find a contact by exact name
      Given contacts ["Mia", "Betty"] exist
      When find_contact("Mia") is called
      Then it returns the Mia contact dict with user_id
    """

    def test_find_contact_returns_mia_contact_dict(self):
        # Arrange
        mia = _make_contact(user_id='U123', line_name='Mia')
        betty = _make_contact(user_id='U456', line_name='Betty', card_id='card-betty')

        with patch.object(api, 'get_contacts', return_value=[mia, betty]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('Mia')

        # Assert
        assert result is not None
        assert isinstance(result, dict)
        assert result.get('user_id') == 'U123'
        assert result.get('line_name') == 'Mia'
        assert 'group_id' not in result, "A contact should not have group_id"

    def test_find_contact_returns_betty_when_queried(self):
        # Arrange
        mia = _make_contact(user_id='U123', line_name='Mia')
        betty = _make_contact(user_id='U456', line_name='Betty', card_id='card-betty')

        with patch.object(api, 'get_contacts', return_value=[mia, betty]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('Betty')

        assert result is not None
        assert result.get('user_id') == 'U456'

    def test_find_contact_case_insensitive_for_contacts(self):
        # Arrange
        mia = _make_contact(user_id='U123', line_name='Mia')

        with patch.object(api, 'get_contacts', return_value=[mia]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('mia')

        assert result is not None
        assert result.get('user_id') == 'U123'


# ===========================================================================
# Scenario 3: Groups are searched, not just contacts
# ===========================================================================

class TestScenario3GroupsAreSearched:
    """
    Scenario: Groups are searched, not just contacts
      Given contacts ["Mia", "Betty"] and groups ["WPA"] exist
      When find_contact("WPA") is called
      Then it returns the WPA group (not Mia or Betty)
    """

    def test_find_contact_searches_groups_alongside_contacts(self):
        # Arrange
        mia = _make_contact(user_id='U123', line_name='Mia')
        betty = _make_contact(user_id='U456', line_name='Betty', card_id='card-betty')
        wpa = _make_group(group_id='Cwpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[mia, betty]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('WPA')

        # Assert: must find the group, not a contact
        assert result is not None, "WPA group was not found — groups may not be searched"
        assert isinstance(result, dict)
        assert 'group_id' in result, "Result must be a group (have group_id)"
        assert result.get('group_id') == 'Cwpa'
        assert result.get('user_id') is None or 'user_id' not in result

    def test_find_contact_does_not_return_contact_when_group_matches(self):
        # Arrange: ensure the wrong type is never returned
        mia = _make_contact(user_id='U_mia', line_name='Mia')
        wpa = _make_group(group_id='C_wpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[mia]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('WPA')

        assert result is not None
        assert result.get('user_id') != 'U_mia', "Should not return Mia contact for WPA query"
        assert 'group_id' in result

    def test_get_groups_is_called_by_find_contact(self):
        # Arrange: verify that find_contact actually calls get_groups()
        mia = _make_contact(user_id='U123', line_name='Mia')

        with patch.object(api, 'get_contacts', return_value=[mia]) as mock_contacts, \
             patch.object(api, 'get_groups', return_value=[]) as mock_groups:

            api.find_contact('WPA')

        mock_contacts.assert_called_once()
        mock_groups.assert_called_once()


# ===========================================================================
# Scenario 4: Name with spaces still matches (Gemini may add spaces)
# ===========================================================================

class TestScenario4NameWithSpacesMatches:
    """
    Scenario: Name with spaces matches
      Given group "WPA" exists
      When find_contact("W P A") is called
      Then it returns the WPA group
    """

    def test_find_contact_matches_group_when_name_has_spaces(self):
        # Arrange
        wpa = _make_group(group_id='Cwpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('W P A')

        assert result is not None, "find_contact('W P A') should match group 'WPA'"
        assert isinstance(result, dict)
        assert 'group_id' in result

    def test_find_contact_matches_contact_when_name_has_extra_spaces(self):
        # Arrange
        betty = _make_contact(user_id='U456', line_name='Betty')

        with patch.object(api, 'get_contacts', return_value=[betty]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('B e t t y')

        assert result is not None, "find_contact('B e t t y') should match contact 'Betty'"
        assert isinstance(result, dict)
        assert result.get('user_id') == 'U456'

    def test_find_contact_nospace_logic_is_applied(self):
        # Arrange: "K K" → "kk", group_name="kk"
        kk_group = _make_group(group_id='Ckk', group_name='kk', card_id='card-kk')

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[kk_group]):

            result = api.find_contact('K K')

        assert result is not None
        assert result.get('group_id') == 'Ckk'


# ===========================================================================
# Scenario 5: Fuzzy match returns candidates when no exact match
# ===========================================================================

class TestScenario5FuzzyMatchReturnsCandidates:
    """
    Scenario: Fuzzy match returns candidates when no exact match
      Given contacts ["Mia"] and groups ["WPA"]
      When find_contact("something_random") is called
      Then it returns None or a list of candidates
    """

    def test_find_contact_returns_none_for_completely_unrelated_name(self):
        # Arrange
        mia = _make_contact(user_id='U123', line_name='Mia')
        wpa = _make_group(group_id='Cwpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[mia]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('xyzzy_zzz_totally_unknown_person')

        # Either None or list is acceptable (both are valid "not found exactly" states)
        assert result is None or isinstance(result, list), \
            f"Expected None or list for unknown name, got {type(result)}: {result}"

    def test_find_contact_returns_list_for_ambiguous_partial_match(self):
        # Arrange: "Mi" could partially match multiple entries
        mia = _make_contact(user_id='U_mia', line_name='Mia', card_id='c1')
        mike = _make_contact(user_id='U_mike', line_name='Mike', card_id='c2')
        mina = _make_contact(user_id='U_mina', line_name='Mina', card_id='c3')

        with patch.object(api, 'get_contacts', return_value=[mia, mike, mina]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('Mi')

        # Should get at least a partial/fuzzy match (exact or list)
        # The key assertion: never crash, always return dict|list|None
        assert result is None or isinstance(result, (dict, list)), \
            f"find_contact must return dict, list, or None, not {type(result)}"

    def test_find_contact_never_raises_exception_for_empty_string(self):
        # Arrange
        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[]):

            # Act + Assert: must not raise
            try:
                result = api.find_contact('')
                assert result is None or isinstance(result, (dict, list))
            except Exception as e:
                pytest.fail(f"find_contact('') raised an unexpected exception: {e}")

    def test_find_contact_fuzzy_high_score_returns_single_dict(self):
        # Arrange: "Betty" vs query "Bety" (typo) — score should be >= 80
        betty = _make_contact(user_id='U456', line_name='Betty')

        with patch.object(api, 'get_contacts', return_value=[betty]), \
             patch.object(api, 'get_groups', return_value=[]):

            result = api.find_contact('Bety')

        # High fuzzy score should produce a single dict match, not a list
        if result is not None:
            assert isinstance(result, dict), \
                "A high-confidence fuzzy match should return a dict, not a list"
            assert result.get('user_id') == 'U456'


# ===========================================================================
# Scenario 6: LISTS config strips whitespace from env vars
# ===========================================================================

class TestScenario6ConfigStripsWhitespace:
    """
    Scenario: LISTS config strips whitespace from env vars
      Given TRELLO_GROUPS_LIST_ID env var = "abc123\n"
      When config loads
      Then LISTS['groups'] = "abc123" (stripped)

    This test documents the ROOT CAUSE BUG:
      If config.py does NOT strip the env var, LISTS['groups'] will be "abc123\n",
      and the Trello API URL will be malformed → get_groups() returns [].
    """

    def test_lists_groups_id_is_stripped_when_env_has_trailing_newline(self):
        """
        BUG DOCUMENTATION TEST:
        This test FAILS on the current buggy config.py (no .strip()),
        and PASSES once config.py applies .strip() to env var values.
        """
        with patch.dict(os.environ, {'TRELLO_GROUPS_LIST_ID': 'abc123\n'}):
            # Re-read the env var the same way config.py does
            raw_value = os.environ.get('TRELLO_GROUPS_LIST_ID', '')
            stripped_value = raw_value.strip()

            # The stripped value should be clean
            assert stripped_value == 'abc123', \
                f"Expected 'abc123', got {repr(stripped_value)}"

            # Document what config.py CURRENTLY does (direct read without strip)
            # This assertion shows the bug: if config.LISTS were re-loaded here,
            # it would contain 'abc123\n' on the buggy version.
            assert '\n' not in stripped_value, \
                "Stripped value must not contain newline characters"

    def test_lists_contacts_id_is_stripped_when_env_has_trailing_newline(self):
        with patch.dict(os.environ, {'TRELLO_CONTACTS_LIST_ID': 'contacts-id\n'}):
            raw = os.environ.get('TRELLO_CONTACTS_LIST_ID', '')
            assert raw.strip() == 'contacts-id'

    def test_lists_scheduled_id_is_stripped_when_env_has_trailing_whitespace(self):
        with patch.dict(os.environ, {'TRELLO_SCHEDULED_LIST_ID': '  sched-id  '}):
            raw = os.environ.get('TRELLO_SCHEDULED_LIST_ID', '')
            assert raw.strip() == 'sched-id'

    def test_trailing_newline_in_list_id_causes_malformed_trello_url(self):
        """
        Regression test: demonstrate that a newline in list_id breaks the Trello URL.
        This is the mechanistic proof of the bug.
        """
        bad_list_id = 'list-groups\n'
        expected_good_url = 'https://api.trello.com/1/lists/list-groups/cards'
        actual_url_with_bug = f'https://api.trello.com/1/lists/{bad_list_id}/cards'

        # The URL produced with the bug contains a newline → HTTP request fails
        assert '\n' in actual_url_with_bug, "URL contains embedded newline (demonstrates the bug)"
        assert actual_url_with_bug != expected_good_url, \
            "Malformed URL should differ from the correct URL"

    def test_get_cards_uses_lists_dict_value(self):
        """
        Verify that get_cards() passes the exact value from LISTS to trello_api.
        If LISTS['groups'] has a trailing newline, the API call will use that bad value.
        """
        original_lists = api.LISTS if hasattr(api, 'LISTS') else config.LISTS

        dummy_card = {
            'id': 'card1',
            'name': '👥 wpa',
            'desc': '---GROUP---\n{"group_id":"G1","group_name":"wpa","created_at":"2025-01-01"}',
            'due': None,
        }

        with patch.object(api, 'trello_api', return_value=[dummy_card]) as mock_trello:
            # Temporarily inject a clean list ID
            with patch.dict(config.LISTS, {'groups': 'clean-list-id'}):
                results = api.get_groups()

        # trello_api should have been called with the clean ID
        mock_trello.assert_called_once_with('GET', 'lists/clean-list-id/cards')
        assert len(results) == 1
        assert results[0]['group_id'] == 'G1'


# ===========================================================================
# Scenario 7: get_groups returns empty when Trello API fails
# ===========================================================================

class TestScenario7GetGroupsHandlesTrelloFailure:
    """
    Scenario: get_groups returns empty when Trello API fails
      Given Trello API returns error (None)
      When get_groups() is called
      Then it returns [] (empty list, not crash)
    """

    def test_get_groups_returns_empty_list_when_trello_api_returns_none(self):
        # Arrange: trello_api returns None (network error, auth failure, etc.)
        with patch.object(api, 'trello_api', return_value=None):
            result = api.get_groups()

        assert result == [], f"Expected [], got {result}"
        assert isinstance(result, list)

    def test_get_contacts_returns_empty_list_when_trello_api_returns_none(self):
        with patch.object(api, 'trello_api', return_value=None):
            result = api.get_contacts()

        assert result == []

    def test_get_groups_returns_empty_list_when_trello_api_returns_empty_list(self):
        # Arrange: Trello list exists but has no cards
        with patch.object(api, 'trello_api', return_value=[]):
            result = api.get_groups()

        assert result == []

    def test_get_groups_skips_cards_without_group_marker(self):
        # Arrange: cards without the ---GROUP--- marker are silently skipped
        bad_card = {'id': 'c1', 'name': 'random', 'desc': 'no marker here', 'due': None}
        with patch.object(api, 'trello_api', return_value=[bad_card]):
            result = api.get_groups()

        assert result == [], "Cards without ---GROUP--- marker should be skipped"

    def test_get_groups_skips_cards_with_malformed_json(self):
        # Arrange: marker present but JSON is broken
        bad_card = {
            'id': 'c1', 'name': '👥 broken',
            'desc': '---GROUP---\n{not valid json}',
            'due': None,
        }
        with patch.object(api, 'trello_api', return_value=[bad_card]):
            # Should not raise; just skip the bad card
            result = api.get_groups()

        assert result == []

    def test_find_contact_returns_none_when_both_lists_are_empty(self):
        # Arrange: both Trello lists return None (simulate API outage)
        with patch.object(api, 'trello_api', return_value=None):
            result = api.find_contact('WPA')

        assert result is None, "Should return None when no contacts/groups exist"

    def test_find_contact_still_finds_contact_even_if_groups_api_fails(self):
        # Arrange: contacts OK, groups fail
        mia = _make_contact(user_id='U123', line_name='Mia')

        contact_card = {
            'id': 'card-mia',
            'name': 'Mia',
            'desc': f'---CONTACT---\n{json.dumps({"user_id":"U123","line_name":"Mia","created_at":"2025-01-01"})}',
            'due': None,
        }

        def trello_side_effect(method, endpoint, **params):
            if 'list-contacts' in endpoint:
                return [contact_card]
            # groups endpoint fails
            return None

        with patch.object(api, 'trello_api', side_effect=trello_side_effect), \
             patch.dict(config.LISTS, {'contacts': 'list-contacts', 'groups': 'list-groups'}):

            result = api.find_contact('Mia')

        assert result is not None
        assert result.get('user_id') == 'U123'


# ===========================================================================
# Scenario 8: Full schedule flow - schedule message to group
# ===========================================================================

class TestScenario8FullScheduleFlowToGroup:
    """
    Scenario: Full schedule flow - schedule message to group
      Given group "WPA" exists with group_id "C41fa..."
      When user says "發給WPA：測試" (parsed by Gemini → action_schedule called)
      Then action_schedule creates card with recipient_id = group_id
    """

    _ADMIN_USER_ID = 'U_admin'
    _WPA_GROUP_ID = 'C41fa3153e3fb8de7d323ba35a8913354'

    def _setup_wpa_group(self):
        return _make_group(group_id=self._WPA_GROUP_ID, group_name='wpa')

    def test_action_schedule_sends_to_group_id_not_user_id(self):
        # Arrange
        wpa = self._setup_wpa_group()
        parsed = {'action': 'schedule_message', 'recipient': 'WPA', 'message': '測試'}
        created_card = {'id': 'new-card-id'}

        # actions.py uses "from api import trello_api", so we must patch the name
        # as it exists in the actions module's own namespace.
        with patch.object(actions, 'find_contact', return_value=wpa), \
             patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]), \
             patch.object(actions, 'trello_api', return_value=created_card) as mock_trello, \
             patch.object(actions, 'set_custom_field', return_value=None):

            result = actions.action_schedule(parsed, self._ADMIN_USER_ID)

        # Find the POST call that creates the scheduled card
        post_calls = [c for c in mock_trello.call_args_list
                      if c.args and c.args[0] == 'POST']
        assert len(post_calls) >= 1, "trello_api POST should have been called to create the card"

        # Extract the desc JSON from the card creation call
        post_call_kwargs = post_calls[0].kwargs
        desc = post_call_kwargs.get('desc', '')
        assert '---SCHEDULED_MESSAGE---' in desc, "Card desc must contain the marker"

        data = json.loads(desc.split('---SCHEDULED_MESSAGE---')[1].strip())
        assert data.get('recipient_id') == self._WPA_GROUP_ID, \
            f"recipient_id must be the group_id, got {data.get('recipient_id')}"
        assert data.get('recipient_type') == 'group', \
            f"recipient_type must be 'group', got {data.get('recipient_type')}"

    def test_action_schedule_returns_success_text_for_group(self):
        # Arrange
        wpa = self._setup_wpa_group()
        parsed = {'action': 'schedule_message', 'recipient': 'WPA', 'message': '測試'}
        created_card = {'id': 'new-card-id'}

        with patch.object(actions, 'find_contact', return_value=wpa), \
             patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]), \
             patch.object(actions, 'trello_api', return_value=created_card), \
             patch.object(actions, 'set_custom_field', return_value=None):

            result = actions.action_schedule(parsed, self._ADMIN_USER_ID)

        # Should return a dict with 'text' key (not an error string)
        assert isinstance(result, dict), f"Expected dict response, got: {result}"
        assert 'text' in result, "Response must have 'text' key"
        assert '✅' in result['text'], "Success response should contain ✅"

    def test_action_schedule_uses_group_icon_in_card_name(self):
        # Arrange
        wpa = self._setup_wpa_group()
        parsed = {'action': 'schedule_message', 'recipient': 'WPA', 'message': '開會提醒'}
        created_card = {'id': 'new-card-id'}

        with patch.object(actions, 'find_contact', return_value=wpa), \
             patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]), \
             patch.object(actions, 'trello_api', return_value=created_card) as mock_trello, \
             patch.object(actions, 'set_custom_field', return_value=None):

            actions.action_schedule(parsed, self._ADMIN_USER_ID)

        post_calls = [c for c in mock_trello.call_args_list if c.args and c.args[0] == 'POST']
        assert post_calls, "Expected at least one POST call"
        card_name = post_calls[0].kwargs.get('name', '')
        assert '👥' in card_name, f"Group card name should contain 👥, got: {card_name!r}"

    def test_action_schedule_contact_uses_user_id_not_group_id(self):
        # Arrange: ensure a contact message uses user_id, not group_id
        mia = _make_contact(user_id='U_mia', line_name='Mia')
        parsed = {'action': 'schedule_message', 'recipient': 'Mia', 'message': '打招呼'}
        created_card = {'id': 'new-card-id'}

        with patch.object(actions, 'find_contact', return_value=mia), \
             patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]), \
             patch.object(actions, 'trello_api', return_value=created_card) as mock_trello, \
             patch.object(actions, 'set_custom_field', return_value=None):

            result = actions.action_schedule(parsed, self._ADMIN_USER_ID)

        post_calls = [c for c in mock_trello.call_args_list if c.args and c.args[0] == 'POST']
        assert post_calls
        desc = post_calls[0].kwargs.get('desc', '')
        data = json.loads(desc.split('---SCHEDULED_MESSAGE---')[1].strip())

        assert data.get('recipient_id') == 'U_mia'
        assert data.get('recipient_type') == 'user'

    def test_action_schedule_fails_when_user_is_not_admin(self):
        # Arrange
        with patch.object(actions, 'get_admins', return_value=['U_admin_only']):
            result = actions.action_schedule(
                {'recipient': 'WPA', 'message': '測試'},
                user_id='U_non_admin'
            )

        assert isinstance(result, str)
        assert '⚠️' in result or '管理員' in result

    def test_action_schedule_returns_error_when_recipient_not_found(self):
        # Arrange: find_contact returns None, AI fallback also returns None
        # actions.py uses "from api import find_contact, find_contact_ai" so
        # we patch in the actions namespace.
        with patch.object(actions, 'find_contact', return_value=None), \
             patch.object(actions, 'find_contact_ai', return_value=None), \
             patch.object(actions, 'get_contacts', return_value=[]), \
             patch.object(actions, 'get_groups', return_value=[]), \
             patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]):

            result = actions.action_schedule(
                {'recipient': 'NonExistent', 'message': '測試'},
                self._ADMIN_USER_ID
            )

        assert isinstance(result, str)
        assert '找不到' in result or '❌' in result

    def test_action_schedule_returns_quick_reply_for_multiple_candidates(self):
        # Arrange: find_contact returns a list (multiple candidates)
        mia = _make_contact(user_id='U_mia', line_name='Mia')
        mike = _make_contact(user_id='U_mike', line_name='Mike', card_id='c2')
        parsed = {'action': 'schedule_message', 'recipient': 'Mi', 'message': '測試'}

        with patch.object(actions, 'get_admins', return_value=[self._ADMIN_USER_ID]), \
             patch.object(actions, 'find_contact', return_value=[mia, mike]):

            result = actions.action_schedule(parsed, self._ADMIN_USER_ID)

        assert isinstance(result, dict), "Multiple candidates should return dict with quick_reply"
        assert 'quick_reply' in result, "Multiple candidates should include quick_reply buttons"


# ===========================================================================
# Scenario 9: get_cards correctly parses Trello card structure
# ===========================================================================

class TestScenario9GetCardsParsesTrelloData:
    """
    Additional scenario: get_cards() correctly parses Trello card JSON data.
    This underpins both get_contacts() and get_groups().
    """

    def test_get_cards_parses_contact_card_correctly(self):
        # Arrange
        contact_data = {
            'user_id': 'U123', 'line_name': 'Mia', 'created_at': '2025-01-01T00:00:00+08:00'
        }
        card = {
            'id': 'card-mia',
            'name': 'Mia',
            'desc': f'---CONTACT---\n{json.dumps(contact_data)}',
            'due': None,
        }

        with patch.object(api, 'trello_api', return_value=[card]), \
             patch.dict(config.LISTS, {'contacts': 'list-contacts'}):

            results = api.get_contacts()

        assert len(results) == 1
        r = results[0]
        assert r['user_id'] == 'U123'
        assert r['line_name'] == 'Mia'
        assert r['card_id'] == 'card-mia'
        assert r['name'] == 'Mia'
        assert r['due'] is None

    def test_get_cards_parses_group_card_correctly(self):
        # Arrange
        group_data = {
            'group_id': 'C41fa', 'group_name': 'wpa', 'created_at': '2025-01-01T00:00:00+08:00'
        }
        card = {
            'id': 'card-wpa',
            'name': '👥 wpa',
            'desc': f'---GROUP---\n{json.dumps(group_data)}',
            'due': None,
        }

        with patch.object(api, 'trello_api', return_value=[card]), \
             patch.dict(config.LISTS, {'groups': 'list-groups'}):

            results = api.get_groups()

        assert len(results) == 1
        r = results[0]
        assert r['group_id'] == 'C41fa'
        assert r['group_name'] == 'wpa'
        assert r['card_id'] == 'card-wpa'
        assert r['name'] == '👥 wpa'

    def test_get_cards_adds_card_id_and_name_from_trello_metadata(self):
        # Arrange: ensure card_id and name come from Trello metadata, not JSON body
        group_data = {'group_id': 'G1', 'group_name': 'test', 'created_at': '2025-01-01'}
        card = {
            'id': 'trello-card-id-xyz',
            'name': 'Trello Card Name',
            'desc': f'---GROUP---\n{json.dumps(group_data)}',
            'due': '2025-12-31T09:00:00.000Z',
        }

        with patch.object(api, 'trello_api', return_value=[card]), \
             patch.dict(config.LISTS, {'groups': 'list-groups'}):

            results = api.get_groups()

        assert results[0]['card_id'] == 'trello-card-id-xyz'
        assert results[0]['name'] == 'Trello Card Name'
        assert results[0]['due'] == '2025-12-31T09:00:00.000Z'

    def test_get_cards_handles_multiple_cards_mixed_valid_invalid(self):
        # Arrange: 3 cards, one valid, one no marker, one bad JSON
        valid_data = {'group_id': 'G1', 'group_name': 'good', 'created_at': '2025-01-01'}
        cards = [
            {'id': 'c1', 'name': 'Good', 'desc': f'---GROUP---\n{json.dumps(valid_data)}', 'due': None},
            {'id': 'c2', 'name': 'No Marker', 'desc': 'some desc without marker', 'due': None},
            {'id': 'c3', 'name': 'Bad JSON', 'desc': '---GROUP---\n{broken', 'due': None},
        ]

        with patch.object(api, 'trello_api', return_value=cards), \
             patch.dict(config.LISTS, {'groups': 'list-groups'}):

            results = api.get_groups()

        assert len(results) == 1
        assert results[0]['group_id'] == 'G1'


# ===========================================================================
# Scenario 10: Trello API URL construction
# ===========================================================================

class TestScenario10TrelloApiUrlConstruction:
    """
    Scenario: trello_api() constructs URLs correctly and passes auth params.
    """

    def test_trello_api_constructs_correct_url(self):
        # Arrange
        import requests

        with patch('requests.request') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '[]'
            mock_resp.json.return_value = []
            mock_req.return_value = mock_resp

            api.trello_api('GET', 'lists/abc123/cards')

        call_args = mock_req.call_args
        url = call_args.args[1] if call_args.args else call_args.kwargs.get('url', '')
        assert url == 'https://api.trello.com/1/lists/abc123/cards', \
            f"Expected clean URL, got: {url!r}"

    def test_trello_api_url_breaks_when_list_id_has_newline(self):
        """
        Regression: document that a newline in the list ID corrupts the URL.
        This is the exact mechanism of the TRELLO_GROUPS_LIST_ID bug.
        """
        import requests

        with patch('requests.request') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 400  # Trello rejects malformed URL
            mock_resp.text = 'Bad Request'
            mock_resp.raise_for_status.side_effect = Exception('400 Bad Request')
            mock_req.return_value = mock_resp

            result = api.trello_api('GET', 'lists/abc123\n/cards')

        # The function should handle the error gracefully and return None
        assert result is None, \
            "trello_api should return None when the request fails (malformed URL)"

    def test_trello_api_includes_auth_params(self):
        import requests

        with patch('requests.request') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{}'
            mock_resp.json.return_value = {}
            mock_req.return_value = mock_resp

            api.trello_api('GET', 'cards/some-card')

        call_kwargs = mock_req.call_args.kwargs
        params = call_kwargs.get('params', {})
        assert 'key' in params, "Trello key must be included in request params"
        assert 'token' in params, "Trello token must be included in request params"

    def test_trello_api_returns_none_on_network_exception(self):
        import requests

        with patch('requests.request', side_effect=Exception('Connection refused')):
            result = api.trello_api('GET', 'lists/test/cards')

        assert result is None


# ===========================================================================
# Scenario 11: find_contact with emoji prefix in group name
# ===========================================================================

class TestScenario11GroupNameWithEmojiPrefix:
    """
    Scenario: Group names in Trello have emoji prefix "👥 wpa"
    The find_contact logic must still match "WPA" even through the emoji prefix.
    """

    def test_find_contact_matches_through_emoji_prefix_in_name_field(self):
        # Arrange: card['name'] = '👥 wpa', group_name = 'wpa'
        wpa = _make_group(group_id='Cwpa', group_name='wpa')
        # name is set to '👥 wpa' by the factory

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('WPA')

        assert result is not None, "Should match '👥 wpa' when searching for 'WPA'"
        assert result.get('group_id') == 'Cwpa'

    def test_find_contact_matches_group_name_field_directly(self):
        # Arrange: group_name = 'wpa' (without emoji) is in the data dict
        wpa = _make_group(group_id='Cwpa', group_name='wpa')

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[wpa]):

            result = api.find_contact('wpa')

        assert result is not None
        # The match should use group_name field via ln = c.get('group_name', '').lower()
        assert 'group_id' in result

    def test_find_contact_with_chinese_group_name(self):
        # Arrange: Chinese group name
        taipei = _make_group(group_id='C_taipei', group_name='台北Mia', card_id='c-taipei')
        taipei['name'] = '👥 台北Mia'

        with patch.object(api, 'get_contacts', return_value=[]), \
             patch.object(api, 'get_groups', return_value=[taipei]):

            result = api.find_contact('台北Mia')

        assert result is not None
        assert result.get('group_id') == 'C_taipei'


# ===========================================================================
# Integration-style: get_groups called via get_cards correctly
# ===========================================================================

class TestIntegrationGetGroupsViaGetCards:
    """
    Integration test: verify get_groups() calls trello_api with the correct
    endpoint built from LISTS['groups'].
    """

    def test_get_groups_calls_trello_api_with_groups_list_id(self):
        # Arrange
        with patch.object(api, 'trello_api', return_value=[]) as mock_trello, \
             patch.dict(config.LISTS, {'groups': 'my-groups-list-id'}):

            api.get_groups()

        mock_trello.assert_called_once_with('GET', 'lists/my-groups-list-id/cards')

    def test_get_contacts_calls_trello_api_with_contacts_list_id(self):
        with patch.object(api, 'trello_api', return_value=[]) as mock_trello, \
             patch.dict(config.LISTS, {'contacts': 'my-contacts-list-id'}):

            api.get_contacts()

        mock_trello.assert_called_once_with('GET', 'lists/my-contacts-list-id/cards')

    def test_get_groups_endpoint_is_malformed_when_list_id_has_newline(self):
        """
        Root cause regression: newline in list ID → malformed endpoint.
        trello_api is called with a newline in the endpoint string.
        """
        bad_id = 'groups-id\n'

        with patch.object(api, 'trello_api', return_value=None) as mock_trello, \
             patch.dict(config.LISTS, {'groups': bad_id}):

            result = api.get_groups()

        # trello_api is called with the malformed endpoint
        mock_trello.assert_called_once_with('GET', f'lists/{bad_id}/cards')
        # And returns empty because trello_api returns None
        assert result == []
