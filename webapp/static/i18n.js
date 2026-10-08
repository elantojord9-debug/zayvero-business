/* FASE 7C — Internacionalización del frontend (es/en, extensible a pt).
 *
 * Catálogo de textos de la interfaz + formateo regional. La lógica de
 * negocio NO se duplica por idioma: los datos analíticos conservan su
 * idioma de origen; aquí solo vive la cáscara de la interfaz.
 *
 * Uso desde app.js:
 *   ZBI18N.setLang("en")            — fija el idioma de la sesión
 *   ZBI18N.t("nav.diagnostic")      — texto de interfaz
 *   ZBI18N.fmtMoney(1234.5)         — moneda de la empresa (config)
 *   ZBI18N.fmtNum(1234.56, 2)       — número regional
 *   ZBI18N.fmtDate("2026-10-08")    — fecha regional
 */
(function (global) {
  "use strict";

  var STRINGS = {
    es: {
      "app.subtitle": "Inteligencia de tu empresa",
      "nav.workspace": "Mi empresa",
      "nav.diagnostic": "Diagnóstico",
      "nav.summary": "Resumen",
      "nav.findings": "Hallazgos",
      "nav.opportunities": "Oportunidades",
      "nav.predictions": "Predicciones",
      "nav.advisor": "Advisor",
      "nav.audit": "Auditoría",
      "login.title": "Iniciar sesión",
      "login.subtitle": "Accede a la inteligencia de tu empresa.",
      "login.email": "Correo electrónico",
      "login.password": "Contraseña",
      "login.submit": "Entrar",
      "logout": "Cerrar sesión",
      "company.current": "Empresa actual",
      "demo.badge": "DEMO · Datos de demostración",
      "config.title": "Configuración de la empresa",
      "config.subtitle": "Identidad y preferencias regionales de tu empresa.",
      "config.name": "Nombre de la empresa",
      "config.country": "País",
      "config.industry": "Industria",
      "config.language": "Idioma",
      "config.currency": "Moneda",
      "config.timezone": "Zona horaria",
      "config.date_format": "Formato de fecha",
      "config.number_format": "Formato numérico",
      "config.save": "Guardar configuración",
      "config.saved": "Configuración guardada.",
      "config.incomplete": "Configuración incompleta",
      "config.incomplete_detail": "Completa los campos pendientes para una mejor experiencia.",
      "config.no_permission": "Solo OWNER o ADMIN pueden modificar la configuración.",
      "config.demo_locked": "La empresa demo no se puede modificar.",
      "currency.missing": "Moneda no configurada",
      "currency.demo_note": "Moneda del dataset de demostración (UCI Online Retail II).",
      "diagnostic.title": "Diagnóstico Ejecutivo",
      "diagnostic.subtitle": "Una visión basada en los datos disponibles de tu empresa.",
      "advisor.cta": "Pregúntale a ZAYVERO",
      "advisor.explainer": "Pregúntale a ZAYVERO sobre los datos y hallazgos de tu empresa.",
      "common.loading": "Cargando…",
      "common.error": "Ocurrió un error. Intenta de nuevo.",
      /* FASE 8 — experiencia de producto */
      "nav.inicio": "Inicio",
      "product.hero.main": "Convierte los datos de tu empresa en inteligencia para detectar problemas, encontrar oportunidades, anticipar riesgos y tomar mejores decisiones.",
      "product.hero.secondary": "No reemplazamos tu sistema. Agregamos una capa de inteligencia sobre los datos que ya tienes.",
      "product.hero.ready": "Tu análisis está listo.",
      "product.source.csv": "Archivos CSV",
      "product.source.xlsx": "Archivos Excel / XLSX",
      "product.source.reports": "Reportes empresariales autorizados",
      "product.source.future": "Integraciones oficiales (en el futuro)",
      "product.source.title": "ZAYVERO trabaja con",
      "product.source.note": "Solo trabajamos con los datos que nos proporcionas o autorizas. No prometemos integraciones que aún no existen.",
      "product.state.title": "Estado actual",
      "product.state.no_data": "Tu empresa todavía no tiene datos cargados.",
      "product.state.processing": "Estamos procesando tus datos.",
      "product.state.ready": "Los datos están listos. ZAYVERO puede analizar tu empresa.",
      "product.state.error": "Hubo un problema al procesar los datos.",
      "product.state.incomplete_configuration": "Completa la configuración de tu empresa para continuar.",
      "product.state.what_now": "Qué puedes hacer ahora",
      "product.action.complete_config": "Completar la configuración de la empresa",
      "product.action.upload_data": "Subir tu primer reporte",
      "product.action.wait": "Esperar a que termine el procesamiento",
      "product.action.view_diagnostic": "Ver mi Diagnóstico Ejecutivo",
      "product.action.open_intelligence": "Abrir el Centro de Inteligencia",
      "product.action.review_data": "Revisar los datos en Mi empresa",
      "onboarding.title": "Primeros pasos",
      "onboarding.subtitle": "Sigue estos pasos para llegar a tu primer diagnóstico. Siempre sabrás en qué paso estás.",
      "onboarding.progress": "Progreso",
      "onboarding.step.done": "Completado",
      "onboarding.step.current": "Paso actual",
      "onboarding.step.pending": "Pendiente",
      "onboarding.step1.title": "Información de la empresa",
      "onboarding.step1.desc": "Cuéntanos el nombre de tu empresa.",
      "onboarding.step2.title": "Configuración regional",
      "onboarding.step2.desc": "País, idioma, moneda, zona horaria y formatos.",
      "onboarding.step3.title": "Carga de datos",
      "onboarding.step3.desc": "Sube un reporte de tu empresa en CSV o XLSX.",
      "onboarding.step4.title": "Validación",
      "onboarding.step4.desc": "Revisamos columnas, fechas y registros.",
      "onboarding.step5.title": "Mapeo",
      "onboarding.step5.desc": "Confirma el mapeo de columnas si es necesario.",
      "onboarding.step6.title": "Procesamiento",
      "onboarding.step6.desc": "Analizamos la información de tu empresa.",
      "onboarding.step7.title": "Primer diagnóstico",
      "onboarding.step7.desc": "Tu Diagnóstico Ejecutivo está listo.",
      "sequence.title": "¿En qué va mi empresa?",
      "sequence.config_complete": "Configuración completa",
      "sequence.data_received": "Datos recibidos",
      "sequence.data_processed": "Datos procesados",
      "sequence.analysis_available": "Análisis disponible",
      "sequence.diagnostic_ready": "Diagnóstico listo",
      "dataflow.title": "Qué pasa después de cargar tus datos",
      "dataflow.received": "Archivo recibido",
      "dataflow.validation": "Validación",
      "dataflow.mapping": "Mapeo",
      "dataflow.processing": "Procesamiento",
      "dataflow.intelligence": "Inteligencia",
      "dataflow.diagnostic": "Diagnóstico",
      "hierarchy.title": "Tu recorrido en ZAYVERO",
      "hierarchy.diagnostic": "Diagnóstico Ejecutivo",
      "hierarchy.diagnostic_desc": "El primer gran resultado: el estado de tu empresa de un vistazo.",
      "hierarchy.intelligence": "Centro de Inteligencia",
      "hierarchy.intelligence_desc": "Tu espacio operativo diario: métricas y señales.",
      "hierarchy.findings": "Hallazgos, riesgos, oportunidades, predicciones",
      "hierarchy.findings_desc": "Explora la evidencia detrás de cada conclusión.",
      "hierarchy.advisor": "Advisor",
      "hierarchy.advisor_desc": "Haz preguntas sobre tus datos y hallazgos.",
      "benefits.title": "¿Qué obtiene tu empresa con ZAYVERO?",
      "benefits.unusual_behavior": "Detectar comportamientos inusuales en tus datos",
      "benefits.prioritize": "Priorizar los problemas que requieren atención",
      "benefits.opportunities": "Encontrar posibles oportunidades que vale la pena revisar",
      "benefits.trends": "Analizar tendencias en el tiempo",
      "benefits.ask": "Consultar la información de tu empresa",
      "benefits.evidence_recs": "Obtener recomendaciones basadas en evidencia para revisar",
      "benefits.executive_diagnostic": "Tener un diagnóstico ejecutivo de tu empresa",
      "benefits.note": "ZAYVERO presenta evidencia y sugerencias de revisión. No garantiza ventas, ahorros ni resultados.",
      "demo.explore.title": "Explorar demo",
      "demo.explore.desc": "Mira cómo funciona ZAYVERO con datos de demostración (UCI Online Retail II). Los datos demo nunca se mezclan con datos de clientes.",
      "demo.explore.cta": "Explorar demo",
      "demo.explore.active": "Estás explorando la demo.",
      "demo.return": "← Volver a mi empresa",
      "demo.enter.failed": "No se pudo abrir la demo.",
      "demo.exit.failed": "No se pudo volver a tu empresa.",
      "demo.exit.session_expired": "Tu sesión anterior expiró. Inicia sesión de nuevo.",
      "demo.badge": "DEMO · Datos de demostración",
      "plans.title": "Planes",
      "plans.subtitle": "Elige el plan que se ajuste a tu empresa.",
      "plans.price.not_definitive": "Por definir — no definitivo",
      "plans.starter.name": "Inicial",
      "plans.starter.f1": "Diagnóstico ejecutivo",
      "plans.starter.f2": "Centro de inteligencia",
      "plans.starter.f3": "1 empresa, 1 usuario",
      "plans.professional.name": "Profesional",
      "plans.professional.f1": "Todo lo del plan Inicial",
      "plans.professional.f2": "Advisor con evidencia",
      "plans.professional.f3": "Hallazgos, riesgos y predicciones",
      "plans.professional.f4": "Hasta 5 usuarios",
      "plans.enterprise.name": "Empresarial",
      "plans.enterprise.f1": "Todo lo del plan Profesional",
      "plans.enterprise.f2": "Múltiples empresas",
      "plans.enterprise.f3": "Configuración regional por empresa",
      "plans.enterprise.f4": "Soporte prioritario",
      "plans.cta": "Elegir plan",
      "plans.note": "Los planes se muestran solo como presentación. Por ahora no se procesan pagos."
    },
    en: {
      "app.subtitle": "Your business intelligence",
      "nav.workspace": "My company",
      "nav.diagnostic": "Diagnostic",
      "nav.summary": "Summary",
      "nav.findings": "Findings",
      "nav.opportunities": "Opportunities",
      "nav.predictions": "Predictions",
      "nav.advisor": "Advisor",
      "nav.audit": "Audit",
      "login.title": "Sign in",
      "login.subtitle": "Access your company's intelligence.",
      "login.email": "Email",
      "login.password": "Password",
      "login.submit": "Sign in",
      "logout": "Sign out",
      "company.current": "Current company",
      "demo.badge": "DEMO · Demonstration data",
      "config.title": "Company settings",
      "config.subtitle": "Your company's identity and regional preferences.",
      "config.name": "Company name",
      "config.country": "Country",
      "config.industry": "Industry",
      "config.language": "Language",
      "config.currency": "Currency",
      "config.timezone": "Time zone",
      "config.date_format": "Date format",
      "config.number_format": "Number format",
      "config.save": "Save settings",
      "config.saved": "Settings saved.",
      "config.incomplete": "Incomplete configuration",
      "config.incomplete_detail": "Complete the pending fields for a better experience.",
      "config.no_permission": "Only OWNER or ADMIN can change settings.",
      "config.demo_locked": "The demo company cannot be modified.",
      "currency.missing": "Currency not configured",
      "currency.demo_note": "Demonstration dataset currency (UCI Online Retail II).",
      "diagnostic.title": "Executive Diagnostic",
      "diagnostic.subtitle": "A view based on your company's available data.",
      "advisor.cta": "Ask ZAYVERO",
      "advisor.explainer": "Ask ZAYVERO about your company's data and findings.",
      "common.loading": "Loading…",
      "common.error": "Something went wrong. Please try again.",
      /* FASE 8 — product experience */
      "nav.inicio": "Home",
      "product.hero.main": "Turn your company's data into intelligence to detect problems, find opportunities, anticipate risks and make better decisions.",
      "product.hero.secondary": "We don't replace your system. We add an intelligence layer on top of the data you already have.",
      "product.hero.ready": "Your analysis is ready.",
      "product.source.csv": "CSV files",
      "product.source.xlsx": "Excel / XLSX files",
      "product.source.reports": "Authorized business reports",
      "product.source.future": "Official integrations (coming in the future)",
      "product.source.title": "ZAYVERO works with",
      "product.source.note": "We only work with data you provide or authorize. We don't promise integrations that don't exist yet.",
      "product.state.title": "Current status",
      "product.state.no_data": "Your company doesn't have data loaded yet.",
      "product.state.processing": "We are processing your data.",
      "product.state.ready": "The data is ready. ZAYVERO can analyze your company.",
      "product.state.error": "There was a problem processing the data.",
      "product.state.incomplete_configuration": "Complete your company's configuration to continue.",
      "product.state.what_now": "What you can do now",
      "product.action.complete_config": "Complete company configuration",
      "product.action.upload_data": "Upload your first report",
      "product.action.wait": "Wait for processing to finish",
      "product.action.view_diagnostic": "View my Executive Diagnostic",
      "product.action.open_intelligence": "Open the Intelligence Center",
      "product.action.review_data": "Review the data in My company",
      "onboarding.title": "Getting started",
      "onboarding.subtitle": "Follow these steps to get your first diagnostic. You always know where you are.",
      "onboarding.progress": "Progress",
      "onboarding.step.done": "Completed",
      "onboarding.step.current": "Current step",
      "onboarding.step.pending": "Pending",
      "onboarding.step1.title": "Company information",
      "onboarding.step1.desc": "Tell us your company's name.",
      "onboarding.step2.title": "Regional configuration",
      "onboarding.step2.desc": "Country, language, currency, time zone and formats.",
      "onboarding.step3.title": "Data upload",
      "onboarding.step3.desc": "Upload a CSV or XLSX report from your company.",
      "onboarding.step4.title": "Validation",
      "onboarding.step4.desc": "We check columns, dates and records.",
      "onboarding.step5.title": "Mapping",
      "onboarding.step5.desc": "Confirm the column mapping if needed.",
      "onboarding.step6.title": "Processing",
      "onboarding.step6.desc": "We analyze your company's information.",
      "onboarding.step7.title": "First diagnostic",
      "onboarding.step7.desc": "Your Executive Diagnostic is ready.",
      "sequence.title": "Where is my company?",
      "sequence.config_complete": "Configuration complete",
      "sequence.data_received": "Data received",
      "sequence.data_processed": "Data processed",
      "sequence.analysis_available": "Analysis available",
      "sequence.diagnostic_ready": "Diagnostic ready",
      "dataflow.title": "What happens after you upload your data",
      "dataflow.received": "File received",
      "dataflow.validation": "Validation",
      "dataflow.mapping": "Mapping",
      "dataflow.processing": "Processing",
      "dataflow.intelligence": "Intelligence",
      "dataflow.diagnostic": "Diagnostic",
      "hierarchy.title": "Your journey in ZAYVERO",
      "hierarchy.diagnostic": "Executive Diagnostic",
      "hierarchy.diagnostic_desc": "The first big result: your company's status at a glance.",
      "hierarchy.intelligence": "Intelligence Center",
      "hierarchy.intelligence_desc": "Your daily operational space: metrics and signals.",
      "hierarchy.findings": "Findings, risks, opportunities, predictions",
      "hierarchy.findings_desc": "Explore the evidence behind every conclusion.",
      "hierarchy.advisor": "Advisor",
      "hierarchy.advisor_desc": "Ask questions about your data and findings.",
      "benefits.title": "What does your company get with ZAYVERO?",
      "benefits.unusual_behavior": "Detect unusual behavior in your data",
      "benefits.prioritize": "Prioritize the problems that need attention",
      "benefits.opportunities": "Find possible opportunities worth reviewing",
      "benefits.trends": "Analyze trends over time",
      "benefits.ask": "Ask questions about your business information",
      "benefits.evidence_recs": "Get evidence-based recommendations to review",
      "benefits.executive_diagnostic": "Have an executive diagnostic of your company",
      "benefits.note": "ZAYVERO presents evidence and suggestions for review. It doesn't guarantee sales, savings or results.",
      "demo.explore.title": "Explore the demo",
      "demo.explore.desc": "See how ZAYVERO works with demonstration data (UCI Online Retail II). Demo data is never mixed with client data.",
      "demo.explore.cta": "Explore demo",
      "demo.explore.active": "You are exploring the demo.",
      "demo.return": "← Back to my company",
      "demo.enter.failed": "Could not open the demo.",
      "demo.exit.failed": "Could not return to your company.",
      "demo.exit.session_expired": "Your previous session expired. Please sign in again.",
      "demo.badge": "DEMO · Demonstration data",
      "plans.title": "Plans",
      "plans.subtitle": "Choose the plan that fits your company.",
      "plans.price.not_definitive": "To be defined — not definitive",
      "plans.starter.name": "Starter",
      "plans.starter.f1": "Executive diagnostic",
      "plans.starter.f2": "Intelligence center",
      "plans.starter.f3": "1 company, 1 user",
      "plans.professional.name": "Professional",
      "plans.professional.f1": "Everything in Starter",
      "plans.professional.f2": "Advisor with evidence",
      "plans.professional.f3": "Findings, risks and predictions",
      "plans.professional.f4": "Up to 5 users",
      "plans.enterprise.name": "Enterprise",
      "plans.enterprise.f1": "Everything in Professional",
      "plans.enterprise.f2": "Multiple companies",
      "plans.enterprise.f3": "Regional configuration per company",
      "plans.enterprise.f4": "Priority support",
      "plans.cta": "Choose plan",
      "plans.note": "Plans are shown for presentation only. No payments are processed at this time."
    }
  };

  var SYMBOLS = {
    USD: "$", DOP: "RD$", EUR: "€", GBP: "£", MXN: "MX$", COP: "COL$",
    ARS: "AR$", CLP: "CL$", PEN: "S/", BRL: "R$", CAD: "CA$", CHF: "CHF ",
    JPY: "¥", CNY: "¥"
  };

  var lang = "es";
  var cfg = {}; // configuración de la empresa (de /api/me)

  function setConfig(companyConfig) {
    cfg = companyConfig || {};
    var l = (cfg.language || "es").toLowerCase();
    lang = (l === "en") ? "en" : "es";
  }

  function t(key) {
    var cat = STRINGS[lang] || STRINGS.es;
    if (cat[key] !== undefined) return cat[key];
    if (STRINGS.es[key] !== undefined) return STRINGS.es[key];
    return key;
  }

  function numFmt() { return cfg.number_format === "en" ? "en" : "es"; }
  function dateFmt() {
    var d = cfg.date_format;
    return (d === "mm/dd/yyyy" || d === "yyyy-mm-dd") ? d : "dd/mm/yyyy";
  }

  function fmtNum(v, dec) {
    if (v === null || v === undefined || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    var d = (dec == null ? 1 : dec);
    var loc = numFmt() === "en" ? "en-US" : "es-ES";
    return n.toLocaleString(loc, { maximumFractionDigits: d, minimumFractionDigits: 0 });
  }

  function fmtMoney(v) {
    if (v === null || v === undefined || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    var cur = (cfg.currency || "").toUpperCase();
    var loc = numFmt() === "en" ? "en-US" : "es-ES";
    var num = n.toLocaleString(loc, { maximumFractionDigits: 0 });
    if (!cur) return num + " (" + t("currency.missing") + ")";
    var sym = SYMBOLS[cur] || (cur + " ");
    return sym + num;
  }

  function fmtDate(s) {
    if (!s) return "—";
    var m = String(s).slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return s;
    var df = dateFmt();
    if (df === "mm/dd/yyyy") return m[2] + "/" + m[3] + "/" + m[1];
    if (df === "yyyy-mm-dd") return m[1] + "-" + m[2] + "-" + m[3];
    return m[3] + "/" + m[2] + "/" + m[1];
  }

  global.ZBI18N = {
    setConfig: setConfig,
    t: t,
    fmtNum: fmtNum,
    fmtMoney: fmtMoney,
    fmtDate: fmtDate,
    lang: function () { return lang; }
  };
})(window);
