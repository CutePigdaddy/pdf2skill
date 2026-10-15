"""Generate the deterministic full.md fixture for M4 alignment verification."""

from pathlib import Path

BASE = Path(__file__).parent


def subsection(chapter: int, idx: int) -> str:
    filler = f"Sub {chapter}.{idx} content: the quick brown fox jumps over the lazy dog. " * 62
    return f"## Sub {chapter}.{idx}: Topic {idx}\n{filler}\n\n"


def chapter(num: int, title: str, subs: int, intro_len: int = 40) -> str:
    intro = f"Chapter {num} introduction: " + "lorem ipsum dolor sit amet. " * intro_len
    parts = [f"# Chapter {num}: {title}\n{intro}\n\n"]
    for i in range(1, subs + 1):
        parts.append(subsection(num, i))
    return "".join(parts)


def main() -> None:
    toc = "# 目录\nChapter 1: Fundamentals ....... 1\nChapter 2: Applications ....... 2\nChapter 3: Summary ....... 3\nReferences ....... 4\n\n"
    doc = (
        "# Test Book: Sample Content\n"
        "This book is a synthetic fixture for alignment verification.\n\n"
        + toc
        + chapter(1, "Fundamentals", 6)
        + chapter(2, "Applications", 6)
        + "# Chapter 3: Summary\n"
        + "A short summary chapter that stays below the token limit.\n\n"
        + "# References\n[1] Fixture reference.\n\n"
    )
    path = BASE / "full.md"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    lines = doc.split("\n")
    for i, line in enumerate(lines, 1):
        if line.startswith("# "):
            print(f"line {i}: {line}")
    print(f"total: {len(doc)} chars, {len(lines)} lines")


if __name__ == "__main__":
    main()
