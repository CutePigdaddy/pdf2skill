import { describe, expect, it } from 'vitest';
import { parseJsonResponse, repairPythonLiteral } from '../src/llm/jsonParser';
import { Pdf2SkillError } from '../src/errors';

describe('parseJsonResponse', () => {
  it('parses plain JSON', () => {
    expect(parseJsonResponse('{"a": 1}')).toEqual({ a: 1 });
  });

  it('parses JSON wrapped in markdown code fences', () => {
    expect(parseJsonResponse('```json\n{"toc_range": [50, 611]}\n```')).toEqual({
      toc_range: [50, 611],
    });
  });

  it('extracts the outermost object from surrounding prose', () => {
    expect(parseJsonResponse('Here is the result: {"x": "y"} hope it helps')).toEqual({ x: 'y' });
  });

  it('strips reasoning-model think tags', () => {
    const open = '<' + 'think>';
    const close = '<' + '/think>';
    const tagged = `${open}internal reasoning, ignore this${close}{"a": 1}`;
    expect(parseJsonResponse(tagged)).toEqual({ a: 1 });
  });

  it('repairs single-quoted Python literals', () => {
    expect(parseJsonResponse("{'a': 1, 'b': 'two'}")).toEqual({ a: 1, b: 'two' });
  });

  it('repairs bare True/False/None', () => {
    expect(parseJsonResponse("{'ok': True, 'off': False, 'nil': None}")).toEqual({
      ok: true,
      off: false,
      nil: null,
    });
  });

  it('strips trailing commas like Python literal_eval does', () => {
    expect(parseJsonResponse('{"a": 1, "b": 2,}')).toEqual({ a: 1, b: 2 });
    expect(parseJsonResponse("{'a': [1, 2, ], 'b': {'c': 3, }, }")).toEqual({
      a: [1, 2],
      b: { c: 3 },
    });
  });

  it('does not convert True inside string contents', () => {
    expect(parseJsonResponse("{'note': 'True story', 'flag': True}")).toEqual({
      note: 'True story',
      flag: true,
    });
  });

  it('escapes double quotes inside single-quoted strings', () => {
    expect(parseJsonResponse("{'q': 'he said \"hi\"'}")).toEqual({ q: 'he said "hi"' });
  });

  it('handles combined dirt: fences + python literals + trailing prose', () => {
    const dirty = '```json\n{"splits": [1, 2], "atomic": True}\n```\nDone.';
    expect(parseJsonResponse(dirty)).toEqual({ splits: [1, 2], atomic: true });
  });

  it('throws Pdf2SkillError on unparseable input', () => {
    expect(() => parseJsonResponse('no json here at all')).toThrow(Pdf2SkillError);
    expect(() => parseJsonResponse('{"a": definitely broken')).toThrow(Pdf2SkillError);
  });
});

describe('repairPythonLiteral', () => {
  it('keeps already-valid JSON unchanged in meaning', () => {
    expect(JSON.parse(repairPythonLiteral('{"a": [1, 2.5, null]}'))).toEqual({ a: [1, 2.5, null] });
  });

  it('preserves escaped quotes in single-quoted strings', () => {
    expect(JSON.parse(repairPythonLiteral("{'a': 'it\\'s'}"))).toEqual({ a: "it's" });
  });
});
