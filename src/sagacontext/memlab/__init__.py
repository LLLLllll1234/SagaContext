"""SagaContext Memory Lab: read-only observation of coding-agent memory mechanics.

Every payload produced here is tagged with an evidence level:

- ``source``     fact anchored in the agent's source code (verified by grep)
- ``local_data`` fact read from the user's local data files/databases
- ``inference``  something this tool derived by joining the two

Nothing in this package writes to ``~/.codex`` or ``~/.claude``.
"""
