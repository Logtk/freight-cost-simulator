"""L1.5「見えている世界 / 見えていない世界」タブ。この作品の主張の核。

チャートはAltair(Streamlit同梱)を使う。ブラウザ側で描画されるため日本語フォントの
問題が起きず、matplotlibのようなフォント指定が不要。

配色は src/common/palette.py の2枠のみ(Lv1 / Lv3)。4つの主因はラベルが自己説明的なので
色相では区別しない(dataviz validatorで4色構成が all-pairs 不合格だったため、
エンコーディング自体を見直した経緯は palette.py のdocstringを参照)。
"""

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis import attribution, data_access as da, visibility
from src.common import config, palette
from src.common.labels import label

PATTERN_ORDER = [
    config.PATTERN_HEALTHY,
    config.PATTERN_LOW_RATE,
    config.PATTERN_LOW_LOADED,
    config.PATTERN_LONG_BINDING,
    config.PATTERN_POOR_FUEL,
]

FACTOR_LABELS = {
    "rate": "運賃水準",
    "loaded": "積載率",
    "binding": "拘束時間",
    "fuel": "燃費",
}


@st.cache_data(show_spinner="運行実績を集計しています…")
def _load(_conn):
    """_conn は先頭アンダースコアでハッシュ対象から除外する(Streamlitの規約)。"""
    trips = da.load_trips(_conn)
    courses = da.load_synth_courses(_conn)
    truth = da.load_truth(_conn)
    labor = da.load_setting(_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    return trips, courses, truth, labor


def _profit_table(features: pd.DataFrame, labor: float, depreciation: float) -> pd.DataFrame:
    df = features.copy()
    df["cost_yen"] = (
        df["fuel_liters"] * df["fuel_price_yen"]
        + df["binding_hours"] * labor
        + df["toll_yen"]
        + depreciation
    )
    df["gross_profit_yen"] = df["revenue_yen"] - df["cost_yen"]
    df["gross_profit_rate"] = df["gross_profit_yen"] / df["revenue_yen"]
    return df


def render(conn) -> None:
    if not da.has_synth_data(conn):
        st.info(
            "運行実績データがまだありません。ターミナルで次を実行してください。\n\n"
            "```\npython -m src.synth.load_to_db\n```"
        )
        return

    trips, courses, truth, labor = _load(conn)
    depreciation = 5500.0

    st.caption(
        "同じ月・同じコース群を、①売上と輸送費総額しか見えない場合と "
        "②原価ドライバーまで見える場合とで並べる。差分そのものがこの画面の主題。"
    )

    result = visibility.compare(
        trips, courses, truth,
        labor_cost_per_hour=labor, depreciation_per_trip=depreciation,
    )
    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor, depreciation)
    profit = _profit_table(features, labor, depreciation)

    name_map = courses.set_index("course_id")["course_name"].to_dict()

    # ---- 主張の核: 的中率の対比 -------------------------------------------
    st.subheader("赤字の原因を、どれだけ当てられるか")
    st.caption(
        f"合成データなので各コースの「真の原因」が分かっている。それを見ずに推定し、"
        f"事後に答え合わせした結果（全{result.n_courses}コース・合成データによる検証）。"
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("① 売上と輸送費だけ見える場合", f"{result.lv1_accuracy:.0%}")
    m2.metric("② 原価ドライバーまで見える場合", f"{result.lv3_accuracy:.0%}")
    # accuracy_gain は比率(0.417)なのでポイント表示には100倍が要る
    m3.metric(
        "差", f"{result.accuracy_gain * 100:+.0f}pt",
        help="可視性を得たことによる的中率の改善幅",
    )

    acc_df = pd.DataFrame(
        {
            "view": ["① 売上と輸送費だけ", "② 原価ドライバーまで"],
            "key": ["lv1", "lv3"],
            "accuracy": [result.lv1_accuracy, result.lv3_accuracy],
        }
    )
    colors = palette.CATEGORICAL_LIGHT
    chart = (
        alt.Chart(acc_df)
        .mark_bar(cornerRadiusEnd=4, size=38)
        .encode(
            x=alt.X("accuracy:Q", title="主因の的中率",
                    axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("view:N", title=None, sort=None),
            color=alt.Color(
                "key:N",
                scale=alt.Scale(domain=["lv1", "lv3"], range=[colors["lv1"], colors["lv3"]]),
                legend=None,
            ),
            tooltip=[alt.Tooltip("view:N", title="視点"),
                     alt.Tooltip("accuracy:Q", title="的中率", format=".1%")],
        )
        .properties(height=130)
    )
    text = chart.mark_text(align="left", dx=6, fontWeight="bold").encode(
        text=alt.Text("accuracy:Q", format=".0%"), color=alt.value("#52514e")
    )
    st.altair_chart(chart + text, use_container_width=True)

    st.markdown(
        "**売上と輸送費しか見えないと、原因の切り分けができない。** "
        "粗利が悪いことは分かるが、運賃・積載率・拘束時間・燃費のどれが効いているかを"
        "識別する情報が入力に存在しないため、結局「とりあえず値上げ交渉」に倒れる。"
    )

    st.divider()

    # ---- 並置ビュー -------------------------------------------------------
    st.subheader("同じコース群を、2つの視点で見る")
    left, right = st.columns(2)

    lv1_view = (
        profit[["course_id", "revenue_yen", "cost_yen", "gross_profit_rate"]]
        .assign(course_name=lambda d: d["course_id"].map(name_map))
        .sort_values("gross_profit_rate")
        .head(12)
    )
    with left:
        st.markdown("**① 今見えている世界**")
        st.caption("売上 − 輸送費 = 粗利。悪いことは分かるが、理由が分からない。")
        st.dataframe(
            lv1_view.assign(
                **{
                    label("course_name"): lv1_view["course_name"],
                    label("revenue_yen"): lv1_view["revenue_yen"].round().astype(int),
                    "輸送費": lv1_view["cost_yen"].round().astype(int),
                    label("gross_profit_rate"): (lv1_view["gross_profit_rate"] * 100).round(1),
                }
            )[[label("course_name"), label("revenue_yen"), "輸送費", label("gross_profit_rate")]],
            hide_index=True, use_container_width=True,
        )

    lv3_view = result.lv3_predictions.merge(
        profit[["course_id", "gross_profit_rate"]], on="course_id"
    )
    lv3_view["course_name"] = lv3_view["course_id"].map(name_map)
    lv3_view = lv3_view.sort_values("gross_profit_rate").head(12)
    with right:
        st.markdown("**② 原価ドライバーまで見える世界**")
        st.caption("主因が切り分けられるので、打ち手と交渉相手まで決まる。")
        st.dataframe(
            pd.DataFrame(
                {
                    label("course_name"): lv3_view["course_name"],
                    label("estimated_pattern"): lv3_view["estimated_pattern"].map(label),
                    label("action"): lv3_view["action"],
                    label("counterpart"): lv3_view["counterpart"],
                }
            ),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    # ---- 誤選択コスト -----------------------------------------------------
    st.subheader("見えないまま動くと、どこで空振りするか")
    mis = result.misdirected_courses.copy()
    if mis.empty:
        st.success("この期間では、Lv1の判断でも誤った打ち手には至らなかった。")
    else:
        mis["course_name"] = mis["course_id"].map(name_map)
        st.warning(
            f"**{len(mis)}コース**で、①の視点なら運賃交渉に動くが、"
            "実際の主因は別にある。荷主に値上げを持ちかけても問題は解消しない。"
        )
        st.dataframe(
            pd.DataFrame(
                {
                    label("course_name"): mis["course_name"],
                    "①で取る打ち手": "運賃改定交渉",
                    "実際の主因": mis["loss_pattern"].map(label),
                    "本来の打ち手": mis["correct_action"],
                    "本来の交渉相手": mis["correct_counterpart"],
                }
            ),
            hide_index=True, use_container_width=True,
        )

    st.divider()

    # ---- 要因分解(コース単位のドリルダウン) -------------------------------
    st.subheader("コース別の要因分解")
    st.caption(
        "そのコースの距離・車格から構造的に期待される水準を基準に、"
        "4要因それぞれが粗利をいくら押し下げ／押し上げているかを円で示す。"
    )

    options = result.lv3_predictions["course_id"].tolist()
    selected = st.selectbox(
        "コース", options, format_func=lambda cid: name_map.get(cid, str(cid))
    )

    row = features[features["course_id"] == selected].iloc[0]
    contrib = attribution.factor_contributions(row, bm)
    est = result.lv3_predictions[result.lv3_predictions["course_id"] == selected].iloc[0]
    true_pattern = truth[truth["course_id"] == selected]["loss_pattern"].iloc[0]

    c1, c2 = st.columns(2)
    c1.metric(label("estimated_pattern"), label(est["estimated_pattern"]))
    c2.metric(
        label("true_pattern"), label(true_pattern),
        help="合成データなので答えが分かる。実データには存在しない情報。",
    )
    if est["estimated_pattern"] != true_pattern:
        st.info("このコースは推定を外している。交絡（副次要因）が効いているケース。")

    cdf = pd.DataFrame(
        {
            "factor": [FACTOR_LABELS[k] for k in contrib],
            "yen": list(contrib.values()),
        }
    ).sort_values("yen")
    bars = (
        alt.Chart(cdf)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("yen:Q", title="1運行あたり粗利への影響（円）"),
            y=alt.Y("factor:N", title=None, sort=cdf["factor"].tolist()),
            color=alt.condition(
                alt.datum.yen < 0,
                alt.value(palette.STATUS["critical"]),
                alt.value(palette.CATEGORICAL_LIGHT["lv3"]),
            ),
            tooltip=[alt.Tooltip("factor:N", title="要因"),
                     alt.Tooltip("yen:Q", title="影響額", format=",.0f")],
        )
        .properties(height=180)
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
        color=palette.CHROME_LIGHT["baseline"], strokeWidth=1.5
    ).encode(x="x:Q")
    st.altair_chart(bars + zero, use_container_width=True)
    st.caption("赤は粗利を押し下げている要因（合成データによる試算）。")
