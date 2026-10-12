"""
Data parsing and cleaning pipeline for the PDCA application.

Raw production data from the injection-moulding line arrives with mixed column
types, inconsistent date formats, and extreme cycle values caused by maintenance
events, idle periods, and sensor anomalies. This module standardises the
DataFrame before any analysis takes place.

Pipeline (executed in order by clean_data):
    1. Standardise column names — strip leading/trailing whitespace.
    2. Parse datetime columns — identify by name pattern; null out-of-range values.
    3. Compute derived metrics — DemouldDuration and CureDuration from timestamps.

After the pipeline, the caller applies an outlier cleaning strategy via
apply_cleaning_method before slicing the data into comparison groups.

Outlier cleaning rationale
--------------------------
Injection-moulding cycle data contains a small proportion of extreme cycles that
do not represent normal production: weekend idle periods where the machine sat
warm but not running, maintenance windows, and failure-recovery cycles after a
rope rip. These cycles inflate period averages and mask genuine process changes.

The default strategy ('per_product_top5') removes the top 5 % of cycle-time
values within each tool number. This threshold has been chosen pragmatically —
it reliably captures the artefact cycles while preserving normal production
variation. A stricter threshold (1 %) risks retaining some artefacts; a looser
one (10 %) begins to remove real production data.
"""

import pandas as pd
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Column name standardisation
# ---------------------------------------------------------------------------

def standardize_column_names(df):
    """
    Remove leading/trailing whitespace from all column names.

    Some Excel exports include invisible spaces in header cells. This step
    ensures column lookups by name work reliably throughout the application.

    Args:
        df: Raw pandas DataFrame.

    Returns:
        DataFrame with cleaned column names.
    """
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    return df


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

def try_parse_datetime_columns(df):
    """
    Attempt to parse columns whose names suggest they contain calendar dates.

    Only columns with 'date' or 'datetime' in their name (case-insensitive) are
    considered. This deliberately excludes process-timing columns such as
    OpDemouldTimestamp, which store durations as integers, not calendar times.

    Args:
        df: DataFrame with standardised column names.

    Returns:
        Tuple (df, parsed_columns) where parsed_columns is a list of the column
        names that were successfully converted to datetime dtype.
    """
    df = df.copy()
    parsed_columns = []
    datetime_keywords = ['datetime', 'date']

    for col in df.columns:
        if any(kw in col.lower() for kw in datetime_keywords):
            converted = pd.to_datetime(df[col], errors='coerce')
            if converted.notna().sum() > 0:
                df[col] = converted
                parsed_columns.append(col)

    return df, parsed_columns


def validate_datetime_columns(df, parsed_columns, min_date='2020-01-01', max_date=None):
    """
    Replace out-of-range datetime values with NaT.

    Dates before 2020 or more than 30 days in the future are almost certainly
    parsing artefacts (e.g. Excel serial numbers misinterpreted as dates) and
    would cause incorrect date-range filtering if left in place.

    Args:
        df: DataFrame with parsed datetime columns.
        parsed_columns: List of column names to validate.
        min_date: Earliest acceptable date string (default '2020-01-01').
        max_date: Latest acceptable date. Defaults to today + 30 days.

    Returns:
        Tuple (df, invalid_date_summary) where invalid_date_summary is a dict
        mapping column name → count of nulled values.
    """
    df = df.copy()

    if max_date is None:
        max_date = datetime.today() + timedelta(days=30)

    min_date = pd.to_datetime(min_date)
    max_date = pd.to_datetime(max_date)
    invalid_date_summary = {}

    for col in parsed_columns:
        invalid_mask = (df[col] < min_date) | (df[col] > max_date)
        invalid_date_summary[col] = int(invalid_mask.sum())
        df.loc[invalid_mask, col] = pd.NaT

    return df, invalid_date_summary


# ---------------------------------------------------------------------------
# Summary statistics (used for the upload confirmation screen)
# ---------------------------------------------------------------------------

def get_cleaning_summary(df):
    """
    Return basic quality metrics for the upload confirmation message.

    Args:
        df: Cleaned DataFrame.

    Returns:
        Dict with keys: rows, columns, duplicates, missing_values_per_column.
    """
    return {
        'rows':                    df.shape[0],
        'columns':                 df.shape[1],
        'duplicates':              int(df.duplicated().sum()),
        'missing_values_per_column': df.isnull().sum(),
    }


# ---------------------------------------------------------------------------
# Derived metric computation
# ---------------------------------------------------------------------------

def add_derived_metrics(df):
    """
    Compute DemouldDuration and CureDuration from raw timestamp columns.

    The machine exports start/end timestamps as integers (Unix-style offsets).
    Subtraction gives a duration in the same units (seconds). Storing the result
    as a plain numeric column (not timedelta) keeps JSON serialisation simple.

    Metrics are only created if all required source columns exist, so the
    function is safe to call on any export regardless of schema.

    Args:
        df: DataFrame after clean_data has run.

    Returns:
        Tuple (df, created_metrics) where created_metrics lists the column names
        that were successfully added.
    """
    df = df.copy()
    created_metrics = []

    if 'EndDemouldTimestamp' in df.columns and 'OpDemouldTimestamp' in df.columns:
        df['DemouldDuration'] = (
            pd.to_numeric(df['EndDemouldTimestamp'], errors='coerce') -
            pd.to_numeric(df['OpDemouldTimestamp'],  errors='coerce')
        )
        created_metrics.append('DemouldDuration')

    if 'CureTimeTimestamp' in df.columns and 'InjTimeTimestamp' in df.columns:
        df['CureDuration'] = (
            pd.to_numeric(df['CureTimeTimestamp'], errors='coerce') -
            pd.to_numeric(df['InjTimeTimestamp'],  errors='coerce')
        )
        created_metrics.append('CureDuration')

    return df, created_metrics


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def clean_data(df):
    """
    Run all structural cleaning steps on a freshly loaded DataFrame.

    This is the single call made by the upload view after load_data returns.
    It does not apply outlier removal — that is deferred to apply_cleaning_method,
    which runs just before the comparison is computed so the analyst's choice of
    strategy is applied consistently.

    Args:
        df: Raw DataFrame from load_data.

    Returns:
        Tuple (df, parsed_columns, invalid_date_summary, summary).
    """
    df = standardize_column_names(df)
    df, parsed_columns = try_parse_datetime_columns(df)
    df, invalid_date_summary = validate_datetime_columns(df, parsed_columns)
    summary = get_cleaning_summary(df)
    return df, parsed_columns, invalid_date_summary, summary


# ---------------------------------------------------------------------------
# Date-range filtering
# ---------------------------------------------------------------------------

def filter_by_date_range(df, date_column, start_date, end_date):
    """
    Return rows where the datetime column falls within [start_date, end_date].

    Both bounds are inclusive. The comparison is made on the .date() component
    so time-of-day does not affect which rows are included.

    Args:
        df: DataFrame with a parsed datetime column.
        date_column: Name of the column to filter on.
        start_date: datetime.date lower bound (inclusive).
        end_date: datetime.date upper bound (inclusive).

    Returns:
        Filtered DataFrame. Returns the original df unchanged if date_column
        is not present.
    """
    if date_column not in df.columns:
        return df

    return df[
        (df[date_column].dt.date >= start_date) &
        (df[date_column].dt.date <= end_date)
    ].copy()


# ---------------------------------------------------------------------------
# Outlier removal strategies
# ---------------------------------------------------------------------------

def apply_cleaning_method(df, method, metric_columns):
    """
    Remove extreme cycle-time values using the selected strategy.

    This is called immediately before comparison so the same cleaned dataset
    is used for period averages, shift detection, and chart generation.

    Strategies:
        'none'             — no removal; use all cycles as-is
        'global_top5'      — remove cycles above the 95th percentile across
                             all tools and orders combined
        'global_top1'      — same but at the 99th percentile (less aggressive)
        'per_product_top5' — remove cycles above the 95th percentile within
                             each tool (JevArtNr group); recommended default
                             because it accounts for natural speed differences
                             between different tooling
        'per_order_top5'   — remove cycles above the 95th percentile within
                             each order run (JevAoNr group)
        'custom_<N>'       — remove cycles where the metric exceeds N seconds;
                             N is an analyst-supplied threshold

    Args:
        df: DataFrame to clean (already date-filtered and tool-filtered).
        method: Strategy key string.
        metric_columns: List of metric column names to apply the threshold to.

    Returns:
        Cleaned DataFrame copy.
    """
    df = df.copy()

    if method == 'none':
        return df

    if method == 'global_top5':
        for col in metric_columns:
            if col in df.columns:
                threshold = df[col].quantile(0.95)
                df = df[df[col] <= threshold]

    elif method == 'global_top1':
        for col in metric_columns:
            if col in df.columns:
                threshold = df[col].quantile(0.99)
                df = df[df[col] <= threshold]

    elif method == 'per_product_top5':
        for col in metric_columns:
            if col in df.columns:
                df = df[
                    df[col] <= df.groupby('JevArtNr')[col].transform(
                        lambda x: x.quantile(0.95)
                    )
                ]

    elif method == 'per_order_top5':
        for col in metric_columns:
            if col in df.columns:
                df = df[
                    df[col] <= df.groupby('JevAoNr')[col].transform(
                        lambda x: x.quantile(0.95)
                    )
                ]

    elif method.startswith('custom_'):
        rest = method[7:]  # everything after 'custom_'
        if ':' in rest:
            # Per-metric format: DemouldDuration:400,CureDuration:900
            for part in rest.split(','):
                if ':' in part:
                    col, thresh_str = part.split(':', 1)
                    col = col.strip()
                    try:
                        thresh = float(thresh_str)
                        if col in df.columns:
                            df = df[df[col] <= thresh]
                    except ValueError:
                        pass
        else:
            # Legacy single-threshold format: custom_400
            try:
                threshold = float(rest)
                for col in metric_columns:
                    if col in df.columns:
                        df = df[df[col] <= threshold]
            except (ValueError, IndexError):
                pass

    return df
