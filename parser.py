"""
SendLater - AI intent parsing and time expression parsing.

Strategy: Gemini handles intent classification + raw field extraction only.
Python handles time expression parsing for reliability.
"""
import re
import json
from datetime import datetime, timedelta

from config import TW_TZ, gemini_model

# Simplified prompt: AI only classifies intent and extracts raw fields
PROMPT = """你是 SendLater 排程訊息助手。只回覆一個扁平 JSON 物件（不要巢狀）。

回覆格式範例：
{{"action":"schedule_message","recipient":"小明","message":"記得開會","time_expression":"明天下午六點"}}
{{"action":"schedule_message","recipient":"Betty","message":"記得開會","time_expression":"3/5 18:00"}}
{{"action":"list_contacts"}}
{{"action":"list_scheduled"}}
{{"action":"cancel_last"}}
{{"action":"chat","reply":"你好！"}}

規則：
- 只回覆一個 JSON 物件，不要 markdown 包裝
- time_expression：直接提取使用者說的時間原文（例如「明天下午六點」「3/5 18:00」「下週三早上」）
- 如果使用者沒提到時間，不要加 time_expression 欄位
- 不需要自己解析時間，直接放原文
- 現在時間：{time}"""


def _clean_ai_response(text):
    """Clean markdown wrapping and handle nested JSON from Gemini response."""
    text = text.strip()

    # Remove markdown code blocks
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\n?|\n?```$', '', text).strip()

    parsed = json.loads(text)

    # If parsed is a list (e.g. invoice array), return as-is
    if isinstance(parsed, list):
        return parsed

    # Handle nested JSON (e.g. {"schedule_message": {"action": ...}})
    if not parsed.get('action'):
        for v in parsed.values():
            if isinstance(v, dict) and v.get('action'):
                return v
    return parsed


def _parse_time_expression(expression, now):
    """Parse Chinese/English time expressions into datetime.

    Supported formats:
    - Relative days: 明天, 後天, 大後天, 今天
    - Weekdays: 下週一~日, 週一~日, 星期一~日
    - Time of day: 早上/上午, 中午, 下午/晚上 + N點/N:MM
    - Absolute: M/D, M月D日, YYYY-M-D
    - ISO format: 2026-03-01T18:00:00+08:00
    - Combined: 明天下午六點, 3/5 18:00
    """
    if not expression:
        return None

    expression = expression.strip()

    # Try ISO format first
    try:
        return datetime.fromisoformat(expression)
    except (ValueError, TypeError):
        pass

    # Start with now, default time is 9:00
    result_date = None
    result_hour = None
    result_minute = 0

    # Chinese number mapping
    cn_nums = {'一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5,
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
               '十一': 11, '十二': 12}

    def cn_to_num(s):
        if s in cn_nums:
            return cn_nums[s]
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    # === Parse date part ===

    # Relative days
    if '大後天' in expression:
        result_date = now.date() + timedelta(days=3)
    elif '後天' in expression:
        result_date = now.date() + timedelta(days=2)
    elif '明天' in expression:
        result_date = now.date() + timedelta(days=1)
    elif '今天' in expression or '今晚' in expression:
        result_date = now.date()

    # Weekday: 下週X, 週X, 星期X
    weekday_map = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
    wk_match = re.search(r'(下週|下星期|這週|這星期|週|星期)([一二三四五六日天])', expression)
    if wk_match:
        prefix, day_char = wk_match.groups()
        target_weekday = weekday_map.get(day_char, 0)
        current_weekday = now.weekday()
        if prefix in ('下週', '下星期'):
            # Next week: find next Monday, then add target weekday
            days_to_monday = 7 - current_weekday if current_weekday != 0 else 7
            result_date = now.date() + timedelta(days=days_to_monday + target_weekday)
        else:
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7  # Same weekday means next week
            result_date = now.date() + timedelta(days=days_ahead)

    # Absolute date: M/D or M月D日
    abs_match = re.search(r'(\d{1,2})[/月](\d{1,2})日?', expression)
    if abs_match:
        month, day = int(abs_match.group(1)), int(abs_match.group(2))
        year = now.year
        try:
            result_date = datetime(year, month, day).date()
            # If the date is in the past, use next year
            if result_date < now.date():
                result_date = datetime(year + 1, month, day).date()
        except ValueError:
            pass

    # Full date: YYYY-M-D or YYYY/M/D
    full_match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', expression)
    if full_match:
        try:
            result_date = datetime(int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3))).date()
        except ValueError:
            pass

    # === Parse time part ===

    # Period of day
    is_pm = bool(re.search(r'下午|晚上|晚間|傍晚|PM|pm', expression))
    is_noon = '中午' in expression

    # Chinese time: N點(M分)
    cn_time = re.search(r'([一二兩三四五六七八九十\d]+)[點時](?:([一二三四五六七八九十\d]+)分?)?(?:半)?', expression)
    if cn_time:
        h = cn_to_num(cn_time.group(1))
        m = cn_to_num(cn_time.group(2)) if cn_time.group(2) else 0
        if '半' in expression and cn_time.end() <= expression.index('半') + 2:
            m = 30
        if h is not None:
            result_hour = h
            result_minute = m or 0

    # Numeric time: HH:MM or H:MM
    num_time = re.search(r'(\d{1,2}):(\d{2})', expression)
    if num_time:
        result_hour = int(num_time.group(1))
        result_minute = int(num_time.group(2))

    # Apply AM/PM logic
    if result_hour is not None:
        if is_pm and result_hour < 12:
            result_hour += 12
        elif is_noon and result_hour == 12:
            pass  # 12 is already noon
    elif is_noon:
        result_hour = 12
        result_minute = 0

    # === Combine date + time ===

    if result_date is None and result_hour is not None:
        # Time only, assume today if future, else tomorrow
        result_date = now.date()
        candidate = datetime(result_date.year, result_date.month, result_date.day,
                             result_hour, result_minute, tzinfo=TW_TZ)
        if candidate <= now:
            result_date += timedelta(days=1)

    if result_date is None:
        return None

    if result_hour is None:
        result_hour = 9  # Default to 9:00 AM
        result_minute = 0

    return datetime(result_date.year, result_date.month, result_date.day,
                    result_hour, result_minute, tzinfo=TW_TZ)


INVOICE_PROMPT = """Identify all invoices/receipts in this image.
For each invoice, return a JSON array with objects containing these fields:
{
  "date": "YYYY/MM/DD",
  "vendor": "store or supplier name",
  "items": [
    {"name": "item name", "quantity": 1, "unit_price": 100}
  ],
  "currency": "TWD",
  "exchange_rate": null,
  "subtotal_before_tax": 0,
  "tax": 0,
  "total": 0,
  "department": "國內 or 國外",
  "account_target": "vendor or payee name",
  "category": ""
}

Rules:
- If Taiwan invoice (統一發票, 收據, 收支單) → department = "國內", currency = "TWD"
- If foreign receipt (Google, AWS, Cloud services, etc.) → department = "國外", detect currency
- exchange_rate: extract if visible on document, otherwise null
- category: leave empty string (will be classified later)
- Return JSON array even for single invoice
- Only return the JSON array, no markdown wrapping, no explanation
- Extract raw data exactly as shown on the document"""


def parse_invoice_image(image_bytes):
    """Parse invoice image using Gemini Vision.

    Args:
        image_bytes: Raw image bytes from LINE Content API

    Returns:
        list of invoice dicts, or None on failure
    """
    if not gemini_model or not image_bytes:
        print(f"Invoice parse: model={bool(gemini_model)}, bytes={len(image_bytes) if image_bytes else 0}", flush=True)
        return None

    try:
        # Pass image as inline data (no Pillow dependency needed)
        image_part = {"mime_type": "image/jpeg", "data": image_bytes}
        print(f"Invoice parse: sending {len(image_bytes)} bytes to Gemini", flush=True)

        result = gemini_model.generate_content([INVOICE_PROMPT, image_part]).text.strip()
        print(f"Invoice Gemini raw: {result[:300]}", flush=True)

        invoices = _clean_ai_response(result)
        if isinstance(invoices, dict):
            invoices = [invoices]

        if not isinstance(invoices, list):
            print(f"Invoice parse: unexpected type {type(invoices)}", flush=True)
            return None

        print(f"Invoice parse: found {len(invoices)} invoice(s)", flush=True)
        return invoices
    except Exception as e:
        import traceback
        print(f"Invoice parse error: {e}", flush=True)
        traceback.print_exc()
        return None


def parse_message(text, current_time, model):
    """Parse user message using Gemini for intent + Python for time.

    Returns: dict with 'action' and relevant fields, or None on failure.
    """
    if not model:
        return None

    try:
        prompt = PROMPT.format(time=current_time.strftime('%Y-%m-%d %H:%M'))
        result = model.generate_content(f"{prompt}\n\n{text}").text.strip()
        print(f"Gemini raw: {result[:200]}", flush=True)

        parsed = _clean_ai_response(result)
        action = parsed.get('action')
        print(f"Gemini parsed: action={action}", flush=True)

        # For schedule_message, parse time expression with Python
        if action == 'schedule_message':
            time_expr = parsed.get('time_expression')
            send_time = _parse_time_expression(time_expr, current_time)
            if send_time:
                parsed['send_time'] = send_time.isoformat()
            # Remove raw time_expression, keep parsed send_time
            parsed.pop('time_expression', None)

        return parsed
    except Exception as e:
        print(f"Parser error: {e}", flush=True)
        return None
