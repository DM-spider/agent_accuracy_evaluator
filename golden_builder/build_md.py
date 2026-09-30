# -*- coding: utf-8 -*-
"""build_md：golden_dataset.json → 黄金测评集_问答打印_<基准日>.md

包含**全部题目**的完整打印：
- 统计值题：逐字段标准值
- 明细题：Markdown 表（≤20 行全展示，超过 20 行打印 10 行并注明总行数；xlsx 明细 sheet 展示全部行）
- 说明型/跳转型/不可算题：标准答案文本
"""
def fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _fmt_val(v, unit, scale=""):
    if v is None:
        return "缺失"
    if scale == "ratio01":
        # 原始值是 0-1 小数，括号内给出换算后的业务单位读数
        return f"{v:g}（={v * 100:g}{unit or '%'}）"
    if unit in ("%", "pp"):
        return f"{v}{unit}"
    return f"{fmt_cell(v)} {unit}"


def build(golden, md_path, B):
    meta = golden["meta"]
    cases = golden["cases"]
    n_stat = sum(1 for c in cases if "fields" in c.get("expected", {}) and "detail" not in c.get("expected", {}))
    n_both = sum(1 for c in cases if "fields" in c.get("expected", {}) and "detail" in c.get("expected", {}))
    n_detail = sum(1 for c in cases if "detail" in c.get("expected", {}) and "fields" not in c.get("expected", {}))
    n_text = sum(1 for c in cases if c.get("result_type") in ("text", "jump", "uncomputable"))

    lines = []
    lines.append(f"# 黄金测评集 · 问答打印（基准日 {B.isoformat()}）")
    lines.append("")
    lines.append(f"> 数据库 ai_agent@10.202.27.163:80 · 生成时间 {meta['generated_at']}")
    lines.append(f"> 各表取数截止：{meta['data_cutoff']}")
    lines.append("")
    lines.append(f"> 全量 {len(cases)} 题：统计值 {n_stat} 题、统计值+明细 {n_both} 题、纯明细 {n_detail} 题、"
                 f"说明/跳转/不可算 {n_text} 题。明细题 ≤20 行全展示，超过 20 行打印 10 行；"
                 f"xlsx 明细 sheet 展示全部行。")
    lines.append("")

    if meta.get("removed_cases"):
        lines.append("## 已剔除题目说明（业务不理解问题表述，不纳入金标）")
        lines.append("")
        lines.append("| 题号 | 题面 | 剔除原因 |")
        lines.append("|---|---|---|")
        for r in meta["removed_cases"]:
            lines.append(f"| {r['case_id']} | {r['question']} | {r['reason']} |")
        lines.append("")

    for case in cases:
        exp = case.get("expected", {})
        lines.append(f"## {case['case_id']} {case['question']}")
        lines.append("")
        lines.append(f"- 场景：{case.get('scene_big', '')} / {case.get('indicator_type', '')}；角色：{'、'.join(case.get('roles', []))}")
        lines.append(f"- 时间：{case.get('time_scope_raw', '') or case.get('time_scope_anchored', '')}；组织：{case.get('org_scope_raw', '') or case.get('org_scope_anchored', '')}")
        lines.append(f"- 口径：{case.get('caliber', '')}")
        lines.append(f"- 结果类型：{case.get('result_type', '')}；可用性：{case.get('availability', '')}")

        if case.get("sql_executable"):
            lines.append("")
            lines.append("**可执行SQL：**")
            lines.append("")
            lines.append("```sql")
            lines.append(case["sql_executable"])
            lines.append("```")

        if "fields" in exp:
            lines.append("")
            lines.append("**标准值（统计）：**")
            lines.append("")
            lines.append("| 指标 | 标准值 |")
            lines.append("|---|---|")
            for f in exp["fields"]:
                lines.append(f"| {f['label']} | {_fmt_val(f['value'], f['unit'], f.get('value_scale', ''))} |")

        if "detail" in exp:
            det = exp["detail"]
            lines.append("")
            lines.append(f"**明细（共 {det['total_rows']} 行{'，截断至1000' if det['total_rows'] > 1000 else ''}）：**")
            if det.get("note"):
                lines.append(f"- 附注：{det['note']}")
            rows = det["rows"]
            if rows:
                cols = list(rows[0].keys())
                max_print = 20 if det["total_rows"] <= 20 else 10
                lines.append("")
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("|" + "|".join(["---"] * len(cols)) + "|")
                for r in rows[:max_print]:
                    lines.append("| " + " | ".join(fmt_cell(r.get(c)) for c in cols) + " |")
                if det["total_rows"] > max_print:
                    lines.append("")
                    lines.append(f"> 共 {det['total_rows']} 行，仅展示 {max_print} 行，完整清单见 xlsx 明细_{case['case_id']} sheet。")
            else:
                lines.append("")
                lines.append("（无明细行）")

        if "standard_answer" in exp:
            lines.append("")
            lines.append(f"**标准答案：** {exp['standard_answer']}")

        if case.get("notes"):
            lines.append("")
            lines.append(f"- 备注：{case['notes']}")
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
