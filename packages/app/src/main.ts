import { Pdf2Skill, VERSION } from '@pdf2skill/sdk';
import { checkHealth } from './api/backend';
import { loadConfigUI, setupConfigTab } from './tabs/config';
import { setupRunTab } from './tabs/run';
import { refreshOutputs, setupOutputsTab } from './tabs/outputs';

const pdf2skill = new Pdf2Skill();

// ── Tabs ──────────────────────────────────────────────────────────

function switchTab(tabId: string): void {
  document.querySelectorAll<HTMLElement>('.tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll<HTMLElement>('.panel').forEach((p) => {
    p.classList.toggle('active', p.id === 'panel-' + tabId);
  });
  if (tabId === 'outputs') void refreshOutputs().catch(() => {});
}

document.querySelectorAll<HTMLElement>('.tab').forEach((tab) => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab!));
});

// Tutorial jump buttons
document.querySelectorAll<HTMLElement>('[data-goto]').forEach((btn) => {
  btn.addEventListener('click', () => {
    switchTab(btn.dataset.goto!);
    const anchor = btn.dataset.anchor;
    if (anchor) {
      const el = document.getElementById(anchor);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});

// ── Health badge ──────────────────────────────────────────────────

async function updateHealth(): Promise<void> {
  const badge = document.getElementById('health-badge')!;
  const text = document.getElementById('health-text')!;
  const ok = await checkHealth();
  badge.className = 'health-badge ' + (ok ? 'ok' : 'bad');
  text.textContent = ok ? '代理后端已连接' : '代理后端未连接（LLM/MinerU 不可用）';
}

// ── Persistent storage ────────────────────────────────────────────

async function requestPersistentStorage(): Promise<void> {
  try {
    const granted = await navigator.storage?.persist?.();
    console.log('[pdf2skill] persistent storage:', granted ?? 'unavailable');
  } catch {
    // Best-effort — OPFS works without the grant, data may be evicted under pressure.
  }
}

// ── Init ──────────────────────────────────────────────────────────

async function init(): Promise<void> {
  console.log(`[pdf2skill] SDK v${VERSION} (pure frontend)`);
  setupConfigTab(pdf2skill);
  setupRunTab(pdf2skill);
  setupOutputsTab(pdf2skill);

  void requestPersistentStorage();

  await updateHealth();
  const healthTimer = setInterval(() => void updateHealth(), 30_000);
  window.addEventListener('pagehide', () => clearInterval(healthTimer));

  try {
    await loadConfigUI();
  } catch (e) {
    console.warn('[pdf2skill] config UI load failed', e);
  }
}

void init();
