# Workspace Console — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 本项目本次采用逐项执行路线，使用 executing-plans；不需要用户再次选择执行方式，也不在规划阶段启动子代理。

**Goal:** 提供按项目/workspace 组织的只读四卡首页，并能从任务、会话、记忆变化和待审批次追溯到持久化证据。

**Architecture:** FastAPI 同源提供 `/console/` 页面和 `/console/v1` 读取 API。独立只读连接从 Ledger 聚合数据，进程状态另取无副作用快照；前端使用 React 路由和带完整作用域的查询缓存。写入、审批、Judge、后端探测不在页面读取路径。

**Tech Stack:** Python >=3.11、现有 FastAPI/Pydantic/SQLite；React、TypeScript、Vite、TanStack Query、React Router；前端 Vitest/Testing Library，浏览器验收使用 Playwright。前端依赖在实施时选互相兼容的版本并提交 package-lock.json。

**Spec:** [已批准设计](../specs/2026-09-11-workspace-console-design.md)。[已审阅线框](../specs/assets/2026-09-11-workspace-overview-v1.html)是信息架构参照，不作为产品源码复用。

**Status:** 2026-09-11 计划已编写并自审；以下实现、测试和构建均未执行。源码核对基线 `13f0bc6`。

## Global Constraints

- 用户已批准「运行控制台 → 工作区优先 → 项目总览网格」与第一期只读范围，不重复请求该设计批准。
- 第一阶段不加入 approve/reject、正文编辑、STOP、rollback 或 activation 的前端写入按钮。
- 一个 owner 最多一个处于 running/draining/stopping/cleanup_required 的 rollout。
- 不显示可选的 active 模式。
- 不在读取请求中新建 Application、运行迁移或调用会更新状态的 rollout.mode/_run()。
- 不能只用 ledger_sequence 做缓存版本。
- 第一期固定可见页每 5 秒刷新，后台页暂停；报错按 5/10/20/30 秒上限退避。
- 接口设置分页上限 100，默认 50；首页最近活动 20 条。
- 零有效样本显示空值；yes+no 为有效分母，unknown 单列。
- 原始 event payload、凭据、基础设施地址、完整 transcript 不序列化到浏览器；operator token 不下发。
- 测试仅使用临时数据库和合成事件，禁止加载本机配置或访问真实 Judge、宿主与后端。
- 提交仅包含当前任务文件；现有未提交 probe 报告和 artifacts 保持其原有工作状态。

## 文件与执行顺序

| 任务 | 主要文件 | 边界与独立交付 |
|---|---|---|
| 1 | `src/sagacontext/console/{__init__,db,models,runtime}.py` | 只读事务与纯状态计算 |
| 2 | `src/sagacontext/console/{queries,service}.py` | 工作区首页、分页任务/会话/活动与运行统计 |
| 3 | `src/sagacontext/console/{details,serialize}.py`、`src/sagacontext/ledger/access.py` | 版本、提案、证据和作用域过滤 |
| 4 | `src/sagacontext/console/{router,security}.py`、`daemon.py` | 完整只读 HTTP 契约 |
| 5 | `web/` | 类型化请求、路由、四卡首页与刷新状态 |
| 6 | `web/src/pages/`、`web/src/components/` | 各对象列表/详情与浏览器导航 |
| 7 | `src/sagacontext/console/static.py`、`pyproject.toml`、`web/vite.config.ts` | 同源静态构建、离线 fixture 和完整验收 |

顺序为 1 → 2 → 3 → 4 → 5 → 6 → 7。任务内先验证关键风险，再完成最小实现；不为样式或静态文案逐项写测试。

## Task 1：建立只读快照与运行状态边界

**Files**

- Create: `src/sagacontext/console/__init__.py`, `db.py`, `models.py`, `runtime.py`。
- Test: `tests/console/__init__.py`, `tests/console/test_snapshot.py`, `tests/console/test_runtime.py`。
- Read: `ledger/service.py` 的 Ledger 构造、sequence，`rollout.py` 的 _run/mode，`scheduler.py` 的线程状态。

**Interfaces**

- `read_snapshot(path: Path) -> ContextManager[sqlite3.Connection]`：每次调用独立连接，默认只读、短事务、可靠关闭。
- `ConsoleReadError(code: str)`：code 取 `ledger_missing`, `schema_unsupported`, `ledger_busy`, `data_invalid`。
- `effective_state(run: dict | None, configured_mode: str, stop_active: bool, now: datetime) -> dict`：返回 configured_mode、persisted_status、effective_mode、block_reason。无网络/数据库写入。
- `ReadLedger(db: sqlite3.Connection, owner_id: str)`：仅 db/owner_id，供已有只读 report 使用；没有写入方法。
- `Page[T]`：items、next_cursor；`SnapshotMeta`：observed_at、schema_version、ledger_sequence、request_id。DTO 用 Pydantic，额外字段禁止。

- [x] **1. 编写不可写、缺库不创建和过期状态不写库的测试。** 在临时目录用 Ledger 建库后关闭写连接；保留逻辑快照，不比较可能受 WAL 读取影响的 shm 文件元数据。

```python
def test_snapshot_rejects_writes(tmp_path):
    import sqlite3
    import pytest
    from sagacontext.ledger import Ledger
    from sagacontext.console.db import read_snapshot
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.close()
    with read_snapshot(path) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM owners")
        assert db.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 1
```

Ledger 使用显式 close，不支持上下文协议。另一个测试对不存在的路径调用 read_snapshot，断言 `ledger_missing` 且路径仍不存在。

- [ ] **2. 执行红灯检查。** `PYTHONPATH=src .venv/bin/python -m pytest tests/console/test_snapshot.py tests/console/test_runtime.py -q`，预期因新增模块尚未实现失败。
- [x] **3. 实现独立只读连接。** URI 使用 `path.resolve().as_uri() + '?mode=ro'`、`uri=True`、`isolation_level=None`、短 busy timeout；设置 query_only 后 BEGIN，在同一事务中检查 schema_version 和 sequence。不得使用 `immutable=1`，它可能忽略活跃 WAL。finally 中 rollback/close，SQLite lock 错误映射 ledger_busy。

```python
db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                     uri=True, isolation_level=None, timeout=0.25)
db.row_factory = sqlite3.Row
db.execute("PRAGMA query_only=ON")
db.execute("BEGIN")
```

版本来自 `SELECT version FROM schema_migrations ORDER BY version`，必须等于 `list(range(1, SCHEMA_VERSION + 1))`，本基线为 1 至 5；不使用未维护的 PRAGMA user_version，也不创建新版本元数据。缺表、断档、未来版本返回 schema_unsupported。`effective_state` 的阻断顺序固定为无 run → config off → STOP → deadline → run 不允许 → 保留 shadow/guarded；stopping、stopped、cleanup_required 都显示 off，但原状态保留。解析失败显示 unknown/data_invalid。

- [x] **4. 运行上述测试并补足 STOP、deadline、cleanup_required、无 run、带时区时间案例。** 对相同输入固定 now，返回值必须稳定，不能调用 RolloutRuntime。
- [x] **5. 提交。** `git add src/sagacontext/console tests/console` 后核对暂存文件，提交 `feat(console): add read-only snapshots and pure runtime status`。

## Task 2：工作区聚合与稳定分页

**Files**

- Create: `src/sagacontext/console/queries.py`, `service.py`。
- Modify: `src/sagacontext/console/models.py`。
- Test: `tests/console/conftest.py`, `test_queries.py`, `test_overview.py`。
- Read: `daily_report.py`, `ledger/schema.py`，其中 tasks 属 project，sessions/rollouts 属 workspace。

**Interfaces**

- `ConsoleReadService(path: Path, owner_id: str)`，每次公开调用独立使用任务 1 的 read_snapshot。
- `projects() -> dict`；`overview(workspace_id: str, start: datetime, end: datetime, runtime: dict) -> dict`。
- `sessions(workspace_id: str, cursor: str | None, limit: int) -> dict`。
- `tasks(project_id: str, workspace_id: str | None, cursor: str | None, limit: int) -> dict`。
- `activity(workspace_id: str, start: datetime, end: datetime, kinds: list[str], cursor: str | None, limit: int) -> dict`。
- `batches(workspace_id: str, status: str | None, cursor: str | None, limit: int) -> dict`。
- `rollout(workspace_id: str, rollout_id: str) -> dict`。
- 普通响应为 `{meta, data}`；分页响应为 `{meta, data: {items, next_cursor}}`。overview.data 包含 runtime、tasks、sessions、rollout、memory_changes、activity 六个固定键；每项为 `{availability, value, reason}`，availability 为 available/unavailable。

- [x] **1. 建立双工作区 fixture 和归属测试。** conftest 在 tmp_path 下建两个目录并注册为同一项目不同 workspace，再增加不同 project 与 owner 的隔离数据；通过既有 Ledger/EventJournal/BatchService 建立 session/task/evidence。合成运行行可直接写入临时 fixture DB，但不调用本机 activate。固定 UTC 时间。

```python
def test_workspace_sessions_do_not_leak(console_case):
    service = console_case.service
    result = service.sessions(console_case.workspace_a, None, 50)
    ids = {row["session_id"] for row in result["data"]["items"]}
    assert console_case.session_a in ids
    assert console_case.session_b not in ids
```

`console_case` fixture 明确提供 service、workspace_a/workspace_b、session_a/session_b、project_a/project_b、owner_a/owner_b、path、start/end；任务 3/4 复用它。另测同项目共享任务仅标关联、不复制成新任务。

- [ ] **2. 运行红灯。** `PYTHONPATH=src .venv/bin/python -m pytest tests/console/test_queries.py tests/console/test_overview.py -q`。
- [x] **3. 实现查询归属与聚合。** 先由 owner+workspace 查 project_locations，再 JOIN 业务表；所有详情 ID 同时校验关联。聚合在单事务内完成。按 rollout_commits 的独立 `(rollout_id, proposal_id, memory_id, revision)` 计已提交操作，禁止连 evidence 后重复计数。投影使用 distinct outbox/operation ID，不把重试 attempt 当多份记忆。

```sql
SELECT s.session_id,s.host,s.opened_at,s.closed_at
FROM sessions s
WHERE s.owner_id=? AND s.workspace_id=?
ORDER BY s.opened_at DESC,s.session_id DESC
LIMIT ?
```

列表 keyset cursor 包含最后的排序元组和 filter digest（含 owner/project/workspace/time/type）。cursor 来自客户端，只作为分页位置，不能作为授权；作用域变化导致 digest 不匹配时返回 400。取 limit+1 生成 next_cursor。活动事件取 received_at，其他记录取 created_at，同一条事实不同时从业务表与 audit 重复列出；event 的发生时间单独展示。

rollout 先验证归属，再通过只读 ReadLedger 调用 `daily_report.report`；核查 report 的传递调用都只读，不能把 RolloutRuntime 或控制类挂入 facade。保留 observations 的 yes/no/unknown、有效样本、延迟 samples，过滤 report 中不属于展示契约的字段。backend 状态只解释已有回执，不主动探测。

- [x] **4. 测试分页与数据变化。** 验证相同时间多行不重漏、分页上限、跨工作区 cursor 拒绝、proposal 不计 commit、另一个工作区的 live rollout 仅作全局提示。仅插入一条 quality_observation 而不递增 ledger_sequence，再读取仍能看到变化。缺少 quality 样本返回 null。
- [x] **5. 提交。** 提交本任务 queries/service/models 与 tests，消息 `feat(console): aggregate workspace overview and paginated activity`。

## Task 3：提案、版本和证据下钻

**Files**

- Create: `src/sagacontext/console/details.py`, `serialize.py`, `src/sagacontext/ledger/access.py`。
- Modify: `console/service.py`, `console/models.py`, `ledger/service.py`。
- Test: `tests/console/test_details.py`, `tests/console/test_serialization.py`；复跑 `tests/ledger/test_ledger.py`。

**Interfaces**

- `scope_allows(scope: Scope, context: TaskContext) -> bool` 放入 ledger/access.py，从 Ledger 原静态函数机械提取，原方法保留委托，避免两套规则漂移。
- service 新增 `session(workspace_id, session_id)`, `batch(workspace_id, batch_id)`, `memories(project_id, workspace_id, context, cursor, limit)`, `memory(workspace_id, memory_id, context)`，参数 ID 为 str、context 为 TaskContext、cursor 可空，返回 `{meta,data}`。
- `safe_text(value: str) -> str` 与专门 DTO 映射函数：不允许直接把 sqlite Row/dict/payload_json 全量 dump 给浏览器。

- [x] **1. 写可见性和来源缺失测试。** 建 global/project/path/task 四种 scope；path 需要相对且规范化的 touched_paths，task 需要数据库验证后的 task_id。已删除记忆仅可通过回执显示墓碑元数据，不能返回删除前正文；retired 版本与 active 版本状态分开。

```python
def test_scope_requires_task_and_path_context():
    from sagacontext.ledger import Scope, TaskContext
    from sagacontext.ledger.access import scope_allows
    context = TaskContext(owner_id="a", project_id="p", workspace_id="w")
    assert not scope_allows(Scope(kind="task", project_id="p", task_id="t"), context)
    assert not scope_allows(Scope(kind="path", project_id="p", path_pattern="src/*"), context)
    assert scope_allows(Scope(kind="project", project_id="p"), context)
```

- [ ] **2. 运行红灯。** `PYTHONPATH=src .venv/bin/python -m pytest tests/console/test_details.py tests/console/test_serialization.py -q`。
- [x] **3. 实现关联查询与字段白名单。** batch → proposal → evidence；session → events/batches/injection；memory → revisions/revision_evidence。target revision 不存在时显示 target_unavailable，不用当前正文代替旧版本。用事件实际 session/workspace 验证证据链接；找不到直接来源时返回 source=null。

```sql
SELECT e.evidence_id,e.source_event_id,e.evidence_kind,e.redacted_excerpt
FROM revision_evidence re JOIN evidence e USING(evidence_id)
WHERE re.memory_id=? AND re.revision=? AND e.owner_id=?
ORDER BY e.observed_at,e.evidence_id
```

证据摘录、提案理由、记忆正文在服务端经过脱敏后再输出，字段白名单拒绝原始 locator、transcript_path、endpoint、authorization、api_key。`event_content.SENSITIVE` 是输入拒收规则，不能当完整输出脱敏器；为 URL、email、PEM、常见 token 前缀和 key/password/secret 赋值编写输出替换规则。任意键值 payload 递归处理，敏感字段值整体显示 `[已隐藏]`。这不承诺识别所有未知秘密；API 契约始终拒绝配置与原始事件载荷。

读取 context 时验证 owner/project/workspace/task 一致，并拒绝绝对路径、`..` 越界。允许用户在项目目录内选择相对路径作为 scope 过滤；不得因记忆 ID 已知而放宽正文可见性。

- [x] **4. 复跑测试并验证下钻边界。** 注入回执只显示 IDs/revisions/omissions，不重跑 RecallPolicy；预算字段缺失显示 unavailable。消费 receipt 是记录证据，operator observation 是观察标签；不添加推测成功。正文含 HTML 时前端按文本呈现；API 输出保留安全文本，不生成可执行 HTML。
- [x] **5. 提交。** 提交 details/serialize/access、必要委托修改与 tests，消息 `feat(console): expose scoped memory and proposal evidence`。

## Task 4：同源读取 API 与错误契约

**Files**

- Create: `src/sagacontext/console/router.py`, `security.py`。
- Modify: `src/sagacontext/daemon.py`, `console/models.py`。
- Test: `tests/console/test_api.py`, `test_api_readonly.py`。

**Interfaces**

- `create_console_router() -> APIRouter`，prefix `/console/v1`，调用任务 2/3 的 service。
- daemon 现有 lifespan 取得 ledger.path/owner_id 后构造 ConsoleReadService；不新增 worker。route 依赖仅拿现有 service 和白名单 runtime 快照。
- 路径与设计表逐一一致，共 11 个；GET 的 query 用 Pydantic 校验。`/projects/{id}/memories` 与 memory detail 接受 task_id、重复 touched_paths 参数；owner 不从 query 接收。
- 每个 route 必须声明具体 Pydantic response_model（Envelope 中的 data 也用具体模型），models.py 定义 ProjectDirectory、WorkspaceOverview、SessionPage、TaskPage、ActivityPage、BatchPage、RolloutDetail、BatchDetail、MemoryPage、MemoryDetail、SessionDetail。禁止以裸 dict/Any 作为生成前端类型的最终 OpenAPI 契约；service 的 dict 返回在响应边界由这些模型校验。
- 错误结构 `{error:{code, retryable}, request_id}`。不可访问/不存在 404；cursor/time/path 错误 400；schema 不兼容 503 不可重试；busy 503 可重试。内部异常记录 error class，不回传 SQL/本机路径/原始消息。

- [x] **1. 写 API 不写入测试。** FastAPI TestClient 使用 tmp_path 配置且 worker_enabled=false。fixture 建库之后开始记录 logical dump；禁止调用 RolloutRuntime._run、Judge 和 backend.search。

```python
def test_get_does_not_use_mutating_runtime(console_client, monkeypatch):
    from sagacontext.rollout import RolloutRuntime
    def forbidden(*args, **kwargs):
        raise AssertionError("console called mutating runtime")
    monkeypatch.setattr(RolloutRuntime, "_run", forbidden)
    response = console_client.get("/console/v1/projects")
    assert response.status_code == 200
    assert "data" in response.json()
```

console_client fixture 使用任务 2 的临时库、同源 localhost base_url。另一测试遍历全部 11 个 GET，确认读取前后 iterdump 完全相同、STOP 文件内容未变、网络调用为零；schema_version/sequence 始终来自当次快照。

- [ ] **2. 运行红灯。** `PYTHONPATH=src .venv/bin/python -m pytest tests/console/test_api.py tests/console/test_api_readonly.py -q`。
- [x] **3. 实现路由挂载和访问约束。** 仅在 `/console` 范围校验 loopback Host 和确切端口；Origin 存在时必须与当前页面源一致，Sec-Fetch-Site 为 cross-site 时拒绝；无 Origin 的浏览器同源页面请求仍可工作。不开 CORS 通配，不改变既有 operator 路由行为。第一期若 daemon 绑定非 loopback，console 默认不开放。

```python
from fastapi import APIRouter, Request
router = APIRouter(prefix="/console/v1")

@router.get("/projects")
def projects(request: Request):
    return request.app.state.console.projects()
```

security 由 router dependency 对每个 API 执行；静态页面在任务 7 复用同一校验。overview runtime 仅读取 scheduler thread/error_class、配置 mode、STOP exists 和当前时间；不要调用 `/health` 或 rollout.mode。

- [x] **4. 验证完整错误矩阵。** 测试恶意 Host/Origin、未知 workspace、不同 project 的 ID、start>=end、无时区日期、非法 limit/cursor、SQLite busy、单卡读取错误。单卡局部错误返回 unavailable；数据库无法打开时整体 503。
- [x] **5. 提交。** 提交 router/security、daemon 挂载与 API tests，消息 `feat(console): serve validated read-only API`。

## Task 5：前端导航、四卡首页与刷新

**Files**

- Create: `web/package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `index.html`。
- Create: `web/src/{main.tsx,App.tsx,styles.css}`、`api/{client.ts,types.ts,queries.ts}`、`pages/OverviewPage.tsx`、`components/{WorkspaceNav,StatusBar,OverviewCards}.tsx`。
- Test: `web/src/api/queries.test.ts`, `web/src/pages/OverviewPage.test.tsx`。
- Modify: `.gitignore` 增加 web/node_modules、前端测试输出、`src/sagacontext/console/_static/` 构建产物和 .superpowers 预览排除规则。

**Interfaces**

- 路由 `/console/projects/:projectId/workspaces/:workspaceId` 为首页；BrowserRouter basename `/console`。
- `getJson<T>(path: string, signal?: AbortSignal): Promise<T>`，相对路径仅指向 `/console/v1`；失败保留结构化 code。
- query key `['console', projectId, workspaceId, resource, filters]`；resource 固定枚举，filters 使用稳定序列化值。
- API 类型由任务 4 OpenAPI 导出：`web/src/api/schema.d.ts`，使用 openapi-typescript 生成并提交；types.ts 从 components/schemas 导出 DTO 别名。OpenAPI 导出用 `create_app(Config(...temporary paths...))` 不启动 lifespan，禁止 Config.load。
- npm scripts：`dev`、`build`（tsc + vite build）、`test`（vitest run）、`typecheck`（tsc --noEmit）。

- [x] **1. 建立页面测试并验证零样本文案。** 安装 React/React DOM/Router/TanStack Query，以及 Vite/TypeScript/Vitest/Testing Library；提交锁文件。测试仅 mock API，不导入真实本机数据。

```tsx
it('keeps an unobserved rate distinct from zero', () => {
  render(<QualityRate yes={0} no={0} unknown={1} />);
  expect(screen.getByText('— 待采样')).toBeInTheDocument();
  expect(screen.queryByText('0%')).not.toBeInTheDocument();
});
```

`QualityRate` 在 OverviewCards.tsx 中导出，props 为三个 number；有效样本 yes+no=0 返回待采样，否则显示比例、有效分母和 unknown 数。

- [ ] **2. 执行红灯。** `npm --prefix web test`，先因缺少对应组件或行为失败。
- [x] **3. 实现路由、API 请求与四卡。** 固定左导航，二列卡片，最近活动在下；项目从 API 自动选第一项仅作浏览，不能注册项目。无工作区时渲染接入说明。卡片依次对应任务、运行与待审核、最近会话、记忆变化；所见数量取 API DTO，不写固定示例值。

```ts
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/console/v1${path}`, {signal, credentials: 'same-origin'});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.code ?? 'read_failed');
  return body as T;
}
```

实际实现需把 code/retryable 挂在专门 ConsoleApiError，而不是丢弃重试语义。Query 禁止自动 retry 与轮询并行形成重试风暴：`retry:false`，refetchInterval 按连续失败次数为 5000/10000/20000/30000，成功归零；`refetchIntervalInBackground:false`。保留最近成功 data 与 dataUpdatedAt，失败时显示过期快照和当前未知。

- [x] **4. 测试迟到响应与断连。** 使用 deferred Promise：先请求 workspace A，再切 B，让 A 最后返回，页面仍必须显示 B；断连保留原快照但不继续显示「当前健康」。测试 mode=off、无运行、另一 workspace 正运行、配额与今日变更口径不同。
- [x] **5. 提交。** 提交前端基础与首页、锁文件、类型和 tests，消息 `feat(console): add workspace overview UI`。

## Task 6：对象列表、详情与证据导航

**Files**

- Create: `web/src/pages/{TasksPage,SessionsPage,MemoriesPage,BatchesPage,RolloutPage,DetailPage}.tsx`。
- Create: `web/src/components/{PagedList,DetailPanel,EvidenceList,RevisionDiff,QualityRate,EmptyState}.tsx`。
- Modify: `App.tsx`, `api/queries.ts`, `components/OverviewCards.tsx`。
- Test: `web/src/pages/DetailPage.test.tsx`, `web/src/components/RevisionDiff.test.tsx`。

**Interfaces**

- 工作区路由后追加 `/tasks`、`/sessions`、`/memories`、`/batches`；详情追加 `/:objectId`，运行详情 `/rollouts/:rolloutId`。
- URL query 保存 task_id、touched_paths、列表类型、时间窗和 cursor；切换 workspace 清除不再适用的对象 ID。
- DetailPanel 接收 heading、children、onClose；EvidenceList 接收 API evidence DTO；RevisionDiff 接收 old/new 可空安全文本。QualityRate 从任务 5 独立成单文件，所有 imports 同步迁移。

- [x] **1. 写关键状态下钻测试。** 列表中的 target_unavailable 不能拿当前正文补旧版本；证据 source=null 无跳转；deleted memory 不显示历史正文。

```tsx
it('shows a missing prior revision without inventing a diff', () => {
  render(<RevisionDiff oldText={null} newText="记录失败类别" />);
  expect(screen.getByText('旧版本不可用')).toBeInTheDocument();
  expect(screen.getByText('记录失败类别')).toBeInTheDocument();
});
```

- [ ] **2. 执行红灯。** `npm --prefix web test -- DetailPage RevisionDiff`。
- [x] **3. 实现每页的数据契约。** Tasks 展示项目归属与当前工作区绑定；Sessions 展示时间/任务/事件；Memories 分为项目适用/全局/本工作区变更来源；Batches 展示 batch.status、proposal 操作和证据；Rollout 展示配额、等待审核、投影、回滚与 observations。详情顶部保留 project/workspace/object ID，来源链接仅使用服务端允许的本地路由。

```tsx
export function RevisionDiff({oldText, newText}: {
  oldText: string | null; newText: string | null;
}) {
  return <div className="revision-diff">
    <section aria-label="旧版本"><pre>{oldText ?? '旧版本不可用'}</pre></section>
    <section aria-label="新版本"><pre>{newText ?? '新版本不可用'}</pre></section>
  </div>;
}
```

优先使用字段级新旧并列比较，数组/对象先规范序列化；不依靠大规模编辑器组件。前端不用 dangerouslySetInnerHTML。首期详情不渲染审批、修改、停止或清理按钮，控制说明以事实状态表达。

- [x] **4. 验证交互闭环。** 首页点击待审批次 → proposal → evidence → session；浏览器后退恢复列表筛选与滚动位置。验证注入输出、消费 receipt、人工标签三者分别显示；没有 budget/used 时明确明细缺失。
- [x] **5. 提交。** 提交对象页面与详情 tests，消息 `feat(console): add memory and session evidence navigation`。

## Task 7：同源构建、离线联调与验收

**Files**

- Create: `src/sagacontext/console/static.py`, `scripts/serve_console_fixture.py`, `web/playwright.config.ts`, `web/e2e/console.spec.ts`。
- Modify: `daemon.py`, `pyproject.toml`, `web/vite.config.ts`。
- Test: `tests/console/test_static.py`, `tests/console/test_fixture_server.py`。
- Docs: `docs/probes/2026-09-11-workspace-console-acceptance.md`, `README.md` 的使用入口、`docs/README.md` 的进展链接。

**Interfaces**

- `mount_console_static(api: FastAPI, dist_path: Path) -> None`：仅挂载 `/console/` 和合法前端路由，API 优先，`/console/v1/*` 的 404 不能返回 index.html。
- Vite base `/console/`，产物输出 `src/sagacontext/console/_static/`，setuptools package-data 包含 HTML/assets；产物由构建生成，不手改。
- fixture server 参数 `--port`、`--scenario normal|empty|stale`，程序创建并退出时清理临时库；只绑定 loopback，worker=false，禁止 Config.load、socket 外连、Judge/backend 实例化。
- package scripts 增加 `test:e2e`；浏览器测试启动 fixture server，跳过所有真实运行 API。

- [x] **1. 写 API 优先和静态缺失测试。** 没构建资源时 API 仍可用，`/console/` 明确 console_assets_missing；未知 `.js` 返回 404，不走 SPA fallback。编码路径穿越返回 404。

```python
def test_unknown_console_api_is_not_spa_html(console_client):
    response = console_client.get('/console/v1/does-not-exist')
    assert response.status_code == 404
    assert 'text/html' not in response.headers.get('content-type', '')
```

- [ ] **2. 执行红灯并构建。** `PYTHONPATH=src .venv/bin/python -m pytest tests/console/test_static.py -q`；实现 static.py 后运行 `npm --prefix web run build`。
- [x] **3. 完成浏览器链路验收。** Playwright 用 fixture 页面检查导航、证据、空态、断连和响应式；只路由 mock `/console/v1` 或访问临时 fixture，不触发后台真实任务。

```ts
test('grid remains usable across widths', async ({page}) => {
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({width, height: 960});
    await page.goto('/console/');
    await expect(page.getByRole('heading', {name:'工作区总览'})).toBeVisible();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  }
});
```

检查 Tab 焦点顺序、Escape 关闭详情、列表返回恢复、错误卡片不会伪装为 0。将各视口截图放临时输出，验收报告只引用已检查的实际产物。归档设计线框的通过不能替代产品截图检查。

- [x] **4. 运行一次所需回归与构建检查。**

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/console -q
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run test:e2e
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

从构建 wheel 解压检查 `sagacontext/console/_static/index.html` 及其引用 assets 全部存在；在临时虚拟环境安装 wheel，用 fixture 配置启动，确认没有 Node 进程也可访问页面。检查 API 与静态文件均无 CDN、凭据和真实事件泄漏。全量测试通过后不无理由反复重跑。

- [x] **5. 写验收报告并提交。** 记录实际 commit、依赖锁、临时 fixture、通过数、失败/限制、三个视口截图和数据口径核验。报告日期用实际验收日期，若晚于 09-11 同步更新文件名。提交 `feat(console): package UI and verify workspace console`，只包含本任务文件。

## 规格覆盖与完成标准

| 设计要求 | 任务 |
|---|---|
| 四卡、工作区导航、活动与空态 | 2、5 |
| 任务/session/rollout/记忆作用域 | 2、3、6 |
| 提案与证据、注入与消费区别 | 3、6 |
| 无副作用读取、一致快照与元数据 | 1、2、4 |
| 分页、切换竞争、5 秒刷新与退避 | 2、5 |
| 质量分母、零样本、投影与回滚状态 | 2、5、6 |
| 同源访问、脱敏、无前端凭据 | 3、4、7 |
| API 路由、静态部署、打包 | 4、7 |
| 响应式、键盘、端到端与回归 | 6、7 |

执行结束必须同时交付可运行页面、真实读取 API、证据导航和验收记录。第一期完成不等于第二期控制台写入获准，也不扩展任何真实 rollout 边界。

## 执行完成记录（2026-09-11）

任务 1–7 的功能交付完成；真实命令结果、提交映射、截图、包校验与调整见 [验收报告](../../probes/2026-09-11-workspace-console-acceptance.md)。代码交付至 `a72ba26`；测试文件和组件按共享契约合并，实际入口以仓库为准。未逐条执行的“先缺模块红灯”过程项保留未勾选，功能验证已用最终专项、浏览器与全量回归完成。
