"""Compare two independent Judge replay artifacts without merging results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sagacontext.bench.admission import audit_models, load_results, markdown_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pro", type=Path, required=True)
    parser.add_argument("--flash", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", choices=("v3", "v4"), default="v4")
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".json").exists():
        parser.error("refusing to overwrite an existing audit artifact")
    audit = audit_models(load_results(args.pro), load_results(args.flash), contract=args.contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown_audit(audit))
    json_path = args.output.with_suffix(".json")
    json_path.write_text(json.dumps(audit, ensure_ascii=True, indent=2) + "\n")
    print(args.output)
    return int(bool(audit["pro_admission_errors"] or audit["flash_admission_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
