import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "../App";
import { useRead } from "../api/queries";
import type { Envelope, Overview, ActivityRow } from "../api/types";
import {
  Card,
  Counts,
  EmptyState,
  QualityRate,
  ReadNotice,
  Status,
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
  const run = o.rollout.value;
  const task = o.tasks.value?.items.find(
    (t) => t.status === "active" && t.session_id,
  );
  const changes = o.memory_changes.value;
  const quality = run?.observations.wrong_recall;
  return (
    <>
      <div className="title-row">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h1>工作区总览</h1>
          <p className="muted">任务进展、会话与记忆变化，在同一处查看。</p>
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
      <div className="runtime-strip">
        <div>
          <span className={`dot ${result.error ? "lost" : ""}`} />
          <b>{result.error ? "连接异常" : "服务可达"}</b>
          <span className="sep">/</span>
          <span>
            调度器 {result.error ? "未知" : label(runtime?.scheduler)}
          </span>
        </div>
        <div>
          <Status value={result.error ? "unknown" : runtime?.effective_mode} />
          <span className="small">
            {result.error ? "当前状态未知" : label(runtime?.block_reason)}
          </span>
        </div>
      </div>
      {runtime?.other_workspace_id && (
        <div className="notice">
          另一个工作区有未结束的运行；当前工作区没有占用它的配额。
        </div>
      )}
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
        <Card
          title="运行与待审核"
          to={run ? `${base}/rollouts/${run.rollout_id}` : `${base}/batches`}
        >
          {o.rollout.availability === "unavailable" ? (
            <p className="muted">运行数据不可用</p>
          ) : run ? (
            <>
              <div className="run-mode">
                <Status value={run.mode} />
                <Status value={run.status} />
              </div>
              <div className="quotas">
                {[
                  ["会话配额占用", run.session_reservations, run.session_limit],
                  [
                    "候选配额占用",
                    run.candidate_reservations,
                    run.candidate_limit,
                  ],
                ].map(([name, used, limit]) => (
                  <div key={name}>
                    <small>{name}</small>
                    <p>
                      <b>{used}</b> / {limit}
                    </p>
                    <meter value={Number(used)} max={Number(limit)} />
                  </div>
                ))}
                <div>
                  <small>截止时间</small>
                  <p>{time(run.deadline)}</p>
                </div>
              </div>
              <Link
                className="queue-link"
                to={`${base}/batches?status=awaiting_review`}
              >
                <span>{run.batches.awaiting_review ?? 0} 个批次等待审核</span>
                <span>查看提案与证据 →</span>
              </Link>
            </>
          ) : (
            <EmptyState title="暂无运行记录">
              已有任务和历史记忆仍可查看。
            </EmptyState>
          )}
        </Card>
        <Card title="最近会话" to={`${base}/sessions`}>
          {o.sessions.availability === "unavailable" ? (
            <p className="muted">会话数据不可用</p>
          ) : o.sessions.value?.items.length ? (
            o.sessions.value.items.slice(0, 3).map((s) => (
              <div className="list-row" key={s.session_id}>
                <div>
                  <Link to={`${base}/sessions/${s.session_id}`}>
                    {s.host}{" "}
                    <small className="mono">{s.session_id.slice(0, 8)}</small>
                  </Link>
                  <p className="small">
                    {time(s.opened_at)} —{" "}
                    {s.closed_at ? time(s.closed_at) : "尚无结束事件"}
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
        <Card title="记忆变化" to={`${base}/memories?view=changes`}>
          <p className="small">今日 · 本工作区产生的已提交变更</p>
          {o.memory_changes.availability === "unavailable" ? (
            <p className="muted">变更数据不可用</p>
          ) : (
            <>
              <Counts
                values={{
                  new: changes?.operations.new ?? 0,
                  refine: changes?.operations.refine ?? 0,
                  supersede: changes?.operations.supersede ?? 0,
                }}
              />
              <p className="small">
                当前投影：
                {Object.entries(changes?.projection_states ?? {})
                  .map(([k, v]) => `${label(k)} ${v}`)
                  .join(" · ") || "暂无投影记录"}
              </p>
            </>
          )}
          <div className="quality-line">
            <span>误召回率</span>
            {o.rollout.availability === "unavailable" ? (
              <span className="muted">质量数据不可用</span>
            ) : quality ? (
              <QualityRate {...quality} />
            ) : (
              <QualityRate yes={0} no={0} unknown={0} />
            )}
          </div>
        </Card>
      </div>
      <Card title="最近活动" to={`${base}/activity?${window}`}>
        {o.activity.value ? (
          <Activity items={o.activity.value.items} base={base} />
        ) : (
          <p className="muted">活动数据不可用</p>
        )}
      </Card>
      <footer>
        任务归属项目 · 会话与运行归属工作区 · 记忆遵循自身作用域
        <span>
          今日范围 · {Intl.DateTimeFormat().resolvedOptions().timeZone}
        </span>
      </footer>
    </>
  );
}
