import { Pdf2Skill, isPdf2SkillError } from '@pdf2skill/sdk';
import type { ResettableStage, StageEvent } from '@pdf2skill/sdk';
import { $, escapeHtml, errInfoOf, showToast } from '../ui/dom';

const STAGE_KEYS = ['mineru', 'chunking', 'peeling', 'skill'] as const;

const RESTART_MODE_TO_STAGE: Record<string, ResettableStage | undefined> = {
  'from-pdf': 'pdf_conversion',
  'from-chunk': 'llm_chunking',
  'from-tree': 'tree_merging',
};

let pdf2skill: Pdf2Skill;
let selectedFile: File | null = null;
let running = false;

// ── Stage progress ────────────────────────────────────────────────

const stageStatus: Record<string, string> = {};

function renderStage(e: StageEvent): void {
  $('pipeline-progress').classList.add('visible');
  const i = STAGE_KEYS.indexOf(e.stage as (typeof STAGE_KEYS)[number]);
  if (i === -1) return;
  stageStatus[e.stage] = e.status;

  const stepEl = $('step-' + e.stage);
  stepEl.classList.remove('pending', 'active', 'done', 'failed');
  stepEl.classList.add(e.status);
  if (e.status === 'active' || e.status === 'done') {
    const detailEl = $('step-' + e.stage + '-detail');
    const parts: string[] = [];
    if (e.resumed) parts.push('已从 checkpoint 恢复');
    if (e.percent !== undefined) parts.push(`${e.percent}%`);
    if (e.detail) parts.push(e.detail);
    detailEl.textContent = parts.join(' · ');
  }

  const connEl = document.getElementById('conn-' + (i + 1));
  if (connEl) {
    const prev = stageStatus[STAGE_KEYS[i]];
    const next = stageStatus[STAGE_KEYS[i + 1]];
    connEl.classList.toggle('done', !!(next && (prev === 'done' || prev === 'failed')));
  }
}

function resetPipelineProgress(): void {
  $('pipeline-progress').classList.remove('visible');
  STAGE_KEYS.forEach((key, i) => {
    const stepEl = $('step-' + key);
    stepEl.classList.remove('active', 'done', 'failed');
    stepEl.classList.add('pending');
    $('step-' + key + '-detail').textContent = '';
    stageStatus[key] = 'pending';
    const connEl = document.getElementById('conn-' + (i + 1));
    if (connEl) connEl.classList.remove('done');
  });
}

// ── Logs & errors ─────────────────────────────────────────────────

function appendLog(level: 'info' | 'warn' | 'error', message: string): void {
  const box = $('log-box');
  const placeholder = box.querySelector('.log-placeholder');
  if (placeholder) placeholder.remove();
  const line = document.createElement('div');
  line.textContent = message;
  if (level === 'warn') line.style.color = '#fbbf24';
  if (level === 'error') line.style.color = '#f87171';
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function showPipelineError(code: string, message: string, advice: string): void {
  $('mineru-error-code').textContent = code ? `错误码: ${code} · ${message}` : message;
  $('mineru-error-advice').textContent = advice || '请稍后重试或检查配置';
  $('mineru-error').classList.add('active');
}

function hidePipelineError(): void {
  $('mineru-error').classList.remove('active');
}

function showUploadError(message: string, advice: string): void {
  $('upload-error-title').textContent = message;
  $('upload-error-advice').textContent = advice || '';
  $('upload-error').style.display = 'block';
}

// ── File selection ────────────────────────────────────────────────

function selectFile(file: File): void {
  const ext = file.name.toLowerCase().split('.').pop() || '';
  if (ext !== 'pdf' && ext !== 'md' && ext !== 'markdown') {
    showUploadError('仅支持 .pdf 或 .md 文件', '请选择 PDF 或 Markdown 文件');
    return;
  }
  if (ext === 'pdf') {
    if (file.size === 0) {
      showUploadError('文件为空', '请选择有效的 PDF 文件');
      return;
    }
    if (file.size > 200 * 1024 * 1024) {
      showUploadError(
        `文件大小超过 200MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`,
        '请拆分文件后重新选择',
      );
      return;
    }
  }
  $('upload-error').style.display = 'none';
  selectedFile = file;
  $('selected-file').style.display = 'block';
  $('selected-file-name').textContent = file.name;
  $('selected-file-size').textContent = `${(file.size / 1024).toFixed(1)}KB`;
  (document.getElementById('input-mode') as HTMLSelectElement).value = ext === 'pdf' ? 'pdf' : 'markdown';
}

function setupDropzone(): void {
  const zone = $('dropzone');
  const input = $('upload-input') as HTMLInputElement;
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files && input.files.length > 0) selectFile(input.files[0]);
  });
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (e.dataTransfer && e.dataTransfer.files.length > 0) selectFile(e.dataTransfer.files[0]);
  });
}

// ── Run / stop ────────────────────────────────────────────────────

function setRunning(isRunning: boolean): void {
  running = isRunning;
  ($('btn-start') as HTMLButtonElement).disabled = isRunning;
  $('btn-stop').style.display = isRunning ? 'inline-flex' : 'none';
}

function setStatus(label: string, cls: string, dot = false): void {
  $('run-status').innerHTML = `<span class="status ${cls}">${dot ? '<span class="status-dot"></span>' : ''}${escapeHtml(label)}</span>`;
}

async function startPipeline(): Promise<void> {
  if (!selectedFile) {
    showToast('请先选择输入文件', 'warn');
    return;
  }

  const mode = (document.getElementById('input-mode') as HTMLSelectElement).value as 'pdf' | 'markdown';
  const restartMode = (document.getElementById('restart-mode') as HTMLSelectElement).value;

  // Log box reset
  $('log-box').innerHTML = '';
  hidePipelineError();
  resetPipelineProgress();
  setRunning(true);
  setStatus('运行中', 'status-running', true);

  try {
    const result = await pdf2skill.run(
      { kind: mode, file: selectedFile, name: selectedFile.name },
      {
        restart: restartMode === 'restart',
        fromStage: RESTART_MODE_TO_STAGE[restartMode],
      },
    );
    setStatus('完成', 'status-done');
    showToast(`技能库已生成: ${result.skillName}（${result.referenceCount} 个引用文件）`);
  } catch (e) {
    if (isPdf2SkillError(e) && e.code === 'ABORTED') {
      setStatus('已停止', 'status-failed');
      appendLog('warn', '任务已中止 — 已完成阶段的 checkpoint 已保存，重新运行同一文件可断点续跑');
    } else {
      const info = errInfoOf(e);
      setStatus('失败', 'status-failed');
      showPipelineError(info.code, info.message, info.advice);
      // Mark the failing stage dot as failed if known
      if (isPdf2SkillError(e) && e.stage) {
        const stepEl = document.getElementById('step-' + e.stage);
        if (stepEl) {
          stepEl.classList.remove('pending', 'active', 'done');
          stepEl.classList.add('failed');
        }
      }
    }
  } finally {
    setRunning(false);
  }
}

// ── Wire up ───────────────────────────────────────────────────────

export function setupRunTab(sdkInstance: Pdf2Skill): void {
  pdf2skill = sdkInstance;
  setupDropzone();
  $('btn-start').addEventListener('click', () => void startPipeline());
  $('btn-stop').addEventListener('click', () => pdf2skill.abort());

  pdf2skill.on('stage', renderStage);
  pdf2skill.on('log', (e) => appendLog(e.level, e.message));
  pdf2skill.on('error', (e) => appendLog('error', `[${e.error.code}] ${e.error.message}`));

  window.addEventListener('beforeunload', (e) => {
    if (running) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}
