let engineReady = false;
let currentPlan = null;
let activeStepIndex = 0;
let manualCompletedSteps = new Set();
const LLM_STORAGE_KEY = 'gaokao_llm_config';

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
      const advisor = data.optional_engines?.gaokao_advisor || {};
      const llm = data.llm_advisor || {};
      const warnings = data.quality_warnings || [];
      el.innerHTML = `
        <strong>主数据源：${escapeHtml(primary.name || '未知')}</strong>
        <span>${primary.ready ? '可用' : '不可用'} · ${escapeHtml(primary.message || '')}</span>
        <span>样本：${coverage.record_count || 0} 条 · 年份：${coverage.year_min || '-'}-${coverage.year_max || '-'} · 省份：${coverage.province_count || 0} 个</span>
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
      <span>缺少录取数据库：<code>data-pipeline/output/unified_admission.db</code> 或 <code>data/admission_clean.db.gz</code></span>
      <span>统一库检测路径：${escapeHtml(data.unified_db_path || '')}</span>
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
    major_keywords: splitInput(form.get('major_keywords') || ''),
    preferred_cities: splitInput(form.get('preferred_cities') || ''),
    family: form.get('family') || '',
    constraints: form.get('constraints') || '',
    max_slots: Number(form.get('max_slots') || 30),
    engine_mode: form.get('engine_mode') || 'unified',
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
  activeStepIndex = firstAvailableStep(data.steps || []);
  renderStepTabs();
  renderActiveStep();
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
  bindCompleteStepButton();
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
  2: {key: 'candidate_pool', label: 'AI 筛选院校范围'},
  3: {key: 'strategy', label: 'AI 确定冲稳保策略'},
  4: {key: 'order', label: 'AI 排序志愿'},
  5: {key: 'charter', label: 'AI 分析招生章程'},
};

function renderStepLlmPanel(stepIndex) {
  const meta = LLM_STEP_MAP[stepIndex];
  if (!meta) return '';
  const analysis = currentPlan?.llm_step_analyses?.[meta.key];
  const label = analysis?.mode === 'llm' ? `AI · ${analysis.model || '已配置'}` : (analysis ? '规则兜底' : '未调用');
  return `
    <section class="advisor-panel">
      <div class="panel-title">
        <h4>${escapeHtml(meta.label)}</h4>
        <span>${escapeHtml(label)}</span>
      </div>
      <button type="button" class="llm-step-button" data-llm-step="${escapeHtml(meta.key)}">
        ${analysis ? '重新调用 AI 分析' : '调用 AI 分析'}
      </button>
      ${renderLlmNotice(analysis)}
      ${analysis?.summary ? `<div class="advisor-text">${escapeHtml(analysis.summary).replaceAll('\n', '<br>')}</div>` : '<p class="muted">点击按钮后才会调用 AI；生成方案本身不触发 AI。</p>'}
    </section>
  `;
}

function renderLlmNotice(analysis) {
  if (!analysis?.error) return '';
  return '<p class="llm-notice">AI 服务本次不可用，已显示规则兜底分析。可以稍后重试或检查模型服务状态。</p>';
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
  const recs = currentPlan?.recommendation?.recommendations || [];
  const filtered = recs.filter(item => keep.has(candidateKey(item)));
  if (!filtered.length) return;
  currentPlan.ai_filtered_recommendations = filtered;
  currentPlan.ai_candidate_filter = {
    kept: filtered.length,
    dropped: Math.max(0, recs.length - filtered.length),
    drop: analysis.drop || [],
    warnings: analysis.warnings || [],
  };
}

function activeRecommendations() {
  return currentPlan?.ai_filtered_recommendations || currentPlan?.recommendation?.recommendations || [];
}

function renderRankStep(data) {
  const profile = data.profile || {};
  return `
    ${table([
    ['省份', profile.province || '-'],
    ['选科大类', profile.category || '-'],
    ['层次', profile.education_level || '-'],
    ['分数', profile.score || '-'],
    ['位次', profile.rank || '未填写'],
    ['核心诉求', profile.goal || '-'],
    ])}
    ${renderBatchLines(data)}
  `;
}

function renderEquivalentStep(data) {
  const eq = data.equivalent_scores || {};
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
    <table>
      <thead><tr><th>年份</th><th>科类</th><th>等位分</th><th>累计位次</th><th>同分人数</th><th>来源</th></tr></thead>
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
  const rows = activeRecommendations();
  if (pool.locked_reason) {
    return `<p class="warning-line">${escapeHtml(pool.locked_reason)}</p>`;
  }
  return `
    <div class="metric-grid">
      <div><dt>候选数</dt><dd>${escapeHtml(pool.total_recommendations ?? 0)}</dd></div>
      <div><dt>学校数</dt><dd>${escapeHtml(pool.school_count ?? 0)}</dd></div>
      <div><dt>专业数</dt><dd>${escapeHtml(pool.major_count ?? 0)}</dd></div>
      <div><dt>风险模型</dt><dd>${escapeHtml(data.strategy?.risk_model || '-')}</dd></div>
    </div>
    ${pool.score_window ? `<p class="muted">分数窗口：${escapeHtml(pool.score_window.low)}-${escapeHtml(pool.score_window.high)}（${escapeHtml(pool.score_window.rule)}）</p>` : ''}
    ${pool.rank_window ? `<p class="muted">位次窗口：${escapeHtml(pool.rank_window.chong_min)}-${escapeHtml(pool.rank_window.bao_max)}（${escapeHtml(pool.rank_window.rule)}）</p>` : ''}
    ${renderAiFilterResult(data)}
    ${renderCandidateTable(rows)}
  `;
}

function renderAiFilterResult(data) {
  const filter = data.ai_candidate_filter;
  if (!filter) return '';
  return `
    <section class="ai-filter-result">
      <strong>AI 已筛选候选池：保留 ${escapeHtml(filter.kept)} 个，剔除 ${escapeHtml(filter.dropped)} 个。</strong>
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

function renderStrategyStep(data) {
  const counts = bucketCounts(activeRecommendations());
  return `
    <div class="metric-grid">
      <div><dt>冲</dt><dd>${escapeHtml(counts['冲'] || 0)}</dd></div>
      <div><dt>稳</dt><dd>${escapeHtml(counts['稳'] || 0)}</dd></div>
      <div><dt>保</dt><dd>${escapeHtml(counts['保'] || 0)}</dd></div>
      <div><dt>模型</dt><dd>${escapeHtml(data.strategy?.risk_model || '-')}</dd></div>
    </div>
    <ul class="notes">${(data.strategy?.notes || []).map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul>
  `;
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
        <div><dt>差距</dt><dd>${escapeHtml(gapText(item, orderItem))}</dd></div>
        <div><dt>效用分</dt><dd>${escapeHtml(String(Math.round(item.plan_score ?? orderItem.plan_score ?? item.utility ?? 0)))}</dd></div>
        <div><dt>层次</dt><dd>${escapeHtml(item.education_level || '-')}</dd></div>
        <div><dt>科类</dt><dd>${escapeHtml(item.category || '-')}</dd></div>
        <div><dt>可信度</dt><dd>${confidenceLabel(item.evidence?.confidence || item.confidence)}</dd></div>
        <div><dt>证据等级</dt><dd>${escapeHtml(item.evidence_level?.label || item.evidence?.level_label || '-')}</dd></div>
      </dl>
      <p>${escapeHtml(orderItem.reason || item.note || '')}</p>
      <p class="life">${escapeHtml(item.school_life?.summary || '暂无本地生活质量摘要，可点击来源继续查。')}</p>
      <a href="${escapeHtml(item.school_life?.source_url || '#')}" target="_blank" rel="noreferrer">学校信息来源</a>
    </article>
  `;
}

function renderCharterStep(data) {
  const rows = data.ai_filtered_recommendations ? data.ai_filtered_recommendations.slice(0, 12).map(item => ({
    school_name: item.school_name,
    major_name: item.sp_name || item.major_name,
    must_check: ['选科要求', '单科成绩', '体检限制', '学费', '校区'],
    source_hint: 'AI 筛选后候选，最终填报前必须核对学校官方招生章程。',
    search_url: `https://www.baidu.com/s?wd=${encodeURIComponent((item.school_name || '') + ' 招生章程 ' + (item.sp_name || item.major_name || ''))}`,
  })) : (data.charter_checks || []);
  if (!rows.length) {
    return '<p class="warning-line">等位分不可用或没有候选结果，暂不生成核验清单。</p>';
  }
  return rows.map(item => `
    <article class="check">
      <strong>${escapeHtml(item.school_name || '')}</strong>
      <span>${escapeHtml(item.major_name || '')}</span>
      <p>待核验：${(item.must_check || []).map(escapeHtml).join('、')}</p>
      <p class="muted">${escapeHtml(item.source_hint || '')}</p>
      ${item.search_url ? `<a href="${escapeHtml(item.search_url)}" target="_blank" rel="noreferrer">检索招生章程</a>` : ''}
    </article>
  `).join('');
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
      data.unified_db_path ? `统一库路径：${data.unified_db_path}` : '',
      data.primary_gz_path ? `备用 gz 路径：${data.primary_gz_path}` : '',
      '',
      '生成 unified_admission.db 或放入 admission_clean.db.gz 后刷新页面即可继续。',
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

function initLlmConfig() {
  const saved = readStoredLlmConfig();
  if (saved.base_url) document.querySelector('#llm-base-url').value = saved.base_url;
  if (saved.model) document.querySelector('#llm-model').value = saved.model;
  if (saved.api_key) document.querySelector('#llm-key').value = saved.api_key;
  if (saved.timeout) document.querySelector('#llm-timeout').value = saved.timeout;
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
  return {
    base_url: document.querySelector('#llm-base-url')?.value.trim() || '',
    model: document.querySelector('#llm-model')?.value.trim() || '',
    api_key: document.querySelector('#llm-key')?.value.trim() || '',
    timeout: Number(document.querySelector('#llm-timeout')?.value || 60),
  };
}
