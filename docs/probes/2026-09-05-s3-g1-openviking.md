# S3-0 / G1 OpenViking 后端准入

**日期：** 2026-09-05
**Probe ID：** `g1-20260905T122242Z-6c4ee756`
**结论：** `passed`，17/17 必需断言为 `pass`

## 1. 固定环境与隔离边界

- OpenViking：`v0.4.17.1`；HTTP API `0.1.0`；上游镜像 revision `420d4074f74070bc6fe7151aa54527cf1ff15152`。
- 镜像：`ghcr.io/volcengine/openviking@sha256:14553ec16f2bda9bd08a188cffb659fcfff4fde5891cbb881ed1bd8488b23294`。
- 本地镜像 ID：`sha256:2e6281eeeea40013db6c296261386925dc686555547f5c06456f0891b85da274`，`linux/arm64`。
- 配置 fingerprint：`sha256:eb13902fa03a1c2068c0362ce08a94747e555b43856a3f58d61155ff03a3b2cc`；计算前递归替换 key/token/secret 字段。
- 数据边界：随机临时用户、独立 `sagacontext-g1/<probe_id>` namespace、纯合成 projection；未读取或写入私人记忆。
- 认证边界：root key 只创建/撤销临时用户；projection 使用临时 user key；所有 key 仅驻留进程内且未写入 artifact。

## 2. Identity 与 locator

探针写入三个 fixture：同一 `memory-alpha` 的 `generation-a/r1`、`generation-a/r2`，以及同形的 `generation-b/r1`。每条记录保存并核验：

```text
memory_id / revision / generation / operation_key /
projection_identity / payload_digest / exact locator
```

重复写入 `generation-a/r1` 后，管理区内仍只有一个有效 locator。三条后端候选按 Ledger 当前 identity `(memory-alpha, 2, generation-a)` 过滤后只保留一条，旧 revision 和另一 generation 不会被当作当前结果。

## 3. 可见性、删除与重启

| 观测 | 结果 | 等待 |
|---|---|---:|
| `generation-a/r1` 首次索引可见 | pass | 2.185s / 3 polls |
| `generation-a/r2` 首次索引可见 | pass | 0.046s / 1 poll |
| `generation-b/r1` 首次索引可见 | pass | 0.053s / 1 poll |
| 精确删除 `generation-a/r1` 后索引不可见 | pass | 0.037s / 1 poll |
| sidecar 重启后 r2 与 generation-b/r1 精确定位 | pass | 35.039s（启动后健康恢复 4.642s） |
| 最终 namespace 清理后全部索引不可见 | pass | 单次轮询均不超过 0.219s |

最终确认：全部 locator 不可读、全部 probe 索引条目不可见、namespace 已删除、临时用户已从账户用户列表移除。

## 4. 错误分类

| 注入场景 | 分类 | 结果 |
|---|---|---|
| 无效 API key | `authentication_failed` | pass |
| loopback 不可监听端口 | `backend_unavailable` | pass |
| loopback 延迟响应超过 client timeout | `backend_timeout` | pass |
| HTTP 200 但缺少 `status=ok/result` envelope | `response_schema_changed` | pass |

这些断言只验证 G1 probe client 的可观测分类；真实 `OpenVikingBackendAdapter` 仍须在 S3-1 获批后按已验证契约实现。

## 5. Artifact

- 人读摘要：[`artifacts/probes/g1-20260905T122242Z-6c4ee756/README.md`](../../artifacts/probes/g1-20260905T122242Z-6c4ee756/README.md)
- 完整机器证据：[`artifacts/probes/g1-20260905T122242Z-6c4ee756/g1-openviking.json`](../../artifacts/probes/g1-20260905T122242Z-6c4ee756/g1-openviking.json)
- 可重复 runner：[`scripts/probe_openviking_backend.py`](../../scripts/probe_openviking_backend.py)

机器 artifact 含 39 条脱敏 HTTP request/response 摘要：方法、API path、body/query 字段、脱敏后 digest、字节数、响应 shape、状态码、错误分类和耗时；不保存 header、key、请求正文或响应正文。

此前调试运行均使用独立 ID 保留：它们记录了本机代理继承、root-key tenant 访问限制和内容尾部元数据解析等问题，且没有留下 projection 或临时用户。最终准入只由本页指定、字段完整的通过 probe 判定。

## 6. 门槛边界

G1 满足已批准设计中的全部必需断言，因此状态为 `passed`。G3 仍是独立门槛；在 G3 也通过并另行批准实现计划前，项目继续停留在 S2，不实现真实 projector、不启用正常会话 hooks、不导入私人数据。
