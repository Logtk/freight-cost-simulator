"""L4(経営レポート、customer_portfolio.py)のテスト。

既存の他テストファイルには一切手を入れない。実DBファイルには触れず、生成とインメモリDBのみ使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from src import db, seed_data
from src.analysis import customer_portfolio as cp, data_access as da
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
def loaded_data(loaded_conn):
    trips = da.load_trips(loaded_conn)
    orders = da.load_trip_orders(loaded_conn)
    customers = da.load_customers(loaded_conn)
    labor = da.load_setting(loaded_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    order_profit = cp.build_order_profit(trips, orders, labor, DEPRECIATION)
    return trips, orders, customers, order_profit


# --------------------------------------------------------------------------
# 生成データにcargo_value_yenが乗っていること
# --------------------------------------------------------------------------

def test_orders_have_cargo_value(frames):
    orders = frames["trip_orders"]
    assert "cargo_value_yen" in orders.columns
    assert (orders["cargo_value_yen"] > 0).all()


def test_cargo_value_is_not_correlated_with_ftl_rate(frames):
    """運賃は距離・車格からしか決まらず、貨物価値を見ていないという設計意図の確認。"""
    orders = frames["trip_orders"]
    corr = orders["cargo_value_yen"].corr(orders["ftl_rate_yen"])
    assert abs(corr) < 0.3


# --------------------------------------------------------------------------
# 案件別粗利の配分
# --------------------------------------------------------------------------

def test_order_profit_revenue_matches_ftl_rate_total(loaded_data):
    trips, orders, customers, order_profit = loaded_data
    assert order_profit["ftl_rate_yen"].sum() == pytest.approx(orders["ftl_rate_yen"].sum())


def test_cost_allocation_sums_to_trip_cost(loaded_data):
    """1運行のコストを案件数で等分配しているので、案件のコスト合計はtrip単位のコストと一致する。"""
    trips, orders, customers, order_profit = loaded_data
    labor = 2400.0  # loaded_dataと同じ設定値を再現(da.load_settingのデフォルトと揃える)
    trip_cost = (
        trips["actual_fuel_liters"] * trips["fuel_price_yen"]
        + trips["actual_binding_hours"] * labor
        + trips["actual_toll_yen"]
        + DEPRECIATION
    )
    expected_total_cost = trip_cost.sum()
    assert order_profit["cost_per_order_yen"].sum() == pytest.approx(expected_total_cost, rel=1e-6)


# --------------------------------------------------------------------------
# 顧客ポートフォリオ
# --------------------------------------------------------------------------

def test_customer_portfolio_covers_all_customers(loaded_data):
    _, _, customers, order_profit = loaded_data
    portfolio = cp.build_customer_portfolio(order_profit, customers)
    assert set(portfolio["quadrant"]) <= {
        cp.QUADRANT_MAINTAIN, cp.QUADRANT_RAISE, cp.QUADRANT_GROW, cp.QUADRANT_EXIT,
    }
    assert len(portfolio) == portfolio["customer_code"].nunique()


def test_concentration_risk_is_sorted_by_revenue_descending(loaded_data):
    _, _, customers, order_profit = loaded_data
    portfolio = cp.build_customer_portfolio(order_profit, customers)
    conc = cp.build_concentration_risk(portfolio, top_n=3)
    revs = conc["revenue_yen"].tolist()
    assert revs == sorted(revs, reverse=True)
    # 累積構成比は単調増加
    shares = conc["cumulative_share"].tolist()
    assert shares == sorted(shares)


# --------------------------------------------------------------------------
# 部署P/Lサマリー
# --------------------------------------------------------------------------

def test_pl_summary_profit_equals_revenue_minus_cost(loaded_data):
    _, _, _, order_profit = loaded_data
    month = sorted(order_profit["month"].unique())[0]
    pl = cp.build_pl_summary(order_profit, month)
    assert pl["profit_yen"] == pytest.approx(pl["revenue_yen"] - pl["cost_yen"])


def test_pl_summary_has_no_yoy_when_data_spans_less_than_a_year(loaded_data):
    """SMALL設定は6ヶ月分しか無いので、どの月も前年同月データが存在しないはず。"""
    _, _, _, order_profit = loaded_data
    for month in order_profit["month"].unique():
        pl = cp.build_pl_summary(order_profit, month)
        assert pl["has_yoy"] is False
        assert pl["yoy_diff_yen"] is None


# --------------------------------------------------------------------------
# 貨物価値×運賃のギャップ
# --------------------------------------------------------------------------

def test_value_gap_ratio_is_positive(loaded_data):
    _, orders, customers, _ = loaded_data
    value_gap = cp.build_value_gap(orders, customers)
    assert (value_gap["rate_to_value_ratio"] > 0).all()


def test_value_gap_quadrants_are_valid(loaded_data):
    _, orders, customers, _ = loaded_data
    value_gap = cp.build_value_gap(orders, customers)
    valid = {
        cp.VALUE_GAP_BALANCED_HIGH, cp.VALUE_GAP_UNDERPRICED,
        cp.VALUE_GAP_OVERPRICED, cp.VALUE_GAP_BALANCED_LOW,
    }
    assert set(value_gap["quadrant"]) <= valid


def test_value_gap_summary_conflict_subset(loaded_data):
    _, orders, customers, _ = loaded_data
    value_gap = cp.build_value_gap(orders, customers)
    summary = cp.ValueGapSummary(value_gap)
    assert set(summary.conflict_customers["quadrant"].unique()) <= set(cp.VALUE_GAP_CONFLICT)
