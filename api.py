"""
SendLater - LINE API, Trello API, and data access layer.
"""
import json
import requests
from datetime import datetime
from rapidfuzz import fuzz

from config import (
    LINE_TOKEN, TRELLO_KEY, TRELLO_TOKEN, LISTS,
    CUSTOM_FIELD_CONTACT, TW_TZ, gemini_model,
)


# ===== LINE API =====

def line_api(method, endpoint, data=None):
    url = f'https://api.line.me/v2/bot/{endpoint}'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'}
    try:
        r = requests.request(method, url, headers=headers, json=data, timeout=10)
        return r.json() if r.text and r.status_code == 200 else r.status_code == 200
    except:
        return None


def reply(token, text, quick_reply=None):
    msg = {'type': 'text', 'text': text}
    if quick_reply:
        msg['quickReply'] = {'items': [
            {'type': 'action', 'action': {'type': 'message', 'label': q['label'][:20], 'text': q['text']}}
            for q in quick_reply
        ]}
    return line_api('POST', 'message/reply', {'replyToken': token, 'messages': [msg]})


def push(user_id, text):
    return line_api('POST', 'message/push', {'to': user_id, 'messages': [{'type': 'text', 'text': text}]})


# ===== Trello API =====

def trello_api(method, endpoint, **params):
    url = f"https://api.trello.com/1/{endpoint}"
    params.update(key=TRELLO_KEY, token=TRELLO_TOKEN)
    try:
        r = requests.request(method, url, params=params, timeout=10)
        r.raise_for_status()
        return r.json() if r.text else {}
    except:
        return None


def set_custom_field(card_id, field_id, value):
    url = f"https://api.trello.com/1/cards/{card_id}/customField/{field_id}/item"
    requests.put(url, params={'key': TRELLO_KEY, 'token': TRELLO_TOKEN},
                 json={'value': {'text': value}}, timeout=10)


# ===== Trello Data =====

def get_cards(list_name, marker):
    """Get cards from a list and parse JSON data after marker."""
    cards = trello_api('GET', f'lists/{LISTS[list_name]}/cards') or []
    results = []
    for card in cards:
        if marker in card.get('desc', ''):
            try:
                data = json.loads(card['desc'].split(marker)[1].strip())
                data.update(card_id=card['id'], name=card['name'], due=card.get('due'))
                results.append(data)
            except:
                pass
    return results


def get_contacts():
    return get_cards('contacts', '---CONTACT---')


def get_groups():
    return get_cards('groups', '---GROUP---')


def get_admins():
    return [c.get('user_id') for c in get_cards('admins', '---CONTACT---') if c.get('user_id')]


def get_scheduled():
    return get_cards('scheduled', '---SCHEDULED_MESSAGE---')


# ===== Contact Management =====

def find_contact(name):
    """Find contact or group. Returns: dict (found), list (candidates), or None."""
    contacts = get_contacts()
    groups = get_groups()
    all_targets = contacts + groups

    if not all_targets:
        return None

    name_lower = name.lower().strip()

    # Exact/partial match
    for c in all_targets:
        cn = c.get('name', '').lower()
        ln = c.get('line_name', c.get('group_name', '')).lower()
        if name_lower in (cn, ln) or name_lower in cn or name_lower in ln or cn in name_lower:
            return c

    # Fuzzy match
    candidates = []
    for c in all_targets:
        cn = c.get('name', '').lower()
        ln = c.get('line_name', c.get('group_name', '')).lower()
        score = max(fuzz.partial_ratio(name_lower, cn), fuzz.partial_ratio(name_lower, ln))
        if score >= 50:
            candidates.append((score, c))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1] if candidates[0][0] >= 80 else [c[1] for c in candidates[:5]]
    return None


def find_contact_ai(name, contacts):
    """Use AI to find contact."""
    if not gemini_model or not contacts:
        return None
    try:
        names = "\n".join(f"- {c.get('name', '')} ({c.get('line_name', '')})" for c in contacts)
        r = gemini_model.generate_content(f"從清單找「{name}」，只回覆名字或「找不到」：\n{names}").text.strip()
        if r != "找不到":
            for c in contacts:
                if r in c.get('name', '') or r in c.get('line_name', ''):
                    return c
    except:
        pass
    return None


def auto_register(user_id):
    """Auto register user as contact. Skip if profile unavailable."""
    contacts = get_contacts()
    if any(c.get('user_id') == user_id for c in contacts):
        return

    profile = line_api('GET', f'profile/{user_id}')
    if not profile or not profile.get('displayName'):
        return

    name = profile['displayName']
    data = {'user_id': user_id, 'line_name': name, 'created_at': datetime.now(TW_TZ).isoformat()}
    card = trello_api('POST', 'cards', idList=LISTS['contacts'], name=name,
                      desc=f"---CONTACT---\n{json.dumps(data, ensure_ascii=False)}", pos='bottom')

    if card and card.get('id'):
        set_custom_field(card['id'], CUSTOM_FIELD_CONTACT, name)


def auto_register_group(group_id):
    """Auto register group when bot joins."""
    print(f"auto_register_group called with: {group_id}", flush=True)
    groups = get_groups()
    print(f"Existing groups: {len(groups)}", flush=True)
    if any(g.get('group_id') == group_id for g in groups):
        print(f"Group already exists, skipping", flush=True)
        return

    summary = line_api('GET', f'group/{group_id}/summary')
    print(f"Group summary: {summary}", flush=True)
    group_name = summary.get('groupName', '未命名群組') if summary else '未命名群組'

    data = {'group_id': group_id, 'group_name': group_name, 'created_at': datetime.now(TW_TZ).isoformat()}
    result = trello_api('POST', 'cards', idList=LISTS['groups'], name=f"👥 {group_name}",
               desc=f"---GROUP---\n{json.dumps(data, ensure_ascii=False)}", pos='bottom')
    print(f"Trello card created: {result}", flush=True)
