import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

# --- 1. 從系統環境變數讀取金鑰 (這是最安全的做法) ---
# 您不需要在這裡填寫金鑰，請在 Render 的 "Environment Variables" 設定這些變數
line_bot_api = LineBotApi(os.getenv('LINE_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 初始化 Gemini 2.5 客戶端
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

app = Flask(__name__)

# --- 2. 喚醒路由 (解決 Render 休眠問題) ---
@app.route("/ping")
def ping():
    return "I am awake!", 200

# --- 3. LINE Webhook 處理 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 4. 訊息處理邏輯 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    
    # 強制要求繁體中文回答
    system_prompt = "請務必使用『台灣繁體中文』來回答以下問題：\n"
    
    try:
        # 使用 Gemini 2.5 API
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=system_prompt + user_msg
        )
        reply_text = response.text
    except Exception as e:
        reply_text = f"🤖 系統發生錯誤: {e}"
        
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    app.run()
