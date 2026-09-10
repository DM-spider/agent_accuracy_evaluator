# Flatten V1 Golden Data Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the `data/golden/v1` directory while preserving v1 mock evaluation, tests, and packaged distributions through explicitly named files in `data/golden`.

**Architecture:** Keep v3 as the default `golden_dataset.json`/Excel pair and add a separately named v1 pair in the same directory. Extend the golden loader with a small v1 entry point so callers never depend on a version subdirectory. Mock answer fixtures remain shared because their root and v1 copies are byte-identical.

**Tech Stack:** Python 3.10, pathlib, openpyxl, pytest, PowerShell/PyInstaller packaging.

---

### Task 1: Add flat v1 loader coverage

**Files:**
- Modify: `tests/test_golden_loader.py`
- Modify: `evaluator/golden_loader.py`

1. Add a test that loads the flat v1 dataset and asserts 73 cases including `CX01`.
2. Run that test and confirm it fails before the loader exists.
3. Add explicit v1 filenames and a `merge_v1_golden()` entry point.
4. Run the loader tests and confirm both v3 and v1 pass.

### Task 2: Remove runtime and test dependence on the v1 directory

**Files:**
- Modify: `evaluator/api.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_run_context.py`

1. Make bootstrap load v1 expected values from the flat v1 files.
2. Make tests use the same loader and shared root answer fixtures.
3. Run API, end-to-end, run-context, and mock tests.

### Task 3: Update packaging and documentation

**Files:**
- Modify: `package.ps1`
- Modify: `README.md`

1. Include the two flat v1 standard-data files in the packaged distribution.
2. Document the flat layout and remove statements requiring the subdirectory.

### Task 4: Remove old directory and verify

**Files:**
- Delete: `data/golden/v1/`

1. Verify the exact resolved directory is inside this project's `data/golden` directory.
2. Remove the old directory after the flat copies exist.
3. Search for remaining `golden/v1` references.
4. Run the full test suite.
