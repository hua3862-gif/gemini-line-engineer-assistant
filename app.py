import os
import json
import re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from notion_client import Client as NotionClient
from datetime import datetime, timedelta

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
notion = NotionClient(auth=os.environ["NOTION_TOKEN"])
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODEL_NAME = "gemini-2.5-flash"
# 時程資料庫 ID
PROGRESS_DB_ID = os.environ["PROGRESS_DB_ID"]
# 報修資料庫 ID（從環境變數讀取原本的 NOTION_DATABASE_ID）
REPAIR_DB_ID = os.environ.get("NOTION_DATABASE_ID", PROGRESS_DB_ID)

# 1. 處理時程寫入
def add_progress_to_notion(title, time_str, system_type):
    if not system_type or system_type.strip() == "":
        system_type = "ALL"

    properties = {
        "title": {
            "title": [{"text": {"content": title}}]
        },
        "系統別": {
            "multi_select": [{"name": system_type}]
        }
    }

    date_match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', time_str)
    if date_match:
        year, month, day = date_match.groups()
        formatted_date = f"{year}-{int(month):02d}-{int(day):02d}"
        properties["預計完成日"] = {
            "date": {"start": formatted_date}
        }
    else:
        days = 0
        day_match = re.search(r'\d+', time_str)
        if day_match:
            days = int(day_match.group())

        properties["相對天數(NTP+天)"] = {
            "number": days
        }

        base_date = datetime(2026, 10, 25)
        target_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d")
        properties["預計完成日"] = {
            "date": {"start": target_date}
        }

    notion.pages.create(
        parent={"database_id": PROGRESS_DB_ID},
        properties=properties
    )

# 2. 處理報修寫入
def add_repair_to_notion(title):
    notion.pages.create(
        parent={"database_id": REPAIR_DB_ID},
        properties={
            "title": {
                "title": [{"text": {"content": title}}]
            }
        }
    )

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    
    # 處理時程
    if text.startswith("時程："):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"請將此工程時程解析為 JSON 格式。請提取項目名稱為 title，將時間描述(如 NTP+45日 或 2026/11/20)提取為 remark，若有提到系統別(如 RST, PSY, COM, SCD, SIG, PSD, AFC)請提取為 system，若無則填入空字串: {text}"
            )
            content = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(content)
            
            if isinstance(data, list):
                data = data[0] if len(data) > 0 else {}

            title = data.get('title', '未命名事項')
            time_str = data.get('remark', '無時間描述')
            system_type = data.get('system', 'ALL')
            
            add_progress_to_notion(title, time_str, system_type)
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入時程 Notion!\n項目名稱: {title}\n系統別: {system_type or 'ALL'}\n時間資訊: {time_str}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )
            
    # 處理報修
    elif text.startswith("報修："):
        try:
            repair_content = text.replace("報修：", "").strip()
            add_repair_to_notion(repair_content)
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入報修 Notion!\n內容: {repair_content}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
