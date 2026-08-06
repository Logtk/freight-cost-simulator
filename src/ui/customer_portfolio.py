"""「経営レポート」タブ(L4)。視座を現場の原価管理から経営のポートフォリオ判断へ上げる。

配色は`perspective_gap.py`と同じくSTATUS(good/warning/critical)を流用する。4象限に
新規のカテゴリカルパレットは割り当てない(`palette.py`の検証経緯を参照)。
"""

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis import customer_portfolio as cp, data_access as da
from src.common import palette
from src.common.labels import label

DEPRECIATION_PER_TRIP = 5500.0

CUSTOMER_QUADRANT_COLOR = {
    cp.QUADRANT_MAINTAIN: palette.STATUS["good"],
    cp.QUADRANT_EXIT: palette.STATUS["critical"],
    cp.QUADRANT_RAISE: palette.STATUS["warning"],
    cp.QUADRANT_GROW: palette.STATUS["warning"],
}

VALUE_GAP_COLOR = {
    cp.VALUE_GAP_BALANCED_HIGH: palette.STATUS["good"],
    cp.VALUE_GAP_BALANCED_LOW: palette.STATUS["good"],
    cp.VALUE_GAP_UNDERPRICED: palette.STATUS["warning"],
    cp.VALUE_GAP_OVERPRICED: palette.STATUS["warning"],
}


@st.cache_data(show_spinner="顧客ポートフォリオを集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    orders = da.load_trip_orders(_conn)
    customers = da.load_customers(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    order_profit = cp.build_order_profit(trips, orders, labor, DEPRECIATION_PER_TRIP)
    return order_profit, orders, customers


def _scatter_2x2(df, x, y, color_col, x_title, y_title, tooltip_cols, x_med, y_med, height=340):
    v_rule = alt.Chart(pd.DataFrame({"x": [x_med]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeDash=[4, 4]
    ).encode(x="x:Q")
    h_rule = alt.Chart(pd.DataFrame({"y": [y_med]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeDash=[4, 4]
    ).encode(y="y:Q")
    points = (
        alt.Chart(df)
        .mark_circle(size=140, opacity=0.85)
        .encode(
            x=alt.X(f"{x}:Q", title=x_title, scale=alt.Scale(zero=False)),
            y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=False)),
            color=alt.Color(f"{color_col}:N", scale=None, legend=None),
            tooltip=tooltip_cols,
        )
    )
    return (points + v_rule + h_rule).properties(height=height)


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    order_profit, orders, customers = _load(conn)

    st.caption(
        "**現場での使い方**: 月次カルテで低採算コースを見つけた後、それが特定の顧客に偏っていないか、"
        "その顧客との取引全体がどうなっているかを確認する。四半期に一度、値上げ交渉・撤退検討の"
        "優先順位を見直すタイミングで開く。"
    )

    # ---- 部署P/Lサマリー -----------------------------------------------------
    st.subheader("部署P/Lサマリー")
    months = sorted(order_profit["month"].unique())
    selected_month = st.selectbox(
        "月を選択", months, index=len(months) - 1, key="portfolio_month_select"
    )
    pl = cp.build_pl_summary(order_profit, selected_month)

    m1, m2, m3 = st.columns(3)
    m1.metric(label("revenue_yen"), f"¥{pl['revenue_yen']:,.0f}")
    m2.metric(label("cost_yen"), f"¥{pl['cost_yen']:,.0f}")
    m3.metric(label("profit_yen"), f"¥{pl['profit_yen']:,.0f}", delta=f"粗利率 {pl['profit_rate']:.1%}")

    if pl["has_yoy"]:
        st.metric(
            f"前年同月({pl['yoy_month']})比",
            f"¥{pl['yoy_diff_yen']:+,.0f}",
            delta=f"{pl['yoy_diff_yen'] / pl['yoy_profit_yen']:+.1%}" if pl["yoy_profit_yen"] else None,
        )
    else:
        st.caption("前年同月のデータが無いため、前年同月比は表示できない(データ開始から12ヶ月未満)。")

    st.divider()

    # ---- 顧客ポートフォリオ2×2 -----------------------------------------------
    st.subheader("顧客ポートフォリオ(全期間)")
    st.caption("売上規模 × 粗利率。中央値で4象限に分ける。単月ではなく全期間の集計。")

    portfolio = cp.build_customer_portfolio(order_profit, customers)
    rev_med = float(portfolio["revenue_yen"].median())
    rate_med = float(portfolio["profit_rate"].median())
    portfolio = portfolio.copy()
    portfolio["color"] = portfolio["quadrant"].map(CUSTOMER_QUADRANT_COLOR)

    chart = _scatter_2x2(
        portfolio, "revenue_yen", "profit_rate", "color",
        label("revenue_yen"), label("profit_rate"),
        [
            alt.Tooltip("customer_name:N", title="顧客"),
            alt.Tooltip("quadrant_label:N", title="象限"),
            alt.Tooltip("revenue_yen:Q", title=label("revenue_yen"), format=",.0f"),
            alt.Tooltip("profit_rate:Q", title=label("profit_rate"), format=".1%"),
        ],
        rev_med, rate_med,
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("🟢維持 🔴撤退検討 🟡値上げ交渉・育成(位置で区別、合成データによる試算)。")

    st.dataframe(
        pd.DataFrame(
            {
                label("customer_name"): portfolio["customer_name"],
                label("industry"): portfolio["industry"],
                label("revenue_yen"): portfolio["revenue_yen"].round().astype(int),
                label("profit_rate"): (portfolio["profit_rate"] * 100).round(1),
                label("quadrant"): portfolio["quadrant_label"],
            }
        ),
        hide_index=True, use_container_width=True,
    )

    st.divider()

    # ---- 集中リスク表 ---------------------------------------------------------
    st.subheader("集中リスク")
    st.caption("上位顧客への売上依存度と、その顧客の採算を並べる。")
    conc = cp.build_concentration_risk(portfolio, top_n=3)
    st.dataframe(
        pd.DataFrame(
            {
                label("customer_name"): conc["customer_name"],
                label("revenue_share"): (conc["revenue_share"] * 100).round(1),
                label("cumulative_share"): (conc["cumulative_share"] * 100).round(1),
                label("profit_rate"): (conc["profit_rate"] * 100).round(1),
            }
        ),
        hide_index=True, use_container_width=True,
    )
    top1_share = conc.iloc[0]["cumulative_share"] if not conc.empty else 0
    top1_rate = conc.iloc[0]["profit_rate"] if not conc.empty else 0
    median_rate = float(portfolio["profit_rate"].median())
    if not conc.empty and top1_rate < median_rate:
        st.warning(
            f"最上位顧客だけで売上の**{conc.iloc[0]['revenue_share']:.1%}**を占めるが、"
            f"粗利率は全社中央値({median_rate:.1%})を下回っている。依存度の高さが経営リスクになる。"
        )

    st.divider()

    # ---- 貨物価値×運賃のギャップ -----------------------------------------------
    st.subheader("貨物価値 × 運賃のギャップ")
    st.caption(
        "今のモデルは運賃を距離・車格・拘束時間からしか決めておらず、貨物の経済価値を見ていない。"
        "「高価値貨物なのに運賃が低い」「低価値貨物なのに運賃が高い」の食い違いを可視化する。"
    )

    value_gap = cp.build_value_gap(orders, customers)
    val_med = float(value_gap["cargo_value_yen"].median())
    ratio_med = float(value_gap["rate_to_value_ratio"].median())
    value_gap = value_gap.copy()
    value_gap["color"] = value_gap["quadrant"].map(VALUE_GAP_COLOR)

    chart2 = _scatter_2x2(
        value_gap, "cargo_value_yen", "rate_to_value_ratio", "color",
        label("cargo_value_yen"), label("rate_to_value_ratio"),
        [
            alt.Tooltip("customer_name:N", title="顧客"),
            alt.Tooltip("quadrant_label:N", title="象限"),
            alt.Tooltip("cargo_value_yen:Q", title=label("cargo_value_yen"), format=",.0f"),
            alt.Tooltip("rate_to_value_ratio:Q", title=label("rate_to_value_ratio"), format=".4f"),
        ],
        val_med, ratio_med,
    )
    st.altair_chart(chart2, use_container_width=True)
    st.caption("🟢価値相応の運賃 🟡食い違い(合成データによる試算)。")

    summary = cp.ValueGapSummary(value_gap)
    conflict = summary.conflict_customers.sort_values("cargo_value_yen", ascending=False)
    if not conflict.empty:
        st.dataframe(
            pd.DataFrame(
                {
                    label("customer_name"): conflict["customer_name"],
                    label("cargo_value_yen"): conflict["cargo_value_yen"].round().astype(int),
                    label("rate_to_value_ratio"): conflict["rate_to_value_ratio"].round(4),
                    label("quadrant"): conflict["quadrant_label"],
                }
            ),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    st.subheader("次にすること")
    st.markdown(
        "- 「値上げ交渉」象限の顧客は、月次カルテでどのコースが低採算の主因になっているか確認する\n"
        "- 集中リスクの高い顧客は、値上げ交渉のタイミングを慎重に検討する(離反の影響が大きいため)\n"
        "- 「高価値貨物なのに運賃が低い」顧客は、賠償リスクに応じた運賃見直しの候補にする"
    )
