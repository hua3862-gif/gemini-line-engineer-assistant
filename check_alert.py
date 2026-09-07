from datetime import datetime
import os
import requests
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID") # 主資料庫 ID
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_DATABASE_ID) 

ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def run_daily_alert():
    print(f"[{datetime.now().strftime('%Y-%m-%d')}] 開始執行工程時程與收發文自動檢查...")
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
                print(f"⚠️ 查詢工程進度資料庫失敗: {res.text}")
                break
            data = res.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        print(f"總共撈取到 {len(all_pages)} 筆工程進度頁面。")

        for page in all_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            status = "未開始"
            for key in ["進度狀態", "進度/狀態", "狀態", "進度"]:
                if key in props and props[key] is not None:
                    status_obj = props.get(key, {})
                    if status_obj and isinstance(status_obj, dict):
                        status = status_obj.get("select", {}).get("name", "未開始") or "未開始"
                        break
                    
            if status == "已完成": 
                continue

            # 防呆機制：檢查相關收發文
            cancel_alert = False
            rel_prop = props.get("相關收發文歷程")
            if rel_prop and isinstance(rel_prop, dict):
                related_docs = rel_prop.get("relation", [])
                for doc in related_docs:
                    doc_id = doc.get("id")
                    if not doc_id:
                        continue
                    try:
                        doc_page_res = requests.get(f"https://api.notion.com/v1/pages/{doc_id}", headers=notion_headers)
                        if doc_page_res.status_code == 200:
                            doc_props = doc_page_res.json().get("properties", {})
                            
                            type_prop = doc_props.get("收/發文")
                            doc_type = ""
                            if type_prop and isinstance(type_prop, dict):
                                doc_type = type_prop.get("select", {}).get("name", "") or ""
                            
                            if doc_type == "發文":
                                cancel_alert = True
                                break
                            
                            if doc_type == "收文":
                                follow_prop = doc_props.get("後續辦理文")
                                if follow_prop and isinstance(follow_prop, dict):
                                    follow_up_docs = follow_prop.get("relation", [])
                                    if follow_up_docs:
                                        cancel_alert = True
                                        break
                    except Exception:
                        pass

            if cancel_alert:
                continue

            c_date, t_date = None, None
            for key in ["契約規定完成日", "契約完成日"]:
                if key in props and props[key] is not None:
                    p_info = props.get(key, {})
                    p_type = p_info.get("type")
                    if p_type == "date":
                        c_date = p_info.get("date", {}).get("start")
                    elif p_type == "formula":
                        c_date = p_info.get("formula", {}).get("date", {}).get("start")
                    break
                    
            for key in ["預計完成日", "預計完工日"]:
                if key in props and props[key] is not None:
                    p_info = props.get(key, {})
                    p_type = p_info.get("type")
                    if p_type == "date":
                        t_date = p_info.get("date", {}).get("start")
                    elif p_type == "formula":
                        t_date = p_info.get("formula", {}).get("date", {}).get("start")
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
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })

    # 2. 查詢收發文歷程明細資料庫 (REPLY_DB_ID) 檢查「限辦日期」
    if REPLY_DB_ID:
        reply_url = f"https://api.notion.com/v1/databases/{REPLY_DB_ID}/query"
        reply_pages = []
        has_more = True
        start_cursor = None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(reply_url, headers=notion_headers, json=payload)
            if res.status_code != 200: 
                print(f"⚠️ 查詢收發文資料庫失敗: {res.text}")
                break
            data = res.json()
            reply_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        print(f"總共撈取到 {len(reply_pages)} 筆收發文頁面。")

        for page in reply_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            status = ""
            if "文件狀態" in props and props["文件狀態"] is not None:
                status_obj = props.get("文件狀態", {})
                if status_obj and isinstance(status_obj, dict):
                    status = status_obj.get("select", {}).get("name", "") or ""
            if status == "已完成":
                continue

            due_str = None
            if "限辦日期" in props and props["限辦日期"] is not None:
                p_info = props.get("限辦日期", {})
                p_type = p_info.get("type")
                if p_type == "date":
                    due_str = p_info.get("date", {}).get("start")
                elif p_type == "formula":
                    due_str = p_info.get("formula", {}).get("date", {}).get("start")

            if due_str:
                due_date = datetime.strptime(due_str[:10], "%Y-%m-%d")
                diff_days = (due_date - today).days
                
                doc_number = ""
                if "正式文號" in props and props["正式文號"] is not None:
                    rt = props.get("正式文號", {}).get("rich_text", [])
                    if rt and len(rt) > 0:
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
        print("No alerts found today.")
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
    run_daily_alert()
