"""
SendLater - Configuration and constants.
"""
import json
import os
from datetime import timedelta, timezone

import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

# LINE
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')

# Trello
TRELLO_KEY = os.environ.get('TRELLO_API_KEY', '')
TRELLO_TOKEN = os.environ.get('TRELLO_TOKEN', '')

# Trello List IDs
LISTS = {
    'scheduled': os.environ.get('TRELLO_SCHEDULED_LIST_ID', '6977369f93d182d2298e671f'),
    'contacts': os.environ.get('TRELLO_CONTACTS_LIST_ID', '69773964fa6f1fe4ff71c21b'),
    'sent': os.environ.get('TRELLO_SENT_LIST_ID', '697742862d609f8dd32aff23'),
    'admins': os.environ.get('TRELLO_ADMINS_LIST_ID', '69775e7a019120099baed077'),
    'groups': os.environ.get('TRELLO_GROUPS_LIST_ID', '697981e5eca68db4fe8c3586'),
}

CUSTOM_FIELD_CONTACT = os.environ.get('TRELLO_CUSTOM_FIELD_CONTACT', '697737cc9cb876d6ede390e4')

# Gemini
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
gemini_model = genai.GenerativeModel('gemini-2.0-flash') if GEMINI_KEY else None

# Cron
CRON_SECRET = os.environ.get('CRON_SECRET', '')

# Google Sheets
GOOGLE_SA_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
INVOICE_SHEET_ID = os.environ.get('INVOICE_SHEET_ID', '')

gs_client = None
if GOOGLE_SA_JSON:
    sa_info = json.loads(GOOGLE_SA_JSON)
    credentials = Credentials.from_service_account_info(
        sa_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    gs_client = gspread.authorize(credentials)

# Timezone
TW_TZ = timezone(timedelta(hours=8))
