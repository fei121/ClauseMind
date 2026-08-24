"""Small, domain-neutral Markdown cleanup helpers used by the demo pipeline."""

import re


def repair_markdown(markdown: str) -> str:
    """Normalize headings and whitespace without applying customer-specific rules."""
    if not markdown:
        return markdown

    lines = []
    for line in markdown.splitlines():
        line = line.rstrip()
        match = re.match(r"^(#{1,6})\s*$", line)
        if match:
            line = f"{match.group(1)} Untitled section"
        lines.append(line)
    return "\n".join(lines).strip() + "\n"
