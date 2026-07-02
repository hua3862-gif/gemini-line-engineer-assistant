import os
import json
import gspread
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# 1. Line 設定
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 2. Google Sheets 初始化
creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
sheet = None

if creds_json:
    try:
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        # 請確認您的 Google Sheet 檔案名稱
        sheet = client.open('Construction_Defect_Log').sheet1
        print("Google Sheets 初始化成功！")
    except Exception as e:
        print(f"Google Sheets 初始化失敗: {e}")

def log_to_sheet(user_msg, bot_res):
    print(f"DEBUG: 準備寫入資料 - 使用者: {user_msg}, 回覆: {bot_res}")
    if sheet:
        try:
            sheet.append_row([str(datetime.now()), user_msg, bot_res])
            print("DEBUG: 資料已成功寫入 Google Sheets")
        except Exception as e:
            print(f"DEBUG: 寫入失敗，錯誤訊息: {e}")
    else:
        print("DEBUG: 寫入失敗，sheet 物件為 None")

# 3. 路由
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
    
    # Gemini 回覆邏輯 (模擬)
    response_text = "工程缺失 AI 助理已接收並記錄您的訊息。"
    
    # 執行記錄
    log_to_sheet(user_msg, response_text)
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

if __name__ == "__main__":
    app.run()
