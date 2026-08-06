"""L3(交渉アクション化、negotiation.py)のテスト。

既存の他テストファイルには一切手を入れない。実DBファイルには触れず、生成とインメモリDBのみ使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from src import db, seed_data
from src.analysis import attribution, customer_portfolio as cp, data_access as da, negotiation as neg
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


@pytest.fixture()
def negotiation_setup(loaded_conn):
    trips = da.load_trips(loaded_conn)
    orders = da.load_trip_orders(loaded_conn)
    courses = da.load_synth_courses(loaded_conn)
    customers = da.load_customers(loaded_conn)
    labor = da.load_setting(loaded_conn, "LABOR_COST_YEN_PER_HOUR") or 2400.0
    target_rate = da.load_setting(loaded_conn, "TARGET_PROFIT_RATE") or 0.10

    order_profit = cp.build_order_profit(trips, orders, labor, DEPRECIATION)
    portfolio = cp.build_customer_portfolio(order_profit, customers)
    as_of = trips["trip_date"].max()
    candidates = neg.build_candidates(portfolio, customers, target_rate, as_of)

    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor, DEPRECIATION)
    rate_sheet = neg.build_standard_rate_sheet(features, bm)

    return loaded_conn, candidates, rate_sheet, target_rate


# --------------------------------------------------------------------------
# 経過月数
# --------------------------------------------------------------------------

def test_months_between_known_dates():
    assert neg._months_between(pd.Timestamp("2024-04-01"), pd.Timestamp("2026-06-30")) == 26
    assert neg._months_between(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-15")) == 0
    # 未来日付を渡しても負にはならない
    assert neg._months_between(pd.Timestamp("2027-01-01"), pd.Timestamp("2026-01-01")) == 0


# --------------------------------------------------------------------------
# 交渉候補
# --------------------------------------------------------------------------

def test_candidates_sorted_by_priority_descending(negotiation_setup):
    _, candidates, _, _ = negotiation_setup
    scores = candidates["priority_score"].tolist()
    assert scores == sorted(scores, reverse=True)


def test_target_increase_is_never_negative(negotiation_setup):
    _, candidates, _, _ = negotiation_setup
    assert (candidates["target_increase_yen"] >= 0).all()


def test_target_increase_includes_time_based_component_even_without_shortfall(negotiation_setup):
    """不足額が0の顧客でも、経過月数がある限り要求増額は0より大きいはず。"""
    _, candidates, _, _ = negotiation_setup
    no_shortfall = candidates[
        (candidates["shortfall_yen"] == 0) & (candidates["months_since_revision"] > 0)
    ]
    if not no_shortfall.empty:
        assert (no_shortfall["target_increase_yen"] > 0).all()


# --------------------------------------------------------------------------
# 自社標準単価表
# --------------------------------------------------------------------------

def test_standard_rate_sheet_diff_is_consistent(negotiation_setup):
    _, _, rate_sheet, _ = negotiation_setup
    expected_diff = rate_sheet["current_rate_yen"] - rate_sheet["standard_rate_yen"]
    assert (rate_sheet["diff_yen"] == expected_diff).all()


# --------------------------------------------------------------------------
# 引き渡しテキスト
# --------------------------------------------------------------------------

def test_handoff_text_contains_customer_name(negotiation_setup):
    _, candidates, _, target_rate = negotiation_setup
    row = candidates.iloc[0].to_dict()
    text = neg.format_handoff_text(row, target_rate)
    assert row["customer_name"] in text
    assert "Freight_rate_hike_justification_template" in text


# --------------------------------------------------------------------------
# 交渉ステータスのCRUD
# --------------------------------------------------------------------------

def test_negotiation_crud_round_trip(negotiation_setup):
    conn, candidates, _, _ = negotiation_setup
    customer_code = candidates.iloc[0]["customer_code"]

    nid = neg.save_negotiation(
        conn, customer_code, neg.STATUS_NOT_STARTED, 100000, "2026-09-01", "初回起票"
    )
    loaded = neg.load_negotiations(conn)
    row = loaded[loaded["negotiation_id"] == nid].iloc[0]
    assert row["status"] == neg.STATUS_NOT_STARTED
    assert row["customer_code"] == customer_code

    neg.update_negotiation(
        conn, nid, neg.STATUS_AGREED, agreed_increase_yen=80000,
        next_review_date="2027-03-01", memo="合意した",
    )
    updated = neg.load_negotiations(conn)
    row2 = updated[updated["negotiation_id"] == nid].iloc[0]
    assert row2["status"] == neg.STATUS_AGREED
    assert row2["agreed_increase_yen"] == 80000

    conn.execute("DELETE FROM negotiations WHERE negotiation_id = ?", (nid,))
    conn.commit()
