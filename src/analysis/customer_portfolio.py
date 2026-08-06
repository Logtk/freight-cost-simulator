"""経営レポート(L4)。視座を現場の原価管理から経営のポートフォリオ判断へ上げる層。

## この画面が想定する現場での使い方

月次カルテで低採算コースを見つけた後、それが「特定の顧客に偏っていないか」「その顧客との
取引全体がどうなっているか」を経営目線で確認する。四半期に一度、値上げ交渉・撤退検討の
優先順位を見直すタイミングで開く。

## 貨物価値×運賃のギャップ(3つ目のギャップ)

L1.5(Lv1 vs Lv3の情報量ギャップ)、L1.5+(荷主目線×業者目線の積載率×集約数ギャップ)に続く
3つ目のギャップ。今のモデルは運賃を距離・車格・拘束時間からしか決めておらず、貨物の経済価値
(数万円〜数千万円)を一切見ていない。「高価値貨物なのに運賃が低い」(賠償リスクに見合った
対価を取れていない)、「低価値貨物なのに運賃が高い」(荷主の値下げ圧力の火種)という食い違いを、
L1.5+と同じ4象限の型で可視化する。本人判断により顧客ポートフォリオの一部として統合し、
独立の2×2として並置する(既存の売上規模×粗利率の2×2に3軸目を詰め込むと読み取りにくくなるため)。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

QUADRANT_RAISE = "raise"          # 売上高 × 粗利率低 → 値上げ交渉
QUADRANT_MAINTAIN = "maintain"    # 売上高 × 粗利率高 → 維持
QUADRANT_GROW = "grow"            # 売上低 × 粗利率高 → 育成
QUADRANT_EXIT = "exit"            # 売上低 × 粗利率低 → 撤退検討

CUSTOMER_QUADRANT_NARRATIVE = {
    QUADRANT_MAINTAIN: ("維持", "売上・粗利率ともに高い優良顧客。関係維持を最優先する。"),
    QUADRANT_RAISE: ("値上げ交渉", "売上は大きいが粗利率が低い。依存度が高い分、値上げ交渉の優先度が最も高い。"),
    QUADRANT_GROW: ("育成", "売上規模は小さいが粗利率は高い。取引量を伸ばす価値がある。"),
    QUADRANT_EXIT: ("撤退検討", "売上・粗利率ともに低い。継続の意義を見直す対象。"),
}

VALUE_GAP_BALANCED_HIGH = "balanced_high"
VALUE_GAP_UNDERPRICED = "underpriced"     # 価値高×比率低: 高価値貨物なのに運賃が低い
VALUE_GAP_OVERPRICED = "overpriced"       # 価値低×比率高: 低価値貨物なのに運賃が高い
VALUE_GAP_BALANCED_LOW = "balanced_low"

VALUE_GAP_CONFLICT = (VALUE_GAP_UNDERPRICED, VALUE_GAP_OVERPRICED)

VALUE_GAP_NARRATIVE = {
    VALUE_GAP_BALANCED_HIGH: ("価値相応の運賃(高価格帯)", "貨物価値に見合った運賃が取れている。"),
    VALUE_GAP_UNDERPRICED: (
        "高価値貨物なのに運賃が低い",
        "賠償・盗難等のリスクに見合った対価を取れていない可能性がある。保険料的な上乗せを"
        "値上げ交渉の材料にできる象限。",
    ),
    VALUE_GAP_OVERPRICED: (
        "低価値貨物なのに運賃が高い",
        "荷主から見て割高に映りうる。値下げ要求・他社への切り替えリスクの火種になる象限。",
    ),
    VALUE_GAP_BALANCED_LOW: ("価値相応の運賃(低価格帯)", "貨物価値に見合った運賃が取れている。"),
}


def _classify(x: float, y: float, x_med: float, y_med: float, hh: str, hl: str, lh: str, ll: str) -> str:
    high_x = x >= x_med
    high_y = y >= y_med
    if high_x and high_y:
        return hh
    if high_x and not high_y:
        return hl
    if not high_x and high_y:
        return lh
    return ll


def build_order_profit(
    trips: pd.DataFrame, orders: pd.DataFrame, labor_cost_per_hour: float, depreciation_per_trip: float
) -> pd.DataFrame:
    """案件(order)単位の粗利。1運行のコストを、その運行に乗っている案件数で等分配する。

    トラック1台の運行コストは1回分で固定、そこに積み合わせた案件の数で収益が決まるという
    L1.5+の考え方をそのまま踏襲し、コストも案件数で割って配分する。
    """
    trip_cost = (
        trips["actual_fuel_liters"] * trips["fuel_price_yen"]
        + trips["actual_binding_hours"] * labor_cost_per_hour
        + trips["actual_toll_yen"]
        + depreciation_per_trip
    )
    trip_info = trips[["trip_id", "month"]].copy()
    trip_info["trip_cost_yen"] = trip_cost

    orders_per_trip = orders.groupby("trip_id").size().rename("orders_per_trip")
    trip_info = trip_info.merge(orders_per_trip, on="trip_id", how="left")
    trip_info["orders_per_trip"] = trip_info["orders_per_trip"].fillna(0)
    trip_info["cost_per_order_yen"] = trip_info["trip_cost_yen"] / trip_info["orders_per_trip"].replace(0, np.nan)

    df = orders.merge(trip_info[["trip_id", "month", "cost_per_order_yen"]], on="trip_id", how="left")
    df["cost_per_order_yen"] = df["cost_per_order_yen"].fillna(0)
    df["profit_yen"] = df["ftl_rate_yen"] - df["cost_per_order_yen"]
    return df


def build_customer_portfolio(order_profit: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """顧客ポートフォリオ2×2: 売上規模 × 粗利率。全期間の集計(単月のノイズを避ける)。"""
    agg = order_profit.groupby("customer_code").agg(
        order_count=("order_id", "size"),
        revenue_yen=("ftl_rate_yen", "sum"),
        cost_yen=("cost_per_order_yen", "sum"),
        profit_yen=("profit_yen", "sum"),
    ).reset_index()
    agg["profit_rate"] = agg["profit_yen"] / agg["revenue_yen"].where(agg["revenue_yen"] != 0)
    agg = agg.merge(customers[["customer_code", "customer_name", "industry"]], on="customer_code", how="left")

    rev_med = float(agg["revenue_yen"].median())
    rate_med = float(agg["profit_rate"].median())
    agg["quadrant"] = agg.apply(
        lambda r: _classify(
            r["revenue_yen"], r["profit_rate"], rev_med, rate_med,
            QUADRANT_MAINTAIN, QUADRANT_RAISE, QUADRANT_GROW, QUADRANT_EXIT,
        ),
        axis=1,
    )
    agg["quadrant_label"] = agg["quadrant"].map(lambda q: CUSTOMER_QUADRANT_NARRATIVE[q][0])
    return agg.sort_values("revenue_yen", ascending=False).reset_index(drop=True)


def build_concentration_risk(customer_portfolio: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """上位顧客への売上依存度と、その顧客の採算を並べる。"""
    df = customer_portfolio.sort_values("revenue_yen", ascending=False).copy()
    total_rev = df["revenue_yen"].sum()
    df["revenue_share"] = df["revenue_yen"] / total_rev if total_rev else 0.0
    df["cumulative_share"] = df["revenue_share"].cumsum()
    return df.head(top_n).reset_index(drop=True)


def _shift_month(month: str, delta_months: int) -> str:
    period = pd.Period(month, freq="M") + delta_months
    return str(period)


def build_pl_summary(order_profit: pd.DataFrame, month: str) -> dict:
    """部署P/Lサマリー(単月)と前年同月比。18ヶ月分のデータでは前年同月が無い月もある。"""
    cur = order_profit[order_profit["month"] == month]
    revenue = float(cur["ftl_rate_yen"].sum())
    cost = float(cur["cost_per_order_yen"].sum())
    profit = float(cur["profit_yen"].sum())

    prior_month = _shift_month(month, -12)
    prior = order_profit[order_profit["month"] == prior_month]
    has_yoy = not prior.empty
    yoy_profit = float(prior["profit_yen"].sum()) if has_yoy else None

    return {
        "month": month,
        "revenue_yen": revenue,
        "cost_yen": cost,
        "profit_yen": profit,
        "profit_rate": (profit / revenue) if revenue else 0.0,
        "has_yoy": has_yoy,
        "yoy_month": prior_month,
        "yoy_profit_yen": yoy_profit,
        "yoy_diff_yen": (profit - yoy_profit) if has_yoy else None,
    }


def build_value_gap(orders: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """貨物価値×運賃のギャップ(3つ目のギャップ)。顧客単位で集計する。

    orders は cargo_value_yen 列を持つ生の案件データ(order_profitである必要はない。
    貨物価値と運賃の関係は原価配分と独立に決まるため)。
    """
    df = orders.copy()
    df["rate_to_value_ratio"] = df["ftl_rate_yen"] / df["cargo_value_yen"].replace(0, np.nan)

    agg = df.groupby("customer_code").agg(
        order_count=("order_id", "size"),
        cargo_value_yen=("cargo_value_yen", "mean"),
        rate_to_value_ratio=("rate_to_value_ratio", "mean"),
    ).reset_index()
    agg = agg.merge(customers[["customer_code", "customer_name", "industry"]], on="customer_code", how="left")

    val_med = float(agg["cargo_value_yen"].median())
    ratio_med = float(agg["rate_to_value_ratio"].median())
    agg["quadrant"] = agg.apply(
        lambda r: _classify(
            r["cargo_value_yen"], r["rate_to_value_ratio"], val_med, ratio_med,
            VALUE_GAP_BALANCED_HIGH, VALUE_GAP_UNDERPRICED, VALUE_GAP_OVERPRICED, VALUE_GAP_BALANCED_LOW,
        ),
        axis=1,
    )
    agg["quadrant_label"] = agg["quadrant"].map(lambda q: VALUE_GAP_NARRATIVE[q][0])
    return agg


@dataclass(frozen=True)
class ValueGapSummary:
    customer_table: pd.DataFrame

    @property
    def conflict_customers(self) -> pd.DataFrame:
        return self.customer_table[self.customer_table["quadrant"].isin(VALUE_GAP_CONFLICT)]
