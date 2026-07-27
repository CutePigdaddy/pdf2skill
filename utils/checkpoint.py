import json
from pathlib import Path
from utils.logger import logger

CHECKPOINT_VERSION = 1

class CheckpointManager:
    """Persistent, versioned stage-completion tracker.

    Saves pipeline progress to ``.checkpoint.json`` inside the output
    directory so that interrupted runs can resume from the last
    completed stage.
    """

    STAGE_ORDER = ["pdf_conversion", "llm_chunking", "tree_merging"]

    def __init__(self, output_dir: Path):
        """Initialise and load an existing checkpoint if present.

        Args:
            output_dir: Directory where the checkpoint file is stored.
        """
        self.output_dir = Path(output_dir)
        self.checkpoint_file = self.output_dir / ".checkpoint.json"
        self.state = self._load()

    def _load(self):
        """Load checkpoint from disk, resetting on version mismatch or corruption."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get("version") != CHECKPOINT_VERSION:
                    logger.warning(
                        f"Checkpoint version mismatch (expected {CHECKPOINT_VERSION}, "
                        f"got {data.get('version')}). Resetting checkpoint."
                    )
                    return {"version": CHECKPOINT_VERSION, "completed_stages": [], "data": {}}
                return data
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load checkpoint: {e}. Resetting.")
        return {"version": CHECKPOINT_VERSION, "completed_stages": [], "data": {}}

    def save(self):
        """Persist current checkpoint state to disk."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state["version"] = CHECKPOINT_VERSION
        with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
            
    def mark_stage_completed(self, stage_name: str, stage_data: dict = None):
        """Record a pipeline stage as completed and persist.

        Args:
            stage_name: Identifier, e.g. ``"pdf_conversion"``.
            stage_data: Optional payload to store alongside the stage.
        """
        if stage_name not in self.state["completed_stages"]:
            self.state["completed_stages"].append(stage_name)
        if stage_data:
            self.state["data"][stage_name] = stage_data
        self.save()
        logger.info(f"Checkpoint saved: Stage '{stage_name}' completed.")

    def is_stage_completed(self, stage_name: str) -> bool:
        """Check whether a stage has been previously completed.

        Args:
            stage_name: Identifier to check.

        Returns:
            ``True`` if the stage is in the completed list.
        """
        return stage_name in self.state.get("completed_stages", [])

    def get_stage_data(self, stage_name: str):
        """Retrieve the data payload stored for a completed stage.

        Args:
            stage_name: Identifier to look up.

        Returns:
            The stored dict, or ``None`` if the stage has no data.
        """
        return self.state.get("data", {}).get(stage_name)

    def reset_from_stage(self, stage_name: str):
        """Remove *stage_name* and all subsequent stages from checkpoint.

        Used to re-run the pipeline from a specific stage while keeping
        earlier stage results intact.

        Args:
            stage_name: Stage to reset from (inclusive).

        Raises:
            ValueError: If *stage_name* is not in ``STAGE_ORDER``.
        """
        if stage_name not in self.STAGE_ORDER:
            raise ValueError(f"Unknown stage: {stage_name}. Valid: {self.STAGE_ORDER}")
        idx = self.STAGE_ORDER.index(stage_name)
        stages_to_remove = self.STAGE_ORDER[idx:]
        for s in stages_to_remove:
            if s in self.state["completed_stages"]:
                self.state["completed_stages"].remove(s)
            self.state["data"].pop(s, None)
        self.save()
        logger.info(f"Checkpoint reset from '{stage_name}': removed {stages_to_remove}")

    def clear_all(self):
        """Remove all stage completion records and data."""
        self.state = {"version": CHECKPOINT_VERSION, "completed_stages": [], "data": {}}
        self.save()
        logger.info("Checkpoint cleared completely")
