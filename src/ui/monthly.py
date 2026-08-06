"""「月次カルテ」タブ(L1)。「毎月開く理由」そのもの。

コース→車両→ドライバー→個別運行の4階層を、行クリックで段階的に掘り下げる。
Streamlitはネスト表を持たないため、各階層ごとに1枚の表を用意し、選択されたら
次階層の表が下に現れる形で実現する。選択状態は`st.session_state`に保持し、
上位階層の選択が変わったら下位の選択を破棄する。
"""

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis import data_access as da, monthly as mo
from src.common import palette
from src.common.labels import label

DEPRECIATION_PER_TRIP = 5500.0

_STATE_MONTH = "monthly_last_month"
_STATE_COURSE = "monthly_selected_course"
_STATE_VEHICLE = "monthly_selected_vehicle"
_STATE_DRIVER = "monthly_selected_driver"


@st.cache_data(show_spinner="運行実績を月次集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    orders = da.load_trip_orders(_conn)
    courses = da.load_synth_courses(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    enriched = mo.enrich_trips(trips, orders, labor, DEPRECIATION_PER_TRIP)
    return enriched, courses


def _fmt_yen(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        out[c] = out[c].round().astype(int)
    return out


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    enriched, courses = _load(conn)
    name_map = courses.set_index("course_id")["course_name"].to_dict()

    st.caption(
        "**現場での使い方**: 毎月月初、先月分の実績が確定したらこの画面を開く。"
        "① 月次推移で悪化トレンドが無いか見る → ② コース別サマリーで低採算コースを見つける → "
        "③ 行をクリックして車両→ドライバー→個別運行と掘り下げ、原因の当たりをつける。"
    )

    # ---- 月次推移 -----------------------------------------------------------
    st.subheader("粗利の月次推移")
    trend = mo.build_profit_trend(enriched)
    chart = (
        alt.Chart(trend)
        .mark_line(point=True, color=palette.CATEGORICAL_LIGHT["lv3"])
        .encode(
            x=alt.X("month:N", title=None),
            y=alt.Y("profit_yen:Q", title="全社粗利(円)"),
            tooltip=[alt.Tooltip("month:N", title="月"), alt.Tooltip("profit_yen:Q", title="粗利", format=",.0f")],
        )
        .properties(height=220)
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeWidth=1.5
    ).encode(y="y:Q")
    st.altair_chart(chart + zero, use_container_width=True)

    st.divider()

    # ---- 月選択 ---------------------------------------------------------------
    months = sorted(enriched["month"].unique())
    selected_month = st.selectbox("月を選択", months, index=len(months) - 1, key="monthly_month_select")

    if st.session_state.get(_STATE_MONTH) != selected_month:
        st.session_state[_STATE_MONTH] = selected_month
        st.session_state.pop(_STATE_COURSE, None)
        st.session_state.pop(_STATE_VEHICLE, None)
        st.session_state.pop(_STATE_DRIVER, None)

    if st.button("ドリルダウンの選択をクリア"):
        st.session_state.pop(_STATE_COURSE, None)
        st.session_state.pop(_STATE_VEHICLE, None)
        st.session_state.pop(_STATE_DRIVER, None)

    # ---- コース別サマリー(第1階層) ------------------------------------------
    st.subheader(f"{selected_month} コース別サマリー")
    st.caption(
        "稼働率は「運行日数 ÷ 当月営業日数」の簡易指標(全車両が常に稼働可能だったと仮定した粗い値)。"
        "行をクリックすると車両別内訳が下に表示される。"
    )
    course_df = mo.build_course_monthly(enriched, courses, selected_month)
    course_view = _fmt_yen(course_df, ["revenue_yen", "cost_yen", "profit_yen"])
    course_view[label("profit_rate")] = (course_view["profit_rate"] * 100).round(1)
    course_view[label("utilization_rate")] = (course_view["utilization_rate"] * 100).round(0)

    course_event = st.dataframe(
        pd.DataFrame(
            {
                label("course_name"): course_view["course_name"],
                label("trip_count"): course_view["trip_count"],
                label("revenue_yen"): course_view["revenue_yen"],
                label("cost_yen"): course_view["cost_yen"],
                label("profit_yen"): course_view["profit_yen"],
                "粗利率(%)": course_view[label("profit_rate")],
                "稼働率(%)": course_view[label("utilization_rate")],
            }
        ),
        hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row", key="monthly_course_table",
    )
    if course_event.selection.rows:
        idx = course_event.selection.rows[0]
        picked = course_df.iloc[idx]["course_id"]
        if st.session_state.get(_STATE_COURSE) != picked:
            st.session_state.pop(_STATE_VEHICLE, None)
            st.session_state.pop(_STATE_DRIVER, None)
        st.session_state[_STATE_COURSE] = picked

    # ---- 車両別内訳(第2階層) -------------------------------------------------
    selected_course = st.session_state.get(_STATE_COURSE)
    if selected_course is not None:
        st.markdown(f"**▸ {name_map.get(selected_course, selected_course)} の車両別内訳**")
        veh_df = mo.build_vehicle_breakdown(enriched, selected_course, selected_month)
        if veh_df.empty:
            st.info("この月・このコースの運行実績がない。")
        else:
            veh_view = _fmt_yen(veh_df, ["revenue_yen", "cost_yen", "profit_yen"])
            veh_event = st.dataframe(
                pd.DataFrame(
                    {
                        label("vehicle_id"): veh_view["vehicle_id"],
                        label("trip_count"): veh_view["trip_count"],
                        label("revenue_yen"): veh_view["revenue_yen"],
                        label("cost_yen"): veh_view["cost_yen"],
                        label("profit_yen"): veh_view["profit_yen"],
                    }
                ),
                hide_index=True, use_container_width=True,
                on_select="rerun", selection_mode="single-row", key="monthly_vehicle_table",
            )
            if veh_event.selection.rows:
                idx = veh_event.selection.rows[0]
                picked = veh_df.iloc[idx]["vehicle_id"]
                if st.session_state.get(_STATE_VEHICLE) != picked:
                    st.session_state.pop(_STATE_DRIVER, None)
                st.session_state[_STATE_VEHICLE] = picked

    # ---- ドライバー別内訳(第3階層) ---------------------------------------------
    selected_vehicle = st.session_state.get(_STATE_VEHICLE)
    if selected_course is not None and selected_vehicle is not None:
        st.markdown(f"**▸▸ {selected_vehicle} のドライバー別内訳**")
        drv_df = mo.build_driver_breakdown(enriched, selected_course, selected_vehicle, selected_month)
        if drv_df.empty:
            st.info("この月・この車両の運行実績がない。")
        else:
            drv_view = _fmt_yen(drv_df, ["revenue_yen", "cost_yen", "profit_yen"])
            drv_event = st.dataframe(
                pd.DataFrame(
                    {
                        label("driver_id"): drv_view["driver_id"],
                        label("trip_count"): drv_view["trip_count"],
                        label("revenue_yen"): drv_view["revenue_yen"],
                        label("cost_yen"): drv_view["cost_yen"],
                        label("profit_yen"): drv_view["profit_yen"],
                    }
                ),
                hide_index=True, use_container_width=True,
                on_select="rerun", selection_mode="single-row", key="monthly_driver_table",
            )
            if drv_event.selection.rows:
                idx = drv_event.selection.rows[0]
                st.session_state[_STATE_DRIVER] = drv_df.iloc[idx]["driver_id"]

    # ---- 個別運行(第4階層・最終) ------------------------------------------------
    selected_driver = st.session_state.get(_STATE_DRIVER)
    if selected_course is not None and selected_vehicle is not None and selected_driver is not None:
        st.markdown(f"**▸▸▸ {selected_driver} の個別運行**")
        detail_df = mo.build_trip_detail(enriched, selected_course, selected_vehicle, selected_driver, selected_month)
        if detail_df.empty:
            st.info("この月・このドライバーの運行実績がない。")
        else:
            detail_view = _fmt_yen(detail_df, ["revenue_yen_actual", "cost_yen", "profit_yen"])
            st.dataframe(
                pd.DataFrame(
                    {
                        label("trip_date"): detail_view["trip_date"],
                        label("revenue_yen_actual"): detail_view["revenue_yen_actual"],
                        label("cost_yen"): detail_view["cost_yen"],
                        label("profit_yen"): detail_view["profit_yen"],
                        label("actual_distance_km"): detail_view["actual_distance_km"].round(1),
                        label("loaded_ratio"): detail_view["loaded_ratio"].round(2),
                    }
                ),
                hide_index=True, use_container_width=True,
            )

    st.divider()

    # ---- 全社ランキング ---------------------------------------------------------
    st.subheader("車両別・ドライバー別ランキング(全社、コース横断)")
    rv = mo.rank_vehicles(enriched, selected_month)
    rd = mo.rank_drivers(enriched, selected_month)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**車両**")
        st.caption("上位")
        st.dataframe(
            _fmt_yen(rv["top"], ["profit_yen"]).rename(columns={"vehicle_id": label("vehicle_id"), "trip_count": label("trip_count"), "profit_yen": label("profit_yen")}),
            hide_index=True, use_container_width=True,
        )
        st.caption("下位")
        st.dataframe(
            _fmt_yen(rv["bottom"], ["profit_yen"]).rename(columns={"vehicle_id": label("vehicle_id"), "trip_count": label("trip_count"), "profit_yen": label("profit_yen")}),
            hide_index=True, use_container_width=True,
        )
    with c2:
        st.markdown("**ドライバー**")
        st.caption("上位")
        st.dataframe(
            _fmt_yen(rd["top"], ["profit_yen"]).rename(columns={"driver_id": label("driver_id"), "trip_count": label("trip_count"), "profit_yen": label("profit_yen")}),
            hide_index=True, use_container_width=True,
        )
        st.caption("下位")
        st.dataframe(
            _fmt_yen(rd["bottom"], ["profit_yen"]).rename(columns={"driver_id": label("driver_id"), "trip_count": label("trip_count"), "profit_yen": label("profit_yen")}),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    st.subheader("次にすること")
    st.markdown(
        "- 粗利率が悪化しているコースは、「見えている世界 / 見えていない世界」タブで主因を確認する\n"
        "- 稼働率が低いコースは、「荷主目線 × 業者目線」タブで積載率・集約数を確認する\n"
        "- 下位ランキングの車両・ドライバーが特定コースに偏っていないか確認する"
    )
