/* FASE 6C — ZAYVERO Business web app (Product Experience).
 *
 * El frontend SOLO presenta información. Toda lógica de negocio, permisos,
 * tenant isolation, cálculos y acceso a datos viven en el backend.
 * Ningún company_id se envía desde el cliente.
 */
(function () {
  "use strict";

  var state = {
    me: null,
    /* SEG-03: token CSRF sincronizado con la sesión. Se obtiene del login
     * o de /api/me y se envía en X-CSRF-Token en operaciones con estado. */
    csrfToken: null,
    summary: null,
    findings: { items: [], total: 0, page: 1, per_page: 20, by_priority: {}, by_type: {} },
    filters: { priority: "all", type: "all", period: "all", q: "" },
    opportunities: [],
    predictions: null,
    audit: [],
    asking: false,
  };

  /* ================= helpers ================= */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function dash(v) {
    return (v === null || v === undefined || v === "") ? "—" : v;
  }
  /* FASE 7C — formateo regional por empresa (config en ZBI18N).
   * La moneda NUNCA se convierte: solo se muestra la configurada.
   * Sin moneda configurada se muestra "Moneda no configurada" (honesto).
   */
  function fmtMoney(v) { return ZBI18N.fmtMoney(v); }
  function fmtNum(v, dec) { return ZBI18N.fmtNum(v, dec); }
  function fmtDate(s) { return ZBI18N.fmtDate(s); }
  /* FASE 7C — texto de interfaz según el idioma de la empresa (es/en). */
  function T(key) { return ZBI18N.t(key); }
  function fmtPct(v) {
    if (v === null || v === undefined || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return (n > 0 ? "+" : "") + n.toFixed(1) + "%";
  }
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    setTimeout(function () { t.classList.add("hidden"); }, 3500);
  }

  /* Mensajes de error amigables: sin stack traces ni información interna. */
  function friendlyError(err) {
    var m = err && err.message;
    if (m === "permiso") return "No tienes permiso para acceder a esta sección.";
    if (m === "no-encontrado") return "No se encontró el recurso solicitado.";
    if (m === "servidor") return "Ocurrió un error interno. Inténtalo de nuevo más tarde.";
    if (err instanceof TypeError) return "No se pudo conectar con el servidor. Verifica tu conexión.";
    return "No se pudo completar la operación. Inténtalo de nuevo.";
  }

  async function api(path, opts) {
    opts = opts || {};
    var fetchOpts = Object.assign({ credentials: "same-origin" }, opts);
    if (fetchOpts.body && typeof fetchOpts.body === "object" && !(fetchOpts.body instanceof FormData)) {
      fetchOpts.body = JSON.stringify(fetchOpts.body);
      fetchOpts.headers = Object.assign({ "Content-Type": "application/json" }, fetchOpts.headers || {});
    }
    /* SEG-03: las operaciones con estado llevan el token CSRF de la
     * sesión. Las lecturas GET no lo necesitan. */
    var method = (fetchOpts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && state.csrfToken) {
      fetchOpts.headers = Object.assign(
        { "X-CSRF-Token": state.csrfToken }, fetchOpts.headers || {});
    }
    var res = await fetch(path, fetchOpts);
    var data = null;
    try { data = await res.json(); } catch (e) { data = {}; }
    if (res.status === 401) { showLogin("Sesión expirada o no autenticado."); throw new Error("auth"); }
    if (res.status === 403) throw new Error("permiso");
    if (res.status === 404) throw new Error("no-encontrado");
    if (res.status >= 500) throw new Error("servidor");
    if (!res.ok) { var err = new Error("error"); err.payload = data; throw err; }
    return data;
  }

  function can(perm) {
    return !!(state.me && state.me.permissions && state.me.permissions.indexOf(perm) !== -1);
  }

  function loadingHTML(msg) {
    return "<div class='loading' role='status' aria-live='polite'>" +
      "<span class='spinner' aria-hidden='true'></span><span>" + esc(msg) + "</span></div>";
  }

  function emptyHTML(msg) {
    return "<div class='empty-state' role='status'><p>" + esc(msg) + "</p></div>";
  }

  /* Ayudas contextuales para métricas: texto simple, sin nuevas métricas. */
  var METRIC_HELP = {
    urgent: "Hallazgos clasificados como URGENT según su impacto, confianza estadística y calidad de evidencia. Requieren atención prioritaria.",
    important: "Hallazgos clasificados como IMPORTANT. Son relevantes para el negocio, aunque con menor urgencia que los anteriores.",
    opportunities: "Oportunidades detectadas a partir de la evidencia. Se presentan como posibles oportunidades: no son una garantía de resultado.",
    predictions: "Estimaciones basadas en el historial. Son proyecciones, no certezas; cada una indica su confianza y limitaciones.",
    data_quality: "Mide la integridad y consistencia de los datos de origen utilizados en el análisis (0–100). No es lo mismo que la confianza del análisis.",
    confidence: "Representa qué tan completo y confiable es el contexto empresarial disponible para ZAYVERO (0–100). No es la confianza de una sola predicción.",
  };
  function helpHTML(key) {
    if (!METRIC_HELP[key]) return "";
    return "<details class='metric-help'><summary>¿Qué significa?</summary><p>" + esc(METRIC_HELP[key]) + "</p></details>";
  }

  /* ================= login / shell ================= */
  var loginView = document.getElementById("login-view");
  var appView = document.getElementById("app-view");
  var loginError = document.getElementById("login-error");

  function showLogin(msg) {
    appView.classList.add("hidden");
    loginView.classList.remove("hidden");
    if (msg) { loginError.textContent = msg; loginError.classList.remove("hidden"); }
  }
  function showApp() {
    loginView.classList.add("hidden");
    appView.classList.remove("hidden");
  }

  document.getElementById("login-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var btn = document.getElementById("login-btn");
    loginError.classList.add("hidden");
    btn.disabled = true;
    try {
      var loginRes = await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("login-email").value,
          password: document.getElementById("login-password").value,
        }),
      });
      /* SEG-03: guardar el token CSRF de la sesión recién creada. */
      if (loginRes && loginRes.csrf_token) state.csrfToken = loginRes.csrf_token;
      document.getElementById("login-password").value = "";
      await boot();
    } catch (err) {
      if (err.message !== "auth") {
        loginError.textContent = err.message === "usuario deshabilitado"
          ? "Este usuario está deshabilitado. Contacta a un administrador."
          : "Credenciales incorrectas. Revisa tu correo y contraseña.";
        loginError.classList.remove("hidden");
      }
    } finally { btn.disabled = false; }
  });

  document.getElementById("logout-btn").addEventListener("click", async function () {
    try { await api("/api/logout", { method: "POST" }); } catch (e) {}
    location.hash = "#/inicio";
    showLogin();
  });

  document.getElementById("menu-toggle").addEventListener("click", function () {
    document.getElementById("sidebar").classList.toggle("open");
  });
  document.getElementById("sidebar").addEventListener("click", function (e) {
    if (e.target.classList.contains("nav-item")) {
      document.getElementById("sidebar").classList.remove("open");
    }
  });

  async function boot() {
    try {
      state.me = await api("/api/me");
    } catch (e) { return; }
    /* SEG-03: re-sincronizar el token CSRF (recargas de página). */
    if (state.me && state.me.csrf_token) state.csrfToken = state.me.csrf_token;
    /* FASE 8 — entrada del producto: tras el login o al abrir la app con
     * sesión válida, la primera pantalla es siempre #/inicio. Se ignora
     * cualquier hash obsoleto (p. ej. #/resumen de una sesión anterior)
     * para que el usuario vea la experiencia de producto, no el dashboard
     * antiguo directamente. */
    if (location.hash !== "#/inicio") { location.hash = "#/inicio"; }
    /* FASE 7C: idioma y configuración regional de la empresa autenticada. */
    ZBI18N.setConfig(state.me.company && state.me.company.config);
    applyChromeLanguage();
    document.getElementById("company-name").textContent = state.me.company.name || "—";
    var badge = document.getElementById("demo-badge");
    badge.textContent = T("demo.badge");
    badge.classList.toggle("hidden", !state.me.company.is_demo);
    document.getElementById("user-name").textContent = state.me.user.name || state.me.user.email;
    document.getElementById("user-role").textContent = state.me.user.role || "";
    document.getElementById("nav-auditoria").style.display = can("audit.read") ? "" : "none";
    showApp();
    route();
  }

  /* FASE 7C — traduce la cáscara de la interfaz (navegación, barra). */
  function applyChromeLanguage() {
    var nav = {
      inicio: "nav.inicio", empresa: "nav.workspace", diagnostico: "nav.diagnostic",
      resumen: "nav.summary", hallazgos: "nav.findings",
      oportunidades: "nav.opportunities", predicciones: "nav.predictions",
      advisor: "nav.advisor", auditoria: "nav.audit"
    };
    document.querySelectorAll(".nav-item").forEach(function (a) {
      var r = a.getAttribute("data-route");
      if (nav[r]) a.textContent = T(nav[r]);
    });
    var lbl = document.querySelector(".company-label");
    if (lbl) lbl.textContent = T("company.current") + ":";
    var lo = document.getElementById("logout-btn");
    if (lo) lo.textContent = T("logout");
  }

  /* ================= FASE 8: EXPERIENCIA DE PRODUCTO ================= */
  /* Primera entrada / onboarding / estados. El backend (product/) calcula
   * el estado; el frontend solo lo presenta. Sin lógica de negocio aquí. */

  function stepStatusLabel(st) {
    if (st === "done") return T("onboarding.step.done");
    if (st === "current") return T("onboarding.step.current");
    return T("onboarding.step.pending");
  }

  /* SEG-02 (CSP): los anchos dinámicos no pueden ir en style="..." inline.
   * Se marcan con data-w y se aplican por DOM tras el innerHTML. */
  function fixupProgressBars(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll(".progress-bar[data-w]").forEach(function (el) {
      var w = parseInt(el.getAttribute("data-w"), 10);
      if (!isNaN(w)) el.style.width = Math.max(0, Math.min(100, w)) + "%";
      el.removeAttribute("data-w");
    });
  }

  function onboardingHTML(ob) {
    var pct = ob.total ? Math.round(100 * ob.completed / ob.total) : 0;
    var h = "<section class='section' aria-labelledby='h-onb'><h2 id='h-onb'>" +
      esc(T("onboarding.title")) + "</h2>" +
      "<p class='muted'>" + esc(T("onboarding.subtitle")) + "</p>" +
      "<div class='progress-wrap' role='progressbar' aria-valuenow='" + pct +
      "' aria-valuemin='0' aria-valuemax='100' aria-label='" + esc(T("onboarding.progress")) + "'>" +
      /* SEG-02 (CSP): sin style="..." inline; el ancho se asigna por DOM
       * tras insertar el HTML (ver fixupProgressBars). */
      "<div class='progress-bar' data-w='" + pct + "'></div></div>" +
      "<p class='muted small'>" + esc(T("onboarding.progress")) + ": " +
      ob.completed + " / " + ob.total + "</p>" +
      "<ol class='onb-steps'>";
    ob.steps.forEach(function (s) {
      var icon = s.status === "done" ? "✓" : (s.status === "current" ? "→" : "○");
      h += "<li class='onb-step onb-" + s.status + "'>" +
        "<span class='onb-icon' aria-hidden='true'>" + icon + "</span>" +
        "<div><strong>" + s.step + ". " + esc(T(s.title_key)) + "</strong><br>" +
        "<span class='muted'>" + esc(T(s.desc_key)) + "</span><br>" +
        "<span class='tag tag-status'>" + esc(stepStatusLabel(s.status)) + "</span></div></li>";
    });
    return h + "</ol></section>";
  }

  function sequenceHTML(seq) {
    var h = "<section class='section' aria-labelledby='h-seq'><h2 id='h-seq'>" +
      esc(T("sequence.title")) + "</h2><ol class='seq-list'>";
    seq.forEach(function (m) {
      var done = m.status === "done";
      h += "<li class='seq-item " + (done ? "seq-done" : "seq-pending") + "'>" +
        "<span class='seq-icon' aria-hidden='true'>" + (done ? "✓" : "○") + "</span> " +
        esc(T(m.label_key)) + "</li>";
    });
    return h + "</ol></section>";
  }

  function dataflowHTML(flow) {
    var h = "<section class='section' aria-labelledby='h-flow'><h2 id='h-flow'>" +
      esc(T("dataflow.title")) + "</h2><ol class='flow-list'>";
    flow.forEach(function (f, i) {
      h += "<li class='flow-step'><span class='flow-num' aria-hidden='true'>" + (i + 1) +
        "</span> " + esc(T(f.label_key)) + "</li>";
    });
    return h + "</ol></section>";
  }

  function hierarchyHTML(items) {
    var h = "<section class='section' aria-labelledby='h-jour'><h2 id='h-jour'>" +
      esc(T("hierarchy.title")) + "</h2><ol class='hier-list'>";
    items.forEach(function (it, i) {
      h += "<li class='hier-item'><a href='" + esc(it.route) + "' class='hier-link'>" +
        "<span class='hier-num' aria-hidden='true'>" + (i + 1) + "</span>" +
        "<span><strong>" + esc(T(it.label_key)) + "</strong><br>" +
        "<span class='muted'>" + esc(T(it.label_key + "_desc")) + "</span></span></a></li>";
    });
    return h + "</ol></section>";
  }

  function benefitsHTML(items) {
    var h = "<section class='section' aria-labelledby='h-ben'><h2 id='h-ben'>" +
      esc(T("benefits.title")) + "</h2><ul class='benefits-grid'>";
    items.forEach(function (b) {
      h += "<li class='benefit-item'><span class='benefit-check' aria-hidden='true'>✓</span> " +
        esc(T(b.label_key)) + "</li>";
    });
    return h + "</ul><p class='muted small'>" + esc(T("benefits.note")) + "</p></section>";
  }

  function plansHTML(plans) {
    var h = "<section class='section' aria-labelledby='h-plans'><h2 id='h-plans'>" +
      esc(T("plans.title")) + "</h2>" +
      "<p class='muted'>" + esc(T("plans.subtitle")) + "</p>" +
      "<div class='plans-grid'>";
    plans.forEach(function (p) {
      h += "<article class='plan-card'><h3>" + esc(T(p.name_key)) + "</h3>" +
        "<div class='plan-price'>" + esc(T(p.price_note_key)) + "</div>" +
        "<ul class='ev-list'>";
      (p.features_keys || []).forEach(function (fk) {
        h += "<li>" + esc(T(fk)) + "</li>";
      });
      h += "</ul><button class='btn btn-secondary plan-cta' disabled aria-disabled='true'>" +
        esc(T("plans.cta")) + "</button></article>";
    });
    return h + "</div><p class='muted small'>" + esc(T("plans.note")) + "</p></section>";
  }

  function demoCardHTML(isDemo) {
    var h = "<section class='section demo-card' aria-labelledby='h-demo'><h2 id='h-demo'>" +
      esc(T("demo.explore.title")) + "</h2>";
    if (isDemo) {
      h += "<div class='demo-banner' role='status'>" + esc(T("demo.explore.active")) + "</div>";
      /* Modo demo iniciado desde "Explorar demo": ofrece volver a la
       * empresa origen sin pedir login (la sesión origen sigue válida en
       * el servidor). Sin la marca, es un usuario demo directo: se conserva
       * el comportamiento anterior. */
      var inDemoMode = false;
      try { inDemoMode = sessionStorage.getItem("zb_demo") === "1"; } catch (e) {}
      if (inDemoMode) {
        h += "<p><button class='btn' id='demo-back'>" + esc(T("demo.return")) + "</button></p>";
      }
      h += "<p><a class='btn' href='#/diagnostico'>" + esc(T("product.action.view_diagnostic")) + "</a></p>";
    } else {
      h += "<p class='muted'>" + esc(T("demo.explore.desc")) + "</p>" +
        "<button class='btn' id='demo-go'>" + esc(T("demo.explore.cta")) + "</button>";
    }
    return h + "</section>";
  }

  async function renderProductEntry(main) {
    main.innerHTML = loadingHTML(T("common.loading"));
    var ov;
    try { ov = (await api("/api/product/overview")).overview; }
    catch (e) {
      main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>";
      return;
    }
    if (!ov) {
      main.innerHTML = emptyHTML(T("common.error"));
      return;
    }
    var st = ov.product_state || {};
    var state = st.state || "NO_DATA";
    var ready = state === "READY";
    var html = "";

    /* ---- HERO: primera entrada ---- */
    html += "<section class='hero' aria-labelledby='h-hero'>" +
      "<div class='brand hero-brand'><div class='brand-mark'>Z</div>" +
      "<div><div class='brand-name'>ZAYVERO</div>" +
      "<div class='brand-tag'>BUSINESS</div></div></div>" +
      "<h1 id='h-hero' class='hero-title'>" + esc(T(ov.first_entry.main_key)) + "</h1>" +
      "<p class='hero-sub'>" + esc(T(ov.first_entry.secondary_key)) + "</p>";
    if (ov.company.is_demo) {
      html += "<div class='demo-banner' role='status'>" + esc(T("demo.badge")) + " — " +
        esc(T("demo.explore.active")) + "</div>";
    }
    /* Fuentes de datos: solo lo que existe hoy. */
    html += "<div class='sources'><h2 class='sub-h'>" + esc(T("product.source.title")) + "</h2><ul class='sources-list'>";
    (ov.data_sources || []).forEach(function (s) {
      html += "<li>" + esc(T(s.label_key)) + "</li>";
    });
    html += "</ul><p class='muted small'>" + esc(T("product.source.note")) + "</p></div>";
    html += "</section>";

    /* ---- ESTADO DEL PRODUCTO ---- */
    html += "<section class='section' aria-labelledby='h-state'><h2 id='h-state'>" +
      esc(T("product.state.title")) + "</h2>" +
      "<div class='state-banner state-" + esc(state) + "' role='status'>" +
      "<strong>" + esc(T(st.message_key)) + "</strong></div>";
    if (ready) {
      html += "<div class='ready-cta'><p class='diag-lead'>" + esc(T(ov.first_entry.ready_key)) + "</p>" +
        "<a class='btn' href='#/diagnostico'>" + esc(T("product.action.view_diagnostic")) + "</a></div>";
    }
    html += "<h3 class='sub-h'>" + esc(T("product.state.what_now")) + "</h3><div class='actions-row'>";
    (st.actions || []).forEach(function (a) {
      html += "<a class='btn btn-secondary' href='" + esc(a.route) + "'>" + esc(T(a.key)) + "</a>";
    });
    html += "</div></section>";

    /* ---- ONBOARDING (7 pasos) ---- */
    if (!(ov.onboarding || {}).all_done) {
      html += onboardingHTML(ov.onboarding);
      html += dataflowHTML(ov.data_flow || []);
    }

    /* ---- SECUENCIA: ¿en qué va mi empresa? ---- */
    html += sequenceHTML(ov.post_sequence || []);

    /* ---- JERARQUÍA ---- */
    html += hierarchyHTML(ov.hierarchy || []);

    /* ---- DEMO ---- */
    html += demoCardHTML(ov.company.is_demo);

    /* ---- BENEFICIOS ---- */
    html += benefitsHTML(ov.benefits || []);

    /* ---- PLANES ---- */
    html += plansHTML(ov.plans || []);

    main.innerHTML = html;
    /* SEG-02 (CSP): asignar anchos de barras de progreso por DOM (el
     * atributo style="..." inline lo bloquearía la CSP). */
    fixupProgressBars(main);

    /* Botón "Explorar demo": entra a la demo SIN destruir la sesión actual.
     * Crea una sesión demo ligada a demo-retail (rol viewer, TTL 1h);
     * la sesión origen queda intacta para poder volver. Nunca mezcla datos. */
    var dg = document.getElementById("demo-go");
    if (dg) dg.addEventListener("click", async function () {
      dg.disabled = true;
      try {
        var de = await api("/api/demo/enter", { method: "POST" });
        /* SEG-03: la sesión demo trae su propio token CSRF. */
        if (de && de.csrf_token) state.csrfToken = de.csrf_token;
        try { sessionStorage.setItem("zb_demo", "1"); } catch (e) {}
        await boot();
      } catch (e) {
        /* 401: api() ya mostró el login con mensaje claro. */
        if (e && e.message !== "auth") {
          var msg = (e.payload && e.payload.error) || friendlyError(e);
          showLogin(T("demo.enter.failed") + " " + msg);
        }
      } finally { dg.disabled = false; }
    });

    /* Botón "Volver a mi empresa": revoca la sesión demo y restaura la
     * sesión origen (el servidor repone la cookie). */
    var db = document.getElementById("demo-back");
    if (db) db.addEventListener("click", async function () {
      db.disabled = true;
      try {
        var r = await api("/api/demo/exit", { method: "POST" });
        /* SEG-03: al volver, la sesión origen trae su token CSRF. */
        if (r && r.csrf_token) state.csrfToken = r.csrf_token;
        try { sessionStorage.removeItem("zb_demo"); } catch (e) {}
        await boot();
        if (r && r.login_required) {
          showLogin(T("demo.exit.session_expired"));
        }
      } catch (e) {
        if (e && e.message !== "auth") {
          showLogin(T("demo.exit.failed") + " " + friendlyError(e));
        }
      } finally { db.disabled = false; }
    });
  }

  /* ================= router ================= */
  function advisorQuestionFromHash(h) {
    var qm = h.indexOf("?q=");
    if (qm < 0) return "";
    try {
      return decodeURIComponent(h.slice(qm + 3).split("#")[0]);
    } catch (e) { return ""; }
  }

  function route() {
    var h = location.hash || "#/inicio";
    document.querySelectorAll(".nav-item").forEach(function (a) {
      a.classList.toggle("active", h.indexOf(a.getAttribute("href")) === 0);
    });
    var main = document.getElementById("main");
    main.innerHTML = loadingHTML(T("common.loading"));
    if (h.indexOf("#/inicio") === 0) { return renderProductEntry(main); }
    if (h.indexOf("#/empresa/") === 0) { return renderDatasetDetail(main, h.split("/")[2]); }
    if (h.indexOf("#/empresa") === 0) { return renderWorkspace(main); }
    if (h.indexOf("#/diagnostico") === 0) { return renderDiagnostic(main); }
    if (h.indexOf("#/resumen") === 0) { return renderSummary(main); }
    if (h.indexOf("#/hallazgos/") === 0) { return renderFindingDetail(h.split("/")[2], main); }
    if (h.indexOf("#/hallazgos") === 0) { return renderFindings(main); }
    if (h.indexOf("#/oportunidades") === 0) { return renderOpportunities(main); }
    if (h.indexOf("#/predicciones") === 0) { return renderPredictions(main); }
    if (h.indexOf("#/advisor") === 0) { return renderAdvisor(main, advisorQuestionFromHash(h)); }
    if (h.indexOf("#/auditoria") === 0) { return renderAudit(main); }
    return renderProductEntry(main);
  }
  window.addEventListener("hashchange", function () {
    if (!state.me) return;
    route();
  });

  /* ================= CENTRO DE INTELIGENCIA (resumen) ================= */
  async function renderSummary(main) {
    main.innerHTML = loadingHTML("Analizando información…");
    try {
      var results = await Promise.all([
        api("/api/summary"),
        api("/api/opportunities").catch(function () { return { opportunities: [] }; }),
        api("/api/predictions").catch(function () { return null; }),
      ]);
      state.summary = results[0];
      state.opportunities = results[1].opportunities || [];
      state.predictions = results[2];
    } catch (e) {
      main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>";
      return;
    }
    var s = state.summary;
    /* FASE 7A: estados de datos — sin datos listos, mostrar onboarding. */
    if (s && s.ready === false) {
      return renderSummaryNotReady(main, s);
    }
    var c = s.cards || {};
    var att = s.attention_level || "—";

    /* Etiqueta "Mostrando N de M" cuando la sección es una selección.
       (Definida aquí y no solo en renderDiagnostic: esta vista también
       la utiliza; fuera de su alcance producía "showingOf is not defined"
       y la vista Resumen quedaba en blanco.) */
    var sc = s.section_counts || {};
    function showingOf(key) {
      var cc = sc[key] || {};
      var shown = cc.shown || 0, total = cc.total || 0;
      if (total > shown && shown > 0) {
        return "<p class='muted'>" + esc(T("diagnostic.showing_of")
          .replace("{shown}", String(shown)).replace("{total}", String(total))) + "</p>";
      }
      return "";
    }

    /* Texto legible para el campo business_period, que llega como objeto
       {start, end}. Nunca muestra "[object Object]": sin fechas válidas
       devuelve "" y el segmento se omite, igual que cuando el período
       está ausente. */
    function periodText(bp) {
      if (!bp || typeof bp !== "object") return "";
      function part(v) {
        var m = String(v == null ? "" : v).slice(0, 10)
          .match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!m) return "";
        /* Validación real de calendario, incluyendo bisiestos: rechaza
           fechas imposibles como 2026-02-30 o 2025-02-29. */
        var y = +m[1], mo = +m[2], d = +m[3];
        if (mo < 1 || mo > 12 || d < 1) return "";
        var leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
        var dim = [31, leap ? 29 : 28, 31, 30, 31, 30,
                   31, 31, 30, 31, 30, 31][mo - 1];
        if (d > dim) return "";
        return fmtDate(m[0]);
      }
      var a = part(bp.start), b = part(bp.end);
      if (a && b) return a + " → " + b;
      return a || b;
    }

    function card(cls, num, cap, helpKey) {
      return "<div class='card " + cls + "'><div class='cap'>" + esc(cap) + "</div>" +
        "<div class='num'>" + esc(dash(num)) + "</div>" + helpHTML(helpKey) + "</div>";
    }

    var periodTxt = periodText(s.business_period);
    var html = "<h1 class='page-title'>Centro de Inteligencia</h1>" +
      "<p class='page-sub'>Estado de tu empresa según la evidencia disponible. " +
      (periodTxt ? "Período analizado: " + esc(periodTxt) + ". " : "") +
      "Fuente: " + esc(dash(s.dataset_label)) + ".</p>";

    /* ---- ESTADO GENERAL ---- */
    html += "<section class='section' aria-labelledby='h-estado'><h2 id='h-estado'>Estado de mi empresa</h2>" +
      "<div class='attention-banner attention-" + esc(att) + "' role='status'>" +
      "Nivel de atención actual: <strong>" + esc(att) + "</strong></div>" +
      "<div class='cards'>" +
      card("urgent", c.urgent_findings, "Hallazgos urgentes", "urgent") +
      card("important", c.important_findings, "Hallazgos importantes", "important") +
      card("", c.opportunities, "Posibles oportunidades", "opportunities") +
      card("", c.predictions, "Predicciones", "predictions") +
      card("", c.data_quality_score != null ? fmtNum(c.data_quality_score) + "/100" : null, "Calidad de datos", "data_quality") +
      card("", c.context_confidence != null ? fmtNum(c.context_confidence) + "/100" : null, "Confianza del análisis", "confidence") +
      "</div>";

    var snap = s.snapshot || {};
    function snapRow(label, m, money) {
      if (!m || m.value === null || m.value === undefined) return "";
      return "<div class='detail-box'><div class='k'>" + esc(label) + "</div><div class='v'>" +
        esc(money ? fmtMoney(m.value) : fmtNum(m.value, 0)) + "</div></div>";
    }
    html += "<div class='detail-grid'>" +
      snapRow("Ingresos brutos", snap.gross_revenue, true) +
      snapRow("Ingresos netos", snap.net_revenue, true) +
      snapRow("Transacciones", snap.unique_transactions) +
      snapRow("Clientes", snap.total_customers) +
      snapRow("Productos", snap.total_products) +
      snapRow("Países", snap.total_countries) +
      "</div></section>";

    /* ---- REQUIERE ATENCIÓN ---- */
    var top = (s.top_urgent_findings || []).filter(function (f) {
      return f.business_priority === "URGENT" || f.business_priority === "IMPORTANT";
    }).slice(0, 4);
    html += "<section class='section' aria-labelledby='h-atencion'><h2 id='h-atencion'>Requiere atención</h2>";
    if (!top.length) {
      html += emptyHTML("Sin hallazgos disponibles.");
    } else {
      top.forEach(function (f) { html += attentionCardHTML(f); });
      html += "<a class='link-more' href='#/hallazgos'>Ver todos los hallazgos →</a>";
    }
    html += "</section>";

    /* ---- OPORTUNIDADES ---- */
    html += "<section class='section' aria-labelledby='h-opp'><h2 id='h-opp'>Posibles oportunidades</h2>" + showingOf("opportunities") +
      "<p class='muted'>Detectadas a partir de la evidencia. <strong>Posible oportunidad: no es una garantía de resultado.</strong></p>";
    var opps = (state.opportunities || []).slice(0, 3);
    if (!opps.length) {
      html += emptyHTML("No hay oportunidades identificadas con los datos actuales.");
    } else {
      opps.forEach(function (o) { html += oppCardHTML(o); });
      html += "<a class='link-more' href='#/oportunidades'>Ver todas las oportunidades →</a>";
    }
    html += "</section>";

    /* ---- TENDENCIAS ---- */
    var trends = s.trends || [];
    var observed = trends.filter(function (t) { return t.trend_type === "OBSERVED_TREND"; });
    var projected = trends.filter(function (t) { return t.trend_type !== "OBSERVED_TREND"; });
    html += "<section class='section' aria-labelledby='h-trends'><h2 id='h-trends'>Tendencias</h2>";
    html += "<h3 class='sub-h'>Observadas</h3>" + showingOf("trends_observed");
    if (!observed.length) {
      html += emptyHTML("Datos insuficientes para generar este análisis.");
    } else {
      observed.forEach(function (t) {
        html += "<p><span class='tag tag-observed'>Observado</span> <strong>" + esc(dash(t.direction)) + "</strong> " +
          esc(dash(t.period)) + "<br><span class='muted'>" + esc(dash(t.interpretation)) + "</span></p>";
      });
    }
    html += "<h3 class='sub-h'>Proyectadas</h3>" + showingOf("trends_projected");
    if (!projected.length) {
      html += emptyHTML("No existen proyecciones disponibles.");
    } else {
      projected.slice(0, 3).forEach(function (t) {
        html += "<p><span class='tag tag-projected'>Proyección</span> <strong>" + esc(dash(t.direction)) + "</strong> " +
          esc(dash(t.period)) + "<br><span class='muted'>" + esc(dash(t.interpretation)) +
          "</span><br><span class='muted small'>Las proyecciones son estimaciones basadas en el historial, no certezas.</span></p>";
      });
    }
    html += "</section>";

    /* ---- PREDICCIONES ---- */
    var pi = s.prediction_intelligence || {};
    var pv = s.prediction_validation || {};
    html += "<section class='section' aria-labelledby='h-pred'><h2 id='h-pred'>Predicciones</h2>" +
      "<div class='meta'>" +
      "<span class='tag'>" + esc(dash(pi.total)) + " predicciones</span>" +
      "<span class='tag'>Calidad alta: " + esc(dash(pi.high_quality)) + "</span>" +
      "<span class='tag'>Riesgo de caída alto: " + esc(dash(pi.high_decline_risk)) + "</span>" +
      "<span class='tag'>Confianza promedio: " + esc(pi.average_confidence != null ? fmtNum(pi.average_confidence) : "—") + "</span></div>" +
      "<p class='muted'>Validación con resultados reales: " + esc(dash(pv.validated)) + " validadas, " +
      esc(dash(pv.pending)) + " pendientes de validación.</p>";
    var preds = state.predictions && state.predictions.insights ? state.predictions.insights : [];
    var topPreds = preds.filter(function (i) {
      return i.attention_level === "URGENT" || i.attention_level === "IMPORTANT" || i.attention_level === "REVIEW";
    }).slice(0, 3);
    if (!preds.length) {
      html += emptyHTML("No existen predicciones disponibles.");
    } else if (topPreds.length) {
      topPreds.forEach(function (i) { html += predCardHTML(i); });
    }
    html += "<a class='link-more' href='#/predicciones'>Ver todas las predicciones →</a></section>";

    /* ---- SIGUIENTE PASO ---- */
    var first = (s.top_urgent_findings || [])[0];
    html += "<section class='section next-step' aria-labelledby='h-next'><h2 id='h-next'>Recomendación: siguiente paso</h2>";
    if (first) {
      html += "<p>Según la evidencia, lo primero que conviene revisar es:</p>" + attentionCardHTML(first);
    } else {
      html += emptyHTML("Datos insuficientes para generar este análisis.");
    }
    html += "</section>";

    /* ---- CTA ADVISOR ---- */
    html += "<section class='section advisor-cta' aria-labelledby='h-ask'><h2 id='h-ask'>Pregúntale a ZAYVERO</h2>" +
      "<p class='muted'>Consulta los datos de tu empresa y recibe respuestas basadas en la evidencia disponible.</p>" +
      "<a class='btn' href='#/advisor'>Abrir el Advisor</a></section>";

    /* ---- LIMITACIONES ---- */
    html += "<section class='section' aria-labelledby='h-lim'><h2 id='h-lim'>Advertencias y limitaciones</h2><ul class='ev-list'>";
    (s.limitations || []).forEach(function (l) { html += "<li>" + esc(l) + "</li>"; });
    html += "</ul></section>";

    main.innerHTML = html;
    bindCardClicks(main);
  }

  function attentionCardHTML(f) {
    return "<article class='finding-item' data-fid='" + esc(f.finding_id) + "' tabindex='0' role='button' " +
      "aria-label='Ver análisis: " + esc(f.title) + "'>" +
      "<h3>" + esc(f.title) + "</h3>" +
      "<div class='meta'><span class='prio prio-" + esc(f.business_priority) + "'>" + esc(f.business_priority) + "</span>" +
      "<span class='tag'>" + esc(f.finding_type) + "</span>" +
      "<span>Impacto: <strong>" + esc(dash(f.impact_score)) + "</strong></span>" +
      "<span>Confianza: <strong>" + esc(dash(f.confidence_score)) + "</strong></span></div>" +
      "<button class='btn btn-small' data-fid='" + esc(f.finding_id) + "'>Ver análisis</button></article>";
  }

  function oppCardHTML(o) {
    return "<article class='opp-item'><h3>" + esc(dash(o.explanation).split(".")[0]) + "</h3>" +
      "<div class='meta'><span class='tag tag-opp'>Posible oportunidad</span>" +
      "<span class='tag'>Relevancia: " + esc(dash(o.importance)) + "</span>" +
      "<span class='tag'>Tipo: " + esc(dash(o.type)) + "</span></div>" +
      "<p>" + esc(dash(o.explanation)) + "</p>" +
      (o.recommendation ? "<p><strong>Recomendación:</strong> " + esc(o.recommendation) + "</p>" : "") + "</article>";
  }

  function predCardHTML(i) {
    var ent = i.entity || {};
    var vstat = i.validation_status || "PENDING";
    var vlabel = vstat === "VALIDATED" ? "Validada"
      : (vstat === "NOT_AVAILABLE" ? "Datos insuficientes" : "Pendiente de validación");
    var pstat = i.prediction_status || "";
    var statusLabel = pstat === "INSUFFICIENT_DATA" ? "Datos insuficientes" : vlabel;
    return "<article class='pred-item'><h3>" + esc(dash(ent.label)) + " · " + esc(dash(i.period)) + "</h3>" +
      "<div class='meta'><span class='tag'>Valor estimado: <strong>" + esc(fmtMoney(i.predicted_value)) + "</strong></span>" +
      "<span class='tag'>Intervalo: " + esc(fmtMoney(i.lower_bound)) + " – " + esc(fmtMoney(i.upper_bound)) + "</span>" +
      "<span class='tag'>Confianza: " + esc(dash(i.confidence_score)) + "</span>" +
      "<span class='tag'>Calidad: " + esc(dash(i.forecast_quality)) + "</span>" +
      "<span class='tag'>" + esc(T("prediction.historical_trend")) + ": " + esc(dash(i.trend)) + "</span>" +
      (i.forecast_direction ? "<span class='tag'>" + esc(T("prediction.forecast_direction")) + ": " + esc(dash(i.forecast_direction)) + "</span>" : "") +
      "<span class='tag'>Riesgo de caída: " + esc(dash(i.decline_risk)) + "</span>" +
      "<span class='tag'>Método: " + esc(dash(i.method)) + "</span>" +
      "<span class='tag tag-status'>" + esc(statusLabel) + "</span></div>" +
      (i.business_interpretation ? "<p class='muted'>" + esc(i.business_interpretation).slice(0, 300) + "</p>" : "") + "</article>";
  }

  function bindCardClicks(root) {
    root.querySelectorAll(".finding-item").forEach(function (el) {
      function go() { location.hash = "#/hallazgos/" + el.getAttribute("data-fid"); }
      el.addEventListener("click", go);
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      });
    });
  }

  /* ================= FASE 7B: DIAGNÓSTICO EJECUTIVO ================= */
  function advisorLink(q) {
    return "#/advisor?q=" + encodeURIComponent(q);
  }

  async function renderDiagnostic(main) {
    main.innerHTML = loadingHTML("Preparando tu diagnóstico…");
    var d;
    try { d = (await api("/api/diagnostic")).diagnostic; }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    if (!d) {
      main.innerHTML = emptyHTML("No se pudo generar el diagnóstico.");
      return;
    }

    var isDemo = !!d.is_demo;
    var html = "<h1 class='page-title'>Diagnóstico Ejecutivo</h1>" +
      "<p class='page-sub'>" + esc(d.subtitle || "Una visión basada en los datos disponibles de tu empresa.") + "</p>";
    if (isDemo) {
      html += "<div class='demo-banner' role='status'>Diagnóstico de demostración — DEMO · Datos de demostración. No representa una empresa real.</div>";
    }

    if (d.diagnostic_status !== "AVAILABLE") {
      html += "<div class='state-banner state-" + esc(d.diagnostic_status === "LIMITED" ? "INSUFFICIENT_DATA" : "NO_DATA") + "' role='status'>" +
        "<strong>" + esc(d.status_note || "Información insuficiente para generar determinadas conclusiones.") + "</strong></div>" +
        "<section class='section'><h2>Comienza aquí</h2>" +
        "<p>" + esc(d.executive_summary && d.executive_summary.text || "") + "</p>" +
        "<p><a class='btn' href='#/empresa'>Ir a Mi empresa</a></p></section>";
      main.innerHTML = html;
      return;
    }

    var sum = d.executive_summary || {};
    var st = d.business_status || {};
    var sc = d.section_counts || {};

    /* Etiqueta "Mostrando N de M" cuando la sección es una selección. */
    function showingOf(key) {
      var c = sc[key] || {};
      var shown = c.shown || 0, total = c.total || 0;
      if (total > shown && shown > 0) {
        return "<p class='muted'>" + esc(T("diagnostic.showing_of")
          .replace("{shown}", String(shown)).replace("{total}", String(total))) + "</p>";
      }
      return "";
    }

    /* ---- A. Resumen ejecutivo ---- */
    html += "<section class='section' aria-labelledby='h-resumen'><h2 id='h-resumen'>Resumen ejecutivo</h2>" +
      "<p class='diag-lead'>" + esc(sum.text || "") + "</p><ul class='ev-list'>" +
      (sum.bullets || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") +
      "</ul></section>";

    /* ---- B. Estado de la empresa ---- */
    function card(cls, num, cap, helpKey) {
      return "<div class='card " + cls + "'><div class='cap'>" + esc(cap) + "</div>" +
        "<div class='num'>" + esc(dash(num)) + "</div>" + helpHTML(helpKey) + "</div>";
    }
    html += "<section class='section' aria-labelledby='h-estado'><h2 id='h-estado'>Estado de la empresa</h2>" +
      "<div class='attention-banner attention-" + esc(st.attention_level || "LOW") + "' role='status'>" +
      "Nivel de atención actual: <strong>" + esc(dash(st.attention_level)) + "</strong></div>" +
      "<div class='cards'>" +
      card("urgent", st.urgent_findings, "Hallazgos urgentes", "urgent") +
      card("important", st.important_findings, "Hallazgos importantes", "important") +
      card("", st.opportunities, "Posibles oportunidades", "opportunities") +
      card("", st.predictions, "Predicciones", "predictions") +
      card("", st.data_quality_score != null ? fmtNum(st.data_quality_score) + "/100" : null, "Calidad de datos", "data_quality") +
      card("", st.context_confidence_score != null ? fmtNum(st.context_confidence_score) + "/100" : null, "Confianza del análisis", "confidence") +
      "</div></section>";

    /* ---- C. Lo que requiere atención ---- */
    html += "<section class='section' aria-labelledby='h-atencion'><h2 id='h-atencion'>Lo que requiere atención</h2>" + showingOf("priority_attention");
    var att = d.priority_attention || [];
    if (!att.length) {
      html += emptyHTML("Sin hallazgos disponibles.");
    } else {
      att.forEach(function (f) { html += attentionCardHTML(f); });
      html += "<a class='link-more' href='#/hallazgos'>Ver todos los hallazgos →</a>";
    }
    html += "</section>";

    /* ---- D. Riesgos a revisar ---- */
    html += "<section class='section' aria-labelledby='h-riesgos'><h2 id='h-riesgos'>Riesgos a revisar</h2>" + showingOf("risks") +
      "<p class='muted'>Riesgos identificados a partir de la evidencia. <strong>Requieren revisión; no son certezas.</strong></p>";
    var risks = d.risks || [];
    if (!risks.length) {
      html += emptyHTML("Sin riesgos identificados con los datos actuales.");
    } else {
      risks.forEach(function (r) {
        html += "<article class='finding-item'><h3>" + esc(dash(r.risk_type).replace(/_/g, " ")) + " — " + esc(dash(r.severity)) + "</h3>" +
          "<div class='meta'><span class='prio prio-" + esc(r.severity === "HIGH" ? "URGENT" : (r.severity === "MEDIUM" ? "IMPORTANT" : "REVIEW")) + "'>" + esc(dash(r.severity)) + "</span>" +
          (r.evidence_id ? "<span class='ev-id'>" + esc(r.evidence_id) + "</span>" : "") + "</div>" +
          "<p>" + esc(dash(r.explanation)) + "</p>" +
          (r.recommendation ? "<p><strong>Qué revisar:</strong> " + esc(r.recommendation) + "</p>" : "") + "</article>";
      });
    }
    html += "</section>";

    /* ---- E. Posibles oportunidades ---- */
    html += "<section class='section' aria-labelledby='h-opp'><h2 id='h-opp'>Posibles oportunidades</h2>" +
      "<p class='muted'><strong>Posible oportunidad: no es una garantía de resultado.</strong></p>";
    var opps = d.opportunities || [];
    if (!opps.length) {
      html += emptyHTML("No hay oportunidades identificadas con los datos actuales.");
    } else {
      opps.forEach(function (o) { html += oppCardHTML(o); });
      html += "<a class='link-more' href='#/oportunidades'>Ver todas las oportunidades →</a>";
    }
    html += "</section>";

    /* ---- F. Tendencias ---- */
    var tr = d.trends || {};
    var obs = tr.observed || [], proj = tr.projected || [];
    html += "<section class='section' aria-labelledby='h-trends'><h2 id='h-trends'>Tendencias</h2>";
    html += "<h3 class='sub-h'>Observadas</h3>";
    if (!obs.length) { html += emptyHTML("Datos insuficientes para generar este análisis."); }
    else { obs.forEach(function (t) { html += trendHTML(t); }); }
    html += "<h3 class='sub-h'>Proyectadas</h3>";
    if (!proj.length) { html += emptyHTML("No existen proyecciones disponibles."); }
    else { proj.forEach(function (t) { html += trendHTML(t); }); }
    html += "</section>";

    /* ---- G. Predicciones ---- */
    html += "<section class='section' aria-labelledby='h-pred'><h2 id='h-pred'>Predicciones que requieren atención</h2>" + showingOf("predictions") +
      "<p class='muted'>Estimaciones basadas en el historial. No son certezas.</p>";
    var preds = d.predictions || [];
    if (!preds.length) {
      html += emptyHTML("No existen predicciones disponibles.");
    } else {
      preds.forEach(function (i) { html += predCardHTML(i); });
      html += "<a class='link-more' href='#/predicciones'>Ver todas las predicciones →</a>";
    }
    html += "</section>";

    /* ---- H. Recomendaciones prioritarias ---- */
    html += "<section class='section' aria-labelledby='h-recs'><h2 id='h-recs'>Recomendaciones prioritarias</h2>" + showingOf("recommendations") +
      "<p class='muted'>Sugerencias de revisión; no ejecutan ninguna acción.</p>";
    var recs = d.recommendations || [];
    if (!recs.length) {
      html += emptyHTML("Sin recomendaciones registradas.");
    } else {
      html += "<ul class='ev-list'>";
      recs.forEach(function (r) {
        html += "<li><span class='tag tag-rec'>" + esc(dash(r.kind)) + "</span> " + esc(dash(r.text)) +
          (r.evidence_id ? " <span class='ev-id'>" + esc(r.evidence_id) + "</span>" : "") + "</li>";
      });
      html += "</ul>";
    }
    html += "</section>";

    /* ---- I. Calidad y cobertura de los datos ---- */
    var dq = d.data_quality || {};
    html += "<section class='section' aria-labelledby='h-dq'><h2 id='h-dq'>Calidad y cobertura de los datos</h2>" +
      "<div class='detail-grid'>" +
      "<div class='detail-box'><div class='k'>Calidad de los datos</div><div class='v'>" + esc(dq.score != null ? fmtNum(dq.score) + "/100" : "—") + "</div></div>" +
      "<div class='detail-box'><div class='k'>Período analizado</div><div class='v'>" + esc(fmtDate(dq.period_start)) + " → " + esc(fmtDate(dq.period_end)) + "</div></div>" +
      "<div class='detail-box'><div class='k'>Registros</div><div class='v'>" + esc(fmtNum(dq.row_count, 0)) + "</div></div>" +
      "<div class='detail-box'><div class='k'>Fuente</div><div class='v'>" + esc(dash(dq.source)) + "</div></div>" +
      "</div><p class='muted'>" + esc(dash(dq.note)) + "</p>";
    if ((dq.warnings || []).length) {
      html += "<ul class='ev-list'>" + dq.warnings.map(function (w) { return "<li>⚠ " + esc(w) + "</li>"; }).join("") + "</ul>";
    }
    html += "</section>";

    /* ---- J. Limitaciones ---- */
    html += "<section class='section' aria-labelledby='h-lim'><h2 id='h-lim'>Limitaciones</h2><ul class='ev-list'>";
    (d.limitations || []).forEach(function (l) {
      html += "<li>" + esc(l.text || l) +
        (l.evidence_id ? " <span class='ev-id'>" + esc(l.evidence_id) + "</span>" : "") + "</li>";
    });
    html += "</ul></section>";

    /* ---- K. Próximos pasos sugeridos ---- */
    html += "<section class='section' aria-labelledby='h-next'><h2 id='h-next'>Qué revisar a continuación</h2><ol class='steps'>";
    (d.next_steps || []).forEach(function (s) {
      var link = "";
      if (s.ref_kind === "finding" && s.ref_id) link = " <a href='#/hallazgos/" + esc(s.ref_id) + "'>Ver análisis →</a>";
      else if (s.ref_kind === "opportunity") link = " <a href='#/oportunidades'>Ver oportunidades →</a>";
      else if (s.ref_kind === "advisor" && s.suggested_question) link = " <a href='" + advisorLink(s.suggested_question) + "'>Preguntar →</a>";
      html += "<li><strong>" + esc(dash(s.title)) + "</strong><br><span class='muted'>" + esc(dash(s.detail)) + "</span>" + link + "</li>";
    });
    html += "</ol></section>";

    /* ---- CTA Advisor ---- */
    html += "<section class='section advisor-cta' aria-labelledby='h-ask'><h2 id='h-ask'>Pregúntale a ZAYVERO</h2>" +
      "<p class='muted'>Profundiza en cualquier punto del diagnóstico con base en la evidencia.</p>" +
      "<div class='suggestions'>" + (d.suggested_advisor_questions || []).map(function (q) {
        return "<a class='suggestion' href='" + advisorLink(q) + "'>" + esc(q) + "</a>";
      }).join("") + "</div></section>";

    main.innerHTML = html;
    bindCardClicks(main);
  }

  function trendHTML(t) {
    var isObs = t.trend_type === "OBSERVED_TREND";
    return "<p><span class='tag " + (isObs ? "tag-observed" : "tag-projected") + "'>" +
      (isObs ? "Observado" : "Proyección") + "</span> <strong>" + esc(dash(t.direction)) + "</strong> " +
      esc(dash(t.period)) + "<br><span class='muted'>" + esc(dash(t.interpretation)) + "</span>" +
      "<br><span class='muted small'>" + esc(dash(t.note)) + "</span></p>";
  }

  /* ================= HALLAZGOS ================= */
  var TYPE_OPTIONS = ["all", "SALES_ANOMALY", "PRODUCT_ANOMALY", "CUSTOMER_ANOMALY",
    "PRICE_ANOMALY", "QUANTITY_ANOMALY", "TEMPORAL_ANOMALY"];
  var PERIOD_OPTIONS = [["all", "Todos los períodos"], ["last_90d", "Últimos 90 días"],
    ["last_6m", "Últimos 6 meses"], ["last_1y", "Último año"], ["older", "Anteriores"]];

  async function loadFindings() {
    var f = state.filters;
    var qs = "priority=" + encodeURIComponent(f.priority) +
      "&type=" + encodeURIComponent(f.type) +
      "&period=" + encodeURIComponent(f.period) +
      "&q=" + encodeURIComponent(f.q) +
      "&page=" + state.findings.page;
    state.findings = await api("/api/findings?" + qs);
  }

  function findingsHTML() {
    var f = state.findings, fl = state.filters;
    var html = "<h1 class='page-title'>Hallazgos</h1>" +
      "<p class='page-sub'>Comportamientos inusuales detectados en los datos de tu empresa, ordenados por prioridad e impacto.</p>";
    html += "<div class='filters' role='search'>" +
      "<label class='sr-only' for='f-priority'>Prioridad</label>" +
      "<select id='f-priority'>" + ["all", "URGENT", "IMPORTANT", "REVIEW", "MONITOR"].map(function (p) {
        return "<option value='" + p + "'" + (fl.priority === p ? " selected" : "") + ">" +
          (p === "all" ? "Todas las prioridades" : p) + "</option>";
      }).join("") + "</select>" +
      "<label class='sr-only' for='f-type'>Tipo</label>" +
      "<select id='f-type'>" + TYPE_OPTIONS.map(function (t) {
        return "<option value='" + t + "'" + (fl.type === t ? " selected" : "") + ">" +
          (t === "all" ? "Todos los tipos" : t) + "</option>";
      }).join("") + "</select>" +
      "<label class='sr-only' for='f-period'>Período</label>" +
      "<select id='f-period'>" + PERIOD_OPTIONS.map(function (o) {
        return "<option value='" + o[0] + "'" + (fl.period === o[0] ? " selected" : "") + ">" + o[1] + "</option>";
      }).join("") + "</select>" +
      "<label class='sr-only' for='f-q'>Buscar</label>" +
      "<input type='search' id='f-q' placeholder='Buscar producto, cliente, título…' value='" + esc(fl.q) + "'>" +
      "<button class='btn' id='f-go'>Filtrar</button></div>";

    html += "<p class='muted'>" + fmtNum(f.total, 0) + " hallazgos" +
      (f.by_priority ? " · URGENT " + (f.by_priority.URGENT || 0) + " · IMPORTANT " + (f.by_priority.IMPORTANT || 0) +
        " · REVIEW " + (f.by_priority.REVIEW || 0) + " · MONITOR " + (f.by_priority.MONITOR || 0) : "") + "</p>";

    if (!(f.items || []).length) {
      html += emptyHTML("Sin hallazgos disponibles.");
    }
    (f.items || []).forEach(function (v) {
      html += "<article class='finding-item' data-fid='" + esc(v.finding_id) + "' tabindex='0' role='button' " +
        "aria-label='Ver análisis: " + esc(v.title) + "'>" +
        "<h3>" + esc(v.title) + "</h3>" +
        "<div class='meta'><span class='prio prio-" + esc(v.business_priority) + "'>" + esc(v.business_priority) + "</span>" +
        "<span class='tag'>" + esc(dash(v.type_label)) + "</span>" +
        "<span>Impacto: <strong>" + esc(dash(v.impact_score)) + "</strong></span>" +
        "<span>Confianza: <strong>" + esc(dash(v.confidence_score)) + "</strong></span>" +
        "<span>Entidad: <strong>" + esc((v.entity || {}).label || "—") + "</strong></span></div>" +
        "<p class='muted'>" + esc(dash(v.business_explanation)).slice(0, 220) + "</p></article>";
    });

    var pages = Math.max(1, Math.ceil(f.total / f.per_page));
    html += "<nav class='pager' aria-label='Paginación'><button class='btn' id='pg-prev'" + (f.page <= 1 ? " disabled" : "") + ">← Anterior</button>" +
      "<span>Página " + f.page + " de " + pages + "</span>" +
      "<button class='btn' id='pg-next'" + (f.page >= pages ? " disabled" : "") + ">Siguiente →</button></nav>";
    return html;
  }

  async function renderFindings(main) {
    main.innerHTML = loadingHTML("Consultando evidencia…");
    try { await loadFindings(); }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    main.innerHTML = findingsHTML();
    function apply() {
      state.filters.priority = document.getElementById("f-priority").value;
      state.filters.type = document.getElementById("f-type").value;
      state.filters.period = document.getElementById("f-period").value;
      state.filters.q = document.getElementById("f-q").value;
      state.findings.page = 1;
      renderFindings(main);
    }
    document.getElementById("f-go").addEventListener("click", apply);
    document.getElementById("f-q").addEventListener("keydown", function (e) {
      if (e.key === "Enter") apply();
    });
    document.getElementById("pg-prev").addEventListener("click", function () {
      if (state.findings.page > 1) { state.findings.page--; renderFindings(main); }
    });
    document.getElementById("pg-next").addEventListener("click", function () {
      state.findings.page++; renderFindings(main);
    });
    bindCardClicks(main);
  }

  /* Detalle de hallazgo: secciones visualmente separadas para no confundir
   * hechos con hipótesis. */
  async function renderFindingDetail(fid, main) {
    main.innerHTML = loadingHTML("Consultando evidencia…");
    var d;
    try { d = await api("/api/findings/" + encodeURIComponent(fid)); }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    var f = d.finding || {};
    var ent = f.entity || {}, per = f.period || {};
    var html = "<a class='back-link' href='#/hallazgos'>← Volver a hallazgos</a>" +
      "<h1 class='page-title'>" + esc(f.title) + "</h1>" +
      "<p class='page-sub'><span class='prio prio-" + esc(f.business_priority) + "'>" + esc(f.business_priority) + "</span> " +
      "<span class='tag'>" + esc(dash(f.type_label)) + "</span> " +
      "<span class='tag'>Entidad: " + esc(ent.label || "—") + "</span> " +
      "<span class='tag'>Período: " + esc(fmtDate(per.start)) + " → " + esc(fmtDate(per.end)) + "</span></p>";

    html += "<section class='section' aria-labelledby='d-hecho'><h2 id='d-hecho'>Hecho observado</h2>" +
      "<div class='fact-box'><span class='lbl'>Hecho observado</span>" + esc(dash(f.statistical_explanation)) + "</div>" +
      "<p>" + esc(dash(f.business_explanation)) + "</p></section>";

    html += "<section class='section' aria-labelledby='d-imp'><h2 id='d-imp'>¿Por qué importa?</h2><div class='detail-grid'>" +
      "<div class='detail-box'><div class='k'>Impacto</div><div class='v'>" + esc(dash(f.impact_score)) + "</div>" +
      "<details class='metric-help'><summary>¿Qué significa?</summary><p>Qué tan importante podría ser este hallazgo para el negocio (0–100), considerando magnitud, desviación, alcance y calidad de datos.</p></details></div>" +
      "<div class='detail-box'><div class='k'>Confianza</div><div class='v'>" + esc(dash(f.confidence_score)) + "</div>" +
      "<details class='metric-help'><summary>¿Qué significa?</summary><p>Nivel de confianza estadística de la detección (0–100), según el historial disponible y la estabilidad del comportamiento.</p></details></div>" +
      "</div>" + (f.requires_review ? "<div class='warn-box' role='note'>Requiere revisión.</div>" : "") + "</section>";

    html += "<section class='section' aria-labelledby='d-ev'><h2 id='d-ev'>Evidencia</h2><div class='detail-grid'>" +
      "<div class='detail-box'><div class='k'>Valor observado</div><div class='v'>" + esc(fmtMoney(f.observed_value)) + "</div></div>" +
      "<div class='detail-box'><div class='k'>Valor esperado</div><div class='v'>" + esc(fmtMoney(f.expected_value)) + "</div></div>" +
      "<div class='detail-box'><div class='k'>Diferencia</div><div class='v'>" + esc(fmtMoney(f.difference)) + "</div></div>" +
      "<div class='detail-box'><div class='k'>Diferencia %</div><div class='v'>" + esc(fmtPct(f.percentage_difference)) + "</div></div>" +
      "</div><p class='muted'>Calidad de evidencia: " + esc(dash(f.evidence_quality)) + ". " +
      "La diferencia se presenta como desviación respecto al comportamiento esperado, nunca como pérdida o ganancia.</p>" +
      (f.data_quality_warning ? "<div class='warn-box' role='note'>" + esc(f.data_quality_warning) + "</div>" : "") + "</section>";

    html += "<section class='section' aria-labelledby='d-ctx'><h2 id='d-ctx'>Contexto</h2>";
    (f.facts || []).forEach(function (x) {
      html += "<div class='fact-box'><span class='lbl'>Hecho observado</span>" + esc(x.text || x) + "</div>";
    });
    (f.observations || []).forEach(function (x) {
      html += "<p>• " + esc(x.text || x) + "</p>";
    });
    if (f.recurrence) html += "<p><strong>Recurrencia:</strong> " + esc(f.recurrence) + "</p>";
    if (!((f.facts || []).length || (f.observations || []).length || f.recurrence)) {
      html += emptyHTML("Datos insuficientes para generar este análisis.");
    }
    html += "</section>";

    html += "<section class='section' aria-labelledby='d-hyp'><h2 id='d-hyp'>Posibles explicaciones</h2>";
    var hyps = f.possible_explanations || [];
    if (!hyps.length) html += emptyHTML("Sin hipótesis registradas.");
    hyps.forEach(function (x) {
      html += "<div class='hypo'><span class='lbl'>Posible explicación — hipótesis, no hecho</span>" + esc(x.text || x) + "</div>";
    });
    html += "</section>";

    html += "<section class='section' aria-labelledby='d-rec'><h2 id='d-rec'>Recomendaciones</h2><ul class='ev-list'>";
    (f.recommendations || []).forEach(function (r) {
      var kind = (r.kind || "RECOMMENDATION") === "ADVISORY_RECOMMENDATION" ? "Recomendación de asesoría" : "Recomendación existente";
      html += "<li><span class='tag tag-rec'>" + esc(kind) + "</span> " + esc(r.text || r) + "</li>";
    });
    if (!(f.recommendations || []).length) html += "<li class='muted'>Sin recomendaciones registradas.</li>";
    html += "</ul><p class='muted'>Las recomendaciones son sugerencias de revisión; no ejecutan ninguna acción.</p></section>";

    html += "<section class='section' aria-labelledby='d-lim'><h2 id='d-lim'>Limitaciones</h2>" +
      "<p class='muted'>Este análisis se basa en la evidencia disponible. ZAYVERO no afirma causas sin evidencia directa.</p></section>";

    main.innerHTML = html;
  }

  /* ================= OPORTUNIDADES ================= */
  async function renderOpportunities(main) {
    main.innerHTML = loadingHTML("Analizando información…");
    try { state.opportunities = (await api("/api/opportunities")).opportunities || []; }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    var html = "<h1 class='page-title'>Posibles oportunidades</h1>" +
      "<p class='page-sub'>Detectadas a partir de la evidencia. Se presentan como <strong>posibles oportunidades</strong>: no son una garantía de resultado.</p>";
    if (!state.opportunities.length) {
      html += emptyHTML("No hay oportunidades identificadas con los datos actuales.");
    }
    state.opportunities.forEach(function (o) { html += oppCardHTML(o); });
    main.innerHTML = html;
  }

  /* ================= PREDICCIONES ================= */
  async function renderPredictions(main) {
    main.innerHTML = loadingHTML("Analizando información…");
    try { state.predictions = await api("/api/predictions"); }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    var p = state.predictions, sum = p.summary || {}, vs = p.validation_summary || {};
    var html = "<h1 class='page-title'>Predicciones</h1>" +
      "<p class='page-sub'>Estimaciones basadas en el historial. Son <strong>proyecciones, no certezas</strong>. " +
      "Validación con resultados reales: " + esc(dash(vs.validated)) + " validadas, " +
      esc(dash(vs.pending)) + " pendientes de validación.</p>";
    html += "<div class='cards'>" +
      "<div class='card'><div class='cap'>Predicciones</div><div class='num'>" + esc(dash(sum.total_predictions)) + "</div></div>" +
      "<div class='card'><div class='cap'>Calidad alta/moderada</div><div class='num'>" + esc(dash(sum.high_quality)) + " / " + esc(dash(sum.moderate_quality)) + "</div>" + helpHTML("predictions") + "</div>" +
      "<div class='card'><div class='cap'>Incertidumbre alta</div><div class='num'>" + esc(dash(sum.high_uncertainty)) + "</div></div>" +
      "<div class='card'><div class='cap'>Riesgo de caída alto</div><div class='num'>" + esc(dash(sum.high_decline_risk)) + "</div></div>" +
      "<div class='card'><div class='cap'>Confianza promedio</div><div class='num'>" + esc(sum.average_confidence != null ? fmtNum(sum.average_confidence) : "—") + "</div></div>" +
      "</div>";
    var insights = p.insights || [];
    if (!insights.length) {
      html += emptyHTML("No existen predicciones disponibles.");
    }
    insights.forEach(function (i) { html += predCardHTML(i); });
    html += "<p class='muted'>Nota: las predicciones pendientes aún no pueden contrastarse con resultados reales. " +
      "ZAYVERO las presenta como referencia para planificación, no como cifras garantizadas.</p>";
    main.innerHTML = html;
  }

  /* ================= ADVISOR ================= */
  var SUGGESTIONS = [
    "¿Qué debería revisar primero?",
    "¿Cuáles son los problemas más importantes?",
    "¿Qué productos presentan comportamientos inusuales?",
    "¿Qué oportunidades debería investigar?",
    "¿Qué predicciones requieren atención?",
    "¿Qué información falta para analizar mejor mi empresa?",
  ];

  function uncertaintyLabel(u) {
    var lvl = (u && u.uncertainty_level) || "UNKNOWN";
    var map = { LOW: "Baja", MEDIUM: "Media", HIGH: "Alta", UNKNOWN: "No determinada" };
    return map[lvl] || lvl;
  }

  function recKindLabel(kind) {
    return kind === "ADVISORY_RECOMMENDATION" ? "Recomendación de asesoría" : "Recomendación existente";
  }

  function renderAdvisor(main, prefillQuestion) {
    if (!can("advisor.use")) {
      main.innerHTML = "<div class='error' role='alert'>Tu rol no tiene acceso al Advisor.</div>";
      return;
    }
    var html = "<h1 class='page-title'>Pregúntale a ZAYVERO</h1>" +
      "<p class='page-sub'>" + esc(T("advisor.explainer")) + "</p>" +
      "<div class='advisor-box'>" +
      "<h2 class='sub-h'>Preguntas sugeridas</h2>" +
      "<div class='suggestions'>" + SUGGESTIONS.map(function (s) {
        return "<button class='suggestion' data-q='" + esc(s) + "'>" + esc(s) + "</button>";
      }).join("") + "</div>" +
      "<div class='ask-row'><label class='sr-only' for='ask-input'>Tu pregunta</label>" +
      "<input id='ask-input' placeholder='Ej.: ¿Qué debería revisar primero?' maxlength='2000'>" +
      "<button class='btn' id='ask-btn'>Preguntar</button></div>" +
      "<div id='qa-list' aria-live='polite'></div></div>";
    main.innerHTML = html;

    function doAsk(q) {
      q = (q || "").trim();
      if (!q || state.asking) return;
      state.asking = true;
      var list = document.getElementById("qa-list");
      var btn = document.getElementById("ask-btn");
      var input = document.getElementById("ask-input");
      btn.disabled = true;
      if (input) input.disabled = true;
      var holder = document.createElement("div");
      holder.className = "qa";
      holder.innerHTML = "<div class='q'>" + esc(q) + "</div>" +
        "<div class='loading' role='status'><span class='spinner' aria-hidden='true'></span>" +
        "<span class='loading-msg'>Analizando evidencia…</span></div>";
      list.prepend(holder);
      /* Segunda fase de carga mientras el motor prepara la respuesta. */
      var stage2 = setTimeout(function () {
        var m = holder.querySelector(".loading-msg");
        if (m) m.textContent = "Preparando respuesta…";
      }, 1500);

      api("/api/advisor/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      }).then(function (r) {
        clearTimeout(stage2);
        holder.innerHTML = advisorAnswerHTML(q, r);
      }).catch(function (err) {
        clearTimeout(stage2);
        if (err.message === "auth") return;
        holder.innerHTML = "<div class='q'>" + esc(q) + "</div>" +
          "<div class='error' role='alert'>" + esc(friendlyError(err)) + "</div>";
      }).finally(function () {
        state.asking = false; btn.disabled = false;
        if (input) input.disabled = false;
      });
    }

    document.getElementById("ask-btn").addEventListener("click", function () {
      doAsk(document.getElementById("ask-input").value);
    });
    document.getElementById("ask-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") doAsk(e.target.value);
    });
    main.querySelectorAll(".suggestion").forEach(function (b) {
      b.addEventListener("click", function () { doAsk(b.getAttribute("data-q")); });
    });
    /* FASE 7B: pregunta sugerida desde el Diagnóstico (mismo flujo 5B → 5C). */
    if (prefillQuestion) {
      var inp = document.getElementById("ask-input");
      if (inp) { inp.value = prefillQuestion; }
      doAsk(prefillQuestion);
    }
  }

  function advisorAnswerHTML(q, r) {
    var conf = r.confidence || {};
    var unc = r.uncertainty || {};
    var h = "<div class='q'>" + esc(q) + "</div>" +
      "<section class='answer-section' aria-label='Respuesta'>" +
      "<h3 class='sub-h'>Respuesta</h3>" +
      "<div class='answer-text'>" + esc(r.answer || "") + "</div></section>";

    if ((r.key_points || []).length) {
      h += "<section class='answer-section' aria-label='Puntos clave'><h3 class='sub-h'>Puntos clave</h3><ul class='ev-list'>" +
        r.key_points.map(function (k) { return "<li>" + esc(k) + "</li>"; }).join("") + "</ul></section>";
    }

    h += "<section class='answer-section' aria-label='Evidencia utilizada'><h3 class='sub-h'>Evidencia utilizada</h3>";
    if ((r.evidence_used || []).length) {
      h += "<p>" + r.evidence_used.slice(0, 12).map(function (e) {
        return "<span class='ev-id'>" + esc(e) + "</span>";
      }).join(" ") + "</p>";
    } else {
      h += "<p class='muted'>No se encontró evidencia suficiente para esta pregunta.</p>";
    }
    h += "</section>";

    h += "<section class='answer-section' aria-label='Confianza e incertidumbre'><h3 class='sub-h'>Confianza e incertidumbre</h3>" +
      "<div class='meta'>" +
      "<span class='tag'>Confianza del contexto: <strong>" + esc(dash(conf.context_confidence_score)) + "</strong></span>" +
      "<span class='tag'>Incertidumbre: <strong>" + esc(uncertaintyLabel(unc)) + "</strong></span>" +
      "<span class='tag'>Estado: " + esc(dash(r.validation_status)) + "</span>" +
      (r.fallback ? "<span class='tag'>Respuesta directa del motor</span>" : "") + "</div>";
    if ((unc.missing_information || []).length) {
      h += "<p class='muted'>Información faltante: " + esc(unc.missing_information.slice(0, 3).join("; ")) + "</p>";
    }
    h += "</section>";

    var recs = r.advisor_recommendations || [];
    h += "<section class='answer-section' aria-label='Recomendaciones'><h3 class='sub-h'>Recomendaciones</h3>";
    if (recs.length) {
      h += "<ul class='ev-list'>" + recs.map(function (x) {
        return "<li><span class='tag tag-rec'>" + esc(recKindLabel(x.kind)) + "</span> " + esc(x.text || "") + "</li>";
      }).join("") + "</ul>";
    } else {
      h += "<p class='muted'>Sin recomendaciones registradas para esta pregunta.</p>";
    }
    h += "<p class='muted small'>Las recomendaciones son sugerencias de revisión; no ejecutan ninguna acción.</p></section>";

    if ((r.limitations || []).length) {
      h += "<section class='answer-section' aria-label='Limitaciones'><h3 class='sub-h'>Limitaciones</h3><ul class='ev-list'>" +
        r.limitations.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul></section>";
    }
    return h;
  }

  /* ================= AUDITORÍA ================= */
  async function renderAudit(main) {
    if (!can("audit.read")) {
      main.innerHTML = "<div class='error' role='alert'>Tu rol no tiene permiso para ver la auditoría.</div>";
      return;
    }
    main.innerHTML = loadingHTML("Analizando información…");
    try { state.audit = (await api("/api/audit")).events || []; }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    var html = "<h1 class='page-title'>Auditoría</h1>" +
      "<p class='page-sub'>Registro de eventos de seguridad de tu empresa. Nunca incluye contraseñas ni secretos.</p>";
    if (!state.audit.length) {
      html += emptyHTML("Sin eventos registrados.");
    } else {
      html += "<div class='section'><div class='table-scroll'><table class='audit'><thead><tr><th>Fecha</th><th>Evento</th><th>Recurso</th><th>Resultado</th></tr></thead><tbody>";
      state.audit.forEach(function (ev) {
        html += "<tr><td>" + esc(fmtDate(ev.timestamp)) + "</td><td>" + esc(dash(ev.action)) + "</td>" +
          "<td>" + esc(dash(ev.resource)) + "</td><td>" + esc(dash(ev.result)) + "</td></tr>";
      });
      html += "</tbody></table></div></div>";
    }
    main.innerHTML = html;
  }

  /* ================= FASE 7A: MI EMPRESA ================= */
  function stateMessageHTML(st, msg) {
    var map = {
      NO_DATA: "Agrega tus datos para comenzar.",
      DATA_UPLOADED: "Revisa tus datos y confirma para analizarlos.",
      PROCESSING: "Estamos procesando tus datos.",
      READY: "Análisis disponible.",
      ERROR: "Revisa el detalle para más información.",
      INSUFFICIENT_DATA: "Los datos no alcanzan para un análisis completo."
    };
    return "<div class='state-banner state-" + esc(st) + "' role='status'>" +
      "<strong>" + esc(msg || map[st] || st) + "</strong></div>";
  }

  async function renderSummaryNotReady(main, s) {
    var st = s.dataset_state || "NO_DATA";
    var html = "<h1 class='page-title'>Centro de Inteligencia</h1>" +
      stateMessageHTML(st, null);
    if (st === "NO_DATA") {
      html += "<section class='section'><h2>Comienza aquí</h2>" +
        "<p>Sube un reporte de tu empresa (CSV o XLSX) y ZAYVERO lo convertirá en inteligencia: hallazgos, oportunidades, predicciones y recomendaciones.</p>" +
        "<p><a class='btn' href='#/empresa'>Ir a Mi empresa</a></p></section>";
    } else if (st === "PROCESSING") {
      html += "<section class='section'><h2>Análisis en curso</h2>" +
        "<p>Estamos procesando tus datos. Esto puede tomar unos minutos según el tamaño del archivo. Puedes seguir trabajando; el resultado aparecerá aquí.</p></section>";
    } else if (st === "ERROR") {
      html += "<section class='section'><h2>No pudimos procesar estos datos</h2>" +
        "<p>Revisa el detalle en <a href='#/empresa'>Mi empresa</a> para saber qué ocurrió y cómo corregirlo.</p></section>";
    } else {
      html += "<section class='section'><p>Revisa el estado de tus datos en <a href='#/empresa'>Mi empresa</a>.</p></section>";
    }
    main.innerHTML = html;
  }

  /* ============ FASE 7C: configuración internacional ============ */
  function configSectionHTML(c) {
    var cfg = (c && c.config) || {};
    var html = "<section class='section' aria-labelledby='h-config'><h2 id='h-config'>" +
      esc(T("config.title")) + "</h2>" +
      "<p class='muted'>" + esc(T("config.subtitle")) + "</p>";
    if (!cfg.complete) {
      html += "<div class='notice' role='status'><strong>" + esc(T("config.incomplete")) +
        "</strong> — " + esc(T("config.incomplete_detail")) + "</div>";
    }
    function row(label, val) {
      return "<div><span>" + esc(label) + "</span><strong>" + esc(dash(val)) + "</strong></div>";
    }
    var langName = cfg.language === "en" ? "English" : (cfg.language === "es" ? "Español" : "");
    var curVal = cfg.currency || "";
    if (curVal && c.is_demo) curVal += " · " + T("currency.demo_note");
    var nfVal = cfg.number_format === "en" ? "1,234.56" : (cfg.number_format === "es" ? "1.234,56" : "");
    html += "<div class='kv'>" +
      row(T("config.name"), c.name) +
      row(T("config.country"), cfg.country) +
      row(T("config.industry"), cfg.industry) +
      row(T("config.language"), langName) +
      row(T("config.currency"), curVal) +
      row(T("config.timezone"), cfg.timezone) +
      row(T("config.date_format"), cfg.date_format) +
      row(T("config.number_format"), nfVal) +
      "</div>";
    if (c.is_demo) {
      html += "<p class='muted'>" + esc(T("config.demo_locked")) + "</p>";
    } else if (can("company.config")) {
      html += configFormHTML(cfg);
    } else {
      html += "<p class='muted'>" + esc(T("config.no_permission")) + "</p>";
    }
    html += "</section>";
    return html;
  }

  function configFormHTML(cfg) {
    function opt(v, label, sel) {
      return "<option value='" + v + "'" + (sel === v ? " selected" : "") + ">" + esc(label) + "</option>";
    }
    return "<form id='config-form'>" +
      "<div class='form-grid'>" +
      "<label>" + esc(T("config.country")) + "<input name='country' maxlength='80' value='" + esc(cfg.country || "") + "'></label>" +
      "<label>" + esc(T("config.industry")) + "<input name='industry' maxlength='80' value='" + esc(cfg.industry || "") + "'></label>" +
      "<label>" + esc(T("config.language")) + "<select name='language'>" +
      opt("", "—", cfg.language) + opt("es", "Español", cfg.language) + opt("en", "English", cfg.language) +
      "</select></label>" +
      "<label>" + esc(T("config.currency")) + "<input name='currency' maxlength='3' placeholder='DOP / USD / EUR' value='" + esc(cfg.currency || "") + "'></label>" +
      "<label>" + esc(T("config.timezone")) + "<input name='timezone' maxlength='64' placeholder='America/Santo_Domingo' value='" + esc(cfg.timezone || "") + "'></label>" +
      "<label>" + esc(T("config.date_format")) + "<select name='date_format'>" +
      opt("", "—", cfg.date_format) + opt("dd/mm/yyyy", "31/12/2026", cfg.date_format) +
      opt("mm/dd/yyyy", "12/31/2026", cfg.date_format) + opt("yyyy-mm-dd", "2026-12-31", cfg.date_format) +
      "</select></label>" +
      "<label>" + esc(T("config.number_format")) + "<select name='number_format'>" +
      opt("", "—", cfg.number_format) + opt("es", "1.234,56", cfg.number_format) + opt("en", "1,234.56", cfg.number_format) +
      "</select></label>" +
      "</div>" +
      "<button class='btn' type='submit'>" + esc(T("config.save")) + "</button>" +
      "</form><div id='config-msg' aria-live='polite'></div>";
  }

  async function submitConfigForm(form) {
    var msg = document.getElementById("config-msg");
    var data = {};
    new FormData(form).forEach(function (v, k) { data[k] = String(v).trim(); });
    try {
      var res = await api("/api/company/config", { method: "PUT", body: data });
      if (res && res.config) ZBI18N.setConfig(res.config);
      if (msg) msg.innerHTML = "<span class='ok'>" + esc(T("config.saved")) + "</span>";
      route();
    } catch (e) {
      var detail = (e.payload && e.payload.fields) ?
        e.payload.fields.map(function (f) { return f.field + ": " + f.error; }).join("; ") : "";
      if (msg) msg.innerHTML = "<span class='error'>" + esc(T("common.error") + (detail ? " " + detail : "")) + "</span>";
    }
  }

  async function renderWorkspace(main) {
    main.innerHTML = loadingHTML("Cargando tu empresa…");
    var ws;
    try { ws = await api("/api/workspace"); }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }
    var c = ws.company || {};
    var u = ws.user || {};
    var html = "<h1 class='page-title'>Mi empresa</h1>";
    if (c.is_demo) {
      html += "<div class='demo-banner' role='status'>DEMO · Datos de demostración — Esta cuenta utiliza datos de demostración, no datos reales de un cliente.</div>";
    }
    html += "<section class='section' aria-labelledby='h-emp'><h2 id='h-emp'>" + esc(c.name || "Mi empresa") + "</h2>" +
      "<div class='kv'>" +
      "<div><span>Estado</span><strong>" + esc(dash(c.status)) + "</strong></div>" +
      "<div><span>Usuario</span><strong>" + esc(u.name || u.email || "—") + "</strong></div>" +
      "<div><span>Rol</span><strong>" + esc((u.role || "").toUpperCase()) + "</strong></div>" +
      "</div></section>";

    /* FASE 7C: configuración internacional de la empresa. */
    html += configSectionHTML(c);

    /* Estado de los datos */
    html += "<section class='section' aria-labelledby='h-datos'><h2 id='h-datos'>Datos de la empresa</h2>" +
      stateMessageHTML(ws.dataset_state, ws.dataset_state_message);
    var ad = ws.active_dataset;
    if (ad) {
      html += "<div class='kv'>" +
        "<div><span>Registros</span><strong>" + esc(fmtNum(ad.row_count)) + "</strong></div>" +
        "<div><span>Período</span><strong>" + esc(fmtDate(ad.period_start)) + " → " + esc(fmtDate(ad.period_end)) + "</strong></div>" +
        "<div><span>Fuente</span><strong>" + esc(dash(ad.is_demo ? "Datos de demostración" : (ad.filename || ad.source))) + "</strong></div>" +
        "<div><span>Última actualización</span><strong>" + esc(fmtDate(ad.updated_at)) + "</strong></div>" +
        "</div>";
    }
    html += "</section>";

    /* Onboarding: pasos */
    html += "<section class='section' aria-labelledby='h-pasos'><h2 id='h-pasos'>Cómo funciona</h2>" +
      "<ol class='steps'>" +
      "<li><strong>Cuéntanos sobre tu empresa</strong><br>Tu empresa ya está registrada.</li>" +
      "<li><strong>Agrega tus datos</strong><br>Sube un reporte en CSV o XLSX.</li>" +
      "<li><strong>Revisamos tus datos</strong><br>Verificamos columnas, fechas y registros.</li>" +
      "<li><strong>Confirma</strong><br>Revisa el mapeo de columnas si es necesario.</li>" +
      "<li><strong>Analizamos tu empresa</strong><br>Procesamos tu información.</li>" +
      "<li><strong>Tu inteligencia está lista</strong><br>Explora el dashboard y el Advisor.</li>" +
      "</ol></section>";

    /* Carga de archivos */
    if (ws.can_upload) {
      html += "<section class='section' aria-labelledby='h-add'><h2 id='h-add'>Agregar datos</h2>" +
        "<p>Sube un reporte de tu empresa para que ZAYVERO pueda analizarlo. Formatos: CSV o XLSX (máx. 100 MB).</p>" +
        "<form id='upload-form'>" +
        "<label for='up-nombre'>Nombre del reporte</label>" +
        "<input id='up-nombre' name='nombre' type='text' maxlength='160' placeholder='Ej.: Ventas 2024'>" +
        "<label for='up-file'>Archivo</label>" +
        "<input id='up-file' name='file' type='file' accept='.csv,.xlsx' required>" +
        "<button class='btn' type='submit'>Subir reporte</button>" +
        "</form><div id='up-msg' aria-live='polite'></div></section>";
    }

    /* Lista de datasets */
    html += "<section class='section' aria-labelledby='h-list'><h2 id='h-list'>Reportes cargados</h2>";
    var list = ws.datasets || [];
    if (!list.length) {
      html += emptyHTML("Aún no hay reportes cargados.");
    } else {
      html += "<div class='cards'>";
      list.forEach(function (d) {
        html += "<a class='card ds-card' href='#/empresa/" + esc(d.dataset_id) + "'>" +
          "<div class='cap'>" + esc(d.nombre) + (d.is_demo ? " <span class='badge-demo'>DEMO</span>" : "") +
          (d.is_active ? " <span class='badge-active'>Activo</span>" : "") + "</div>" +
          "<div class='ds-status'>" + esc(d.status_label || d.status) + "</div>" +
          (d.row_count != null ? "<div class='ds-meta'>" + esc(fmtNum(d.row_count)) + " registros</div>" : "") +
          "</a>";
      });
      html += "</div>";
    }
    html += "</section>";
    main.innerHTML = html;

    var form = document.getElementById("upload-form");
    if (form) {
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        var msg = document.getElementById("up-msg");
        var fileInput = document.getElementById("up-file");
        var nombre = document.getElementById("up-nombre").value;
        if (!fileInput.files.length) { msg.innerHTML = "<div class='error'>Elige un archivo primero.</div>"; return; }
        msg.innerHTML = loadingHTML("Subiendo reporte…");
        var fd = new FormData();
        fd.append("file", fileInput.files[0]);
        fd.append("nombre", nombre);
        try {
          /* SEG-03: la subida multipart también lleva el token CSRF. */
          var upHeaders = {};
          if (state.csrfToken) upHeaders["X-CSRF-Token"] = state.csrfToken;
          var res = await fetch("/api/datasets/upload", { method: "POST", body: fd, credentials: "same-origin", headers: upHeaders });
          var payload = await res.json();
          if (!res.ok) { msg.innerHTML = "<div class='error' role='alert'>" + esc(payload.error || "No se pudo subir el archivo.") + "</div>"; return; }
          location.hash = "#/empresa/" + payload.dataset.dataset_id;
        } catch (err) {
          msg.innerHTML = "<div class='error' role='alert'>No se pudo subir el archivo.</div>";
        }
      });
    }
    /* FASE 7C: formulario de configuración internacional. */
    var cfgForm = document.getElementById("config-form");
    if (cfgForm) {
      cfgForm.addEventListener("submit", function (e) {
        e.preventDefault();
        submitConfigForm(cfgForm);
      });
    }
  }

  function warnIcon(t) {
    var icons = { missing_columns: "⚠", unmapped_columns: "⚠", nulls: "⚠", negative_quantities: "⚠", bad_prices: "⚠", duplicates: "⚠", cancellations: "ℹ" };
    return icons[t] || "⚠";
  }

  async function renderDatasetDetail(main, datasetId) {
    main.innerHTML = loadingHTML("Cargando reporte…");
    var d;
    try { d = (await api("/api/datasets/" + encodeURIComponent(datasetId))).dataset; }
    catch (e) { main.innerHTML = "<div class='error' role='alert'>" + esc(friendlyError(e)) + "</div>"; return; }

    var html = "<p><a href='#/empresa'>← Volver a Mi empresa</a></p>" +
      "<h1 class='page-title'>" + esc(d.nombre) + "</h1>" +
      stateMessageHTML(d.status, d.status_message || null);
    if (d.is_demo) html += "<div class='demo-banner'>DEMO · Datos de demostración</div>";

    var up = d.upload || {};
    html += "<section class='section'><h2>Archivo</h2><div class='kv'>" +
      "<div><span>Nombre</span><strong>" + esc(dash(up.filename)) + "</strong></div>" +
      "<div><span>Tamaño</span><strong>" + esc(up.size_bytes != null ? (up.size_bytes / 1048576).toFixed(1) + " MB" : "—") + "</strong></div>" +
      "<div><span>Formato</span><strong>" + esc((up.format || "").toUpperCase()) + "</strong></div>" +
      "<div><span>Fecha de carga</span><strong>" + esc(fmtDate(up.uploaded_at)) + "</strong></div>" +
      "<div><span>Registros</span><strong>" + esc(fmtNum(d.row_count)) + "</strong></div>" +
      "<div><span>Período</span><strong>" + esc(fmtDate(d.period_start)) + " → " + esc(fmtDate(d.period_end)) + "</strong></div>" +
      "</div></section>";

    /* Revisión de datos */
    html += "<section class='section' aria-labelledby='h-rev'><h2 id='h-rev'>Revisión de datos</h2><div id='review-box'>" + loadingHTML("Revisando datos…") + "</div></section>";

    /* Mapeo */
    html += "<section class='section' aria-labelledby='h-map'><h2 id='h-map'>Mapeo de columnas</h2><div id='mapping-box'>" + loadingHTML("Cargando…") + "</div></section>";

    /* Vista previa */
    html += "<section class='section' aria-labelledby='h-prev'><h2 id='h-prev'>Vista previa</h2><div id='preview-box'>" + loadingHTML("Cargando…") + "</div></section>";

    /* Acciones */
    html += "<section class='section' aria-labelledby='h-act'><h2 id='h-act'>Análisis</h2><div id='actions-box'></div><div id='act-msg' aria-live='polite'></div></section>";

    main.innerHTML = html;
    var dsId = d.dataset_id;

    /* --- revisión --- */
    try {
      var rev = (await api("/api/datasets/" + encodeURIComponent(dsId) + "/review")).review;
      var rh = "<ul class='checks'>";
      (rev.checks || []).forEach(function (c) {
        rh += "<li class='" + (c.ok ? "ok" : "bad") + "'>" + (c.ok ? "✓" : "✗") + " " + esc(c.label) +
          (c.detail ? " — " + esc(c.detail) : "") + "</li>";
      });
      rh += "</ul>";
      (rev.warnings || []).forEach(function (w) {
        rh += "<div class='warning'>" + warnIcon(w.type) + " <strong>" + esc(w.label) + ".</strong> " + esc(w.detail) + "</div>";
      });
      document.getElementById("review-box").innerHTML = rh;
    } catch (e) {
      document.getElementById("review-box").innerHTML = "<div class='error'>No se pudo completar la revisión.</div>";
    }

    /* --- mapeo --- */
    var mappingState = { suggestion: null, confirmed: !!(d.mapping && d.mapping.confirmed) };
    async function loadMapping() {
      try {
        var m = await api("/api/datasets/" + encodeURIComponent(dsId) + "/mapping");
        mappingState.suggestion = m.suggestion;
        mappingState.confirmed = !!(m.mapping && m.mapping.confirmed);
        renderMapping(m);
      } catch (e) {
        document.getElementById("mapping-box").innerHTML = "<div class='error'>No se pudo cargar el mapeo.</div>";
      }
    }
    function renderMapping(m) {
      var box = document.getElementById("mapping-box");
      var sug = m.suggestion || {};
      var cur = m.mapping || {};
      var h = "";
      if (mappingState.confirmed) {
        h += "<div class='ok-banner'>✓ Mapeo de columnas confirmado.</div>";
      }
      (sug.warnings || []).forEach(function (w) {
        h += "<div class='warning'>⚠ " + esc(w) + "</div>";
      });
      h += "<p>ZAYVERO sugiere la correspondencia de tus columnas. Revísala y confirma.</p><div class='table-scroll'><table class='mapping'><thead><tr><th>Campo</th><th>Tu columna</th><th>Estado</th></tr></thead><tbody>";
      (sug.suggestions || []).forEach(function (s) {
        var sel = "<select data-canonical='" + esc(s.canonical) + "' " + (mappingState.confirmed ? "disabled" : "") + ">";
        sel += "<option value=''>— sin asignar —</option>";
        (revCols(sug)).forEach(function (c) {
          var chosen = (cur.mapping && cur.mapping[s.canonical] === c) || s.source === c;
          sel += "<option value='" + esc(c) + "'" + (chosen ? " selected" : "") + ">" + esc(c) + "</option>";
        });
        sel += "</select>";
        h += "<tr><td><strong>" + esc(s.canonical) + "</strong>" + (sug.required && sug.required.indexOf(s.canonical) >= 0 ? " *" : "") +
          "<br><small>" + esc(s.note || "") + "</small></td><td>" + sel + "</td>" +
          "<td>" + (s.status === "suggested" ? "✓ Sugerido" : "⚠ Requiere revisión") + "</td></tr>";
      });
      h += "</tbody></table></div>";
      if (!mappingState.confirmed) {
        h += "<p><small>* Campos necesarios para el análisis.</small></p><button class='btn' id='map-confirm'>Confirmar mapeo</button> <span id='map-msg' aria-live='polite'></span>";
      }
      box.innerHTML = h;
      guardMappingDuplicates(box);
      var btn = document.getElementById("map-confirm");
      if (btn) btn.addEventListener("click", async function () {
        var selects = box.querySelectorAll("select[data-canonical]");
        var mp = {};
        selects.forEach(function (s2) { if (s2.value) mp[s2.getAttribute("data-canonical")] = s2.value; });
        document.getElementById("map-msg").textContent = "Confirmando…";
        try {
          var res = await api("/api/datasets/" + encodeURIComponent(dsId) + "/mapping", { method: "POST", body: { mapping: mp } });
          mappingState.confirmed = true;
          renderMapping({ suggestion: mappingState.suggestion, mapping: res.mapping });
          renderActions();
        } catch (e) {
          var msg = "Revisa el mapeo de columnas.";
          if (e.payload && e.payload.errors) msg = e.payload.errors.join(" ");
          else if (e.payload && e.payload.error) msg = e.payload.error;
          document.getElementById("map-msg").innerHTML = "<span class='error'>" + esc(msg) + "</span>";
        }
      });
    }
    function revCols(sug) {
      // TODAS las columnas originales del archivo (incluso sin sugerencia
      // automática), para permitir asignación manual. Fallback al
      // comportamiento anterior si el servidor no trae la lista.
      if (sug.columns && sug.columns.length) return sug.columns.slice();
      var set = {};
      (sug.suggestions || []).forEach(function (s) {
        if (s.source) set[s.source] = 1;
        (s.candidates || []).forEach(function (c) { set[c] = 1; });
      });
      return Object.keys(set);
    }
    function guardMappingDuplicates(box) {
      // Cada columna de origen solo puede asignarse a un campo destino.
      // Marca "en uso" las opciones ocupadas en otros selectores y revierte
      // cualquier cambio que cree un duplicado, con mensaje explicativo.
      var msg = document.getElementById("map-msg");
      function usedBy() {
        var m = {};
        box.querySelectorAll("select[data-canonical]").forEach(function (s2) {
          if (s2.value) m[s2.value] = s2.getAttribute("data-canonical");
        });
        return m;
      }
      function refresh() {
        var used = usedBy();
        box.querySelectorAll("select[data-canonical]").forEach(function (s2) {
          var mine = s2.getAttribute("data-canonical");
          Array.prototype.forEach.call(s2.options, function (o) {
            if (!o.value) return;
            if (!o.getAttribute("data-base")) o.setAttribute("data-base", o.text);
            var other = used[o.value] && used[o.value] !== mine;
            o.disabled = !!other;
            o.text = o.getAttribute("data-base") + (other ? " · en uso" : "");
          });
        });
      }
      box.querySelectorAll("select[data-canonical]").forEach(function (s2) {
        if (s2.disabled) return;
        s2.setAttribute("data-prev", s2.value);
        s2.addEventListener("change", function () {
          var val = s2.value, clash = null;
          box.querySelectorAll("select[data-canonical]").forEach(function (o2) {
            if (o2 !== s2 && o2.value && o2.value === val) {
              clash = o2.getAttribute("data-canonical");
            }
          });
          if (val && clash) {
            s2.value = s2.getAttribute("data-prev");
            if (msg) msg.innerHTML = "<span class='error'>La columna '" + esc(val) +
              "' ya está asignada a '" + esc(clash) +
              "'. Libérala primero o elige otra.</span>";
          } else {
            s2.setAttribute("data-prev", s2.value);
            if (msg) msg.textContent = "";
          }
          refresh();
        });
      });
      refresh();
      // Avisa si el estado inicial ya trae un duplicado (p. ej. mapeo previo).
      var seen = {}, dup = null;
      box.querySelectorAll("select[data-canonical]").forEach(function (s2) {
        if (s2.value) {
          if (seen[s2.value]) dup = s2.value;
          seen[s2.value] = 1;
        }
      });
      if (dup && msg) {
        msg.innerHTML = "<span class='error'>La columna '" + esc(dup) +
          "' aparece asignada a más de un campo. Corrígela antes de confirmar.</span>";
      }
    }

    /* --- vista previa --- */
    try {
      var pv = (await api("/api/datasets/" + encodeURIComponent(dsId) + "/preview")).preview;
      var ph = "<p class='muted'>" + esc(pv.note || "") + "</p><div class='table-scroll'><table class='preview'><thead><tr>";
      (pv.columns || []).forEach(function (c) { ph += "<th>" + esc(c) + "</th>"; });
      ph += "</tr></thead><tbody>";
      (pv.rows || []).forEach(function (r) {
        ph += "<tr>";
        (pv.columns || []).forEach(function (c) { ph += "<td>" + esc(dash(r[c])) + "</td>"; });
        ph += "</tr>";
      });
      ph += "</tbody></table></div>";
      document.getElementById("preview-box").innerHTML = ph;
    } catch (e) {
      document.getElementById("preview-box").innerHTML = emptyHTML("Vista previa no disponible.");
    }

    /* --- acciones --- */
    function renderActions() {
      var box = document.getElementById("actions-box");
      var h = "";
      if (d.status === "READY") {
        h += "<p>✓ Tus datos fueron analizados. " + (d.is_active ? "Este es el reporte activo que ves en el dashboard." : "") + "</p>";
        if (!d.is_active) h += "<button class='btn' id='act-activate'>Usar este reporte</button> ";
        h += "<button class='btn btn-secondary' id='act-reprocess'>Analizar de nuevo</button>";
      } else if (d.status === "PROCESSING") {
        h += "<p>" + loadingHTML("Estamos procesando tus datos…") + "</p><p class='muted'>Puedes seguir trabajando; el resultado aparecerá automáticamente.</p>";
        setTimeout(function () { if (location.hash.indexOf(dsId) >= 0) renderDatasetDetail(main, dsId); }, 8000);
      } else if (d.status === "ERROR") {
        h += "<div class='error' role='alert'>" + esc((d.processing && d.processing.error) || "No pudimos procesar estos datos.") + "</div>" +
          "<p>Qué puedes revisar: que el archivo tenga columnas de fecha, cantidad y precio, y que el formato sea CSV o XLSX. Si el problema continúa, vuelve a cargar el archivo.</p>" +
          (mappingState.confirmed ? "<button class='btn' id='act-process'>Intentar de nuevo</button>" : "");
      } else {
        h += mappingState.confirmed
          ? "<p>Todo listo. Inicia el análisis de tu empresa:</p><button class='btn' id='act-process'>Analizar datos</button>"
          : "<p class='muted'>Confirma el mapeo de columnas para continuar.</p>";
      }
      box.innerHTML = h;
      var bp = document.getElementById("act-process");
      if (bp) bp.addEventListener("click", async function () {
        document.getElementById("act-msg").innerHTML = loadingHTML("Iniciando análisis…");
        try {
          await api("/api/datasets/" + encodeURIComponent(dsId) + "/process", { method: "POST", body: {} });
          renderDatasetDetail(main, dsId);
        } catch (e) { document.getElementById("act-msg").innerHTML = "<div class='error'>" + esc(friendlyError(e)) + "</div>"; }
      });
      var br = document.getElementById("act-reprocess");
      if (br) br.addEventListener("click", async function () {
        document.getElementById("act-msg").innerHTML = loadingHTML("Iniciando análisis…");
        try {
          await api("/api/datasets/" + encodeURIComponent(dsId) + "/process", { method: "POST", body: {} });
          renderDatasetDetail(main, dsId);
        } catch (e) { document.getElementById("act-msg").innerHTML = "<div class='error'>" + esc(friendlyError(e)) + "</div>"; }
      });
      var ba = document.getElementById("act-activate");
      if (ba) ba.addEventListener("click", async function () {
        try {
          await api("/api/datasets/" + encodeURIComponent(dsId) + "/activate", { method: "POST", body: {} });
          renderDatasetDetail(main, dsId);
        } catch (e) { document.getElementById("act-msg").innerHTML = "<div class='error'>" + esc(friendlyError(e)) + "</div>"; }
      });
    }

    await loadMapping();
    renderActions();
  }

  /* ================= inicio ================= */
  (async function init() {
    try {
      state.me = await api("/api/me");
      await boot();
    } catch (e) {
      showLogin();
    }
  })();
})();
