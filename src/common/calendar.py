"""営業日カレンダーの生成。

祝日カレンダーは持たない(土日のみを非稼働とする簡易モデル)。合成データの目的は
採算構造の再現であり、祝日の厳密さは分析結果に影響しないため。
"""

from datetime import date

import pandas as pd

from src.common.config import SETTINGS, Settings


def build_date_range(start_date: date, num_months: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(start_date)
    end = start + pd.DateOffset(months=num_months) - pd.Timedelta(days=1)
    return pd.date_range(start, end, freq="D")


def _month_boundary_flags(dates: pd.DatetimeIndex, n_days: int) -> tuple:
    """月内の営業日のうち、先頭n日を月初・末尾n日を月末としてフラグ化する。"""
    df = pd.DataFrame({"date": dates})
    df["is_business_day"] = df["date"].dt.dayofweek < 5
    df["month"] = df["date"].dt.to_period("M")

    is_start = pd.Series(False, index=df.index)
    is_end = pd.Series(False, index=df.index)

    for _, group in df.groupby("month", sort=False):
        business = group[group["is_business_day"]]
        if business.empty:
            continue
        is_start.loc[business.index[:n_days]] = True
        is_end.loc[business.index[-n_days:]] = True

    return is_start, is_end


def generate_calendar(start_date: date, num_months: int, settings: Settings = SETTINGS) -> pd.DataFrame:
    """date / dow / is_business_day / is_month_start / is_month_end / month を返す。"""
    dates = build_date_range(start_date, num_months)
    is_start, is_end = _month_boundary_flags(dates, settings.month_boundary_days)

    return pd.DataFrame(
        {
            "date": dates,
            "dow": dates.dayofweek,
            "is_business_day": dates.dayofweek < 5,
            "is_month_start": is_start.to_numpy(),
            "is_month_end": is_end.to_numpy(),
            "month": dates.to_period("M").astype(str),
        }
    )
