"""可視性ギャップの定量化。Lv1の情報だけでは主因を当てられないことを数値で示す。

## この module がやること

1. **Lv3視点**: 原価ドライバー(積載率・拘束時間・燃費・運賃水準)が見える前提で、
   `attribution.py` の要因分解により主因を推定する。
2. **Lv1視点**: 売上と輸送費総額しか見えない前提で主因を推定しようとする。
   粗利の大小しか分からないため、**どの要因かを識別する情報が原理的に存在しない**。
   ここでは「最も件数の多い赤字パターンを一律に当てはめる」という、情報が無いときに
   人が実際に取る最善の戦略(=多数派に賭ける)を模擬する。
3. 両者を ground truth と突き合わせ、的中率と混同行列を出す。

Lv1が当たらないのはロジックの出来が悪いからではなく、**入力に情報が無いから**である。
この非対称性こそが可視性の価値であり、この作品の中心的な主張になる。
"""

from dataclasses import dataclass

import pandas as pd

from src.common import config
from src.analysis import attribution


@dataclass
class VisibilityComparison:
    lv1_accuracy: float
    lv3_accuracy: float
    lv1_predictions: pd.DataFrame
    lv3_predictions: pd.DataFrame
    confusion_lv3: pd.DataFrame
    misdirected_courses: pd.DataFrame
    n_courses: int

    @property
    def accuracy_gain(self) -> float:
        return self.lv3_accuracy - self.lv1_accuracy


def estimate_lv1(features: pd.DataFrame, gross_profit: pd.Series) -> pd.DataFrame:
    """Lv1(売上−輸送費総額のみ)での主因推定を模擬する。

    粗利が目標を下回っていることは分かるが、その理由を切り分ける手掛かりが無い。
    情報が無いとき人が実際に取る戦略 —「一番ありがちな原因(=運賃が安い)だと仮定して
    値上げ交渉に動く」— を模擬する。運賃交渉は最も着手しやすいため現場で選ばれやすい。
    """
    threshold = gross_profit.median()
    records = []
    for course_id, gp in gross_profit.items():
        pattern = config.PATTERN_HEALTHY if gp >= threshold else config.PATTERN_LOW_RATE
        action, counterpart = config.PATTERN_ACTIONS[pattern]
        records.append(
            {
                "course_id": course_id,
                "estimated_pattern": pattern,
                "gross_profit_yen": gp,
                "action": action,
                "counterpart": counterpart,
            }
        )
    return pd.DataFrame(records)


def _accuracy(pred: pd.DataFrame, truth: pd.DataFrame) -> float:
    merged = pred.merge(truth, on="course_id")
    if merged.empty:
        return 0.0
    return float((merged["estimated_pattern"] == merged["loss_pattern"]).mean())


def compare(
    trips: pd.DataFrame,
    courses: pd.DataFrame,
    truth: pd.DataFrame,
    labor_cost_per_hour: float,
    depreciation_per_trip: float,
    healthy_threshold_ratio: float = 0.06,
) -> VisibilityComparison:
    """Lv1 と Lv3 の主因推定を突き合わせる。

    truth は course_id / loss_pattern を持つ DataFrame(synth_course_truth)。
    """
    features = attribution.build_course_features(trips, courses)
    bm = attribution.build_benchmarks(features, labor_cost_per_hour, depreciation_per_trip)

    lv3 = attribution.estimate_patterns(features, bm, healthy_threshold_ratio)

    # Lv1が見られるのは粗利だけ
    gp = (
        features.set_index("course_id")
        .apply(
            lambda r: r["revenue_yen"]
            - (r["fuel_liters"] * r["fuel_price_yen"]
               + r["binding_hours"] * labor_cost_per_hour
               + r["toll_yen"] + depreciation_per_trip),
            axis=1,
        )
    )
    lv1 = estimate_lv1(features, gp)

    lv3_acc = _accuracy(lv3, truth)
    lv1_acc = _accuracy(lv1, truth)

    merged3 = lv3.merge(truth, on="course_id")
    confusion = pd.crosstab(
        merged3["loss_pattern"], merged3["estimated_pattern"], dropna=False
    )

    # 誤選択コスト: Lv1判断で運賃交渉に動くが、実際の主因が別だったコース
    merged1 = lv1.merge(truth, on="course_id")
    misdirected = merged1[
        (merged1["estimated_pattern"] == config.PATTERN_LOW_RATE)
        & (merged1["loss_pattern"] != config.PATTERN_LOW_RATE)
        & (merged1["loss_pattern"] != config.PATTERN_HEALTHY)
    ].copy()
    misdirected["correct_action"] = misdirected["loss_pattern"].map(
        lambda p: config.PATTERN_ACTIONS[p][0]
    )
    misdirected["correct_counterpart"] = misdirected["loss_pattern"].map(
        lambda p: config.PATTERN_ACTIONS[p][1]
    )

    return VisibilityComparison(
        lv1_accuracy=lv1_acc,
        lv3_accuracy=lv3_acc,
        lv1_predictions=lv1,
        lv3_predictions=lv3,
        confusion_lv3=confusion,
        misdirected_courses=misdirected,
        n_courses=len(features),
    )
