# -*- coding: utf-8 -*-
# 跟单组监督系统（Streamlit + Python）
# 记录每个组、每个跟单员的每日跟进情况，并可视化趋势

from datetime import date, datetime

import pandas as pd
import streamlit as st
import altair as alt

import gspread
from google.oauth2.service_account import Credentials

# ================== 0. 页面配置 ==================
st.set_page_config(
    page_title="跟单监督面板",
    layout="wide",
)

st.title("📊 跟单组监督系统（Daily Follow-up Tracker）")

# ================== 0.1 Google Sheet 存储配置 ==================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

@st.cache_resource
def get_gsheet_worksheet():
    # 调试：看看当前到底有哪些 secrets key
    st.sidebar.write("Secrets keys:", list(st.secrets.keys()))

    try:
        sa_info = dict(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
    except KeyError:
        st.error(
            f"❌ 未找到 GCP_SERVICE_ACCOUNT_JSON，请到 Settings → Secrets 配置。当前 secrets keys: {list(st.secrets.keys())}"
        )
        st.stop()

    try:
        sheet_id = st.secrets["GSHEET_SPREADSHEET_ID"]
    except KeyError:
        st.error(
            f"❌ 未找到 GSHEET_SPREADSHEET_ID，请到 Settings → Secrets 配置。当前 secrets keys: {list(st.secrets.keys())}"
        )
        st.stop()

    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sh = client.open_by_key(sheet_id)

    try:
        ws = sh.worksheet("log")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="log", rows=1000, cols=7)
        ws.append_row(
            ["date", "group", "member",
             "incident_number", "tech_followup",
             "custom_followup", "score"]
        )
    return ws



@st.cache_data
def load_log() -> pd.DataFrame:
    """
    从 Google Sheet 读取全部日志数据。
    返回字段：date, group, member, incident_number, tech_followup, custom_followup, score
    """
    ws = get_gsheet_worksheet()
    try:
        records = ws.get_all_records()
    except Exception as e:
        st.sidebar.error(f"读取 Google Sheet 失败：{e}")
        records = []

    base_cols = [
        "date",
        "group",
        "member",
        "incident_number",
        "tech_followup",
        "custom_followup",
        "score",
    ]

    if not records:
        return pd.DataFrame(columns=base_cols)

    df = pd.DataFrame.from_records(records)
    # 补齐缺失列
    for c in base_cols:
        if c not in df.columns:
            df[c] = pd.NA

    # 统一日期格式
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def save_single_entry(entry: dict):
    """
    保存单条记录到 Google Sheet（追加一行）。
    entry 字段：
        date, group, member, incident_number, tech_followup, custom_followup, score
    """
    ws = get_gsheet_worksheet()

    d = entry.copy()
    if isinstance(d["date"], (datetime, date)):
        d["date"] = d["date"].strftime("%Y-%m-%d")
    else:
        d["date"] = str(d["date"])

    row = [
        d.get("date", ""),
        d.get("group", ""),
        d.get("member", ""),
        d.get("incident_number", ""),
        d.get("tech_followup", ""),
        d.get("custom_followup", ""),
        int(d.get("score", 0)),
    ]

    ws.append_row(row)


def delete_record(idx_in_df: int):
    """
    根据 DataFrame 的 index 删除一条记录。
    注意：
    - get_all_records() 返回的是从表格第 2 行开始的数据（第 1 行是表头）
    - DataFrame 的 index 0 对应表格第 2 行，以此类推
    """
    ws = get_gsheet_worksheet()
    try:
        sheet_row = int(idx_in_df) + 2  # +1 因为 index 从 0；再 +1 跳过表头
        ws.delete_rows(sheet_row)
    except Exception as e:
        st.warning(f"删除记录时出错：{e}")


# ================== 1. 基础数据配置 ==================

GROUPS = {
    "The First Group": ["Desiree", "Jessica Dollins"],
    "The Second Group": ["Christie Debrah", "Michelly Maldonado"],
    "The Third Group": ["Abbigale Lee"],  # 后期你可以在这里加人
    "The FOURTH Group": ["Kris Ramsey"],
}

# 增加 Normal、Blank 选项
FOLLOWUP_OPTIONS = [
    "Normal",  # 默认：一切正常（合格）
    "Blank",  # 空白，也视为不及格
    "Up to date (0 days)",
    "No update for 2 days",
    "No update for 3 days",
    "No update for 4 days",
    "No update for 5 days",
]


# ================== 工具函数 ==================


def parse_days(option: str) -> int:
    """
    转换成“未更新天数”
    Normal = 0
    Blank = 4
    No update for X days = X
    其它默认 0
    """
    if option == "Normal":
        return 0
    if option == "Blank":
        return 4  # ⭐ Blank 当作 4 天未更新
    if "No update for" in option:
        try:
            return int(option.split("for")[1].split("days")[0].strip())
        except Exception:
            return 0
    return 0


def calc_score(tech_option: str, custom_option: str) -> int:
    """
    计算表现分数：
    - 取 Tech / Custom 里“最大未更新天数”（Blank 按 4 天算）
    - score = -max_days   （越小越差，曲线越往下）
    """
    days_tech = parse_days(tech_option)
    days_custom = parse_days(custom_option)
    max_days = max(days_tech, days_custom)
    return -max_days


# ================== 2. 页面主逻辑 ==================

# 选择记录日期（默认今天）
record_date = st.date_input("📅 记录日期（通常选今天）", value=date.today())

# 读取历史数据
log_df = load_log()

# ========= 2.1 当日总览（仿 Excel 四个大块） =========
st.markdown("### 📋 当日跟进总览（按小组 & 跟单员）")

if log_df.empty:
    st.info("目前还没有任何历史数据。")
else:
    # 确保日期格式正确，并按当天过滤
    day_df = log_df.copy()
    day_df["date"] = pd.to_datetime(day_df["date"], errors="coerce")
    day_df = day_df[day_df["date"].dt.date == record_date]

    if day_df.empty:
        st.info(f"📅 {record_date} 当天还没有任何记录。")
    else:
        # 四列：四个组
        overview_cols = st.columns(len(GROUPS))

        for (group_name, members), col in zip(GROUPS.items(), overview_cols):
            with col:
                # 组标题
                st.markdown(
                    f"""
                    <div style="
                        background-color:#4F81BD;
                        color:white;
                        font-weight:bold;
                        padding:4px 6px;
                        border-radius:4px;
                        text-align:center;
                        margin-bottom:4px;
                    ">
                        {group_name}（{record_date}）
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # 表头：Incident / Tech / Custom
                st.markdown(
                    """
                    <div style="
                        background-color:#D9D9D9;
                        font-weight:bold;
                        padding:2px 4px;
                        border-radius:3px;
                        font-size:12px;
                    ">
                        Incideng Number&nbsp;&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;Tech Followup&nbsp;&nbsp;|&nbsp;&nbsp;Custom Followup
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # 每个成员一块
                for member in members:
                    st.markdown(
                        f"""
                        <div style="
                            background-color:#F2F2F2;
                            font-weight:bold;
                            padding:2px 4px;
                            margin-top:6px;
                            border-radius:3px;
                            font-size:12px;
                        ">
                            {member}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    mdf = day_df[
                        (day_df["group"] == group_name)
                        & (day_df["member"] == member)
                    ][["incident_number", "tech_followup", "custom_followup"]]

                    if mdf.empty:
                        st.caption("（当天暂无记录）")
                    else:
                        mdf_display = mdf.rename(
                            columns={
                                "incident_number": "Incident",
                                "tech_followup": "Tech",
                                "custom_followup": "Customer",
                            }
                        )
                        st.table(mdf_display.reset_index(drop=True))

st.write("---")

# ================== 3. 数据录入区域（按组显示，每人单独保存） ==================

st.subheader("📝 每日记录（按小组，每人单独保存）")

cols = st.columns(len(GROUPS))

for (group_name, members), col in zip(GROUPS.items(), cols):
    with col:
        st.markdown(f"### {group_name}")

        # 找这个组最近一次更新日期
        group_logs = log_df[log_df["group"] == group_name]
        if not group_logs.empty:
            valid_dates = pd.to_datetime(group_logs["date"], errors="coerce").dropna()
            if not valid_dates.empty:
                last_date = valid_dates.max().date()
                st.caption(f"Last update: {last_date}")
            else:
                st.caption("Last update: N/A")
        else:
            st.caption("Last update: N/A")

        st.write("")

        # 👉 每个成员一块：有自己的输入 + 保存按钮
        for member in members:
            st.markdown(f"**👤 {member}**")

            incident_key = f"incident_{group_name}_{member}"
            tech_key = f"tech_{group_name}_{member}"
            custom_key = f"custom_{group_name}_{member}"
            reset_key = f"reset_{group_name}_{member}"

            # 若上次保存后需要重置输入框
            if st.session_state.get(reset_key, False):
                if incident_key in st.session_state:
                    st.session_state[incident_key] = ""
                st.session_state[tech_key] = "Normal"
                st.session_state[custom_key] = "Normal"
                st.session_state[reset_key] = False

            incident = st.text_input(
                f"Incident Number - {member}",
                key=incident_key,
                placeholder="例如：W102025-00123，多个可用逗号分隔",
            )

            tech_follow = st.selectbox(
                f"Tech Followup - {member}",
                FOLLOWUP_OPTIONS,
                key=tech_key,
            )

            custom_follow = st.selectbox(
                f"Custom Followup - {member}",
                FOLLOWUP_OPTIONS,
                key=custom_key,
            )

            score = calc_score(tech_follow, custom_follow)

            st.markdown(
                f"<span style='font-size:12px;color:#666;'>当前分数(score)：{score}（越低代表未更新天数越多；Blank 视为 4 天未跟进）</span>",
                unsafe_allow_html=True,
            )

            # ✅ 每个人下面都有自己的保存按钮
            if st.button("💾 保存该人员记录", key=f"save_{group_name}_{member}"):
                entry = {
                    "date": record_date,
                    "group": group_name,
                    "member": member,
                    "incident_number": incident,
                    "tech_followup": tech_follow,
                    "custom_followup": custom_follow,
                    "score": score,
                }
                save_single_entry(entry)
                st.success(f"✅ 已保存 {member} 在 {record_date} 的记录")

                # 标记需要重置输入框，然后刷新页面
                st.session_state[reset_key] = True
                st.rerun()

            st.write("---")

st.write("---")

# ================== 4. 可视化分析 ==================

st.subheader("📉 跟进表现趋势（越往下代表越差）")

log_df = load_log()

if log_df.empty:
    st.info("目前还没有历史数据，请先保存至少一条记录。")
else:
    log_df["date"] = pd.to_datetime(log_df["date"], errors="coerce")

    # ---- 组过滤：多选，默认全选 ----
    group_options = sorted(log_df["group"].dropna().unique().tolist())
    selected_groups = st.multiselect(
        "选择小组（可多选，默认全部）",
        options=group_options,
        default=group_options,
    )
    if not selected_groups:
        selected_groups = group_options

    df_group_filtered = log_df[log_df["group"].isin(selected_groups)].copy()

    # ---- 成员过滤：只影响明细，不影响折线（折线按组汇总）----
    member_options = (
        df_group_filtered["member"].dropna().unique().tolist()
        if not df_group_filtered.empty
        else []
    )
    ALL_MEMBERS_LABEL = "All members (所有成员)"

    member_selected = st.selectbox(
        "选择跟单员（默认全部，仅影响下方明细）",
        [ALL_MEMBERS_LABEL] + member_options,
        index=0,
    )

    if member_selected == ALL_MEMBERS_LABEL:
        df_for_detail = df_group_filtered.copy()
    else:
        df_for_detail = df_group_filtered[
            df_group_filtered["member"] == member_selected
        ].copy()

    # ---------- 原始明细：放在图表前，默认收起 ----------
    with st.expander("🔍 原始明细（可删除）", expanded=False):
        if df_for_detail.empty:
            st.warning("当前筛选条件下没有明细记录。")
        else:
            display_df = df_for_detail.copy()

            # 按日期+原始 index 排序（越新越上）
            display_df["__idx"] = display_df.index
            display_df = display_df.sort_values(
                by=["date", "__idx"],
                ascending=[False, False],
                na_position="last",
            )

            header_cols = st.columns([2, 3, 3, 3, 3, 1])
            header_cols[0].markdown("**日期**")
            header_cols[1].markdown("**Group**")
            header_cols[2].markdown("**Member**")
            header_cols[3].markdown("**Incident**")
            header_cols[4].markdown("**状态(Tech / Customer)**")
            header_cols[5].markdown("**操作**")

            for idx, row in display_df.iterrows():
                row_cols = st.columns([2, 3, 3, 3, 3, 1])

                date_str = "" if pd.isna(row["date"]) else row["date"].strftime(
                    "%Y-%m-%d"
                )

                row_cols[0].write(date_str)
                row_cols[1].write(row.get("group", ""))
                row_cols[2].write(row.get("member", ""))
                row_cols[3].write(row.get("incident_number", ""))
                row_cols[4].write(
                    f"T: {row.get('tech_followup', '')} | C: {row.get('custom_followup', '')}"
                )

                # 删除时使用原始 index（__idx）
                rec_idx = int(row["__idx"])

                if row_cols[5].button("🗑️ 删除", key=f"del_{rec_idx}"):
                    delete_record(rec_idx)
                    st.success("记录已删除")
                    st.rerun()

    # ---------- 折线图：每条线表示一个组（按日期取该组平均 score） ----------
    chart_src = df_group_filtered.dropna(subset=["date"]).copy()

    if chart_src.empty:
        st.info("所选小组的数据中日期无效，暂时无法绘制趋势图。")
    else:
        chart_group_df = (
            chart_src.groupby(["date", "group"], as_index=False)["score"]
            .mean()
            .sort_values("date")
        )
        chart_group_df["date_str"] = chart_group_df["date"].dt.strftime("%Y-%m-%d")

        st.markdown(
            "**各组平均分数趋势：**  \n"
            "每条线 = 一个组，当天该组所有成员的平均 score（Blank 按 4 天未跟进计入）。"
        )

        chart = (
            alt.Chart(chart_group_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T", title="日期"),
                y=alt.Y("score:Q", title="Score（越低越差）"),
                color=alt.Color("group:N", title="组别"),
                tooltip=[
                    "date_str:N",
                    "group:N",
                    "score:Q",
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(chart, use_container_width=True)

# ================== 5. 调试用：查看全部原始数据 ==================

st.write("---")
with st.expander("📄 查看全部原始数据（调试用）"):
    debug_df = load_log()
    st.dataframe(debug_df, use_container_width=True)
