# -*- coding: utf-8 -*-
"""CLI 对账产物的版本目录：一天一个版本，用日期做版本号。"""
import pytest

from cli_eval import outdir


def test_active_date_defaults_to_today(monkeypatch):
    monkeypatch.delenv(outdir.DATE_ENV, raising=False)
    assert outdir.active_date() == outdir.today()
    assert outdir.active_date() in outdir.version_dir().name


def test_use_date_sets_the_version(monkeypatch):
    monkeypatch.delenv(outdir.DATE_ENV, raising=False)
    assert outdir.use_date("2026-10-26") == "20261026"
    assert outdir.version_dir().name == "report_20261026"
    monkeypatch.delenv(outdir.DATE_ENV, raising=False)


def test_invalid_date_is_rejected(monkeypatch):
    monkeypatch.delenv(outdir.DATE_ENV, raising=False)
    with pytest.raises(ValueError):
        outdir.use_date("2026/10/26")


def test_same_day_always_maps_to_one_directory(monkeypatch):
    monkeypatch.delenv(outdir.DATE_ENV, raising=False)
    first = outdir.version_dir("20261026")
    second = outdir.version_dir("20261026")
    assert first == second
    assert first.name == "report_20261026"


def test_versions_are_listed_in_date_order(monkeypatch, tmp_path):
    monkeypatch.setattr(outdir, "OUT", tmp_path)
    (tmp_path / "report_20261026").mkdir()
    (tmp_path / "report_20261027").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert outdir.list_versions() == ["20261026", "20261027"]
    assert outdir.latest_version() == "20261027"
