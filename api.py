"""
SendLater - LINE API, Trello API, and data access layer.
"""
import json
import base64
import requests
from datetime import datetime
from rapidfuzz import fuzz

from config import (
    LINE_TOKEN, TRELLO_KEY, TRELLO_TOKEN, LISTS,
    CUSTOM_FIELD_CONTACT, TW_TZ, gemini_model,
    gs_client, INVOICE_SHEET_ID,
)


# ===== LINE API =====

def line_api(method, endpoint, data=None, return_json=False):
    url = f'https://api.line.me/v2/bot/{endpoint}'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'}
    try:
        r = requests.request(method, url, headers=headers, json=data, timeout=10)
        if r.status_code != 200:
            print(f"LINE API error: {method} {endpoint} → {r.status_code} {r.text[:200]}", flush=True)
            return None
        if return_json and r.text:
            return r.json()
        return True
    except Exception as e:
        print(f"LINE API exception: {method} {endpoint} → {e}", flush=True)
        return None


def reply(token, text, quick_reply=None):
    msg = {'type': 'text', 'text': text}
    if quick_reply:
        msg['quickReply'] = {'items': [
            {'type': 'action', 'action': {'type': 'message', 'label': q['label'][:20], 'text': q['text']}}
            for q in quick_reply
        ]}
    return line_api('POST', 'message/reply', {'replyToken': token, 'messages': [msg]})


def reply_flex(token, flex_messages):
    """Reply with one or more Flex Messages."""
    messages = []
    for fm in flex_messages:
        messages.append({'type': 'flex', 'altText': fm.get('altText', '發票辨識結果'), 'contents': fm['contents']})
    return line_api('POST', 'message/reply', {'replyToken': token, 'messages': messages[:5]})


def push(user_id, text):
    return line_api('POST', 'message/push', {'to': user_id, 'messages': [{'type': 'text', 'text': text}]})


def push_flex(user_id, flex_messages):
    """Push one or more Flex Messages."""
    messages = []
    for fm in flex_messages:
        messages.append({'type': 'flex', 'altText': fm.get('altText', '發票辨識結果'), 'contents': fm['contents']})
    return line_api('POST', 'message/push', {'to': user_id, 'messages': messages[:5]})


def get_line_image(message_id):
    """Download image from LINE Content API. Returns image bytes or None."""
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    headers = {'Authorization': f'Bearer {LINE_TOKEN}'}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.content
        print(f"LINE image download failed: {r.status_code}", flush=True)
    except Exception as e:
        print(f"LINE image download error: {e}", flush=True)
    return None


def build_invoice_flex(invoice, index):
    """Build a Flex Message card for one invoice result.

    Args:
        invoice: dict with invoice fields from Gemini Vision
        index: invoice index (for postback data identification)

    Returns:
        dict with 'altText' and 'contents' for Flex Message
    """
    date = invoice.get('date', '—')
    vendor = invoice.get('vendor', '—')
    currency = invoice.get('currency', 'TWD')
    department = invoice.get('department', '—')
    total = invoice.get('total', 0)
    tax = invoice.get('tax', 0)
    subtotal = invoice.get('subtotal_before_tax', 0)

    # Build items text
    items = invoice.get('items', [])
    items_text = ""
    if items:
        lines = []
        for item in items:
            name = item.get('name', '')
            qty = item.get('quantity', 1)
            price = item.get('unit_price', 0)
            lines.append(f"{name} x{qty}  ${price:,.0f}")
        items_text = "\n".join(lines)
    else:
        items_text = "—"

    # Encode invoice data for postback
    invoice_json = json.dumps(invoice, ensure_ascii=False)
    encoded = base64.urlsafe_b64encode(invoice_json.encode()).decode()

    # If encoded data > 250 bytes, truncate items for postback but keep display
    postback_data_betty = f"invoice_confirm&payer=Betty&idx={index}&data={encoded}"
    postback_data_chihao = f"invoice_confirm&payer=Chihao&idx={index}&data={encoded}"
    postback_data_entertainment = f"invoice_confirm&payer=交際費&idx={index}&data={encoded}"

    # If postback exceeds 300 bytes, use simplified data
    if len(postback_data_betty.encode()) > 300:
        short = json.dumps({
            'd': date, 'v': vendor, 'c': currency, 't': total,
            'tax': tax, 'st': subtotal, 'dep': department,
            'at': invoice.get('account_target', ''),
        }, ensure_ascii=False)
        encoded = base64.urlsafe_b64encode(short.encode()).decode()
        postback_data_betty = f"invoice_confirm&payer=Betty&idx={index}&data={encoded}"
        postback_data_chihao = f"invoice_confirm&payer=Chihao&idx={index}&data={encoded}"
        postback_data_entertainment = f"invoice_confirm&payer=交際費&idx={index}&data={encoded}"

    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "📄 發票辨識結果", "weight": "bold", "size": "lg"},
                {"type": "separator", "margin": "md"},
                {"type": "box", "layout": "vertical", "margin": "md", "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "日期", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": str(date), "size": "sm", "flex": 5},
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "店家", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": str(vendor), "size": "sm", "flex": 5},
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "品項", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": items_text, "size": "sm", "flex": 5, "wrap": True},
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "總金額", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": f"${total:,.0f}", "size": "sm", "weight": "bold", "flex": 5},
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "部門", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": str(department), "size": "sm", "flex": 5},
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "幣別", "size": "sm", "color": "#888888", "flex": 2},
                        {"type": "text", "text": str(currency), "size": "sm", "flex": 5},
                    ]},
                ]},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "color": "#4A90D9",
                    "action": {"type": "postback", "label": "Betty墊", "data": postback_data_betty},
                },
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "color": "#50C878",
                    "action": {"type": "postback", "label": "Chihao墊", "data": postback_data_chihao},
                },
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "color": "#FF6B6B",
                    "action": {"type": "postback", "label": "交際費", "data": postback_data_entertainment},
                },
            ],
        },
    }

    return {
        "altText": f"發票：{vendor} ${total:,.0f}",
        "contents": contents,
    }


def build_continue_or_end_flex():
    """Build a Flex Message asking user to continue uploading or end."""
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "✅ 辨識完成", "weight": "bold", "size": "md"},
                {"type": "text", "text": "繼續上傳發票照片，或按結束離開記帳模式", "size": "sm", "color": "#888888", "margin": "md", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "action": {"type": "postback", "label": "繼續上傳", "data": "invoice_continue"},
                },
                {
                    "type": "button", "style": "secondary", "height": "sm",
                    "action": {"type": "postback", "label": "結束", "data": "invoice_end"},
                },
            ],
        },
    }
    return {"altText": "繼續上傳或結束", "contents": contents}


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
    print(f"find_contact({repr(name)}): {len(contacts)} contacts, {len(groups)} groups", flush=True)

    if not all_targets:
        return None

    name_lower = name.lower().strip()
    # Also try without spaces (e.g. "W P A" → "wpa")
    name_nospace = name_lower.replace(' ', '')
    print(f"find_contact: name_lower={repr(name_lower)}, name_nospace={repr(name_nospace)}", flush=True)

    # Exact/partial match
    for c in all_targets:
        cn = c.get('name', '').lower()
        ln = c.get('line_name', c.get('group_name', '')).lower()
        cn_nospace = cn.replace(' ', '')
        ln_nospace = ln.replace(' ', '')
        if (name_lower in (cn, ln) or name_lower in cn or name_lower in ln or cn in name_lower
                or name_nospace in (cn_nospace, ln_nospace) or name_nospace in cn_nospace or name_nospace in ln_nospace):
            print(f"find_contact: exact match → {c.get('name')} (cn={repr(cn)}, ln={repr(ln)})", flush=True)
            return c

    # Fuzzy match (use both original and no-space versions)
    candidates = []
    for c in all_targets:
        cn = c.get('name', '').lower()
        ln = c.get('line_name', c.get('group_name', '')).lower()
        score = max(
            fuzz.partial_ratio(name_lower, cn),
            fuzz.partial_ratio(name_lower, ln),
            fuzz.partial_ratio(name_nospace, cn.replace(' ', '')),
            fuzz.partial_ratio(name_nospace, ln.replace(' ', '')),
        )
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

    profile = line_api('GET', f'profile/{user_id}', return_json=True)
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

    summary = line_api('GET', f'group/{group_id}/summary', return_json=True)
    print(f"Group summary: {summary}", flush=True)
    group_name = summary.get('groupName', '未命名群組') if summary else '未命名群組'

    data = {'group_id': group_id, 'group_name': group_name, 'created_at': datetime.now(TW_TZ).isoformat()}
    result = trello_api('POST', 'cards', idList=LISTS['groups'], name=f"👥 {group_name}",
               desc=f"---GROUP---\n{json.dumps(data, ensure_ascii=False)}", pos='bottom')
    print(f"Trello card created: {result}", flush=True)


# ===== Google Sheets =====

def write_invoice_to_sheets(invoice, payer, is_entertainment=False):
    """Write invoice data to Google Sheets.

    Args:
        invoice: dict with invoice fields
        payer: 'Betty', 'Chihao', or '交際費'
        is_entertainment: True if 交際費 button was pressed

    Returns:
        True on success, False on failure
    """
    if not gs_client or not INVOICE_SHEET_ID:
        print("Google Sheets not configured", flush=True)
        return False

    try:
        sheet = gs_client.open_by_key(INVOICE_SHEET_ID).sheet1

        # Build items description
        items_desc = ""
        if not is_entertainment:
            items = invoice.get('items', [])
            if items:
                lines = [f"{it.get('name', '')} x{it.get('quantity', 1)} ${it.get('unit_price', 0)}" for it in items]
                items_desc = "; ".join(lines)

        row = [
            invoice.get('date', ''),                    # A: 日期
            invoice.get('vendor', ''),                   # B: 店家/供應商
            items_desc,                                  # C: 品項描述
            invoice.get('currency', 'TWD'),               # D: 幣別
            invoice.get('exchange_rate', '') or '',        # E: 匯率
            invoice.get('items', [{}])[0].get('quantity', 1) if not is_entertainment and invoice.get('items') else '',  # F: 數量
            invoice.get('items', [{}])[0].get('unit_price', '') if not is_entertainment and invoice.get('items') else '',  # G: 單價
            invoice.get('subtotal_before_tax', ''),       # H: 未稅金額
            invoice.get('tax', ''),                       # I: 稅金
            invoice.get('total', 0),                      # J: 小計
            invoice.get('department', ''),                 # K: 部門
            payer if not is_entertainment else '',         # L: 墊付人
            'Betty' if is_entertainment else invoice.get('account_target', ''),  # M: 帳款對象
            '',                                           # N: 帳款類別 (reserved)
        ]

        sheet.append_row(row, value_input_option='USER_ENTERED')
        print(f"Invoice written to Sheets: {invoice.get('vendor', '')} ${invoice.get('total', 0)}", flush=True)
        return True
    except Exception as e:
        print(f"Sheets write error: {e}", flush=True)
        return False
