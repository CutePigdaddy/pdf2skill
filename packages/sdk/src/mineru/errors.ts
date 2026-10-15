import { Pdf2SkillError } from '../errors';

/** Port of utils/logger.py::MinerUAPIError.ERROR_MAP. */
export const MINERU_ERROR_MAP: Record<string, { zh: string; advice: string }> = {
  A0202: { zh: 'API Token 错误', advice: '请检查 API Key 是否正确，或在 API 管理页面重新创建' },
  A0211: { zh: 'API Token 过期', advice: '请在 API 管理页面创建新 Token' },
  '-500': { zh: '请求参数错误', advice: '请检查 MinerU 配置项是否正确' },
  '-60001': { zh: '生成上传链接失败', advice: '请稍后重试' },
  '-60002': { zh: '文件类型不支持', advice: '请确保文件为 PDF/Word/PPT/Excel/图片，且文件名含正确后缀' },
  '-60003': { zh: '文件读取失败', advice: '文件可能已损坏，请重新上传' },
  '-60004': { zh: '空文件', advice: '请上传有效文件' },
  '-60005': { zh: '文件超过 200MB', advice: '请拆分文件后重新上传' },
  '-60006': { zh: '文件页数超限', advice: '请在配置中设置 page_ranges 或拆分文件' },
  '-60007': { zh: '模型服务不可用', advice: '请稍后重试' },
  '-60008': { zh: '文件读取超时', advice: '请检查文件 URL 是否可访问' },
  '-60009': { zh: '任务队列已满', advice: '请稍后重试' },
  '-60010': { zh: '解析失败', advice: '请稍后重试，或尝试切换 model_version' },
  '-60011': { zh: '获取有效文件失败', advice: '请确保文件已上传' },
  '-60015': { zh: '文件转换失败', advice: '请手动转为 PDF 后重新上传' },
  '-60018': { zh: '每日配额已用完', advice: '免费额度已用尽，请明日再试' },
  '-NO_TOKEN': { zh: 'API Key 未设置', advice: '请在配置页面设置 MinerU API Key' },
};

const CODE_FAMILY: Record<string, string> = {
  A0202: 'MINERU_AUTH',
  A0211: 'MINERU_AUTH',
  '-60002': 'MINERU_FILE',
  '-60003': 'MINERU_FILE',
  '-60004': 'MINERU_FILE',
  '-60005': 'MINERU_SIZE',
  '-60006': 'MINERU_SIZE',
  '-60018': 'MINERU_QUOTA',
  '-60019': 'MINERU_QUOTA',
  '-60007': 'MINERU_SERVICE',
  '-60009': 'MINERU_SERVICE',
  '-60010': 'MINERU_SERVICE',
  '-60001': 'MINERU_SERVICE',
  '-60011': 'MINERU_SERVICE',
};

/** Port of core/pdf_processor.py::_raise_mineru_error. */
export function mineruError(errCode: string | number | undefined, apiMsg = ''): Pdf2SkillError {
  const code = errCode === undefined || errCode === null ? '' : String(errCode);
  const mapped = MINERU_ERROR_MAP[code];
  return new Pdf2SkillError(
    CODE_FAMILY[code] ?? 'MINERU_API',
    mapped?.zh ?? (apiMsg || '未知错误'),
    mapped?.advice ?? '请稍后重试或检查配置',
    'mineru',
  );
}
