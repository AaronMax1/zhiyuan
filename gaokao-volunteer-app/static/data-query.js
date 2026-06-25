const form = document.querySelector('#data-query-form');
const table = document.querySelector('#query-table');
const summary = document.querySelector('#query-summary');
const notes = document.querySelector('#query-notes');
const pageLabel = document.querySelector('#page-label');
const prevButton = document.querySelector('#prev-page');
const nextButton = document.querySelector('#next-page');
const resetButton = document.querySelector('#reset-query');
const downloadButton = document.querySelector('#download-query');
const provinceOptionsEl = document.querySelector('#query-province-options');
const selectedProvincesEl = document.querySelector('#query-selected-provinces');
const cityOptionsEl = document.querySelector('#query-city-options');
const selectedCitiesEl = document.querySelector('#query-selected-cities');
const majorOptionsEl = document.querySelector('#query-major-options');
const selectedMajorsEl = document.querySelector('#query-selected-majors');

let currentPage = 1;
let currentTotal = 0;
let currentPageSize = 30;
let currentSource = 'history';
const selectedCities = new Set();
const selectedProvinces = new Set(['河北']);
const selectedMajors = new Set();

const citiesByProvince = {
  河北: ['石家庄', '保定', '廊坊', '唐山', '秦皇岛', '邯郸', '邢台', '沧州', '衡水', '张家口', '承德'],
  北京: ['北京'],
  天津: ['天津'],
  山东: ['济南', '青岛', '烟台', '潍坊', '临沂', '济宁', '淄博', '泰安', '威海', '日照'],
  河南: ['郑州', '洛阳', '开封', '新乡', '焦作', '安阳', '南阳', '信阳', '商丘'],
  山西: ['太原', '大同', '临汾', '运城', '长治', '晋中'],
  内蒙古: ['呼和浩特', '包头', '赤峰', '通辽', '鄂尔多斯'],
  辽宁: ['沈阳', '大连', '锦州', '鞍山', '抚顺'],
  吉林: ['长春', '吉林', '延边', '四平'],
  黑龙江: ['哈尔滨', '齐齐哈尔', '牡丹江', '大庆'],
  江苏: ['南京', '苏州', '无锡', '常州', '徐州', '南通', '扬州'],
  浙江: ['杭州', '宁波', '温州', '绍兴', '金华', '嘉兴'],
  安徽: ['合肥', '芜湖', '蚌埠', '马鞍山', '安庆'],
  江西: ['南昌', '赣州', '九江', '景德镇'],
  湖北: ['武汉', '宜昌', '襄阳', '荆州'],
  湖南: ['长沙', '湘潭', '衡阳', '株洲'],
  广东: ['广州', '深圳', '珠海', '佛山', '东莞', '汕头'],
  广西: ['南宁', '桂林', '柳州', '北海'],
  海南: ['海口', '三亚'],
  重庆: ['重庆'],
  四川: ['成都', '绵阳', '德阳', '南充', '宜宾'],
  贵州: ['贵阳', '遵义', '安顺'],
  云南: ['昆明', '大理', '曲靖'],
  陕西: ['西安', '咸阳', '宝鸡', '延安'],
  甘肃: ['兰州', '天水', '酒泉'],
  宁夏: ['银川', '石嘴山', '吴忠'],
  新疆: ['乌鲁木齐', '伊犁', '喀什', '石河子'],
};

const popularMajors = [
  '会计学', '财务管理', '审计学', '汉语言文学', '法学', '小学教育', '学前教育',
  '护理学', '市场营销', '电子商务', '英语', '人力资源管理', '行政管理',
  '计算机科学与技术', '软件工程', '数据科学与大数据技术', '电气工程及其自动化',
  '电子信息工程', '临床医学', '口腔医学', '中医学',
];

const historySortOptions = [
  ['rank', '最低位次'],
  ['line_diff', '线差'],
  ['min_score', '最低分'],
  ['avg_score', '平均分'],
  ['tuition', '学费'],
  ['plan_count', '计划数'],
  ['year', '年度'],
];

const planSortOptions = [
  ['school', '院校'],
  ['tuition', '学费'],
  ['plan_count', '计划数'],
  ['duration', '学制'],
];

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function buildParams(page) {
  const fd = new FormData(form);
  const params = new URLSearchParams();
  for (const [key, value] of fd.entries()) {
    const text = String(value || '').trim();
    if (key === 'city_text' || key === 'major_text') continue;
    if (text) params.set(key, text);
  }
  if (selectedCities.size) {
    for (const city of selectedCities) params.append('city', city);
  } else {
    for (const province of selectedProvinces) params.append('province', province);
  }
  for (const major of selectedMajors) params.append('major', major);
  const cityText = String(form.elements.city_text?.value || '').trim();
  const majorText = String(form.elements.major_text?.value || '').trim();
  if (cityText) params.append('city', cityText);
  if (majorText) params.append('major', majorText);
  params.set('page', String(page));
  if (!params.has('page_size')) params.set('page_size', '30');
  if (params.get('source') === 'plan') {
    params.delete('year');
    params.delete('score_min');
    params.delete('score_max');
    params.delete('rank_min');
    params.delete('rank_max');
    params.delete('line_filter');
    params.delete('line_delta');
  }
  return params;
}

function getProvinceCities() {
  return selectedProvinces.size
    ? Array.from(new Set(Array.from(selectedProvinces).flatMap(province => citiesByProvince[province] || [])))
    : [];
}

async function runQuery(page = 1) {
  currentPage = page;
  const params = buildParams(page);
  currentSource = params.get('source') || 'history';
  currentPageSize = Number(params.get('page_size') || 30);
  summary.textContent = '查询中...';
  table.querySelector('thead').innerHTML = '';
  table.querySelector('tbody').innerHTML = '';
  try {
    const res = await fetch(`/api/data-query?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '查询失败');
    currentTotal = Number(data.total || 0);
    renderNotes(data.data_notes || []);
    renderTable(data.items || [], data.source || currentSource);
    renderPager();
    summary.textContent = `共 ${currentTotal} 条，当前第 ${currentPage} 页，每页 ${currentPageSize} 条。`;
  } catch (err) {
    currentTotal = 0;
    renderPager();
    summary.textContent = err.message || String(err);
    notes.innerHTML = '';
  }
}

function renderNotes(items) {
  notes.innerHTML = items.length
    ? items.map(item => `<span>${escapeHtml(item)}</span>`).join('')
    : '';
}

function renderTable(items, source) {
  const head = table.querySelector('thead');
  const body = table.querySelector('tbody');
  if (source === 'plan') {
    head.innerHTML = `<tr>
      <th>年度</th><th>批次</th><th>科类</th>${sortHeader('院校', 'school')}<th>评价</th><th>专业</th>
      ${sortHeader('计划', 'plan_count')}${sortHeader('学制', 'duration')}${sortHeader('学费', 'tuition')}<th>选科要求</th><th>备注</th>
    </tr>`;
    body.innerHTML = items.length ? items.map(row => `
      <tr>
        <td>${escapeHtml(row.year)}</td>
        <td>${escapeHtml(row.batch_name)}</td>
        <td>${escapeHtml(row.category_name)}</td>
        <td class="school-cell">${escapeHtml(row.school_name)}<small>${escapeHtml(row.school_code)}</small></td>
        <td>${schoolLifeLink(row)}</td>
        <td class="major-cell">${escapeHtml(row.major_name)}<small>${escapeHtml(row.major_code)}</small></td>
        <td>${escapeHtml(row.plan_count ?? '-')}</td>
        <td>${escapeHtml(row.duration || '-')}</td>
        <td>${formatTuition(row)}</td>
        <td>${escapeHtml(row.subject_requirement || '-')}</td>
        <td>${escapeHtml(row.remarks || '-')}</td>
      </tr>
    `).join('') : '<tr><td colspan="11">没有符合条件的数据。</td></tr>';
    bindSortHeaders();
    return;
  }
  head.innerHTML = `<tr>
    ${sortHeader('年度', 'year')}<th>批次</th><th>科类</th><th>院校</th><th>评价</th><th>专业</th>
    ${sortHeader('最低分', 'min_score')}${sortHeader('平均分', 'avg_score')}${sortHeader('最低位次', 'rank')}${sortHeader('线差', 'line_diff')}
    ${sortHeader('计划', 'plan_count')}<th>学制</th>${sortHeader('学费', 'tuition')}<th>选科要求</th><th>备注</th>
  </tr>`;
  body.innerHTML = items.length ? items.map(row => `
    <tr>
      <td>${escapeHtml(row.year)}</td>
      <td>${escapeHtml(row.batch_name)}</td>
      <td>${escapeHtml(row.category_name)}</td>
      <td class="school-cell">${escapeHtml(row.school_name)}<small>${escapeHtml(row.school_code)}</small></td>
      <td>${schoolLifeLink(row)}</td>
      <td class="major-cell">${escapeHtml(row.major_name)}<small>${escapeHtml(row.major_code)}</small></td>
      <td>${escapeHtml(row.min_score ?? '-')}</td>
      <td>${escapeHtml(row.avg_score ?? '-')}</td>
      <td>${escapeHtml(row.min_rank ?? '-')}</td>
      <td>${formatLineDiff(row)}</td>
      <td>${escapeHtml(row.plan_count ?? '-')}</td>
      <td>${escapeHtml(row.duration || '-')}</td>
      <td>${formatTuition(row)}</td>
      <td>${escapeHtml(row.subject_requirement || '-')}</td>
      <td>${escapeHtml(row.remarks || '-')}</td>
    </tr>
  `).join('') : '<tr><td colspan="15">没有符合条件的数据。</td></tr>';
  bindSortHeaders();
}

function schoolLifeLink(row) {
  const url = row.school_life_url || 'https://cn.colleges.chat/universities/';
  return `<a class="review-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">看评价</a>`;
}

function sortHeader(label, key) {
  const currentKey = form.elements.sort.value;
  const currentDir = form.elements.dir.value;
  const marker = currentKey === key ? (currentDir === 'asc' ? '↑' : '↓') : '';
  return `<th><button type="button" class="sort-head" data-sort="${escapeHtml(key)}">${escapeHtml(label)} ${marker}</button></th>`;
}

function bindSortHeaders() {
  table.querySelectorAll('[data-sort]').forEach(button => {
    button.addEventListener('click', () => {
      const key = button.dataset.sort || '';
      if (form.elements.sort.value === key) {
        form.elements.dir.value = form.elements.dir.value === 'asc' ? 'desc' : 'asc';
      } else {
        form.elements.sort.value = key;
        form.elements.dir.value = ['rank', 'tuition', 'line_diff', 'plan_count', 'school'].includes(key) ? 'asc' : 'desc';
      }
      runQuery(1);
    });
  });
}

function formatTuition(row) {
  if (row.tuition_text) return `${escapeHtml(row.tuition_text)} 元/年`;
  if (row.tuition) return `${escapeHtml(row.tuition)} 元/年`;
  return '-';
}

function formatLineDiff(row) {
  if (row.line_diff === null || row.line_diff === undefined) return '-';
  const sign = Number(row.line_diff) > 0 ? '+' : '';
  return `${sign}${escapeHtml(row.line_diff)}<small>线 ${escapeHtml(row.control_line ?? '-')}</small>`;
}

function renderPager() {
  const totalPages = Math.max(1, Math.ceil(currentTotal / currentPageSize));
  pageLabel.textContent = `第 ${currentPage} / ${totalPages} 页`;
  prevButton.disabled = currentPage <= 1;
  nextButton.disabled = currentPage >= totalPages || currentTotal === 0;
  downloadButton.disabled = currentTotal === 0;
}

form.addEventListener('submit', event => {
  event.preventDefault();
  runQuery(1);
});

prevButton.addEventListener('click', () => {
  if (currentPage > 1) runQuery(currentPage - 1);
});

nextButton.addEventListener('click', () => {
  const totalPages = Math.max(1, Math.ceil(currentTotal / currentPageSize));
  if (currentPage < totalPages) runQuery(currentPage + 1);
});

downloadButton.addEventListener('click', () => {
  const params = buildParams(1);
  params.set('export', 'csv');
  params.delete('page');
  params.delete('page_size');
  window.location.href = `/api/data-query?${params.toString()}`;
});

resetButton.addEventListener('click', () => {
  form.reset();
  selectedCities.clear();
  selectedProvinces.clear();
  selectedProvinces.add('河北');
  selectedMajors.clear();
  renderFilterChips();
  currentPage = 1;
  runQuery(1);
});

function renderFilterChips() {
  const provinceOptions = Object.keys(citiesByProvince);
  const cityOptions = getProvinceCities();
  pruneSelectedCities(cityOptions);
  provinceOptionsEl.innerHTML = provinceOptions.map(province => chipButton(province, selectedProvinces.has(province), 'province-option')).join('');
  selectedProvincesEl.innerHTML = renderSelectedChips(selectedProvinces, 'province-remove', '已选省份');
  cityOptionsEl.innerHTML = cityOptions.map(city => chipButton(city, selectedCities.has(city), 'city-option')).join('');
  majorOptionsEl.innerHTML = popularMajors.map(major => chipButton(major, selectedMajors.has(major), 'major-option')).join('');
  selectedCitiesEl.innerHTML = renderSelectedCities();
  selectedMajorsEl.innerHTML = renderSelectedChips(selectedMajors, 'major-remove', '已选专业');

  provinceOptionsEl.querySelectorAll('[data-province-option]').forEach(button => {
    button.addEventListener('click', () => toggleSetValue(selectedProvinces, button.dataset.provinceOption || ''));
  });
  cityOptionsEl.querySelectorAll('[data-city-option]').forEach(button => {
    button.addEventListener('click', () => toggleSetValue(selectedCities, button.dataset.cityOption || ''));
  });
  majorOptionsEl.querySelectorAll('[data-major-option]').forEach(button => {
    button.addEventListener('click', () => toggleSetValue(selectedMajors, button.dataset.majorOption || ''));
  });
  selectedCitiesEl.querySelectorAll('[data-city-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedCities.delete(button.dataset.cityRemove || '');
      renderFilterChips();
    });
  });
  selectedProvincesEl.querySelectorAll('[data-province-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedProvinces.delete(button.dataset.provinceRemove || '');
      renderFilterChips();
    });
  });
  selectedMajorsEl.querySelectorAll('[data-major-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedMajors.delete(button.dataset.majorRemove || '');
      renderFilterChips();
    });
  });
}

function renderSelectedCities() {
  if (selectedCities.size) return renderSelectedChips(selectedCities, 'city-remove', '已选城市');
  if (selectedProvinces.size) {
    return `<span class="query-chip-empty">已选城市：按已选省份全部院校筛选</span>`;
  }
  return '<span class="query-chip-empty">已选城市：不限</span>';
}

function pruneSelectedCities(cityOptions) {
  const allowed = new Set(cityOptions);
  for (const city of Array.from(selectedCities)) {
    if (!allowed.has(city)) selectedCities.delete(city);
  }
}

function chipButton(value, selected, key) {
  return `
    <button type="button" class="major-chip ${selected ? 'selected' : ''}" data-${key}="${escapeHtml(value)}" aria-pressed="${selected ? 'true' : 'false'}">
      ${escapeHtml(value)}
    </button>
  `;
}

function renderSelectedChips(values, key, label) {
  const items = Array.from(values);
  if (!items.length) return `<span class="query-chip-empty">${escapeHtml(label)}：不限</span>`;
  return items.map(value => `
    <button type="button" class="major-chip selected" data-${key}="${escapeHtml(value)}">${escapeHtml(value)} ×</button>
  `).join('');
}

function toggleSetValue(set, value) {
  if (!value) return;
  if (set.has(value)) {
    set.delete(value);
  } else {
    set.add(value);
  }
  renderFilterChips();
}

function bindAddOnEnter(inputName, set) {
  const input = form.elements[inputName];
  input.addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    const value = String(input.value || '').trim();
    if (!value) return;
    event.preventDefault();
    set.add(value);
    input.value = '';
    renderFilterChips();
    runQuery(1);
  });
}

function updateSourceControls() {
  const plan = form.elements.source.value === 'plan';
  for (const name of ['year', 'score_min', 'score_max', 'rank_min', 'rank_max', 'line_filter', 'line_delta']) {
    form.elements[name].disabled = plan;
  }
  form.elements.current_plan_only.disabled = plan;
  const select = form.elements.sort;
  const options = plan ? planSortOptions : historySortOptions;
  const oldValue = select.value;
  select.innerHTML = options.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
  select.value = options.some(([value]) => value === oldValue) ? oldValue : options[0][0];
}

form.elements.source.addEventListener('change', updateSourceControls);

bindAddOnEnter('city_text', selectedCities);
bindAddOnEnter('major_text', selectedMajors);
renderFilterChips();
updateSourceControls();
runQuery(1);
