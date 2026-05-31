import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.onboarding import OnboardingWizard, _QuitRequested


class TestNeedsOnboarding:
    """Test needs_onboarding detection logic."""

    def test_no_env_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MINERU_API_MODE", raising=False)
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.delenv("CHUNKING_PROVIDER", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is True

    def test_empty_env_file(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("# empty\n", encoding="utf-8")
        monkeypatch.delenv("MINERU_API_MODE", raising=False)
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.delenv("CHUNKING_PROVIDER", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is True

    def test_complete_local_env(self, tmp_path, monkeypatch):
        env_content = (
            "MINERU_API_MODE=local\n"
            "CHUNKING_PROVIDER=siliconflow\n"
            "SILICONFLOW_API_KEY=sk-test1234\n"
        )
        (tmp_path / ".env").write_text(env_content, encoding="utf-8")
        monkeypatch.delenv("MINERU_API_MODE", raising=False)
        monkeypatch.delenv("CHUNKING_PROVIDER", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is False

    def test_remote_env_missing_key(self, tmp_path, monkeypatch):
        env_content = "MINERU_API_MODE=remote\nCHUNKING_PROVIDER=siliconflow\n"
        (tmp_path / ".env").write_text(env_content, encoding="utf-8")
        monkeypatch.delenv("MINERU_API_MODE", raising=False)
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is True

    def test_missing_provider(self, tmp_path, monkeypatch):
        env_content = "MINERU_API_MODE=local\nSILICONFLOW_API_KEY=sk-test\n"
        (tmp_path / ".env").write_text(env_content, encoding="utf-8")
        monkeypatch.delenv("CHUNKING_PROVIDER", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is True

    def test_missing_llm_key(self, tmp_path, monkeypatch):
        env_content = "MINERU_API_MODE=local\nCHUNKING_PROVIDER=siliconflow\n"
        (tmp_path / ".env").write_text(env_content, encoding="utf-8")
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        wizard = OnboardingWizard(tmp_path)
        assert wizard.needs_onboarding() is True


class TestLoadEnvValues:
    """Test .env file reading."""

    def test_reads_key_value_pairs(self, tmp_path):
        (tmp_path / ".env").write_text("KEY1=val1\nKEY2=val2\n", encoding="utf-8")
        wizard = OnboardingWizard(tmp_path)
        result = wizard._load_env_values()
        assert result == {"KEY1": "val1", "KEY2": "val2"}

    def test_skips_comments(self, tmp_path):
        (tmp_path / ".env").write_text("# comment\nKEY1=val1\n", encoding="utf-8")
        wizard = OnboardingWizard(tmp_path)
        result = wizard._load_env_values()
        assert result == {"KEY1": "val1"}

    def test_empty_file(self, tmp_path):
        (tmp_path / ".env").write_text("", encoding="utf-8")
        wizard = OnboardingWizard(tmp_path)
        result = wizard._load_env_values()
        assert result == {}


class TestWriteEnv:
    """Test .env file writing."""

    def test_writes_new_file(self, tmp_path):
        wizard = OnboardingWizard(tmp_path)
        wizard._write_env({"KEY1": "val1", "KEY2": "val2"})
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "KEY1=val1" in content
        assert "KEY2=val2" in content

    def test_preserves_existing_keys(self, tmp_path):
        (tmp_path / ".env").write_text("EXISTING=kept\n", encoding="utf-8")
        wizard = OnboardingWizard(tmp_path)
        wizard._write_env({"NEW_KEY": "new_val"})
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "EXISTING=kept" in content
        assert "NEW_KEY=new_val" in content

    def test_overwrites_existing_key(self, tmp_path):
        (tmp_path / ".env").write_text("KEY=old\n", encoding="utf-8")
        wizard = OnboardingWizard(tmp_path)
        wizard._write_env({"KEY": "new"})
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "KEY=new" in content
        assert "KEY=old" not in content

    def test_empty_value_commented(self, tmp_path):
        wizard = OnboardingWizard(tmp_path)
        wizard._write_env({"EMPTY_KEY": ""})
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "# EMPTY_KEY=" in content


class TestHasEssentials:
    """Test essential config detection."""

    def test_local_mode_complete(self):
        wizard = OnboardingWizard()
        env = {
            "MINERU_API_MODE": "local",
            "CHUNKING_PROVIDER": "test",
            "TEST_API_KEY": "sk-test",
        }
        assert wizard._has_essentials(env) is True

    def test_remote_mode_needs_key(self):
        wizard = OnboardingWizard()
        env = {
            "MINERU_API_MODE": "remote",
            "CHUNKING_PROVIDER": "test",
            "TEST_API_KEY": "sk-test",
        }
        assert wizard._has_essentials(env) is False

    def test_no_provider(self):
        wizard = OnboardingWizard()
        env = {"MINERU_API_MODE": "local", "TEST_API_KEY": "sk-test"}
        assert wizard._has_essentials(env) is False

    def test_no_llm_key(self):
        wizard = OnboardingWizard()
        env = {"MINERU_API_MODE": "local", "CHUNKING_PROVIDER": "test"}
        assert wizard._has_essentials(env) is False


class TestBuildSummary:
    """Test summary formatting."""

    def test_local_mode_summary(self):
        wizard = OnboardingWizard()
        env = {
            "MINERU_API_MODE": "local",
            "MINERU_LOCAL_BASE_URL": "http://localhost:7860",
            "CHUNKING_PROVIDER": "siliconflow",
            "SILICONFLOW_API_KEY": "sk-abcdef1234",
            "CHUNKING_MODEL": "deepseek-r1",
            "PEELING_MODEL": "deepseek-v3",
            "SKILL_ENGINE_MODEL": "glm-4",
        }
        lines = wizard._build_summary(env)
        text = " ".join(lines)
        assert "local" in text
        assert "siliconflow" in text
        assert "deepseek-r1" in text

    def test_remote_mode_summary(self):
        wizard = OnboardingWizard()
        env = {
            "MINERU_API_MODE": "remote",
            "MINERU_API_KEY": "sk-mineru-key",
            "CHUNKING_PROVIDER": "google",
            "GOOGLE_API_KEY": "ai-test-key-5678",
        }
        lines = wizard._build_summary(env)
        text = " ".join(lines)
        assert "remote" in text
        assert "5678" in text


class TestQuitSignal:
    """Test quit signal handling."""

    def test_quit_exception_is_raised(self):
        exc = _QuitRequested("test")
        assert isinstance(exc, Exception)