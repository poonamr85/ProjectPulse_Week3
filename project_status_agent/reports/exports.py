"""Downloadable reports rendered from the same reviewed report payload."""

import base64
import json
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, RGBColor
from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

from project_status_agent.reports.content import render_report
from project_status_agent.ui.charts import report_chart_assets


def download_dropdown(report: dict, status: str, basename: str) -> str:
    """A native select that downloads on user selection, without a second button."""
    formats = [
        (
            word_bytes,
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (pdf_bytes, ".pdf", "application/pdf"),
        (markdown_bytes, ".md", "text/markdown"),
    ]
    files = [
        {
            "name": basename + extension,
            "mime": mime,
            "data": base64.b64encode(exporter(report, status)).decode("ascii"),
        }
        for exporter, extension, mime in formats
    ]
    payload = json.dumps(files).replace("<", "\\u003c")
    return (
        """<!doctype html><html><head><style>
    body{margin:0;font:14px system-ui;color:#172b3a}
    label{display:block;margin-bottom:7px}
    select{width:100%;max-width:460px;padding:11px;border:1px solid #cbd5df;
    border-radius:8px;background:#edf2f5;color:#172b3a;font:inherit}
    select:focus{outline:2px solid #0f766e;outline-offset:2px}
    </style></head><body><label for="format">Report format</label>
    <select id="format"><option value="">Choose a download format…</option>
    <option value="0">Download Word</option><option value="1">Download PDF</option>
    <option value="2">Download Markdown</option></select>
    <script>const files="""
        + payload
        + """;
    document.getElementById('format').addEventListener('change',function(){
      if(this.value==='')return;
      const file=files[Number(this.value)];
      const bytes=Uint8Array.from(atob(file.data),c=>c.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:file.mime}));
      const link=document.createElement('a');link.href=url;link.download=file.name;
      document.body.appendChild(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),30000);this.value='';
    });</script></body></html>"""
    )


def display_status(status: str) -> str:
    """An exported draft must never look approved."""
    if status == "saved":
        return "Approved"
    if status == "rejected":
        return "Rejected — not approved"
    return "Draft — not approved"


def plain_text(text: str) -> str:
    return re.sub(r"\*\*(.*?)\*\*", r"\1", text)


def markdown_bytes(report: dict, status: str) -> bytes:
    text = render_report(report, display_status(status))
    graphics = ["\n## Progress graphs\n", chart_caption(report)]
    for asset in report_chart_assets(report):
        data = base64.b64encode(asset["svg"].encode()).decode("ascii")
        graphics += [
            f"\n### {asset['title']}\n",
            f'<img alt="{asset["title"]}" src="data:image/svg+xml;base64,{data}" />\n',
        ]
    text = text.replace(
        "\n## Proposed risk mitigation plan",
        "\n".join(graphics) + "\n## Proposed risk mitigation plan",
        1,
    )
    return text.encode("utf-8")


def chart_caption(report: dict) -> str:
    trend = report["facts"]["project_trends"]
    start = ", ".join(trend["start_observation_dates"]) or "No baseline"
    end = ", ".join(trend["end_observation_dates"]) or "No observation"
    return f"Actual project observation dates: {start} to {end}. Owner chart shows current-sprint task counts, not hours or capacity."


def word_bytes(report: dict, status: str) -> bytes:
    """Create an editable .docx with headings, bullets, and numbered pages."""
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.7)
    section.left_margin = section.right_margin = Inches(0.8)
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(7)
    for name in ("Title", "Heading 1", "Heading 2"):
        document.styles[name].font.color.rgb = RGBColor.from_string("123E48")
    section.header.paragraphs[0].text = "PROJECT PULSE  /  WEEKLY DELIVERY REPORT"
    footer = section.footer.paragraphs[0]
    footer.text = f"{display_status(status)}  •  {report['project_id']}  •  Page "
    page = OxmlElement("w:fldSimple")
    page.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}instr", "PAGE"
    )
    footer._p.append(page)
    document.core_properties.title = f"Weekly status — {report['project_id']}"
    document.core_properties.subject = (
        f"Version {report['version']} | {display_status(status)}"
    )
    for line in render_report(report, display_status(status)).splitlines():
        if not line.strip():
            continue
        text = plain_text(line)
        if text == "## Proposed risk mitigation plan":
            document.add_heading("Progress graphs", level=1)
            document.add_paragraph(chart_caption(report))
            for asset in report_chart_assets(report):
                document.add_heading(asset["title"], level=2)
                document.add_picture(BytesIO(asset["png"]), width=Inches(6.4))
        if text.startswith("### "):
            document.add_heading(text[4:], level=2)
        elif text.startswith("## "):
            document.add_heading(text[3:], level=1)
        elif text.startswith("# "):
            document.add_heading(text[2:], level=0)
        elif text.startswith("- "):
            document.add_paragraph(text[2:], style="List Bullet")
        else:
            document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes(report: dict, status: str) -> bytes:
    """Create a paginated, selectable-text PDF with clear version/status labels."""
    fonts = Path(reportlab.__file__).parent / "fonts"
    if "Pulse" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Pulse", str(fonts / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("PulseBold", str(fonts / "VeraBd.ttf")))
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "PulseBody",
            fontName="Pulse",
            fontSize=9,
            leading=14,
            spaceAfter=7,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            "PulseTitle",
            fontName="PulseBold",
            fontSize=21,
            leading=28,
            spaceAfter=12,
            textColor=colors.HexColor("#123e48"),
        )
    )
    styles.add(
        ParagraphStyle(
            "PulseHeading",
            fontName="PulseBold",
            fontSize=13,
            leading=18,
            spaceBefore=13,
            spaceAfter=7,
            textColor=colors.HexColor("#0f766e"),
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            "PulseSubheading", parent=styles["PulseHeading"], fontSize=10, leading=15
        )
    )
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"Weekly status — {report['project_id']}",
        author="Project Pulse",
    )
    story = []
    for line in render_report(report, display_status(status)).splitlines():
        if not line.strip():
            continue
        text = plain_text(line)
        if text == "## Proposed risk mitigation plan":
            story.append(Paragraph("Progress graphs", styles["PulseHeading"]))
            story.append(Paragraph(escape(chart_caption(report)), styles["PulseBody"]))
            for asset in report_chart_assets(report):
                story.append(
                    Paragraph(escape(asset["title"]), styles["PulseSubheading"])
                )
                with PillowImage.open(BytesIO(asset["png"])) as image:
                    image_width, image_height = image.size
                width = min(document.width, 480)
                height = width * image_height / image_width
                # Fit each whole chart on a page without stretching its aspect ratio.
                scale = min(1, 440 / height)
                story.append(
                    Image(
                        BytesIO(asset["png"]),
                        width=width * scale,
                        height=height * scale,
                    )
                )
                story.append(Spacer(1, 12))
        style = styles["PulseBody"]
        if text.startswith("### "):
            text, style = text[4:], styles["PulseSubheading"]
        elif text.startswith("## "):
            text, style = text[3:], styles["PulseHeading"]
        elif text.startswith("# "):
            text, style = text[2:], styles["PulseTitle"]
        elif text.startswith("- "):
            text = "• " + text[2:]
        story.append(Paragraph(escape(text), style))
    story.append(Spacer(1, 8))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Pulse", 7)
        canvas.setFillColor(colors.HexColor("#526573"))
        canvas.drawString(
            doc.leftMargin,
            0.35 * inch,
            f"PROJECT PULSE | {display_status(status)} | v{report['version']}",
        )
        canvas.drawRightString(A4[0] - doc.rightMargin, 0.35 * inch, str(doc.page))
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
