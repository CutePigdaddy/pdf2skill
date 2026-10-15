/**
 * Levenshtein edit distance with unit costs, operating on Unicode code points
 * (matching Python's `Levenshtein.distance` semantics).
 *
 * Uses Myers' bit-parallel algorithm (O(n*ceil(m/w))) when the pattern fits a
 * 32-bit word, falling back to a classic DP for longer patterns. This matters
 * for anchor search, which can evaluate tens of thousands of windows.
 */

export function codePoints(s: string): string[] {
  return [...s];
}

export function charCount(s: string): number {
  return [...s].length;
}

function naiveDp(a: readonly string[], b: readonly string[]): number {
  const m = a.length;
  const n = b.length;
  let prev = new Array<number>(n + 1);
  let curr = new Array<number>(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n];
}

/** Myers 1999 bit-vector algorithm. `pattern` must have length 1..32. */
function myers(pattern: readonly string[], text: readonly string[]): number {
  const m = pattern.length;
  const peq = new Map<string, number>();
  for (let j = 0; j < m; j++) {
    peq.set(pattern[j], (peq.get(pattern[j]) ?? 0) | (1 << j));
  }
  const mask = m === 32 ? -1 : (1 << m) - 1;
  const high = 1 << (m - 1);
  let pv = mask;
  let mv = 0;
  let score = m;
  for (let i = 0; i < text.length; i++) {
    const eq = peq.get(text[i]) ?? 0;
    const xv = eq | mv;
    const xh = (((eq & pv) + pv) ^ pv) | eq;
    let ph = mv | ~(xh | pv);
    let mh = pv & xh;
    if (ph & high) score += 1;
    else if (mh & high) score -= 1;
    ph = ((ph << 1) | 1) & mask;
    mh = (mh << 1) & mask;
    pv = (mh | ~(xv | ph)) & mask;
    mv = ph & xv;
  }
  return score;
}

export function levenshteinDistance(a: string, b: string): number {
  return levenshteinCp(codePoints(a), codePoints(b));
}

export function levenshteinCp(a: readonly string[], b: readonly string[]): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  if (a.length <= 32) return myers(a, b);
  if (b.length <= 32) return myers(b, a);
  return naiveDp(a, b);
}
