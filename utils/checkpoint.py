import json
from pathlib import Path
from utils.logger import logger

class CheckpointManager:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.checkpoint_file = self.output_dir / ".checkpoint.json"
        self.state = self._load()

    def _load(self):
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}")
        return {"completed_stages": [], "data": {}}

    def save(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
            
    def mark_stage_completed(self, stage_name: str, stage_data: dict = None):
        if stage_name not in self.state["completed_stages"]:
            self.state["completed_stages"].append(stage_name)
        if stage_data:
            self.state["data"][stage_name] = stage_data
        self.save()
        logger.info(f"Checkpoint saved: Stage '{stage_name}' completed.")

    def is_stage_completed(self, stage_name: str) -> bool:
        return stage_name in self.state.get("completed_stages", [])

    def get_stage_data(self, stage_name: str):
        return self.state.get("data", {}).get(stage_name)
