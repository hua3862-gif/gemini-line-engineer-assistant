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

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 雙群組 ID 設定（優先讀取環境變數，若無則帶入預設值）
ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")
REPAIR_GROUP_ID = os.getenv("REPAIR_GROUP_ID", "Cbb96b6ff1d2d1b609655b4bb7d3948cf")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# ----------------- LINE Webhook 接收點 -----------------
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
    
    # 範例：如果您未來需要將報修或特定指令推送到報修群組 (REPAIR_GROUP_ID)，
    # 可以使用以下的寫法透過 ApiClient 發送主動訊息：
    # with ApiClient(configuration) as api_client:
    #     MessagingApi(api_client).push_message(
    #         PushMessageRequest(to=REPAIR_GROUP_ID, messages=[TextMessage(text=f"收到報修訊息: {text}")])
    #     )
    
    # 目前保持基本的回覆邏輯
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"已收到您的訊息：{text}")]
            )
        )

# ----------------- 每日時程自動檢查路由 -----------------
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
        if res.status_code != 200: 
            return "Error querying Notion", 500
        data = res.json()
        all_pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    tasks = []
    for page in all_pages:
        props = page.get("properties", {})
        
        # 抓取標題
        title = "無標題"
        for prop_name, prop_val in props.items():
            if prop_val.get("type") == "title":
                title_array = prop_val.get("title", [])
                if title_array:
                    title = title_array[0].get("text", {}).get("content", "無標題")
                break

        # 抓取進度狀態
        status = "未開始"
        for key in ["進度狀態", "進度/狀態", "狀態", "進度"]:
            if key in props:
                status = (props.get(key, {}).get("select", {}).get("name", "未開始")) or "未開始"
                break
                
        if status == "已完成": 
            continue

        # 取得日期進行比較
        c_date, t_date = None, None
        for key in ["契約規定完成日", "契約完成日"]:
            if key in props:
                c_date = props.get(key, {}).get("date", {}).get("start")
                break
        for key in ["預計完成日", "預計完工日"]:
            if key in props:
                t_date = props.get(key, {}).get("date", {}).get("start")
                break
        
        dates = []
        if c_date: 
            dates.append(datetime.strptime(c_date[:10], "%Y-%m-%d"))
        if t_date: 
            dates.append(datetime.strptime(t_date[:10], "%Y-%m-%d"))
        
        if dates:
            due_date = min(dates) # 取兩者中較早者
            diff_days = (due_date - today).days
            tasks.append({
                "title": title, 
                "due_date": due_date.strftime("%Y-%m-%d"), 
                "diff_days": diff_days
            })

    # 分類告警
    alerts = {
        "before_7": [], "before_1": [], "today": [], 
        "after_3": [], "after_7": [], "after_14": [], "after_monthly": []
    }
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
    labels = [
        ("before_7", "⏳ 剩餘 1 週"), 
        ("before_1", "⚠️ 剩餘 1 天"), 
        ("today", "🚨 今日到期"), 
        ("after_3", "❌ 已逾期 3 天"), 
        ("after_7", "❌ 已逾期 1 週"), 
        ("after_14", "❌ 已逾期 2 週"), 
        ("after_monthly", "❗ 長期逾期")
    ]
    
    has_alert = False
    for key, label in labels:
        if alerts[key]:
            has_alert = True
            msg_lines.append(f"\n{label}:")
            for t in alerts[key]: 
                msg_lines.append(f"• {t['title']} ({t['due_date']})")
    
    if not has_alert: 
        return "No alerts."

    # 發送到專屬的時程告警群組 (ALERT_GROUP_ID)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=ALERT_GROUP_ID, 
                messages=[TextMessage(text="\n".join(msg_lines))]
            )
        )
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
