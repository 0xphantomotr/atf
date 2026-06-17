import re

ARTICLE_RE = re.compile(r"(?m)^\s*Neni\s+(\d+)\s*$")


def split_law_articles(text: str) -> list[tuple[str, str]]:
    matches = list(ARTICLE_RE.finditer(text))
    articles: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        articles.append((match.group(1), text[start:end].strip()))
    return articles

