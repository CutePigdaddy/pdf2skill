"""Unit tests for utils/naming.py and its parity with core/skill_engine.py.

The rules mirror packages/sdk/src/naming.ts (see test/naming.test.ts).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.naming import (  # noqa: E402
    SKILL_NAME_MAX_LENGTH,
    slugify_skill_name,
    validate_skill_name,
)


class TestValidateSkillName:
    def test_accepts_valid_kebab_names(self):
        for name in ["my-book", "calculus-ii-notes", "a", "abc-123", "skill-1739456789", "a-b-c"]:
            assert validate_skill_name(name) == [], name

    def test_rejects_empty(self):
        assert validate_skill_name("") == ["名称不能为空"]

    def test_rejects_uppercase(self):
        assert any("小写" in i for i in validate_skill_name("My-Book"))

    def test_rejects_whitespace(self):
        assert any("空格" in i for i in validate_skill_name("my book"))

    def test_rejects_non_ascii(self):
        assert any("中划线" in i for i in validate_skill_name("我的书"))

    def test_rejects_underscore(self):
        assert any("中划线" in i for i in validate_skill_name("my_book"))

    def test_rejects_leading_trailing_hyphen(self):
        assert any("开头或结尾" in i for i in validate_skill_name("-book"))
        assert any("开头或结尾" in i for i in validate_skill_name("book-"))

    def test_rejects_double_hyphen(self):
        assert any("连续" in i for i in validate_skill_name("my--book"))

    def test_rejects_over_length(self):
        name = "a" * (SKILL_NAME_MAX_LENGTH + 1)
        assert any("长度超过" in i for i in validate_skill_name(name))

    def test_accepts_exact_max_length(self):
        assert validate_skill_name("a" * SKILL_NAME_MAX_LENGTH) == []


class TestSlugifySkillName:
    def test_converts_to_kebab(self):
        assert slugify_skill_name("My Great Book! (v2)") == "my-great-book-v2"
        assert slugify_skill_name("  --Weird___name--  ") == "weird-name"

    def test_caps_at_max_length(self):
        assert slugify_skill_name("a" * 70 + "-") == "a" * SKILL_NAME_MAX_LENGTH

    def test_non_ascii_slugifies_to_empty(self):
        assert slugify_skill_name("微积分 第二版") == ""


class TestSkillEngineParity:
    def test_validate_kebab_name_matches_naming_module(self):
        from core.skill_engine import SkillEngine

        cases = ["ok-name", "Bad Name", "", "-x", "x-", "a--b", "我的书", "a" * 65, "a" * 64]
        for name in cases:
            assert SkillEngine._validate_kebab_name(name) == (validate_skill_name(name) == []), name
