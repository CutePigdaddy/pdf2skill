"""Unit tests for core/tree_merger.py — the pieces with real logic in them.

These mirror the SDK-side cases in packages/sdk/test/treeMerger.test.ts
(title alignment, token estimation, anchor lookup) so the two ports stay
in parity. LLMClient is faked; no network access.
"""
import json

import pytest

from core.tree_merger import TreeMerger, ChunkNode


class FakeLLM:
    def __init__(self, response=""):
        self.response = response
        self.prompts = []

    def chat(self, prompt):
        self.prompts.append(prompt)
        return self.response


@pytest.fixture(autouse=True)
def _stub_llm_client(monkeypatch):
    """TreeMerger.__init__ builds a real LLMClient, which requires configured
    API keys (resolves providers from settings.yaml routers). CI has no .env,
    and these tests never hit the LLM anyway — patch it module-wide."""
    monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: FakeLLM())


def make_merger(monkeypatch, response="", **overrides):
    fake = FakeLLM(response)
    monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake)
    merger = TreeMerger()
    for attr, value in overrides.items():
        setattr(merger, attr, value)
    return merger, fake


class TestEstimateTokens:
    def test_pure_ascii_uses_quarter(self):
        merger = TreeMerger()
        # 40 latin chars -> ceil(40/4) = 10 tokens
        assert merger.estimate_tokens("abcd" * 10) == 10

    def test_pure_cjk_uses_one_token_per_char(self):
        merger = TreeMerger()
        assert merger.estimate_tokens("中" * 100) == 100

    def test_mixed_text_weights_each_class(self):
        merger = TreeMerger()
        # 50 CJK chars + 40 latin chars -> 50 + ceil(40/4) = 60
        assert merger.estimate_tokens("中" * 50 + "a" * 40) == 60

    def test_kana_and_fullwidth_count_as_cjk(self):
        merger = TreeMerger()
        assert merger.estimate_tokens("あ" * 10 + "Ａ" * 8) == 18

    def test_empty_text(self):
        merger = TreeMerger()
        assert merger.estimate_tokens("") == 0


class TestFindAnchorPosition:
    def setup_method(self):
        import inspect

        self.merger = TreeMerger()

    def test_exact_match(self):
        assert self.merger.find_anchor_position("aaaaTARGETbbbb", "TARGET") == 4

    def test_empty_anchor_returns_minus_one(self):
        assert self.merger.find_anchor_position("content", "   ") == -1

    def test_fuzzy_match_within_threshold(self):
        # threshold for an 11-char anchor = max(2, 11 // 5) = 2
        assert (
            self.merger.find_anchor_position("The beginning here", "The beginning herx") == 0
        )

    def test_fuzzy_beyond_threshold_returns_minus_one(self):
        assert (
            self.merger.find_anchor_position("The beginning here", "xxxxxxxxxg herx") == -1
        )


class TestPeelingCache:
    """Per-subtree cache: successful peels survive a restart; failed peels do not."""

    def _anchored_response(self):
        return json.dumps({
            "anchors": [
                {"anchor": "SECB_ANCHOR", "description": "", "suggested_title": "Title B"},
                {"anchor": "SECC_ANCHOR", "description": "", "suggested_title": "Title C"},
            ],
            "is_atomic": False,
        })

    def _content(self):
        return "a" * 1500 + "SECB_ANCHOR" + "b" * 1500 + "SECC_ANCHOR" + "c" * 1500

    def test_reuses_cached_subtree_without_llm_and_assigns_fresh_ids(
        self, monkeypatch, tmp_path
    ):
        cache_dir = tmp_path / "peeling_cache"
        fake1 = FakeLLM(self._anchored_response())
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake1)
        merger1 = TreeMerger(cache_dir=cache_dir)
        merger1.chunk_max_tokens = 500

        content = self._content()
        node1 = ChunkNode(id="chunk_0001", title="Big Chapter", parent_path=[], content=content)
        merger1.recursive_peel(node1)
        assert len(node1.children) == 3
        assert merger1.cache_writes > 0

        class NoCallLLM:
            def __init__(self):
                self.prompts = []

            def chat(self, prompt):
                self.prompts.append(prompt)
                raise AssertionError("cache miss")

        fake2 = NoCallLLM()
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake2)
        merger2 = TreeMerger(cache_dir=cache_dir)
        merger2.chunk_max_tokens = 500
        node2 = ChunkNode(id="chunk_0002", title="Big Chapter", parent_path=[], content=content)
        merger2.recursive_peel(node2)

        assert fake2.prompts == []
        assert len(node2.children) == 3
        assert merger2.cache_hits > 0
        ids = [node2.id] + [child.id for child in node2.children]
        assert len(ids) == len(set(ids))
        assert node2.children[1].parent_path == ["Big Chapter"]

    def test_does_not_cache_when_peel_had_errors(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "peeling_cache"
        fake1 = FakeLLM("not json at all {{{")
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake1)
        merger1 = TreeMerger(cache_dir=cache_dir)
        merger1.chunk_max_tokens = 500
        node1 = ChunkNode(
            id="chunk_0001", title="Big", parent_path=[], content="x" * 60000
        )
        merger1.recursive_peel(node1)
        assert merger1.peel_errors == 1
        assert merger1.cache_writes == 0

        fake2 = FakeLLM(json.dumps({"anchors": [], "is_atomic": False}))
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake2)
        merger2 = TreeMerger(cache_dir=cache_dir)
        merger2.chunk_max_tokens = 500
        node2 = ChunkNode(
            id="chunk_0001", title="Big", parent_path=[], content="x" * 60000
        )
        merger2.recursive_peel(node2)
        assert len(fake2.prompts) == 1
        assert merger2.cache_hits == 0

    def test_cache_key_invalidated_by_config_change(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "peeling_cache"
        content = "x" * 60000
        response = json.dumps({"anchors": [], "is_atomic": False})

        fake1 = FakeLLM(response)
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake1)
        merger1 = TreeMerger(cache_dir=cache_dir)
        merger1.chunk_max_tokens = 500
        merger1.chunk_min_threshold = 1
        merger1.recursive_peel(
            ChunkNode(id="chunk_0001", title="Big", parent_path=[], content=content)
        )
        assert merger1.cache_writes == 1

        fake2 = FakeLLM(response)
        monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake2)
        merger2 = TreeMerger(cache_dir=cache_dir)
        merger2.chunk_max_tokens = 500
        merger2.chunk_min_threshold = 1000
        merger2.recursive_peel(
            ChunkNode(id="chunk_0001", title="Big", parent_path=[], content=content)
        )
        assert len(fake2.prompts) == 1
        assert merger2.cache_hits == 0


class TestSuggestedTitleAlignment:
    """Anchor i's title describes the segment STARTING at anchor i — the
    first segment has no LLM title. Guards against the off-by-one where
    every title shifted one segment up."""

    def test_titles_align_to_anchor_segments(self, monkeypatch):
        anchors = json.dumps({
            "anchors": [
                {"anchor": "SECB_ANCHOR", "description": "", "suggested_title": "Title B"},
                {"anchor": "SECC_ANCHOR", "description": "", "suggested_title": "Title C"},
            ],
            "is_atomic": False,
        })
        merger, fake = make_merger(monkeypatch, anchors, chunk_max_tokens=500)

        content = "a" * 1500 + "SECB_ANCHOR" + "b" * 1500 + "SECC_ANCHOR" + "c" * 1500
        node = ChunkNode(id="chunk_0001", title="Big Chapter", parent_path=[], content=content)
        leaves = merger.recursive_peel(node)

        assert node.children is not None
        assert len(node.children) == 3
        # First segment: no LLM title, no headers -> "(Part 1)" fallback
        assert node.children[0].title == "Big Chapter (Part 1)"
        # Anchor titles land on their own segments, not the previous one
        assert node.children[1].title == "Title B"
        assert node.children[2].title == "Title C"
        assert merger.peel_errors == 0

    def test_single_anchor_title_starts_at_anchor(self, monkeypatch):
        anchors = json.dumps({
            "anchors": [
                {"anchor": "SECB_ANCHOR", "description": "", "suggested_title": "Title B"},
            ],
            "is_atomic": False,
        })
        merger, fake = make_merger(monkeypatch, anchors, chunk_max_tokens=500)

        content = "a" * 1500 + "SECB_ANCHOR" + "b" * 2500
        node = ChunkNode(id="chunk_0001", title="Chapter", parent_path=[], content=content)
        merger.recursive_peel(node)

        assert len(node.children) == 2
        assert node.children[0].title == "Chapter (Part 1)"
        assert node.children[1].title == "Title B"

    def test_atomic_decision_sets_flag(self, monkeypatch):
        anchors = json.dumps({"anchors": [], "is_atomic": True})
        merger, fake = make_merger(monkeypatch, anchors, chunk_max_tokens=500)
        node = ChunkNode(id="chunk_0001", title="Exercises", parent_path=[], content="q" * 60000)
        result = merger.recursive_peel(node)
        assert result == [node]
        assert node.is_atomic is True

    def test_unparseable_response_counts_peel_error(self, monkeypatch):
        merger, fake = make_merger(monkeypatch, "This is not JSON {{{", chunk_max_tokens=500)
        node = ChunkNode(id="chunk_0001", title="Big", parent_path=[], content="x" * 60000)
        result = merger.recursive_peel(node)
        assert result == [node]
        assert merger.peel_errors == 1

    def test_network_error_counts_as_peel_error_not_crash(self, monkeypatch):
        """llm_client wraps HTTP/timeout failures as ValueError; RetrySession
        raises NetworkError/RateLimitError — a transient failure must degrade
        to peel_errors instead of crashing Stage 3."""
        from utils.retry_client import NetworkError, RateLimitError

        for exc in (ValueError("boom"), NetworkError("down"), RateLimitError("429")):
            fake = FakeLLM()
            fake.chat = lambda prompt, _e=exc: (_ for _ in ()).throw(_e)
            monkeypatch.setattr("core.tree_merger.LLMClient", lambda stage: fake)
            merger = TreeMerger()
            merger.chunk_max_tokens = 500
            node = ChunkNode(id="chunk_0001", title="Big", parent_path=[], content="x" * 60000)
            result = merger.recursive_peel(node)
            assert result == [node]
            assert merger.peel_errors == 1
