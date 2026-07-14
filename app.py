import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from notion_client import Client
import google.generativeai as genai

# 初始化
app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

notion = Client(auth=os.environ.get('NOTION_TOKEN'))
DB_MAIN = os.environ.get('NOTION_DATABASE_ID')        # 工程缺失管理 ID
DB_TRAIN = os.environ.get('TRAINING_DB_ID')           # AI 訓練庫 ID

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# --- 核心邏輯 ---
def process_with_ai(user_msg):
    prompt = f"""
    請分析工地缺失描述，並嚴格輸出 JSON 格式 (不要包含 markdown 標記)。
    日期請統一為 YYYY-MM-DD，若沒提到則填入 {datetime.now().strftime('%Y-%m-%d')}。
    格式：{{"缺失項目": "...", "嚴重程度": "...", "日期": "...", "站別": "..."}}
    描述: {user_msg}
    """
    response = model.generate_content(prompt)
    cleaned = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

def add_to_notion(data, db_id):
    notion.pages.create(
        parent={"database_id": db_id},
        properties={
            "缺失項目": {"title": [{"text": {"content": data.get("缺失項目", "無名稱")}}]},
            "嚴重程度": {"select": {"name": data.get("嚴重程度", "普通")}},
            "日期": {"date": {"start": data.get("日期", datetime.now().strftime('%Y-%m-%d'))}},
            "站別": {"select": {"name": data.get("站別", "其他")}}
        }
    )

def save_to_training_lib(original_msg, data):
    notion.pages.create(
        parent={"database_id": DB_TRAIN},
        properties={
            "原始描述": {"title": [{"text": {"content": original_msg}}]},
            "修正後描述": {"rich_text": [{"text": {"content": data.get("缺失項目", "無")}}]},
            "正確嚴重程度": {"select": {"name": data.get("嚴重程度", "普通")}},
            "正確站別": {"select": {"name": data.get("站別", "其他")}}
        }
    )

# --- 路由 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    try:
        # 修正指令邏輯
        if msg.startswith("修正："):
            orig = msg.replace("修正：", "").strip()
            data = process_with_ai(orig)
            add_to_notion(data, DB_MAIN)
            save_to_training_lib(orig, data)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已修正並同步至訓練庫！"))
        # 一般紀錄邏輯
        else:
            data = process_with_ai(msg)
            add_to_notion(data, DB_MAIN)
            reply = f"✅ 已記錄至 Notion！\n項目：{data.get('缺失項目')}\n程度：{data.get('嚴重程度')}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"處理失敗: {str(e)}"))

if __name__ == "__main__":
    app.run()
