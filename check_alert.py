from datetime import datetime, timezone, timedelta
import os
import requests
import re 
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage

# ----------------- 環境變數與設定 -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_DATABASE_ID) 
ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")

# 設定台灣時區 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def extract_date_from_prop(prop_info):
    """結合 Claude 建議的精準 Formula 解析器"""
    if not prop_info or not isinstance(prop_info, dict):
        return None
        
    candidates = []
    prop_type = prop_info.get("type")
    
    # 1. 如果直接就是 date 欄位
    if prop_type == "date":
        d_obj = prop_info.get("date")
        if isinstance(d_obj, dict) and d_obj.get("start"):
            candidates.append(d_obj.get("start"))
            
    # 2. 如果是 formula 欄位（依照 Claude 的精準邏輯）
    elif prop_type == "formula":
        formula = prop_info.get("formula", {})
        f_type = formula.get("type")
        
        # 情況 A：公式回傳類型是 date
        if f_type == "date":
            f_date = formula.get("date")
            if isinstance(f_date, dict) and f_date.get("start"):
                candidates.append(f_date.get("start"))
                
        # 情況 B：公式回傳類型是 string（字串型態日期）
        elif f_type == "string":
            f_str = formula.get("string")
            if isinstance(f_str, str):
                candidates.append(f_str)
                
        # 備援：防止結構不同，把 formula 內所有可能的值抓出來
        for k, v in formula.items():
            if isinstance(v, str) and len(v) >= 8:
                candidates.append(v)
            elif isinstance(v, dict) and v.get("start"):
                candidates.append(v.get("start"))

    # 3. 其它通用備援（防呆）
    for k, v in prop_info.items():
        if isinstance(v, str) and len(v) >= 8:
            candidates.append(v)
        elif isinstance(v, dict):
            if v.get("start"):
                candidates.append(v.get("start"))

    # 4. 正規表達式確保擷取出標準 YYYY-MM-DD
    for date_str in candidates:
        if not date_str:
            continue
        cleaned = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
        match = re.search(r'\d{4}-\d{2}-\d{2}', cleaned)
        if match:
            try:
                dt = datetime.strptime(match.group(0), "%Y-%m-%d")
                return dt.replace(tzinfo=TW_TZ)
            except ValueError:
                continue
    return None


def run_daily_alert():
    now_tw = datetime.now(TW_TZ)
    print(f"\n================ [{now_tw.strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ================")
    today = now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
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
                print(f"⚠️ 讀取工程時程資料庫失敗，狀態碼: {res.status_code}, 內容: {res.text}")
                break
            data = res.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        print(f"📊 工程時程資料庫總共撈取到 {len(all_pages)} 筆頁面。")

        for light_page in all_pages:
            page_id = light_page.get("id")
            if not page_id:
                continue
            
            # 透過單頁 API 抓取，強制 Notion 計算公式
            page_res = requests.get(f"https://api.notion.com/v1/pages/{page_id}", headers=notion_headers)
            if page_res.status_code != 200:
                continue
            page = page_res.json()

            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

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

            # 邏輯：只要「相關收發文歷程」有資料，就交由收發文資料庫管控
            has_related_docs = False
            rel_prop = props.get("相關收發文歷程")
            if rel_prop and isinstance(rel_prop, dict):
                if rel_prop.get("relation", []):
                    has_related_docs = True

            if has_related_docs:
                continue 

            # 鎖定「契約規定完成日」
            due_date = None
            for key in ["契約規定完成日", "契約完成日", "合約期限"]:
                if key in props:
                    # 💡 印出該欄位的完整結構以便除錯
                    print(f"🔍 檢查專案 [{title}] 的欄位 [{key}] 內容: {props.get(key)}")
                    due_date = extract_date_from_prop(props.get(key))
                    if due_date:
                        break

            if due_date:
                diff_days = (due_date - today).days
                print(f"👉 [工程檢查成功] 項目: {title} | 到期日: {due_date.strftime('%Y-%m-%d')} | 剩餘天數: {diff_days}")
                tasks.append({
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })
            else:
                print(f"    [注意] 項目 '{title}' 找不到有效的期限日期欄位")

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
