"""BLOQUE PLATAFORMA SAAS (2026-10-13) — Centro de ayuda.

Contenido ESTÁTICO (ES/EN), servido por la API sin base de datos.
Preguntas frecuentes por módulo. Sin facturación real: donde aplique se
aclara que los precios son de referencia en preparación local.
"""
from __future__ import annotations

# Estructura: {idioma: {modulo: [{"id","pregunta","respuesta"}, ...]}}
# modulo "general" = aplica a todo el producto.

AYUDA = {
    "es": {
        "general": [
            {
                "id": "que-es",
                "pregunta": "¿Qué es ZAYVERO Business OS?",
                "respuesta": (
                    "Es la plataforma de inteligencia empresarial de "
                    "ZAYVERO: inventario, compras, finanzas, CRM, ventas, "
                    "cobranzas, tesorería, reportes y alertas inteligentes "
                    "(Shield), todo aislado por empresa. Funciona con datos "
                    "sintéticos en esta fase de preparación local."),
            },
            {
                "id": "empezar",
                "pregunta": "¿Cómo empiezo?",
                "respuesta": (
                    "1) Registra tu empresa, 2) invita a tu equipo, "
                    "3) carga tu catálogo de productos, 4) registra tu "
                    "primera venta y 5) genera tu primer reporte. La "
                    "checklist de puesta en marcha (onboarding) te guía "
                    "paso a paso y marca tu progreso."),
            },
            {
                "id": "planes",
                "pregunta": "¿Cuánto cuesta?",
                "respuesta": (
                    "Hay 4 planes de referencia: Starter US$49, Business "
                    "US$149, Professional US$349 y Enterprise desde US$799. "
                    "Aclaración importante: en esta fase no hay facturación "
                    "real; los precios son de referencia para preparación "
                    "local y ningún flujo genera cargos."),
            },
            {
                "id": "idiomas",
                "pregunta": "¿En qué idiomas está disponible?",
                "respuesta": (
                    "El panel y esta ayuda están en español e inglés. Puedes "
                    "cambiar el idioma desde la configuración."),
            },
        ],
        "registro": [
            {
                "id": "datos-registro",
                "pregunta": "¿Qué necesito para registrar mi empresa?",
                "respuesta": (
                    "Nombre de la empresa, identificador fiscal, moneda base "
                    "(código ISO 4217, p. ej. USD o DOP), plan elegido y las "
                    "sucursales o almacenes iniciales. También creas tu "
                    "cuenta de propietario con correo y contraseña."),
            },
            {
                "id": "fiscal-verificacion",
                "pregunta": "¿Verifican mi identificador fiscal?",
                "respuesta": (
                    "No. En esta fase el identificador fiscal solo se "
                    "valida por formato (5 a 20 caracteres alfanuméricos) y "
                    "se usa para evitar registros duplicados. La "
                    "verificación contra autoridades fiscales está "
                    "pendiente para producción."),
            },
            {
                "id": "duplicados",
                "pregunta": "¿Puedo registrar dos veces la misma empresa?",
                "respuesta": (
                    "No. El sistema rechaza el registro si el identificador "
                    "fiscal o el nombre (normalizado) ya existen. Por "
                    "seguridad, el mensaje de rechazo no indica cuál dato "
                    "colisionó."),
            },
            {
                "id": "varias-empresas",
                "pregunta": "¿Puedo manejar varias empresas con una cuenta?",
                "respuesta": (
                    "Sí, según tu plan: Starter permite 2 empresas, Business "
                    "5, Professional 15 y Enterprise 100. Cada empresa queda "
                    "totalmente aislada de las demás."),
            },
        ],
        "planes": [
            {
                "id": "que-incluye",
                "pregunta": "¿Qué incluye cada plan?",
                "respuesta": (
                    "Todos incluyen inventario, compras, CRM, ventas, "
                    "cobranzas, tesorería, finanzas y reportes. Business "
                    "agrega catálogo, documentos, Shield y aprobaciones. "
                    "Professional agrega simulador, asesor de precios y "
                    "mesa del contador. Enterprise agrega soporte dedicado "
                    "y los límites más altos."),
            },
            {
                "id": "limites",
                "pregunta": "¿Qué pasa si supero un límite de mi plan?",
                "respuesta": (
                    "El sistema bloquea la acción con un mensaje claro que "
                    "indica el límite del plan, tu uso actual y que la "
                    "solución es subir de plan. Nada se cobra "
                    "automáticamente."),
            },
            {
                "id": "cambiar-plan",
                "pregunta": "¿Puedo cambiar de plan?",
                "respuesta": (
                    "Sí. El propietario o un administrador puede cambiar el "
                    "plan de la cuenta. Si bajas de plan y tu uso actual "
                    "supera los límites del plan menor, el cambio se "
                    "bloquea hasta que reduzcas el uso."),
            },
        ],
        "usuarios": [
            {
                "id": "invitar",
                "pregunta": "¿Cómo invito a alguien a mi empresa?",
                "respuesta": (
                    "Un propietario o administrador genera una invitación "
                    "con el correo y el rol de la persona. El sistema crea "
                    "un token único (válido 7 días) que debes entregar por "
                    "tu propio canal: el sistema NO envía correos."),
            },
            {
                "id": "roles",
                "pregunta": "¿Qué roles existen?",
                "respuesta": (
                    "Owner (propietario: control total), admin "
                    "(administra usuarios y configuración), analyst (lee y "
                    "analiza) y viewer (solo lectura). Solo owner y admin "
                    "pueden invitar o desactivar usuarios."),
            },
            {
                "id": "desactivar",
                "pregunta": "¿Qué pasa al desactivar un usuario?",
                "respuesta": (
                    "Pierde el acceso de inmediato pero su historial queda "
                    "intacto en la auditoría. No puedes desactivarte a ti "
                    "mismo ni al último propietario activo."),
            },
        ],
        "inventario": [
            {
                "id": "stock",
                "pregunta": "¿Cómo se calcula el stock?",
                "respuesta": (
                    "El stock se deriva únicamente de los movimientos "
                    "(entradas, salidas, ajustes, traslados y devoluciones). "
                    "No se edita a mano: cada cambio deja rastro en la "
                    "auditoría."),
            },
            {
                "id": "negativo",
                "pregunta": "¿Puedo vender sin stock?",
                "respuesta": (
                    "Depende de la configuración: el stock negativo puede "
                    "bloquearse para evitar vender lo que no tienes."),
            },
        ],
        "ventas": [
            {
                "id": "una-venta",
                "pregunta": "¿Qué ocurre al registrar una venta?",
                "respuesta": (
                    "Una venta genera en una sola operación: la salida de "
                    "inventario, el ingreso en el estado de resultados y la "
                    "cuenta por cobrar (si es a crédito). Todo queda "
                    "auditado."),
            },
        ],
        "finanzas": [
            {
                "id": "rentabilidad",
                "pregunta": "¿Cómo se calcula la rentabilidad?",
                "respuesta": (
                    "Ingresos menos costo de ventas (promedio ponderado), "
                    "menos devoluciones, descuentos e impuestos. Puedes "
                    "verla por producto, sucursal y período."),
            },
        ],
        "reportes": [
            {
                "id": "formatos",
                "pregunta": "¿En qué formatos puedo descargar reportes?",
                "respuesta": (
                    "CSV, XLSX y PDF, para inventario, finanzas, compras, "
                    "cobranzas y Shield. Los reportes financieros exigen "
                    "permiso de escritura."),
            },
        ],
        "shield": [
            {
                "id": "alertas",
                "pregunta": "¿Qué son las alertas de Shield?",
                "respuesta": (
                    "Son 8 reglas que vigilan tu negocio: stock crítico, "
                    "cuentas vencidas, concentración de proveedores o "
                    "clientes, crédito sin cobro, movimientos inusuales, "
                    "devoluciones frecuentes y reposiciones tardías. Cada "
                    "alerta explica su causa con evidencia."),
            },
        ],
        "onboarding": [
            {
                "id": "checklist",
                "pregunta": "¿Qué es la checklist de puesta en marcha?",
                "respuesta": (
                    "Son 5 pasos: crear la empresa, invitar usuarios, "
                    "cargar el catálogo, registrar la primera venta y "
                    "generar el primer reporte. Algunos se marcan solos "
                    "cuando los completas; otros puedes marcarlos a mano."),
            },
        ],
    },
    "en": {
        "general": [
            {
                "id": "que-es",
                "pregunta": "What is ZAYVERO Business OS?",
                "respuesta": (
                    "It is ZAYVERO's business intelligence platform: "
                    "inventory, purchasing, finance, CRM, sales, "
                    "collections, treasury, reports and smart alerts "
                    "(Shield), all isolated per company. It runs on "
                    "synthetic data in this local preparation phase."),
            },
            {
                "id": "empezar",
                "pregunta": "How do I get started?",
                "respuesta": (
                    "1) Register your company, 2) invite your team, "
                    "3) load your product catalog, 4) record your first "
                    "sale and 5) generate your first report. The setup "
                    "checklist (onboarding) guides you step by step and "
                    "tracks your progress."),
            },
            {
                "id": "planes",
                "pregunta": "How much does it cost?",
                "respuesta": (
                    "There are 4 reference plans: Starter US$49, Business "
                    "US$149, Professional US$349 and Enterprise from "
                    "US$799. Important: there is no real billing in this "
                    "phase; prices are reference only for local "
                    "preparation and no flow generates charges."),
            },
            {
                "id": "idiomas",
                "pregunta": "Which languages are supported?",
                "respuesta": (
                    "The panel and this help center are available in "
                    "Spanish and English. You can switch languages from "
                    "settings."),
            },
        ],
        "registro": [
            {
                "id": "datos-registro",
                "pregunta": "What do I need to register my company?",
                "respuesta": (
                    "Company name, tax identifier, base currency (ISO 4217 "
                    "code, e.g. USD or DOP), chosen plan and initial "
                    "branches or warehouses. You also create your owner "
                    "account with email and password."),
            },
            {
                "id": "fiscal-verificacion",
                "pregunta": "Do you verify my tax identifier?",
                "respuesta": (
                    "No. In this phase the tax identifier is only format-"
                    "validated (5 to 20 alphanumeric characters) and used "
                    "to prevent duplicate registrations. Verification "
                    "against tax authorities is pending for production."),
            },
            {
                "id": "duplicados",
                "pregunta": "Can I register the same company twice?",
                "respuesta": (
                    "No. Registration is rejected if the tax identifier or "
                    "the (normalized) name already exists. For security, "
                    "the rejection message does not say which field "
                    "collided."),
            },
            {
                "id": "varias-empresas",
                "pregunta": "Can I manage several companies with one account?",
                "respuesta": (
                    "Yes, per your plan: Starter allows 2 companies, "
                    "Business 5, Professional 15 and Enterprise 100. Each "
                    "company is fully isolated from the others."),
            },
        ],
        "planes": [
            {
                "id": "que-incluye",
                "pregunta": "What does each plan include?",
                "respuesta": (
                    "All include inventory, purchasing, CRM, sales, "
                    "collections, treasury, finance and reports. Business "
                    "adds catalog, documents, Shield and approvals. "
                    "Professional adds simulator, price advisor and the "
                    "accountant desk. Enterprise adds dedicated support "
                    "and the highest limits."),
            },
            {
                "id": "limites",
                "pregunta": "What happens if I exceed a plan limit?",
                "respuesta": (
                    "The system blocks the action with a clear message "
                    "showing the plan limit, your current usage, and that "
                    "upgrading is the solution. Nothing is charged "
                    "automatically."),
            },
            {
                "id": "cambiar-plan",
                "pregunta": "Can I change plans?",
                "respuesta": (
                    "Yes. An owner or administrator can change the account "
                    "plan. If you downgrade and your current usage exceeds "
                    "the lower plan's limits, the change is blocked until "
                    "you reduce usage."),
            },
        ],
        "usuarios": [
            {
                "id": "invitar",
                "pregunta": "How do I invite someone to my company?",
                "respuesta": (
                    "An owner or administrator creates an invitation with "
                    "the person's email and role. The system generates a "
                    "unique token (valid 7 days) that you must deliver "
                    "through your own channel: the system does NOT send "
                    "emails."),
            },
            {
                "id": "roles",
                "pregunta": "What roles exist?",
                "respuesta": (
                    "Owner (full control), admin (manages users and "
                    "settings), analyst (reads and analyzes) and viewer "
                    "(read only). Only owner and admin can invite or "
                    "deactivate users."),
            },
            {
                "id": "desactivar",
                "pregunta": "What happens when I deactivate a user?",
                "respuesta": (
                    "They lose access immediately but their history stays "
                    "in the audit log. You cannot deactivate yourself or "
                    "the last active owner."),
            },
        ],
        "inventario": [
            {
                "id": "stock",
                "pregunta": "How is stock calculated?",
                "respuesta": (
                    "Stock is derived only from movements (receipts, "
                    "issues, adjustments, transfers and returns). It is "
                    "never edited by hand: every change leaves an audit "
                    "trail."),
            },
            {
                "id": "negativo",
                "pregunta": "Can I sell without stock?",
                "respuesta": (
                    "It depends on configuration: negative stock can be "
                    "blocked to avoid selling what you do not have."),
            },
        ],
        "ventas": [
            {
                "id": "una-venta",
                "pregunta": "What happens when I record a sale?",
                "respuesta": (
                    "One sale generates, in a single operation: the "
                    "inventory issue, the income statement entry and the "
                    "receivable (if on credit). Everything is audited."),
            },
        ],
        "finanzas": [
            {
                "id": "rentabilidad",
                "pregunta": "How is profitability calculated?",
                "respuesta": (
                    "Revenue minus cost of goods sold (weighted average), "
                    "minus returns, discounts and taxes. You can view it "
                    "by product, branch and period."),
            },
        ],
        "reportes": [
            {
                "id": "formatos",
                "pregunta": "Which formats can I download reports in?",
                "respuesta": (
                    "CSV, XLSX and PDF, for inventory, finance, purchasing, "
                    "collections and Shield. Financial reports require "
                    "write permission."),
            },
        ],
        "shield": [
            {
                "id": "alertas",
                "pregunta": "What are Shield alerts?",
                "respuesta": (
                    "Eight rules watch your business: critical stock, "
                    "overdue receivables, supplier or customer "
                    "concentration, credit without collection, unusual "
                    "movements, frequent returns and late replenishments. "
                    "Each alert explains its cause with evidence."),
            },
        ],
        "onboarding": [
            {
                "id": "checklist",
                "pregunta": "What is the setup checklist?",
                "respuesta": (
                    "Five steps: create the company, invite users, load "
                    "the catalog, record the first sale and generate the "
                    "first report. Some are marked automatically when you "
                    "complete them; others you can mark manually."),
            },
        ],
    },
}

IDIOMAS = ("es", "en")
MODULOS = tuple(AYUDA["es"].keys())


def obtener_ayuda(idioma: str = "es", modulo: str | None = None) -> dict:
    """Contenido de ayuda. Idioma desconocido → español; módulo
    desconocido → error (el llamador decide el status)."""
    lang = (idioma or "es").strip().lower()
    if lang not in IDIOMAS:
        lang = "es"
    contenido = AYUDA[lang]
    if modulo:
        mod = modulo.strip().lower()
        if mod not in contenido:
            return {"error": "modulo_desconocido",
                    "modulos": list(MODULOS)}
        return {"idioma": lang, "modulo": mod,
                "preguntas": contenido[mod]}
    return {"idioma": lang,
            "modulos": {m: contenido[m] for m in MODULOS}}
