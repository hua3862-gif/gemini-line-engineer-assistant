import os
import json
import gspread
import google.generativeai as genai
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# 1. API 設定
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash') # 建議使用 2.5-flash 穩定性較佳

# 2. 動態初始化 Google Sheets 函式
def get_sheet():
    spreadsheet_id = os.environ.get('SPREADSHEET_ID', '').strip()
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    
    if not spreadsheet_id or not creds_json:
        print("DEBUG: 缺少環境變數 SPREADSHEET_ID 或 GOOGLE_SHEETS_CREDENTIALS")
        return None

    try:
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client_gspread = gspread.authorize(creds)
        return client_gspread.open_by_key(spreadsheet_id).sheet1
    except Exception as e:
        print(f"DEBUG: 連接 Google Sheets 失敗: {e}")
        return None

# 3. AI 結構化提取邏輯
def process_with_ai(user_msg):
    prompt = f"""
    你是專業工程監造主管。請分析以下工地訊息，僅以 JSON 格式輸出。
    訊息: "{user_msg}"
    格式: {{
        "站別": "", "位置": "", "設備": "", "缺失項目": "", "嚴重程度": "高/中/低"
    }}
    """
    response = model.generate_content(prompt)
    text = response.text.replace('```json', '').replace('```', '').strip()
    return json.loads(text)

# 4. LINE 訊息處理
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    try:
        data = process_with_ai(user_msg)
        sheet = get_sheet() # 每次都嘗試取得連線，確保不會斷線
        
        if sheet:
            sheet.append_row([
                str(datetime.now()),
                data.get("站別"), data.get("位置"), 
                data.get("設備"), data.get("缺失項目"), 
                data.get("嚴重程度")
            ])
            reply = f"已成功記錄！\n缺失：{data.get('缺失項目')}\n程度：{data.get('嚴重程度')}"
        else:
            reply = "系統錯誤：無法連接至 Google Sheets，請查看伺服器日誌。"
            
    except Exception as e:
        reply = f"處理失敗，錯誤: {str(e)}"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

if __name__ == "__main__":
    app.run()
