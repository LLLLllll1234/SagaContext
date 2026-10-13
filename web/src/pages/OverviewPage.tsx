import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "../App";
import { useRead } from "../api/queries";
import type {
  Envelope,
  Overview,
  ActivityRow,
  MemoryRow,
  Page,
} from "../api/types";
import {
  Card,
  Counts,
  EmptyState,
  HostBadge,
  ReadNotice,
  Status,
  Summary,
  collectionOnlyHosts,
  label,
  time,
} from "../components/Common";

export function today() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return new URLSearchParams({
    start: start.toISOString(),
    end: end.toISOString(),
  });
}
export function useToday() {
  const [window, setWindow] = useState(today);
  useEffect(() => {
    const id = globalThis.setInterval(
      () =>
        setWindow((previous) => {
          const next = today();
          return next.toString() === previous.toString() ? previous : next;
        }),
      30000,
    );
    return () => globalThis.clearInterval(id);
  }, []);
  return window;
}
export function Activity({
  items,
  base,
}: {
  items: ActivityRow[];
  base: string;
}) {
  return (
    <div className="activity">
      {items.map((item) => {
        const to = item.memory_id
          ? `${base}/memories/${item.memory_id}`
          : item.batch_id
            ? `${base}/batches/${item.batch_id}`
            : item.session_id
              ? `${base}/sessions/${item.session_id}`
              : item.rollout_id
                ? `${base}/rollouts/${item.rollout_id}`
                : null;
        return (
          <div
            className="activity-row"
            key={`${item.object_type}:${item.object_id}`}
          >
            <time>{time(item.recorded_at)}</time>
            <Status value={item.kind} />
            <span>
              {label(item.status)}{" "}
              <small className="mono">{item.object_id.slice(0, 8)}</small>
            </span>
            {to ? (
              <Link to={to}>查看记录 ↗</Link>
            ) : (
              <span className="muted">来源不可定位</span>
            )}
          </div>
        );
      })}
      {!items.length && <p className="muted">当前时间范围内没有活动。</p>}
    </div>
  );
}
// Why there is no memory yet, in the order a user would act on it.
export function emptyMemoryHint(
  awaiting: number,
  mode: string | null | undefined,
  base: string,
): { text: string; to?: string; link?: string } {
  if (awaiting > 0)
    return {
      text: `有 ${awaiting} 个批次等待审核，审核通过后记忆才会写入。`,
      to: `${base}/batches?status=awaiting_review`,
      link: "查看待审核批次 →",
    };
  if (!mode || mode === "off")
    return {
      text: "记忆服务未启用，会话不会产生候选记忆。",
      to: "/deployment",
      link: "查看接入步骤 →",
    };
  return {
    text: "运行已开启，会话结束后产生的候选记忆审核通过后会出现在这里。",
  };
}
function RecentMemories({
  awaiting,
  mode,
}: {
  awaiting: number;
  mode: string | null | undefined;
}) {
  const { projectId, workspaceId, base } = useWorkspace();
  const result = useRead<Envelope<Page<MemoryRow>>>(
    `/projects/${projectId}/memories?workspace_id=${workspaceId}&limit=5`,
  );
  const rows = result.data?.data.items;
  if (!rows)
    return (
      <p className="muted">
        {result.isLoading ? "正在读取记忆" : "记忆数据不可用"}
      </p>
    );
  if (!rows.length) {
    const hint = emptyMemoryHint(awaiting, mode, base);
    return (
      <EmptyState title="还没有适用于本项目的记忆">
        {hint.text} {hint.to && <Link to={hint.to}>{hint.link}</Link>}
      </EmptyState>
    );
  }
  return (
    <ul className="memory-list">
      {rows.map((m) => (
        <li key={m.memory_id}>
          <Link to={`${base}/memories/${m.memory_id}?project_id=${projectId}`}>
            <Summary text={m.summary} />
          </Link>
          <span className="small">
            {label(m.scope_kind)} · {label(m.memory_type)} ·{" "}
            {time(m.created_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}
export default function OverviewPage() {
  const { workspaceId, base } = useWorkspace();
  const window = useToday();
  const result = useRead<Envelope<Overview>>(
    `/workspaces/${workspaceId}/overview?${window}`,
  );
  const o = result.data?.data;
  if (!o)
    return (
      <>
        <ReadNotice error={result.error} />
        <EmptyState
          title={result.isLoading ? "正在读取工作区" : "工作区暂不可用"}
        />
      </>
    );
  const runtime = o.runtime.value;
  const task = o.tasks.value?.items.find(
    (t) => t.status === "active" && t.session_id,
  );
  const changes = o.memory_changes.value;
  const awaiting = o.rollout.value?.batches.awaiting_review ?? 0;
  return (
    <>
      <div className="title-row">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h1>工作区总览</h1>
          <p className="muted">Agent 在这个工作区记住了什么、带回了什么。</p>
        </div>
        <div className="snapshot">
          最近快照
          <br />
          <b>{time(result.data?.meta.observed_at)}</b>
        </div>
      </div>
      <ReadNotice
        error={result.error}
        observed={result.data?.meta.observed_at}
      />
      <Link className="status-line" to={`${base}/ops`}>
        <span className={`dot ${result.error ? "lost" : ""}`} />
        <span>
          {result.error ? "连接异常" : "服务可达"} · 记忆服务{" "}
          <Status value={result.error ? "unknown" : runtime?.effective_mode} />
          {!result.error && runtime?.block_reason && (
            <span className="small"> {label(runtime.block_reason)}</span>
          )}
          {awaiting > 0 && (
            <span className="small"> · {awaiting} 个批次待审核</span>
          )}
        </span>
        <span>查看运维状态 →</span>
      </Link>
      <div className="grid">
        <Card title="当前任务" to={`${base}/tasks`}>
          {o.tasks.availability === "unavailable" ? (
            <p className="muted">任务数据不可用</p>
          ) : task ? (
            <>
              <div className="task-heading">
                <h3>{task.goal}</h3>
                <Status value={task.status} />
              </div>
              <p className="small">项目任务 · 与本工作区会话关联</p>
              <div className="checkpoint">
                <span className="eyebrow">最近检查点</span>
                <p>
                  {task.checkpoint_at
                    ? time(task.checkpoint_at)
                    : "暂无检查点事件"}
                </p>
                <Link to={`${base}/sessions/${task.session_id}`}>
                  查看关联会话 →
                </Link>
              </div>
            </>
          ) : (
            <EmptyState title="暂无关联任务">
              还没有任务绑定到这个工作区的会话。
            </EmptyState>
          )}
        </Card>
        <Card title="适用记忆" to={`${base}/memories`}>
          <RecentMemories
            awaiting={awaiting}
            mode={result.error ? undefined : runtime?.effective_mode}
          />
        </Card>
        <Card title="最近会话" to={`${base}/sessions`}>
          {o.sessions.availability === "unavailable" ? (
            <p className="muted">会话数据不可用</p>
          ) : o.sessions.value?.items.length ? (
            o.sessions.value.items.slice(0, 3).map((s) => (
              <div className="list-row" key={s.session_id}>
                <div>
                  <Link to={`${base}/sessions/${s.session_id}`}>
                    <HostBadge host={s.host} />{" "}
                    <small className="mono">{s.session_id.slice(0, 8)}</small>
                  </Link>
                  <p className="small">
                    {time(s.opened_at)} ·{" "}
                    {collectionOnlyHosts.has(s.host)
                      ? "仅采集"
                      : s.injected_count == null
                        ? "未带入记忆"
                        : `带入 ${s.injected_count} 条记忆`}
                  </p>
                </div>
                <span className="session-icon">↗</span>
              </div>
            ))
          ) : (
            <EmptyState title="暂无会话" />
          )}
          <p className="caption">依据已收到的会话事件展示。</p>
        </Card>
        <Card title="今日记忆变化" to={`${base}/memories?view=changes`}>
          <p className="small">本工作区今天提交的变更</p>
          {o.memory_changes.availability === "unavailable" ? (
            <p className="muted">变更数据不可用</p>
          ) : (
            <Counts
              values={{
                new: changes?.operations.new ?? 0,
                refine: changes?.operations.refine ?? 0,
                supersede: changes?.operations.supersede ?? 0,
              }}
            />
          )}
        </Card>
      </div>
      <footer>
        任务归属项目 · 会话与运行归属工作区 · 记忆遵循自身作用域
        <span>
          今日范围 · {Intl.DateTimeFormat().resolvedOptions().timeZone}
        </span>
      </footer>
    </>
  );
}
