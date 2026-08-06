"""赤字の主因を運行実績から推定する(要因分解 / ブリッジ方式)。

## 考え方

コースごとに「そのコースの諸元(距離・車格)から構造的に期待される水準」を基準線として置き、
実測がそこからどれだけ乖離しているかを **円建ての粗利インパクト** に換算する。
最も粗利を押し下げている要因をそのコースの主因と判定する。

    期待値: 距離と車格から決まる標準拘束時間・標準燃料・標準運賃
      → 実測との差を1要因ずつ円換算
      → 最も負の寄与が大きい要因が主因

### なぜ「全社中央値との比較」ではないのか(重要)

当初は `拘束時間/km` や `運賃/km` を全社中央値と比較していたが、これらの指標は
**距離に構造的に依存する**。荷役時間は距離によらずほぼ一定なので、短距離コースほど
時間/kmが大きくなる。運賃/kmも同様に短距離ほど高い。全社中央値と比べると
「短距離である」ことを「拘束時間が長い」と誤判定してしまい、実際に的中率が
Lv1を下回った。距離の違いを性能の違いと読み違えないため、**各コース自身の期待値**を
基準にする。

この「1要因ずつ振る」機構は L2 のトルネードチャートと同一である。両者で
`factor_contributions()` を共有し、重複実装しない。

**この module は ground truth(synth_course_truth)を一切参照しない。**
的中率の検証は src/analysis/visibility.py が事後に突き合わせて行う。
"""

from dataclasses import dataclass

import pandas as pd

from src.common import config

FACTOR_TO_PATTERN = {
    "rate": config.PATTERN_LOW_RATE,
    "loaded": config.PATTERN_LOW_LOADED,
    "binding": config.PATTERN_LONG_BINDING,
    "fuel": config.PATTERN_POOR_FUEL,
}

FACTORS = tuple(FACTOR_TO_PATTERN.keys())

# 車格ごとの公称燃費(km/L)。src/seed_data.py の vehicle_types と一致させる。
NOMINAL_FUEL_EFFICIENCY = {"2T": 9.0, "4T": 6.5, "10T": 4.0}


@dataclass(frozen=True)
class Benchmarks:
    """全社の標準水準。個々のコースの期待値を組み立てるための素材。"""
    average_speed_kmh: float      # 実績から逆算した平均走行速度
    fixed_overhead_hours: float   # 距離によらない固定時間(荷役+標準的な荷待ち)
    loaded_ratio: float           # 標準的な積載率
    markup: float                 # 標準原価に対する標準的なマークアップ
    labor_cost_per_hour: float
    fuel_price: float
    depreciation_per_trip: float


def build_course_features(trips: pd.DataFrame, courses: pd.DataFrame) -> pd.DataFrame:
    """運行明細をコース単位に集約する。

    trips: course_id, revenue_yen, actual_distance_km, actual_binding_hours,
           actual_fuel_liters, actual_toll_yen, loaded_ratio, fuel_price_yen
    courses: course_id, vehicle_code
    """
    agg = trips.groupby("course_id").agg(
        trip_count=("revenue_yen", "size"),
        revenue_yen=("revenue_yen", "mean"),
        distance_km=("actual_distance_km", "mean"),
        binding_hours=("actual_binding_hours", "mean"),
        fuel_liters=("actual_fuel_liters", "mean"),
        toll_yen=("actual_toll_yen", "mean"),
        loaded_ratio=("loaded_ratio", "mean"),
        fuel_price_yen=("fuel_price_yen", "mean"),
    ).reset_index()

    return agg.merge(courses[["course_id", "vehicle_code"]], on="course_id", how="left")


def build_benchmarks(
    features: pd.DataFrame,
    labor_cost_per_hour: float,
    depreciation_per_trip: float,
    average_speed_kmh: float = 28.0,
) -> Benchmarks:
    """実績の中央値から、期待値を組み立てるためのベンチマークを推定する。"""
    # 距離に比例しない固定時間(荷役+標準的な荷待ち)を実績から逆算する
    overhead = features["binding_hours"] - features["distance_km"] / average_speed_kmh
    fixed_overhead = float(overhead.median())

    loaded = float(features["loaded_ratio"].median())
    fuel_price = float(features["fuel_price_yen"].median())

    # 標準原価に対する実際のマークアップの中央値を「標準的な値決め水準」とみなす
    expected_fuel = features["distance_km"] / features["vehicle_code"].map(NOMINAL_FUEL_EFFICIENCY)
    expected_hours = features["distance_km"] / average_speed_kmh + fixed_overhead
    expected_cost = (
        expected_fuel * fuel_price
        + expected_hours * labor_cost_per_hour
        + features["toll_yen"]
        + depreciation_per_trip
    )
    markup = float((features["revenue_yen"] / expected_cost).median())

    return Benchmarks(
        average_speed_kmh=average_speed_kmh,
        fixed_overhead_hours=fixed_overhead,
        loaded_ratio=loaded,
        markup=markup,
        labor_cost_per_hour=labor_cost_per_hour,
        fuel_price=fuel_price,
        depreciation_per_trip=depreciation_per_trip,
    )


def expected_values(row, bm: Benchmarks) -> dict:
    """そのコースの諸元から構造的に期待される水準を返す。"""
    distance = float(row["distance_km"])
    eff = NOMINAL_FUEL_EFFICIENCY.get(row["vehicle_code"], 6.5)

    exp_fuel = distance / eff
    exp_hours = distance / bm.average_speed_kmh + bm.fixed_overhead_hours
    exp_cost = (
        exp_fuel * bm.fuel_price
        + exp_hours * bm.labor_cost_per_hour
        + float(row["toll_yen"])
        + bm.depreciation_per_trip
    )
    return {
        "fuel_liters": exp_fuel,
        "binding_hours": exp_hours,
        "cost_yen": exp_cost,
        "revenue_yen": exp_cost * bm.markup,
    }


def factor_contributions(row, bm: Benchmarks) -> dict:
    """4要因それぞれの粗利インパクト(円)。負なら粗利を押し下げている。

    加法的に分解されるので、合計すると実際の粗利と期待粗利の差におおむね一致する。
    L1.5の主因推定と L2 のトルネードチャートが共有する中核関数。
    """
    exp = expected_values(row, bm)

    loaded_actual = float(row["loaded_ratio"])
    revenue_actual = float(row["revenue_yen"])

    # 積載率: 積んだ分だけ請求できるので売上側に効く
    loaded_index = loaded_actual / bm.loaded_ratio
    contrib_loaded = exp["revenue_yen"] * (loaded_index - 1.0)

    # 運賃水準: 積載率の影響を除いたうえでの値決めの高低
    revenue_expected_at_actual_loaded = exp["revenue_yen"] * loaded_index
    contrib_rate = revenue_actual - revenue_expected_at_actual_loaded

    # 拘束時間: 期待より長い分だけ人件費が余計にかかる
    contrib_binding = -(float(row["binding_hours"]) - exp["binding_hours"]) * bm.labor_cost_per_hour

    # 燃費: 期待より多く食った分だけ燃料費が余計にかかる
    contrib_fuel = -(float(row["fuel_liters"]) - exp["fuel_liters"]) * bm.fuel_price

    return {
        "rate": contrib_rate,
        "loaded": contrib_loaded,
        "binding": contrib_binding,
        "fuel": contrib_fuel,
    }


def estimate_patterns(
    features: pd.DataFrame,
    bm: Benchmarks,
    healthy_threshold_ratio: float = 0.06,
) -> pd.DataFrame:
    """コースごとに主因を推定する。

    最も粗利を押し下げている要因を主因とする。ただし押し下げ額が期待売上の
    `healthy_threshold_ratio` に満たない場合は、どの要因も支配的でないとみなし健全と判定する。
    """
    records = []
    for _, row in features.iterrows():
        contrib = factor_contributions(row, bm)
        exp = expected_values(row, bm)

        worst_factor = min(contrib, key=contrib.get)
        worst_value = contrib[worst_factor]
        threshold = -abs(healthy_threshold_ratio * exp["revenue_yen"])

        if worst_value >= threshold:
            pattern = config.PATTERN_HEALTHY
        else:
            pattern = FACTOR_TO_PATTERN[worst_factor]

        action, counterpart = config.PATTERN_ACTIONS[pattern]
        records.append(
            {
                "course_id": row["course_id"],
                "estimated_pattern": pattern,
                "worst_factor": worst_factor,
                "impact_yen": worst_value,
                "expected_revenue_yen": exp["revenue_yen"],
                "action": action,
                "counterpart": counterpart,
                **{f"contrib_{k}": v for k, v in contrib.items()},
            }
        )

    return pd.DataFrame(records)
