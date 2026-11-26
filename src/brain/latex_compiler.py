from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

LATEX_SPECIAL_CHARS = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    """Escapes special LaTeX characters in LLM-generated strings."""
    return "".join(LATEX_SPECIAL_CHARS.get(c, c) for c in text)


def unescape_latex(text: str) -> str:
    """Restores plain readable text from LaTeX-escaped strings for ReportLab fallback rendering."""
    cleaned = text
    for raw, escaped in LATEX_SPECIAL_CHARS.items():
        cleaned = cleaned.replace(escaped, raw)
    return cleaned


def create_latex_jinja_env(template_dir: str | Path = "templates") -> Environment:
    """Configures Jinja2 with custom delimiters so LaTeX curly braces never conflict."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        trim_blocks=True,
        autoescape=False,
    )
    env.filters["escape_latex"] = escape_latex
    return env


def render_tailored_tex(
    context_data: dict[str, Any],
    output_tex_path: Path,
    template_dir: str | Path = "templates",
) -> Path:
    env = create_latex_jinja_env(template_dir)
    template = env.get_template("resume_master.tex.j2")
    rendered = template.render(**context_data)
    output_tex_path.parent.mkdir(parents=True, exist_ok=True)
    output_tex_path.write_text(rendered, encoding="utf-8")
    return output_tex_path


def compile_tex_to_pdf(tex_path: Path) -> Path:
    """Compiles a .tex file into a PDF using Tectonic CLI."""
    subprocess.run(
        ["tectonic", "-X", "compile", str(tex_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return tex_path.with_suffix(".pdf")


def render_fallback_pdf(context_data: dict[str, Any], pdf_path: Path) -> Path:
    """
    Renders a clean, ATS-readable 1-page A4 resume PDF via ReportLab when Tectonic CLI is absent.
    Falls back to a minimal PDF byte header only if ReportLab is unavailable.
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except ImportError:
        pdf_path.write_bytes(b"%PDF-1.4\n% Fallback compiled PDF\n%%EOF\n")
        return pdf_path

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4
    margin_x = 0.55 * inch
    y = height - 0.55 * inch

    def draw_wrapped(text: str, font: str = "Helvetica", size: float = 9.5, indent: float = 0.0, max_chars: int = 96) -> None:
        nonlocal y
        c.setFont(font, size)
        lines = textwrap.wrap(unescape_latex(text), width=max_chars) or [""]
        for line in lines:
            if y < 0.55 * inch:
                c.showPage()
                y = height - 0.55 * inch
                c.setFont(font, size)
            c.drawString(margin_x + indent, y, line)
            y -= size + 3.2

    def draw_section(title: str) -> None:
        nonlocal y
        y -= 6
        if y < 0.8 * inch:
            c.showPage()
            y = height - 0.55 * inch
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_x, y, title.upper())
        y -= 4
        c.setLineWidth(0.6)
        c.line(margin_x, y, width - margin_x, y)
        y -= 13

    cand = context_data.get("candidate", {})
    name = unescape_latex(str(cand.get("name", "Candidate")))
    contact = " | ".join(
        unescape_latex(str(part))
        for part in [cand.get("location", ""), cand.get("email", ""), cand.get("phone", "")]
        if str(part).strip()
    )

    c.setFont("Helvetica-Bold", 17)
    c.drawCentredString(width / 2.0, y, name)
    y -= 16
    c.setFont("Helvetica", 9.5)
    c.drawCentredString(width / 2.0, y, contact)
    y -= 12

    draw_section("Summary")
    draw_wrapped(str(context_data.get("tailored_summary", "")))

    draw_section("Technical Skills")
    for category, skill_list in (context_data.get("skills") or {}).items():
        draw_wrapped(f"{category}: {skill_list}", font="Helvetica", size=9.2, max_chars=96)

    draw_section("Selected AI & Engineering Projects")
    for proj in context_data.get("selected_projects") or []:
        title = unescape_latex(str(proj.get("title", "")))
        stack = unescape_latex(str(proj.get("tech_stack", "")))
        draw_wrapped(f"{title} ({stack})", font="Helvetica-Bold", size=9.8, max_chars=90)
        for bullet in proj.get("bullets") or []:
            draw_wrapped(f"- {bullet}", font="Helvetica", size=9.2, indent=10, max_chars=92)
        y -= 3

    edu = context_data.get("education") or {}
    if edu:
        draw_section("Education")
        edu_line = f"{edu.get('degree', '')} - {edu.get('institution', '')} ({edu.get('graduation_year', '')})"
        draw_wrapped(edu_line, font="Helvetica-Bold", size=9.5, max_chars=94)

    c.save()
    return pdf_path
