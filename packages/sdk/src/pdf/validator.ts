import { PDFDocument } from 'pdf-lib';
import { Pdf2SkillError } from '../errors';

/** MinerU remote API enforces a 200 MB limit; mirror it locally. */
export const MAX_FILE_SIZE = 200 * 1024 * 1024;

export type ValidationError = { code: string; message: string; advice: string };

/**
 * Port of frontend/pdf_validator.py::validate_pdf. Uses pdf-lib for the
 * parseability/encryption checks (PyPDF2 equivalent; acceptance may differ
 * marginally on damaged files — documented known difference).
 */
export function validatePdf(
  fileBytes: Uint8Array,
  filename: string,
): ValidationError | null {
  // 1. Filename presence
  if (!filename) {
    return { code: 'NAME_MISSING', message: '未提供文件名', advice: '请选择一个文件后上传' };
  }
  // 2. Filename extension
  if (!filename.toLowerCase().endsWith('.pdf')) {
    return { code: 'FORMAT_EXTENSION', message: '文件扩展名不是 .pdf', advice: '请确保上传 .pdf 后缀的文件' };
  }
  // 3. Filename safety
  if (filename.includes('..') || filename.includes('/') || filename.includes('\\') || filename.includes('\0')) {
    return { code: 'NAME_INVALID', message: '文件名包含非法字符', advice: '请使用仅含字母、数字、中文、下划线和连字符的文件名' };
  }
  // 4. File not empty
  if (fileBytes.length === 0) {
    return { code: 'SIZE_EMPTY', message: '文件为空（0 字节）', advice: '请上传有效的 PDF 文件' };
  }
  // 5. File size limit
  const sizeMb = fileBytes.length / (1024 * 1024);
  if (fileBytes.length > MAX_FILE_SIZE) {
    return {
      code: 'SIZE_EXCEED',
      message: `文件大小超过 200MB 限制（当前 ${sizeMb.toFixed(1)}MB）`,
      advice: '请拆分文件后重新上传，或使用本地模式处理',
    };
  }
  // 6. PDF magic bytes
  const magic = new TextDecoder('latin1').decode(fileBytes.slice(0, 5));
  if (magic !== '%PDF-') {
    return {
      code: 'FORMAT_INVALID',
      message: '文件内容不是有效的 PDF 格式',
      advice: '请确保上传真正的 PDF 文件，而非重命名的其他格式',
    };
  }
  return null;
}

/** Parseability + encryption checks (the slow structural checks). */
export async function validatePdfStructure(
  fileBytes: Uint8Array,
): Promise<ValidationError | null> {
  try {
    const doc = await PDFDocument.load(fileBytes, { ignoreEncryption: true, throwOnInvalidObject: false });
    if (doc.isEncrypted) {
      return { code: 'FORMAT_ENCRYPTED', message: 'PDF 文件已加密，无法处理', advice: '请先解除密码保护后重新上传' };
    }
    // pdf-lib parses lazily — force the page-tree parse to surface corruption
    if (doc.getPageCount() < 1) {
      throw new Error('empty page tree');
    }
  } catch {
    return {
      code: 'FORMAT_CORRUPT',
      message: 'PDF 文件已损坏，无法解析',
      advice: '请尝试用 PDF 阅读器打开确认文件完整性，或重新导出 PDF',
    };
  }
  return null;
}

/** Full validation: fast checks first, then structural parse. */
export async function validatePdfFull(
  fileBytes: Uint8Array,
  filename: string,
): Promise<ValidationError | null> {
  return validatePdf(fileBytes, filename) ?? (await validatePdfStructure(fileBytes));
}

/** Port of pdf_validator.py::sanitize_filename (keeps letters/digits/CJK/._-). */
export function sanitizeFilename(filename: string): string {
  let name = filename.split(/[\\/]/).pop() ?? '';
  if (!name || name === '.' || name === '..' || name.includes('\0')) {
    throw new Pdf2SkillError('NAME_INVALID', '非法文件名', '请使用有效的文件名');
  }
  name = name.replace(/[^\p{L}\p{N}._-]/gu, '_');
  return name;
}
