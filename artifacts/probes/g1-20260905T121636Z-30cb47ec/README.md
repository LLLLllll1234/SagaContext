# OpenViking G1 准入探针

**Probe ID：** `g1-20260905T121636Z-30cb47ec`
**状态：** `failed_contract`
**运行时间：** `2026-09-05T12:16:36.698113+00:00` 至 `2026-09-05T12:16:36.917840+00:00`

## 固定环境

- 容器版本：`None`
- API 版本：`None`
- 镜像：`None`
- 镜像 ID：`None`
- 上游源码 revision：`None`
- 配置 fingerprint：`None`
- namespace：``
- payload：仅合成数据；HTTP artifact 不保存正文、key 或响应正文

## 必需断言

| 断言 | 结果 |
|---|---|
| `namespace_cleanup` | **fail** |

## 等待与清理

- 可见性观测：`not_run`
- 清理结果：`not_run`
- 精确 locator 全部不可读：`None`
- 索引条目全部不可见：`None`

完整脱敏请求/响应摘要见同目录 `g1-openviking.json`。G1 仅在全部必需断言为 `pass` 时为 `passed`。
