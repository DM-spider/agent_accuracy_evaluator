# -*- coding: utf-8 -*-
from evaluator.contract_loader import generate_contracts
from evaluator.paths import CONFIG_DIR

if __name__ == "__main__":
    contracts = generate_contracts(dest=CONFIG_DIR / "contracts.json")
    numeric = [c for c in contracts if c.numeric_evaluable]
    ready = [c for c in numeric if c.realtime_ready]
    print(f"total={len(contracts)} numeric={len(numeric)} realtime_ready={len(ready)}")
    for c in numeric:
        if not c.realtime_ready:
            print("NOT_READY", c.case_id, c.parameter_resolver, c.review_required)
