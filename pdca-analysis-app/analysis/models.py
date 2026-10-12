"""
Data model for the PDCA analysis application.

A single Experiment record captures everything needed to reproduce and audit a
PDCA comparison without the original production file. Aggregated metric results
are stored as JSON for fast table display; the raw period DataFrames are stored
as JSON blobs so charts can be regenerated at any time from the detail view.
"""

from django.db import models


class Experiment(models.Model):
    """
    Represents one saved PDCA comparison — either a period-vs-period or a
    tool-vs-tool analysis.

    The record is self-contained: it holds the parameter selections made by the
    analyst, the computed results, and the raw cycle data needed for charting.
    This means experiments remain accessible even after the source file is
    removed from the session.

    comparison_type distinguishes the two analysis modes:
        'period' — one tool compared across two time periods (baseline vs post-change)
        'tool'   — two different tools compared over the same date range
    """

    # ---- Identity -------------------------------------------------------
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    # ---- Source configuration -------------------------------------------
    # Which column was used as the datetime axis (e.g. 'CycleDatetime')
    machine = models.CharField(max_length=100, blank=True)
    date_column = models.CharField(max_length=100, blank=True)

    # ---- Analysis mode --------------------------------------------------
    # 'period' or 'tool' — determines how period_1/2 fields are interpreted
    comparison_type = models.CharField(max_length=10, default='period')
    # For tool comparisons: the tool number used as the second group (Tool B)
    tool_b = models.CharField(max_length=50, blank=True, default='')

    # ---- Date ranges ----------------------------------------------------
    # Period comparison: p1 = baseline window, p2 = post-change window
    # Tool comparison:   p1 == p2 (same shared window for both tools)
    period_1_start = models.DateField(null=True, blank=True)
    period_1_end   = models.DateField(null=True, blank=True)
    period_2_start = models.DateField(null=True, blank=True)
    period_2_end   = models.DateField(null=True, blank=True)

    # ---- Filter selections ----------------------------------------------
    # JevArtNr values selected at save time; for tool comparisons this is [tool_a, tool_b]
    selected_art_nr  = models.JSONField(default=list, blank=True)
    # JevAoNr values (order numbers) selected at save time
    selected_ao_nr   = models.JSONField(default=list, blank=True)
    # Metric column names included in this comparison
    selected_metrics = models.JSONField(default=list, blank=True)
    # Key of the cleaning method applied (e.g. 'per_product_top5')
    cleaning_method  = models.CharField(max_length=100, default='none')

    # ---- Computed results -----------------------------------------------
    # List of per-metric dicts: {metric, period_1_avg, period_2_avg, pct_change, ...}
    results = models.JSONField(default=list)
    # Human-readable summary sentence, e.g. "CureDuration improved by 4.2% in Period B"
    verdict = models.CharField(max_length=500, blank=True)
    # Analyst-written notes, editable post-save
    notes = models.TextField(blank=True)
    # Rope-rip rate comparison dict, or None if Feeding_cnt column is absent
    rope_rip = models.JSONField(default=None, null=True, blank=True)

    # ---- Cycle exclusions -----------------------------------------------
    # CycleCnt values manually excluded by the analyst via the scatter chart
    excluded_cycles = models.JSONField(default=list, blank=True)
    # When True, excluded cycles still count toward the rope-rip rate
    keep_rope_rip = models.BooleanField(default=False)

    # ---- Row counts (post-cleaning, post-exclusion) ---------------------
    p1_rows = models.IntegerField(default=0)
    p2_rows = models.IntegerField(default=0)
    cycles_removed = models.IntegerField(default=0)

    # ---- Raw period DataFrames (JSON) -----------------------------------
    # Stored so per-cycle charts (scatter, violin, control) can be regenerated
    # without the source file. Empty string means charts are unavailable.
    df_p1_json = models.TextField(blank=True, default='')
    df_p2_json = models.TextField(blank=True, default='')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']
