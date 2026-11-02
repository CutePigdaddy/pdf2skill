import { Pdf2Skill } from '@pdf2skill/sdk';
import type { JobMeta, OutputTreeNode } from '@pdf2skill/sdk';
import { $, escapeHtml, errInfoOf, showToast } from '../ui/dom';
import { syncSelect } from '../ui/dropdown';

export interface OutputsTabOptions {
  /** Switch to the Run tab with this job selected for resume. */
  onResume?: (jobId: string) => void;
}

let pdf2skill: Pdf2Skill;
let currentJobId = '';
let blobUrl: string | null = null;
let outputsOptions: OutputsTabOptions = {};
let resumableJobIds = new Set<string>();
let skillReadyJobIds = new Set<string>();
let exporting = false;

function updateJobActions(): void {
  const set = (id: string, disabled: boolean) => {
    const btn = document.getElementById(id) as HTMLButtonElement | null;
    if (btn) btn.disabled = disabled;
  };
  const hasJob = !!currentJobId;
  set('btn-resume-job', !hasJob || !resumableJobIds.has(currentJobId));
  set('btn-download-zip', !hasJob || exporting);
  set('btn-download-skill-zip', !hasJob || exporting || !skillReadyJobIds.has(currentJobId));
  set('btn-delete-job', !hasJob || exporting);
}

function fmtBytes(n: number): string {
  if (n < 1024) return n + 'B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + 'GB';
}

// ── Storage usage ─────────────────────────────────────────────────

async function updateStorageUsage(): Promise<void> {
  const el = document.getElementById('storage-usage');
  if (!el) return;
  try {
    const est = await pdf2skill.storageEstimate();
    el.textContent = est && est.quota > 0 ? `存储 ${fmtBytes(est.usage)} / ${fmtBytes(est.quota)}` : '';
  } catch {
    el.textContent = '';
  }
}

// ── Job list ──────────────────────────────────────────────────────

async function refreshJobList(): Promise<void> {
  const sel = $('job-select') as HTMLSelectElement;
  const jobs = await pdf2skill.listJobs();
  resumableJobIds = new Set(jobs.filter((j) => j.resumable).map((j) => j.jobId));
  skillReadyJobIds = new Set(
    jobs.filter((j) => j.completedStages.includes('skill_generation')).map((j) => j.jobId),
  );
  const prev = currentJobId;
  sel.innerHTML = '<option value="">选择任务…</option>' + jobs
    .map((j: JobMeta) => {
      const date = j.updatedAt ? new Date(j.updatedAt).toLocaleString() : '未知时间';
      const stages = j.completedStages.length ? `${j.completedStages.length}/4 阶段` : '未完成';
      return `<option value="${escapeHtml(j.jobId)}">${escapeHtml(j.inputName)}（${stages}，${date}）</option>`;
    })
    .join('');
  if (prev && jobs.some((j) => j.jobId === prev)) sel.value = prev;
  currentJobId = sel.value;
  syncSelect(sel);
  updateJobActions();
}

// ── Tree rendering ────────────────────────────────────────────────

function renderTree(node: OutputTreeNode, parentEl: HTMLElement, pathPrefix: string): void {
  if (!node.children) return;
  for (const child of node.children) {
    const childPath = pathPrefix ? `${pathPrefix}/${child.name}` : child.name;
    const div = document.createElement('div');

    const item = document.createElement('div');
    item.className = 'tree-item';
    if (child.type === 'dir') {
      const countLabel = child.count != null ? `<span class="tree-size">${child.count} 项</span>` : '';
      item.innerHTML = `<span class="tree-icon"><i class="ph ph-folder"></i></span><span class="tree-name">${escapeHtml(child.name)}</span>${countLabel}`;
      div.appendChild(item);

      const childrenDiv = document.createElement('div');
      childrenDiv.className = 'tree-children';
      childrenDiv.style.display = 'none';
      div.appendChild(childrenDiv);

      item.addEventListener('click', () => {
        const open = childrenDiv.style.display !== 'none';
        childrenDiv.style.display = open ? 'none' : 'block';
        item.querySelector('.tree-icon i')!.className = open ? 'ph ph-folder' : 'ph ph-folder-open';
        if (!open && !childrenDiv.hasChildNodes()) renderTree(child, childrenDiv, childPath);
      });
    } else {
      item.innerHTML = `<span class="tree-icon"><i class="ph ph-file-text"></i></span><span class="tree-name">${escapeHtml(child.name)}</span><span class="tree-size">${child.size ? fmtBytes(child.size) : ''}</span>`;
      item.addEventListener('click', () => void previewFile(childPath));
      div.appendChild(item);
    }
    parentEl.appendChild(div);
  }
}

const EMPTY_STATE = `
  <div class="outputs-empty">
    <div class="empty-icon"><i class="ph ph-folder-open"></i></div>
    <div class="empty-title">暂无输出</div>
    <div class="empty-desc">运行 Pipeline 后，输出文件将显示在这里</div>
  </div>`;

async function loadTree(): Promise<void> {
  const treeEl = $('output-tree');
  const viewer = $('file-viewer');
  treeEl.innerHTML = '';
  viewer.style.display = 'none';
  viewer.textContent = '';
  if (blobUrl) {
    URL.revokeObjectURL(blobUrl);
    blobUrl = null;
  }
  if (!currentJobId) {
    treeEl.innerHTML = EMPTY_STATE;
    return;
  }
  try {
    const tree = await pdf2skill.listOutputTree(currentJobId);
    if (!tree.children || tree.children.length === 0) {
      treeEl.innerHTML = `
        <div class="outputs-empty">
          <div class="empty-icon"><i class="ph ph-folder-open"></i></div>
          <div class="empty-title">暂无输出</div>
          <div class="empty-desc">该任务还没有输出文件</div>
        </div>`;
      return;
    }
    renderTree(tree, treeEl, '');
  } catch (e) {
    const info = errInfoOf(e);
    treeEl.innerHTML = `<div class="outputs-empty"><div class="empty-icon"><i class="ph ph-warning"></i></div><div class="empty-title">读取失败</div><div class="empty-desc">${escapeHtml(info.message)}</div></div>`;
  }
}

// ── File preview ──────────────────────────────────────────────────

async function previewFile(path: string): Promise<void> {
  const viewer = $('file-viewer');
  const jobId = currentJobId;
  if (!jobId) return;
  try {
    const file = await pdf2skill.readOutputFile(jobId, path);
    if (jobId !== currentJobId) return;
    viewer.style.display = 'block';
    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      blobUrl = null;
    }
    if (file.type === 'text') {
      viewer.textContent = file.content;
    } else if (file.type === 'image') {
      blobUrl = URL.createObjectURL(file.blob);
      viewer.innerHTML = `<img src="${blobUrl}" style="max-width:100%" alt="${escapeHtml(path)}">`;
    } else {
      viewer.textContent = `(Binary file, ${file.size} bytes)`;
    }
  } catch (e) {
    const info = errInfoOf(e);
    viewer.style.display = 'block';
    viewer.textContent = 'Error: ' + info.message;
  }
}

// ── Zip download / delete ─────────────────────────────────────────

function triggerDownload(blob: Blob, filename: string): void {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
}

async function downloadZip(): Promise<void> {
  if (!currentJobId || exporting) return;
  exporting = true;
  updateJobActions();
  try {
    const blob = await pdf2skill.exportZip(currentJobId);
    triggerDownload(blob, `${currentJobId}.zip`);
    showToast('整包 zip 已生成，正在下载');
  } catch (e) {
    const info = errInfoOf(e);
    showToast('下载失败: ' + info.message + (info.advice ? '：' + info.advice : ''), 'error');
  } finally {
    exporting = false;
    updateJobActions();
  }
}

async function downloadSkillZip(): Promise<void> {
  if (!currentJobId || exporting) return;
  exporting = true;
  updateJobActions();
  try {
    const { blob, skillName } = await pdf2skill.exportSkillZip(currentJobId);
    triggerDownload(blob, `${skillName}.zip`);
    showToast(`Skill zip 已生成：${skillName}.zip`);
  } catch (e) {
    const info = errInfoOf(e);
    showToast('下载失败: ' + info.message + (info.advice ? '：' + info.advice : ''), 'error');
  } finally {
    exporting = false;
    updateJobActions();
  }
}

async function deleteJob(): Promise<void> {
  if (!currentJobId) {
    showToast('请先选择任务', 'warn');
    return;
  }
  if (!confirm(`确定删除任务 '${currentJobId}'？其全部输出与 checkpoint 将从浏览器存储中移除。`)) return;
  try {
    await pdf2skill.deleteJob(currentJobId);
    currentJobId = '';
    showToast('任务已删除');
    await refreshJobList();
    await loadTree();
    await updateStorageUsage();
    $('file-viewer').style.display = 'none';
    updateJobActions();
  } catch (e) {
    const info = errInfoOf(e);
    showToast('删除失败: ' + info.message, 'error');
  }
}

// ── Wire up ───────────────────────────────────────────────────────

export function setupOutputsTab(
  sdkInstance: Pdf2Skill,
  options: OutputsTabOptions = {},
): void {
  pdf2skill = sdkInstance;
  outputsOptions = options;
  ($('job-select') as HTMLSelectElement).addEventListener('change', (e) => {
    currentJobId = (e.target as HTMLSelectElement).value;
    updateJobActions();
    void loadTree();
  });
  $('btn-refresh-outputs').addEventListener('click', () => void refreshOutputs());
  $('btn-download-zip').addEventListener('click', () => void downloadZip());
  $('btn-download-skill-zip').addEventListener('click', () => void downloadSkillZip());
  $('btn-delete-job').addEventListener('click', () => void deleteJob());
  $('btn-resume-job').addEventListener('click', () => {
    if (!currentJobId) {
      showToast('请先选择任务', 'warn');
      return;
    }
    if (!resumableJobIds.has(currentJobId)) {
      showToast('该任务缺少 Stage 1 输入，无法继续；请重新上传原文件', 'warn');
      return;
    }
    outputsOptions.onResume?.(currentJobId);
  });
}

export async function refreshOutputs(): Promise<void> {
  await refreshJobList();
  await loadTree();
  await updateStorageUsage();
}
