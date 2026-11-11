/* AgentTrace 回放工作台 · Ink & Ledger（数据接口：/api/attempts、/api/attempts/{id}、/api/attempts/{id}/metrics） */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ICONS = {
  llm: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8 14.2 8 8 14.2 1.8 8Z"/></svg>',
  tool: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="1.3" fill="currentColor" stroke="none"/></svg>',
  diff: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2.5 4h11M2.5 8h7M2.5 12h11"/></svg>',
  cmd: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 4.5 7 8l-4 3.5M9.5 11.5H13"/></svg>',
  run: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="5.5"/><path d="M5.3 8.2 7.2 10 10.8 6.4"/></svg>',
  audit: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8 13 3.6v4.2c0 3.4-2.2 5.6-5 6.6-2.8-1-5-3.2-5-6.6V3.6Z"/></svg>',
  err: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3l10 10M13 3 3 13"/></svg>',
  check: '<svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M2.5 8.5 6.5 12.5 13.5 4"/></svg>',
  cross: '<svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3l10 10M13 3 3 13"/></svg>',
  play: '<svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor"><path d="M2.5 1.5 10.5 6 2.5 10.5Z"/></svg>',
  pause: '<svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor"><path d="M2.5 1.5h2.6v9H2.5zM6.9 1.5h2.6v9H6.9z"/></svg>',
};

const TYPE_META = {
  llm_call: { label: "模型调用", icon: ICONS.llm },
  tool_call: { label: "工具动作", icon: ICONS.tool },
  file_diff: { label: "文件改动", icon: ICONS.diff },
  command_exec: { label: "命令执行", icon: ICONS.cmd },
  run_result: { label: "测试判定", icon: ICONS.run },
  mcp_audit: { label: "MCP 声明", icon: ICONS.audit },
  policy_audit: { label: "权限决策", icon: ICONS.audit },
  error: { label: "异常", icon: ICONS.err },
};

const LABEL_CN = {
  passed: "通过", parse_error: "动作解析失败", empty_response: "模型空回复", max_turns: "轮数耗尽",
  tests_failed: "实现未通过测试", incomplete: "未完成即终止",
  workspace_escape: "工作区越界尝试", unknown_tool: "调用未知工具", unknown: "未知原因",
};

const state = { attempts: [], doc: null, metrics: null, cursor: 0, playing: false, timer: null, t0: 0, span: 1 };

/* 访问令牌：服务开启鉴权时，通过 URL 查询参数 ?token=... 传入 */
const TOKEN = new URLSearchParams(location.search).get("token") || "";

async function api(path) {
  const opts = TOKEN ? { headers: { "X-AgentTrace-Token": TOKEN } } : undefined;
  const res = await fetch(path, opts);
  if (res.status === 401) throw new Error("HTTP 401（缺少或错误的访问令牌）");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function fmtTime(iso) {
  const d = new Date(iso);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

function fmtDur(ms) {
  if (ms == null || isNaN(ms)) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function fmtClock(ms) {
  const s = Math.max(0, ms) / 1000;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `00:${p(m)}:${p(r.toFixed(1).padStart(4, "0"))}`;
}

function shortDesc(ev) {
  const d = ev.data || {};
  const cut = (s, n = 70) => (s && s.length > n ? s.slice(0, n) + "…" : s || "");
  switch (ev.type) {
    case "llm_call": {
      const u = d.usage || {};
      return `${d.model} · ${u.prompt_tokens ?? "?"}+${u.completion_tokens ?? "?"} tok`;
    }
    case "tool_call": return `${d.tool} → ${d.status}`;
    case "file_diff": return d.path;
    case "command_exec": return `$ ${cut((d.cmd || []).join(" "))} · exit ${d.exit_code}`;
    case "run_result": return d.passed ? "隐藏测试全部通过" : "未通过";
    case "mcp_audit": return `${d.decision} · ${d.tool} · ${d.rule_id}`;
    case "policy_audit": return `${d.decision} · ${d.tool} · ${d.rule_id}`;
    case "error": return cut(d.message, 60);
    default: return "";
  }
}

/* ---------- 尝试列表 ---------- */

async function loadAttempts() {
  try {
    const data = await api("/api/attempts");
    state.attempts = (data.attempts || []).slice().reverse();
    renderList();
  } catch (e) {
    $("attempt-list").innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`;
  }
}

function renderList() {
  const box = $("attempt-list");
  const rows = state.attempts;
  if (!rows.length) {
    box.innerHTML = '<div class="rail-cap">Attempts</div><div class="hint">暂无数据：先运行一次评测任务（python atp.py run-task …）</div>';
    return;
  }
  const nPass = rows.filter((a) => a.status === "passed").length;
  box.innerHTML =
    `<div class="rail-cap">Attempts · ${rows.length}</div>
     <div class="filters"><span class="fchip on">全部 ${rows.length}</span><span class="fchip">通过 ${nPass}</span><span class="fchip">失败 ${rows.length - nPass}</span></div>` +
    rows
      .map((a) => {
        const dot = a.status === "passed" ? "st" : "st bad";
        return `<div class="attempt" data-id="${esc(a.attempt_id)}">
          <span class="${dot}"></span>
          <div class="info"><div class="t">${esc(a.task_id)}</div>
          <div class="m">${esc(a.agent)} / ${esc(a.model)} · ${esc(fmtTime(a.started_at))}</div></div>
        </div>`;
      })
      .join("");
  box.querySelectorAll(".attempt").forEach((el) =>
    el.addEventListener("click", () => selectAttempt(el.dataset.id))
  );
}

/* ---------- 轨迹视图 ---------- */

async function selectAttempt(id) {
  document
    .querySelectorAll(".attempt")
    .forEach((el) => el.classList.toggle("active", el.dataset.id === id));
  $("empty-hint").classList.add("hidden");
  $("trace-view").classList.remove("hidden");
  state.doc = await api(`/api/attempts/${id}`);
  try {
    state.metrics = await api(`/api/attempts/${id}/metrics`);
  } catch (e) {
    state.metrics = null;
  }
  stopPlay();
  state.cursor = 0;
  renderSummary();
  renderWaterfall();
  $("event-detail").innerHTML = '<div class="hint">点击瀑布图中的事件查看完整详情，或按播放逐步回放</div>';
  const a = state.doc.attempt;
  $("metafoot").innerHTML =
    `ATTEMPT ${esc(a.attempt_id)}<br>` +
    `RUN ${esc(a.meta?.run_id || "—")} · SANDBOX ${esc((a.meta?.sandbox || "local").toUpperCase())}<br>` +
    `${esc(a.started_at)} → ${esc(a.finished_at || "—")} UTC`;
}

function renderSummary() {
  const a = state.doc.attempt;
  const evs = state.doc.events;
  const mt = state.metrics?.metrics;
  const cl = state.metrics?.classification;

  const tok = evs
    .filter((e) => e.type === "llm_call")
    .reduce((s, e) => s + (e.data.usage?.prompt_tokens || 0) + (e.data.usage?.completion_tokens || 0), 0);
  const llmN = evs.filter((e) => e.type === "llm_call").length;
  const toolN = evs.filter((e) => e.type === "tool_call").length;
  const dur = a.started_at && a.finished_at ? new Date(a.finished_at) - new Date(a.started_at) : null;

  const stampCls = a.status === "passed" ? "stamp" : a.status === "failed" ? "stamp fail" : "stamp err";
  const stampTxt = a.status === "passed" ? "通过 · 独立判定" : a.status === "failed" ? "未通过" : "执行异常";

  let metricHtml = "";
  if (mt && cl) {
    metricHtml =
      `<span class="stat"><span class="k">失败归因</span><span class="v" style="font-family:var(--font-body)">${esc(LABEL_CN[cl.primary] || cl.primary)}</span></span>` +
      `<span class="stat"><span class="k">代码审计</span><span class="v">${mt.code_audit_high + mt.code_audit_warn}<span class="u"> 项</span></span></span>` +
      `<span class="stat"><span class="k">审计拒绝</span><span class="v">${mt.audit_denied || 0}</span></span>`;
  }
  let proc = "";
  if (mt) {
    proc =
      `<div class="metarow">过程层：无效调用 <b>${(mt.invalid_tool_rate * 100).toFixed(1)}%</b> · ` +
      `重复调用 <b>${(mt.duplicate_tool_rate * 100).toFixed(1)}%</b> · ` +
      `连续错误重试 <b>${mt.max_consecutive_errors}</b> · ` +
      `循环检测 <b>${mt.loop_detected ? "检出" : "未检出"}</b></div>`;
  }

  $("summary").innerHTML =
    `<div class="toprow">
      <span class="title">${esc(a.task_id)}</span>
      <span class="${stampCls}">${esc(stampTxt)}</span>
      <span class="stat"><span class="k">Tokens</span><span class="v">${tok.toLocaleString()}</span></span>
      <span class="stat"><span class="k">耗时</span><span class="v">${esc(fmtDur(dur))}</span></span>
      <span class="stat"><span class="k">LLM 轮次</span><span class="v">${llmN}</span></span>
      <span class="stat"><span class="k">工具调用</span><span class="v">${toolN}</span></span>
      ${metricHtml}
    </div>${proc}`;
}

/* ---------- 执行瀑布图 ---------- */

function niceStep(raw) {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 3600];
  for (const s of steps) if (s >= raw) return s;
  return Math.ceil(raw / 3600) * 3600;
}

function fmtAxis(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? m + "m" + r + "s" : m + "m";
}

function renderWaterfall() {
  const evs = state.doc.events;
  const t0 = new Date(evs[0].ts).getTime();
  const tEnd = new Date(evs[evs.length - 1].ts).getTime();
  const span = Math.max(tEnd - t0, 1);
  state.t0 = t0;
  state.span = span;
  const pct = (ms) => Math.min(100, Math.max(0, ((ms - t0) / span) * 100));

  const step = niceStep(span / 5000);
  let grid = "", labels = "";
  for (let s = 0; s <= span; s += step) {
    const p = ((s / span) * 100).toFixed(2);
    grid += `<i style="left:calc(150px + (100% - 164px)*${p / 100})"></i>`;
    labels += `<span style="left:${p}%">${fmtAxis(s)}</span>`;
  }

  let prevTs = t0;
  const rows = evs
    .map((e, i) => {
      const ts = new Date(e.ts).getTime();
      const off = pct(ts);
      const meta = TYPE_META[e.type] || { label: e.type, icon: ICONS.tool };
      let barHtml = "";
      if (e.type === "llm_call" && e.data.duration_ms != null) {
        const d = e.data.duration_ms;
        const startPct = Math.max(0, pct(ts - d));
        const w = Math.max(0.6, (d / span) * 100);
        barHtml = `<span class="bar llm" style="left:${startPct.toFixed(2)}%;width:${w.toFixed(2)}%"></span>`;
        const u = e.data.usage || {};
        const tokTxt = `${(u.prompt_tokens || 0) + (u.completion_tokens || 0)} tok`;
        const endPct = pct(ts);
        barHtml +=
          endPct <= 86
            ? `<span class="tok" style="left:${(endPct + 0.8).toFixed(2)}%">${tokTxt}</span>`
            : `<span class="tok" style="left:${((startPct + endPct) / 2).toFixed(2)}%;transform:translateX(-50%);color:var(--paper)">${tokTxt}</span>`;
      } else if (e.type === "command_exec") {
        // R-09：优先使用采集侧真实耗时；旧轨迹无 duration_ms 时回退为事件间隔估算
        const d = e.data.duration_ms != null ? e.data.duration_ms : ts - prevTs;
        const startPct = Math.max(0, pct(ts - d));
        const w = Math.max(0.6, (d / span) * 100);
        barHtml = `<span class="bar cmd" style="left:${startPct.toFixed(2)}%;width:${w.toFixed(2)}%"></span>`;
      } else if (e.type === "tool_call" && e.data.duration_ms != null) {
        // R-09：工具调用按真实耗时画条（旧轨迹仍回退为 marker 点）
        const d = e.data.duration_ms;
        const startPct = Math.max(0, pct(ts - d));
        const w = Math.max(0.6, (d / span) * 100);
        barHtml = `<span class="bar tool" style="left:${startPct.toFixed(2)}%;width:${w.toFixed(2)}%"></span>`;
      } else {
        const mk = { tool_call: "mk-tool", file_diff: "mk-diff", run_result: "mk-run", mcp_audit: "mk-audit", error: "mk-err" }[e.type] || "mk-tool";
        barHtml = `<span class="marker ${mk}" style="left:${off.toFixed(2)}%"></span>`;
      }
      prevTs = ts;
      return `<div class="row" style="--i:${i}" data-i="${i}">
        <div class="gut"><span class="sq">#${e.seq}</span>${meta.icon}<span class="lb">${esc(shortDesc(e))}</span></div>
        <div class="track"><span class="lane"></span>${barHtml}</div>
      </div>`;
    })
    .join("");

  const ticks = evs
    .map((e) => `<span class="tick" style="left:${pct(new Date(e.ts).getTime()).toFixed(2)}%"></span>`)
    .join("");

  $("timeline").innerHTML =
    `<div class="wf">
      <div class="wf-head"><div class="axis">${labels}</div></div>
      <div class="wf-body"><div class="grid">${grid}</div>${rows}<div class="playhead" id="playhead" style="display:none"></div></div>
      <div class="wf-foot">
        <span class="li"><span class="sw" style="background:var(--accent)"></span>模型调用</span>
        <span class="li"><span class="sw" style="background:var(--ink)"></span>工具动作</span>
        <span class="li"><span class="sw" style="border:2px solid var(--accent);background:var(--paper);transform:rotate(45deg)"></span>文件改动</span>
        <span class="li"><span class="sw" style="background:linear-gradient(90deg,rgba(214,69,34,.10),rgba(214,69,34,.42));border-left:2.5px solid var(--accent)"></span>命令执行</span>
        <span class="li"><span class="sw" style="border:2px solid var(--teal);background:var(--paper);border-radius:50%"></span>测试判定</span>
        <span class="li note">条长 = 实际耗时 · 间隙 = 等待与传输</span>
      </div>
    </div>`;

  $("scrub-ticks").innerHTML = ticks;
  $("timeline")
    .querySelectorAll(".row[data-i]")
    .forEach((el) => el.addEventListener("click", () => renderDetail(state.doc.events[+el.dataset.i])));
  updateTimeline();
}

function updateTimeline() {
  const evs = state.doc.events;
  $("timeline")
    .querySelectorAll(".row[data-i]")
    .forEach((el, i) => {
      el.classList.toggle("past", i < state.cursor);
      el.classList.toggle("current", i === state.cursor - 1);
      el.classList.toggle("future", i >= state.cursor);
    });
  const cur = state.cursor > 0 ? evs[state.cursor - 1] : null;
  const ph = $("playhead");
  if (cur) {
    const p = Math.min(100, Math.max(0, ((new Date(cur.ts).getTime() - state.t0) / state.span) * 100));
    ph.style.left = `calc(150px + (100% - 164px)*${(p / 100).toFixed(4)})`;
    ph.style.display = "block";
  } else {
    ph.style.display = "none";
  }
  $("scrub-fill").style.width = (state.cursor / evs.length) * 100 + "%";
  $("scrub-head").style.left = (state.cursor / evs.length) * 100 + "%";
  renderProgress();
}

function renderProgress() {
  const n = state.doc ? state.doc.events.length : 0;
  const cur = state.cursor > 0 ? state.doc.events[state.cursor - 1] : null;
  const meta = TYPE_META[cur?.type] || { label: cur?.type || "" };
  $("progress").innerHTML =
    `<b>${state.cursor}</b> / ${n}` + (cur ? ` · ${esc(meta.label)}` : "");
}

/* ---------- 事件详情 ---------- */

function pre(text) {
  return `<pre>${esc(text)}</pre>`;
}

function diffHtml(diff) {
  return diff
    .split("\n")
    .map((line) => {
      const cls =
        line.startsWith("+++") || line.startsWith("---")
          ? "diff-meta"
          : line.startsWith("+")
            ? "diff-add"
            : line.startsWith("-")
              ? "diff-del"
              : "diff-ctx";
      return `<span class="${cls}">${esc(line) || " "}</span>`;
    })
    .join("\n");
}

function renderDetail(ev) {
  const m = TYPE_META[ev.type] || { label: ev.type, color: "#8f887b" };
  let body = "";
  if (ev.type === "llm_call") {
    const u = ev.data.usage || {};
    const dur = ev.data.duration_ms != null ? ` · 耗时 <b>${esc(fmtDur(ev.data.duration_ms))}</b>` : "";
    body += `<div class="kv"><span class="k">Model</span><span class="v">${esc(ev.data.model)}</span></div>`;
    body += `<div class="kv"><span class="k">Tokens</span><span class="v">${u.prompt_tokens ?? "?"} + ${u.completion_tokens ?? "?"}</span></div>`;
    if (dur) body += `<div class="kv"><span class="k">耗时</span><span class="v">${esc(fmtDur(ev.data.duration_ms))}</span></div>`;
    body += (ev.data.messages || [])
      .map((msg) => `<div class="msg ${esc(msg.role)}"><div class="role">${esc(msg.role)}</div>${pre(msg.content)}</div>`)
      .join("");
    body += `<div class="kv" style="margin-top:10px"><span class="k">Response</span></div>${pre(ev.data.response)}`;
  } else if (ev.type === "tool_call") {
    body += `<div class="kv"><span class="k">Tool</span><span class="v">${esc(ev.data.tool)}</span></div>`;
    body += `<div class="kv"><span class="k">Status</span><span class="v">${esc(ev.data.status)}</span></div>`;
    body += `<div class="kv" style="margin-top:8px"><span class="k">Args</span></div>${pre(JSON.stringify(ev.data.args, null, 2))}`;
    const r = ev.data.result;
    body += `<div class="kv"><span class="k">Result</span></div>${pre(typeof r === "string" ? r : JSON.stringify(r, null, 2))}`;
  } else if (ev.type === "file_diff") {
    body += `<div class="kv"><span class="k">File</span><span class="v">${esc(ev.data.path)}</span></div>`;
    body += `<div class="console"><div class="cap"><span>Diff · Unified</span><span>${esc(ev.data.path)}</span></div>` +
      diffHtml(ev.data.diff).split("\n").map((l) => `<div class="ln">${l}</div>`).join("") + `</div>`;
    const au = ev.data.audit;
    if (au) {
      const syn = au.syntax_ok
        ? `<span class="okline">${ICONS.check} 语法检查通过</span>`
        : `<span class="badline">${ICONS.cross} 语法检查未通过</span>`;
      const rows = (au.findings || [])
        .map((f) => `<div class="kv"><span class="k">L${f.lineno}</span><span class="v">[${esc(f.level)}] ${esc(f.rule)} · ${esc(f.message)}</span></div>`)
        .join("");
      body += `<div class="audit"><div class="head"><b>代码审计</b><span class="n">${(au.findings || []).length} 项</span></div>` +
        `<div class="sub">${syn} · 风险项 ${(au.findings || []).length}</div>${rows}` +
        `<div class="note">审计随改动实时生成，随轨迹留痕</div></div>`;
    }
  } else if (ev.type === "command_exec") {
    const ok = ev.data.exit_code === 0;
    body += `<div class="kv"><span class="k">Cmd</span><span class="v">${esc((ev.data.cmd || []).join(" "))}</span></div>`;
    body += `<div class="kv"><span class="k">Exit</span><span class="v ${ok ? "okline" : "badline"}">${ev.data.exit_code}</span></div>`;
    if (ev.data.stdout_tail) body += `<div class="kv" style="margin-top:8px"><span class="k">Stdout</span></div>${pre(ev.data.stdout_tail)}`;
    if (ev.data.stderr_tail) body += `<div class="kv"><span class="k">Stderr</span></div>${pre(ev.data.stderr_tail)}`;
  } else if (ev.type === "run_result") {
    body += ev.data.passed
      ? `<div class="okline">${ICONS.check} 隐藏测试全部通过（passed）</div>`
      : `<div class="badline">${ICONS.cross} 未通过（failed）</div>`;
    body += pre(ev.data.output_tail || "");
  } else if (ev.type === "mcp_audit" || ev.type === "policy_audit") {
    const d = ev.data;
    const deny = d.decision === "deny";
    body += `<div class="kv"><span class="k">决策</span><span class="v ${deny ? "badline" : "okline"}">${esc(d.decision)}</span></div>`;
    body += `<div class="kv"><span class="k">工具</span><span class="v">${esc(d.tool)}</span></div>`;
    body += `<div class="kv"><span class="k">规则</span><span class="v">${esc(d.rule_id)}</span></div>`;
    body += `<div class="kv"><span class="k">说明</span><span class="v">${esc(d.reason)}</span></div>`;
  } else if (ev.type === "error") {
    body += `<div class="badline">${ICONS.cross} 异常</div>${pre(ev.data.message)}`;
  }
  $("event-detail").innerHTML =
    `<div class="ev-head">${m.icon || ""}<b>#${ev.seq} ${esc(m.label || ev.type)}</b>` +
    `<span class="tm">${esc(fmtTime(ev.ts))}</span></div>` + body;
}

/* ---------- 播放器 ---------- */

function stopPlay() {
  state.playing = false;
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
  const b = $("btn-play");
  b.innerHTML = ICONS.play;
  b.classList.remove("playing");
}

function tick() {
  if (!state.doc || state.cursor >= state.doc.events.length) return stopPlay();
  state.cursor += 1;
  updateTimeline();
  renderDetail(state.doc.events[state.cursor - 1]);
  const cur = $("timeline").querySelector(".row.current");
  if (cur) cur.scrollIntoView({ block: "nearest" });
  if (state.cursor >= state.doc.events.length) stopPlay();
}

function togglePlay() {
  if (!state.doc) return;
  if (state.playing) return stopPlay();
  if (state.cursor >= state.doc.events.length) state.cursor = 0;
  state.playing = true;
  const b = $("btn-play");
  b.innerHTML = ICONS.pause;
  b.classList.add("playing");
  state.timer = setInterval(tick, Number($("speed").value));
}

function wire() {
  $("btn-refresh").addEventListener("click", loadAttempts);
  $("btn-play").addEventListener("click", togglePlay);
  $("btn-next").addEventListener("click", () => {
    stopPlay();
    if (state.doc && state.cursor < state.doc.events.length) {
      state.cursor += 1;
      updateTimeline();
    }
  });
  $("btn-prev").addEventListener("click", () => {
    stopPlay();
    if (state.doc) {
      state.cursor = Math.max(0, state.cursor - 1);
      updateTimeline();
    }
  });
  $("btn-start").addEventListener("click", () => {
    stopPlay();
    if (state.doc) {
      state.cursor = 0;
      updateTimeline();
      $("event-detail").innerHTML = '<div class="hint">已重置到开头</div>';
    }
  });
  $("btn-end").addEventListener("click", () => {
    stopPlay();
    if (state.doc) {
      state.cursor = state.doc.events.length;
      updateTimeline();
    }
  });
  $("speed").addEventListener("change", () => {
    if (state.playing) {
      clearInterval(state.timer);
      state.timer = setInterval(tick, Number($("speed").value));
    }
  });
}

wire();
loadAttempts();
