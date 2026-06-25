from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.agents.public_formatting import format_public_value
from app.core.config import settings
from app.reports.schemas import AuditReport

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _report_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Tirane")


def local_report_date(value: datetime) -> str:
    if not isinstance(value, datetime):
        return ""
    timestamp = value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(_report_timezone()).strftime("%d.%m.%Y")


def render_audit_report_html(report: AuditReport) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["public_value"] = format_public_value
    env.filters["local_report_date"] = local_report_date
    template = env.get_template("audit_report_sq.html")
    return template.render(report=report)
