from typing import Any

from app.agents.state import AuditGraphState


def build_claim_evidence_catalog(
    state: AuditGraphState,
) -> dict[str, dict[str, Any]]:
    dossier = state.get("professional_dossier", {})
    if not isinstance(dossier, dict):
        dossier = {}

    catalog: dict[str, dict[str, Any]] = {}
    field_evidence_ids: dict[str, list[str]] = {}
    registers = dossier.get("registers", {})
    if isinstance(registers, dict):
        for register_name, entries in registers.items():
            if not isinstance(entries, list):
                continue
            register = str(register_name)
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                evidence_id = register_evidence_id(register, index)
                field_name = str(entry.get("field_name") or "").strip()
                catalog[evidence_id] = {
                    "evidence_id": evidence_id,
                    "kind": "register_entry",
                    "register": register,
                    "field_name": field_name,
                    "value": entry.get("value"),
                    "normalized_value": entry.get("normalized_value"),
                    "confidence_level": entry.get("confidence_level"),
                    "source_references": source_references(entry.get("sources")),
                }
                if field_name:
                    field_evidence_ids.setdefault(field_name, []).append(evidence_id)

    canonical = dossier.get("canonical_facts", {})
    if isinstance(canonical, dict):
        for field_name, fact in canonical.items():
            if not isinstance(fact, dict):
                continue
            field = str(field_name)
            evidence_id = canonical_evidence_id(field)
            supporting_ids = field_evidence_ids.get(field, [])
            catalog[evidence_id] = {
                "evidence_id": evidence_id,
                "kind": (
                    "user_confirmation"
                    if fact.get("user_confirmed") is True
                    else "canonical_fact"
                ),
                "field_name": field,
                "value": fact.get("value"),
                "confidence_level": fact.get("confidence_level"),
                "supporting_evidence_ids": supporting_ids,
                "source_references": _supporting_sources(supporting_ids, catalog),
            }

    for index, conflict in enumerate(dossier.get("conflicts", [])):
        if not isinstance(conflict, dict):
            continue
        evidence_id = f"conflict:{index}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "canonical_conflict",
            "field_name": conflict.get("field"),
            "selected_value": conflict.get("selected_value"),
            "alternatives": conflict.get("alternatives", [])[:3],
            "source_references": [],
        }

    for index, issue in enumerate(dossier.get("integrity_issues", [])):
        if not isinstance(issue, dict):
            continue
        evidence_id = f"integrity:{index}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "integrity_issue",
            "code": issue.get("code"),
            "severity": issue.get("severity"),
            "description": issue.get("description"),
            "source_references": [],
        }

    seen_law_references: set[tuple[str, str]] = set()
    for rule in state.get("rules", []):
        if not isinstance(rule, dict):
            continue
        rule_code = str(rule.get("rule_code") or "").strip()
        law_reference = str(rule.get("law_reference") or "").strip()
        key = rule_code, law_reference
        if not any(key) or key in seen_law_references:
            continue
        seen_law_references.add(key)
        evidence_id = f"law:{len(seen_law_references) - 1}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "verified_law_reference",
            "rule_code": rule_code,
            "law_reference": law_reference,
            "law_document_code": rule.get("law_document_code"),
            "source_references": [],
        }

    return catalog


def canonical_evidence_id(field_name: str) -> str:
    return f"canonical:{field_name}"


def register_evidence_id(register: str, index: int) -> str:
    return f"{register}:{index}"


def source_references(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in value:
        if not isinstance(source, dict):
            continue
        file_version_id = str(source.get("file_version_id") or "").strip()
        source_document = str(source.get("source_document") or "").strip()
        key = file_version_id, source_document
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "source_document": source_document or None,
                "document_type": source.get("document_type"),
                "file_version_id": file_version_id or None,
                "chunk_ids": [
                    str(chunk.get("chunk_id"))
                    for chunk in source.get("chunk_references", [])
                    if isinstance(chunk, dict) and chunk.get("chunk_id")
                ][:6],
            }
        )
    return references[:8]


def claim_source_references(
    evidence_ids: list[str],
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for evidence_id in evidence_ids:
        item = catalog.get(evidence_id)
        if not isinstance(item, dict):
            continue
        for source in item.get("source_references", []):
            if not isinstance(source, dict):
                continue
            key = (
                str(source.get("file_version_id") or ""),
                str(source.get("source_document") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            references.append(dict(source))
    return references[:12]


def current_file_version_ids(state: AuditGraphState) -> set[str]:
    return {
        str(document.get("version_id"))
        for document in state.get("documents", [])
        if isinstance(document, dict) and document.get("version_id")
    }


def evidence_is_current(
    evidence: dict[str, Any],
    *,
    current_version_ids: set[str],
) -> bool:
    if evidence.get("kind") in {
        "verified_law_reference",
        "canonical_conflict",
        "integrity_issue",
        "user_confirmation",
    }:
        return True
    if not current_version_ids:
        return False
    source_ids = {
        str(source.get("file_version_id"))
        for source in evidence.get("source_references", [])
        if isinstance(source, dict) and source.get("file_version_id")
    }
    return bool(source_ids) and source_ids.issubset(current_version_ids)


def _supporting_sources(
    evidence_ids: list[str],
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return claim_source_references(evidence_ids, catalog)
