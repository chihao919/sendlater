# SendLater

LINE Bot for scheduling messages. Write messages at night, send them in the morning.

## Features

- 📨 Schedule messages via natural language
- 👥 Auto-register contacts when they message the bot
- 🔍 Fuzzy name matching with AI fallback
- 🔐 Admin-only access control
- ⏰ Daily cron job at 9 AM

## Quick Start

### 1. Create LINE Bot

1. Go to [LINE Developers Console](https://developers.line.biz/)
2. Create a new Messaging API channel
3. Get `Channel Access Token` and `Channel Secret`

### 2. Create Trello Board

Create a board with these lists:
- 👑 Admins - Users who can schedule messages
- 📇 Contacts - Auto-registered contacts
- 📥 Inbox - Scheduled messages
- ✅ Sent - Delivered messages

Get list IDs: Open board → add `.json` to URL → find list IDs

### 3. Get API Keys

- **Trello**: [Get API Key](https://trello.com/app-key)
- **Gemini**: [Get API Key](https://aistudio.google.com/app/apikey)

### 4. Deploy to Vercel

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/sendlater.git
cd sendlater

# Deploy
vercel

# Add environment variables (see .env.example)
vercel env add LINE_CHANNEL_ACCESS_TOKEN
vercel env add LINE_CHANNEL_SECRET
# ... add all variables from .env.example

# Deploy to production
vercel --prod
```

### 5. Set Webhook URL

In LINE Developers Console, set webhook URL:
```
https://your-project.vercel.app/webhook
```

## Usage

| Command | Description |
|---------|-------------|
| `發給小明：記得開會` | Schedule a message |
| `聯絡人` | List contacts |
| `排程` | List scheduled messages |
| `取消` | Cancel last scheduled message |

## Architecture

```
User sends message (LINE)
        ↓
  Gemini parses natural language
        ↓
  Store in Trello (📥 Inbox)
        ↓
  Vercel Cron triggers at 9 AM
        ↓
  LINE Push API sends message
        ↓
  Move card to ✅ Sent
```

## License

MIT
