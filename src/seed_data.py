"""マスタ・サンプルコースの初期投入。

車種の燃費・減価償却額、燃料単価・人件費単価等のコスト設定は全て仮置きの架空値。
実務で使う際は資源エネルギー庁の燃料価格、賃金構造基本統計調査、自社の実際の車両減価償却額で
`cost_settings` / `vehicle_types` を更新すること(README参照)。
コース名・現在の運賃も実在の取引先データではなく説明用のダミー値。
"""

import sqlite3

from src import db

VEHICLE_TYPES = [
    # vehicle_code, vehicle_name, fuel_efficiency_km_per_l, daily_depreciation_yen, display_order
    ("2T", "2t車", 9.0, 3000, 1),
    ("4T", "4t車", 6.5, 5500, 2),
    ("10T", "10t車(大型)", 4.0, 9500, 3),
]

COST_SETTINGS = [
    ("FUEL_PRICE_YEN_PER_L", "165", "軽油単価(円/L)。資源エネルギー庁の価格調査で更新すること"),
    ("LABOR_COST_YEN_PER_HOUR", "2400", "人件費単価(円/h)。賃金構造基本統計調査等で更新すること"),
    ("SAFETY_COST_RATE", "0.03", "安全確保費率(燃料費+人件費+減価償却費に対する割合)"),
    ("TARGET_PROFIT_RATE", "0.10", "適正原価に上乗せする目標利益率"),
    ("WARNING_THRESHOLD_RATE", "0.0", "この値未満(現在額が適正原価を下回る)でWARNING"),
    ("CRITICAL_THRESHOLD_RATE", "-0.10", "この値未満でCRITICAL(適正原価を10%以上下回る)"),
]

COURSES = [
    # course_name, vehicle_code, distance_km, binding_hours, toll_fee_yen, mode, current_rate_yen, memo
    ("羽田-厚木便(協力会社)", "4T", 45.0, 6.0, 1200, "payment", 18000,
     "協力会社への支払い運賃が適正原価に対して妥当か確認するサンプル"),
    ("関東近郊配送(自社2t便)", "2T", 25.0, 4.0, 0, "payment", 9000,
     "近距離・高速代なしのサンプル"),
    ("首都圏長距離便(荷主請求)", "10T", 180.0, 10.0, 4500, "negotiation", 48000,
     "荷主への請求額が適正原価に対して妥当か・値上げ余地があるか確認するサンプル"),
]


def seed(conn: sqlite3.Connection) -> None:
    if db.is_seeded(conn):
        return

    for code, name, fuel_eff, depreciation, order in VEHICLE_TYPES:
        conn.execute(
            """INSERT INTO vehicle_types
               (vehicle_code, vehicle_name, fuel_efficiency_km_per_l, daily_depreciation_yen, display_order)
               VALUES (?, ?, ?, ?, ?)""",
            (code, name, fuel_eff, depreciation, order),
        )

    for key, value, desc in COST_SETTINGS:
        conn.execute(
            "INSERT INTO cost_settings (setting_key, setting_value, description) VALUES (?, ?, ?)",
            (key, value, desc),
        )

    for course_name, vehicle_code, distance, hours, toll, mode, current_rate, memo in COURSES:
        conn.execute(
            """INSERT INTO courses
               (course_name, vehicle_code, distance_km, binding_hours, toll_fee_yen, mode, current_rate_yen, memo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_name, vehicle_code, distance, hours, toll, mode, current_rate, memo),
        )

    conn.commit()


def main() -> None:
    conn = db.get_connection()
    db.init_schema(conn)
    seed(conn)
    conn.close()
    print(f"seed完了: {db.DB_PATH}")


if __name__ == "__main__":
    main()
