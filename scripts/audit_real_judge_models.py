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
    args = parser.parse_args()
    audit = audit_models(load_results(args.pro), load_results(args.flash))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown_audit(audit))
    json_path = args.output.with_suffix(".json")
    json_path.write_text(json.dumps(audit, ensure_ascii=True, indent=2) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
