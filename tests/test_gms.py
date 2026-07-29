"""Pure-logic tests for mcp_server.tools.gms — no DB/network required."""

from datetime import datetime

import pytest

from mcp_server.tools import gms
from mcp_server.utils.errors import ToolError


# ── _zone ────────────────────────────────────────────────────────────────

def test_zone_k1x_maps_to_zone1():
    assert gms._zone("K18") == "1"


def test_zone_k2x_maps_to_zone2():
    assert gms._zone("K25") == "2"


def test_zone_lowercase_input():
    assert gms._zone("k18") == "1"


def test_zone_unknown_building_raises():
    with pytest.raises(ToolError):
        gms._zone("K30")


# ── _system_from_tag ────────────────────────────────────────────────────

def test_system_from_tag_gms():
    assert gms._system_from_tag("K18_GMS_A1_PRESSURE") == "GMS"


def test_system_from_tag_pmsh():
    assert gms._system_from_tag("K18_PMSH_A1_TEMP") == "PMS"


def test_system_from_tag_pms():
    assert gms._system_from_tag("K18_PMS_A1_TEMP") == "PMS"


def test_system_from_tag_unrecognized_raises():
    with pytest.raises(ToolError):
        gms._system_from_tag("K18_UNKNOWN_A1")


# ── _oracle_table ────────────────────────────────────────────────────────

def test_oracle_table_builds_expected_name():
    assert gms._oracle_table("K18", "GMS") == "FACCIMTAB.ZONE1_K18_GMS"


def test_oracle_table_uppercases_building():
    assert gms._oracle_table("k25", "PMS") == "FACCIMTAB.ZONE2_K25_PMS"


# ── _chunk ───────────────────────────────────────────────────────────────

def test_chunk_default_size():
    chunks = list(gms._chunk([str(i) for i in range(25)]))
    assert [len(c) for c in chunks] == [10, 10, 5]


def test_chunk_custom_size():
    chunks = list(gms._chunk(["a", "b", "c"], size=2))
    assert chunks == [["a", "b"], ["c"]]


def test_chunk_empty_list():
    assert list(gms._chunk([])) == []


# ── _in_clause ───────────────────────────────────────────────────────────

def test_in_clause_builds_bind_placeholders():
    clause, params = gms._in_clause("t", ["A", "B"])
    assert clause == ":t0, :t1"
    assert params == {"t0": "A", "t1": "B"}


def test_in_clause_single_value():
    clause, params = gms._in_clause("t", ["A"])
    assert clause == ":t0"
    assert params == {"t0": "A"}


# ── _parse_dt ────────────────────────────────────────────────────────────

def test_parse_dt_valid():
    dt = gms._parse_dt("2026-07-03 12:30:00", "start_time")
    assert dt == datetime(2026, 7, 3, 12, 30, 0)


def test_parse_dt_invalid_raises():
    with pytest.raises(ToolError):
        gms._parse_dt("2026/07/03", "start_time")


def test_parse_dt_invalid_message_mentions_label():
    with pytest.raises(ToolError, match="start_time"):
        gms._parse_dt("not-a-date", "start_time")


# ── _fetch_points: category/equipment_type filtering (regression) ───────
# Regression lock for commit d8482fb: building+device_id is not unique
# (e.g. K18's A4 can be an air compressor AND a dryer AND a vacuum pump),
# so filtering MUST happen on v_point_detail's own category/equipment_type
# columns. The buggy original JOINed v_equipment_list, which only gated on
# whether a matching equipment row EXISTS and then returned every point row
# for that device_id — mixing all three categories together.

class _FakeCfg:
    @staticmethod
    def resolve_db(name):
        return f"fake://{name}"


def _capture_run_select(monkeypatch):
    captured = {}

    def fake_run_select(dsn, cfg, sql, params=None):
        captured["dsn"] = dsn
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(gms.database, "run_select", fake_run_select)
    return captured


def test_fetch_points_filters_on_v_point_detail_without_join(monkeypatch):
    captured = _capture_run_select(monkeypatch)
    gms._fetch_points(_FakeCfg, "K18", "A4", category="空壓機", equipment_type="離心機")
    sql = captured["sql"]
    assert "JOIN" not in sql.upper(), "JOIN against v_equipment_list reintroduces the mixing bug (d8482fb)"
    assert "v_point_detail" in sql
    assert "category = %(category)s" in sql
    assert "equipment_type = %(equipment_type)s" in sql
    assert captured["params"]["category"] == "空壓機"
    assert captured["params"]["equipment_type"] == "離心機"


def test_fetch_points_omits_filters_when_not_given(monkeypatch):
    captured = _capture_run_select(monkeypatch)
    gms._fetch_points(_FakeCfg, "K18", "A4")
    sql = captured["sql"]
    assert "category" not in sql
    assert "equipment_type" not in sql
    assert captured["params"] == {"building": "K18", "device_id": "A4"}


def test_fetch_points_keyword_uses_like(monkeypatch):
    captured = _capture_run_select(monkeypatch)
    gms._fetch_points(_FakeCfg, "K18", "A4", keyword="壓力")
    assert "point_name LIKE %(keyword)s" in captured["sql"]
    assert captured["params"]["keyword"] == "%壓力%"


# ── _validate_bucket ─────────────────────────────────────────────────────

def test_validate_bucket_accepts_presets():
    for b in ("15m", "1h", "1d"):
        assert gms._validate_bucket(b) == b


def test_validate_bucket_rejects_unknown():
    with pytest.raises(ToolError):
        gms._validate_bucket("30m")


# ── _validate_aggs ───────────────────────────────────────────────────────

def test_validate_aggs_empty_defaults_to_avg():
    assert gms._validate_aggs([]) == ["avg"]


def test_validate_aggs_lowercases_and_dedupes_preserving_order():
    assert gms._validate_aggs(["MAX", "avg", "Max"]) == ["max", "avg"]


def test_validate_aggs_allows_full_menu():
    menu = ["avg", "min", "max", "last", "first", "count"]
    assert gms._validate_aggs(menu) == menu


def test_validate_aggs_rejects_unknown():
    with pytest.raises(ToolError):
        gms._validate_aggs(["median"])


# ── _numeric: VALUE is VARCHAR2 and may hold NULLs/junk ──────────────────
# The historian shares one text VALUE column between digital and analog
# points. Summarising those samples as they arrive is wrong twice over:
# max()/min() over a list containing None raises TypeError, and over plain
# strings they compare lexicographically ("9.5" > "10.2").

def test_numeric_parses_text_samples():
    assert gms._numeric(["7.25", "10.2", "9.5"]) == [7.25, 10.2, 9.5]


def test_numeric_drops_nulls_instead_of_raising():
    # max() over a list holding None used to raise TypeError and kill the tool.
    values = gms._numeric(["7.1", None, "7.3"])
    assert values == [7.1, 7.3]
    assert max(values) == 7.3


def test_numeric_drops_non_numeric_readings():
    assert gms._numeric(["7.1", "OFF", " ", "BAD", "7.3"]) == [7.1, 7.3]


def test_numeric_all_unusable_yields_empty():
    # Caller must report summary=null here, not invent a number.
    assert gms._numeric([None, "OFF", ""]) == []


def test_numeric_comparison_is_not_lexicographic():
    # As strings, max() would pick "9.5"; numerically the answer is 10.2.
    assert max(gms._numeric(["9.5", "10.2"])) == 10.2


def test_numeric_preserves_order_for_latest():
    # "latest" is the last usable sample, so order must survive filtering.
    assert gms._numeric(["1.0", None, "2.0", "OFF"])[-1] == 2.0


# ── _estimate_rows ───────────────────────────────────────────────────────

def test_estimate_rows_hourly_over_two_days():
    start = datetime(2026, 7, 1, 0, 0, 0)
    end = datetime(2026, 7, 3, 0, 0, 0)  # 48h span
    # 2 tags × (48 buckets + 1 inclusive edge) = 98
    assert gms._estimate_rows(2, start, end, "1h") == 98


def test_estimate_rows_daily_scales_down():
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 31)  # 30-day span
    assert gms._estimate_rows(1, start, end, "1d") == 31


# ── _oracle_aggregate: SQL construction ──────────────────────────────────

def _capture_oracle(monkeypatch):
    captured = {}

    def fake_run_select(dsn, cfg, sql, params=None):
        captured["dsn"] = dsn
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(gms.database, "run_select", fake_run_select)
    return captured


def test_oracle_aggregate_selects_and_groups_by_same_bucket_expr(monkeypatch):
    captured = _capture_oracle(monkeypatch)
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 2)
    gms._oracle_aggregate(
        _FakeCfg, "dsn", "FACCIMTAB.ZONE1_K18_GMS", ["T1", "T2"],
        start, end, "1h", ["avg", "max"],
    )
    sql = captured["sql"]
    bucket_expr = gms._BUCKET_SQL["1h"]
    # The bucket expression must appear in both SELECT and GROUP BY, verbatim.
    assert sql.count(bucket_expr) == 2
    assert f"AVG({gms._NUM}) AS AGG_AVG" in sql
    assert f"MAX({gms._NUM}) AS AGG_MAX" in sql
    # WHERE filters the bare column so an index range scan stays usable — the
    # bucket math must never end up inside WHERE.
    where = sql[sql.index("WHERE"):sql.index("GROUP BY")]
    assert "TRUNC" not in where
    assert captured["params"]["start_time"] == start
    assert captured["params"]["end_time"] == end


def test_oracle_aggregate_last_uses_keep_dense_rank(monkeypatch):
    captured = _capture_oracle(monkeypatch)
    gms._oracle_aggregate(
        _FakeCfg, "dsn", "FACCIMTAB.ZONE1_K18_GMS", ["T1"],
        datetime(2026, 7, 1), datetime(2026, 7, 2), "1d", ["last"],
    )
    # 'last' is the newest-in-time value, not the largest: must use KEEP.
    assert "KEEP (DENSE_RANK LAST ORDER BY DATETIME) AS AGG_LAST" in captured["sql"]


# ── numeric conversion: VALUE is VARCHAR2 with NULLs/junk ────────────────
# The historian's VALUE column is text, so aggregating it raw is wrong twice
# over: AVG() raises ORA-01722/ORA-00932, and MIN()/MAX() compare
# lexicographically ("9.5" > "10.2"). Every aggregate must go through the
# numeric conversion, which maps non-numeric samples to NULL.

def test_no_aggregate_touches_the_raw_value_column():
    for name, expr in gms._AGG_SQL.items():
        assert gms._NUM in expr, f"{name} must aggregate the converted value"
        assert "(VALUE)" not in expr.replace(gms._NUM, ""), (
            f"{name} aggregates raw VARCHAR2 VALUE — AVG errors, MIN/MAX "
            f"compare lexicographically"
        )


def test_numeric_conversion_maps_bad_values_to_null():
    # Junk must become NULL (and be skipped by the aggregate), never raise.
    assert "DEFAULT NULL ON CONVERSION ERROR" in gms._NUM


def test_count_counts_usable_numeric_samples_not_raw_rows():
    # COUNT(*) would include NULL/junk rows and overstate bucket coverage.
    assert gms._AGG_SQL["count"] == f"COUNT({gms._NUM})"


# ── 15m bucket must survive a TIMESTAMP column ───────────────────────────

def test_15m_bucket_casts_datetime_to_date():
    # On a TIMESTAMP column, (DATETIME - TRUNC(DATETIME)) yields an INTERVAL,
    # which cannot be multiplied by 1440. CAST keeps the arithmetic numeric.
    expr = gms._BUCKET_SQL["15m"]
    assert "CAST(DATETIME AS DATE)" in expr
    assert "(DATETIME -" not in expr
