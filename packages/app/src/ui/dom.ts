export function escapeHtml(s: unknown): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export type ToastKind = 'success' | 'warn' | 'error';

const TOAST_ICONS: Record<ToastKind, string> = {
  success: 'ph-check-circle',
  warn: 'ph-warning-circle',
  error: 'ph-x-circle',
};

export function showToast(msg: string, kind: ToastKind = 'success'): void {
  const t = document.getElementById('toast');
  if (!t) return;
  t.innerHTML = `<i class="ph ${TOAST_ICONS[kind]}"></i><span>${escapeHtml(msg)}</span>`;
  t.classList.remove('toast-success', 'toast-warn', 'toast-error');
  t.classList.add('toast-' + kind, 'show');
  setTimeout(() => t.classList.remove('show'), kind === 'success' ? 2000 : 3200);
}

export interface ErrorInfo {
  code: string;
  message: string;
  advice: string;
}

export function errInfoOf(e: unknown): ErrorInfo {
  const anyE = e as { errInfo?: ErrorInfo; message?: string; advice?: string; code?: string };
  if (anyE?.errInfo) return anyE.errInfo;
  if (anyE?.code && anyE?.message) {
    return { code: anyE.code, message: anyE.message, advice: anyE.advice ?? '' };
  }
  return { code: 'UNKNOWN', message: anyE?.message || String(e), advice: '' };
}

export function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el;
}

export function val(id: string): string {
  return ($(id) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
}

export function setVal(id: string, v: string): void {
  ($(id) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value = v;
}

export function checked(id: string): boolean {
  return ($(id) as HTMLInputElement).checked;
}

export function setChecked(id: string, v: boolean): void {
  ($(id) as HTMLInputElement).checked = v;
}
