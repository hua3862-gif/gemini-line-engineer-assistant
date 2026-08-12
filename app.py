import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from notion_client import Client
import google.generativeai as genai

app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

notion = Client(auth=os.environ.get('NOTION_TOKEN'))

# 資料庫環境變數設定
DB_REPAIR = os.environ.get('NOTION_DATABASE_ID')      # 缺失管理
DB_SCHEDULE = os.environ.get('PROGRESS_DB_ID')        # 工程時程
DB_TRAIN = os.environ.get('TRAINING_DB_ID')           # AI 訓練庫

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

# --- AI 解析邏輯 ---
def ai_analyze(user_msg, task_type):
    if task_type == "repair":
        prompt = f"分析缺失，輸出JSON: {{\"缺失項目\": \"...\", \"嚴重程度\": \"...\", \"日期\": \"...\", \"站別\": \"...\"}}。描述: {user_msg}"
    else: # schedule
        prompt = f"分析時程，輸出JSON: {{\"工作項目\": \"...\", \"預定完成日\": \"...\", \"進度狀態\": \"...\"}}。描述: {user_msg}"
    
    response = model.generate_content(prompt)
    cleaned = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

# --- 寫入 Notion ---
def add_to_notion(data, db_id, task_type):
    if task_type == "repair":
        props = {
            "缺失項目": {"title": [{"text": {"content": data.get("缺失項目", "無名稱")}}]},
            "嚴重程度": {"select": {"name": data.get("嚴重程度", "普通")}},
            "日期": {"date": {"start": data.get("日期", datetime.now().strftime('%Y-%m-%d'))}},
            "站別": {"select": {"name": data.get("站別", "其他")}}
        }
    else:
        props = {
            "工作項目": {"title": [{"text": {"content": data.get("工作項目", "未命名")}}]},
            "預定完成日": {"date": {"start": data.get("預定完成日", datetime.now().strftime('%Y-%m-%d'))}},
            "進度狀態": {"select": {"name": data.get("進度狀態", "進行中")}}
        }
    notion.pages.create(parent={"database_id": db_id}, properties=props)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    try:
        if msg.startswith("修正："):
            orig = msg.replace("修正：", "").strip()
            data = ai_analyze(orig, "repair")
            add_to_notion(data, DB_REPAIR, "repair")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 修正成功！"))
        
        elif msg.startswith("時程："):
            content = msg.replace("時程：", "").strip()
            data = ai_analyze(content, "schedule")
            add_to_notion(data, DB_SCHEDULE, "schedule")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📅 已新增時程：{data.get('工作項目')}"))
            
        elif msg.startswith("報修："):
            content = msg.replace("報修：", "").strip()
            data = ai_analyze(content, "repair")
            add_to_notion(data, DB_REPAIR, "repair")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 報修已記錄：{data.get('缺失項目')}"))
            
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"處理失敗: {str(e)}"))

if __name__ == "__main__":
    app.run()
