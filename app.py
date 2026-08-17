from datetime import datetime, timedelta
import json
import os
from flask import Flask, abort, request
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import requests

app = Flask(__name__)

# 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALERT_GROUP_ID = "C5c0b9ad86a00149bb16b5db6a8d0b622"

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    # (此處省略報修與時程寫入邏輯，與前一版相同)
    # ... (程式碼邏輯保持您之前的輸入解析與寫入) ...

@app.route("/check-schedule", methods=["GET"])
def check_schedule():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 查詢 Notion 資料庫
    url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
    all_pages = []
    has_more = True
    start_cursor = None
    
    while has_more:
        payload = {"start_cursor": start_cursor} if start_cursor else {}
        res = requests.post(url, headers=notion_headers, json=payload)
        if res.status_code != 200: return "Error", 500
        data = res.json()
        all_pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    tasks = []
    for page in all_pages:
        props = page.get("properties", {})
        title = props.get("title", {}).get("title", [{}])[0].get("text", {}).get("content", "無標題")
        status = props.get("進度/狀態", {}).get("select", {}).get("name", "未開始")
        if status == "已完成": continue

        # 取得兩個日期進行比較
        c_date = props.get("契約規定完成日", {}).get("date", {}).get("start")
        t_date = props.get("預計完成日", {}).get("date", {}).get("start")
        
        dates = []
        if c_date: dates.append(datetime.strptime(c_date[:10], "%Y-%m-%d"))
        if t_date: dates.append(datetime.strptime(t_date[:10], "%Y-%m-%d"))
        
        if dates:
            due_date = min(dates) # 取兩者中較早者
            diff_days = (due_date - today).days
            tasks.append({"title": title, "due_date": due_date.strftime("%Y-%m-%d"), "diff_days": diff_days})

    # 分類告警
    alerts = {"before_7": [], "before_1": [], "today": [], "after_3": [], "after_7": [], "after_14": [], "after_monthly": []}
    for t in tasks:
        d = t["diff_days"]
        if d == 7: alerts["before_7"].append(t)
        elif d == 1: alerts["before_1"].append(t)
        elif d == 0: alerts["today"].append(t)
        elif d == -3: alerts["after_3"].append(t)
        elif d == -7: alerts["after_7"].append(t)
        elif d == -14: alerts["after_14"].append(t)
        elif d < 0 and abs(d) % 30 == 0: alerts["after_monthly"].append(t)

    # 組裝訊息
    msg_lines = ["📢 【工程時程進度自動告警】"]
    labels = [("before_7", "⏳ 剩餘 1 週"), ("before_1", "⚠️ 剩餘 1 天"), ("today", "🚨 今日到期"), 
              ("after_3", "❌ 已逾期 3 天"), ("after_7", "❌ 已逾期 1 週"), ("after_14", "❌ 已逾期 2 週"), ("after_monthly", "❗ 長期逾期")]
    
    has_alert = False
    for key, label in labels:
        if alerts[key]:
            has_alert = True
            msg_lines.append(f"\n{label}:")
            for t in alerts[key]: msg_lines.append(f"• {t['title']} ({t['due_date']})")
    
    if not has_alert: return "No alerts."

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(PushMessageRequest(to=ALERT_GROUP_ID, messages=[TextMessage(text="\n".join(msg_lines))]))
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
