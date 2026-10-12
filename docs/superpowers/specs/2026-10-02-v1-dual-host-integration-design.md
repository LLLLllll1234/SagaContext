# V1 与双宿主分支整合设计

日期：2026-10-02

## 目标

将 `origin/main` 已完成的 V1.0.0、只读 workspace console 和部署能力，与
`codex/dual-host-admission-2026-09-30` 上的 Memory Lab、Claude Code 采集和
Codex 0.159.2 准入实现合并到同一条可验证分支，并保留 guarded 运行的安全边界。

本轮产出包括：

- 一条同时包含 V1 console、Memory Lab 和双宿主 runtime 的集成分支；
- 统一的版本、默认宿主版本、测试入口和状态文档；
- 合并后的静态回归、console 回归和双宿主 preflight 证据；
- 一次受限的双宿主 guarded 观察，或在外部环境阻断时留下可复核的阻断证据。

## 分支策略

采用“将 `origin/main` 合并到当前双宿主分支”的方式，不对远端分支做 rebase，也不直接推送
`origin/main`。合并前记录当前 HEAD、工作树状态和未跟踪文件清单，并创建本地回退引用。
合并只提交受控的源代码、测试和文档；本地 probe/Judge artifact 继续按现有规则保持未跟踪。

重点冲突面是 `src/sagacontext/daemon.py`、`pyproject.toml`、配置和 Ledger 迁移、README、
文档索引以及测试目录。解决后必须确认 `/console/v1`、`/memlab/`、`/events`、health 和
双宿主 host mapping 同时存在。

## 版本与配置

- Python 包和 FastAPI metadata 统一为 `1.0.0`。
- 新安装的 Codex 默认版本更新为已重新准入的 `codex-cli 0.159.2`。
- Claude Code 不作为无配置安装的强制依赖；双宿主通过 `[rollout.hosts]` 显式启用。
- 保留旧版本探针和历史验收文档的原始版本号，并在当前入口中标明其历史性质。
- 运行时继续默认 `off`；`active` 不开放，guarded 观察仍受 STOP、工作区白名单、10/20/24h
  上限、人工审核和可审计回滚约束。

## 测试与文档

以 `PYTHONPATH=src .venv/bin/python -m pytest -q` 作为统一回归入口，覆盖 Python、console、
Memory Lab 和多宿主测试，并保留 96 个 subtest 的结果。同步检查 `compileall`、前端静态资源
语法、`git diff --check`，以及 console 的构建/打包验证（若当前环境具备 web 工具链）。

README、`docs/README.md` 和 `docs/implemented-features.md` 只写当前集成分支可由源码或报告
支持的事实；合成测试、受控真实验收、真实日常质量和未解决的 provider/注入问题分别标注。
如果 unittest discover 仍与 pytest 产生不同结果，要么修复其状态隔离问题，要么明确 pytest
是唯一受支持入口并记录原因。

## 双宿主观察

观察前依次执行配置诊断、宿主版本/路径冻结、hook 检查和 off 状态连通验证。观察窗口仅允许：

- 当前 SagaContext workspace；
- 最多 10 个 session、20 个 candidate、24 小时；
- `new` proposal 的逐条人工审核；
- STOP、deadline、异常或错误召回时冻结并按 rollout 回滚。

Codex 0.159.2 进入注入和消费统计。Claude Code 2.1.285 只进入事件采集、候选和审核链路，
SessionStart 不注入；只有在官方端点对照解释 `additionalContext` 未被消费后，才重新评估开放
注入。观察结束必须恢复 `off + STOP`，并核对本地和 OpenViking 无残留。

## 验收标准

1. 合并后的单一分支同时包含 console、Memory Lab、Ledger/maintenance、双宿主 runtime 和
   相关测试；无未解决冲突。
2. 版本与默认配置没有互相矛盾，health/OpenAPI/package metadata 使用同一版本。
3. pytest 全量通过；静态检查通过；任何保留的 warning 或测试入口差异都有明确记录。
4. 双宿主 preflight 能报告冻结版本、路径、hook 和配置状态，不泄露凭据。
5. 若 guarded 观察启动，结束时必须为 `off + STOP`，并产出候选、审核、消费和清理证据；
   若因外部环境无法启动，报告阻断原因，不用合成数据替代真实日常质量。

## 非目标

- 不开放 `active` 模式；
- 不扩大 workspace、配额或观察窗口；
- 不把 Claude Code 的未消费注入标记为成功；
- 不提交本地敏感配置、transcript 或未被代码引用的运行 artifact；
- 不在本轮改写历史验收结论。
