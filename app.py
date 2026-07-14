import os
import json
import gspread
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
import google.generativeai as genai

# 初始化 Flask 與 LINE API
app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 初始化 Gemini
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# 讀取環境變數 (請確認 Render 已設定這些 Key)
SUCCESS_GROUP_ID = os.environ.get('SUCCESS_GROUP_ID')
ALERT_GROUP_ID = os.environ.get('ALERT_GROUP_ID')

# --- 您的核心邏輯函式 ---
def process_with_ai(user_msg):
    # 【請確保這裡的提示詞(Prompt)與解析 JSON 的邏輯與您原本成功運作的版本一致】
    prompt = f"請分析這段工地缺失描述，並輸出為JSON格式 (包含：站別, 位置, 設備, 缺失項目, 嚴重程度): {user_msg}"
    response = model.generate_content(prompt)
    
    # 假設 AI 回傳 JSON 字串，轉為 Dictionary
    data = json.loads(response.text.replace("```json", "").replace("```", ""))
    return data

def get_sheet():
    # 【這裡填入您原本連線 Google Sheets 的邏輯】
    # 例如：gc = gspread.service_account(...)
    # return gc.open("您的試算表名稱").sheet1
    return None # 請記得修改為實際的 sheet 物件

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
    # 1. 除錯功能：抓取群組 ID
    if event.source.type == "group":
        group_id = event.source.group_id
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"DEBUG: 群組ID為: {group_id}"))
        return 

    # 2. AI 處理與入庫
    user_msg = event.message.text
    try:
        data = process_with_ai(user_msg)
        sheet = get_sheet()
        
        if sheet:
            sheet.append_row([
                str(datetime.now()), data.get("站別"), data.get("位置"), 
                data.get("設備"), data.get("缺失項目"), data.get("嚴重程度")
            ])
            
            # 回覆個人
            reply_text = f"✅ 已成功記錄！\n缺失：{data.get('缺失項目')}\n程度：{data.get('嚴重程度')}"
            
            # 分流推播邏輯
            if ALERT_GROUP_ID and data.get("嚴重程度") == "高":
                alert_msg = f"⚠️ [緊急異常]\n站別: {data.get('站別')}\n缺失: {data.get('缺失項目')}"
                line_bot_api.push_message(ALERT_GROUP_ID, TextSendMessage(text=alert_msg))
            
            if SUCCESS_GROUP_ID:
                line_bot_api.push_message(SUCCESS_GROUP_ID, TextSendMessage(text=f"✅ 同步完成: {data.get('缺失項目')}"))
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="系統錯誤：無法連接至 Google Sheets"))
            
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"處理失敗: {str(e)}"))

if __name__ == "__main__":
    app.run()
