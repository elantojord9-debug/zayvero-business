"""FASE 5A — Índice central de evidencia.

Cada afirmación del BusinessIntelligenceContext se registra aquí con su
fuente, de modo que el futuro AI Business Advisor pueda responder
"¿por qué dices eso?" y encontrar la evidencia correspondiente.

Determinista: los IDs se asignan en orden secuencial según el orden de
registro (las secciones iteran sus fuentes en orden ordenado).
"""

from __future__ import annotations


class EvidenceIndex:
    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._counter = 0

    def add(
        self,
        source_module: str,
        source_file: str,
        source_record: str | None,
        field: str,
        value,
        period: str | None,
        trace: dict | None,
    ) -> str:
        """Registra una evidencia y devuelve su evidence_id (EV-0001, ...)."""
        self._counter += 1
        evidence_id = f"EV-{self._counter:04d}"
        self._entries.append(
            {
                "evidence_id": evidence_id,
                "source_module": source_module,
                "source_file": source_file,
                "source_record": source_record,
                "field": field,
                "value": value,
                "period": period,
                "trace": trace,
            }
        )
        return evidence_id

    def as_list(self) -> list[dict]:
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)
