"""
SendLater - Action functions for handling user commands.
"""
import json
from datetime import datetime, timedelta

from config import LISTS, CUSTOM_FIELD_CONTACT, TW_TZ
from api import (
    trello_api, set_custom_field,
    get_contacts, get_groups, get_admins, get_scheduled,
    find_contact, find_contact_ai,
)


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

    # Try AI if not found
    if not contact:
        contact = find_contact_ai(recipient, get_contacts() + get_groups())
        ai_match = bool(contact)

    if not contact:
        return f"❌ 找不到「{recipient}」\n\n輸入「聯絡人」查看名單"

    is_group = 'group_id' in contact

    # Determine send time
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
    display_name = contact.get('group_name', contact.get('name', recipient)) if is_group else contact.get('name', recipient)
    card_name = f"{icon} {display_name}：{message[:30]}"
    card = trello_api('POST', 'cards', idList=LISTS['scheduled'], name=card_name,
                      desc=f"---SCHEDULED_MESSAGE---\n{json.dumps(data, ensure_ascii=False)}",
                      due=send_time.isoformat(), pos='bottom')

    if card and card.get('id'):
        set_custom_field(card['id'], CUSTOM_FIELD_CONTACT, contact.get('name', recipient))

    ai_hint = "\n🤖 AI 判斷" if ai_match else ""
    target_icon = "👥" if is_group else "👤"
    return {
        'text': f"✅ 已排程{ai_hint}\n\n{target_icon} {contact.get('name')}\n📝 {message}\n⏰ {send_time.strftime('%m/%d %H:%M')}",
        'quick_reply': [{'label': '❌ 取消', 'text': '取消'}]
    }


# Action dispatch table
ACTIONS = {
    'list_contacts': lambda p, u: action_contacts(),
    'list_scheduled': lambda p, u: action_scheduled(),
    'cancel_last': lambda p, u: action_cancel(u),
    'schedule_message': action_schedule,
    'chat': lambda p, u: p.get('reply', '你好！'),
    'help': lambda p, u: action_help(),
}

# Quick command patterns
QUICK_CMDS = {
    r'^(help|幫助|\?)$': 'help',
    r'^(contacts|聯絡人|通訊錄)$': 'list_contacts',
    r'^(scheduled|排程|排程訊息)$': 'list_scheduled',
    r'^(cancel|取消|不對|錯了)$': 'cancel_last',
}
