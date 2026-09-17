from datetime import datetime
import os
import requests
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_DATABASE_ID) 
ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def extract_date_from_prop(prop_info):
    """全方位萬用日期擷取器"""
    if not prop_info or not isinstance(prop_info, dict):
        return None
    candidates = []
    
    date_obj = prop_info.get("date")
    if isinstance(date_obj, dict) and date_obj.get("start"):
        candidates.append(date_obj.get("start"))
        
    formula = prop_info.get("formula")
    if isinstance(formula, dict):
        if isinstance(formula.get("string"), str):
            candidates.append(formula.get("string"))
        f_date = formula.get("date")
        if isinstance(f_date, dict) and f_date.get("start"):
            candidates.append(f_date.get("start"))
        for k, v in formula.items():
            if isinstance(v, str) and len(v) >= 8:
                candidates.append(v)
                
    for k, v in prop_info.items():
        if isinstance(v, str) and len(v) >= 8:
            candidates.append(v)
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, str) and len(sub_v) >= 8:
                    candidates.append(sub_v)

    for date_str in candidates:
        if not date_str:
            continue
        cleaned = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")[:10]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
    return None


def run_daily_alert():
    print(f"\n================ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S' )}] 開始執行檢查 ================")
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    # 1. 查詢工程時程進度資料庫 (PROGRESS_DB_ID)
    if PROGRESS_DB_ID:
        url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
        all_pages = []
        has_more = True
        start_cursor = None
        
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
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            # 🔍【特別除錯】：印出這筆資料的所有屬性名稱與內容，看「技師簽證執行計畫」到底把日期存在哪個欄位裡！
            if "技師" in title:
                print(f"\n🔍 找到目標項目: 【{title}】")
                for p_key, p_val in props.items():
                    print(f"   - 欄位名稱 [{p_key}]: 內容 -> {p_val}")

            status = "未開始"
            for key in ["進度狀態", "進度/狀態", "狀態", "進度"]:
                if key in props and props[key] is not None:
                    status_obj = props.get(key)
                    if isinstance(status_obj, dict):
                        sel = status_obj.get("select")
                        if isinstance(sel, dict):
                            status = sel.get("name", "未開始") or "未開始"
                            break
            if status == "已完成": 
                continue

            cancel_alert = False
            rel_prop = props.get("相關收發文歷程")
            if rel_prop and isinstance(rel_prop, dict):
                for doc in rel_prop.get("relation", []):
                    if not isinstance(doc, dict) or not doc.get("id"):
                        continue
                    try:
                        doc_page_res = requests.get(f"https://api.notion.com/v1/pages/{doc.get('id')}", headers=notion_headers)
                        if doc_page_res.status_code == 200:
                            doc_props = doc_page_res.json().get("properties", {})
                            type_prop = doc_props.get("收/發文")
                            doc_type = ""
                            if type_prop and isinstance(type_prop, dict):
                                sel = type_prop.get("select")
                                if isinstance(sel, dict):
                                    doc_type = sel.get("name", "") or ""
                            if doc_type == "發文":
                                cancel_alert = True
                                break
                            if doc_type == "收文" and doc_props.get("後續辦理文", {}).get("relation", []):
                                cancel_alert = True
                                break
                    except Exception:
                        pass
            if cancel_alert:
                continue

            # 🛠️ 尋找所有可能的日期欄位，或者直接掃描整頁所有屬性找日期！
            dates = []
            for p_key, p_val in props.items():
                parsed_d = extract_date_from_prop(p_val)
                if parsed_d:
                    dates.append(parsed_d)
            
            if dates:
                due_date = min(dates)
                diff_days = (due_date - today).days
                tasks.append({
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })

    # 2. 查詢收發文歷程明細資料庫 (REPLY_DB_ID) 
    if REPLY_DB_ID:
        reply_url = f"https://api.notion.com/v1/databases/{REPLY_DB_ID}/query"
        reply_pages = []
        has_more = True
        start_cursor = None
        
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

            status = ""
            for key in ["文件狀態", "狀態"]:
                if key in props and props[key] is not None:
                    status_obj = props.get(key, {})
                    if isinstance(status_obj, dict):
                        sel = status_obj.get("select")
                        if isinstance(sel, dict):
                            status = sel.get("name", "") or ""
                            break
            if status == "已完成":
                continue

            cancel_reply_alert = False
            for rel_key in ["續辦文", "後續辦理文"]:
                if rel_key in props and props[rel_key] is not None:
                    if props[rel_key].get("relation", []):
                        cancel_reply_alert = True
                        break
            if cancel_reply_alert:
                continue

            due_date = extract_date_from_prop(props.get("限辦日期"))

            if due_date:
                diff_days = (due_date - today).days
                doc_number = ""
                for key in ["正式文號", "文號"]:
                    if key in props and props[key] is not None:
                        rt = props[key].get("rich_text", [])
                        if rt and isinstance(rt[0], dict):
                            doc_number = rt[0].get("text", {}).get("content", "")
                            break

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
        print("No alerts found today.")
        return

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=ALERT_GROUP_ID, 
                messages=[TextMessage(text="\n".join(msg_lines))]
            )
        )
    print("Alert pushed successfully!\n")

if __name__ == "__main__":
    run_daily_alert()
