# -*- coding: utf-8 -*-
from golden_builder.fragments import CX_RATE_AGG, CX_RATE_ROW, PCT_TO_RATIO, ZONE_GROUP_TOTAL, ZONE_LOCAL_13, zone_dwmc_as, zone_name_as

NAME = zone_name_as("f.zoneid")
P6 = ":period_0,:period_1,:period_2,:period_3,:period_4,:period_5"
P5 = ":period_0,:period_1,:period_2,:period_3,:period_4"

# 注意：SELECT 列表中的期间参数必须显式 CAST(... AS int)。
# 评测端 pg8000 走服务端参数绑定（$1），裸参数无类型上下文会被推导为 text，
# 与 WHERE 中 numeric 列 businessyearmonth 的推导冲突（42P08 inconsistent types）；
# 黄金生成端 psycopg2 是客户端字面量替换，掩盖了该问题。


def _group(periodtype, extra_select=""):
    extra = (", " + extra_select) if extra_select else ""
    return """
SELECT {rate} AS rate{extra}
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='{pt}' AND f.businessyearmonth=:period
  AND f.zoneid IN ({zones})
""".format(rate=CX_RATE_AGG, extra=extra, pt=periodtype, zones=ZONE_GROUP_TOTAL)


def cx_group_month_rate(spec):
    return _group("SzwgBusiness")


def cx_group_ytd_rate(spec):
    return _group("SzwgBusinessYear")


def cx_group_supply_sales(spec):
    return """
SELECT '环水集团' AS "组织", CAST(:period AS int) AS "月份(期数)",
       SUM(f.totalproduct) AS supply, SUM(f.totalconsume) AS sales
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period
  AND f.zoneid IN ({zones})
""".format(zones=ZONE_GROUP_TOTAL)


def cx_group_diff_vol(spec):
    return """
SELECT SUM(f.totalproduct)-SUM(f.totalconsume) AS diff_vol
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period
  AND f.zoneid IN ({zones})
""".format(zones=ZONE_GROUP_TOTAL)


def cx_group_target_gap(spec):
    return """
SELECT '环水集团' AS "组织", CAST(:period AS int) AS "月份(期数)",
       a.rate, t.cxcndmb{div} AS target_rate, a.rate - t.cxcndmb{div} AS gap
FROM (
  SELECT {rate} AS rate
  FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period
    AND f.zoneid IN ({zones})
) a
LEFT JOIN (
  SELECT cxcndmb FROM dm_yyzbtx_gggsgwlsl
  WHERE dwmc='环水集团' AND ywrq=(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:as_of_date AS date))
) t ON TRUE
""".format(rate=CX_RATE_AGG, div=PCT_TO_RATIO, zones=ZONE_GROUP_TOTAL)


def cx_group_yoy(spec):
    return """
SELECT y.rate AS yoy_rate, c.rate - y.rate AS yoy_change
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid IN ({zones})
) c
CROSS JOIN (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period_yoy AND f.zoneid IN ({zones})
) y
""".format(rate=CX_RATE_AGG, zones=ZONE_GROUP_TOTAL)


def cx_group_mom(spec):
    return """
SELECT '环水集团' AS "组织", CAST(:period AS int) AS "本期月份", CAST(:period_prev AS int) AS "上期月份",
       c.rate AS rate, p.rate AS prev_rate, c.rate - p.rate AS mom_change
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period AND f.zoneid IN ({zones})
) c
CROSS JOIN (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period_prev AND f.zoneid IN ({zones})
) p
""".format(rate=CX_RATE_AGG, zones=ZONE_GROUP_TOTAL)


def _group_series(periodtype, periods):
    return """
SELECT f.businessyearmonth AS "月份(期数)", {rate} AS rate
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='{pt}' AND f.businessyearmonth IN ({periods})
  AND f.zoneid IN ({zones})
GROUP BY 1
ORDER BY 1 ASC, 1 ASC
""".format(rate=CX_RATE_AGG, pt=periodtype, periods=periods, zones=ZONE_GROUP_TOTAL)


def cx_group_last6_month_rate(spec):
    return _group_series("SzwgBusiness", P6)


def cx_group_last6_ytd_rate(spec):
    return _group_series("SzwgBusinessYear", P6)


def cx_group_jan_may_ytd(spec):
    return _group_series("SzwgBusinessYear", P5)


def cx_group_h1_rate(spec):
    return _group("SzwgBusinessYear")


def cx_group_last6_minmax(spec):
    inner = """
SELECT f.businessyearmonth AS ym, {rate} AS rate
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth IN ({p})
  AND f.zoneid IN ({z})
GROUP BY 1
""".format(rate=CX_RATE_AGG, p=P6, z=ZONE_GROUP_TOTAL)
    return """
SELECT
  (SELECT ym FROM ({inner}) s ORDER BY rate DESC NULLS LAST, ym ASC LIMIT 1) AS max_ym,
  (SELECT rate FROM ({inner}) s ORDER BY rate DESC NULLS LAST, ym ASC LIMIT 1) AS max_rate,
  (SELECT ym FROM ({inner}) s ORDER BY rate ASC NULLS LAST, ym ASC LIMIT 1) AS min_ym,
  (SELECT rate FROM ({inner}) s ORDER BY rate ASC NULLS LAST, ym ASC LIMIT 1) AS min_rate
""".format(inner=inner)


def _org_rate(periodtype, order="rate DESC NULLS LAST, f.zoneid ASC", extra="", having=""):
    return """
SELECT {name}, {rate} AS rate {extra}
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='{pt}' AND f.businessyearmonth=:period
  AND f.zoneid IN ({zones})
GROUP BY f.zoneid
{having}
ORDER BY {order}
""".format(name=NAME, rate=CX_RATE_AGG, extra=extra, pt=periodtype, zones=ZONE_LOCAL_13, having=having, order=order)


def cx_org_month_rate(spec):
    return _org_rate("SzwgBusiness")


def cx_org_ytd_rate(spec):
    return _org_rate("SzwgBusinessYear")


def cx_org_volumes(spec):
    return """
SELECT {name}, SUM(f.totalproduct) AS supply, SUM(f.totalconsume) AS sales,
       SUM(f.totalproduct)-SUM(f.totalconsume) AS diff_vol
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period
  AND f.zoneid IN ({zones})
GROUP BY f.zoneid
ORDER BY f.zoneid ASC
""".format(name=NAME, zones=ZONE_LOCAL_13)


def _org_target(filter_sql="", order="a.zoneid ASC"):
    dwmc_expr = zone_dwmc_as("f.zoneid").rsplit(" AS ", 1)[0]
    return """
SELECT a."组织", a.rate, a.target_rate, a.rate - a.target_rate AS gap,
       a.rate / NULLIF(a.target_rate,0) AS completion
FROM (
  SELECT {dwmc_expr} AS "组织", f.zoneid, {rate} AS rate, t.cxcndmb{div} AS target_rate
  FROM dwd_lsxt_fqcxfx f
  LEFT JOIN (
    SELECT dwmc, cxcndmb FROM dm_yyzbtx_gggsgwlsl
    WHERE ywrq=(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:as_of_date AS date))
  ) t ON {dwmc_expr} = t.dwmc
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period
    AND f.zoneid IN ({zones})
  GROUP BY f.zoneid, t.cxcndmb
) a
{filt}
ORDER BY {order}
""".format(dwmc_expr=dwmc_expr, rate=CX_RATE_AGG, div=PCT_TO_RATIO, zones=ZONE_LOCAL_13, filt=filter_sql, order=order)


def cx_org_ytd_target(spec):
    return _org_target()


def cx_org_target_gap(spec):
    return _org_target()


def cx_org_completion(spec):
    return _org_target()


def cx_org_meet_target(spec):
    return _org_target("WHERE a.rate <= a.target_rate")


def cx_org_exceed_target(spec):
    return _org_target("WHERE a.rate > a.target_rate")


def cx_org_yoy(spec):
    return """
SELECT {name}, y.rate AS yoy_rate, c.rate - y.rate AS yoy_change
FROM (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) c
JOIN (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period_yoy AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) y ON c.zoneid=y.zoneid
JOIN dwd_lsxt_fqcxfx f ON f.zoneid=c.zoneid AND f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period
GROUP BY c.zoneid, y.rate, c.rate, f.zoneid
ORDER BY c.zoneid ASC
""".format(name=NAME, rate=CX_RATE_AGG, z=ZONE_LOCAL_13)


def cx_org_mom(spec):
    return """
SELECT {name}, CAST(:period AS int) AS "本期月份", CAST(:period_prev AS int) AS "上期月份",
       p.rate AS prev_rate, c.rate - p.rate AS mom_change
FROM (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) c
JOIN (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period_prev AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) p ON c.zoneid=p.zoneid
JOIN dwd_lsxt_fqcxfx f ON f.zoneid=c.zoneid AND f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period
GROUP BY c.zoneid, p.rate, c.rate, f.zoneid
ORDER BY c.zoneid ASC
""".format(name=NAME, rate=CX_RATE_AGG, z=ZONE_LOCAL_13)


def cx_org_top3_month(spec):
    return _org_rate("SzwgBusiness") + " LIMIT 3"


def cx_org_bottom3_ytd(spec):
    return _org_rate("SzwgBusinessYear", order="rate ASC NULLS LAST, f.zoneid ASC") + " LIMIT 3"


def cx_org_max_yoy_down(spec):
    org_expr = zone_name_as("f.zoneid").rsplit(" AS ", 1)[0]
    return """
SELECT {name} AS org, c.rate - y.rate AS yoy_change
FROM (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) c
JOIN (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period_yoy AND f.zoneid IN ({z})
  GROUP BY f.zoneid
) y ON c.zoneid=y.zoneid
JOIN dwd_lsxt_fqcxfx f ON f.zoneid=c.zoneid AND f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period
GROUP BY c.zoneid, c.rate, y.rate, f.zoneid
ORDER BY yoy_change ASC NULLS LAST, c.zoneid ASC
LIMIT 1
""".format(name=org_expr, rate=CX_RATE_AGG, z=ZONE_LOCAL_13)


def cx_org_max_yoy_up(spec):
    sql = cx_org_max_yoy_down(spec)
    return sql.replace("ORDER BY yoy_change ASC", "ORDER BY yoy_change DESC")


def _pair(periodtype, spec):
    args = spec.get("builder_args") or {}
    left, right = args.get("left", "F1003"), args.get("right", "F1007")
    return """
SELECT l.rate AS left_rate, r.rate AS right_rate, ABS(l.rate - r.rate) AS gap
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='{pt}' AND f.businessyearmonth=:period AND f.zoneid='{left}'
) l
CROSS JOIN (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='{pt}' AND f.businessyearmonth=:period AND f.zoneid='{right}'
) r
""".format(rate=CX_RATE_AGG, pt=periodtype, left=left, right=right)


def cx_pair_ytd(spec):
    return _pair("SzwgBusinessYear", spec)


def cx_pair_month(spec):
    return _pair("SzwgBusiness", spec)


def cx_nanshan_volumes(spec):
    return """
SELECT SUM(f.totalproduct) AS supply, SUM(f.totalconsume) AS sales,
       SUM(f.totalproduct)-SUM(f.totalconsume) AS diff_vol
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth=:period AND f.zoneid='F1003'
"""


def cx_yantian_target(spec):
    return """
SELECT a.rate, t.cxcndmb{div} AS target_rate,
       CASE WHEN a.rate <= t.cxcndmb{div} THEN 1 ELSE 0 END AS met
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid='F1001'
) a
LEFT JOIN (
  SELECT cxcndmb FROM dm_yyzbtx_gggsgwlsl
  WHERE dwmc='盐田分公司' AND ywrq=(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:as_of_date AS date))
) t ON TRUE
""".format(rate=CX_RATE_ROW, div=PCT_TO_RATIO)


def cx_guangming_yoy(spec):
    return """
SELECT c.rate, c.rate - y.rate AS yoy_change
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid='F1005'
) c
CROSS JOIN (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period_yoy AND f.zoneid='F1005'
) y
""".format(rate=CX_RATE_ROW)


def cx_yantian_jan_may_volumes(spec):
    return """
SELECT f.businessyearmonth AS "月份(期数)", SUM(f.totalproduct) AS supply,
       SUM(f.totalconsume) AS sales, SUM(f.totalproduct)-SUM(f.totalconsume) AS diff_vol
FROM dwd_lsxt_fqcxfx f
WHERE f.periodtype='SzwgBusiness' AND f.businessyearmonth IN ({p}) AND f.zoneid='F1001'
GROUP BY 1
ORDER BY 1 ASC
""".format(p=P5)


def cx_futian_jan_jun_mom(spec):
    return """
SELECT ym AS "月份(期数)", rate, rate - prev_rate AS mom_change
FROM (
  SELECT ym, rate, LAG(rate) OVER (ORDER BY ym) AS prev_rate
  FROM (
    SELECT f.businessyearmonth AS ym, {rate} AS rate
    FROM dwd_lsxt_fqcxfx f
    WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth IN ({p}) AND f.zoneid='F1004'
    GROUP BY f.businessyearmonth
  ) a
) s
ORDER BY 1 ASC
""".format(rate=CX_RATE_AGG, p=P6)


def cx_benbu_vs_grouprow(spec):
    return """
SELECT
  (SELECT {rate} FROM dwd_lsxt_fqcxfx f WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid='F100') AS benbu_rate,
  (SELECT {rate} FROM dwd_lsxt_fqcxfx f WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period AND f.zoneid='F1') AS group_rate
""".format(rate=CX_RATE_ROW)


# ===== CX-036 / CX-037（2026-09-21 补录业务核对过的 Q1 / Q3）=====

def cx_org_jan_may_trend(spec):
    """业务核对 Q1：2026 年 1-5 月各单位年累计产销差率月度趋势（透视 5 列 + 集团合计行）。

    口径：SzwgBusinessYear 逐月（红字“默认都是算年累计产销差率”）；
    行=全部 zoneid（含 F1/F100 与 13 组织）+ 集团合计（剔除布吉的 12 组织）。
    """
    name = zone_name_as("t.zoneid")
    pivot = """MAX(CASE WHEN t.businessyearmonth=CAST(:period_0 AS int) THEN t.rate END) AS jan,
       MAX(CASE WHEN t.businessyearmonth=CAST(:period_1 AS int) THEN t.rate END) AS feb,
       MAX(CASE WHEN t.businessyearmonth=CAST(:period_2 AS int) THEN t.rate END) AS mar,
       MAX(CASE WHEN t.businessyearmonth=CAST(:period_3 AS int) THEN t.rate END) AS apr,
       MAX(CASE WHEN t.businessyearmonth=CAST(:period_4 AS int) THEN t.rate END) AS may"""
    pivot_g = pivot.replace("t.", "g.")
    return """
SELECT "组织", jan, feb, mar, apr, may FROM (
  SELECT {name}, {pivot}
  FROM (
    SELECT f.zoneid, f.businessyearmonth, {rate} AS rate
    FROM dwd_lsxt_fqcxfx f
    WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth IN ({p})
    GROUP BY f.zoneid, f.businessyearmonth
  ) t
  GROUP BY t.zoneid
) x
UNION ALL
SELECT '集团合计' AS "组织", jan, feb, mar, apr, may FROM (
  SELECT {pivot_g}
  FROM (
    SELECT f.businessyearmonth, {rate} AS rate
    FROM dwd_lsxt_fqcxfx f
    WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth IN ({p})
      AND f.zoneid IN ({zones})
    GROUP BY f.businessyearmonth
  ) g
) y
ORDER BY 1 ASC
""".format(name=name, pivot=pivot, pivot_g=pivot_g, rate=CX_RATE_AGG, p=P5, zones=ZONE_GROUP_TOTAL)


def cx_org_h1_yoy(spec):
    """业务核对 Q3：各分行 2026 上半年 vs 2025 上半年年累计产销差率同比。

    口径：上半年=6 月年累计期数（R3），2026=:period，2025=:period-100；行=全部 zoneid。
    """
    name = zone_name_as("f.zoneid")
    return """
SELECT {name}, c.rate AS rate_2026, y.rate AS rate_2025, c.rate - y.rate AS yoy_change
FROM (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=CAST(:period AS int)
  GROUP BY f.zoneid
) c
JOIN (
  SELECT f.zoneid, {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=CAST(:period AS int)-100
  GROUP BY f.zoneid
) y ON c.zoneid=y.zoneid
JOIN dwd_lsxt_fqcxfx f ON f.zoneid=c.zoneid
 AND f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=CAST(:period AS int)
GROUP BY c.zoneid, c.rate, y.rate, f.zoneid
ORDER BY yoy_change ASC NULLS LAST, 1 ASC
""".format(name=name, rate=CX_RATE_AGG)


BUILDERS = {k: v for k, v in globals().items() if k.startswith("cx_")}
