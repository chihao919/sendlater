# SendLater

LINE 排程訊息機器人。晚上寫訊息，早上自動發送。

[English README](README.md)


## 功能

- 📨 用自然語言排程訊息
- 👥 自動記住傳訊息給 Bot 的人
- 🔍 模糊比對聯絡人 + AI 輔助
- 🔐 只有管理員能排程
- ⏰ 每天早上 9 點自動發送

## 快速開始

### 1. 建立 LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Messaging API channel
3. 取得 `Channel Access Token` 和 `Channel Secret`

### 2. 建立 Trello Board

建立一個 Board，包含以下 Lists：
- 👑 Admins - 管理員（可以排程的人）
- 📇 Contacts - 聯絡人（自動新增）
- 📥 Inbox - 排程中的訊息
- ✅ Sent - 已發送的訊息

取得 List ID：打開 Board → 網址後面加 `.json` → 找到各 List 的 ID

### 3. 取得 API Keys

- **Trello**: [取得 API Key](https://trello.com/app-key)
- **Gemini**: [取得 API Key](https://aistudio.google.com/app/apikey)

### 4. 部署到 Vercel

```bash
# Clone
git clone https://github.com/chihao919/sendlater.git
cd sendlater

# 部署
vercel

# 新增環境變數（參考 .env.example）
vercel env add LINE_CHANNEL_ACCESS_TOKEN
vercel env add LINE_CHANNEL_SECRET
vercel env add TRELLO_API_KEY
vercel env add TRELLO_TOKEN
vercel env add TRELLO_SCHEDULED_LIST_ID
vercel env add TRELLO_CONTACTS_LIST_ID
vercel env add TRELLO_SENT_LIST_ID
vercel env add TRELLO_ADMINS_LIST_ID
vercel env add GEMINI_API_KEY
vercel env add CRON_SECRET

# 部署到 Production
vercel --prod
```

### 5. 設定 Webhook

在 LINE Developers Console 設定 Webhook URL：
```
https://your-project.vercel.app/webhook
```

## 使用方式

| 指令 | 說明 |
|------|------|
| `發給小明：記得開會` | 排程訊息 |
| `聯絡人` | 查看聯絡人清單 |
| `排程` | 查看排程中的訊息 |
| `取消` | 取消最後一筆排程 |

## 運作流程

```
用戶傳訊息 (LINE)
        ↓
  Gemini AI 解析自然語言
        ↓
  儲存到 Trello (📥 Inbox)
        ↓
  Vercel Cron 每天早上 9 點觸發
        ↓
  LINE Push API 發送訊息
        ↓
  卡片移到 ✅ Sent
```

## 環境變數

參考 `.env.example`

## License

MIT
