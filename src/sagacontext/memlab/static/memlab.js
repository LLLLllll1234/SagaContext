/* SagaContext Memory Lab — vanilla JS, no build step. */
(() => {
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN'));
const short = (s, n = 8) => (s ? String(s).slice(0, n) : '—');
const when = ts => { if (!ts) return '—'; const d = new Date(ts); return isNaN(d) ? String(ts).slice(0, 19) : d.toLocaleString('zh-CN', {hour12: false}); };
const day = ts => { if (!ts) return '—'; const d = new Date(ts); return isNaN(d) ? String(ts).slice(0, 10) : d.toLocaleDateString('zh-CN'); };
const EV = {source: '<span class="ev ev-source">源码事实</span>', local_data: '<span class="ev ev-local">本地数据</span>', inference: '<span class="ev ev-infer">系统推断</span>'};
const ev = k => EV[k] || '';

const state = {product: 'codex', tab: 'overview', cache: {}, sel: {}, timers: []};
const main = $('#main');

async function api(path) {
  if (state.cache[path]) return state.cache[path];
  const r = await fetch('/memlab/api' + path);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return (state.cache[path] = await r.json());
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); setTimeout(() => t.classList.add('hidden'), 2600); }
function drawer(title, html) { $('#drawer-title').innerHTML = title; $('#drawer-body').innerHTML = html; $('#drawer').classList.remove('hidden'); }
$('#drawer-close').onclick = () => $('#drawer').classList.add('hidden');
function stopTimers() { state.timers.forEach(clearTimeout); state.timers = []; }
const later = (fn, ms) => state.timers.push(setTimeout(fn, ms));

// ---------------------------------------------------------------- routing
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; render(); });
document.querySelectorAll('#products button').forEach(b => b.onclick = () => { state.product = b.dataset.product; render(); });

async function render() {
  stopTimers();
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === state.tab));
  document.querySelectorAll('#products button').forEach(b => b.classList.toggle('active', b.dataset.product === state.product));
  main.innerHTML = '<section class="panel"><p class="muted">正在读取本地数据…</p></section>';
  try {
    const fn = views[state.product][state.tab] || views.fallback;
    main.innerHTML = '';
    await fn(main);
  } catch (e) { main.innerHTML = `<section class="panel"><div class="callout warn">读取失败：${esc(e.message)}</div></section>`; }
}
const views = {codex: {}, claude: {}, fallback: el => { el.innerHTML = '<section class="panel"><p class="muted">该产品暂无此页面。</p></section>'; }};

// ================================================================ CODEX · 总览
views.codex.overview = async el => {
  const o = await api('/overview');
  const lc = await api('/codex/lifecycles');
  const c = o.codex, h = c.home, cfg = h.config || {};
  const src = c.source;
  el.innerHTML = `
  <section class="panel">
    <h1>Codex 是怎么“记住”你的？</h1>
    <p class="muted">下面所有内容来自你本机：<code>${esc(h.home)}</code>（数据）与 <code>${esc(src.path)}</code>（源码）。每个结论都标注证据等级。</p>
    <div class="grid c4" style="margin-top:14px">
      <div class="stat"><div class="num">${lc.counts.memory_blocks}</div><div class="lbl">长期记忆资产（MEMORY.md 任务组）${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${lc.counts.stage1}</div><div class="lbl">阶段一候选（stage1_outputs 行）${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${lc.counts.threads}</div><div class="lbl">本地会话线程（state_5.sqlite）${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${lc.counts.citations}</div><div class="lbl">模型引用记忆的次数（&lt;oai-mem-citation&gt;）${ev('local_data')}</div></div>
    </div>
    <div class="grid c3" style="margin-top:14px">
      <div class="stat"><div class="num mono" style="font-size:18px">${esc(h.cli_version || '未知')}</div><div class="lbl">最近一次会话使用的 Codex 版本 ${ev('local_data')}</div></div>
      <div class="stat"><div class="num mono" style="font-size:18px">${esc(short(src.revision, 12))}</div><div class="lbl">本地源码 commit · ${esc(day(src.commit_date))} · ${esc(src.subject || '')} ${ev('source')}</div></div>
      <div class="stat"><div class="num" style="font-size:18px">${cfg.memories ? Object.entries(cfg.memories).map(([k,v]) => `<span class="pill ${v===true?'ok':v===false?'bad':''}">${esc(k)}=${esc(String(v))}</span>`).join(' ') : '<span class="pill">未配置 [memories]</span>'}</div><div class="lbl">config.toml 中的记忆开关 · features.memories=${esc(String(cfg['features.memories']))} ${ev('local_data')}</div></div>
    </div>
    ${c.health.stage1_errors ? `<div class="callout warn" style="margin-top:14px">${ev('local_data')} 记忆抽取任务最近 ${c.health.stage1_errors} 次失败（共 ${c.health.stage1_jobs} 次，最后一次 ${when(c.health.last_error_at)}，最后一次成功 ${when(c.health.last_success_at)}）。错误：<code>${esc((c.health.last_error || '').slice(0, 220))}</code><br>${ev('inference')} 若错误提到模型不受支持，说明你的自定义 provider 没有源码默认的抽取模型；可在 config.toml 的 <code>[memories]</code> 里设置 <code>extract_model</code> / <code>consolidation_model</code>。</div>` : ''}
    <div class="callout infer" style="margin-top:14px">${ev('inference')} 源码是 ${esc(short(src.revision, 8))}（${esc(day(src.commit_date))}），本地数据由 ${esc(h.cli_version || '未知版本')} 写出。二者不一定同一版本：源码页里每条事实都会在本地源码里重新定位锚点；本地数据结构若与源码不一致会在对应页面标出。</div>
  </section>

  <section class="panel">
    <h2>记忆是怎样形成、被用、再被取代的</h2>
    <p class="muted">点击“播放”看一遍完整链路。每个节点下的数字是你本机上的真实数量。</p>
    <div class="flow" id="flow">
      ${flowNode('session', '① 会话 rollout.jsonl', '每轮对话追加写入', lc.counts.threads, 'local_data')}
      ${flowNode('candidate', '② 阶段一抽取', 'gpt-5.6-luna 读过滤后的对话 → raw_memory + rollout_summary', lc.counts.stage1, 'source')}
      ${flowNode('selected', '③ 阶段二整合', '沙箱 agent 在 ~/.codex/memories 里读 git diff 改文件', h.rollout_summaries, 'source')}
      ${flowNode('asset', '④ 记忆资产', 'MEMORY.md 任务组 + memory_summary.md', lc.counts.memory_blocks, 'local_data')}
      ${flowNode('recall', '⑤ 召回', 'memory_summary 截断到 2500 tokens 注入 developer 指令', lc.counts.citations, 'source')}
      ${flowNode('forget', '⑥ 取代/遗忘', '30 天未引用 → 证据文件删除 → agent 移除', lc.counts.forgotten, 'inference')}
    </div>
    <div class="row"><button class="primary" id="play-flow">▶ 播放形成过程</button><span class="muted" id="flow-caption">按源码顺序演示一次：触发 → 抽取 → 整合 → 注入 → 引用 → 遗忘。</span></div>
  </section>

`;
  $('#play-flow').onclick = () => playFlow(lc);
};
function flowNode(id, t, s, n, evk) { return `<div class="node" data-node="${id}"><div class="t">${esc(t)}</div><div class="s">${esc(s)}</div><div class="n">${fmt(n)}</div><div>${ev(evk)}</div></div>`; }
function playFlow(lc) {
  stopTimers();
  const nodes = [...document.querySelectorAll('#flow .node')];
  const cap = $('#flow-caption');
  const steps = [
    ['session', '你在 Codex 里开始新的一轮 → app-server 的 turn_start 触发 start_memories_startup_task（不是会话结束时）。'],
    ['candidate', `挑最多 2 个闲置超过 6 小时、10 天内活跃的别的会话，过滤掉 developer 消息与压缩摘要，交给 gpt-5.6-luna 抽取 → 写入 stage1_outputs（本机 ${lc.counts.stage1} 行）。`],
    ['selected', `把 ≤256 条 30 天内用过的候选同步成 rollout_summaries/*.md，git diff 后启动沙箱 agent（gpt-5.6-terra）读 diff 更新文件（本机 ${lc.counts.summary_files} 个证据文件）。`],
    ['asset', `agent 重写 MEMORY.md（本机 ${lc.counts.memory_blocks} 个任务组）与 memory_summary.md；Rust 只校验文件存在、首行 v1。`],
    ['recall', `下个会话开始：memory_summary.md 截到 2500 tokens 塞进 developer 指令；模型引用时输出 <oai-mem-citation>，对应线程 usage_count+1（本机 ${lc.counts.citations} 次引用）。`],
    ['forget', `30 天没被引用的候选不再入选 → 它的证据文件被删除 → agent 在 diff 里看到删除，按提示词移除“只被它支撑的记忆”（本机当前 ${lc.counts.forgotten} 条处于该状态）。`],
  ];
  nodes.forEach(n => n.classList.add('dim'));
  steps.forEach(([id, text], i) => later(() => {
    nodes.forEach(n => { n.classList.remove('lit'); n.classList.add('dim'); });
    const n = nodes.find(x => x.dataset.node === id); n.classList.remove('dim'); n.classList.add('lit');
    cap.textContent = text;
  }, i * 2600));
  later(() => nodes.forEach(n => n.classList.remove('dim', 'lit')), steps.length * 2600 + 1500);
}

// ================================================================ CODEX · 记忆资产
views.codex.assets = async el => {
  const a = await api('/codex/assets');
  const lc = await api('/codex/lifecycles');
  const lifeByThread = new Map(lc.lifecycles.map(x => [x.thread_id, x]));
  const sum = a.summary;
  const limit = 2500;
  el.innerHTML = `
  <section class="panel">
    <div class="row between"><h2 style="margin:0">真正注入每个新会话的：memory_summary.md</h2><span>${ev('local_data')} ${ev('source')}</span></div>
    <p class="muted">源码把这个文件截断到 ${fmt(limit)} tokens 后放进 developer 指令（MEMORY_SUMMARY BEGINS … ENDS）。本机文件约 ${fmt(sum.approx_tokens)} tokens（中文按字、英文按 4 字符/token 粗估）${sum.approx_tokens > limit ? '，<b style="color:var(--danger)">可能已超出，尾部会被截掉</b>' : '，未超出'}。首行 <code>${esc(sum.version_line)}</code> · 更新于 ${when(sum.modified)}。</p>
    <div class="window" style="margin:8px 0 12px"><div class="fill" style="width:${Math.min(100, sum.approx_tokens / limit * 100)}%"></div><div class="thr" style="left:${Math.min(100, limit / Math.max(limit, sum.approx_tokens) * 100)}%"></div><div class="lbl">${fmt(sum.approx_tokens)} / ${fmt(limit)} tokens</div></div>
    <div class="grid c2">${(sum.sections || []).map(s => `<div class="item"><b>${esc(s.title)}</b> <small>第 ${s.line_start} 行起 · ${s.items.length} 条</small><ul style="margin:6px 0 0;padding-left:18px">${s.items.slice(0, 6).map(i => `<li class="muted" style="font-size:13px">${esc(i.replace(/^[-*]\s*/, '')).slice(0, 180)}</li>`).join('')}${s.items.length > 6 ? `<li class="muted">… 还有 ${s.items.length - 6} 条</li>` : ''}</ul></div>`).join('')}</div>
    <button class="secondary raw-open" data-raw-title="memory_summary.md 原文" data-raw-text="${esc(sum.text).replace(/"/g,'&quot;')}" style="margin-top:10px">查看 memory_summary.md 原文</button>
  </section>

  <section class="panel">
    <div class="row between"><h2 style="margin:0">登记册 MEMORY.md：${a.assets.length} 个任务组</h2><span>${ev('local_data')}</span></div>
    <p class="muted">模型被要求“先搜 MEMORY.md，再最多打开 1-2 个证据文件”。每个任务组下方列出它来自哪些会话、被后续会话引用了几次。点击查看详情与溯源。</p>
    <div class="asset-grid" id="asset-list">${a.assets.map(x => { const ls=x.threads.map(t=>lifeByThread.get(t)).filter(Boolean); const active=ls.some(l=>l.stages.some(s=>s.stage==='recall'&&s.status==='present')); const stale=ls.some(l=>l.stages.some(s=>s.stage==='forget'&&s.status==='present')); return `<div class="item clickable asset-card" data-i="${x.index}"><div class="row between"><b>${esc(x.title.replace(/^Task Group:\s*/, ''))}</b><span class="pill ${stale?'bad':active?'ok':'warn'}">${stale?'待取代':active?'正在使用':'已写入'}</span></div><div class="desc">${esc((x.sections['User preferences'] || x.sections['Reusable knowledge'] || [])[0] || '暂无摘要')}</div><div class="asset-meta"><span>${x.threads.length} 个来源会话</span><span>引用 ${x.recall_count} 次</span><span>MEMORY.md ${x.line_start}-${x.line_end} 行</span></div><div class="rail">${['形成','写入','使用','更新/取代'].map((s,i)=>`<span class="st ${i<2|| (i===2&&active) ? 'present': stale&&i===3?'dropped':'unknown'}">${s}</span>`).join('<span class="sep">→</span>')}</div></div>`; }).join('')}</div>
  </section>

  <section class="panel">
    <h2>其他资产</h2>
    <div class="grid c3">
      <div class="item"><b>skills/</b><div class="desc">整合 agent 可选生成的技能目录</div>${a.skills.length ? a.skills.map(s => `<div><code>${esc(s)}</code></div>`).join('') : '<p class="muted">无</p>'}</div>
      <div class="item"><b>extensions/ad_hoc/notes/ · ${a.ad_hoc_notes.length} 条</b><div class="desc">你明确说“记住/改掉”时模型写的便签；下次整合被视为权威信息（非指令），并打 [ad-hoc note] 标签</div>${a.ad_hoc_notes.map((n, i) => `<div><a href="#" data-note="${i}">${esc(n.file)}</a></div>`).join('')}</div>
      <div class="item"><b>整合任务记录 · ${a.consolidations.length} 次</b><div class="desc">memory_consolidate_global 任务（jobs 表）</div>${a.consolidations.slice(0, 5).map(j => `<div><span class="pill ${j.status === 'done' ? 'ok' : 'bad'}">${esc(j.status)}</span> ${when(j.finished_at || j.started_at)}${j.duration_s != null ? ` · ${j.duration_s}s` : ''}</div>`).join('')}${a.git_log.length ? `<div class="muted" style="margin-top:6px">memories/.git 基线：${esc(a.git_log[0].subject)} · ${day(a.git_log[0].date)}（每次整合成功后重置，所以只剩一条）</div>` : ''}</div>
    </div>
  </section>`;
  el.querySelectorAll('#asset-list .item').forEach(n => n.onclick = () => showAsset(a.assets[+n.dataset.i]));
  el.querySelectorAll('[data-note]').forEach(n => n.onclick = e => { e.preventDefault(); const x = a.ad_hoc_notes[+n.dataset.note]; drawer(esc(x.file), `<pre>${esc(x.text)}</pre>`); });
};
function showAsset(x) {
  const secs = Object.entries(x.sections).filter(([k, v]) => v.length && !/^Task \d/.test(k));
  drawer(`${esc(x.title.replace(/^Task Group:\s*/, ''))} ${ev('local_data')}`, `
    <p class="muted">MEMORY.md 第 ${x.line_start}-${x.line_end} 行 · ${x.threads.length} 个来源会话 · 被后续会话引用 ${x.recall_count} 次</p>
    <div class="callout">${ev('inference')} 生命周期：形成 → 写入 → 使用 → 更新/取代。下方每个来源会话都保留可定位证据。</div>
    ${secs.map(([k, v]) => `<h3>${esc(k)}</h3><div class="memory-facts">${v.map((i, n) => `<div class="fact"><div class="row between"><b>信息 ${n + 1}</b>${ev('local_data')}</div><div style="margin-top:5px">${esc(i)}</div><div class="muted" style="margin-top:5px">这条信息当前属于 MEMORY.md 的“${esc(k)}”分组；下方生命周期会展示它如何从会话候选变成可召回资产。</div></div>`).join('')}</div>`).join('')}
    <h3>来源会话（溯源）</h3>
    <table><tr><th>会话</th><th>时间</th><th>候选</th><th>证据文件</th><th></th></tr>${x.provenance.map(p => `<tr><td><b>${esc(p.title || p.thread_id)}</b><br><small class="mono">${esc(p.thread_id)}</small><br><small>${esc(p.cwd || '')}</small></td><td>${day(p.updated_at)}</td><td>${p.in_stage1 ? `<span class="pill ok">有 · 引用 ${p.usage_count}</span>` : '<span class="pill bad">已清理</span>'}</td><td>${p.summary_file ? `<span class="pill ok">有</span>` : '<span class="pill bad">无</span>'}</td><td><button data-life="${p.thread_id}">形成过程</button> ${p.rollout_exists ? `<button data-roll="${p.thread_id}">原始会话</button>` : ''}</td></tr>`).join('')}</table>
    <h3>被哪些会话引用过</h3>
    ${x.recalls.length ? `<table><tr><th>引用它的会话</th><th>时间</th><th>引用条目</th></tr>${x.recalls.map(r => `<tr><td class="mono">${esc(short(r.citing_thread, 13))}…</td><td>${when(r.timestamp)}</td><td>${r.entries.map(e => `<div><code>${esc(e.file)}:${e.start}-${e.end}</code> <small>${esc(e.note)}</small></div>`).join('')}</td></tr>`).join('')}</table>` : '<p class="muted">rollout 中没有找到引用该任务组来源线程的 &lt;oai-mem-citation&gt;。</p>'}
    <div class="callout infer" style="margin-top:12px">${ev('inference')} “被引用”按 &lt;rollout_ids&gt; 里的线程 id 匹配。citation_entries 里的 MEMORY.md 行号指向引用当时的文件版本，MEMORY.md 每次整合都会被重写，所以行号不用于匹配。</div>
    <button class="secondary raw-open" data-raw-title="${esc(x.title.replace(/^Task Group:\s*/, ''))} · MEMORY.md 原文" data-raw-text="${esc(x.text).replace(/"/g,'&quot;')}">查看 MEMORY.md 原文</button>`);
  $('#drawer-body').querySelectorAll('[data-life]').forEach(b => b.onclick = () => { state.sel.life = b.dataset.life; state.tab = 'pipeline'; $('#drawer').classList.add('hidden'); render(); });
  $('#drawer-body').querySelectorAll('[data-roll]').forEach(b => b.onclick = () => { state.sel.thread = b.dataset.roll; state.tab = 'context'; $('#drawer').classList.add('hidden'); render(); });
}
document.addEventListener('click', e => {
  const b = e.target.closest('.raw-open'); if (!b) return;
  drawer(esc(b.dataset.rawTitle), `<div class="callout source">${ev('local_data')} 以下内容直接读取本地记忆文件，未经过摘要或重排。</div><pre class="raw-doc">${esc(b.dataset.rawText || '')}</pre>`);
});

// ================================================================ CODEX · 形成过程
views.codex.pipeline = async el => {
  const lc = await api('/codex/lifecycles');
  const s1 = await api('/codex/stage1');
  const list = lc.lifecycles;
  const sel = state.sel.life && list.find(l => l.thread_id === state.sel.life) ? state.sel.life : (list[0] || {}).thread_id;
  el.innerHTML = `
  <section class="panel tight">
    <div class="row between"><h2 style="margin:0">从一次会话到一条记忆：逐会话回放</h2><span>${ev('local_data')} ${ev('inference')}</span></div>
    <p class="muted">左侧是所有在记忆系统里留下痕迹的会话（${list.length} 个）。选一个，右侧按源码顺序回放它经过的每一站，并附上每一站的本地证据。</p>
  </section>
  <div class="twocol">
    <div class="list" id="life-list" style="max-height:900px;overflow:auto">${list.map(l => `<div class="item clickable ${l.thread_id === sel ? 'active' : ''}" data-id="${l.thread_id}"><b>${esc(l.title || l.thread_id).slice(0, 70)}</b><div class="desc">${esc(l.cwd || '')}</div><div class="rail" style="margin-top:6px">${l.stages.map(s => `<span class="st ${s.status}" title="${esc(s.label)}">${esc(s.label.split('（')[0].replace(/ .*$/, ''))}</span>`).join('')}</div></div>`).join('')}</div>
    <div id="life-detail"></div>
  </div>
  <section class="panel" style="margin-top:18px">
    <h2>后台任务台账（memories_1.sqlite / jobs）${ev('local_data')}</h2>
    <p class="muted">没有锁文件：靠 jobs 表的租约（1 小时）与 ownership_token 互斥。worker_id 是“顺手”干活的那个会话。</p>
    <table><tr><th>类型</th><th>对象</th><th>状态</th><th>开始</th><th>耗时</th><th>触发它的会话</th><th>错误</th></tr>${s1.jobs.slice(0, 40).map(j => `<tr><td>${esc(j.kind.replace('memory_', ''))}</td><td class="mono">${esc(short(j.job_key, 13))}</td><td><span class="pill ${j.status === 'done' ? 'ok' : j.status === 'error' ? 'bad' : 'warn'}">${esc(j.status)}</span></td><td>${when(j.started_at)}</td><td>${j.duration_s != null ? j.duration_s + 's' : '—'}</td><td class="mono">${esc(short(j.worker_id, 13))}</td><td><small>${esc(j.last_error || '')}</small></td></tr>`).join('')}</table>
  </section>`;
  el.querySelectorAll('#life-list .item').forEach(n => n.onclick = () => { state.sel.life = n.dataset.id; el.querySelectorAll('#life-list .item').forEach(x => x.classList.toggle('active', x === n)); showLife(list.find(l => l.thread_id === n.dataset.id), s1); });
  if (sel) showLife(list.find(l => l.thread_id === sel), s1);
};
function showLife(l, s1) {
  const box = $('#life-detail');
  const row = s1.rows.find(r => r.thread_id === l.thread_id);
  const icons = {session: '会', candidate: '抽', selected: '选', summary_file: '证', asset: '资', recall: '引', forget: '忘'};
  box.innerHTML = `
  <section class="panel">
    <div class="row between"><h2 style="margin:0">${esc(l.title || l.thread_id)}</h2><button class="primary" id="play-life">▶ 回放</button></div>
    <p class="muted mono">${esc(l.thread_id)} · ${esc(l.cwd || '')}</p>
    <div id="life-steps">${l.stages.map((s, i) => `<div class="step ${s.status}" data-step="${i}"><div class="dot">${icons[s.stage] || i + 1}</div><div><div class="row between"><span class="lbl">${esc(s.label)}</span><span>${s.at ? `<small>${when(s.at)}</small> ` : ''}${ev(s.evidence)}</span></div><div class="note">${esc(s.note)}</div>${stepExtra(s, row, l)}</div></div>${i < l.stages.length - 1 ? transitionExtra(s.stage, l.stages[i+1].stage, row) : ''}`).join('')}</div>
  </section>`;
  $('#play-life').onclick = () => {
    stopTimers();
    const steps = [...box.querySelectorAll('.step')];
    steps.forEach(s => s.style.opacity = .25);
    steps.forEach((s, i) => later(() => { steps.forEach(x => x.style.transform = ''); s.style.opacity = 1; s.style.transform = 'translateX(4px)'; s.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }, i * 1400));
    later(() => steps.forEach(s => { s.style.opacity = 1; s.style.transform = ''; }), l.stages.length * 1400 + 600);
  };
  box.querySelectorAll('[data-roll]').forEach(b => b.onclick = () => { state.sel.thread = b.dataset.roll; state.tab = 'context'; render(); });
}
function stepExtra(s, row, l) {
  if (s.stage === 'session') return `<div style="margin-top:6px"><button data-roll="${l.thread_id}">查看这次会话的上下文与压缩</button></div>`;
  if (s.stage === 'candidate' && row) return `<details style="margin-top:6px"><summary>阶段一产出：raw_memory（${fmt(row.raw_memory_chars)} 字） · rollout_summary（${fmt(row.rollout_summary_chars)} 字）${s.job ? ` · 抽取任务 ${esc(s.job.status)} 耗时 ${s.job.duration_s}s，由会话 ${esc(short(s.job.worker_id, 8))} 触发` : ''}</summary><h3>raw_memory（模型抽取的偏好/知识/失败教训）</h3><pre>${esc(row.raw_memory)}</pre><h3>rollout_summary（会话叙事摘要）</h3><pre>${esc(row.rollout_summary)}</pre></details>`;
  if (s.stage === 'summary_file' && s.file) return `<div style="margin-top:4px"><a href="#" data-sumfile="${esc(s.file)}" class="mono">${esc(s.file)}</a></div>`;
  if (s.stage === 'asset' && s.blocks && s.blocks.length) return `<div style="margin-top:4px">${s.blocks.map(b => `<div><span class="pill brand">MEMORY.md 第 ${b.line_start}-${b.line_end} 行</span> ${esc(b.title.replace(/^Task Group:\s*/, ''))}</div>`).join('')}</div>`;
  if (s.stage === 'recall' && s.uses && s.uses.length) return `<details style="margin-top:4px"><summary>${s.uses.length} 次引用明细</summary><table><tr><th>引用它的会话</th><th>时间</th><th>引用条目</th></tr>${s.uses.map(u => `<tr><td class="mono">${esc(short(u.citing_thread, 13))}…</td><td>${when(u.timestamp)}</td><td>${u.entries.map(e => `<div><code>${esc(e.file)}:${e.start}-${e.end}</code> <small>${esc(e.note)}</small></div>`).join('')}</td></tr>`).join('')}</table></details>`;
  return '';
}
function transitionExtra(from, to, row) {
  const key = `${from}->${to}`;
  const map = {
    'session->candidate': ['触发：下一轮开始时，后台挑选其他闲置会话', '传递：过滤后的用户/模型对话', '过滤：去掉 developer 指令与压缩摘要；最多挑 2 个会话，再交给 gpt-5.6-luna。', '解释：当前会话不会抽自己，而是借下一轮启动的机会整理“别的会话”。'],
    'candidate->selected': ['触发：阶段一写入 stage1_outputs 后进入整合窗口', '传递：raw_memory + rollout_summary + usage_count', '过滤：只选 30 天内、被使用过且不超过 256 条的候选。', '解释：候选先进入待审核队列，只有满足保留条件才会送给阶段二。'],
    'selected->summary_file': ['触发：阶段二整合任务启动', '传递：候选序列化为 rollout_summaries/*.md', '过滤：每个文件保留会话叙事和可追溯来源，作为 agent 的工作材料。', '解释：这一步把数据库里的候选变成 agent 能读、能 diff 的证据文件。'],
    'summary_file->asset': ['触发：沙箱 agent 读取证据文件 diff', '传递：更新后的 MEMORY.md / memory_summary.md', '过滤：agent 按提示词合并、改写或移除任务组；Rust 只校验 v1 和文件存在。', '解释：长期记忆不是数据库自动拼接，而是整合 agent 根据证据重写文件。'],
    'asset->recall': ['触发：新会话启动', '传递：memory_summary.md 前 2500 tokens 注入 developer 指令', '过滤：超出 2500 tokens 的尾部不会进入上下文；模型可按需再搜 MEMORY.md。', '解释：写入文件不会立刻等于“模型记住”，必须在下一次会话启动时被注入。'],
    'recall->forget': ['触发：长期未被引用或来源证据被清理', '传递：usage_count、引用线程和删除 diff', '过滤：30 天未使用的候选会被 prune；只由它支撑的资产可能在下一次整合中被移除。', '解释：遗忘是下一轮整合根据证据缺失做出的结果，不是一次即时删除。'],
  };
  const x = map[key]; if (!x) return '';
  return `<div class="transition"><div class="transition-arrow">↓</div><div class="transition-body"><b>${esc(x[0])}</b><div><span class="transition-label">${esc(x[1])}</span></div><div><span class="transition-label">${esc(x[2])}</span></div><div class="transition-explain">${ev('inference')} ${esc(x[3])}</div></div></div>`;
}
document.addEventListener('click', async e => {
  const a = e.target.closest('[data-sumfile]'); if (!a) return; e.preventDefault();
  const d = await api('/codex/summaries'); const f = d.files.find(x => x.file === a.dataset.sumfile);
  if (f) drawer(`${esc(f.title)} ${ev('local_data')}`, `<p class="muted mono">rollout_summaries/${esc(f.file)}</p><pre>${esc(f.text)}</pre>`);
});

// ================================================================ CODEX · 短期上下文
views.codex.context = async el => {
  const th = (await api('/codex/threads?limit=400')).threads.filter(t => t.rollout_exists);
  const sel = state.sel.thread && th.find(t => t.id === state.sel.thread) ? state.sel.thread : ((th.find(t => t.compactions > 0) || th[0]) || {}).id;
  el.innerHTML = `
  <section class="panel tight">
    <div class="row between"><h2 style="margin:0">短期上下文：增长、压缩、什么留下了</h2><span>${ev('local_data')} ${ev('source')}</span></div>
    <div class="row" style="margin-top:8px"><label>选择会话</label><select id="thread-sel" style="max-width:640px">${th.map(t => `<option value="${t.id}" ${t.id === sel ? 'selected' : ''}>${esc(day(t.updated_at))} · ${esc((t.title || t.id).replace(/\s+/g, ' ').slice(0, 70))} · ${fmt(t.tokens_used)} tokens${t.compactions ? ` · ⚠ 压缩 ${t.compactions} 次` : ''}${t.stage1 ? ' · 已抽记忆' : ''}${t.archived ? ' · 已归档' : ''}</option>`).join('')}</select><span class="muted">${th.length} 个可读 rollout，其中 ${th.filter(t => t.compactions).length} 个发生过压缩（默认选中最近一个）</span></div>
  </section>
  <div id="ctx-body"></div>`;
  $('#thread-sel').onchange = e => { state.sel.thread = e.target.value; loadContext(e.target.value); };
  if (sel) loadContext(sel);
};
async function loadContext(id) {
  const box = $('#ctx-body');
  box.innerHTML = '<section class="panel"><p class="muted">解析 rollout…</p></section>';
  let r; try { r = await api('/codex/rollout/' + id); } catch (e) { box.innerHTML = `<section class="panel"><div class="callout warn">${esc(e.message)}</div></section>`; return; }
  renderContext(box, r, 'codex');
}
function renderContext(box, r, product) {
  const cv = r.curve, m = r.meta || {};
  const pts = cv.points, comps = cv.compactions;
  const feedEvents = r.events.filter(e => !['reasoning', 'task'].includes(e.kind));
  box.innerHTML = `
  <section class="panel">
    <div class="row between"><div><b>${esc(m.cwd || m.project || '')}</b> <small class="mono">${esc(r.thread_id || r.id)}</small></div><div>${m.cli_version ? `<span class="pill">${esc(m.originator || '')} ${esc(m.cli_version)}</span>` : ''}${m.git_branch ? ` <span class="pill">${esc(m.git_branch)}</span>` : ''}<span class="pill">${fmt(r.bytes)} 字节</span></div></div>
    <div class="grid c4" style="margin:12px 0">
      <div class="stat"><div class="num">${fmt(cv.window)}</div><div class="lbl">模型上下文窗口 ${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${fmt(cv.auto_compact_threshold)}</div><div class="lbl">自动压缩阈值 = 窗口 × 90% ${ev('source')}</div></div>
      <div class="stat"><div class="num">${fmt(cv.peak_context)}</div><div class="lbl">本会话上下文峰值 ${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${comps.length}</div><div class="lbl">压缩次数 ${ev('local_data')}</div></div>
    </div>
    <div class="chart" id="chart"></div>
    <div class="ctrl"><button class="primary" id="play-ctx">▶ 播放</button><button id="pause-ctx">⏸</button><input type="range" id="scrub" min="0" max="${feedEvents.length - 1}" value="0"><span class="muted" id="ctx-pos">0 / ${feedEvents.length}</span><select id="speed"><option value="140">慢</option><option value="60" selected>中</option><option value="18">快</option></select></div>
    <div class="event-filters"><span class="muted">筛选事件：</span>${[['session_meta','会话开始'],['memory_injection','注入记忆'],['developer','developer'],['harness','环境注入'],['turn','新一轮'],['user','用户'],['assistant','模型'],['tool_call','工具调用'],['tool_output','工具输出']].map(([k,n])=>`<button class="filter active" data-kind="${k}">${n}</button>`).join('')}<button class="filter-reset" id="filter-reset">全选</button></div>
    <div class="window" id="win"><div class="fill" style="width:0"></div>${cv.window ? `<div class="thr" style="left:${cv.auto_compact_threshold / cv.window * 100}%"></div>` : ''}<div class="lbl" id="win-lbl">0 tokens</div></div>
    <div class="grid c2" style="margin-top:12px">
      <div><h3 style="margin-top:0">事件流 <small class="muted">（${r.counts.tool_call || 0} 次工具调用 · ${r.counts.user || 0} 条用户消息 · ${r.counts.assistant || 0} 条回复）</small></h3><div class="feed" id="feed">${feedEvents.map((e, i) => `<div class="e" data-i="${i}"><small>#${e.ordinal}</small><span class="k k-${e.kind}">${esc(kindName(e.kind))}</span><span>${esc(e.summary)}</span></div>`).join('')}</div></div>
      <div><h3 style="margin-top:0">此刻发生了什么</h3><div id="narr" class="callout">按播放，随时间观察上下文如何增长、何时压缩、压缩后留下了什么。</div><div id="comp-detail"></div></div>
    </div>
    ${product === 'codex' ? `<div class="callout source" style="margin-top:12px">${ev('source')} 压缩后历史 = ≤20k tokens 的历史用户消息（新到旧） + 一条 user 角色摘要（前缀 “Another language model started to solve this problem…”）。assistant 回复、推理、工具调用与输出全部丢弃。<br>${ev('source')} 压缩摘要不会进入长期记忆：阶段一序列化时 Compacted 项被丢弃（memories/write/src/phase1.rs）。${r.memory_prompt_tokens ? `<br>${ev('local_data')} 本会话开始时注入的 MEMORY_SUMMARY 约 ${fmt(r.memory_prompt_tokens)} tokens（粗估，上限 2500）。` : ''}${r.citations.length ? `<br>${ev('local_data')} 本会话模型引用了记忆 ${r.citations.length} 次。` : ''}</div>` : `<div class="callout infer" style="margin-top:12px">${ev('inference')} ${esc(r.note || '')}</div>`}
  </section>`;
  drawChart($('#chart'), cv);
  // playback
  const fill = $('#win .fill'), lbl = $('#win-lbl'), feed = $('#feed'), narr = $('#narr'), scrub = $('#scrub'), pos = $('#ctx-pos'), win = $('#win');
  const filterKinds = new Set(['session_meta','memory_injection','developer','harness','turn','user','assistant','tool_call','tool_output']);
  function applyFilters() { feed.querySelectorAll('.e').forEach(row => { const e = feedEvents[+row.dataset.i]; row.style.display = filterKinds.has(e.kind) ? '' : 'none'; }); }
  document.querySelectorAll('.event-filters .filter').forEach(btn => btn.onclick = () => { const k=btn.dataset.kind; if (filterKinds.has(k)) { filterKinds.delete(k); btn.classList.remove('active'); } else { filterKinds.add(k); btn.classList.add('active'); } applyFilters(); });
  $('#filter-reset').onclick = () => { document.querySelectorAll('.event-filters .filter').forEach(btn => { filterKinds.add(btn.dataset.kind); btn.classList.add('active'); }); applyFilters(); };
  let ctx = 0, i = 0, playing = false, timer = null;
  const ptsByOrd = new Map(pts.map(p => [p.ordinal, p]));
  function setIdx(n) {
    i = Math.max(0, Math.min(feedEvents.length - 1, n)); scrub.value = i; pos.textContent = `${i + 1} / ${feedEvents.length}`;
    ctx = 0; for (let k = 0; k <= i; k++) { const p = ptsByOrd.get(feedEvents[k].ordinal); if (p) ctx = p.context; if (feedEvents[k].kind === 'compacted') ctx = 0; }
    const e = feedEvents[i];
    const pct = cv.window ? Math.min(100, ctx / cv.window * 100) : Math.min(100, ctx / Math.max(1, cv.peak_context) * 100);
    fill.style.width = pct + '%'; lbl.textContent = `${fmt(ctx)} tokens${cv.window ? ` · ${pct.toFixed(0)}%` : ''}`;
    win.classList.toggle('flash', cv.auto_compact_threshold && ctx >= cv.auto_compact_threshold * 0.97);
    feed.querySelectorAll('.e').forEach(x => x.classList.remove('now')); const row = feed.querySelector(`.e[data-i="${i}"]`); if (row) { row.classList.add('now'); row.scrollIntoView({block: 'nearest'}); }
    narrate(e, ctx);
    markChart(e.ordinal);
  }
  function narrate(e, ctx) {
    const map = {
      session_meta: '会话开始。系统提示、developer 指令、环境上下文进入窗口。',
      memory_injection: '长期记忆进入短期上下文：memory_summary.md（截到 2500 tokens）作为 developer 消息注入。这是“召回”的第一步，后续模型按需再搜 MEMORY.md。',
      harness: 'harness 注入的环境/权限说明，也占上下文。',
      turn: '新一轮开始。此时 app-server 会顺手为“别的”闲置会话触发记忆抽取（不是为这个会话）。',
      user: '你的消息追加进历史。压缩时用户消息是唯一会被保留的原文（≤20k tokens）。',
      assistant: e.detail && e.detail.cited_memory ? '模型回复并附带 <oai-mem-citation>：引用到的线程 usage_count+1，这决定它们在下次整合里的排序与是否被保留。' : '模型回复追加进历史。压缩时会被丢弃。',
      tool_call: '工具调用，占上下文。', tool_output: `工具输出 ${fmt(e.detail && e.detail.chars)} 字进入上下文，这是上下文膨胀的主因。`,
      token_count: cv.auto_compact_threshold && ctx >= cv.auto_compact_threshold ? `上下文 ${fmt(ctx)} tokens，已到达 90% 阈值——下一次采样前/后会触发压缩。` : `上下文 ${fmt(ctx)} tokens${cv.window ? `（窗口 ${fmt(cv.window)}，阈值 ${fmt(cv.auto_compact_threshold)}）` : ''}。`,
      compacted: '压缩发生。历史被替换为 replacement_history：只保留最近的用户消息与一条交接摘要。',
      summary_message: '压缩摘要以 user 角色消息进入新的历史。', developer: 'developer 指令。', agent_message: '子 agent 消息。', unparsed: '这一行无法解析（保留为未解析事件）。',
    };
    narr.className = 'callout' + (e.kind === 'compacted' ? ' warn' : e.kind === 'memory_injection' || (e.kind === 'assistant' && e.detail && e.detail.cited_memory) ? ' infer' : '');
    narr.innerHTML = `<b>${esc(kindName(e.kind))}</b> · ${esc(when(e.timestamp))}<br>${esc(map[e.kind] || '')}`;
    const cd = $('#comp-detail');
    if (e.kind === 'compacted') {
      const c = comps.find(x => x.ordinal === e.ordinal) || {};
      cd.innerHTML = `<div class="item" style="border-color:var(--danger)"><b>压缩 #${c.window_number ?? '?'}</b> <span class="pill bad">${fmt(c.context_before)} → ${fmt(c.context_after)} tokens</span><div class="muted" style="margin:6px 0">replacement_history 保留 ${(c.kept || []).length} 项：${(c.kept || []).map(k => `<code>${esc(k)}</code>`).join(' ')}</div>${c.kept_user_messages && c.kept_user_messages.length ? `<details open><summary>保留的用户消息（${c.kept_user_messages.length} 条）</summary>${c.kept_user_messages.map(t => `<pre style="max-height:90px">${esc(t)}</pre>`).join('')}</details>` : ''}<details><summary>交接摘要（${fmt(c.summary_chars)} 字）</summary><pre>${esc(c.summary_text || '')}</pre></details></div>`;
    }
  }
  function tick() { if (!playing) return; if (i >= feedEvents.length - 1) { playing = false; return; } setIdx(i + 1); const e = feedEvents[i]; timer = setTimeout(tick, e.kind === 'compacted' ? 2600 : e.kind === 'user' || e.kind === 'memory_injection' ? 1200 : +$('#speed').value); }
  $('#play-ctx').onclick = () => { if (playing) return; playing = true; if (i >= feedEvents.length - 1) setIdx(0); tick(); };
  $('#pause-ctx').onclick = () => { playing = false; clearTimeout(timer); };
  scrub.oninput = e => { playing = false; clearTimeout(timer); setIdx(+e.target.value); };
  state.stopCtx = () => { playing = false; clearTimeout(timer); };
  setIdx(0);
  applyFilters();
}
const kindName = k => ({session_meta: '会话开始', memory_injection: '注入记忆', harness: '环境注入', developer: 'developer', turn: '新一轮', user: '用户', assistant: '模型', reasoning: '推理', tool_call: '工具调用', tool_output: '工具输出', token_count: '计数', compacted: '⚠ 压缩', summary_message: '摘要入史', agent_message: '子agent', task: '任务', unparsed: '未解析'}[k] || k);

function drawChart(el, cv) {
  const pts = cv.points; if (!pts.length) { el.innerHTML = '<p class="muted" style="padding:20px">该会话没有 token_count 记录。</p>'; return; }
  const W = el.clientWidth || 900, H = 300, P = {l: 64, r: 16, t: 14, b: 28};
  const maxO = Math.max(...pts.map(p => p.ordinal), ...cv.compactions.map(c => c.ordinal), 1);
  const maxY = Math.max(cv.window || 0, cv.peak_context || 0, 1) * 1.04;
  const x = o => P.l + (o / maxO) * (W - P.l - P.r), y = v => P.t + (1 - v / maxY) * (H - P.t - P.b);
  let d = ''; pts.forEach((p, i) => { d += (i ? 'L' : 'M') + x(p.ordinal).toFixed(1) + ',' + y(p.context).toFixed(1); });
  const ticks = [0, .25, .5, .75, 1].map(f => Math.round(maxY * f / 1000) * 1000);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${ticks.map(v => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="#eef1f3"/><text x="${P.l - 8}" y="${y(v) + 4}" font-size="11" text-anchor="end" fill="#8a97a0">${v >= 1000 ? (v / 1000) + 'k' : v}</text>`).join('')}
    ${cv.window ? `<line x1="${P.l}" x2="${W - P.r}" y1="${y(cv.window)}" y2="${y(cv.window)}" stroke="#66747f" stroke-dasharray="4 4"/><text x="${W - P.r}" y="${y(cv.window) - 4}" font-size="11" text-anchor="end" fill="#66747f">窗口 ${fmt(cv.window)}</text>` : ''}
    ${cv.auto_compact_threshold ? `<line x1="${P.l}" x2="${W - P.r}" y1="${y(cv.auto_compact_threshold)}" y2="${y(cv.auto_compact_threshold)}" stroke="#b3382c" stroke-dasharray="2 4"/><text x="${W - P.r}" y="${y(cv.auto_compact_threshold) + 12}" font-size="11" text-anchor="end" fill="#b3382c">90% 阈值（源码推算）</text>` : ''}
    <path d="${d}" fill="none" stroke="#20625a" stroke-width="2"/>
    ${cv.compactions.map(c => `<line x1="${x(c.ordinal)}" x2="${x(c.ordinal)}" y1="${P.t}" y2="${H - P.b}" stroke="#b3382c" stroke-width="2"/><text x="${x(c.ordinal) + 4}" y="${P.t + 12}" font-size="11" fill="#b3382c">压缩 #${c.window_number ?? ''}</text>`).join('')}
    <line id="cursor" x1="${P.l}" x2="${P.l}" y1="${P.t}" y2="${H - P.b}" stroke="#1f2a33" stroke-width="1"/>
    <text x="${P.l}" y="${H - 8}" font-size="11" fill="#8a97a0">rollout 行序 →</text>
  </svg>`;
  el._x = x;
}
function markChart(ordinal) { const c = $('#cursor'), el = $('#chart'); if (c && el && el._x) { c.setAttribute('x1', el._x(ordinal)); c.setAttribute('x2', el._x(ordinal)); } }

// ================================================================ CODEX · 源码对照
views.codex.source = async el => {
  const d = await api('/codex/facts');
  const topics = [['trigger', '何时触发记忆生成'], ['selection', '哪些会话会被抽取'], ['stage1', '阶段一：抽取'], ['stage2', '阶段二：整合'], ['files', '文件产物'], ['recall', '召回与引用'], ['retention', '保留、取代与遗忘'], ['compaction', '上下文压缩'], ['rollout', 'rollout 格式']];
  el.innerHTML = `
  <section class="panel tight">
    <div class="row between"><h2 style="margin:0">源码对照：${d.counts.verified} 条已定位 · ${d.counts.drifted} 条锚点漂移 · ${d.counts.missing} 条文件缺失</h2><span>${ev('source')}</span></div>
    <p class="muted">源码 <code>${esc(d.source.path)}</code> @ <code>${esc(short(d.source.revision, 12))}</code>（${esc(day(d.source.commit_date))}）。每条事实的锚点在页面加载时重新 grep，所以行号跟你本地 checkout 一致；“漂移”表示文件在但锚点找不到，说明该版本行为可能已变化。</p>
  </section>
  ${topics.map(([k, t]) => { const fs = d.facts.filter(f => f.topic === k); return fs.length ? `<section class="panel"><h2>${esc(t)}</h2><div class="grid c2">${fs.map(f => `<div class="fact ${f.status}"><div class="row between"><b>${esc(f.title)}</b><span class="pill ${f.status === 'verified' ? 'ok' : f.status === 'drifted' ? 'warn' : 'bad'}">${f.status === 'verified' ? '已定位' : f.status === 'drifted' ? '锚点漂移' : '文件缺失'}</span></div><p style="font-size:13px">${esc(f.detail)}</p><div class="loc mono">codex-rs/${esc(f.file)}${f.line ? ':' + f.line : ''}</div>${f.snippet ? `<pre style="max-height:70px;margin:6px 0 0">${esc(f.snippet)}</pre>` : ''}${Object.keys(f.constants || {}).length ? `<div style="margin-top:6px">${Object.entries(f.constants).map(([a, b]) => `<span class="pill">${esc(a)}=${esc(String(b))}</span>`).join(' ')}</div>` : ''}</div>`).join('')}</div></section>` : ''; }).join('')}`;
};

// ================================================================ CLAUDE CODE
views.claude.overview = async el => {
  const o = await api('/overview'); const c = o.claude;
  el.innerHTML = `
  <section class="panel">
    <h1>Claude Code 的记忆：能从本地看到什么</h1>
    <p class="muted">数据目录 <code>${esc(c.home)}</code>。Claude Code 没有本地源码可对照，因此这里只有「本地数据」与「系统推断」两种证据等级。</p>
    <div class="grid c4" style="margin-top:14px">
      <div class="stat"><div class="num">${c.counts.memories}</div><div class="lbl">自动记忆文件（projects/*/memory/*.md）${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${c.counts.projects}</div><div class="lbl">项目目录 ${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${c.transcripts}</div><div class="lbl">会话转录 jsonl ${ev('local_data')}</div></div>
      <div class="stat"><div class="num">${c.global_claude_md.exists ? fmt(c.global_claude_md.bytes) : '无'}</div><div class="lbl">全局 CLAUDE.md 字节数（用户手写指令）${ev('local_data')}</div></div>
    </div>
    <div class="callout infer" style="margin-top:14px">${ev('inference')} 从文件形态推断：Claude Code 的长期记忆是<b>按项目目录</b>存放的 markdown（一文件一事实，带 frontmatter：name/description/type/originSessionId），由 <code>MEMORY.md</code> 索引在每次会话加载；短期上下文压缩表现为转录里带 <code>isCompactSummary:true</code> 的 user 消息，以“This session is being continued from a previous conversation…”开头。写入时机与召回规则无法从本地数据确证。</div>
  </section>
  <section class="panel"><h2>与 Codex 的对照 ${ev('inference')}</h2>
    <table><tr><th></th><th>Codex</th><th>Claude Code</th></tr>
    <tr><td>长期记忆载体</td><td>~/.codex/memories/{memory_summary.md, MEMORY.md, rollout_summaries/}，全局一份</td><td>~/.claude/projects/&lt;项目&gt;/memory/*.md + MEMORY.md 索引，按项目分</td></tr>
    <tr><td>谁来写</td><td>后台两阶段：抽取模型 → 沙箱整合 agent（源码事实）</td><td>会话中的模型自己写文件（从 originSessionId 字段推断）</td></tr>
    <tr><td>召回方式</td><td>memory_summary 截 2500 tokens 注入 + 模型按需搜 MEMORY.md（源码事实）</td><td>MEMORY.md 索引进入上下文（推断）</td></tr>
    <tr><td>压缩落盘</td><td>rollout 里的 compacted 行，带 replacement_history</td><td>转录里 isCompactSummary:true 的 user 消息</td></tr>
    <tr><td>压缩 ↔ 长期记忆</td><td>独立：抽取时丢弃压缩摘要（源码事实）</td><td>未知</td></tr>
    </table></section>`;
};
views.claude.assets = async el => {
  const o = await api('/overview'); const ms = o.claude.memories;
  const byP = {}; ms.forEach(m => (byP[m.project] ||= []).push(m));
  el.innerHTML = `<section class="panel tight"><div class="row between"><h2 style="margin:0">自动记忆文件 · ${ms.length} 条</h2>${ev('local_data')}</div><p class="muted">每个文件一条事实。type 字段区分 user / feedback / project / reference；originSessionId 指回写入它的会话。</p></section>
  ${Object.entries(byP).map(([p, list]) => `<section class="panel"><h2>${esc(p.replace(/^-Users-[^-]+-/, '').replace(/-/g, '/'))} <small class="muted">${list.length} 条</small></h2><div class="list">${list.map((m, i) => `<div class="item clickable" data-p="${esc(p)}" data-i="${i}"><div class="row between"><b>${esc(m.name)}</b><span><span class="pill">${esc(m.type || '—')}</span> <span class="pill">${day(m.modified)}</span></span></div><div class="desc">${esc(m.description || '')}</div></div>`).join('')}</div></section>`).join('') || '<section class="panel"><p class="muted">没有找到记忆文件。</p></section>'}`;
  el.querySelectorAll('.item.clickable').forEach(n => n.onclick = () => { const m = byP[n.dataset.p][+n.dataset.i]; drawer(`${esc(m.name)} ${ev('local_data')}`, `<p class="muted">${esc(m.project)}/memory/${esc(m.file)} · type=${esc(m.type)} · 来源会话 <span class="mono">${esc(m.origin_session || '未知')}</span></p><pre>${esc(m.body)}</pre>${m.origin_session ? `<button data-cs="${esc(m.origin_session)}">查看来源会话的上下文</button>` : ''}`); $('#drawer-body').querySelectorAll('[data-cs]').forEach(b => b.onclick = () => { state.sel.csession = b.dataset.cs; state.tab = 'context'; $('#drawer').classList.add('hidden'); render(); }); });
};
views.claude.pipeline = async el => {
  el.innerHTML = `<section class="panel"><h2>形成过程</h2><div class="callout infer">${ev('inference')} Claude Code 没有本地源码，也没有类似 stage1_outputs 的中间表可读，所以无法像 Codex 那样逐站回放。可观察到的链路只有：会话转录（originSessionId）→ memory/*.md 文件 → MEMORY.md 索引行。请到「记忆资产」页从一条记忆跳到它的来源会话。</div></section>`;
};
views.claude.source = async el => {
  el.innerHTML = `<section class="panel"><h2>源码对照</h2><div class="callout warn">Claude Code 不提供本地源码，本页无内容。所有 Claude Code 相关结论都只能标为「本地数据」或「系统推断」。</div></section>`;
};
views.claude.context = async el => {
  const ss = (await api('/claude/sessions')).sessions;
  const sel = state.sel.csession && ss.find(s => s.id === state.sel.csession) ? state.sel.csession : (ss[0] || {}).id;
  el.innerHTML = `<section class="panel tight"><div class="row between"><h2 style="margin:0">短期上下文（Claude Code）</h2>${ev('local_data')}</div>
  <div class="row" style="margin-top:8px"><label>选择会话</label><select id="cs-sel" style="max-width:640px">${ss.map(s => `<option value="${s.id}" ${s.id === sel ? 'selected' : ''}>${esc(day(s.modified))} · ${esc((s.title || s.id).replace(/\s+/g, ' ').slice(0, 70))}${s.compact_summaries ? ` · 压缩 ${s.compact_summaries} 次` : ''}</option>`).join('')}</select></div></section><div id="ctx-body"></div>`;
  const load = async id => { const box = $('#ctx-body'); box.innerHTML = '<section class="panel"><p class="muted">解析转录…</p></section>'; try { const r = await api('/claude/session/' + id); renderContext(box, r, 'claude'); } catch (e) { box.innerHTML = `<section class="panel"><div class="callout warn">${esc(e.message)}</div></section>`; } };
  $('#cs-sel').onchange = e => { state.sel.csession = e.target.value; load(e.target.value); };
  if (sel) load(sel);
};

// hash routing (#codex/context?thread=...) + stop context playback on navigation
const _render = render;
render = async function () {
  if (state.stopCtx) { state.stopCtx(); state.stopCtx = null; }
  const q = new URLSearchParams(); if (state.sel.thread) q.set('thread', state.sel.thread); if (state.sel.life) q.set('life', state.sel.life); if (state.sel.csession) q.set('csession', state.sel.csession);
  const h = `#${state.product}/${state.tab}${[...q].length ? '?' + q : ''}`;
  if (location.hash !== h) history.replaceState(null, '', h);
  return _render();
};
function fromHash() {
  const m = location.hash.match(/^#(codex|claude)\/(overview|assets|pipeline|context|source)(?:\?(.*))?$/);
  if (!m) return; state.product = m[1]; state.tab = m[2];
  const q = new URLSearchParams(m[3] || ''); ['thread', 'life', 'csession'].forEach(k => { if (q.get(k)) state.sel[k] = q.get(k); });
}
window.addEventListener('hashchange', () => { fromHash(); render(); });
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; render(); });
document.querySelectorAll('#products button').forEach(b => b.onclick = () => { state.product = b.dataset.product; render(); });
fromHash(); render();
})();
