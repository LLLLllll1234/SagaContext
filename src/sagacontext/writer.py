from __future__ import annotations
from .ov_client import OpenVikingClient
from .reconcile import WritePlan
from .memfile import parse, render

async def apply(plans: list[WritePlan], client: OpenVikingClient) -> list[str]:
    written = []
    for plan in plans:
        try:
            current = plan
            if plan.mode == "update":
                for attempt in range(3):
                    payload = await client.read(plan.uri)
                    record = parse(plan.uri, client.content_from_response(payload), plan.type)
                    if record.version == current.expected_version:
                        break
                    if attempt == 2:
                        raise RuntimeError(f"version conflict: {plan.uri}")
                    fields = dict(current.fields)
                    fields["version"] = record.version + 1
                    current = WritePlan(current.uri, current.type, render(fields), fields, "update", record.version)
            await client.write(current.uri, current.content)
            written.append(plan.uri)
        except Exception:
            # A failed write remains recoverable in the SQLite buffer.
            continue
    return written
