from datetime import datetime, timedelta
import os
import re
from urllib.parse import quote
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
import requests

# ----------------- 環境變數與設定 -----------------
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
        if rt and isinstance(rt, list):
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
        if arr and arr[0].get("type") == "number":
            return arr[0].get("number")
    return None

def fetch_prop_item(page_id, prop_id):
    """用專屬 endpoint 強制計算並取得屬性值"""
    url = f"https://api.notion.com/v1/pages/{page_id}/properties/{quote(prop_id)}"
    res = requests.get(url, headers=notion_headers)
    if res.status_code != 200:
        return None
    return extract_date_from_prop(res.json(), "property_item")

def get_base_date(props):
    """取得『前置事件核定日』(已擴充支援所有常見關聯公文日期欄位)"""
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
            page = get_page(rel.get("id", ""))
            rel_props = page.get("properties", {})
            for b_key in ["核定日", "契約規定完成日", "預計完成日", "最近發文日期", "關聯限辦日期", "發文日期", "限辦日期", "日期"]:
                d = extract_date_from_prop(rel_props.get(b_key), b_key)
                if d:
                    return d
    return None

def calc_contract_due(props):
    """Python 本地備援計算公式"""
    d = extract_date_from_prop(props.get("預計完成日"), "預計完成日")
    if d:
        return d

    base = get_base_date(props)
    offset = get_number_from_prop(props.get("相對天數(NTP+天)"))
    if base and offset is not None:
        return base + timedelta(days=int(offset))
    return None

# ----------------- 主程式 -----------------
def run_check():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    print(f"================== [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ==================")

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

        print(f"工程時程資料庫總共撈取到 {len(all_pages)} 筆頁面。")

        for page in all_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            due_date = None
            if "契約規定完成日" in props:
                # 1. 先用標準解析
                due_date = extract_date_from_prop(props.get("契約規定完成日"), "契約規定完成日")
                # 2. 如果為空，用 property item 終端強制抓取
                if not due_date and "id" in props.get("契約規定完成日", {}):
                    due_date = fetch_prop_item(page["id"], props["契約規定完成日"]["id"])
                # 3. 如果還是空，用 Python 本地直接算出來！
                if not due_date:
                    due_date = calc_contract_due(props)

            if due_date:
                diff_days = (due_date - today).days
                print(f"👉 [工程] 項目: {title} | 到期日: {due_date.strftime('%Y-%m-%d')} | 剩餘天數: {diff_days}")
                tasks.append({
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })

    # 收發文歷程檢查
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
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            due_date = None
            if "限辦日期" in props:
                due_date = extract_date_from_prop(props.get("限辦日期"), "限辦日期")

            if due_date:
                diff_days = (due_date - today).days
                doc_number = ""
                if "正式文號" in props:
                    rt_prop = props.get("正式文號")
                    if rt_prop and isinstance(rt_prop, dict):
                        rt = rt_prop.get("rich_text", [])
                        if rt and isinstance(rt, list):
                            doc_number = rt[0].get("text", {}).get("content", "")

                display_title = f"[收發文] {doc_number} - {title}" if doc_number else f"[收發文] {title}"
                tasks.append({
                    "title": display_title,
                    "due_date": due_date.strftime("%Y-%m-%d"),
                    "diff_days": diff_days
                })

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
        print("沒有符合條件的即將到期或逾期項目。")
        return

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=ALERT_GROUP_ID, 
                messages=[TextMessage(text="\n".join(msg_lines))]
            )
        )
    print("Alert pushed successfully!")

if __name__ == "__main__":
    run_check()
