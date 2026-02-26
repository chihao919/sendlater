"""
SendLater - LINE Bot for Scheduled Messages
"""
import os, re, json, hashlib, hmac, base64, requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort, jsonify
import google.generativeai as genai
from rapidfuzz import fuzz

app = Flask(__name__)

# Config
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
TRELLO_KEY = os.environ.get('TRELLO_API_KEY', '')
TRELLO_TOKEN = os.environ.get('TRELLO_TOKEN', '')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
CRON_SECRET = os.environ.get('CRON_SECRET', '')

# Trello List IDs
LISTS = {
    'scheduled': os.environ.get('TRELLO_SCHEDULED_LIST_ID', '6977369f93d182d2298e671f'),
    'contacts': os.environ.get('TRELLO_CONTACTS_LIST_ID', '69773964fa6f1fe4ff71c21b'),
    'sent': os.environ.get('TRELLO_SENT_LIST_ID', '697742862d609f8dd32aff23'),
    'admins': os.environ.get('TRELLO_ADMINS_LIST_ID', '69775e7a019120099baed077'),
    'groups': os.environ.get('TRELLO_GROUPS_LIST_ID', '697981e5eca68db4fe8c3586'),
}

# Init Gemini
gemini = genai.GenerativeModel('gemini-2.0-flash') if GEMINI_KEY else None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

TW_TZ = timezone(timedelta(hours=8))

PROMPT = """你是 SendLater 排程訊息助手。用 JSON 回覆：
- schedule_message: {{"action":"schedule_message","recipient":"名字","message":"內容"}}
- list_contacts: {{"action":"list_contacts"}}
- list_scheduled: {{"action":"list_scheduled"}}
- cancel_last: {{"action":"cancel_last"}}
- chat: {{"action":"chat","reply":"回覆"}}
只回覆 JSON。時間：{time}"""


# ===== API Helpers =====

def line_api(method, endpoint, data=None):
    url = f'https://api.line.me/v2/bot/{endpoint}'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'}
    try:
        r = requests.request(method, url, headers=headers, json=data, timeout=10)
        return r.json() if r.text and r.status_code == 200 else r.status_code == 200
    except:
        return None

def trello_api(method, endpoint, **params):
    url = f"https://api.trello.com/1/{endpoint}"
    params.update(key=TRELLO_KEY, token=TRELLO_TOKEN)
    try:
        r = requests.request(method, url, params=params, timeout=10)
        r.raise_for_status()
        return r.json() if r.text else {}
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
    if not gemini or not contacts:
        return None
    try:
        names = "\n".join(f"- {c.get('name', '')} ({c.get('line_name', '')})" for c in contacts)
        r = gemini.generate_content(f"從清單找「{name}」，只回覆名字或「找不到」：\n{names}").text.strip()
        if r != "找不到":
            for c in contacts:
                if r in c.get('name', '') or r in c.get('line_name', ''):
                    return c
    except:
        pass
    return None

CUSTOM_FIELD_CONTACT = os.environ.get('TRELLO_CUSTOM_FIELD_CONTACT', '697737cc9cb876d6ede390e4')

def set_custom_field(card_id, field_id, value):
    """Set a custom field value on a card."""
    url = f"https://api.trello.com/1/cards/{card_id}/customField/{field_id}/item"
    requests.put(url, params={'key': TRELLO_KEY, 'token': TRELLO_TOKEN},
                 json={'value': {'text': value}}, timeout=10)

def auto_register(user_id):
    """Auto register user as contact. Skip if profile unavailable."""
    contacts = get_contacts()
    if any(c.get('user_id') == user_id for c in contacts):
        return

    profile = line_api('GET', f'profile/{user_id}')
    if not profile or not profile.get('displayName'):
        return  # Skip if can't get profile (e.g. user not friend with bot)

    name = profile['displayName']
    data = {'user_id': user_id, 'line_name': name, 'created_at': datetime.now(TW_TZ).isoformat()}
    card = trello_api('POST', 'cards', idList=LISTS['contacts'], name=name,
                      desc=f"---CONTACT---\n{json.dumps(data, ensure_ascii=False)}", pos='bottom')

    # Set custom field
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

    # Get group summary (name)
    summary = line_api('GET', f'group/{group_id}/summary')
    print(f"Group summary: {summary}", flush=True)
    group_name = summary.get('groupName', '未命名群組') if summary else '未命名群組'

    data = {'group_id': group_id, 'group_name': group_name, 'created_at': datetime.now(TW_TZ).isoformat()}
    result = trello_api('POST', 'cards', idList=LISTS['groups'], name=f"👥 {group_name}",
               desc=f"---GROUP---\n{json.dumps(data, ensure_ascii=False)}", pos='bottom')
    print(f"Trello card created: {result}", flush=True)


# ===== Actions =====

def action_help():
    return """📨 SendLater

• 「發給小明：記得開會」
• 「聯絡人」「排程」「取消」

讓朋友傳訊息給我就會自動記住！"""

def action_contacts():
    contacts = get_contacts()
    if not contacts:
        return "📇 目前沒有聯絡人"
    lines = [f"📇 聯絡人 ({len(contacts)} 人)\n"]
    lines += [f"{i}. {c.get('name', '?')}" for i, c in enumerate(contacts[:15], 1)]
    return "\n".join(lines)

def action_scheduled():
    msgs = get_scheduled()
    if not msgs:
        return "📤 沒有排程中的訊息"
    lines = [f"📤 排程 ({len(msgs)} 則)\n"]
    for i, m in enumerate(msgs[:10], 1):
        due = m.get('due', '')
        try:
            due = datetime.fromisoformat(due.replace('Z', '+00:00')).astimezone(TW_TZ).strftime('%m/%d %H:%M')
        except:
            due = '?'
        lines.append(f"{i}. → {m.get('recipient_name', '?')}：{m.get('message', '')[:15]}... ({due})")
    return "\n".join(lines)

def action_cancel(user_id):
    if user_id not in get_admins():
        return "⚠️ 只有管理員可以取消"

    msgs = [m for m in get_scheduled() if m.get('sender_user_id') == user_id]
    if not msgs:
        return "❌ 沒有可取消的排程"

    msgs.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    trello_api('DELETE', f"cards/{msgs[0]['card_id']}")
    return f"✅ 已取消：{msgs[0].get('recipient_name', '?')} - {msgs[0].get('message', '')[:20]}..."

def action_schedule(parsed, user_id):
    if user_id not in get_admins():
        return "⚠️ 只有管理員可以排程"

    recipient, message = parsed.get('recipient', ''), parsed.get('message', '')
    if not recipient or not message:
        return "❌ 範例：發給小明：記得開會"

    contact = find_contact(recipient)
    ai_match = False

    # Multiple candidates - show buttons
    if isinstance(contact, list):
        return {
            'text': f"🔍 「{recipient}」有多個可能：",
            'quick_reply': [{'label': c.get('name', '')[:20], 'text': f"發給 {c.get('name', '')}：{message}"}
                          for c in contact] + [{'label': '❌ 取消', 'text': '取消'}]
        }

    # Try AI if not found (only for contacts, not groups)
    if not contact:
        contact = find_contact_ai(recipient, get_contacts() + get_groups())
        ai_match = bool(contact)

    if not contact:
        return f"❌ 找不到「{recipient}」\n\n輸入「聯絡人」查看名單"

    # Check if it's a group or contact
    is_group = 'group_id' in contact

    # Create scheduled message
    send_time = datetime.now(TW_TZ).replace(hour=9, minute=0, second=0) + timedelta(days=1)
    if parsed.get('send_time'):
        try:
            send_time = datetime.fromisoformat(parsed['send_time'])
        except:
            pass

    data = {
        'recipient_name': contact.get('name', recipient),
        'recipient_id': contact.get('group_id') if is_group else contact.get('user_id'),
        'recipient_type': 'group' if is_group else 'user',
        'sender_user_id': user_id,
        'message': message,
        'created_at': datetime.now(TW_TZ).isoformat()
    }
    icon = "👥" if is_group else "📨"
    card_name = f"{icon} {contact.get('name', recipient)}：{message[:30]}"
    card = trello_api('POST', 'cards', idList=LISTS['scheduled'], name=card_name,
                      desc=f"---SCHEDULED_MESSAGE---\n{json.dumps(data, ensure_ascii=False)}",
                      due=send_time.isoformat(), pos='bottom')

    # Set contact custom field
    if card and card.get('id'):
        set_custom_field(card['id'], CUSTOM_FIELD_CONTACT, contact.get('name', recipient))

    ai_hint = "\n🤖 AI 判斷" if ai_match else ""
    target_icon = "👥" if is_group else "👤"
    return {
        'text': f"✅ 已排程{ai_hint}\n\n{target_icon} {contact.get('name')}\n📝 {message}\n⏰ {send_time.strftime('%m/%d %H:%M')}",
        'quick_reply': [{'label': '❌ 取消', 'text': '取消'}]
    }


# ===== Main Processing =====

ACTIONS = {
    'list_contacts': lambda p, u: action_contacts(),
    'list_scheduled': lambda p, u: action_scheduled(),
    'cancel_last': lambda p, u: action_cancel(u),
    'schedule_message': action_schedule,
    'chat': lambda p, u: p.get('reply', '你好！'),
    'help': lambda p, u: action_help(),
}

QUICK_CMDS = {
    r'^(help|幫助|\?)$': 'help',
    r'^(contacts|聯絡人|通訊錄)$': 'list_contacts',
    r'^(scheduled|排程|排程訊息)$': 'list_scheduled',
    r'^(cancel|取消|不對|錯了)$': 'cancel_last',
}

def process(text, user_id):
    text = text.strip()

    # Quick commands
    for pattern, action in QUICK_CMDS.items():
        if re.match(pattern, text.lower()):
            return ACTIONS[action]({}, user_id)

    # Gemini parse
    if gemini:
        try:
            prompt = PROMPT.format(time=datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M')) + f"\n\n{text}"
            result = gemini.generate_content(prompt).text.strip()
            if result.startswith('```'):
                result = re.sub(r'^```(?:json)?\n?|\n?```$', '', result)
            parsed = json.loads(result)
            action = parsed.get('action')
            if action in ACTIONS:
                return ACTIONS[action](parsed, user_id)
        except:
            pass

    return action_help()


# ===== Routes =====

@app.route("/")
def index():
    return "SendLater 📨"

@app.route("/webhook", methods=['POST'])
def webhook():
    sig = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    expected = base64.b64encode(hmac.new(LINE_SECRET.encode(), body.encode(), hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(sig, expected):
        abort(400)

    try:
        events = json.loads(body).get('events', [])
        print(f"Received {len(events)} events: {[e.get('type') for e in events]}", flush=True)

        for event in events:
            event_type = event.get('type')
            source = event.get('source', {})
            source_type = source.get('type', '')
            print(f"Event: {event_type}, Source: {source_type}, Full: {json.dumps(source)}", flush=True)

            # Handle bot join group
            if event_type == 'join' and source_type == 'group':
                group_id = source.get('groupId', '')
                print(f"JOIN EVENT - Group ID: {group_id}", flush=True)
                if group_id:
                    try:
                        auto_register_group(group_id)
                        print(f"Group registered successfully", flush=True)
                    except Exception as e:
                        print(f"Error registering group: {e}", flush=True)
                continue

            # Handle messages
            if event_type == 'message' and event.get('message', {}).get('type') == 'text':
                token = event.get('replyToken')
                text = event.get('message', {}).get('text', '').strip()
                user_id = source.get('userId', '')

                # Auto register contact
                if user_id:
                    auto_register(user_id)

                # Only reply in private chat (not in groups/rooms)
                if source_type == 'user' and token and text:
                    response = process(text, user_id)
                    if isinstance(response, dict):
                        reply(token, response['text'], response.get('quick_reply'))
                    else:
                        reply(token, response)

    except Exception as e:
        import traceback
        print(f"Error: {e}", flush=True)
        traceback.print_exc()

    return 'OK'

@app.route("/api/cron/send", methods=['GET', 'POST'])
def cron_send():
    auth = request.headers.get('Authorization', '')
    if CRON_SECRET and auth != f'Bearer {CRON_SECRET}' and request.args.get('secret') != CRON_SECRET:
        abort(401)

    now = datetime.now(timezone.utc)
    sent = 0

    for msg in get_scheduled():
        due = msg.get('due')
        if due and datetime.fromisoformat(due.replace('Z', '+00:00')) <= now:
            # Get sender name
            sender = next((c for c in get_contacts() if c.get('user_id') == msg.get('sender_user_id')), {})
            sender_name = sender.get('name', '某人')

            # Get recipient ID (support both old and new format)
            recipient_id = msg.get('recipient_id') or msg.get('recipient_user_id')

            if push(recipient_id, f"📬 來自 {sender_name}：\n\n{msg['message']}"):
                sent += 1
                trello_api('PUT', f"cards/{msg['card_id']}", idList=LISTS['sent'])
                push(msg['sender_user_id'], f"✅ 已發送給 {msg['recipient_name']}\n\n📝 {msg['message']}")

    return jsonify({'status': 'success', 'sent': sent, 'time': datetime.now(TW_TZ).isoformat()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
