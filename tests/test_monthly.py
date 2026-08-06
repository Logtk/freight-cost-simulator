"""L1(月次採算カルテ、monthly.py)のテスト。

既存の他テストファイルには一切手を入れない。実DBファイルには触れず、生成とインメモリDBのみ使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from src import db, seed_data
from src.analysis import data_access as da, monthly as mo
from src.common.config import SETTINGS
from src.synth import generate, load_to_db

SMALL = replace(SETTINGS, num_months=6)
DEPRECIATION = 5500.0


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
def enriched_and_courses(loaded_conn):
    trips = da.load_trips(loaded_conn)
    orders = da.load_trip_orders(loaded_conn)
    courses = da.load_synth_courses(loaded_conn)
    labor = da.load_setting(loaded_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    enriched = mo.enrich_trips(trips, orders, labor, DEPRECIATION)
    return enriched, courses


# --------------------------------------------------------------------------
# 営業日数
# --------------------------------------------------------------------------

@pytest.mark.parametrize("month,expected", [
    ("2025-02", 20),   # 2025-02-01は土曜、28日間=4週間ちょうど
    ("2025-01", 23),   # 31日、1日が水曜始まり
])
def test_business_days_in_month(month, expected):
    assert mo.business_days_in_month(month) == expected


# --------------------------------------------------------------------------
# 月次売上の一致
# --------------------------------------------------------------------------

def test_course_monthly_revenue_matches_trip_orders(enriched_and_courses):
    enriched, courses = enriched_and_courses
    month = sorted(enriched["month"].unique())[0]
    course_monthly = mo.build_course_monthly(enriched, courses, month)

    expected_total = enriched[enriched["month"] == month]["revenue_yen_actual"].sum()
    assert course_monthly["revenue_yen"].sum() == pytest.approx(expected_total)


def test_profit_trend_covers_all_months(enriched_and_courses):
    enriched, _ = enriched_and_courses
    trend = mo.build_profit_trend(enriched)
    assert set(trend["month"]) == set(enriched["month"].unique())


# --------------------------------------------------------------------------
# ドリルダウンの整合性
# --------------------------------------------------------------------------

def test_vehicle_breakdown_sums_to_course_total(enriched_and_courses):
    enriched, courses = enriched_and_courses
    month = sorted(enriched["month"].unique())[-1]
    course_monthly = mo.build_course_monthly(enriched, courses, month)
    course_monthly = course_monthly[course_monthly["trip_count"] > 0]
    assert not course_monthly.empty

    course_id = course_monthly.iloc[0]["course_id"]
    course_total = course_monthly.iloc[0]["profit_yen"]

    veh = mo.build_vehicle_breakdown(enriched, course_id, month)
    assert veh["profit_yen"].sum() == pytest.approx(course_total)


def test_driver_breakdown_sums_to_vehicle_total(enriched_and_courses):
    enriched, courses = enriched_and_courses
    month = sorted(enriched["month"].unique())[-1]
    course_monthly = mo.build_course_monthly(enriched, courses, month)
    course_monthly = course_monthly[course_monthly["trip_count"] > 0]
    course_id = course_monthly.iloc[0]["course_id"]

    veh = mo.build_vehicle_breakdown(enriched, course_id, month)
    vehicle_id = veh.iloc[0]["vehicle_id"]
    vehicle_total = veh.iloc[0]["profit_yen"]

    drv = mo.build_driver_breakdown(enriched, course_id, vehicle_id, month)
    assert drv["profit_yen"].sum() == pytest.approx(vehicle_total)


def test_trip_detail_sums_to_driver_total(enriched_and_courses):
    enriched, courses = enriched_and_courses
    month = sorted(enriched["month"].unique())[-1]
    course_monthly = mo.build_course_monthly(enriched, courses, month)
    course_monthly = course_monthly[course_monthly["trip_count"] > 0]
    course_id = course_monthly.iloc[0]["course_id"]
    veh = mo.build_vehicle_breakdown(enriched, course_id, month)
    vehicle_id = veh.iloc[0]["vehicle_id"]
    drv = mo.build_driver_breakdown(enriched, course_id, vehicle_id, month)
    driver_id = drv.iloc[0]["driver_id"]
    driver_total = drv.iloc[0]["profit_yen"]

    detail = mo.build_trip_detail(enriched, course_id, vehicle_id, driver_id, month)
    assert detail["profit_yen"].sum() == pytest.approx(driver_total)


# --------------------------------------------------------------------------
# 稼働率
# --------------------------------------------------------------------------

def test_utilization_rate_is_between_zero_and_one(enriched_and_courses):
    enriched, courses = enriched_and_courses
    for month in enriched["month"].unique():
        course_monthly = mo.build_course_monthly(enriched, courses, month)
        assert (course_monthly["utilization_rate"] >= 0).all()
        assert (course_monthly["utilization_rate"] <= 1).all()


# --------------------------------------------------------------------------
# ランキング
# --------------------------------------------------------------------------

def test_rank_vehicles_top_is_sorted_descending(enriched_and_courses):
    enriched, _ = enriched_and_courses
    month = sorted(enriched["month"].unique())[-1]
    rv = mo.rank_vehicles(enriched, month, top_n=5)
    top = rv["top"]["profit_yen"].tolist()
    assert top == sorted(top, reverse=True)


def test_rank_drivers_bottom_is_sorted_ascending(enriched_and_courses):
    enriched, _ = enriched_and_courses
    month = sorted(enriched["month"].unique())[-1]
    rd = mo.rank_drivers(enriched, month, top_n=5)
    bottom = rd["bottom"]["profit_yen"].tolist()
    assert bottom == sorted(bottom)
