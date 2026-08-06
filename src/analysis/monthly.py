"""月次採算カルテ(L1)。「毎月開く理由」を作る層。

## この画面が想定する現場での使い方

毎月月初、先月分の実績が確定したらこの画面を開く。月次推移で悪化トレンドが無いかを見て、
コース別サマリーで低採算コースを見つけ、車両→ドライバー→個別運行とクリックで掘り下げて
原因の当たりをつける。

## 収益モデルについて(重要な設計上の注記)

本モジュールは`trip_orders`(業者目線の実収益、L1.5+で導入)を正として使う。既存の
L1.5(`attribution.py`/`visibility.py`)とL2(`sensitivity.py`)は`trips.revenue_yen`
(積載率ベースの旧モデル)を使っており、**2つの収益モデルが並存している**。
本人の実務知見(集約数が実際の利益を決める)に基づき、月次の実績を扱うL1では
より実態に近い`trip_orders`ベースを採用した。旧モデルの移行は今回のスコープ外。
"""

import pandas as pd


def business_days_in_month(month: str) -> int:
    """"YYYY-MM"の営業日数(月〜金)を返す。祝日は考慮しない(calendar.pyと同じ簡易モデル)。"""
    period = pd.Period(month, freq="M")
    dates = pd.date_range(period.start_time, period.end_time, freq="D")
    return int((dates.dayofweek < 5).sum())


def enrich_trips(
    trips: pd.DataFrame, orders: pd.DataFrame, labor_cost_per_hour: float, depreciation_per_trip: float
) -> pd.DataFrame:
    """tripsに、その運行の実収益(trip_orders合計)と実コストを付与する。

    重い集計を1回だけ行い、以降の build_*/rank_* 関数はこの結果を使い回す。
    """
    revenue = orders.groupby("trip_id")["ftl_rate_yen"].sum().rename("revenue_yen_actual")
    df = trips.merge(revenue, on="trip_id", how="left")
    df["revenue_yen_actual"] = df["revenue_yen_actual"].fillna(0)
    df["cost_yen"] = (
        df["actual_fuel_liters"] * df["fuel_price_yen"]
        + df["actual_binding_hours"] * labor_cost_per_hour
        + df["actual_toll_yen"]
        + depreciation_per_trip
    )
    df["profit_yen"] = df["revenue_yen_actual"] - df["cost_yen"]
    return df


def build_course_monthly(enriched: pd.DataFrame, courses: pd.DataFrame, month: str) -> pd.DataFrame:
    """コース別の月次サマリー。売上・原価・粗利・粗利率・運行回数・稼働率。"""
    month_df = enriched[enriched["month"] == month]
    biz_days = business_days_in_month(month)

    agg = month_df.groupby("course_id").agg(
        trip_count=("trip_id", "size"),
        revenue_yen=("revenue_yen_actual", "sum"),
        cost_yen=("cost_yen", "sum"),
        profit_yen=("profit_yen", "sum"),
        active_days=("trip_date", "nunique"),
    ).reset_index()

    agg["profit_rate"] = agg["profit_yen"] / agg["revenue_yen"].where(agg["revenue_yen"] != 0)
    agg["utilization_rate"] = (agg["active_days"] / biz_days).clip(upper=1.0) if biz_days else 0.0
    agg = agg.merge(courses[["course_id", "course_name"]], on="course_id", how="left")
    return agg.sort_values("profit_yen").reset_index(drop=True)


def build_profit_trend(enriched: pd.DataFrame) -> pd.DataFrame:
    """全期間の月次粗利推移(コース横断合計)。"""
    return (
        enriched.groupby("month")
        .agg(
            revenue_yen=("revenue_yen_actual", "sum"),
            cost_yen=("cost_yen", "sum"),
            profit_yen=("profit_yen", "sum"),
        )
        .reset_index()
        .sort_values("month")
    )


def build_vehicle_breakdown(enriched: pd.DataFrame, course_id: int, month: str) -> pd.DataFrame:
    """ドリルダウン第2階層: 選択したコース×月の車両別内訳。"""
    sel = enriched[(enriched["course_id"] == course_id) & (enriched["month"] == month)]
    agg = sel.groupby("vehicle_id").agg(
        trip_count=("trip_id", "size"),
        revenue_yen=("revenue_yen_actual", "sum"),
        cost_yen=("cost_yen", "sum"),
        profit_yen=("profit_yen", "sum"),
    ).reset_index()
    return agg.sort_values("profit_yen").reset_index(drop=True)


def build_driver_breakdown(enriched: pd.DataFrame, course_id: int, vehicle_id: str, month: str) -> pd.DataFrame:
    """ドリルダウン第3階層: 選択したコース×車両×月のドライバー別内訳。"""
    sel = enriched[
        (enriched["course_id"] == course_id)
        & (enriched["vehicle_id"] == vehicle_id)
        & (enriched["month"] == month)
    ]
    agg = sel.groupby("driver_id").agg(
        trip_count=("trip_id", "size"),
        revenue_yen=("revenue_yen_actual", "sum"),
        cost_yen=("cost_yen", "sum"),
        profit_yen=("profit_yen", "sum"),
    ).reset_index()
    return agg.sort_values("profit_yen").reset_index(drop=True)


def build_trip_detail(enriched: pd.DataFrame, course_id: int, vehicle_id: str, driver_id: str, month: str) -> pd.DataFrame:
    """ドリルダウン第4階層(最終): 個別運行一覧。"""
    sel = enriched[
        (enriched["course_id"] == course_id)
        & (enriched["vehicle_id"] == vehicle_id)
        & (enriched["driver_id"] == driver_id)
        & (enriched["month"] == month)
    ]
    cols = [
        "trip_id", "trip_date", "revenue_yen_actual", "cost_yen", "profit_yen",
        "actual_distance_km", "actual_binding_hours", "loaded_ratio",
    ]
    return sel[cols].sort_values("trip_date").reset_index(drop=True)


def _rank(enriched: pd.DataFrame, month: str, by: str, top_n: int) -> dict:
    month_df = enriched[enriched["month"] == month]
    agg = month_df.groupby(by).agg(
        trip_count=("trip_id", "size"),
        profit_yen=("profit_yen", "sum"),
    ).reset_index().sort_values("profit_yen", ascending=False)
    return {
        "top": agg.head(top_n).reset_index(drop=True),
        "bottom": agg.tail(top_n).sort_values("profit_yen").reset_index(drop=True),
    }


def rank_vehicles(enriched: pd.DataFrame, month: str, top_n: int = 5) -> dict:
    """全社(コース横断)の車両別採算ランキング。"""
    return _rank(enriched, month, "vehicle_id", top_n)


def rank_drivers(enriched: pd.DataFrame, month: str, top_n: int = 5) -> dict:
    """全社(コース横断)のドライバー別採算ランキング。"""
    return _rank(enriched, month, "driver_id", top_n)
