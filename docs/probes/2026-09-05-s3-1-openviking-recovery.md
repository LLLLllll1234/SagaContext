# S3-1 OpenViking 适配器与真实故障恢复

**状态：** 首次真实后端验收通过，22/22 断言；P1–P6 已在后续 69/69 完整运行中再次通过。后续阶段见 [S3 合成纵向验收](2026-09-06-s3-policy-shadow-g5-g6.md)。

## 实现

`src/sagacontext/backends/openviking.py` 实现 BackendAdapter，通过 G1 已验证的 HTTP content write/read、search/find、fs 删除接口运行。Ledger 提交生成 outbox，显式 Projector drain 写入真实 sidecar，核验完整 projection 后记录 receipt。没有常驻 worker 或全局 hooks。

每个实例固定 owner 和独立 `sagacontext/<run_id>` 管理区。generation、memory_id、revision 规范化哈希决定唯一 URI；operation_key 随正文保存。重复请求先精确读取并核对正文、摘要和 operation_key，不重复 materialize。后端不提供 CAS，`exactly_once=false`；本地 lease fencing 和 Ledger 当前 revision 决定有效性。

适配器对不确定写入返回 unknown，对核验不可用返回 verification timeout；认证拒绝和身份冲突 fail closed。Projector 增补实际调用耗时的 lease 检查、恰好过期时回收、核验异常持久化，以及未知删除按目标 locator 不存在恢复。

## 真实验收

运行记录：[s3-1-20260905T154749Z-6641f690](../../artifacts/probes/s3-1-20260905T154749Z-6641f690/s3-1.json)。固定镜像 digest 与 G1 相同，运行前验证容器实际 image ID 匹配；记录代码 revision 和修改文件的内容摘要。

| 序列 | 结果与证据 |
|---|---|
| P1 | Ledger 重开后 outbox 可显式 drain，confirmed |
| P2 | 真实 HTTP 写成功后在客户端传输层丢弃响应，outbox unknown；关闭客户端与 Ledger 后重开，通过精确 locate 恢复 confirmed，未增加 write 请求 |
| P3 | r2 confirmed 后才发出 r1 的真实写请求；旧 claim 完成时判 obsolete，生成并完成精确删除 |
| P4 | 在 lease 恰好到期的边界回收；调用前到期转 retry，调用后到期转 unknown 并恢复；旧 worker 均被 fenced |
| P5 | 已真实写入后注入核验传输超时，第二次尝试进入 blocked，保留记录 |
| P6 | 显式重复 materialize 返回相同 locator；重放 outbox 复用唯一 receipt，不增加 write，attempt 编号递增 |
| 检索与清理 | r2 在 60 秒轮询窗口内可见；管理区枚举 6 个投影；结束时精确 locator 不可读、搜索无命中、临时用户撤销 |

故障发生在真实后端外的客户端传输层；未声称模拟了 sidecar 内部崩溃。P4 使用显式逻辑时间跨越 lease，未等待 30 秒墙钟；真实写读照常发生。另有单元测试覆盖 drain 的实际耗时跨越 lease。sidecar 重启持久性沿用 G1 证据，本轮没有再次重启。

## 复验

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py
```

runner 只创建临时合成用户与 namespace，凭据驻留内存。Artifact 包含脱敏请求摘要、outbox、attempt、receipt、断言和清理结果，不包含认证材料或内部连接配置。首次通过不等于 S3-2 RecallPolicy、S3-3 Shadow、G5 或 G6 通过。
