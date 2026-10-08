/* ZAYVERO Business Dashboard MVP — frontend vanilla JS */
(function () {
  "use strict";

  var state = { priority: "all", type: "all", period: "all", q: "", offset: 0, total: 0 };
  var LIMIT = 24;
  var PRIORITY_ES = { URGENT: "Urgent", IMPORTANT: "Important", REVIEW: "Review", MONITOR: "Monitor" };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function money(v) {
    if (v == null) return "—";
    return "£" + Number(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pct(v) {
    if (v == null) return "—";
    return Number(v).toLocaleString("en-GB", { maximumFractionDigits: 1 }) + "%";
  }

  function num(v) {
    if (v == null) return "—";
    return Number(v).toLocaleString("en-GB");
  }

  function dateOnly(iso) {
    if (!iso) return "—";
    return String(iso).slice(0, 10);
  }

  function periodLabel(v) {
    var s = dateOnly(v.period_start), e = dateOnly(v.period_end);
    if (s === "—" && e === "—") return "—";
    return s === e ? s : s + " → " + e;
  }

  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* ---------- resumen superior ---------- */
  function loadMeta() {
    return fetchJSON("/api/meta").then(function (meta) {
      document.getElementById("count-urgent").textContent = num(meta.by_priority.URGENT);
      document.getElementById("count-important").textContent = num(meta.by_priority.IMPORTANT);
      document.getElementById("count-review").textContent = num(meta.by_priority.REVIEW);
      document.getElementById("dataset-note").textContent =
        "Fuente: " + meta.dataset + " — 1,067,371 registros procesados.";
      document.getElementById("footer-disclaimer").textContent = meta.demo_disclaimer;

      var pt = document.getElementById("f-priority");
      ["URGENT", "IMPORTANT", "REVIEW", "MONITOR"].forEach(function (p) {
        if (meta.by_priority[p] > 0) {
          var o = document.createElement("option");
          o.value = p; o.textContent = PRIORITY_ES[p] + " (" + meta.by_priority[p] + ")";
          pt.appendChild(o);
        }
      });
      var ty = document.getElementById("f-type");
      meta.types.forEach(function (t) {
        var o = document.createElement("option");
        o.value = t.id; o.textContent = t.label + " (" + t.count + ")";
        ty.appendChild(o);
      });
      var pe = document.getElementById("f-period");
      meta.periods.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p.id; o.textContent = p.label + " (" + p.count + ")";
        pe.appendChild(o);
      });
    });
  }

  function loadPanorama() {
    return fetchJSON("/api/panorama").then(function (p) {
      document.getElementById("pan-total").textContent = num(p.total);
      document.getElementById("pan-impact").textContent = p.avg_impact_score == null ? "—" : p.avg_impact_score;
      document.getElementById("pan-conf").textContent = p.avg_confidence == null ? "—" : p.avg_confidence;
      document.getElementById("pan-rec").textContent = num(p.recurrent);
      document.getElementById("pan-iso").textContent = num(p.isolated);
    });
  }

  /* ---------- lista de hallazgos ---------- */
  function queryString(offset) {
    var p = new URLSearchParams();
    if (state.priority !== "all") p.set("priority", state.priority);
    if (state.type !== "all") p.set("type", state.type);
    if (state.period !== "all") p.set("period", state.period);
    if (state.q) p.set("q", state.q);
    p.set("limit", LIMIT); p.set("offset", offset);
    return p.toString();
  }

  function badgeClass(prio) { return (prio || "").toLowerCase(); }

  function cardHTML(v) {
    var lowEv = v.evidence_quality === "LOW";
    return (
      '<article class="card" tabindex="0" data-id="' + esc(v.finding_id) + '">' +
        '<div class="card-top">' +
          '<span class="badge ' + badgeClass(v.business_priority) + '">' + esc(PRIORITY_ES[v.business_priority] || v.business_priority) + '</span>' +
          '<span class="badge type">' + esc(v.type_label) + '</span>' +
          (lowEv ? '<span class="badge low">Evidence: LOW</span>' : '') +
          (v.requires_review ? '<span class="badge rec">Requires review</span>' : '') +
        '</div>' +
        '<h3>' + esc(v.title) + '</h3>' +
        '<div class="entity">' + esc(v.entity_label || "—") + ' · ' + esc(periodLabel(v)) + '</div>' +
        '<p class="explain clamp">' + esc(v.business_explanation || "") + '</p>' +
        '<div class="card-metrics">' +
          '<div class="metric"><div class="m-v">' + (v.impact_score == null ? "—" : v.impact_score) + '</div><div class="m-l">Impact score</div></div>' +
          '<div class="metric"><div class="m-v">' + (v.confidence_score == null ? "—" : v.confidence_score) + '</div><div class="m-l">Confidence</div></div>' +
          '<div class="metric"><div class="m-v">' + money(v.difference) + '</div><div class="m-l">Desviación</div></div>' +
        '</div>' +
        '<div class="card-foot"><span>' + num(v.n_recommendations) + ' recomendaciones de revisión</span>' +
        '<span>' + esc(v.finding_id || "") + '</span></div>' +
      '</article>'
    );
  }

  function loadFindings(reset) {
    if (reset) { state.offset = 0; document.getElementById("cards").innerHTML = ""; }
    return fetchJSON("/api/findings?" + queryString(state.offset)).then(function (res) {
      state.total = res.total;
      var cards = document.getElementById("cards");
      cards.insertAdjacentHTML("beforeend", res.items.map(cardHTML).join(""));
      document.getElementById("results-count").textContent =
        "Mostrando " + Math.min(state.offset + res.items.length, res.total) +
        " de " + num(res.total) + " hallazgos";
      state.offset += res.items.length;
      document.getElementById("btn-more").style.display =
        state.offset < res.total ? "" : "none";
      bindCards();
    });
  }

  function bindCards() {
    document.querySelectorAll(".card").forEach(function (el) {
      if (el.dataset.bound) return;
      el.dataset.bound = "1";
      el.addEventListener("click", function () { openDetail(el.dataset.id); });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(el.dataset.id); }
      });
    });
  }

  /* ---------- detalle ---------- */
  function evRow(label, value) {
    return '<tr><th>' + esc(label) + '</th><td>' + esc(value) + '</td></tr>';
  }

  function block(kind, text, source) {
    if (!text) return "";
    var tag = kind === "FACT" ? "Hecho" : kind === "OBSERVATION" ? "Observación" : kind;
    return '<div class="fact"><div class="fact-tag">' + esc(tag.toUpperCase()) + '</div>' +
      esc(text) + (source ? '<div style="font-size:.78rem;color:var(--muted);margin-top:4px">Fuente: ' + esc(source) + '</div>' : "") + '</div>';
  }

  function renderDetail(v) {
    var lowEv = v.evidence_quality === "LOW";
    var h = '<div class="meta-row">' +
      '<span class="badge ' + badgeClass(v.business_priority) + '">' + esc(PRIORITY_ES[v.business_priority] || v.business_priority) + '</span>' +
      '<span class="badge type">' + esc(v.type_label) + '</span>' +
      '<span class="badge rec">' + esc(v.finding_id || "") + '</span>' +
      (lowEv ? '<span class="badge low">Evidence: LOW</span>' : "") +
      (v.requires_review ? '<span class="badge rec">Requires review</span>' : "") +
      '</div>' +
      '<h1 id="d-title">' + esc(v.title) + '</h1>' +
      '<p style="color:var(--muted);font-size:.9rem">' +
      esc((v.entity || {}).label || "—") + ' · ' +
      esc(dateOnly((v.period || {}).start) + " → " + dateOnly((v.period || {}).end)) + '</p>';

    if (lowEv) {
      h += '<div class="notice low-evidence"><div class="notice-title">Evidence: LOW</div>' +
        'ZAYVERO no dispone de suficiente historial para contextualizar este hallazgo. ' +
        esc(v.evidence_quality_note || "") + '</div>';
    }
    if (v.data_quality_warning) {
      h += '<div class="notice"><div class="notice-title">Advertencia de calidad de datos</div>' +
        esc(v.data_quality_warning) + '</div>';
    }
    if (v.requires_review && v.requires_review_reason) {
      h += '<div class="notice"><div class="notice-title">Requires review</div>' +
        esc(v.requires_review_reason) + '</div>';
    }

    // ¿QUÉ DETECTAMOS?
    h += '<h2>¿Qué detectamos?</h2>';
    if (v.business_explanation) h += block("FACT", v.business_explanation, "FASE 2B — Business Finding");
    if (v.statistical_explanation) h += block("FACT", v.statistical_explanation, "FASE 2A — motor estadístico");

    // ¿POR QUÉ IMPORTA?
    h += '<h2>¿Por qué importa?</h2>';
    h += '<table class="ev-table">' +
      evRow("Prioridad empresarial", PRIORITY_ES[v.business_priority] || v.business_priority) +
      evRow("Impact score", v.impact_score == null ? "—" : v.impact_score + " / 100") +
      evRow("Confidence", v.confidence_score == null ? "—" : v.confidence_score + " / 100") +
      evRow("Calidad de evidencia", v.evidence_quality || "—") +
      '</table>';

    // EVIDENCIA
    h += '<h2>Evidencia</h2><table class="ev-table">' +
      evRow("Valor observado", money(v.observed_value)) +
      evRow("Valor esperado", money(v.expected_value)) +
      evRow("Desviación", money(v.difference)) +
      evRow("Desviación porcentual", pct(v.percentage_difference)) +
      evRow("Confidence", v.confidence_score == null ? "—" : v.confidence_score + " / 100") +
      evRow("Evidence quality", v.evidence_quality || "—") +
      '</table>';

    // CONTEXTO (2C)
    if (v.context_status === "insufficient_context") {
      h += '<h2>Contexto</h2><div class="notice low-evidence"><div class="notice-title">Contexto insuficiente</div>' +
        'ZAYVERO no dispone de suficiente historial para contextualizar este hallazgo. ' +
        esc(v.evidence_quality_note || "No existe evidencia suficiente para determinar la causa.") + '</div>';
    } else {
      h += '<h2>Contexto</h2>';
      (v.facts || []).forEach(function (f) { h += block(f.kind || "FACT", f.text, f.source); });
      (v.observations || []).forEach(function (o) { h += block(o.kind || "OBSERVATION", o.text, o.basis); });
      var hc = v.historical_context || {};
      if (hc.before || hc.during || hc.after) {
        h += '<h2 style="font-size:.92rem">Antes / durante / después</h2><table class="ev-table"><tr><th></th><th>Antes</th><th>Durante</th><th>Después</th></tr>';
        [["revenue", "Revenue"], ["n_trx", "Transacciones"], ["avg_daily", "Promedio diario"]].forEach(function (row) {
          h += '<tr><th>' + row[1] + '</th>' +
            [hc.before, hc.during, hc.after].map(function (s) {
              if (!s) return "<td>—</td>";
              var val = s[row[0]];
              return "<td>" + (val == null ? "—" : (row[0] === "revenue" || row[0] === "avg_daily" ? money(val) : num(Math.round(val)))) + "</td>";
            }).join("") + '</tr>';
        });
        h += '</table>';
      }
      var tc = v.trend_context || {};
      if (tc.trend) h += block("OBSERVATION", "Tendencia: " + tc.trend + (tc.note ? ". " + tc.note : ""));
      var rd = v.recurrence_detail || {};
      if (rd.note) h += block("OBSERVATION", "Recurrencia (" + (v.recurrence || "desconocida") + "): " + rd.note);
      var cc = v.concentration || {};
      if (cc.n_trx) {
        h += block("OBSERVATION", "Concentración: " + num(cc.n_trx) + " transacciones; " +
          (cc.concentrated ? "evento concentrado" : "evento no concentrado") + ".");
      }
    }

    // POSIBLES EXPLICACIONES — siempre como hipótesis
    h += '<h2>Posibles explicaciones</h2>';
    if ((v.possible_explanations || []).length === 0) {
      h += '<p style="color:var(--muted)">No existe evidencia suficiente para determinar la causa.</p>';
    } else {
      (v.possible_explanations || []).forEach(function (e) {
        h += '<div class="hypothesis"><div class="hypo-tag">POSIBLE EXPLICACIÓN — HIPÓTESIS, NO HECHO</div>' +
          esc(e.text) + (e.basis ? '<div style="font-size:.78rem;color:var(--muted);margin-top:4px">Base: ' + esc(e.basis) + '</div>' : "") + '</div>';
      });
    }

    // QUÉ REVISAR
    h += '<h2>Qué revisar</h2>';
    if ((v.recommendations || []).length === 0) {
      h += '<p style="color:var(--muted)">Sin recomendaciones disponibles.</p>';
    } else {
      (v.recommendations || []).forEach(function (r) {
        h += '<div class="rec"><div class="rec-tag">RECOMENDACIÓN DE REVISIÓN</div>' +
          esc(r.text) + '</div>';
      });
    }
    h += '<p style="font-size:.82rem;color:var(--muted);margin-top:18px">ZAYVERO no ejecuta acciones: ' +
      'estas recomendaciones son de revisión humana únicamente.</p>';

    return h;
  }

  function openDetail(fid) {
    fetchJSON("/api/findings/" + encodeURIComponent(fid))
      .then(function (v) {
        document.getElementById("detail-body").innerHTML = renderDetail(v);
        document.getElementById("detail-overlay").hidden = false;
        document.body.style.overflow = "hidden";
        document.getElementById("detail-overlay").scrollTop = 0;
      })
      .catch(function () { alert("No se pudo cargar el detalle del hallazgo."); });
  }

  function closeDetail() {
    document.getElementById("detail-overlay").hidden = true;
    document.body.style.overflow = "";
  }

  /* ---------- init ---------- */
  function debounce(fn, ms) {
    var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadMeta().then(loadFindings).catch(function () {
      document.getElementById("results-count").textContent =
        "No se pudieron cargar los datos del dashboard.";
    });
    loadPanorama();
    document.getElementById("btn-more").addEventListener("click", function () { loadFindings(false); });
    document.getElementById("f-priority").addEventListener("change", function (e) { state.priority = e.target.value; loadFindings(true); });
    document.getElementById("f-type").addEventListener("change", function (e) { state.type = e.target.value; loadFindings(true); });
    document.getElementById("f-period").addEventListener("change", function (e) { state.period = e.target.value; loadFindings(true); });
    document.getElementById("f-search").addEventListener("input", debounce(function (e) { state.q = e.target.value; loadFindings(true); }, 300));
    document.getElementById("detail-close").addEventListener("click", closeDetail);
    document.getElementById("detail-overlay").addEventListener("click", function (e) {
      if (e.target === this) closeDetail();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !document.getElementById("detail-overlay").hidden) closeDetail();
    });
  });
})();
