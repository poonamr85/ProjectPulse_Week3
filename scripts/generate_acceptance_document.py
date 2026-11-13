"""Regenerate the current acceptance Word/PDF from preserved evidence.

Run: python -m scripts.generate_acceptance_document
No API requests, data sync, model calls, or report approvals are performed.
"""

import json
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import reportlab
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def main():
    root = Path(__file__).resolve().parents[1]
    blocks = []

    def h(t, l=1):
        blocks.append(("h", t, l))

    def p(t):
        blocks.append(("p", t))

    def bullet(t):
        blocks.append(("bullet", t))

    def tbl(head, rows):
        blocks.append(("table", head, rows))

    def page():
        blocks.append(("page",))

    def link(label, url):
        blocks.append(("link", label, url))

    current = json.loads(
        (root / "data/acceptance/grounding-evaluations.json").read_text()
    )
    blocks = []
    h("Project Pulse — Acceptance Tests & Evidence", 0)
    h("1. Data baseline")
    tbl(
        ["Baseline", "Reference"],
        [
            ("Model / scope", "GLM-5.3-Flash · PRJ-001 · Notion-backed saved observations · reporting cutoff November 10, 2026."),
            ("Dataset", "Synthetic demonstration: 20 tasks, 3 sprints, 200 historical observations."),
            ("Current Sprint 3", "12 assigned tasks: 7 Done, 3 Blocked, 2 In progress. Planned sprint membership differs from current membership."),
            ("Recorded history", "Status, assigned sprint, blocker notes at dated snapshots. Owner, priority, due dates, and dependencies are current attributes."),
            ("Review method", "Answers checked against tool results and the shared field contract. This is selected Q&A evidence, not full application sign-off."),
        ],
    )
    h("Test Categories", 2)
    tbl(
        ["Category", "Included checks"],
        [
            ("Project Analysis & Evidence Accuracy", "G01–G06 and the facts-versus-forecasts retest."),
            ("Failure Handling & Recovery", "F01: Mem0 failure; F02: Notion refresh failure."),
            ("Human-in-the-Loop & Approval", "H01: refresh cancellation; H02: report revision and approval."),
            ("Supporting Automated Checks", "Stale-approval rejection, checkpoint recovery and duplicate-save prevention; covered by the existing regression suite."),
        ],
    )
    for index, item in enumerate(current["tests"]):
        if index % 2 == 0:
            page()
            h("2. Project Analysis & Evidence Accuracy" + (" — continued" if index else ""))
        h(item["test"] + " · " + item["title"], 2)
        local = datetime.fromisoformat(item["start_time"]).astimezone(ZoneInfo("America/Los_Angeles"))
        p(f"{local.strftime('%b %d, %Y %I:%M:%S %p %Z')} · {item['duration_seconds']:.2f} seconds · {item['assessment']}")
        calls = [
            f"{call['tool']} {json.dumps(call.get('arguments', {}), sort_keys=True)} — {call['outcome']}"
            for call in item["tool_trace"]
        ]
        decision = (
            "Retrieved relevant records, then answered from the returned evidence."
            if calls else
            "Answered using successful read-tool evidence already retained in the same project/source/cutoff conversation; no new tool was needed."
        )
        tbl(
            ["Test detail", "Reviewed outcome"],
            [
                ("Question", item["question"]),
                ("Expected", item["expected"]),
                ("Actual answer — excerpt", "“" + item["actual_answer_excerpt"] + "”"),
                ("Reviewer assessment", item["assessment"] + " — " + item["review_findings"]),
                ("Model calls", f"{item['model_calls']} calls; {item['model_spans']} verified model spans."),
                ("Tool calls", f"{item['tool_calls']} new calls; {item['tool_errors']} errors; {item['reused_evidence_count']} reusable observations reported.\n" + ("\n".join(calls) or "No new tool calls.")),
                ("Observed decision", decision),
            ],
        )
        link("Open " + item["title"] + " trace", item["trace_url"])
        p("Copyable trace URL: " + item["trace_url"])
    page()
    h("2. Project Analysis & Evidence Accuracy — continued")
    retest = current["supplemental_retest"]
    h("Supplemental check · Recorded Facts vs. Forecasts", 2)
    tbl(
        ["Check", "Evidence"],
        [
            ("Question", retest["question"]),
            ("Result", retest["review_findings"]),
            ("Execution", "2 model calls; query_sprint_history and risk_scorer succeeded; 0 tool errors; no reused conversation evidence."),
        ],
    )
    link("Open facts-versus-forecast retest trace", retest["trace_url"])
    p("Copyable trace URL: " + retest["trace_url"])
    p("Full reviewed answers, tool observations and trace references: data/acceptance/grounding-evaluations.json. Offline checks: data/acceptance/regression-tests.txt.")
    failures = json.loads(
        (root / "data/acceptance/failure-recovery-evaluations.json").read_text()
    )
    for item in failures["tests"]:
        page()
        h("3. Failure Handling & Recovery")
        h(item["test"] + " · " + item["title"], 2)
        local = datetime.fromisoformat(item["start_time"]).astimezone(ZoneInfo("America/Los_Angeles"))
        p(f"{local.strftime('%b %d, %Y %I:%M:%S %p %Z')} · GLM-5.3-Flash · {item['assessment']}")
        calls = []
        for number, call in enumerate(item["trace"], 1):
            detail = f"{number}. {call['tool']} {json.dumps(call.get('arguments', {}), sort_keys=True)} — {call['outcome']}"
            if call.get("error"):
                detail += ": " + str(call["error"])
            calls.append(detail)
        tbl(
            ["Test detail", "Verified outcome"],
            [
                ("Scenario", item["scenario"]),
                ("Question", item["question"]),
                ("Expected", item["expected"]),
                ("Actual", item["actual"]),
                ("Actual answer — excerpt", "“" + item["excerpt"] + "”"),
                ("What the tools did", "\n".join(calls)),
                ("Agent recovery decision", item["decision"]),
                ("Calls / evidence", f"{item['model_calls']} model calls; {item['tool_calls']} tool calls; {item['tool_errors']} tool error. {item['verified_model_spans']} model spans verified in the linked trace. " + item["call_count_scope"]),
                ("Reviewer assessment", item["review"]),
            ],
        )
        link("Open " + item["test"] + " failure-recovery trace", item["trace_url"])
        p("Copyable trace URL: " + item["trace_url"])
    p("Failure-recovery evidence: data/acceptance/failure-recovery-evaluations.json. Existing HTTP 401 tool outputs and successful fallback observations were inspected; no new model requests were made for this document update.")
    human_tests = json.loads(
        (root / "data/acceptance/human-review-evaluations.json").read_text()
    )
    for item in human_tests["tests"]:
        page()
        h("4. Human-in-the-Loop & Approval")
        h(item["test"] + " · " + item["title"], 2)
        p(item["assessment"] + " · existing saved decisions and verified traces")
        tbl(
            ["Test detail", "Verified outcome"],
            [
                ("Scenario", item["scenario"]),
                ("Request", item["question"]),
                ("Expected", item["expected"]),
                ("Human decision", item["human_decision"]),
                ("Actual", item["actual"]),
                (item.get("excerpt_label", "Actual answer — excerpt"), "“" + item["excerpt"] + "”"),
                ("What the tools / workflow did", item["tool_behavior"]),
                ("Pause and resume", item["state_transition"]),
                ("Reviewer assessment", item["review"]),
            ],
        )
        for reference in item["trace_links"]:
            link("Open " + item["test"] + " — " + reference["label"], reference["url"])
            p("Copyable trace URL: " + reference["url"])
    p("Human review evidence: data/acceptance/human-review-evaluations.json. Existing checkpoint interrupts, decisions and trace stages inspected; no new approvals or model calls made for this update.")
    page()
    h("5. Supporting Automated Checks")
    tbl(
        ["Check", "Evidence / result"],
        [
            ("Stale approval — PASS", "test_revision_requires_new_approval: revision creates v2; approval of v1 is rejected as stale; v2 requires its own approval."),
            ("Restart recovery — PASS", "test_approval_survives_restart_and_is_idempotent: a fresh workflow instance reopens the checkpoint, retains Awaiting review, and completes approval without duplicate saved reports."),
        ],
    )
    p("Source: tests/test_reports.py; existing regression run: 103 tests PASS (data/acceptance/regression-tests.txt). Automated persistence/review evidence; no new manual UI test performed for this update.")
    h("6. Selected Test Results Summary")
    tbl(
        ["Area", "Result"],
        [
            ("Selected Q&A tests", "6/6 PASS against the available project evidence."),
            ("Execution evidence", "All six LangSmith traces verified; tool calls and historical-evidence reuse recorded."),
            ("Review scope", "Six grounding scenarios, two failure-recovery scenarios, and two human-in-the-loop scenarios have separate evidence. Broader application acceptance is assessed separately."),
        ],
    )
    out = root / "docs/Project_Pulse_Acceptance_Tests_and_Evidence"
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = Inches(0.6)
    sec.left_margin = sec.right_margin = Inches(0.65)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].paragraph_format.space_after = Pt(5)
    for name in ["Heading 1", "Heading 2"]:
        doc.styles[name].font.color.rgb = RGBColor.from_string("0F766E")
    sec.header.paragraphs[0].text = "PROJECT PULSE | ACCEPTANCE SCENARIOS & EVIDENCE"
    foot = sec.footer.paragraphs[0]
    foot.text = "Synthetic project records • Page "
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    foot._p.append(fld)
    fonts = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("Vera", str(fonts / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("VeraBold", str(fonts / "VeraBd.ttf")))
    styles = getSampleStyleSheet()
    for name in ["Normal", "Heading1", "Heading2", "Title"]:
        styles[name].fontName = "VeraBold" if name != "Normal" else "Vera"
        styles[name].fontSize = {
            "Normal": 9,
            "Heading1": 15,
            "Heading2": 11,
            "Title": 22,
        }[name]
        styles[name].leading = styles[name].fontSize * 1.35
        styles[name].spaceAfter = 7
    small = ParagraphStyle("SmallCell", parent=styles["Normal"], fontSize=8, leading=10)
    story = []

    def para(t, style="Normal"):
        return Paragraph(escape(str(t)).replace("\n", "<br/>"), styles[style])

    def addlink(label, url):
        paragraph = doc.add_paragraph()
        hlink = OxmlElement("w:hyperlink")
        hlink.set(
            qn("r:id"), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        )
        run = OxmlElement("w:r")
        props = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0F766E")
        props.append(color)
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        props.append(underline)
        run.append(props)
        txt = OxmlElement("w:t")
        txt.text = label
        run.append(txt)
        hlink.append(run)
        paragraph._p.append(hlink)
        story.append(
            Paragraph(
                '<a href="'
                + escape(url, quote=True)
                + '">'
                + "<u>"
                + escape(label)
                + "</u></a>",
                styles["Normal"],
            )
        )

    for b in blocks:
        if b[0] == "h":
            doc.add_heading(b[1], b[2])
            story.append(para(b[1], "Title" if b[2] == 0 else "Heading" + str(b[2])))
        elif b[0] in ["p", "bullet"]:
            doc.add_paragraph(b[1], style="List Bullet" if b[0] == "bullet" else None)
            story.append(para(("• " if b[0] == "bullet" else "") + b[1]))
        elif b[0] == "page":
            doc.add_page_break()
            story.append(PageBreak())
        elif b[0] == "link":
            addlink(b[1], b[2])
        elif b[0] == "table":
            headers, rows = (b[1], b[2])
            t = doc.add_table(rows=1, cols=len(headers))
            t.style = "Light Shading Accent 1"
            for c, v in zip(t.rows[0].cells, headers):
                c.text = v
            marker = OxmlElement("w:tblHeader")
            t.rows[0]._tr.get_or_add_trPr().append(marker)
            for row in rows:
                for c, v in zip(t.add_row().cells, row):
                    c.text = str(v)
            data = [
                [Paragraph(escape(str(v)), small) for v in row]
                for row in [headers, *rows]
            ]
            width = A4[0] - 2 * 42
            fractions = {
                2: [0.28, 0.72],
                3: [0.25, 0.39, 0.36],
                4: [0.1, 0.38, 0.32, 0.2],
            }[len(headers)]
            pt = Table(
                data,
                colWidths=[width * x for x in fractions],
                repeatRows=1,
                hAlign="LEFT",
            )
            pt.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EEEB")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D1D5DB")),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.extend([pt, Spacer(1, 9)])

    doc.save(str(out) + ".docx")

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Vera", 8)
        canvas.drawString(
            42, 22, "Project Pulse • Synthetic demonstration • GLM-5.3-Flash"
        )
        canvas.drawRightString(A4[0] - 42, 22, str(document.page))
        canvas.restoreState()

    SimpleDocTemplate(
        str(out) + ".pdf",
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=38,
        bottomMargin=38,
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    print(out)
    print(
        "Content blocks:",
        len(blocks),
        "Recorded cases:",
        len(current["tests"]),
        "Direct trace links:",
        sum(b[0] == "link" for b in blocks),
    )


if __name__ == "__main__":
    main()
