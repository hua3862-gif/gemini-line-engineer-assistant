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

def add_to_notion(title, relative_day):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": f"{title} ({relative_day})"
                        }
                    }
                ]
            }
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
                contents=f"請將此工程時程解析為 JSON 格式。請提取項目名稱為 title，將時間描述(如NTP+45日)提取為 remark: {text}"
            )
            content = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(content)
            
            if isinstance(data, list):
                data = data[0] if len(data) > 0 else {}

            title = data.get('title', '未命名事項')
            relative_day = data.get('remark', '無時間描述')
            
            add_to_notion(title, relative_day)
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入 Notion!\n項目名稱: {title}\n時間資訊: {relative_day}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
