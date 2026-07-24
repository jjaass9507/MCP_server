"""GMS 空壓系統點位查詢工具 (Mode A~E).

把「空壓系統點位查詢助理」prompt 中固定的領域邏輯（schema 前綴、Oracle
Zone 判斷、GMS/PMS 系統分類、Tag 分批、1 天歷史上限、跨庫合併）收斂為
少數參數化、唯讀工具，取代 agent 每次自行拼接 SQL 的作法。

- 點位主檔在 PostgreSQL（連線名 CATALOG_DB），即時/歷史數值在 Oracle
  （連線名 REALTIME_DB）；連線本身沿用 config.toml 既有設定，不新增
  任何 config 機制。
- 通用的 db_query / db_table_schema 仍保留，供臨時或探索性查詢使用。
"""

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from mcp_server.tools import database
from mcp_server.utils import download_server, export as export_utils
from mcp_server.utils.errors import ToolError
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("gms")

# Connection names as configured under [database.connections] in config.toml.
CATALOG_DB = "postgreSQL_CIM"
REALTIME_DB = "oracle"

_MAX_HISTORY = timedelta(days=1)
_DT_FMT = "%Y-%m-%d %H:%M:%S"

# Aggregate (Mode F) — server-side downsampling so long-range analysis returns
# one row per time bucket instead of one per raw per-minute sample.
#
# Bucket expressions must be IDENTICAL between SELECT and GROUP BY, so each is
# stored once here and reused. The per-N-minute form works because 15 divides
# a day evenly; '1h'/'1d' use TRUNC for a lighter plan.
_BUCKET_SQL = {
    "15m": "TRUNC(DATETIME) + FLOOR((DATETIME - TRUNC(DATETIME)) * 1440 / 15) * 15 / 1440",
    "1h": "TRUNC(DATETIME, 'HH')",
    "1d": "TRUNC(DATETIME)",
}
_BUCKET_SECONDS = {"15m": 900, "1h": 3600, "1d": 86400}

# How to collapse the raw samples inside one bucket. 'last'/'first' need the
# VALUE of the newest/oldest row in the bucket — that's the KEEP ... DENSE_RANK
# idiom, done inside the same GROUP BY (a plain MAX would give the largest
# value, not the last-in-time one). All keys are a fixed whitelist, so they are
# safe to interpolate into SQL directly.
_AGG_SQL = {
    "avg": "AVG(VALUE)",
    "min": "MIN(VALUE)",
    "max": "MAX(VALUE)",
    "last": "MAX(VALUE) KEEP (DENSE_RANK LAST ORDER BY DATETIME)",
    "first": "MAX(VALUE) KEEP (DENSE_RANK FIRST ORDER BY DATETIME)",
    "count": "COUNT(*)",
}
_DEFAULT_AGGS = ["avg"]

# Inline responses embed one object per (tag, bucket); past this many the
# aggregate is refused unless to_file=True, to keep from flooding context.
_MAX_INLINE_ROWS = 5000


# ── fixed domain logic ─────────────────────────────────────────────────────

def _zone(building: str) -> str:
    """Map a building code to its Oracle zone: K1x -> '1', K2x -> '2'."""
    b = building.upper()
    if b.startswith("K1"):
        return "1"
    if b.startswith("K2"):
        return "2"
    raise ToolError(
        f"無法判斷廠棟 '{building}' 所屬 Zone（僅支援 K1x → ZONE1, K2x → ZONE2）。"
    )


def _system_from_tag(tag: str) -> str:
    """Classify a SCADA tag into its Oracle system table: GMS or PMS."""
    if "_GMS_" in tag:
        return "GMS"
    if "_PMSH_" in tag or "_PMS_" in tag:
        return "PMS"
    raise ToolError(
        f"無法判斷 Tag '{tag}' 所屬系統（需含 _GMS_ 或 _PMSH_/_PMS_）。"
    )


def _oracle_table(building: str, system: str) -> str:
    zone = _zone(building)
    return f"FACCIMTAB.ZONE{zone}_{building.upper()}_{system}"


def _chunk(seq: list[str], size: int = 10):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _in_clause(prefix: str, values: list[str]) -> tuple[str, dict]:
    names = [f"{prefix}{i}" for i in range(len(values))]
    clause = ", ".join(f":{n}" for n in names)
    return clause, dict(zip(names, values))


def _parse_dt(s: str, label: str) -> datetime:
    try:
        return datetime.strptime(s, _DT_FMT)
    except ValueError as e:
        raise ToolError(
            f"{label} 格式錯誤：'{s}'，需為 'YYYY-MM-DD HH:MM:SS'。"
        ) from e


def _validate_bucket(bucket: str) -> str:
    if bucket not in _BUCKET_SQL:
        raise ToolError(
            f"不支援的 bucket '{bucket}'，可用：{', '.join(_BUCKET_SQL)}。"
        )
    return bucket


def _validate_aggs(aggs: list[str]) -> list[str]:
    """Lower-case, de-duplicate (order-preserving) and whitelist the requested
    aggregations; empty falls back to _DEFAULT_AGGS."""
    if not aggs:
        return list(_DEFAULT_AGGS)
    result: list[str] = []
    for a in aggs:
        key = a.lower()
        if key not in _AGG_SQL:
            raise ToolError(
                f"不支援的彙整方式 '{a}'，可用：{', '.join(_AGG_SQL)}。"
            )
        if key not in result:
            result.append(key)
    return result


def _estimate_rows(n_tags: int, start: datetime, end: datetime, bucket: str) -> int:
    """Upper bound on inline objects: n_tags × number of buckets in the range."""
    span = (end - start).total_seconds()
    buckets = int(span // _BUCKET_SECONDS[bucket]) + 1
    return n_tags * buckets


# ── PostgreSQL: point lookup shared by list_points, D and E ────────────────

def _fetch_points(
    cfg: "_CfgModule",
    building: str,
    device_id: str,
    category: str = "",
    equipment_type: str = "",
    keyword: str = "",
    require_scada: bool = False,
) -> list[dict]:
    """Query v_point_detail for a building+device_id.

    building+device_id alone is not guaranteed unique (e.g. two 'A4' units,
    one an air compressor and one a dryer). v_point_detail carries its own
    category (broad class, e.g. 空壓機/乾燥機/真空機) and equipment_type
    (specific type, e.g. 離心機/變頻螺旋機) columns, so filtering happens
    directly on this table — no join against v_equipment_list needed (a
    join on building+device_id alone cannot discriminate between equipment
    sharing the same device_id, since it only gates on whether a matching
    equipment_list row exists, not which point rows belong to it).
    """
    dsn = cfg.resolve_db(CATALOG_DB)
    where = ["building = %(building)s", "device_id = %(device_id)s"]
    params: dict[str, Any] = {"building": building, "device_id": device_id}
    if category:
        where.append("category = %(category)s")
        params["category"] = category
    if equipment_type:
        where.append("equipment_type = %(equipment_type)s")
        params["equipment_type"] = equipment_type
    if keyword:
        where.append("point_name LIKE %(keyword)s")
        params["keyword"] = f"%{keyword}%"
    if require_scada:
        where.append("scada_available = TRUE")
        where.append("tag_name IS NOT NULL")
    sql = f"""
        SELECT point_seq, point_name, phase, unit, tag_name,
               scada_available, remark
        FROM "GMS_agent".v_point_detail
        WHERE {' AND '.join(where)}
        ORDER BY point_seq, phase
    """
    return database.run_select(dsn, cfg, sql, params)


def _fetch_points_by_tags(cfg: "_CfgModule", building: str, tag_names: list[str]) -> list[dict]:
    """Look up point metadata (point_name/phase/unit) for already-known tag_names.

    Used by gms_realtime_values/gms_history_values, which only consume tags
    already resolved via gms_list_points — no device_id/category/equipment_type
    filtering here, since the Oracle side only has TAGNAME/DATETIME/VALUE and
    these tools' sole job is to fetch values for a confirmed tag list.
    """
    dsn = cfg.resolve_db(CATALOG_DB)
    sql = """
        SELECT point_seq, point_name, phase, unit, tag_name, scada_available, remark
        FROM "GMS_agent".v_point_detail
        WHERE building = %(building)s AND tag_name = ANY(%(tag_names)s)
        ORDER BY point_seq, phase
    """
    return database.run_select(dsn, cfg, sql, {"building": building, "tag_names": tag_names})


# ── Oracle: realtime / history value lookup ─────────────────────────────────

def _oracle_latest(cfg: "_CfgModule", oracle_dsn: str, table: str, tags: list[str]) -> list[dict]:
    """Fetch the latest row per tag.

    Uses a per-tag ROW_NUMBER() rather than a single global MAX(DATETIME):
    tags in the same batch can update at different frequencies, so a slower
    tag may have no row at the batch's global-max timestamp and would
    otherwise come back null even though it has an older latest value.
    """
    clause, params = _in_clause("t", tags)
    sql = f"""
        SELECT TAGNAME, VALUE, DATETIME FROM (
            SELECT TAGNAME, VALUE, DATETIME,
                   ROW_NUMBER() OVER (PARTITION BY TAGNAME ORDER BY DATETIME DESC) rn
            FROM {table}
            WHERE TAGNAME IN ({clause})
        )
        WHERE rn = 1
        ORDER BY TAGNAME
    """
    return database.run_select(oracle_dsn, cfg, sql, params)


def _oracle_history(
    cfg: "_CfgModule", oracle_dsn: str, table: str, tags: list[str],
    start: datetime, end: datetime,
) -> list[dict]:
    """start/end are bound as native datetime objects (DB_TYPE_TIMESTAMP), not
    strings — binding strings forces Oracle to implicitly parse them via
    NLS_DATE_FORMAT, which raises ORA-01843 whenever that session setting
    doesn't match our 'YYYY-MM-DD HH:MM:SS' format.
    """
    clause, params = _in_clause("t", tags)
    params["start_time"] = start
    params["end_time"] = end
    sql = f"""
        SELECT TAGNAME, VALUE, DATETIME
        FROM {table}
        WHERE TAGNAME IN ({clause})
        AND DATETIME >= :start_time AND DATETIME <= :end_time
        ORDER BY TAGNAME, DATETIME
    """
    return database.run_select(oracle_dsn, cfg, sql, params)


def _oracle_aggregate(
    cfg: "_CfgModule", oracle_dsn: str, table: str, tags: list[str],
    start: datetime, end: datetime, bucket: str, agg_list: list[str],
) -> list[dict]:
    """Bucket the raw series in Oracle and return one row per (tag, bucket).

    The bucket expression is pushed only into SELECT/GROUP BY, never into
    WHERE — WHERE still filters on the bare DATETIME column so an index range
    scan on (TAGNAME, DATETIME) stays usable. Rows come back with keys
    TAGNAME, BUCKET_TIME, and AGG_<NAME> per selected aggregation.
    """
    clause, params = _in_clause("t", tags)
    params["start_time"] = start
    params["end_time"] = end
    bucket_expr = _BUCKET_SQL[bucket]
    select_aggs = ",\n               ".join(
        f"{_AGG_SQL[a]} AS AGG_{a.upper()}" for a in agg_list
    )
    sql = f"""
        SELECT TAGNAME,
               {bucket_expr} AS BUCKET_TIME,
               {select_aggs}
        FROM {table}
        WHERE TAGNAME IN ({clause})
        AND DATETIME >= :start_time AND DATETIME <= :end_time
        GROUP BY TAGNAME, {bucket_expr}
        ORDER BY TAGNAME, BUCKET_TIME
    """
    return database.run_select(oracle_dsn, cfg, sql, params)


def _group_tags_by_table(building: str, tags: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for tag in tags:
        system = _system_from_tag(tag)
        table = _oracle_table(building, system)
        groups.setdefault(table, []).append(tag)
    return groups


# ── tool registration ────────────────────────────────────────────────────────

def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def gms_list_equipment(
        building: str = "", category: str = "", equipment_type: str = "", floor: str = ""
    ) -> str:
        """List compressed-air equipment from the PostgreSQL equipment master (Mode A).

        building, category, equipment_type and floor are all optional
        exact-match filters; omit all to list every active piece of equipment.

        Args:
            building:       Building code, e.g. 'K18'. Optional.
            category:       Broad equipment category, e.g. '空壓機'/'乾燥機'/'真空機'. Optional.
            equipment_type: Specific equipment type, e.g. '離心機'/'變頻螺旋機'. Optional.
            floor:          Floor, e.g. '2F'. Optional.
        """
        dsn = cfg.resolve_db(CATALOG_DB)
        where = ["is_active = TRUE"]
        params: dict[str, Any] = {}
        if building:
            where.append("building = %(building)s")
            params["building"] = building
        if category:
            where.append("category = %(category)s")
            params["category"] = category
        if equipment_type:
            where.append("equipment_type = %(equipment_type)s")
            params["equipment_type"] = equipment_type
        if floor:
            where.append("floor = %(floor)s")
            params["floor"] = floor
        sql = f"""
            SELECT floor, category, equipment_type, brand, model, device_id
            FROM "GMS_agent".v_equipment_list
            WHERE {' AND '.join(where)}
            ORDER BY floor, category, equipment_type, device_id
        """
        rows = database.run_select(dsn, cfg, sql, params)
        return json.dumps(rows, ensure_ascii=False, default=str)

    @mcp.tool()
    def gms_list_points(
        building: str = "",
        device_id: str = "",
        category: str = "",
        equipment_type: str = "",
        keyword: str = "",
    ) -> str:
        """List monitoring points and SCADA tags for one piece of equipment (Mode B).

        building+device_id is not guaranteed unique (e.g. two 'A1' units, one an
        air compressor and one a dryer) — pass category and/or equipment_type
        to disambiguate.

        Args:
            building:       Building code, e.g. 'K18'. Required.
            device_id:      Equipment number, e.g. 'B4'. Required.
            category:       Broad equipment category, e.g. '空壓機'/'乾燥機'/'真空機'. Optional.
            equipment_type: Specific equipment type, e.g. '離心機'/'變頻螺旋機'. Optional.
            keyword:        Substring filter on point_name (LIKE). Optional.
        """
        if not building or not device_id:
            raise ToolError("請提供 building 與 device_id。")
        rows = _fetch_points(cfg, building, device_id, category, equipment_type, keyword)
        if not rows:
            msg = f"查無點位：building='{building}' device_id='{device_id}'"
            if category:
                msg += f" category='{category}'"
            if equipment_type:
                msg += f" equipment_type='{equipment_type}'"
            if not category and not equipment_type:
                msg += "。同編號可能對應多種設備，可提供 category 或 equipment_type 以精確鎖定"
            raise ToolError(msg + "。")
        return json.dumps(rows, ensure_ascii=False, default=str)

    @mcp.tool()
    def gms_list_pipe_points(building: str = "", system_name: str = "") -> str:
        """List pipe-network monitoring points for a building (Mode C).

        Args:
            building:    Building code, e.g. 'K18'. Required.
            system_name: Pipe system, one of 'HCDA' / 'LCDA' / 'HVAC'. Required.
        """
        if not building or not system_name:
            raise ToolError("請提供 building 與 system_name（HCDA / LCDA / HVAC）。")
        dsn = cfg.resolve_db(CATALOG_DB)
        sql = """
            SELECT floor, location, point_name, unit, tag_name, scada_available
            FROM "GMS_agent".pipe_point
            WHERE building = %(building)s AND system_name = %(system_name)s
              AND scada_available = TRUE
            ORDER BY floor, location, point_name
        """
        rows = database.run_select(
            dsn, cfg, sql, {"building": building, "system_name": system_name}
        )
        return json.dumps(rows, ensure_ascii=False, default=str)

    @mcp.tool()
    def gms_realtime_values(building: str = "", tag_names: list[str] = []) -> str:
        """Get the latest SCADA value for a list of already-known tags (Mode D).

        This tool only fetches Oracle values for tag_names you already have —
        it does not search by device_id/category/equipment_type/keyword. Call
        gms_list_points first to resolve the tag_name(s) you need. Groups tags
        by Oracle system table (GMS/PMS), batches in groups of 10, and merges
        the latest Oracle values with point metadata (point_name/phase/unit)
        looked up from PostgreSQL by tag_name.

        Args:
            building:  Building code, e.g. 'K18'. Required — used to resolve
                       the Oracle zone/system table for each tag.
            tag_names: Exact SCADA tag names to fetch, e.g. from a prior
                       gms_list_points call. Required.
        """
        if not building or not tag_names:
            raise ToolError("請提供 building 與 tag_names（請先呼叫 gms_list_points 取得確切的 tag_name）。")
        points = _fetch_points_by_tags(cfg, building, tag_names)
        if not points:
            raise ToolError("查無對應的點位中繼資料，請確認 tag_names 是否正確。")

        by_tag = {p["tag_name"]: p for p in points}
        groups = _group_tags_by_table(building, list(by_tag))

        oracle_dsn = cfg.resolve_db(REALTIME_DB)
        values: dict[str, dict] = {}
        for table, tags in groups.items():
            for batch in _chunk(tags):
                for row in _oracle_latest(cfg, oracle_dsn, table, batch):
                    values[row["TAGNAME"]] = row

        result = []
        for tag, meta in by_tag.items():
            v = values.get(tag)
            result.append(
                {
                    "point_name": meta["point_name"],
                    "phase": meta["phase"],
                    "unit": meta["unit"],
                    "tag_name": tag,
                    "value": v["VALUE"] if v else None,
                    "datetime": v["DATETIME"] if v else None,
                }
            )
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def gms_history_values(
        building: str = "",
        start_time: str = "",
        end_time: str = "",
        tag_names: list[str] = [],
        to_file: bool = False,
    ) -> str:
        """Get a historical value series for a list of already-known tags (Mode E).

        This tool only fetches Oracle values for tag_names you already have —
        it does not search by device_id/category/equipment_type/keyword. Call
        gms_list_points first to resolve the tag_name(s) you need.

        History queries are capped at 1 day; a longer range is silently
        clamped to the most recent 1 day of the requested end_time and the
        result reports adjusted=true.

        Set to_file=true when querying multiple tags and/or a long time
        window, especially if you plan to chart the data afterwards: instead
        of embedding every (tag, datetime, value) sample inline, the series
        is written to one CSV file (columns: tag_name, point_name, phase,
        unit, datetime, value — all tags combined) under the server's export
        directory, and the response keeps only adjusted/start_time/end_time,
        each tag's point_name/phase/unit/tag_name/summary, and the file info.
        Do NOT read_file() that CSV back into context — pass its path to a
        downstream file-processing tool or external workflow instead. With
        to_file=false (default) behavior is unchanged: series stays embedded
        per tag.

        If [export] serve_downloads is enabled in config.toml, result.file
        also includes "download_url": a time-limited (default 60 minutes),
        unguessable HTTP URL for this CSV, meant to be handed to a
        *different* machine's MCP server so it can stream the file over
        HTTP instead of needing local filesystem access to this machine.
        Do NOT GET that URL yourself to pull the contents back into context.

        Args:
            building:   Building code, e.g. 'K18'. Required — used to resolve
                        the Oracle zone/system table for each tag.
            start_time: Range start, 'YYYY-MM-DD HH:MM:SS'. Required.
            end_time:   Range end, 'YYYY-MM-DD HH:MM:SS'. Required.
            tag_names:  Exact SCADA tag names to fetch, e.g. from a prior
                        gms_list_points call. Required.
            to_file:    Write the series to a CSV file instead of embedding
                        it in the response (see above). Default: False.
        """
        if not building or not start_time or not end_time or not tag_names:
            raise ToolError("請提供 building、start_time、end_time、tag_names（請先呼叫 gms_list_points 取得確切的 tag_name）。")
        start_dt = _parse_dt(start_time, "start_time")
        end_dt = _parse_dt(end_time, "end_time")
        if start_dt > end_dt:
            raise ToolError("start_time 不可晚於 end_time。")

        adjusted = False
        if end_dt - start_dt > _MAX_HISTORY:
            start_dt = end_dt - _MAX_HISTORY
            adjusted = True

        points = _fetch_points_by_tags(cfg, building, tag_names)
        if not points:
            raise ToolError("查無對應的點位中繼資料，請確認 tag_names 是否正確。")

        by_tag = {p["tag_name"]: p for p in points}
        groups = _group_tags_by_table(building, list(by_tag))

        oracle_dsn = cfg.resolve_db(REALTIME_DB)
        series: dict[str, list[dict]] = {tag: [] for tag in by_tag}
        for table, tags in groups.items():
            for batch in _chunk(tags):
                for row in _oracle_history(
                    cfg, oracle_dsn, table, batch, start_dt, end_dt,
                ):
                    series[row["TAGNAME"]].append({"value": row["VALUE"], "datetime": row["DATETIME"]})

        points_out = []
        csv_rows: list[dict] = []
        for tag, meta in by_tag.items():
            pts = series[tag]
            summary = None
            if pts:
                values = [p["value"] for p in pts]
                summary = {"max": max(values), "min": min(values), "latest": pts[-1]["value"]}
            if to_file:
                for p in pts:
                    csv_rows.append({
                        "tag_name": tag,
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "datetime": p["datetime"],
                        "value": p["value"],
                    })
                points_out.append(
                    {
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "tag_name": tag,
                        "summary": summary,
                    }
                )
            else:
                points_out.append(
                    {
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "tag_name": tag,
                        "series": pts,
                        "summary": summary,
                    }
                )

        result: dict[str, Any] = {
            "adjusted": adjusted,
            "start_time": start_dt.strftime(_DT_FMT),
            "end_time": end_dt.strftime(_DT_FMT),
            "points": points_out,
        }
        if to_file:
            export_dir = cfg.get_export_dir()
            columns = ["tag_name", "point_name", "phase", "unit", "datetime", "value"]
            path = export_utils.export_csv(export_dir, columns, csv_rows)
            result["file"] = {
                "path": str(path),
                "row_count": len(csv_rows),
                "size_kb": round(path.stat().st_size / 1024, 2),
            }
            if cfg.get_download_config()["serve_downloads"]:
                result["file"]["download_url"] = download_server.register_file(path)
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def gms_history_aggregate(
        building: str = "",
        start_time: str = "",
        end_time: str = "",
        tag_names: list[str] = [],
        bucket: str = "1h",
        aggs: list[str] = [],
        to_file: bool = False,
    ) -> str:
        """Get a downsampled historical series for long-range analysis (Mode F).

        Oracle stores one raw sample per minute, so pulling a raw series over
        weeks/months (gms_history_values) is far too heavy. This tool pushes a
        time-bucket GROUP BY down to Oracle and returns one row per bucket
        instead of per raw sample — the response size scales with the number
        of buckets, not the length of the range. Prefer it over
        gms_history_values whenever the range is longer than a day or you only
        need trends/statistics rather than every minute. Like the other value
        tools it only fetches tags you already have — call gms_list_points
        first to resolve exact tag_name(s).

        Args:
            building:   Building code, e.g. 'K18'. Required — used to resolve
                        the Oracle zone/system table for each tag.
            start_time: Range start, 'YYYY-MM-DD HH:MM:SS'. Required.
            end_time:   Range end, 'YYYY-MM-DD HH:MM:SS'. Required.
            tag_names:  Exact SCADA tag names, e.g. from gms_list_points. Required.
            bucket:     Bucket width, one of '15m' / '1h' / '1d'. Default '1h'.
            aggs:       How to collapse samples within each bucket; multi-select
                        from 'avg' / 'min' / 'max' / 'last' / 'first' / 'count'
                        (each becomes a column in every bucket). 'last'/'first'
                        are the newest/oldest value in the bucket by time, not
                        the largest/smallest. Default ['avg'].
            to_file:    Write the buckets to a CSV instead of embedding them
                        (columns: tag_name, point_name, phase, unit, time, then
                        one column per agg). Required for large results — an
                        inline request estimated to exceed a few thousand
                        (tag × bucket) rows is refused with a hint to set this
                        True or use a coarser bucket. Do NOT read that CSV back
                        into context; hand its path/download_url downstream.
                        Default False.
        """
        if not building or not start_time or not end_time or not tag_names:
            raise ToolError("請提供 building、start_time、end_time、tag_names（請先呼叫 gms_list_points 取得確切的 tag_name）。")
        start_dt = _parse_dt(start_time, "start_time")
        end_dt = _parse_dt(end_time, "end_time")
        if start_dt > end_dt:
            raise ToolError("start_time 不可晚於 end_time。")
        bucket = _validate_bucket(bucket)
        agg_list = _validate_aggs(aggs)

        if not to_file:
            estimated = _estimate_rows(len(tag_names), start_dt, end_dt, bucket)
            if estimated > _MAX_INLINE_ROWS:
                raise ToolError(
                    f"預估回傳約 {estimated} 列（tag 數 × 桶數）超過 {_MAX_INLINE_ROWS} 上限，"
                    f"請改用更粗的 bucket 或設 to_file=True。"
                )

        points = _fetch_points_by_tags(cfg, building, tag_names)
        if not points:
            raise ToolError("查無對應的點位中繼資料，請確認 tag_names 是否正確。")

        by_tag = {p["tag_name"]: p for p in points}
        groups = _group_tags_by_table(building, list(by_tag))

        oracle_dsn = cfg.resolve_db(REALTIME_DB)
        series: dict[str, list[dict]] = {tag: [] for tag in by_tag}
        for table, tags in groups.items():
            for batch in _chunk(tags):
                for row in _oracle_aggregate(
                    cfg, oracle_dsn, table, batch, start_dt, end_dt, bucket, agg_list,
                ):
                    point = {"time": row["BUCKET_TIME"]}
                    for a in agg_list:
                        point[a] = row[f"AGG_{a.upper()}"]
                    series[row["TAGNAME"]].append(point)

        points_out = []
        csv_rows: list[dict] = []
        for tag, meta in by_tag.items():
            buckets = series[tag]
            if to_file:
                for b in buckets:
                    csv_rows.append({
                        "tag_name": tag,
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "time": b["time"],
                        **{a: b[a] for a in agg_list},
                    })
                points_out.append(
                    {
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "tag_name": tag,
                        "bucket_count": len(buckets),
                    }
                )
            else:
                points_out.append(
                    {
                        "point_name": meta["point_name"],
                        "phase": meta["phase"],
                        "unit": meta["unit"],
                        "tag_name": tag,
                        "series": buckets,
                    }
                )

        result: dict[str, Any] = {
            "bucket": bucket,
            "aggs": agg_list,
            "start_time": start_dt.strftime(_DT_FMT),
            "end_time": end_dt.strftime(_DT_FMT),
            "points": points_out,
        }
        if to_file:
            export_dir = cfg.get_export_dir()
            columns = ["tag_name", "point_name", "phase", "unit", "time", *agg_list]
            path = export_utils.export_csv(export_dir, columns, csv_rows)
            result["file"] = {
                "path": str(path),
                "row_count": len(csv_rows),
                "size_kb": round(path.stat().st_size / 1024, 2),
            }
            if cfg.get_download_config()["serve_downloads"]:
                result["file"]["download_url"] = download_server.register_file(path)
        return json.dumps(result, ensure_ascii=False, default=str)
