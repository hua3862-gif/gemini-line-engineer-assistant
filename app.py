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

# 資料庫 ID
DB_REPAIR = os.environ.get('NOTION_DATABASE_ID')
DB_SCHEDULE = os.environ.get('PROGRESS_DB_ID')
DB_TRAIN = os.environ.get('TRAINING_DB_ID')

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

# --- AI 解析核心 ---
def ai_analyze(user_msg, task_type):
    if task_type == "repair":
        prompt = f"分析缺失，輸出JSON格式 (無Markdown): {{\"缺失項目\": \"...\", \"嚴重程度\": \"...\", \"日期\": \"...\", \"站別\": \"...\"}}。描述: {user_msg}"
    else: # schedule
        prompt = f"""
        分析工程時程，輸出JSON格式 (無Markdown)。
        日期格式 YYYY-MM-DD，若沒提到填入 {datetime.now().strftime('%Y-%m-%d')}。
        系統別對應：RST, PSY, ALL, COM, SCD, SIG, PSD, AFC。無法判定則填 'ALL'。
        格式：{{"工作項目": "...", "預定完成日": "...", "進度狀態": "...", "系統別": "..."}}
        描述: {user_msg}
        """
    response = model.generate_content(prompt)
    cleaned = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

# --- Notion 寫入核心 ---
def add_to_notion(data, db_id, task_type):
    if task_type == "repair":
        props = {
            "缺失項目": {"title": [{"text": {"content": data.get("缺失項目", "無名稱")}}]},
            "嚴重程度": {"select": {"name": data.get("嚴重程度", "普通")}},
            "日期": {"date": {"start": data.get("日期", datetime.now().strftime('%Y-%m-%d'))}},
            "站別": {"select": {"name": data.get("站別", "其他")}}
        }
    else: # schedule
        props = {
            "工作項目": {"title": [{"text": {"content": data.get("工作項目", "未命名")}}]},
            "預定完成日": {"date": {"start": data.get("預定完成日", datetime.now().strftime('%Y-%m-%d'))}},
            "進度狀態": {"select": {"name": data.get("進度狀態", "進行中")}},
            "系統別": {"select": {"name": data.get("系統別", "ALL")}}
        }
    notion.pages.create(parent={"database_id": db_id}, properties=props)

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
        if msg.startswith("修正："):
            orig = msg.replace("修正：", "").strip()
            data = ai_analyze(orig, "repair")
            add_to_notion(data, DB_REPAIR, "repair")
            save_to_training_lib(orig, data)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已修正並同步至訓練庫！"))
        
        elif msg.startswith("時程："):
            content = msg.replace("時程：", "").strip()
            data = ai_analyze(content, "schedule")
            add_to_notion(data, DB_SCHEDULE, "schedule")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📅 已新增時程：{data.get('工作項目')} ({data.get('系統別')})"))
            
        elif msg.startswith("報修："):
            content = msg.replace("報修：", "").strip()
            data = ai_analyze(content, "repair")
            add_to_notion(data, DB_REPAIR, "repair")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 報修已記錄：{data.get('缺失項目')}"))
            
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"系統處理失敗: {str(e)}"))

if __name__ == "__main__":
    app.run()
