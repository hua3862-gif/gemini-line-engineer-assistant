from datetime import datetime, timedelta
import os
import re
from urllib.parse import quote
from flask import Flask
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
import requests

# ----------------- 應用程式與環境變數設定 -----------------
app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
REPLY_DB_ID = os.getenv("REPLY_DB_ID")

ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

_page_cache = {}

def get_page(page_id):
    if page_id not in _page_cache:
        res = requests.get(f"https://api.notion.com/v1/pages/{page_id}", headers=notion_headers)
        _page_cache[page_id] = res.json() if res.status_code == 200 else {}
    return _page_cache[page_id]

# ----------------- 日期與數字解析輔助函式 -----------------
def extract_date_from_prop(prop_info, prop_name=""):
    if not prop_info or not isinstance(prop_info, dict):
        return None
    
    p_type = prop_info.get("type")
    date_str = None

    if p_type == "date":
        d_val = prop_info.get("date")
        if isinstance(d_val, dict) and d_val.get("start"):
            date_str = d_val["start"]
    elif p_type == "formula":
        f_val = prop_info.get("formula", {})
        if isinstance(f_val, dict):
            f_type = f_val.get("type")
            if f_type == "date" and f_val.get("date") is not None:
                date_str = f_val["date"].get("start")
            elif f_type == "string":
                date_str = f_val.get("string")
    elif p_type == "rollup":
        r_val = prop_info.get("rollup", {})
        if r_val.get("type") == "date" and r_val.get("date"):
            date_str = r_val["date"].get("start")
        elif r_val.get("type") == "array":
            arr = r_val.get("array", [])
            if arr:
                return extract_date_from_prop(arr[0], prop_name)
    elif p_type == "rich_text":
        rt = prop_info.get("rich_text", [])
        if rt and isinstance(rt, list) and isinstance(rt[0], dict):
            date_str = rt[0].get("text", {}).get("content", "")

    if not date_str:
        return None

    cleaned = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
    match = re.search(r'\d{4}-\d{1,2}-\d{1,2}', cleaned)
    if match:
        try:
            parts = match.group(0).split('-')
            return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None
    return None

def get_number_from_prop(prop):
    if not isinstance(prop, dict):
        return None
    t = prop.get("type")
    if t == "number":
        return prop.get("number")
    if t == "formula":
        return prop.get("formula", {}).get("number")
    if t == "rollup":
        r = prop.get("rollup", {})
        if r.get("type") == "number":
            return r.get("number")
        arr = r.get("array") or []
        if arr and isinstance(arr[0], dict) and arr[0].get("type") == "number":
            return arr[0].get("number")
    return None

def get_base_date(props):
    prop = props.get("前置事件核定日")
    if not isinstance(prop, dict):
        return None

    t = prop.get("type")
    if t in ("rollup", "formula", "date"):
        d = extract_date_from_prop(prop, "前置事件核定日")
        if d:
            return d

    if t == "relation":
        for rel in prop.get("relation", []):
            if not isinstance(rel, dict):
                continue
            page = get_page(rel.get("id", ""))
            rel_props = page.get("properties", {})
            for b_key in ["核定日", "契約規定完成日", "預計完成日", "最近發文日期", "關聯限辦日期", "發文日期", "限辦日期", "日期"]:
                d = extract_date_from_prop(rel_props.get(b_key), b_key)
                if d:
                    return d
    return None

def calc_contract_due(props):
    d = extract_date_from_prop(props.get("預計完成日"), "預計完成日")
    if d:
        return d

    base = get_base_date(props)
    offset = get_number_from_prop(props.get("相對天數(NTP+天)"))
    
    if base and offset is not None:
        calculated_date = base + timedelta(days=int(offset))
        return calculated_date
        
    return None

# ----------------- 主檢查邏輯 -----------------
def run_check():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    print(f"================== [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ==================")

    # 1. 工程時程檢查
    if PROGRESS_DB_ID:
        url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
        all_pages, has_more, start_cursor = [], True, None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(url, headers=notion_headers, json=payload)
            if res.status_code != 200: 
                break
            data = res.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        for page in all_pages:
            props = page.get("properties") or {}
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title") or []
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            due_date = calc_contract_due(props)

            if due_date:
                diff_days = (due_date - today).days
                tasks.append({
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })

    # 2. 收發文歷程檢查
    if REPLY_DB_ID:
        reply_url = f"https://api.notion.com/v1/databases/{REPLY_DB_ID}/query"
        reply_pages, has_more, start_cursor = [], True, None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(reply_url, headers=notion_headers, json=payload)
            if res.status_code != 200: 
                break
            data = res.json()
            reply_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        for page in reply_pages:
            props = page.get("properties") or {}
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title") or []
                    if title_array and isinstance(title_array[0], dict):
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            # 安全取得文件狀態，若為「已完成」則跳過
            status_prop = props.get("文件狀態")
            status = ""
            if isinstance(status_prop, dict):
                select_obj = status_prop.get("select")
                if isinstance(select_obj, dict):
                    status = select_obj.get("name", "") or ""
            
            if status == "已完成":
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

    # 3. 彙整告警分類
    alerts = {
        "before_7": [], "before_1": [], "today": [], "overdue": []
    }
    
    for t in tasks:
        d = t["diff_days"]
        if d == 7: 
            alerts["before_7"].append(t)
        elif d == 1: 
            alerts["before_1"].append(t)
        elif d == 0: 
            alerts["today"].append(t)
        elif d < 0: 
            t['overdue_days'] = abs(d)
            alerts["overdue"].append(t)

    msg_lines = ["📢 【工程時程與公文限辦自動告警】"]
    has_alert = False

    if alerts["before_7"]:
        has_alert = True
        msg_lines.append("\n⏳ 剩餘 1 週:")
        for t in alerts["before_7"]:
            msg_lines.append(f"• {t['title']} (到期日: {t['due_date']})")

    if alerts["before_1"]:
        has_alert = True
        msg_lines.append("\n⚠️ 剩餘 1 天:")
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

    if not has_alert: 
        print("沒有符合條件的即將到期或逾期項目。")
        return

    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=ALERT_GROUP_ID, 
                    messages=[TextMessage(text="\n".join(msg_lines))]
                )
            )
        print("Alert pushed successfully!")
    except Exception as e:
        print(f"Push message failed: {str(e)}")

# ----------------- Flask 路由 -----------------
@app.route("/")
def home():
    return "Line Engineer Assistant is running!", 200

@app.route("/check-schedule")
def check_schedule_route():
    try:
        run_check()
        return "OK (Checked successfully)", 200
    except Exception as e:
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
