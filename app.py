from datetime import datetime, timedelta
import json
import os
import re
from flask import Flask, abort, request
from google import genai
from google.genai import types
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
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent 
import requests

app = Flask(__name__)

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_DATABASE_ID) 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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

# ----------------- 輔助函式：強效日期解析器 -----------------
def extract_date_from_prop(prop_info):
    """從 Notion 屬性結構中安全擷取日期字串並轉為 datetime 物件"""
    if not prop_info or not isinstance(prop_info, dict):
        return None
    
    candidates = []
    
    def search_dict(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ["start", "string", "content"] and isinstance(v, str):
                    candidates.append(v)
                elif isinstance(v, (dict, list)):
                    search_dict(v)
        elif isinstance(d, list):
            for item in d:
                search_dict(item)

    search_dict(prop_info)
    
    if prop_info.get("type") == "date" and prop_info.get("date"):
        if isinstance(prop_info["date"], dict) and prop_info["date"].get("start"):
            candidates.append(prop_info["date"]["start"])

    for date_str in candidates:
        if not date_str:
            continue
        cleaned = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
        match = re.search(r'\d{4}-\d{2}-\d{2}', cleaned)
        if match:
            try:
                return datetime.strptime(match.group(0), "%Y-%m-%d")
            except ValueError:
                continue
    return None

# ----------------- 根目錄與 LINE Webhook 接收點 -----------------
@app.route("/", methods=["GET"])
def home():
    return "Gemini LINE Engineer Assistant is running!"

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
def handle_text_message(event):
    text = event.message.text.strip()
    reply_token = event.reply_token
    
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

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    reply_token = event.reply_token
    message_id = event.message.id

    image_bytes = None
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApi(api_client)
        image_bytes = line_bot_blob_api.get_message_content(message_id)

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
      "doc_number": "正式文號",
      "sender": "發文單位",
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
                contents=[types.Part.from_bytes(data=content, mime_type="image/jpeg"), prompt]
            )
        else:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{prompt}\n\n公文內容：\n{content}"
            )
        
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        doc_data = json.loads(raw_text)
        
        notion_url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": REPLY_DB_ID},
            "properties": {
                "title": {"title": [{"text": {"content": doc_data.get("title", "未命名公文")}}]
                },
                "正式文號": {"rich_text": [{"text": {"content": doc_data.get("doc_number", "")}}]
                },
                "發文單位": {"select": {"name": doc_data.get("sender", "專管")}},
                "正本受文單位": {"select": {"name": doc_data.get("receiver_main", "局")}},
                "副本受文單位": {"rich_text": [{"text": {"content": doc_data.get("receiver_copy", "")}}]
                },
                "收/發文": {"select": {"name": doc_data.get("type", "收文")}},
                "日期": {"date": {"start": doc_data.get("date", datetime.now().strftime("%Y-%m-%d"))}}
            }
        }
        res = requests.post(notion_url, headers=notion_headers, json=payload)
        if res.status_code == 200:
            return f"✅ 成功辨識並自動建檔至 Notion！\n• 主旨：{doc_data.get('title')}\n• 文號：{doc_data.get('doc_number')}"
        else:
            return f"⚠️ 寫入 Notion 失敗：{res.text}"
    except Exception as e:
        return f"❌ 發生錯誤：{str(e)}"

# ----------------- 每日時程與收發文自動檢查路由 -----------------
@app.route("/check-schedule", methods=["GET"])
def check_schedule():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    # 1. 查詢工程時程進度資料庫 (PROGRESS_DB_ID)
    if PROGRESS_DB_ID:
        url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
        all_pages, has_more, start_cursor = [], True, None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(url, headers=notion_headers, json=payload)
            if res.status_code != 200: break
            data = res.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        for page in all_pages:
            props = page.get("properties") or {}
            title = "無標題"
            for prop_name, prop_val in props.items():
                if isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title") or []
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            status = "未開始"
            for key in ["進度狀態", "進度/狀態", "狀態", "進度"]:
                if key in props:
                    prop_obj = props.get(key) or {}
                    select_obj = prop_obj.get("select") or {}
                    status = (select_obj.get("name") if isinstance(select_obj, dict) else None) or "未開始"
                    break
                    
            if status == "已完成": continue

            cancel_alert = False
            related_docs = props.get("相關收發文歷程") or {}
            if isinstance(related_docs, dict):
                for doc in (related_docs.get("relation") or []):
                    doc_id = doc.get("id")
                    if not doc_id: continue
                    try:
                        doc_page_res = requests.get(f"https://api.notion.com/v1/pages/{doc_id}", headers=notion_headers)
                        if doc_page_res.status_code == 200:
                            doc_props = doc_page_res.json().get("properties") or {}
                            
                            type_prop = doc_props.get("收/發文") or {}
                            select_box = type_prop.get("select") or {}
                            doc_type = select_box.get("name") if isinstance(select_box, dict) else ""
                            
                            if doc_type == "發文":
                                cancel_alert = True
                                break
                            
                            follow_prop = doc_props.get("後續辦理文") or {}
                            if (follow_prop.get("relation") or []):
                                cancel_alert = True
                                break
                    except Exception:
                        pass

            if cancel_alert: continue

            due_date = None
            for key in ["預計完成日", "契約規定完成日", "契約完成日", "合約期限"]:
                if key in props:
                    extracted = extract_date_from_prop(props.get(key))
                    if extracted:
                        due_date = extracted
                        break

            if not due_date:
                pre_date = None
                if "前置事件核定日" in props:
                    pre_date = extract_date_from_prop(props.get("前置事件核定日"))
                
                rel_days = 0
                num_prop = props.get("相對天數(NTP+天)") or {}
                if isinstance(num_prop, dict) and num_prop.get("type") == "number":
                    rel_days = num_prop.get("number") or 0
                
                if pre_date and rel_days is not None:
                    due_date = pre_date + timedelta(days=int(rel_days))

            if due_date:
                diff_days = (due_date - today).days
                tasks.append({"title": f"[工程] {title}", "due_date": due_date.strftime("%Y-%m-%d"), "diff_days": diff_days})

    # 2. 查詢收發文歷程明細資料庫 (REPLY_DB_ID) 檢查「限辦日期」
    if REPLY_DB_ID:
        reply_url = f"https://api.notion.com/v1/databases/{REPLY_DB_ID}/query"
        reply_pages, has_more, start_cursor = [], True, None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(reply_url, headers=notion_headers, json=payload)
            if res.status_code != 200: break
            data = res.json()
            reply_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        for page in reply_pages:
            props = page.get("properties") or {}
            title = "無標題"
            for prop_name, prop_val in props.items():
                if isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title") or []
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            status = ""
            if "文件狀態" in props:
                st_prop = props.get("文件狀態") or {}
                st_select = st_prop.get("select") or {}
                status = (st_select.get("name") if isinstance(st_select, dict) else "") or ""
            if status == "已完成":
                continue

            due_date = None
            if "限辦日期" in props:
                due_date = extract_date_from_prop(props.get("限辦日期"))

            if due_date:
                diff_days = (due_date - today).days
                
                doc_number = ""
                if "正式文號" in props:
                    rt = props.get("正式文號", {}).get("rich_text") or []
                    if rt and isinstance(rt[0], dict):
                        doc_number = rt[0].get("text", {}).get("content", "")

                display_title = f"[收發文] {doc_number} - {title}" if doc_number else f"[收發文] {title}"
                tasks.append({"title": display_title, "due_date": due_date.strftime("%Y-%m-%d"), "diff_days": diff_days})

    # 彙整告警分類
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

    msg_lines = ["📢 【工程時程與公文限辦自動告警】"]
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
        return "No alerts (checked successfully)."

    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=ALERT_GROUP_ID, 
                    messages=[TextMessage(text="\n".join(msg_lines))]
                )
            )
    except Exception as e:
        return f"Push message failed: {str(e)}", 500

    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
