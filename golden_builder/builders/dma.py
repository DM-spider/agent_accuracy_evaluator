# -*- coding: utf-8 -*-
from golden_builder.fragments import (CX_RATE_AGG, DMA_INVALID, DMA_IN_USE, ORG_MAP_SQL, WLLS_DAY,
                          WLLS_RATE_AGG, zone_name_as)

ORG = ORG_MAP_SQL + ' AS "组织"'
WLLS_RANGE = "taskstart::date >= CAST(:start_date AS date) AND taskstart::date < CAST(:next_month_start AS date)"


def dma_inuse_total(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_lsxt_dma WHERE {w}".format(w=DMA_IN_USE)


def dma_inuse_by_org(spec):
    return """
SELECT {org}, COUNT(*) AS cnt, COUNT(*)*1.0/NULLIF(SUM(COUNT(*)) OVER (),0) AS share
FROM dwd_lsxt_dma
WHERE {w}
GROUP BY 1
ORDER BY cnt DESC, 1 ASC
""".format(org=ORG, w=DMA_IN_USE)


def dma_valid_total(spec):
    return """
SELECT COUNT(DISTINCT w.dmaid) AS cnt
FROM dwd_lsxt_wlls w
JOIN dwd_lsxt_dma d ON d.dmaid=w.dmaid AND {w}
WHERE {day} AND {rng}
""".format(w=DMA_IN_USE, day=WLLS_DAY, rng=WLLS_RANGE)


def dma_invalid_total(spec):
    return "SELECT COUNT(*) AS cnt FROM dwd_lsxt_dma WHERE {w}".format(w=DMA_INVALID)


def dma_invalid_share(spec):
    return """
SELECT invalid*1.0/NULLIF(valid+invalid,0) AS share
FROM (
  SELECT COUNT(*) FILTER (WHERE {inv}) AS invalid,
         COUNT(*) FILTER (WHERE {use}) AS valid
  FROM dwd_lsxt_dma
) s
""".format(inv=DMA_INVALID, use=DMA_IN_USE)


def dma_invalid_mom(spec):
    return """
SELECT COUNT(*) AS cnt, NULL::int AS prev_cnt, NULL::int AS delta
FROM dwd_lsxt_dma WHERE {w}
""".format(w=DMA_INVALID)


def dma_org_invalid(spec):
    return """
SELECT {org},
       COUNT(*) AS total_cnt,
       COUNT(*) FILTER (WHERE {inv}) AS invalid_cnt,
       COUNT(*) FILTER (WHERE {inv})*1.0/NULLIF(COUNT(*),0) AS share
FROM dwd_lsxt_dma
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG, inv=DMA_INVALID)


def dma_org_valid_rate(spec):
    return """
SELECT {org}, COUNT(DISTINCT w.dmaid) AS cnt, {rate} AS rate
FROM dwd_lsxt_wlls w
JOIN dwd_lsxt_dma d ON d.dmaid=w.dmaid AND {use}
WHERE {day} AND {rng}
GROUP BY 1
ORDER BY 1 ASC
""".format(org=ORG_MAP_SQL.replace("bz_bm", "w.bz_bm").replace("zonecode", "w.zonecode") + ' AS "组织"',
           rate=WLLS_RATE_AGG, use=DMA_IN_USE, day=WLLS_DAY, rng=WLLS_RANGE)


def dma_group_rate(spec):
    return """
SELECT {rate} AS rate
FROM dwd_lsxt_wlls w
JOIN dwd_lsxt_dma d ON d.dmaid=w.dmaid AND {use}
WHERE {day} AND {rng}
""".format(rate=WLLS_RATE_AGG, use=DMA_IN_USE, day=WLLS_DAY, rng=WLLS_RANGE)


def dma_org_rate_bottom3(spec):
    return dma_org_valid_rate(spec).replace("ORDER BY 1 ASC", "ORDER BY rate ASC NULLS LAST, 1 ASC LIMIT 3")


def dma_org_rate_top3(spec):
    return dma_org_valid_rate(spec).replace("ORDER BY 1 ASC", "ORDER BY rate DESC NULLS LAST, 1 ASC LIMIT 3")


def dma_invalid_top3(spec):
    return """
SELECT {org}, COUNT(*) AS invalid_cnt
FROM dwd_lsxt_dma WHERE {w}
GROUP BY 1
ORDER BY invalid_cnt DESC, 1 ASC
LIMIT 3
""".format(org=ORG, w=DMA_INVALID)


def dma_invalid_share_top3(spec):
    return """
SELECT "组织", share FROM (
  SELECT {org}, COUNT(*) FILTER (WHERE {inv})*1.0/NULLIF(COUNT(*),0) AS share
  FROM dwd_lsxt_dma
  GROUP BY 1
) s
ORDER BY share DESC NULLS LAST, "组织" ASC
LIMIT 3
""".format(org=ORG, inv=DMA_INVALID)


def _dma_rate_list(having, extra_select=""):
    extra = (", " + extra_select) if extra_select else ""
    return """
SELECT w.dmaid AS "DMA编码", MAX(w.dmaname) AS "DMA名称", {org},
       {rate} AS rate {extra}
FROM dwd_lsxt_wlls w
WHERE {day} AND {rng}
GROUP BY w.dmaid, {org_g}
HAVING {having}
ORDER BY rate DESC NULLS LAST, w.dmaid ASC
""".format(
        org=ORG_MAP_SQL.replace("bz_bm", "w.bz_bm").replace("zonecode", "w.zonecode") + ' AS "组织"',
        org_g=ORG_MAP_SQL.replace("bz_bm", "w.bz_bm").replace("zonecode", "w.zonecode"),
        rate=WLLS_RATE_AGG, extra=extra, day=WLLS_DAY, rng=WLLS_RANGE, having=having,
    )


def dma_high_loss_count(spec):
    return """
SELECT COUNT(*) AS cnt FROM (
  SELECT w.dmaid
  FROM dwd_lsxt_wlls w
  WHERE {day} AND {rng}
  GROUP BY w.dmaid
  HAVING {rate} >= 0.20
) s
""".format(day=WLLS_DAY, rng=WLLS_RANGE, rate=WLLS_RATE_AGG)


def dma_high_loss_list(spec):
    return _dma_rate_list("{rate} >= 0.20".format(rate=WLLS_RATE_AGG))


def dma_neg_loss_count(spec):
    return """
SELECT COUNT(*) AS cnt FROM (
  SELECT w.dmaid FROM dwd_lsxt_wlls w
  WHERE {day} AND {rng}
  GROUP BY w.dmaid
  HAVING {rate} < 0
) s
""".format(day=WLLS_DAY, rng=WLLS_RANGE, rate=WLLS_RATE_AGG)


def dma_abnormal_neg_list(spec):
    return _dma_rate_list("{rate} < -0.05".format(rate=WLLS_RATE_AGG))


def dma_loss_vol_list(spec):
    return """
SELECT w.dmaid AS "DMA编码", MAX(w.dmaname) AS "DMA名称", {org}, SUM(physicalloss) AS loss_vol
FROM dwd_lsxt_wlls w
WHERE {day} AND {rng}
GROUP BY w.dmaid, {org_g}
HAVING SUM(physicalloss) > 10000
ORDER BY loss_vol DESC NULLS LAST, w.dmaid ASC
""".format(
        org=ORG_MAP_SQL.replace("bz_bm", "w.bz_bm").replace("zonecode", "w.zonecode") + ' AS "组织"',
        org_g=ORG_MAP_SQL.replace("bz_bm", "w.bz_bm").replace("zonecode", "w.zonecode"),
        day=WLLS_DAY, rng=WLLS_RANGE,
    )


def _buckets(org_filter=""):
    filt = ("AND " + org_filter) if org_filter else ""
    return """
SELECT bucket AS "区间", COUNT(*) AS cnt, COUNT(*)*1.0/NULLIF(SUM(COUNT(*)) OVER (),0) AS share
FROM (
  SELECT CASE
           WHEN {rate} < 0 THEN '<0%%'
           WHEN {rate} < 0.10 THEN '0-10%%'
           WHEN {rate} < 0.20 THEN '10-20%%'
           ELSE '>=20%%'
         END AS bucket
  FROM dwd_lsxt_wlls w
  WHERE {day} AND {rng} {filt}
  GROUP BY w.dmaid
) s
GROUP BY 1
ORDER BY 1 ASC
""".format(rate=WLLS_RATE_AGG, day=WLLS_DAY, rng=WLLS_RANGE, filt=filt)


def dma_bucket_group(spec):
    return _buckets()


def dma_bucket_futian(spec):
    return _buckets("w.bz_bm='福田分公司'")


def dma_nanshan_high(spec):
    return """
SELECT high AS cnt, high*1.0/NULLIF(total,0) AS share
FROM (
  SELECT COUNT(*) FILTER (WHERE rate>=0.20) AS high, COUNT(*) AS total
  FROM (
    SELECT {rate} AS rate
    FROM dwd_lsxt_wlls w
    WHERE {day} AND {rng} AND w.bz_bm='南山分公司'
    GROUP BY w.dmaid
  ) a
) s
""".format(rate=WLLS_RATE_AGG, day=WLLS_DAY, rng=WLLS_RANGE)


def dma_group_dev_rate(spec):
    return dma_group_rate(spec).replace("AS rate", "AS rate")


def dma_org_dev_rate(spec):
    return dma_org_valid_rate(spec).replace("AS rate", "AS rate").replace("COUNT(DISTINCT w.dmaid) AS cnt, ", "")


def dma_pair_invalid(spec):
    return """
SELECT l.share AS left_share, r.share AS right_share, ABS(l.share-r.share) AS gap
FROM (
  SELECT COUNT(*) FILTER (WHERE {inv})*1.0/NULLIF(COUNT(*),0) AS share
  FROM dwd_lsxt_dma WHERE bz_bm='南山分公司'
) l
CROSS JOIN (
  SELECT COUNT(*) FILTER (WHERE {inv})*1.0/NULLIF(COUNT(*),0) AS share
  FROM dwd_lsxt_dma WHERE bz_bm='宝安水务集团'
) r
""".format(inv=DMA_INVALID)


def dma_pair_rate(spec):
    return """
SELECT l.rate AS left_rate, r.rate AS right_rate, ABS(l.rate-r.rate) AS gap
FROM (
  SELECT {rate} AS rate FROM dwd_lsxt_wlls w
  WHERE {day} AND {rng} AND w.bz_bm='南山分公司'
) l
CROSS JOIN (
  SELECT {rate} AS rate FROM dwd_lsxt_wlls w
  WHERE {day} AND {rng} AND w.bz_bm='宝安水务集团'
) r
""".format(rate=WLLS_RATE_AGG, day=WLLS_DAY, rng=WLLS_RANGE)


# ===== DMA-026（2026-09-21 补录业务核对过的 Q28）：产销差率 × DMA 漏损量关联 =====

def dma_org_cx_loss(spec):
    """业务核对 Q28：按组织关联“月 DMA 漏损量 + DMA 小区数”与“累计产销差率”。

    口径：漏损量/小区数=dwd_lsxt_wlls Day/GOOD（与产销差率同一固定月）；
    产销差率=dwd_lsxt_fqcxfx SzwgBusinessYear(month)。禁止把两者平均成单 DMA 漏损（R11）。
    """
    name = zone_name_as("f.zoneid")
    return """
SELECT COALESCE(w."组织", r."组织") AS "组织", r.rate AS rate,
       w.loss_vol AS loss_vol, w.dma_cnt AS dma_cnt
FROM (
  SELECT {org}, SUM(w.physicalloss) AS loss_vol, COUNT(DISTINCT w.dmaid) AS dma_cnt
  FROM dwd_lsxt_wlls w
  WHERE {day} AND w.taskstart::date >= CAST(:start_date AS date)
    AND w.taskstart::date <= CAST(:end_date AS date)
  GROUP BY 1
) w
FULL JOIN (
  SELECT {name}, {rate} AS rate
  FROM dwd_lsxt_fqcxfx f
  WHERE f.periodtype='SzwgBusinessYear' AND f.businessyearmonth=CAST(:period AS int)
  GROUP BY f.zoneid
) r ON w."组织" = r."组织"
ORDER BY loss_vol DESC NULLS LAST, 1 ASC
""".format(org=ORG, name=name, rate=CX_RATE_AGG, day=WLLS_DAY)


BUILDERS = {k: v for k, v in globals().items() if k.startswith("dma_")}
