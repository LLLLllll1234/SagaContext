"""Source-code fact registry for Codex memory mechanics.

Each fact names the file and a regex anchor inside the local Codex checkout.
At request time the anchor is re-grepped, so a fact is reported as
``verified`` (anchor found, with the current line number), ``drifted``
(file exists, anchor missing) or ``missing`` (file absent). The narrative text
is what we believe the code does; the anchor is how the reader can check.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, asdict, field
from functools import lru_cache
from pathlib import Path
from typing import Any


def default_source_root() -> Path:
    return Path(os.environ.get("SAGACONTEXT_CODEX_SOURCE", str(Path.home() / "Downloads" / "codex"))).expanduser()


@dataclass(frozen=True)
class SourceFact:
    id: str
    topic: str            # trigger | selection | stage1 | stage2 | files | recall | retention | compaction | rollout
    title: str
    detail: str           # what the code does, in Chinese for the UI
    file: str             # relative to codex-rs/
    anchor: str           # regex that must appear in the file
    constants: dict[str, Any] = field(default_factory=dict)


FACTS: tuple[SourceFact, ...] = (
    # ---- long-term memory: trigger ----
    SourceFact(
        "trigger.turn_start", "trigger", "记忆生成在“新一轮开始”时触发，而不是会话结束",
        "唯一的生产调用点在 app-server 的 turn_start 处理器里：当一轮有用户输入且成功启动时，"
        "spawn 一个后台任务 start_memories_startup_task。CLI/TUI/exec 路径没有调用。",
        "app-server/src/request_processors/turn_processor.rs", r"start_memories_startup_task\(",
    ),
    SourceFact(
        "trigger.gates", "trigger", "三道门：非 ephemeral、MemoryTool 特性开启、非子 agent",
        "start_memories_startup_task 开头直接 return：config.ephemeral、features 未启用 MemoryTool、"
        "或 source.is_non_root_agent() 任一成立就不生成记忆。",
        "memories/write/src/start.rs", r"is_non_root_agent\(\)",
    ),
    SourceFact(
        "trigger.rate_limit", "trigger", "配额守卫：剩余额度低于 25% 就跳过",
        "guard.rs 读取 Codex 后端用量；已用超过 100 - min_rate_limit_remaining_percent（默认 25）"
        "或已触发限流时不生成。非 Codex 登录方式始终放行。",
        "config/src/types.rs", r"DEFAULT_MEMORIES_MIN_RATE_LIMIT_REMAINING_PERCENT: i64 = (\d+)",
        {"min_rate_limit_remaining_percent": 25},
    ),
    # ---- selection ----
    SourceFact(
        "selection.candidates", "selection", "每次最多抽 2 个“别的”会话，且要闲置 6 小时、10 天内",
        "候选会话必须：来源是交互式（Cli/VSCode/atlas/chatgpt）、memory_mode='enabled'、不是当前会话、"
        "updated_at 在 max_rollout_age_days（10）内且早于 min_rollout_idle_hours（6）前；"
        "每轮最多认领 max_rollouts_per_startup（2）个。",
        "config/src/types.rs", r"DEFAULT_MEMORIES_MAX_ROLLOUTS_PER_STARTUP: usize = (\d+)",
        {"max_rollouts_per_startup": 2, "max_rollout_age_days": 10, "min_rollout_idle_hours": 6},
    ),
    SourceFact(
        "selection.sources", "selection", "只有交互式会话来源才会被抽取记忆",
        "INTERACTIVE_SESSION_SOURCES = [Cli, VSCode, Custom(atlas), Custom(chatgpt)]。"
        "内部 agent（如记忆整合 agent 自己）不会被再次抽取。",
        "rollout/src/lib.rs", r"INTERACTIVE_SESSION_SOURCES",
    ),
    SourceFact(
        "selection.memory_mode", "selection", "memory_mode 在会话创建时定死，接触外部内容会被标为 polluted",
        "generate_memories=false 时线程创建即 memory_mode='disabled'；"
        "disable_on_external_context 开启时，web 搜索/工具搜索结果会把线程标为 'polluted'，之后不再抽取。",
        "core/src/stream_events_utils.rs", r"mark_thread_memory_mode_polluted_if_external_context",
    ),
    SourceFact(
        "selection.lease", "selection", "没有锁文件，靠 SQLite jobs 表的租约做互斥",
        "jobs(kind, job_key) 主键 + lease_until（3600 秒）+ ownership_token UUID 充当锁；"
        "阶段二是单例任务 job_key='global'，成功后 6 小时冷却。",
        "state/src/runtime/memories.rs", r"PHASE2_SUCCESS_COOLDOWN_SECONDS: i64 = 6 \* 60 \* 60",
        {"job_lease_seconds": 3600, "phase2_cooldown_hours": 6},
    ),
    # ---- stage 1 ----
    SourceFact(
        "stage1.input_filter", "stage1", "阶段一输入只保留对话项，压缩摘要与 developer 消息被丢弃",
        "序列化 rollout 时只保留 ResponseItem / InterAgentCommunication；SessionMeta、Compacted、TurnContext、"
        "TokenUsageRecord、EventMsg 全部映射为 None。developer 角色消息、AGENTS.md 与 <skill> 片段也被剥离。",
        "memories/write/src/rollout_input.rs", r"RolloutItem::Compacted\(_\)",
    ),
    SourceFact(
        "stage1.truncation", "stage1", "输入按 token 预算头尾截断：上下文窗口 × 有效比例 × 70%",
        "rollout_token_limit = context_window × effective_context_window_percent × 70%，取不到时回退 150k tokens；"
        "先 redact_secrets 再截断。",
        "memories/write/src/prompts.rs", r"rollout_token_limit",
    ),
    SourceFact(
        "stage1.prompt", "stage1", "阶段一提示词：Memory Writing Agent Phase 1，允许且偏好“无输出”",
        "系统提示要求返回一个 JSON：rollout_summary、rollout_slug、raw_memory。明确写着 No-op is allowed and preferred，"
        "并要求 Overindex on user messages / Underindex on assistant messages。",
        "memories/write/templates/memories/stage_one_system.md", r"No-op is allowed and preferred",
    ),
    SourceFact(
        "stage1.model", "stage1", "阶段一默认模型 gpt-5.6-luna，reasoning=Low，严格 JSON schema",
        "memories.extract_model 未配置时用 DEFAULT_MEMORY_EXTRACTION_PREFERRED_MODEL；最多 8 个会话并发抽取。",
        "model-provider/src/provider.rs", r"DEFAULT_MEMORY_EXTRACTION_PREFERRED_MODEL: &str = \"([^\"]+)\"",
    ),
    SourceFact(
        "stage1.upsert", "stage1", "阶段一结果按 thread_id upsert，空输出会删掉旧行",
        "mark_stage1_job_succeeded：ON CONFLICT(thread_id) DO UPDATE，仅当新 source_updated_at ≥ 旧值；"
        "模型返回空则 mark_stage1_job_succeeded_no_output 删除该线程已有行，然后把全局整合任务置为 pending。",
        "state/src/runtime/memories.rs", r"fn mark_stage1_job_succeeded",
    ),
    # ---- stage 2 ----
    SourceFact(
        "stage2.selection", "stage2", "阶段二最多整合 256 条，30 天未用的不进入",
        "get_phase2_input_selection：raw_memory 非空、last_usage（或 source_updated_at）在 30 天内，"
        "按 usage_count DESC、最近使用 DESC 排序，只取仍为 memory_mode='enabled' 的线程。",
        "config/src/types.rs", r"DEFAULT_MEMORIES_MAX_RAW_MEMORIES_FOR_CONSOLIDATION: usize = (\d+)",
        {"max_raw_memories_for_consolidation": 256, "max_unused_days": 30},
    ),
    SourceFact(
        "stage2.agent", "stage2", "阶段二是一个真实的沙箱 agent，cwd 就是 ~/.codex/memories",
        "spawn_consolidation_agent 新建 CodexThread（SessionSource::Internal(MemoryConsolidation)），"
        "ephemeral=true、generate_memories=false、approval=Never、WorkspaceWrite 只允许写 memories 根目录、禁网。"
        "默认模型 gpt-5.6-terra，reasoning=Medium。",
        "model-provider/src/provider.rs", r"DEFAULT_MEMORY_CONSOLIDATION_PREFERRED_MODEL: &str = \"([^\"]+)\"",
    ),
    SourceFact(
        "stage2.git_diff", "stage2", "阶段二靠 git diff 告诉 agent“什么变了”，无差异则不起 agent",
        "prepare_memory_workspace 建 git 基线，同步 rollout_summaries 后 git diff；无差异且产物合法直接成功；"
        "有差异则写 phase2_workspace_diff.md 供 agent 先读。",
        "memories/write/src/phase2.rs", r"write_workspace_diff\(&root",
    ),
    SourceFact(
        "stage2.prompt", "stage2", "整合提示词：INIT / INCREMENTAL UPDATE 两种模式，只删“被删除输入支撑的记忆”",
        "consolidation.md：先读 diff 文件；rollout_summaries 里的每个变更都是权威的；"
        "Delete only memory supported by deleted inputs；输出 MEMORY.md、skills/*、memory_summary.md。",
        "memories/write/templates/memories/consolidation.md", r"INCREMENTAL UPDATE",
    ),
    # ---- files ----
    SourceFact(
        "files.raw_memories", "files", "raw_memories.md 由 Rust 全量重建，按 thread_id 升序",
        "rebuild_raw_memories_file 每次整合前整文件替换，每个线程一个 ## Thread 块，"
        "带 updated_at / cwd / rollout_path / rollout_summary_file 头。",
        "memories/write/src/storage.rs", r"fn rebuild_raw_memories_file",
    ),
    SourceFact(
        "files.prune_summaries", "files", "不在本次选集里的 rollout_summaries/*.md 会被删除——这就是“遗忘”信号",
        "prune_rollout_summaries 删除 keep 集合之外的文件；删除会出现在 git diff 里，agent 据此移除对应记忆。",
        "memories/write/src/storage.rs", r"fn prune_rollout_summaries",
    ),
    SourceFact(
        "files.memory_md", "files", "MEMORY.md 与 memory_summary.md 由模型写，Rust 只校验存在性和 v1 首行",
        "validate_consolidation_artifacts_for_version 检查文件存在、非符号链接、memory_summary.md 首行为 v1；"
        "内容结构（# Task Group / ### rollout_summary_files / thread_id=）完全由提示词约定。",
        "memories/write/src/workspace.rs", r"fn validate_consolidation_artifacts",
    ),
    # ---- recall ----
    SourceFact(
        "recall.injection", "recall", "召回 = 把 memory_summary.md 截断到 2500 tokens 塞进 developer 指令",
        "build_memory_tool_developer_instructions 读取 memory_summary.md，按 2_500 tokens 截断，"
        "渲染 read_path.md 模板（包含 MEMORY_SUMMARY BEGINS/ENDS 区块与 Quick memory pass 步骤）。",
        "ext/memories/src/lib.rs", r"MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT: usize = ([\d_]+)",
        {"summary_token_limit": 2500},
    ),
    SourceFact(
        "recall.quick_pass", "recall", "模型被要求做“快速记忆通道”：先查 MEMORY.md，再最多打开 1-2 个摘要",
        "read_path.md：Skim MEMORY_SUMMARY → Search MEMORY.md → 只在 MEMORY.md 指向时打开 rollout_summaries/skills → "
        "需要精确证据时才搜 rollout_path；预算 4-6 步。",
        "ext/memories/templates/memories/read_path.md", r"Quick memory pass",
    ),
    SourceFact(
        "recall.citation", "recall", "模型引用记忆要输出 <oai-mem-citation>，其中的 rollout_ids 触发 usage_count+1",
        "citations.rs 解析 <citation_entries>（file:start-end|note=[...]）与 <rollout_ids>；"
        "stream_events_utils 在 assistant 消息完成时调用 record_stage1_output_usage：usage_count+1、last_usage=now。",
        "memories/read/src/citations.rs", r"<citation_entries>",
    ),
    SourceFact(
        "recall.usage_update", "recall", "usage_count/last_usage 只在“模型真的引用了”时更新",
        "record_stage1_output_usage 对引用到的每个 thread_id 执行 usage_count = COALESCE(usage_count,0)+1。"
        "这是后续阶段二排序与 30 天保留的依据。",
        "state/src/runtime/memories.rs", r"pub async fn record_stage1_output_usage",
    ),
    # ---- retention / supersede ----
    SourceFact(
        "retention.prune_db", "retention", "30 天未使用且未进入上次整合的 stage1 行会被清理",
        "prune_stage1_outputs_for_retention：删除 selected_for_phase2=0 且 COALESCE(last_usage, source_updated_at) < now-30d 的行，"
        "每次最多 200 条。上次整合用到的行受保护。",
        "state/src/runtime/memories.rs", r"fn prune_stage1_outputs_for_retention",
    ),
    SourceFact(
        "retention.selected_flag", "retention", "selected_for_phase2 每次整合成功后先全清零再标记本次输入",
        "mark_global_phase2_job_succeeded 重置所有行为 0，再把本次喂给 agent 的行置 1 并记录快照时间。",
        "state/src/runtime/memories.rs", r"fn mark_global_phase2_job_succeeded",
    ),
    SourceFact(
        "retention.adhoc", "retention", "用户要求“记住/改掉”时，模型只能写 extensions/ad_hoc/notes/，不能直接改记忆文件",
        "read_path.md：Updating memories 仅在用户明确要求时，写一个 <timestamp>-<slug>.md 到 ad_hoc/notes；"
        "下次整合时 agent 把 notes 视为权威信息（但不是指令）并打上 [ad-hoc note] 标签。",
        "ext/memories/templates/memories/read_path.md", r"extensions/ad_hoc/notes",
    ),
    # ---- compaction ----
    SourceFact(
        "compaction.threshold", "compaction", "自动压缩阈值默认 = 上下文窗口的 90%",
        "auto_compact_token_limit 未配置时为 (context_window * 9) / 10；配置了也会被夹到 ≤90%。"
        "effective_context_window_percent（默认 95）只用于硬上限。",
        "protocol/src/openai_models.rs", r"\(context_window \* 9\) / 10",
        {"auto_compact_ratio": 0.9, "effective_window_percent": 95},
    ),
    SourceFact(
        "compaction.token_estimate", "compaction", "判定用的 token 数不是 API 原始值，而是“上次用量 + 之后新增项的估算”",
        "context_window.rs 取 sess.get_total_token_usage()；history.rs 用 last_token_usage.total_tokens 加上"
        "最后一个模型输出之后追加项的估算。",
        "core/src/session/context_window.rs", r"token_limit_reached",
    ),
    SourceFact(
        "compaction.phases", "compaction", "两个时机：轮前（PreTurn）与轮中（MidTurn 每次采样后）",
        "run_pre_sampling_compact 在轮开始前检查；采样循环里 should_roll_over = needs_follow_up && (新窗口请求 || 超限)，"
        "成立则在最后一条用户消息之前注入并 continue。手动 /compact 走 CompactTask。",
        "core/src/session/turn.rs", r"should_roll_over",
    ),
    SourceFact(
        "compaction.prompt", "compaction", "压缩提示词：CONTEXT CHECKPOINT COMPACTION，写给“下一个 LLM”的交接摘要",
        "prompt.md 要求包含：当前进展与关键决定、重要约束或用户偏好、剩余步骤、关键数据。"
        "summary_prefix.md 是那句 Another language model started to solve this problem...",
        "prompts/templates/compact/prompt.md", r"CONTEXT CHECKPOINT COMPACTION",
    ),
    SourceFact(
        "compaction.kept", "compaction", "压缩后只保留：≤20k tokens 的历史用户消息 + 一条 user 角色的摘要",
        "build_compacted_history 从新到旧收集真实用户消息直到 COMPACT_USER_MESSAGE_MAX_TOKENS=20_000，"
        "溢出的那条被截断、更旧的丢弃；assistant 消息、reasoning、工具调用与输出全部丢弃。",
        "core/src/compact.rs", r"COMPACT_USER_MESSAGE_MAX_TOKENS: usize = ([\d_]+)",
        {"user_message_budget_tokens": 20000},
    ),
    SourceFact(
        "compaction.retry", "compaction", "压缩本身超窗时，逐条删最早的项重试",
        "compact.rs 在 ContextWindowExceeded 时 history.remove_first_item() 后重试；只剩一项仍失败则 set_total_tokens_full 报错。",
        "core/src/compact.rs", r"remove_first_item\(\)",
    ),
    SourceFact(
        "compaction.record", "compaction", "压缩落盘为 rollout 里的 compacted 行，带 replacement_history",
        "replace_compacted_history 依次写入 Compacted（message、replacement_history、window_number、window_id…）、"
        "WorldState、TurnContext（轮中）、ThreadSettingsApplied，然后 recompute_token_usage。",
        "core/src/session/mod.rs", r"fn replace_compacted_history",
    ),
    SourceFact(
        "compaction.resume", "compaction", "恢复会话时，用最新 compacted 的 replacement_history 作为历史种子",
        "rollout_reconstruction.rs：找最新带 replacement_history 的 Compacted → history.replace_annotated → 再回放其后的项。",
        "core/src/session/rollout_reconstruction.rs", r"replacement_history",
    ),
    SourceFact(
        "compaction.vs_memory", "compaction", "压缩摘要与长期记忆是两条独立链路：记忆抽取不读压缩摘要",
        "memories/write 在序列化 rollout 时把 RolloutItem::Compacted 映射为 None；没有任何代码读取 CompactedItem.message。"
        "长期记忆看到的是被过滤、截断后的原始对话，而不是压缩产物。",
        "memories/write/src/phase1.rs", r"RolloutItem::Compacted\(_\)",
    ),
    # ---- rollout format ----
    SourceFact(
        "rollout.items", "rollout", "rollout.jsonl 每行 {timestamp, type, payload}，type 是 RolloutItem 的 snake_case",
        "RolloutItem = SessionMeta | ResponseItem | InterAgentCommunication | Compacted | TurnContext | TokenUsageRecord | "
        "WorldState | SecurityRiskScore | RetainedContext | EventMsg | RealtimeItem。",
        "history/src/lib.rs", r"Compacted\(CompactedItem\)",
    ),
    SourceFact(
        "rollout.token_count", "rollout", "event_msg/token_count 携带 total、last 与 model_context_window",
        "TokenUsageInfo { total_token_usage, last_token_usage, model_context_window }；"
        "last_token_usage.input_tokens 近似当前上下文占用，total 是累计。",
        "protocol/src/protocol.rs", r"pub struct TokenUsageInfo",
    ),
)


@lru_cache(maxsize=8)
def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def source_revision(root: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(root), "exists": root.exists(), "revision": None, "commit_date": None, "subject": None}
    if not root.exists():
        return info
    try:
        out = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H%n%cI%n%s"],
                                      text=True, stderr=subprocess.DEVNULL, timeout=3).strip().splitlines()
        if len(out) >= 3:
            info.update(revision=out[0], commit_date=out[1], subject=out[2])
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def verify_fact(fact: SourceFact, root: Path) -> dict[str, Any]:
    path = root / "codex-rs" / fact.file
    text = _read(str(path))
    result = asdict(fact)
    result.update(path=str(path), status="missing", line=None, snippet=None, evidence="source")
    if text is None:
        return result
    match = re.search(fact.anchor, text)
    if not match:
        result["status"] = "drifted"
        return result
    line = text.count("\n", 0, match.start()) + 1
    lines = text.splitlines()
    result.update(status="verified", line=line, snippet=lines[line - 1].strip()[:200])
    if match.groups():
        result["captured"] = match.group(1)
    return result


# Narrative parameters used by the UI: name -> (fact id, constants key, or None to use the anchor capture).
PARAMETERS: dict[str, tuple[str, str | None]] = {
    "extract_model": ("stage1.model", None),
    "consolidation_model": ("stage2.agent", None),
    "summary_token_limit": ("recall.injection", None),
    "user_message_budget_tokens": ("compaction.kept", None),
    "max_rollouts_per_startup": ("selection.candidates", None),
    "max_rollout_age_days": ("selection.candidates", "max_rollout_age_days"),
    "min_rollout_idle_hours": ("selection.candidates", "min_rollout_idle_hours"),
    "max_raw_memories_for_consolidation": ("stage2.selection", None),
    "max_unused_days": ("stage2.selection", "max_unused_days"),
    "auto_compact_ratio": ("compaction.threshold", "auto_compact_ratio"),
}
# Registry beliefs, used when a value cannot be read from the local checkout.
PARAMETER_DEFAULTS: dict[str, Any] = {
    "extract_model": "gpt-5.6-luna", "consolidation_model": "gpt-5.6-terra",
    "summary_token_limit": 2500, "user_message_budget_tokens": 20000,
    "max_rollouts_per_startup": 2, "max_rollout_age_days": 10, "min_rollout_idle_hours": 6,
    "max_raw_memories_for_consolidation": 256, "max_unused_days": 30, "auto_compact_ratio": 0.9,
}


def _literal(value: str) -> Any:
    digits = value.replace("_", "")
    return int(digits) if digits.isdigit() else value


def resolve_parameters(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each parameter's value and how it was checked.

    ``read``: taken from the anchor capture in local source. ``anchor``: the fact's anchor
    was found but this value is the registry's belief. ``unverified``: the anchor is
    missing or drifted, so the value is only the registry default.
    """
    by_id = {f["id"]: f for f in facts}
    result = {}
    for name, (fact_id, key) in PARAMETERS.items():
        fact = by_id[fact_id]
        value, check = PARAMETER_DEFAULTS[name], "unverified"
        if fact["status"] == "verified":
            if key is None and fact.get("captured") is not None:
                value, check = _literal(fact["captured"]), "read"
            else:
                value, check = fact["constants"].get(key, value) if key else value, "anchor"
        result[name] = {"value": value, "check": check, "fact_id": fact_id}
    return result


def verify_all(root: Path | None = None) -> dict[str, Any]:
    root = root or default_source_root()
    facts = [verify_fact(f, root) for f in FACTS]
    counts = {"verified": 0, "drifted": 0, "missing": 0}
    for f in facts:
        counts[f["status"]] += 1
    return {"source": source_revision(root), "facts": facts, "counts": counts,
            "params": resolve_parameters(facts)}
