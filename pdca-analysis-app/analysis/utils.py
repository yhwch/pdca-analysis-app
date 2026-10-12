"""
General-purpose utility functions for the PDCA application.

These helpers are format/display utilities that do not depend on pandas or Django
and can be used in any layer of the application.
"""


def format_number(value, decimals=2):
    """
    Format a numeric value as a fixed-decimal string for display.

    Returns '-' for None or NaN values so the UI shows a clear placeholder
    rather than 'nan' or an empty cell.

    Args:
        value: Numeric value to format, or None.
        decimals: Number of decimal places (default 2).

    Returns:
        Formatted string, e.g. '4.73', or '-' if the value is missing.
    """
    if value is None:
        return '-'
    try:
        if value != value:  # NaN check: NaN is the only value not equal to itself
            return '-'
        return f'{value:.{decimals}f}'
    except Exception:
        return '-'
