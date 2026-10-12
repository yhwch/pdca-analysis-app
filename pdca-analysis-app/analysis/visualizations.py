"""
Plotly chart generation functions for the PDCA application.

Each function returns a self-contained HTML fragment (Plotly figure serialised
with full_html=False) that is injected directly into a Django template or served
from an AJAX endpoint. Plotly.js is loaded once per page via CDN and reused by
all chart fragments, so include_plotlyjs='cdn' is only set for the initial page
render; AJAX responses use include_plotlyjs=False.

Visual design conventions
--------------------------
All charts use a dark colour palette:
  Background : transparent (rgba(0,0,0,0)) so the panel card colour shows through
  Grid lines : #1e2a3a
  Group A    : blue  #2e7ebb  (baseline — Period A or Tool A)
  Group B    : green #4caf7d  (comparison — Period B or Tool B)
  Out-of-ctrl: red   #e05c5c

Chart types available
---------------------
  plot_period_comparison  — grouped bar (or scatter/line) of per-metric averages
  plot_scatter_with_trend — individual cycle values over time with rolling mean
                            and outlier highlighting; primary exploratory view
  plot_line_with_slope    — rolling mean + OLS regression slope line per period;
                            shows whether a metric was trending within the period
  plot_violin             — mirrored kernel density + embedded box plot;
                            exposes the full distribution shape, not just the mean
  plot_control_chart      — SPC Shewhart chart; UCL/LCL are set from Period A
                            statistics (mean ± 3σ) and Period B points are judged
                            against that baseline process capability
"""

import plotly.graph_objects as go
import pandas as pd
import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
# Summary comparison chart (bar / scatter / line)
# ---------------------------------------------------------------------------

def plot_period_comparison(comparison_df, selected_art_nr=None, selected_ao_nr=None, cleaning_method=None, chart_type='bar', include_plotlyjs='cdn', period_a_label='Period A', period_b_label='Period B', return_fig=False):
    """
    Render a grouped chart comparing per-metric averages between two groups.

    Supports bar (default), scatter (dot plot), and line chart types. The
    period_a_label / period_b_label parameters allow the same function to be
    reused for both period comparisons ('Period A' / 'Period B') and tool
    comparisons ('Tool 240771' / 'Tool 240772').

    Args:
        comparison_df: DataFrame returned by compare_two_periods(), with columns
            metric, period_1_avg, period_2_avg.
        selected_art_nr: List of tool numbers included, used for the chart title.
        selected_ao_nr: List of order numbers included, used for the chart title.
        cleaning_method: Cleaning strategy key, shown in the chart title.
        chart_type: 'bar', 'scatter', or 'line'.
        include_plotlyjs: Passed to fig.to_html(); use 'cdn' for first render,
            False for AJAX fragments.
        period_a_label: Legend label for the baseline group.
        period_b_label: Legend label for the comparison group.

    Returns:
        HTML string containing the Plotly figure.
    """
    fig = go.Figure()

    if chart_type == 'bar':
        fig.add_trace(go.Bar(name=period_a_label, x=comparison_df['metric'], y=comparison_df['period_1_avg'], marker_color='#2e7ebb'))
        fig.add_trace(go.Bar(name=period_b_label, x=comparison_df['metric'], y=comparison_df['period_2_avg'], marker_color='#4caf7d'))

    elif chart_type == 'scatter':
        fig.add_trace(go.Scatter(name=period_a_label, x=comparison_df['metric'], y=comparison_df['period_1_avg'], mode='markers', marker=dict(color='#2e7ebb', size=12)))
        fig.add_trace(go.Scatter(name=period_b_label, x=comparison_df['metric'], y=comparison_df['period_2_avg'], mode='markers', marker=dict(color='#4caf7d', size=12)))

    elif chart_type == 'line':
        fig.add_trace(go.Scatter(name=period_a_label, x=comparison_df['metric'], y=comparison_df['period_1_avg'], mode='lines+markers', marker=dict(color='#2e7ebb')))
        fig.add_trace(go.Scatter(name=period_b_label, x=comparison_df['metric'], y=comparison_df['period_2_avg'], mode='lines+markers', marker=dict(color='#4caf7d')))

    elif chart_type == 'box':
        fig.add_trace(go.Bar(name=period_a_label, x=comparison_df['metric'], y=comparison_df['period_1_avg'], marker_color='#2e7ebb'))
        fig.add_trace(go.Bar(name=period_b_label, x=comparison_df['metric'], y=comparison_df['period_2_avg'], marker_color='#4caf7d'))

    title_parts = [f'{period_a_label} vs {period_b_label}']
    if selected_art_nr:
        title_parts.append(f"Tool: {', '.join(selected_art_nr)}")
    if selected_ao_nr:
        title_parts.append(f"Order: {', '.join(selected_ao_nr)}")
    if cleaning_method and cleaning_method != 'none':
        cleaning_labels = {
            'global_top1': 'Global top 1% removed',
            'global_top5': 'Global top 5% removed',
            'per_product_top5': 'Per tool top 5% removed',
            'per_order_top5': 'Per order top 5% removed',
        }
        label = cleaning_labels.get(cleaning_method, 'Custom threshold')
        title_parts.append(label)

    fig.update_layout(
        barmode='group',
        title=' · '.join(title_parts),
        xaxis_title='Metric',
        yaxis_title='Average Value (seconds)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
    )

    if return_fig:
        return fig
    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)

# ---------------------------------------------------------------------------
# Scatter plot with rolling mean and outlier highlighting
# ---------------------------------------------------------------------------

def plot_scatter_with_trend(df_p1, df_p2, metric, date_column, selected_ao_nr=None, rolling_window=80, excluded_cycles=None, return_fig=False):
    """
    Render individual cycle values over time for both groups on a shared axis.

    Cycles are laid out in chronological order: Period A occupies the left half
    of the x-axis and Period B the right half, separated by a dashed boundary
    line. This makes temporal trends and the difference between groups visually
    obvious without the axis representing real calendar time.

    Outliers (top 5 % of the combined dataset) are highlighted in red so the
    analyst can identify abnormal cycles and optionally exclude them using the
    exclusion bar. A rolling mean line is drawn only when the process shows
    meaningful variance (IQR > 10 % of median) — for stable processes the
    scatter alone is sufficient.

    Args:
        df_p1: Baseline group DataFrame (sorted by date_column inside function).
        df_p2: Comparison group DataFrame.
        metric: Column name of the metric to plot.
        date_column: Column name used for chronological ordering.
        selected_ao_nr: Unused; kept for API consistency with other chart functions.
        rolling_window: Number of cycles in the rolling mean window (default 80).
        excluded_cycles: List of CycleCnt values to remove before plotting.

    Returns:
        HTML string containing the Plotly figure (include_plotlyjs=False).
    """
    import plotly.graph_objects as go

    if metric not in df_p1.columns or metric not in df_p2.columns:
        return "<p>Metric not found.</p>"

    excluded_cycles = excluded_cycles or []

    df_p1 = df_p1.copy().sort_values(date_column).reset_index(drop=True)
    df_p2 = df_p2.copy().sort_values(date_column).reset_index(drop=True)

    # Apply user exclusions
    if 'CycleCnt' in df_p1.columns and excluded_cycles:
        df_p1 = df_p1[~df_p1['CycleCnt'].isin(excluded_cycles)].reset_index(drop=True)
        df_p2 = df_p2[~df_p2['CycleCnt'].isin(excluded_cycles)].reset_index(drop=True)

    has_cyclecnt = 'CycleCnt' in df_p1.columns

    # Combined cycle index — Period A first, Period B continues after
    df_p1['_cycle_idx'] = range(len(df_p1))
    df_p2['_cycle_idx'] = range(len(df_p1), len(df_p1) + len(df_p2))
    df_p1['_period'] = 'A'
    df_p2['_period'] = 'B'

    boundary = len(df_p1)
    total = len(df_p1) + len(df_p2)
    padding = max(int(total * 0.02), 5)

    combined_metric = pd.concat([df_p1[metric], df_p2[metric]])
    outlier_threshold = combined_metric.quantile(0.95)

    fig = go.Figure()

    def add_period(df, period_label, colour):
        is_outlier = df[metric] > outlier_threshold
        normal_df = df[~is_outlier]
        outlier_df = df[is_outlier]

        cd_cols = [date_column, 'JevAoNr']
        if has_cyclecnt:
            cd_cols.append('CycleCnt')

        hover = (
            f'<b>Period {period_label}</b><br>'
            'Order: %{customdata[1]}<br>'
            f'{metric}: %{{y:.1f}}s<br>'
            'Date: %{customdata[0]}<br>'
        )
        if has_cyclecnt:
            hover += 'CycleCnt: %{customdata[2]}<br>'
        hover += '<extra></extra>'

        fig.add_trace(go.Scatter(
            x=normal_df['_cycle_idx'],
            y=normal_df[metric],
            mode='markers',
            name=f'Period {period_label}',
            marker=dict(color=colour, size=5, opacity=0.5),
            customdata=normal_df[cd_cols].values,
            hovertemplate=hover,
        ))

        if not outlier_df.empty:
            outlier_hover = (
                f'<b>⚠ Outlier (Period {period_label})</b><br>'
                'Order: %{customdata[1]}<br>'
                f'{metric}: %{{y:.1f}}s<br>'
                'Date: %{customdata[0]}<br>'
            )
            if has_cyclecnt:
                outlier_hover += 'CycleCnt: %{customdata[2]}<br>'
            outlier_hover += '<extra></extra>'

            fig.add_trace(go.Scatter(
                x=outlier_df['_cycle_idx'],
                y=outlier_df[metric],
                mode='markers',
                name=f'Outlier ({period_label})',
                marker=dict(color='#e05c5c', size=6, opacity=0.95),
                customdata=outlier_df[cd_cols].values,
                hovertemplate=outlier_hover,
            ))

        # Skip rolling mean if most cycles cluster tightly (IQR small relative to median)
        median_val = df[metric].median()
        q1 = df[metric].quantile(0.25)
        q3 = df[metric].quantile(0.75)
        iqr = q3 - q1
        relative_iqr = (iqr / median_val) if median_val else 0
        low_variance = relative_iqr < 0.10  # less than 10% spread in middle 50%

        if len(df) >= rolling_window and not low_variance:
            df_sorted = df.sort_values('_cycle_idx').copy()
            df_sorted['_rolling'] = df_sorted[metric].rolling(window=rolling_window, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=df_sorted['_cycle_idx'],
                y=df_sorted['_rolling'],
                mode='lines',
                name=f'Mean — Period {period_label}',
                line=dict(color=colour, width=3),
                opacity=1.0,
            ))

    add_period(df_p1, 'A', '#2e7ebb')
    add_period(df_p2, 'B', '#4caf7d')

    # Background shading
    fig.add_vrect(
        x0=-padding, x1=boundary - 0.5,
        fillcolor='rgba(46,126,187,0.05)',
        layer='below', line_width=0,
    )
    fig.add_vrect(
        x0=boundary - 0.5, x1=total + padding,
        fillcolor='rgba(76,175,125,0.05)',
        layer='below', line_width=0,
    )

    # Period boundary divider
    fig.add_vline(
        x=boundary - 0.5,
        line=dict(color='#4a7fa5', width=1, dash='dash'),
    )

    # Period labels
    fig.add_annotation(x=boundary / 2, y=1.05, yref='paper',
        text=f'Period A · {len(df_p1)} cycles', showarrow=False,
        font=dict(color='#2e7ebb', size=12), xanchor='center')
    fig.add_annotation(x=boundary + (total - boundary) / 2, y=1.05, yref='paper',
        text=f'Period B · {len(df_p2)} cycles', showarrow=False,
        font=dict(color='#4caf7d', size=12), xanchor='center')

    y_min = combined_metric.min()
    y_max = combined_metric.max()
    y_padding = (y_max - y_min) * 0.08

    fig.update_layout(
        xaxis_title='Cycle',
        yaxis_title=f'{metric} (seconds)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(
            gridcolor='#1e2a3a',
            range=[-padding, total + padding],
            zeroline=False,
            showline=True,
            linecolor='#1e2a3a',
        ),
        yaxis=dict(
            gridcolor='#1e2a3a',
            range=[max(0, y_min - y_padding), y_max + y_padding],
            zeroline=False,
            showline=True,
            linecolor='#1e2a3a',
        ),
        margin=dict(t=60),
    )

    if return_fig:
        return fig
    return fig.to_html(full_html=False, include_plotlyjs=False)

# ---------------------------------------------------------------------------
# Line chart with regression slope
# ---------------------------------------------------------------------------

def plot_line_with_slope(df_p1, df_p2, metric, date_column, rolling_window=80):
    """
    Render rolling mean lines and OLS regression slope lines for both groups.

    The slope (expressed as seconds per cycle) answers the question: was the
    process trending upward or downward within each period? A flat slope
    indicates a stable process; a rising slope may signal gradual degradation.
    The slope label is shown in the legend next to each trace so it can be
    read without hovering.

    Args:
        df_p1: Baseline group DataFrame.
        df_p2: Comparison group DataFrame.
        metric: Metric column name to plot.
        date_column: Column used for chronological ordering.
        rolling_window: Rolling mean window size in cycles (default 80).

    Returns:
        HTML string containing the Plotly figure (include_plotlyjs=False).
    """
    import plotly.graph_objects as go
    import numpy as np

    if metric not in df_p1.columns or metric not in df_p2.columns:
        return "<p>Metric not found.</p>"

    df_p1 = df_p1.copy().sort_values(date_column).reset_index(drop=True)
    df_p2 = df_p2.copy().sort_values(date_column).reset_index(drop=True)
    df_p1['_cycle_idx'] = range(len(df_p1))
    df_p2['_cycle_idx'] = range(len(df_p1), len(df_p1) + len(df_p2))

    boundary = len(df_p1)
    total = len(df_p1) + len(df_p2)
    padding = max(int(total * 0.02), 5)

    fig = go.Figure()

    def add_period_lines(df, period_label, colour):
        if len(df) < 2:
            return

        # Rolling mean
        if len(df) >= rolling_window:
            df_sorted = df.sort_values('_cycle_idx').copy()
            df_sorted['_rolling'] = df_sorted[metric].rolling(window=rolling_window, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=df_sorted['_cycle_idx'],
                y=df_sorted['_rolling'],
                mode='lines',
                name=f'Rolling mean — Period {period_label}',
                line=dict(color=colour, width=2),
                opacity=0.85,
            ))

        # Regression slope line
        x = df['_cycle_idx'].values
        y = df[metric].values
        valid = ~np.isnan(y)
        if valid.sum() < 2:
            return

        slope, intercept = np.polyfit(x[valid], y[valid], 1)
        y_fit = slope * x + intercept

        # Slope direction text
        slope_text = f'{slope:+.3f}/cycle'
        fig.add_trace(go.Scatter(
            x=x,
            y=y_fit,
            mode='lines',
            name=f'Slope {period_label} ({slope_text})',
            line=dict(color=colour, width=2, dash='dash'),
            opacity=1.0,
        ))

    add_period_lines(df_p1, 'A', '#2e7ebb')
    add_period_lines(df_p2, 'B', '#4caf7d')

    # Background shading
    fig.add_vrect(x0=-padding, x1=boundary - 0.5,
        fillcolor='rgba(46,126,187,0.05)', layer='below', line_width=0)
    fig.add_vrect(x0=boundary - 0.5, x1=total + padding,
        fillcolor='rgba(76,175,125,0.05)', layer='below', line_width=0)
    fig.add_vline(x=boundary - 0.5, line=dict(color='#4a7fa5', width=1, dash='dash'))

    fig.add_annotation(x=boundary / 2, y=1.05, yref='paper',
        text=f'Period A · {len(df_p1)} cycles', showarrow=False,
        font=dict(color='#2e7ebb', size=12), xanchor='center')
    fig.add_annotation(x=boundary + (total - boundary) / 2, y=1.05, yref='paper',
        text=f'Period B · {len(df_p2)} cycles', showarrow=False,
        font=dict(color='#4caf7d', size=12), xanchor='center')

    combined = pd.concat([df_p1[metric], df_p2[metric]])
    y_min, y_max = combined.min(), combined.max()
    y_padding = (y_max - y_min) * 0.08

    fig.update_layout(
        xaxis_title='Cycle',
        yaxis_title=f'{metric} (seconds)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(gridcolor='#1e2a3a', range=[-padding, total + padding], zeroline=False, showline=True, linecolor='#1e2a3a'),
        yaxis=dict(gridcolor='#1e2a3a', range=[max(0, y_min - y_padding), y_max + y_padding], zeroline=False, showline=True, linecolor='#1e2a3a'),
        margin=dict(t=60),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)

# ---------------------------------------------------------------------------
# Violin plot (distribution comparison)
# ---------------------------------------------------------------------------

def plot_violin(df_p1, df_p2, metric):
    """
    Render a mirrored violin plot comparing the full distribution of both groups.

    The violin shape is a kernel density estimate: wider regions indicate more
    cycles at that value. An embedded box plot shows the median and interquartile
    range, and the mean line gives a direct visual comparison of central tendency.

    Unlike the bar chart (which shows only the mean) or the scatter (which shows
    every cycle), the violin exposes whether the distributions are unimodal,
    skewed, or bimodal — patterns that are invisible in average-based views.

    Args:
        df_p1: Baseline group DataFrame.
        df_p2: Comparison group DataFrame.
        metric: Metric column name to compare.

    Returns:
        HTML string containing the Plotly figure (include_plotlyjs=False).
    """
    if metric not in df_p1.columns or metric not in df_p2.columns:
        return "<p>Metric not found.</p>"

    fig = go.Figure()

    fig.add_trace(go.Violin(
        y=df_p1[metric].dropna(),
        name='Period A',
        side='negative',
        line_color='#2e7ebb',
        fillcolor='rgba(46,126,187,0.18)',
        meanline_visible=True,
        box_visible=True,
        points=False,
    ))

    fig.add_trace(go.Violin(
        y=df_p2[metric].dropna(),
        name='Period B',
        side='positive',
        line_color='#4caf7d',
        fillcolor='rgba(76,175,125,0.18)',
        meanline_visible=True,
        box_visible=True,
        points=False,
    ))

    fig.update_layout(
        violinmode='overlay',
        yaxis_title=f'{metric} (seconds)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(showticklabels=False, gridcolor='#1e2a3a', zeroline=False),
        yaxis=dict(gridcolor='#1e2a3a', zeroline=False, showline=True, linecolor='#1e2a3a'),
        margin=dict(t=30, l=60),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


# ---------------------------------------------------------------------------
# Statistical Process Control (SPC) control chart
# ---------------------------------------------------------------------------

def plot_control_chart(df_p1, df_p2, metric, date_column, excluded_cycles=None):
    """
    Render a Shewhart individuals control chart.

    The Upper and Lower Control Limits (UCL / LCL) are calculated from Period A
    alone (mean ± 3σ), representing the process capability of the baseline period.
    Period B cycles are then plotted against those limits: points outside the
    limits (marked with ×) indicate the process has shifted beyond what was
    historically considered normal — which may be an improvement (below LCL)
    or a degradation (above UCL).

    The annotation at the bottom of the chart reports the count and percentage of
    out-of-control points in Period B, giving a quick summary without reading
    individual × markers.

    Control limit choice: ±3σ is the conventional Shewhart boundary, giving
    approximately 0.27 % false-alarm rate for a normally distributed process.

    Args:
        df_p1: Baseline group DataFrame (used to compute UCL/LCL).
        df_p2: Comparison group DataFrame (judged against the baseline limits).
        metric: Metric column name to chart.
        date_column: Column used for chronological ordering.
        excluded_cycles: List of CycleCnt values to remove before charting.

    Returns:
        HTML string containing the Plotly figure (include_plotlyjs=False).
    """
    if metric not in df_p1.columns or metric not in df_p2.columns:
        return "<p>Metric not found.</p>"

    excluded_cycles = excluded_cycles or []

    df_p1 = df_p1.copy().sort_values(date_column).reset_index(drop=True)
    df_p2 = df_p2.copy().sort_values(date_column).reset_index(drop=True)

    if 'CycleCnt' in df_p1.columns and excluded_cycles:
        df_p1 = df_p1[~df_p1['CycleCnt'].isin(excluded_cycles)].reset_index(drop=True)
        df_p2 = df_p2[~df_p2['CycleCnt'].isin(excluded_cycles)].reset_index(drop=True)

    has_cyclecnt = 'CycleCnt' in df_p1.columns

    df_p1['_cycle_idx'] = range(len(df_p1))
    df_p2['_cycle_idx'] = range(len(df_p1), len(df_p1) + len(df_p2))

    boundary = len(df_p1)
    total = len(df_p1) + len(df_p2)
    padding = max(int(total * 0.02), 5)

    # Control limits from Period A
    mean_a = df_p1[metric].mean()
    std_a  = df_p1[metric].std()
    ucl    = mean_a + 3 * std_a
    lcl    = max(0.0, mean_a - 3 * std_a)
    mean_b = df_p2[metric].mean()

    fig = go.Figure()

    cd_cols = [date_column, 'JevAoNr'] + (['CycleCnt'] if has_cyclecnt else [])

    def add_period(df, period_label, colour):
        in_ctrl  = (df[metric] >= lcl) & (df[metric] <= ucl)
        norm_df  = df[in_ctrl]
        ooc_df   = df[~in_ctrl]

        base_hover = (
            f'<b>Period {period_label}</b><br>'
            'Order: %{customdata[1]}<br>'
            f'{metric}: %{{y:.1f}}s<br>'
            'Date: %{customdata[0]}<br>'
        )
        if has_cyclecnt:
            base_hover += 'CycleCnt: %{customdata[2]}<br>'
        base_hover += '<extra></extra>'

        if not norm_df.empty:
            fig.add_trace(go.Scatter(
                x=norm_df['_cycle_idx'], y=norm_df[metric],
                mode='markers', name=f'Period {period_label}',
                marker=dict(color=colour, size=5, opacity=0.5),
                customdata=norm_df[cd_cols].values,
                hovertemplate=base_hover,
            ))

        if not ooc_df.empty:
            ooc_hover = base_hover.replace(f'<b>Period {period_label}</b>', f'<b>⚠ Out of control ({period_label})</b>')
            fig.add_trace(go.Scatter(
                x=ooc_df['_cycle_idx'], y=ooc_df[metric],
                mode='markers', name=f'OOC ({period_label})',
                marker=dict(color='#e05c5c', size=8, opacity=0.95, symbol='x-thin', line=dict(width=2, color='#e05c5c')),
                customdata=ooc_df[cd_cols].values,
                hovertemplate=ooc_hover,
            ))

    add_period(df_p1, 'A', '#2e7ebb')
    add_period(df_p2, 'B', '#4caf7d')

    x_span = [-padding, total + padding]

    # UCL / LCL
    fig.add_trace(go.Scatter(x=x_span, y=[ucl, ucl], mode='lines',
        name=f'UCL  {ucl:.1f}s', line=dict(color='#e05c5c', width=1.5, dash='dash'), opacity=0.85))
    if lcl > 0:
        fig.add_trace(go.Scatter(x=x_span, y=[lcl, lcl], mode='lines',
            name=f'LCL  {lcl:.1f}s', line=dict(color='#e05c5c', width=1.5, dash='dash'), opacity=0.85))

    # Center line (mean A) and mean B
    fig.add_trace(go.Scatter(x=x_span, y=[mean_a, mean_a], mode='lines',
        name=f'Mean A  {mean_a:.1f}s', line=dict(color='#2e7ebb', width=1.5, dash='dot'), opacity=0.9))
    fig.add_trace(go.Scatter(x=x_span, y=[mean_b, mean_b], mode='lines',
        name=f'Mean B  {mean_b:.1f}s', line=dict(color='#4caf7d', width=1.5, dash='dot'), opacity=0.9))

    # Background shading + period boundary
    fig.add_vrect(x0=-padding, x1=boundary - 0.5,
        fillcolor='rgba(46,126,187,0.05)', layer='below', line_width=0)
    fig.add_vrect(x0=boundary - 0.5, x1=total + padding,
        fillcolor='rgba(76,175,125,0.05)', layer='below', line_width=0)
    fig.add_vline(x=boundary - 0.5, line=dict(color='#4a7fa5', width=1, dash='dash'))

    fig.add_annotation(x=boundary / 2, y=1.05, yref='paper',
        text=f'Period A · {len(df_p1)} cycles', showarrow=False,
        font=dict(color='#2e7ebb', size=12), xanchor='center')
    fig.add_annotation(x=boundary + (total - boundary) / 2, y=1.05, yref='paper',
        text=f'Period B · {len(df_p2)} cycles', showarrow=False,
        font=dict(color='#4caf7d', size=12), xanchor='center')

    ooc_b = int(((df_p2[metric] > ucl) | (df_p2[metric] < lcl)).sum())
    if ooc_b > 0:
        pct = ooc_b / len(df_p2) * 100
        fig.add_annotation(x=boundary + (total - boundary) / 2, y=0.0, yref='paper',
            text=f'⚠ {ooc_b} out of control in B ({pct:.1f}%)',
            showarrow=False, font=dict(color='#e05c5c', size=11), xanchor='center')

    combined = pd.concat([df_p1[metric], df_p2[metric]])
    y_min = combined.min()
    y_max = max(combined.max(), ucl)
    y_pad = (y_max - y_min) * 0.10

    fig.update_layout(
        xaxis_title='Cycle',
        yaxis_title=f'{metric} (seconds)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(gridcolor='#1e2a3a', range=[-padding, total + padding],
                   zeroline=False, showline=True, linecolor='#1e2a3a'),
        yaxis=dict(gridcolor='#1e2a3a', range=[max(0, y_min - y_pad), y_max + y_pad],
                   zeroline=False, showline=True, linecolor='#1e2a3a'),
        margin=dict(t=60),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


# ---------------------------------------------------------------------------
# PDCA trend chart (dashboard — improvement over experiments)
# ---------------------------------------------------------------------------

def plot_experiment_trend(trend_data, include_plotlyjs='cdn'):
    """
    Render a multi-line chart showing pct_change per metric across saved experiments.

    Answers the PDCA question: "Are we consistently improving with each cycle?"
    Each line is one metric; points are green when the metric improved (pct_change < 0)
    and red when it worsened. A dashed zero reference line marks no change.

    Args:
        trend_data: Dict mapping metric name → list of (datetime, pct_change) tuples,
                    sorted chronologically. Only metrics with ≥ 2 points are included.
        include_plotlyjs: Passed to fig.to_html(). Use 'cdn' for the dashboard since
                          Plotly is not pre-loaded on that page.

    Returns:
        HTML string containing the Plotly figure.
    """
    metric_colours = ['#2e7ebb', '#4caf7d', '#e0a050', '#a070d0', '#e05c5c', '#50c0c0']

    fig = go.Figure()

    for i, (metric, points) in enumerate(trend_data.items()):
        points_sorted = sorted(points, key=lambda p: p[0])
        dates = [p[0] for p in points_sorted]
        pcts = [p[1] for p in points_sorted]
        base_colour = metric_colours[i % len(metric_colours)]

        point_colours = ['#4caf7d' if v < 0 else '#e05c5c' for v in pcts]

        fig.add_trace(go.Scatter(
            x=dates,
            y=pcts,
            mode='lines+markers',
            name=metric,
            line=dict(color=base_colour, width=2),
            marker=dict(color=point_colours, size=9, line=dict(color=base_colour, width=1.5)),
            hovertemplate=f'<b>{metric}</b><br>%{{x|%Y-%m-%d}}<br>Change: %{{y:+.1f}}%<extra></extra>',
        ))

    # Zero reference line
    fig.add_hline(
        y=0,
        line=dict(color='#4a7fa5', width=1, dash='dash'),
        annotation_text='no change',
        annotation_position='right',
        annotation_font=dict(color='#4a7fa5', size=10),
    )

    fig.update_layout(
        yaxis_title='% change (negative = improved)',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(gridcolor='#1e2a3a', zeroline=False, showline=True, linecolor='#1e2a3a'),
        yaxis=dict(gridcolor='#1e2a3a', zeroline=False, showline=True, linecolor='#1e2a3a'),
        margin=dict(t=20, b=40, l=60, r=80),
        height=280,
    )

    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)


# ---------------------------------------------------------------------------
# Histogram (frequency distribution)
# ---------------------------------------------------------------------------

def plot_histogram(df_p1, df_p2, metric, excluded_cycles=None, period_a_label='Period A', period_b_label='Period B'):
    """
    Render overlapping frequency histograms for both groups.

    Shows the distribution shape of cycle times rather than just the mean —
    useful for spotting bimodal distributions or a shift in the whole spread.
    Bins are computed from the combined dataset so both periods are directly
    comparable on the same x-axis.

    Args:
        df_p1: Baseline group DataFrame.
        df_p2: Comparison group DataFrame.
        metric: Metric column name to plot.
        excluded_cycles: List of CycleCnt values to remove before plotting.
        period_a_label: Legend label for the baseline group.
        period_b_label: Legend label for the comparison group.

    Returns:
        HTML string containing the Plotly figure (include_plotlyjs=False).
    """
    if metric not in df_p1.columns or metric not in df_p2.columns:
        return "<p>Metric not found.</p>"

    excluded_cycles = excluded_cycles or []
    df_p1 = df_p1.copy()
    df_p2 = df_p2.copy()

    if excluded_cycles and 'CycleCnt' in df_p1.columns:
        df_p1 = df_p1[~df_p1['CycleCnt'].isin(excluded_cycles)]
        df_p2 = df_p2[~df_p2['CycleCnt'].isin(excluded_cycles)]

    combined = pd.concat([df_p1[metric].dropna(), df_p2[metric].dropna()])
    x_min, x_max = combined.min(), combined.max()
    bin_size = (x_max - x_min) / 40 if x_max > x_min else 1

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=df_p1[metric].dropna(),
        name=period_a_label,
        marker_color='rgba(46,126,187,0.7)',
        xbins=dict(start=x_min, end=x_max, size=bin_size),
    ))
    fig.add_trace(go.Histogram(
        x=df_p2[metric].dropna(),
        name=period_b_label,
        marker_color='rgba(76,175,125,0.7)',
        xbins=dict(start=x_min, end=x_max, size=bin_size),
    ))

    x_label = f'{metric} (seconds)' if metric not in ('Feeding_cnt',) else metric

    fig.update_layout(
        barmode='overlay',
        xaxis_title=x_label,
        yaxis_title='Cycle count',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#8ba8c0'),
        legend=dict(bgcolor='rgba(15,26,38,0.8)', bordercolor='#1e2a3a', borderwidth=1),
        xaxis=dict(gridcolor='#1e2a3a', zeroline=False, showline=True, linecolor='#1e2a3a'),
        yaxis=dict(gridcolor='#1e2a3a', zeroline=False, showline=True, linecolor='#1e2a3a'),
        margin=dict(t=30),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)