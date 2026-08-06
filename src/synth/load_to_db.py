"""合成データをSQLiteへ投入する。

v0.1のデモコース(seed_data由来の3件)と共存させるため、合成コースのcourse_idには
`SYNTH_COURSE_ID_OFFSET` を加算する。これにより両者のIDが恒久的に衝突しない。
"""

import json
import sqlite3
from datetime import date

import pandas as pd

from src import db, seed_data
from src.common.config import SETTINGS, Settings
from src.synth import generate

SYNTH_COURSE_ID_OFFSET = 1000


def _clear_synth(conn: sqlite3.Connection) -> None:
    for table in ("trip_orders", "trips", "synth_course_truth", "cost_settings_snapshots",
                  "drivers", "vehicles", "customers"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM courses WHERE course_id >= ?", (SYNTH_COURSE_ID_OFFSET,))
    conn.commit()


def _insert_snapshots(conn: sqlite3.Connection, trips: pd.DataFrame, settings: Settings) -> dict:
    """月ごとに燃料単価のスナップショットを作り、month -> snapshot_id を返す。

    燃料単価を更新しても過去の計算を再現できるようにするための仕組み。
    """
    base = {row["setting_key"]: row["setting_value"]
            for row in conn.execute("SELECT setting_key, setting_value FROM cost_settings")}

    monthly = trips.assign(month=trips["trip_date"].str[:7]).groupby("month")["fuel_price_yen"].mean()

    mapping = {}
    for month, price in monthly.items():
        payload = dict(base)
        payload["FUEL_PRICE_YEN_PER_L"] = f"{price:.1f}"
        cur = conn.execute(
            "INSERT INTO cost_settings_snapshots (effective_from, settings_json, note) VALUES (?, ?, ?)",
            (f"{month}-01", json.dumps(payload, ensure_ascii=False), f"{month} の平均燃料単価を適用"),
        )
        mapping[month] = cur.lastrowid
    conn.commit()
    return mapping


def load(conn: sqlite3.Connection, frames: dict, settings: Settings = SETTINGS) -> dict:
    _clear_synth(conn)

    customers = frames["customers"]
    vehicles = frames["vehicles"]
    drivers = frames["drivers"]
    courses = frames["courses"]
    trips = frames["trips"].copy()

    conn.executemany(
        """INSERT INTO customers
           (customer_code, customer_name, industry, contract_start, last_rate_revision, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        customers[["customer_code", "customer_name", "industry",
                   "contract_start", "last_rate_revision", "is_active"]].itertuples(index=False, name=None),
    )

    conn.executemany(
        """INSERT INTO vehicles
           (vehicle_id, vehicle_code, acquisition_cost_yen, in_service_date, is_owned, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        vehicles[["vehicle_id", "vehicle_code", "acquisition_cost_yen",
                  "in_service_date", "is_owned", "is_active"]].itertuples(index=False, name=None),
    )

    conn.executemany(
        """INSERT INTO drivers
           (driver_id, employment_type, hourly_cost_yen, fuel_skill_factor, is_active)
           VALUES (?, ?, ?, ?, ?)""",
        drivers[["driver_id", "employment_type", "hourly_cost_yen",
                 "fuel_skill_factor", "is_active"]].itertuples(index=False, name=None),
    )

    # コース: 計画上の諸元を courses に入れる(実績ではない)
    median_rev = trips.groupby("course_id")["revenue_yen"].median()
    course_rows = []
    for row in courses.itertuples(index=False):
        standard_hours = (
            row.distance_km / settings.average_speed_kmh + row.handling_hours + row.base_waiting_hours
        )
        toll = int(row.has_toll * row.distance_km * settings.toll_yen_per_km)
        course_rows.append(
            (
                SYNTH_COURSE_ID_OFFSET + row.course_id,
                row.course_name,
                row.vehicle_code,
                float(row.distance_km),
                round(float(standard_hours), 2),
                toll,
                "negotiation",
                int(median_rev.get(row.course_id, 0)),
                "合成データ",
                1,
            )
        )
    conn.executemany(
        """INSERT INTO courses
           (course_id, course_name, vehicle_code, distance_km, binding_hours,
            toll_fee_yen, mode, current_rate_yen, memo, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        course_rows,
    )

    # ground truth(実データには存在しない表)
    conn.executemany(
        "INSERT INTO synth_course_truth (course_id, loss_pattern, severity, confounder_pattern) VALUES (?, ?, ?, ?)",
        [
            (SYNTH_COURSE_ID_OFFSET + r.course_id, r.loss_pattern, float(r.severity), r.confounder_pattern)
            for r in courses.itertuples(index=False)
        ],
    )
    conn.commit()

    snapshot_map = _insert_snapshots(conn, trips, settings)
    trips["month"] = trips["trip_date"].str[:7]
    trips["settings_snapshot_id"] = trips["month"].map(snapshot_map)
    trips["course_id"] = trips["course_id"] + SYNTH_COURSE_ID_OFFSET

    conn.executemany(
        """INSERT INTO trips
           (trip_id, trip_date, course_id, customer_code, vehicle_id, driver_id, revenue_yen,
            actual_distance_km, actual_binding_hours, actual_fuel_liters, actual_toll_yen,
            loaded_ratio, is_empty_run, settings_snapshot_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        trips[["trip_id", "trip_date", "course_id", "customer_code", "vehicle_id", "driver_id",
               "revenue_yen", "actual_distance_km", "actual_binding_hours",
               "actual_fuel_liters", "actual_toll_yen", "loaded_ratio", "is_empty_run",
               "settings_snapshot_id"]].itertuples(index=False, name=None),
    )
    conn.commit()

    # 案件(FTL請求単位、業者目線の実収益)。trip_id は generate.py で明示採番済みのため
    # そのままFKとして使える。
    trip_orders = frames["trip_orders"]
    conn.executemany(
        """INSERT INTO trip_orders (order_id, trip_id, customer_code, ftl_rate_yen, cargo_value_yen, requested_service)
           VALUES (?, ?, ?, ?, ?, ?)""",
        trip_orders[["order_id", "trip_id", "customer_code", "ftl_rate_yen", "cargo_value_yen",
                     "requested_service"]].itertuples(index=False, name=None),
    )
    conn.commit()

    return {
        "customers": len(customers), "vehicles": len(vehicles), "drivers": len(drivers),
        "courses": len(courses), "trips": len(trips), "snapshots": len(snapshot_map),
        "trip_orders": len(trip_orders),
    }


def main() -> None:
    frames = generate.generate_trips(start_date=date(2025, 1, 1))
    conn = db.get_connection()
    db.init_schema(conn)
    seed_data.seed(conn)  # vehicle_types / cost_settings / v0.1のデモコース
    counts = load(conn, frames)
    conn.close()

    print(f"投入完了: {db.DB_PATH}")
    for k, v in counts.items():
        print(f"  {k:<12} {v:,}")


if __name__ == "__main__":
    main()
