from __future__ import annotations
from .ov_client import OpenVikingClient

async def publish(client: OpenVikingClient, uri: str, content: str, group: str) -> dict:
    """Explicit team publish boundary; callers must opt in with a group."""
    if not group.strip(): raise ValueError("group is required for team publish")
    return await client.write(uri, content)
