import { describe, it, expect } from 'vitest';
import { validateSkillName, slugifySkillName, SKILL_NAME_MAX_LENGTH } from '../src/naming';
import { SkillEngine } from '../src/stages/skillEngine';

describe('validateSkillName', () => {
  const valid = ['my-book', 'calculus-ii-notes', 'a', 'abc-123', 'skill-1739456789', 'a-b-c'];
  it.each(valid)('accepts %s', (name) => {
    expect(validateSkillName(name)).toEqual([]);
  });

  it.each([
    ['', '名称不能为空'],
    ['My-Book', '小写字母'],
    ['my book', '空格'],
    ['我的书', '中划线'],
    ['my_book', '中划线'],
    ['-book', '开头或结尾'],
    ['book-', '开头或结尾'],
    ['my--book', '连续中划线'],
    ['a'.repeat(SKILL_NAME_MAX_LENGTH + 1), '长度超过'],
  ])('rejects %j with a matching issue', (name, keyword) => {
    const issues = validateSkillName(name);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.some((i) => i.includes(keyword))).toBe(true);
  });

  it('accepts a name of exactly the max length', () => {
    expect(validateSkillName('a'.repeat(SKILL_NAME_MAX_LENGTH))).toEqual([]);
  });
});

describe('slugifySkillName', () => {
  it('converts mixed input to kebab-case', () => {
    expect(slugifySkillName('My Great Book! (v2)')).toBe('my-great-book-v2');
    expect(slugifySkillName('  --Weird___name--  ')).toBe('weird-name');
  });

  it('collapses separators and trims trailing hyphens at the 64-char cut', () => {
    expect(slugifySkillName('a'.repeat(70) + '-')).toBe('a'.repeat(64));
    expect(slugifySkillName('ab'.repeat(32) + 'c-')).not.toMatch(/-$/);
  });

  it('returns empty for non-ASCII-only input so callers can auto-generate', () => {
    expect(slugifySkillName('微积分 第二版')).toBe('');
  });
});

describe('SkillEngine.validateKebabName parity', () => {
  it('agrees with validateSkillName', () => {
    for (const name of ['ok-name', 'Bad Name', '', '-x', 'x-', 'a--b', '我的书', 'a'.repeat(65)]) {
      expect(SkillEngine.validateKebabName(name)).toBe(validateSkillName(name).length === 0);
    }
  });
});
