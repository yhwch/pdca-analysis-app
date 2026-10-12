"""
Statistical metric calculations for the PDCA comparison engine.

The core analysis reduces each period to a set of average metric values, then
expresses the change between periods as both an absolute difference and a
percentage. Cycle-time metrics are expressed in seconds throughout.

Percentage change is always computed relative to the baseline group (Period A /
Tool A), so a negative value means Period B / Tool B is faster — consistent with
the lean manufacturing goal of minimising injection-moulding cycle time.

The rope-rip rate is a domain-specific quality indicator: it measures the fraction
of cycles where the rubber-rope feeding mechanism failed (Feeding_cnt == 1).
Unlike cycle-time metrics, this is not cleaned before calculation — every cycle
counts regardless of duration.
"""

import pandas as pd


def compare_two_periods(df_period_1, df_period_2, metric_columns, excluded_cycles=None):
    """
    Compute per-metric averages and percentage changes between two data groups.

    Groups can represent time periods (baseline vs post-change) or two different
    tools run over the same date range, depending on how the DataFrames were sliced
    before this call.

    Args:
        df_period_1: DataFrame for the baseline group (Period A or Tool A).
        df_period_2: DataFrame for the comparison group (Period B or Tool B).
        metric_columns: List of column names to compare (e.g. ['CureDuration']).
        excluded_cycles: Optional list of CycleCnt integers to remove before
            computing averages. Used when the analyst has manually flagged
            individual cycles via the scatter chart exclusion bar.

    Returns:
        DataFrame with one row per metric and columns:
            metric, period_1_avg, period_2_avg, difference, pct_change,
            improved (bool), verdict (str).
    """
    excluded_cycles = excluded_cycles or []

    # Apply manual cycle exclusions before computing averages
    if excluded_cycles and 'CycleCnt' in df_period_1.columns:
        df_period_1 = df_period_1[~df_period_1['CycleCnt'].isin(excluded_cycles)]
    if excluded_cycles and 'CycleCnt' in df_period_2.columns:
        df_period_2 = df_period_2[~df_period_2['CycleCnt'].isin(excluded_cycles)]

    results = []

    for col in metric_columns:
        period_1_mean = df_period_1[col].mean()
        period_2_mean = df_period_2[col].mean()
        difference = period_2_mean - period_1_mean

        # Percentage change relative to the baseline — undefined if baseline is zero or NaN
        if pd.notna(period_1_mean) and period_1_mean != 0:
            pct_change = (difference / period_1_mean) * 100
        else:
            pct_change = None

        if pct_change is not None:
            improved = pct_change < 0
            verdict = (
                f"Improved {abs(round(pct_change, 1))}%"
                if improved
                else f"Worsened {abs(round(pct_change, 1))}%"
            )
        else:
            improved = None
            verdict = "No data"

        results.append({
            'metric':      col,
            'period_1_avg': period_1_mean,
            'period_2_avg': period_2_mean,
            'difference':   difference,
            'pct_change':   pct_change,
            'improved':     bool(improved) if improved is not None else None,
            'verdict':      verdict,
        })

    return pd.DataFrame(results)


def calculate_rope_rip(df_period_1, df_period_2, excluded_cycles=None, keep_rope_rip=False):
    """
    Compute the rope-rip rate for both periods and the change between them.

    The rope-rip rate is the percentage of cycles where Feeding_cnt == 1,
    indicating that the rope-feeding mechanism failed during that cycle. A lower
    rate is always better.

    Returns None if the Feeding_cnt column is absent from either DataFrame, or
    if there are no valid (0 or 1) readings in either group.

    Args:
        df_period_1: Baseline group DataFrame.
        df_period_2: Comparison group DataFrame.
        excluded_cycles: CycleCnt values to exclude (unless keep_rope_rip is True).
        keep_rope_rip: When True, excluded cycles still count toward the rate.
            Use when the cycle duration was anomalous but the feed reading was valid.

    Returns:
        Dict with keys: p1_rate, p2_rate, change, p1_cycles, p2_cycles, improved.
        All rates are percentages rounded to one decimal place.
        Returns None if the column is missing or empty.
    """
    excluded_cycles = excluded_cycles or []

    # Honour manual exclusions unless the analyst chose to preserve rope-rip data
    if excluded_cycles and not keep_rope_rip and 'CycleCnt' in df_period_1.columns:
        df_period_1 = df_period_1[~df_period_1['CycleCnt'].isin(excluded_cycles)]
    if excluded_cycles and not keep_rope_rip and 'CycleCnt' in df_period_2.columns:
        df_period_2 = df_period_2[~df_period_2['CycleCnt'].isin(excluded_cycles)]

    if 'Feeding_cnt' not in df_period_1.columns or 'Feeding_cnt' not in df_period_2.columns:
        return None

    p1 = df_period_1['Feeding_cnt']
    p2 = df_period_2['Feeding_cnt']

    # Keep only valid binary readings; non-zero/one values are sensor artefacts
    p1_clean = p1[p1.isin([0, 1])]
    p2_clean = p2[p2.isin([0, 1])]

    if len(p1_clean) == 0 or len(p2_clean) == 0:
        return None

    p1_rate = (p1_clean.sum() / len(p1_clean)) * 100
    p2_rate = (p2_clean.sum() / len(p2_clean)) * 100
    change  = p2_rate - p1_rate

    return {
        'p1_rate':   round(p1_rate, 1),
        'p2_rate':   round(p2_rate, 1),
        'change':    round(change, 1),
        'p1_cycles': len(p1_clean),
        'p2_cycles': len(p2_clean),
        'improved':  bool(change < 0),
    }


def seconds_to_minsec(seconds):
    """
    Convert a duration in seconds to a human-readable string.

    Examples:
        45   → '45s'
        272  → '4m 32s'
        None → '-'

    Args:
        seconds: Numeric value in seconds, or None / NaN.

    Returns:
        Formatted string suitable for display in the UI.
    """
    if seconds is None or pd.isna(seconds):
        return '-'
    seconds = int(round(seconds))
    if seconds < 60:
        return f'{seconds}s'
    m = seconds // 60
    s = seconds % 60
    return f'{m}m {s:02d}s'
