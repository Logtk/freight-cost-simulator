"""交渉アクション化(L3)。分析を行動に変える出口。月次で開く2つ目の理由(交渉の進捗確認)。

## この画面が想定する現場での使い方

月次カルテ・経営レポートで低採算/依存度の高い顧客を確認した後、四半期に一度この画面を開き、
交渉候補の優先順位を確認 → ステータスを更新 → 次回見直し日を決める、というサイクルを回す。

## 交渉相手はコースではなく顧客(重要な設計判断)

1コースを複数顧客が積み合わせる実態(L1.5+参照)があるため、コース単位では交渉相手が
一意に決まらない。`negotiations`テーブルは`customer_code`をキーに持つ(L0で作成した当初の
`course_id`キー設計から、未使用のうちにL3実装時に変更した)。

## Freight_rate_hike_justification_templateとの関係

交渉資料そのものは同案件のテンプレートで作る。本モジュールは数値の供給に留め、
コピー&ペーストしやすいテキストブロックを生成するところまでを担当する
(ファイルへの直接書き込みは行わない。プロジェクト間の越権を避けるため)。
"""

from datetime import date

import pandas as pd

from src.analysis import attribution

STATUS_NOT_STARTED = "未着手"
STATUS_IN_PROGRESS = "交渉中"
STATUS_AGREED = "合意"
STATUS_REJECTED = "決裂"
STATUS_ON_HOLD = "保留"

STATUSES = (STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_AGREED, STATUS_REJECTED, STATUS_ON_HOLD)


def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def build_candidates(
    customer_portfolio: pd.DataFrame,
    customers: pd.DataFrame,
    target_profit_rate: float,
    as_of_date,
    annual_increase_rate: float = 0.02,
) -> pd.DataFrame:
    """顧客ごとに交渉候補の優先度を算出する。

    不足額(目標粗利率に対する不足)だけを優先度の軸にすると、全顧客が目標を上回っている
    期間には候補が1件も出なくなり機能しない(合成データでは実際にそうなることを確認した)。
    優先度は**売上規模 × 経過月数**(大きく・長く据え置かれている顧客ほど優先)を主軸にし、
    不足額があれば加点する方式にした。要求増額も、不足額だけでなく「経過月数に応じた
    据え置き分の緩やかな改定」(年率`annual_increase_rate`を月数按分)を土台に加える。
    これにより粗利が健全な期間でも、据え置き期間が長い顧客には妥当な改定額が算出される。
    """
    df = customer_portfolio.merge(
        customers[["customer_code", "last_rate_revision"]], on="customer_code", how="left"
    )

    target_profit_yen = df["revenue_yen"] * target_profit_rate
    df["shortfall_yen"] = (target_profit_yen - df["profit_yen"]).clip(lower=0)

    as_of = pd.Timestamp(as_of_date)
    df["months_since_revision"] = df["last_rate_revision"].apply(
        lambda d: _months_between(pd.Timestamp(d), as_of) if pd.notna(d) else 0
    )

    df["time_based_increase_yen"] = (
        df["revenue_yen"] * annual_increase_rate * (df["months_since_revision"] / 12.0)
    )
    df["target_increase_yen"] = (df["shortfall_yen"] + df["time_based_increase_yen"]).round().astype(int)

    # 優先度: 売上規模×経過月数(常に差が付く主軸) + 不足額への大きめの加点(不足がある顧客を
    # 優先的に引き上げる)。スケールを揃えるため不足額側の係数を大きくしている。
    df["priority_score"] = df["revenue_yen"] * df["months_since_revision"] + df["shortfall_yen"] * 100.0

    return df.sort_values("priority_score", ascending=False).reset_index(drop=True)


def build_standard_rate_sheet(features: pd.DataFrame, bm: attribution.Benchmarks) -> pd.DataFrame:
    """自社標準単価表。コースごとに、諸元から構造的に期待される標準単価と現行運賃を並べる。"""
    rows = []
    for _, row in features.iterrows():
        exp = attribution.expected_values(row, bm)
        rows.append(
            {
                "course_id": row["course_id"],
                "vehicle_code": row["vehicle_code"],
                "distance_km": row["distance_km"],
                "standard_cost_yen": round(exp["cost_yen"]),
                "standard_rate_yen": round(exp["revenue_yen"]),
                "current_rate_yen": round(row["revenue_yen"]),
            }
        )
    df = pd.DataFrame(rows)
    df["diff_yen"] = df["current_rate_yen"] - df["standard_rate_yen"]
    return df


def format_handoff_text(candidate: dict, target_profit_rate: float) -> str:
    """Freight_rate_hike_justification_templateへコピー&ペーストする用のテキストブロック。"""
    revision = candidate.get("last_rate_revision") or "記録なし"
    return (
        f"■ 運賃改定交渉 数値メモ({candidate['customer_name']})\n\n"
        f"- 対象顧客: {candidate['customer_name']}({candidate.get('industry', '-')})\n"
        f"- 現在の売上規模: {candidate['revenue_yen']:,.0f}円(全期間累計)\n"
        f"- 現在の粗利率: {candidate['profit_rate']:.1%}"
        f"(目標{target_profit_rate:.0%}に対し{candidate['shortfall_yen']:,.0f}円不足)\n"
        f"- 前回運賃改定: {revision}({candidate['months_since_revision']}ヶ月経過)\n"
        f"- 要求増額目安: {candidate['target_increase_yen']:,.0f}円\n\n"
        "※ここまでの数値は合成データによる試算。詳細な交渉資料は"
        "Freight_rate_hike_justification_templateのテンプレートを使って作成する。"
    )


def save_negotiation(
    conn, customer_code: str, status: str, target_increase_yen: int,
    next_review_date: str, memo: str, opened_at: str = None,
) -> int:
    opened_at = opened_at or date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO negotiations
           (customer_code, opened_at, status, target_increase_yen, next_review_date, memo)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (customer_code, opened_at, status, target_increase_yen, next_review_date, memo),
    )
    conn.commit()
    return cur.lastrowid


def update_negotiation(
    conn, negotiation_id: int, status: str, agreed_increase_yen: int = None,
    next_review_date: str = None, memo: str = None,
) -> None:
    conn.execute(
        """UPDATE negotiations
           SET status = ?, agreed_increase_yen = ?, next_review_date = ?, memo = ?
           WHERE negotiation_id = ?""",
        (status, agreed_increase_yen, next_review_date, memo, negotiation_id),
    )
    conn.commit()


def load_negotiations(conn) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT n.*, c.customer_name
           FROM negotiations n
           LEFT JOIN customers c ON c.customer_code = n.customer_code
           ORDER BY n.opened_at DESC""",
        conn,
    )
