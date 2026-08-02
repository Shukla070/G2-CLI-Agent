from __future__ import annotations


def render_confidence_banner(confidence: str) -> str:
    """Return a simple, presentable confidence label for the CLI."""
    return f"Confidence: {confidence}"


def render_option_menu(options: list[str]) -> str:
    """Render a simple numbered option menu string for the CLI."""
    lines = ["Options:"]
    for idx, option in enumerate(options, start=1):
        lines.append(f"{idx}. {option}")
    return "\n".join(lines)
