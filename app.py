import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timezone, timedelta
import google.generativeai as genai
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, ReplyMessageRequest, TextMessage as V3TextMessage
from linebot import WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage

app = Flask(__name__)

# 從環境變數讀取設定
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB_ID = os.environ.get("PROGRESS_DB_ID") or os.environ.get("NOTION_DB_ID")
REPLY_DB_ID = os.environ.get("REPLY_DB_ID")
REPAIR_DB_ID = os.environ.get("REPAIR_DB_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 初始化 Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 自動依次檢查常見的群組 ID 變數名稱
LINE_GROUP_ID = (
    os.environ.get("ALERT_GROUP_ID") or 
    os.environ.get("LINE_GROUP_ID") or 
    os.environ.get("REPAIR_GROUP_ID")
)

handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None
NOTION_VERSION = "2022-06-28"

def extract_date_from_prop(prop_val, prop_name="日期"):
    """輔助函式：從 Notion 屬性中安全地抓取日期"""
    if not isinstance(prop_val, dict):
        return None
    
    if prop_val.get("type") == "date":
        date_obj = prop_val.get("date")
        if isinstance(date_obj, dict):
            date_str = date_obj.get("start")
            if date_str:
                try:
                    return datetime.fromisoformat(date_str).date()
                except ValueError:
                    try:
                        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass
    elif prop_val.get("type") == "formula":
        form_obj = prop_val.get("formula")
        if isinstance(form_obj, dict):
            if form_obj.get("type") == "date":
                date_obj = form_obj.get("date")
                if isinstance(date_obj, dict) and date_obj.get("start"):
                    try:
                        return datetime.fromisoformat(date_obj.get("start")).date()
                    except Exception:
                        pass
            elif form_obj.get("type") == "string":
                date_str = form_obj.get("string")
                if date_str:
                    try:
                        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass
    return None

def fetch_notion_database(database_id):
    """查詢 Notion 資料庫的所有分頁"""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }
    all_pages = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {}
        if start_cursor:
            payload["start_cursor"] = start_cursor
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                print(f"Notion API 錯誤 ({database_id}): {response.text}")
                break
            data = response.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"連線 Notion 發生例外: {e}")
            break
            
    return all_pages

@app.route("/")
def home():
    return "Gemini Line Engineer Assistant is running!"

@app.route("/check-schedule")
def check_schedule():
    """定時觸發檢查：掃描工程時程與收發文，並發送密集期限警示至 LINE"""
    if not NOTION_TOKEN:
        return "Error: NOTION_TOKEN is missing.", 500

    tz_taipei = timezone(timedelta(hours=8))
    today = datetime.now(tz_taipei).date()

    tasks = []

    # 1. 讀取工程時程資料庫
    if NOTION_DB_ID:
        pages = fetch_notion_database(NOTION_DB_ID)
        for page in pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break
            
            # 💡 狀態檢查：只有當「進度狀態」是空白或是「未開始」時才啟動日期警示
            status_prop = props.get("進度狀態")
            is_valid_status = False
            
            if not status_prop or status_prop.get("type") is None:
                is_valid_status = True  # 空白視為有效
            else:
                p_type = status_prop.get("type")
                if p_type == "select":
                    select_obj = status_prop.get("select")
                    if not select_obj:
                        is_valid_status = True  # 選項為空視為有效
                    else:
                        status_name = select_obj.get("name", "").strip()
                        if not status_name or status_name == "未開始":
                            is_valid_status = True
                elif p_type == "status":
                    status_obj = status_prop.get("status")
                    if not status_obj:
                        is_valid_status = True
                    else:
                        status_name = status_obj.get("name", "").strip()
                        if not status_name or status_name == "未開始":
                            is_valid_status = True
            
            if not is_valid_status:
                continue  # 如果不是空白或未開始，直接跳過不警示

            # 💡 警示依據：只抓取「預計完成日」
            due_date = None
            if "預計完成日" in props:
                due_date = extract_date_from_prop(props.get("預計完成日"), "預計完成日")
            
            if due_date:
                diff_days = (due_date - today).days
                tasks.append({
                    "title": f"[工程] {title}",
                    "due_date": due_date.strftime("%Y-%m-%d"),
                    "diff_days": diff_days
                })

    # 2. 讀取收發文歷程明細資料庫
    if REPLY_DB_ID:
        reply_pages = fetch_notion_database(REPLY_DB_ID)
        for page in reply_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            status_prop = props.get("文件狀態")
            status = ""
            if isinstance(status_prop, dict):
                select_obj = status_prop.get("select")
                if isinstance(select_obj, dict):
                    status = select_obj.get("name", "") or ""
            if status == "已完成":
                continue

            relation_prop = props.get("續辦文")
            has_relation = False
            if isinstance(relation_prop, dict):
                rel_list = relation_prop.get("relation", [])
                if rel_list and len(rel_list) > 0:
                    has_relation = True
            if has_relation:
                continue

            due_date = None
            if "限辦日期" in props:
                due_date = extract_date_from_prop(props.get("限辦日期"), "限辦日期")

            if due_date:
                diff_days = (due_date - today).days
                doc_number = ""
                if "正式文號" in props:
                    rt_prop = props.get("正式文號")
                    if isinstance(rt_prop, dict):
                        rt = rt_prop.get("rich_text", [])
                        if rt and isinstance(rt, list) and isinstance(rt[0], dict):
                            doc_number = rt[0].get("text", {}).get("content", "")

                display_title = f"[收發文] {doc_number} - {title}" if doc_number else f"[收發文] {title}"
                tasks.append({
                    "title": display_title,
                    "due_date": due_date.strftime("%Y-%m-%d"),
                    "diff_days": diff_days
                })

    # 3. 分類任務
    alerts = {
        "before_3": [],
        "before_2": [],
        "before_1": [],
        "today": [],
        "overdue": []
    }

    for t in tasks:
        d = t["diff_days"]
        if d == 3:
            alerts["before_3"].append(t)
        elif d == 2:
            alerts["before_2"].append(t)
        elif d == 1:
            alerts["before_1"].append(t)
        elif d == 0:
            alerts["today"].append(t)
        elif d < 0:
            t['overdue_days'] = abs(d)
            alerts["overdue"].append(t)

    # 4. 組裝 LINE 推播訊息
    has_alert = False
    msg_lines = [f"📊 【工程與收發文管考提醒】\n今天是 {today.strftime('%Y-%m-%d')}"]

    if alerts["before_3"]:
        has_alert = True
        msg_lines.append("\n⚠️ 3天後到期:")
        for t in alerts["before_3"]:
            msg_lines.append(f"• {t['title']} (到期日: {t['due_date']})")

    if alerts["before_2"]:
        has_alert = True
        msg_lines.append("\n⚠ 2天後到期:")
        for t in alerts["before_2"]:
            msg_lines.append(f"• {t['title']} (到期日: {t['due_date']})")

    if alerts["before_1"]:
        has_alert = True
        msg_lines.append("\n⚠ 明天到期:")
        for t in alerts["before_1"]:
            msg_lines.append(f"• {t['title']} (到期日: {t['due_date']})")

    if alerts["today"]:
        has_alert = True
        msg_lines.append("\n🚨 今日到期:")
        for t in alerts["today"]:
            msg_lines.append(f"• {t['title']} (到期日: {t['due_date']})")

    if alerts["overdue"]:
        has_alert = True
        msg_lines.append("\n❌ 已逾期項目:")
        alerts["overdue"].sort(key=lambda x: x['diff_days'])
        for t in alerts["overdue"]:
            msg_lines.append(f"• {t['title']} (已逾期 {t['overdue_days']} 天，原到期日: {t['due_date']})")

    # 5. 發送至 LINE
    if has_alert and LINE_CHANNEL_ACCESS_TOKEN and LINE_GROUP_ID:
        full_message = "\n".join(msg_lines)
        try:
            configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(
                        to=LINE_GROUP_ID,
                        messages=[V3TextMessage(text=full_message)]
                    )
                )
            print("LINE 警示推播成功！")
        except Exception as e:
            print(f"發送 LINE 訊息失敗: {e}")
            return f"Error sending LINE message: {e}", 500

    return "OK (Checked successfully)"

# LINE Webhook 事件處理：整合 Gemini AI 辨識並自動填入 Notion
if handler:
    @handler.add(MessageEvent, message=TextMessage)
    def handle_text_message(event):
        user_text = event.message.text.strip()

        if user_text.lower() ==
