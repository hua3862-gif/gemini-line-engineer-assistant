from datetime import datetime, timedelta
import json
import os
from flask import Flask, abort, request
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import pandas as pd
import requests

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Notion 設定與資料庫 ID
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")  # 報修/缺失資料庫
PROGRESS_DB_ID = os.getenv("PROGRESS_DB_ID")  # 時程資料庫

# Gemini API 設定
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

NOTION_VERSION = "2022-06-28"
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


@app.route("/callback", methods=["POST"])
def callback():
  signature = request.headers.get("X-Line-Signature", "")
  body = request.get_data(as_text=True)
  app.logger.info("Request body: " + body)

  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  text = event.message.text.strip()
  reply_token = event.reply_token

  response_message = "收到您的訊息！"

  # 1. 處理「報修」指令
  if text.startswith("報修：") or text.startswith("報修:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    prompt = f"""
    請智慧分析以下工程報修文字，並精準拆解為以下欄位，以 JSON 格式回傳：
    - title: 缺失項目主旨（必填，請去除站別與位置後的實際問題描述）
    - severity: 嚴重程度 (例如：高、中、低、待評估，若文字未提及請填 "待評估")
    - date: 日期 (請將文字中的日期轉為 YYYY-MM-DD 格式，若未提及則填 "TODAY")
    - status: 狀態 (例如：未開始、進行中、已完成、延遲，若未提及請填 "未開始")
    - station: 站別 (請智慧辨識並統一轉為標準格式，例如：K6, K7, K9 等，若無則填 null)
    - location: 位置細節 (例如：現金房、機房、月台等詳細位置，若無則填 null)

    報修文字：{content}
    請僅回傳 JSON 格式字串，不要包含 markdown 標籤或額外文字。
    """
    try:
      ai_res = gemini_client.models.generate_content(
          model="gemini-2.5-flash", contents=prompt
      )
      parsed_data = json.loads(
          ai_res.text.strip().replace("```json", "").replace("```", "")
      )
    except Exception as e:
      parsed_data = {
          "title": content,
          "severity": "待評估",
          "date": "TODAY",
          "status": "未開始",
          "station": None,
          "location": None,
      }

    today_str = datetime.now().strftime("%Y-%m-%d")
    severity_val = parsed_data.get("severity") or "待評估"
    date_val = parsed_data.get("date")
    if not date_val or date_val == "TODAY":
      date_val = today_str
    status_val = parsed_data.get("status") or "未開始"

    properties = {
        "缺失項目": {"title": [{"text": {"content": parsed_data.get("title", content)}}]},
        "嚴重程度": {"select": {"name": str(severity_val)}},
        "日期": {"date": {"start": str(date_val)}},
        "狀態": {"select": {"name": str(status_val)}},
    }

    if parsed_data.get("station"):
      properties["站別"] = {"select": {"name": str(parsed_data["station"])}}
    if parsed_data.get("location"):
      properties["位置"] = {"rich_text": [{"text": {"content": str(parsed_data["location"])}}]}

    notion_data = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = (
          f"✅ 已成功寫入【工程缺失管理】:\n• 項目：{parsed_data.get('title', content)}\n• 站別："
          f"{parsed_data.get('station', '無')}\n• 位置：{parsed_data.get('location', '無')}\n• 狀態："
          f"{status_val}"
      )
    else:
      response_message = f"❌ 寫入失敗：{res.text}"

  # 2. 處理「時程」指令
  elif text.startswith("時程：") or text.startswith("時程:"):
    content = text.split("：" if "：" in text else ":", 1)[1].strip()

    prompt = f"""
    請智慧分析以下工程時程文字，並以 JSON 格式回傳以下欄位：
    - title: 工作項目名稱（必填）
    - system: 系統別 (若無法分辨或未提及，請務必填 "ALL"，可選值包含 ALL, RST, PSY, COM, SCD, SIG, PSD, AFC)
    - progress: 進度狀態 (例如：未開始、進行中、已完成、延遲，若未提及請填 "未開始")
    - relative_days: 相對天數數字 (例如 NTP+45 則填 45，若無則填 null)
    - contract_date: 契約規定完成日期 (格式 YYYY-MM-DD，若無則填 null)
    - target_date: 預定完成日/臨時性需求日期 (格式 YYYY-MM-DD，若無則填 null)
    - remark: 備註說明 (若無則填 null)

    時程文字：{content}
    請僅回傳 JSON 格式字串，不要包含 markdown 標籤或額外文字。
    """
    try:
      ai_res = gemini_client.models.generate_content(
          model="gemini-2.5-flash", contents=prompt
      )
      parsed_data = json.loads(
          ai_res.text.strip().replace("```json", "").replace("```", "")
      )
    except Exception as e:
      parsed_data = {
          "title": content,
          "system": "ALL",
          "progress": "未開始",
          "relative_days": None,
          "contract_date": None,
          "target_date": None,
          "remark": None,
      }

    sys_val = parsed_data.get("system") or "ALL"
    progress_val = parsed_data.get("progress") or "未開始"
    contract_d = parsed_data.get("contract_date")
    target_d = parsed_data.get("target_date")

    if contract_d and not target_d:
      target_d = contract_d

    properties = {
        "title": {"title": [{"text": {"content": parsed_data.get("title", content)}}]},
        "系統別": {"multi_select": [{"name": str(sys_val)}]},
        "進度/狀態": {"select": {"name": str(progress_val)}},
    }

    if parsed_data.get("relative_days") is not None:
      properties["相對天數(NTP+天)"] = {"number": int(parsed_data["relative_days"])}
    if contract_d:
      properties["契約規定完成日"] = {"date": {"start": str(contract_d)}}
    if target_d:
      properties["預計完成日"] = {"date": {"start": str(target_d)}}
    if parsed_data.get("remark"):
      properties["備註"] = {"rich_text": [{"text": {"content": str(parsed_data["remark"])}}]}

    notion_data = {"parent": {"database_id": PROGRESS_DB_ID}, "properties": properties}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json=notion_data,
    )
    if res.status_code == 200:
      response_message = (
          f"✅ 已成功新增至【時程資料庫】:\n• 項目：{content}\n• 系統別：{sys_val}\n•"
          f" 進度：{progress_val}"
      )
    else:
      response_message = f"❌ 時程新增失敗：{res.text}"

  else:
    response_message = (
        "💡 使用說明：\n• 輸入「報修：[內容]」\n• 輸入「時程：[內容]」"
    )

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    line_bot_api.reply_message_with_http_info(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=response_message)],
        )
    )


# 3. 智慧多階段告警路由 (配合您要求的各個時間點)
@app.route("/check-schedule", methods=["GET"])
def check_schedule():
  ALERT_GROUP_ID = "C5c0b9ad86a00149bb16b5db6a8d0b622"
  today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

  # 從 Notion 讀取時程資料庫所有頁面
  url = f"https://api.notion.com/v1/databases/{PROGRESS_DB_ID}/query"
  all_pages = []
  has_more = True
  start_cursor = None

  while has_more:
    payload = {}
    if start_cursor:
      payload["start_cursor"] = start_cursor
    res = requests.post(url, headers=notion_headers, json=payload)
    if res.status_code != 200:
      return f"Error querying Notion: {res.text}", 500
    data = res.json()
    all_pages.extend(data.get("results", []))
    has_more = data.get("has_more", False)
    start_cursor = data.get("next_cursor")

  # 解析資料
  tasks = []
  for page in all_pages:
    props = page.get("properties", {})
    # 取得標題
    title_list = props.get("title", {}).get("title", [])
    title = title_list[0].get("text", {}).get("content", "無標題") if title_list else "無標題"

    # 取得狀態
    status_obj = props.get("進度/狀態", {}).get("select")
    status = status_obj.get("name") if status_obj else "未開始"

    # 若已完成則跳過告警
    if status == "已完成":
      continue

    # 取得預計完成日
    date_obj = props.get("預計完成日", {}).get("date")
    due_date_str = date_obj.get("start") if date_obj else None

    if due_date_str:
      due_date = datetime.strptime(due_date_str[:10], "%Y-%m-%d")
      diff_days = (due_date - today).days  # 負數代表已逾期，正數代表距離到期還有幾天

      tasks.append({"title": title, "status": status, "due_date": due_date_str, "diff_days": diff_days})

  # 分類告警群組
  # 到期前1周: diff_days == 7
  # 到期前1日: diff_days == 1
  # 當日: diff_days == 0
  # 到期後3日: diff_days == -3
  # 到期後1周: diff_days == -7
  # 到期後2周: diff_days == -14
  # 到期後每月 (滿 30 天的倍數，例如 -30, -60, -90...)

  alerts = {"before_7": [], "before_1": [], "today": [], "after_3": [], "after_7": [], "after_14": [], "after_monthly": []}

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

  # 組裝訊息
  msg_lines = ["📢 【桃園棕線時程進度自動告警】\n"]
  has_alert = False

  if alerts["before_7"]:
    has_alert = True
    msg_lines.append("⏳ 【將屆：剩餘 1 週】")
    for t in alerts["before_7"]:
      msg_lines.append(f"• {t['title']} (預定：{t['due_date']})")
    msg_lines.append("")

  if alerts["before_1"]:
    has_alert = True
    msg_lines.append("⚠️ 【將屆：剩餘 1 天】")
    for t in alerts["before_1"]:
      msg_lines.append(f"• {t['title']} (預定：{t['due_date']})")
    msg_lines.append("")

  if alerts["today"]:
    has_alert = True
    msg_lines.append("🚨 【今日到期】")
    for t in alerts["today"]:
      msg_lines.append(f"• {t['title']} (預定：{t['due_date']})")
    msg_lines.append("")

  if alerts["after_3"]:
    has_alert = True
    msg_lines.append("❌ 【已逾期 3 天】")
    for t in alerts["after_3"]:
      msg_lines.append(f"• {t['title']} (原定：{t['due_date']})")
    msg_lines.append("")

  if alerts["after_7"]:
    has_alert = True
    msg_lines.append("❌ 【已逾期 1 週】")
    for t in alerts["after_7"]:
      msg_lines.append(f"• {t['title']} (原定：{t['due_date']})")
    msg_lines.append("")

  if alerts["after_14"]:
    has_alert = True
    msg_lines.append("❌ 【已逾期 2 週】")
    for t in alerts["after_14"]:
      msg_lines.append(f"• {t['title']} (原定：{t['due_date']})")
    msg_lines.append("")

  if alerts["after_monthly"]:
    has_alert = True
    msg_lines.append("❗ 【長期逾期提醒 (滿月倍數)】")
    for t in alerts["after_monthly"]:
      msg_lines.append(f"• {t['title']} (原定：{t['due_date']}, 已逾期 {abs(t['diff_days'])} 天)")
    msg_lines.append("")

  if not has_alert:
    return "No schedule alerts for today."

  final_msg = "\n".join(msg_lines)

  # 推播至指定群組
  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    line_bot_api.push_message(
        PushMessageRequest(
            to=ALERT_GROUP_ID, messages=[TextMessage(text=final_msg)]
        )
    )

  return "Schedule alerts sent successfully."


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=10000)
