"""Output / skill name rules, shared by the pipeline and the web frontend.

The output name doubles as the output directory name and the SKILL.md
frontmatter ``name``, so it must stay kebab-case per the Claude Agent Skill
spec. Mirrors ``packages/sdk/src/naming.ts`` — keep the two in sync.
"""

import re

SKILL_NAME_MAX_LENGTH = 64

SKILL_NAME_RULES = (
    "仅限小写字母、数字和中划线（-），长度 ≤ 64 字符；"
    "不能以 - 开头或结尾，不能有连续 --（例如 my-book-notes）。"
)


def validate_skill_name(name: str) -> list:
    """Validate a skill/output name against the kebab-case spec.

    Returns a list of violation messages; an empty list means valid.
    """
    if not name:
        return ["名称不能为空"]
    issues = []
    if len(name) > SKILL_NAME_MAX_LENGTH:
        issues.append(f"长度超过 {SKILL_NAME_MAX_LENGTH} 个字符（当前 {len(name)}）")
    if re.search(r"[A-Z]", name):
        issues.append("必须全部使用小写字母")
    if re.search(r"\s", name):
        issues.append("不能包含空格")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        issues.append("只能包含小写字母、数字和中划线（-）")
    if name.startswith("-") or name.endswith("-"):
        issues.append("不能以中划线（-）开头或结尾")
    if "--" in name:
        issues.append("不能包含连续中划线（--）")
    return issues


def slugify_skill_name(text: str) -> str:
    """Best-effort kebab-case conversion of a filename stem.

    Non-ASCII input (e.g. Chinese titles) slugifies to ``""`` — callers
    should fall back to leaving the name empty (auto-generate).
    """
    kebab = re.sub(r"[^a-z0-9]+", "-", text.lower())
    kebab = re.sub(r"-+", "-", kebab).strip("-")
    return kebab[:SKILL_NAME_MAX_LENGTH].rstrip("-")
