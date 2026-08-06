"""「交渉管理」タブ(L3)。分析を行動に変える出口。月次で開く2つ目の理由(交渉の進捗確認)。
"""

import pandas as pd
import streamlit as st

from src.analysis import attribution, customer_portfolio as cp, data_access as da, negotiation as neg
from src.common.labels import label

DEPRECIATION_PER_TRIP = 5500.0


@st.cache_data(show_spinner="交渉候補を集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    orders = da.load_trip_orders(_conn)
    courses = da.load_synth_courses(_conn)
    customers = da.load_customers(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    target_rate = da.load_setting(_conn, "TARGET_PROFIT_RATE") or 0.10

    order_profit = cp.build_order_profit(trips, orders, labor, DEPRECIATION_PER_TRIP)
    portfolio = cp.build_customer_portfolio(order_profit, customers)
    as_of = trips["trip_date"].max()
    candidates = neg.build_candidates(portfolio, customers, target_rate, as_of)

    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor, DEPRECIATION_PER_TRIP)
    rate_sheet = neg.build_standard_rate_sheet(features, bm)
    course_name_map = courses.set_index("course_id")["course_name"].to_dict()

    return candidates, rate_sheet, course_name_map, target_rate


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    candidates, rate_sheet, course_name_map, target_rate = _load(conn)

    st.caption(
        "**現場での使い方**: 月次カルテ・経営レポートで低採算/依存度の高い顧客を確認した後、"
        "四半期に一度この画面を開く。① 交渉候補の優先順位を確認 → ② ステータスを更新 → "
        "③ 次回見直し日を決める、というサイクルを回す。"
    )

    # ---- 交渉候補リスト -----------------------------------------------------
    st.subheader("交渉候補リスト")
    st.caption(
        "優先度 = 売上規模 × 前回改定からの経過月数(大きく・長く据え置かれている顧客ほど優先)。"
        "目標粗利率に不足があれば加点する。要求増額目安は、据え置き期間に応じた緩やかな改定分に"
        "不足額を上乗せしたもの。"
    )
    st.dataframe(
        pd.DataFrame(
            {
                label("customer_name"): candidates["customer_name"],
                label("revenue_yen"): candidates["revenue_yen"].round().astype(int),
                label("profit_rate"): (candidates["profit_rate"] * 100).round(1),
                label("months_since_revision"): candidates["months_since_revision"],
                label("shortfall_yen"): candidates["shortfall_yen"].round().astype(int),
                label("target_increase_yen"): candidates["target_increase_yen"],
            }
        ),
        hide_index=True, use_container_width=True,
    )
    if (candidates["shortfall_yen"] == 0).all():
        st.caption(
            f"この期間は全顧客が目標粗利率({target_rate:.0%})を上回っている"
            "(不足額はいずれも0円)。優先度は経過月数由来の据え置き改定分のみで決まっている。"
        )

    st.divider()

    # ---- 候補の詳細とステータス更新 --------------------------------------------
    st.subheader("交渉を起票・更新する")
    options = candidates["customer_code"].tolist()
    name_map = candidates.set_index("customer_code")["customer_name"].to_dict()
    selected_code = st.selectbox(
        "顧客", options, format_func=lambda c: name_map.get(c, c), key="negotiation_customer_select"
    )
    candidate_row = candidates[candidates["customer_code"] == selected_code].iloc[0].to_dict()

    with st.expander("引き渡しテキスト(コピーして交渉資料作成に使う)", expanded=False):
        st.code(neg.format_handoff_text(candidate_row, target_rate), language=None)

    with st.form("negotiation_form"):
        status = st.selectbox("ステータス", neg.STATUSES)
        target_increase = st.number_input(
            "要求増額(円)", min_value=0, value=int(candidate_row["target_increase_yen"]), step=1000
        )
        agreed_increase = st.number_input("合意増額(円、合意時のみ)", min_value=0, value=0, step=1000)
        next_review = st.date_input("次回見直し日")
        memo = st.text_area("メモ")
        submitted = st.form_submit_button("保存する", width="stretch")

    if submitted:
        neg.save_negotiation(
            conn, selected_code, status, int(target_increase), next_review.isoformat(), memo,
        )
        st.success(f"{name_map.get(selected_code, selected_code)} の交渉記録を保存した。")
        st.cache_data.clear()

    st.divider()

    # ---- 交渉ステータス一覧 ---------------------------------------------------
    st.subheader("交渉ステータス一覧")
    negotiations = neg.load_negotiations(conn)
    if negotiations.empty:
        st.info("まだ起票された交渉が無い。上のフォームから記録を始める。")
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    label("customer_name"): negotiations["customer_name"],
                    label("opened_at"): negotiations["opened_at"],
                    label("status"): negotiations["status"],
                    label("target_increase_yen"): negotiations["target_increase_yen"],
                    label("agreed_increase_yen"): negotiations["agreed_increase_yen"],
                    label("next_review_date"): negotiations["next_review_date"],
                    label("memo"): negotiations["memo"],
                }
            ),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    # ---- 自社標準単価表 -------------------------------------------------------
    st.subheader("自社標準単価表")
    st.caption("コースの諸元(距離・車格)から構造的に期待される標準単価と、現行運賃を並べる。")
    rate_sheet_display = rate_sheet.copy()
    rate_sheet_display["course_name"] = rate_sheet_display["course_id"].map(course_name_map)
    st.dataframe(
        pd.DataFrame(
            {
                label("course_name"): rate_sheet_display["course_name"],
                "車種": rate_sheet_display["vehicle_code"],
                label("standard_cost_yen"): rate_sheet_display["standard_cost_yen"],
                label("standard_rate_yen"): rate_sheet_display["standard_rate_yen"],
                label("current_rate_yen"): rate_sheet_display["current_rate_yen"],
                label("diff_yen"): rate_sheet_display["diff_yen"],
            }
        ),
        hide_index=True, use_container_width=True,
    )
    st.download_button(
        "標準単価表をCSVでダウンロード",
        data=rate_sheet_display.to_csv(index=False).encode("utf-8-sig"),
        file_name="standard_rate_sheet.csv",
        mime="text/csv",
    )

    st.divider()

    st.subheader("次にすること")
    st.markdown(
        "- 優先度の高い顧客から、引き渡しテキストを使って"
        "`Freight_rate_hike_justification_template`側で交渉資料を作成する\n"
        "- 交渉を起票したら、次回見直し日を必ず設定する(放置を防ぐ)\n"
        "- 「合意」になったら合意増額を記録し、`customers.last_rate_revision`(実務上のマスタ)も"
        "更新する運用にする"
    )
