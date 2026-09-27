/**
 * SortableTable — makes any <table> with <th data-sort-key="..."> headers
 * click-to-sort. Sorts by each row's matching <td data-value="..."> when
 * present (for numbers/dates), otherwise falls back to the cell's text.
 * Toggles ascending/descending on repeated clicks of the same header.
 */
(function (global) {
  function enableSortableTable(table) {
    const headers = table.querySelectorAll('thead th[data-sort-key]');
    const tbody = table.querySelector('tbody');
    if (!headers.length || !tbody) return;

    headers.forEach(function (th, colIndex) {
      th.classList.add('sortable-col');
      th.addEventListener('click', function () {
        const ascending = th.dataset.sortDir !== 'asc';
        headers.forEach((h) => delete h.dataset.sortDir);
        th.dataset.sortDir = ascending ? 'asc' : 'desc';
        headers.forEach((h) => h.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add(ascending ? 'sort-asc' : 'sort-desc');

        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function (rowA, rowB) {
          const a = cellValue(rowA, colIndex);
          const b = cellValue(rowB, colIndex);
          if (a < b) return ascending ? -1 : 1;
          if (a > b) return ascending ? 1 : -1;
          return 0;
        });
        rows.forEach((row) => tbody.appendChild(row));
      });
    });
  }

  function cellValue(row, colIndex) {
    const cell = row.children[colIndex];
    if (!cell) return '';
    if (cell.dataset.value !== undefined) {
      const num = parseFloat(cell.dataset.value);
      return isNaN(num) ? cell.dataset.value : num;
    }
    const text = cell.textContent.trim();
    const num = parseFloat(text);
    return isNaN(num) ? text.toLowerCase() : num;
  }

  global.SortableTable = { enable: enableSortableTable };
})(window);
