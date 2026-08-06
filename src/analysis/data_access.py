"""DBから分析用のDataFrameを読み出す。UI層とロジック層の間の薄い接着剤。"""

import json
import sqlite3

import pandas as pd

from src.synth.load_to_db import SYNTH_COURSE_ID_OFFSET


def load_trips(conn: sqlite3.Connection) -> pd.DataFrame:
    """運行実績を読み、当時の設定スナップショットから燃料単価を復元する。

    燃料単価をスナップショットから引くことで、cost_settings を更新しても
    過去の運行の採算が変わらない(再現性が保たれる)。
    """
    df = pd.read_sql(
        """SELECT t.*, s.settings_json
           FROM trips t
           LEFT JOIN cost_settings_snapshots s ON s.snapshot_id = t.settings_snapshot_id""",
        conn,
    )
    if df.empty:
        return df

    df["fuel_price_yen"] = df["settings_json"].map(
        lambda s: float(json.loads(s)["FUEL_PRICE_YEN_PER_L"]) if s else None
    )
    df["month"] = df["trip_date"].str[:7]
    return df.drop(columns=["settings_json"])


def load_trip_orders(conn: sqlite3.Connection) -> pd.DataFrame:
    """案件(FTL請求単位、業者目線の実収益)を読む。trip_ordersはtripsの子表。"""
    return pd.read_sql("SELECT * FROM trip_orders", conn)


def load_customers(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM customers", conn)


def load_synth_courses(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM courses WHERE course_id >= ? ORDER BY course_id",
        conn,
        params=(SYNTH_COURSE_ID_OFFSET,),
    )


def load_truth(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT course_id, loss_pattern, severity FROM synth_course_truth", conn)


def load_setting(conn: sqlite3.Connection, key: str, cast=float):
    row = conn.execute(
        "SELECT setting_value FROM cost_settings WHERE setting_key = ?", (key,)
    ).fetchone()
    return cast(row["setting_value"]) if row else None


def has_synth_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()
    return row["n"] > 0
