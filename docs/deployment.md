# SagaContext V1 部署与接入

SagaContext V1 的安装默认是安全的：daemon 绑定 loopback，`rollout.mode=off`，不会自动启动 hooks、采集事件、连接 Judge 或写入 OpenViking。先安装和检查，再按需接入。

## 一键安装

在仓库根目录运行：

```sh
./scripts/install.sh
```

脚本会检查仓库、使用 `uv sync --locked` 创建依赖环境，并在 `~/.sagacontext/config.toml` 不存在时生成权限为 `0600` 的模板。已有配置不会被覆盖。

常用选项：

```sh
# 只预览命令，不写配置、不启动服务
./scripts/install.sh --dry-run

# 启动 pinned OpenViking sidecar，再生成 SagaContext 配置
./scripts/install.sh --with-openviking

# 将当前仓库的 Codex hooks 写入目标 workspace；已有文件先备份
./scripts/install.sh --with-hooks --workspace /path/to/project

# 显式启动 daemon，并在健康后打开只读控制台
./scripts/install.sh --start --open-console
```

`--with-hooks` 只生成四个事件入口：`SessionStart`、`UserPromptSubmit`、`Stop`、`SessionEnd`。它不会启用 guarded；配置和 operator token 仍由用户显式提供。已有 `.codex/hooks.json` 会保留到 `hooks.json.sagacontext-backup`，写入采用临时文件替换。

hooks 接入要求宿主版本严格为 `codex-cli 0.153.4`；版本不匹配或命令不存在时，脚本会在任何写入前失败。

`--install-service` 注册一个直接由 launchd/systemd 托管的 daemon 服务，服务环境固定为 `rollout.mode=off`；脚本不会把一次性安装命令伪装成长驻服务。服务注册前仍会完成 `uv sync --locked` 和配置检查。

Windows PowerShell 使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun
```

## 配置与接入

编辑 `~/.sagacontext/config.toml` 时，密钥只使用受保护文件路径或环境变量，不要写入仓库。先运行：

```sh
.venv/bin/python -m sagacontext.cli config-doctor
```

它只打印配置是否存在、daemon 端口和配额，不打印密钥值。真实 Judge、OpenViking 普通用户 namespace、固定 Codex 版本和授权主体必须分别验证。V1 默认只支持 loopback 控制台；不要把 operator token 发给浏览器。

配置好后可显式启动：

```sh
.venv/bin/python -m sagacontext.operator start
```

读取页面位于 `http://127.0.0.1:37780/console/`，读取 API 位于 `/console/v1/`。控制台只有观察、分页和证据导航，不提供 approve/reject、编辑、STOP、rollback 或激活按钮。

## 验证与停止

```sh
curl -fsS http://127.0.0.1:37780/health
.venv/bin/python -m sagacontext.operator status
.venv/bin/python -m sagacontext.operator stop
```

停止会设置本地 STOP 开关；清理状态仍以 Ledger 和已有回执为准。安装脚本不会自动改变正在运行的 rollout。

## 合成演示

不接触真实配置时，可运行只读演示：

```sh
PYTHONPATH=src .venv/bin/python -m scripts.serve_console_fixture --port 37781
```

演示使用临时数据库并在退出时清理，页面会显示“演示环境”。`--scenario empty` 和 `--scenario stale` 用于空数据、断连和旧快照验证。

## 发布边界

`main` 的 `v1.0.0` 是 guarded memory baseline；控制台和本部署脚本属于同一 V1 交付，但不会改变默认 off 或自动授权范围。长期质量、其他宿主和跨平台服务管理需要独立验收。
