"""合成データ生成器と可視性ギャップ分析のテスト。

中核の主張「Lv1では主因を当てられないが、Lv3なら当てられる」が成立し続けることを
テストで固定する。ロジックを改変して精度が落ちたら検知できるようにする。
実DBファイルには触れず、生成とインメモリDBのみを使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from src import db, seed_data
from src.analysis import attribution, data_access as da, visibility
from src.common import config
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


# --------------------------------------------------------------------------
# 生成器
# --------------------------------------------------------------------------

def test_generator_is_deterministic():
    a = generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)
    b = generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)
    for key in a:
        pd.testing.assert_frame_equal(a[key], b[key])


def test_different_seed_changes_output():
    other = replace(SMALL, random_seed=SMALL.random_seed + 1)
    a = generate.generate_trips(start_date=date(2025, 1, 1), settings=SMALL)
    b = generate.generate_trips(start_date=date(2025, 1, 1), settings=other)
    assert not a["trips"].equals(b["trips"])


def test_all_four_patterns_are_planted(frames):
    counts = frames["courses"]["loss_pattern"].value_counts()
    for pattern in config.LOSS_PATTERNS:
        assert counts.get(pattern, 0) > 0, f"{pattern} が1件も仕込まれていない"


def test_healthy_share_matches_setting(frames):
    courses = frames["courses"]
    healthy = (courses["loss_pattern"] == config.PATTERN_HEALTHY).sum()
    expected = round(len(courses) * SMALL.healthy_course_share)
    assert healthy == expected


def test_confounders_exist_but_are_not_universal(frames):
    """交絡が全く無いと切り分けが自明になり、全部に有ると主因が埋もれる。"""
    courses = frames["courses"]
    faulty = courses[courses["loss_pattern"] != config.PATTERN_HEALTHY]
    n_conf = faulty["confounder_pattern"].notna().sum()
    assert 0 < n_conf < len(faulty)


def test_confounder_never_equals_main_pattern(frames):
    courses = frames["courses"]
    both = courses[courses["confounder_pattern"].notna()]
    assert (both["loss_pattern"] != both["confounder_pattern"]).all()


def test_trips_have_no_impossible_values(frames):
    trips = frames["trips"]
    assert (trips["revenue_yen"] > 0).all()
    assert (trips["actual_distance_km"] > 0).all()
    assert (trips["actual_binding_hours"] > 0).all()
    assert (trips["actual_fuel_liters"] > 0).all()
    assert trips["loaded_ratio"].between(0, 1).all()


def test_planted_patterns_show_up_as_signal(frames):
    """仕込んだパターンが実際にデータへ現れているか。

    現れていなければ生成器のバグであり、下流の的中率も意味を持たない。
    """
    trips = frames["trips"].merge(
        frames["courses"][["course_id", "loss_pattern"]], on="course_id"
    )
    trips["binding_per_km"] = trips["actual_binding_hours"] / trips["actual_distance_km"]
    trips["fuel_per_km"] = trips["actual_fuel_liters"] / trips["actual_distance_km"]
    g = trips.groupby("loss_pattern")

    healthy = g.get_group(config.PATTERN_HEALTHY)
    assert g.get_group(config.PATTERN_LOW_LOADED)["loaded_ratio"].mean() < healthy["loaded_ratio"].mean()
    assert g.get_group(config.PATTERN_LONG_BINDING)["binding_per_km"].mean() > healthy["binding_per_km"].mean()
    assert g.get_group(config.PATTERN_POOR_FUEL)["fuel_per_km"].mean() > healthy["fuel_per_km"].mean()


def test_revenue_does_not_absorb_actual_cost_overruns(frames):
    """運賃は事前に合意されるので、実績の悪化で売上が連動して上がってはいけない。

    実コストから逆算していた初期実装では、荷待ちが伸びても売上が上がって粗利が痩せず、
    仕込んだ赤字パターンが粗利に現れないという誤りが起きた。その回帰テスト。
    """
    trips = frames["trips"].merge(
        frames["courses"][["course_id", "loss_pattern"]], on="course_id"
    )
    trips["cost"] = (
        trips["actual_fuel_liters"] * trips["fuel_price_yen"]
        + trips["actual_binding_hours"] * 2400
        + trips["actual_toll_yen"] + 5500
    )
    trips["gpr"] = (trips["revenue_yen"] - trips["cost"]) / trips["revenue_yen"]
    by_pattern = trips.groupby("loss_pattern")["gpr"].mean()

    healthy_gpr = by_pattern[config.PATTERN_HEALTHY]
    for pattern in (config.PATTERN_LOW_RATE, config.PATTERN_LOW_LOADED,
                    config.PATTERN_LONG_BINDING, config.PATTERN_POOR_FUEL):
        assert by_pattern[pattern] < healthy_gpr, f"{pattern} の粗利率が健全を下回っていない"


# --------------------------------------------------------------------------
# DB投入
# --------------------------------------------------------------------------

def test_load_populates_all_tables(loaded_conn, frames):
    for table, expected in [
        ("customers", len(frames["customers"])),
        ("vehicles", len(frames["vehicles"])),
        ("drivers", len(frames["drivers"])),
        ("trips", len(frames["trips"])),
        ("synth_course_truth", len(frames["courses"])),
    ]:
        n = loaded_conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        assert n == expected, f"{table} の件数が一致しない"


def test_synth_courses_do_not_collide_with_demo_courses(loaded_conn):
    """v0.1のデモコースと合成コースが共存できていること。"""
    demo = loaded_conn.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE course_id < ?",
        (load_to_db.SYNTH_COURSE_ID_OFFSET,),
    ).fetchone()["n"]
    assert demo == 3


def test_load_is_idempotent(loaded_conn, frames):
    before = loaded_conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
    load_to_db.load(loaded_conn, frames, settings=SMALL)
    after = loaded_conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
    assert before == after


def test_snapshot_preserves_fuel_price_after_settings_change(loaded_conn):
    """cost_settings を更新しても、過去の運行の燃料単価が変わらないこと。"""
    before = da.load_trips(loaded_conn)["fuel_price_yen"].tolist()
    loaded_conn.execute(
        "UPDATE cost_settings SET setting_value = '999' WHERE setting_key = 'FUEL_PRICE_YEN_PER_L'"
    )
    loaded_conn.commit()
    after = da.load_trips(loaded_conn)["fuel_price_yen"].tolist()
    assert before == after

    loaded_conn.execute(
        "UPDATE cost_settings SET setting_value = '165' WHERE setting_key = 'FUEL_PRICE_YEN_PER_L'"
    )
    loaded_conn.commit()


# --------------------------------------------------------------------------
# 要因分解と可視性ギャップ
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def comparison(loaded_conn):
    return visibility.compare(
        da.load_trips(loaded_conn),
        da.load_synth_courses(loaded_conn),
        da.load_truth(loaded_conn),
        labor_cost_per_hour=2400.0,
        depreciation_per_trip=5500.0,
    )


def test_attribution_never_reads_ground_truth():
    """attribution module が ground truth 表を参照していないこと(構造的な担保)。"""
    source = (attribution.__file__)
    with open(source, encoding="utf-8") as f:
        code = f.read()
    body = code.split('"""', 2)[-1]  # docstringを除いた本体
    assert "synth_course_truth" not in body
    assert "loss_pattern" not in body


def test_lv3_beats_lv1_substantially(comparison):
    """この作品の中心的な主張そのもの。成立しなくなったら検知する。"""
    assert comparison.lv3_accuracy > comparison.lv1_accuracy
    assert comparison.accuracy_gain >= 0.25, (
        f"可視性による改善が小さすぎる: Lv1 {comparison.lv1_accuracy:.1%} / "
        f"Lv3 {comparison.lv3_accuracy:.1%}"
    )


def test_lv3_accuracy_regression_floor(comparison):
    """切り分けロジックの精度が落ちたら落ちる回帰テスト。"""
    assert comparison.lv3_accuracy >= 0.75


def test_lv3_is_not_perfect(comparison):
    """交絡を入れている以上、満点にはならないはず。

    満点になったら交絡が効いておらず、問題が易しすぎることを意味する。
    """
    assert comparison.lv3_accuracy < 1.0


def test_every_prediction_has_an_action(comparison):
    preds = comparison.lv3_predictions
    assert preds["action"].notna().all()
    assert preds["counterpart"].notna().all()
    assert len(preds) == comparison.n_courses


def test_factor_contributions_are_additive(loaded_conn):
    """4要因の寄与合計が、期待粗利と実粗利の差におおむね一致すること。"""
    trips = da.load_trips(loaded_conn)
    courses = da.load_synth_courses(loaded_conn)
    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, 2400.0, 5500.0)

    for _, row in features.iterrows():
        contrib = attribution.factor_contributions(row, bm)
        exp = attribution.expected_values(row, bm)

        expected_profit = exp["revenue_yen"] - exp["cost_yen"]
        actual_profit = row["revenue_yen"] - (
            row["fuel_liters"] * bm.fuel_price
            + row["binding_hours"] * bm.labor_cost_per_hour
            + row["toll_yen"] + bm.depreciation_per_trip
        )
        gap = actual_profit - expected_profit
        assert sum(contrib.values()) == pytest.approx(gap, rel=0.02, abs=50)


def test_misdirected_courses_are_genuinely_misdirected(comparison):
    """誤選択リストに載るコースは、実際には運賃以外が主因であること。"""
    mis = comparison.misdirected_courses
    assert (mis["loss_pattern"] != config.PATTERN_LOW_RATE).all()
    assert (mis["loss_pattern"] != config.PATTERN_HEALTHY).all()
