import re
import unicodedata
from collections import defaultdict
from typing import Any

from app.agents.state import AuditGraphState
from app.files.status import is_parsed_status

MAX_FACTS_PER_CATEGORY = 8
MAX_VALUE_LENGTH = 220


FACT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "category": "object_name",
        "label": "Objekti",
        "keywords": ("objekti", "emertimi i objektit", "emertesa e objektit"),
        "confidence": 0.76,
    },
    {
        "category": "location",
        "label": "Vendndodhja",
        "keywords": ("vendndodh", "adresa", "bashkia", "zona kadastrale", "zk "),
        "confidence": 0.72,
    },
    {
        "category": "investor",
        "label": "Investitori/Zhvilluesi",
        "keywords": ("investitor", "zhvillues", "porosites", "porositës"),
        "confidence": 0.72,
    },
    {
        "category": "contractor",
        "label": "Sipërmarrësi/Kontraktori",
        "keywords": ("sipermarres", "sipërmarrës", "kontraktor", "subjekti ndertues"),
        "confidence": 0.72,
    },
    {
        "category": "supervisor",
        "label": "Mbikëqyrësi",
        "keywords": ("mbikeqyres", "mbikëqyrës", "mbikqyres", "supervizor"),
        "confidence": 0.72,
    },
    {
        "category": "kolaudator",
        "label": "Kolaudatori",
        "keywords": ("kolaudator", "kolaudues", "kolaudimi kryhet"),
        "confidence": 0.74,
    },
    {
        "category": "designer",
        "label": "Projektuesi",
        "keywords": ("projektues", "hartues i projektit", "studio projektuese"),
        "confidence": 0.7,
    },
    {
        "category": "permit",
        "label": "Leje/Vendim",
        "keywords": ("leje ndertimi", "leje ndërtimi", "leje zhvillimi", "vendim nr"),
        "confidence": 0.8,
    },
    {
        "category": "timeline",
        "label": "Afate/Data",
        "keywords": ("fillim", "perfundim", "përfundim", "afati", "date", "datë"),
        "confidence": 0.62,
    },
    {
        "category": "technical_metrics",
        "label": "Të dhëna teknike",
        "keywords": ("siperfaq", "sipërfaq", "kate", "lartesi", "lartësi", "distanc"),
        "confidence": 0.68,
    },
    {
        "category": "economic_values",
        "label": "Vlera ekonomike",
        "keywords": ("vlera", "preventiv", "situacion", "lek", "kontrates", "kontratës"),
        "confidence": 0.66,
    },
)


DATE_PATTERN = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
NUMBER_PATTERN = re.compile(r"\b(?:nr\.?|num[eë]r)\s*[:.\-]?\s*[A-Za-z0-9/_-]+", re.I)


def extract_project_facts(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("fact_extractor")

    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parsed_documents = 0
    documents_with_text = 0

    for document in state.get("documents", []):
        if not is_parsed_status(document.get("parse_status")):
            continue

        parsed_documents += 1
        text = str(document.get("text_excerpt") or "").strip()
        if not text:
            continue

        documents_with_text += 1
        for line in _candidate_lines(text):
            normalized_line = _normalize(line)
            for definition in FACT_DEFINITIONS:
                if _line_matches(normalized_line, definition["keywords"]):
                    _append_fact(categories, definition, line, document)

            if DATE_PATTERN.search(line) or NUMBER_PATTERN.search(line):
                if _line_matches(
                    normalized_line,
                    ("leje", "vendim", "fillim", "perfundim", "përfundim", "afati"),
                ):
                    _append_fact(
                        categories,
                        {
                            "category": "formal_references",
                            "label": "Referenca formale",
                            "confidence": 0.68,
                        },
                        line,
                        document,
                    )

    limitations = []
    if parsed_documents and not documents_with_text:
        limitations.append(
            "Dokumentet janë klasifikuar, por workflow-i nuk mori tekst të lexueshëm për nxjerrje faktesh."
        )
    if not categories:
        limitations.append(
            "Nuk u nxorën ende fakte të mjaftueshme nga tekstet; kërkohet OCR/parse më i mirë ose verifikim manual."
        )

    state["extracted_facts"] = {
        "categories": dict(sorted(categories.items())),
        "summary": {
            "parsed_documents": parsed_documents,
            "documents_with_text": documents_with_text,
            "categories_with_facts": len(categories),
            "fact_count": sum(len(items) for items in categories.values()),
        },
        "limitations": limitations,
    }
    if limitations:
        state["needs_human_review"] = True
    return state


def _candidate_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line or len(line) < 6:
            continue
        if len(line) > 500:
            line = line[:500]
        lines.append(line)
    return lines[:700]


def _line_matches(normalized_line: str, keywords: tuple[str, ...]) -> bool:
    return any(_normalize(keyword) in normalized_line for keyword in keywords)


def _append_fact(
    categories: dict[str, list[dict[str, Any]]],
    definition: dict[str, Any],
    line: str,
    document: dict[str, Any],
) -> None:
    category = str(definition["category"])
    if len(categories[category]) >= MAX_FACTS_PER_CATEGORY:
        return

    value = _extract_value(line)
    if not value:
        return

    normalized_value = _normalize(value)
    if any(_normalize(item["value"]) == normalized_value for item in categories[category]):
        return

    categories[category].append(
        {
            "label": definition.get("label", category),
            "value": value,
            "source_document": document.get("original_filename"),
            "document_type": document.get("document_type"),
            "confidence": definition.get("confidence", 0.6),
            "snippet": line[:MAX_VALUE_LENGTH],
        }
    )


def _extract_value(line: str) -> str:
    for separator in (":", "-", "–"):
        if separator in line:
            candidate = line.split(separator, 1)[1].strip()
            if len(candidate) >= 3:
                return candidate[:MAX_VALUE_LENGTH]
    return line[:MAX_VALUE_LENGTH]


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.lower().split())
