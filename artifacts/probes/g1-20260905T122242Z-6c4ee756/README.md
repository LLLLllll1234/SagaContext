# OpenViking G1 准入探针

**Probe ID：** `g1-20260905T122242Z-6c4ee756`
**状态：** `passed`
**运行时间：** `2026-09-05T12:22:42.763310+00:00` 至 `2026-09-05T12:23:22.660269+00:00`

## 固定环境

- 容器版本：`v0.4.17.1`
- API 版本：`0.1.0`
- 镜像：`ghcr.io/volcengine/openviking@sha256:14553ec16f2bda9bd08a188cffb659fcfff4fde5891cbb881ed1bd8488b23294`
- 镜像 ID：`sha256:2e6281eeeea40013db6c296261386925dc686555547f5c06456f0891b85da274`
- 上游源码 revision：`420d4074f74070bc6fe7151aa54527cf1ff15152`
- 配置 fingerprint：`sha256:eb13902fa03a1c2068c0362ce08a94747e555b43856a3f58d61155ff03a3b2cc`
- namespace：`viking://user/sagag1-e4a46c5b0f94/memories/sagacontext-g1/g1-20260905T122242Z-6c4ee756`
- payload：仅合成数据；HTTP artifact 不保存正文、key 或响应正文

## 必需断言

| 断言 | 结果 |
|---|---|
| `image_reference_pinned` | **pass** |
| `backend_ready` | **pass** |
| `isolated_namespace` | **pass** |
| `projection_write` | **pass** |
| `identity_mapping` | **pass** |
| `exact_locator` | **pass** |
| `idempotent_materialize` | **pass** |
| `revision_generation_locators` | **pass** |
| `index_eventually_visible` | **pass** |
| `old_revision_and_generation_filtered` | **pass** |
| `precise_delete` | **pass** |
| `locator_survives_restart` | **pass** |
| `authentication_failure_classified` | **pass** |
| `backend_unavailable_classified` | **pass** |
| `request_timeout_classified` | **pass** |
| `response_shape_change_classified` | **pass** |
| `namespace_cleanup` | **pass** |

## 等待与清理

- 可见性观测：`visible` 2.185s/3 polls, `visible` 0.046s/1 polls, `visible` 0.053s/1 polls, `absent` 0.037s/1 polls, `absent` 0.219s/1 polls, `absent` 0.045s/1 polls, `absent` 0.036s/1 polls
- sidecar 重启：命令 35.039s；启动后健康恢复 4.642s
- 清理结果：`pass`
- 精确 locator 全部不可读：`True`
- 索引条目全部不可见：`True`

完整脱敏请求/响应摘要见同目录 `g1-openviking.json`。G1 仅在全部必需断言为 `pass` 时为 `passed`。
