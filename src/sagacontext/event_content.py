"""Conservative admission of explicitly stated decisions from new prompts."""
import re

SENSITIVE = re.compile(r"sk-[\w-]{8,}|-----BEGIN|(?:api[_ -]?key|password|secret|token)\s*[:=]|https?://|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
DURABLE = re.compile(r"remember|for this project|we decided|from now on|记住|约定|决定|本项目", re.I)


def event_payload(record: dict, event: str) -> dict:
    result = {"hook_event_name": event}
    text = record.get("prompt", record.get("text", ""))
    if (event == "UserPromptSubmit" and isinstance(text, str) and 0 < len(text) <= 4000
            and DURABLE.search(text) and not SENSITIVE.search(text)):
        result["text"] = text
    return result
