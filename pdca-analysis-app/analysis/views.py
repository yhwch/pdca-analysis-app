"""
Django views for the PDCA analysis application.

This module implements the full request/response cycle for the web interface:
file upload, two analysis modes (period comparison and tool comparison), saved
experiment management, and JSON API endpoints consumed by client-side JavaScript.

Analysis modes
--------------
Period comparison  (/compare/)
    The analyst defines a baseline window (Period A) and a post-change window
    (Period B) for the same tool. The system computes metric averages and
    percentage changes, showing whether cycle times improved after an intervention.

Tool comparison  (/compare/tools/)
    Two different tools (JevArtNr) are compared over the same shared date range.
    Useful for evaluating whether a new tooling setup outperforms the current one
    under identical production conditions.

Data flow
---------
The uploaded DataFrame is serialised to JSON and stored in the Django session.
All analysis views read from the session — no raw production data is written to
the database. Saved Experiment records store both aggregated results and the raw
period DataFrames (as JSON blobs) so charts can be regenerated later without the
original file.

Session keys
    'df'             — full DataFrame as JSON string
    'parsed_columns' — list of column names that were parsed as datetimes
    'created_metrics'— list of derived metric column names (e.g. 'CureDuration')
    'art_nr_options' — sorted list of unique JevArtNr values (tool numbers)
    'ao_nr_options'  — sorted list of unique JevAoNr values (order numbers)
    'file_name'      — original filename, shown in the UI
    'file_bytes'     — base64-encoded file bytes for sheet selection (Excel only)
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import base64
import io
import pandas as pd
from django.shortcuts import render, get_object_or_404
from .data_loader import get_excel_sheet_names, load_data
from .data_cleaning import clean_data, add_derived_metrics
from .models import Experiment
from .metrics import compare_two_periods, calculate_rope_rip, seconds_to_minsec


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload(request):
    """
    Handle file upload and optional sheet selection for multi-sheet Excel files.

    On a file POST the uploaded bytes are read once, base64-encoded into the
    session (so the sheet-selector step can re-read them without a second upload),
    and the DataFrame is parsed, cleaned, and stored in the session as JSON.

    For multi-sheet Excel files a sheet-selector form is rendered first; the
    analyst picks a sheet and submits a second POST that reads the stored bytes
    and loads the chosen sheet.

    Session state is fully replaced on each successful upload so stale data from
    a previous file never contaminates the current analysis.
    """
    context = {
        'file_name': request.session.get('file_name', None),
    }

    if request.method == 'POST' and request.FILES.get('data_file'):
        uploaded_file = request.FILES['data_file']
        file_name = uploaded_file.name.lower()

        try:
            file_bytes = uploaded_file.read()
            request.session['file_bytes'] = base64.b64encode(file_bytes).decode('utf-8')
            request.session['file_name'] = uploaded_file.name

            if file_name.endswith('.xlsx') or file_name.endswith('.xlsm'):
                sheet_names = get_excel_sheet_names(io.BytesIO(file_bytes))
                context['sheet_names'] = sheet_names
            else:
                df = load_data(io.BytesIO(file_bytes), file_name=uploaded_file.name)
                df, parsed_columns, invalid_date_summary, summary = clean_data(df)
                df, created_metrics = add_derived_metrics(df)

                request.session['df'] = df.to_json(date_format='iso')
                request.session['parsed_columns'] = parsed_columns
                request.session['created_metrics'] = created_metrics
                request.session['art_nr_options'] = sorted(df['JevArtNr'].dropna().apply(lambda x: str(int(float(x)))).unique().tolist(), key=int)
                request.session['ao_nr_options'] = sorted(df['JevAoNr'].dropna().apply(lambda x: str(int(float(x)))).unique().tolist(), key=int)

                context['success'] = True
                context['rows'] = df.shape[0]
                context['columns'] = df.shape[1]
                context['parsed_columns'] = parsed_columns
                context['created_metrics'] = created_metrics

        except Exception as e:
            context['error'] = str(e)

    elif request.method == 'POST' and request.POST.get('sheet_name'):
        try:
            file_bytes = base64.b64decode(request.session.get('file_bytes', ''))
            selected_sheet = request.POST.get('sheet_name')
            file_name = request.session.get('file_name', '')

            df = load_data(io.BytesIO(file_bytes), sheet_name=selected_sheet, file_name=file_name)
            df, parsed_columns, invalid_date_summary, summary = clean_data(df)
            df, created_metrics = add_derived_metrics(df)

            request.session['df'] = df.to_json(date_format='iso')
            request.session['parsed_columns'] = parsed_columns
            request.session['created_metrics'] = created_metrics
            request.session['art_nr_options'] = sorted(df['JevArtNr'].dropna().apply(lambda x: str(int(float(x)))).unique().tolist(), key=int)
            request.session['ao_nr_options'] = sorted(df['JevAoNr'].dropna().apply(lambda x: str(int(float(x)))).unique().tolist(), key=int)

            context['success'] = True
            context['selected_sheet'] = selected_sheet
            context['rows'] = df.shape[0]
            context['columns'] = df.shape[1]
            context['parsed_columns'] = parsed_columns
            context['created_metrics'] = created_metrics

            sheet_names = get_excel_sheet_names(io.BytesIO(file_bytes))
            context['sheet_names'] = sheet_names

        except Exception as e:
            context['error'] = str(e)

    return render(request, 'analysis/upload.html', context)



# ---------------------------------------------------------------------------
# Period comparison
# ---------------------------------------------------------------------------

def compare(request):
    """
    Main period-comparison view.

    GET  — renders the comparison form with tool/order chip selectors, date
           inputs, and cleaning method chips. Pre-computes the available date
           range and order tooltip statistics (ao_nr_stats) from the full
           session DataFrame so the UI can populate instantly.

    POST — applies the selected cleaning method, slices the DataFrame into two
           period windows, calls compare_two_periods() and calculate_rope_rip(),
           renders the initial bar chart, and optionally saves an Experiment record
           if the 'save_experiment' hidden field is present in the POST body.

    The form is hidden and replaced by a collapsed summary bar when results are
    shown; 'Edit ↑' restores it without a page reload.
    """
    df_json = request.session.get('df', None)
    parsed_columns = request.session.get('parsed_columns', [])
    created_metrics = request.session.get('created_metrics', [])
    art_nr_options = request.session.get('art_nr_options', [])
    ao_nr_options = request.session.get('ao_nr_options', [])

    if not df_json:
        return render(request, 'analysis/compare.html', {'no_data': True})

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    default_date_column = parsed_columns[0] if parsed_columns else None

    available_min = df[default_date_column].min() if default_date_column else None
    available_max = df[default_date_column].max() if default_date_column else None

    import json as _json
    ao_nr_stats = {}
    if default_date_column and 'JevAoNr' in df.columns:
        for ao_key, grp in df.dropna(subset=['JevAoNr']).groupby('JevAoNr'):
            d_min = grp[default_date_column].min()
            d_max = grp[default_date_column].max()
            ao_nr_stats[str(ao_key)] = {
                'cycles': len(grp),
                'from': str(d_min.date()) if pd.notna(d_min) else '',
                'to': str(d_max.date()) if pd.notna(d_max) else '',
            }

    context = {
        'parsed_columns': parsed_columns,
        'created_metrics': created_metrics,
        'art_nr_options': art_nr_options,
        'ao_nr_options': ao_nr_options,
        'available_min': str(available_min.date()) if available_min and pd.notna(available_min) else None,
        'available_max': str(available_max.date()) if available_max and pd.notna(available_max) else None,
        'cleaning_method': 'per_product_top5',
        'custom_threshold': '',
        'cleaning_choices': [
            ('per_product_top5', 'Per tool top 5% (recommended)'),
            ('per_order_top5', 'Per order top 5%'),
            ('global_top5', 'Global top 5%'),
            ('global_top1', 'Global top 1%'),
            ('none', 'No cleaning'),
            ('custom', 'Custom threshold'),
        ],
        'file_name': request.session.get('file_name', None),
        'ao_nr_stats_json': _json.dumps(ao_nr_stats),
    }

    if request.method == 'POST':
        period_1_start = request.POST.get('period_1_start')
        period_1_end = request.POST.get('period_1_end')
        period_2_start = request.POST.get('period_2_start')
        period_2_end = request.POST.get('period_2_end')
        date_column = request.POST.get('date_column') or default_date_column
        selected_metrics = request.POST.getlist('metrics')
        selected_art_nr = request.POST.getlist('art_nr')
        selected_ao_nr = request.POST.getlist('ao_nr')
        cleaning_method = request.POST.get('cleaning_method', 'none')

        excluded_str = request.POST.get('excluded_cycles', '')
        excluded_cycles = []
        if excluded_str:
            try:
                excluded_cycles = [int(x.strip()) for x in excluded_str.split(',') if x.strip()]
            except ValueError:
                pass

        keep_rope_rip_flag = request.POST.get('keep_rope_rip', 'false').lower() == 'true'

        if cleaning_method == 'custom':
            parts = []
            for metric in selected_metrics:
                val = request.POST.get(f'custom_threshold_{metric}', '').strip()
                if val:
                    try:
                        float(val)
                        parts.append(f'{metric}:{val}')
                    except ValueError:
                        pass
            cleaning_method_key = f'custom_{",".join(parts)}' if parts else 'none'
        else:
            cleaning_method_key = cleaning_method

        custom_thresholds_dict = {}
        if cleaning_method_key.startswith('custom_'):
            for part in cleaning_method_key[7:].split(','):
                if ':' in part:
                    m, t = part.split(':', 1)
                    custom_thresholds_dict[m.strip()] = t.strip()

        context.update({
            'period_1_start': period_1_start,
            'period_1_end': period_1_end,
            'period_2_start': period_2_start,
            'period_2_end': period_2_end,
            'date_column': date_column,
            'selected_metrics': selected_metrics,
            'selected_art_nr': selected_art_nr,
            'selected_ao_nr': selected_ao_nr,
            'cleaning_method': cleaning_method,
            'cleaning_method_key': cleaning_method_key,
            'custom_thresholds_dict': custom_thresholds_dict,
        })

        filtered_df = df.copy()
        if selected_art_nr:
            filtered_df = filtered_df[filtered_df['JevArtNr'].isin(selected_art_nr)]
        if selected_ao_nr:
            filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

        filtered_min = filtered_df[date_column].min() if len(filtered_df) > 0 else None
        filtered_max = filtered_df[date_column].max() if len(filtered_df) > 0 else None

        context.update({
            'available_min': str(filtered_min.date()) if filtered_min and pd.notna(filtered_min) else None,
            'available_max': str(filtered_max.date()) if filtered_max and pd.notna(filtered_max) else None,
        })

        from .data_cleaning import apply_cleaning_method
        rows_before_cleaning = len(filtered_df)
        filtered_df = apply_cleaning_method(filtered_df, cleaning_method_key, selected_metrics)
        cycles_removed = rows_before_cleaning - len(filtered_df)

        if period_1_start and period_1_end and period_2_start and period_2_end and selected_metrics:
            from .data_cleaning import filter_by_date_range
            from .metrics import compare_two_periods
            from .visualizations import plot_period_comparison

            p1_start = pd.to_datetime(period_1_start).date()
            p1_end = pd.to_datetime(period_1_end).date()
            p2_start = pd.to_datetime(period_2_start).date()
            p2_end = pd.to_datetime(period_2_end).date()

            df_p1 = filter_by_date_range(filtered_df, date_column, p1_start, p1_end)
            df_p2 = filter_by_date_range(filtered_df, date_column, p2_start, p2_end)

            from .metrics import compare_two_periods, calculate_rope_rip

            comparison_df = compare_two_periods(df_p1, df_p2, selected_metrics)
            rope_rip = calculate_rope_rip(df_p1, df_p2)

            chart_html = plot_period_comparison(
                comparison_df,
                selected_art_nr=selected_art_nr,
                selected_ao_nr=selected_ao_nr,
                cleaning_method=cleaning_method_key
            )

            valid_metrics = comparison_df.dropna(subset=['pct_change'])
            if not valid_metrics.empty:
                best_metric = valid_metrics.loc[valid_metrics['pct_change'].abs().idxmax()]
                pct = round(best_metric['pct_change'], 1)
                direction = "improved" if pct < 0 else "worsened"
                verdict = f"{best_metric['metric']} {direction} by {abs(pct)}% in Period B"
            else:
                verdict = "No data found for the selected filters and date ranges."

            comparison_records = comparison_df.to_dict('records')
            for row in comparison_records:
                row['period_1_avg_fmt'] = seconds_to_minsec(row['period_1_avg'])
                row['period_2_avg_fmt'] = seconds_to_minsec(row['period_2_avg'])

            context.update({
                'comparison': comparison_records,
                'chart_html': chart_html,
                'p1_rows': df_p1.shape[0],
                'p2_rows': df_p2.shape[0],
                'verdict': verdict,
                'rope_rip': rope_rip,
                'cycles_removed': cycles_removed,
            })

            if request.POST.get('save_experiment'):
                art_nr_list = selected_art_nr if selected_art_nr else []
                ao_nr_list = selected_ao_nr if selected_ao_nr else []

                from datetime import datetime as _dt
                def _mon(d):
                    try:
                        return _dt.strptime(str(d), '%Y-%m-%d').strftime('%b %Y')
                    except Exception:
                        return str(d)

                if len(art_nr_list) == 1:
                    tool_part = f"Tool {art_nr_list[0]}"
                elif art_nr_list:
                    tool_part = f"Tools {', '.join(art_nr_list)}"
                else:
                    tool_part = "All tools"

                if not ao_nr_list:
                    order_part = None
                elif len(ao_nr_list) <= 3:
                    order_part = f"Ord. {', '.join(ao_nr_list)}"
                else:
                    order_part = f"{len(ao_nr_list)} orders"

                period_part = f"{_mon(period_1_start)} vs {_mon(period_2_start)}"

                auto_name = " · ".join(p for p in [tool_part, order_part, period_part] if p)

                Experiment.objects.create(
                    name=auto_name,
                    date_column=date_column,
                    period_1_start=p1_start,
                    period_1_end=p1_end,
                    period_2_start=p2_start,
                    period_2_end=p2_end,
                    selected_art_nr=art_nr_list,
                    selected_ao_nr=ao_nr_list,
                    selected_metrics=selected_metrics,
                    cleaning_method=cleaning_method_key,
                    results=comparison_records,
                    verdict=verdict,
                    rope_rip=rope_rip,
                    p1_rows=df_p1.shape[0],
                    p2_rows=df_p2.shape[0],
                    cycles_removed=cycles_removed,
                    excluded_cycles=excluded_cycles,
                    keep_rope_rip=keep_rope_rip_flag,
                    df_p1_json=df_p1.to_json(date_format='iso'),
                    df_p2_json=df_p2.to_json(date_format='iso'),
                )
                context['saved'] = True

    return render(request, 'analysis/compare.html', context)



# ---------------------------------------------------------------------------
# Experiment management
# ---------------------------------------------------------------------------

def experiments(request):
    """Render the saved-experiments list in reverse chronological order."""
    from .models import Experiment
    all_experiments = Experiment.objects.all()
    return render(request, 'analysis/experiments.html', {'experiments': all_experiments})


def experiment_detail(request, experiment_id):
    """
    Render the detail view for a single saved experiment.

    Metric cards and the verdict banner are rendered from the stored JSON results.
    Charts are loaded lazily via the experiment_chart endpoint (see the modal in
    the template) so the page loads instantly even for large period DataFrames.
    """
    from .models import Experiment
    from .visualizations import plot_period_comparison
    import pandas as pd

    experiment = get_object_or_404(Experiment, id=experiment_id)

    chart_html = None
    if request.GET.get('load_chart'):
        chart_type = request.GET.get('chart_type', 'bar')
        comparison_df = pd.DataFrame(experiment.results)
        chart_html = plot_period_comparison(
            comparison_df,
            selected_art_nr=experiment.selected_art_nr,
            selected_ao_nr=experiment.selected_ao_nr,
            cleaning_method=experiment.cleaning_method,
            chart_type=chart_type,
            include_plotlyjs=False,
        )

    return render(request, 'analysis/experiment_detail.html', {
        'experiment': experiment,
        'chart_html': chart_html,
        'p1_rows': experiment.p1_rows,
        'p2_rows': experiment.p2_rows,
    })


def experiment_delete(request, experiment_id):
    """Delete an experiment record on POST and redirect to the experiments list."""
    from .models import Experiment
    from django.shortcuts import redirect
    if request.method == 'POST':
        experiment = get_object_or_404(Experiment, id=experiment_id)
        experiment.delete()
    return redirect('experiments')


def experiment_update_notes(request, experiment_id):
    """Save free-text analyst notes for an experiment via POST."""
    from django.shortcuts import redirect
    if request.method == 'POST':
        experiment = get_object_or_404(Experiment, id=experiment_id)
        experiment.notes = request.POST.get('notes', '')
        experiment.save()
    return redirect('experiment_detail', experiment_id=experiment_id)


def experiment_print(request, experiment_id):
    """Render a print-friendly view of the experiment with static chart images."""
    from .visualizations import plot_period_comparison, plot_scatter_with_trend
    import pandas as pd
    import io
    import base64

    experiment = get_object_or_404(Experiment, id=experiment_id)

    if experiment.comparison_type == 'tool' and experiment.selected_art_nr:
        pa_lbl = f"Tool {experiment.selected_art_nr[0]}"
        pb_lbl = f"Tool {experiment.tool_b}" if experiment.tool_b else "Tool B"
    else:
        pa_lbl, pb_lbl = 'Period A', 'Period B'

    def fig_to_png(fig, width=900, height=400):
        img_bytes = fig.to_image(format='png', width=width, height=height, scale=2)
        return 'data:image/png;base64,' + base64.b64encode(img_bytes).decode()

    comparison_df = pd.DataFrame(experiment.results)
    bar_fig = plot_period_comparison(
        comparison_df,
        selected_art_nr=experiment.selected_art_nr,
        selected_ao_nr=experiment.selected_ao_nr,
        cleaning_method=experiment.cleaning_method,
        chart_type='bar',
        period_a_label=pa_lbl,
        period_b_label=pb_lbl,
        return_fig=True,
    )
    # Use a white background for the static image so it reads on paper.
    bar_fig.update_layout(
        paper_bgcolor='white',
        plot_bgcolor='white',
        font=dict(color='#333'),
    )
    bar_chart_img = fig_to_png(bar_fig)

    scatter_charts = []
    if experiment.df_p1_json and experiment.selected_metrics:
        df_p1 = pd.read_json(io.StringIO(experiment.df_p1_json), convert_dates=False)
        df_p2 = pd.read_json(io.StringIO(experiment.df_p2_json), convert_dates=False)
        date_col = experiment.date_column
        if date_col:
            df_p1[date_col] = pd.to_datetime(df_p1[date_col], errors='coerce').dt.strftime('%Y-%m-%d')
            df_p2[date_col] = pd.to_datetime(df_p2[date_col], errors='coerce').dt.strftime('%Y-%m-%d')
        for metric in experiment.selected_metrics:
            scatter_fig = plot_scatter_with_trend(
                df_p1, df_p2, metric, date_col,
                selected_ao_nr=experiment.selected_ao_nr,
                excluded_cycles=experiment.excluded_cycles,
                return_fig=True,
            )
            scatter_fig.update_layout(
                paper_bgcolor='white',
                plot_bgcolor='#f8f9fa',
                font=dict(color='#333'),
            )
            scatter_charts.append((metric, fig_to_png(scatter_fig, height=450)))

    return render(request, 'analysis/experiment_print.html', {
        'experiment': experiment,
        'bar_chart_img': bar_chart_img,
        'scatter_charts': scatter_charts,
    })


def experiment_rename(request, experiment_id):
    """Rename an experiment inline via POST. Returns plain-text 'ok' consumed by JS."""
    from django.http import HttpResponse
    if request.method == 'POST':
        experiment = get_object_or_404(Experiment, id=experiment_id)
        experiment.name = request.POST.get('name', experiment.name)
        experiment.save()
    return HttpResponse('ok')


def experiment_chart(request, experiment_id):
    """
    AJAX endpoint that returns a Plotly HTML fragment for the chart modal.

    Per-cycle charts (scatter, line, violin, control) read from the stored
    df_p1_json / df_p2_json blobs; if those are absent the response explains
    that re-saving is needed. The bar chart reads from the aggregated results
    JSON, so it always works even for older saved experiments.

    For tool experiments, period_a_label / period_b_label are set to the actual
    tool numbers so the legend is correct.
    """
    from .models import Experiment
    from .visualizations import plot_period_comparison, plot_scatter_with_trend, plot_line_with_slope, plot_violin, plot_control_chart, plot_histogram
    from django.http import HttpResponse
    import pandas as pd
    import io

    experiment = get_object_or_404(Experiment, id=experiment_id)
    chart_type = request.GET.get('chart_type', 'bar')

    if experiment.comparison_type == 'tool' and experiment.selected_art_nr:
        pa_lbl = f"Tool {experiment.selected_art_nr[0]}"
        pb_lbl = f"Tool {experiment.tool_b}" if experiment.tool_b else "Tool B"
    else:
        pa_lbl, pb_lbl = 'Period A', 'Period B'

    if chart_type in ('scatter', 'line', 'violin', 'control', 'histogram'):
        if not experiment.df_p1_json:
            return HttpResponse('<p class="text-hgf-muted text-sm p-4">Raw cycle data not available for this experiment. Re-save to enable these charts.</p>')

        df_p1 = pd.read_json(io.StringIO(experiment.df_p1_json), convert_dates=False)
        df_p2 = pd.read_json(io.StringIO(experiment.df_p2_json), convert_dates=False)
        date_col = experiment.date_column
        if date_col:
            df_p1[date_col] = pd.to_datetime(df_p1[date_col], errors='coerce')
            df_p2[date_col] = pd.to_datetime(df_p2[date_col], errors='coerce')

        metric = request.GET.get('metric') or (experiment.selected_metrics[0] if experiment.selected_metrics else None)
        if not metric:
            return HttpResponse('<p class="text-hgf-muted text-sm p-4">No metric available.</p>')

        if chart_type == 'scatter':
            chart_html = plot_scatter_with_trend(
                df_p1, df_p2, metric, date_col,
                selected_ao_nr=experiment.selected_ao_nr,
                excluded_cycles=experiment.excluded_cycles,
            )
        elif chart_type == 'line':
            chart_html = plot_line_with_slope(df_p1, df_p2, metric, date_col)
        elif chart_type == 'violin':
            chart_html = plot_violin(df_p1, df_p2, metric)
        elif chart_type == 'histogram':
            chart_html = plot_histogram(
                df_p1, df_p2, metric,
                excluded_cycles=experiment.excluded_cycles,
                period_a_label=pa_lbl,
                period_b_label=pb_lbl,
            )
        else:
            chart_html = plot_control_chart(
                df_p1, df_p2, metric, date_col,
                excluded_cycles=experiment.excluded_cycles,
            )
        return HttpResponse(chart_html)

    comparison_df = pd.DataFrame(experiment.results)
    chart_html = plot_period_comparison(
        comparison_df,
        selected_art_nr=experiment.selected_art_nr,
        selected_ao_nr=experiment.selected_ao_nr,
        cleaning_method=experiment.cleaning_method,
        chart_type=chart_type,
        include_plotlyjs=False,
        period_a_label=pa_lbl,
        period_b_label=pb_lbl,
    )
    return HttpResponse(chart_html)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def dashboard(request):
    """
    Render the landing page with a PDCA progress summary.

    Counts improved/worsened experiments from the verdict field so the analyst
    gets an immediate read on overall progress without opening individual records.
    Navigation cards adapt based on whether a file is currently loaded in the
    session.
    """
    from .models import Experiment
    from collections import defaultdict
    from .visualizations import plot_experiment_trend

    all_experiments = Experiment.objects.all()
    total = all_experiments.count()
    improved_count = all_experiments.filter(verdict__icontains='improved').count()

    trend_data = defaultdict(list)
    for exp in Experiment.objects.order_by('created_at'):
        for row in (exp.results or []):
            trend_data[row['metric']].append((exp.created_at, row['pct_change']))
    trend_data = {m: pts for m, pts in trend_data.items() if len(pts) >= 2}
    trend_chart = plot_experiment_trend(trend_data) if trend_data else None

    return render(request, 'analysis/dashboard.html', {
        'recent_experiments': all_experiments[:5],
        'has_data': bool(request.session.get('df')),
        'file_name': request.session.get('file_name', None),
        'total_experiments': total,
        'improved_count': improved_count,
        'worsened_count': all_experiments.filter(verdict__icontains='worsened').count(),
        'trend_chart': trend_chart,
    })






# ---------------------------------------------------------------------------
# JSON API endpoints (called by JavaScript, no full page reload)
# ---------------------------------------------------------------------------

def api_date_range(request):
    """
    Return date range statistics and a statistically detected shift point.

    Called by the comparison form's JavaScript when the tool/order selection or
    cleaning method changes. Updates the 'Available range' panel and drives the
    'Split by detected shift' button.

    The shift detection uses the ruptures library (Binseg algorithm, L2 cost,
    one breakpoint) on the primary derived metric. A detected split is only
    reported if the mean of the post-split segment differs from the pre-split
    segment by at least one standard deviation of the pre-split data. This
    adaptive threshold scales to each dataset's natural variability, avoiding
    false positives on noisy data and false negatives on very stable processes.

    Detection runs on cleaned data because extreme cycles (maintenance, idle
    periods) are domain-known artefacts and should not drive the split point.

    Query parameters:
        art_nr[]        — tool numbers to filter the DataFrame before ranging
        ao_nr[]         — order numbers to filter
        p1_start, p1_end, p2_start, p2_end — optional; used for live cycle counts
        cleaning_method — applied before detection

    Returns JSON:
        available_min, available_max, total_cycles, midpoint,
        p1_cycles, p2_cycles, detected_split (date string or null)
    """
    import json
    from django.http import JsonResponse

    df_json = request.session.get('df', None)
    if not df_json:
        return JsonResponse({'error': 'No data loaded'}, status=400)

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    parsed_columns = request.session.get('parsed_columns', [])
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    date_column = parsed_columns[0] if parsed_columns else None
    if not date_column:
        return JsonResponse({'error': 'No date column'}, status=400)

    selected_art_nr = request.GET.getlist('art_nr')
    selected_ao_nr = request.GET.getlist('ao_nr')
    p1_start = request.GET.get('p1_start')
    p1_end = request.GET.get('p1_end')
    p2_start = request.GET.get('p2_start')
    p2_end = request.GET.get('p2_end')

    filtered_df = df.copy()
    if selected_art_nr:
        filtered_df = filtered_df[filtered_df['JevArtNr'].isin(selected_art_nr)]
    if selected_ao_nr:
        filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

    available_min = filtered_df[date_column].min()
    available_max = filtered_df[date_column].max()
    total_cycles = len(filtered_df)

    # Calculate midpoint for auto-fill suggestion
    if pd.notna(available_min) and pd.notna(available_max):
        midpoint = available_min + (available_max - available_min) / 2
        mid_str = str(midpoint.date())
    else:
        mid_str = None

    # Count cycles for each period if dates provided
    p1_cycles = None
    p2_cycles = None

    if p1_start and p1_end:
        p1 = filtered_df[
            (filtered_df[date_column].dt.date >= pd.to_datetime(p1_start).date()) &
            (filtered_df[date_column].dt.date <= pd.to_datetime(p1_end).date())
        ]
        p1_cycles = len(p1)

    if p2_start and p2_end:
        p2 = filtered_df[
            (filtered_df[date_column].dt.date >= pd.to_datetime(p2_start).date()) &
            (filtered_df[date_column].dt.date <= pd.to_datetime(p2_end).date())
        ]
        p2_cycles = len(p2)

    # Apply cleaning before detection so the shift is found in cleaned data
    created_metrics = request.session.get('created_metrics', [])
    cleaning_method_param = request.GET.get('cleaning_method', 'none')
    if cleaning_method_param and cleaning_method_param != 'none':
        from .data_cleaning import apply_cleaning_method
        filtered_df = apply_cleaning_method(filtered_df, cleaning_method_param, created_metrics)

    # Change point detection
    detected_split = None
    primary_metric = created_metrics[0] if created_metrics else None
    if primary_metric and primary_metric in filtered_df.columns and len(filtered_df) >= 50:
        try:
            import ruptures as rpt
            df_sorted = filtered_df.dropna(subset=[primary_metric]).sort_values(date_column).reset_index(drop=True)
            signal = df_sorted[primary_metric].values.reshape(-1, 1)
            algo = rpt.Binseg(model='l2', min_size=20).fit(signal)
            result = algo.predict(n_bkps=1)
            split_idx = result[0]  # 1-indexed exclusive end of first segment
            if 0 < split_idx < len(df_sorted):
                before = df_sorted[primary_metric].iloc[:split_idx]
                after  = df_sorted[primary_metric].iloc[split_idx:]
                mean_before = before.mean()
                if before.std() > 0 and abs(after.mean() - mean_before) >= before.std():
                    detected_split = str(df_sorted[date_column].iloc[split_idx].date())
        except Exception:
            pass

    return JsonResponse({
        'available_min': str(available_min.date()) if pd.notna(available_min) else None,
        'available_max': str(available_max.date()) if pd.notna(available_max) else None,
        'total_cycles': total_cycles,
        'midpoint': mid_str,
        'p1_cycles': p1_cycles,
        'p2_cycles': p2_cycles,
        'detected_split': detected_split,
    })




def compare_chart(request):
    """
    AJAX endpoint returning a Plotly HTML fragment for the period-comparison page.

    Called when the user switches chart type or selects a different metric.
    Applies the same cleaning and date filtering as the main compare() view so
    the chart is always consistent with the metric cards shown above it.
    """
    from django.http import HttpResponse
    import io

    df_json = request.session.get('df', None)
    if not df_json:
        return HttpResponse('No data loaded')

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    parsed_columns = request.session.get('parsed_columns', [])
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    date_column = parsed_columns[0] if parsed_columns else None
    chart_type = request.GET.get('chart_type', 'bar')
    selected_art_nr = request.GET.getlist('art_nr')
    selected_ao_nr = request.GET.getlist('ao_nr')
    selected_metrics = request.GET.getlist('metrics')
    cleaning_method = request.GET.get('cleaning_method', 'none')
    period_1_start = request.GET.get('period_1_start')
    period_1_end = request.GET.get('period_1_end')
    period_2_start = request.GET.get('period_2_start')
    period_2_end = request.GET.get('period_2_end')

    if not all([period_1_start, period_1_end, period_2_start, period_2_end]):
        return HttpResponse('<p class="text-hgf-muted text-sm p-4">Missing date parameters.</p>', status=400)

    filtered_df = df.copy()
    if selected_art_nr:
        filtered_df = filtered_df[filtered_df['JevArtNr'].isin(selected_art_nr)]
    if selected_ao_nr:
        filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

    from .data_cleaning import apply_cleaning_method, filter_by_date_range
    filtered_df = apply_cleaning_method(filtered_df, cleaning_method, selected_metrics)

    p1_start = pd.to_datetime(period_1_start).date()
    p1_end = pd.to_datetime(period_1_end).date()
    p2_start = pd.to_datetime(period_2_start).date()
    p2_end = pd.to_datetime(period_2_end).date()

    df_p1 = filter_by_date_range(filtered_df, date_column, p1_start, p1_end)
    df_p2 = filter_by_date_range(filtered_df, date_column, p2_start, p2_end)

    from .visualizations import plot_period_comparison, plot_scatter_with_trend

    excluded_str = request.GET.get('excluded_cycles', '')
    excluded_cycles = []
    if excluded_str:
        try:
            excluded_cycles = [int(x.strip()) for x in excluded_str.split(',') if x.strip()]
        except ValueError:
            pass

    if chart_type in ('scatter', 'line', 'violin', 'control', 'histogram'):
        metric = request.GET.get('metric', selected_metrics[0] if selected_metrics else None)
        if not metric:
            return HttpResponse('<p class="text-hgf-muted text-sm p-4">No metric selected.</p>')

        if chart_type == 'scatter':
            chart_html = plot_scatter_with_trend(
                df_p1, df_p2, metric, date_column,
                selected_ao_nr=selected_ao_nr,
                excluded_cycles=excluded_cycles,
            )
        elif chart_type == 'line':
            from .visualizations import plot_line_with_slope
            chart_html = plot_line_with_slope(df_p1, df_p2, metric, date_column)
        elif chart_type == 'violin':
            from .visualizations import plot_violin
            chart_html = plot_violin(df_p1, df_p2, metric)
        elif chart_type == 'histogram':
            from .visualizations import plot_histogram
            chart_html = plot_histogram(df_p1, df_p2, metric, excluded_cycles=excluded_cycles)
        else:
            from .visualizations import plot_control_chart
            chart_html = plot_control_chart(df_p1, df_p2, metric, date_column, excluded_cycles=excluded_cycles)
        return HttpResponse(chart_html)
    
    from .metrics import compare_two_periods
    comparison_df = compare_two_periods(df_p1, df_p2, selected_metrics)
    chart_html = plot_period_comparison(
        comparison_df,
        selected_art_nr=selected_art_nr,
        selected_ao_nr=selected_ao_nr,
        cleaning_method=cleaning_method,
        chart_type=chart_type,
        include_plotlyjs=False,
    )
    return HttpResponse(chart_html)

def compare_recalculate(request):
    """
    AJAX endpoint for live metric recalculation when cycles are excluded.

    Called by the scatter/control chart exclusion bar after the analyst adds or
    removes CycleCnt values. Re-runs compare_two_periods() and
    calculate_rope_rip() on the filtered data and returns updated averages and
    percentage changes as JSON. The client updates the metric cards in-place
    without a full page reload.

    Also validates the submitted excluded_cycles list against the actual data —
    invalid CycleCnt values (not present in either period) are reported back so
    the UI can display a warning and remove them from the exclusion chip list.
    """
    from django.http import JsonResponse
    from .data_cleaning import apply_cleaning_method, filter_by_date_range
    from .metrics import compare_two_periods, calculate_rope_rip, seconds_to_minsec
    import io

    df_json = request.session.get('df', None)
    if not df_json:
        return JsonResponse({'error': 'No data loaded'}, status=400)

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    parsed_columns = request.session.get('parsed_columns', [])
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    date_column = parsed_columns[0] if parsed_columns else None
    selected_art_nr = request.GET.getlist('art_nr')
    selected_ao_nr = request.GET.getlist('ao_nr')
    selected_metrics = request.GET.getlist('metrics')
    cleaning_method = request.GET.get('cleaning_method', 'none')
    period_1_start = request.GET.get('period_1_start')
    period_1_end = request.GET.get('period_1_end')
    period_2_start = request.GET.get('period_2_start')
    period_2_end = request.GET.get('period_2_end')

    excluded_str = request.GET.get('excluded_cycles', '')
    excluded_cycles = []
    if excluded_str:
        try:
            excluded_cycles = [int(x.strip()) for x in excluded_str.split(',') if x.strip()]
        except ValueError:
            pass

    keep_rope_rip = request.GET.get('keep_rope_rip', 'false').lower() == 'true'

    if not all([period_1_start, period_1_end, period_2_start, period_2_end]):
        return JsonResponse({'error': 'Missing date parameters'}, status=400)

    filtered_df = df.copy()
    if selected_art_nr:
        filtered_df = filtered_df[filtered_df['JevArtNr'].isin(selected_art_nr)]
    if selected_ao_nr:
        filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

    filtered_df = apply_cleaning_method(filtered_df, cleaning_method, selected_metrics)

    p1_start = pd.to_datetime(period_1_start).date()
    p1_end = pd.to_datetime(period_1_end).date()
    p2_start = pd.to_datetime(period_2_start).date()
    p2_end = pd.to_datetime(period_2_end).date()

    df_p1 = filter_by_date_range(filtered_df, date_column, p1_start, p1_end)
    df_p2 = filter_by_date_range(filtered_df, date_column, p2_start, p2_end)

    comparison_df = compare_two_periods(df_p1, df_p2, selected_metrics, excluded_cycles=excluded_cycles)
    rope_rip = calculate_rope_rip(df_p1, df_p2, excluded_cycles=excluded_cycles, keep_rope_rip=keep_rope_rip)

    comparison_records = comparison_df.to_dict('records')
    for row in comparison_records:
        row['period_1_avg_fmt'] = seconds_to_minsec(row['period_1_avg'])
        row['period_2_avg_fmt'] = seconds_to_minsec(row['period_2_avg'])

    # Adjust row counts after exclusion
    p1_rows = df_p1.shape[0]
    p2_rows = df_p2.shape[0]
    if excluded_cycles and 'CycleCnt' in df_p1.columns:
        p1_rows = df_p1[~df_p1['CycleCnt'].isin(excluded_cycles)].shape[0]
        p2_rows = df_p2[~df_p2['CycleCnt'].isin(excluded_cycles)].shape[0]

    # Validate excluded cycles against actual data
    valid_excluded = []
    invalid_excluded = []
    if excluded_cycles and 'CycleCnt' in df_p1.columns:
        all_cycle_cnts = pd.concat([df_p1['CycleCnt'], df_p2['CycleCnt']]).dropna().astype(int).tolist()
        for c in excluded_cycles:
            if c in all_cycle_cnts:
                valid_excluded.append(c)
            else:
                invalid_excluded.append(c)
    else:
        valid_excluded = excluded_cycles

    return JsonResponse({
        'comparison': comparison_records,
        'rope_rip': rope_rip,
        'p1_rows': p1_rows,
        'p2_rows': p2_rows,
        'valid_excluded': valid_excluded,
        'invalid_excluded': invalid_excluded,
    })



def api_orders_for_product(request):
    """
    Return order numbers (JevAoNr) that appear for a given set of tools.

    Called by the comparison form JavaScript when the analyst selects a tool chip,
    so the orders panel can be filtered to show only orders that involve that tool.
    This prevents the analyst from accidentally comparing orders that were run on
    a completely different set of tools.

    Query parameters:
        art_nr[] — one or more JevArtNr values

    Returns JSON:
        { orders: ['12345', '12346', ...] }
    """
    from django.http import JsonResponse

    df_json = request.session.get('df', None)
    if not df_json:
        return JsonResponse({'error': 'No data'}, status=400)

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    selected_art_nr = request.GET.getlist('art_nr')

    filtered = df[df['JevArtNr'].isin(selected_art_nr)]
    orders = sorted(filtered['JevAoNr'].dropna().unique().tolist())

    return JsonResponse({'orders': orders})


# ---------------------------------------------------------------------------
# Tool comparison
# ---------------------------------------------------------------------------

def compare_tools(request):
    """
    Tool-versus-tool comparison view.

    Structurally similar to compare(), but instead of splitting the dataset
    by date, it splits by JevArtNr (tool ID) within a single shared date range.
    Tool A acts as the baseline and Tool B as the comparison group.

    This mode is useful when two different tooling setups (e.g. current mould vs
    a new mould) are run simultaneously or consecutively under the same production
    conditions, and the analyst wants to know whether one tool performs better.

    Saving stores comparison_type='tool' on the Experiment record so the
    experiments list and detail views can display the correct labels (Tool A /
    Tool B instead of Period A / Period B).
    """
    df_json = request.session.get('df', None)
    parsed_columns = request.session.get('parsed_columns', [])
    created_metrics = request.session.get('created_metrics', [])
    art_nr_options = request.session.get('art_nr_options', [])
    ao_nr_options = request.session.get('ao_nr_options', [])

    if not df_json:
        return render(request, 'analysis/compare_tools.html', {'no_data': True})

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    default_date_column = parsed_columns[0] if parsed_columns else None
    available_min = df[default_date_column].min() if default_date_column else None
    available_max = df[default_date_column].max() if default_date_column else None

    import json as _json
    ao_nr_stats = {}
    if default_date_column and 'JevAoNr' in df.columns:
        for ao_key, grp in df.dropna(subset=['JevAoNr']).groupby('JevAoNr'):
            d_min = grp[default_date_column].min()
            d_max = grp[default_date_column].max()
            ao_nr_stats[str(ao_key)] = {
                'cycles': len(grp),
                'from': str(d_min.date()) if pd.notna(d_min) else '',
                'to': str(d_max.date()) if pd.notna(d_max) else '',
            }

    context = {
        'parsed_columns': parsed_columns,
        'created_metrics': created_metrics,
        'art_nr_options': art_nr_options,
        'ao_nr_options': ao_nr_options,
        'available_min': str(available_min.date()) if available_min and pd.notna(available_min) else None,
        'available_max': str(available_max.date()) if available_max and pd.notna(available_max) else None,
        'cleaning_method': 'per_product_top5',
        'custom_threshold': '',
        'cleaning_choices': [
            ('per_product_top5', 'Per tool top 5% (recommended)'),
            ('per_order_top5', 'Per order top 5%'),
            ('global_top5', 'Global top 5%'),
            ('global_top1', 'Global top 1%'),
            ('none', 'No cleaning'),
            ('custom', 'Custom threshold'),
        ],
        'file_name': request.session.get('file_name', None),
        'ao_nr_stats_json': _json.dumps(ao_nr_stats),
        'tool_a': '',
        'tool_b': '',
        'date_start': '',
        'date_end': '',
        'selected_ao_nr': [],
        'selected_metrics': created_metrics,
    }

    if request.method == 'POST':
        tool_a = request.POST.get('tool_a', '').strip()
        tool_b_val = request.POST.get('tool_b', '').strip()
        date_start = request.POST.get('date_start')
        date_end = request.POST.get('date_end')
        date_column = request.POST.get('date_column') or default_date_column
        selected_metrics = request.POST.getlist('metrics')
        selected_ao_nr = request.POST.getlist('ao_nr')
        cleaning_method = request.POST.get('cleaning_method', 'none')

        if cleaning_method == 'custom':
            parts = []
            for metric in selected_metrics:
                val = request.POST.get(f'custom_threshold_{metric}', '').strip()
                if val:
                    try:
                        float(val)
                        parts.append(f'{metric}:{val}')
                    except ValueError:
                        pass
            cleaning_method_key = f'custom_{",".join(parts)}' if parts else 'none'
        else:
            cleaning_method_key = cleaning_method

        custom_thresholds_dict = {}
        if cleaning_method_key.startswith('custom_'):
            for part in cleaning_method_key[7:].split(','):
                if ':' in part:
                    m, t = part.split(':', 1)
                    custom_thresholds_dict[m.strip()] = t.strip()

        context.update({
            'tool_a': tool_a,
            'tool_b': tool_b_val,
            'date_start': date_start,
            'date_end': date_end,
            'date_column': date_column,
            'selected_metrics': selected_metrics,
            'selected_ao_nr': selected_ao_nr,
            'cleaning_method': cleaning_method,
            'cleaning_method_key': cleaning_method_key,
            'custom_thresholds_dict': custom_thresholds_dict,
        })

        if tool_a and tool_b_val and date_start and date_end and selected_metrics:
            from .data_cleaning import apply_cleaning_method, filter_by_date_range
            from .metrics import compare_two_periods, calculate_rope_rip
            from .visualizations import plot_period_comparison

            d_start = pd.to_datetime(date_start).date()
            d_end = pd.to_datetime(date_end).date()

            filtered_df = df.copy()
            if selected_ao_nr:
                filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

            rows_before_cleaning = len(filtered_df)
            filtered_df = apply_cleaning_method(filtered_df, cleaning_method_key, selected_metrics)
            cycles_removed = rows_before_cleaning - len(filtered_df)
            date_filtered = filter_by_date_range(filtered_df, date_column, d_start, d_end)

            df_p1 = date_filtered[date_filtered['JevArtNr'] == tool_a].copy()
            df_p2 = date_filtered[date_filtered['JevArtNr'] == tool_b_val].copy()

            comparison_df = compare_two_periods(df_p1, df_p2, selected_metrics)
            rope_rip = calculate_rope_rip(df_p1, df_p2)

            chart_html = plot_period_comparison(
                comparison_df,
                selected_art_nr=[tool_a, tool_b_val],
                selected_ao_nr=selected_ao_nr,
                cleaning_method=cleaning_method_key,
                period_a_label=f'Tool {tool_a}',
                period_b_label=f'Tool {tool_b_val}',
            )

            valid_metrics = comparison_df.dropna(subset=['pct_change'])
            if not valid_metrics.empty:
                best_metric = valid_metrics.loc[valid_metrics['pct_change'].abs().idxmax()]
                pct = round(best_metric['pct_change'], 1)
                direction = 'improved' if pct < 0 else 'worsened'
                verdict = f"Tool {tool_b_val} {direction} by {abs(pct)}% on {best_metric['metric']} vs Tool {tool_a}"
            else:
                verdict = "No data found for the selected tools and date range."

            comparison_records = comparison_df.to_dict('records')
            for row in comparison_records:
                row['period_1_avg_fmt'] = seconds_to_minsec(row['period_1_avg'])
                row['period_2_avg_fmt'] = seconds_to_minsec(row['period_2_avg'])

            context.update({
                'comparison': comparison_records,
                'chart_html': chart_html,
                'p1_rows': df_p1.shape[0],
                'p2_rows': df_p2.shape[0],
                'verdict': verdict,
                'rope_rip': rope_rip,
                'cycles_removed': cycles_removed,
            })

            if request.POST.get('save_experiment'):
                from datetime import datetime as _dt
                def _mon(d):
                    try:
                        return _dt.strptime(str(d), '%Y-%m-%d').strftime('%b %Y')
                    except Exception:
                        return str(d)

                if not selected_ao_nr:
                    order_part = None
                elif len(selected_ao_nr) <= 3:
                    order_part = f"Ord. {', '.join(selected_ao_nr)}"
                else:
                    order_part = f"{len(selected_ao_nr)} orders"

                if date_start == date_end:
                    period_part = _mon(date_start)
                else:
                    period_part = f"{_mon(date_start)}–{_mon(date_end)}"

                auto_name = " · ".join(p for p in [f"Tool {tool_a} vs {tool_b_val}", order_part, period_part] if p)

                Experiment.objects.create(
                    name=auto_name,
                    date_column=date_column,
                    comparison_type='tool',
                    tool_b=tool_b_val,
                    period_1_start=d_start,
                    period_1_end=d_end,
                    period_2_start=d_start,
                    period_2_end=d_end,
                    selected_art_nr=[tool_a, tool_b_val],
                    selected_ao_nr=selected_ao_nr if selected_ao_nr else [],
                    selected_metrics=selected_metrics,
                    cleaning_method=cleaning_method_key,
                    results=comparison_records,
                    verdict=verdict,
                    rope_rip=rope_rip,
                    p1_rows=df_p1.shape[0],
                    p2_rows=df_p2.shape[0],
                    cycles_removed=cycles_removed,
                    df_p1_json=df_p1.to_json(date_format='iso'),
                    df_p2_json=df_p2.to_json(date_format='iso'),
                )
                context['saved'] = True

    return render(request, 'analysis/compare_tools.html', context)


def compare_tools_chart(request):
    """
    AJAX endpoint returning a Plotly HTML fragment for the tool-comparison page.

    Mirrors compare_chart() but slices by JevArtNr rather than date range.
    The period_a_label / period_b_label are set to the actual tool numbers so
    chart legends correctly read 'Tool 240771' / 'Tool 240772' rather than
    'Period A' / 'Period B'.
    """
    from django.http import HttpResponse

    df_json = request.session.get('df', None)
    if not df_json:
        return HttpResponse('No data loaded')

    df = pd.read_json(io.StringIO(df_json), convert_dates=False)
    parsed_columns = request.session.get('parsed_columns', [])
    for col in parsed_columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    if 'JevArtNr' in df.columns:
        df['JevArtNr'] = df['JevArtNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)
    if 'JevAoNr' in df.columns:
        df['JevAoNr'] = df['JevAoNr'].apply(lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None)

    date_column = parsed_columns[0] if parsed_columns else None
    chart_type = request.GET.get('chart_type', 'bar')
    tool_a = request.GET.get('tool_a', '').strip()
    tool_b_val = request.GET.get('tool_b', '').strip()
    date_start = request.GET.get('date_start')
    date_end = request.GET.get('date_end')
    selected_metrics = request.GET.getlist('metrics')
    selected_ao_nr = request.GET.getlist('ao_nr')
    cleaning_method = request.GET.get('cleaning_method', 'none')

    if not all([tool_a, tool_b_val, date_start, date_end]):
        return HttpResponse('<p class="text-hgf-muted text-sm p-4">Missing parameters.</p>', status=400)

    from .data_cleaning import apply_cleaning_method, filter_by_date_range

    filtered_df = df.copy()
    if selected_ao_nr:
        filtered_df = filtered_df[filtered_df['JevAoNr'].isin(selected_ao_nr)]

    filtered_df = apply_cleaning_method(filtered_df, cleaning_method, selected_metrics)

    d_start = pd.to_datetime(date_start).date()
    d_end = pd.to_datetime(date_end).date()
    date_filtered = filter_by_date_range(filtered_df, date_column, d_start, d_end)

    df_p1 = date_filtered[date_filtered['JevArtNr'] == tool_a].copy()
    df_p2 = date_filtered[date_filtered['JevArtNr'] == tool_b_val].copy()

    if chart_type in ('scatter', 'line', 'violin', 'control', 'histogram'):
        metric = request.GET.get('metric', selected_metrics[0] if selected_metrics else None)
        if not metric:
            return HttpResponse('<p class="text-hgf-muted text-sm p-4">No metric selected.</p>')

        if chart_type == 'scatter':
            from .visualizations import plot_scatter_with_trend
            chart_html = plot_scatter_with_trend(df_p1, df_p2, metric, date_column, selected_ao_nr=selected_ao_nr)
        elif chart_type == 'line':
            from .visualizations import plot_line_with_slope
            chart_html = plot_line_with_slope(df_p1, df_p2, metric, date_column)
        elif chart_type == 'violin':
            from .visualizations import plot_violin
            chart_html = plot_violin(df_p1, df_p2, metric)
        elif chart_type == 'histogram':
            from .visualizations import plot_histogram
            chart_html = plot_histogram(df_p1, df_p2, metric, period_a_label=f'Tool {tool_a}', period_b_label=f'Tool {tool_b_val}')
        else:
            from .visualizations import plot_control_chart
            chart_html = plot_control_chart(df_p1, df_p2, metric, date_column)
        return HttpResponse(chart_html)

    from .metrics import compare_two_periods
    from .visualizations import plot_period_comparison
    comparison_df = compare_two_periods(df_p1, df_p2, selected_metrics)
    chart_html = plot_period_comparison(
        comparison_df,
        selected_art_nr=[tool_a, tool_b_val],
        selected_ao_nr=selected_ao_nr,
        cleaning_method=cleaning_method,
        chart_type=chart_type,
        include_plotlyjs=False,
        period_a_label=f'Tool {tool_a}',
        period_b_label=f'Tool {tool_b_val}',
    )
    return HttpResponse(chart_html)