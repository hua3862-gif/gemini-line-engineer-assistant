from datetime import datetime
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

# Notion 設定與資料庫 ID
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")  # 報修/缺失資料庫
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")  # 時程資料庫

# Gemini API 設定
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

    prompt = f"""
    請智慧分析以下工程報修文字，並以 JSON 格式回傳以下欄位：
    - title: 缺失項目主旨（必填）
    - severity: 嚴重程度 (例如：高、中、低、待評估，若文字未提及請填 "待評估")
    - date: 日期 (請將文字中的日期轉為 YYYY-MM-DD 格式，若未提及則填 "TODAY")
    - status: 狀態 (例如：未開始、進行中、已完成、延遲，若未提及請填 "未開始")
    - station: 站別 (請智慧辨識並統一轉為標準格式，例如：K6, K7, A站 等，若無則填 null)

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
      parsed_data = {
          "title": content,
          "severity": "待評估",
          "date": "TODAY",
          "status": "未開始",
          "station": None,
      }

    today_str = datetime.now().strftime("%Y-%m-%d")
    severity_val = parsed_data.get("severity") or "待評估"
    date_val = parsed_data.get("date")
    if not date_val or date_val == "TODAY":
      date_val = today_str
    status_val = parsed_data.get("status") or "未開始"

    properties = {
        "缺失項目": {"title": [{"text": {"content": parsed_data.get("title", content)}}]},
        "嚴重程度": {"select": {"name": str(severity_val)}},
        "日期": {"date": {"start": str(date_val)}},
        "狀態": {"select": {"name": str(status_val)}},
    }

    if parsed_data.get("station"):
      properties["站別"] = {"select": {"name": str(parsed_data["station"])}}

    notion_data = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = (
          f"✅ 已成功寫入【工程缺失管理】:\n• 項目：{content}\n• 嚴重程度：{severity_val}\n•"
          f" 日期：{date_val}\n• 狀態：{status_val}"
      )
    else:
      response_message = f"❌ 寫入失敗：{res.text}"

  # 2. 處理「時程」指令
  elif text.startswith("時程：") or text.startswith("時程:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    prompt = f"""
    請智慧分析以下工程時程文字，並以 JSON 格式回傳以下欄位：
    - title: 工作項目名稱（必填）
    - system: 系統別 (若無法分辨或未提及，請務必填 "ALL"，可選值包含 ALL, RST, PSY, COM, SCD, SIG, PSD, AFC)
    - relative_days: 相對天數數字 (例如 NTP+45 則填 45，若無則填 null)
    - contract_date: 契約規定完成日期 (格式 YYYY-MM-DD，若無則填 null)
    - target_date: 預定完成日/臨時性需求日期 (格式 YYYY-MM-DD，若無則填 null)
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
          "system": "ALL",
          "relative_days": None,
          "contract_date": None,
          "target_date": None,
          "remark": None,
      }

    sys_val = parsed_data.get("system")
    if not sys_val:
      sys_val = "ALL"

    contract_d = parsed_data.get("contract_date")
    target_d = parsed_data.get("target_date")

    if contract_d and not target_d:
      target_d = contract_d

    properties = {
        "title": {"title": [{"text": {"content": parsed_data.get("title", content)}}]},
        "系統別": {"multi_select": [{"name": str(sys_val)}]},  # 配合 multi_select 型態
    }

    if parsed_data.get("relative_days") is not None:
      properties["相對天數(NTP+天)"] = {"number": int(parsed_data["relative_days"])}
    if contract_d:
      properties["契約規定完成日"] = {"date": {"start": str(contract_d)}}
    if target_d:
      properties["預計完成日"] = {"date": {"start": str(target_d)}}
    if parsed_data.get("remark"):
      properties["備註"] = {"rich_text": [{"text": {"content": str(parsed_data["remark"])}}]}

    notion_data = {"parent": {"database_id": PROGRESS_DB_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = f"✅ 已成功新增至【時程資料庫】:\n• 項目：{content}\n• 系統別：{sys_val}"
    else:
      response_message = f"❌ 時程新增失敗：{res.text}"

  else:
    response_message = (
        "💡 使用說明：\n• 輸入「報修：[內容]」\n• 輸入「時程：[內容]」"
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
