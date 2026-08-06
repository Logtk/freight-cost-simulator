"""荷主目線(積載率)×業者目線(集約数)のギャップを分析する。

## 背景

物理積載率(`loaded_ratio`)は「トラックの中身がどれだけ詰まっているか」という、荷主が
体感しうるナイーブな専有効率の軸。集約案件数(`orders_per_trip`)は「1回の運行に何件の
FTL請求案件を積み合わせられたか」という、業者だけが見えている実収益を決める軸。
実務は特積(混載)だが荷主にはFTLとして請求する、という実態を踏まえると、この2軸は
**独立に動きうる**。

    1件の大口荷物で満載   → 積載率は高いが集約数は1件 = 薄利
    小口5件を積み合わせ   → 積載率は中程度でも収益は5件分 = 業者は儲かっている

**この乖離が生まれるコースこそが、情報開示の要否・値決め交渉の検討余地になる**、という
実務知見に基づく分析。

`attribution.py` / `visibility.py` の4パターン推定とは独立の軸として扱う(既存の
loss_pattern・ground truth検証には一切依存しない)。ground truthに対する的中率検証は
行わない — これは推定タスクではなく、実データから直接計算できる集計だから。
"""

from dataclasses import dataclass

import pandas as pd

QUADRANT_HIGH_HIGH = "high_high"
QUADRANT_HIGH_LOW = "high_low"
QUADRANT_LOW_HIGH = "low_high"
QUADRANT_LOW_LOW = "low_low"

# 見え方が食い違う(=荷主の認識と業者の実態が一致しない)象限
CONFLICT_QUADRANTS = (QUADRANT_HIGH_LOW, QUADRANT_LOW_HIGH)

# (見出し, 解説)。荷主にどう見えるか / 業者の実態はどうかを併記する。
QUADRANT_NARRATIVE = {
    QUADRANT_HIGH_HIGH: (
        "見え方に矛盾なし",
        "荷主から見ても効率的、業者から見ても採算が良い。基準として扱ってよい水準。",
    ),
    QUADRANT_HIGH_LOW: (
        "荷主は満足、業者は薄利",
        "積載率が高く荷主には効率的に映るが、大口1件への依存で集約できていない。"
        "経営リスクが高く、値上げの必要性を最も裏付けやすい象限。",
    ),
    QUADRANT_LOW_HIGH: (
        "荷主は非効率と感じうるが、業者は儲かっている",
        "積載率だけを見た荷主には「小さい荷物のために大きいトラックを走らせている」と"
        "映りうるが、実際は複数案件の積み合わせで高い採算を確保できている。"
        "情報を開示すべきか、交渉材料として温存すべきかの判断が要る象限。",
    ),
    QUADRANT_LOW_LOW: (
        "両方厳しい",
        "積載率も集約数も低い。典型的な赤字コース。",
    ),
}


@dataclass(frozen=True)
class GapSummary:
    course_table: pd.DataFrame
    quadrant_counts: pd.Series
    loaded_ratio_median: float
    orders_per_trip_median: float

    @property
    def conflict_courses(self) -> pd.DataFrame:
        return self.course_table[self.course_table["quadrant"].isin(CONFLICT_QUADRANTS)]


def _classify(loaded_ratio: float, orders_per_trip: float, loaded_med: float, orders_med: float) -> str:
    high_loaded = loaded_ratio >= loaded_med
    high_orders = orders_per_trip >= orders_med
    if high_loaded and high_orders:
        return QUADRANT_HIGH_HIGH
    if high_loaded and not high_orders:
        return QUADRANT_HIGH_LOW
    if not high_loaded and high_orders:
        return QUADRANT_LOW_HIGH
    return QUADRANT_LOW_LOW


def build_course_gap(
    trips: pd.DataFrame,
    orders: pd.DataFrame,
    courses: pd.DataFrame,
    labor_cost_per_hour: float,
    depreciation_per_trip: float,
) -> pd.DataFrame:
    """コース単位に荷主目線(積載率)と業者目線(集約数・実収益)を集計する。

    trips: trip_id, course_id, loaded_ratio, actual_fuel_liters, actual_binding_hours,
           actual_toll_yen, fuel_price_yen
    orders: trip_id, ftl_rate_yen
    courses: course_id, course_name
    """
    orders_per_trip = orders.groupby("trip_id").size().rename("orders_per_trip")
    trip_revenue = orders.groupby("trip_id")["ftl_rate_yen"].sum().rename("actual_revenue_yen")

    t = trips.set_index("trip_id").join(orders_per_trip).join(trip_revenue)
    t["orders_per_trip"] = t["orders_per_trip"].fillna(0)
    t["actual_revenue_yen"] = t["actual_revenue_yen"].fillna(0)
    t["trip_cost_yen"] = (
        t["actual_fuel_liters"] * t["fuel_price_yen"]
        + t["actual_binding_hours"] * labor_cost_per_hour
        + t["actual_toll_yen"]
        + depreciation_per_trip
    )
    t["actual_gross_profit_yen"] = t["actual_revenue_yen"] - t["trip_cost_yen"]

    agg = t.groupby("course_id").agg(
        trip_count=("orders_per_trip", "size"),
        loaded_ratio=("loaded_ratio", "mean"),
        orders_per_trip=("orders_per_trip", "mean"),
        actual_revenue_yen=("actual_revenue_yen", "mean"),
        trip_cost_yen=("trip_cost_yen", "mean"),
        actual_gross_profit_yen=("actual_gross_profit_yen", "mean"),
    ).reset_index()

    agg = agg.merge(courses[["course_id", "course_name"]], on="course_id", how="left")

    loaded_med = float(agg["loaded_ratio"].median())
    orders_med = float(agg["orders_per_trip"].median())

    agg["quadrant"] = agg.apply(
        lambda r: _classify(r["loaded_ratio"], r["orders_per_trip"], loaded_med, orders_med), axis=1
    )
    agg["quadrant_label"] = agg["quadrant"].map(lambda q: QUADRANT_NARRATIVE[q][0])

    return agg


def summarize(course_gap: pd.DataFrame) -> GapSummary:
    return GapSummary(
        course_table=course_gap,
        quadrant_counts=course_gap["quadrant"].value_counts(),
        loaded_ratio_median=float(course_gap["loaded_ratio"].median()),
        orders_per_trip_median=float(course_gap["orders_per_trip"].median()),
    )
