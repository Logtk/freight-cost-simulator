"""合成データ生成のパラメータを一元管理する。

係数をコード側に直書きしないこと。生成ロジックを読む人が「どの数字が効いているか」を
このファイルだけで把握できる状態を保つ。シナリオ違いを作る場合は
`dataclasses.replace(SETTINGS, ...)` で派生させ、SETTINGS自体は変更しない。
"""

from dataclasses import dataclass, field

# 赤字の真因(loss_pattern)。合成データ生成時に各コースへ意図的に仕込み、
# ground truthとして保存する。切り分けロジックはこれを見ずに推定する。
PATTERN_HEALTHY = "healthy"
PATTERN_LOW_RATE = "low_rate"
PATTERN_LOW_LOADED = "low_loaded_ratio"
PATTERN_LONG_BINDING = "long_binding"
PATTERN_POOR_FUEL = "poor_fuel"

LOSS_PATTERNS = (
    PATTERN_HEALTHY,
    PATTERN_LOW_RATE,
    PATTERN_LOW_LOADED,
    PATTERN_LONG_BINDING,
    PATTERN_POOR_FUEL,
)

# 主因ごとの打ち手と交渉相手。docs/data_visibility_assessment.md の4パターン表と対応させる。
PATTERN_ACTIONS = {
    PATTERN_HEALTHY: ("維持", "—"),
    PATTERN_LOW_RATE: ("運賃改定交渉", "荷主"),
    PATTERN_LOW_LOADED: ("帰り荷営業・配車統合", "他部署(配車)"),
    PATTERN_LONG_BINDING: ("荷待ち削減・条件見直し", "荷主 or 自社倉庫"),
    PATTERN_POOR_FUEL: ("教育・車両更新", "他部署(輸送)"),
}


@dataclass(frozen=True)
class Settings:
    # --- 再現性 ---
    random_seed: int = 42

    # --- 生成期間・規模 ---
    num_months: int = 18
    num_courses: int = 24
    num_customers: int = 6
    num_vehicles: int = 18
    num_drivers: int = 22

    # --- 波動(乗算型) ---
    # 1コースあたりの基準運行回数(営業日ベース)
    base_trips_per_course_per_day: float = 1.0
    dow_coefficients: dict = field(
        default_factory=lambda: {0: 1.05, 1: 1.00, 2: 0.98, 3: 1.02, 4: 1.15, 5: 0.0, 6: 0.0}
    )
    # 季節指数は月ごとに1回抽選し、その月内は一定に保つ(正弦波にはしない)
    seasonal_index_range: tuple = (0.85, 1.18)
    month_end_coefficient: float = 1.25
    month_start_coefficient: float = 0.92
    month_boundary_days: int = 2
    volume_noise_sd: float = 0.06

    # --- コース諸元の分布 ---
    distance_km_range: tuple = (18.0, 220.0)
    # 拘束時間 = 走行時間(距離/平均速度) + 積み下ろし + 荷待ち
    average_speed_kmh: float = 28.0
    handling_hours_range: tuple = (0.8, 1.8)
    base_waiting_hours_range: tuple = (0.2, 0.9)
    toll_probability: float = 0.55
    toll_yen_per_km: float = 26.0

    # --- 運賃(売上)の基準 ---
    # 健全コースは適正原価に対してこの範囲のマークアップで請求されている想定
    healthy_markup_range: tuple = (1.24, 1.44)
    revenue_noise_sd: float = 0.04

    # --- 積載率 ---
    healthy_loaded_ratio_range: tuple = (0.78, 0.95)
    loaded_ratio_noise_sd: float = 0.05

    # --- 燃費 ---
    # ドライバーごとに公称燃費に対する倍率を持たせる(1.0=公称通り、<1.0=悪い)
    driver_fuel_skill_range: tuple = (0.93, 1.07)
    fuel_noise_sd: float = 0.04

    # --- 燃料単価の時系列変動(L2感度分析の裏付け) ---
    fuel_price_start_yen: float = 158.0
    fuel_price_end_yen: float = 178.0
    fuel_price_noise_sd: float = 2.5

    # --- 赤字パターンの仕込み ---
    # 健全コースの割合。残りを4パターンへ均等配分する
    healthy_course_share: float = 0.34
    # 主因の強度(severity)。各パターンの該当変数だけをこの幅で悪化させる
    severity_range: tuple = (0.55, 1.0)
    pattern_low_rate_discount: float = 0.30      # 運賃を最大30%下げる
    pattern_low_loaded_penalty: float = 0.42     # 積載率を最大42%下げる
    pattern_long_binding_extra_hours: float = 3.2  # 荷待ちを最大3.2h上乗せ
    # 燃料費は原価に占める割合が人件費より小さいため、他パターンと同等の粗利インパクトを
    # 出すには強めの係数が要る。ここを他と揃えると「燃費だけ検知されない」状態になる。
    pattern_poor_fuel_penalty: float = 0.44      # 燃費を最大44%悪化させる

    # --- 交絡(重要) ---
    # 全コースが単一要因できれいに説明できると切り分けが自明になり、正解率の指標が
    # 意味を失う。副次要因を軽度に混ぜて「当たるが完璧ではない」水準に保つ。
    confounder_probability: float = 0.35
    confounder_strength: float = 0.34

    # --- 案件の集約(業者目線)。実務は特積(混載)だが荷主にはFTLとして請求する実態を反映。
    # トラック1台の運行コストは1回分で固定、そこに積み合わせたFTL請求案件の数が収益を決める。
    # 既存の loss_pattern(4パターン)とは独立の軸として扱う: あるコースの loaded_ratio(物理
    # 積載率、荷主が体感しうる軸)と orders_per_trip(集約数、業者だけが見える軸)は別物であり、
    # 両者の乖離こそが「荷主目線×業者目線のギャップ」の分析対象になる。
    orders_per_trip_base_range: tuple = (1, 6)   # 1運行あたりの基本案件数(コースごとに抽選)
    # loaded_ratioとの相関強度。1.0だと積載率がそのまま集約数を決めてしまい食い違いが
    # 生まれない。0.0だと完全無相関でストーリーが弱くなる。中間を狙う。
    consolidation_loaded_ratio_correlation: float = 0.2
    orders_per_trip_noise_sd: float = 1.1
    # 各案件のFTL単価算出に使うマークアップ(healthy_markup_rangeとは別に、案件単価は
    # 積み合わせ相手の有無に関わらず一定という実態を反映するため独立に持つ)
    order_markup_range: tuple = (1.15, 1.35)
    order_rate_noise_sd: float = 0.08

    requested_services: tuple = ("時間帯指定", "定時納品", "当日便対応", "附帯作業付き")

    # --- 貨物価値(L4「貨物価値×運賃のギャップ」用)。運賃は距離・車格・拘束時間からしか
    # 決まっておらず、貨物の経済価値を一切見ていない、という実態を検証するための軸。
    # 業種ごとに価値密度のレンジを分け(精密機械>化学品>食品)、レンジ内は対数一様分布で
    # 抽選する(実際の貨物価値は数千円〜数千万円までオーダーが何桁も違うため)。
    cargo_value_range_by_industry: dict = field(
        default_factory=lambda: {
            "精密機械": (500_000.0, 50_000_000.0),
            "化学品": (200_000.0, 20_000_000.0),
            "食品": (50_000.0, 5_000_000.0),
        }
    )


SETTINGS = Settings()
