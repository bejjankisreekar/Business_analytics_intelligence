/**
 * Initialize searchable dropdowns on the page.
 * Call this after DOM is loaded or whenever new searchable dropdowns are added.
 */
function initializeSearchableDropdowns() {
  document.querySelectorAll('.searchable-dropdown').forEach((container) => {
    const select = container.querySelector('select');
    const searchInput = container.querySelector('.searchable-dropdown-search');
    const optionsList = container.querySelector('.searchable-dropdown-options');

    if (!select || !searchInput || !optionsList) return;

    // Opt-in (data-allow-new on the <select>): a typed name that is not listed
    // can be picked as a new option.
    const allowNew = select.hasAttribute('data-allow-new');

    // Read from the <select> each time, so an option added later by page
    // script shows up in the list.
    function getOptions() {
      return Array.from(select.options).map((o) => ({ value: o.value, label: o.text }));
    }

    // Labels can be user-typed text, so never put them into HTML raw.
    function esc(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function currentLabel() {
      const opt = getOptions().find((o) => o.value === select.value);
      return opt ? opt.label : '';
    }

    function renderOptions(filter) {
      const options = getOptions();
      const q = (filter || '').trim().toLowerCase();
      const filtered = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
      const typed = (filter || '').trim();
      const exact = options.some((o) => o.label.toLowerCase() === q);
      const addNew = allowNew && typed && !exact
        ? `<button type="button" data-value="${esc(typed)}" data-new="true" class="combobox-option">+ Use “${esc(typed)}”</button>`
        : '';
      optionsList.innerHTML = filtered.length || addNew
        ? addNew + filtered
            .map(
              (o) =>
                `<button type="button" data-value="${esc(o.value)}" class="combobox-option${
                  o.value === select.value ? ' combobox-option--active' : ''
                }">${esc(o.label)}</button>`
            )
            .join('')
        : '<p class="combobox-empty">No matches</p>';
    }

    function choose(value, label, isNew) {
      if (isNew && !getOptions().some((o) => o.value === value)) {
        select.add(new Option(label, value));
      }
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      searchInput.value = label;
      closeList();
    }

    function openList() {
      renderOptions(searchInput.value === currentLabel() ? '' : searchInput.value);
      optionsList.classList.remove('hidden');
    }

    function closeList() {
      optionsList.classList.add('hidden');
    }

    searchInput.value = currentLabel();

    // Page script that sets the select directly and fires "change" keeps the
    // visible text in step.
    select.addEventListener('change', () => {
      searchInput.value = currentLabel();
    });

    searchInput.addEventListener('focus', () => {
      searchInput.select();
      openList();
    });

    searchInput.addEventListener('input', () => {
      renderOptions(searchInput.value);
      optionsList.classList.remove('hidden');
    });

    optionsList.addEventListener('mousedown', (e) => {
      const btn = e.target.closest('[data-value]');
      if (!btn) return;
      e.preventDefault();
      const isNew = btn.dataset.new === 'true';
      choose(btn.dataset.value, isNew ? btn.dataset.value : btn.textContent, isNew);
    });

    document.addEventListener('click', (e) => {
      if (!container.contains(e.target)) {
        closeList();
        searchInput.value = currentLabel();
      }
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeList();
        searchInput.blur();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const first = optionsList.querySelector('[data-value]');
        if (first) {
          const isNew = first.dataset.new === 'true';
          choose(first.dataset.value, isNew ? first.dataset.value : first.textContent, isNew);
        }
      }
    });
  });
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeSearchableDropdowns);
} else {
  initializeSearchableDropdowns();
}
