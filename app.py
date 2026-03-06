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
from api import (
    reply, reply_flex, push, push_flex, trello_api,
    get_contacts, get_scheduled, auto_register, auto_register_group,
    get_line_image, build_invoice_flex, build_continue_or_end_flex,
)
from parser import parse_message, parse_invoice_image
from actions import ACTIONS, QUICK_CMDS, action_help

app = Flask(__name__)


# ===== Main Processing =====

def process(text, user_id):
    text = text.strip()

    # Quick commands
    for pattern, action in QUICK_CMDS.items():
        if re.match(pattern, text.lower()):
            if action == 'start_invoice':
                return "📸 記帳功能\n\n直接傳送發票照片即可（一張照片可包含多張發票）"
            return ACTIONS[action]({}, user_id)

    # AI parse
    now = datetime.now(TW_TZ)
    parsed = parse_message(text, now, gemini_model)
    if parsed and parsed.get('action') in ACTIONS:
        return ACTIONS[parsed['action']](parsed, user_id)

    return action_help()


def handle_invoice_image(token, user_id, message_id):
    """Handle invoice image: download, recognize, reply with cards."""
    image_bytes = get_line_image(message_id)
    if not image_bytes:
        reply(token, "❌ 無法下載圖片，請重新傳送")
        return

    invoices = parse_invoice_image(image_bytes)
    if not invoices:
        reply(token, "❌ 無法辨識發票，請確認照片清晰後重新上傳")
        return

    # Build flex cards for each invoice + continue/end card
    flex_messages = []
    for i, inv in enumerate(invoices):
        flex_messages.append(build_invoice_flex(inv, i))

    flex_messages.append(build_continue_or_end_flex())

    # LINE allows max 5 messages per reply
    reply_flex(token, flex_messages[:5])

    # If more than 4 invoices, push remaining via push
    if len(flex_messages) > 5:
        push_flex(user_id, flex_messages[5:10])


def handle_invoice_confirm(token, user_id, postback_data):
    """Handle invoice confirm button press: write to Google Sheets."""
    from api import write_invoice_to_sheets

    try:
        # Parse postback data: invoice_confirm&payer=Betty&idx=0&data=base64
        params = {}
        for part in postback_data.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = v

        payer = params.get('payer', '')
        encoded_data = params.get('data', '')
        invoice = json.loads(base64.urlsafe_b64decode(encoded_data).decode())

        # Handle shortened keys
        if 'd' in invoice and 'date' not in invoice:
            invoice = {
                'date': invoice.get('d', ''),
                'vendor': invoice.get('v', ''),
                'currency': invoice.get('c', 'TWD'),
                'total': invoice.get('t', 0),
                'tax': invoice.get('tax', 0),
                'subtotal_before_tax': invoice.get('st', 0),
                'department': invoice.get('dep', ''),
                'account_target': invoice.get('at', ''),
                'items': [],
            }

        is_entertainment = (payer == '交際費')

        success = write_invoice_to_sheets(invoice, payer, is_entertainment)
        if success:
            if is_entertainment:
                reply(token, f"✅ 已記錄交際費 ${invoice.get('total', 0):,.0f}")
            else:
                reply(token, f"✅ 已記錄 {invoice.get('vendor', '')} ${invoice.get('total', 0):,.0f}（{payer}墊）")
        else:
            reply(token, "❌ 記錄失敗，請稍後再試")
    except Exception as e:
        print(f"Invoice confirm error: {e}", flush=True)
        reply(token, "❌ 記錄失敗，請稍後再試")


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

            # Handle postback events (button presses)
            if event_type == 'postback':
                token = event.get('replyToken')
                user_id = source.get('userId', '')
                postback_data = event.get('postback', {}).get('data', '')
                print(f"Postback: {postback_data[:100]}", flush=True)

                if postback_data == 'invoice_continue':
                    reply(token, "📸 請繼續傳送發票照片")
                elif postback_data == 'invoice_end':
                    reply(token, "✅ 記帳完成")
                elif postback_data.startswith('invoice_confirm'):
                    handle_invoice_confirm(token, user_id, postback_data)
                continue

            # Handle messages
            if event_type == 'message':
                token = event.get('replyToken')
                msg = event.get('message', {})
                msg_type = msg.get('type')
                user_id = source.get('userId', '')

                # Auto register contact
                if user_id:
                    auto_register(user_id)

                # Only reply in private chat
                if source_type != 'user' or not token:
                    continue

                # Handle image message — always process as invoice in private chat
                if msg_type == 'image':
                    print(f"Image received, msg_id={msg.get('id')}", flush=True)
                    handle_invoice_image(token, user_id, msg.get('id'))
                    continue

                # Handle text message
                if msg_type == 'text':
                    text = msg.get('text', '').strip()
                    if text:
                        response = process(text, user_id)
                        if isinstance(response, dict):
                            if response.get('flex'):
                                reply_flex(token, response['flex'])
                            else:
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
