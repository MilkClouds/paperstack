import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_relative_documentation_links_resolve():
    documents = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    broken = []
    for document in documents:
        for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            path = target.split("#", 1)[0]
            if path and "://" not in path and not (document.parent / path).exists():
                broken.append(f"{document.relative_to(ROOT)}: {target}")
    assert not broken, "broken documentation links:\n" + "\n".join(broken)
