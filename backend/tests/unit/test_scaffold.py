from app.files.classifier import classify_document, classify_document_type
from app.files.parser import is_supported_filename
from app.files.status import is_parsed_status
from app.laws.parser import split_law_articles
from app.telegram.messages import WELCOME_MESSAGE


def test_supported_filename() -> None:
    assert is_supported_filename("dosja.pdf")
    assert is_supported_filename("dosja.zip")
    assert is_supported_filename("Kronologjia.mpp")
    assert not is_supported_filename("script.exe")


def test_ocr_and_native_parse_statuses_are_usable_evidence() -> None:
    assert is_parsed_status("parsed")
    assert is_parsed_status("parsed_with_ocr")
    assert not is_parsed_status("needs_ocr")


def test_basic_classifier_is_conservative() -> None:
    assert classify_document_type("file.txt", "pa lidhje")[0] == "unknown"
    assert (
        classify_document_type("raport_45.pdf", "raportim 45 ditor")[0]
        == "forty_five_day_report"
    )


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
    assert classify_document_type("Kronologjia.mpp", "")[0] == "project_schedule"


def test_classifier_prefers_explicit_official_permit_header_over_schedule_terms() -> None:
    text = (
        "BASHKIA FIER Nr.4571/4 Prot. LEJE NDËRTIMI Nr. 274, Datë 21.08.2020 "
        "I JEPET: SHEZAI ÇOBO. AFATI KOHOR I KËSAJ LEJE dhe grafiku i punimeve."
    )

    result = classify_document("4571.4 LEJE KRYETAR.pdf", text)

    assert result.document_type == "construction_permit"
    assert result.confidence == 0.99


def test_classifier_handles_real_dossier_filenames() -> None:
    examples = {
        "0. Kontrate Kolaudatorin me z.Naqe Bala.docx": "contract_and_related_acts",
        "0. Kontrate Mbiqkyrsin me z. Oltion Kaba.docx": "supervisor_contract",
        "0.0 Ditari i Punimeve OK.docx": "daily_site_log",
        "1.1 Njoftim Fillim Punimesh OK.docx": "start_works_notification",
        "1.2 Proces Verbal Akt Dorezim Sheshi OK.docx": "site_handover_act",
        "1.3 Proces Verbal Fillim OK.docx": "start_works_minutes",
        "1.4  Proces Verbal Akt Piketim OK.docx": "setting_out_act",
        "1.5 [1] Akt kontroll i ngritjes se kantierit.docx": "site_setup_control_act",
        "1.5 [2] Akt kontroll Piketim.docx": "structure_setting_out_control_act",
        "1.9.1.1 Proces verb.punim.mask.Form-1(plintat).docx": "hidden_works_minutes",
        "2.0 [3] Akt kontrolli Përfundimi i themeleve.docx": (
            "foundation_completion_and_level_0_00_control_act"
        ),
        "3.1 [4] Akt Kontrolli Përfundimi i karabinasë.docx": (
            "structural_frame_completion_control_act"
        ),
        "4.1 [5] Akt kontrolli Përfundimi i fasadave dhe rifiniturave.docx": (
            "facade_and_finishing_completion_control_act"
        ),
        "5.1 [6] Akt kontrolli Përfundimi i sistemit të jashtëm.docx": (
            "external_system_completion_control_act"
        ),
        "6.2 Projekt per mirmbajtjen e Objektit.docx": "maintenance_project",
        "7. Deklarat Konformiteti Sipermarresi.docx": (
            "construction_permit_conformity_declaration"
        ),
        "deklart mbikqyresi alisha kerpi.docx": "technical_declaration",
        "Kronologjia.mpp": "project_schedule",
        "X.Akt Kolaudimi.docx": "kolaudim_act",
    }

    for filename, expected_type in examples.items():
        assert classify_document_type(filename, "")[0] == expected_type


def test_explicit_real_dossier_filename_overrides_template_body() -> None:
    template_text = (
        "Ne baze te kontrates me mbikqyresin dhe lejes se ndertimit, "
        "mbikqyresi deklaron detyrimet e tij."
    )
    examples = {
        "1.1 Njoftim Fillim Punimesh OK.docx": "start_works_notification",
        "1.2 Proces Verbal Akt Dorezim Sheshi OK.docx": "site_handover_act",
        "1.5 [1] Akt kontroll i ngritjes se kantierit.docx": "site_setup_control_act",
        "1.5 [2] Akt kontroll Piketim.docx": "structure_setting_out_control_act",
        "X.Akt Kolaudimi.docx": "kolaudim_act",
    }

    for filename, expected_type in examples.items():
        assert classify_document_type(filename, template_text)[0] == expected_type


def test_phase_completion_notice_is_not_supervisor_contract() -> None:
    template_text = (
        "Kontrata me mbikqyresin dhe studimi gjeologo-inxhinierik permenden "
        "ne tekstin standard te dokumentit."
    )

    examples = [
        "1.6 Proces Verbal mbi perfundimin e Germimit te Themeleve .docx",
        "1.7 Proces Verbal mbi Kontrollin e tabanit te themeleve.docx",
        "2.2 Njoftim mbi perfundimin e Ndertimit te Themeleve deri ne 0.00.docx",
    ]

    for filename in examples:
        assert classify_document_type(filename, template_text)[0] == "unknown"


def test_supervisor_contract_is_not_classified_as_report_when_it_mentions_45_days() -> None:
    result = classify_document_type(
        "0. Kontrate Mbiqkyrsin me z. Oltion Kaba.docx",
        "Kontrate me mbikqyresin e punimeve. Detyrimi per raportim cdo 45 dite.",
    )

    assert result[0] == "supervisor_contract"


def test_classifier_does_not_treat_vkm_law_as_project_document() -> None:
    text = "Keshilli i Ministrave Vendim nr. 610. Aktkontrolli ne kuoten 0.00."
    assert classify_document_type("Vendim_nr_610_date_22_9_2022.pdf", text)[0] == "unknown"


def test_split_law_articles() -> None:
    articles = split_law_articles("Neni 1\nTeksti A\n\nNeni 2\nTeksti B")
    assert articles == [("1", "Teksti A"), ("2", "Teksti B")]


def test_albanian_welcome_message() -> None:
    assert "Mirë se erdhët" in WELCOME_MESSAGE
