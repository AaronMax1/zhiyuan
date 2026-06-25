let engineReady = false;
let currentPlan = null;
let activeStepIndex = 0;
let manualCompletedSteps = new Set();
let fullPoolPage = 1;
let majorOptions = [];
let selectedMajorKeywords = new Set();
let selectedPreferredCities = new Set();
const LLM_STORAGE_KEY = 'gaokao_llm_config';
const MAJOR_SELECTION_STORAGE_KEY = 'gaokao_selected_major_keywords';
const CITY_SELECTION_STORAGE_KEY = 'gaokao_selected_preferred_cities';
const FULL_POOL_PAGE_SIZE = 20;
const COMMON_TARGET_CITIES = [
  '不限城市',
  '北京',
  '天津',
  '石家庄',
  '保定',
  '唐山',
  '秦皇岛',
  '廊坊',
  '邯郸',
  '济南',
  '青岛',
  '郑州',
  '太原',
  '西安',
  '南京',
  '苏州',
  '杭州',
  '上海',
  '武汉',
  '长沙',
  '成都',
  '重庆',
  '广州',
  '深圳',
];

const PROVINCE_IDS = {
  北京: 11,
  天津: 12,
  河北: 13,
  山西: 14,
  内蒙古: 15,
  辽宁: 21,
  吉林: 22,
  黑龙江: 23,
  上海: 31,
  江苏: 32,
  浙江: 33,
  安徽: 34,
  福建: 35,
  江西: 36,
  山东: 37,
  河南: 41,
  湖北: 42,
  湖南: 43,
  广东: 44,
  广西: 45,
  海南: 46,
  重庆: 50,
  四川: 51,
  贵州: 52,
  云南: 53,
  西藏: 54,
  陕西: 61,
  甘肃: 62,
  青海: 63,
  宁夏: 64,
  新疆: 65,
};

async function checkHealth() {
  const el = document.querySelector('#health');
  const button = document.querySelector('button[type="submit"]');

  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    engineReady = Boolean(data.engine_ready);

    if (engineReady) {
      el.className = 'status ok';
      const primary = data.primary_data_source || {};
      const coverage = primary.coverage || {};
      const planCoverage = primary.plan_coverage || {};
      const advisor = data.optional_engines?.gaokao_advisor || {};
      const llm = data.llm_advisor || {};
      const warnings = data.quality_warnings || [];
      el.innerHTML = `
        <strong>主数据源：${escapeHtml(primary.name || '未知')}</strong>
        <span>${primary.ready ? '可用' : '不可用'} · ${escapeHtml(primary.message || '')}</span>
        <span>样本：${coverage.record_count || 0} 条 · 年份：${coverage.year_min || '-'}-${coverage.year_max || '-'} · 范围：河北考生</span>
        <span>2026招生计划：${planCoverage.ready ? `${planCoverage.official_count || planCoverage.record_count || 0} 条官方计划` : '未接入'}</span>
        <span>高级引擎：${advisor.ready ? '可用' : '未接入 data/gaokao.db'}</span>
        <span>AI顾问：${llm.ready ? `可用 · ${escapeHtml(llm.model || '')}` : '未配置，使用规则总结'}</span>
        ${warnings.map(w => `<span class="warning">${escapeHtml(w)}</span>`).join('')}
      `;
      button.disabled = false;
      return;
    }

    el.className = 'status warn';
    el.innerHTML = `
      <strong>推荐引擎未就绪</strong>
      <span>缺少河北专项录取库：<code>data-pipeline/output/hebei_lnwc_loggedin.db</code></span>
      <span>河北专项库检测路径：${escapeHtml(data.hebei_lnwc_db_path || '')}</span>
      <span>备用 gz 路径：${escapeHtml(data.primary_db ? data.primary_db.gz_path : '')}</span>
    `;
    showDetailMessage('当前页面和接口正常，但还没有可用于推荐的录取数据库。');
    button.disabled = true;
  } catch (error) {
    engineReady = false;
    el.className = 'status error';
    el.textContent = `无法连接后端服务：${error.message}`;
    button.disabled = true;
    showDetailMessage('后端服务不可用，请确认 python3 app.py 是否正在运行。');
  }
}

function splitInput(value) {
  return value.split(',').map(x => x.trim()).filter(Boolean);
}

document.querySelector('#form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!engineReady) {
    await checkHealth();
    return;
  }
  const form = new FormData(event.currentTarget);
  const province = form.get('province');
  const payload = {
    province,
    province_id: PROVINCE_IDS[province] || 0,
    education_level: form.get('education_level'),
    category: form.get('category'),
    score: Number(form.get('score')),
    rank: Number(form.get('rank') || 0),
    goal: form.get('goal'),
    major_keywords: Array.from(selectedMajorKeywords),
    preferred_cities: Array.from(selectedPreferredCities),
    family: form.get('family') || '',
    constraints: form.get('constraints') || '',
    max_slots: Number(form.get('max_slots') || 30),
    engine_mode: form.get('engine_mode') || 'hebei',
  };

  const steps = document.querySelector('#steps');
  const stepDetail = document.querySelector('#step-detail');
  steps.innerHTML = '';
  showDetailMessage('生成中...');

  const res = await fetch('/api/recommend/plan', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    showDetailMessage(humanizeError(data));
    return;
  }

  renderPlan(data);
});

function renderPlan(data) {
  currentPlan = data;
  manualCompletedSteps = new Set();
  fullPoolPage = 1;
  activeStepIndex = firstAvailableStep(data.steps || []);
  renderStepTabs();
  renderActiveStep();
  autoRunCandidateFilter();
}

function showDetailMessage(message) {
  document.querySelector('#step-detail').innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
}

function firstAvailableStep(steps) {
  const blockedIndex = steps.findIndex(step => step.status === 'blocked');
  if (blockedIndex >= 0 && blockedIndex < 2) {
    return blockedIndex;
  }
  return steps.length > 2 && steps[2].status !== 'locked' ? 2 : 0;
}

function renderStepTabs() {
  const stepsEl = document.querySelector('#steps');
  const steps = currentPlan?.steps || [];
  stepsEl.innerHTML = steps.map((step, index) => {
    const disabled = !isStepAccessible(index);
    const status = effectiveStepStatus(step, index);
    return `
      <button type="button" class="step-tab ${escapeHtml(status)} ${index === activeStepIndex ? 'active' : ''}"
        data-step-index="${index}" ${disabled ? 'disabled' : ''}>
        <span class="step-no">${index + 1}</span>
        <span class="step-name">${escapeHtml(step.title || step.id || '')}</span>
        <span class="step-status">${statusLabel(status)}</span>
      </button>
    `;
  }).join('');

  stepsEl.querySelectorAll('.step-tab').forEach(button => {
    button.addEventListener('click', () => {
      activeStepIndex = Number(button.dataset.stepIndex || 0);
      renderStepTabs();
      renderActiveStep();
    });
  });
}

function renderActiveStep() {
  const detail = document.querySelector('#step-detail');
  const steps = currentPlan?.steps || [];
  const step = steps[activeStepIndex];
  if (!step) {
    detail.innerHTML = '';
    return;
  }
  const renderers = [
    renderRankStep,
    renderEquivalentStep,
    renderCandidateStep,
    renderStrategyStep,
    renderOrderStep,
    renderCharterStep,
  ];
  const status = effectiveStepStatus(step, activeStepIndex);
  detail.innerHTML = `
    ${renderPlanBlockingAlert()}
    <div class="step-heading">
      <div>
        <h3>${escapeHtml(step.title || step.id || '')}</h3>
        <p>${escapeHtml(step.summary || '')}</p>
      </div>
      <span class="badge ${escapeHtml(status)}">${statusLabel(status)}</span>
    </div>
    ${renderStepLlmPanel(activeStepIndex)}
    ${step.blocking_reason ? `<p class="warning-line">${escapeHtml(step.blocking_reason)}</p>` : ''}
    ${renderStepMeta(step, activeStepIndex)}
    ${renderers[activeStepIndex] ? renderers[activeStepIndex](currentPlan) : ''}
    ${renderManualStepActions(activeStepIndex)}
  `;
  bindStepLlmButton();
  bindStepChat();
  bindFullPoolPager();
  bindCompleteStepButton();
}

function renderPlanBlockingAlert() {
  const check = currentPlan?.score_rank_check || {};
  if (!check.blocking) return '';
  return `
    <div class="critical-alert">
      <strong>报考层次需要先确认</strong>
      <span>${escapeHtml(check.message || '当前输入与所选批次存在硬矛盾，请核对后重新生成方案。')}</span>
      <span>在确认前，第 2 步不能完成，后续筛院校、冲稳保、排序和章程核验都会锁定。</span>
    </div>
  `;
}

function isStepAccessible(index) {
  const steps = currentPlan?.steps || [];
  const step = steps[index];
  if (!step || step.status === 'locked') return false;
  if (index <= 1) return true;
  if (index === 2) return steps[1]?.status !== 'blocked';
  return manualCompletedSteps.has(index - 1);
}

function effectiveStepStatus(step, index) {
  if (!isStepAccessible(index)) return 'locked';
  if (index <= 1) return step.status || 'done';
  if (manualCompletedSteps.has(index)) return 'done';
  if (step.status === 'blocked' || step.status === 'empty' || step.status === 'missing') return step.status;
  return 'pending_manual';
}

const LLM_STEP_MAP = {
  2: {key: 'candidate_pool', label: 'AI 确认专业与院校范围'},
  3: {key: 'strategy', label: 'AI 确定冲稳保策略'},
  4: {key: 'order', label: 'AI 排序志愿'},
  5: {key: 'charter', label: 'AI 分析招生章程'},
};

function renderStepLlmPanel(stepIndex) {
  const meta = LLM_STEP_MAP[stepIndex];
  if (!meta) return '';
  const analysis = currentPlan?.llm_step_analyses?.[meta.key];
  const isLocalCandidateFallback = meta.key === 'candidate_pool' && currentPlan?.ai_candidate_filter?.status === 'fallback';
  const label = analysis?.mode === 'llm'
    ? `AI · ${analysis.model || '已配置'}`
    : (isLocalCandidateFallback ? '本地规则筛选' : (analysis ? '规则分析' : '未调用'));
  return `
    <section class="advisor-panel">
      <div class="panel-title">
        <h4>${escapeHtml(meta.label)}</h4>
        <span>${escapeHtml(label)}</span>
      </div>
      <button type="button" class="llm-step-button" data-llm-step="${escapeHtml(meta.key)}">
        ${analysis ? '重新调用 AI 分析' : '调用 AI 分析'}
      </button>
      ${renderLlmNotice(analysis, meta.key)}
      ${analysis?.summary ? `<div class="advisor-text">${escapeHtml(analysis.summary).replaceAll('\n', '<br>')}</div>` : '<p class="muted">点击按钮后才会调用 AI；生成方案本身不触发 AI。</p>'}
      ${renderStepChat(meta.key)}
    </section>
  `;
}

function renderStepChat(stepKey) {
  const messages = currentPlan?.llm_step_chats?.[stepKey] || [];
  return `
    <div class="step-chat" data-chat-step="${escapeHtml(stepKey)}">
      <div class="chat-title">
        <strong>继续和 AI 沟通</strong>
        <span>围绕本步候选、风险和思路追问</span>
      </div>
      <div class="chat-messages">
        ${messages.length ? messages.map(renderChatMessage).join('') : '<p class="muted">可以追问：这些候选保底够不够？哪些专业更适合就业？哪些需要先查章程？</p>'}
      </div>
      <div class="chat-input-row">
        <input class="chat-input" data-chat-input="${escapeHtml(stepKey)}" placeholder="输入你想继续问 AI 的问题">
        <button type="button" class="chat-send" data-chat-send="${escapeHtml(stepKey)}">发送</button>
      </div>
    </div>
  `;
}

function renderChatMessage(item) {
  const role = item.role === 'assistant' ? 'assistant' : 'user';
  const label = role === 'assistant' ? 'AI' : '你';
  return `
    <div class="chat-message ${role}">
      <strong>${label}</strong>
      <div>${escapeHtml(item.content || '').replaceAll('\n', '<br>')}</div>
    </div>
  `;
}

function renderLlmNotice(analysis, stepKey = '') {
  if (!analysis?.error) return '';
  if (stepKey === 'candidate_pool' && currentPlan?.ai_candidate_filter?.status === 'fallback') return '';
  return '<p class="llm-notice">AI 服务本次不可用，当前先使用本地规则结果。可以稍后重试或检查页面里的 AI 配置。</p>';
}

function bindStepLlmButton() {
  const button = document.querySelector('.llm-step-button');
  if (!button) return;
  button.addEventListener('click', async () => {
    const step = button.dataset.llmStep;
    button.disabled = true;
    button.textContent = 'AI 分析中...';
    try {
      const res = await fetch('/api/llm/step', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          step,
          plan: currentPlan,
          llm_config: getLlmConfig(),
          filter_context: step === 'candidate_pool' ? candidateChatContext() : '',
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'AI 调用失败');
      currentPlan.llm_step_analyses = currentPlan.llm_step_analyses || {};
      currentPlan.llm_step_analyses[step] = data;
      if (step === 'candidate_pool') applyAiCandidateFilter(data);
    } catch (error) {
      currentPlan.llm_step_analyses = currentPlan.llm_step_analyses || {};
      currentPlan.llm_step_analyses[step] = {
        mode: 'frontend_error',
        step,
        error: error.message,
        summary: 'AI 调用失败，请检查页面里的 Base URL、Model、Key 和网络状态。',
      };
    } finally {
      renderActiveStep();
    }
  });
}

async function autoRunCandidateFilter() {
  if (!currentPlan || currentPlan.score_rank_check?.blocking) return;
  const config = getLlmConfig();
  if (!config.api_key || !config.base_url || !config.model) {
    setLocalCandidateFilterFallback('未配置 AI，已使用本地规则筛选结果。');
    renderActiveStep();
    return;
  }
  currentPlan.ai_candidate_filter = {
    status: 'running',
    source: 'ai',
    message: '正在自动调用 AI 筛选完整候选池...',
  };
  renderActiveStep();
  await runCandidateFilter('auto');
}

async function runCandidateFilter(source = 'manual') {
  try {
    const res = await fetch('/api/llm/step', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        step: 'candidate_pool',
        plan: currentPlan,
        llm_config: getLlmConfig(),
        filter_context: candidateChatContext(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'AI 筛选失败');
    currentPlan.llm_step_analyses = currentPlan.llm_step_analyses || {};
    currentPlan.llm_step_analyses.candidate_pool = data;
    if (!applyAiCandidateFilter(data, source)) {
      setLocalCandidateFilterFallback(data.error ? `AI 不可用，已回退本地规则筛选：${data.error}` : 'AI 未返回可用筛选结果，已回退本地规则筛选。');
    }
  } catch (error) {
    currentPlan.llm_step_analyses = currentPlan.llm_step_analyses || {};
    currentPlan.llm_step_analyses.candidate_pool = {
      mode: 'frontend_error',
      step: 'candidate_pool',
      error: error.message,
      summary: 'AI 筛选失败，已使用本地规则筛选结果。',
    };
    setLocalCandidateFilterFallback(`AI 筛选失败，已回退本地规则筛选：${error.message}`);
  } finally {
    renderActiveStep();
  }
}

function setLocalCandidateFilterFallback(message) {
  const analysis = currentPlan?.llm_step_analyses?.candidate_pool || {};
  delete currentPlan.ai_filtered_recommendations;
  currentPlan.ai_candidate_filter = {
    status: 'fallback',
    source: 'local_rule',
    kept: baseRecommendations().length,
    dropped: Math.max(0, fullPoolRecommendations().length - baseRecommendations().length),
    message,
    error: analysis.error || '',
    warnings: [],
  };
}

function bindStepChat() {
  document.querySelectorAll('.chat-send').forEach(button => {
    button.addEventListener('click', () => sendStepChat(button.dataset.chatSend, button));
  });
  document.querySelectorAll('.chat-input').forEach(input => {
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        const button = document.querySelector(`.chat-send[data-chat-send="${CSS.escape(input.dataset.chatInput)}"]`);
        if (button) sendStepChat(input.dataset.chatInput, button);
      }
    });
  });
}

async function sendStepChat(step, button) {
  const input = document.querySelector(`.chat-input[data-chat-input="${CSS.escape(step)}"]`);
  const message = (input?.value || '').trim();
  if (!message) return;
  currentPlan.llm_step_chats = currentPlan.llm_step_chats || {};
  const history = currentPlan.llm_step_chats[step] || [];
  const priorHistory = history.slice();
  history.push({role: 'user', content: message});
  currentPlan.llm_step_chats[step] = history;
  if (input) input.value = '';
  button.disabled = true;
  button.textContent = '发送中...';
  renderActiveStep();
  try {
    const res = await fetch('/api/llm/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        step,
        plan: currentPlan,
        message,
        history: priorHistory,
        llm_config: getLlmConfig(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'AI 聊天失败');
    currentPlan.llm_step_chats[step].push({
      role: 'assistant',
      content: data.summary || '',
      mode: data.mode,
      model: data.model,
    });
    if (step === 'candidate_pool') {
      currentPlan.ai_candidate_filter = {
        status: 'running',
        source: 'ai',
        message: '正在根据刚才的沟通重新筛选候选...',
      };
      renderActiveStep();
      await runCandidateFilter('chat');
      return;
    }
  } catch (error) {
    currentPlan.llm_step_chats[step].push({
      role: 'assistant',
      content: `AI 聊天失败：${error.message}`,
      mode: 'frontend_error',
    });
  } finally {
    renderActiveStep();
  }
}

function renderManualStepActions(stepIndex) {
  if (stepIndex < 2) return '';
  if (!isStepAccessible(stepIndex)) return '';
  const step = currentPlan?.steps?.[stepIndex] || {};
  if (['blocked', 'missing', 'empty', 'locked'].includes(step.status)) return '';
  if (manualCompletedSteps.has(stepIndex)) {
    return '<div class="step-actions"><span class="done-text">本步已完成</span></div>';
  }
  const isLast = stepIndex >= 5;
  return `
    <div class="step-actions">
      <button type="button" class="complete-step-button" data-complete-step="${stepIndex}">
        ${isLast ? '完成全部流程' : '完成本步，进入下一步'}
      </button>
    </div>
  `;
}

function bindCompleteStepButton() {
  const button = document.querySelector('.complete-step-button');
  if (!button) return;
  button.addEventListener('click', () => {
    const index = Number(button.dataset.completeStep || activeStepIndex);
    manualCompletedSteps.add(index);
    const steps = currentPlan?.steps || [];
    const nextIndex = index + 1;
    if (nextIndex < steps.length && isStepAccessible(nextIndex)) {
      activeStepIndex = nextIndex;
    }
    renderStepTabs();
    renderActiveStep();
  });
}

function renderStepMeta(step, stepIndex) {
  const input = step.input || {};
  const output = step.output || {};
  const evidence = stepIndex === 2 ? [] : (step.evidence || []);
  return `
    <div class="step-meta">
      ${Object.keys(input).length ? `<section><h4>输入</h4>${objectTable(input)}</section>` : ''}
      ${Object.keys(output).length ? `<section><h4>结果</h4>${objectTable(output)}</section>` : ''}
      ${evidence.length ? `<section><h4>证据</h4>${renderEvidence(evidence)}</section>` : ''}
    </div>
  `;
}

function applyAiCandidateFilter(analysis) {
  if (!analysis?.structured || !Array.isArray(analysis.keep_keys) || !analysis.keep_keys.length) return;
  const keep = new Set(analysis.keep_keys);
  const recs = fullPoolRecommendations();
  const filtered = recs.filter(item => keep.has(candidateKey(item)));
  if (!filtered.length) return;
  currentPlan.ai_filtered_recommendations = filtered;
  currentPlan.ai_candidate_filter = {
    status: 'done',
    source: 'ai',
    kept: filtered.length,
    dropped: Math.max(0, recs.length - filtered.length),
    drop: analysis.drop || [],
    warnings: analysis.warnings || [],
    message: analysis.summary || 'AI 已基于用户需求筛选候选池。',
  };
  return true;
}

function candidateChatContext() {
  const messages = currentPlan?.llm_step_chats?.candidate_pool || [];
  return messages.map(item => `${item.role === 'assistant' ? 'AI' : '用户'}：${item.content || ''}`).join('\n');
}

function activeRecommendations() {
  return currentPlan?.ai_filtered_recommendations || currentPlan?.recommendation?.recommendations || [];
}

function baseRecommendations() {
  return currentPlan?.recommendation?.recommendations || [];
}

function fullPoolRecommendations() {
  return currentPlan?.recommendation?.candidate_pool_recommendations || baseRecommendations();
}

function renderRankStep(data) {
  const profile = data.profile || {};
  return `
    ${table([
    ['考生生源地', profile.province || '-'],
    ['报考数据范围', data.data_scope?.target_scope || '-'],
    ['选科大类', profile.category || '-'],
    ['层次', profile.education_level || '-'],
    ['分数', profile.score || '-'],
    ['位次', profile.rank || '未填写'],
    ['核心诉求', profile.goal || '-'],
    ])}
    ${renderDataScope(data)}
    ${renderBatchLines(data)}
  `;
}

function renderDataScope(data) {
  const scope = data.data_scope || {};
  const rows = scope.rows_by_year || [];
  const body = rows.length ? rows.map(row => `
    <tr>
      <td>${escapeHtml(row.year)}</td>
      <td>${escapeHtml(row.rows)}</td>
      <td>${escapeHtml(row.school_count)}</td>
      <td>${escapeHtml(row.major_count)}</td>
      <td>${escapeHtml(row.missing_rank_pct)}%</td>
      <td>${escapeHtml(row.missing_quota_pct)}%</td>
    </tr>
  `).join('') : '<tr><td colspan="6">暂无覆盖统计</td></tr>';
  return `
    <section class="data-scope">
      <div class="section-title-row">
        <h4>${escapeHtml(scope.label || '生源地招生数据')}</h4>
        <span>${escapeHtml(scope.focus === 'hebei_candidate_national_colleges' ? '河北优先' : '通用模式')}</span>
      </div>
      <table>
        <thead><tr><th>年份</th><th>记录</th><th>学校</th><th>专业</th><th>缺位次</th><th>缺计划</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      ${scope.warnings?.length ? `<ul class="notes">${scope.warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
      ${scope.priorities?.length ? `<ul class="notes priority-notes">${scope.priorities.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
      ${renderPlanCoverage(scope.plan_coverage)}
    </section>
  `;
}

function renderPlanCoverage(planCoverage = {}) {
  if (!planCoverage.ready) {
    return '<p class="warning-line">2026招生计划库未接入；计划数、学费、学制和选科要求需要人工查招生计划册。</p>';
  }
  const rows = planCoverage.batch_category_counts || [];
  return `
    <section class="plan-coverage">
      <div class="section-title-row">
        <h4>2026 招生计划覆盖</h4>
        <span>${escapeHtml(planCoverage.official_count || planCoverage.record_count || 0)} 条官方计划</span>
      </div>
      <table>
        <thead><tr><th>批次</th><th>科类</th><th>计划专业</th><th>院校</th><th>学费字段</th><th>计划数字段</th></tr></thead>
        <tbody>
          ${rows.map(row => `
            <tr>
              <td>${escapeHtml(row.batch || '-')}</td>
              <td>${escapeHtml(row.category || '-')}</td>
              <td>${escapeHtml(row.records || 0)}</td>
              <td>${escapeHtml(row.schools || 0)}</td>
              <td>${escapeHtml(row.tuition_text_count || 0)}</td>
              <td>${escapeHtml(row.plan_count_count || 0)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </section>
  `;
}

function renderEquivalentStep(data) {
  const eq = data.equivalent_scores || {};
  const check = data.score_rank_check || {};
  if (check.blocking) {
    const anchor2025 = (eq.years || []).find(row => Number(row.year) === 2025) || {};
    return `
      <section class="conflict-check-panel">
        <div class="section-title-row">
          <h4>分数与位次核对</h4>
          <span>确认前不继续换算</span>
        </div>
        <table>
          <thead><tr><th>核对项</th><th>输入</th><th>按 2025 河北一分一段查到</th><th>说明</th></tr></thead>
          <tbody>
            <tr>
              <td>按分数查位次</td>
              <td>${escapeHtml(check.score ?? eq.score ?? '-')} 分</td>
              <td>${escapeHtml(check.estimated_rank ? `${check.estimated_rank} 位` : '-')}</td>
              <td>这是判断“分数和位次是否匹配”的依据。</td>
            </tr>
            <tr>
              <td>按位次反推分数</td>
              <td>${escapeHtml(check.provided_rank ?? eq.rank ?? '-')} 位</td>
              <td>${escapeHtml(anchor2025.equivalent_score ? `${anchor2025.equivalent_score} 分，对应累计 ${anchor2025.cumulative_rank} 位` : '-')}</td>
              <td>这是等位分换算结果；因为当前已冲突，暂不用于后续推荐。</td>
            </tr>
          </tbody>
        </table>
        <p class="critical-inline">${escapeHtml(check.message || '分数和位次明显不一致，请先核对。')}</p>
      </section>
      ${renderBatchLines(data)}
    `;
  }
  const rows = eq.years || [];
  const body = rows.length ? rows.map(row => `
    <tr>
      <td>${escapeHtml(row.year)}</td>
      <td>${escapeHtml(row.category)}</td>
      <td>${escapeHtml(row.equivalent_score)}</td>
      <td>${escapeHtml(row.cumulative_rank)}</td>
      <td>${escapeHtml(row.same_score_count)}</td>
      <td>${escapeHtml(sourceLabel(row.source_type))}</td>
    </tr>
  `).join('') : `<tr><td colspan="6">${escapeHtml(eq.message || '暂无等位分数据')}</td></tr>`;
  return `
    ${check.message ? `<p class="info-line">${escapeHtml(check.message)}</p>` : ''}
    <p class="muted">等位分按“用户位次”反推：同一位次在往年对应多少分。表里的“累计位次”是该等位分所在分数段的累计人数，可能略大于输入位次。</p>
    <table>
      <thead><tr><th>年份</th><th>科类</th><th>位次对应分</th><th>该分累计位次</th><th>同分人数</th><th>来源</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
    ${renderBatchLines(data)}
    ${eq.missing_years?.length ? `<p class="warning-line">缺失年份：${eq.missing_years.map(escapeHtml).join('、')}。2025 年优先级最高，需要继续补齐。</p>` : ''}
  `;
}

function renderBatchLines(data) {
  const info = data.batch_control_lines || {};
  const rows = info.lines || [];
  const body = rows.length ? rows.map(row => `
    <tr>
      <td>${escapeHtml(row.year)}</td>
      <td>${escapeHtml(row.category)}</td>
      <td>${escapeHtml(row.line_type)}</td>
      <td>${escapeHtml(row.score)}</td>
      <td>${row.source_url ? `<a href="${escapeAttr(row.source_url)}" target="_blank" rel="noreferrer">来源</a>` : '-'}</td>
    </tr>
  `).join('') : '<tr><td colspan="5">暂无省控线数据</td></tr>';
  return `
    <section class="batch-lines">
      <div class="section-title-row">
        <h4>2025 省控线</h4>
        <span>${escapeHtml(info.ready ? '已接入' : '未就绪')}</span>
      </div>
      <table>
        <thead><tr><th>年份</th><th>科类</th><th>批次线</th><th>分数</th><th>来源</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      ${info.warnings?.length ? `<ul class="notes">${info.warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
    </section>
  `;
}

function renderCandidateStep(data) {
  const pool = data.candidate_pool || {};
  const filteredRows = activeRecommendations();
  const baseRows = baseRecommendations();
  const poolRows = fullPoolRecommendations();
  if (pool.locked_reason) {
    return `<p class="warning-line">${escapeHtml(pool.locked_reason)}</p>`;
  }
  return `
    <div class="metric-grid">
      <div><dt>完整池</dt><dd>${escapeHtml(poolRows.length || pool.total_recommendations || 0)}</dd></div>
      <div><dt>筛选后</dt><dd>${escapeHtml(filteredRows.length)}</dd></div>
      <div><dt>学校数</dt><dd>${escapeHtml(pool.school_count ?? 0)}</dd></div>
      <div><dt>专业数</dt><dd>${escapeHtml(pool.major_count ?? 0)}</dd></div>
      <div><dt>官方计划</dt><dd>${escapeHtml(pool.official_plan_matched ?? 0)}</dd></div>
      <div><dt>待核计划</dt><dd>${escapeHtml(pool.plan_missing ?? 0)}</dd></div>
    </div>
    ${pool.score_window ? `<p class="muted">分数窗口：${escapeHtml(pool.score_window.low)}-${escapeHtml(pool.score_window.high)}（${escapeHtml(pool.score_window.rule)}）</p>` : ''}
    ${pool.rank_window ? `<p class="muted">位次窗口：${escapeHtml(pool.rank_window.chong_min)}-${escapeHtml(pool.rank_window.bao_max)}（${escapeHtml(pool.rank_window.rule)}）</p>` : ''}
    ${data.data_scope?.label ? `<p class="muted">当前候选池范围：${escapeHtml(data.data_scope.label)}。</p>` : ''}
    ${renderHardFilterResult(pool.hard_filter)}
    ${renderAiFilterResult(data)}
    <section class="candidate-section">
      <div class="section-title-row">
        <h4>第 3 步确认后的专业与院校候选</h4>
        <span>${escapeHtml(filterStageLabel(data, filteredRows, baseRows))}</span>
      </div>
      <p class="muted">这里是正式筛选结果，用于后续冲稳保、排序和章程核验。未选择专业偏好时，AI 会先根据就业/稳定/深造、城市、家庭资源和风险偏好推荐合适方向。</p>
      ${renderCandidateTable(filteredRows)}
    </section>
    <section class="candidate-section">
      <div class="section-title-row">
        <h4>完整院校专业池</h4>
        <span>${escapeHtml(poolRows.length)} 条</span>
      </div>
      <p class="muted">这是按位次窗口、批次、选科和历史录取数据召回的底层池子，只展示原始数据字段，不带冲稳保策略。</p>
      ${renderFullPoolTable(poolRows)}
    </section>
  `;
}

function renderHardFilterResult(filter = {}) {
  if (!filter || !filter.enabled) return '';
  const reasons = filter.reason_counts || {};
  const reasonText = Object.keys(reasons).length
    ? Object.entries(reasons).map(([key, value]) => `${key} ${value}`).join('，')
    : '无硬条件剔除';
  return `
    <section class="hard-filter-result">
      <strong>硬条件过滤：${escapeHtml(filter.before || 0)} 条 -> ${escapeHtml(filter.after || 0)} 条，剔除 ${escapeHtml(filter.removed_count || 0)} 条。</strong>
      <p class="muted">规则：${filter.tuition_budget ? `学费不超过 ${escapeHtml(filter.tuition_budget)} 元/年` : '未设置学费预算'}${filter.reject_private ? '；不接受民办' : ''}${filter.reject_coop ? '；不接受中外合作' : ''}</p>
      <p class="muted">剔除原因：${escapeHtml(reasonText)}</p>
      ${filter.removed_samples?.length ? `
        <details>
          <summary>查看剔除样例</summary>
          <ul class="notes">${filter.removed_samples.slice(0, 8).map(item => `<li>${escapeHtml(item.school_name || '')} · ${escapeHtml(item.major_name || '')}：${(item.reasons || []).map(escapeHtml).join('、')}</li>`).join('')}</ul>
        </details>
      ` : ''}
    </section>
  `;
}

function filterStageLabel(data, filteredRows, baseRows) {
  if (data.ai_candidate_filter) return `AI 筛选 ${filteredRows.length} 条`;
  if (baseRows.length !== fullPoolRecommendations().length) return `规则筛选 ${filteredRows.length} 条`;
  return `当前 ${filteredRows.length} 条`;
}

function renderAiFilterResult(data) {
  const filter = data.ai_candidate_filter;
  if (!filter) return '';
  if (filter.status === 'running') {
    return `
      <section class="ai-filter-result">
        <strong>${escapeHtml(filter.message || '正在调用 AI 筛选候选池...')}</strong>
      </section>
    `;
  }
  if (filter.status === 'fallback') {
    return `
      <section class="ai-filter-result fallback">
        <strong>${escapeHtml(filter.message || '当前使用本地规则筛选结果。')}</strong>
        <p class="muted">本地规则会按位次窗口、专业关键词、城市偏好和冲稳保比例先给出一版结果。</p>
        ${filter.error ? `<p class="muted">AI 失败原因：${escapeHtml(filter.error)}</p>` : ''}
      </section>
    `;
  }
  return `
    <section class="ai-filter-result">
      <strong>AI 已筛选候选池：保留 ${escapeHtml(filter.kept)} 个，剔除 ${escapeHtml(filter.dropped)} 个。</strong>
      ${filter.message ? `<p class="muted">${escapeHtml(filter.message)}</p>` : ''}
      ${filter.warnings?.length ? `<ul class="notes">${filter.warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
    </section>
  `;
}

function renderCandidateTable(rows) {
  if (!rows.length) {
    return '<p class="warning-line">当前没有候选院校，建议放宽专业、城市或层次限制。</p>';
  }
  return `
    <div class="table-wrap">
      <table class="candidate-table">
        <thead>
          <tr>
            <th>学校</th>
            <th>专业</th>
            <th>科类</th>
            <th>年份</th>
            <th>录取分</th>
            <th>录取位次</th>
            <th>等位分</th>
            <th>学费</th>
            <th>计划</th>
            <th>选科</th>
            <th>计划来源</th>
            <th>稳定性</th>
            <th>差距</th>
            <th>策略</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(item => `
            <tr>
              <td class="school-cell">${escapeHtml(item.school_name || '-')}<small>${escapeHtml([item.city, item.tier].filter(Boolean).join(' · '))}</small></td>
              <td class="major-cell">${escapeHtml(item.sp_name || item.major_name || '-')}</td>
              <td>${escapeHtml(item.category || '-')}</td>
              <td>${escapeHtml(item.source_year ?? item.year ?? '-')}</td>
              <td>${escapeHtml(item.source_score ?? item.score ?? '-')}</td>
              <td>${escapeHtml(item.source_rank ?? item.rank_value ?? '-')}</td>
              <td>${escapeHtml(item.equivalent_score ?? '-')}</td>
              <td>${escapeHtml(planFeeText(item))}</td>
              <td>${escapeHtml(planCountText(item))}</td>
              <td>${escapeHtml(item.subject_requirement || '-')}</td>
              <td>${escapeHtml(planMatchLabel(item.plan_match_status))}</td>
              <td>${escapeHtml(stabilityText(item))}</td>
              <td>${escapeHtml(gapText(item, item))}</td>
              <td><span class="tag ${escapeHtml(item.tag || '')}">${escapeHtml(item.tag || '-')}</span></td>
              <td>${escapeHtml(item.evidence_level?.label || confidenceLabel(item.evidence?.confidence || item.confidence))}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderFullPoolTable(rows) {
  if (!rows.length) {
    return '<p class="warning-line">当前完整候选池为空，建议放宽专业、城市或层次限制。</p>';
  }
  const totalPages = Math.max(1, Math.ceil(rows.length / FULL_POOL_PAGE_SIZE));
  fullPoolPage = Math.min(Math.max(1, fullPoolPage), totalPages);
  const start = (fullPoolPage - 1) * FULL_POOL_PAGE_SIZE;
  const pageRows = rows.slice(start, start + FULL_POOL_PAGE_SIZE);
  return `
    <div class="pager-row">
      <span>第 ${escapeHtml(fullPoolPage)} / ${escapeHtml(totalPages)} 页 · ${escapeHtml(start + 1)}-${escapeHtml(start + pageRows.length)} / ${escapeHtml(rows.length)}</span>
      <div>
        <button type="button" class="pager-button" data-pool-page="prev" ${fullPoolPage <= 1 ? 'disabled' : ''}>上一页</button>
        <button type="button" class="pager-button" data-pool-page="next" ${fullPoolPage >= totalPages ? 'disabled' : ''}>下一页</button>
      </div>
    </div>
    <div class="table-wrap">
      <table class="candidate-table pool-table">
        <thead>
          <tr>
            <th>学校</th>
            <th>专业</th>
            <th>科类</th>
            <th>批次</th>
            <th>年份</th>
            <th>录取分</th>
            <th>录取位次</th>
            <th>等位分</th>
            <th>学费</th>
            <th>计划</th>
            <th>选科</th>
            <th>计划来源</th>
          </tr>
        </thead>
        <tbody>
          ${pageRows.map(item => `
            <tr>
              <td class="school-cell">${escapeHtml(item.school_name || '-')}<small>${escapeHtml([item.city, item.tier].filter(Boolean).join(' · '))}</small></td>
              <td class="major-cell">${escapeHtml(item.sp_name || item.major_name || '-')}</td>
              <td>${escapeHtml(item.category || '-')}</td>
              <td>${escapeHtml(item.batch || item.education_level || '-')}</td>
              <td>${escapeHtml(item.source_year ?? item.year ?? '-')}</td>
              <td>${escapeHtml(item.source_score ?? item.score ?? '-')}</td>
              <td>${escapeHtml(item.source_rank ?? item.rank_value ?? '-')}</td>
              <td>${escapeHtml(item.equivalent_score ?? '-')}</td>
              <td>${escapeHtml(planFeeText(item))}</td>
              <td>${escapeHtml(planCountText(item))}</td>
              <td>${escapeHtml(item.subject_requirement || '-')}</td>
              <td>${escapeHtml(planMatchLabel(item.plan_match_status))}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function bindFullPoolPager() {
  document.querySelectorAll('.pager-button[data-pool-page]').forEach(button => {
    button.addEventListener('click', () => {
      const action = button.dataset.poolPage;
      fullPoolPage += action === 'next' ? 1 : -1;
      renderActiveStep();
    });
  });
}

function renderStrategyStep(data) {
  const counts = bucketCounts(activeRecommendations());
  const safety = data.strategy?.safety_check || {};
  return `
    <div class="metric-grid">
      <div><dt>冲</dt><dd>${escapeHtml(counts['冲'] || 0)}</dd></div>
      <div><dt>稳</dt><dd>${escapeHtml(counts['稳'] || 0)}</dd></div>
      <div><dt>保</dt><dd>${escapeHtml(counts['保'] || 0)}</dd></div>
      <div><dt>模型</dt><dd>${escapeHtml(data.strategy?.risk_model || '-')}</dd></div>
      <div><dt>保底学校</dt><dd>${escapeHtml(safety.bao_school_count ?? '-')}</dd></div>
      <div><dt>官方计划保底</dt><dd>${escapeHtml(safety.official_plan_count ?? '-')}</dd></div>
      <div><dt>小计划保底</dt><dd>${escapeHtml(safety.small_plan_count ?? '-')}</dd></div>
      <div><dt>保底状态</dt><dd>${escapeHtml(safetyStatusLabel(safety.status))}</dd></div>
    </div>
    <ul class="notes">${(data.strategy?.notes || []).map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul>
  `;
}

function safetyStatusLabel(status) {
  if (status === 'adequate') return '初步可用';
  if (status === 'thin') return '偏薄';
  if (status === 'needs_plan_review') return '需核计划';
  if (status === 'small_plan_risk') return '小计划风险';
  return '-';
}

function renderOrderStep(data) {
  const rows = activeOrderRows(data);
  if (!rows.length) {
    return '<p class="warning-line">等位分不可用或没有候选结果，暂不排序。</p>';
  }
  return `<div class="rec-list">${rows.map(item => renderRecommendation(item, activeRecommendations())).join('')}</div>`;
}

function activeOrderRows(data) {
  if (!data.ai_filtered_recommendations) return data.volunteer_order || [];
  return data.ai_filtered_recommendations.map((item, index) => ({
    order: index + 1,
    tag: item.tag,
    school_name: item.school_name,
    major_name: item.sp_name || item.major_name,
    source_year: item.source_year || item.year,
    source_score: item.source_score || item.score,
    source_rank: item.source_rank || item.rank_value,
    equivalent_score: item.equivalent_score,
    score_gap: item.score_gap,
    rank_gap: item.rank_gap,
    plan_score: item.plan_score,
    plan_year: item.plan_year,
    plan_count: item.plan_count,
    tuition_text: item.tuition_text,
    duration: item.duration,
    campus: item.campus,
    subject_requirement: item.subject_requirement,
    plan_match_status: item.plan_match_status,
    plan_remarks: item.plan_remarks,
    stability: item.stability,
    stability_label: item.stability_label,
    stability_risk: item.stability_risk,
    source: item.source,
    evidence_level: item.evidence_level || {},
    reason: 'AI 筛选后保留，排序仍按当前效用分和风险标签展示。',
  }));
}

function renderRecommendation(orderItem, recs) {
  const item = recs.find(r => r.school_name === orderItem.school_name && r.sp_name === orderItem.major_name) || orderItem;
  return `
    <article class="rec ${escapeHtml(orderItem.tag || item.tag || '')}">
      <div class="top">
        <strong>${escapeHtml(orderItem.order || item.rank || '')}. ${escapeHtml(orderItem.school_name || item.school_name || '')}</strong>
        <span>${escapeHtml(orderItem.tag || item.tag || '')} · ${escapeHtml(item.p_pct || '历史区间')}</span>
      </div>
      <div>${[item.city, item.tier, orderItem.major_name || item.sp_name].filter(Boolean).map(escapeHtml).join(' · ')}</div>
      <dl class="evidence">
        <div><dt>来源</dt><dd>${escapeHtml(item.evidence?.source || item.source || item.sources?.join(', ') || orderItem.source || '未知')}</dd></div>
        <div><dt>年份</dt><dd>${escapeHtml(String(item.evidence?.year ?? item.source_year ?? orderItem.source_year ?? '-'))}</dd></div>
        <div><dt>分数</dt><dd>${escapeHtml(String(item.evidence?.score ?? item.source_score ?? orderItem.source_score ?? '-'))}</dd></div>
        <div><dt>位次</dt><dd>${escapeHtml(String(item.evidence?.rank ?? item.source_rank ?? orderItem.source_rank ?? '-'))}</dd></div>
        <div><dt>等位分</dt><dd>${escapeHtml(String(item.equivalent_score ?? orderItem.equivalent_score ?? '-'))}</dd></div>
        <div><dt>学费</dt><dd>${escapeHtml(planFeeText(item, orderItem))}</dd></div>
        <div><dt>计划数</dt><dd>${escapeHtml(planCountText(item, orderItem))}</dd></div>
        <div><dt>学制</dt><dd>${escapeHtml(String(item.duration ?? orderItem.duration ?? '-'))}</dd></div>
        <div><dt>校区</dt><dd>${escapeHtml(String(item.campus ?? orderItem.campus ?? '-'))}</dd></div>
        <div><dt>选科</dt><dd>${escapeHtml(String(item.subject_requirement ?? orderItem.subject_requirement ?? '-'))}</dd></div>
        <div><dt>稳定性</dt><dd>${escapeHtml(stabilityText(item, orderItem))}</dd></div>
        <div><dt>差距</dt><dd>${escapeHtml(gapText(item, orderItem))}</dd></div>
        <div><dt>效用分</dt><dd>${escapeHtml(String(Math.round(item.plan_score ?? orderItem.plan_score ?? item.utility ?? 0)))}</dd></div>
        <div><dt>层次</dt><dd>${escapeHtml(item.education_level || '-')}</dd></div>
        <div><dt>科类</dt><dd>${escapeHtml(item.category || '-')}</dd></div>
        <div><dt>可信度</dt><dd>${confidenceLabel(item.evidence?.confidence || item.confidence)}</dd></div>
        <div><dt>证据等级</dt><dd>${escapeHtml(item.evidence_level?.label || item.evidence?.level_label || '-')}</dd></div>
      </dl>
      <p>${escapeHtml(orderItem.reason || item.note || '')}</p>
      <p class="muted">${escapeHtml(planStatusText(item, orderItem))}</p>
      <p class="life">${escapeHtml(item.school_life?.summary || '暂无本地生活质量摘要，可点击来源继续查。')}</p>
      <a href="${escapeHtml(item.school_life?.source_url || '#')}" target="_blank" rel="noreferrer">学校信息来源</a>
    </article>
  `;
}

function renderCharterStep(data) {
  const rows = data.ai_filtered_recommendations ? data.ai_filtered_recommendations.slice(0, 12).map(item => ({
    school_name: item.school_name,
    major_name: item.sp_name || item.major_name,
    must_check: ['单科成绩', '体检限制', '外语语种', '转专业/调剂', '招生章程年份'],
    known_plan: {
      plan_count: item.plan_count,
      tuition_text: item.tuition_text,
      duration: item.duration,
      subject_requirement: item.subject_requirement,
      plan_match_status: item.plan_match_status,
    },
    source_hint: 'AI 筛选后候选；计划数/学费/选科优先参考河北考试院2026招生计划，章程仍需人工核验。',
    search_url: `https://www.baidu.com/s?wd=${encodeURIComponent((item.school_name || '') + ' 招生章程 ' + (item.sp_name || item.major_name || ''))}`,
  })) : (data.charter_checks || []);
  if (!rows.length) {
    return '<p class="warning-line">等位分不可用或没有候选结果，暂不生成核验清单。</p>';
  }
  return rows.map(item => `
    <article class="check">
      <strong>${escapeHtml(item.school_name || '')}</strong>
      <span>${escapeHtml(item.major_name || '')}</span>
      ${item.known_plan ? renderKnownPlan(item.known_plan) : ''}
      <p>待核验：${(item.must_check || []).map(escapeHtml).join('、')}</p>
      <p class="muted">${escapeHtml(item.source_hint || '')}</p>
      ${item.search_url ? `<a href="${escapeHtml(item.search_url)}" target="_blank" rel="noreferrer">检索招生章程</a>` : ''}
    </article>
  `).join('');
}

function renderKnownPlan(plan = {}) {
  return `
    <dl class="evidence compact-evidence">
      <div><dt>计划数</dt><dd>${escapeHtml(planCountText(plan))}</dd></div>
      <div><dt>学费</dt><dd>${escapeHtml(planFeeText(plan))}</dd></div>
      <div><dt>学制</dt><dd>${escapeHtml(plan.duration || '-')}</dd></div>
      <div><dt>选科</dt><dd>${escapeHtml(plan.subject_requirement || '-')}</dd></div>
      <div><dt>计划来源</dt><dd>${escapeHtml(planMatchLabel(plan.plan_match_status))}</dd></div>
    </dl>
  `;
}

function table(rows) {
  return `
    <table>
      <tbody>${rows.map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(value)}</td></tr>`).join('')}</tbody>
    </table>
  `;
}

function objectTable(obj) {
  return table(Object.entries(obj).map(([key, value]) => [key, formatValue(value)]));
}

function renderEvidence(evidence) {
  if (typeof evidence[0] === 'string') {
    return `<ul class="notes">${evidence.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  }
  return `
    <table>
      <tbody>
        ${evidence.map(item => `
          <tr>${Object.entries(item).map(([, value]) => `<td>${escapeHtml(formatValue(value))}</td>`).join('')}</tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) return value.map(formatValue).join('、');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function gapText(item, orderItem) {
  const rankGap = item.rank_gap ?? orderItem.rank_gap;
  const scoreGap = item.score_gap ?? orderItem.score_gap;
  if (rankGap !== undefined && rankGap !== null) return `位次差 ${rankGap}`;
  if (scoreGap !== undefined && scoreGap !== null) return `分差 ${scoreGap}`;
  return '-';
}

function planFeeText(item, orderItem = {}) {
  const value = item.tuition_text ?? orderItem.tuition_text;
  if (value) return value;
  return '待核对';
}

function planCountText(item, orderItem = {}) {
  const value = item.plan_count ?? orderItem.plan_count;
  if (value !== undefined && value !== null && value !== '') return String(value);
  return '待核对';
}

function planStatusText(item, orderItem = {}) {
  const status = item.plan_match_status || orderItem.plan_match_status;
  const remarks = item.plan_remarks || orderItem.plan_remarks || '';
  if (String(status || '').startsWith('official_matched')) return remarks || `已匹配河北2026官方招生计划（${planMatchLabel(status)}）。`;
  if (status === 'mock_matched') return remarks || '当前为Mock计划数据，后续以河北2026招生计划为准。';
  return remarks || '未匹配到2026河北招生计划；学费、计划数、学制和选科要求需要人工核对。';
}

function planMatchLabel(status) {
  if (status === 'official_matched') return '官方精确';
  if (status === 'official_matched_by_school_major_code') return '官方兜底';
  if (status === 'official_matched_by_school_major_name') return '官方名称匹配';
  if (status === 'mock_matched') return 'Mock';
  if (status === 'reserved_waiting_official_plan') return '未匹配';
  if (!status) return '未匹配';
  return String(status);
}

function stabilityText(item, orderItem = {}) {
  const stability = item.stability || orderItem.stability || {};
  const label = item.stability_label || orderItem.stability_label || stability.label;
  if (!label) return '-';
  const trend = stability.trend ? `/${stability.trend}` : '';
  const years = stability.years_count ? `${stability.years_count}年` : '';
  return [label + trend, years].filter(Boolean).join(' · ');
}

function candidateKey(item) {
  return [
    item.school_name || '',
    item.sp_name || item.major_name || '',
    String(item.source_year || item.year || ''),
    item.category || '',
  ].join('|');
}

function bucketCounts(rows) {
  return rows.reduce((acc, item) => {
    const tag = item.tag || '其他';
    acc[tag] = (acc[tag] || 0) + 1;
    return acc;
  }, {冲: 0, 稳: 0, 保: 0});
}

function humanizeError(data) {
  if (data && data.error === 'recommendation engine is not ready') {
    return [
      '推荐引擎还没有就绪。',
      '',
      '原因：缺少录取数据库。',
      data.db_path ? `当前检测路径：${data.db_path}` : '',
      data.hebei_lnwc_db_path ? `河北专项库路径：${data.hebei_lnwc_db_path}` : '',
      data.primary_gz_path ? `备用 gz 路径：${data.primary_gz_path}` : '',
      '',
      '生成 hebei_lnwc_loggedin.db 和 score_segments.db 后刷新页面即可继续。',
    ].filter(Boolean).join('\n');
  }
  if (data && data.error) {
    return `生成失败：${data.error}`;
  }
  return JSON.stringify(data, null, 2);
}

function confidenceLabel(value) {
  return {
    high: '高',
    medium: '中',
    low: '低',
    unknown: '未知',
  }[value] || escapeHtml(String(value || '未知'));
}

function statusLabel(value) {
  return {
    done: '已完成',
    partial: '部分完成',
    pending_manual: '待确认',
    missing: '缺信息',
    empty: '无结果',
    pending_data: '待补数据',
    pending_web: '待联网核验',
    blocked: '阻断',
    locked: '锁定',
  }[value] || escapeHtml(String(value || '未知'));
}

function sourceLabel(value) {
  return {
    official: '官方',
    local_vision_dxsbb_2025: '2025一分一段清洗',
    local_crawler: '自爬',
    gaokao_advisor: 'gaokao-advisor',
    open_source: '开源历史',
  }[value] || value || '未知';
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

checkHealth();
initLlmConfig();
initMajorSelector();
initCitySelector();

function initCitySelector() {
  selectedPreferredCities = readStoredCitySelections();
  renderCityOptions();
  renderSelectedCities();
}

function renderCityOptions() {
  const el = document.querySelector('#city-options');
  if (!el) return;
  el.innerHTML = COMMON_TARGET_CITIES.map(city => {
    const isAny = city === '不限城市';
    const selected = isAny ? selectedPreferredCities.size === 0 : selectedPreferredCities.has(city);
    return `
      <button type="button" class="major-chip ${selected ? 'selected' : ''}" data-city="${escapeAttr(city)}" aria-pressed="${selected ? 'true' : 'false'}">
        ${escapeHtml(city)}
      </button>
    `;
  }).join('');
  el.querySelectorAll('[data-city]').forEach(button => {
    button.addEventListener('click', () => {
      const city = button.dataset.city || '';
      if (city === '不限城市') {
        selectedPreferredCities.clear();
      } else if (selectedPreferredCities.has(city)) {
        selectedPreferredCities.delete(city);
      } else {
        selectedPreferredCities.add(city);
      }
      saveStoredCitySelections();
      renderCityOptions();
      renderSelectedCities();
    });
  });
}

function renderSelectedCities() {
  const el = document.querySelector('#selected-cities');
  if (!el) return;
  const selected = Array.from(selectedPreferredCities);
  el.innerHTML = selected.length ? selected.map(city => `
    <button type="button" class="major-chip selected" data-city-remove="${escapeAttr(city)}">${escapeHtml(city)}</button>
  `).join('') : '<span class="muted">不限制城市</span>';
  el.querySelectorAll('[data-city-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedPreferredCities.delete(button.dataset.cityRemove || '');
      saveStoredCitySelections();
      renderCityOptions();
      renderSelectedCities();
    });
  });
}

function readStoredCitySelections() {
  try {
    const items = JSON.parse(localStorage.getItem(CITY_SELECTION_STORAGE_KEY) || '[]');
    return new Set(Array.isArray(items) ? items.filter(Boolean) : []);
  } catch {
    return new Set();
  }
}

function saveStoredCitySelections() {
  localStorage.setItem(CITY_SELECTION_STORAGE_KEY, JSON.stringify(Array.from(selectedPreferredCities)));
}

function initMajorSelector() {
  const form = document.querySelector('#form');
  const category = form?.elements.category;
  const level = form?.elements.education_level;
  selectedMajorKeywords = readStoredMajorSelections();
  document.querySelector('#open-major-page')?.addEventListener('click', () => {
    const params = new URLSearchParams({
      category: category?.value || '',
      education_level: level?.value || '',
      goal: form?.elements.goal?.value || '',
    });
    window.location.href = `/majors.html?${params.toString()}`;
  });
  category?.addEventListener('change', () => {
    renderSelectedMajors();
  });
  level?.addEventListener('change', () => {
    renderSelectedMajors();
  });
  window.addEventListener('storage', event => {
    if (event.key === MAJOR_SELECTION_STORAGE_KEY) {
      selectedMajorKeywords = readStoredMajorSelections();
      renderSelectedMajors();
    }
  });
  renderSelectedMajors();
}

async function loadMajorOptions() {
  const form = document.querySelector('#form');
  const category = encodeURIComponent(form?.elements.category?.value || '');
  const educationLevel = encodeURIComponent(form?.elements.education_level?.value || '');
  const container = document.querySelector('#major-options');
  if (container) container.innerHTML = '<p class="muted">正在加载专业...</p>';
  try {
    const res = await fetch(`/api/major-options?category=${category}&education_level=${educationLevel}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '专业列表加载失败');
    majorOptions = data.items || [];
  } catch (error) {
    majorOptions = [];
    if (container) container.innerHTML = `<p class="warning-line">${escapeHtml(error.message)}</p>`;
    renderSelectedMajors();
    return;
  }
  renderSelectedMajors();
  renderMajorOptions();
}

function renderSelectedMajors() {
  const el = document.querySelector('#selected-majors');
  if (!el) return;
  const selected = Array.from(selectedMajorKeywords);
  el.innerHTML = selected.length ? selected.map(name => `
    <button type="button" class="major-chip selected" data-major-remove="${escapeAttr(name)}">${escapeHtml(name)}</button>
  `).join('') : '<span class="muted">可跳过；不选则交给第 3 步统一筛选</span>';
  el.querySelectorAll('[data-major-remove]').forEach(button => {
    button.addEventListener('click', () => {
      selectedMajorKeywords.delete(button.dataset.majorRemove || '');
      saveStoredMajorSelections();
      renderSelectedMajors();
    });
  });
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

function renderMajorOptions() {
  const el = document.querySelector('#major-options');
  if (!el) return;
  const keyword = (document.querySelector('#major-search')?.value || '').trim().toLowerCase();
  const limit = keyword ? 160 : 40;
  const filtered = majorOptions
    .filter(item => !keyword || String(item.name || '').toLowerCase().includes(keyword))
    .slice(0, limit);
  if (!filtered.length) {
    el.innerHTML = '<p class="muted">没有匹配专业。可以清空搜索或切换选科/层次。</p>';
    return;
  }
  const hint = keyword
    ? `匹配 ${filtered.length} 个专业，点击即可选择`
    : `已加载 ${majorOptions.length} 个专业，先显示招生覆盖较多的 ${filtered.length} 个，点击即可选择`;
  el.innerHTML = `<div class="major-options-hint">${escapeHtml(hint)}</div>` + filtered.map(item => {
    const name = item.name || '';
    const selected = selectedMajorKeywords.has(name);
    return `
      <button type="button" class="major-chip ${selected ? 'selected' : ''}" data-major="${escapeAttr(name)}">
        ${escapeHtml(name)}
        <small>${escapeHtml(item.school_count || 0)}校</small>
      </button>
    `;
  }).join('');
  el.querySelectorAll('[data-major]').forEach(button => {
    button.addEventListener('click', () => {
      const name = button.dataset.major || '';
      if (selectedMajorKeywords.has(name)) selectedMajorKeywords.delete(name);
      else selectedMajorKeywords.add(name);
      renderSelectedMajors();
      renderMajorOptions();
    });
  });
}

function initLlmConfig() {
  const saved = readStoredLlmConfig();
  if (saved.base_url) document.querySelector('#llm-base-url').value = saved.base_url;
  if (saved.model) document.querySelector('#llm-model').value = saved.model;
  if (saved.api_key) document.querySelector('#llm-key').value = saved.api_key;
  document.querySelector('#llm-timeout').value = String(Math.max(120, Number(saved.timeout || 120)));
  document.querySelector('#save-llm').addEventListener('click', () => {
    localStorage.setItem(LLM_STORAGE_KEY, JSON.stringify(getLlmConfig()));
    const button = document.querySelector('#save-llm');
    button.textContent = '已保存';
    setTimeout(() => { button.textContent = '保存 AI 配置'; }, 1200);
  });
}

function readStoredLlmConfig() {
  try {
    return JSON.parse(localStorage.getItem(LLM_STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function getLlmConfig() {
  const timeout = Number(document.querySelector('#llm-timeout')?.value || 120);
  return {
    base_url: document.querySelector('#llm-base-url')?.value.trim() || '',
    model: document.querySelector('#llm-model')?.value.trim() || '',
    api_key: document.querySelector('#llm-key')?.value.trim() || '',
    timeout: Math.max(120, Math.min(timeout, 120)),
  };
}
