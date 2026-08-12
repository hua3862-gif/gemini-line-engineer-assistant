import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from notion_client import Client as NotionClient

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
notion = NotionClient(auth=os.environ["NOTION_TOKEN"])

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL_NAME = "gemini-2.5-flash"
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
                contents=f"請將此工程時程解析為 JSON 物件格式 (包含欄位 title 和 date): {text}"
            )
            content = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(content)
            
            # 防呆處理：如果 AI 回傳的是 List，自動抓取第一筆物件
            if isinstance(data, list):
                if len(data) > 0:
                    data = data[0]
                else:
                    raise ValueError("AI 回傳的 JSON 列表為空")

            title = data.get('title', '未命名事項')
            date_info = data.get('date', '未指定日期')
            
            add_to_notion(title, date_info)
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入 Notion!\n名稱: {title}\n日期: {date_info}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
