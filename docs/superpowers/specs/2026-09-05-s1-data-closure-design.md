# SagaContext S1 数据收口设计

**日期：** 2026-09-05

**状态：** 架构方向已批准；实现仍需用户审阅本文件后批准。

**上位设计：** `2026-09-05-sagacontext-v0.3-design.md` 的 S1 权威内核。

## 1. 目标与边界

S1 将本地运行时的数据权威收敛到 `Ledger`。daemon 和 CLI 不再导入、构造或写入旧 `Store` 与 `OpenVikingClient`。旧模块暂时保留在源码中，供后续迁移参考，但不处于入口依赖图内。

本阶段不连接真实后端、不消费 outbox、不启用宿主 hooks、不读取 transcript，也不迁移旧 `state.db`。EventJournal、固定对账批次、异步 projector 和远端删除收敛属于 S2/S3。

## 2. 应用组合层与数据库生命周期

新增 `src/sagacontext/application.py`，提供 `Application`：

- 只接收解析完成的 `Config`，并从 `config.ledger_path` 创建一个 `Ledger`。
- `ledger_path` 默认且唯一解析为 `${SAGACONTEXT_HOME:-~/.sagacontext}/ledger-v3.db`。配置文件本阶段不能指定第二套隐式路径；测试显式传入临时路径。
- daemon 通过 FastAPI lifespan 创建一个 `Application`，保存于 `app.state`，shutdown 时调用 `close()`。模块导入不得创建数据库、连接或外部客户端。
- CLI 每个命令通过 context manager 创建 `Application`，命令结束或异常时都关闭连接。
- `Application.close()` 幂等；关闭后的对象不得继续服务请求。
- `owner_id` 由 Ledger 文件内的 owner 记录稳定恢复，HTTP 请求不能覆盖它。

旧 `state.db` 不读取、不写入、不创建迁移标记。首次启动只允许创建 `ledger-v3.db` 及 SQLite 自身的 WAL/SHM 文件。

## 3. 唯一本地数据流

### 3.1 写入

所有记忆变更都构造 `CommitRequest` 并调用 `Ledger.commit`。HTTP/CLI 层只负责输入解析，不复制 CAS、scope、evidence、receipt 或事务规则。

项目、workspace、task、session 和 task binding 分别通过 Ledger 的领域方法创建或绑定。S1 不接受未经注册的 project/task，也不依据 remote、root commit 或分支名自动合并项目身份。

### 3.2 读取

`current` 和 `history` 必须接收完整 `TaskContext`。可信 `owner_id` 由 `Application` 注入，请求只提交 `project_id / workspace_id / task_id / touched_paths / stage`。Ledger 在每次读取中再次检查 owner、project、task 和 path scope；HTTP/CLI 的预校验不是授权依据。

后端 locator、旧 URI 和索引命中不能直接返回正文。S1 只从 Ledger 当前修订或历史修订读取。

### 3.3 删除

`forget(memory_id, receipt)` 在本地事务中完成以下动作：

1. 将 head 标记为 `deleted`，使 `current` 和 `history` 立即返回空结果。
2. 清空本地 revision 正文及证据摘录，创建 suppression rule 和 deletion job。
3. 对所有已登记 generation 写入删除 outbox；没有 generation 时状态停在 `local_redacted`。
4. 相同 receipt 的重复请求返回同一结果；旧证据重放不能恢复记忆。

S1 查询接口必须返回 deletion job 的当前状态和相关 pending outbox 数量。允许状态为 `local_redacted` 或 `remote_pending`；S1 不得返回 `completed`，因为本阶段没有远端清理 worker。检索测试后端中的投影不构成生产索引，测试结束即销毁。

## 4. Daemon 接口

本阶段提供以下本地 JSON 接口；具体 URL 仅作为 S1 内部契约，不宣称对外稳定：

| 操作 | 行为 |
|---|---|
| `GET /health` | 返回 schema version、ledger path 是否可用及 `host_ingestion=disabled`；不输出密钥 |
| `POST /projects/register` | 显式注册位置，返回稳定 project/workspace ID |
| `POST /projects/{id}/locations` | 用户显式把另一位置绑定到已有项目 |
| `POST /tasks` | 在已注册项目下创建独立 task |
| `POST /sessions` | 创建或幂等取得 host session |
| `POST /sessions/{id}/tasks/{task_id}` | 用事件边界切换当前 task |
| `POST /memories/commit` | 将请求交给 Ledger，原样返回 commit/conflict/rejected 状态 |
| `POST /memories/current` | 使用请求中的 memory IDs 和 TaskContext 做权威读取 |
| `POST /memories/{id}/history` | 使用 TaskContext 读取授权历史 |
| `POST /memories/{id}/forget` | 执行幂等本地删除并返回删除状态 |
| `GET /deletions/{job_id}` | 返回删除阶段和 pending outbox 数量 |
| `GET /outbox` | 只读列出 pending 项，不执行投影 |

旧 `POST /events` 保留兼容路由，但固定返回 HTTP `501`：

```json
{"status":"host_ingestion_disabled","stage":"S1"}
```

该路由不得解析 transcript、创建 session/candidate、调度后台任务或写任何数据库。

## 5. CLI 接口

CLI 与 daemon 共享同一个 `Application` 和领域输入模型，提供项目注册/位置绑定、任务创建、session/task 绑定、memory commit/current/history/forget、deletion status 和 outbox list。

CLI 的结构化输入首版从 JSON 文件或 stdin 读取，避免用大量 flags 重建嵌套的 `CommitRequest` 与 `TaskContext`。所有输出为 JSON，失败使用非零退出码并保留 `conflict / rejected / invalid_scope / not_found` 等可区分类别。

旧 `add/show/pending/review/tasks/coldstart/bench` 中直接依赖 `Store` 或 OpenViking 的命令不再注册到 S1 CLI。确定性的 benchmark 库代码保留，但不属于收口入口。

## 6. G4 身份 fixture

fixture 只使用临时目录和合成 Git 元数据，不访问用户仓库。

| 场景 | 预期 project | 预期 workspace | 预期 task |
|---|---|---|---|
| 同一仓库的 Git worktree，经用户绑定 | 相同 | 不同 | 不自动共享；显式绑定后共享指定 task |
| 不同 fork，remote/root 相似但未确认 | 不同 | 不同 | 隔离 |
| monorepo 根与子目录，只有根已注册 | 相同 | 相同 | 由显式 task 决定，不按子目录自动建 task |
| monorepo 子项目分别显式注册 | 不同，最长路径匹配子项目 | 不同 | 隔离 |
| 已绑定目录移动并显式 rebind | 相同 | 保留原 workspace ID | 原 task 仍属于该 project |
| 同一分支两个并行 task | 相同 | 相同 | task ID 不同；切换 session binding 不暂停另一个 task |
| clone 到新路径但未显式绑定 | 不自动关联 | 新注册后为不同 workspace | 不自动恢复旧 task |

实现不得用一个 root hash、remote URL 或 branch 名兜底改变上述归属。

## 7. 固定验收清单

I01–I11 按上位设计逐条落到测试：

| 不变量 | S1 断言 |
|---|---|
| I01 | current/history 正文仅来自 Ledger；测试后端伪造新正文不能改变读取 |
| I02 | owner/project/task/path 在 current、history 和 commit 时均复核 |
| I03 | revision、evidence、head、receipt、outbox 故障时一起回滚 |
| I04 | Ledger 写事务期间不调用 BackendAdapter 或任何外部客户端 |
| I05 | S1 只产生本地 commit 与 pending outbox；无投影也不丢 evidence |
| I06 | 相同来源事件与 claim 重放不增加独立 evidence |
| I07 | stale expected revision 返回 conflict，不覆盖当前 head |
| I08 | deleted/retired/越权 ID 即使被后端返回也不进入 current |
| I09 | task scope 不能通过 update 扩大为 project/global scope |
| I10 | evidence/verification 字段不由后端 score 或执行结果代替 |
| I11 | forget 后重放旧 evidence 被 suppression 拒绝；retire 不等于 forget |

集成验收额外要求：

- daemon 模块导入无文件副作用，lifespan 结束后连接关闭。
- CLI 与 daemon 在相同 `SAGACONTEXT_HOME` 下解析到同一 `ledger-v3.db`。
- monkeypatch `Store` 与 `OpenVikingClient` 构造器为抛错后，所有 S1 API/CLI 测试仍通过。
- 测试前后记录旧 `state.db` 的 SHA-256、大小与 `mtime_ns`，三者完全不变；原先不存在时仍不得创建。
- `/events` 调用前后比较 Ledger 表计数与 sequence，均不变化。
- G4 七类 fixture 全部通过。

固定命令：

```bash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m compileall -q src tests
.venv/bin/pip check
git diff --check
```

测试报告需列出 I01–I11、G4 和入口隔离的用例名称，不能只报告测试总数。

## 8. 实施顺序与退出条件

1. 先完成 `Config.ledger_path` 解析测试和 `Application` 生命周期测试。
2. 再让 daemon 只依赖 `Application`，首先将 `/events` 改为无副作用的 `501`。
3. 增加 Ledger 查询删除状态/outbox 所需的只读方法，再接 daemon API。
4. CLI 改用同一组合层并增加 JSON 输入集成测试。
5. 补齐 G4 与 I01–I11 映射测试，运行固定命令。

只有以上测试全部通过，且入口依赖扫描确认 daemon/CLI 不再导入旧 Store/OpenViking 路径，才能宣称“S1 数据收口完成”。S0 的 G1/G3 仍可保持受阻；这不阻止本地 S1 退出，但继续阻止真实后端、自动宿主注入和私人数据导入。
