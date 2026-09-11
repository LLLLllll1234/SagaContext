# 工作区只读控制台验收

日期：2026-09-11。结果：第一期功能已实现，合成数据验收通过。

设计基线为 `8ceaf9c`；本次功能、最终修复和 Python 包验证对应 `a72ba26`，分支 `codex/workspace-console`。实现位于隔离工作树 `.worktrees/console`，未合入原工作区，也未触碰其中正在进行的其他改动。

## 交付

- React + TypeScript 四卡首页、工作区切换、项目任务/会话/记忆/批次分页目录、活动列表。
- 12 个 `/console/v1` GET 读取接口。相较定稿表新增单个项目任务查询，避免详情依赖目录前 100 条。
- 批次 → 提案 → 证据 → 会话 → 注入回执/记忆；历史版本与有提交记录支撑的取代关系；运行配额、投影、观察质量与回滚统计。
- SQLite 独立只读事务、作用域检查与字段白名单、脱敏、稳定错误；每次响应带读取时间、Ledger 序列、schema 与 request ID。
- 同源静态资源、SPA 合法路由与 API 分离、wheel 打包、独立合成演示服务。

启动方法见 [README 控制台入口](../../README.md#工作区控制台)。本次演示地址为 `http://127.0.0.1:37781/console/`；服务退出后需按 README 重启。

## 验证结果

| 检查 | 实际结果 |
|---|---|
| Python 全量回归 | 304 tests、96 subtests 通过；其中 console 58 tests |
| 最后一次 console + 既有 daemon 定向回归 | 61 tests 通过 |
| 前端组件与查询 | 11 tests 通过 |
| TypeScript 与 Vite build | 通过；JS 330.59 kB，gzip 103.49 kB；CSS 11.02 kB |
| Playwright Chromium | 4 条端到端测试通过 |
| Python compileall / git diff --check | 通过 |
| npm 依赖审计 | 锁定依赖审计 0 vulnerabilities；Vitest 使用 4.1.11 |
| wheel 检查与安装 | HTML 及引用的 2 个 assets 齐全，无旧 assets 残留；独立 Python 虚拟环境安装后通过页面/API/资源/404 测试 |

原 Python 测试环境为 Python 3.14.7。两条已有警告来自 Starlette/httpx TestClient 与 anyio BlockingPortal 弃用提示，不影响此次结果。测试中没有读取真实 Ledger、配置、transcript，未连接真实 Judge 或 OpenViking。

## 功能与边界证据

| 要求 | 覆盖 |
|---|---|
| 页面读取无领域写入 | 遍历全部读取接口，禁止 `_run` 与外部 socket connect；读取前后数据库 logical dump 相同。fixture 禁止 `Config.load`、`Application` 构造与 socket 外连，worker 不启动，临时目录退出清理 |
| 作用域与删除 | 两工作区、另一项目、另一 owner；path/task context 校验；删除的 new 产物和 supersede 后继均屏蔽提案正文；详情访问失效后隐藏前端旧缓存 |
| 数据正确归属 | 首页先筛选本工作区有当前绑定的 active task，再 limit；检查点按绑定事件区间归属；其他工作区的运行不计入当前配额 |
| 版本与证据 | expected_revision 缺失不拿当前正文补齐；source=null 没有伪造来源链接；取代关系仅来自同一提案的明确 commit 对 |
| 统计与分页 | 等时记录完整分页无重漏，cursor 绑定范围；待审不计 commit；今日变更与累计配额分开；只改批次状态、不变 ledger_sequence 仍读取到新状态 |
| 失败与部分数据 | SQLite busy、缺库、schema 不支持、损坏 JSON/响应 DTO 返回安全错误；单卡失败不转换成零；断连保留旧快照，当前状态变为未知 |
| 刷新与切换 | 5 秒轮询，可见页启用、隐藏暂停、错误退避上限 30 秒；取消 A 请求后其迟到结果不覆盖 B；午夜窗口定时更新 |
| 质量与消费 | yes/(yes+no)，unknown 单列；无有效样本显示待采样；已输出、消费回执、人工观察分别呈现；历史预算明细缺失不补造 |
| 同源与静态边界 | loopback Host/端口、Origin、Sec-Fetch-Site；API no-store；未知 API/JS 与编码路径穿越不会变成 SPA HTML；静态内容有 CSP，无外部 CDN 依赖 |
| 浏览器交互 | 待审筛选与切回全部、提案证据到会话/记忆、后退、Escape 返回目录、Tab 可达、断连与空工作区、三个视口无横向溢出 |

## 实际截图

以下均来自正式构建后的临时 fixture 页面，已经打开检查；不是设计线框。

- [1440 像素四卡网格](assets/workspace-console/overview-1440.png)
- [1024 像素窄窗口](assets/workspace-console/overview-1024.png)
- [390 像素单列](assets/workspace-console/overview-390.png)
- [记忆版本与来源证据](assets/workspace-console/memory-detail.png)
- [连接中断与过期快照](assets/workspace-console/disconnected.png)
- [空工作区](assets/workspace-console/empty.png)

## 包与复验

本次生成 `dist/sagacontext-0.1.0-py3-none-any.whl`（构建产物不纳入 Git）。独立环境中的实际 import 来自安装后的 `site-packages`；没有启动 Node 服务。wheel SHA-256：

`663f028b1a9b971c6a2d66ea980acf9e7d999a99d4e3d6444ad3588fd0f3e598`

```sh
uv run pytest -q
uv run python -m compileall -q src scripts tests
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
CONSOLE_PYTHON="$(pwd)/.venv/bin/python" npm --prefix web run test:e2e
uv build --wheel
git diff --check
```

API 类型可通过 `scripts.console_openapi` 导出，只保留读取接口可达 DTO；生成过程不运行 daemon lifespan。详情、首页和目录共用 API 请求层与查询缓存。

## 实施调整与限制

1. 页面详情采用独立整页 URL；窄窗口保持相同导航，没有新增模态侧栏。组件按共用契约合并为 `ResourcePage`、`DetailPage` 与 `Common`，未逐项创建计划中的所有文件名。
2. 会话详情的事件、候选、批次、注入回执显示最近 100 条，超过时明确列出截断类别。顶层目录仍有分页；单条记忆返回其版本历史。尚未做大型账本性能基准。
3. 原计划部分任务的逐步“先缺模块红灯、后实现”流程改为集成实现后补边界回归；实际失败包括类型收窄、测试订阅/选择器和演示默认目录选择，均修复后通过。不能把最后通过结果描述为逐项严格 TDD 的记录。
4. 本轮独立后端审查发现的删除提案、序列化脱敏、任务筛选/检查点、effective mode 问题均已修复，并增加回归；收尾另检查并修复了缓存正文失效、空筛选与异常 DTO 的错误边界。
5. 本次只证明只读控制台与合成数据链路。长期实际运行质量、真实宿主消费和大规模数据性能不在本轮证据范围。页面没有批准/拒绝、编辑、启用、STOP、回滚写入操作。
