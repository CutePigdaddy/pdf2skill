import os
from pathlib import Path
from colorama import Fore, Style, init

init(autoreset=True)

QUIT_SIGNAL = "q"


class _QuitRequested(Exception):
    """Raised when user types q during onboarding."""


class OnboardingWizard:
    """First-run onboarding wizard for pdf2skill."""

    def __init__(self, project_root: Path = None):
        self.project_root = project_root or Path.cwd()
        self.env_path = self.project_root / ".env"

    # -- Public API --------------------------------------------------------

    def needs_onboarding(self) -> bool:
        """Return True if essential config is missing."""
        if not self.env_path.exists():
            return True
        loaded = self._load_env_values()
        return not self._has_essentials(loaded)

    def run(self) -> bool:
        """Execute the full wizard. Returns True if config was saved."""
        self._welcome()
        env = self._load_env_values()

        try:
            env = self._step_mineru(env)
            env = self._step_provider(env)
            env = self._step_api_keys(env)
            env = self._step_models(env)
        except _QuitRequested:
            print("\n{}Onboarding cancelled. No changes saved.{}".format(Fore.YELLOW, Style.RESET_ALL))
            return False

        if self._confirm_and_save(env):
            print("\n{}Configuration saved! Re-run setup anytime with: python main.py --setup{}".format(Fore.GREEN, Style.RESET_ALL))
            return True
        print("\n{}Onboarding cancelled. No changes saved.{}".format(Fore.YELLOW, Style.RESET_ALL))
        return False

    # -- Steps -------------------------------------------------------------

    def _welcome(self):
        print()
        print(Fore.CYAN + "=" * 60)
        print("  Welcome to pdf2skill!".center(60))
        print(Fore.CYAN + "=" * 60)
        print()
        print("  {}pdf2skill converts PDF textbooks and documents into".format(Fore.WHITE))
        print("  {}structured knowledge indexes and reusable skill files.".format(Fore.WHITE))
        print()
        print("  {}This wizard will help you configure the essential settings.".format(Fore.YELLOW))
        print("  {}Press Enter to accept defaults, or type {}q{} to quit at any step.".format(Fore.YELLOW, Fore.WHITE, Fore.YELLOW))
        print()

    def _step_mineru(self, env: dict) -> dict:
        """Step 1: MinerU mode and credentials."""
        print(Fore.CYAN + "-" * 60)
        print("  {}[1/4] MinerU Configuration".format(Fore.WHITE))
        print(Fore.CYAN + "-" * 60)
        print()
        print("  MinerU converts PDF to Markdown. Choose a mode:")
        print("    1: remote  - Use the MinerU cloud API (requires API key)")
        print("    2: local   - Use a local MinerU Gradio server")
        print()

        current_mode = env.get("MINERU_API_MODE", "local")
        mode = self._prompt_choice(
            "Select MinerU mode", ["remote", "local"],
            default=current_mode,
        )
        env["MINERU_API_MODE"] = mode

        if mode == "remote":
            key = self._prompt(
                "Enter your MINERU_API_KEY",
                default=env.get("MINERU_API_KEY", ""),
                secret=True,
            )
            env["MINERU_API_KEY"] = key
        else:
            base_url = self._prompt(
                "MinerU local server URL",
                default=env.get("MINERU_LOCAL_BASE_URL", "http://localhost:7860"),
            )
            env["MINERU_LOCAL_BASE_URL"] = base_url
            env.setdefault("MINERU_API_KEY", "")

        print()
        return env

    def _step_provider(self, env: dict) -> dict:
        """Step 2: LLM Provider selection."""
        print(Fore.CYAN + "-" * 60)
        print("  {}[2/4] LLM Provider Selection".format(Fore.WHITE))
        print(Fore.CYAN + "-" * 60)
        print()

        from config.config import Config
        Config._instance = None
        cfg = Config()
        providers = list(cfg.get("llm.providers", {}).keys())

        if not providers:
            print("  {}No providers configured in settings.yaml!{}".format(Fore.RED, Style.RESET_ALL))
            print("  Please add at least one provider and re-run setup.")
            raise _QuitRequested()

        print("  Available providers:")
        for i, p in enumerate(providers, 1):
            marker = " (current)" if p == env.get("CHUNKING_PROVIDER") else ""
            print("    {}: {}{}".format(i, p, marker))
        print()

        default_provider = env.get("CHUNKING_PROVIDER", providers[0])
        provider = self._prompt_choice(
            "Select default LLM provider for all stages",
            providers,
            default=default_provider,
        )
        env["CHUNKING_PROVIDER"] = provider
        env["PEELING_PROVIDER"] = provider
        env["SKILL_ENGINE_PROVIDER"] = provider
        print()
        return env

    def _step_api_keys(self, env: dict) -> dict:
        """Step 3: API Key for selected provider."""
        print(Fore.CYAN + "-" * 60)
        print("  {}[3/4] API Key Configuration".format(Fore.WHITE))
        print(Fore.CYAN + "-" * 60)
        print()

        from config.config import Config
        Config._instance = None
        cfg = Config()
        provider = env.get("CHUNKING_PROVIDER", "")
        api_key_env = cfg.get(
            "llm.providers.{}.api_key_env".format(provider),
            "{}_API_KEY".format(provider.upper()),
        )

        existing_key = env.get(api_key_env, os.getenv(api_key_env, ""))
        if existing_key:
            print("  {}API key for {} is already set.{}".format(Fore.GREEN, provider, Style.RESET_ALL))
            overwrite = self._prompt("Overwrite? (y/n)", default="n").lower()
            if overwrite == "y":
                key = self._prompt("Enter {}".format(api_key_env), default="", secret=True)
                env[api_key_env] = key
        else:
            key = self._prompt("Enter {}".format(api_key_env), default="", secret=True)
            env[api_key_env] = key

        print()
        return env

    def _step_models(self, env: dict) -> dict:
        """Step 4: Model confirmation."""
        print(Fore.CYAN + "-" * 60)
        print("  {}[4/4] Model Configuration".format(Fore.WHITE))
        print(Fore.CYAN + "-" * 60)
        print()

        from config.config import Config
        Config._instance = None
        cfg = Config()
        provider = env.get("CHUNKING_PROVIDER", "")

        stages = [
            ("chunking", "CHUNKING_MODEL", "chunking_model"),
            ("peeling", "PEELING_MODEL", "peeling_model"),
            ("skill_engine", "SKILL_ENGINE_MODEL", "skill_engine_model"),
        ]

        for stage, env_key, config_key in stages:
            pkey = "llm.providers.{}.{}".format(provider, config_key)
            default_model = cfg.get(pkey, "")
            current = env.get(env_key, default_model)
            label = "{} model ({})".format(stage.capitalize(), provider)
            model = self._prompt(label, default=current)
            env[env_key] = model

        print()
        return env

    # -- Helpers -----------------------------------------------------------

    def _prompt(self, message: str, default: str = "", secret: bool = False) -> str:
        """Prompt user for input with a default value."""
        if default:
            display = "***" if secret else default
            text = "  {} [{}]: ".format(message, display)
        else:
            text = "  {}: ".format(message)

        try:
            value = input(text).strip()
        except (EOFError, KeyboardInterrupt):
            raise _QuitRequested()

        if value.lower() == QUIT_SIGNAL:
            raise _QuitRequested()

        return value if value else default

    def _prompt_choice(self, message: str, choices: list, default: str = "") -> str:
        """Prompt user to choose from a numbered list."""
        default_idx = choices.index(default) + 1 if default in choices else 1
        choices_str = self._format_choices(choices)
        text = "  {} ({}, default {}): ".format(message, choices_str, default_idx)
        try:
            value = input(text).strip()
        except (EOFError, KeyboardInterrupt):
            raise _QuitRequested()

        if value.lower() == QUIT_SIGNAL:
            raise _QuitRequested()

        if not value:
            return default or choices[0]

        try:
            idx = int(value) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass

        if value in choices:
            return value

        print("  {}Invalid choice, using default: {}{}".format(
            Fore.RED, default or choices[0], Style.RESET_ALL))
        return default or choices[0]

    def _format_choices(self, choices: list) -> str:
        parts = []
        for i, c in enumerate(choices):
            parts.append("{}:{}".format(i + 1, c))
        return ", ".join(parts)

    def _confirm_and_save(self, env: dict) -> bool:
        """Display summary and persist to .env if confirmed."""
        print(Fore.CYAN + "=" * 60)
        print("  Configuration Summary".center(60))
        print(Fore.CYAN + "=" * 60)
        print()

        for line in self._build_summary(env):
            print("  {}".format(line))
        print()

        confirm = self._prompt("Save this configuration? (y/n)", default="y").lower()
        if confirm != "y":
            return False

        self._write_env(env)
        return True

    def _build_summary(self, env: dict) -> list:
        lines = []
        mode = env.get("MINERU_API_MODE", "local")
        lines.append("{}MinerU Mode    : {}".format(Fore.WHITE, mode))
        if mode == "remote":
            tail = env.get("MINERU_API_KEY", "")[-4:]
            lines.append("{}MinerU API Key : ***{}".format(Fore.WHITE, tail))
        else:
            url = env.get("MINERU_LOCAL_BASE_URL", "http://localhost:7860")
            lines.append("{}MinerU URL     : {}".format(Fore.WHITE, url))

        provider = env.get("CHUNKING_PROVIDER", "")
        lines.append("{}LLM Provider   : {}".format(Fore.WHITE, provider))

        from config.config import Config
        Config._instance = None
        cfg = Config()
        ak_key = "llm.providers.{}.api_key_env".format(provider)
        api_key_env = cfg.get(ak_key, "{}_API_KEY".format(provider.upper()))
        key_val = env.get(api_key_env, os.getenv(api_key_env, ""))
        if key_val:
            lines.append("{}API Key        : ***{}".format(Fore.WHITE, key_val[-4:]))
        else:
            lines.append("{}API Key        : (not set)".format(Fore.YELLOW))

        model_labels = [
            ("CHUNKING_MODEL", "Chunking"),
            ("PEELING_MODEL", "Peeling"),
            ("SKILL_ENGINE_MODEL", "Skill Engine"),
        ]
        for env_key, label in model_labels:
            model = env.get(env_key, "")
            padded = "{} Model".format(label)
            lines.append("{}{:<16}: {}".format(Fore.WHITE, padded, model))

        return lines

    def _write_env(self, env: dict):
        """Write env dict to .env file, preserving existing keys not in dict."""
        existing = {}
        if self.env_path.exists():
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if "=" in stripped and not stripped.startswith("#"):
                        key, _, val = stripped.partition("=")
                        existing[key.strip()] = val.strip()

        existing.update(env)

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# pdf2skill configuration (generated by onboarding wizard)\n")
            for key, val in sorted(existing.items()):
                if not key.startswith("#"):
                    if val:
                        f.write("{}={}\n".format(key, val))
                    else:
                        f.write("# {}=\n".format(key))

        from dotenv import load_dotenv
        load_dotenv(str(self.env_path), override=True)

    # -- Env reading -------------------------------------------------------

    def _load_env_values(self) -> dict:
        """Read key=value pairs from .env file if it exists."""
        env = {}
        if not self.env_path.exists():
            return env
        with open(self.env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    key, _, val = stripped.partition("=")
                    env[key.strip()] = val.strip()
        return env

    def _has_essentials(self, env: dict) -> bool:
        """Check whether the env dict has enough to run the pipeline."""
        has_mode = "MINERU_API_MODE" in env
        mode = env.get("MINERU_API_MODE", "local")
        has_mkey = True
        if mode == "remote":
            has_mkey = bool(env.get("MINERU_API_KEY"))
        has_provider = bool(env.get("CHUNKING_PROVIDER"))
        has_llm_key = any(
            v for k, v in env.items()
            if k.endswith("_API_KEY") and k != "MINERU_API_KEY"
        )
        return has_mode and has_mkey and has_provider and has_llm_key
