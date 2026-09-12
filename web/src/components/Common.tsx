import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

export const labels: Record<string, string> = {
  user_statement: "用户陈述",
  source_event: "来源事件",
  convention: "项目约定",
  read_failed: "读取失败",
  ledger_missing: "账本不存在",
  schema_unsupported: "账本版本不受支持",
  off: "未启用",
  shadow: "观察",
  guarded: "受控",
  unknown: "未知",
  active: "进行中",
  paused: "暂停",
  completed: "完成",
  abandoned: "已放弃",
  running: "运行中",
  draining: "收尾中",
  stopping: "正在停止",
  stopped: "已停止",
  cleanup_required: "需要清理",
  awaiting_review: "待审核",
  settled: "已处理",
  retry: "等待重试",
  blocked: "受阻",
  pending: "等待处理",
  proposed: "已提议",
  committed: "已提交",
  new: "新增",
  refine: "补全",
  supersede: "取代",
  confirm: "确认",
  conflict: "冲突",
  no_change: "无变化",
  emitted: "已输出",
  consumed: "有消费记录",
  confirmed: "已确认",
  disabled: "未启用",
  error: "异常",
  user_message: "用户消息",
  checkpoint_requested: "检查点",
  session_opened: "会话开始",
  session_closed: "会话结束",
  injection: "记忆输出",
  batch: "批次",
  memory_new: "记忆新增",
  memory_refine: "记忆补全",
  memory_supersede: "记忆取代",
  projection: "投影状态",
  consumption: "消费记录",
  retired: "已退场",
  deleted: "已删除",
  none: "无冲突",
  unresolved: "未解决",
  project: "项目",
  global: "全局",
  path: "路径",
  task: "任务",
  configured_off: "配置未启用",
  no_run: "无运行记录",
  stop_active: "停止开关已生效",
  deadline_exceeded: "超过截止时间",
  run_not_allowed: "运行已停止或受限",
  data_invalid: "数据不可用",
  connection_lost: "连接中断",
  ledger_busy: "账本暂忙",
  not_found: "对象不存在或不可访问",
};
export const label = (value: string | null | undefined) =>
  value ? (labels[value] ?? value) : "—";
export function Status({ value }: { value: string | null | undefined }) {
  return (
    <span
      className={`tag ${["blocked", "error", "cleanup_required"].includes(value ?? "") ? "danger" : ["awaiting_review", "retry", "draining"].includes(value ?? "") ? "amber" : ["confirmed", "completed", "committed"].includes(value ?? "") ? "green" : ""}`}
    >
      {label(value)}
    </span>
  );
}
export const time = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export function EmptyState({
  title = "暂无数据",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">◇</span>
      <h2>{title}</h2>
      <p>{children ?? "这个范围内尚无记录。"}</p>
    </div>
  );
}
export function Card({
  title,
  to,
  children,
}: {
  title: string;
  to?: string;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <header>
        <h2>{title}</h2>
        {to && (
          <Link to={to}>
            查看详情 <span aria-hidden>↗</span>
          </Link>
        )}
      </header>
      {children}
    </section>
  );
}
export function QualityRate({
  yes,
  no,
  unknown,
}: {
  yes: number;
  no: number;
  unknown: number;
}) {
  const n = yes + no;
  return (
    <span>
      {n ? `${((yes / n) * 100).toFixed(1)}%` : "— 待采样"}{" "}
      <small>
        是 {yes} · 否 {no} · 未知 {unknown} · 有效标注 {n}
      </small>
    </span>
  );
}
export function Counts({ values }: { values: Record<string, number> }) {
  return Object.keys(values).length ? (
    <div className="counts">
      {Object.entries(values).map(([key, count]) => (
        <div key={key}>
          <b>{count}</b>
          <small>{label(key)}</small>
        </div>
      ))}
    </div>
  ) : (
    <p className="muted">暂无记录</p>
  );
}
export function Payload({ value }: { value: unknown }) {
  return (
    <pre>{value == null ? "正文不可用" : JSON.stringify(value, null, 2)}</pre>
  );
}
export function RevisionDiff({
  oldText,
  newText,
}: {
  oldText: unknown;
  newText: unknown;
}) {
  return (
    <div className="diff">
      <div>
        <h3>旧版本</h3>
        {oldText == null ? (
          <p className="muted">旧版本不可用</p>
        ) : (
          <Payload value={oldText} />
        )}
      </div>
      <div>
        <h3>建议内容</h3>
        <Payload value={newText} />
      </div>
    </div>
  );
}
export function PagedList({ next }: { next?: string | null }) {
  const location = useLocation();
  if (!next) return null;
  const search = new URLSearchParams(location.search);
  search.set("cursor", next);
  return (
    <Link className="button" to={`${location.pathname}?${search}`}>
      下一页 →
    </Link>
  );
}
export function ReadNotice({
  error,
  observed,
}: {
  error?: Error | null;
  observed?: string;
}) {
  return error ? (
    <div className="notice warning" role="status">
      <b>{label(error.message)}</b>
      {observed
        ? ` · 正在展示 ${time(observed)} 的快照，当前状态未知。`
        : " · 读取失败，请检查本地服务。"}
    </div>
  ) : null;
}
