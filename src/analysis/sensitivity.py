"""感度分析(L2)。外部環境の変動と自社の打ち手が、営業利益にどう効くかを言語化する。

## この画面が想定する現場での使い方

毎月、燃料単価が確定したらこの画面を開く。

1. 土台スライダー(燃料単価・人件費単価)を実際の値に合わせる
2. 「影響はどこに集中しているか」で、悪化が集中しているコースを確認する
3. 打ち手スライダー(値上げ率・積載率改善)でどこまで打ち返せるかを試す
4. 打ち返しきれない場合、その額と対象コースを「交渉候補」として次のアクションに持っていく

**土台(燃料単価・人件費単価)は外部環境で自社では動かせない。打ち手(値上げ率・
積載率改善)は自社の努力で動かせる。** この2つを分けて見せることで、「外部環境の
悪化をどこまで自社の努力で打ち返せるか、どこからは打ち返せないか」という代替関係を
可視化する。打ち返せない領域の存在を正直に示すことが、分析としての誠実さになる。

積載率改善は`generate.py`のbilling_factor(積んだ分だけ請求できる)と同じ考え方で
売上に反映する。値上げ率は運賃全体への乗算的な上乗せ。
"""

from dataclasses import dataclass, field, replace

import pandas as pd

from src.analysis.attribution import Benchmarks

LEVER_FUEL_PRICE = "fuel_price"
LEVER_LABOR_COST = "labor_cost"
LEVER_RATE_HIKE = "rate_hike"
LEVER_LOADED_RATIO = "loaded_ratio"

# 土台(外部環境、自社では動かせない) / 打ち手(自社の努力、動かせる)
FOUNDATION_LEVERS = (LEVER_FUEL_PRICE, LEVER_LABOR_COST)
ACTION_LEVERS = (LEVER_RATE_HIKE, LEVER_LOADED_RATIO)

_FIELD_BY_LEVER = {
    LEVER_FUEL_PRICE: "fuel_price_yen",
    LEVER_LABOR_COST: "labor_cost_yen",
    LEVER_RATE_HIKE: "rate_hike_pct",
    LEVER_LOADED_RATIO: "loaded_ratio_delta",
}

DEFAULT_SWING = {
    LEVER_FUEL_PRICE: 20.0,     # 燃料単価 ±20円/L
    LEVER_LABOR_COST: 300.0,    # 人件費単価 ±300円/h
    LEVER_RATE_HIKE: 0.10,      # 値上げ率 ±10%
    LEVER_LOADED_RATIO: 0.10,   # 積載率 ±10pt
}


@dataclass(frozen=True)
class Scenario:
    """4レバーの現在値からのズレ。すべて0だとベースラインと同じ。"""
    fuel_price_yen: float = 0.0
    labor_cost_yen: float = 0.0
    rate_hike_pct: float = 0.0
    loaded_ratio_delta: float = 0.0


BASELINE = Scenario()


@dataclass
class ScenarioResult:
    course_table: pd.DataFrame
    total_profit_yen: float

    @property
    def total_revenue_yen(self) -> float:
        return float(self.course_table["scenario_revenue_yen"].sum())

    @property
    def total_cost_yen(self) -> float:
        return float(self.course_table["scenario_cost_yen"].sum())


def evaluate(features: pd.DataFrame, bm: Benchmarks, scenario: Scenario = BASELINE) -> ScenarioResult:
    """コースごとの実績(features)に、シナリオで指定した変化を適用した粗利を計算する。"""
    df = features.copy()

    fuel_price = bm.fuel_price + scenario.fuel_price_yen
    labor_cost = bm.labor_cost_per_hour + scenario.labor_cost_yen

    new_loaded = (df["loaded_ratio"] + scenario.loaded_ratio_delta).clip(0.05, 1.0)
    loaded_factor = new_loaded / df["loaded_ratio"]

    revenue = df["revenue_yen"] * loaded_factor * (1.0 + scenario.rate_hike_pct)
    cost = (
        df["fuel_liters"] * fuel_price
        + df["binding_hours"] * labor_cost
        + df["toll_yen"]
        + bm.depreciation_per_trip
    )

    df["scenario_revenue_yen"] = revenue
    df["scenario_cost_yen"] = cost
    df["scenario_profit_yen"] = revenue - cost

    return ScenarioResult(course_table=df, total_profit_yen=float((revenue - cost).sum()))


def concentration_summary(
    baseline: ScenarioResult, scenario: ScenarioResult, courses: pd.DataFrame, top_n: int = 3
) -> dict:
    """シナリオによる利益変化が、上位何コースにどれだけ集中しているかを言語化する材料を返す。

    「軽油+10円で営業利益の○%が消え、そのうち○%は特定N件のコースに集中している」
    という一文をここから組み立てる。
    """
    diff = scenario.course_table[["course_id", "scenario_profit_yen"]].merge(
        baseline.course_table[["course_id", "scenario_profit_yen"]],
        on="course_id", suffixes=("_scenario", "_baseline"),
    )
    diff["impact_yen"] = diff["scenario_profit_yen_scenario"] - diff["scenario_profit_yen_baseline"]
    diff = diff.merge(courses[["course_id", "course_name"]], on="course_id", how="left")

    total_impact = float(diff["impact_yen"].sum())
    worst = diff.sort_values("impact_yen").head(top_n).copy()
    worst_impact_sum = float(worst["impact_yen"].sum())

    impact_ratio = (total_impact / baseline.total_profit_yen) if baseline.total_profit_yen else 0.0
    # 影響が悪化方向(マイナス)のときだけ「集中度」に意味がある
    concentration_ratio = (worst_impact_sum / total_impact) if total_impact < 0 else 0.0

    return {
        "total_impact_yen": total_impact,
        "impact_ratio": impact_ratio,
        "top_courses": worst[["course_id", "course_name", "impact_yen"]],
        "concentration_ratio": concentration_ratio,
    }


def tornado_sweep(
    features: pd.DataFrame, bm: Benchmarks, base_scenario: Scenario = BASELINE, swing: dict = None
) -> pd.DataFrame:
    """4レバーを各々振り、総利益への影響幅でランキングする。

    L1.5の`attribution.factor_contributions`と同じ「1変数ずつ振る」考え方だが、
    対象はコース単位の主因ではなく全社の総利益。土台/打ち手の分類も付す。
    """
    swing = swing or DEFAULT_SWING
    base_profit = evaluate(features, bm, base_scenario).total_profit_yen

    records = []
    for lever, amount in swing.items():
        field_name = _FIELD_BY_LEVER[lever]
        base_val = getattr(base_scenario, field_name)

        up_scenario = replace(base_scenario, **{field_name: base_val + amount})
        down_scenario = replace(base_scenario, **{field_name: base_val - amount})
        profit_up = evaluate(features, bm, up_scenario).total_profit_yen
        profit_down = evaluate(features, bm, down_scenario).total_profit_yen

        records.append(
            {
                "lever": lever,
                "impact_up_yen": profit_up - base_profit,
                "impact_down_yen": profit_down - base_profit,
                "swing_yen": abs(profit_up - profit_down),
                "category": "foundation" if lever in FOUNDATION_LEVERS else "action",
            }
        )
    return pd.DataFrame(records).sort_values("swing_yen", ascending=False).reset_index(drop=True)


def breakeven_rates(row, fuel_price: float, labor_cost_per_hour: float, depreciation_per_trip: float) -> dict:
    """変動費ベースと全部原価ベース、2本の分岐点運賃を返す。

    変動費ベース(燃料費+高速代のみ)は`cost_engine.py`の損益分岐運賃と同じ定義。
    全部原価ベースはそれに人件費・減価償却を加えたもの。2本ある理由は、変動費ベースを
    下回ると運行するだけ即赤字だが、全部原価ベースを下回ると人件費・車両償却分が
    賄えていない(=長期的に持続不可能)、と意味が異なるため。
    """
    variable_cost = float(row["fuel_liters"]) * fuel_price + float(row["toll_yen"])
    full_cost = variable_cost + float(row["binding_hours"]) * labor_cost_per_hour + depreciation_per_trip
    return {
        "variable_cost_breakeven_yen": variable_cost,
        "full_cost_breakeven_yen": full_cost,
    }
