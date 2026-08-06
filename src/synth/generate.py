"""運行実績の合成データ生成器。

## 設計の中心

各コースに「赤字の真因」(loss_pattern)を意図的に仕込み、ground truthとして保存する。
切り分けロジック(src/analysis/attribution.py)はこの正解を見ずに運行実績だけから
主因を推定するため、**推定の的中率を測定できる**。

これにより「Lv1の情報(売上−輸送費のみ)では主因を当てられないが、Lv3の情報があれば
当てられる」を数値で示せる。可視性の価値の定量化そのものである。

パターンは該当する変数だけを悪化させる:
    low_rate         → revenue_yen を下げる
    low_loaded_ratio → loaded_ratio を下げ、空車回送を増やす
    long_binding     → actual_binding_hours を距離から期待される値より伸ばす(荷待ち)
    poor_fuel        → actual_fuel_liters を公称燃費より悪化させる(ドライバー単位)
    healthy          → いずれも正常

ただし全コースが単一要因できれいに説明できると切り分けが自明になり、的中率の指標が
意味を失う。`confounder_probability` で副次要因を軽度に混ぜ、「当たるが完璧ではない」
水準に保つ。

シードはステージごとにオフセットする(`default_rng(seed + N)`)ので、あるステージの
生成方法を変えても他ステージの結果は変わらない。
"""

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import config
from src.common.calendar import generate_calendar
from src.common.config import SETTINGS, Settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "synth"

VEHICLE_CODES = ("2T", "4T", "10T")
# 車格ごとの公称燃費(km/L)。src/seed_data.py の vehicle_types と一致させること。
NOMINAL_FUEL_EFFICIENCY = {"2T": 9.0, "4T": 6.5, "10T": 4.0}
NOMINAL_DAILY_DEPRECIATION = {"2T": 3000, "4T": 5500, "10T": 9500}
INDUSTRIES = ("精密機械", "化学品", "食品")
EMPLOYMENT_TYPES = ("正社員", "契約", "協力会社")


def build_customers(settings: Settings = SETTINGS) -> pd.DataFrame:
    rng = np.random.default_rng(settings.random_seed + 1)
    n = settings.num_customers
    codes = [f"顧客{chr(ord('A') + i)}" for i in range(n)]
    return pd.DataFrame(
        {
            "customer_code": codes,
            "customer_name": codes,
            "industry": rng.choice(INDUSTRIES, size=n),
            "contract_start": "2023-04-01",
            "last_rate_revision": rng.choice(
                ["2024-04-01", "2024-10-01", "2025-04-01", "2025-10-01"], size=n
            ),
            "is_active": 1,
        }
    )


def build_vehicles(settings: Settings = SETTINGS) -> pd.DataFrame:
    rng = np.random.default_rng(settings.random_seed + 2)
    n = settings.num_vehicles
    codes = rng.choice(VEHICLE_CODES, size=n, p=[0.35, 0.45, 0.20])
    return pd.DataFrame(
        {
            "vehicle_id": [f"車両{i + 1:02d}" for i in range(n)],
            "vehicle_code": codes,
            "acquisition_cost_yen": [NOMINAL_DAILY_DEPRECIATION[c] * 1000 for c in codes],
            "in_service_date": "2022-04-01",
            "is_owned": rng.choice([1, 0], size=n, p=[0.7, 0.3]),
            "is_active": 1,
        }
    )


def build_drivers(settings: Settings = SETTINGS) -> pd.DataFrame:
    """ドライバーごとに燃費スキル係数を持たせる(1.0=公称通り、<1.0=燃費が悪い)。

    これがあることで「同じコース・同じ車格でもドライバーによって燃費が違う」という
    分析が成立する。poor_fuel パターンの担い手でもある。
    """
    rng = np.random.default_rng(settings.random_seed + 3)
    n = settings.num_drivers
    lo, hi = settings.driver_fuel_skill_range
    return pd.DataFrame(
        {
            "driver_id": [f"ドライバー{chr(ord('A') + i)}" for i in range(n)],
            "employment_type": rng.choice(EMPLOYMENT_TYPES, size=n, p=[0.5, 0.2, 0.3]),
            "hourly_cost_yen": rng.integers(2200, 2700, size=n),
            "fuel_skill_factor": rng.uniform(lo, hi, size=n),
            "is_active": 1,
        }
    )


def build_courses(settings: Settings = SETTINGS) -> pd.DataFrame:
    """コース諸元と、各コースに仕込む loss_pattern(ground truth)を決める。"""
    rng = np.random.default_rng(settings.random_seed + 4)
    n = settings.num_courses

    dist_lo, dist_hi = settings.distance_km_range
    distance = rng.uniform(dist_lo, dist_hi, size=n)

    hand_lo, hand_hi = settings.handling_hours_range
    wait_lo, wait_hi = settings.base_waiting_hours_range

    # 主因の割り当て: healthy を所定割合、残りを4パターンへ均等配分
    n_healthy = int(round(n * settings.healthy_course_share))
    faulty = [
        config.PATTERN_LOW_RATE,
        config.PATTERN_LOW_LOADED,
        config.PATTERN_LONG_BINDING,
        config.PATTERN_POOR_FUEL,
    ]
    patterns = [config.PATTERN_HEALTHY] * n_healthy
    for i in range(n - n_healthy):
        patterns.append(faulty[i % len(faulty)])
    patterns = np.array(patterns, dtype=object)
    rng.shuffle(patterns)

    sev_lo, sev_hi = settings.severity_range
    severity = np.where(
        patterns == config.PATTERN_HEALTHY, 0.0, rng.uniform(sev_lo, sev_hi, size=n)
    )

    # 交絡: 一部のコースに副次要因を軽度で付与する。これが無いと切り分けが自明になり、
    # 的中率という指標が意味を持たなくなる。
    has_conf = (rng.random(n) < settings.confounder_probability) & (
        patterns != config.PATTERN_HEALTHY
    )
    confounder = np.full(n, None, dtype=object)
    for i in range(n):
        if not has_conf[i]:
            continue
        candidates = [p for p in faulty if p != patterns[i]]
        confounder[i] = candidates[rng.integers(0, len(candidates))]

    return pd.DataFrame(
        {
            "course_id": np.arange(1, n + 1),
            "course_name": [f"コース{i + 1:02d}" for i in range(n)],
            "vehicle_code": rng.choice(VEHICLE_CODES, size=n, p=[0.35, 0.45, 0.20]),
            "distance_km": distance.round(1),
            "handling_hours": rng.uniform(hand_lo, hand_hi, size=n).round(2),
            "base_waiting_hours": rng.uniform(wait_lo, wait_hi, size=n).round(2),
            "has_toll": (rng.random(n) < settings.toll_probability).astype(int),
            "loss_pattern": patterns,
            "severity": severity.round(3),
            "confounder_pattern": confounder,
        }
    )


def _fuel_price_series(calendar_df: pd.DataFrame, settings: Settings) -> pd.Series:
    """燃料単価を期間中に上昇させる(L2の感度分析の裏付けになる)。"""
    rng = np.random.default_rng(settings.random_seed + 5)
    n = len(calendar_df)
    trend = np.linspace(settings.fuel_price_start_yen, settings.fuel_price_end_yen, n)
    noise = rng.normal(0.0, settings.fuel_price_noise_sd, size=n)
    return pd.Series(np.clip(trend + noise, 100.0, None), index=calendar_df.index)


def _pattern_strength(course_row, pattern: str, settings: Settings) -> float:
    """そのコースが指定パターンをどの強度で持つか。主因なら severity、交絡なら減衰。"""
    if course_row["loss_pattern"] == pattern:
        return float(course_row["severity"])
    if course_row["confounder_pattern"] == pattern:
        return float(course_row["severity"]) * settings.confounder_strength
    return 0.0


def build_trip_orders(
    trips: pd.DataFrame,
    courses: pd.DataFrame,
    customers: pd.DataFrame,
    settings: Settings = SETTINGS,
) -> pd.DataFrame:
    """トラック1台の運行に積み合わせたFTL請求案件(業者目線の実収益)を生成する。

    実務は特積(混載)だが荷主にはFTLとして請求する、という実態を反映する:
    各案件の単価は積み合わせ相手が何件いようと一定(=積み合わせても値引きしない)。
    コストは trips 側(1運行=1回分)に既に確定しているので、集約数が多いほど
    同じコストで多くの案件収入を得られる。

    trips.loaded_ratio(物理積載率、荷主が体感しうる軸)とは緩く相関させつつ十分な
    ノイズを残す。「積載率は高いが集約数は少ない」「積載率は低いが集約数は多い」
    という食い違いケースが実際に生まれることが、荷主目線×業者目線ギャップ分析の前提。
    """
    rng = np.random.default_rng(settings.random_seed + 8)
    n = len(trips)

    lo, hi = settings.orders_per_trip_base_range
    base_mean = (lo + hi) / 2.0

    # 相関付けは「コース単位で1回だけ」行う。トリップ単位でノイズを足すと、
    # 1コースあたり数百件のトリップを平均した時点で大数の法則によりノイズが
    # 打ち消し合い、コース集計後の相関が設計値より大幅に強く出てしまうため
    # (実測で trip単位0.38 → コース単位0.99 まで見かけ上増幅する現象を確認した)。
    course_loaded = trips.groupby("course_id")["loaded_ratio"].mean()
    course_ids = courses["course_id"].to_numpy()
    course_loaded_vals = course_loaded.reindex(course_ids).fillna(course_loaded.mean()).to_numpy()
    loaded_z = (course_loaded_vals - course_loaded_vals.mean()) / (course_loaded_vals.std() + 1e-9)

    course_rng = np.random.default_rng(settings.random_seed + 10)
    course_setpoint = (
        base_mean
        + settings.consolidation_loaded_ratio_correlation * loaded_z * (hi - lo) / 2.0
        + course_rng.normal(0.0, settings.orders_per_trip_noise_sd, size=len(course_ids))
    )
    setpoint_map = dict(zip(course_ids, course_setpoint))
    trip_setpoint = trips["course_id"].map(setpoint_map).to_numpy()

    # トリップ単位のノイズ(同じコースでも日によって積める案件数は変動する)
    raw_orders = trip_setpoint + rng.normal(0.0, settings.orders_per_trip_noise_sd * 0.5, size=n)
    orders_per_trip = np.clip(np.round(raw_orders), 1, None).astype(int)

    course_map = courses.set_index("course_id")
    distance = trips["course_id"].map(course_map["distance_km"]).to_numpy()
    vehicle_code = trips["course_id"].map(course_map["vehicle_code"]).to_numpy()
    handling_hours = trips["course_id"].map(course_map["handling_hours"]).to_numpy()
    base_waiting_hours = trips["course_id"].map(course_map["base_waiting_hours"]).to_numpy()
    has_toll = trips["course_id"].map(course_map["has_toll"]).to_numpy()

    nominal_eff = np.array([NOMINAL_FUEL_EFFICIENCY[v] for v in vehicle_code])
    nominal_dep = np.array([NOMINAL_DAILY_DEPRECIATION[v] for v in vehicle_code])

    # 1運行の標準原価(計画ベース)。generate_trips()内のrevenue_yen算出と同じ考え方:
    # 燃料費+人件費(契約時点の参照単価2400円/h)+減価償却+高速代の全要素を含める。
    # 燃料費・減価償却だけを見て人件費・高速代を漏らすと、単価が原価の4割程度にしか
    # ならず全コースが恒常的な赤字になる(実測で確認した)。
    standard_fuel = distance / nominal_eff
    standard_hours = distance / settings.average_speed_kmh + handling_hours + base_waiting_hours
    standard_toll = has_toll * distance * settings.toll_yen_per_km
    standard_cost_per_trip = (
        standard_fuel * settings.fuel_price_start_yen
        + standard_hours * 2400.0
        + nominal_dep
        + standard_toll
    )

    mk_lo, mk_hi = settings.order_markup_range
    markup = rng.uniform(mk_lo, mk_hi, size=n)
    # 1運行あたりの基準総額を「基本案件数」で割り、1案件あたりの単価アンカーにする。
    # 実際のその運行の集約数では割らない(=積み合わせても単価が変わらない実態のため)。
    base_rate_per_order = (standard_cost_per_trip * markup) / base_mean

    rep_trip_id = np.repeat(trips["trip_id"].to_numpy(), orders_per_trip)
    rep_base_rate = np.repeat(base_rate_per_order, orders_per_trip)
    m = len(rep_trip_id)

    order_rng = np.random.default_rng(settings.random_seed + 9)
    ftl_rate = rep_base_rate * order_rng.normal(1.0, settings.order_rate_noise_sd, size=m)
    ftl_rate = np.clip(ftl_rate, 1000, None).round().astype(int)

    customer_codes = order_rng.choice(customers["customer_code"].to_numpy(), size=m)
    requested_service = order_rng.choice(settings.requested_services, size=m)

    # 貨物価値(運賃とは独立に決まる。運賃が距離・車格からしか決まっていない実態を
    # 検証するための軸なので、意図的にftl_rateと相関させない)。
    industry_map = customers.set_index("customer_code")["industry"].to_dict()
    order_industry = pd.Series(customer_codes).map(industry_map).to_numpy()
    value_rng = np.random.default_rng(settings.random_seed + 11)
    cargo_value = np.empty(m)
    for industry, (lo, hi) in settings.cargo_value_range_by_industry.items():
        mask = order_industry == industry
        n_ind = int(mask.sum())
        if n_ind == 0:
            continue
        log_value = value_rng.uniform(np.log(lo), np.log(hi), size=n_ind)
        cargo_value[mask] = np.exp(log_value)
    cargo_value_yen = np.round(cargo_value).astype(int)

    return pd.DataFrame(
        {
            "order_id": np.arange(1, m + 1),
            "trip_id": rep_trip_id,
            "customer_code": customer_codes,
            "ftl_rate_yen": ftl_rate,
            "cargo_value_yen": cargo_value_yen,
            "requested_service": requested_service,
        }
    )


def generate_trips(
    start_date: date = date(2025, 1, 1),
    num_months: int = None,
    settings: Settings = SETTINGS,
) -> dict:
    """マスタと運行実績を生成して dict of DataFrame を返す。永続化は行わない。"""
    num_months = num_months if num_months is not None else settings.num_months

    customers = build_customers(settings)
    vehicles = build_vehicles(settings)
    drivers = build_drivers(settings)
    courses = build_courses(settings)

    calendar_df = generate_calendar(start_date, num_months, settings)
    calendar_df = calendar_df[calendar_df["is_business_day"]].reset_index(drop=True)
    fuel_price = _fuel_price_series(calendar_df, settings)

    rng = np.random.default_rng(settings.random_seed + 6)

    # 季節指数は月ごとに1回抽選し、その月内は一定に保つ(正弦波にはしない)
    months = calendar_df["month"].unique()
    s_lo, s_hi = settings.seasonal_index_range
    seasonal = dict(zip(months, rng.uniform(s_lo, s_hi, size=len(months))))
    calendar_df["seasonal_index"] = calendar_df["month"].map(seasonal)

    calendar_df["dow_coef"] = calendar_df["dow"].map(settings.dow_coefficients)
    calendar_df["boundary_coef"] = np.where(
        calendar_df["is_month_end"],
        settings.month_end_coefficient,
        np.where(calendar_df["is_month_start"], settings.month_start_coefficient, 1.0),
    )

    # コース×営業日の総当たりを作り、運行有無を確率的に決める
    grid = courses.assign(_k=1).merge(calendar_df.assign(_k=1), on="_k").drop(columns="_k")

    intensity = (
        settings.base_trips_per_course_per_day
        * grid["dow_coef"]
        * grid["seasonal_index"]
        * grid["boundary_coef"]
        * rng.normal(1.0, settings.volume_noise_sd, size=len(grid))
    )
    grid = grid[rng.random(len(grid)) < np.clip(intensity, 0.0, 1.0)].reset_index(drop=True)

    n = len(grid)
    rng2 = np.random.default_rng(settings.random_seed + 7)

    # 車両・ドライバーの割当
    grid["vehicle_id"] = rng2.choice(vehicles["vehicle_id"].to_numpy(), size=n)
    grid["driver_id"] = rng2.choice(drivers["driver_id"].to_numpy(), size=n)
    grid["customer_code"] = rng2.choice(customers["customer_code"].to_numpy(), size=n)

    veh_map = vehicles.set_index("vehicle_id")["vehicle_code"].to_dict()
    grid["actual_vehicle_code"] = grid["vehicle_id"].map(veh_map)

    drv_fuel = drivers.set_index("driver_id")["fuel_skill_factor"].to_dict()
    grid["driver_fuel_skill"] = grid["driver_id"].map(drv_fuel)

    # --- パターン強度をベクトル化して取り出す ---
    for pat in (
        config.PATTERN_LOW_RATE,
        config.PATTERN_LOW_LOADED,
        config.PATTERN_LONG_BINDING,
        config.PATTERN_POOR_FUEL,
    ):
        grid[f"s_{pat}"] = grid.apply(lambda r: _pattern_strength(r, pat, settings), axis=1)

    # --- 実走行距離 ---
    grid["actual_distance_km"] = (
        grid["distance_km"] * rng2.normal(1.0, 0.05, size=n)
    ).clip(1.0).round(1)

    # --- 積載率(low_loaded_ratio が効く) ---
    ll_lo, ll_hi = settings.healthy_loaded_ratio_range
    base_loaded = rng2.uniform(ll_lo, ll_hi, size=n)
    loaded = base_loaded * (1.0 - settings.pattern_low_loaded_penalty * grid[f"s_{config.PATTERN_LOW_LOADED}"])
    loaded = loaded * rng2.normal(1.0, settings.loaded_ratio_noise_sd, size=n)
    grid["loaded_ratio"] = loaded.clip(0.05, 1.0).round(3)
    grid["is_empty_run"] = (grid["loaded_ratio"] < 0.15).astype(int)

    # --- 実拘束時間(long_binding が効く。走行時間+荷役+荷待ち) ---
    drive_hours = grid["actual_distance_km"] / settings.average_speed_kmh
    extra_wait = settings.pattern_long_binding_extra_hours * grid[f"s_{config.PATTERN_LONG_BINDING}"]
    binding = (
        drive_hours + grid["handling_hours"] + grid["base_waiting_hours"] + extra_wait
    ) * rng2.normal(1.0, 0.06, size=n)
    grid["actual_binding_hours"] = binding.clip(0.5, None).round(2)

    # --- 実燃料(poor_fuel が効く。ドライバースキルとの合成) ---
    nominal_eff = grid["actual_vehicle_code"].map(NOMINAL_FUEL_EFFICIENCY)
    effective_eff = (
        nominal_eff
        * grid["driver_fuel_skill"]
        * (1.0 - settings.pattern_poor_fuel_penalty * grid[f"s_{config.PATTERN_POOR_FUEL}"])
    )
    fuel_liters = (grid["actual_distance_km"] / effective_eff) * rng2.normal(
        1.0, settings.fuel_noise_sd, size=n
    )
    grid["actual_fuel_liters"] = fuel_liters.clip(0.1, None).round(2)

    # --- 高速代 ---
    grid["actual_toll_yen"] = (
        grid["has_toll"] * grid["actual_distance_km"] * settings.toll_yen_per_km
    ).round().astype(int)

    # --- 売上 ---
    # 重要: 運賃は「計画上のコース諸元」に基づいて事前に合意されるものであり、
    # 実績が悪化しても自動的には上がらない。実績悪化はキャリア側が被る。
    # (実コストから逆算すると、荷待ちが伸びても燃費が悪化しても売上が連動して上がり、
    #  粗利が痩せない = 仕込んだ赤字パターンが粗利に現れないという誤りになる)
    calendar_df["fuel_price"] = fuel_price.to_numpy()
    price_map = calendar_df.set_index("date")["fuel_price"].to_dict()
    grid["fuel_price_yen"] = grid["date"].map(price_map)

    # 計画上の標準原価。荷待ち上乗せ・ドライバー燃費差・積載率低下は一切含めない。
    standard_hours = (
        grid["distance_km"] / settings.average_speed_kmh
        + grid["handling_hours"]
        + grid["base_waiting_hours"]
    )
    standard_fuel = grid["distance_km"] / grid["actual_vehicle_code"].map(NOMINAL_FUEL_EFFICIENCY)
    standard_toll = (grid["has_toll"] * grid["distance_km"] * settings.toll_yen_per_km)
    standard_cost = (
        standard_fuel * settings.fuel_price_start_yen  # 契約時点の燃料単価で値決めされる
        + standard_hours * 2400.0
        + grid["actual_vehicle_code"].map(NOMINAL_DAILY_DEPRECIATION)
        + standard_toll
    )

    mk_lo, mk_hi = settings.healthy_markup_range
    markup = rng2.uniform(mk_lo, mk_hi, size=n)
    base_rate = standard_cost * markup

    # 積載率は請求量に連動する(積んだ分だけ請求できる)。健全水準を1.0とする相対値。
    reference_loaded = float(np.mean(settings.healthy_loaded_ratio_range))
    billing_factor = (grid["loaded_ratio"] / reference_loaded).clip(upper=1.05)

    revenue = (
        base_rate
        * billing_factor
        * (1.0 - settings.pattern_low_rate_discount * grid[f"s_{config.PATTERN_LOW_RATE}"])
        * rng2.normal(1.0, settings.revenue_noise_sd, size=n)
    )
    grid["revenue_yen"] = revenue.clip(1000, None).round().astype(int)

    trips = grid[
        [
            "date", "course_id", "customer_code", "vehicle_id", "driver_id",
            "revenue_yen", "actual_distance_km", "actual_binding_hours",
            "actual_fuel_liters", "actual_toll_yen", "loaded_ratio", "is_empty_run",
            "fuel_price_yen",
        ]
    ].rename(columns={"date": "trip_date"}).reset_index(drop=True)
    trips["trip_date"] = trips["trip_date"].dt.strftime("%Y-%m-%d")
    # trip_idを生成段階で明示的に採番する(trip_ordersからFKで参照するため。
    # DB投入時のAUTOINCREMENTだけに頼ると、Python側でIDを把握できない)
    trips.insert(0, "trip_id", np.arange(1, len(trips) + 1))

    trip_orders = build_trip_orders(trips, courses, customers, settings)

    return {
        "customers": customers,
        "vehicles": vehicles,
        "drivers": drivers,
        "courses": courses,
        "trips": trips,
        "trip_orders": trip_orders,
    }


def save_frames(frames: dict, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(description="運行実績の合成データを生成する")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--num-months", type=int, default=SETTINGS.num_months)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    frames = generate_trips(
        start_date=date.fromisoformat(args.start_date), num_months=args.num_months
    )
    save_frames(frames, Path(args.output))

    trips = frames["trips"]
    trip_orders = frames["trip_orders"]
    print(f"生成完了: {args.output}")
    print(f"  運行件数 {len(trips):,} / コース {len(frames['courses'])} / 期間 {args.num_months}ヶ月")
    print(
        f"  案件件数 {len(trip_orders):,}"
        f"(1運行あたり平均 {len(trip_orders) / len(trips):.2f}件)"
    )
    print("  仕込んだ主因の内訳:")
    for pattern, cnt in frames["courses"]["loss_pattern"].value_counts().items():
        print(f"    {pattern:<18} {cnt}")


if __name__ == "__main__":
    main()
