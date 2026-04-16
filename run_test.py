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
        print(f"✓ MINERU_API_KEY is detected from environment/.env.")

    # Select LLM Provider for Chunking
    print("\n[2] LLM Provider Configuration")
    print("You can select different providers for each stage (Chunking, Peeling, Skill Engine).")
    
    def select_provider(stage_name, default_val="1"):
        choice = prompt_user(f"Select API provider for {stage_name} (1: VectorEngine, 2: SiliconFlow, 3: Google)", default_val)
        if choice == "1": return "vectorengine"
        elif choice == "2": return "siliconflow"
        elif choice == "3": return "google"
        return "vectorengine"

    chunk_provider = select_provider("CHUNKING", "1")
    peel_provider = select_provider("PEELING", "1")
    skill_provider = select_provider("SKILL ENGINE", "1")

    os.environ["CHUNKING_PROVIDER"] = chunk_provider
    os.environ["PEELING_PROVIDER"] = peel_provider
    os.environ["SKILL_ENGINE_PROVIDER"] = skill_provider
    
    print("\n[3] Model Override (Optional)")
    print("Default models are set in config/settings.yaml. Press Enter to use defaults, or input model names to override.")
    
    from config.config import config
    
    # helper to get model with specific provider
    def get_model_config(provider, stage):
        return config.get(f"llm.providers.{provider}.{stage}_model")

    chunk_model = prompt_user(f"Enter CHUNKING model ({chunk_provider})", get_model_config(chunk_provider, "chunking"))
    peel_model = prompt_user(f"Enter PEELING model ({peel_provider})", get_model_config(peel_provider, "peeling"))
    skill_model = prompt_user(f"Enter SKILL ENGINE model ({skill_provider})", get_model_config(skill_provider, "skill_engine"))
    
    # Update environment for the current run
    os.environ["CHUNKING_MODEL"] = chunk_model
    os.environ["PEELING_MODEL"] = peel_model
    os.environ["SKILL_ENGINE_MODEL"] = skill_model
        
    print(f"\n[4] API Key Verification")
    # Prompt for the chosen providers' API keys
    needed_keys = set([chunk_provider, peel_provider, skill_provider])
    for p in needed_keys:
        key_env = f"{p.upper()}_API_KEY"
        if not os.getenv(key_env):
            api_key = prompt_user(f"Enter your {key_env}")
            os.environ[key_env] = api_key
        else:
            print(f"✓ KEY for {p} is already set.")

    # Request Interval Configuration
    print("\n[5] Request Interval Configuration")
    default_interval = str(config.get("llm.request_interval", "1.0"))
    request_interval = prompt_user("Enter API request interval (seconds)", default_interval)
    os.environ["REQUEST_INTERVAL"] = request_interval

    print("\n[6] File Configuration")
    input_mode = prompt_user("Select input mode (1: PDF, 2: Existing Markdown)", "1")
    
    if input_mode == "1":
        default_test_pdf = "../test_data/financial_statement_analysis_test2.pdf" if Path("../test_data/financial_statement_analysis_test2.pdf").exists() else ""
        pdf_path_input = prompt_user("Enter the path to the PDF file", default_test_pdf)
        pdf_path = Path(pdf_path_input)
        if not pdf_path.exists():
            print(f"[ERROR] The file {pdf_path.absolute()} does not exist. Exiting.")
            sys.exit(1)
        input_file = pdf_path
        mode = "pdf"
    else:
        md_path_input = prompt_user("Enter the path to the existing Markdown file")
        md_path = Path(md_path_input)
        if not md_path.exists():
            print(f"[ERROR] The file {md_path.absolute()} does not exist. Exiting.")
            sys.exit(1)
        input_file = md_path
        mode = "markdown"

    output_dir = prompt_user("Enter the output directory", "test_outputs")

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
        
        # Import config and main AFTER environment variables are set
        from main import run_pipeline
        from utils.checkpoint import CheckpointManager
        
        print("\n🚀 Starting Pipeline...\n")
        
        # If markdown mode, we bypass stage 1 by pre-marking checkpoint
        if mode == "markdown":
            out_path = Path(out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            checkpoint = CheckpointManager(out_path)
            # This will make the pipeline think stage 1 is already finished using this MD
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
