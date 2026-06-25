(function () {
  const state = {
    columns: [],
    initialRows: [],
    rows: [],
    deleted: [],
    draggingId: null,
    search: '',
    source: new URLSearchParams(window.location.search).get('source') || 'sorted',
  };

  const body = document.getElementById('volunteer-body');
  const summary = document.getElementById('volunteer-summary');
  const errorBox = document.getElementById('volunteer-error');
  const searchInput = document.getElementById('volunteer-search');

  function rowId(row, index) {
    return `${index}-${row['院校'] || ''}-${row['专业'] || ''}-${row['2025最低位次'] || ''}`;
  }

  function escapeCsv(value) {
    const text = String(value == null ? '' : value);
    if (/[",\n\r]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }

  function downloadCsv() {
    const lines = [
      state.columns.map(escapeCsv).join(','),
      ...state.rows.map((row) => state.columns.map((col) => escapeCsv(row[col])).join(',')),
    ];
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = state.source === 'supplement'
      ? '63334位次-稳保补充清单-手动筛选.csv'
      : 'hebei-history-query-手动排序志愿清单.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function visibleRows() {
    const keyword = state.search.trim().toLowerCase();
    if (!keyword) {
      return state.rows.map((row, index) => ({ row, index }));
    }
    return state.rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => ['院校', '专业', '冲稳保', '专业权重说明', '备注']
        .some((key) => String(row[key] || '').toLowerCase().includes(keyword)));
  }

  function updateSummary() {
    summary.textContent = `当前 ${state.rows.length} 条，已删除 ${state.deleted.length} 条，原始 ${state.initialRows.length} 条。`;
  }

  function moveRow(from, to) {
    if (to < 0 || to >= state.rows.length || from === to) return;
    const [row] = state.rows.splice(from, 1);
    state.rows.splice(to, 0, row);
    render();
  }

  function deleteRow(index) {
    const [row] = state.rows.splice(index, 1);
    if (row) state.deleted.push(row);
    render();
  }

  function render() {
    updateSummary();
    const rows = visibleRows();
    body.innerHTML = '';
    if (!rows.length) {
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="12" class="muted">没有匹配的数据。</td>';
      body.appendChild(tr);
      return;
    }
    rows.forEach(({ row, index }) => {
      const tr = document.createElement('tr');
      const id = row.__id;
      tr.draggable = true;
      tr.dataset.id = id;
      tr.innerHTML = `
        <td class="volunteer-order">${index + 1}</td>
        <td class="volunteer-row-actions">
          <button type="button" data-action="up" data-index="${index}" title="上移">↑</button>
          <button type="button" data-action="down" data-index="${index}" title="下移">↓</button>
          <button type="button" data-action="delete" data-index="${index}" class="danger-button" title="删除">删</button>
        </td>
        <td><span class="strategy-tag strategy-${row['冲稳保'] || '待判断'}">${row['冲稳保'] || ''}</span></td>
        <td>${row['专业权重说明'] || ''}</td>
        <td>${row['院校'] || ''}</td>
        <td>${row['专业'] || ''}</td>
        <td>${row['2025最低分'] || ''} / ${row['2025最低位次'] || ''}</td>
        <td>${row['相对63334位次差_排序用'] || ''}</td>
        <td>${row['三年最低位次均值'] || ''}</td>
        <td>${row['学费'] || row['学费文本'] || ''}</td>
        <td>${row['最新计划数'] || row['2025计划数'] || ''}</td>
        <td class="remarks-cell">${row['备注'] || row['排序建议'] || ''}</td>
      `;
      body.appendChild(tr);
    });
  }

  body.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const index = Number(button.dataset.index);
    if (button.dataset.action === 'up') moveRow(index, index - 1);
    if (button.dataset.action === 'down') moveRow(index, index + 1);
    if (button.dataset.action === 'delete') deleteRow(index);
  });

  body.addEventListener('dragstart', (event) => {
    const tr = event.target.closest('tr[data-id]');
    if (!tr) return;
    state.draggingId = tr.dataset.id;
    tr.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
  });

  body.addEventListener('dragend', (event) => {
    const tr = event.target.closest('tr[data-id]');
    if (tr) tr.classList.remove('dragging');
    state.draggingId = null;
  });

  body.addEventListener('dragover', (event) => {
    event.preventDefault();
    const target = event.target.closest('tr[data-id]');
    if (!target || !state.draggingId || target.dataset.id === state.draggingId) return;
    const from = state.rows.findIndex((row) => row.__id === state.draggingId);
    const to = state.rows.findIndex((row) => row.__id === target.dataset.id);
    if (from >= 0 && to >= 0) moveRow(from, to);
  });

  document.getElementById('export-volunteer').addEventListener('click', downloadCsv);

  document.getElementById('restore-deleted').addEventListener('click', () => {
    if (!state.deleted.length) return;
    state.rows.push(...state.deleted.splice(0));
    render();
  });

  document.getElementById('reset-order').addEventListener('click', () => {
    state.rows = state.initialRows.map((row) => ({ ...row }));
    state.deleted = [];
    render();
  });

  searchInput.addEventListener('input', () => {
    state.search = searchInput.value;
    render();
  });

  async function load() {
    try {
      const response = await fetch(`/api/volunteer-list?source=${encodeURIComponent(state.source)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '读取失败');
      state.columns = data.columns.filter((col) => col !== '__id');
      state.initialRows = data.items.map((row, index) => ({ ...row, __id: rowId(row, index) }));
      state.rows = state.initialRows.map((row) => ({ ...row }));
      errorBox.textContent = `数据来源：${data.path}`;
      render();
    } catch (error) {
      errorBox.textContent = error.message;
      summary.textContent = '读取失败。';
    }
  }

  load();
})();
