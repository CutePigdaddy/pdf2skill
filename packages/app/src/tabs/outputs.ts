import { Pdf2Skill } from '@pdf2skill/sdk';
import type { JobMeta, OutputTreeNode } from '@pdf2skill/sdk';
import { $, escapeHtml, errInfoOf, showToast } from '../ui/dom';

let pdf2skill: Pdf2Skill;
let currentJobId = '';
let blobUrl: string | null = null;

function fmtBytes(n: number): string {
  return (n / 1024).toFixed(1) + 'KB';
}

// ── Job list ──────────────────────────────────────────────────────

async function refreshJobList(): Promise<void> {
  const sel = $('job-select') as HTMLSelectElement;
  const jobs = await pdf2skill.listJobs();
  const prev = currentJobId;
  sel.innerHTML = '<option value="">— 选择任务 —</option>' + jobs
    .map((j: JobMeta) => {
      const date = j.updatedAt ? new Date(j.updatedAt).toLocaleString() : '未知时间';
      const stages = j.completedStages.length ? `${j.completedStages.length}/4 阶段` : '未完成';
      return `<option value="${escapeHtml(j.jobId)}">${escapeHtml(j.inputName)} · ${stages} · ${date}</option>`;
    })
    .join('');
  if (prev && jobs.some((j) => j.jobId === prev)) sel.value = prev;
  currentJobId = sel.value;
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
      const countLabel = child.count != null ? `<span class="tree-size">${child.count} files</span>` : '';
      item.innerHTML = `<span class="tree-icon">📁</span><span class="tree-name">${escapeHtml(child.name)}</span>${countLabel}`;
      div.appendChild(item);

      const childrenDiv = document.createElement('div');
      childrenDiv.className = 'tree-children';
      childrenDiv.style.display = 'none';
      div.appendChild(childrenDiv);

      item.addEventListener('click', () => {
        const open = childrenDiv.style.display !== 'none';
        childrenDiv.style.display = open ? 'none' : 'block';
        if (!open && !childrenDiv.hasChildNodes()) renderTree(child, childrenDiv, childPath);
      });
    } else {
      item.innerHTML = `<span class="tree-icon">📄</span><span class="tree-name">${escapeHtml(child.name)}</span><span class="tree-size">${child.size ? fmtBytes(child.size) : ''}</span>`;
      item.addEventListener('click', () => void previewFile(childPath));
      div.appendChild(item);
    }
    parentEl.appendChild(div);
  }
}

async function loadTree(): Promise<void> {
  const treeEl = $('output-tree');
  treeEl.innerHTML = '';
  if (!currentJobId) {
    treeEl.innerHTML = `
      <div class="outputs-empty">
        <div class="empty-icon">📂</div>
        <div class="empty-title">暂无输出</div>
        <div class="empty-desc">运行 Pipeline 后，输出文件将显示在这里</div>
      </div>`;
    return;
  }
  try {
    const tree = await pdf2skill.listOutputTree(currentJobId);
    if (!tree.children || tree.children.length === 0) {
      treeEl.innerHTML = `
        <div class="outputs-empty">
          <div class="empty-icon">📂</div>
          <div class="empty-title">暂无输出</div>
          <div class="empty-desc">该任务还没有输出文件</div>
        </div>`;
      return;
    }
    renderTree(tree, treeEl, '');
  } catch (e) {
    const info = errInfoOf(e);
    treeEl.innerHTML = `<div class="outputs-empty"><div class="empty-title">读取失败</div><div class="empty-desc">${escapeHtml(info.message)}</div></div>`;
  }
}

// ── File preview ──────────────────────────────────────────────────

async function previewFile(path: string): Promise<void> {
  const viewer = $('file-viewer');
  try {
    const file = await pdf2skill.readOutputFile(currentJobId, path);
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

async function downloadZip(): Promise<void> {
  if (!currentJobId) {
    alert('请先选择任务');
    return;
  }
  try {
    const blob = await pdf2skill.exportZip(currentJobId);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${currentJobId}.zip`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
    showToast('zip 已生成，正在下载');
  } catch (e) {
    const info = errInfoOf(e);
    alert('下载失败: ' + info.message + (info.advice ? '\n' + info.advice : ''));
  }
}

async function deleteJob(): Promise<void> {
  if (!currentJobId) {
    alert('请先选择任务');
    return;
  }
  if (!confirm(`确定删除任务 '${currentJobId}'？其全部输出与 checkpoint 将从浏览器存储中移除。`)) return;
  try {
    await pdf2skill.deleteJob(currentJobId);
    currentJobId = '';
    showToast('任务已删除');
    await refreshJobList();
    await loadTree();
    $('file-viewer').style.display = 'none';
  } catch (e) {
    const info = errInfoOf(e);
    alert('删除失败: ' + info.message);
  }
}

// ── Wire up ───────────────────────────────────────────────────────

export function setupOutputsTab(sdkInstance: Pdf2Skill): void {
  pdf2skill = sdkInstance;
  ($('job-select') as HTMLSelectElement).addEventListener('change', (e) => {
    currentJobId = (e.target as HTMLSelectElement).value;
    void loadTree();
  });
  $('btn-refresh-outputs').addEventListener('click', () => {
    void (async () => {
      await refreshJobList();
      await loadTree();
    })();
  });
  $('btn-download-zip').addEventListener('click', () => void downloadZip());
  $('btn-delete-job').addEventListener('click', () => void deleteJob());
}

export async function refreshOutputs(): Promise<void> {
  await refreshJobList();
  await loadTree();
}
