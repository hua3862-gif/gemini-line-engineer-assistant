import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="工地缺失管理儀表板", layout="wide")

st.title("🚧 工地工程缺失管理儀表板")

# 1. 模擬載入數據 (建議改為連接 Google Sheets)
@st.cache_data
def load_data():
    # 實際運作時，這裡會是 pd.read_csv 或連結 Google Sheet
    data = {
        '缺失類型': ['混凝土龜裂', '鋼筋裸露', '水管滲漏', '混凝土龜裂', '安全防護不足'],
        '嚴重程度': ['高', '高', '中', '低', '高'],
        '狀態': ['待修復', '已修復', '待修復', '待修復', '已修復']
    }
    return pd.DataFrame(data)

df = load_data()

# 2. 儀表板排版
col1, col2 = st.columns(2)

with col1:
    st.subheader("缺失類型分佈")
    fig = px.pie(df, names='缺失類型')
    st.plotly_chart(fig)

with col2:
    st.subheader("待修復 vs 已修復")
    fig_status = px.bar(df, x='狀態', color='嚴重程度')
    st.plotly_chart(fig_status)

# 3. 數據表格
st.subheader("缺失清單明細")
st.dataframe(df)
