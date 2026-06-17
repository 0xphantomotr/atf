from weasyprint import HTML


def html_to_pdf_bytes(html: str) -> bytes:
    return HTML(string=html).write_pdf()

