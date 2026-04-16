import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
sys.path.append(str(Path(__file__).parent))

load_dotenv()

from core.llm_chunker import LLMChunker
from config.config import config

def debug_chunking():
    md_path = Path("CS/full.md")
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return
        
    print(f"--- Debugging Chunking for {md_path} ---")
    chunker = LLMChunker()
    
    # Manually trigger the LLM call to see the raw output
    content = md_path.read_text(encoding="utf-8")
    lines = content.split('\n')
    
    headers = []
    import re
    from dataclasses import dataclass
    @dataclass
    class Header:
        level: int
        text: str
        line_number: int

    for i, line in enumerate(lines, 1):
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            headers.append(Header(len(match.group(1)), match.group(2).strip(), i))
    
    print(f"Total headers found: {len(headers)}")
    
    # Inspect the first few headers
    for h in headers[:10]:
        print(f"  [L{h.line_number}] {'#'*h.level} {h.text}")
        
    split_data = chunker.split(md_path)
    print("\n--- LLM Result ---")
    print(f"Splits: {split_data.get('splits')}")
    print(f"TOC Range: {split_data.get('toc_range')}")
    print(f"Preface Range: {split_data.get('preface_range')}")
    print(f"Atomic Ranges: {split_data.get('atomic_ranges')}")
    
    if not split_data.get('splits'):
        print("\n[WARNING] No splits returned! This explains why the whole file is one chunk.")

if __name__ == "__main__":
    debug_chunking()
