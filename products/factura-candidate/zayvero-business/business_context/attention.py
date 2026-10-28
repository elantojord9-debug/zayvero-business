"""FASE 5A — attention_summary: nivel de atención empresarial.

Reglas deterministas y documentadas (nada arbitrario):

- CRITICAL: urgent_findings >= 20 OR high_severity_risks >= 5
- HIGH:     urgent_findings >= 5  OR important_findings >= 100
            OR high_decline_risk >= 2
- MEDIUM:   important_findings >= 10 OR review_findings >= 50
            OR medium_severity_risks >= 3
- LOW:      en otro caso

Se evalúan en orden CRITICAL → HIGH → MEDIUM → LOW (primera que cumpla).
"""

from __future__ import annotations


def build_attention_summary(critical_findings, key_risks, pi_summary, profile) -> dict:
    by_priority = {"URGENT": 0, "IMPORTANT": 0, "REVIEW": 0, "MONITOR": 0}
    for f in critical_findings:
        p = f.get("business_priority")
        if p in by_priority:
            by_priority[p] += 1

    sev = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in key_risks:
        s = r.get("severity")
        if s in sev:
            sev[s] += 1

    high_decline = (pi_summary.get("summary", {}) or {}).get("high_decline_risk", 0) or 0
    dq_score = (profile.get("data_quality", {}) or {}).get("score")

    signals = {
        "urgent_findings": by_priority["URGENT"],
        "important_findings": by_priority["IMPORTANT"],
        "review_findings": by_priority["REVIEW"],
        "high_severity_risks": sev["HIGH"],
        "medium_severity_risks": sev["MEDIUM"],
        "high_decline_risk_predictions": high_decline,
        "data_quality_score": dq_score,
    }

    if signals["urgent_findings"] >= 20 or signals["high_severity_risks"] >= 5:
        level = "CRITICAL"
        reason = "urgent_findings >= 20 o high_severity_risks >= 5"
    elif (signals["urgent_findings"] >= 5 or signals["important_findings"] >= 100
          or signals["high_decline_risk_predictions"] >= 2):
        level = "HIGH"
        reason = "urgent_findings >= 5, important_findings >= 100 o high_decline_risk >= 2"
    elif (signals["important_findings"] >= 10 or signals["review_findings"] >= 50
          or signals["medium_severity_risks"] >= 3):
        level = "MEDIUM"
        reason = "important_findings >= 10, review_findings >= 50 o medium_severity_risks >= 3"
    else:
        level = "LOW"
        reason = "ningún umbral superior alcanzado"

    return {
        "attention_level": level,
        "signals": signals,
        "rule_triggered": reason,
        "rules": ("CRITICAL: urgent>=20 o high_risks>=5; "
                  "HIGH: urgent>=5 o important>=100 o high_decline>=2; "
                  "MEDIUM: important>=10 o review>=50 o medium_risks>=3; "
                  "LOW: en otro caso."),
    }
