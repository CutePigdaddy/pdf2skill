/**
 * Free-text combobox over a <datalist>.
 *
 * Native datalist popups filter suggestions by the input's current value: a
 * prefilled model (the normal case) makes the popup show only that model, so
 * fetched model lists look like a single entry. This enhancement drops the
 * `list` attribute and renders a themed popup instead, while the datalist stays
 * in the DOM as the data source that `renderDatalists` keeps updating.
 */

const OPEN: Set<HTMLElement> = new Set();

function optionsOf(input: HTMLInputElement): string[] {
  const id = input.dataset.listId;
  const dl = id ? (document.getElementById(id) as HTMLDataListElement | null) : null;
  return dl ? Array.from(dl.options).map((o) => o.value) : [];
}

function close(wrap: HTMLElement, input: HTMLInputElement): void {
  wrap.classList.remove('open');
  input.setAttribute('aria-expanded', 'false');
  OPEN.delete(wrap);
}

function buildMenu(
  input: HTMLInputElement,
  menu: HTMLElement,
  query?: string,
): number {
  const q = query?.trim().toLowerCase() ?? '';
  const models = optionsOf(input).filter((m) => !q || m.toLowerCase().includes(q));
  menu.innerHTML = '';
  for (const model of models) {
    const item = document.createElement('div');
    item.className = 'dd-option';
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(model === input.value));
    item.tabIndex = -1;
    const text = document.createElement('span');
    text.textContent = model;
    const check = document.createElement('i');
    check.className = 'ph ph-check';
    item.append(text, check);
    item.addEventListener('mousedown', (e) => e.preventDefault());
    item.addEventListener('click', () => {
      input.value = model;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      close(menu.parentElement as HTMLElement, input);
      input.focus();
    });
    menu.appendChild(item);
  }
  return models.length;
}

function open(wrap: HTMLElement, input: HTMLInputElement, menu: HTMLElement, query?: string): void {
  for (const other of [...OPEN]) {
    if (other !== wrap) {
      other.classList.remove('open');
      other.querySelector<HTMLInputElement>('input')?.setAttribute('aria-expanded', 'false');
      OPEN.delete(other);
    }
  }
  if (buildMenu(input, menu, query) === 0) {
    close(wrap, input);
    return;
  }
  const rect = input.getBoundingClientRect();
  wrap.classList.toggle('up', rect.bottom + 280 > window.innerHeight && rect.top > 300);
  wrap.classList.add('open');
  input.setAttribute('aria-expanded', 'true');
  OPEN.add(wrap);
}

function focusItem(menu: HTMLElement, index: number): void {
  menu.querySelectorAll<HTMLElement>('.dd-option')[index]?.focus();
}

export function enhanceModelInputs(root: ParentNode = document): void {
  root.querySelectorAll<HTMLInputElement>('input[data-field$="_model"][list]').forEach((input) => {
    if (input.dataset.cb === '1') return;
    const listId = input.getAttribute('list');
    if (!listId) return;
    input.dataset.cb = '1';
    input.dataset.listId = listId;
    input.removeAttribute('list');

    const wrap = document.createElement('div');
    wrap.className = 'dd cb';
    input.before(wrap);
    wrap.appendChild(input);

    const menu = document.createElement('div');
    menu.className = 'dd-menu';
    menu.id = `${listId}-menu`;
    menu.setAttribute('role', 'listbox');
    wrap.appendChild(menu);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', menu.id);

    input.addEventListener('click', () => open(wrap, input, menu));
    input.addEventListener('input', () => open(wrap, input, menu, input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!OPEN.has(wrap)) open(wrap, input, menu);
        focusItem(menu, e.key === 'ArrowDown' ? 0 : menu.children.length - 1);
      } else if (e.key === 'Escape') {
        close(wrap, input);
      } else if (e.key === 'Tab') {
        close(wrap, input);
      }
    });

    menu.addEventListener('keydown', (e) => {
      const items = Array.from(menu.querySelectorAll<HTMLElement>('.dd-option'));
      const i = items.indexOf(document.activeElement as HTMLElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const next = e.key === 'ArrowDown' ? Math.min(i + 1, items.length - 1) : Math.max(i - 1, 0);
        items[next]?.focus();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        (document.activeElement as HTMLElement)?.click();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        close(wrap, input);
        input.focus();
      } else if (e.key === 'Tab') {
        close(wrap, input);
      }
    });
  });
}

document.addEventListener('pointerdown', (e) => {
  for (const wrap of [...OPEN]) {
    if (!wrap.contains(e.target as Node)) {
      wrap.classList.remove('open');
      wrap.querySelector<HTMLInputElement>('input')?.setAttribute('aria-expanded', 'false');
      OPEN.delete(wrap);
    }
  }
});
