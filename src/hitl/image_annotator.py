from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from src.agent.state import FormFieldSpec


def highlight_field_on_screenshot(
    screenshot_path: Path,
    bounding_box: dict[str, int],
    output_path: Path,
) -> Path:
    """Draws a high-visibility red bounding box around the uncertain input field for Telegram HITL cards."""
    img = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    x = bounding_box.get("x", 0)
    y = bounding_box.get("y", 0)
    w = bounding_box.get("width", 200)
    h = bounding_box.get("height", 50)
    draw.rectangle([x - 4, y - 4, x + w + 4, y + h + 4], outline="#FF1744", width=4)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def render_hitl_preview_card(
    output_path: Path,
    company_name: str,
    role_title: str,
    portal: str,
    low_confidence_fields: list[FormFieldSpec],
    is_dream: bool = False,
    security_blocker: bool = False,
) -> Path:
    """Generates a visual annotated HITL preview card when no live browser screenshot exists yet."""
    img = Image.new("RGB", (920, 540), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)

    draw.rectangle([24, 24, 896, 118], fill=(30, 41, 59), outline=(71, 85, 105), width=2)
    draw.text((44, 42), f"AUTOAPPLY-AI HITL CHECKPOINT  |  {portal.upper()}", fill=(248, 250, 252))
    draw.text((44, 68), f"{company_name} — {role_title}", fill=(56, 189, 248))
    flags = []
    if is_dream:
        flags.append("DREAM COMPANY REVIEW")
    if security_blocker:
        flags.append("SECURITY BLOCKER / OTP")
    if low_confidence_fields:
        flags.append(f"{len(low_confidence_fields)} LOW-CONFIDENCE FIELD(S)")
    draw.text((44, 92), " | ".join(flags) or "SUPERVISED REVIEW", fill=(251, 191, 36))

    y = 142
    if not low_confidence_fields:
        draw.rectangle([36, y, 884, y + 110], fill=(30, 41, 59), outline=(255, 23, 68), width=4)
        draw.text((54, y + 20), "Manual Approval Required Before Submission", fill=(248, 250, 252))
        draw.text(
            (54, y + 48),
            "Paused by Dream Company safeguard or security verification gate.",
            fill=(203, 213, 225),
        )
    else:
        for idx, field in enumerate(low_confidence_fields[:3], 1):
            box_bottom = y + 112
            draw.rectangle([36, y, 884, box_bottom], fill=(30, 41, 59), outline=(255, 23, 68), width=4)
            conf = field.get("confidence", 0.0)
            label = str(field.get("label_text", field.get("field_id", "")))[:95]
            draft = str(field.get("proposed_value") or "<Needs Human Input>")[:95]
            draw.text((54, y + 16), f"[{idx}] {label}  (conf={conf:.2f})", fill=(248, 113, 113))
            draw.text((54, y + 48), f"Draft: {draft}", fill=(226, 232, 240))
            draw.text((54, y + 76), f"Reason: {str(field.get('reasoning', ''))[:95]}", fill=(148, 163, 184))
            y = box_bottom + 16

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
