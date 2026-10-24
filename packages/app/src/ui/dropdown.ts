/**
 * Progressive-enhancement dropdown.
 *
 * The native <select> popup is rendered by the OS and cannot be themed, so
 * each select is visually replaced by a trigger button + custom listbox.
 * The native select stays in the DOM as the single source of truth:
 *  - reading `select.value` keeps working everywhere,
 *  - code that rebuilds options (innerHTML) is picked up by a MutationObserver,
 *  - choosing an option sets the value and dispatches a real `change` event,
 *    so existing onchange handlers fire unchanged.
 */

const OPEN_MENUS: Set<HTMLElement> = new Set();

function selectedLabel(sel: HTMLSelectElement): string {
  return sel.options[sel.selectedIndex]?.text ?? '';
}

function buildMenu(sel: HTMLSelectElement, menu: HTMLElement, trigger: HTMLElement): void {
  menu.innerHTML = '';
  for (const opt of Array.from(sel.options)) {
    const item = document.createElement('div');
    item.className = 'dd-option';
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(opt.selected));
    item.dataset.value = opt.value;
    item.tabIndex = -1;
    const text = document.createElement('span');
    text.textContent = opt.text;
    const check = document.createElement('i');
    check.className = 'ph ph-check';
    item.append(text, check);
    item.addEventListener('click', () => {
      if (sel.value !== opt.value) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      }
      close(menu);
      trigger.focus();
    });
    menu.appendChild(item);
  }
}

function open(menu: HTMLElement, trigger: HTMLElement, sel: HTMLSelectElement): void {
  for (const other of OPEN_MENUS) close(other);
  buildMenu(sel, menu, trigger);
  // drop up when there is no room below the trigger
  const rect = trigger.getBoundingClientRect();
  menu.classList.toggle('up', rect.bottom + 280 > window.innerHeight && rect.top > 300);
  menu.parentElement!.classList.add('open');
  trigger.setAttribute('aria-expanded', 'true');
  OPEN_MENUS.add(menu);
}

function close(menu: HTMLElement): void {
  menu.parentElement!.classList.remove('open');
  const trigger = menu.parentElement!.querySelector<HTMLElement>('.dd-trigger');
  trigger?.setAttribute('aria-expanded', 'false');
  OPEN_MENUS.delete(menu);
}

/** Refresh one select's trigger label + menu (call after programmatic .value writes). */
export function syncSelect(sel: HTMLSelectElement): void {
  const wrap = sel.nextElementSibling as HTMLElement | null;
  const labelEl = wrap?.querySelector<HTMLElement>('.dd-label');
  if (labelEl) labelEl.textContent = selectedLabel(sel) || sel.options[0]?.text || '';
  const menu = wrap?.querySelector<HTMLElement>('.dd-menu');
  if (menu && OPEN_MENUS.has(menu)) buildMenu(sel, menu, menu.parentElement!.querySelector<HTMLElement>('.dd-trigger')!);
}

function enhance(sel: HTMLSelectElement): void {
  if (sel.dataset.dd === '1' || sel.multiple) return;
  sel.dataset.dd = '1';
  sel.classList.add('dd-native');

  const wrap = document.createElement('div');
  wrap.className = 'dd ' + sel.className;

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'dd-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  const labelEl = document.createElement('span');
  labelEl.className = 'dd-label';
  labelEl.textContent = selectedLabel(sel) || sel.options[0]?.text || '';
  const caret = document.createElement('i');
  caret.className = 'ph ph-caret-down';
  trigger.append(labelEl, caret);

  const menu = document.createElement('div');
  menu.className = 'dd-menu';
  menu.setAttribute('role', 'listbox');

  wrap.append(trigger, menu);
  sel.after(wrap);

  const refresh = (): void => {
    labelEl.textContent = selectedLabel(sel) || sel.options[0]?.text || '';
  };
  new MutationObserver(refresh).observe(sel, { childList: true });
  sel.addEventListener('change', refresh);

  trigger.addEventListener('click', () => {
    OPEN_MENUS.has(menu) ? close(menu) : open(menu, trigger, sel);
  });
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (!OPEN_MENUS.has(menu)) open(menu, trigger, sel);
      focusOption(menu, Math.max(sel.selectedIndex, 0));
    }
  });

  menu.addEventListener('keydown', (e) => {
    const items = Array.from(menu.querySelectorAll<HTMLElement>('.dd-option'));
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') {
      close(menu);
      trigger.focus();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = e.key === 'ArrowDown' ? Math.min(i + 1, items.length - 1) : Math.max(i - 1, 0);
      focusOption(menu, next);
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      focusOption(menu, e.key === 'Home' ? 0 : items.length - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      (document.activeElement as HTMLElement)?.click();
      trigger.focus();
    } else if (e.key === 'Tab') {
      close(menu);
    }
  });
}

function focusOption(menu: HTMLElement, index: number): void {
  const items = menu.querySelectorAll<HTMLElement>('.dd-option');
  items[index]?.focus();
}

// One open dropdown at a time; clicking outside closes it.
document.addEventListener('click', (e) => {
  const target = e.target as HTMLElement;
  for (const menu of [...OPEN_MENUS]) {
    if (!menu.parentElement!.contains(target)) close(menu);
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') for (const menu of [...OPEN_MENUS]) close(menu);
});

/** Enhance every select currently in the document. */
export function enhanceSelects(root: ParentNode = document): void {
  root.querySelectorAll('select').forEach((s) => enhance(s as HTMLSelectElement));
}
