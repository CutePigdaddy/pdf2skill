"""
Unit tests for the structured 4-stage pipeline progress
(``server._parse_stage_status`` and ``/api/status``'s ``stages`` field).

Calls the endpoint functions directly — no HTTP server required.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))

import server as fe_server  # noqa: E402


def statuses(stages):
    return [s["status"] for s in stages]


def detail_of(stages, key):
    for s in stages:
        if s["key"] == key:
            return s["detail"]
    return None


FULL_LOG = """--- Stage 1: PDF to Markdown ---
__MINERU_PROGRESS__{"phase":"解析中","pct":50,"extracted":5,"total":10}
--- Stage 2: LLM Chunking Strategy ---
Original chunks saved to: outputs/x/full_chunks_original
--- Stage 3: TOC Drilling & Merging ---
Peeling:  50%|██████      | 13/26
Peeled chunks saved to: outputs/x/full_chunks
--- Stage 4: Skill Generation ---
Pipeline Execution Finished Successfully!
"""


def test_full_pipeline_success():
    stages = fe_server._parse_stage_status(FULL_LOG, "done", {"phase": "done", "pct": 100, "extracted": 1, "total": 1}, None)
    assert statuses(stages) == ["done", "done", "done", "done"]


def test_all_checkpoint_resume():
    log = "Loaded existing markdown: x.md\nLoaded 15 base chunks from checkpoint\nLoading merged tree from checkpoint..."
    stages = fe_server._parse_stage_status(log, "done", None, None)
    assert statuses(stages) == ["done", "done", "done", "done"]
    assert detail_of(stages, "mineru") == "已从 checkpoint 恢复"
    assert detail_of(stages, "chunking") == "已从 checkpoint 恢复"
    assert detail_of(stages, "peeling") == "已从 checkpoint 恢复"


def test_markdown_mode_stage1_done():
    log = "Input: x.md\nLoaded existing markdown: x.md\n--- Stage 2: LLM Chunking Strategy ---"
    stages = fe_server._parse_stage_status(log, "running", None, None)
    assert stages[0]["status"] == "done"
    assert stages[1]["status"] == "active"


def test_mineru_failure_maps_mineru():
    log = "--- Stage 1: PDF to Markdown ---\nMinerUAuthError: Key 无效 — 请检查\n"
    stages = fe_server._parse_stage_status(log, "failed", None, {"err_code": "", "message": "Key 无效", "advice": ""})
    assert statuses(stages) == ["failed", "pending", "pending", "pending"]


def test_chunking_failure():
    log = "--- Stage 2: LLM Chunking Strategy ---\nLLMParsingError: 响应无法解析\n"
    stages = fe_server._parse_stage_status(log, "failed", None, {"err_code": "", "message": "响应无法解析", "advice": ""})
    assert statuses(stages) == ["done", "failed", "pending", "pending"]


def test_peeling_failure():
    log = "--- Stage 3: TOC Drilling & Merging ---\nMergeError: 合并失败\n"
    stages = fe_server._parse_stage_status(log, "failed", None, {"err_code": "", "message": "合并失败", "advice": ""})
    assert statuses(stages) == ["done", "done", "failed", "pending"]


def test_skill_failure():
    log = "--- Stage 4: Skill Generation ---\nSkillGenerationError: 生成失败\n"
    stages = fe_server._parse_stage_status(log, "failed", None, {"err_code": "", "message": "生成失败", "advice": ""})
    assert statuses(stages) == ["done", "done", "done", "failed"]


def test_llm_parsing_error_attrs_to_active_stage():
    # LLMParsingError is stage-agnostic -> attributed to the active (chunking) stage
    log = "--- Stage 2: LLM Chunking Strategy ---\nLLMParsingError: 响应无法解析\n"
    stages = fe_server._parse_stage_status(log, "failed", None, {"err_code": "", "message": "响应无法解析", "advice": ""})
    assert statuses(stages) == ["done", "failed", "pending", "pending"]


def test_stopped_mid_mineru_all_pending():
    log = "--- Stage 1: PDF to Markdown ---\n__MINERU_PROGRESS__{\"phase\":\"排队中\",\"pct\":0,\"extracted\":0,\"total\":10}"
    stages = fe_server._parse_stage_status(log, "stopped", {"phase": "排队中", "pct": 0, "extracted": 0, "total": 10}, None)
    assert statuses(stages) == ["pending", "pending", "pending", "pending"]


def test_stopped_mid_chunking_stage1_done():
    log = "--- Stage 1: PDF to Markdown ---\n--- Stage 2: LLM Chunking Strategy ---\n"
    stages = fe_server._parse_stage_status(log, "stopped", None, None)
    assert statuses(stages) == ["done", "pending", "pending", "pending"]


def test_local_mode_no_pages_detail():
    log = "--- Stage 1: PDF to Markdown ---\nMarkdown already generated. Skipping local conversion."
    stages = fe_server._parse_stage_status(log, "running", None, None)
    assert stages[0]["status"] == "active"
    assert detail_of(stages, "mineru") is None  # no marker -> no page detail


def test_no_log_immediate_failure_all_pending():
    stages = fe_server._parse_stage_status("", "failed", None, {"err_code": "CONN", "message": "连接失败", "advice": ""})
    assert statuses(stages) == ["pending", "pending", "pending", "pending"]


def test_done_status_all_done():
    stages = fe_server._parse_stage_status("--- Stage 1: PDF to Markdown ---", "done", None, None)
    assert statuses(stages) == ["done", "done", "done", "done"]


def test_mineru_active_detail_with_pages():
    log = "--- Stage 1: PDF to Markdown ---\n__MINERU_PROGRESS__{\"phase\":\"解析中\",\"pct\":30,\"extracted\":3,\"total\":10}"
    stages = fe_server._parse_stage_status(log, "running", {"phase": "解析中", "pct": 30, "extracted": 3, "total": 10}, None)
    assert stages[0]["status"] == "active"
    assert "3/10" in detail_of(stages, "mineru")


def test_tqdm_detail_for_peeling():
    log = "--- Stage 3: TOC Drilling & Merging ---\nPeeling:  50%|██████      | 13/26"
    stages = fe_server._parse_stage_status(log, "running", None, None)
    assert stages[2]["status"] == "active"
    assert detail_of(stages, "peeling") == "Peeling 50%"
