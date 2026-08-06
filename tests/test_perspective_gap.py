"""荷主目線(積載率)×業者目線(集約数)ギャップ分析のテスト。

既存の tests/test_synth_and_visibility.py には一切手を入れない(別軸の新機能のため)。
実DBファイルには触れず、生成とインメモリDBのみを使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from src import db, seed_data
from src.analysis import data_access as da, perspective_gap as pg
from src.common.config import SETTINGS
from src.synth import generate, load_to_db

# 生成は重いのでモジュール内で使い回す
SMALL = replace(SETTINGS, num_months=6)


@pytest.fixture(scope="module")
def frames():
    return generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)


@pytest.fixture(scope="module")
def loaded_conn(frames):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    seed_data.seed(conn)
    load_to_db.load(conn, frames, settings=SMALL)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def course_gap(loaded_conn):
    trips = da.load_trips(loaded_conn)
    orders = da.load_trip_orders(loaded_conn)
    courses = da.load_synth_courses(loaded_conn)
    labor = da.load_setting(loaded_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    return pg.build_course_gap(
        trips, orders, courses, labor_cost_per_hour=labor, depreciation_per_trip=5500.0
    )


# --------------------------------------------------------------------------
# 案件生成
# --------------------------------------------------------------------------

def test_trip_orders_generation_is_deterministic():
    a = generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)
    b = generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)
    pd.testing.assert_frame_equal(a["trip_orders"], b["trip_orders"])


def test_every_trip_has_at_least_one_order(frames):
    trips = frames["trips"]
    orders = frames["trip_orders"]
    orders_per_trip = orders.groupby("trip_id").size()
    assert set(trips["trip_id"]) == set(orders_per_trip.index)
    assert (orders_per_trip >= 1).all()


# --------------------------------------------------------------------------
# 荷主目線(積載率) × 業者目線(集約数)の相関
# --------------------------------------------------------------------------

def test_loaded_ratio_and_consolidation_are_neither_locked_nor_independent(frames):
    """完全一致(相関1)でも完全無相関(相関0)でもないこと。

    完全一致だと2つの軸を分ける意味が無くなり、無相関だと「積載率が高いのに
    集約できていない」等の食い違いが構造的に生まれず、ギャップ分析が成立しない。
    """
    trips = frames["trips"]
    orders = frames["trip_orders"]
    orders_per_trip = orders.groupby("trip_id").size().rename("orders_per_trip")
    merged = trips.set_index("trip_id").join(orders_per_trip)

    course_level = merged.groupby("course_id").agg(
        loaded_ratio=("loaded_ratio", "mean"), orders_per_trip=("orders_per_trip", "mean")
    )
    corr = course_level["loaded_ratio"].corr(course_level["orders_per_trip"])
    assert 0.1 < corr < 0.85, f"相関が想定範囲外: {corr}"


# --------------------------------------------------------------------------
# 4象限の分類
# --------------------------------------------------------------------------

def test_all_four_quadrants_are_populated(course_gap):
    """食い違いケースが実際に発生していることの確認。0件だと機能として意味がない。"""
    counts = course_gap["quadrant"].value_counts()
    for q in (pg.QUADRANT_HIGH_HIGH, pg.QUADRANT_HIGH_LOW, pg.QUADRANT_LOW_HIGH, pg.QUADRANT_LOW_LOW):
        assert counts.get(q, 0) >= 1, f"{q} に分類されたコースが無い"


def test_quadrant_counts_sum_to_total_courses(course_gap):
    summary = pg.summarize(course_gap)
    assert int(summary.quadrant_counts.sum()) == len(course_gap)


def test_conflict_courses_is_subset_of_offdiagonal_quadrants(course_gap):
    summary = pg.summarize(course_gap)
    assert set(summary.conflict_courses["quadrant"].unique()) <= set(pg.CONFLICT_QUADRANTS)
