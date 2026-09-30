# -*- coding: utf-8 -*-
from golden_builder.fragments import (
    CX_RATE_AGG, GD_WO, GSGD_NAME_CASE, LS_GROUP_DWMC, PCT_TO_RATIO, TLXL_NAME_CASE, TLXL_ORG_EXCL,
    ZONE_GROUP_TOTAL,
)

P6 = ":period_0,:period_1,:period_2,:period_3,:period_4,:period_5"
YWRQ = "(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:as_of_date AS date))"
YWRQ_YOY = "(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:date_yoy AS date))"
YWRQ_PREV = "(SELECT MAX(ywrq) FROM dm_yyzbtx_gggsgwlsl WHERE ywrq<=CAST(:date_prev AS date))"


def _group_row(cols, ywrq=YWRQ):
    return """
SELECT {cols}
FROM dm_yyzbtx_gggsgwlsl
WHERE dwmc={g} AND ywrq={ywrq}
""".format(cols=cols, g=LS_GROUP_DWMC, ywrq=ywrq)


def ls_group_ytd_rate(spec):
    return _group_row("ljgggsgwlsl{div} AS rate".format(div=PCT_TO_RATIO))


def ls_group_target(spec):
    return _group_row("lslndmb{div} AS target_rate".format(div=PCT_TO_RATIO))


def ls_group_gap(spec):
    return _group_row(
        "ljgggsgwlsl{d} AS rate, lslndmb{d} AS target_rate, "
        "ljgggsgwlsl{d}-lslndmb{d} AS gap, "
        "CASE WHEN ljgggsgwlsl{d}<=lslndmb{d} THEN 1 ELSE 0 END AS met".format(d=PCT_TO_RATIO)
    )


def ls_group_yoy(spec):
    return """
SELECT y.ljgggsgwlsl{d} AS yoy_rate, c.ljgggsgwlsl{d}-y.ljgggsgwlsl{d} AS yoy_change
FROM dm_yyzbtx_gggsgwlsl c
CROSS JOIN dm_yyzbtx_gggsgwlsl y
WHERE c.dwmc={g} AND c.ywrq={yc}
  AND y.dwmc={g} AND y.ywrq={yy}
""".format(d=PCT_TO_RATIO, g=LS_GROUP_DWMC, yc=YWRQ, yy=YWRQ_YOY)


def ls_group_mom(spec):
    return """
SELECT COALESCE(p.bygggsgwlsl, p.ljgggsgwlsl){d} AS prev_rate,
       COALESCE(c.bygggsgwlsl, c.ljgggsgwlsl){d} - COALESCE(p.bygggsgwlsl, p.ljgggsgwlsl){d} AS mom_change
FROM dm_yyzbtx_gggsgwlsl c
CROSS JOIN dm_yyzbtx_gggsgwlsl p
WHERE c.dwmc={g} AND c.ywrq={yc}
  AND p.dwmc={g} AND p.ywrq={yp}
""".format(d=PCT_TO_RATIO, g=LS_GROUP_DWMC, yc=YWRQ, yp=YWRQ_PREV)


def ls_group_last6(spec):
    return """
SELECT CAST(to_char(ywrq,'YYYYMM') AS int) AS "月份(期数)", ljgggsgwlsl{d} AS rate
FROM dm_yyzbtx_gggsgwlsl
WHERE dwmc={g} AND CAST(to_char(ywrq,'YYYYMM') AS int) IN ({p})
ORDER BY 1 ASC
""".format(d=PCT_TO_RATIO, g=LS_GROUP_DWMC, p=P6)


def ls_fixed_202606(spec):
    return """
SELECT ljgggsgwlsl{d} AS rate
FROM dm_yyzbtx_gggsgwlsl
WHERE dwmc={g} AND CAST(to_char(ywrq,'YYYYMM') AS int)=:period
ORDER BY ywrq DESC
LIMIT 1
""".format(d=PCT_TO_RATIO, g=LS_GROUP_DWMC)


def _org(select_sql, where_extra="", order="dwmc ASC", limit=""):
    return """
SELECT dwmc AS "组织", {select_sql}
FROM dm_yyzbtx_gggsgwlsl
WHERE ywrq={y} AND dwmc NOT IN ('环水集团','本地','其他','本部区域分公司')
{extra}
ORDER BY {order}
{limit}
""".format(select_sql=select_sql, y=YWRQ, extra=where_extra, order=order, limit=limit)


def ls_org_rate_target(spec):
    return _org("ljgggsgwlsl{d} AS rate, lslndmb{d} AS target_rate".format(d=PCT_TO_RATIO))


def ls_org_gap(spec):
    return _org("ljgggsgwlsl{d}-lslndmb{d} AS gap".format(d=PCT_TO_RATIO))


def ls_org_yoy(spec):
    return """
SELECT c.dwmc AS "组织", y.ljgggsgwlsl{d} AS yoy_rate, c.ljgggsgwlsl{d}-y.ljgggsgwlsl{d} AS yoy_change
FROM dm_yyzbtx_gggsgwlsl c
JOIN dm_yyzbtx_gggsgwlsl y ON c.dwmc=y.dwmc
WHERE c.ywrq={yc} AND y.ywrq={yy}
  AND c.dwmc NOT IN ('环水集团','本地','其他','本部区域分公司')
ORDER BY c.dwmc ASC
""".format(d=PCT_TO_RATIO, yc=YWRQ, yy=YWRQ_YOY)


def ls_org_mom(spec):
    return """
SELECT c.dwmc AS "组织",
       COALESCE(c.bygggsgwlsl, c.ljgggsgwlsl){d} - COALESCE(p.bygggsgwlsl, p.ljgggsgwlsl){d} AS mom_change
FROM dm_yyzbtx_gggsgwlsl c
JOIN dm_yyzbtx_gggsgwlsl p ON c.dwmc=p.dwmc
WHERE c.ywrq={yc} AND p.ywrq={yp}
  AND c.dwmc NOT IN ('环水集团','本地','其他','本部区域分公司')
ORDER BY c.dwmc ASC
""".format(d=PCT_TO_RATIO, yc=YWRQ, yp=YWRQ_PREV)


def ls_org_bottom3(spec):
    return _org("ljgggsgwlsl{d} AS rate".format(d=PCT_TO_RATIO), order="rate ASC NULLS LAST, dwmc ASC", limit="LIMIT 3")


def ls_org_top3(spec):
    return _org("ljgggsgwlsl{d} AS rate".format(d=PCT_TO_RATIO), order="rate DESC NULLS LAST, dwmc ASC", limit="LIMIT 3")


def ls_org_meet(spec):
    return _org(
        "ljgggsgwlsl{d} AS rate".format(d=PCT_TO_RATIO),
        where_extra="AND ljgggsgwlsl{d}<=lslndmb{d}".format(d=PCT_TO_RATIO),
    )


def ls_org_exceed(spec):
    return _org(
        "ljgggsgwlsl{d} AS rate, ljgggsgwlsl{d}-lslndmb{d} AS gap".format(d=PCT_TO_RATIO),
        where_extra="AND ljgggsgwlsl{d}>lslndmb{d}".format(d=PCT_TO_RATIO),
    )


def ls_org_max_yoy_down(spec):
    return """
SELECT c.dwmc AS org, c.ljgggsgwlsl{d}-y.ljgggsgwlsl{d} AS yoy_change
FROM dm_yyzbtx_gggsgwlsl c
JOIN dm_yyzbtx_gggsgwlsl y ON c.dwmc=y.dwmc
WHERE c.ywrq={yc} AND y.ywrq={yy}
  AND c.dwmc NOT IN ('环水集团','本地','其他','本部区域分公司')
ORDER BY yoy_change ASC NULLS LAST, c.dwmc ASC
LIMIT 1
""".format(d=PCT_TO_RATIO, yc=YWRQ, yy=YWRQ_YOY)


def _one(dwmc, cols="ljgggsgwlsl{d} AS rate, lslndmb{d} AS target_rate"):
    return """
SELECT {cols}
FROM dm_yyzbtx_gggsgwlsl
WHERE dwmc='{dwmc}' AND ywrq={y}
""".format(cols=cols.format(d=PCT_TO_RATIO), dwmc=dwmc, y=YWRQ)


def ls_nanshan(spec):
    return _one("南山分公司")


def ls_baoan(spec):
    return """
SELECT c.ljgggsgwlsl{d} AS rate, c.lslndmb{d} AS target_rate, c.ljgggsgwlsl{d}-y.ljgggsgwlsl{d} AS yoy_change
FROM dm_yyzbtx_gggsgwlsl c
LEFT JOIN dm_yyzbtx_gggsgwlsl y ON y.dwmc=c.dwmc AND y.ywrq={yy}
WHERE c.dwmc='宝安水务集团' AND c.ywrq={yc}
""".format(d=PCT_TO_RATIO, yc=YWRQ, yy=YWRQ_YOY)


def ls_pair_nanshan_baoan(spec):
    return """
SELECT l.ljgggsgwlsl{d} AS left_rate, r.ljgggsgwlsl{d} AS right_rate,
       ABS(l.ljgggsgwlsl{d}-r.ljgggsgwlsl{d}) AS gap
FROM dm_yyzbtx_gggsgwlsl l
CROSS JOIN dm_yyzbtx_gggsgwlsl r
WHERE l.dwmc='南山分公司' AND l.ywrq={y}
  AND r.dwmc='宝安水务集团' AND r.ywrq={y}
""".format(d=PCT_TO_RATIO, y=YWRQ)


def ls_pair_futian_luohu(spec):
    return """
SELECT l.ljgggsgwlsl{d} AS left_rate, l.ljgggsgwlsl{d}-l.lslndmb{d} AS left_gap,
       r.ljgggsgwlsl{d} AS right_rate, r.ljgggsgwlsl{d}-r.lslndmb{d} AS right_gap
FROM dm_yyzbtx_gggsgwlsl l
CROSS JOIN dm_yyzbtx_gggsgwlsl r
WHERE l.dwmc='福田分公司' AND l.ywrq={y}
  AND r.dwmc='罗湖分公司' AND r.ywrq={y}
""".format(d=PCT_TO_RATIO, y=YWRQ)


def ls_longgang_yoy(spec):
    return """
SELECT c.ljgggsgwlsl{d} AS rate, y.ljgggsgwlsl{d} AS yoy_rate
FROM dm_yyzbtx_gggsgwlsl c
LEFT JOIN dm_yyzbtx_gggsgwlsl y ON y.dwmc=c.dwmc AND y.ywrq={yy}
WHERE c.dwmc='龙岗水务集团' AND c.ywrq={yc}
""".format(d=PCT_TO_RATIO, yc=YWRQ, yy=YWRQ_YOY)


def ls_guangming_met(spec):
    return _one("光明水务公司", "ljgggsgwlsl{d} AS rate, CASE WHEN ljgggsgwlsl{d}<=lslndmb{d} THEN 1 ELSE 0 END AS met")


def _unit_loss(order):
    return """
SELECT COALESCE(l.org, p.org) AS "组织",
       l.loss_vol / NULLIF(p.pipe_len_km * EXTRACT(DAY FROM CAST(:end_date AS date)), 0) AS unit_loss
FROM (
  SELECT {g} AS org, SUM(NULLIF(water_leakage,'')::numeric) AS loss_vol
  FROM dwd_gdgl_gd_gsgdwxmx
  WHERE {gd}
  GROUP BY 1
) l
FULL JOIN (
  SELECT {t} AS org, gdcd::numeric AS pipe_len_km
  FROM dm_lszb_tlxl_tj
  WHERE sjsj=(SELECT MAX(sjsj) FROM dm_lszb_tlxl_tj) AND {excl}
) p ON l.org=p.org
ORDER BY unit_loss {order} NULLS LAST, COALESCE(l.org,p.org) ASC
LIMIT 3
""".format(g=GSGD_NAME_CASE, t=TLXL_NAME_CASE, excl=TLXL_ORG_EXCL, order=order, gd=GD_WO)


def ls_unit_pipe_loss_top3_low(spec):
    return _unit_loss("ASC")


def ls_unit_pipe_loss_top3_high(spec):
    return _unit_loss("DESC")


def ls_vs_cx_group(spec):
    return """
SELECT c.rate AS cx_rate, l.ljgggsgwlsl{d} AS ls_rate, c.rate - l.ljgggsgwlsl{d} AS gap
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=:period
    AND f.zoneid IN ({z})
) c
CROSS JOIN dm_yyzbtx_gggsgwlsl l
WHERE l.dwmc={g} AND l.ywrq={y}
""".format(d=PCT_TO_RATIO, rate=CX_RATE_AGG, z=ZONE_GROUP_TOTAL, g=LS_GROUP_DWMC, y=YWRQ)


BUILDERS = {k: v for k, v in globals().items() if k.startswith("ls_")}
