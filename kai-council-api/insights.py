import logging
import re
from datetime import datetime
from fastapi import APIRouter, HTTPException
from council_config import COUNCIL_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

INSIGHT_CATEGORIES = {"Insights", "Truths", "Patterns", "Realizations", "Questions"}

INSIGHT_PATTERN = re.compile(
    r"\[INSIGHT:([A-Za-z]+)\](.*?)(?:\[/INSIGHT\]|(?=\[INSIGHT:)|\Z)",
    re.DOTALL,
)


def extract_and_strip_insights(text: str) -> tuple:
    """Extract [INSIGHT:...] tags from text, return (clean_text, insights_list)."""
    insights = []
    for match in INSIGHT_PATTERN.finditer(text):
        category = match.group(1).strip()
        content = match.group(2).strip()
        if category in INSIGHT_CATEGORIES and content:
            insights.append({"category": category, "content": content})

    clean = re.sub(r"\[INSIGHT:[A-Za-z]+\].*?(?:\[/INSIGHT\])", "", text, flags=re.DOTALL)
    clean = re.sub(r"\[INSIGHT:[A-Za-z]+\].*$", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"\[/INSIGHT\]", "", clean)
    clean = clean.strip()

    return clean, insights


def append_insights_to_vault(insights: list) -> int:
    """Append extracted insights to ember/insights.md. Returns count written."""
    if not insights:
        return 0

    insights_file = COUNCIL_PATH / "ember" / "insights.md"
    if not insights_file.exists():
        return 0

    content = insights_file.read_text(encoding="utf-8")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    for item in insights:
        category = item["category"]
        entry = f"- [{date_str}] {item['content']}"

        header = f"## {category}"
        if header in content:
            content = content.replace(
                header,
                f"{header}\n{entry}",
                1,
            )
        else:
            content += f"\n\n{header}\n{entry}\n"

    insights_file.write_text(content, encoding="utf-8")
    return len(insights)


@router.get("/council/insights")
def get_insights():
    insights_file = COUNCIL_PATH / "ember" / "insights.md"
    if not insights_file.exists():
        raise HTTPException(status_code=404, detail="Insights file not found")
    return {"content": insights_file.read_text(encoding="utf-8")}
