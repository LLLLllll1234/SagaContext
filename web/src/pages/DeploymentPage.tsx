import { Link } from "react-router-dom";
import { useRead } from "../api/queries";
import type { DeploymentStatus } from "../api/types";
import { EmptyState, ReadNotice, Status } from "../components/Common";

const configured = (value: boolean) => (value ? "已配置" : "未配置");

export default function DeploymentPage() {
  const result = useRead<DeploymentStatus>("/deployment");
  const deployment = result.data;

  return (
    <main className="deployment-page">
      <Link className="logo" to="/">
        Saga<span>Context</span>
        <i>记忆运行控制台</i>
      </Link>
      <div className="title-row">
        <div>
          <p className="eyebrow">DEPLOYMENT</p>
          <h1>部署状态</h1>
          <p className="muted">仅展示本地配置，不检查外部依赖是否可达。</p>
        </div>
        <Link className="button" to="/">
          返回项目目录
        </Link>
      </div>
      <ReadNotice error={result.error} />
      {!deployment ? (
        <EmptyState
          title={result.isLoading ? "正在读取部署状态" : "部署状态暂不可用"}
        >
          本地服务恢复后可重新读取。
        </EmptyState>
      ) : (
        <>
          <section className="deployment-summary" aria-label="部署标识">
            <div>
              <span className="eyebrow">PRODUCT</span>
              <h2>{deployment.product}</h2>
              <p>版本 {deployment.version}</p>
            </div>
            <div>
              <span className="eyebrow">INSTANCE</span>
              <p className="mono">{deployment.instance_id}</p>
            </div>
            <div>
              <span className="eyebrow">MODE</span>
              <Status value={deployment.rollout_mode} />
            </div>
          </section>
          <section className="deployment-grid" aria-label="部署配置">
            <article>
              <h2>运行控制</h2>
              <dl>
                <div>
                  <dt>后台工作器</dt>
                  <dd>{configured(deployment.worker_enabled)}</dd>
                </div>
                <div>
                  <dt>调度器</dt>
                  <dd>
                    <Status value={deployment.scheduler} />
                  </dd>
                </div>
                <div>
                  <dt>停止开关</dt>
                  <dd>{deployment.stop_active ? "已生效" : "未生效"}</dd>
                </div>
              </dl>
            </article>
            <article>
              <h2>依赖配置</h2>
              <dl>
                <div>
                  <dt>OpenViking</dt>
                  <dd>{configured(deployment.openviking_configured)}</dd>
                </div>
                <div>
                  <dt>语言模型</dt>
                  <dd>{configured(deployment.llm_configured)}</dd>
                </div>
                <div>
                  <dt>控制台资源</dt>
                  <dd>
                    {deployment.console_assets_available ? "已打包" : "缺失"}
                  </dd>
                </div>
              </dl>
            </article>
          </section>
        </>
      )}
    </main>
  );
}
