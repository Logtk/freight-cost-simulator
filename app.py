"""運送採算カルテ — Streamlit UI。

原価ドライバーが他部署にあるとき採算責任をどう持つか、を扱うダッシュボード。
中心は「見えている世界 / 見えていない世界」タブ(L1.5)。
運賃判断タブは単発の原価計算(v0.1)をそのまま収容している。
"""

import pandas as pd
import streamlit as st

from datetime import date

from src import cost_engine as ce
from src import db
from src import seed_data
from src.synth import generate as synth_generate
from src.synth import load_to_db as synth_load
from src.ui import customer_portfolio, monthly, negotiation, perspective_gap, sensitivity, visibility_gap

st.set_page_config(page_title="運送採算カルテ", page_icon="🚛", layout="wide")


@st.cache_resource
def get_conn():
    conn = db.get_connection(check_same_thread=False)
    db.init_schema(conn)
    seed_data.seed(conn)
    # クラウド等、`python -m src.synth.load_to_db` を事前実行できない環境向けに
    # 運行実績が空なら初回アクセス時に合成データを自動投入する(冪等・キャッシュ済み接続のため1回のみ)。
    if conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"] == 0:
        frames = synth_generate.generate_trips(start_date=date(2025, 1, 1))
        synth_load.load(conn, frames)
    return conn


def load_masters(conn):
    # st.selectbox はウィジェット状態を deepcopy するため sqlite3.Row は不可(pickle不可)。dictに変換する。
    vehicles = [dict(r) for r in conn.execute("SELECT * FROM vehicle_types ORDER BY display_order")]
    courses = [
        dict(r) for r in conn.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY course_name")
    ]
    return vehicles, courses


def render_alert(result: ce.CostResult, mode: str):
    diff = result.diff_vs_current_yen
    ratio_pct = result.diff_ratio * 100

    if mode == ce.MODE_PAYMENT:
        if result.alert_level == "OK":
            st.success(f"🟢 現在の支払額は適正原価を上回っています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)")
        elif result.alert_level == "WARNING":
            st.warning(
                f"🟡 現在の支払額が適正原価をわずかに下回っています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)"
                " — 協力会社への支払いが妥当か確認を推奨します。"
            )
        else:
            st.error(
                f"🔴 現在の支払額が適正原価を大きく下回っています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)"
                " — 供給継続リスクの可能性があります。支払い条件の見直しを検討してください。"
            )
    else:
        if result.alert_level == "OK":
            st.success(f"🟢 現在の請求額は適正原価を確保できています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)")
        elif result.alert_level == "WARNING":
            st.warning(
                f"🟡 現在の請求額が適正原価をわずかに下回っています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)"
                " — 値上げ交渉の余地があります。"
            )
        else:
            st.error(
                f"🔴 現在の請求額が適正原価を大きく下回っています(差額 {diff:+,}円 / {ratio_pct:+.1f}%)"
                " — 値上げ交渉を強く検討すべき水準です。"
            )


def save_calculation(conn, cost_input: ce.CostInput, result: ce.CostResult) -> int:
    cur = conn.execute(
        """INSERT INTO calculations
           (course_id, course_name, vehicle_code, mode, distance_km, binding_hours, toll_fee_yen,
            current_rate_yen, fuel_cost_yen, labor_cost_yen, depreciation_cost_yen, safety_cost_yen,
            breakeven_rate_yen, appropriate_cost_yen, diff_vs_current_yen, alert_level)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cost_input.course_id, cost_input.course_name, cost_input.vehicle_code, cost_input.mode,
            cost_input.distance_km, cost_input.binding_hours, cost_input.toll_fee_yen,
            cost_input.current_rate_yen, result.fuel_cost_yen, result.labor_cost_yen,
            result.depreciation_cost_yen, result.safety_cost_yen, result.breakeven_rate_yen,
            result.appropriate_cost_yen, result.diff_vs_current_yen, result.alert_level,
        ),
    )
    conn.commit()
    return cur.lastrowid


def render_calc_form(conn):
    vehicles, courses = load_masters(conn)

    st.caption(
        "保存済みコースを選ぶと入力欄に反映されます。そのまま計算するか、値を書き換えて計算できます。"
    )
    course_options = [None] + courses
    selected_course = st.selectbox(
        "保存済みコース(任意)",
        course_options,
        format_func=lambda c: "(手入力する)" if c is None else c["course_name"],
    )

    defaults = selected_course or {}

    with st.form("calc_form"):
        course_name = st.text_input("コース名", value=defaults.get("course_name", ""))

        vehicle_codes = [v["vehicle_code"] for v in vehicles]
        default_vehicle_idx = (
            vehicle_codes.index(defaults["vehicle_code"]) if defaults.get("vehicle_code") in vehicle_codes else 0
        )
        vehicle = st.selectbox(
            "車種", vehicles, index=default_vehicle_idx, format_func=lambda v: v["vehicle_name"]
        )

        st.subheader("運行条件")
        c1, c2, c3 = st.columns(3)
        distance_km = c1.number_input(
            "走行距離(km)", min_value=0.0, value=float(defaults.get("distance_km", 0.0)), step=5.0
        )
        binding_hours = c2.number_input(
            "拘束時間(h)", min_value=0.0, value=float(defaults.get("binding_hours", 0.0)), step=0.5
        )
        toll_fee = c3.number_input(
            "高速代(円)", min_value=0, value=int(defaults.get("toll_fee_yen", 0)), step=100
        )

        st.subheader("モードと現在の運賃")
        mode_keys = [ce.MODE_PAYMENT, ce.MODE_NEGOTIATION]
        default_mode_idx = mode_keys.index(defaults["mode"]) if defaults.get("mode") in mode_keys else 0
        mode = st.radio(
            "モード", mode_keys, index=default_mode_idx, format_func=lambda m: ce.MODE_LABELS[m]
        )
        rate_label = "現在の支払額(円)" if mode == ce.MODE_PAYMENT else "現在の請求額(円)"
        current_rate = st.number_input(
            rate_label, min_value=0, value=int(defaults.get("current_rate_yen", 0)), step=500
        )

        submitted = st.form_submit_button("原価を計算する", width="stretch")

    if submitted:
        cost_input = ce.CostInput(
            course_name=course_name or "(無題のコース)",
            vehicle_code=vehicle["vehicle_code"],
            distance_km=distance_km,
            binding_hours=binding_hours,
            toll_fee_yen=int(toll_fee),
            mode=mode,
            current_rate_yen=int(current_rate),
            course_id=selected_course["course_id"] if selected_course else None,
        )
        try:
            result = ce.calculate_cost(conn, cost_input)
        except ce.CostEngineError as e:
            st.error(str(e))
            return
        st.session_state["last_cost_input"] = cost_input
        st.session_state["last_result"] = result

    if "last_result" in st.session_state:
        result: ce.CostResult = st.session_state["last_result"]
        cost_input: ce.CostInput = st.session_state["last_cost_input"]

        for w in result.warnings:
            st.warning(w)

        st.divider()
        m1, m2, m3 = st.columns(3)
        m1.metric("損益分岐運賃", f"¥{result.breakeven_rate_yen:,}")
        m2.metric("適正原価(標準的運賃相当)", f"¥{result.appropriate_cost_yen:,}")
        m3.metric(
            "現在の運賃" if cost_input.mode == ce.MODE_PAYMENT else "現在の請求額",
            f"¥{cost_input.current_rate_yen:,}",
        )
        render_alert(result, cost_input.mode)

        with st.expander("計算内訳を見る"):
            df = pd.DataFrame(
                [{"内訳": s.label, "金額(円)": s.amount_yen} for s in result.breakdown]
            )
            st.dataframe(df, hide_index=True, width="stretch")

        if st.button("この計算を履歴に記録する", width="stretch"):
            calc_id = save_calculation(conn, cost_input, result)
            st.success(f"計算ID {calc_id} として保存しました。「計算履歴」タブで確認できます。")


def render_history(conn):
    rows = conn.execute(
        """SELECT calc_id, created_at, course_name, vehicle_code, mode, distance_km, binding_hours,
                  toll_fee_yen, current_rate_yen, breakeven_rate_yen, appropriate_cost_yen,
                  diff_vs_current_yen, alert_level
           FROM calculations
           ORDER BY created_at DESC
           LIMIT 100"""
    ).fetchall()

    if not rows:
        st.info("まだ計算履歴がありません。「原価計算」タブから計算・記録してください。")
        return

    n_critical = sum(1 for r in rows if r["alert_level"] == "CRITICAL")
    n_warning = sum(1 for r in rows if r["alert_level"] == "WARNING")
    if n_critical:
        st.error(f"🔴 CRITICAL(適正原価を大きく下回る)が {n_critical} 件あります。")
    if n_warning:
        st.warning(f"🟡 WARNING(適正原価をわずかに下回る)が {n_warning} 件あります。")

    df = pd.DataFrame([dict(r) for r in rows])
    df["mode"] = df["mode"].map(ce.MODE_LABELS)
    df["alert_level"] = df["alert_level"].map({"OK": "🟢 OK", "WARNING": "🟡 WARNING", "CRITICAL": "🔴 CRITICAL"})
    df = df.rename(columns={
        "calc_id": "ID", "created_at": "日時", "course_name": "コース", "vehicle_code": "車種",
        "mode": "モード", "distance_km": "距離(km)", "binding_hours": "拘束時間(h)",
        "toll_fee_yen": "高速代", "current_rate_yen": "現在額", "breakeven_rate_yen": "損益分岐運賃",
        "appropriate_cost_yen": "適正原価", "diff_vs_current_yen": "差額", "alert_level": "判定",
    })
    st.dataframe(df, hide_index=True, width="stretch")


def main():
    conn = get_conn()

    st.title("🚛 運送採算カルテ")
    st.caption(
        "原価ドライバーが他部署にあるとき、採算責任をどう持つか — "
        "すべて合成データであり、実在の企業・取引・運賃とは一切関係ありません"
    )

    (
        tab_monthly, tab_gap, tab_perspective, tab_sensitivity, tab_portfolio,
        tab_negotiation, tab_calc, tab_hist,
    ) = st.tabs(
        [
            "月次カルテ", "見えている世界 / 見えていない世界", "荷主目線 × 業者目線",
            "感度分析", "経営レポート", "交渉管理", "運賃判断", "計算履歴",
        ]
    )
    with tab_monthly:
        monthly.render(conn)
    with tab_gap:
        visibility_gap.render(conn)
    with tab_perspective:
        perspective_gap.render(conn)
    with tab_sensitivity:
        sensitivity.render(conn)
    with tab_portfolio:
        customer_portfolio.render(conn)
    with tab_negotiation:
        negotiation.render(conn)
    with tab_calc:
        render_calc_form(conn)
    with tab_hist:
        render_history(conn)


if __name__ == "__main__":
    main()
