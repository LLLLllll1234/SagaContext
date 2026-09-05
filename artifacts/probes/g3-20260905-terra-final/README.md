# G3: gpt-5.6-terra

- Probe: `g3-20260905T135808Z-dbc31c09`
- Capture: [JSON](g3-codex-host-capture.json)
- Independent evaluation: [JSON](g3-codex-host-evaluation.json)
- Report: [G3 report](../../../docs/probes/2026-09-05-s3-g3-codex-host-terra.md)
- Gate: `passed`, 19/19 required assertions pass.
- All four scenarios emitted the required lifecycle receipts, including duplicate, nonzero-exit, timeout, restart recovery, and marker injection evidence.
- Evidence remains synthetic and redacted. No global configuration, private transcript, real memory, or production host integration was changed.

```sh
.venv/bin/python scripts/probe_codex_host.py --model gpt-5.6-terra --output artifacts/probes/g3-20260905-terra-final/g3-codex-host-capture.json --timeout 240
.venv/bin/python scripts/evaluate_codex_host_probe.py --input artifacts/probes/g3-20260905-terra-final/g3-codex-host-capture.json --output artifacts/probes/g3-20260905-terra-final/g3-codex-host-evaluation.json --report docs/probes/2026-09-05-s3-g3-codex-host-terra.md
```
