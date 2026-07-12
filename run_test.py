import os
import sys
from pathlib import Path

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))

def prompt_user(message: str, default: str = None) -> str:
    if default:
        user_input = input(f"{message} [{default}]: ").strip()
        return user_input if user_input else default
    else:
        while True:
            user_input = input(f"{message}: ").strip()
            if user_input:
                return user_input
            print("This field cannot be empty.")

def resolve_input_file(name_input: str, inputs_dir: Path) -> Path | None:
    """Search for a file in inputs/ by name, with recursive fallback."""
    # Strip any path components — only the filename is used for searching
    name_input = Path(name_input).name

    candidate = inputs_dir / name_input
    if candidate.exists():
        return candidate
    matches = list(inputs_dir.rglob(name_input))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print("Found multiple matches in inputs/:")
        for i, m in enumerate(matches):
            print(f"  {i+1}: {m.relative_to(inputs_dir)}")
        choice = prompt_user("Select which file to use", "1")
        try:
            return matches[int(choice) - 1]
        except (ValueError, IndexError):
            print(f"Invalid selection, using first match: {matches[0].name}")
            return matches[0]
    return None

def setup_environment():
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 60)
    print(" PDF2Skills v2.0 - Interactive Test Runner ".center(60))
    print("=" * 60)
    print("\n[1] API Configuration")
    
    # Check and prompt for MinerU API Key
    if not os.getenv("MINERU_API_KEY"):
        api_key = prompt_user("Enter your MINERU_API_KEY")
        os.environ["MINERU_API_KEY"] = api_key
    else:
        print(f"✅ MINERU_API_KEY is detected from environment/.env.")

    # Select LLM Provider for each stage
    print("\n[2] LLM Provider Configuration")
    
    from config.config import config
    
    available_providers = list(config.get("llm.providers", {}).keys())
    if not available_providers:
        print("[ERROR] No providers configured in settings.yaml. Please add at least one provider.")
        sys.exit(1)
    
    provider_list_str = ", ".join(f"{i+1}: {p}" for i, p in enumerate(available_providers))
    default_provider = available_providers[0]
    
    print(f"You can select different providers for each stage. Available: {provider_list_str}")
    
    def select_provider(stage_name):
        default_idx = str(available_providers.index(default_provider) + 1)
        choice = prompt_user(
            f"Select API provider for {stage_name} ({provider_list_str})",
            default_idx
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available_providers):
                return available_providers[idx]
        except ValueError:
            pass
        # Allow direct name input
        if choice in available_providers:
            return choice
        print(f"Invalid choice, defaulting to '{default_provider}'")
        return default_provider

    chunk_provider = select_provider("CHUNKING")
    peel_provider = select_provider("PEELING")
    skill_provider = select_provider("SKILL ENGINE")

    os.environ["CHUNKING_PROVIDER"] = chunk_provider
    os.environ["PEELING_PROVIDER"] = peel_provider
    os.environ["SKILL_ENGINE_PROVIDER"] = skill_provider
    
    print("\n[3] Model Override (Optional)")
    print("Default models are set in config/settings.yaml. Press Enter to use defaults, or input model names to override.")
    
    def get_model_config(provider, stage):
        return config.get(f"llm.providers.{provider}.{stage}_model", "")

    chunk_model = prompt_user(f"Enter CHUNKING model ({chunk_provider})", get_model_config(chunk_provider, "chunking"))
    peel_model = prompt_user(f"Enter PEELING model ({peel_provider})", get_model_config(peel_provider, "peeling"))
    skill_model = prompt_user(f"Enter SKILL ENGINE model ({skill_provider})", get_model_config(skill_provider, "skill_engine"))
    
    os.environ["CHUNKING_MODEL"] = chunk_model
    os.environ["PEELING_MODEL"] = peel_model
    os.environ["SKILL_ENGINE_MODEL"] = skill_model
        
    print(f"\n[4] API Key Verification")
    needed_providers = set([chunk_provider, peel_provider, skill_provider])
    for p in needed_providers:
        api_key_env = config.get(f"llm.providers.{p}.api_key_env", f"{p.upper()}_API_KEY")
        if not os.getenv(api_key_env):
            api_key = prompt_user(f"Enter your {api_key_env}")
            os.environ[api_key_env] = api_key
        else:
            print(f"✅ KEY for {p} is already set.")

    # Request Interval Configuration
    print("\n[5] Request Interval Configuration")
    default_interval = str(config.get("llm.request_interval", "1.0"))
    request_interval = prompt_user("Enter API request interval (seconds)", default_interval)
    os.environ["REQUEST_INTERVAL"] = request_interval

    print("\n[6] File Configuration")
    input_mode = prompt_user("Select input mode (1: PDF, 2: Existing Markdown)", "1")

    PROJECT_ROOT = Path(__file__).parent
    INPUTS_DIR = PROJECT_ROOT / "inputs"
    OUTPUTS_DIR = PROJECT_ROOT / "outputs"

    if not INPUTS_DIR.exists():
        print(f"[ERROR] inputs/ directory not found at {INPUTS_DIR}. Please create it and add your files.")
        sys.exit(1)

    if input_mode == "1":
        file_name = prompt_user("Enter the PDF file name (searches inputs/ folder)")
        input_file = resolve_input_file(file_name, INPUTS_DIR)
        if input_file is None:
            print(f"[ERROR] '{file_name}' not found in inputs/ directory. Exiting.")
            sys.exit(1)
        mode = "pdf"
    else:
        file_name = prompt_user("Enter the Markdown file name (searches inputs/ folder)")
        input_file = resolve_input_file(file_name, INPUTS_DIR)
        if input_file is None:
            print(f"[ERROR] '{file_name}' not found in inputs/ directory. Exiting.")
            sys.exit(1)
        mode = "markdown"

    default_output = str(OUTPUTS_DIR / input_file.stem)
    output_dir = prompt_user("Enter the output directory", default_output)

    # Final Confirmation
    print("\n" + "=" * 60)
    print("Ready to start the pipeline with:")
    print(f"  Input File     : {input_file} ({mode.upper()} mode)")
    print(f"  Output Dir     : {output_dir}")
    print(f"  Chunk Config   : {chunk_provider} -> {chunk_model}")
    print(f"  Peel Config    : {peel_provider} -> {peel_model}")
    print(f"  Skill Config   : {skill_provider} -> {skill_model}")
    print(f"  Interval       : {request_interval}s")
    print("=" * 60)
    
    confirm = prompt_user("Start processing? (y/n)", "y").lower()
    if confirm != 'y':
        print("Test cancelled.")
        sys.exit(0)
        
    return str(input_file), output_dir, mode

if __name__ == "__main__":
    try:
        target_file, out_dir, mode = setup_environment()
        
        from main import run_pipeline
        from utils.checkpoint import CheckpointManager
        
        print("\n🚀 Starting Pipeline...\n")
        
        if mode == "markdown":
            out_path = Path(out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            checkpoint = CheckpointManager(out_path)
            checkpoint.mark_stage_completed("pdf_conversion", {"md_file": target_file})
            
        run_pipeline(target_file, out_dir)
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Test failed with an error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)