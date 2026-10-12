"""
Custom Django template filters for the PDCA application.

These filters translate internal key strings (cleaning method codes, metric
column names) into human-readable labels and tooltip text used throughout the
comparison and experiment detail templates.

Registered filters:
    cleaning_label   — converts a cleaning method key to a display label
    cleaning_tooltip — returns the tooltip explanation for a cleaning method
    metric_tooltip   — returns the tooltip explanation for a metric column
"""

from django import template

register = template.Library()


# Human-readable display names for each cleaning strategy
CLEANING_LABELS = {
    'none':             'No cleaning',
    'global_top1':      'Global top 1% removal',
    'global_top5':      'Global top 5% removal',
    'per_product_top5': 'Per tool top 5% removal',
    'per_order_top5':   'Per order top 5% removal',
}

# Tooltip text explaining each cleaning strategy to the analyst
CLEANING_TOOLTIPS = {
    'per_product_top5': (
        'Removes cycles where the metric exceeds the 95th percentile for that '
        'specific tool. Accounts for natural speed differences between tools.'
    ),
    'per_order_top5': (
        'Removes cycles exceeding the 95th percentile within each individual '
        'order run. Useful when order durations vary significantly.'
    ),
    'global_top5': (
        'Removes the 5 % slowest cycles across all tools and orders combined. '
        'Simpler than per-tool, but less precise for mixed datasets.'
    ),
    'global_top1': (
        'Removes only the 1 % most extreme cycles globally — retains more data '
        'than top 5 % while still catching sensor errors and long idle periods.'
    ),
    'none': '',
    'custom': (
        'Excludes all cycles where the selected metric exceeds your threshold '
        'value (in seconds). Set the threshold based on domain knowledge.'
    ),
}

# Tooltip text explaining each derived metric column
METRIC_TOOLTIPS = {
    'DemouldDuration': (
        'Seconds from mould-opening start to end of demoulding. '
        'Lower values indicate faster demoulding. '
        'Computed as EndDemouldTimestamp − OpDemouldTimestamp.'
    ),
    'CureDuration': (
        'Seconds the part spends curing inside the mould after injection. '
        'Consistency matters more than absolute direction here — a sudden '
        'change may indicate a process parameter shift. '
        'Computed as CureTimeTimestamp − InjTimeTimestamp.'
    ),
}


@register.filter
def cleaning_label(value):
    if not value:
        return value
    if value.startswith('custom_'):
        rest = value[7:]
        if ':' in rest:
            parts = []
            for part in rest.split(','):
                if ':' in part:
                    metric, thresh = part.split(':', 1)
                    parts.append(f'{metric.strip()} ≤ {thresh.strip()}s')
            return f'Custom ({", ".join(parts)})' if parts else 'Custom threshold'
        try:
            return f'Custom threshold ({rest}s)'
        except Exception:
            return 'Custom threshold'
    return CLEANING_LABELS.get(value, value)


@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter
def cleaning_tooltip(value):
    """
    Return the tooltip explanation string for a cleaning method key.

    Args:
        value: Cleaning method key string.

    Returns:
        Tooltip text, or empty string if none is defined.
    """
    return CLEANING_TOOLTIPS.get(value, '')


@register.filter
def metric_tooltip(value):
    """
    Return the tooltip explanation string for a derived metric column name.

    Args:
        value: Metric column name (e.g. 'CureDuration').

    Returns:
        Tooltip text, or empty string if none is defined.
    """
    return METRIC_TOOLTIPS.get(value, '')
