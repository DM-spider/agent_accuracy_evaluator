# -*- coding: utf-8 -*-
from golden_builder.fragments import (CX_RATE_AGG, GD_L6, GD_WO, GSGD_NAME_CASE, PCT_TO_RATIO,
                          TLXL_NAME_CASE, TLXL_ORG_EXCL, zone_name_as)

ORG = GSGD_NAME_CASE + ' AS "组织"'
TLXL_ORG = TLXL_NAME_CASE + ' AS "组织"'
CUR = "sjsj=(SELECT MAX(sjsj) FROM dm_lszb_tlxl_tj WHERE sjsj>=CAST(:year_start AS date) AND sjsj<CAST(:end_date AS date))"
PREV = "sjsj=(SELECT MAX(sjsj) FROM dm_lszb_tlxl_tj WHERE sjsj>=CAST(:year_start AS date) - INTERVAL '1 year' AND sjsj<CAST(:end_date AS date) - INTERVAL '1 year')"
WO = "create_time>=CAST(:start_date AS timestamp) AND create_time<CAST(:next_month_start AS timestamp)"


def jl_group_repair_cnt(spec):
    return "SELECT SUM(gsgdwxgds) AS cnt FROM dm_lszb_tlxl_tj WHERE {c} AND {e}".format(c=CUR, e=TLXL_ORG_EXCL)


def jl_group_repair_per_km(spec):
    return "SELECT SUM(gsgdwxgds)*1.0/NULLIF(SUM(gdcd),0) AS per_km FROM dm_lszb_tlxl_tj WHERE {c} AND {e}".format(c=CUR, e=TLXL_ORG_EXCL)


def jl_group_repair_yoy(spec):
    return """
SELECT p.cnt AS yoy_cnt, (c.cnt-p.cnt)*1.0/NULLIF(p.cnt,0) AS yoy_change
FROM (SELECT SUM(gsgdwxgds) AS cnt FROM dm_lszb_tlxl_tj WHERE {c} AND {e}) c
CROSS JOIN (SELECT SUM(gsgdwxgds) AS cnt FROM dm_lszb_tlxl_tj WHERE {p} AND {e}) p
""".format(c=CUR, p=PREV, e=TLXL_ORG_EXCL)


def jl_group_detect_cnt(spec):
    return "SELECT SUM(jlwxgds) AS cnt FROM dm_lszb_tlxl_tj WHERE {c} AND {e}".format(c=CUR, e=TLXL_ORG_EXCL)


def jl_group_detect_per_km(spec):
    return "SELECT SUM(jlwxgds)*1.0/NULLIF(SUM(gdcd),0) AS per_km FROM dm_lszb_tlxl_tj WHERE {c} AND {e}".format(c=CUR, e=TLXL_ORG_EXCL)


def jl_group_efficiency(spec):
    return """
SELECT COUNT(*) FILTER (WHERE finish_time IS NOT NULL)*1.0/NULLIF(COUNT(*),0) AS rate
FROM dwd_gdgl_gd_jlmx
WHERE {wo}
""".format(wo=WO)


def jl_group_efficiency_yoy(spec):
    return """
SELECT p.rate AS yoy_rate, c.rate-p.rate AS yoy_change
FROM (
  SELECT COUNT(*) FILTER (WHERE finish_time IS NOT NULL)*1.0/NULLIF(COUNT(*),0) AS rate
  FROM dwd_gdgl_gd_jlmx WHERE create_time>=CAST(:year_start AS timestamp) AND create_time<CAST(:end_date AS timestamp)
) c
CROSS JOIN (
  SELECT COUNT(*) FILTER (WHERE finish_time IS NOT NULL)*1.0/NULLIF(COUNT(*),0) AS rate
  FROM dwd_gdgl_gd_jlmx
  WHERE create_time>=CAST(:year_start AS timestamp)-INTERVAL '1 year'
    AND create_time<CAST(:end_date AS timestamp)-INTERVAL '1 year'
) p
"""


def jl_org_repair(spec):
    return """
SELECT {org}, gsgdwxgds AS cnt, gsgdwxgds*1.0/NULLIF(gdcd,0) AS per_km
FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY 1 ASC
""".format(org=TLXL_ORG, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_detect(spec):
    return """
SELECT {org}, jlwxgds AS cnt, jlwxgds*1.0/NULLIF(gdcd,0) AS per_km
FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY 1 ASC
""".format(org=TLXL_ORG, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_efficiency(spec):
    return """
SELECT c.dwmc AS "组织", c.jlxl{d} AS rate, c.jlxl{d}-p.jlxl{d} AS yoy_change
FROM dm_lszb_tlxl_tj c
LEFT JOIN dm_lszb_tlxl_tj p ON p.dwmc=c.dwmc AND {p}
WHERE {c} AND c.dwmc NOT IN ('本地','其他')
ORDER BY 1 ASC
""".format(d=PCT_TO_RATIO, p=PREV.replace("sjsj=", "p.sjsj=", 1), c=CUR.replace("sjsj=", "c.sjsj=", 1))


def jl_org_eff_top3(spec):
    return """
SELECT {org}, jlxl{d} AS rate FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY rate DESC NULLS LAST, dwmc ASC
LIMIT 3
""".format(org=TLXL_ORG, d=PCT_TO_RATIO, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_eff_bottom3(spec):
    return jl_org_eff_top3(spec).replace("DESC", "ASC")


def jl_org_repair_top3(spec):
    return """
SELECT {org}, gsgdwxgds AS cnt FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY gsgdwxgds DESC NULLS LAST, dwmc ASC
LIMIT 3
""".format(org=TLXL_ORG, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_repair_per_km_top3(spec):
    return """
SELECT {org}, gsgdwxgds*1.0/NULLIF(gdcd,0) AS per_km FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY per_km DESC NULLS LAST, dwmc ASC
LIMIT 3
""".format(org=TLXL_ORG, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_detect_top3(spec):
    return """
SELECT {org}, jlwxgds AS cnt FROM dm_lszb_tlxl_tj
WHERE {c} AND {e}
ORDER BY jlwxgds DESC NULLS LAST, dwmc ASC
LIMIT 3
""".format(org=TLXL_ORG, c=CUR, e=TLXL_ORG_EXCL)


def jl_org_leakage(spec):
    return """
SELECT {org}, SUM(NULLIF(water_leakage,'')::numeric) AS loss_vol
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, gd=GD_WO)


def jl_hydrant_cnt(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_xfswxmx WHERE {gd}".format(gd=GD_WO)


def jl_org_hydrant(spec):
    return """
SELECT {org}, COUNT(*) AS cnt,
       SUM(NULLIF(discharge_water,'')::numeric) AS discharge,
       SUM(NULLIF(water_leakage,'')::numeric) AS loss_vol
FROM dwd_gdgl_gd_xfswxmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, gd=GD_WO)


def jl_latest10(spec):
    return """
SELECT id AS "工单编号", {org}, title AS "标题", create_time AS "创建时间"
FROM dwd_gdgl_gd_jlmx
WHERE {wo}
ORDER BY create_time DESC, id ASC
LIMIT 10
""".format(org=ORG, wo=WO)


def jl_group_response(spec):
    return """
SELECT AVG(EXTRACT(EPOCH FROM (arrive_time-create_time))/3600.0) AS hours
FROM dwd_gdgl_gd_jlmx
WHERE {wo} AND arrive_time IS NOT NULL
""".format(wo=WO)


def jl_org_response(spec):
    return """
SELECT {org}, AVG(EXTRACT(EPOCH FROM (arrive_time-create_time))/3600.0) AS hours
FROM dwd_gdgl_gd_jlmx
WHERE {wo} AND arrive_time IS NOT NULL
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, wo=WO)


def jl_org_complete(spec):
    return """
SELECT {org}, COUNT(*) AS total_cnt,
       COUNT(*) FILTER (WHERE finish_time IS NOT NULL) AS fin_cnt,
       COUNT(*) FILTER (WHERE finish_time IS NOT NULL)*1.0/NULLIF(COUNT(*),0) AS rate
FROM dwd_gdgl_gd_jlmx
WHERE {wo}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, wo=WO)


def jl_arrive_intime(spec):
    return """
SELECT COUNT(*) FILTER (WHERE arrive_intime='是')*1.0
       / NULLIF(COUNT(*) FILTER (WHERE arrive_time IS NOT NULL),0) AS rate
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {gd}
""".format(gd=GD_WO)


def jl_repair_8h(spec):
    return """
SELECT COUNT(*) FILTER (
         WHERE start_repair_time IS NOT NULL AND start_repair_time<>'' AND finish_time IS NOT NULL
           AND (finish_time - start_repair_time::timestamp) <= INTERVAL '8 hours'
       )*1.0
       / NULLIF(COUNT(*) FILTER (
         WHERE start_repair_time IS NOT NULL AND start_repair_time<>'' AND finish_time IS NOT NULL
       ),0) AS rate
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {gd}
""".format(gd=GD_WO)


def jl_pair_nanshan_baoan(spec):
    return """
SELECT
  l.gsgdwxgds AS left_repair, l.jlwxgds AS left_detect, l.jlxl{d} AS left_rate,
  r.gsgdwxgds AS right_repair, r.jlwxgds AS right_detect, r.jlxl{d} AS right_rate
FROM dm_lszb_tlxl_tj l
CROSS JOIN dm_lszb_tlxl_tj r
WHERE {cl} AND l.dwmc='南山分公司'
  AND {cr} AND r.dwmc='宝安水务集团'
""".format(d=PCT_TO_RATIO, cl=CUR.replace("sjsj=", "l.sjsj=", 1), cr=CUR.replace("sjsj=", "r.sjsj=", 1))


# ===== JL-026~JL-035：维修工单基础统计，期间 create_time 半开区间 =====

def jl_repair_cnt(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_gsgdwxmx WHERE {gd}".format(gd=GD_WO)


def jl_repair_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, gd=GD_WO)


def jl_repair_last6(spec):
    return """
SELECT to_char(create_time, 'YYYYMM')::int AS ym, COUNT(*) AS cnt
FROM dwd_gdgl_gd_gsgdwxmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(gd=GD_L6)


def jl_valve_cnt(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_fmwxgdmx WHERE {gd}".format(gd=GD_WO)


def jl_valve_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt
FROM dwd_gdgl_gd_fmwxgdmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, gd=GD_WO)


def jl_hydrant_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt
FROM dwd_gdgl_gd_xfswxmx
WHERE {gd}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, gd=GD_WO)


def jl_three_split(spec):
    return """
SELECT (SELECT COUNT(*) FROM dwd_gdgl_gd_gsgdwxmx WHERE {gd}) AS repair_cnt,
       (SELECT COUNT(*) FROM dwd_gdgl_gd_fmwxgdmx WHERE {gd}) AS valve_cnt,
       (SELECT COUNT(*) FROM dwd_gdgl_gd_xfswxmx WHERE {gd}) AS hydrant_cnt
""".format(gd=GD_WO)


def jl_three_total(spec):
    return """
SELECT (SELECT COUNT(*) FROM dwd_gdgl_gd_gsgdwxmx WHERE {gd})
     + (SELECT COUNT(*) FROM dwd_gdgl_gd_fmwxgdmx WHERE {gd})
     + (SELECT COUNT(*) FROM dwd_gdgl_gd_xfswxmx WHERE {gd}) AS cnt
""".format(gd=GD_WO)


def jl_repair_mom(spec):
    return """
SELECT c.cnt AS cnt, p.cnt AS prev_cnt, (c.cnt-p.cnt)*1.0/NULLIF(p.cnt,0) AS mom_change
FROM (
  SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_gsgdwxmx
  WHERE {gd}
) c
CROSS JOIN (
  SELECT COUNT(*) AS cnt FROM dwd_gdgl_gd_gsgdwxmx
  WHERE create_time>=CAST(:prev_month_start AS timestamp) AND create_time<CAST(:start_date AS timestamp)
) p
""".format(gd=GD_WO)


# ===== JL-036（2026-09-21 补录业务核对过的 Q30）：产销差率 × 检漏工单数对照 =====

def jl_org_cx_detect(spec):
    """单月产销差率（fqcxfx SzwgBusiness）与检漏工单数（jlmx create_time）按组织 FULL JOIN。

    业务核对口径（25 题 Q30）：产销差率=2026-06 单月；工单=2026-07 创建（工单类型=检漏，
    jlmx 单表即检漏）；无“每单平均产销差”；工单缺失组织补 0。
    """
    name = zone_name_as("f.zoneid")
    return """
SELECT COALESCE(r."组织", o."组织") AS "组织", r.rate AS rate, COALESCE(o.cnt, 0) AS detect_cnt
FROM (
  SELECT {name}, {rate} AS rate
  FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=CAST(:period AS int)
  GROUP BY f.zoneid
) r
FULL JOIN (
  SELECT {org}, COUNT(*) AS cnt
  FROM dwd_gdgl_gd_jlmx
  WHERE create_time>=CAST(:start_date AS date) + INTERVAL '1 month'
    AND create_time<CAST(:start_date AS date) + INTERVAL '2 months'
  GROUP BY 1
) o ON r."组织" = o."组织"
ORDER BY rate DESC NULLS LAST, 1 ASC
""".format(name=name, rate=CX_RATE_AGG, org=ORG)


BUILDERS = {k: v for k, v in globals().items() if k.startswith("jl_")}
