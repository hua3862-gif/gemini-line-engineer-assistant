from datetime import datetime, timedelta
import json
import os
from flask import Flask, abort, request
from google import genai
from google.genai import types # 用於處理圖片格式
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
# 引入圖片訊息的 Content 支援
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent 
import requests

app = Flask(__name__)

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID") # 主資料庫 ID
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
# 新增：收發文歷程明細資料庫 ID
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_DATABASE_ID) 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 雙群組 ID 設定
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

# 1. 處理文字訊息 (包含快速查詢 ID)
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    
    # 如果打「ID」回報群組 ID
    if text.upper() == "ID":
        group_id = "此聊天室不是群組"
        if hasattr(event.source, "group_id") and event.source.group_id:
            group_id = event.source.group_id
        elif hasattr(event.source, "room_id") and event.source.room_id:
            group_id = event.source.room_id
        response_text = f"📌 本群組 ID :\n{group_id}"
    else:
        response_text = process_document_with_ai(text, is_image=False)

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=response_text)]
            )
        )

# 2. 處理圖片訊息 (使用者直接傳送公文照片)
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    reply_token = event.reply_token
    message_id = event.message.id

    # 透過 LINE API 取得圖片二進位資料
    image_bytes = None
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApi(api_client)
        image_bytes = line_bot_blob_api.get_message_content(message_id)

    # 呼叫 Gemini AI 進行圖片解析並寫入 Notion
    response_text = process_document_with_ai(image_bytes, is_image=True)

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=response_text)]
            )
        )

# ----------------- AI 解析公文並寫入 Notion 核心函式 -----------------
def process_document_with_ai(content, is_image=False):
    prompt = """
    你是一個專業的公共工程文管助理。請從這份公文內容或圖片中擷取以下欄位，並嚴格回傳標準 JSON 格式（不要包覆在 markdown codeblock 中，直接回傳 JSON 即可）：
    {
      "title": "公文主旨摘要",
      "doc_number": "正式文號 (例如 桃捷棕字第...) ",
      "sender": "發文單位 (例如 捷運工程局、統包商、監造)",
      "receiver_main": "正本受文單位",
      "receiver_copy": "副本受文單位",
      "type": "收文 或是 發文",
      "date": "發文日期 (格式 YYYY-MM-DD，若無則填今天)"
    }
    """
    
    try:
        if is_image:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=content, mime_type="image/jpeg"),
                    prompt
                ]
            )
        else:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{prompt}\n\n公文內容：\n{content}"
            )
        
        # 清理 AI 回傳的 JSON 格式
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        doc_data = json.loads(raw_text)
        
        notion_url = "https://api.notion.com/v1/pages"
        
        payload = {
            "parent": {"database_id": REPLY_DB_ID},
            "properties": {
                "title": {
                    "title": [{"text": {"content": doc_data.get("title", "未命名公文")}}]
                },
                "正式文號": {
                    "rich_text": [{"text": {"content": doc_data.get("doc_number", "")}}]
                },
                "發文單位": {
                    "select": {"name": doc_data.get("sender", "專管")}
                },
                "正本受文單位": {
                    "select": {"name": doc_data.get("receiver_main", "局")}
                },
                "副本受文單位": {
                    "rich_text": [{"text": {"content": doc_data.get("receiver_copy", "")}}]
                },
                "收/發文": {
                    "select": {"name": doc_data.get("type", "收文")}
                },
                "日期": {
                    "date": {"start": doc_data.get("date", datetime.now().strftime("%Y-%m-%d"))}
                }
            }
        }
        
        res = requests.post(notion_url, headers=notion_headers, json=payload)
        if res.status_code == 200:
            return f"✅ 成功辨識並自動建檔至 Notion！\n• 主旨：{doc_data.get('title')}\n• 文號：{doc_data.get('doc_number')}\n• 類型：{doc_data.get('type')}"
        else:
            return f"⚠️ AI 解析成功，但寫入 Notion 失敗：{res.text}"
            
    except Exception as e:
        return f"❌ 解析或建檔發生錯誤：{str(e)}"

# ----------------- 每日時程自動檢查路由 (含已發文取消告警機制) -----------------
@app.route("/check-schedule", methods=["GET"])
def check_schedule():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
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
        
        # 1. 取得任務名稱
        title = "無標題"
        for prop_name, prop_val in props.items():
            if prop_val.get("type") == "title":
                title_array = prop_val.get("title", [])
                if title_array:
                    title = title_array[0].get("text", {}).get("content", "無標題")
                break

        # 2. 檢查狀態，若已完成則略過
        status = "未開始"
        for key in ["進度狀態", "進度/狀態", "狀態", "進度"]:
            if key in props:
                status = (props.get(key, {}).get("select", {}).get("name", "未開始")) or "未開始"
                break
                
        if status == "已完成": 
            continue

        # 3. 【防呆機制】檢查是否有關聯「發文」記錄，有則直接取消/略過告警
        has_sent_document = False
        related_docs = props.get("相關收發文歷程", {}).get("relation", [])
        for doc in related_docs:
            doc_id = doc["id"]
            try:
                doc_page_res = requests.get(f"https://api.notion.com/v1/pages/{doc_id}", headers=notion_headers)
                if doc_page_res.status_code == 200:
                    doc_props = doc_page_res.json().get("properties", {})
                    doc_type = doc_props.get("收/發文", {}).get("select", {}).get("name", "")
                    if doc_type == "發文":
                        has_sent_document = True
                        break
            except Exception:
                pass

        if has_sent_document:
            print(f"【已提送 - 取消告警】任務「{title}」已有對應的發文記錄。")
            continue

        # 4. 取得完成日 (支援公式欄位或日期欄位計算)
        c_date, t_date = None, None
        for key in ["契約規定完成日", "契約完成日"]:
            if key in props:
                p_type = props.get(key, {}).get("type")
                if p_type == "date":
                    c_date = props.get(key, {}).get("date", {}).get("start")
                elif p_type == "formula":
                    c_date = props.get(key, {}).get("formula", {}).get("date", {}).get("start")
                break
                
        for key in ["預計完成日", "預計完工日"]:
            if key in props:
                p_type = props.get(key, {}).get("type")
                if p_type == "date":
                    t_date = props.get(key, {}).get("date", {}).get("start")
                elif p_type == "formula":
                    t_date = props.get(key, {}).get("formula", {}).get("date", {}).get("start")
                break
        
        dates = []
        if c_date: 
            dates.append(datetime.strptime(c_date[:10], "%Y-%m-%d"))
        if t_date: 
            dates.append(datetime.strptime(t_date[:10], "%Y-%m-%d"))
        
        if dates:
            due_date = min(dates)
            diff_days = (due_date - today).days
            tasks.append({
                "title": title, 
                "due_date": due_date.strftime("%Y-%m-%d"), 
                "diff_days": diff_days
            })

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
