from datetime import datetime
import os
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
import requests

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")
ALERT_GROUP_ID = os.getenv(
    "ALERT_GROUP_ID", "C5c0b9ad86a00149bb16b5db6a8d0b622"
)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def run_daily_alert():
  today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
  print(f"[{today.strftime('%Y-%m-%d')}] 開始執行工程時程自動檢查...")

  url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
  res = requests.post(url, headers=notion_headers, json={})
  if res.status_code != 200:
    print(f"Notion API 查詢失敗: {res.text}")
    return

  data = res.json()
  all_pages = data.get("results", [])
  print(f"總共撈取到 {len(all_pages)} 筆頁面。")

  for page in all_pages:
    props = page.get("properties", {})

    # 抓取標題
    title = "無標題"
    for prop_name, prop_val in props.items():
      if prop_val.get("type") == "title":
        title_array = prop_val.get("title", [])
        if title_array:
          title = title_array[0].get("text", {}).get("content", "無標題")
        break

    if title == "TEST":
      print(f"\n===== 找到 TEST 任務的完整 Notion 屬性結構 =====")
      import json

      print(json.dumps(props, indent=2, ensure_ascii=False))
      print("============================================\n")


if __name__ == "__main__":
  run_daily_alert()
