import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from notion_client import Client
import google.generativeai as genai

# 初始化 Flask 與 LINE API
app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 初始化 Notion 與 Gemini
notion = Client(auth=os.environ.get('NOTION_TOKEN'))
notion_db_id = os.environ.get('NOTION_DATABASE_ID')
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
# 建議使用 gemini-2.5-flash，穩定性與速度較佳
model = genai.GenerativeModel('gemini-2.5-flash') 

# 讀取群組 ID
SUCCESS_GROUP_ID = os.environ.get('SUCCESS_GROUP_ID')
ALERT_GROUP_ID = os.environ.get('ALERT_GROUP_ID')

# --- 核心邏輯 ---
def process_with_ai(user_msg):
    # Prompt 優化：要求 JSON 格式並限定日期格式
    prompt = f"請分析以下工地缺失描述，並輸出為 JSON 格式 (包含：站別, 缺失項目, 嚴重程度, 日期): {user_msg}。日期請以 YYYY-MM-DD 格式輸出。"
    response = model.generate_content(prompt)
    # 清理回應內容以利 JSON 解析
    cleaned_json = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned_json)

def add_to_notion(data):
    """將解析後的資料寫入 Notion 資料庫"""
    notion.pages.create(
        parent={"database_id": notion_db_id},
        properties={
            "缺失項目": {"title": [{"text": {"content": data.get("缺失項目", "未命名缺失")}}]},
            "嚴重程度": {"select": {"name": data.get("嚴重程度", "普通")}},
            "日期": {"date": {"start": data.get("日期", datetime.now().strftime('%Y-%m-%d'))}},
            "站別": {"select": {"name": data.get("站別", "其他")}}
        }
    )

# --- Webhook 路由 ---
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
    user_msg = event.message.text
    try:
        data = process_with_ai(user_msg)
        add_to_notion(data) # 執行寫入
        
        reply_text = f"✅ 已記錄至 Notion！\n缺失：{data.get('缺失項目')}\n程度：{data.get('嚴重程度')}"
        
        # 分流推播邏輯
        if ALERT_GROUP_ID and data.get("嚴重程度") == "高":
            line_bot_api.push_message(ALERT_GROUP_ID, TextSendMessage(text=f"⚠️ [緊急異常]\n站別: {data.get('站別')}\n缺失: {data.get('缺失項目')}"))
        
        if SUCCESS_GROUP_ID:
            line_bot_api.push_message(SUCCESS_GROUP_ID, TextSendMessage(text=f"✅ 同步完成: {data.get('缺失項目')}"))
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            
    except Exception as e:
        print(f"錯誤細節: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"處理失敗，請檢查格式: {str(e)}"))

if __name__ == "__main__":
    app.run()
