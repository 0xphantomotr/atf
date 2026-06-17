from app.files.classifier import classify_document_type
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


def test_split_law_articles() -> None:
    articles = split_law_articles("Neni 1\nTeksti A\n\nNeni 2\nTeksti B")
    assert articles == [("1", "Teksti A"), ("2", "Teksti B")]


def test_albanian_welcome_message() -> None:
    assert "Mirë se erdhët" in WELCOME_MESSAGE

