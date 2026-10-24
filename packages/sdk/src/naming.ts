/**
 * Output / skill name rules, mirroring the Claude Agent Skill spec's
 * kebab-case `name` field. The output name doubles as the output directory
 * name and the SKILL.md frontmatter `name`, so it must stay spec-compliant.
 */

/** Human-readable naming convention shown in UI hints and error advice. */
export const SKILL_NAME_RULES =
  '仅限小写字母、数字和中划线（-），长度 ≤ 64 字符；不能以 - 开头或结尾，不能有连续 --（例如 my-book-notes）。';

/** Maximum length of a skill name in code points (skill spec limit). */
export const SKILL_NAME_MAX_LENGTH = 64;

/**
 * Validate a skill/output name against the kebab-case spec.
 * Returns violation messages; an empty array means the name is valid.
 */
export function validateSkillName(name: string): string[] {
  if (!name) return ['名称不能为空'];
  const issues: string[] = [];
  const chars = [...name];
  if (chars.length > SKILL_NAME_MAX_LENGTH) {
    issues.push(`长度超过 ${SKILL_NAME_MAX_LENGTH} 个字符（当前 ${chars.length}）`);
  }
  if (/[A-Z]/.test(name)) issues.push('必须全部使用小写字母');
  if (/\s/.test(name)) issues.push('不能包含空格');
  if (!/^[a-z0-9-]+$/.test(name)) issues.push('只能包含小写字母、数字和中划线（-）');
  if (name.startsWith('-') || name.endsWith('-')) issues.push('不能以中划线（-）开头或结尾');
  if (name.includes('--')) issues.push('不能包含连续中划线（--）');
  return issues;
}

/**
 * Best-effort kebab-case conversion for auto-deriving an output name from a
 * filename stem. Non-ASCII input (e.g. Chinese titles) slugifies to '' —
 * callers should fall back to leaving the name empty (auto-generate).
 */
export function slugifySkillName(input: string): string {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SKILL_NAME_MAX_LENGTH)
    .replace(/-+$/g, '');
}
