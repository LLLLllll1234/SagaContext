# Claude Code 宿主准入与双宿主观察：设计

日期：2026-09-30。状态：**首轮审阅意见已合入；第二轮复核（Codex 版本漂移）已合入，待确认**。源码核对基线：`0b354bd`。

## 1. 背景与已拍板

2026-09-11 首轮 24 小时观察（rollout `5d6b4431…`）0 session / 0 candidate：本机只有 `~/.codex/hooks.json` 接入 `sagactl-hook`，日常主要使用的 Claude Code 未接入。

已拍板（2026-09-30）：

1. 先补 Claude Code 宿主准入，再做双宿主日常观察。
2. **单 rollout 多宿主**：同一个 rollout 同时接收 `codex` 与 `claude-code` 事件，共用 10 session / 20 candidate 配额。
3. **窗口期冻结精确版本**：准入探针固定 Claude Code 精确版本；观察期间关闭自动更新；版本漂移即拒绝事件并冻结窗口。
4. **自动更新在用户级关闭**：`~/.local/bin/claude` 是指向 `versions/<ver>` 的全局软链，任何项目的会话自动更新都会改变它。窗口期内在 `~/.claude/settings.json` 的 `env` 设置 `DISABLE_AUTOUPDATER=1`，收尾时恢复原值。本期的用户级配置改动只有这一处和已拍板 6 的 Codex updater 开关。
5. **可执行文件绝对路径随版本一起冻结**：activation 时把每个宿主的 `(version, executable_realpath)` 写入 run；preflight 与巡检只按冻结的绝对路径调用，不依赖 `PATH`（launchd 的默认 `PATH` 不含 `~/.local/bin`）。
6. **Codex 在当前版本上重新准入，与 G3-CC 同期**（2026-09-30）：Codex 已于 2026-09-30 15:59 自动更新到 `codex-cli 0.159.2`，本机不再保留 0.153.4。此前所有 Codex 准入和受控闭环均基于 0.153.4，不外推到 0.159.2。本期对 0.159.2 重跑 G3，通过后再更新 `config.toml` 中冻结的版本；未通过则 Codex 不进入观察映射。Codex 的自动更新在窗口期内同样关闭（§5）。

## 2. 已核实的现状

| 事实 | 位置 | 影响 |
|---|---|---|
| runtime 只认单一 `rollout_host` / `rollout_host_version`，session 查找、开会话、JournalEvent 都用 config 里的 host | `config.py:28`、`rollout.py:136-163` | 需改为按事件 host 处理，并校验 run 冻结的映射 |
| `trust_class` 固定为 `codex_verified_receipt` | `rollout.py:163` | 按宿主区分，Codex 保持原值 |
| hook 发送的版本头取自 config，daemon 再与 config 比较，**实际上不检测真实版本** | `hook.py:41`、`rollout.py:137` | 真实漂移检测须由 preflight + 巡检执行（§5） |
| preflight 只检查 `codex --version`、`codex features`、`~/.codex/hooks.json` | `observation.py:248` | 需按宿主泛化 |
| SessionStart 注入输出 `hookSpecificOutput.additionalContext`；`hook.py` 原样透传 | `rollout.py:528`、`hook.py:45` | 与 Claude Code 的 SessionStart 输出字段同名，是否被模型接收由探针确认 |
| `hook.py` 只转发 cwd 与 workspace 完全一致的四类事件，从不读 transcript | `hook.py:14` | 保持不变 |
| Claude Code 原生安装，2026-09 内已有 2.1.278 / 2.1.280 / 2.1.285 三个版本 | `~/.local/share/claude/versions` | 必须在窗口期关闭自动更新 |
| skill-evolution SessionEnd hook 后台运行 `claude -p`，工作目录继承触发会话的 cwd，带 `SKILL_EVOLUTION_REVIEW=1` | `~/.claude/hooks/skill-evolution-session-end.sh:115` | 在本仓库会触发 SagaContext hook，产生元会话样本，必须排除 |
| 本仓库没有 `.claude/` 目录 | — | 可用项目级 `settings.local.json` 限定接入范围 |
| `~/.local/bin/claude` 是指向 `~/.local/share/claude/versions/2.1.285` 的软链 | 本机 `ls -l` | 其他项目触发的自动更新同样会改变版本，项目级关闭无效 |
| watchdog plist 的 `EnvironmentVariables` 只有 `SAGACONTEXT_HOME`、`PYTHONPATH`，没有 `PATH` | `observation.py:232` | 巡检里直接调用 `claude` / `codex` 会找不到可执行文件 |
| `rollout_runs` 没有保存宿主映射的列；迁移按 `MIGRATION_1…5` 顺序追加 | `ledger/schema.py:361`、`schema.py:515` | 冻结映射需要 `MIGRATION_6` |
| `hook.py` 每次调用生成新的 `source_event_ref` | `hook.py:25` | Claude Code 的 SessionStart 在 compact 时复用 session_id，会重复记 `session_opened` 并重复注入 |
| `src/` 中没有代码按 `trust_class` 取值分支 | `grep trust_class src/` | 新增 `claude_code_verified_receipt` 不影响下游 |
| Codex 已是 `codex-cli 0.159.2`；`~/.local/bin/codex → standalone/current → releases/0.159.2-…`，`releases/` 下只剩这一个版本；`config.toml` 仍冻结 `0.153.4` | 本机 `codex --version`、`ls -l` | 按现配置开窗会 `host_version_changed`；需重新准入（已拍板 6） |
| Codex 托管更新由 app-server 的 updater 循环执行，读取 `~/.codex/app-server-daemon/settings.json` 的 `updater.autoUpdateEnabled`（缺省为 true）；本机该文件不存在 | 源码 `codex-rs/app-server-daemon/src/{settings,update_loop}.rs`（本地 checkout `c4017a87`，2026-09-12，**早于 0.159.2，未证实完全一致**） | 窗口期写入 `false`；由于是源码推断，realpath 巡检仍是必需的兜底 |
| `ledger/schema.py` 当前 `SCHEMA_VERSION = 5`；迁移在 `service.py:229` 的元组中逐项执行，`_validate_schema` 按版本逐级检查表和列 | `schema.py:1`、`service.py:229,271` | `MIGRATION_6` 需同步改这三处，`/health` 的 schema 版本随之变为 6 |

## 3. G3-CC：Claude Code 宿主事件准入探针

新增 `scripts/probe_claude_host.py` 与 `scripts/evaluate_claude_host_probe.py`。结构、脱敏规则和报告格式沿用 `probe_codex_host.py` / `g3_hook_recorder.py`；recorder 复用，只扩充枚举。

**隔离**

- 临时 Git 仓库做 cwd，合成 prompt 与固定 marker，不触及本仓库 workspace。
- `--setting-sources` 排除用户和项目设置，`--settings` 只加载探针 hook；用户全局 hook（vibe-island、skill-evolution）不加载。
- 认证与 provider 地址只从本机设置读入子进程环境，不写入 artifact。
- 第 0 步先验证上述隔离确实生效：探针 hook 触发、用户 hook 不触发。不生效就判 `blocked_environment`，不继续。`--setting-sources` 不覆盖 managed/policy 设置；本机若存在 managed hook，第 0 步会如实判 `blocked_environment`。
- 第 0 步同时验证环境变量继承：以 `SKILL_EVOLUTION_REVIEW=1` 启动 `claude -p`，确认 hook 子进程能读到该变量。读不到则 §4 的排除机制无效，判 `blocked_environment`。

**场景**：与 Codex 一致，四个场景均使用 `claude -p`。

| 场景 | 验证 |
|---|---|
| `baseline_with_duplicate` | 六类事件、稳定关联、重复事件可识别、SessionStart marker 被模型收到 |
| `hook_nonzero_exit` | hook 非零退出不阻断宿主 |
| `hook_timeout` | hook 超时的宿主行为与耗时 |
| `restart_recovery` | `--resume` 后 session_id 连续性 |
| `session_start_sources` | 记录 `startup` / `resume` / `compact` / `clear` 各自的 SessionStart 形状：`source` 字段、session_id 是否变化、matcher 过滤是否生效 |

**必需断言**：`executable_version_pinned`、`executable_path_pinned`、`hook_env_inherited`、`session_start_source_recorded`、`config_fingerprint_recorded`、`hooks_runtime_enabled`、`synthetic_isolation`、`event_{SessionStart,UserPromptSubmit,PreToolUse,PostToolUse,Stop,SessionEnd}`、`stable_event_linkage`、`duplicate_event_observed`、`session_start_context_received`，以及三个故障场景各自的断言。

**限制**：探针走 `-p` 非交互模式，日常使用是交互模式。交互模式下 SessionEnd、Stop 的触发时机可能不同，所以增加一次**手动交互冒烟**：用户在临时仓库打开 `claude`，发一句话后 `/exit`，recorder 记录事件形状。缺少这一步时，报告标注「交互模式未验证」，不判 passed。

参照：Codex 在 2026-09-09 的 final 运行中出现过 `failed_contract`（timeout / restart 两个场景）。Claude Code 的相应场景按实际结果判定，不预设会通过。

**Codex 0.159.2 重新准入（G3-Codex-0.159.2）**

- 沿用 `scripts/probe_codex_host.py` 与 `evaluate_codex_host_probe.py`，把 `PINNED_EXECUTABLE_VERSION` 改为 `codex-cli 0.159.2`；探针同样只按冻结的绝对路径调用。
- 固定模型沿用本机已配置的模型，报告中写明模型名；更换模型不视为同一准入。
- 新增记录项：compact 时 SessionStart 是否触发、payload 是否带 `source` 字段。它决定 §4 的 source 兜底是否也适用于 Codex。
- marker 消费复核（原 v5 的 21 项）在 0.159.2 上重跑。
- 判定独立于 G3-CC：任一宿主未通过，只把该宿主移出观察映射，另一个宿主仍可开窗。两者都未通过则不开窗。

## 4. 多宿主 runtime

**配置**：新增 `[rollout.hosts]` 映射，保留旧的单值字段作兼容，旧配置等价于只有一项的映射。

```toml
[rollout.hosts]
codex = "codex-cli 0.153.4"
claude-code = "2.1.285 (Claude Code)"
```

**activation 冻结**：activation 时把 host→`{version, executable}` 映射写入 run，并纳入 request digest。`executable` 取 activation 时解析出的可执行文件 realpath（Claude Code 为 `~/.local/share/claude/versions/<ver>`，Codex 为 `~/.codex/packages/standalone/releases/<ver>-<target>/bin/codex`）。ingest 以 run 的映射为准，不再读取当前 config。运行期间改 config 不影响已激活的窗口。

**digest 一致性**：`hosts` 以规范化 JSON（键排序）进入 `activate()` 的 `fields`，因而进入 `ControlAuth.request_digest`。预注册 grant 由调用方先算 digest，所以所有调用方（`observation.start_window`、`operator.py`、`scripts/run_guarded_runtime.py`）必须使用同一个 `hosts` 规范化函数，不各自拼装。

**schema**：新增 `MIGRATION_6`，`ALTER TABLE rollout_runs ADD COLUMN hosts_json TEXT`。配套改动：`SCHEMA_VERSION = 6`；`service.py` 迁移元组追加 `(6, MIGRATION_6)`；`_validate_schema` 在 `version >= 6` 时检查 `rollout_runs.hosts_json`。`/health` 返回的 schema 版本随之变为 6。**实现修订（2026-09-30）**：`rollout_sessions` 带有 `UNIQUE(rollout_id, host_session_id)`，两个宿主复用同一个 session id 时会冲突（多宿主测试复现）。新增 `MIGRATION_7` 重建该表并去掉这条约束（`session_id` 已按 `(owner, host, host_session_id)` 唯一），预约查找改按 `session_id`。`SCHEMA_VERSION` 最终为 7。旧 run 该列为 NULL，读取时视为 `{config 中的 rollout_host: {version: rollout_host_version, executable: null}}`；`executable` 为 null 的 run 不能通过新 preflight，只能走原有收尾路径。

**ingest**

- host 不在映射中 → `host_not_allowed`；版本头与映射不符 → `host_version_not_verified`。
- session 查找、`open_session`、JournalEvent 都使用事件自身的 host。`(owner, host, host_session_id)` 已有唯一约束，两个宿主的 session 不会互相冲突。
- `trust_class`：Codex 保持 `codex_verified_receipt`，Claude Code 为 `claude_code_verified_receipt`。
- 配额按 rollout 共享：`rollout_sessions` 已按 rollout 计数，无需改动。

**hook.py**

- 版本头取 `rollout.hosts[host]`。
- 环境变量 `SKILL_EVOLUTION_REVIEW=1` 或 `SAGACONTEXT_HOOK_DISABLE=1` 时直接输出 `{}`，不发送任何请求。

**SessionStart 的 source**：Claude Code 在 `startup`、`resume`、`compact`、`clear` 时都会触发 SessionStart。默认取舍（可在探针结果后调整）：
- `.claude/settings.local.json` 中 SessionStart 的 matcher 只写 `startup|resume`，compact / clear 不触发 SessionStart hook，避免重复 `session_opened` 与重复注入。
- `hook.py` 在 SessionStart 时把 payload 的 `source` 带入 record，daemon 对 `source ∉ {startup, resume}` 拒绝为 `session_start_source_not_allowed`，作为 matcher 失效时的兜底。
- 兜底**只对 `claude-code` 生效**。Codex 的 `hooks.json` 没有 matcher，compact 时的行为由 0.159.2 探针记录（§3）；在拿到证据之前不改变 Codex 的现有行为。缺少 `source` 字段的 Claude Code SessionStart 视为不合规并拒绝，不默认按 startup 处理。
- `/clear` 产生新的 session_id，其后的 UserPromptSubmit 照常开新 session 并占用配额。这是预期行为，在报告中计入。

**注入**：SessionStart 输出格式不变。Claude Code 是否实际消费注入，沿用现有「consumption receipt 另行记录」的规则，不因注入成功就推断已消费。

## 5. 版本冻结与漂移检测

**关闭自动更新**（两个宿主都要关，见已拍板 4、6）

| 宿主 | 写入位置 | 键 |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | `env.DISABLE_AUTOUPDATER = "1"` |
| Codex | `~/.codex/app-server-daemon/settings.json`（本机当前不存在） | `updater.autoUpdateEnabled = false` |

写入规则，两个文件相同：

- 只改上表中的那一个键，其他键原样保留。`~/.claude/settings.json` 含认证 token，任何日志、artifact、observation 文件都不复制整份内容。
- 原子写入（同目录临时文件 + `rename`），保留原文件权限；文件原本不存在时以 0600 新建。
- `start` 前在 `observations/<rollout_id>/host-settings.json` 只记录该键的原值（或「键不存在 / 文件不存在」），`finish` 时据此恢复：原本不存在的键删除，原本不存在的文件删除。
- 写入与读回校验都在 activation 之前完成，失败就不开窗。恢复失败记入 closure 的 `host_settings_restore`，不影响回滚结果，并在 `latest.json` 中保持可见，直到恢复成功。

**preflight 与巡检**

- preflight 按映射逐个检查宿主，一律使用冻结的绝对路径：`<executable> --version` 精确匹配；hook 已安装；上表中的键已生效。
- **巡检新增版本检查**，对映射中的**每个**宿主执行，不写死宿主名：
  - `<executable> --version` 与冻结版本一致；
  - 该宿主入口软链（`~/.local/bin/claude`、`~/.local/bin/codex`）的 realpath 仍等于冻结的 `executable`。
  - 任一不符就以 `host_version_changed` 冻结并回滚；可执行文件不存在或无法执行记为 `host_executable_missing`，同样冻结。
- 映射项的 `executable` 为 null（MIGRATION_6 之前的旧 run）时，巡检**跳过**这一项的版本与路径检查，其余停止条件照常。不因为缺少冻结路径而冻结窗口。

**已知限制**

- 已安装的新版本要等宿主重启后才生效，所以巡检检查的是可执行文件，不是正在运行的进程。
- 开窗前已在运行的 Claude Code 会话读不到新的 `env`，仍可能自行更新；preflight 只能保证之后新开的会话。进行中的更新会被 realpath 巡检发现并冻结窗口。
- Codex 的 updater 行为来自 2026-09-12 的本地源码 checkout，早于 0.159.2；是否仍读取同一文件、同一键，以 0.159.2 探针期间的实测为准：写入 `false` 后观察 updater 日志或 `current` 软链是否保持不变。

## 6. 接入范围

- hook 写入**本仓库** `.claude/settings.local.json`，不写入 `~/.claude/settings.json`。Claude Code 在 `.worktrees/*` 中是否会向上加载本仓库的 `.claude/` 未验证；无论是否加载，`hook.py:19` 的 cwd 精确匹配都会丢弃非本仓库根目录的事件，接入范围由这一条兜底。
- 四类事件 `SessionStart`（matcher `startup|resume`）、`UserPromptSubmit`、`Stop`、`SessionEnd` 均指向**绝对路径** `<repo>/bin/sagactl-hook claude-code <Event>`。preflight 按 `shlex.split` 精确比对，规则与 Codex 的 `observation.py:262` 一致。
- `.gitignore` 增加 `.claude/settings.local.json`。
- 与已有的全局 hook（vibe-island、skill-evolution）并存。skill-evolution 的后台 reviewer 由 §4 的环境变量排除。
- 接入后先在 `off + STOP` 状态下打开一次真实会话，确认 daemon 记录到 `mode_off` 拒绝回执，从而在不产生数据的前提下验证链路连通。

## 7. 数据流变化（需用户知悉）

接入后，本仓库 Claude Code 会话的**用户 prompt 原文**会进入本机 Ledger。窗口期内还会发送给已配置的外部 Judge（DeepSeek）生成 proposal。这与 Codex 的现有做法相同，但覆盖面扩大到日常主力宿主。transcript、工具输出和助手回复不会被读取或发送。

## 8. 验收与顺序

1. 单元测试，全量回归通过：
   - 多宿主 ingest；
   - `MIGRATION_6` 迁移，以及旧 run 读取与巡检跳过；
   - grant 预注册在多宿主下 digest 一致；
   - hook 排除；
   - SessionStart source 兜底只作用于 claude-code；
   - 巡检对每个宿主的版本与 realpath 检查；
   - 两个宿主设置文件的单键写入、原值记录、恢复（含「文件原本不存在」），以及 token 不出现在任何输出中。
2. G3-CC 探针：离线 fixture 测试 → 真实运行 → 独立评价 → 手动交互冒烟 → 报告 `docs/probes/<日期>-g3-claude-code.md`。
2b. G3-Codex-0.159.2：重跑 G3 与 marker 消费复核 → 独立评价 → 报告 `docs/probes/<日期>-g3-codex-0.159.2.md`。通过后才更新 `config.toml` 的冻结版本。
3. 接入 `.claude/settings.local.json`，完成 off 状态连通验证；同时在真实环境触发一次 skill-evolution reviewer，确认 daemon 没有收到它的事件。
4. 用户授权后开窗：`bin/sagacontext-observe start --hours 24`，沿用现有边界：本项目、仅 new proposal、10/20 配额、代理审核、巡检收尾。

不在本期范围：active 模式、跨项目 / 全局 scope、transcript 读取、控制台改动。
