/** Structured error carrying the code/message/advice triple used across the UI. */
export class Pdf2SkillError extends Error {
  readonly code: string;
  readonly advice: string;
  readonly stage?: StageKey;

  constructor(code: string, message: string, advice = '', stage?: StageKey, cause?: unknown) {
    super(message);
    this.name = 'Pdf2SkillError';
    this.code = code;
    this.advice = advice;
    this.stage = stage;
    if (cause !== undefined) (this as { cause?: unknown }).cause = cause;
  }
}

export type StageKey = 'mineru' | 'chunking' | 'peeling' | 'skill';

export function isPdf2SkillError(e: unknown): e is Pdf2SkillError {
  return e instanceof Pdf2SkillError;
}
