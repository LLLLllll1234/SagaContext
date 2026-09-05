# 本地 OpenViking 部署记录

**更新时间：** 2026-09-05
**用途：** SagaContext S3-0/G1 的本地后端旁路探针
**当前状态：** G1 已通过；17/17 必需断言为 `pass`，完整证据见 `artifacts/probes/g1-20260905T122242Z-6c4ee756/`

## 1. 部署边界

OpenViking 作为独立 Docker sidecar 运行，不属于 SagaContext Python 运行时依赖。当前不启用：

- SagaContext 真实 `BackendAdapter`；
- daemon `/events` 宿主接入；
- Codex hooks、自动注入和 transcript 扫描；
- 私人记忆或私人 transcript 导入。

OpenViking 只允许接收 G1 使用的合成数据。G1 通过前，不能把服务健康检查等同于真实投影能力已验证。

## 2. 目录与容器

项目内的部署目录为：

```text
/Users/lqy0584/Downloads/SagaContext/OpenViking/
├── docker-compose.yml
└── data/                 # 本地配置、数据库和运行数据；已被 Git 忽略
    └── ov.conf
```

Compose 使用：

```text
./data:/app/.openviking
```

外部 OpenViking 源码 checkout 位于：

```text
/Users/lqy0584/Downloads/resume-strategy/OpenViking/
```

它仅作为源码参考和上游部署文件来源，不是 SagaContext 的运行目录。当前容器实际挂载的是项目内 `OpenViking/data`。

## 3. 前置依赖

- macOS Apple Silicon：当前机器为 Apple M3、16 GB 内存；
- OrbStack：提供 Docker daemon；
- Ollama：运行在宿主机 `127.0.0.1:11434`；
- Ollama 模型：

```bash
ollama list
```

当前已准备：

```text
llama3.2:3b
nomic-embed-text:latest
```

`ollama serve` 如果提示 `address already in use`，表示 Ollama 已经在运行，不要重复启动。

## 4. 启动与停止

所有 compose 操作必须从项目部署目录执行：

```bash
cd /Users/lqy0584/Downloads/SagaContext/OpenViking
docker compose up -d openviking
docker compose ps
```

停止服务：

```bash
cd /Users/lqy0584/Downloads/SagaContext/OpenViking
docker compose down
```

查看日志：

```bash
docker logs --tail 100 openviking
```

## 5. 宿主 Ollama 连接

容器中的 `127.0.0.1` 指向容器自身，不能用于访问宿主 Ollama。容器应使用：

```text
http://host.docker.internal:11434
```

当前 `ov.conf` 的模型连接约定为：

```text
embedding: Ollama / nomic-embed-text / dimension 768
vlm: LiteLLM / ollama/llama3.2:3b
api_base: http://host.docker.internal:11434/v1
```

检查宿主和容器到 Ollama 的连通性：

```bash
curl -fsS http://127.0.0.1:11434/api/tags
docker exec openviking sh -c \
  'curl -fsS http://host.docker.internal:11434/api/tags'
```

## 6. 认证配置

Docker 容器必须监听 `0.0.0.0` 才能通过端口映射访问；因此不能使用无 key 的 `dev` 模式。当前配置使用本地专用 `api_key` 模式：

```json
{
  "server": {
    "host": "0.0.0.0",
    "auth_mode": "api_key",
    "root_api_key": "[local secret, not committed]"
  }
}
```

真实 key 不记录在文档、Git、artifact 或聊天记录中。它只存在于已被 Git 忽略的本地 `OpenViking/data/ov.conf`；探针为每次运行创建随机临时 user key，只在进程内持有，清理时撤销对应用户。

## 7. 健康检查

```bash
curl -i http://127.0.0.1:1933/health
```

2026-09-05 当前观测：

```json
{
  "status": "ok",
  "healthy": true,
  "version": "v0.4.17.1",
  "auth_mode": "api_key"
}
```

G1 正式运行时 `/ready` 也通过：AGFS、VectorDB、API key manager、embedding 和 Ollama 均为 `ok`（AGFS `multiwrite_sync` 为后端明确返回的 `not_supported`）。

服务端口：

```text
宿主 127.0.0.1:1933 -> 容器 1933
```

## 8. 当前已知注意事项

- `docker-compose.yml` 已固定为 `ghcr.io/volcengine/openviking@sha256:14553ec16f2bda9bd08a188cffb659fcfff4fde5891cbb881ed1bd8488b23294`；容器版本为 `v0.4.17.1`，镜像 ID 为 `sha256:2e6281eeeea40013db6c296261386925dc686555547f5c06456f0891b85da274`。
- 宿主端口只绑定 `127.0.0.1:1933`。
- 当前旧的 `~/.openviking` 目录仍可能存在，但容器已不再挂载它；确认项目内实例稳定后再清理旧目录。
- `OpenViking/data/` 已加入 `/Users/lqy0584/Downloads/SagaContext/.gitignore`，不得通过 `git add -f` 提交。

## 9. G1 结果

正式 probe `g1-20260905T122242Z-6c4ee756` 使用临时用户和独立 namespace 完成：

1. 同一 `memory_id` 的 revision 1/2 和另一 generation 共三个 projection 写入、完整 identity 映射与精确 locator；
2. 重复 materialize 只有一个有效 locator；
3. 三个索引均在 2.185 秒内可见，旧 revision/generation 候选经当前 identity 过滤后只保留一个；
4. 精确删除后 0.037 秒确认索引不可见；
5. sidecar 重启命令 35.039 秒完成，启动后 4.642 秒恢复健康，保留 fixture 仍可由精确 locator 定位；
6. 认证失败、服务不可用、请求超时和响应结构变化均得到独立错误分类；
7. 最终 namespace、全部 locator、索引记录和临时用户均清理完成。

17/17 必需断言均为 `pass`，因此 G1 状态为 `passed`。这只批准 OpenViking 后端候选，不代表 G3 或整个 S3-0 已通过，也不授权实现真实 adapter。
