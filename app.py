import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from notion_client import Client as NotionClient

app = Flask(__name__)

# 初始化設定
line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
notion = NotionClient(auth=os.environ["NOTION_TOKEN"])

# 使用新版 SDK 初始化
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL_NAME = "gemini-2.0-flash"
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

def add_to_notion(title, date_info):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "名稱": {"title": [{"text": {"content": title}}]},
            "日期": {"rich_text": [{"text": {"content": date_info}}]}
        }
    )

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
    text = event.message.text
    if text.startswith("時程："):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"請將此工程時程解析為 JSON 格式 (欄位: title, date): {text}"
            )
            content = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(content)
            
            add_to_notion(data['title'], data['date'])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入 Notion!\n名稱: {data['title']}\n日期: {data['date']}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )

if __name__ == "__main__":
    # Render 會自動設定 PORT 環境變數，若無則預設 10000
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
