import { useEffect } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import { useRead } from "./api/queries";
import type { Directory, Envelope } from "./api/types";
import { EmptyState, ReadNotice } from "./components/Common";
import OverviewPage from "./pages/OverviewPage";
import ResourcePage from "./pages/ResourcePage";
import DetailPage from "./pages/DetailPage";
import DeploymentPage from "./pages/DeploymentPage";

export function useWorkspace() {
  const p = useParams();
  return {
    projectId: p.projectId!,
    workspaceId: p.workspaceId!,
    base: `/projects/${p.projectId}/workspaces/${p.workspaceId}`,
  };
}
function Shell() {
  const { projectId, workspaceId, base } = useWorkspace();
  const location = useLocation();
  const directory = useRead<Envelope<Directory>>("/projects");
  const project = directory.data?.data.items.find(
    (p) => p.project_id === projectId,
  );
  const workspace = project?.workspaces.find(
    (w) => w.workspace_id === workspaceId,
  );
  useEffect(() => {
    const key = `scroll:${location.key}`;
    const y = Number(sessionStorage.getItem(key) ?? 0);
    window.scrollTo(0, y);
    const save = () => sessionStorage.setItem(key, String(window.scrollY));
    window.addEventListener("scroll", save);
    return () => window.removeEventListener("scroll", save);
  }, [location.key]);
  if (directory.data && !workspace)
    return (
      <main className="landing">
        <Link to="/">返回项目目录</Link>
        <EmptyState title="项目与工作区不匹配" />
      </main>
    );
  return (
    <>
      <FixtureBanner />
      <div className="shell">
        <aside>
          <Link className="logo" to="/">
            Saga<span>Context</span>
            <i>记忆运行控制台</i>
          </Link>
          <p className="nav-label">项目与工作区</p>
          <ReadNotice error={directory.error} />
          {directory.data?.data.items.map((p) => (
            <div className="project-nav" key={p.project_id}>
              <b>{p.name}</b>
              {p.workspaces.map((w) => (
                <NavLink
                  key={w.workspace_id}
                  to={`/projects/${p.project_id}/workspaces/${w.workspace_id}`}
                >
                  ◇ {w.name}
                </NavLink>
              ))}
            </div>
          ))}
          <NavLink className="deployment-nav" to="/deployment">
            部署状态
          </NavLink>
          <div className="aside-foot">
            <span className="dot" />
            本地运行视图
            <br />
            一个工作区，一条可追溯的记忆链。
          </div>
        </aside>
        <main>
          <div className="crumb">
            <span>
              {project?.name ?? "项目"} <b>/</b> {workspace?.name ?? "工作区"}
            </span>
            <span className="small">只读控制台</span>
          </div>
          <nav className="tabs">
            {[
              ["", "总览"],
              ["tasks", "任务"],
              ["sessions", "会话"],
              ["memories", "记忆与证据"],
              ["batches", "运行与审核"],
            ].map(([path, name]) => (
              <NavLink
                key={path}
                end={path === ""}
                to={`${base}${path ? "/" + path : ""}`}
              >
                {name}
              </NavLink>
            ))}
          </nav>
          <Routes>
            <Route index element={<OverviewPage />} />
            <Route path=":resource" element={<ResourcePage />} />
            <Route path=":resource/:objectId" element={<DetailPage />} />
          </Routes>
        </main>
      </div>
    </>
  );
}
function FixtureBanner() {
  return document.querySelector('meta[name="console-fixture"]') ? (
    <div className="fixture-banner">演示环境 · 使用临时合成数据</div>
  ) : null;
}
function Landing() {
  const result = useRead<Envelope<Directory>>("/projects");
  const p = result.data?.data.items.find((p) => p.workspaces.length);
  if (p)
    return (
      <Navigate
        replace
        to={`/projects/${p.project_id}/workspaces/${p.workspaces[0].workspace_id}`}
      />
    );
  return (
    <main className="landing">
      <Link className="logo" to="/">
        Saga<span>Context</span>
      </Link>
      <ReadNotice error={result.error} />
      <EmptyState
        title={result.isLoading ? "正在读取工作区" : "暂无注册工作区"}
      >
        在本地注册项目后，可查看会话、记忆与运行状态。
      </EmptyState>
      <Link className="button" to="/deployment">
        查看部署状态
      </Link>
    </main>
  );
}
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/deployment" element={<DeploymentPage />} />
      <Route
        path="/projects/:projectId/workspaces/:workspaceId/*"
        element={<Shell />}
      />
      <Route path="*" element={<EmptyState title="页面不存在" />} />
    </Routes>
  );
}
