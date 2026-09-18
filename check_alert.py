from datetime import datetime, timedelta
import os
import re
from google import genai
from linebot.v3 import WebhookHandler
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
                if k in ["start", "string", "content"] and isinstance(v, str):
                    candidates.append(v)
                elif isinstance(v, (dict, list)):
                    search_dict(v)
        elif isinstance(d, list):
            for item in d:
                search_dict(item)

    search_dict(prop_info)
    
    if prop_info.get("type") == "date" and prop_info.get("date"):
        if prop_info["date"].get("start"):
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

# ----------------- 執行自動檢查與告警主程式 -----------------
def run_check():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    print(f"================== [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行檢查 ==================")

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
                print(f"⚠️ 讀取工程時程資料庫失敗: {res.text}")
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

            # ==========================================
            # 💡 【防呆限制區塊】：目前全部加上 # 備註不執行
            # 等測試完確定會發報警後，若想恢復再把 # 拿掉
            # ==========================================
            
            # 檢查 1：進度狀態是否已完成 (目前不執行)
            # status = ""
            # if "進度狀態" in props:
            #     status_prop = props.get("進度狀態")
            #     if status_prop and isinstance(status_prop, dict):
            #         if status_prop.get("type") == "status" and status_prop.get("status"):
            #             status = status_prop["status"].get("name", "")
            #         elif status_prop.get("type") == "select" and status_prop.get("select"):
            #             status = status_prop["select"].get("name", "")
            # if status == "已完成":
            #     continue

            # 檢查 2：是否有關聯收發文 (目前不執行)
            # has_linked_doc = False
            # if "相關收發文歷程" in props:
            #     rel_prop = props.get("相關收發文歷程")
            #     if rel_prop and isinstance(rel_prop, dict) and rel_prop.get("type") == "relation":
            #         if rel_prop.get("relation", []):
            #             has_linked_doc = True
            # if has_linked_doc:
            #     continue
            # ==========================================

            # 日期計算：優先抓取欄位，若公式回傳 None 則改由「前置事件核定日 + 相對天數」自行計算
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
                if "相對天數(NTP+天)" in props:
                    num_prop = props.get("相對天數(NTP+天)")
                    if isinstance(num_prop, dict) and num_prop.get("type") == "number":
                        rel_days = num_prop.get("number") or 0
                
                if pre_date and rel_days is not None:
                    due_date = pre_date + timedelta(days=int(rel_days))

            if due_date:
                diff_days = (due_date - today).days
                # 印出檢查紀錄供 Log 參考
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
            if "限辦日期" in props:
                due_date = extract_date_from_prop(props.get("限辦日期"))

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
