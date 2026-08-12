import os
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from notion_client import Client as NotionClient

app = Flask(__name__)

# 初始化 API 客戶端
line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
notion = NotionClient(auth=os.environ["NOTION_TOKEN"])

# 使用新版 SDK 初始化 Gemini
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL_NAME = "gemini-2.5-flash"
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

def add_to_notion(title, relative_day):
    """將解析後的資料寫入 Notion，確保必定能寫入資料庫的主標題欄位"""
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            # 請確保您的 Notion 資料庫第一欄（Title 屬性）名稱叫 "項目名稱"
            "項目名稱": {"title": [{"text": {"content": f"{title} [{relative_day}]"}}]}
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
            # 讓 AI 解析出 title 與相對天數/時間描述 (remark)
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"請將此工程時程解析為 JSON 物件格式。提取項目名稱為 title，並將時間或相對天數描述(如NTP+45日)提取為 remark: {text}"
            )
            content = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(content)
            
            # 防呆處理：若 AI 回傳 List 則自動取第一筆
            if isinstance(data, list):
                if len(data) > 0:
                    data = data[0]
                else:
                    raise ValueError("AI 回傳的 JSON 列表為空")

            title = data.get('title', '未命名事項')
            relative_day = data.get('remark', '未指定天數')
            
            # 寫入 Notion
            add_to_notion(title, relative_day)
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"成功匯入 Notion!\n項目名稱: {title}\n相對天數: {relative_day}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"處理失敗: {str(e)}")
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
