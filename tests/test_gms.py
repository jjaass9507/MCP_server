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
