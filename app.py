import os
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import requests

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Notion 設定與兩個資料庫 ID
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")  # 報修/缺失資料庫
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")  # 時程資料庫

NOTION_VERSION = "2022-06-28"
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


@app.route("/callback", methods=["POST"])
def callback():
  signature = request.headers.get("X-Line-Signature", "")
  body = request.get_data(as_text=True)
  app.logger.info("Request body: " + body)

  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  text = event.message.text.strip()
  reply_token = event.reply_token

  response_message = "收到您的訊息！"

  # 1. 處理「報修」指令（寫入工程缺失管理資料庫）
  if text.startswith("報修：") or text.startswith("報修:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    notion_data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "缺失項目": {  # 報修資料庫的標題欄位
                "title": [{"text": {"content": content}}]
            }
        },
    }

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = f"✅ 已成功新增至【工程缺失管理】:\n{content}"
    else:
      response_message = f"❌ 報修記錄寫入失敗：{res.text}"

  # 2. 處理「時程」指令（新增一筆資料至時程資料庫）
  elif text.startswith("時程：") or text.startswith("時程:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    notion_data = {
        "parent": {"database_id": PROGRESS_DB_ID},
        "properties": {
            "title": {  # 時程資料庫的標題欄位名稱為 title
                "title": [{"text": {"content": content}}]
            }
        },
    }

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = f"✅ 已成功新增一筆至【時程資料庫】:\n{content}"
    else:
      response_message = f"❌ 時程資料新增失敗：{res.text}"

  else:
    response_message = (
        "💡 機器人使用說明：\n• 輸入「報修：[內容]」可新增至工程缺失資料庫。\n•"
        " 輸入「時程：[內容]」可新增至時程資料庫。"
    )

  # 回傳 LINE 訊息
  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    line_bot_api.reply_message_with_http_info(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=response_message)],
        )
    )


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=10000)
