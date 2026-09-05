# S3 测试结果清单

发布分支：`codex/s3-acceptance-2026-09-06`。人读报告见 [S3-1](../../docs/probes/2026-09-05-s3-1-openviking-recovery.md) 和 [S3-2 至 S3-5](../../docs/probes/2026-09-06-s3-policy-shadow-g5-g6.md)。

## 原始运行

| UTC 运行 ID | 范围 | 结果 | JSON |
|---|---|---|---|
| s3-1-20260905T154749Z-6641f690 | 首次 S3-1 | passed，22 项 | [记录](s3-1-20260905T154749Z-6641f690/s3-1.json) |
| s3-1-20260905T160014Z-2fb03cc5 | 扩展调试 | failed，runner 的 evidence 关联查询错误；清理通过 | [记录](s3-1-20260905T160014Z-2fb03cc5/s3-1.json) |
| s3-1-20260905T160137Z-85b10ac2 | S3-1 至 G5 | passed，40 项 | [记录](s3-1-20260905T160137Z-85b10ac2/s3-1.json) |
| s3-1-20260905T160452Z-1685e03f | 首次三条 G6 | passed，63 项 | [记录](s3-1-20260905T160452Z-1685e03f/s3-1.json) |
| s3-1-20260905T160955Z-941808e7 | 完整纵向，含逐条删除/重开 | passed，69 项 | [记录](s3-1-20260905T160955Z-941808e7/s3-1.json) |
| s3-1-20260905T161222Z-9482aa6f | 最终后端与策略回归 | passed，40 项 | [记录](s3-1-20260905T161222Z-9482aa6f/s3-1.json) |

这些是不同运行，不相加为一个测试通过数。所有运行均保留各自状态和清理结果。JSON 中的 base code revision 是运行时尚未提交变更所基于的提交；`source_digests` 标识当时实际执行文件，不能把 base revision 当成包含本次实现的提交。

## 本地回归

[提交前 unittest 日志](s3-release-unittest.log)：111 项测试。复验命令：

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py --policy-stages
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py --longitudinal
```

后两条会创建临时测试用户、真实后端投影；`--longitudinal` 还运行隔离 Codex CLI 会话。凭据和内部连接配置不进入 artifact。测试 fixture 和 ScriptedJudge 的边界见人读报告。
