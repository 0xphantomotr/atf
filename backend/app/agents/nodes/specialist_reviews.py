import json
from collections import Counter
from typing import Any

from app.agents.llm import (
    AIQuotaLimitError,
    LLMReviewError,
    request_specialist_review,
    specialist_review_input_token_budget,
)
from app.agents.state import AuditGraphState
from app.ai.stages import ai_settings_for_stage
from app.core.config import settings

APPROX_CHARS_PER_TOKEN = 3

SPECIALIST_DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "code": "legal_administrative",
        "title": "Dokumentacioni ligjor dhe administrativ",
        "registers": ("permits_property_licenses", "stakeholders"),
    },
    {
        "code": "project_parameters",
        "title": "Parametrat e projektit, lejet dhe të dhënat e pronës",
        "registers": ("project_parameters", "permits_property_licenses"),
    },
    {
        "code": "chronology_completion",
        "title": "Kronologjia, afatet dhe evidenca e përfundimit",
        "registers": (
            "construction_chronology",
            "declarations_and_conclusions",
        ),
    },
    {
        "code": "structural_hidden_works",
        "title": "Fazat konstruktive dhe punimet e maskuara",
        "registers": ("technical_works", "construction_chronology"),
    },
    {
        "code": "materials_quality",
        "title": "Materialet, provat dhe evidenca e cilësisë",
        "registers": ("materials_and_tests", "technical_works"),
    },
    {
        "code": "contractual_economic",
        "title": "Kontratat, sasitë dhe të dhënat ekonomike",
        "registers": (
            "contracts_and_economics",
            "stakeholders",
            "declarations_and_conclusions",
        ),
    },
)

REGISTER_ENTRY_LIMIT = 24
REGISTER_MINIMUMS = {
    "stakeholders": 4,
    "permits_property_licenses": 3,
    "project_parameters": 3,
    "construction_chronology": 4,
    "technical_works": 4,
    "materials_and_tests": 3,
    "contracts_and_economics": 3,
    "declarations_and_conclusions": 1,
    "supporting_evidence": 0,
}
STATEMENT_KEYS = (
    "established_facts",
    "technical_assessments",
    "qualifications",
    "writer_guidance",
)


def review_specialist_domains(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("specialist_reviews")
    if state.get("job", {}).get("job_type") != "kolaudim_act":
        state["specialist_reviews"] = {
            "status": "skipped",
            "reason": "not_kolaudim_act",
            "memoranda": [],
            "summary": _summary([], evidence_count=0),
        }
        return state

    ai_settings = state.get("ai_settings")
    if not settings.ai_senior_review_enabled:
        state["specialist_reviews"] = _skipped_result(
            state,
            reason="ai_specialist_review_disabled",
        )
        return state
    if not isinstance(ai_settings, dict) or not ai_settings.get("api_key"):
        state["specialist_reviews"] = _skipped_result(
            state,
            reason="missing_user_ai_settings",
        )
        return state
    ai_settings = ai_settings_for_stage(ai_settings, "synthesis")

    review_input = _build_specialist_review_input(state, ai_settings=ai_settings)
    evidence_count = len(review_input["evidence_catalog"])
    if not evidence_count:
        memoranda = _base_memoranda(review_input)
        state["specialist_reviews"] = {
            "status": "insufficient_evidence",
            "memoranda": memoranda,
            "summary": _summary(memoranda, evidence_count=0),
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
        }
        state["needs_human_review"] = True
        return state

    try:
        response = request_specialist_review(review_input, ai_settings=ai_settings)
    except AIQuotaLimitError:
        raise
    except LLMReviewError as exc:
        memoranda = _base_memoranda(review_input)
        state["specialist_reviews"] = {
            "status": "failed",
            "reason": str(exc)[:500],
            "memoranda": memoranda,
            "summary": _summary(memoranda, evidence_count=evidence_count),
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
        }
        state["needs_human_review"] = True
        return state

    memoranda = _normalize_memoranda(response, review_input)
    review_status = _review_status(memoranda)
    state["specialist_reviews"] = {
        "status": review_status,
        "memoranda": memoranda,
        "summary": _summary(memoranda, evidence_count=evidence_count),
        "provider": ai_settings.get("provider"),
        "model": ai_settings.get("model"),
        "api_key_hint": ai_settings.get("api_key_hint"),
    }
    if review_status != "reviewed":
        state["needs_human_review"] = True
    return state


def _build_specialist_review_input(
    state: AuditGraphState,
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    dossier = state.get("professional_dossier", {})
    registers = dossier.get("registers", {}) if isinstance(dossier, dict) else {}
    if not isinstance(registers, dict):
        registers = {}

    evidence_catalog: dict[str, dict[str, Any]] = {}
    register_ids: dict[str, list[str]] = {}
    for register_name, entries in registers.items():
        if not isinstance(entries, list):
            continue
        register = str(register_name)
        register_ids[register] = []
        for index, entry in enumerate(entries[:REGISTER_ENTRY_LIMIT]):
            if not isinstance(entry, dict):
                continue
            evidence_id = f"{register}:{index}"
            evidence_catalog[evidence_id] = _compact_register_evidence(
                evidence_id,
                register,
                entry,
            )
            register_ids[register].append(evidence_id)

    domains = [
        {
            "code": definition["code"],
            "title": definition["title"],
            "registers": list(definition["registers"]),
            "evidence_ids": [
                evidence_id
                for register in definition["registers"]
                for evidence_id in register_ids.get(register, [])
            ],
        }
        for definition in SPECIALIST_DOMAINS
    ]
    _add_dossier_concerns(evidence_catalog, domains, dossier)

    payload: dict[str, Any] = {
        "project": dict(state.get("project", {})),
        "legal_basis": _legal_basis(state),
        "domains": domains,
        "evidence_catalog": evidence_catalog,
        "instructions": [
            "Analizo çdo fushë vetëm nga evidence_ids e lejuara për atë fushë.",
            "Çdo pohim duhet të citojë të paktën një evidence_id të inputit.",
            "Mos krijo fakte, kontrolle fizike, matje ose konkluzione pa evidencë.",
            "Jep udhëzim profesional për shkruesin, jo tekst final të Aktit.",
        ],
    }
    return _fit_specialist_input(
        payload,
        input_token_budget=specialist_review_input_token_budget(ai_settings),
        ai_settings=ai_settings,
    )


def _compact_register_evidence(
    evidence_id: str,
    register: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "kind": "register_entry",
        "register": register,
        "field_name": entry.get("field_name"),
        "value": entry.get("value"),
        "normalized_value": entry.get("normalized_value"),
        "confidence": entry.get("confidence"),
        "confidence_level": entry.get("confidence_level"),
        "corroborating_source_count": entry.get("corroborating_source_count"),
        "source_references": _source_references(entry.get("sources")),
    }


def _source_references(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    references = []
    for source in value[:3]:
        if not isinstance(source, dict):
            continue
        chunk_ids = [
            str(chunk.get("chunk_id"))
            for chunk in source.get("chunk_references", [])[:3]
            if isinstance(chunk, dict) and chunk.get("chunk_id")
        ]
        references.append(
            {
                "source_document": source.get("source_document"),
                "file_version_id": source.get("file_version_id"),
                "chunk_ids": chunk_ids,
            }
        )
    return references


def _add_dossier_concerns(
    catalog: dict[str, dict[str, Any]],
    domains: list[dict[str, Any]],
    dossier: object,
) -> None:
    if not isinstance(dossier, dict):
        return
    for index, conflict in enumerate(dossier.get("conflicts", [])[:20]):
        if not isinstance(conflict, dict):
            continue
        evidence_id = f"conflict:{index}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "canonical_conflict",
            "field_name": conflict.get("field"),
            "selected_value": conflict.get("selected_value"),
            "alternatives": conflict.get("alternatives", [])[:3],
        }
        _assign_concern(domains, catalog, evidence_id)

    for index, issue in enumerate(dossier.get("integrity_issues", [])[:20]):
        if not isinstance(issue, dict):
            continue
        evidence_id = f"integrity:{index}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "integrity_issue",
            "code": issue.get("code"),
            "severity": issue.get("severity"),
            "description": issue.get("description"),
        }
        if issue.get("code") == "DOSSIER-CHRONOLOGY-ORDER":
            _domain_by_code(domains, "chronology_completion")["evidence_ids"].append(
                evidence_id
            )
        else:
            for domain in domains:
                domain["evidence_ids"].append(evidence_id)


def _assign_concern(
    domains: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    evidence_id: str,
) -> None:
    field = catalog[evidence_id].get("field_name")
    matching_registers = {
        item.get("register")
        for item in catalog.values()
        if item.get("kind") == "register_entry" and item.get("field_name") == field
    }
    assigned = False
    for domain in domains:
        if matching_registers.intersection(domain["registers"]):
            domain["evidence_ids"].append(evidence_id)
            assigned = True
    if not assigned:
        for domain in domains:
            domain["evidence_ids"].append(evidence_id)


def _domain_by_code(domains: list[dict[str, Any]], code: str) -> dict[str, Any]:
    return next(domain for domain in domains if domain["code"] == code)


def _fit_specialist_input(
    payload: dict[str, Any],
    *,
    input_token_budget: int,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    for _ in range(300):
        _set_budget_metadata(
            payload,
            input_token_budget=input_token_budget,
            ai_settings=ai_settings,
        )
        if _estimate_tokens(payload) <= input_token_budget:
            return payload
        if not _shrink_specialist_input(payload):
            break
    _set_budget_metadata(
        payload,
        input_token_budget=input_token_budget,
        ai_settings=ai_settings,
    )
    return payload


def _set_budget_metadata(
    payload: dict[str, Any],
    *,
    input_token_budget: int,
    ai_settings: dict[str, Any],
) -> None:
    payload["budget"] = {
        "provider": ai_settings.get("provider"),
        "model": ai_settings.get("model"),
        "target_input_tokens": input_token_budget,
        "selected_evidence_count": len(payload["evidence_catalog"]),
        "estimated_input_tokens": 0,
    }
    payload["budget"]["estimated_input_tokens"] = _estimate_tokens(payload)


def _shrink_specialist_input(payload: dict[str, Any]) -> bool:
    catalog = payload.get("evidence_catalog")
    if not isinstance(catalog, dict):
        return False

    source_candidates = [
        item
        for item in catalog.values()
        if isinstance(item, dict)
        and isinstance(item.get("source_references"), list)
        and len(item["source_references"]) > 1
    ]
    if source_candidates:
        largest = max(source_candidates, key=lambda item: len(item["source_references"]))
        largest["source_references"].pop()
        return True

    counts = Counter(
        str(item.get("register"))
        for item in catalog.values()
        if isinstance(item, dict) and item.get("kind") == "register_entry"
    )
    candidates = [
        item
        for item in catalog.values()
        if isinstance(item, dict)
        and item.get("kind") == "register_entry"
        and counts[str(item.get("register"))]
        > REGISTER_MINIMUMS.get(str(item.get("register")), 1)
    ]
    if not candidates:
        return False
    removable = min(candidates, key=_evidence_priority)
    evidence_id = str(removable["evidence_id"])
    del catalog[evidence_id]
    for domain in payload.get("domains", []):
        if isinstance(domain, dict) and evidence_id in domain.get("evidence_ids", []):
            domain["evidence_ids"].remove(evidence_id)
    return True


def _evidence_priority(item: dict[str, Any]) -> tuple[float, int]:
    confidence = item.get("confidence")
    score = float(confidence) if isinstance(confidence, int | float) else 0.0
    corroboration = item.get("corroborating_source_count")
    source_count = int(corroboration) if isinstance(corroboration, int) else 0
    return score, source_count


def _normalize_memoranda(
    response: object,
    review_input: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_memoranda = response.get("memoranda", []) if isinstance(response, dict) else []
    by_code = {
        str(item.get("code")): item
        for item in raw_memoranda
        if isinstance(item, dict) and item.get("code")
    }
    catalog = review_input["evidence_catalog"]
    memoranda = []
    for domain in review_input["domains"]:
        raw = by_code.get(domain["code"], {})
        allowed_ids = set(domain["evidence_ids"])
        memo = _base_memorandum(domain, catalog)
        if not allowed_ids:
            memoranda.append(memo)
            continue
        for key in STATEMENT_KEYS:
            memo[key] = _normalize_statements(
                raw.get(key),
                allowed_ids=allowed_ids,
                catalog=catalog,
            )
        statement_count = sum(len(memo[key]) for key in STATEMENT_KEYS)
        memo["status"] = "reviewed" if statement_count else "no_supported_statements"
        memo["confidence"] = _safe_confidence(raw.get("confidence"))
        memoranda.append(memo)
    return memoranda


def _normalize_statements(
    value: object,
    *,
    allowed_ids: set[str],
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        statement = " ".join(str(item.get("statement") or "").split())
        raw_evidence_ids = item.get("evidence_ids")
        if not isinstance(raw_evidence_ids, list):
            continue
        evidence_ids = list(
            dict.fromkeys(
                str(evidence_id)
                for evidence_id in raw_evidence_ids
                if str(evidence_id) in allowed_ids and str(evidence_id) in catalog
            )
        )[:6]
        if not statement or not evidence_ids:
            continue
        normalized.append(
            {
                "statement": statement[:800],
                "evidence_ids": evidence_ids,
                "source_references": _statement_sources(evidence_ids, catalog),
            }
        )
    return normalized


def _statement_sources(
    evidence_ids: list[str],
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = []
    seen = set()
    for evidence_id in evidence_ids:
        for source in catalog[evidence_id].get("source_references", []):
            if not isinstance(source, dict):
                continue
            key = (
                str(source.get("file_version_id") or ""),
                str(source.get("source_document") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            sources.append(dict(source))
    return sources[:8]


def _skipped_result(state: AuditGraphState, *, reason: str) -> dict[str, Any]:
    review_input = _build_specialist_review_input(
        state,
        ai_settings={"provider": "none", "model": "none"},
    )
    memoranda = _base_memoranda(review_input)
    return {
        "status": "skipped",
        "reason": reason,
        "memoranda": memoranda,
        "summary": _summary(
            memoranda,
            evidence_count=len(review_input["evidence_catalog"]),
        ),
    }


def _base_memoranda(review_input: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = review_input["evidence_catalog"]
    return [_base_memorandum(domain, catalog) for domain in review_input["domains"]]


def _base_memorandum(
    domain: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = [
        evidence_id
        for evidence_id in domain["evidence_ids"]
        if evidence_id in catalog
    ]
    source_documents = sorted(
        {
            str(source.get("source_document"))
            for evidence_id in evidence_ids
            for source in catalog[evidence_id].get("source_references", [])
            if isinstance(source, dict) and source.get("source_document")
        }
    )
    fields = sorted(
        {
            str(catalog[evidence_id].get("field_name"))
            for evidence_id in evidence_ids
            if catalog[evidence_id].get("field_name")
        }
    )
    return {
        "code": domain["code"],
        "title": domain["title"],
        "status": "evidence_prepared" if evidence_ids else "insufficient_evidence",
        "evidence_count": len(evidence_ids),
        "covered_fields": fields,
        "source_documents": source_documents,
        "established_facts": [],
        "technical_assessments": [],
        "qualifications": [],
        "writer_guidance": [],
        "confidence": None,
    }


def _summary(memoranda: list[dict[str, Any]], *, evidence_count: int) -> dict[str, Any]:
    return {
        "memorandum_count": len(memoranda),
        "reviewable_count": sum(
            1 for memo in memoranda if int(memo.get("evidence_count") or 0) > 0
        ),
        "reviewed_count": sum(1 for memo in memoranda if memo.get("status") == "reviewed"),
        "insufficient_evidence_count": sum(
            1 for memo in memoranda if memo.get("status") == "insufficient_evidence"
        ),
        "supported_statement_count": sum(
            len(memo.get(key, []))
            for memo in memoranda
            for key in STATEMENT_KEYS
        ),
        "evidence_item_count": evidence_count,
    }


def _review_status(memoranda: list[dict[str, Any]]) -> str:
    reviewable_count = sum(
        1 for memo in memoranda if int(memo.get("evidence_count") or 0) > 0
    )
    reviewed_count = sum(1 for memo in memoranda if memo.get("status") == "reviewed")
    if reviewable_count and reviewed_count == reviewable_count:
        return "reviewed"
    if reviewed_count:
        return "partially_reviewed"
    return "invalid_model_output"


def _legal_basis(state: AuditGraphState) -> dict[str, Any]:
    references = sorted(
        {
            str(rule.get("law_reference"))
            for rule in state.get("rules", [])
            if isinstance(rule, dict) and rule.get("law_reference")
        }
    )
    return {
        "law_scope": state.get("job", {}).get("law_scope", []),
        "verified_references": references[:30],
    }


def _estimate_tokens(value: object) -> int:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(serialized) // APPROX_CHARS_PER_TOKEN)


def _safe_confidence(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return round(max(0.0, min(1.0, float(value))), 3)
