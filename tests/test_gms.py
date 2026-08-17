"""Pure-logic tests for mcp_server.tools.gms — no DB/network required."""

from datetime import datetime

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server.tools import gms
from mcp_server.utils.errors import ToolError


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


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
    # category/equipment_type are always SELECTed (needed for batch ambiguity
    # detection); only the WHERE clause must stay free of them when not given.
    where_clause = sql[sql.index("WHERE"):]
    assert "category" not in where_clause
    assert "equipment_type" not in where_clause
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


# ── _list_points_batch: multi-device gms_list_points ─────────────────────
# devices=[{building, device_id, category?, equipment_type?}, ...] resolves
# tags for many devices in one call. It must reuse _fetch_points' own
# category/equipment_type filtering (never a JOIN, per d8482fb) and must not
# silently mix tags from different equipment when a device_id is ambiguous
# and no category/equipment_type was given to disambiguate.

def _stub_fetch_points(responses: dict):
    """Fake _fetch_points keyed by (building, device_id); records every call."""
    calls = []

    def fake(cfg_arg, building, device_id, category="", equipment_type="", keyword="", require_scada=False):
        calls.append(
            {
                "building": building,
                "device_id": device_id,
                "category": category,
                "equipment_type": equipment_type,
                "keyword": keyword,
                "require_scada": require_scada,
            }
        )
        return responses.get((building, device_id), [])

    return fake, calls


def test_list_points_batch_requires_nonempty_devices():
    with pytest.raises(ToolError):
        gms._list_points_batch(_FakeCfg, [], "", 100, "")


def test_list_points_batch_requires_devices_to_be_a_list():
    with pytest.raises(ToolError):
        gms._list_points_batch(_FakeCfg, "not-a-list", "", 100, "")


def test_list_points_batch_requires_building_and_device_id_per_entry():
    with pytest.raises(ToolError, match=r"devices\[0\]"):
        gms._list_points_batch(_FakeCfg, [{"building": "K18"}], "", 100, "")


def test_list_points_batch_resolves_multiple_devices_across_buildings(monkeypatch):
    responses = {
        ("K18", "A1"): [
            {
                "point_name": "出口壓力", "phase": "", "unit": "bar", "tag_name": "K18_GMS_A1_P",
                "category": "空壓機", "equipment_type": "離心機",
            },
        ],
        ("K28", "B2"): [
            {
                "point_name": "溫度", "phase": "", "unit": "C", "tag_name": "K28_GMS_B2_T",
                "category": "乾燥機", "equipment_type": "",
            },
        ],
    }
    fake, calls = _stub_fetch_points(responses)
    monkeypatch.setattr(gms, "_fetch_points", fake)

    result = gms._list_points_batch(
        _FakeCfg,
        [
            {"building": "K18", "device_id": "A1", "category": "空壓機", "equipment_type": "離心機"},
            {"building": "K28", "device_id": "B2"},
        ],
        "",
        100,
        "",
    )

    assert result["requested_count"] == 2
    assert result["matched_device_count"] == 2
    assert result["warnings"] == []
    assert result["count"] == 2
    assert {row["tag_name"] for row in result["items"]} == {"K18_GMS_A1_P", "K28_GMS_B2_T"}
    for row in result["items"]:
        assert "building" in row and "device_id" in row
    # Batch mode only wants tagged points — untagged rows would burn the page
    # budget for nothing.
    assert all(c["require_scada"] is True for c in calls)


def test_list_points_batch_skips_device_with_no_points_and_warns(monkeypatch):
    fake, _ = _stub_fetch_points({})
    monkeypatch.setattr(gms, "_fetch_points", fake)

    result = gms._list_points_batch(
        _FakeCfg, [{"building": "K18", "device_id": "Z9"}], "", 100, ""
    )

    assert result["matched_device_count"] == 0
    assert result["items"] == []
    assert len(result["warnings"]) == 1
    assert "K18/Z9" in result["warnings"][0]


def test_list_points_batch_skips_ambiguous_device_without_category(monkeypatch):
    # Same regression this guards against as d8482fb: K18's A4 matches both
    # an air compressor and a dryer. Without category/equipment_type to
    # disambiguate, the batch must not silently merge their tags — it must
    # skip the device and say why, not guess.
    responses = {
        ("K18", "A4"): [
            {
                "point_name": "壓力", "phase": "", "unit": "bar", "tag_name": "T1",
                "category": "空壓機", "equipment_type": "離心機",
            },
            {
                "point_name": "溫度", "phase": "", "unit": "C", "tag_name": "T2",
                "category": "乾燥機", "equipment_type": "",
            },
        ],
    }
    fake, _ = _stub_fetch_points(responses)
    monkeypatch.setattr(gms, "_fetch_points", fake)

    result = gms._list_points_batch(
        _FakeCfg, [{"building": "K18", "device_id": "A4"}], "", 100, ""
    )

    assert result["matched_device_count"] == 0
    assert result["items"] == []
    assert len(result["warnings"]) == 1
    assert "K18/A4" in result["warnings"][0]


def test_list_points_batch_does_not_flag_ambiguity_when_category_given(monkeypatch):
    responses = {
        ("K18", "A4"): [
            {
                "point_name": "壓力", "phase": "", "unit": "bar", "tag_name": "T1",
                "category": "空壓機", "equipment_type": "離心機",
            },
        ],
    }
    fake, calls = _stub_fetch_points(responses)
    monkeypatch.setattr(gms, "_fetch_points", fake)

    result = gms._list_points_batch(
        _FakeCfg,
        [{"building": "K18", "device_id": "A4", "category": "空壓機"}],
        "",
        100,
        "",
    )

    assert result["matched_device_count"] == 1
    assert result["warnings"] == []
    assert calls[0]["category"] == "空壓機"


# ── gms_list_points tool: single vs. batch mode dispatch ─────────────────

def test_gms_list_points_rejects_mixing_devices_with_single_mode_params():
    mcp = FastMCP(name="test")
    gms.register(mcp, _FakeCfg)
    tool = _get_tool(mcp, "gms_list_points")

    with pytest.raises(ToolError, match="互斥"):
        tool(building="K18", devices=[{"building": "K18", "device_id": "A1"}])


def test_gms_list_points_batch_mode_dispatches_to_list_points_batch(monkeypatch):
    mcp = FastMCP(name="test")
    gms.register(mcp, _FakeCfg)
    tool = _get_tool(mcp, "gms_list_points")

    monkeypatch.setattr(
        gms,
        "_list_points_batch",
        lambda cfg_arg, devices, keyword, limit, cursor: {"sentinel": True, "devices": devices},
    )

    result = tool(devices=[{"building": "K18", "device_id": "A1"}])
    assert result == {"sentinel": True, "devices": [{"building": "K18", "device_id": "A1"}]}


# ── gms_history_aggregate: to_file raises the row ceiling but keeps one ──
# _MAX_INLINE_ROWS protects context from flooding; to_file=True skips that
# risk but not the risk of an unbounded number of serialized Oracle queries
# with no statement timeout of its own — _MAX_FILE_ROWS caps that instead.

def test_gms_history_aggregate_inline_mode_rejects_before_touching_postgres(monkeypatch):
    mcp = FastMCP(name="test")
    gms.register(mcp, _FakeCfg)
    tool = _get_tool(mcp, "gms_history_aggregate")

    def fail_if_called(*a, **k):
        raise AssertionError("inline mode must reject before touching Postgres/Oracle")

    monkeypatch.setattr(gms, "_fetch_points_by_tags", fail_if_called)

    with pytest.raises(ToolError, match=f"{gms._MAX_INLINE_ROWS} 上限"):
        tool(
            building="K18",
            start_time="2026-07-01 00:00:00",
            end_time="2026-07-31 00:00:00",
            tag_names=[f"T{i}" for i in range(100)],
            bucket="15m",
            to_file=False,
        )


def test_gms_history_aggregate_to_file_allows_sizes_over_the_inline_cap(monkeypatch):
    mcp = FastMCP(name="test")
    gms.register(mcp, _FakeCfg)
    tool = _get_tool(mcp, "gms_history_aggregate")

    calls = []
    monkeypatch.setattr(
        gms, "_fetch_points_by_tags", lambda *a, **k: calls.append(1) or []
    )

    # Same size that inline mode rejects above — to_file must proceed past
    # the estimate gate and reach the (stubbed) Postgres lookup.
    with pytest.raises(ToolError, match="查無對應的點位"):
        tool(
            building="K18",
            start_time="2026-07-01 00:00:00",
            end_time="2026-07-31 00:00:00",
            tag_names=[f"T{i}" for i in range(100)],
            bucket="15m",
            to_file=True,
        )
    assert calls == [1]


def test_gms_history_aggregate_to_file_still_capped_at_max_file_rows(monkeypatch):
    mcp = FastMCP(name="test")
    gms.register(mcp, _FakeCfg)
    tool = _get_tool(mcp, "gms_history_aggregate")

    def fail_if_called(*a, **k):
        raise AssertionError("to_file mode must still reject an unbounded estimate")

    monkeypatch.setattr(gms, "_fetch_points_by_tags", fail_if_called)

    with pytest.raises(ToolError, match=f"{gms._MAX_FILE_ROWS}"):
        tool(
            building="K18",
            start_time="2020-01-01 00:00:00",
            end_time="2030-01-01 00:00:00",
            tag_names=[f"T{i}" for i in range(600)],
            bucket="15m",
            to_file=True,
        )
