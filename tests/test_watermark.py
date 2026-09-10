# -*- coding: utf-8 -*-
from evaluator.models import WatermarkSnapshot
from evaluator.watermark import tables_in_sql, watermark_changed


def test_extract_tables_and_detect_change():
    sql = "SELECT * FROM dwd_lsxt_fqcxfx f JOIN dwd_lsxt_wlls w ON 1=1"
    assert "dwd_lsxt_fqcxfx" in tables_in_sql(sql)
    assert "dwd_lsxt_wlls" in tables_in_sql(sql)
    before = WatermarkSnapshot(tables={"dwd_lsxt_fqcxfx": "2026-08-17"})
    after = WatermarkSnapshot(tables={"dwd_lsxt_fqcxfx": "2026-08-18"})
    assert watermark_changed(before, after)
    assert not watermark_changed(before, before)
