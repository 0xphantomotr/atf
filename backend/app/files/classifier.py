import re
import unicodedata
from dataclasses import dataclass

UNKNOWN_DOCUMENT_TYPE = "unknown"
CLASSIFICATION_TEXT_LIMIT = 12_000


@dataclass(frozen=True)
class ClassificationResult:
    document_type: str
    confidence: float
    evidence: str | None = None

    def as_metadata(self) -> dict[str, object]:
        return {
            "document_type": self.document_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class DocumentSignature:
    document_type: str
    required_terms: tuple[str, ...]
    confidence: float
    evidence: str


DOCUMENT_SIGNATURES: tuple[DocumentSignature, ...] = (
    DocumentSignature(
        "foundation_completion_and_level_0_00_control_act",
        ("akt kontroll", "theme", "0.00"),
        0.92,
        "akt kontroll + themele + kuota 0.00",
    ),
    DocumentSignature(
        "foundation_completion_and_level_0_00_control_act",
        ("akt kontroll", "theme", "+0.00"),
        0.92,
        "akt kontroll + themele + kuota +0.00",
    ),
    DocumentSignature(
        "level_0_00_control_act",
        ("akt kontroll", "0.00"),
        0.9,
        "akt kontroll + kuota 0.00",
    ),
    DocumentSignature(
        "level_0_00_control_act",
        ("aktkontroll", "0.00"),
        0.9,
        "aktkontroll + kuota 0.00",
    ),
    DocumentSignature(
        "structural_frame_completion_control_act",
        ("akt kontroll", "karabina"),
        0.9,
        "akt kontroll + karabina",
    ),
    DocumentSignature(
        "structural_frame_completion_control_act",
        ("akt kontroll", "struktur"),
        0.88,
        "akt kontroll + struktura",
    ),
    DocumentSignature(
        "facade_and_finishing_completion_control_act",
        ("akt kontroll", "fasad", "rifinitur"),
        0.9,
        "akt kontroll + fasada/rifinitura",
    ),
    DocumentSignature(
        "external_system_completion_control_act",
        ("akt kontroll", "rrjet", "jasht"),
        0.9,
        "akt kontroll + rrjete te jashtme",
    ),
    DocumentSignature(
        "site_setup_control_act",
        ("akt kontroll", "ngritje", "kantier"),
        0.88,
        "akt kontroll + ngritje kantieri",
    ),
    DocumentSignature(
        "structure_setting_out_control_act",
        ("akt kontroll", "piket"),
        0.88,
        "akt kontroll + piketim",
    ),
    DocumentSignature(
        "construction_permit_conformity_declaration",
        ("deklarat", "perputh", "leje ndertimi"),
        0.9,
        "deklarate perputhshmerie + leje ndertimi",
    ),
    DocumentSignature(
        "professional_liability_insurance_policy",
        ("polic", "sigur", "pergjegjesi", "profesional"),
        0.9,
        "police sigurimi + pergjegjesi profesionale",
    ),
    DocumentSignature(
        "professional_liability_insurance_policy",
        ("kontrat", "sigur", "pergjegjesi", "profesional"),
        0.86,
        "kontrate sigurimi + pergjegjesi profesionale",
    ),
    DocumentSignature(
        "technical_administrative_document_handover_act",
        ("akt", "dorezim", "dokumentacion", "teknik", "administrativ"),
        0.89,
        "akt dorezimi + dokumentacion teknik administrativ",
    ),
    DocumentSignature(
        "site_handover_act",
        ("akt", "dorezim", "shesh"),
        0.88,
        "akt dorezimi sheshi",
    ),
    DocumentSignature(
        "start_works_notification_letter",
        ("shkrese", "njoftim", "fillim", "punim"),
        0.88,
        "shkrese njoftimi + fillim punimesh",
    ),
    DocumentSignature(
        "start_works_notification",
        ("njoftim", "fillim", "punim"),
        0.84,
        "njoftim fillimi punimesh",
    ),
    DocumentSignature(
        "start_works_minutes",
        ("procesverbal", "fillim", "punim"),
        0.86,
        "procesverbal fillimi punimesh",
    ),
    DocumentSignature(
        "completion_minutes",
        ("procesverbal", "perfundim", "punim"),
        0.86,
        "procesverbal perfundimi punimesh",
    ),
    DocumentSignature(
        "start_interruption_extension_completion_minutes",
        ("procesverbal", "nderprerje", "punim"),
        0.86,
        "procesverbal nderprerje punimesh",
    ),
    DocumentSignature(
        "start_interruption_extension_completion_minutes",
        ("procesverbal", "shtyrje", "punim"),
        0.86,
        "procesverbal shtyrje punimesh",
    ),
    DocumentSignature(
        "forty_five_day_report",
        ("raport", "45", "dit"),
        0.88,
        "raport + 45 ditor",
    ),
    DocumentSignature(
        "forty_five_day_report",
        ("raport", "dyzet e pese"),
        0.88,
        "raport + dyzet e pese dite",
    ),
    DocumentSignature(
        "supervisor_contract",
        ("kontrat", "mbikeqyr"),
        0.86,
        "kontrate + mbikeqyres",
    ),
    DocumentSignature(
        "development_permit",
        ("leje zhvillimi",),
        0.86,
        "leje zhvillimi",
    ),
    DocumentSignature(
        "construction_permit",
        ("leje ndertimi",),
        0.86,
        "leje ndertimi",
    ),
    DocumentSignature(
        "approved_execution_project",
        ("projekt zbatimi",),
        0.84,
        "projekt zbatimi",
    ),
    DocumentSignature(
        "approved_execution_project",
        ("projekti i zbatimit",),
        0.84,
        "projekti i zbatimit",
    ),
    DocumentSignature(
        "technical_opposition",
        ("oponenc", "teknik"),
        0.84,
        "oponence teknike",
    ),
    DocumentSignature(
        "bill_of_quantities",
        ("preventiv",),
        0.82,
        "preventiv",
    ),
    DocumentSignature(
        "geological_engineering_study",
        ("gjeolog", "inxhinier"),
        0.84,
        "studim gjeologo-inxhinierik",
    ),
    DocumentSignature(
        "topographic_documentation",
        ("topograf",),
        0.82,
        "dokumentacion topografik",
    ),
    DocumentSignature(
        "seismic_study",
        ("sizmik",),
        0.82,
        "studim sizmik",
    ),
    DocumentSignature(
        "construction_organization_plan",
        ("planorganizim",),
        0.84,
        "planorganizim",
    ),
    DocumentSignature(
        "construction_organization_plan",
        ("organizim", "kantier"),
        0.8,
        "organizim kantieri",
    ),
    DocumentSignature(
        "professional_license",
        ("licenc", "profesional"),
        0.82,
        "licence profesionale",
    ),
    DocumentSignature(
        "professional_license",
        ("certifikat", "profesional"),
        0.8,
        "certifikate profesionale",
    ),
    DocumentSignature(
        "setting_out_act",
        ("akt", "piket"),
        0.82,
        "akt piketimi",
    ),
    DocumentSignature(
        "site_book",
        ("libri", "kantier"),
        0.84,
        "libri i kantierit",
    ),
    DocumentSignature(
        "daily_site_log",
        ("ditari", "objekt"),
        0.82,
        "ditari i objektit",
    ),
    DocumentSignature(
        "daily_site_log",
        ("ditari", "punim"),
        0.82,
        "ditari i punimeve",
    ),
    DocumentSignature(
        "monthly_situations",
        ("situacion", "mujor"),
        0.82,
        "situacion mujor",
    ),
    DocumentSignature(
        "hidden_works_minutes",
        ("punim", "maskuar"),
        0.84,
        "punime te maskuara",
    ),
    DocumentSignature(
        "material_quality_certificate",
        ("certifikat", "ciles", "material"),
        0.86,
        "certifikate cilesie materiali",
    ),
    DocumentSignature(
        "material_quality_certificate",
        ("certifikat", "konformitet", "material"),
        0.84,
        "certifikate konformiteti materiali",
    ),
    DocumentSignature(
        "technical_declaration",
        ("deklarat", "teknik"),
        0.82,
        "deklarate teknike",
    ),
    DocumentSignature(
        "safety_documentation",
        ("siguri", "kantier"),
        0.8,
        "siguria ne kantier",
    ),
    DocumentSignature(
        "safety_documentation",
        ("sigurim teknik", "punim"),
        0.8,
        "sigurim teknik punimesh",
    ),
    DocumentSignature(
        "photo_video_documentation",
        ("foto", "video"),
        0.78,
        "foto/video",
    ),
    DocumentSignature(
        "photo_video_documentation",
        ("dokumentacion", "fotograf"),
        0.78,
        "dokumentacion fotografik",
    ),
    DocumentSignature(
        "as_built_project",
        ("azhorn", "projekt"),
        0.82,
        "projekt azhornimi",
    ),
    DocumentSignature(
        "as_built_project",
        ("as built", "projekt"),
        0.82,
        "as-built project",
    ),
    DocumentSignature(
        "maintenance_project",
        ("projekt", "mirembajt"),
        0.82,
        "projekt mirembajtjeje",
    ),
    DocumentSignature(
        "kolaudim_act",
        ("akt", "kolaudim"),
        0.82,
        "akt kolaudimi",
    ),
    DocumentSignature(
        "contract_and_related_acts",
        ("kontrat", "akte"),
        0.76,
        "kontrate dhe akte",
    ),
    DocumentSignature(
        "accounting_records",
        ("dokumentacion", "kontabil"),
        0.78,
        "dokumentacion kontabel",
    ),
    DocumentSignature(
        "accounting_records",
        ("liber", "kontabil"),
        0.78,
        "liber kontabel",
    ),
    DocumentSignature(
        "control_act",
        ("akt kontroll",),
        0.72,
        "akt kontrolli",
    ),
    DocumentSignature(
        "control_act",
        ("aktkontroll",),
        0.72,
        "aktkontroll",
    ),
)


def classify_document(filename: str, text: str | None) -> ClassificationResult:
    normalized_filename = _normalize_text(filename)
    normalized_text = _normalize_text(text or "")
    if _looks_like_legal_reference_document(normalized_filename, normalized_text):
        return ClassificationResult(
            document_type=UNKNOWN_DOCUMENT_TYPE,
            confidence=0.0,
            evidence="official law or regulation marker",
        )

    haystack = f"{normalized_filename} {normalized_text[:CLASSIFICATION_TEXT_LIMIT]}"
    best_match: ClassificationResult | None = None

    for signature in DOCUMENT_SIGNATURES:
        if _signature_matches(signature, haystack):
            result = ClassificationResult(
                document_type=signature.document_type,
                confidence=signature.confidence,
                evidence=signature.evidence,
            )
            if best_match is None or result.confidence > best_match.confidence:
                best_match = result

    if best_match is None:
        return ClassificationResult(
            document_type=UNKNOWN_DOCUMENT_TYPE,
            confidence=0.0,
            evidence=None,
        )
    return best_match


def classify_document_type(filename: str, text: str | None) -> tuple[str, float]:
    result = classify_document(filename, text)
    return result.document_type, result.confidence


def _signature_matches(signature: DocumentSignature, haystack: str) -> bool:
    return all(term in haystack for term in signature.required_terms)


def _looks_like_legal_reference_document(filename: str, text: str) -> bool:
    legal_filename_markers = ("vkm", "vendim", "ligj", "udhezim")
    legal_text_markers = (
        "keshilli i ministrave",
        "republika e shqiperise",
        "fletore zyrtare",
        "vendim nr",
        "gazeta zyrtare",
    )

    has_legal_filename = any(marker in filename for marker in legal_filename_markers)
    if not has_legal_filename:
        return False

    text_start = text[:4_000]
    marker_count = sum(marker in text_start for marker in legal_text_markers)
    return marker_count >= 1


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = normalized.replace("-", " ").replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()
