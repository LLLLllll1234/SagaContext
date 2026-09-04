from __future__ import annotations
from .ov_client import OpenVikingClient
from .reconcile import WritePlan

async def apply(plans: list[WritePlan], client: OpenVikingClient) -> list[str]:
    written = []
    for plan in plans:
        try:
            await client.write(plan.uri, plan.content)
            written.append(plan.uri)
        except Exception:
            # A failed write remains recoverable in the SQLite buffer.
            continue
    return written
