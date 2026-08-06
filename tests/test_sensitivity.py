"""L2感度分析(sensitivity.py)のテスト。

既存の他テストファイルには一切手を入れない。実DBファイルには触れず、生成とインメモリDBのみ使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from src import db, seed_data
from src.analysis import attribution, data_access as da, sensitivity as sn
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
def features_and_bm(loaded_conn):
    trips = da.load_trips(loaded_conn)
    courses = da.load_synth_courses(loaded_conn)
    labor = da.load_setting(loaded_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor, DEPRECIATION)
    return features, bm, courses


# --------------------------------------------------------------------------
# シナリオ適用
# --------------------------------------------------------------------------

def test_baseline_scenario_changes_nothing(features_and_bm):
    features, bm, _ = features_and_bm
    result = sn.evaluate(features, bm, sn.BASELINE)
    # ベースラインは実績そのものなので、原価+粗利=売上になっているはず
    total_revenue = result.course_table["scenario_revenue_yen"].sum()
    total_cost = result.course_table["scenario_cost_yen"].sum()
    assert result.total_profit_yen == pytest.approx(total_revenue - total_cost)


def test_higher_fuel_price_decreases_profit(features_and_bm):
    features, bm, _ = features_and_bm
    baseline = sn.evaluate(features, bm)
    up = sn.evaluate(features, bm, sn.Scenario(fuel_price_yen=20.0))
    assert up.total_profit_yen < baseline.total_profit_yen


def test_rate_hike_increases_profit(features_and_bm):
    features, bm, _ = features_and_bm
    baseline = sn.evaluate(features, bm)
    hike = sn.evaluate(features, bm, sn.Scenario(rate_hike_pct=0.05))
    assert hike.total_profit_yen > baseline.total_profit_yen


def test_loaded_ratio_improvement_increases_profit(features_and_bm):
    features, bm, _ = features_and_bm
    baseline = sn.evaluate(features, bm)
    improved = sn.evaluate(features, bm, sn.Scenario(loaded_ratio_delta=0.1))
    assert improved.total_profit_yen > baseline.total_profit_yen


# --------------------------------------------------------------------------
# 集中度サマリー
# --------------------------------------------------------------------------

def test_concentration_summary_reports_negative_impact(features_and_bm):
    features, bm, courses = features_and_bm
    baseline = sn.evaluate(features, bm)
    worse = sn.evaluate(features, bm, sn.Scenario(fuel_price_yen=30.0))
    summary = sn.concentration_summary(baseline, worse, courses, top_n=3)
    assert summary["total_impact_yen"] < 0
    assert 0.0 <= summary["concentration_ratio"] <= 1.0
    assert len(summary["top_courses"]) == 3


# --------------------------------------------------------------------------
# トルネードチャート
# --------------------------------------------------------------------------

def test_tornado_sweep_covers_all_four_levers(features_and_bm):
    features, bm, _ = features_and_bm
    tornado = sn.tornado_sweep(features, bm)
    assert set(tornado["lever"]) == {
        sn.LEVER_FUEL_PRICE, sn.LEVER_LABOR_COST, sn.LEVER_RATE_HIKE, sn.LEVER_LOADED_RATIO,
    }
    assert set(tornado["category"]) == {"foundation", "action"}
    # swing_yen で降順ソートされていること
    assert list(tornado["swing_yen"]) == sorted(tornado["swing_yen"], reverse=True)


def test_tornado_sweep_is_symmetric_around_base(features_and_bm):
    """上振れ・下振れの絶対値は、線形なコスト構造なのでほぼ一致するはず。"""
    features, bm, _ = features_and_bm
    tornado = sn.tornado_sweep(features, bm)
    for _, row in tornado.iterrows():
        assert abs(row["impact_up_yen"]) == pytest.approx(abs(row["impact_down_yen"]), rel=0.05)


# --------------------------------------------------------------------------
# 損益分岐点
# --------------------------------------------------------------------------

def test_variable_cost_breakeven_is_never_above_full_cost_breakeven(features_and_bm):
    features, bm, _ = features_and_bm
    for _, row in features.iterrows():
        be = sn.breakeven_rates(row, bm.fuel_price, bm.labor_cost_per_hour, DEPRECIATION)
        assert be["variable_cost_breakeven_yen"] <= be["full_cost_breakeven_yen"]


def test_breakeven_responds_to_fuel_price(features_and_bm):
    features, bm, _ = features_and_bm
    row = features.iloc[0]
    low = sn.breakeven_rates(row, fuel_price=100.0, labor_cost_per_hour=bm.labor_cost_per_hour,
                              depreciation_per_trip=DEPRECIATION)
    high = sn.breakeven_rates(row, fuel_price=300.0, labor_cost_per_hour=bm.labor_cost_per_hour,
                               depreciation_per_trip=DEPRECIATION)
    assert high["variable_cost_breakeven_yen"] > low["variable_cost_breakeven_yen"]
    assert high["full_cost_breakeven_yen"] > low["full_cost_breakeven_yen"]
