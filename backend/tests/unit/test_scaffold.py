from app.files.classifier import classify_document, classify_document_type
from app.files.parser import is_supported_filename
from app.laws.parser import split_law_articles
from app.telegram.messages import WELCOME_MESSAGE


def test_supported_filename() -> None:
    assert is_supported_filename("dosja.pdf")
    assert is_supported_filename("dosja.zip")
    assert not is_supported_filename("script.exe")


def test_basic_classifier_is_conservative() -> None:
    assert classify_document_type("file.txt", "pa lidhje")[0] == "unknown"
    assert classify_document_type("raport_45.pdf", "raportim 45 ditor")[0] == "forty_five_day_report"


def test_classifier_tracks_confidence_and_evidence() -> None:
    result = classify_document(
        "polica.pdf",
        "Polica e sigurimit te pergjegjesise profesionale per mbikeqyresin.",
    )
    assert result.document_type == "professional_liability_insurance_policy"
    assert result.confidence >= 0.85
    assert result.evidence is not None


def test_classifier_handles_common_vkm_610_documents() -> None:
    assert classify_document_type("leje_ndertimi.pdf", "")[0] == "construction_permit"
    assert classify_document_type("libri_i_kantierit.pdf", "")[0] == "site_book"
    assert classify_document_type("akt_kontrolli_0.00.pdf", "")[0] == "level_0_00_control_act"


def test_classifier_does_not_treat_vkm_law_as_project_document() -> None:
    text = "Keshilli i Ministrave Vendim nr. 610. Aktkontrolli ne kuoten 0.00."
    assert classify_document_type("Vendim_nr_610_date_22_9_2022.pdf", text)[0] == "unknown"


def test_split_law_articles() -> None:
    articles = split_law_articles("Neni 1\nTeksti A\n\nNeni 2\nTeksti B")
    assert articles == [("1", "Teksti A"), ("2", "Teksti B")]


def test_albanian_welcome_message() -> None:
    assert "Mirë se erdhët" in WELCOME_MESSAGE
