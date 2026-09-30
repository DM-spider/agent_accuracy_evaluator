# -*- coding: utf-8 -*-
"""标准集 SQL 片段。比率表达式禁止 *100；LIKE 使用 %% 以兼容 pyformat。"""
from golden_builder.registry import GROUP_TOTAL, SHENZHEN_LOCAL_13, ZONEID_DWMC_CASE, ZONEID_NAME_CASE


def in_list(ids):
    return ",".join("'%s'" % i for i in ids)


ZONE_GROUP_TOTAL = in_list(GROUP_TOTAL)
ZONE_LOCAL_13 = in_list(SHENZHEN_LOCAL_13)
assert "F1014" not in ZONE_GROUP_TOTAL
assert "F1014" in ZONE_LOCAL_13
assert "'F1'" not in ZONE_LOCAL_13
assert "'F100'" not in ZONE_LOCAL_13

# 比率：只输出小数比值
CX_RATE_AGG = "(SUM(f.totalproduct)-SUM(f.totalconsume))/NULLIF(SUM(f.totalproduct),0)"
CX_RATE_ROW = "(totalproduct-totalconsume)/NULLIF(totalproduct,0)"
WLLS_RATE_AGG = "SUM(physicalloss)/NULLIF(SUM(totalproduct),0)"

# 目标表/漏损率字段实测为百分数（7.55 表示 7.55%），输出时 /100 对齐为小数。
PCT_TO_RATIO = "/100.0"
# 集团行 dwmc（dm_yyzbtx_gggsgwlsl / TARGET_ORG_MAP）
LS_GROUP_DWMC = "'环水集团'"

ORG_MAP_SQL = """CASE
    WHEN bz_bm = '其他' AND zonecode LIKE '%%F1014%%' THEN '布吉水司'
    WHEN bz_bm = '其他' AND zonecode LIKE '%%F1013%%' THEN '莲塘供水'
    WHEN bz_bm = '其他' AND zonecode LIKE '%%F1009%%' THEN '坪地供水'
    ELSE bz_bm
END"""

GSGD_NAME_CASE = """CASE bz_bm WHEN '坪地分公司' THEN '坪地供水' WHEN '莲塘分公司' THEN '莲塘供水'
     ELSE bz_bm END"""

TLXL_NAME_CASE = """CASE dwmc WHEN '光明水务公司' THEN '光明水务集团' WHEN '龙华水务公司' THEN '龙华水务集团'
     WHEN '龙岗坪地供水公司' THEN '坪地供水' WHEN '布吉供水公司' THEN '布吉水司'
     WHEN '莲塘供水公司' THEN '莲塘供水' WHEN '深汕水务公司' THEN '深汕水务'
     ELSE dwmc END"""

BURST_FILTER = "(accident_type LIKE '%%爆%%' OR flow_status_name LIKE '%%爆管%%')"

# dwd_lsxt_dma 无 isdelete 列（2026-09 实测），在用/无效只按 status。
DMA_IN_USE = "status='WORK'"
DMA_INVALID = "status IN ('PAUSE','RETIRE')"

WLLS_DAY = "periodtype='Day' AND diffresult='GOOD'"

TLXL_ORG_EXCL = "dwmc NOT IN ('本地','其他')"

# --- 工单表（gsgdwxmx/fmwxgdmx/xfswxmx/jlmx）统一时间口径（GD-001~GD-011）---
# 期间：create_time 半开区间；完成判定：finish_time IS NOT NULL。
GD_WO = "create_time>=CAST(:start_date AS timestamp) AND create_time<CAST(:next_month_start AS timestamp)"
GD_L6 = "create_time>=CAST(:start_date AS timestamp) AND create_time<CAST(:end_date AS timestamp)"

def zone_name_as(col="zoneid", alias='"组织"'):
    expr = ZONEID_NAME_CASE.replace("zoneid", col)
    return "%s AS %s" % (expr, alias)


def zone_dwmc_as(col="zoneid", alias='"组织"'):
    expr = ZONEID_DWMC_CASE.replace("zoneid", col)
    return "%s AS %s" % (expr, alias)


# 仅无歧义维度。业务指标中文名必须来自当前题目 measures，禁止用 rate→比率 这类全局猜测。
COLUMN_CN = {
    "org": "组织", "dwmc": "组织", "bz_bm": "组织",
    "ym": "月份(期数)", "period": "月份(期数)",
    "period_prev": "上期月份", "period_yoy": "同期月份",
    "dmaid": "DMA编码", "dmaname": "DMA名称",
    "id": "工单编号", "title": "标题", "create_time": "创建时间",
    "pipe_number": "管道编码", "pipe_code": "管道编码",
    "facility_code": "设施编码", "valve_number": "设施编码",
    "fire_hydrant_number": "设施编码",
}

FUZZY_CN = {"比率", "值", "数值", "数量", "占比", "差距", "目标", "上月值", "环比变化"}


def chinese_col(col, measures=None):
    for item in measures or []:
        if item.get("sql_column") == col or item.get("key") == col:
            return item["key"]
    if col in COLUMN_CN:
        return COLUMN_CN[col]
    return col
