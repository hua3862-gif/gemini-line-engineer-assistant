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

# --- 設定 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 將這裡換成您剛剛複製的試算表 ID
SPREADSHEET_ID = '1QNC3xAakhhheCQXaWA54zkSkmT03u72Rd3wElIC_g0g' 

sheet = None

def init_sheet():
    global sheet
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    try:
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # 使用 ID 直接開啟，最精準
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        print("DEBUG: 試算表 ID 開啟成功")
    except Exception as e:
        print(f"DEBUG: 初始化試算表錯誤: {e}")

# 初始化
init_sheet()

def log_to_sheet(user_msg, bot_res):
    if sheet:
        try:
            sheet.append_row([str(datetime.now()), user_msg, bot_res])
            print("DEBUG: 資料寫入成功")
        except Exception as e:
            print(f"DEBUG: 寫入失敗: {e}")
    else:
        print("DEBUG: 寫入失敗，sheet 是 None")

# --- 路由 ---
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
    response_text = "已收到並記錄。"
    log_to_sheet(user_msg, response_text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response_text))

if __name__ == "__main__":
    app.run()
