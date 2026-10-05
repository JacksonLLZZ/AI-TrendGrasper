/* AI 趋势雷达 · 前端看板逻辑
 * 数据来自 /api/items 与 /api/meta，筛选/排序/收藏/反馈均在前端完成。 */

const SECTIONS = [
  { id: 'overview', name: '趋势总览', desc: '收录规模与内容分布' },
  { id: 'tech', name: '技术前沿', desc: '面向研发：技术栈筛选与热度榜' },
  { id: 'product', name: '产品趋势', desc: '面向产品：赛道筛选与趋势方向' },
  { id: 'favorites', name: '我的收藏', desc: '待调研清单 / 机会库' },
];

const LS = {
  favorites: 'radar.favorites',
  feedback: 'radar.feedback',
  prefs: 'radar.prefs',
};

const MATURITY_LABEL = { production: '生产级', usable: '可用', experimental: '实验性', unknown: '未知' };
const TREND_LABEL = { rising: '↑ 上升', stable: '→ 平稳', cooling: '↓ 降温', unknown: '— 未知' };
const STATUS_LABEL = { success: 'AI 提取成功', partial: '部分字段缺失', fallback: '规则兜底' };

const state = {
  items: [],
  meta: null,
  favorites: new Set(),
  feedback: {},
  prefs: { order: SECTIONS.map((s) => s.id), visible: {} },
  filters: {
    q: '',
    techTags: new Set(),
    techSort: 'stars',
    techMaturity: '',
    techDomain: '',
    productDomain: '',
    productTrend: '',
  },
};

/* ---------------- 工具函数 ---------------- */

const $ = (sel) => document.querySelector(sel);

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatStars(n) {
  const num = Number(n) || 0;
  if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
  return String(num);
}

function formatDate(value) {
  if (!value) return '未知';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '未知';
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function relativeTime(value) {
  if (!value) return '未知';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '未知';
  const diffDays = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (diffDays <= 0) return '今天';
  if (diffDays === 1) return '昨天';
  if (diffDays < 30) return `${diffDays} 天前`;
  return formatDate(value);
}

function loadLocal() {
  try {
    state.favorites = new Set(JSON.parse(localStorage.getItem(LS.favorites) || '[]'));
    state.feedback = JSON.parse(localStorage.getItem(LS.feedback) || '{}');
    const prefs = JSON.parse(localStorage.getItem(LS.prefs) || 'null');
    if (prefs && Array.isArray(prefs.order)) {
      const known = prefs.order.filter((id) => SECTIONS.some((s) => s.id === id));
      const missing = SECTIONS.map((s) => s.id).filter((id) => !known.includes(id));
      state.prefs = { order: [...known, ...missing], visible: prefs.visible || {} };
    }
  } catch {
    /* 本地数据损坏则忽略，使用默认值 */
  }
}

function saveFavorites() {
  localStorage.setItem(LS.favorites, JSON.stringify([...state.favorites]));
}

function saveFeedback() {
  localStorage.setItem(LS.feedback, JSON.stringify(state.feedback));
}

function savePrefs() {
  localStorage.setItem(LS.prefs, JSON.stringify(state.prefs));
}

/* ---------------- 数据加载 ---------------- */

async function fetchJson(url) {
  const resp = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!resp.ok) throw new Error(`${url} 返回 ${resp.status}`);
  return resp.json();
}

async function loadData() {
  const [itemsRes, metaRes] = await Promise.all([
    fetchJson('/api/items?ts=' + Date.now()),
    fetchJson('/api/meta'),
  ]);
  state.items = itemsRes.items || [];
  state.meta = metaRes;
}

/* ---------------- 筛选与排序 ---------------- */

const isTechItem = (it) => it.audience.includes('technical') || it.audience.length === 0;
const isProductItem = (it) => it.audience.includes('product') || !!it.product_use_case;

function matchesSearch(item) {
  const q = state.filters.q.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    item.name, item.full_name, item.summary, item.description,
    item.category, item.product_use_case,
    ...(item.tags || []), ...(item.highlights || []),
  ].join(' ').toLowerCase();
  return haystack.includes(q);
}

function sortItems(items, mode) {
  const sorted = [...items];
  if (mode === 'delta') {
    sorted.sort((a, b) => b.stars_delta_7d - a.stars_delta_7d);
  } else if (mode === 'updated') {
    sorted.sort((a, b) => new Date(b.last_updated || 0) - new Date(a.last_updated || 0));
  } else {
    sorted.sort((a, b) => b.stars - a.stars);
  }
  return sorted;
}

/* ---------------- 渲染：卡片 ---------------- */

function badgeHtml(item, mode) {
  const badges = [];
  const status = item.extraction?.status;

  if (mode === 'tech') {
    badges.push(`<span class="badge maturity-${item.maturity}">${MATURITY_LABEL[item.maturity] || '未知'}</span>`);
  } else if (mode === 'product') {
    const cls = item.trend === 'rising' ? 'trend-rising' : item.trend === 'cooling' ? 'trend-cooling' : '';
    badges.push(`<span class="badge ${cls}">${TREND_LABEL[item.trend] || '— 未知'}</span>`);
  }

  if (!item.has_readme) badges.push('<span class="badge warn">信息有限</span>');
  if (status === 'fallback') badges.push('<span class="badge warn">规则兜底</span>');
  else if (status === 'partial') badges.push('<span class="badge warn">部分字段缺失</span>');

  return badges.join('');
}

function cardHtml(item, mode = 'tech') {
  const faved = state.favorites.has(item.id);
  const vote = state.feedback[item.id];

  const highlights = (item.highlights || [])
    .map((h) => `<li>${escapeHtml(h)}</li>`)
    .join('');

  const tags = (item.tags || [])
    .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
    .join('');

  const delta = item.stars_delta_7d;
  const deltaHtml = delta
    ? `<span class="${delta > 0 ? 'delta-up' : 'delta-down'}">${delta > 0 ? '+' : ''}${delta}/7d</span>`
    : '';

  const useCase = item.product_use_case
    ? `<div class="card-usecase"><b>产品机会</b><br>${escapeHtml(item.product_use_case)}</div>`
    : '';

  return `
    <article class="card" data-id="${escapeHtml(item.id)}">
      <div class="card-top">
        <div>
          <a class="card-title" href="#/item/${encodeURIComponent(item.id)}">${escapeHtml(item.name)}</a>
          <div class="card-repo">${escapeHtml(item.full_name)} · ${escapeHtml(item.domain)}</div>
        </div>
        <div class="badges">${badgeHtml(item, mode)}</div>
      </div>

      <p class="card-summary">${escapeHtml(item.summary)}</p>
      ${highlights ? `<ul class="card-highlights">${highlights}</ul>` : ''}
      ${mode === 'product' ? useCase : ''}
      ${tags ? `<div class="card-tags">${tags}</div>` : ''}

      <div class="card-meta">
        <span class="star">★ ${formatStars(item.stars)}</span>
        ${deltaHtml}
        <span>${escapeHtml(item.language || '未知语言')}</span>
        <span>${escapeHtml(item.license || '无 License')}</span>
        <span>更新于 ${relativeTime(item.last_updated)}</span>
      </div>

      <div class="card-actions">
        <a class="icon-btn" href="#/item/${encodeURIComponent(item.id)}">总结报告 →</a>
        <button class="icon-btn js-fav ${faved ? 'active' : ''}" type="button">${faved ? '★ 已收藏' : '☆ 收藏'}</button>
        <button class="icon-btn js-vote ${vote === 'useful' ? 'active' : ''}" data-vote="useful" type="button">有用</button>
        <button class="icon-btn js-vote ${vote === 'useless' ? 'active' : ''}" data-vote="useless" type="button">没用</button>
        <span class="spacer"></span>
        <a class="icon-btn" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">查看源码 ↗</a>
      </div>
    </article>`;
}

function renderGrid(el, items, mode) {
  el.innerHTML = items.map((it) => cardHtml(it, mode)).join('');
}

/* ---------------- 渲染：总览 ---------------- */

function renderOverview() {
  const items = state.items;
  const stats = [
    { label: '收录项目', value: items.length },
    { label: '知识领域', value: new Set(items.map((i) => i.domain)).size },
    { label: '技术关键词', value: new Set(items.flatMap((i) => i.tags)).size },
    {
      label: '高置信占比',
      value: items.length
        ? Math.round((items.filter((i) => (i.extraction?.confidence || 0) >= 0.8).length / items.length) * 100) + '%'
        : '—',
    },
  ];

  $('#stats-grid').innerHTML = stats
    .map((s) => `<div class="stat-card"><div class="stat-value">${s.value}</div><div class="stat-label">${s.label}</div></div>`)
    .join('');

  // 一级分类：知识领域分布
  const domains = state.meta?.domains || [];
  const max = Math.max(1, ...domains.map(([, c]) => c));
  $('#domain-bars').innerHTML = domains
    .map(([name, count]) => `
      <div class="bar-row">
        <span class="bar-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(count / max) * 100}%"></span></span>
        <span class="bar-value">${count}</span>
      </div>`)
    .join('') || '<span class="tag">暂无数据</span>';

  // 二级分类：各知识领域下的热门技术关键词
  const domainTags = state.meta?.domain_tags || {};
  $('#domain-tags').innerHTML = domains
    .map(([name]) => {
      const chips = (domainTags[name] || [])
        .map(([tag, count]) => `<span class="tag">${escapeHtml(tag)} · ${count}</span>`)
        .join('');
      return `
        <div class="domain-group">
          <div class="domain-group-name">${escapeHtml(name)}</div>
          <div class="tag-cloud">${chips || '<span class="tag">暂无关键词</span>'}</div>
        </div>`;
    })
    .join('') || '<span class="tag">暂无数据</span>';
}

/* ---------------- 渲染：技术前沿 ---------------- */

function renderTechTagChips() {
  const counts = new Map();
  state.items.filter(isTechItem).forEach((it) => {
    (it.tags || []).forEach((t) => counts.set(t, (counts.get(t) || 0) + 1));
  });
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);

  $('#tech-tags').innerHTML = top
    .map(([tag, count]) => {
      const active = state.filters.techTags.has(tag) ? 'active' : '';
      return `<button class="chip ${active}" data-tag="${escapeHtml(tag)}" type="button">${escapeHtml(tag)} · ${count}</button>`;
    })
    .join('');
}

function techItems() {
  let items = state.items.filter(isTechItem).filter(matchesSearch);
  if (state.filters.techDomain) {
    items = items.filter((it) => it.domain === state.filters.techDomain);
  }
  if (state.filters.techTags.size) {
    items = items.filter((it) => (it.tags || []).some((t) => state.filters.techTags.has(t)));
  }
  if (state.filters.techMaturity) {
    items = items.filter((it) => it.maturity === state.filters.techMaturity);
  }
  return sortItems(items, state.filters.techSort);
}

/* ---------------- 渲染：产品趋势 ---------------- */

/* 一级分类下拉：知识领域（技术 / 产品两个分区共用） */
function renderDomainSelects() {
  const domains = state.meta?.domains || [];
  const options = domains
    .map(([name, count]) => `<option value="${escapeHtml(name)}">${escapeHtml(name)} (${count})</option>`)
    .join('');

  const techSelect = $('#tech-domain');
  techSelect.innerHTML = '<option value="">全部知识领域</option>' + options;
  techSelect.value = state.filters.techDomain;

  const productSelect = $('#product-domain');
  productSelect.innerHTML = '<option value="">全部知识领域</option>' + options;
  productSelect.value = state.filters.productDomain;
}

function productItems() {
  let items = state.items.filter(isProductItem).filter(matchesSearch);
  if (state.filters.productDomain) items = items.filter((it) => it.domain === state.filters.productDomain);
  if (state.filters.productTrend) items = items.filter((it) => it.trend === state.filters.productTrend);
  return sortItems(items, 'delta');
}

/* ---------------- 渲染：收藏 ---------------- */

function favoriteItems() {
  return sortItems(state.items.filter((it) => state.favorites.has(it.id)).filter(matchesSearch), 'stars');
}

/* ---------------- 渲染：分区可见性与顺序 ---------------- */

function renderSectionSettings() {
  $('#section-list').innerHTML = state.prefs.order
    .map((id, index) => {
      const sec = SECTIONS.find((s) => s.id === id);
      if (!sec) return '';
      const visible = state.prefs.visible[id] !== false;
      return `
        <li data-id="${id}">
          <label>
            <input type="checkbox" class="js-sec-visible" data-id="${id}" ${visible ? 'checked' : ''} />
            <span>
              <strong>${escapeHtml(sec.name)}</strong>
              <span class="sec-desc"> · ${escapeHtml(sec.desc)}</span>
            </span>
          </label>
          <span class="sec-actions">
            <button class="js-move" data-id="${id}" data-dir="-1" type="button" ${index === 0 ? 'disabled' : ''}>↑</button>
            <button class="js-move" data-id="${id}" data-dir="1" type="button" ${index === state.prefs.order.length - 1 ? 'disabled' : ''}>↓</button>
          </span>
        </li>`;
    })
    .join('');
}

function applySectionLayout() {
  const main = $('#main');
  const empty = $('#empty-state');

  // 按用户设定顺序重排分区
  state.prefs.order.forEach((id) => {
    const el = document.getElementById('sec-' + id);
    if (el) main.appendChild(el);
  });
  main.appendChild(empty);

  // 可见性
  SECTIONS.forEach((sec) => {
    const el = document.getElementById('sec-' + sec.id);
    if (el) el.hidden = state.prefs.visible[sec.id] === false;
  });
}

/* ---------------- 主渲染 ---------------- */

function renderAll() {
  renderOverview();

  renderTechTagChips();
  const tech = techItems();
  $('#tech-count').textContent = tech.length;
  renderGrid($('#tech-grid'), tech, 'tech');

  renderDomainSelects();
  const product = productItems();
  $('#product-count').textContent = product.length;
  renderGrid($('#product-grid'), product, 'product');

  const favs = favoriteItems();
  $('#fav-count').textContent = favs.length;
  $('#fav-grid').innerHTML = favs.length
    ? favs.map((it) => cardHtml(it, 'tech')).join('')
    : '<div class="empty-state">还没有收藏。点击卡片上的「☆ 收藏」加入待调研清单 / 机会库。</div>';

  applySectionLayout();

  const anyVisible = tech.length + product.length + favs.length > 0;
  $('#empty-state').hidden = anyVisible;

  renderMetaInfo();
}

function renderMetaInfo() {
  const meta = state.meta;
  const updated = meta?.last_updated ? relativeTime(meta.last_updated) : '未知';
  $('#meta-updated').textContent = `数据更新：${updated}`;
  $('#meta-dot').className = 'meta-dot ' + (meta?.capabilities?.github_enabled ? 'ok' : 'warn');

  const cap = meta?.capabilities || {};
  const mode = cap.github_enabled ? '真实抓取模式' : '示例数据模式';
  const llm = cap.llm_enabled ? 'LLM 已配置' : 'LLM 未配置（规则兜底）';
  const statuses = meta?.statuses || {};
  const fallback = statuses.fallback || 0;
  $('#footer-note').textContent =
    `数据来源：${mode} · ${llm} · 共 ${meta?.total ?? 0} 条` +
    (fallback ? ` · 其中 ${fallback} 条为规则兜底` : '') +
    ' · 收藏与反馈保存在浏览器本地';
}

/* ---------------- 第二层信息：总结报告详情（hash 路由 #/item/{id}） ---------------- */

let currentDetailId = null;

function parseRoute() {
  const match = location.hash.match(/^#\/item\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function detailSkeletonHtml(item) {
  const name = item ? escapeHtml(item.name) : '正在加载…';
  return `
    <a class="back-link" href="#">← 返回看板</a>
    <header class="detail-head"><div><h2>${name}</h2><div class="detail-sub">正在生成总结报告…</div></div></header>
    <div class="skeleton-note">正在调用大模型阅读并分析该项目（首次生成可能需要数秒；未配置大模型时返回规则兜底报告）。</div>
    ${'<div class="skeleton-block"></div>'.repeat(4)}`;
}

function detailHtml(data) {
  const item = data.item;
  const report = data.report || {};
  const sections = (report.sections || [])
    .map((sec) => {
      const paragraphs = (sec.paragraphs || []).map((p) => `<p>${escapeHtml(p)}</p>`).join('');
      const bullets = (sec.bullets || []).length
        ? `<ul>${sec.bullets.map((b) => `<li>${escapeHtml(b)}</li>`).join('')}</ul>`
        : '';
      return `<section class="report-section"><h3>${escapeHtml(sec.title)}</h3>${paragraphs}${bullets}</section>`;
    })
    .join('');

  const statusBadge = report.status === 'success'
    ? '<span class="badge maturity-production">AI 分析完成</span>'
    : report.status === 'partial'
      ? '<span class="badge warn">部分内容需复核</span>'
      : '<span class="badge warn">规则兜底（未配置大模型）</span>';

  const tags = (item.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');

  return `
    <a class="back-link" href="#">← 返回看板</a>

    <header class="detail-head">
      <div>
        <h2>${escapeHtml(item.name)}</h2>
        <div class="detail-sub">${escapeHtml(item.full_name)} · ${escapeHtml(item.domain)}</div>
      </div>
      <div class="badges">${badgeHtml(item, 'tech')}</div>
    </header>

    <div class="detail-meta">
      <span class="star">★ ${formatStars(item.stars)}</span>
      <span>${escapeHtml(item.language || '未知语言')}</span>
      <span>${escapeHtml(item.license || '无 License')}</span>
      <span>更新于 ${relativeTime(item.last_updated)}</span>
      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">查看源码 ↗</a>
    </div>

    <div class="detail-tags">${tags}</div>

    <div class="report-note">
      ${statusBadge}
      <span class="report-note-text">${data.from_cache ? '命中缓存' : '本次新生成'} · 模型：${escapeHtml(report.model || '-')}</span>
    </div>

    <div class="report-body">${sections || '<p>暂无报告内容。</p>'}</div>`;
}

async function handleRoute() {
  const id = parseRoute();
  const detail = $('#detail-view');
  const sections = document.querySelectorAll('.board-section');
  const empty = $('#empty-state');

  // 返回看板
  if (!id) {
    currentDetailId = null;
    detail.hidden = true;
    detail.innerHTML = '';
    sections.forEach((s) => { s.hidden = state.prefs.visible[s.id.replace('sec-', '')] === false; });
    empty.hidden = true;
    applySectionLayout();
    window.scrollTo({ top: 0 });
    return;
  }

  if (currentDetailId === id && !detail.hidden) return;
  currentDetailId = id;

  const item = state.items.find((it) => it.id === id) || null;
  sections.forEach((s) => { s.hidden = true; });
  empty.hidden = true;
  detail.hidden = false;
  detail.innerHTML = detailSkeletonHtml(item);
  window.scrollTo({ top: 0 });

  try {
    const data = await fetchJson(`/api/analysis/${encodeURIComponent(id)}`);
    if (currentDetailId !== id) return; // 加载期间已切换
    detail.innerHTML = detailHtml(data);
  } catch (err) {
    if (currentDetailId !== id) return;
    detail.innerHTML = `<a class="back-link" href="#">← 返回看板</a>
      <div class="empty-state">报告生成失败：${escapeHtml(String(err))}</div>`;
  }
}

/* ---------------- 事件绑定 ---------------- */

function bindEvents() {
  $('#search-input').addEventListener('input', (e) => {
    state.filters.q = e.target.value;
    renderAll();
  });

  $('#btn-settings').addEventListener('click', () => {
    const panel = $('#settings-panel');
    panel.hidden = !panel.hidden;
    if (!panel.hidden) renderSectionSettings();
  });

  $('#btn-refresh').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = '刷新中…';
    try {
      await loadData();
      renderAll();
    } catch (err) {
      console.error(err);
    } finally {
      btn.disabled = false;
      btn.textContent = '刷新';
    }
  });

  // 技术分区：标签 chip
  $('#tech-tags').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    const tag = chip.dataset.tag;
    if (state.filters.techTags.has(tag)) state.filters.techTags.delete(tag);
    else state.filters.techTags.add(tag);
    renderAll();
  });

  $('#tech-sort').addEventListener('change', (e) => {
    state.filters.techSort = e.target.value;
    renderAll();
  });

  $('#tech-maturity').addEventListener('change', (e) => {
    state.filters.techMaturity = e.target.value;
    renderAll();
  });

  $('#tech-domain').addEventListener('change', (e) => {
    state.filters.techDomain = e.target.value;
    renderAll();
  });

  $('#product-domain').addEventListener('change', (e) => {
    state.filters.productDomain = e.target.value;
    renderAll();
  });

  $('#product-trend').addEventListener('change', (e) => {
    state.filters.productTrend = e.target.value;
    renderAll();
  });

  // 卡片操作（事件委托）
  document.addEventListener('click', (e) => {
    const card = e.target.closest('.card');
    if (!card) return;
    const id = card.dataset.id;

    if (e.target.closest('.js-fav')) {
      if (state.favorites.has(id)) state.favorites.delete(id);
      else state.favorites.add(id);
      saveFavorites();
      renderAll();
      return;
    }

    const voteBtn = e.target.closest('.js-vote');
    if (voteBtn) {
      const vote = voteBtn.dataset.vote;
      state.feedback[id] = state.feedback[id] === vote ? undefined : vote;
      saveFeedback();
      postFeedback(id, vote);
      renderAll();
      return;
    }

    // 点击卡片其他区域 -> 进入第二层「总结报告」界面
    if (e.target.closest('a, button')) return;
    location.hash = `#/item/${encodeURIComponent(id)}`;
  });

  // 路由：两级信息结构切换
  window.addEventListener('hashchange', handleRoute);

  // 分区设置面板
  $('#section-list').addEventListener('change', (e) => {
    if (!e.target.classList.contains('js-sec-visible')) return;
    const id = e.target.dataset.id;
    state.prefs.visible[id] = e.target.checked;
    savePrefs();
    applySectionLayout();
  });

  $('#section-list').addEventListener('click', (e) => {
    const btn = e.target.closest('.js-move');
    if (!btn) return;
    const { id, dir } = btn.dataset;
    const order = state.prefs.order;
    const index = order.indexOf(id);
    const target = index + Number(dir);
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    savePrefs();
    renderSectionSettings();
    applySectionLayout();
  });

  $('#btn-settings-reset').addEventListener('click', () => {
    state.prefs = { order: SECTIONS.map((s) => s.id), visible: {} };
    savePrefs();
    renderSectionSettings();
    applySectionLayout();
  });
}

async function postFeedback(id, vote) {
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: id, vote }),
    });
  } catch {
    /* 反馈上报失败不影响本地使用 */
  }
}

/* ---------------- 启动 ---------------- */

async function init() {
  loadLocal();
  bindEvents();
  try {
    await loadData();
    renderAll();
    await handleRoute();
  } catch (err) {
    console.error(err);
    $('#empty-state').hidden = false;
    $('#empty-state').textContent = '数据加载失败，请检查后端服务是否正常。';
  }
}

init();