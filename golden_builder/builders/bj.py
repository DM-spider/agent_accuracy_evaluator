# -*- coding: utf-8 -*-
from golden_builder.fragments import BURST_FILTER, GD_WO, GSGD_NAME_CASE, ORG_MAP_SQL

ORG = ORG_MAP_SQL + ' AS "组织"'
WO_ORG = GSGD_NAME_CASE + ' AS "组织"'
NF = "srctype='NightFlow'"
WD = "warndate::date>=CAST(:start_date AS date) AND warndate::date<CAST(:next_month_start AS date)"
# 工单表期间口径：create_time 半开区间（GD-001~GD-011）
CT = GD_WO


def bj_nightflow_cnt(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_lsxt_bj WHERE {nf} AND {wd}".format(nf=NF, wd=WD)


def bj_nightflow_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt
FROM dwd_lsxt_bj
WHERE {nf} AND {wd}
GROUP BY 1
ORDER BY cnt DESC, 1 ASC
""".format(org=ORG, nf=NF, wd=WD)


def bj_nightflow_top10(spec):
    return """
SELECT dmaid AS "DMA编码", MAX(dmaname) AS "DMA名称", {org}, COUNT(*) AS cnt
FROM dwd_lsxt_bj
WHERE {nf} AND {wd} AND dmaid IS NOT NULL
GROUP BY dmaid, {g}
ORDER BY cnt DESC, dmaid ASC
LIMIT 10
""".format(org=ORG, g=ORG_MAP_SQL, nf=NF, wd=WD)


def bj_handle_rate(spec):
    return """
SELECT COUNT(*) FILTER (WHERE handled=true AND handldate IS NOT NULL)*1.0/NULLIF(COUNT(*),0) AS rate
FROM dwd_lsxt_bj WHERE {nf} AND {wd}
""".format(nf=NF, wd=WD)


def bj_handle_split(spec):
    return """
SELECT COUNT(*) AS total_cnt,
       COUNT(*) FILTER (WHERE handled=true AND handldate IS NOT NULL) AS handled_cnt,
       COUNT(*) FILTER (WHERE NOT (handled=true AND handldate IS NOT NULL)) AS open_cnt
FROM dwd_lsxt_bj WHERE {nf} AND {wd}
""".format(nf=NF, wd=WD)


def bj_overdue(spec):
    return """
SELECT COUNT(*) AS cnt
FROM dwd_lsxt_bj
WHERE {nf} AND {wd}
  AND NOT (handled=true AND handldate IS NOT NULL)
""".format(nf=NF, wd=WD)


def bj_yesterday_list(spec):
    return """
SELECT dmaid AS "DMA编码", MAX(dmaname) AS "DMA名称", {org}, COUNT(*) AS cnt
FROM dwd_lsxt_bj
WHERE {nf} AND warndate::date>=CAST(:start_date AS date) AND warndate::date<CAST(:next_month_start AS date)
  AND dmaid IS NOT NULL
GROUP BY dmaid, {g}
ORDER BY cnt DESC, dmaid ASC
""".format(org=ORG, g=ORG_MAP_SQL, nf=NF)


def bj_burst_cnt(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_gsgdwxmx WHERE {ct} AND {b}".format(ct=CT, b=BURST_FILTER)


def bj_burst_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {ct} AND {b} AND bz_bm IS NOT NULL
GROUP BY 1
ORDER BY cnt DESC, 1 ASC
""".format(org=WO_ORG, ct=CT, b=BURST_FILTER)


def bj_burst_caliber(spec):
    return """
SELECT CASE
         WHEN caliber_n < 400 THEN '不足400'
         WHEN caliber_n < 600 THEN '400至不足600'
         WHEN caliber_n < 1000 THEN '600至不足1000'
         ELSE '1000毫米及以上'
       END AS "管径分段", COUNT(*) AS cnt
FROM (
  SELECT NULLIF(caliber,'')::numeric AS caliber_n
  FROM dwd_gdgl_gd_gsgdwxmx
  WHERE {ct} AND {b} AND NULLIF(caliber,'') IS NOT NULL
) s
GROUP BY 1
ORDER BY 1 ASC
""".format(ct=CT, b=BURST_FILTER)


def bj_burst_avg_caliber(spec):
    return """
SELECT AVG(NULLIF(caliber,'')::numeric) AS avg_caliber,
       COUNT(*) FILTER (WHERE NULLIF(caliber,'') IS NOT NULL) AS sample_cnt
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {ct} AND {b}
""".format(ct=CT, b=BURST_FILTER)


def bj_repeat_pipe(spec):
    return """
SELECT pipe_number AS "管道编码", {org}, MAX(detailedaddress) AS "地址", COUNT(*) AS cnt
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {ct} AND pipe_number IS NOT NULL AND pipe_number<>''
GROUP BY pipe_number, {g}
HAVING COUNT(*) > 2
ORDER BY cnt DESC, pipe_number ASC
""".format(org=WO_ORG, g=GSGD_NAME_CASE, ct=CT)


def _top5(table, code_col):
    return """
SELECT {code} AS "设施编码", COUNT(*) AS cnt
FROM {table}
WHERE {ct} AND {code} IS NOT NULL AND CAST({code} AS text)<>''
GROUP BY 1
ORDER BY cnt DESC, 1 ASC
LIMIT 5
""".format(code=code_col, table=table, ct=CT)


def bj_top5_pipe(spec):
    return _top5("dwd_gdgl_gd_gsgdwxmx", "pipe_number")


def bj_top5_valve(spec):
    return _top5("dwd_gdgl_gd_fmwxgdmx", "valve_number")


def bj_top5_hydrant(spec):
    return _top5("dwd_gdgl_gd_xfswxmx", "fire_hydrant_number")


def bj_burst_caliber_org(spec):
    """爆管按管径分段 × 单位，下钻三级部门（R10）。
    """
    return """
SELECT seg AS "管径分段", "组织", MAX(level3) AS "三级部门", COUNT(*) AS cnt
FROM (
  SELECT CASE WHEN NULLIF(caliber,'')::numeric IS NULL THEN '未知'
              WHEN NULLIF(caliber,'')::numeric>=1000 THEN '1000毫米及以上'
              WHEN NULLIF(caliber,'')::numeric>=600 THEN '600至不足1000'
              WHEN NULLIF(caliber,'')::numeric>=400 THEN '400至不足600'
              ELSE '不足400' END AS seg,
         {org}, level3_name AS level3
  FROM dwd_gdgl_gd_gsgdwxmx
  WHERE {ct} AND {b}
) t
GROUP BY 1, 2
ORDER BY 1 ASC, cnt DESC
""".format(org=WO_ORG, ct=CT, b=BURST_FILTER)


# ===== BJ-016（2026-09-21 补录业务核对过的 Q19）：漏损预警超时（10 天）未处理 =====

def bj_overdue_10d(spec):
    """业务核对口径（25 题 Q19）：不限 srctype；超时=预警产生 10 天后仍未处理。

    以月末（:end_date）为观察点回推 10 天，等价于业务核对题里的 warndate < 月末-10 天。
    """
    return """
SELECT COUNT(*) AS total_cnt,
       COUNT(*) FILTER (WHERE NOT handled) AS unhandled_cnt,
       COUNT(*) FILTER (WHERE NOT handled
              AND warndate::date < CAST(:end_date AS date) - 10) AS overdue_cnt
FROM dwd_lsxt_bj
WHERE warndate::date>=CAST(:start_date AS date) AND warndate::date<CAST(:next_month_start AS date)
"""


BUILDERS = {k: v for k, v in globals().items() if k.startswith("bj_")}
