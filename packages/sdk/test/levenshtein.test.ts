import { describe, expect, it } from 'vitest';
import { levenshteinDistance } from '../src/stages/levenshtein';
import { naiveLevenshtein } from './helpers/naiveLevenshtein';

describe('levenshteinDistance (Myers bit-parallel)', () => {
  it('matches naive DP on basics', () => {
    expect(levenshteinDistance('', '')).toBe(0);
    expect(levenshteinDistance('abc', '')).toBe(3);
    expect(levenshteinDistance('', 'abc')).toBe(3);
    expect(levenshteinDistance('kitten', 'sitting')).toBe(3);
    expect(levenshteinDistance('flaw', 'lawn')).toBe(2);
    expect(levenshteinDistance('intention', 'execution')).toBe(5);
  });

  it('handles identical and disjoint strings', () => {
    const s = 'The quick brown fox jumps over the lazy';
    expect(levenshteinDistance(s, s)).toBe(0);
    expect(levenshteinDistance('aaaa', 'bbbb')).toBe(4);
  });

  it('matches naive DP on random strings (≤32 pattern)', () => {
    let seed = 42;
    const rand = (n: number) => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed % n;
    };
    const alpha = 'abcdefg';
    for (let t = 0; t < 300; t++) {
      const lenA = 1 + rand(32);
      const lenB = 1 + rand(32);
      let a = '';
      let b = '';
      for (let i = 0; i < lenA; i++) a += alpha[rand(alpha.length)];
      for (let i = 0; i < lenB; i++) b += alpha[rand(alpha.length)];
      expect(levenshteinDistance(a, b)).toBe(naiveLevenshtein(a, b));
    }
  });

  it('falls back to DP for patterns longer than 32', () => {
    const a = 'x'.repeat(40);
    const b = 'y'.repeat(35);
    expect(levenshteinDistance(a, b)).toBe(40);
    expect(levenshteinDistance('abc', 'x'.repeat(60))).toBe(60);
  });

  it('counts astral (non-BMP) characters as single code points', () => {
    // U+1F600 is one code point in Python but two UTF-16 units in JS.
    expect(levenshteinDistance('😀😀😀', '😀😀')).toBe(1);
    expect(levenshteinDistance('a😀b', 'ab')).toBe(1);
  });

  it('matches Python-generated expected values', () => {
    // Produced by: python -c "from Levenshtein import distance; ..."
    const cases: Array<[string, string, number]> = [
      ['kitten', 'sitting', 3],
      ['saturday', 'sunday', 3],
      ['阁楼上的灯', '阁楼的灯', 1],
      ['😀emoji👍test', 'emoji_test', 2],
      ['anchor-30-chars-xxxxxxxxxxxxxx', 'anchor-30-chars-xxxxxxxxxxxxxy', 1],
    ];
    for (const [a, b, expected] of cases) {
      expect(levenshteinDistance(a, b)).toBe(expected);
    }
  });
});
