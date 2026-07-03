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

# 初始化 Gemini (使用您環境中確認可運行的模型)
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# 2. 初始化 Google Sheets
sheet = None

def init_sheet():
    global sheet
    spreadsheet_id = os.environ.get('SPREADSHEET_ID', '').strip()
    try:
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client_gspread = gspread.authorize(creds)
        sheet = client_gspread.open_by_key(spreadsheet_id).sheet1
    except Exception as e:
        print(f"DEBUG: 初始化試算表失敗: {e}")

init_sheet()

# 3. AI 結構化提取邏輯
def process_with_ai(user_msg):
    prompt = f"""
    你是專業工程監造主管。請分析以下工地訊息，並僅以 JSON 格式輸出。

    評估標準 (嚴重程度)：
    - 高：涉及人員安全、結構安全、造成作業立即停擺、需當日立刻處理。
    - 中：影響品質或進度、需在 3-7 日內完成改善。
    - 低：一般維護事項、清潔、建議改善，不影響整體安全與工期。

    訊息: "{user_msg}"
    格式: {{
        "站別": "",
        "位置": "",
        "設備": "",
        "缺失項目": "",
        "嚴重程度": "高/中/低"
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
        if sheet:
            sheet.append_row([
                str(datetime.now()),
                data.get("站別"), data.get("位置"), 
                data.get("設備"), data.get("缺失項目"), 
                data.get("嚴重程度")
            ])
            reply = f"已成功記錄！\n缺失：{data.get('缺失項目')}\n程度：{data.get('嚴重程度')}"
        else:
            reply = "系統錯誤：無法寫入試算表。"
    except Exception as e:
        reply = f"處理失敗，錯誤: {e}"
    
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
