import { Link, useParams, useSearchParams } from "react-router-dom";
import { useWorkspace } from "../App";
import { useRead } from "../api/queries";
import type {
  Envelope,
  Page,
  TaskRow,
  SessionRow,
  MemoryRow,
  BatchRow,
  ActivityRow,
} from "../api/types";
import {
  EmptyState,
  PagedList,
  Payload,
  ReadNotice,
  Status,
  time,
} from "../components/Common";
import { Activity, useToday } from "./OverviewPage";

type Row = TaskRow | SessionRow | MemoryRow | BatchRow | ActivityRow;
export default function ResourcePage() {
  const { projectId, workspaceId, base } = useWorkspace();
  const { resource } = useParams();
  const [search, setSearch] = useSearchParams();
  const defaultWindow = useToday();
  const q = new URLSearchParams(search);
  q.set("workspace_id", workspaceId);
  q.set("limit", "50");
  if (resource === "activity") {
    if (!q.has("start")) q.set("start", defaultWindow.get("start")!);
    if (!q.has("end")) q.set("end", defaultWindow.get("end")!);
  }
  const endpoint =
    resource === "tasks" || resource === "memories"
      ? `/projects/${projectId}/${resource}`
      : `/workspaces/${workspaceId}/${resource}`;
  const result = useRead<Envelope<Page<Row>>>(`${endpoint}?${q}`);
  const rows = result.data?.data.items ?? [];
  const title =
    (
      {
        tasks: "项目任务",
        sessions: "会话",
        memories: "记忆与证据",
        batches: "运行与待审核",
        activity: "活动记录",
      } as Record<string, string>
    )[resource ?? ""] ?? "记录";
  const filters = (key: string, value: string) => {
    const next = new URLSearchParams(search);
    next.delete("cursor");
    next.set(key, value);
    setSearch(next);
  };
  return (
    <>
      <div className="title-row">
        <div>
          <p className="eyebrow">WORKSPACE RECORDS</p>
          <h1>{title}</h1>
          <p className="muted">每条记录都保留所属范围与来源。</p>
        </div>
        <span className="snapshot">{time(result.data?.meta.observed_at)}</span>
      </div>
      <ReadNotice
        error={result.error}
        observed={result.data?.meta.observed_at}
      />
      {resource === "memories" && (
        <div className="filters">
          <label>
            查看{" "}
            <select
              value={search.get("view") ?? "applicable"}
              onChange={(e) => filters("view", e.target.value)}
            >
              <option value="applicable">项目适用记忆</option>
              <option value="global">全局记忆</option>
              <option value="changes">本工作区变更来源</option>
            </select>
          </label>
          <label>
            相对路径{" "}
            <input
              placeholder="如 src/app.py"
              defaultValue={search.get("touched_paths") ?? ""}
              onBlur={(e) => filters("touched_paths", e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter")
                  filters("touched_paths", e.currentTarget.value);
              }}
            />
          </label>
          <label>
            任务 ID{" "}
            <input
              placeholder="查看任务作用域"
              defaultValue={search.get("task_id") ?? ""}
              onBlur={(e) => filters("task_id", e.target.value)}
            />
          </label>
        </div>
      )}
      {resource === "batches" && (
        <div className="filters">
          <label>
            批次状态{" "}
            <select
              value={search.get("status") ?? ""}
              onChange={(e) => filters("status", e.target.value)}
            >
              <option value="">全部</option>
              <option value="awaiting_review">待审核</option>
              <option value="blocked">受阻</option>
              <option value="retry">等待重试</option>
              <option value="settled">已处理</option>
            </select>
          </label>
        </div>
      )}
      <section className="card resource-list">
        {!rows.length ? (
          <EmptyState
            title={
              result.isLoading
                ? "正在读取"
                : result.error
                  ? "记录暂不可用"
                  : "当前范围暂无记录"
            }
          />
        ) : resource === "activity" ? (
          <Activity items={rows as ActivityRow[]} base={base} />
        ) : (
          rows.map((r) => {
            if ("goal" in r)
              return (
                <article className="record" key={r.task_id}>
                  <div className="record-head">
                    <Link to={`${base}/tasks/${r.task_id}`}>{r.goal}</Link>
                    <Status value={r.status} />
                  </div>
                  <p className="small">
                    最近活动 {time(r.last_active)} ·{" "}
                    {r.session_id
                      ? "与此工作区有关联"
                      : "项目任务，当前工作区无会话绑定"}
                  </p>
                  {r.session_id && (
                    <Link to={`${base}/sessions/${r.session_id}`}>
                      关联会话 →
                    </Link>
                  )}
                </article>
              );
            if ("opened_at" in r)
              return (
                <article className="record" key={r.session_id}>
                  <div className="record-head">
                    <Link to={`${base}/sessions/${r.session_id}`}>
                      {r.host}{" "}
                      <small className="mono">{r.session_id.slice(0, 8)}</small>
                    </Link>
                    <span className="tag">
                      {r.closed_at ? "已结束" : "尚无结束事件"}
                    </span>
                  </div>
                  <p className="small">
                    {time(r.opened_at)} — {time(r.closed_at)}
                  </p>
                  {r.task_id && (
                    <Link to={`${base}/tasks/${r.task_id}`}>关联任务 →</Link>
                  )}
                </article>
              );
            if ("memory_id" in r && "payload" in r) {
              const qs = new URLSearchParams(search);
              qs.set("project_id", projectId);
              qs.delete("cursor");
              return (
                <article className="record" key={r.memory_id}>
                  <div className="record-head">
                    <Link to={`${base}/memories/${r.memory_id}?${qs}`}>
                      记忆{" "}
                      <span className="mono">{r.memory_id.slice(0, 8)}</span> ·
                      v{r.current_revision}
                    </Link>
                    <div>
                      <Status value={r.scope_kind} /> <Status value={r.state} />
                    </div>
                  </div>
                  <Payload value={r.payload} />
                </article>
              );
            }
            if ("proposal_count" in r)
              return (
                <article className="record" key={r.batch_id}>
                  <div className="record-head">
                    <Link to={`${base}/batches/${r.batch_id}`}>
                      批次{" "}
                      <span className="mono">{r.batch_id.slice(0, 8)}</span>
                    </Link>
                    <Status value={r.status} />
                  </div>
                  <p className="small">
                    {r.proposal_count} 个提案 · {time(r.created_at)} · 尝试{" "}
                    {r.attempt_count} 次
                  </p>
                  {r.last_error_class && (
                    <p className="warning">{r.last_error_class}</p>
                  )}
                  {r.rollout_id && (
                    <Link to={`${base}/rollouts/${r.rollout_id}`}>
                      所属运行 →
                    </Link>
                  )}
                </article>
              );
            return null;
          })
        )}
        <PagedList next={result.data?.data.next_cursor} />
      </section>
    </>
  );
}
