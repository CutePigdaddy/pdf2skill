import json
from pathlib import Path
from core.tree_merger import ChunkNode
from core.skill_engine import SkillEngine
from utils.checkpoint import CheckpointManager
from utils.logger import logger

def manual_generate_skill(checkpoint_path: str, output_dir: str):
    """
    手动从断点文件恢复树结构并生成 SKILL.md
    """
    checkpoint_file = Path(checkpoint_path)
    if not checkpoint_file.exists():
        print(f"错误: 找不到断点文件 {checkpoint_path}")
        return

    # 1. 加载断点
    with open(checkpoint_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    tree_data = state.get("data", {}).get("tree_merging", {}).get("master_root")
    if not tree_data:
        print("错误: 断点文件中没有 tree_merging 或 master_root 数据。")
        print("请确保 Stage 3 至少运行成功过一次。")
        return

    # 2. 恢复树结构
    print("正在恢复树结构...")
    master_root = ChunkNode.from_dict(tree_data)
    
    # 3. 初始化 SkillEngine 并生成
    print("正在生成 SKILL.md...")
    skill_out_dir = Path(output_dir) / "generated_skills"
    engine = SkillEngine(skill_out_dir)
    
    # 获取文件名 (假设是 TC)
    pdf_stem = state.get("data", {}).get("pdf_conversion", {}).get("md_file", "document").replace(".md", "")
    
    engine.generate(master_root, pdf_stem)
    print(f"成功! Skill 文件已生成至: {skill_out_dir}")

if __name__ == "__main__":
    # 你可以在这里修改路径
    CHECKPOINT = "test_outputs/.checkpoint.json"
    OUTPUT = "test_outputs"
    
    manual_generate_skill(CHECKPOINT, OUTPUT)
