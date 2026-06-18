import re
from io import BytesIO

from pypdf import PdfReader

ARTICLE_RE = re.compile(r"(?m)^\s*Neni\s+(\d+)\s*$")


def split_law_articles(text: str) -> list[tuple[str, str]]:
    matches = list(ARTICLE_RE.finditer(text))
    articles: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        articles.append((match.group(1), text[start:end].strip()))
    return articles


def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, int, int]:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_texts: list[str] = []
    pages_with_text = 0

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages_with_text += 1
            page_texts.append(f"--- Faqe {index} ---\n{text}")

    return "\n\n".join(page_texts), len(reader.pages), pages_with_text
