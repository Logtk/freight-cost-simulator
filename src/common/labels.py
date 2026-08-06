"""表示名マッピングの一元管理。

列名・関数名・変数名は英語のまま維持し(スキーマ安定性のため)、人が読む表示だけを
ここで日本語化する。未登録キーはそのまま返すので、新しい列を足しても壊れない。
"""

from src.common import config

DISPLAY_NAMES = {
    # 赤字の主因(loss_pattern)
    config.PATTERN_HEALTHY: "健全",
    config.PATTERN_LOW_RATE: "運賃が安い",
    config.PATTERN_LOW_LOADED: "積載率が低い",
    config.PATTERN_LONG_BINDING: "拘束時間が長い",
    config.PATTERN_POOR_FUEL: "燃費が悪い",

    # 運行実績(trips)の列
    "trip_date": "運行日",
    "course_name": "コース",
    "customer_code": "顧客",
    "vehicle_id": "車両",
    "driver_id": "ドライバー",
    "revenue_yen": "売上",
    "actual_distance_km": "実走行距離(km)",
    "actual_binding_hours": "実拘束時間(h)",
    "actual_fuel_liters": "実燃料(L)",
    "actual_toll_yen": "高速代",
    "loaded_ratio": "積載率",

    # 原価の内訳
    "fuel_cost_yen": "燃料費",
    "labor_cost_yen": "人件費",
    "depreciation_cost_yen": "減価償却費",
    "safety_cost_yen": "安全確保費",
    "total_cost_yen": "原価計",
    "gross_profit_yen": "粗利",
    "gross_profit_rate": "粗利率",

    # 可視性
    "visibility_level": "可視性レベル",
    "estimated_pattern": "推定主因",
    "true_pattern": "真の主因",
    "accuracy": "的中率",
    "lv1_accuracy": "Lv1の的中率",
    "lv3_accuracy": "Lv3の的中率",
    "action": "打ち手",
    "counterpart": "交渉相手",
    "trip_count": "運行回数",

    # 荷主目線×業者目線のギャップ(trip_orders)
    "order_id": "案件ID",
    "orders_per_trip": "集約数(1運行あたり案件数)",
    "ftl_rate_yen": "FTL請求額",
    "requested_service": "希望サービス",
    "shipper_view": "荷主目線(積載率)",
    "carrier_view": "業者目線(集約数)",
    "quadrant": "象限",
    "actual_gross_profit_yen": "実際の粗利",

    # 月次採算カルテ(L1)
    "month": "月",
    "revenue_yen_actual": "実収益(業者目線)",
    "cost_yen": "原価",
    "profit_yen": "粗利",
    "profit_rate": "粗利率",
    "utilization_rate": "稼働率(簡易)",
    "active_days": "運行日数",

    # 経営レポート(L4)
    "customer_name": "顧客",
    "industry": "業種",
    "order_count": "案件数",
    "revenue_share": "売上構成比",
    "cumulative_share": "累積構成比",
    "cargo_value_yen": "貨物価値(平均)",
    "rate_to_value_ratio": "運賃/貨物価値比率",
    "yoy_profit_yen": "前年同月粗利",
    "yoy_diff_yen": "前年同月比",

    # 交渉アクション化(L3)
    "shortfall_yen": "目標粗利率への不足額",
    "months_since_revision": "前回改定からの経過月数",
    "target_increase_yen": "要求増額目安",
    "agreed_increase_yen": "合意増額",
    "priority_score": "優先度",
    "status": "ステータス",
    "opened_at": "起票日",
    "next_review_date": "次回見直し日",
    "memo": "メモ",
    "standard_cost_yen": "標準原価",
    "standard_rate_yen": "標準単価",
    "current_rate_yen": "現行運賃",
    "diff_yen": "差額",
}


def label(key: str) -> str:
    return DISPLAY_NAMES.get(key, key)
