import json
import os
from flask import Flask, abort, request
from google import genai
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

# Gemini API 設定 (使用您的 GEMINI_API_KEY)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

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

  # 1. 處理「報修」指令
  if text.startswith("報修：") or text.startswith("報修:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    # 使用 Gemini 智慧解析缺失內容，提取欄位
    prompt = f"""
    請分析以下工程報修文字，並以 JSON 格式回傳以下欄位：
    - title: 缺失項目主旨
    - severity: 嚴重程度 (例如：高、中、低、待評估，若無則填 null)
    - date: 日期 (格式 YYYY-MM-DD，若無則填 null)
    - station: 站別 (例如：K6, A站等，若無則填 null)

    報修文字：{content}
    請僅回傳 JSON 格式字串，不要包含 markdown 標籤或額外文字。
    """
    try:
      ai_res = gemini_client.models.generate_content(
          model="gemini-2.5-flash", contents=prompt
      )
      parsed_data = json.loads(
          ai_res.text.strip().replace("```json", "").replace("```", "")
      )
    except Exception as e:
      parsed_data = {"title": content, "severity": None, "date": None, "station": None}

    # 組裝 Notion 寫入屬性
    properties = {
        "缺失項目": {"title": [{"text": {"content": parsed_data.get("title", content)}}]}
    }
    if parsed_data.get("severity"):
      properties["嚴重程度"] = {"select": {"name": parsed_data["severity"]}}
    if parsed_data.get("date"):
      properties["日期"] = {"date": {"start": parsed_data["date"]}}
    if parsed_data.get("station"):
      properties["站別"] = {"select": {"name": parsed_data["station"]}}

    notion_data = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = f"✅ 已智慧解析並記錄至【工程缺失管理】:\n• 項目：{content}\n• 欄位已自動歸類填入！"
    else:
      response_message = f"❌ 報修記錄寫入失敗：{res.text}"

  # 2. 處理「時程」指令
  elif text.startswith("時程：") or text.startswith("時程:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    # 使用 Gemini 智慧解析時程內容
    prompt = f"""
    請分析以下工程時程文字，並以 JSON 格式回傳以下欄位：
    - title: 工作項目名稱
    - system: 系統別 (例如：ALL, RST, PSY, COM, SCD, SIG, PSD, AFC，若無則填 null)
    - relative_days: 相對天數數字 (例如 NTP+45 則填 45，若無則填 null)
    - target_date: 契約規定完成日期或預計完成日 (格式 YYYY-MM-DD，若無則填 null)
    - remark: 備註說明 (若無則填 null)

    時程文字：{content}
    請僅回傳 JSON 格式字串，不要包含 markdown 標籤或額外文字。
    """
    try:
      ai_res = gemini_client.models.generate_content(
          model="gemini-2.5-flash", contents=prompt
      )
      parsed_data = json.loads(
          ai_res.text.strip().replace("```json", "").replace("```", "")
      )
    except Exception as e:
      parsed_data = {
          "title": content,
          "system": None,
          "relative_days": None,
          "target_date": None,
          "remark": None,
      }

    # 組裝 Notion 時程寫入屬性
    properties = {
        "title": {"title": [{"text": {"content": parsed_data.get("title", content)}}]}
    }
    if parsed_data.get("system"):
      properties["系統別"] = {"select": {"name": parsed_data["system"]}}
    if parsed_data.get("relative_days") is not None:
      properties["相對天數(NTP+天)"] = {"number": int(parsed_data["relative_days"])}
    if parsed_data.get("target_date"):
      properties["契約規定完成日"] = {"date": {"start": parsed_data["target_date"]}}
    if parsed_data.get("remark"):
      properties["備註"] = {"rich_text": [{"text": {"content": parsed_data["remark"]}}]}

    notion_data = {"parent": {"database_id": PROGRESS_DB_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = f"✅ 已智慧解析並新增至【時程資料庫】:\n• 項目：{content}\n• 各欄位已自動歸類完成！"
    else:
      response_message = f"❌ 時程資料新增失敗：{res.text}"

  else:
    response_message = (
        "💡 機器人智慧說明：\n• 輸入「報修：[描述]」Gemini 會自動提取嚴重程度、日期、站別！\n•"
        " 輸入「時程：[描述]」Gemini 會自動提取系統別、相對天數與日期！"
    )

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
