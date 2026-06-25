let majorOptions = [];
let selectedMajorKeywords = new Set();
let majorChatHistory = [];

const LLM_STORAGE_KEY = 'gaokao_llm_config';
const MAJOR_SELECTION_STORAGE_KEY = 'gaokao_selected_major_keywords';

initMajorPage();

function initMajorPage() {
  const params = new URLSearchParams(window.location.search);
  setValue('#major-category', params.get('category') || '理科');
  setValue('#major-level', params.get('education_level') || '本科');
  setValue('#major-goal', params.get('goal') || '就业优先');
  selectedMajorKeywords = readStoredMajorSelections();

  document.querySelector('#major-category').addEventListener('change', () => {
    loadMajorOptions();
    renderMajorAnalysis();
  });
  document.querySelector('#major-level').addEventListener('change', () => {
    loadMajorOptions();
    renderMajorAnalysis();
  });
  document.querySelector('#major-goal').addEventListener('change', () => {
    renderSelectedMajors();
    renderMajorAnalysis();
  });
  document.querySelector('#major-search-page').addEventListener('input', renderMajorOptions);
  document.querySelector('#clear-majors').addEventListener('click', () => {
    selectedMajorKeywords = new Set();
    saveStoredMajorSelections();
    renderSelectedMajors();
    renderMajorOptions();
  });
  document.querySelector('#major-chat-send').addEventListener('click', sendMajorChat);
  document.querySelector('#major-chat-input').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMajorChat();
    }
  });

  renderSelectedMajors();
  renderMajorAnalysis();
  loadMajorOptions();
}

async function loadMajorOptions() {
  const category = encodeURIComponent(document.querySelector('#major-category').value || '');
  const educationLevel = encodeURIComponent(document.querySelector('#major-level').value || '');
  const container = document.querySelector('#major-page-options');
  container.innerHTML = '<p class="muted">正在加载专业...</p>';
  document.querySelector('#major-count').textContent = '加载中';
  try {
    const res = await fetch(`/api/major-options?category=${category}&education_level=${educationLevel}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '专业列表加载失败');
    majorOptions = data.items || [];
    document.querySelector('#major-count').textContent = `${majorOptions.length} 个专业`;
    renderMajorOptions();
  } catch (error) {
    majorOptions = [];
    document.querySelector('#major-count').textContent = '加载失败';
    container.innerHTML = `<p class="warning-line">${escapeHtml(error.message)}</p>`;
  }
}

function renderMajorOptions() {
  const el = document.querySelector('#major-page-options');
  const keyword = (document.querySelector('#major-search-page').value || '').trim().toLowerCase();
  const filtered = majorOptions.filter(item => !keyword || String(item.name || '').toLowerCase().includes(keyword));
  if (!filtered.length) {
    el.innerHTML = '<p class="muted">没有匹配专业。可以清空搜索或切换科类/层次。</p>';
    return;
  }
  el.innerHTML = filtered.map(item => {
    const name = item.name || '';
    const selected = selectedMajorKeywords.has(name);
    return `
      <button type="button" class="major-row ${selected ? 'selected' : ''}" data-major="${escapeAttr(name)}">
        <span>${escapeHtml(name)}</span>
        <small>${escapeHtml(item.school_count || 0)} 校 · ${escapeHtml(item.records || 0)} 条历史记录</small>
      </button>
    `;
  }).join('');
  el.querySelectorAll('[data-major]').forEach(button => {
    button.addEventListener('click', () => {
      const name = button.dataset.major || '';
      if (selectedMajorKeywords.has(name)) selectedMajorKeywords.delete(name);
      else selectedMajorKeywords.add(name);
      saveStoredMajorSelections();
      renderSelectedMajors();
      renderMajorOptions();
    });
  });
}

function renderSelectedMajors() {
  const el = document.querySelector('#major-page-selected');
  const selected = Array.from(selectedMajorKeywords);
  el.innerHTML = selected.length ? selected.map(name => `
    <button type="button" class="major-chip selected" data-major-remove="${escapeAttr(name)}">${escapeHtml(name)}</button>
  `).join('') : '<span class="muted">还没有选择专业。建议先选 3-8 个可接受方向。</span>';
  el.querySelectorAll('[data-major-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedMajorKeywords.delete(button.dataset.majorRemove || '');
      saveStoredMajorSelections();
      renderSelectedMajors();
      renderMajorOptions();
    });
  });
}

async function sendMajorChat() {
  const input = document.querySelector('#major-chat-input');
  const button = document.querySelector('#major-chat-send');
  const message = input.value.trim();
  if (!message) return;
  majorChatHistory.push({role: 'user', content: message});
  input.value = '';
  renderChatMessages();
  button.disabled = true;
  button.textContent = '发送中...';
  try {
    const res = await fetch('/api/llm/major-chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message,
        history: majorChatHistory.slice(0, -1),
        llm_config: getLlmConfig(),
        context: buildMajorContext(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'AI 专业咨询失败');
    majorChatHistory.push({role: 'assistant', content: data.summary || '', mode: data.mode, model: data.model});
  } catch (error) {
    majorChatHistory.push({role: 'assistant', content: `AI 专业咨询失败：${error.message}`});
  } finally {
    button.disabled = false;
    button.textContent = '发送';
    renderChatMessages();
  }
}

function buildMajorContext() {
  return {
    profile: {
      category: document.querySelector('#major-category').value || '',
      education_level: document.querySelector('#major-level').value || '',
      goal: document.querySelector('#major-goal').value || '',
    },
    selected_majors: Array.from(selectedMajorKeywords),
    major_options: majorOptions,
  };
}

function renderChatMessages() {
  const el = document.querySelector('#major-chat-messages');
  el.innerHTML = majorChatHistory.length ? majorChatHistory.map(item => `
    <div class="chat-message ${item.role === 'assistant' ? 'assistant' : 'user'}">
      <strong>${item.role === 'assistant' ? 'AI' : '你'}</strong>
      <div>${escapeHtml(item.content || '').replaceAll('\n', '<br>')}</div>
    </div>
  `).join('') : '<p class="muted">可以问：物理类普通家庭就业优先怎么选？这些专业哪些适合考公？计算机和电子信息怎么取舍？</p>';
}

function renderMajorAnalysis() {
  const category = document.querySelector('#major-category').value || '';
  const level = document.querySelector('#major-level').value || '';
  const goal = document.querySelector('#major-goal').value || '';
  const isHistory = category.includes('文') || category.includes('历史');
  const isPhysics = category.includes('理') || category.includes('物理');
  const data = isHistory ? historyMajorAnalysis() : (isPhysics ? physicsMajorAnalysis() : combinedMajorAnalysis());
  document.querySelector('#major-analysis-scope').textContent = `${category || '全部科类'} · ${level || '不限层次'} · ${goal || '未指定目标'}`;
  document.querySelector('#major-analysis-content').innerHTML = `
    <div class="expert-grid">
      ${data.experts.map(item => `
        <article class="expert-card">
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.text)}</p>
        </article>
      `).join('')}
    </div>
    <div class="recommend-grid">
      ${renderRecommendColumn('优先关注', data.priority, 'priority')}
      ${renderRecommendColumn('谨慎选择', data.caution, 'caution')}
      ${renderRecommendColumn('除非强兴趣否则不优先', data.avoid, 'avoid')}
    </div>
    <section class="analysis-notes">
      <h3>落地选择方法</h3>
      <ul>
        ${data.notes.map(note => `<li>${escapeHtml(note)}</li>`).join('')}
      </ul>
    </section>
  `;
}

function renderRecommendColumn(title, rows, kind) {
  return `
    <section class="recommend-column ${kind}">
      <h3>${escapeHtml(title)}</h3>
      ${rows.map(item => `
        <article>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(item.reason)}</p>
        </article>
      `).join('')}
    </section>
  `;
}

function historyMajorAnalysis() {
  return {
    experts: [
      {title: '产业趋势专家', text: '未来5-10年，文科更有韧性的不是泛管理，而是规则、财务、公共服务、内容传播和医疗服务这些能和AI工具结合的方向。'},
      {title: '就业薪酬专家', text: '文科本科就业要看岗位入口是否清楚。财会审计、法务合规、教师、护理和新媒体运营比“听起来高级但岗位泛”的专业更可落地。'},
      {title: '考公考编专家', text: '岗位目录适配度很关键。法学、汉语言文学、会计审计、财务管理、思想政治教育、师范类通常更容易找到对应岗位。'},
      {title: '升学深造专家', text: '法学、心理、新闻传播、经济金融等方向读研后上限更高；如果家庭希望本科尽快就业，要谨慎选择强依赖深造的专业。'},
      {title: '家庭风险专家', text: '普通家庭优先低学费、公办、证书路径清晰的专业；高学费、中外合作、岗位高度依赖资源和城市平台的方向要降低优先级。'},
    ],
    priority: [
      {name: '法学/知识产权/纪检监察', reason: '合规、知识产权、基层治理和考公长期有需求，但要接受法考、考研或长期备考。'},
      {name: '汉语言文学/思想政治教育/小学教育', reason: '教师编、公务员文字岗、事业单位和内容岗位适配度高，稳定性强。'},
      {name: '会计学/审计学/财务管理/税收学', reason: '企业、国企、事务所、税务财政审计系统都有入口，AI会替代低端记账但不会替代合规判断。'},
      {name: '护理学/康复治疗/医学技术', reason: '老龄化带来长期需求，就业刚性较强，但要接受工作强度、资格证和职业环境。'},
      {name: '网络与新媒体/数字出版', reason: '内容运营、政务新媒体、品牌传播仍有需求，适合表达和执行强、愿意做作品集的人。'},
    ],
    caution: [
      {name: '金融学/经济学/国际经济与贸易', reason: '强校和一线城市更有优势，普通院校容易泛化为销售或基础岗位，需要叠加数据、财会或英语能力。'},
      {name: '英语/商务英语/翻译/小语种', reason: '单纯语言红利下降，必须复合法律、外贸、教育、跨境电商或技术文档方向。'},
      {name: '行政管理/公共事业管理/社会工作', reason: '适合考公考编，但市场化岗位不够硬，不能只因为名字像体制内就选。'},
      {name: '新闻学/广告学', reason: '传统岗位收缩，新媒体机会多但波动大，核心看实习、作品和数据运营能力。'},
    ],
    avoid: [
      {name: '电子商务', reason: '就业面宽但专业壁垒弱，容易流向运营、客服、销售，除非能叠加数据分析和供应链能力。'},
      {name: '旅游管理/酒店管理/会展', reason: '行业周期性和服务属性强，薪资稳定性一般，除非热爱且有明确城市资源。'},
      {name: '工商管理/市场营销/人力资源管理', reason: '入口宽但替代性强，更依赖个人能力、实习平台和销售/运营接受度。'},
      {name: '哲学/历史学非师范/文化产业管理', reason: '学术价值高，但本科直接就业较窄，通常要深造、考公或依赖兴趣长期投入。'},
    ],
    notes: [
      '就业优先时，先看本科毕业是否有明确岗位入口，再看是否需要考研或证书。',
      '文科不要只追“热门”，优先选岗位目录清晰、证书路径明确、能和AI工具结合的方向。',
      '建议先选 3-6 个方向：法学、汉语言/师范、财会审计、护理康复、新媒体中按性格和家庭预算取舍。',
    ],
  };
}

function physicsMajorAnalysis() {
  return {
    experts: [
      {title: '产业趋势专家', text: '理科未来5-10年主线是AI、先进制造、新能源、半导体、医疗健康和数字基础设施，核心是数学、工程和持续学习能力。'},
      {title: '就业薪酬专家', text: '计算机、电子信息、电气、自动化等方向薪酬弹性较高，但课程硬、竞争强；医学和电力方向确定性更强但周期更长。'},
      {title: '考公考编专家', text: '理科考公岗位不如文科集中，但计算机、电子信息、财会、统计、法学第二学位等方向仍有岗位；电力和医学更偏行业就业。'},
      {title: '升学深造专家', text: '生物、材料、环境、基础理学等前沿方向本科就业分化大，读研后平台和方向更重要。'},
      {title: '家庭风险专家', text: '普通家庭要权衡培养周期和试错成本。高学费中外合作、强读研依赖、小计划冷门专业不宜作为主线。'},
    ],
    priority: [
      {name: '计算机科学与技术/软件工程/数据科学', reason: 'AI时代不是过时，而是门槛升高。适合数学和自学能力强、能持续做项目的人。'},
      {name: '电子信息/通信工程/集成电路/微电子', reason: '智能硬件、车企、通信、半导体长期需要工程人才，专业壁垒高。'},
      {name: '电气工程及其自动化/自动化', reason: '电网、新能源、电力设备、工业控制需求稳定，适合追求确定性的家庭。'},
      {name: '临床医学/口腔医学/医学影像', reason: '医疗刚需和老龄化支撑长期需求，但培养周期长，读研规培成本高。'},
      {name: '机械/车辆/智能制造/机器人工程', reason: '传统名称不等于没前景，关键看是否叠加自动化、新能源车和工业软件。'},
    ],
    caution: [
      {name: '土木工程/建筑类', reason: '地产基建周期变化后分化明显，除非学校平台、地区机会或细分方向明确。'},
      {name: '生物科学/生物工程/药学', reason: '行业前沿但本科岗位有限，通常需要读研或进入医药产业链细分岗位。'},
      {name: '材料类/环境工程', reason: '国家需要但本科薪资和岗位分化大，更适合能接受深造的人。'},
      {name: '金融工程/经济统计', reason: '数学和编程要求高，就业更看学校层次、城市和实习。'},
    ],
    avoid: [
      {name: '信息管理/管理科学等泛交叉', reason: '名字像技术，但岗位常不如计算机、电子信息明确。'},
      {name: '小计划新工科概念专业', reason: '名称新不等于就业好，要看课程、师资、行业入口和近年计划数。'},
      {name: '高学费中外合作但路径不清的专业', reason: '投入高，若不能带来学校层次、语言和升学优势，性价比不稳。'},
      {name: '自己明显排斥数学物理的硬工科', reason: '专业前景再好，也需要学习适配，否则大学阶段和就业都会吃力。'},
    ],
    notes: [
      '理科选专业先判断数学、物理、编程和实验动手能力能不能接住。',
      '就业优先时，计算机、电子信息、电气、自动化、医学是主线，但要结合分数位次和城市产业。',
      '建议先选 3-8 个方向，再用2026计划数、学费、院校层次和保底厚度筛。'
    ],
  };
}

function combinedMajorAnalysis() {
  return {
    experts: [
      {title: '综合判断', text: '先选科类再选专业，否则文理方向的岗位入口和学习要求差异太大。'},
      {title: '就业导向', text: '不管文理，都优先选择岗位入口清楚、技能可迁移、证书或项目路径明确的专业。'},
      {title: '风险控制', text: '避免只看热门名称。学费、城市、是否读研、是否考公、家庭资源都要一起判断。'},
    ],
    priority: [
      {name: '文科主线', reason: '法学、汉语言/师范、财会审计、护理康复、新媒体。'},
      {name: '理科主线', reason: '计算机、电子信息、电气、自动化、医学、智能制造。'},
    ],
    caution: [
      {name: '泛管理、泛经济、泛交叉', reason: '需要结合学校层次和个人能力，不能只看名字。'},
    ],
    avoid: [
      {name: '岗位入口不清的高学费专业', reason: '投入高且不确定性大，应放到备选。'},
    ],
    notes: ['建议先切换到文科或理科大类，再看更具体的专业建议。'],
  };
}

function readStoredMajorSelections() {
  try {
    const items = JSON.parse(localStorage.getItem(MAJOR_SELECTION_STORAGE_KEY) || '[]');
    return new Set(Array.isArray(items) ? items.filter(Boolean) : []);
  } catch {
    return new Set();
  }
}

function saveStoredMajorSelections() {
  localStorage.setItem(MAJOR_SELECTION_STORAGE_KEY, JSON.stringify(Array.from(selectedMajorKeywords)));
}

function getLlmConfig() {
  try {
    return JSON.parse(localStorage.getItem(LLM_STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function setValue(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.value = value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function escapeAttr(value) {
  return escapeHtml(value);
}
