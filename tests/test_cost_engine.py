"""src/cost_engine.py の回帰テスト。実DBファイルには一切触れず、インメモリSQLiteのみを使う。"""

import sqlite3
from dataclasses import asdict

import pytest

from src import cost_engine as ce
from src import db
from src import seed_data


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    db.init_schema(connection)
    seed_data.seed(connection)
    yield connection
    connection.close()


def test_payment_mode_matches_hand_calculation(conn):
    """4t車・45km・拘束6h・高速代1200円・現在支払額18000円のケース(ブラウザ確認済みの値と一致させる)。"""
    cost_input = ce.CostInput(
        course_name="検証コース",
        vehicle_code="4T",
        distance_km=45.0,
        binding_hours=6.0,
        toll_fee_yen=1200,
        mode=ce.MODE_PAYMENT,
        current_rate_yen=18000,
    )
    result = ce.calculate_cost(conn, cost_input)

    assert result.fuel_cost_yen == 1142
    assert result.labor_cost_yen == 14400
    assert result.depreciation_cost_yen == 5500
    assert result.safety_cost_yen == 631
    assert result.breakeven_rate_yen == 2342
    assert result.appropriate_cost_yen == 25161
    assert result.diff_vs_current_yen == -7161
    assert result.alert_level == "CRITICAL"
    assert result.warnings == []


def test_mode_does_not_change_the_math(conn):
    """支払い側/交渉側は「現在額」の解釈が違うだけで、計算式自体は完全に同一であるべき(回帰チェック)。"""
    common = dict(
        course_name="共通コース",
        vehicle_code="10T",
        distance_km=180.0,
        binding_hours=10.0,
        toll_fee_yen=4500,
        current_rate_yen=48000,
    )
    payment_result = ce.calculate_cost(conn, ce.CostInput(mode=ce.MODE_PAYMENT, **common))
    negotiation_result = ce.calculate_cost(conn, ce.CostInput(mode=ce.MODE_NEGOTIATION, **common))

    assert asdict(payment_result) == asdict(negotiation_result)


def test_zero_distance_and_hours_do_not_crash(conn):
    cost_input = ce.CostInput(
        course_name="極端値コース",
        vehicle_code="2T",
        distance_km=0.0,
        binding_hours=0.0,
        toll_fee_yen=0,
        mode=ce.MODE_NEGOTIATION,
        current_rate_yen=0,
    )
    result = ce.calculate_cost(conn, cost_input)

    assert len(result.warnings) == 2
    assert result.fuel_cost_yen == 0
    assert result.labor_cost_yen == 0
    assert result.breakeven_rate_yen == 0
    # 減価償却費(2T=3000円)のみがベースになり、安全確保費・目標利益が乗る
    assert result.appropriate_cost_yen == 3399
    assert result.alert_level == "CRITICAL"


def test_invalid_mode_raises(conn):
    cost_input = ce.CostInput(
        course_name="不正モード", vehicle_code="2T", distance_km=10.0, binding_hours=1.0,
        toll_fee_yen=0, mode="bogus", current_rate_yen=1000,
    )
    with pytest.raises(ce.CostEngineError):
        ce.calculate_cost(conn, cost_input)


def test_unknown_vehicle_raises(conn):
    cost_input = ce.CostInput(
        course_name="未登録車種", vehicle_code="XX", distance_km=10.0, binding_hours=1.0,
        toll_fee_yen=0, mode=ce.MODE_PAYMENT, current_rate_yen=1000,
    )
    with pytest.raises(ce.MasterNotFoundError):
        ce.calculate_cost(conn, cost_input)


def test_missing_cost_setting_raises(conn):
    conn.execute("DELETE FROM cost_settings WHERE setting_key = 'FUEL_PRICE_YEN_PER_L'")
    cost_input = ce.CostInput(
        course_name="設定欠落", vehicle_code="2T", distance_km=10.0, binding_hours=1.0,
        toll_fee_yen=0, mode=ce.MODE_PAYMENT, current_rate_yen=1000,
    )
    with pytest.raises(ce.MasterNotFoundError):
        ce.calculate_cost(conn, cost_input)


def test_seed_is_idempotent(conn):
    # フィクスチャで既に1回seed済み。もう1回呼んでも重複投入されないこと。
    seed_data.seed(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM vehicle_types").fetchone()["n"]
    assert count == 3
