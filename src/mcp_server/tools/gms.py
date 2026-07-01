"""GMS 空壓系統點位查詢工具 (Mode A~E).

把「空壓系統點位查詢助理」prompt 中固定的領域邏輯（schema 前綴、Oracle
Zone 判斷、GMS/PMS 系統分類、Tag 分批、3 小時歷史上限、跨庫合併）收斂為
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
from mcp_server.utils.errors import ToolError
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("gms")

# Connection names as configured under [database.connections] in config.toml.
CATALOG_DB = "postgreSQL_CIM"
REALTIME_DB = "oracle"

_MAX_HISTORY = timedelta(hours=3)
_DT_FMT = "%Y-%m-%d %H:%M:%S"


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


# ── PostgreSQL: point lookup shared by list_points, D and E ────────────────

def _fetch_points(
    cfg: "_CfgModule",
    building: str,
    device_id: str,
    equipment_type: str = "",
    keyword: str = "",
    require_scada: bool = False,
) -> list[dict]:
    """Query v_point_detail for a building+device_id.

    building+device_id alone is not guaranteed unique (e.g. two 'A1' units,
    one an air compressor and one a dryer), so equipment_type joins against
    v_equipment_list to disambiguate when provided.
    """
    dsn = cfg.resolve_db(CATALOG_DB)
    where = ["p.building = %(building)s", "p.device_id = %(device_id)s"]
    params: dict[str, Any] = {"building": building, "device_id": device_id}
    join = ""
    if equipment_type:
        join = (
            'JOIN "GMS_agent".v_equipment_list e '
            "ON e.building = p.building AND e.device_id = p.device_id"
        )
        where.append("e.equipment_type = %(equipment_type)s")
        params["equipment_type"] = equipment_type
    if keyword:
        where.append("p.point_name LIKE %(keyword)s")
        params["keyword"] = f"%{keyword}%"
    if require_scada:
        where.append("p.scada_available = TRUE")
        where.append("p.tag_name IS NOT NULL")
    sql = f"""
        SELECT p.point_seq, p.point_name, p.phase, p.unit, p.tag_name,
               p.scada_available, p.remark
        FROM "GMS_agent".v_point_detail p
        {join}
        WHERE {' AND '.join(where)}
        ORDER BY p.point_seq, p.phase
    """
    return database.run_select(dsn, cfg, sql, params)


# ── Oracle: realtime / history value lookup ─────────────────────────────────

def _oracle_latest(cfg: "_CfgModule", oracle_dsn: str, table: str, tags: list[str]) -> list[dict]:
    clause, params = _in_clause("t", tags)
    sql = f"""
        SELECT TAGNAME, VALUE, DATETIME
        FROM {table}
        WHERE TAGNAME IN ({clause})
        AND DATETIME = (
            SELECT MAX(DATETIME) FROM {table}
            WHERE TAGNAME IN ({clause})
        )
        ORDER BY TAGNAME
    """
    return database.run_select(oracle_dsn, cfg, sql, params)


def _oracle_history(
    cfg: "_CfgModule", oracle_dsn: str, table: str, tags: list[str], start: str, end: str
) -> list[dict]:
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
    def gms_list_equipment(building: str = "", category: str = "", floor: str = "") -> str:
        """List compressed-air equipment from the PostgreSQL equipment master (Mode A).

        building, category and floor are all optional exact-match filters;
        omit all to list every active piece of equipment.

        Args:
            building: Building code, e.g. 'K18'. Optional.
            category: Equipment category, e.g. '空壓機'/'乾燥機'/'真空機'. Optional.
            floor:    Floor, e.g. '2F'. Optional.
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
        building: str = "", device_id: str = "", equipment_type: str = "", keyword: str = ""
    ) -> str:
        """List monitoring points and SCADA tags for one piece of equipment (Mode B).

        building+device_id is not guaranteed unique (e.g. two 'A1' units, one an
        air compressor and one a dryer) — pass equipment_type to disambiguate.

        Args:
            building:       Building code, e.g. 'K18'. Required.
            device_id:      Equipment number, e.g. 'B4'. Required.
            equipment_type: Equipment type to disambiguate duplicate device_ids. Optional.
            keyword:        Substring filter on point_name (LIKE). Optional.
        """
        if not building or not device_id:
            raise ToolError("請提供 building 與 device_id。")
        rows = _fetch_points(cfg, building, device_id, equipment_type, keyword)
        if not rows:
            msg = f"查無點位：building='{building}' device_id='{device_id}'"
            if equipment_type:
                msg += f" equipment_type='{equipment_type}'"
            else:
                msg += "。同編號可能對應多種設備，可提供 equipment_type 以精確鎖定"
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
    def gms_realtime_values(
        building: str = "",
        device_id: str = "",
        equipment_type: str = "",
        keyword: str = "",
        tag_names: list[str] = [],
    ) -> str:
        """Get the latest SCADA value for each monitored point of a piece of equipment (Mode D).

        Looks up tags in PostgreSQL, groups them by Oracle system table (GMS/PMS),
        batches tags in groups of 10, and merges the latest Oracle values back
        onto the point metadata.

        Args:
            building:       Building code, e.g. 'K18'. Required.
            device_id:      Equipment number, e.g. 'B4'. Required.
            equipment_type: Equipment type to disambiguate duplicate device_ids. Optional.
            keyword:        Substring filter on point_name (LIKE). Optional, ignored if tag_names is set.
            tag_names:      Exact SCADA tag names to fetch, bypassing the fuzzy keyword match.
                             Use this when tag_name is already known from a prior
                             gms_list_points call — do not paraphrase point_name into keyword. Optional.
        """
        if not building or not device_id:
            raise ToolError("請提供 building 與 device_id。")
        points = _fetch_points(
            cfg, building, device_id, equipment_type,
            "" if tag_names else keyword, require_scada=True,
        )
        if tag_names:
            wanted = set(tag_names)
            points = [p for p in points if p["tag_name"] in wanted]
        if not points:
            raise ToolError("查無有 SCADA 訊號的點位，無法查詢即時值。")

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
        device_id: str = "",
        start_time: str = "",
        end_time: str = "",
        equipment_type: str = "",
        keyword: str = "",
        tag_names: list[str] = [],
    ) -> str:
        """Get a historical value series for each monitored point (Mode E).

        History queries are capped at 3 hours; a longer range is silently
        clamped to the most recent 3 hours of the requested end_time and the
        result reports adjusted=true.

        Args:
            building:       Building code, e.g. 'K18'. Required.
            device_id:      Equipment number, e.g. 'B4'. Required.
            start_time:     Range start, 'YYYY-MM-DD HH:MM:SS'. Required.
            end_time:       Range end, 'YYYY-MM-DD HH:MM:SS'. Required.
            equipment_type: Equipment type to disambiguate duplicate device_ids. Optional.
            keyword:        Substring filter on point_name (LIKE). Optional, ignored if tag_names is set.
            tag_names:      Exact SCADA tag names to fetch, bypassing the fuzzy keyword match.
                             Use this when tag_name is already known from a prior
                             gms_list_points call — do not paraphrase point_name into keyword. Optional.
        """
        if not building or not device_id or not start_time or not end_time:
            raise ToolError("請提供 building、device_id、start_time、end_time。")
        start_dt = _parse_dt(start_time, "start_time")
        end_dt = _parse_dt(end_time, "end_time")
        if start_dt > end_dt:
            raise ToolError("start_time 不可晚於 end_time。")

        adjusted = False
        if end_dt - start_dt > _MAX_HISTORY:
            start_dt = end_dt - _MAX_HISTORY
            adjusted = True

        points = _fetch_points(
            cfg, building, device_id, equipment_type,
            "" if tag_names else keyword, require_scada=True,
        )
        if tag_names:
            wanted = set(tag_names)
            points = [p for p in points if p["tag_name"] in wanted]
        if not points:
            raise ToolError("查無有 SCADA 訊號的點位，無法查詢歷史數據。")

        by_tag = {p["tag_name"]: p for p in points}
        groups = _group_tags_by_table(building, list(by_tag))

        oracle_dsn = cfg.resolve_db(REALTIME_DB)
        series: dict[str, list[dict]] = {tag: [] for tag in by_tag}
        for table, tags in groups.items():
            for batch in _chunk(tags):
                for row in _oracle_history(
                    cfg, oracle_dsn, table, batch,
                    start_dt.strftime(_DT_FMT), end_dt.strftime(_DT_FMT),
                ):
                    series[row["TAGNAME"]].append({"value": row["VALUE"], "datetime": row["DATETIME"]})

        points_out = []
        for tag, meta in by_tag.items():
            pts = series[tag]
            summary = None
            if pts:
                values = [p["value"] for p in pts]
                summary = {"max": max(values), "min": min(values), "latest": pts[-1]["value"]}
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
        return json.dumps(
            {
                "adjusted": adjusted,
                "start_time": start_dt.strftime(_DT_FMT),
                "end_time": end_dt.strftime(_DT_FMT),
                "points": points_out,
            },
            ensure_ascii=False,
            default=str,
        )
