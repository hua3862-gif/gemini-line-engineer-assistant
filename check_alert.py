from datetime import datetime
import os
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
import requests

# 讀取環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
ALERT_GROUP_ID = os.getenv("ALERT_GROUP_ID")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def run_daily_alert():
  today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
  print(f"[{today.strftime('%Y-%m-%d')}] 開始執行工程時程自動檢查...")

  # 查詢 Notion 資料庫
  url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
  all_pages = []
  has_more = True
  start_cursor = None

  while has_more:
    payload = {"start_cursor": start_cursor} if start_cursor else {}
    res = requests.post(url, headers=notion_headers, json=payload)
    if res.status_code != 200:
      print(f"Notion API 查詢失敗: {res.status_code}, {res.text}")
      return
    data = res.json()
    all_pages.extend(data.get("results", []))
    has_more = data.get("has_more", False)
    start_cursor = data.get("next_cursor")

  tasks = []
  for page in all_pages:
    props = page.get("properties", {})

    # 安全地取得標題（自動掃描 Notion 中的 title 欄位）
    title = "無標題"
    for prop_name, prop_val in props.items():
      if prop_val.get("type") == "title":
        title_array = prop_val.get("title", [])
        if title_array:
          title = title_array[0].get("text", {}).get("content", "無標題")
        break

    # 檢查進度狀態，若已完成則跳過
    status = props.get("進度/狀態", {}).get("select", {}).get("name", "未開始")
    if status == "已完成":
      continue

    # 取得雙日期（契約規定完成日與預計完成日），取較早者
    c_date = props.get("契約規定完成日", {}).get("date", {}).get("start")
    t_date = props.get("預計完成日", {}).get("date", {}).get("start")

    dates = []
    if c_date:
      dates.append(datetime.strptime(c_date[:10], "%Y-%m-%d"))
    if t_date:
      dates.append(datetime.strptime(t_date[:10], "%Y-%m-%d"))

    if dates:
      due_date = min(dates)
      diff_days = (due_date - today).days
      tasks.append({
          "title": title,
          "due_date": due_date.strftime("%Y-%m-%d"),
          "diff_days": diff_days,
      })

  # 分類告警（七階段）
  alerts = {
      "before_7": [],
      "before_1": [],
      "today": [],
      "after_3": [],
      "after_7": [],
      "after_14": [],
      "after_monthly": [],
  }
  for t in tasks:
    d = t["diff_days"]
    if d == 7:
      alerts["before_7"].append(t)
    elif d == 1:
      alerts["before_1"].append(t)
    elif d == 0:
      alerts["today"].append(t)
    elif d == -3:
      alerts["after_3"].append(t)
    elif d == -7:
      alerts["after_7"].append(t)
    elif d == -14:
      alerts["after_14"].append(t)
    elif d < 0 and abs(d) % 30 == 0:
      alerts["after_monthly"].append(t)

  # 組裝 LINE 告警訊息
  msg_lines = ["📢 【工程時程進度自動告警】"]
  labels = [
      ("before_7", "⏳ 剩餘 1 週"),
      ("before_1", "⚠️ 剩餘 1 天"),
      ("today", "🚨 今日到期"),
      ("after_3", "❌ 已逾期 3 天"),
      ("after_7", "❌ 已逾期 1 週"),
      ("after_14", "❌ 已逾期 2 週"),
      ("after_monthly", "❗ 長期逾期"),
  ]

  has_alert = False
  for key, label in labels:
    if alerts[key]:
      has_alert = True
      msg_lines.append(f"\n{label}:")
      for t in alerts[key]:
        msg_lines.append(f"• {t['title']} ({t['due_date']})")

  if not has_alert:
    print("目前沒有符合條件的告警項目。")
    return

  # 發送 LINE 訊息
  with ApiClient(configuration) as api_client:
    MessagingApi(api_client).push_message(
        PushMessageRequest(
            to=ALERT_GROUP_ID, messages=[TextMessage(text="\n".join(msg_lines))]
        )
    )
  print("LINE 告警訊息已成功發送！")


if __name__ == "__main__":
  run_daily_alert()
