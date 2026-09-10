# 2026-09-10 Guarded 真实运行闭环

已完成真实 Codex → daemon → Judge → 审核提交 → OpenViking → 下一会话消费 → rollback。
这是在已批准 SagaContext workspace 中执行的**受控验收**，不是日常流量质量验收。
用户明确授权代理执行后续审核；报告记录为 `user_delegated_operator`，不是用户逐条点击审核。

## 证据

- 最终运行：[run-04.json](../../artifacts/probes/20260910-guarded-runtime/run-04.json)
- 独立远端残留复核与评价：[evaluation.json](../../artifacts/probes/20260910-guarded-runtime/evaluation.json)
- 可执行 runner：[run_guarded_runtime.py](../../scripts/run_guarded_runtime.py)
- rollout：`2b8291de-b30b-4b14-ade5-fc5ec174afab`
- Codex：`codex-cli 0.153.4`，本机已配置模型 `gpt-6-astra`。
- Judge：本机已授权 DeepSeek Pro；OpenViking：固定镜像的 `v0.4.17.1`。

使用临时最小 CODEX_HOME 调用真实 Codex CLI，workspace 为本仓库，hooks 请求实际 daemon。
没有读取或导入历史 transcript。独立会话执行了：

1. 无记忆对照返回 `MISSING`。
2. 新提示要求记住本项目验证命令 `uv run --locked pytest -q`；真实 UserPromptSubmit 形成 1 个 candidate。
3. 真实 Judge proposal 经过内容检查，绑定审核 receipt 后提交 1 条 memory。
4. 同 rollout Projector 确认 1 个 projection，search 可见。
5. 下一独立 Codex 会话从 SessionStart 召回，并在最终消息中准确返回保存的命令；另行保存 consumption receipt。
6. rollback 清理 memory/candidate/locator，产生 1 条 compensation receipt。
7. 回滚后独立会话再次返回 `MISSING`；再次精确 inspect 和 search 确认远端无残留。

回滚 audit：1 candidate、12 个保留审计事件、1 batch、1 memory target、1 projection target、1 compensation receipt；
residual memories/candidates/operations/outbox 均为 0。重复执行同 rollout rollback 仍 completed，补偿 receipt 未重复增加。
本地审计记录和被遗忘 memory 的历史身份保留，不宣称数据库行全部删除。

## 根因与修复

原 root key 真实有效，但 OpenViking 禁止 root key 访问租户数据 API，返回 403 PERMISSION_DENIED。
原适配器误分类成 authentication_failed；重启、额外 Bearer header 都不能解决权限边界。
通过服务管理接口创建专用普通用户 `sagacontext-guarded`，用户 key 仅写入本机受保护文件，namespace 绑定该用户。
保留标准 X-API-Key 请求头，区分 401 与 403。root 配置已收紧为 0600。

同时修复：

- backend 失败产生 blocked injection receipt，不再向宿主返回 HTTP 500。
- hook 仅处理已支持的四类事件，只允许配置 workspace；不读取 transcript。
- 每次 hook 调用具有独立事件引用，HTTP 重试可复用同一 prepared record；同会话不同提示不再折叠。
- 同事件重试不再重复 reservation/candidate。
- 对新提示的明确持久决策做保守内容准入；敏感模式或非持久文本不进入语义输入。
- Judge 可以取得入库的决策文本；此前只传 hook 名称无法形成真实语义提案。
- hook stdout 仅输出合规 SessionStart context 或空对象。
- daemon control 使用独立进程会话、启动健康检查和准确 PID；支持带 STOP 的停止及指定 rollout 的幂等回滚。

## 质量与限制

此受控样本中，candidate 有效 1/1、审核修改 0/1、消费确认 1/1、两次负对照错误召回 0/2。
Judge 延迟 7427 ms，projection 确认 88 ms（不包括后续 search 等待），四次完整宿主调用约 15.9–16.8 s。
样本仅 1 条有意义决策，不能推导日常有效率或扩大 workspace/quota；日常质量样本数仍为 0。

失败尝试单独保留：run-01 宿主调用超时；run-02 旧 Luna 模型不可用；run-03 随机标签被 Judge 返回 no_change，审核拦截。
每轮均执行清理。未修改 Judge 冻结契约以放行标签。
run-04 中 stderr 的非致命鉴权信号被旧分类器误标为 blocker；四个进程均退出 0 并输出正确最终答案。
evaluation 保留原始 artifact 并明确订正分类；runner 现仅对失败调用标记 blocker。

## 本机最终状态与操作

daemon 已启动，跨工具调用 status 复核成功；运行模式 `off`，STOP 开关存在，`active` 未启用。
专用 backend 用户与受保护凭据保留作为运行配置，测试 memory/projection 均已清理。
普通 hooks 仅安装 SessionStart/UserPromptSubmit/Stop/SessionEnd，保留原有 skill-evolution hooks。
日常批处理尚不作为无人审核自动提交启用；本次 Judge 调度和 projection 由可审计 runner 显式驱动。

```sh
./bin/sagacontext-daemon-control status
./bin/sagacontext-daemon-control start
./bin/sagacontext-daemon-control stop
./bin/sagacontext-daemon-control rollback --rollout-id 2b8291de-b30b-4b14-ade5-fc5ec174afab
```

start 不会清除 STOP，也不会创建 activation；stop 先置 STOP 并冻结运行，再终止所跟踪 daemon。
使用既有授权开始下一轮观察时，应明确解除 STOP 并创建新的限额/期限 rollout；不能把常驻 daemon 当作 rollout 已启用。

验证：`227 passed, 96 subtests passed`；compileall、git diff --check 通过；保留 2 个既存弃用警告。
