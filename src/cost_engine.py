"""適正原価の積算ロジック。Streamlit(UI)に依存しない純粋なロジック層。

国交省「適正運賃」の積算要素(燃料費・人件費・減価償却費・安全確保費)に高速代を加えた
ボトムアップ方式で、損益分岐運賃(変動費のみ)と適正原価(標準的運賃相当・目標利益込み)を算出する。
支払い側(協力会社への支払い妥当性チェック)・交渉側(荷主への値上げ交渉根拠)のどちらも
同じ計算ロジックを使い、「現在の運賃」が何を指すか(支払額 or 請求額)だけがモードで変わる。
"""

import sqlite3
from dataclasses import dataclass, field

MODE_PAYMENT = "payment"          # 支払い側:協力会社への支払い妥当性チェック
MODE_NEGOTIATION = "negotiation"  # 交渉側:荷主への値上げ交渉根拠

MODE_LABELS = {
    MODE_PAYMENT: "支払い側(協力会社への支払いチェック)",
    MODE_NEGOTIATION: "交渉側(荷主への値上げ交渉)",
}


class CostEngineError(Exception):
    """原価計算を中断すべき業務エラー。"""


class MasterNotFoundError(CostEngineError):
    pass


@dataclass
class CostInput:
    course_name: str
    vehicle_code: str
    distance_km: float
    binding_hours: float
    toll_fee_yen: int
    mode: str
    current_rate_yen: int
    course_id: int = None


@dataclass
class BreakdownStep:
    label: str
    amount_yen: int


@dataclass
class CostResult:
    fuel_cost_yen: int
    labor_cost_yen: int
    depreciation_cost_yen: int
    safety_cost_yen: int
    breakeven_rate_yen: int
    appropriate_cost_yen: int
    diff_vs_current_yen: int
    diff_ratio: float
    alert_level: str
    warnings: list = field(default_factory=list)
    breakdown: list = field(default_factory=list)


def _get_setting(conn: sqlite3.Connection, key: str, cast=float):
    row = conn.execute(
        "SELECT setting_value FROM cost_settings WHERE setting_key = ?", (key,)
    ).fetchone()
    if row is None:
        raise MasterNotFoundError(f"cost_settingsに{key}が未登録です")
    return cast(row["setting_value"])


def get_vehicle(conn: sqlite3.Connection, vehicle_code: str):
    row = conn.execute(
        "SELECT * FROM vehicle_types WHERE vehicle_code = ?", (vehicle_code,)
    ).fetchone()
    if row is None:
        raise MasterNotFoundError(f"車種 {vehicle_code} が見つかりません")
    return row


def judge_alert_level(diff_ratio: float, warning_threshold: float, critical_threshold: float) -> str:
    """diff_ratio = (現在額 - 適正原価) / 適正原価。マイナスほど現在額が適正原価を下回っている。"""
    if diff_ratio < critical_threshold:
        return "CRITICAL"
    if diff_ratio < warning_threshold:
        return "WARNING"
    return "OK"


def calculate_cost(conn: sqlite3.Connection, cost_input: CostInput) -> CostResult:
    warnings = []
    breakdown = []

    if cost_input.mode not in (MODE_PAYMENT, MODE_NEGOTIATION):
        raise CostEngineError(f"不正なモードです: {cost_input.mode}")

    fuel_price = _get_setting(conn, "FUEL_PRICE_YEN_PER_L", float)
    labor_cost_per_hour = _get_setting(conn, "LABOR_COST_YEN_PER_HOUR", float)
    safety_cost_rate = _get_setting(conn, "SAFETY_COST_RATE", float)
    target_profit_rate = _get_setting(conn, "TARGET_PROFIT_RATE", float)
    warning_threshold = _get_setting(conn, "WARNING_THRESHOLD_RATE", float)
    critical_threshold = _get_setting(conn, "CRITICAL_THRESHOLD_RATE", float)

    vehicle = get_vehicle(conn, cost_input.vehicle_code)

    if cost_input.distance_km <= 0:
        warnings.append("走行距離が0です。燃料費が計算されません。入力を確認してください。")
    if cost_input.binding_hours <= 0:
        warnings.append("拘束時間が0です。人件費が計算されません。入力を確認してください。")

    # Step1: 燃料費 = 走行距離 ÷ 燃費(km/L) × 燃料単価
    fuel_cost = (cost_input.distance_km / vehicle["fuel_efficiency_km_per_l"]) * fuel_price
    breakdown.append(BreakdownStep("燃料費", round(fuel_cost)))

    # Step2: 人件費 = 拘束時間 × 人件費単価
    labor_cost = cost_input.binding_hours * labor_cost_per_hour
    breakdown.append(BreakdownStep("人件費", round(labor_cost)))

    # Step3: 減価償却費 = 車種マスタの日額(1運行=1日分と仮定した簡易按分)
    depreciation_cost = float(vehicle["daily_depreciation_yen"])
    breakdown.append(BreakdownStep("車両減価償却費(日割り)", round(depreciation_cost)))

    # Step4: 安全確保費 = (燃料費+人件費+減価償却費) × 安全確保費率
    safety_cost = (fuel_cost + labor_cost + depreciation_cost) * safety_cost_rate
    breakdown.append(BreakdownStep(f"安全確保費(率{safety_cost_rate:.1%})", round(safety_cost)))

    # Step5: 高速代(実費パススルー)
    toll_fee = float(cost_input.toll_fee_yen)
    if toll_fee:
        breakdown.append(BreakdownStep("高速代(実費)", round(toll_fee)))

    # Step6: 変動費計(燃料費+高速代) = 損益分岐運賃相当(これを下回ると運行するだけ赤字)
    breakeven_rate = fuel_cost + toll_fee

    # Step7: 適正原価(標準的運賃相当) = 全コスト要素の合計 + 目標利益率の上乗せ
    total_cost = fuel_cost + labor_cost + depreciation_cost + safety_cost + toll_fee
    appropriate_cost = total_cost * (1 + target_profit_rate)
    breakdown.append(
        BreakdownStep(f"目標利益(率{target_profit_rate:.1%})上乗せ", round(appropriate_cost - total_cost))
    )

    breakeven_rate = round(breakeven_rate)
    appropriate_cost = round(appropriate_cost)

    # Step8: モードに応じて現在額(支払額 or 請求額)との差分を評価
    diff = cost_input.current_rate_yen - appropriate_cost
    diff_ratio = diff / appropriate_cost if appropriate_cost else 0.0
    alert_level = judge_alert_level(diff_ratio, warning_threshold, critical_threshold)

    return CostResult(
        fuel_cost_yen=round(fuel_cost),
        labor_cost_yen=round(labor_cost),
        depreciation_cost_yen=round(depreciation_cost),
        safety_cost_yen=round(safety_cost),
        breakeven_rate_yen=breakeven_rate,
        appropriate_cost_yen=appropriate_cost,
        diff_vs_current_yen=diff,
        diff_ratio=diff_ratio,
        alert_level=alert_level,
        warnings=warnings,
        breakdown=breakdown,
    )
