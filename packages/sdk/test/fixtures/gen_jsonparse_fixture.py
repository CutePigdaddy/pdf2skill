"""Generate jsonparse_fixture.json by running the Python reference parser.

The fixture pins utils/llm_client.py::LLMClient.parse_json_response behavior;
the vitest parity test (test/jsonParserParity.test.ts) replays the same
inputs through the TypeScript port and must get identical results.

Regenerate from the repo root with:
    python packages/sdk/test/fixtures/gen_jsonparse_fixture.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from utils.llm_client import LLMClient  # noqa: E402

# (name, raw LLM response text) — strict parity: Python and TS must agree.
CASES = [
    ("plain_json", '{"a": 1, "b": "two"}'),
    ("markdown_fence", '```json\n{"subsections": [], "suggested_title": "Sub"}\n```'),
    ("surrounding_prose", 'Here is the result: {"x": "y"} hope it helps'),
    ("think_multiline", '<think>\nstep 1\nstep 2\n</think>\n{"answer": 42}'),
    (
        "think_multiple_and_python_literals",
        "<think>a</think>prefix<think>b</think>{'k': 'v', 'n': None, 'flag': True}",
    ),
    ("python_escapes", "{'title': 'It\\'s a \\\"test\\\"', 'n': None}"),
    ("nested", '{"deep": {"nested": {"a": [1, 2, {"b": 3}]}}}'),
    ("whitespace_padded", '  \n\t {"spaced": 1} \n '),
    ("trailing_comma_dict", '{"a": 1, "b": 2,}'),
    ("trailing_comma_nested", "{'a': [1, 2, ], 'b': {'c': 3, }, }"),
    ("escaped_newline_in_string", "{'msg': 'line1\\nline2'}"),
    ("unclosed_think_tag", '<think>unclosed {"a": 1}'),
    ("top_level_array", '[{"a": 1}]'),
    ("two_objects_with_prose_between", 'text {"a": 1} more {"b": 2} tail'),
    ("unclosed_object", '{"a": 1'),
]

# (name, input, ts_expected_value) — the TS port is intentionally MORE
# lenient here: Python's ast.literal_eval rejects JSON keywords
# (true/false/null) that appear in prose-wrapped JSON, while the port
# runs JSON.parse on the extracted braces before the repair pass.
LENIENT_CASES = [
    (
        "json_keywords_in_prose",
        'Here is the result: {"x": {"y": [true, false, null]}} — done.',
        {"x": {"y": [True, False, None]}},
    ),
]


def main() -> None:
    client = LLMClient.__new__(LLMClient)  # bypass __init__ (needs live config/keys)
    client.api_key = ""

    cases = []
    for name, text in CASES:
        entry = {"name": name, "input": text}
        try:
            entry["ok"] = True
            entry["value"] = client.parse_json_response(text)
        except Exception as e:
            entry["ok"] = False
            entry["error_type"] = type(e).__name__
        cases.append(entry)

    lenient = []
    for name, text, ts_value in LENIENT_CASES:
        entry = {"name": name, "input": text, "ts_value": ts_value}
        try:
            client.parse_json_response(text)
            entry["python_error_type"] = None  # parser unexpectedly accepted it
        except Exception as e:
            entry["python_error_type"] = type(e).__name__
        lenient.append(entry)

    out = {
        "generator": "packages/sdk/test/fixtures/gen_jsonparse_fixture.py",
        "reference": "utils/llm_client.py::LLMClient.parse_json_response",
        "cases": cases,
        "lenient_cases": lenient,
    }
    dest = Path(__file__).resolve().parent / "jsonparse_fixture.json"
    dest.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ok = sum(1 for c in cases if c["ok"])
    print(
        f"wrote {dest} ({len(cases)} strict cases: {ok} ok, {len(cases) - ok} error; "
        f"{len(lenient)} lenient cases)"
    )


if __name__ == "__main__":
    main()
