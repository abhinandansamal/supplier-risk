// Supplier Risk Investigator UI: plain JS, no build step, no external resources.
// Security: every value from the server is untrusted and is only ever set with textContent
// (backend/tests/unit/test_web_static.py forbids HTML sinks). The session cookie is httpOnly,
// so this script never sees the token; nothing is kept in browser storage.
"use strict";

(() => {
  const API = "/api/v1";
  const ID_PATTERN = /^inv-[0-9a-f]{16}$/;
  const ACTION_PATTERN = /^[A-Za-z0-9_:.-]{1,128}$/;
  const FINISHED = new Set(["completed", "incomplete", "escalated", "rejected", "failed"]);

  const $ = (id) => document.getElementById(id);
  let currentUser = null;
  let currentId = null;
  let source = null;
  let evidenceById = new Map();

  // --- DOM helpers (text only) ---------------------------------------------------------------

  function el(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
    if (options.type) node.type = options.type;
    if (options.title) node.title = String(options.title);
    for (const child of children) {
      if (child) node.appendChild(child);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function show(node, visible) {
    node.hidden = !visible;
  }

  // --- HTTP ---------------------------------------------------------------------------------

  async function api(method, path, body) {
    const options = { method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(API + path, options);
    let data = null;
    if (response.status !== 204) {
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }
    }
    if (response.status === 401 && path !== "/auth/login") {
      loggedOut();
    }
    return { ok: response.ok, status: response.status, data };
  }

  function errorText(result, fallback) {
    if (result.data && typeof result.data.message === "string") return result.data.message;
    return fallback;
  }

  // --- cache banner -------------------------------------------------------------------------

  async function checkCache() {
    try {
      const response = await fetch("/ready", { credentials: "same-origin" });
      const data = await response.json();
      const warming = data && data.cache === "warming";
      show($("cache-banner"), warming);
      if (warming) setTimeout(checkCache, 10000);
    } catch (_) {
      setTimeout(checkCache, 30000);
    }
  }

  // --- login --------------------------------------------------------------------------------

  function loggedIn(user) {
    currentUser = user;
    const scope = user.category ? `${user.role}, ${user.category}` : user.role;
    $("whoami-text").textContent = `${user.username} (${scope})`;
    show($("whoami"), true);
    show($("login-view"), false);
    show($("app-view"), true);
    loadHistory();
  }

  function loggedOut() {
    currentUser = null;
    closeStream();
    show($("whoami"), false);
    show($("app-view"), false);
    show($("login-view"), true);
  }

  async function onLogin(event) {
    event.preventDefault();
    $("login-error").textContent = "";
    const result = await api("POST", "/auth/login", {
      username: $("login-user").value,
      password: $("login-password").value,
    });
    $("login-password").value = "";
    if (!result.ok) {
      $("login-error").textContent = errorText(result, "Login failed.");
      return;
    }
    loggedIn(result.data);
  }

  async function onLogout() {
    await api("POST", "/auth/logout");
    loggedOut();
  }

  // --- history ------------------------------------------------------------------------------

  async function loadHistory() {
    const result = await api("GET", "/investigations");
    const list = $("history");
    clear(list);
    if (!result.ok) return;
    for (const item of result.data.investigations || []) {
      if (!ID_PATTERN.test(item.id)) continue;
      const owner = item.owner !== currentUser.username ? ` · ${item.owner}` : "";
      const button = el("button", { type: "button", text: item.question }, [
        el("span", { className: "meta", text: `${item.status}${owner} · ${shortTime(item.created_at)}` }),
      ]);
      button.addEventListener("click", () => openInvestigation(item.id));
      list.appendChild(el("li", {}, [button]));
    }
  }

  function shortTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
  }

  // --- asking -------------------------------------------------------------------------------

  async function onAsk(event) {
    event.preventDefault();
    $("ask-error").textContent = "";
    const question = $("question").value.trim();
    if (!question) return;
    $("ask-button").disabled = true;
    try {
      const result = await api("POST", "/investigations", { question });
      if (!result.ok) {
        $("ask-error").textContent = errorText(result, "Could not start the investigation.");
        return;
      }
      loadHistory();
      openInvestigation(result.data.id, result.data.queue_position);
    } finally {
      $("ask-button").disabled = false;
    }
  }

  function closeStream() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function openInvestigation(id, position) {
    if (!ID_PATTERN.test(id)) return;
    closeStream();
    currentId = id;
    clear($("timeline"));
    clear($("result"));
    show($("evidence-panel"), false);
    $("result").appendChild(el("p", { className: "muted", text: "Working on it…" }));
    setStatus(position ? { status: "queued", queue_position: position } : { status: "loading" });
    source = new EventSource(`${API}/investigations/${id}/events`);
    source.addEventListener("status", (message) => setStatus(parse(message)));
    source.addEventListener("trace", (message) => addTrace(parse(message)));
    source.addEventListener("done", (message) => {
      // Close at once: an open EventSource would reconnect and replay the stream.
      closeStream();
      const data = parse(message);
      setStatus(data);
      if (id === currentId) loadResult(id);
      loadHistory();
    });
    source.onerror = () => {
      if (source && source.readyState === EventSource.CLOSED) {
        closeStream();
        loadResult(id);
      }
    };
  }

  function parse(message) {
    try {
      return JSON.parse(message.data);
    } catch (_) {
      return {};
    }
  }

  function setStatus(data) {
    const status = data && data.status;
    let text = status ? `Status: ${status}` : "";
    if (status === "queued" && data.queue_position) text += ` (position ${data.queue_position} in the queue)`;
    if (status === "running") text = "Status: running (the agents are working)";
    $("run-status").textContent = text;
  }

  // --- timeline -----------------------------------------------------------------------------

  function addTrace(event) {
    const parts = [];
    if (event.model_requested) parts.push(`model ${event.model_requested}`);
    if (event.routed_model && event.routed_model !== event.model_requested) parts.push(`served by ${event.routed_model}`);
    if (event.receipt_id) parts.push(`receipt ${event.receipt_id}`);
    if (typeof event.latency_ms === "number") parts.push(`${event.latency_ms} ms`);
    if (typeof event.tokens === "number") parts.push(`${event.tokens} tokens`);
    if (event.tools && event.tools.length) parts.push(`tools ${event.tools.join(", ")}`);
    if (event.evidence_ids && event.evidence_ids.length) parts.push(`evidence ${event.evidence_ids.join(", ")}`);
    if (event.verdict) parts.push(`verdict ${event.verdict}`);
    if (event.note) parts.push(`note ${event.note}`);
    if (typeof event.budget_left === "number") parts.push(`budget left ${event.budget_left}`);
    const step = event.step ? `/${event.step}` : "";
    const warn = /fallback|degraded|failed|injection|stopped/.test(event.action || "");
    const className = event.action === "model_call" ? "model" : warn ? "warn" : "";
    $("timeline").appendChild(
      el("li", { className }, [
        el("span", { className: "agent", text: `${event.agent}${step}` }),
        el("span", { text: ` ${event.action}` }),
        parts.length ? el("span", { className: "detail", text: parts.join(" · ") }) : null,
      ]),
    );
  }

  // --- result -------------------------------------------------------------------------------

  async function loadResult(id) {
    const response = await api("GET", `/investigations/${id}`);
    if (id !== currentId) return;
    const box = $("result");
    clear(box);
    if (!response.ok) {
      box.appendChild(el("p", { className: "error", text: errorText(response, "Could not load the result.") }));
      return;
    }
    const data = response.data;
    if (!FINISHED.has(data.status)) {
      box.appendChild(el("p", { className: "muted", text: `Status: ${data.status}` }));
      return;
    }
    const result = data.result || {};
    if (!$("timeline").firstChild) (result.trace || []).forEach(addTrace);
    evidenceById = new Map((result.evidence || []).map((e) => [e.evidence_id, e]));
    box.appendChild(el("p", { className: "muted", text: `Question: ${data.question}` }));
    if (data.message) box.appendChild(el("p", { className: "status", text: data.message }));
    renderScores(box, result.scores || []);
    renderReport(box, result.report);
    renderProposals(box, data.proposals || []);
    if (typeof data.model_calls === "number") {
      box.appendChild(el("p", { className: "muted", text: `${data.model_calls} model calls · status ${data.status}` }));
    }
  }

  function badge(rating) {
    const known = ["red", "amber", "green", "insufficient"].includes(rating) ? rating : "neutral";
    const icon = { red: "▲", amber: "●", green: "✓", insufficient: "?" }[known] || "";
    return el("span", { className: `badge ${known}`, text: `${icon} ${String(rating).toUpperCase()}` });
  }

  function evidenceButtons(ids) {
    const span = el("span");
    for (const id of ids || []) {
      const button = el("button", { type: "button", className: "evidence-id", text: id, title: "Show evidence" });
      button.addEventListener("click", () => showEvidence(id));
      span.appendChild(button);
    }
    return span;
  }

  function renderScores(box, scores) {
    for (const score of scores) {
      const total = typeof score.total === "number" ? score.total.toFixed(1) : "not scored";
      box.appendChild(
        el("div", { className: "score-head" }, [
          badge(score.rating),
          el("strong", { text: `${score.supplier_id} · score ${total} · coverage ${score.coverage_pct} %` }),
        ]),
      );
      const critical = (score.critical_signals || []).map((s) => `${s.component} (${s.value})`);
      box.appendChild(el("p", { className: "muted", text: `Critical signals: ${critical.join(", ") || "none"}` }));
      const rows = (score.components || []).map((component) => {
        const bar = el("div", { className: "bar" }, [el("span")]);
        if (component.assessed && typeof component.value === "number") {
          bar.firstChild.style.width = `${Math.max(0, Math.min(100, component.value))}%`;
        }
        return el("tr", {}, [
          el("td", { text: component.name }),
          el("td", { text: component.assessed ? String(component.value) : "not assessed" }),
          el("td", {}, [bar]),
          el("td", { text: String(component.weight) }),
          el("td", {}, [el("span", { text: `${component.reason} ` }), evidenceButtons(component.evidence_ids)]),
        ]);
      });
      box.appendChild(
        el("table", {}, [
          el("thead", {}, [
            el("tr", {}, ["Component", "Sub-score", "", "Weight", "Reason"].map((h) => el("th", { text: h }))),
          ]),
          el("tbody", {}, rows),
        ]),
      );
    }
  }

  function renderReport(box, report) {
    if (!report) return;
    box.appendChild(el("p", { className: "headline", text: report.headline }));
    if (report.claims && report.claims.length) {
      box.appendChild(el("h3", { text: "Claims" }));
      const list = el("ol", { className: "claims" });
      for (const claim of report.claims) {
        list.appendChild(
          el("li", {}, [
            el("span", { text: `${claim.text} ` }),
            claim.kind === "hypothesis" ? el("span", { className: "kind", text: "(hypothesis) " }) : null,
            evidenceButtons(claim.evidence_ids),
            claim.verified ? null : el("span", { className: "unverified", text: `Unverified: ${(claim.problems || []).join("; ")}` }),
          ]),
        );
      }
      box.appendChild(list);
    }
    if (report.missing && report.missing.length) {
      box.appendChild(el("h3", { text: "Missing" }));
      box.appendChild(el("ul", {}, report.missing.map((m) => el("li", { text: m }))));
    }
    if (report.notes && report.notes.length) {
      box.appendChild(el("h3", { text: "Notes" }));
      box.appendChild(
        el("ul", { className: "notes" }, report.notes.map((n) =>
          el("li", { className: /instruction-like/.test(n) ? "injection" : "", text: n }),
        )),
      );
    }
  }

  function canDecide(proposal) {
    if (!currentUser || proposal.status !== "pending") return false;
    if (currentUser.role === "risk_manager") return true;
    return currentUser.role === "buyer" && proposal.impact !== "high";
  }

  function renderProposals(box, proposals) {
    if (!proposals.length) return;
    box.appendChild(el("h3", { text: "Proposed actions (need a human decision)" }));
    for (const proposal of proposals) {
      const decided = proposal.status !== "pending";
      const card = el("div", { className: `proposal${decided ? " decided" : ""}` }, [
        el("strong", { text: `${proposal.kind} for ${proposal.supplier_id} · impact ${proposal.impact}` }),
        el("p", { text: proposal.rationale }),
        evidenceButtons(proposal.evidence_ids),
        el("p", {
          className: "muted",
          text: decided ? `${proposal.status} by ${proposal.decided_by}` : "Pending: nothing happens until someone approves.",
        }),
      ]);
      if (canDecide(proposal) && ACTION_PATTERN.test(proposal.action_id)) {
        const comment = el("input", { type: "text" });
        comment.maxLength = 500;
        comment.placeholder = "Comment (optional)";
        comment.setAttribute("aria-label", "Decision comment");
        const approve = el("button", { type: "button", text: "Approve" });
        const reject = el("button", { type: "button", className: "secondary", text: "Reject" });
        const error = el("p", { className: "error" });
        const decide = async (verb) => {
          approve.disabled = reject.disabled = true;
          const body = comment.value.trim() ? { comment: comment.value.trim() } : {};
          const result = await api("POST", `/proposals/${encodeURIComponent(proposal.action_id)}/${verb}`, body);
          if (!result.ok) {
            error.textContent = errorText(result, "The decision was not recorded.");
            approve.disabled = reject.disabled = false;
            return;
          }
          loadResult(currentId);
        };
        approve.addEventListener("click", () => decide("approve"));
        reject.addEventListener("click", () => decide("reject"));
        card.appendChild(el("div", { className: "actions" }, [comment, approve, reject]));
        card.appendChild(error);
      }
      box.appendChild(card);
    }
  }

  // --- evidence panel -----------------------------------------------------------------------

  function showEvidence(id) {
    const record = evidenceById.get(id);
    const body = $("evidence-body");
    clear(body);
    if (!record) {
      body.appendChild(el("p", { className: "muted", text: `Evidence ${id} is not part of this investigation.` }));
    } else {
      const fields = [
        ["Evidence id", record.evidence_id],
        ["Tool", record.tool],
        ["Source", record.source],
        ["View", record.view || "none"],
        ["SQL template", record.sql_template_id || "none"],
        ["Snapshot at", record.snapshot_at || "none"],
        ["Retrieved at", record.retrieved_at],
        ["Zetaris query id", record.zetaris_query_id || "none"],
        ["Rows used", record.row_count],
        ["Parameters", JSON.stringify(record.params || {})],
      ];
      if (record.note) fields.push(["Note", record.note]);
      const list = el("dl", { className: "fields" });
      for (const [name, value] of fields) {
        list.appendChild(el("dt", { text: name }));
        list.appendChild(el("dd", { text: value }));
      }
      body.appendChild(list);
      const facts = Object.entries(record.facts || {});
      body.appendChild(el("h3", { text: "Facts (computed from all rows)" }));
      body.appendChild(
        facts.length
          ? el("table", {}, [
              el("tbody", {}, facts.map(([key, value]) =>
                el("tr", {}, [el("td", { text: key }), el("td", { text: JSON.stringify(value) })]),
              )),
            ])
          : el("p", { className: "muted", text: "No facts." }),
      );
    }
    show($("evidence-panel"), true);
    $("evidence-close").focus();
  }

  // --- start --------------------------------------------------------------------------------

  async function init() {
    $("login-form").addEventListener("submit", onLogin);
    $("logout").addEventListener("click", onLogout);
    $("ask-form").addEventListener("submit", onAsk);
    $("evidence-close").addEventListener("click", () => show($("evidence-panel"), false));
    for (const button of document.querySelectorAll("button.example")) {
      button.addEventListener("click", () => {
        $("question").value = button.dataset.question || "";
        $("question").focus();
      });
    }
    checkCache();
    const me = await api("GET", "/auth/me");
    if (me.ok) loggedIn(me.data);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
