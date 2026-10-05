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

    const options = Array.from(select.options).map((o) => ({ value: o.value, label: o.text }));

    function currentLabel() {
      const opt = options.find((o) => o.value === select.value);
      return opt ? opt.label : '';
    }

    function renderOptions(filter) {
      const q = (filter || '').trim().toLowerCase();
      const filtered = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
      optionsList.innerHTML = filtered.length
        ? filtered
            .map(
              (o) =>
                `<button type="button" data-value="${o.value}" class="combobox-option${
                  o.value === select.value ? ' combobox-option--active' : ''
                }">${o.label}</button>`
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
