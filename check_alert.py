def extract_date_from_prop(prop_info):
    """終極強效日期解析器：深度穿透 Notion 的 Formula、Rollup 與 Date 結構"""
    if not prop_info or not isinstance(prop_info, dict):
        return None
    
    candidates = []

    # 遞迴暴力搜尋字典與列表內所有可能的日期字串
    def deep_search(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ["start", "string", "content", "date", "formula", "rollup"] and isinstance(v, (str, int, float)):
                    candidates.append(str(v))
                elif isinstance(v, (dict, list)):
                    deep_search(v)
        elif isinstance(obj, list):
            for item in obj:
                deep_search(item)

    # 執行深度搜尋
    deep_search(prop_info)

    # 針對標準型態額外直接抓取
    p_type = prop_info.get("type")
    if p_type == "formula":
        f_val = prop_info.get("formula", {})
        if isinstance(f_val, dict):
            if f_val.get("date") and isinstance(f_val["date"], dict):
                if f_val["date"].get("start"):
                    candidates.append(f_val["date"]["start"])
            if f_val.get("string"):
                candidates.append(f_val["string"])
    elif p_type == "date":
        d_val = prop_info.get("date", {})
        if isinstance(d_val, dict) and d_val.get("start"):
            candidates.append(d_val["start"])

    # 開始比對並轉為日期
    for date_str in candidates:
        if not date_str:
            continue
        cleaned = str(date_str).strip().replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
        match = re.search(r'\d{4}-\d{1,2}-\d{1,2}', cleaned)
        if match:
            try:
                parts = match.group(0).split('-')
                formatted_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                return datetime.strptime(formatted_date, "%Y-%m-%d")
            except ValueError:
                continue
    return None
