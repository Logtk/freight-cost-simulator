"""「荷主目線 × 業者目線」タブ。積載率(物理)と集約数(FTL請求案件数)のギャップを可視化する。

配色は`palette.py`のSTATUS(good/warning/critical)を流用する。4象限に新規の
カテゴリカルパレットは割り当てない — `dataviz`スキルの検証で4色構成はall-pairs
不合格だった経緯があるため(`visibility_gap.py`と同じ判断)。「見え方に矛盾なし」を
good、「両方厳しい」をcritical、2つの食い違い象限をwarningで表す(ラベル・位置で
2つの食い違いは区別できるので色を分ける必要はない)。
"""

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis import data_access as da, perspective_gap as pg
from src.common import palette
from src.common.labels import label

QUADRANT_COLOR = {
    pg.QUADRANT_HIGH_HIGH: palette.STATUS["good"],
    pg.QUADRANT_LOW_LOW: palette.STATUS["critical"],
    pg.QUADRANT_HIGH_LOW: palette.STATUS["warning"],
    pg.QUADRANT_LOW_HIGH: palette.STATUS["warning"],
}


@st.cache_data(show_spinner="荷主目線・業者目線を集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    orders = da.load_trip_orders(_conn)
    courses = da.load_synth_courses(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    return trips, orders, courses, labor


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    trips, orders, courses, labor = _load(conn)
    depreciation = 5500.0

    st.caption(
        "実務は特積(混載)だが荷主にはFTLとして請求している場合、物理積載率(荷主が"
        "体感しうる軸)と集約案件数(業者だけが見える、実収益を決める軸)は別物であり、"
        "食い違いが生まれうる。その食い違いこそが交渉・値決めの検討余地になる。"
    )

    gap = pg.build_course_gap(
        trips, orders, courses, labor_cost_per_hour=labor, depreciation_per_trip=depreciation
    )
    summary = pg.summarize(gap)

    st.subheader("積載率 × 集約数の散布図")

    gap = gap.copy()
    gap["color"] = gap["quadrant"].map(QUADRANT_COLOR)

    points = (
        alt.Chart(gap)
        .mark_circle(size=110, opacity=0.85)
        .encode(
            x=alt.X("loaded_ratio:Q", title=label("loaded_ratio"), scale=alt.Scale(zero=False)),
            y=alt.Y("orders_per_trip:Q", title=label("orders_per_trip"), scale=alt.Scale(zero=False)),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("course_name:N", title="コース"),
                alt.Tooltip("quadrant_label:N", title="象限"),
                alt.Tooltip("loaded_ratio:Q", title=label("loaded_ratio"), format=".2f"),
                alt.Tooltip("orders_per_trip:Q", title=label("orders_per_trip"), format=".1f"),
                alt.Tooltip("actual_gross_profit_yen:Q", title=label("actual_gross_profit_yen"), format=",.0f"),
            ],
        )
    )
    v_rule = alt.Chart(pd.DataFrame({"x": [summary.loaded_ratio_median]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeDash=[4, 4]
    ).encode(x="x:Q")
    h_rule = alt.Chart(pd.DataFrame({"y": [summary.orders_per_trip_median]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeDash=[4, 4]
    ).encode(y="y:Q")

    st.altair_chart((points + v_rule + h_rule).properties(height=380), use_container_width=True)
    st.caption(
        "点線は中央値。🟢見え方に矛盾なし 🔴両方厳しい 🟡食い違い(位置で2種類を区別、"
        "合成データによる試算)。"
    )

    st.divider()

    st.subheader("象限ごとの実際の粗利")
    quad_profit = gap.groupby("quadrant_label")["actual_gross_profit_yen"].mean().sort_values(ascending=False)
    cols = st.columns(len(quad_profit))
    for col, (name, value) in zip(cols, quad_profit.items()):
        col.metric(name, f"¥{value:,.0f}")

    st.divider()

    st.subheader("食い違いコース一覧")
    conflict = summary.conflict_courses.sort_values("actual_gross_profit_yen", ascending=False)
    if conflict.empty:
        st.success("この期間では、積載率と集約数の見え方が食い違うコースはなかった。")
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    label("course_name"): conflict["course_name"],
                    label("quadrant"): conflict["quadrant_label"],
                    label("loaded_ratio"): conflict["loaded_ratio"].round(2),
                    label("orders_per_trip"): conflict["orders_per_trip"].round(1),
                    label("actual_gross_profit_yen"): conflict["actual_gross_profit_yen"].round().astype(int),
                }
            ),
            hide_index=True, use_container_width=True,
        )

    for q in pg.CONFLICT_QUADRANTS:
        title, body = pg.QUADRANT_NARRATIVE[q]
        st.markdown(f"**{title}**  \n{body}")
