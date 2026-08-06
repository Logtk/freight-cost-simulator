"""「感度分析」タブ(L2)。外部環境の変動と自社の打ち手が、営業利益にどう効くかを見せる。

このタブは「スライダーを動かすと数字が変わる」だけで終わらせず、現場での運用シナリオ
(毎月何をどう見て、次に何をするか)を画面上に明示する。設計の経緯: L1.5・L1.5+で診断層は
育ったが、それを毎月の業務動線に接続する層(L1・L3)を後回しにしたままL2だけ足すと、
v0.1を作り直した理由(「一回使ったきりで終わる」)が高度な形で再発する、という指摘を受けて
この構成にした。
"""

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis import attribution, data_access as da, sensitivity as sn
from src.common import palette
from src.common.labels import label

LEVER_LABELS = {
    sn.LEVER_FUEL_PRICE: "燃料単価",
    sn.LEVER_LABOR_COST: "人件費単価",
    sn.LEVER_RATE_HIKE: "値上げ率",
    sn.LEVER_LOADED_RATIO: "積載率改善",
}

# 土台(自社では動かせない) / 打ち手(自社の努力)の対比。既存の2枠パレット(元はLv1/Lv3の
# 対比用、visibility_gap.pyで使用)をこのタブでは lv1→土台、lv3→打ち手 の意味で再利用する。
LEVER_COLOR = {
    "foundation": palette.CATEGORICAL_LIGHT["lv1"],
    "action": palette.CATEGORICAL_LIGHT["lv3"],
}

DEPRECIATION_PER_TRIP = 5500.0


@st.cache_data(show_spinner="運行実績を集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    courses = da.load_synth_courses(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    return trips, courses, labor


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    trips, courses, labor = _load(conn)
    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor, DEPRECIATION_PER_TRIP)

    st.caption(
        "**現場での使い方**: 毎月、燃料単価が確定したらこの画面を開く。"
        "① 土台スライダーを実際の値に合わせる → ② 影響が集中しているコースを確認する → "
        "③ 打ち手スライダーでどこまで打ち返せるか試す → ④ 打ち返しきれない分は交渉候補として次に持っていく。"
    )

    st.subheader("スライダー")
    col_f, col_a = st.columns(2)
    with col_f:
        st.markdown("**土台**(外部環境。自社では動かせない)")
        fuel_delta = st.slider(
            "燃料単価の変化(円/L)", -40.0, 40.0, 0.0, step=1.0,
            help="現在の想定燃料単価からの増減",
        )
        labor_delta = st.slider(
            "人件費単価の変化(円/h)", -500.0, 500.0, 0.0, step=50.0,
        )
    with col_a:
        st.markdown("**打ち手**(自社の努力。自社で動かせる)")
        rate_hike_pct = st.slider(
            "値上げ率(%)", -10, 20, 0, step=1,
            help="全コース一律に運賃をこの率だけ変える想定",
        )
        loaded_delta_pt = st.slider(
            "積載率の改善(pt)", -20, 20, 0, step=1,
            help="積んだ分だけ請求できるという実態を反映し、売上に連動する",
        )
        rate_hike = rate_hike_pct / 100.0
        loaded_delta = loaded_delta_pt / 100.0

    scenario = sn.Scenario(
        fuel_price_yen=fuel_delta, labor_cost_yen=labor_delta,
        rate_hike_pct=rate_hike, loaded_ratio_delta=loaded_delta,
    )
    baseline = sn.evaluate(features, bm)
    result = sn.evaluate(features, bm, scenario)
    diff = result.total_profit_yen - baseline.total_profit_yen

    st.divider()
    st.subheader("全社への影響")
    m1, m2, m3 = st.columns(3)
    m1.metric("現状の営業利益(1運行あたり平均・全コース計)", f"¥{baseline.total_profit_yen:,.0f}")
    m2.metric("シナリオ後の営業利益", f"¥{result.total_profit_yen:,.0f}", delta=f"{diff:+,.0f}円")
    ratio = diff / baseline.total_profit_yen if baseline.total_profit_yen else 0.0
    m3.metric("変化率", f"{ratio:+.1%}")

    if abs(diff) > 1:
        conc = sn.concentration_summary(baseline, result, courses, top_n=3)
        n_top = len(conc["top_courses"])
        if diff < 0:
            st.warning(
                f"このシナリオで営業利益の**{abs(ratio):.1%}**が失われ、"
                f"そのうち**{conc['concentration_ratio']:.0%}**は特定{n_top}コースに集中している。"
            )
        else:
            st.success(f"このシナリオで営業利益が**{ratio:+.1%}**改善する。")
        st.dataframe(
            pd.DataFrame(
                {
                    label("course_name"): conc["top_courses"]["course_name"],
                    "影響額(円)": conc["top_courses"]["impact_yen"].round().astype(int),
                }
            ),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    # ---- トルネードチャート -------------------------------------------------
    st.subheader("どの変数が一番効くか")
    st.caption("各レバーを現在のスライダー位置から単独で±振った場合の、全社営業利益への影響幅。")

    tornado = sn.tornado_sweep(features, bm, base_scenario=scenario)
    tornado["label"] = tornado["lever"].map(LEVER_LABELS)
    tornado["color"] = tornado["category"].map(LEVER_COLOR)
    tornado["x_lo"] = tornado[["impact_up_yen", "impact_down_yen"]].min(axis=1)
    tornado["x_hi"] = tornado[["impact_up_yen", "impact_down_yen"]].max(axis=1)
    order = tornado.sort_values("swing_yen")["label"].tolist()

    bars = (
        alt.Chart(tornado)
        .mark_bar(cornerRadiusEnd=4, height=20)
        .encode(
            y=alt.Y("label:N", title=None, sort=order),
            x=alt.X("x_lo:Q", title="全社営業利益への影響(円)"),
            x2="x_hi:Q",
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("label:N", title="レバー"),
                alt.Tooltip("impact_up_yen:Q", title="レバーを増やした場合", format=",.0f"),
                alt.Tooltip("impact_down_yen:Q", title="レバーを減らした場合", format=",.0f"),
            ],
        )
        .properties(height=160)
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeWidth=1.5
    ).encode(x="x:Q")
    st.altair_chart(bars + zero, use_container_width=True)
    st.caption("🟠土台(外部環境) 🔵打ち手(自社の努力)。詳細な方向はツールチップで確認。")

    st.divider()

    # ---- 損益分岐点分析 -----------------------------------------------------
    st.subheader("コース別の損益分岐点")
    name_map = courses.set_index("course_id")["course_name"].to_dict()
    selected = st.selectbox(
        "コース", features["course_id"].tolist(), format_func=lambda cid: name_map.get(cid, str(cid)),
        key="sensitivity_course_select",
    )
    row = features[features["course_id"] == selected].iloc[0]
    fuel_price_now = bm.fuel_price + fuel_delta
    labor_now = bm.labor_cost_per_hour + labor_delta
    be = sn.breakeven_rates(row, fuel_price_now, labor_now, DEPRECIATION_PER_TRIP)

    c1, c2, c3 = st.columns(3)
    c1.metric("変動費ベース分岐点", f"¥{be['variable_cost_breakeven_yen']:,.0f}")
    c2.metric("全部原価ベース分岐点", f"¥{be['full_cost_breakeven_yen']:,.0f}")
    c3.metric("現行運賃", f"¥{row['revenue_yen']:,.0f}")

    if row["revenue_yen"] < be["variable_cost_breakeven_yen"]:
        st.error("🔴 現行運賃が変動費すら賄えていない。運行するだけ即赤字。")
    elif row["revenue_yen"] < be["full_cost_breakeven_yen"]:
        st.warning("🟡 変動費は賄えているが、人件費・車両償却分までは賄えていない。")
    else:
        st.success("🟢 全部原価を上回っている。")

    st.caption(
        "変動費ベース=燃料費+高速代のみ。全部原価ベース=それに人件費・車両償却を加えたもの。"
        "2本ある理由: 変動費ベースを下回ると運行するだけ即赤字、全部原価ベースを下回ると"
        "人件費・車両償却分が賄えず長期的に持続不可能、と意味が異なるため。"
        "土台スライダーの値がこの分岐点にも反映されている。"
    )

    st.divider()

    # ---- 次にすること -------------------------------------------------------
    st.subheader("次にすること")
    st.markdown(
        "- 影響が集中しているコースを、「荷主目線 × 業者目線」タブで積載率・集約数を確認する\n"
        "- 全部原価ベースを下回っているコースは、「運賃判断」タブで交渉側モードの適正原価と照らし合わせる\n"
        "- 打ち手で打ち返しきれない額が残るなら、次回の運賃改定交渉の優先順位に加える"
    )
