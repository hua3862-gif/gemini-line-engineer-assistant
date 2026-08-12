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
NOTION_DATABASE_ID = os.getenv(
    "NOTION_DATABASE_ID"
)  # 上方：工程缺失/報修資料庫
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")  # 下方：時程資料庫

NOTION_VERSION = "2022-06-28"
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


@app.route("/callback", methods=["POST"])
def callback():
  # 取得 LINE 簽章與內文
  signature = request.headers.get("X-Line-Signature", "")
  body = request.get_data(as_text=True)
  app.logger.info("Request body: " + body)

  try:
    # 修正處：傳入 body 與 signature 給 handler.handle
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  text = event.message.text.strip()
  reply_token = event.reply_token

  response_message = "收到您的訊息！"

  # 1. 處理「報修」指令（寫入上方報修資料庫）
  if text.startswith("報修：") or text.startswith("報修:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    notion_data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "標題": {  # 請確認 Notion 報修資料庫的標題欄位名稱
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
      response_message = f"✅ 已成功將報修內容記錄至【工程缺失管理資料庫】：\n{content}"
    else:
      response_message = f"❌ 報修記錄寫入失敗：{res.text}"

  # 2. 處理「時程」指令（查詢下方時程資料庫）
  elif text.startswith("時程：") or text.startswith("時程:"):
    query_text = text.split("：" if "：" in text else ":", 1)[1].strip()

    notion_query_url = (
        f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
    )
    res = requests.post(notion_query_url, headers=notion_headers)

    if res.status_code == 200:
      results = res.json().get("results", [])
      response_message = (
          f"📅【桃園棕線時程資料庫】目前共有 {len(results)} 項主要里程碑資料。"
      )
    else:
      response_message = f"❌ 時程資料庫查詢失敗：{res.text}"

  else:
    response_message = (
        "💡 機器人使用說明：\n• 輸入「報修：[內容]」可記錄至工程缺失資料庫。\n•"
        " 輸入「時程：查詢」可讀取時程資料庫。"
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
