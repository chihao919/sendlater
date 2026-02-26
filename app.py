"""
SendLater - LINE Bot for Scheduled Messages
"""
import re
import json
import hashlib
import hmac
import base64
from datetime import datetime, timezone

from flask import Flask, request, abort, jsonify

from config import LINE_SECRET, CRON_SECRET, LISTS, TW_TZ, gemini_model
from api import reply, push, trello_api, get_contacts, get_scheduled, auto_register, auto_register_group
from parser import parse_message
from actions import ACTIONS, QUICK_CMDS, action_help

app = Flask(__name__)


# ===== Main Processing =====

def process(text, user_id):
    text = text.strip()

    # Quick commands
    for pattern, action in QUICK_CMDS.items():
        if re.match(pattern, text.lower()):
            return ACTIONS[action]({}, user_id)

    # AI parse
    now = datetime.now(TW_TZ)
    parsed = parse_message(text, now, gemini_model)
    if parsed and parsed.get('action') in ACTIONS:
        return ACTIONS[parsed['action']](parsed, user_id)

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
            sender = next((c for c in get_contacts() if c.get('user_id') == msg.get('sender_user_id')), {})
            sender_name = sender.get('name', '某人')

            recipient_id = msg.get('recipient_id') or msg.get('recipient_user_id')

            if push(recipient_id, f"📬 來自 {sender_name}：\n\n{msg['message']}"):
                sent += 1
                trello_api('PUT', f"cards/{msg['card_id']}", idList=LISTS['sent'])
                push(msg['sender_user_id'], f"✅ 已發送給 {msg['recipient_name']}\n\n📝 {msg['message']}")

    return jsonify({'status': 'success', 'sent': sent, 'time': datetime.now(TW_TZ).isoformat()})


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
