# -*- coding: utf-8 -*-
"""黄金测评集 · 口径注册表（唯一口径事实源）。

所有 case 模块、golden_runner、extractor、evaluate 均从这里 import，
禁止在业务代码里散落魔法值。来源：
- 时间解析规则：docs/plans/...执行方案.md §3.1（基准日动态解析）
- 组织映射：dwd_lsxt_fqcxfx 202607 期数实测 15 行（§3.2）
- 红字规则：两份 Word 报告红色批注（§3.3）
- 字典/铁律：01/02 指标文档 + 实测（§3.4）
"""
from datetime import date, timedelta

# ---------------------------------------------------------------- 时间解析规则

def ym_of(d: date) -> int:
    return d.year * 100 + d.month


def ym_shift(ym: int, delta: int) -> int:
    """期数月份平移（支持跨年）。"""
    y, m = ym // 100, ym % 100
    m += delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y * 100 + m


def ym_first_day(ym: int) -> date:
    return date(ym // 100, ym % 100, 1)


def ym_last_day(ym: int) -> date:
    nxt = ym_shift(ym, 1)
    return ym_first_day(nxt) - timedelta(days=1)




# ---------------------------------------------------------------- 组织口径映射（实测 202607 期数）

# (zoneid, level, zonename, bz_bm) —— 来自 2026-08-17 实测 SELECT
ORG_ROWS = [
    ("F1",    -1, "深圳环境水务集团", "其他"),          # 集团行（业务口径不一致，默认不用）
    ("F100",   0, "集团本部", "其他"),                 # 不在深圳本地清单
    ("F1007",  0, "深水宝安", "宝安水务集团"),
    ("F1501",  0, "深水龙岗", "龙岗水务集团"),
    ("F1005",  0, "深水光明", "光明水务集团"),
    ("F1500",  0, "深水龙华", "龙华水务集团"),
    ("F1013",  0, "莲塘供水", "其他"),
    ("F1014",  0, "布吉水司", "其他"),                 # 集团合计须剔除
    ("F1009",  0, "深水坪地供水", "其他"),
    ("F1012",  0, "深水大鹏水务", "其他"),
    ("F1011",  0, "深汕水务", "深汕水务"),
    ("F1001",  1, "盐田分公司", "盐田分公司"),
    ("F1002",  1, "罗湖分公司", "罗湖分公司"),
    ("F1004",  1, "福田分公司", "福田分公司"),
    ("F1003",  1, "南山分公司", "南山分公司"),
]

BENBU_4 = ["F1001", "F1002", "F1004", "F1003"]                       # 深圳本部：盐田/罗湖/福田/南山
SHENZHEN_LOCAL_13 = (BENBU_4 + ["F1007", "F1501", "F1005", "F1500", "F1013",
                                "F1014", "F1009", "F1012", "F1011"])  # 深圳本地 13 组织
GROUP_TOTAL = [z for z in SHENZHEN_LOCAL_13 if z != "F1014"]          # 集团合计：剔除布吉水司


ZONEID_NAME_CASE = """
CASE zoneid WHEN 'F1' THEN '深圳环境水务集团'
     WHEN 'F100' THEN '集团本部'
     WHEN 'F1001' THEN '盐田分公司'
     WHEN 'F1002' THEN '罗湖分公司' WHEN 'F1004' THEN '福田分公司' WHEN 'F1003' THEN '南山分公司'
     WHEN 'F1007' THEN '宝安水务集团' WHEN 'F1501' THEN '龙岗水务集团'
     WHEN 'F1500' THEN '龙华水务集团' WHEN 'F1005' THEN '光明水务集团'
     WHEN 'F1012' THEN '大鹏新区分公司' WHEN 'F1014' THEN '布吉水司'
     WHEN 'F1013' THEN '莲塘供水' WHEN 'F1009' THEN '坪地供水' WHEN 'F1011' THEN '深汕水务'
     ELSE zoneid END"""

# zoneid → 目标表 dwmc 名称（与 dm_yyzbtx_gggsgwlsl.dwmc 对齐，用于跨表 JOIN）
ZONEID_DWMC_CASE = """
CASE zoneid WHEN 'F1001' THEN '盐田分公司'
     WHEN 'F1002' THEN '罗湖分公司' WHEN 'F1004' THEN '福田分公司' WHEN 'F1003' THEN '南山分公司'
     WHEN 'F1007' THEN '宝安水务集团' WHEN 'F1501' THEN '龙岗水务集团'
     WHEN 'F1005' THEN '光明水务公司' WHEN 'F1500' THEN '龙华水务公司'
     WHEN 'F1013' THEN '莲塘供水公司' WHEN 'F1014' THEN '布吉供水公司'
     WHEN 'F1009' THEN '龙岗坪地供水公司' WHEN 'F1012' THEN '大鹏新区分公司'
     WHEN 'F1011' THEN '深汕水务公司'
     ELSE zoneid END"""

# ---- Task 2 实测补充（2026-08-17）----

# ① 各表 bz_bm 实测取值（2026 年以来，含"其他"）。
BZ_BM_DICTS = {
    "dwd_lsxt_bj": ["龙岗水务集团", "龙华水务集团", "南山分公司", "其他", "福田分公司",
                    "罗湖分公司", "宝安水务集团", "盐田分公司", "光明水务集团", "大鹏新区分公司"],
    "dwd_lsxt_wlls": ["龙岗水务集团", "南山分公司", "龙华水务集团", "福田分公司",
                      "罗湖分公司", "其他", "盐田分公司", "大鹏新区分公司", "光明水务集团"],
    "dwd_lsxt_yjzxll": ["龙岗水务集团", "南山分公司", "龙华水务集团", "福田分公司",
                        "罗湖分公司", "其他", "盐田分公司", "大鹏新区分公司", "深汕水务",
                        "光明水务集团", "宝安水务集团"],
    "dwd_gdgl_gd_gsgdwxmx": ["龙岗水务集团", "福田分公司", "南山分公司", "罗湖分公司",
                             "宝安水务集团", "龙华水务集团", "盐田分公司", "光明水务集团",
                             "大鹏新区分公司", "坪地分公司", "莲塘分公司", "深汕水务", "其他", "布沙分公司"],
    "dwd_gdgl_gd_jlmx": ["宝安水务集团", "龙岗水务集团", "光明水务集团", "福田分公司",
                         "南山分公司", "龙华水务集团", "盐田分公司"],
    "dwd_gdgl_gd_xfswxmx": ["福田分公司", "龙华水务集团", "光明水务集团", "龙岗水务集团",
                            "宝安水务集团", "南山分公司", "盐田分公司", "罗湖分公司",
                            "坪地分公司", "深汕水务", "大鹏新区分公司", "莲塘分公司"],
    "dwd_gdgl_gd_fmwxgdmx": ["龙岗水务集团", "福田分公司", "宝安水务集团", "南山分公司",
                             "罗湖分公司", "光明水务集团", "龙华水务集团", "坪地分公司",
                             "盐田分公司", "莲塘分公司", "大鹏新区分公司", "深汕水务", "其他"],
    "dwd_gdgl_gd_jhtsmx": ["福田分公司", "宝安水务集团", "罗湖分公司", "龙岗水务集团",
                           "南山分公司", "龙华水务集团", "盐田分公司", "大鹏新区分公司",
                           "坪地分公司", "莲塘分公司", "其他", "光明水务集团"],
}

# ② "其他"行归属（R4：分公司不能有"其他"）。
#    实测：bj/wlls/dma 的"其他"行 zonecode 携带真实组织编码——
#    '%F1014%'→布吉水司（旗下南湾/布吉/坂田营业所）、'%F1013%'→莲塘供水、'%F1009%'→坪地供水。
#    SQL 片段（bj/wlls 表可用；dma 档案表同样成立）：
# ③ wlls 表口径重大修正（实测）：
#    periodtype='Month' 最新仅到 2023-10；'Year' 到 2022-01。2026 年只有 'Day' 行（最新 2026-08-17）。
#    → 所有"月度 DMA 漏损/高漏耗"口径一律用 Day 行按月聚合：
#      WHERE periodtype='Day' AND diffresult='GOOD' AND taskstart::date BETWEEN 月首 AND 月末
#      GROUP BY dmaid → 月均率=AVG(physicallossrate)，月漏量=SUM(physicalloss)/SUM(totalloss)
WLLS_MONTH_STALE = True   # Month 行停更，强制 Day 聚合

# ④ 产销差单月行现状（实测）：SzwgBusiness 202607 售水量仅 1/14 行回填（龙岗），
#    其余为 0 → 7 月单月产销差率不可信；202606 单月 12/14 行售水非零（最新可用完整单月）。
#    Year 202607 与 202606 数值完全相同（承载 1-6 月累计）。
#    期数滞后语义（实测）：Year 期数 M 承载 1~(M-1) 月累计（如 202607=1-6月累计）；
#    同比同期 = 期数 M-101（如 202607 vs 202506，同为 1-6 月累计）。
#    规则：单月优先 SzwgBusiness 行；若该月售水非零行占比 < 50%，判"未回填"，
#    回退到最近一个售水非零占比 ≥50% 的单月期数，并记录 fallback。
FQC_SINGLE_MONTH_NOTE = "202607 单月售水未回填（1/14）；202606 为最新完整单月；Year 202607=202606=1-6月累计；同比同期=期数-101"
# ⑤ 目标表 dm_yyzbtx_gggsgwlsl 组织映射（dwmc → fqcxfx 对应口径）
TARGET_ORG_MAP = {
    "环水集团": "集团", "本部区域分公司": "集团本部", "盐田分公司": "盐田分公司",
    "罗湖分公司": "罗湖分公司", "福田分公司": "福田分公司", "南山分公司": "南山分公司",
    "宝安水务集团": "宝安水务集团", "龙岗水务集团": "龙岗水务集团",
    "龙华水务公司": "龙华水务集团", "光明水务公司": "光明水务集团",
    "布吉供水公司": "布吉水司", "莲塘供水公司": "莲塘供水",
    "龙岗坪地供水公司": "坪地供水", "大鹏新区分公司": "大鹏新区分公司",
    "深汕水务公司": "深汕水务",
}
# 目标表 bz_bm 字段与 dwmc 基本一致（布吉/莲塘/坪地/本部等行为'其他'），以 dwmc 为准。

# ⑥ 爆管识别：accident_type LIKE '%爆%'（横向爆裂/纵向爆裂/管道爆裂破洞/阀门爆裂）
#    2026-07 实测 1320 条；title LIKE '%爆管%' 仅 160 条，order_type_name 无爆管值。
BURST_FILTER = "accident_type LIKE '%爆%'"

# ⑦ jlmx = 检漏工单表（type_name='检漏'）；GD-005 检漏效率（R5）= 检漏工单数/供水管道维修工单数，
#    亦可用 dm_lszb_tlxl_tj（字段 jlwxgds/gsgdwxgds/jlxl/gdcd，按 dwmc）。

# ---------------------------------------------------------------- 字典（实测）

PERIODTYPE_FQC = {          # dwd_lsxt_fqcxfx.periodtype（2026-08-17 实测）
    "SzwgBusinessYear": "业务年累计（默认产销差口径）",
    "SzwgBusiness": "业务月单月",
    "MunicipalLossRateYear": "市政累计（字段留白）",
    "MunicipalLossRateMonth": "市政单月（字段留白）",
    "DmaLossRateYear": "DMA 累计（字段留白）",
    "DmaLossRateMonth": "DMA 单月（字段留白）",
    "Day": "日口径", "DayProduct": "日产量明细",
    "Year": "自然年累计（不建议）", "Month": "自然月（不建议）",
    "ZnMonth": "历史口径（停用）", "BothUcisMonth": "UCIS 月度",
}

SRCTYPE_BJ = {             # dwd_lsxt_bj.srctype（实测）
    "NightFlow": "夜间流量异常", "PhysicalLoss": "物理漏损",
    "WaterProduct": "压力超限", "WaterDiff": "水量差异",
    "MeterDataException": "表计数据异常", "MeterException": "表计异常",
    "OfflineParent": "离线总表",
}

DIFFRESULT_WLLS = {"GOOD": "数据正常", "DATA_LESS": "数据缺失", None: "空值"}

DMA_STATUS = {"WORK": "有效", "PAUSE": "暂停", "RETIRE": "退役"}

# ---------------------------------------------------------------- 红字业务规则（R1~R12）

RED_RULES = {
    "R1": "深圳本部=盐田/罗湖/福田/南山四分公司；集团口径=深圳本地剔除布吉水司",
    "R2": "产销差率默认年累计；问'月'才单月；每月水量=当月累计-上月累计（不允许直接展示计量/未计量拆分）",
    "R3": "2026 上半年=6 月年累计（盐田 9.36%）；2025 上半年盐田 7.73%",
    "R4": "夜间小流预警按分公司统计 DMA 小区总数；展示'报警原因'而非预警值；handled 显示'已处理'；分公司不得出现'其他'；数量不带小数点",
    "R5": "检漏效率=检漏工单数/供水管道维修工单数（完成率≠检漏效率；探漏工单一般不问完成率）",
    "R6": "'超时'=漏损预警 10 天内未处理（不是探漏工单超时）",
    "R7": "维修工单漏损水量=water_leakage（≠leakage_quantity）；禁止'平均每单'（不是所有工单都有漏失）",
    "R8": "消防栓：工单数、排放水量保留；去掉'平均每次'，展示漏失水量",
    "R9": "设施复发维修=同一设施编码在某时间段内重复出现故障（按编码统计，不是'DMA 小区'）",
    "R10": "展示具体分公司 + 分公司下面的水务所/运营中心（下钻层级）",
    "R11": "产销差率不能按单 DMA 平均漏损推算；问数必须明确月份",
    "R12": "无'水务公司'说法；工单类型必须明确（供水管道维修/检漏/阀门维修）",
}

# ---------------------------------------------------------------- 容差规则

TOLERANCE = {
    "rate_abs": 0.01,      # 率类（%）：绝对容差 ±0.01pp
    "volume_rel": 0.001,   # 量类（m³）：相对容差 ±0.1%
    "count_exact": True,   # 数量类（个/条/单）：必须精确
}

# ---------------------------------------------------------------- 明细列名中文化（英文字段/别名 → 中文业务名）

COLUMN_CN = {
    # 产销差
    "ym": "月份(期数)", "period": "期间", "month": "月份",
    "supply": "供水量(m³)", "sales": "售水量(m³)", "diff_vol": "产销差量(m³)",
    "cum_rate": "累计产销差率(%)", "group_cum_rate": "集团累计产销差率(%)",
    "actual_rate": "实际产销差率(%)", "target_rate": "目标产销差率(%)",
    "completion": "完成率(%)", "weight_pct": "影响权重(%)", "gap": "差距(pp)",
    "freewater": "免费水量(m³)",
    # 漏损
    "avg_rate": "月均漏损率(%)", "avg_rate_cur": "本月月均漏损率(%)",
    "physicalloss": "物理漏损量(m³)", "loss_vol": "漏损量(m³)", "vol": "水量(m³)",
    "unit_loss": "单位管长漏损量(m³/km)", "pipe_len_km": "管长(km)", "gdcd": "管长(km)",
    "d": "差值", "list_type": "榜单类型", "direction": "变化方向", "section": "区间",
    # DMA / 组织（业务术语：二级部门=分公司/水务公司，三级部门=水务所/运营中心）
    "dmaid": "DMA编码", "dmaname": "DMA名称", "dma_cnt": "DMA小区数",
    "bz_bm": "二级部门", "org": "二级部门", "dwmc": "二级部门",
    "level3": "三级部门", "l3_name": "三级部门", "level3_name": "三级部门",
    # 预警
    "warn_cnt": "预警次数", "warnvalue": "预警值", "max_warnvalue": "最大预警值",
    "warndate": "预警日期", "first_warndate": "首次预警日期",
    "alarm_reason": "报警原因", "srctype": "预警类型", "handled": "是否已处理",
    "handled_cnt": "已处理数", "unhandled_cnt": "未处理数", "new_cnt": "新增数",
    "max_nmf": "夜间最小流量最大值", "avg_nmf": "夜间最小流量均值",
    "avg_nightminflow": "夜间最小流量均值", "avg_dayflow": "日均流量",
    "max_stdten": "波动标准差最大值", "meanv": "均值", "varv": "偏离量", "triple": "3σ值",
    "metercode": "表计编码", "metername": "表计名称",
    # 总分表
    "dev_rate": "总分表偏差率(%)", "dev_vol": "总分表偏差量(m³)",
    # 数据质量
    "complete_rate": "完整率(%)", "good_days": "数据正常天数", "total_days": "总天数",
    "days": "天数", "diffresult": "数据状态", "total_meter": "水表总数",
    # 工单 / 设施
    "id": "工单编号", "title": "标题", "order_type": "工单类型", "flow_status": "流转状态",
    "status": "状态", "finished": "完成数", "finish_time": "完成时间",
    "arrive_time": "到场时间", "arrive_intime": "是否及时到场",
    "start_repair_time": "修复开始时间", "repair_cnt": "维修工单数",
    "total_water_leak": "漏损水量(m³)", "leak_order_cnt": "漏水工单数",
    "jlxl": "检漏效率", "jlwxgds": "检漏工单数", "gsgdwxgds": "供水管道维修工单数",
    "facility_code": "设施编码", "facility_type": "设施类型", "burst_cnt": "爆管次数",
    "pipe_number": "管道编号", "address": "位置", "unfinished": "未完成数",
    "discharge_water": "排放水量(m³)", "water_leakage": "漏失水量(m³)",
    # 25 题报告扩展
    "total": "总数", "create_time": "创建时间", "avg_hours": "平均响应时长(h)",
    "seg": "管径分段", "comp_cnt": "涉及分公司数", "month_rate": "6月产销差率(%)",
    "h1_rate": "2026上半年产销差率(%)", "h1_2026": "2026上半年产销差率(%)",
    "h1_2025": "2025上半年产销差率(%)", "yoy_pp": "同比变化(pp)", "trend": "趋势",
    "order_cnt": "检漏工单数", "dma_share": "集团占比(%)", "mom_pp": "环比变化(pp)",
    "mom_pct": "变化幅度(%)", "rank": "排名", "gap_pp": "与第1名差距(pp)",
    "days_cycle": "处理周期(天)",
    # 通用
    "n": "数量", "cnt": "数量", "total_cnt": "总数", "pct": "占比(%)",
    "note": "备注", "flag": "标记", "yslb": "用水类别",
}

# ---------------------------------------------------------------- 锚点清单（结构式校验，与基准日无关）
ANCHORS = [
    {"id": "A1", "desc": "盐田分公司 2026-01 产销差率（F1001）", "expect": "供 2515137 / 售 2298681 / 率 8.61",
     "sql": "SELECT ROUND(totalproduct,0), ROUND(totalconsume,0), ROUND((totalproduct-totalconsume)*100.0/NULLIF(totalproduct,0),2) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear' AND businessyearmonth=202601 AND zoneid='F1001'",
     "check": "expect_rows", "expect_rows": [[2515137, 2298681, 8.61]]},
    {"id": "A2", "desc": "F1 行 2026-01（=集团合计口径的行级验证）", "expect": "供 165962168.50 / 率 4.70",
     "sql": "SELECT totalproduct, ROUND((totalproduct-totalconsume)*100.0/NULLIF(totalproduct,0),2) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear' AND businessyearmonth=202601 AND zoneid='F1'",
     "check": "expect_rows", "expect_rows": [[165962168.5, 4.7]]},
    {"id": "A3", "desc": "盐田 2026/2025 上半年年累计率（红字）", "expect": "202606=9.36 / 202506=7.73",
     "sql": "SELECT businessyearmonth, ROUND((totalproduct-totalconsume)*100.0/NULLIF(totalproduct,0),2) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear' AND businessyearmonth IN (202606,202506) AND zoneid='F1001' ORDER BY businessyearmonth",
     "check": "expect_rows", "expect_rows": [[202506, 7.73], [202606, 9.36]]},
    {"id": "A4", "desc": "2026-07 维修工单漏损水量字段（R7：water_leakage≠leakage_quantity）", "expect": "water_leakage 有值条数 > leakage_quantity 条数",
     "sql": "SELECT COUNT(*) FILTER (WHERE water_leakage IS NOT NULL AND water_leakage<>''), COUNT(*) FILTER (WHERE leakage_quantity IS NOT NULL AND leakage_quantity<>'') FROM dwd_gdgl_gd_gsgdwxmx WHERE create_time>='2026-07-01' AND create_time<'2026-08-01'",
     "check": "water_gt_leakage"},
    {"id": "A5", "desc": "2026-07 消防栓排放/漏失（R8 两字段不可混用）", "expect": "排放合计 307 / 漏失合计 945",
     "sql": "SELECT ROUND(SUM(NULLIF(discharge_water,'')::numeric),0), ROUND(SUM(NULLIF(water_leakage,'')::numeric),0) FROM dwd_gdgl_gd_xfswxmx WHERE create_time>='2026-07-01' AND create_time<'2026-08-01'",
     "check": "exact_307_945"},
    {"id": "A6", "desc": "2026-07 夜间流量预警（R4：bz_bm 全覆盖）", "expect": "总数 564；bz_bm 非空 564",
     "sql": "SELECT COUNT(*), COUNT(zonename), COUNT(bz_bm) FROM dwd_lsxt_bj WHERE srctype='NightFlow' AND warndate>='2026-07-01' AND warndate<'2026-08-01'",
     "check": "bj_total_564"},
    {"id": "A7", "desc": "2026-07 物理漏损预警", "expect": "7328~7600 条",
     "sql": "SELECT COUNT(*) FROM dwd_lsxt_bj WHERE srctype='PhysicalLoss' AND warndate>='2026-07-01' AND warndate<'2026-08-01'",
     "check": "range_7328_7600"},
    {"id": "A8", "desc": "DMA 状态字典", "expect": "WORK 5630 / PAUSE 213 / RETIRE 12",
     "sql": "SELECT status, COUNT(*) FROM dwd_lsxt_dma GROUP BY status ORDER BY 2 DESC",
     "check": "expect_rows", "expect_rows": [["WORK", 5630], ["PAUSE", 213], ["RETIRE", 12]]},
    {"id": "A9", "desc": "jlmx 探漏/检漏表 leakage_quantity 有值", "expect": "有值条数>0 且合计>0（08-17 实测 223 条/1076；08-08 快照 268/668）",
     "sql": "SELECT COUNT(*) FILTER (WHERE leakage_quantity IS NOT NULL AND leakage_quantity<>''), ROUND(SUM(NULLIF(leakage_quantity,'')::numeric),0) FROM dwd_gdgl_gd_jlmx WHERE create_time>='2026-07-01' AND create_time<'2026-08-01'",
     "check": "positive2"},
    {"id": "A10", "desc": "南山管道复发维修（红字：5 次=1 个、3 次不止 2 个；口径=2026-05~07，pipe_number）", "expect": "c=5 的管道 1 个；c=3 的管道 ≥3 个（实测 7 个）",
     "sql": "SELECT (SELECT COUNT(*) FROM (SELECT pipe_number FROM dwd_gdgl_gd_gsgdwxmx WHERE bz_bm='南山分公司' AND create_time>='2026-05-01' AND create_time<'2026-08-01' AND pipe_number IS NOT NULL AND pipe_number<>'' GROUP BY 1 HAVING COUNT(*)=5) t), (SELECT COUNT(*) FROM (SELECT pipe_number FROM dwd_gdgl_gd_gsgdwxmx WHERE bz_bm='南山分公司' AND create_time>='2026-05-01' AND create_time<'2026-08-01' AND pipe_number IS NOT NULL AND pipe_number<>'' GROUP BY 1 HAVING COUNT(*)=3) t)",
     "check": "pipe_c5_c3"},
    {"id": "A11", "desc": "202607 期数行结构", "expect": "level -1=1、0=10、1=4（共 15 行）",
     "sql": "SELECT level, COUNT(*) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear' AND businessyearmonth=202607 GROUP BY level ORDER BY 1",
     "check": "expect_rows", "expect_rows": [[-1, 1], [0, 10], [1, 4]]},
    {"id": "A12", "desc": "集团合计=F1 行（红字 R1 数据验证：GROUP_TOTAL 汇总==F1）", "expect": "供 868747310 / 售 803192724.5 / 率 7.55",
     "sql": "SELECT ROUND(SUM(totalproduct),0), ROUND(SUM(totalconsume),1), ROUND((SUM(totalproduct)-SUM(totalconsume))*100.0/NULLIF(SUM(totalproduct),0),2) FROM dwd_lsxt_fqcxfx WHERE periodtype='SzwgBusinessYear' AND businessyearmonth=202607 AND zoneid IN ('F1001','F1002','F1004','F1003','F1007','F1501','F1005','F1500','F1013','F1009','F1012','F1011')",
     "check": "expect_rows", "expect_rows": [[868747310, 803192724.5, 7.55]]},
]
