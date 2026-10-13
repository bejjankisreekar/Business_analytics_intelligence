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

    // Read from the <select> each time, so an option added later by page
    // script (e.g. "+ Add new insurance") shows up in the list.
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
      optionsList.innerHTML = filtered.length
        ? filtered
            .map(
              (o) =>
                `<button type="button" data-value="${esc(o.value)}" class="combobox-option${
                  o.value === select.value ? ' combobox-option--active' : ''
                }">${esc(o.label)}</button>`
            )
            .join('')
        : '<p class="combobox-empty">No matches</p>';
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
      select.value = btn.dataset.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      searchInput.value = btn.textContent;
      closeList();
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
          select.value = first.dataset.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          searchInput.value = first.textContent;
          closeList();
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
