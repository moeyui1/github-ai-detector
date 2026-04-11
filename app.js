window.loadScriptOnce = (function() {
  var pending = {};
  return function(src, globalName) {
    if (globalName && window[globalName]) return Promise.resolve(window[globalName]);
    if (pending[src]) return pending[src];
    pending[src] = new Promise(function(resolve, reject) {
      var script = document.createElement('script');
      function fail(message) {
        delete pending[src];
        reject(new Error(message));
      }
      script.async = true;
      script.src = src;
      script.onload = function() {
        if (!globalName || window[globalName]) {
          resolve(globalName ? window[globalName] : true);
          return;
        }
        fail('Script loaded from ' + src + ' without expected global: ' + globalName);
      };
      script.onerror = function() {
        fail('Failed to load script: ' + src);
      };
      document.head.appendChild(script);
    });
    return pending[src];
  };
})();

document.addEventListener('DOMContentLoaded', function() {
  // Assign section IDs from data attributes (deferred to prevent browser anchor jump)
  document.querySelectorAll('[data-section-id]').forEach(function(el) {
    el.id = el.getAttribute('data-section-id');
  });
  window.scrollTo(0, 0);

  var body = document.body;
  var sidebar = document.getElementById('sidebar');
  var links = sidebar ? Array.prototype.slice.call(sidebar.querySelectorAll('nav a[data-repo]')) : [];
  var sections = Array.prototype.slice.call(document.querySelectorAll('.repo-section'));
  var menuToggle = document.getElementById('menu-toggle');
  var overlay = document.getElementById('sidebar-overlay');
  var navItems = sidebar ? sidebar.querySelectorAll('.nav-item') : [];
  var rankToggle = document.getElementById('rank-toggle');
  var collapsedRankings = document.getElementById('rank-collapsed');
  var sectionMarkupCache = {};
  var sectionMarkupPromises = {};
  var echartsPromise = null;

  function setMenuExpanded(isExpanded) {
    if (!menuToggle) return;
    var menuIcon = menuToggle.querySelector('.icon-menu');
    var closeIcon = menuToggle.querySelector('.icon-close');
    menuToggle.setAttribute('aria-expanded', String(isExpanded));
    if (menuIcon) menuIcon.classList.toggle('hidden', isExpanded);
    if (closeIcon) closeIcon.classList.toggle('hidden', !isExpanded);
  }

  // ── Mobile menu ──
  function closeMobile() {
    if (sidebar) sidebar.classList.add('max-md:-translate-x-full');
    if (overlay) overlay.classList.add('hidden');
    body.classList.remove('overflow-hidden');
    setMenuExpanded(false);
  }
  if (menuToggle) {
    setMenuExpanded(false);
    menuToggle.addEventListener('click', function() {
      var isOpen = !sidebar.classList.contains('max-md:-translate-x-full');
      if (isOpen) {
        closeMobile();
      } else {
        sidebar.classList.remove('max-md:-translate-x-full');
        overlay.classList.remove('hidden');
        body.classList.add('overflow-hidden');
        setMenuExpanded(true);
      }
    });
  }
  if (overlay) overlay.addEventListener('click', closeMobile);

  // ── Collapse all nav-items ──
  function collapseAll() {
    navItems.forEach(function(ni) { ni.classList.remove('expanded'); });
  }

  function setActiveLink(id, options) {
    links.forEach(function(l) {
      l.classList.remove('!opacity-100', '!border-l-blue-600', '!bg-beige-200/60');
      l.classList.add('opacity-60');
    });
    var link = sidebar ? sidebar.querySelector('a[data-repo="' + id + '"]') : null;
    if (link) {
      link.classList.add('!opacity-100', '!border-l-blue-600', '!bg-beige-200/60');
      link.classList.remove('opacity-60');
      collapseAll();
      var parentNavItem = link.closest('.nav-item');
      if (parentNavItem) parentNavItem.classList.add('expanded');
      link.scrollIntoView({ block: 'nearest', behavior: options && options.instant ? 'auto' : 'smooth' });
    }
  }

  function getFragmentPath(section) {
    return section ? section.getAttribute('data-fragment') || '' : '';
  }

  function fetchSectionMarkup(section) {
    var fragmentPath = getFragmentPath(section);
    if (!fragmentPath) return Promise.resolve('');
    if (sectionMarkupCache[fragmentPath]) return Promise.resolve(sectionMarkupCache[fragmentPath]);
    if (sectionMarkupPromises[fragmentPath]) return sectionMarkupPromises[fragmentPath];

    sectionMarkupPromises[fragmentPath] = fetch(fragmentPath, { credentials: 'same-origin' }).then(function(response) {
      if (!response.ok) {
        throw new Error('Failed to load section fragment: ' + fragmentPath);
      }
      return response.text();
    }).then(function(markup) {
      sectionMarkupCache[fragmentPath] = markup;
      delete sectionMarkupPromises[fragmentPath];
      return markup;
    }).catch(function(error) {
      delete sectionMarkupPromises[fragmentPath];
      throw error;
    });

    return sectionMarkupPromises[fragmentPath];
  }

  function ensureSectionContent(section) {
    if (!section || section.dataset.loaded === 'true' || !getFragmentPath(section)) {
      return Promise.resolve(section);
    }

    section.setAttribute('aria-busy', 'true');
    section.dataset.loading = 'true';

    return fetchSectionMarkup(section).then(function(markup) {
      section.innerHTML = markup;
      section.dataset.loaded = 'true';
      delete section.dataset.loading;
      section.removeAttribute('aria-busy');
      return section;
    }).catch(function(error) {
      delete section.dataset.loading;
      section.dataset.loaded = 'error';
      section.removeAttribute('aria-busy');
      section.innerHTML = '<div class="repo-section-placeholder rounded-xl border border-dashed border-beige-200 bg-white/80 p-5 text-center text-sm text-error shadow-sm"><p>Repository detail failed to load. Click again to retry.</p></div>';
      throw error;
    });
  }

  function ensureECharts() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (!echartsPromise) {
      echartsPromise = window.loadScriptOnce('https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js', 'echarts');
    }
    return echartsPromise;
  }

  function showChartFallback(el) {
    el.innerHTML = '<p class="text-center text-sm text-base-content/60 py-10">Trend chart unavailable right now.</p>';
    el.dataset.chartReady = 'error';
  }

  function renderChart(el) {
    if (!el || el.dataset.chartReady === 'true' || el.dataset.chartLoading === 'true') return;
    el.dataset.chartLoading = 'true';

    ensureECharts().then(function() {
      var dates = JSON.parse(el.getAttribute('data-dates') || '[]');
      var values = JSON.parse(el.getAttribute('data-values') || '[]');
      var color = el.getAttribute('data-color') || '#2563eb';
      var name = el.getAttribute('data-name') || 'AII';
      if (!dates.length || !values.length) {
        showChartFallback(el);
        return;
      }

      var chart = echarts.init(el, null, { renderer: 'canvas' });
      chart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#fffdf9',
          borderColor: '#d9cfc3',
          borderRadius: 8,
          padding: [10, 14],
          textStyle: { color: '#1f2937', fontSize: 13, fontFamily: 'DM Sans, sans-serif' },
          formatter: function(params) {
            var p = params[0];
            return '<b>' + p.name + '</b><br/><span style="color:' + color + '">●</span> ' + name + ': <b>' + p.value.toFixed(2) + '%</b>';
          }
        },
        grid: { left: 48, right: 16, top: 16, bottom: 32 },
        xAxis: {
          type: 'category',
          data: dates,
          axisLine: { lineStyle: { color: '#e2dace' } },
          axisTick: { show: false },
          axisLabel: {
            color: '#9ca3af',
            fontSize: 11,
            fontFamily: 'DM Mono, monospace',
            interval: Math.max(0, Math.floor(dates.length / 6) - 1),
            formatter: function(v) { return v.slice(5); }
          }
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: '#f0ebe4', type: 'dashed' } },
          axisLabel: {
            color: '#9ca3af',
            fontSize: 11,
            fontFamily: 'DM Mono, monospace',
            formatter: '{value}%'
          }
        },
        series: [{
          type: 'line',
          data: values,
          smooth: 0.3,
          symbol: 'circle',
          symbolSize: 7,
          lineStyle: { color: color, width: 2.5 },
          itemStyle: { color: color, borderColor: '#fff', borderWidth: 2 },
          emphasis: { itemStyle: { borderWidth: 3, shadowBlur: 8, shadowColor: color + '44' } },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: color + '30' },
            { offset: 0.6, color: color + '10' },
            { offset: 1, color: color + '02' }
          ]) }
        }]
      });
      el.dataset.chartReady = 'true';
      delete el.dataset.chartLoading;
      if (window.ResizeObserver) {
        new ResizeObserver(function() { chart.resize(); }).observe(el);
      }
    }).catch(function(err) {
      console.error(err);
      showChartFallback(el);
    });
  }

  function ensureChartsFor(section) {
    if (!section) return;
    section.querySelectorAll('.echart-container').forEach(renderChart);
  }

  function scheduleSectionWarmup() {
    var prefetchIds = links.map(function(link) {
      return link.getAttribute('data-repo');
    }).filter(function(id) {
      return id && id !== 'summary';
    }).slice(0, 3);

    if (!prefetchIds.length) return;

    function warmup() {
      prefetchIds.forEach(function(id, index) {
        setTimeout(function() {
          var section = document.getElementById(id);
          if (!section || section.dataset.loaded === 'true') return;
          fetchSectionMarkup(section).catch(function() { return null; });
        }, index * 180);
      });
    }

    if ('requestIdleCallback' in window) {
      window.requestIdleCallback(warmup, { timeout: 1200 });
    } else {
      setTimeout(warmup, 900);
    }
  }

  // ── Section switching ──
  function show(id, push, options) {
    sections.forEach(function(s) { s.classList.remove('active'); });
    var target = document.getElementById(id);
    if (!target && sections.length > 0) target = sections[0];
    if (target) target.classList.add('active');
    if (!target) return;
    setActiveLink(target.id, options);
    ensureSectionContent(target).then(function(section) {
      if (section && section.classList.contains('active')) ensureChartsFor(section);
    }).catch(function(err) {
      console.error(err);
    });
    if (push) {
      history.pushState({ section: target.id }, '', '#' + target.id);
    }
    if (!(options && options.skipScroll)) {
      window.scrollTo({ top: 0, behavior: options && options.instant ? 'auto' : 'smooth' });
    }
    closeMobile();
  }

  links.forEach(function(link) {
    link.addEventListener('click', function(e) {
      e.preventDefault();
      show(this.getAttribute('data-repo'), true);
    });
  });

  // Ranking items click → navigate to repo detail
  document.querySelectorAll('.rank-item[data-target]').forEach(function(item) {
    item.addEventListener('click', function() {
      show(this.getAttribute('data-target'), true);
    });
  });

  // Detail table repo links → navigate to repo detail
  document.querySelectorAll('a[href^="#"]').forEach(function(a) {
    if (a.hasAttribute('data-repo')) return;   // already handled above
    var target = a.getAttribute('href').slice(1);
    if (target && document.getElementById(target)) {
      a.addEventListener('click', function(e) {
        e.preventDefault();
        show(target, true);
      });
    }
  });

  // ── Browser back/forward ──
  window.addEventListener('popstate', function(e) {
    var id = (e.state && e.state.section) || location.hash.slice(1);
    if (id && document.getElementById(id)) {
      show(id, false, { skipScroll: true });
    } else if (sections.length > 0) {
      show(sections[0].id, false, { skipScroll: true });
    }
  });

  // Show section from hash or default to summary
  // Use scrollTo instant (no smooth) on first load to prevent visible scroll jump
  var hash = location.hash.slice(1);
  if (hash && document.getElementById(hash)) {
    show(hash, false, { skipScroll: true, instant: true });
  } else if (sections.length > 0) {
    show(sections[0].id, false, { skipScroll: true, instant: true });
  }
  window.scrollTo({ top: 0, behavior: 'auto' });

  if (rankToggle && collapsedRankings) {
    rankToggle.addEventListener('click', function() {
      var isExpanded = collapsedRankings.classList.contains('hidden');
      collapsedRankings.classList.toggle('hidden', !isExpanded);
      rankToggle.textContent = isExpanded ? rankToggle.getAttribute('data-label-collapse') : rankToggle.getAttribute('data-label-expand');
      rankToggle.setAttribute('aria-expanded', String(isExpanded));
    });
    rankToggle.setAttribute('aria-expanded', 'false');
  }

  scheduleSectionWarmup();

  // ── Animate bars on load ──
  requestAnimationFrame(function() {
    document.querySelectorAll('.rank-bar-fill').forEach(function(bar, i) {
      setTimeout(function() {
        bar.value = parseInt(bar.getAttribute('data-width')) || 0;
      }, i * 60);
    });
  });
});

// ── Share ranking as image ──
function shareRanking() {
  var btn = document.getElementById('share-btn');
  if (!btn || btn.disabled) return;

  // Open modal with loading state
  var modal = document.getElementById('share-modal');
  var loading = document.getElementById('share-loading');
  var preview = document.getElementById('share-preview');
  if (!modal || !loading || !preview) return;
  var loadingText = loading.querySelector('p');
  var loadingSpinner = loading.querySelector('.loading');
  btn.disabled = true;
  btn.classList.add('loading', 'loading-spinner');
  if (loadingSpinner) loadingSpinner.classList.remove('hidden');
  loading.classList.remove('hidden');
  preview.classList.add('hidden');
  if (loadingText) loadingText.textContent = 'Generating image…';
  modal.showModal();

  var siteUrlEl = document.getElementById('share-site-url');
  var siteUrl = siteUrlEl ? siteUrlEl.getAttribute('data-url') : 'http://localhost:8080';

  window.loadScriptOnce('https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js', 'qrcode').then(function() {
    // Collect ranking data from visible cards
    var cards = document.querySelectorAll('#summary .space-y-2.mb-8 > div:not(#rank-collapsed)');
    var items = [];
    cards.forEach(function(card) {
      var rankEl = card.querySelector('.font-mono.text-base');
      var nameEl = card.querySelector('.font-bold.text-sm.truncate');
      var scoreEl = card.querySelector('.font-extrabold.font-mono.text-lg');
      var avatarEl = card.querySelector('img.w-10');
      if (rankEl && nameEl && scoreEl) {
        items.push({
          rank: rankEl.textContent.trim(),
          name: nameEl.textContent.trim(),
          score: scoreEl.textContent.trim(),
          avatarUrl: avatarEl ? avatarEl.src : '',
        });
      }
    });

    // Get date
    var dateEl = document.querySelector('#summary p');
    var dateMatch = dateEl ? dateEl.textContent.match(/\d{4}-\d{2}-\d{2}/) : null;
    var dateStr = dateMatch ? dateMatch[0] : 'today';

    // Preload avatar images, then draw
    var loadCount = 0;
    var totalToLoad = items.length;
    items.forEach(function(item) {
      if (!item.avatarUrl) { item.avatarImg = null; loadCount++; checkDraw(); return; }
      var img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function() { item.avatarImg = img; loadCount++; checkDraw(); };
      img.onerror = function() { item.avatarImg = null; loadCount++; checkDraw(); };
      img.src = item.avatarUrl;
    });
    if (totalToLoad === 0) drawShareImage(items, dateStr, siteUrl, btn);

    function checkDraw() {
      if (loadCount >= totalToLoad) drawShareImage(items, dateStr, siteUrl, btn);
    }
  }).catch(function() {
    if (loadingSpinner) loadingSpinner.classList.add('hidden');
    if (loadingText) loadingText.textContent = 'Unable to load sharing tools. Close this dialog and try again.';
    btn.disabled = false;
    btn.classList.remove('loading', 'loading-spinner');
  });
}

function drawShareImage(items, dateStr, siteUrl, btn) {
  var dpr = 2;
  var W = 540 * dpr;
  var padX = 28 * dpr;

  // Layout measurements
  var headerH = 100 * dpr;
  var podiumH = 240 * dpr;       // top-3 podium zone
  var restCardH = 48 * dpr;
  var restGap = 6 * dpr;
  var restCount = Math.max(0, items.length - 3);
  var restZoneH = restCount > 0 ? (24 * dpr + restCount * (restCardH + restGap)) : 0;
  var footerH = 110 * dpr;
  var totalH = headerH + podiumH + restZoneH + footerH + 40 * dpr;
  var contentW = W - padX * 2;

  var canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = totalH;
  var ctx = canvas.getContext('2d');

  // ── Background ──
  ctx.fillStyle = '#f0ebe4';
  ctx.fillRect(0, 0, W, totalH);

  // ── Dark header banner ──
  var grd = ctx.createLinearGradient(0, 0, W, headerH);
  grd.addColorStop(0, '#1e293b');
  grd.addColorStop(1, '#334155');
  ctx.fillStyle = grd;
  ctx.fillRect(0, 0, W, headerH);

  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold ' + (26 * dpr) + 'px "DM Sans", system-ui, sans-serif';
  ctx.fillText('\ud83d\udef8 AI Involvement Ranking', padX, 46 * dpr);
  ctx.fillStyle = 'rgba(255,255,255,0.5)';
  ctx.font = (12 * dpr) + 'px "DM Sans", system-ui, sans-serif';
  ctx.fillText(dateStr + '  \u00b7  ' + items.length + ' repositories', padX, 68 * dpr);

  // ── Podium: Top 3 ──
  var podiumBaseY = headerH + 16 * dpr;
  var podiumColors = [
    { bg: '#fffbeb', border: '#f59e0b', accent: '#b45309', glow: 'rgba(245,158,11,0.15)', medal: '🥇', label: '1ST' },
    { bg: '#f0f9ff', border: '#64748b', accent: '#475569', glow: 'rgba(100,116,139,0.12)', medal: '🥈', label: '2ND' },
    { bg: '#fdf4ff', border: '#a855f7', accent: '#7e22ce', glow: 'rgba(168,85,247,0.12)', medal: '🥉', label: '3RD' },
  ];

  var top3 = items.slice(0, 3);
  var podiumCardW = Math.floor((contentW - 2 * 12 * dpr) / 3);
  var podiumCardH = podiumH - 16 * dpr;
  // Podium order: 2nd(left) → 1st(center) → 3rd(right), with vertical offsets
  var podiumOrder = top3.length >= 3 ? [1, 0, 2] : top3.map(function(_, i) { return i; });
  var podiumOffsets = [20 * dpr, 0, 36 * dpr]; // Y offsets: 2nd slightly lower, 1st top, 3rd lowest

  podiumOrder.forEach(function(dataIdx, posIdx) {
    var item = top3[dataIdx];
    if (!item) return;
    var c = podiumColors[dataIdx];
    var cx = padX + posIdx * (podiumCardW + 12 * dpr);
    var podiumY = podiumBaseY + (top3.length >= 3 ? podiumOffsets[posIdx] : 0);
    var cardH = podiumCardH - (top3.length >= 3 ? podiumOffsets[posIdx] : 0);

    // Card shadow / glow
    ctx.fillStyle = c.glow;
    _roundRect(ctx, cx + 4 * dpr, podiumY + 4 * dpr, podiumCardW, cardH, 16 * dpr);

    // Card bg
    ctx.fillStyle = c.bg;
    _roundRect(ctx, cx, podiumY, podiumCardW, cardH, 16 * dpr);

    // Accent top edge (clipped to card shape)
    ctx.save();
    _roundRectPath(ctx, cx, podiumY, podiumCardW, cardH, 16 * dpr);
    ctx.clip();
    ctx.fillStyle = c.border;
    ctx.fillRect(cx, podiumY, podiumCardW, 6 * dpr);
    ctx.restore();

    // Card border
    ctx.strokeStyle = c.border;
    ctx.lineWidth = 1.5 * dpr;
    ctx.globalAlpha = 0.3;
    _roundRectStroke(ctx, cx, podiumY, podiumCardW, cardH, 16 * dpr);
    ctx.globalAlpha = 1;

    // Content area: center elements within the card
    var contentTop = podiumY + 10 * dpr;

    // Medal + label
    ctx.font = (36 * dpr) + 'px "DM Sans", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#1f2937';
    ctx.fillText(c.medal, cx + podiumCardW / 2, contentTop + 34 * dpr);

    ctx.font = 'bold ' + (11 * dpr) + 'px "DM Sans", system-ui, sans-serif';
    ctx.fillStyle = c.accent;
    ctx.fillText(c.label, cx + podiumCardW / 2, contentTop + 48 * dpr);

    // Avatar (centered, large)
    var avatarSize = 48 * dpr;
    var avatarX = cx + (podiumCardW - avatarSize) / 2;
    var avatarY2 = contentTop + 56 * dpr;
    if (item.avatarImg) {
      ctx.save();
      _roundRectPath(ctx, avatarX, avatarY2, avatarSize, avatarSize, 12 * dpr);
      ctx.clip();
      ctx.drawImage(item.avatarImg, avatarX, avatarY2, avatarSize, avatarSize);
      ctx.restore();
      ctx.strokeStyle = c.border;
      ctx.lineWidth = 2 * dpr;
      ctx.globalAlpha = 0.4;
      _roundRectStroke(ctx, avatarX, avatarY2, avatarSize, avatarSize, 12 * dpr);
      ctx.globalAlpha = 1;
    } else {
      ctx.fillStyle = '#e2dace';
      _roundRect(ctx, avatarX, avatarY2, avatarSize, avatarSize, 12 * dpr);
    }

    // Repo name
    var nameMaxW = podiumCardW - 24 * dpr;
    ctx.font = 'bold ' + (13 * dpr) + 'px "DM Sans", system-ui, sans-serif';
    ctx.fillStyle = '#1f2937';
    var displayName = item.name;
    while (ctx.measureText(displayName).width > nameMaxW && displayName.length > 4) {
      displayName = displayName.slice(0, -4) + '…';
    }
    ctx.fillText(displayName, cx + podiumCardW / 2, contentTop + 122 * dpr);

    // Score (big)
    var cls = _getScoreCls(item.score);
    ctx.font = 'bold ' + (24 * dpr) + 'px "DM Mono", monospace';
    ctx.fillStyle = _scoreColor(cls);
    ctx.fillText(item.score, cx + podiumCardW / 2, contentTop + 150 * dpr);

    ctx.textAlign = 'left';
  });

  // ── Remaining items (4th+) ──
  if (restCount > 0) {
    var restY = podiumBaseY + podiumH + 8 * dpr;

    // Section label
    ctx.fillStyle = '#9ca3af';
    ctx.font = (12 * dpr) + 'px "DM Sans", system-ui, sans-serif';
    ctx.fillText('OTHER REPOSITORIES', padX + 4 * dpr, restY + 14 * dpr);
    restY += 24 * dpr;

    for (var i = 3; i < items.length; i++) {
      var item = items[i];
      var cy = restY + (i - 3) * (restCardH + restGap);
      var cls = _getScoreCls(item.score);

      // Card bg
      ctx.fillStyle = '#ffffff';
      _roundRect(ctx, padX, cy, contentW, restCardH, 10 * dpr);
      ctx.strokeStyle = '#e2dace';
      ctx.lineWidth = 1 * dpr;
      _roundRectStroke(ctx, padX, cy, contentW, restCardH, 10 * dpr);

      var centerY = cy + restCardH / 2;

      // Rank number
      ctx.font = (14 * dpr) + 'px "DM Mono", monospace';
      ctx.fillStyle = '#9ca3af';
      ctx.textAlign = 'center';
      ctx.fillText(item.rank, padX + 28 * dpr, centerY + 5 * dpr);
      ctx.textAlign = 'left';

      // Small avatar
      var sAvatarSize = 28 * dpr;
      var sAvatarX = padX + 50 * dpr;
      var sAvatarY = centerY - sAvatarSize / 2;
      if (item.avatarImg) {
        ctx.save();
        _roundRectPath(ctx, sAvatarX, sAvatarY, sAvatarSize, sAvatarSize, 6 * dpr);
        ctx.clip();
        ctx.drawImage(item.avatarImg, sAvatarX, sAvatarY, sAvatarSize, sAvatarSize);
        ctx.restore();
      } else {
        ctx.fillStyle = '#e2dace';
        _roundRect(ctx, sAvatarX, sAvatarY, sAvatarSize, sAvatarSize, 6 * dpr);
      }

      // Name
      var nameX = sAvatarX + sAvatarSize + 12 * dpr;
      ctx.font = (14 * dpr) + 'px "DM Sans", system-ui, sans-serif';
      ctx.fillStyle = '#374151';
      var dn = item.name.length > 45 ? item.name.substring(0, 42) + '…' : item.name;
      ctx.fillText(dn, nameX, centerY + 5 * dpr);

      // Score
      var scoreW = 70 * dpr;
      var scoreH = 26 * dpr;
      var scoreX = padX + contentW - 16 * dpr - scoreW;
      var scoreY = centerY - scoreH / 2;
      ctx.fillStyle = _scoreBg(cls);
      _roundRect(ctx, scoreX, scoreY, scoreW, scoreH, 6 * dpr);
      ctx.fillStyle = _scoreColor(cls);
      ctx.font = 'bold ' + (13 * dpr) + 'px "DM Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(item.score, scoreX + scoreW / 2, scoreY + scoreH / 2 + 5 * dpr);
      ctx.textAlign = 'left';

      // Bar
      var barX = nameX + ctx.measureText(dn).width + 16 * dpr;
      var barW = scoreX - barX - 16 * dpr;
      if (barW > 30 * dpr) {
        var pct = parseFloat(item.score) / 100;
        ctx.fillStyle = '#f0ebe4';
        _roundRect(ctx, barX, centerY - 3 * dpr, barW, 6 * dpr, 3 * dpr);
        if (pct > 0) {
          ctx.fillStyle = _scoreColor(cls);
          ctx.globalAlpha = 0.25;
          _roundRect(ctx, barX, centerY - 3 * dpr, barW * Math.min(pct, 1), 6 * dpr, 3 * dpr);
          ctx.globalAlpha = 1;
        }
      }
    }
  }

  // ── Footer ──
  var footerY = totalH - footerH - 10 * dpr;
  ctx.strokeStyle = '#cdc3b5';
  ctx.lineWidth = 1.5 * dpr;
  ctx.beginPath();
  ctx.moveTo(padX, footerY);
  ctx.lineTo(W - padX, footerY);
  ctx.stroke();

  // QR code
  var qr = qrcode(0, 'M');
  qr.addData(siteUrl);
  qr.make();
  var qrModules = qr.getModuleCount();
  var qrSize = 76 * dpr;
  var qrX = W - padX - qrSize;
  var qrY = footerY + 16 * dpr;
  var cellSize = qrSize / qrModules;

  // QR background
  ctx.fillStyle = '#ffffff';
  _roundRect(ctx, qrX - 6 * dpr, qrY - 6 * dpr, qrSize + 12 * dpr, qrSize + 12 * dpr, 8 * dpr);

  ctx.fillStyle = '#1e293b';
  for (var row = 0; row < qrModules; row++) {
    for (var col = 0; col < qrModules; col++) {
      if (qr.isDark(row, col)) {
        ctx.fillRect(qrX + col * cellSize, qrY + row * cellSize, cellSize + 0.5, cellSize + 0.5);
      }
    }
  }

  // Footer text
  ctx.fillStyle = '#1f2937';
  ctx.font = 'bold ' + (20 * dpr) + 'px "DM Sans", system-ui, sans-serif';
  ctx.fillText('🛸 AI Intrusion Report', padX, footerY + 36 * dpr);
  ctx.fillStyle = '#6b7280';
  ctx.font = (13 * dpr) + 'px "DM Sans", system-ui, sans-serif';
  ctx.fillText('Scan QR to view full report', padX, footerY + 58 * dpr);
  ctx.fillStyle = '#9ca3af';
  ctx.font = (12 * dpr) + 'px "DM Mono", monospace';
  ctx.fillText(siteUrl, padX, footerY + 80 * dpr);

  // Download
  var dataUrl = canvas.toDataURL('image/png');
  var modal = document.getElementById('share-modal');
  var loading = document.getElementById('share-loading');
  var preview = document.getElementById('share-preview');
  var imgEl = document.getElementById('share-img');
  var dlLink = document.getElementById('share-download');

  imgEl.src = dataUrl;
  dlLink.href = dataUrl;
  dlLink.download = 'ai-ranking-' + dateStr + '.png';
  loading.classList.add('hidden');
  preview.classList.remove('hidden');

  btn.disabled = false;
  btn.classList.remove('loading', 'loading-spinner');
}

function _getScoreCls(txt) {
  var v = parseFloat(txt);
  if (v >= 60) return 'high';
  if (v >= 30) return 'med';
  return 'low';
}
function _scoreColor(cls) {
  return { high: '#dc2626', med: '#ca8a04', low: '#16a34a' }[cls] || '#2563eb';
}
function _scoreBg(cls) {
  return { high: '#fef2f2', med: '#fefce8', low: '#f0fdf4' }[cls] || '#f5f5f5';
}

// Canvas rounded rect helpers
function _roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}

function _roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function _roundRectStroke(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.stroke();
}
