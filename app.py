import os
import json
import gspread
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from oauth2client.service_account import ServiceAccountCredentials

# 1. Flask 與 Line 設定
app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 2. Google Sheets 設定
# 注意：這會從 Render 的環境變數讀取您貼上的 JSON
creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
sheet = None

if creds_json:
    try:
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        # 請確保您的 Google Sheet 檔案名稱為 Construction_Defect_Log
        sheet = client.open('Construction_Defect_Log').sheet1
    except Exception as e:
        print(f"Google Sheets 初始化失敗: {e}")

def log_to_sheet(user_msg, bot_res):
    """將缺失記錄寫入試算表"""
    if sheet:
        try:
            sheet.append_row([str(datetime.now()), user_msg, bot_res])
        except Exception as e:
            print(f"寫入資料庫失敗: {e}")

# 3. 路由設定
@app.route("/ping", methods=['GET'])
def ping():
    return "OK", 200

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
    
    # 這裡放入您原本呼叫 Gemini 的邏輯
    # 假設 response_text 是 Gemini 的回覆
    response_text = "模擬 Gemini 回覆: 缺失已記錄。"
    
    # 記錄到 Google Sheets
    log_to_sheet(user_msg, response_text)
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

if __name__ == "__main__":
    app.run()
