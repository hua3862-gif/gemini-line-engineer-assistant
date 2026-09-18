from datetime import datetime, timedelta
import os
import re
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
REPLY_DB_ID = os.getenv("REPLY_DB_ID", NOTION_TOKEN) 

ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

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
                if k in ["start", "string", "content", "date"] and isinstance(v, str):
                    candidates.append(v)
                elif isinstance(v, (dict, list)):
                    search_dict(v)
        elif isinstance(d, list):
            for item in d:
                search_dict(item)

    search_dict(prop_info)
    
    p_type = prop_info.get("type")
    if p_type == "date" and prop_info.get("date"):
        d_val = prop_info["date"]
        if isinstance(d_val, dict) and d_val.get("start"):
            candidates.append(d_val["start"])
            
    elif p_type == "formula" and prop_info.get("formula"):
        f_val = prop_info["formula"]
        if isinstance(f_val, dict):
            if f_val.get("type") == "string" and f_val.get("string"):
                candidates.append(f_val["string"])
            elif f_val.get("type") == "date" and f_val.get("date"):
                d_obj = f_val["date"]
                if isinstance(d_obj, dict) and d_obj.get("start"):
                    candidates.append(d_obj["start"])
                    
    elif p_type == "rollup" and prop_info.get("rollup"):
        r_val = prop_info["rollup"]
        if isinstance(r_val, dict):
            r_type = r_val.get("type")
            if r_type == "date" and r_val.get("date"):
                candidates.append(r_val["date"])
            elif r_type == "array" and isinstance(r_val.get("array"), list):
                for item in r_val["array"]:
                    sub_date = extract_date_from_prop(item)
                    if sub_date:
                        if isinstance(sub_date, datetime):
                            candidates.append(sub_date.strftime("%Y-%m-%d"))
                        else:
                            candidates.append(str(sub_date))

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

# ----------------- 執行自動檢查與告警主程式 -----------------
def run_check():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    print(f"================== [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ==================")

    if PROGRESS_DB_ID:
        url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
        all_pages = []
        has_more = True
        start_cursor = None
        
        while has_more:
            payload = {"start_cursor": start_cursor} if start_cursor else {}
            res = requests.post(url, headers=notion_headers, json=payload)
            if res.status_code != 200: 
                print(f"⚠️ 讀取工程時程資料庫失敗: {res.text}")
                break
            data = res.json()
            all_pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        print(f"工程時程資料庫總共撈取到 {len(all_pages)} 筆頁面。")

        # 💡 除錯用：印出第一筆資料的所有欄位名稱與型態，讓我們先睹為快
        if all_pages:
            sample_props = all_pages[0].get("properties", {})
            print("🔍 【Notion 欄位名稱檢視】：")
            for k, v in sample_props.items():
                print(f"   - 欄位名稱: 「{k}」 (型態: {v.get('type')})")

        for page in all_pages:
            props = page.get("properties", {})
            title = "無標題"
            for prop_name, prop_val in props.items():
                if prop_val and isinstance(prop_val, dict) and prop_val.get("type") == "title":
                    title_array = prop_val.get("title", [])
                    if title_array:
                        title = title_array[0].get("text", {}).get("content", "無標題")
                    break

            # 尋找所有可能的日期欄位
            due_date = None
            for key in ["預計完成日", "契約規定完成日", "契約完成日", "合約期限", "完成日期", "期限"]:
                if key in props:
                    extracted = extract_date_from_prop(props.get(key))
                    if extracted:
                        due_date = extracted
                        break

            if not due_date:
                pre_date = None
                for pre_key in ["前置事件核定日", "核定日期", "前置核定日"]:
                    if pre_key in props:
                        pre_date = extract_date_from_prop(props.get(pre_key))
                        if pre_date:
                            break
                
                rel_days = 0
                for day_key in ["相對天數(NTP+天)", "相對天數", "天數"]:
                    if day_key in props:
                        num_prop = props.get(day_key)
                        if isinstance(num_prop, dict) and num_prop.get("type") == "number":
                            rel_days = num_prop.get("number") or 0
                            break
                
                if pre_date and rel_days is not None:
                    due_date = pre_date + timedelta(days=int(rel_days))

            if due_date:
                diff_days = (due_date - today).days
                print(f"👉 項目: {title} | 到期日: {due_date.strftime('%Y-%m-%d')} | 剩餘天數: {diff_days}")
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
            for key in ["限辦日期", "辦理期限", "到期日"]:
                if key in props:
                    due_date = extract_date_from_prop(props.get(key))
                    if due_date:
                        break

            if due_date:
                diff_days = (due_date - today).days
                
                doc_number = ""
                for num_key in ["正式文號", "文號", "發文字號"]:
                    if num_key in props:
                        rt_prop = props.get(num_key)
                        if rt_prop and isinstance(rt_prop, dict):
                            rt = rt_prop.get("rich_text", [])
                            if rt and isinstance(rt, list):
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
