from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.agents.public_formatting import format_public_value
from app.reports.schemas import AuditReport

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_audit_report_html(report: AuditReport) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["public_value"] = format_public_value
    template = env.get_template("audit_report_sq.html")
    return template.render(report=report)
