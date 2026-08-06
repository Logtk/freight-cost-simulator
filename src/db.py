"""SQLiteスキーマ定義と接続ユーティリティ。適正原価シミュレーターのマスタ・履歴を管理する。"""

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "freight_cost.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_types (
    vehicle_code TEXT PRIMARY KEY,
    vehicle_name TEXT NOT NULL,
    fuel_efficiency_km_per_l REAL NOT NULL,
    daily_depreciation_yen INTEGER NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cost_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    course_id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    vehicle_code TEXT NOT NULL REFERENCES vehicle_types(vehicle_code),
    distance_km REAL NOT NULL,
    binding_hours REAL NOT NULL,
    toll_fee_yen INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL,
    current_rate_yen INTEGER NOT NULL,
    memo TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calculations (
    calc_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    course_id INTEGER REFERENCES courses(course_id),
    course_name TEXT NOT NULL,
    vehicle_code TEXT NOT NULL,
    mode TEXT NOT NULL,
    distance_km REAL NOT NULL,
    binding_hours REAL NOT NULL,
    toll_fee_yen INTEGER NOT NULL,
    current_rate_yen INTEGER NOT NULL,
    fuel_cost_yen INTEGER NOT NULL,
    labor_cost_yen INTEGER NOT NULL,
    depreciation_cost_yen INTEGER NOT NULL,
    safety_cost_yen INTEGER NOT NULL,
    breakeven_rate_yen INTEGER NOT NULL,
    appropriate_cost_yen INTEGER NOT NULL,
    diff_vs_current_yen INTEGER NOT NULL,
    alert_level TEXT NOT NULL
);

-- ============================================================================
-- v1.0(運送採算カルテ)で追加。上記4テーブルは変更しない。
-- ============================================================================

-- 顧客マスタ(売上の帰属先)。Sales_quote_optimとは結合しないため自前で持つ。
CREATE TABLE IF NOT EXISTS customers (
    customer_code TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    industry TEXT,
    contract_start TEXT,
    last_rate_revision TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 車両インスタンス(車番単位)。vehicle_typesは車格クラスであり別物。
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,
    vehicle_code TEXT NOT NULL REFERENCES vehicle_types(vehicle_code),
    acquisition_cost_yen INTEGER,
    in_service_date TEXT,
    is_owned INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS drivers (
    driver_id TEXT PRIMARY KEY,
    employment_type TEXT NOT NULL,
    hourly_cost_yen INTEGER NOT NULL,
    fuel_skill_factor REAL NOT NULL DEFAULT 1.0,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 設定値スナップショット。燃料単価等を更新しても過去の計算を再現できるようにする。
CREATE TABLE IF NOT EXISTS cost_settings_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    effective_from TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    note TEXT
);

-- 運行実績ファクト。1行=1運行。本設計の中心。
CREATE TABLE IF NOT EXISTS trips (
    trip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_date TEXT NOT NULL,
    course_id INTEGER NOT NULL REFERENCES courses(course_id),
    customer_code TEXT NOT NULL REFERENCES customers(customer_code),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    driver_id TEXT NOT NULL REFERENCES drivers(driver_id),
    revenue_yen INTEGER NOT NULL,
    actual_distance_km REAL NOT NULL,
    actual_binding_hours REAL NOT NULL,
    actual_fuel_liters REAL,
    actual_toll_yen INTEGER NOT NULL DEFAULT 0,
    loaded_ratio REAL NOT NULL DEFAULT 1.0,
    is_empty_run INTEGER NOT NULL DEFAULT 0,
    settings_snapshot_id INTEGER REFERENCES cost_settings_snapshots(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_trips_date ON trips(trip_date);
CREATE INDEX IF NOT EXISTS idx_trips_course ON trips(course_id);

-- 合成データのground truth。実データには一切存在しない。
-- 切り分けロジックはこの表を参照せずに主因を推定し、事後にここと突き合わせて的中率を測る。
CREATE TABLE IF NOT EXISTS synth_course_truth (
    course_id INTEGER PRIMARY KEY REFERENCES courses(course_id),
    loss_pattern TEXT NOT NULL,
    severity REAL NOT NULL,
    confounder_pattern TEXT
);

-- 案件(FTL請求単位、業者目線)。1運行(trips)に対し複数の案件が積み合わされる。
-- trips.revenue_yen(荷主目線・既存)とは独立に持つ。積み合わせ相手が何件いようと
-- 各案件はそれぞれFTL相当額で請求される、という実態を反映する。
CREATE TABLE IF NOT EXISTS trip_orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL REFERENCES trips(trip_id),
    customer_code TEXT NOT NULL REFERENCES customers(customer_code),
    ftl_rate_yen INTEGER NOT NULL,
    cargo_value_yen INTEGER,
    requested_service TEXT
);
CREATE INDEX IF NOT EXISTS idx_trip_orders_trip ON trip_orders(trip_id);

-- 交渉ステータス(L3で使用)。顧客単位で持つ: 1コースを複数顧客が積み合わせる実態
-- (L1.5+参照)があるため、コース単位では交渉相手が一意に決まらない。
CREATE TABLE IF NOT EXISTS negotiations (
    negotiation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code TEXT NOT NULL REFERENCES customers(customer_code),
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL,
    target_increase_yen INTEGER,
    agreed_increase_yen INTEGER,
    next_review_date TEXT,
    memo TEXT
);
"""


def get_connection(check_same_thread: bool = True) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def is_seeded(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM vehicle_types").fetchone()
    return row["n"] > 0
