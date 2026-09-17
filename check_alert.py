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
    
    # 1. 檢查標準日期格式
    date_obj = prop_info.get("date")
    if isinstance(date_obj, dict) and date_obj.get("start"):
        candidates.append(date_obj.get("start"))
        
    # 2. 檢查公式計算結果 (Formula)
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
                
    # 3. 檢查其他型態或子屬性
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
    print(f"\n================ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ================")
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

        print(f"📌 [工程進度] 總共撈取到 {len(all_pages)} 筆頁面。")

        for page in all_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            # 偵錯：印出每一筆的名稱與抓到的日期原始資料
            c_prop = props.get("契約規定完成日")
            t_prop = props.get("預計完成日")
            c_dt = extract_date_from_prop(c_prop) if c_prop else None
            t_dt = extract_date_from_prop(t_prop) if t_prop else None
            
            print(f"  > 檢查項目: 【{title}】 | 契約日解析結果: {c_dt} | 預計日解析結果: {t_dt}")

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
                print(f"    ↳ 跳過：狀態為已完成")
                continue

            # 防呆機制：檢查相關收發文
            cancel_alert = False
            rel_prop = props.get("相關收發文歷程")
            if rel_prop and isinstance(rel_prop, dict):
                related_docs = rel_prop.get("relation", [])
                for doc in related_docs:
                    if not isinstance(doc, dict):
                        continue
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
                                sel = type_prop.get("select")
                                if isinstance(sel, dict):
                                    doc_type = sel.get("name", "") or ""
                            
                            if doc_type == "發文":
                                cancel_alert = True
                                break
                            if doc_type == "收文":
                                follow_prop = doc_props.get("後續辦理文")
                                if follow_prop and isinstance(follow_prop, dict):
                                    if follow_prop.get("relation", []):
                                        cancel_alert = True
                                        break
                    except Exception:
                        pass

            if cancel_alert:
                print(f"    ↳ 跳過：已有相關發文或後續辦理文")
                continue

            dates = []
            if c_dt: dates.append(c_dt)
            if t_dt: dates.append(t_dt)
            
            if dates:
                due_date = min(dates)
                diff_days = (due_date - today).days
                print(f"    ✅ 成功加入排程！最終判定日: {due_date.strftime('%Y-%m-%d')} (剩餘 {diff_days} 天)")
                tasks.append({
                    "title": f"[工程] {title}", 
                    "due_date": due_date.strftime("%Y-%m-%d"), 
                    "diff_days": diff_days
                })
            else:
                print(f"    ❌ 失敗：找不到任何有效日期")

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
