(() => {
  const esc = value => String(value).replace(/[&<>]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[char]));
  const attr = value => esc(value).replace(/["']/g, char => char === '"' ? '&quot;' : '&#39;');
  const list = value => Array.isArray(value) ? value : value ? [value] : [];
  const kindOf = entry => entry.kind || 'paper';
  const kindLabel = entry => ({paper: 'paper', talk: 'talk', post: 'post'}[kindOf(entry)] || kindOf(entry));
  const kindPlural = kind => ({paper: 'Papers', talk: 'Talks', post: 'Posts'}[kind] || kind);
  const kindCountLabel = (kind, count) => count === 1 ? kind : kindPlural(kind).toLowerCase();
  const safeHref = href => {
    const value = String(href || '');
    return /^(?:https?:\/\/|mailto:)/i.test(value) || /^(?:\.\.?\/)?[a-z0-9_./-]+(?:#[a-z0-9_.:-]+)?$/i.test(value) || /^#[a-z0-9_.:-]+$/i.test(value) ? value : '#';
  };
  const readJson = async path => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  };

  const configureMarkdown = headingShift => marked.use({
    gfm: true,
    breaks: false,
    renderer: {
      heading({tokens, depth}) {
        const level = Math.min(6, depth + headingShift);
        return `<h${level}>${this.parser.parseInline(tokens)}</h${level}>\n`;
      },
      link({href, title, tokens}) {
        const value = safeHref(href);
        const match = /^https?:\/\//i.test(value) ? null : value.match(/(?:^|\/)([a-z0-9]+)\.md$/);
        const target = match ? `entry.html?key=${encodeURIComponent(match[1])}` : value;
        return `<a href="${attr(target)}"${title ? ` title="${attr(title)}"` : ''}>${this.parser.parseInline(tokens)}</a>`;
      },
      image({href, title, text}) {
        const target = safeHref(href);
        return target === '#' ? esc(text) : `<img src="${attr(target)}" alt="${attr(text)}"${title ? ` title="${attr(title)}"` : ''}>`;
      },
      html({text}) { return esc(text); },
    },
  });
  const markdown = body => marked.parse(body.replace(/^# .*\n?/m, ''));
  const titleOf = entry => (entry.body.match(/^# (.+)/m) || [, entry.key])[1];
  const primarySourceOf = entry => {
    const id = String(entry.id || '').trim();
    let match;
    if ((match = id.match(/^arxiv:(.+)$/i))) return {label: 'Paper', href: `https://arxiv.org/abs/${match[1]}`};
    if ((match = id.match(/^doi:(.+)$/i))) return {label: 'Paper', href: `https://doi.org/${match[1]}`};
    if ((match = id.match(/^openreview:(.+)$/i))) return {label: 'Paper', href: `https://openreview.net/forum?id=${encodeURIComponent(match[1])}`};
    if ((match = id.match(/^dblp:(.+)$/i))) return {label: 'Paper', href: `https://dblp.org/rec/${match[1]}`};
    if ((match = id.match(/^hdl:(.+)$/i))) return {label: 'Paper', href: `https://hdl.handle.net/${match[1]}`};
    if (/^https?:\/\//i.test(id)) return {label: kindLabel(entry)[0].toUpperCase() + kindLabel(entry).slice(1), href: id};
    return null;
  };
  const externalLink = (label, href, className = 'resource-link') => {
    const target = safeHref(href);
    if (target === '#') return '';
    return `<a class="${attr(className)}" href="${attr(target)}" target="_blank" rel="noopener noreferrer" ` +
      `aria-label="${attr(`${label} (opens in a new tab)`)}">${esc(label)} <span class="external-mark" aria-hidden="true">↗</span></a>`;
  };
  const linkedTitle = entry => {
    const title = titleOf(entry);
    const source = primarySourceOf(entry);
    return source ? externalLink(title, source.href, 'paper-title') : esc(title);
  };
  const resourceActions = (entry, {source = false, review = false, markdownSource = false} = {}) => {
    const links = [];
    const primary = primarySourceOf(entry);
    if (source && primary) links.push(externalLink(primary.label, primary.href));
    if (entry.project) links.push(externalLink('Project', entry.project));
    if (entry.github) links.push(externalLink('GitHub', entry.github));
    if (review) {
      const label = kindOf(entry) === 'paper' ? 'Review' : 'Notes';
      links.push(`<a class="resource-link review-link" href="entry.html?key=${encodeURIComponent(entry.key)}">${label} <span aria-hidden="true">→</span></a>`);
    }
    if (markdownSource) links.push(`<a class="resource-link" href="entries/${attr(entry.path || `papers/${entry.key}.md`)}">Markdown</a>`);
    return links.filter(Boolean).length ? `<nav class="resource-actions" aria-label="Resources">${links.filter(Boolean).join('')}</nav>` : '';
  };

  async function renderEntry() {
    configureMarkdown(0);
    const {entries, citations} = await readJson('data.json');
    const key = new URLSearchParams(location.search).get('key');
    const entry = entries.find(candidate => candidate.key === key);
    if (!entry) {
      document.title = 'Entry not found · Paperstack';
      document.getElementById('content').innerHTML = '<p>entry not found</p>';
      return;
    }

    const title = titleOf(entry);
    const citationCount = citationOf(entry, citations && citations.papers);
    const entryKind = kindOf(entry);
    const labels = `<span class="kind-label kind-${attr(entryKind)}">${esc(kindLabel(entry))}</span>` +
      (entryKind === 'paper' ? `<span class="quality ${attr(entry.quality)}">${esc(entry.quality)}</span>` : '');
    const metadata = entryKind === 'talk'
      ? [entry.id, entry.published, list(entry.speaker).join(', '), entry.channel]
      : entryKind === 'post'
        ? [entry.id, entry.published, entry.publisher]
        : [entry.id, entry.venue, list(entry.lab).join(', '), citationCount == null ? null : `${citationCount.toLocaleString()} citations`];
    document.title = `${title} · Paperstack`;
    document.getElementById('header').innerHTML = `<h1>${linkedTitle(entry)}</h1><div class="meta">` +
      `<div class="entry-labels">${labels}</div>${metadata.filter(Boolean).map(esc).join(' · ')}<br>` +
      `editor: ${esc(entry.editor || 'unknown')}</div>${resourceActions(entry, {source: true, markdownSource: true})}`;
    document.getElementById('content').innerHTML = markdown(entry.body);
  }

  async function renderIndex() {
    configureMarkdown(1);
    const {entries, collections, citations} = await readJson('data.json');
    const citationData = citations && citations.papers || {};
    const kinds = ['paper', 'talk', 'post'];
    const qualities = ['excellent','good','fair','poor','TODO'];
    const colors = {excellent:'#1a7f37', good:'#1f6feb', fair:'#9a6700', poor:'#cf222e', TODO:'#8888'};
    const qualityOrder = new Map(qualities.map((value, index) => [value, index]));
    const tags = [...new Set(entries.flatMap(entry => list(entry.tags)))].sort();
    const pageSize = 24;
    let collectionId = null;
    let kind = 'paper';
    let quality = null;
    let tag = null;
    let search = '';
    let minCitations = null;
    let order = 'default';
    let limit = pageSize;

    const selectedCollection = () => collections.find(item => item.id === collectionId);
    const memberMap = selected => selected && new Map(selected.entries.map((key, index) => [key, index]));
    const yearOf = entry => ((entry.key || '').match(/(19|20)\d\d/) || [])[0];
    const matches = (entry, members, includeTag = true) =>
      (!members || members.has(entry.key)) &&
      kindOf(entry) === kind &&
      (!quality || entry.quality === quality) &&
      (!includeTag || !tag || list(entry.tags).includes(tag)) &&
      (!minCitations || (citationOf(entry, citationData) != null && citationOf(entry, citationData) >= minCitations)) &&
      (!search || [entry.title, entry.body, entry.editor].some(value => String(value || '').toLowerCase().includes(search)));
    const sortEntries = (hits, members) => {
      if (order === 'default' && members) return hits.sort((a, b) => members.get(a.key) - members.get(b.key));
      if (order === 'quality') return hits.sort((a, b) =>
        (qualityOrder.get(a.quality) ?? 9) - (qualityOrder.get(b.quality) ?? 9) ||
        (yearOf(b) || '').localeCompare(yearOf(a) || '') || a.key.localeCompare(b.key));
      return hits.sort((a, b) => (yearOf(b) || '').localeCompare(yearOf(a) || '') || a.key.localeCompare(b.key));
    };
    const stats = hits => {
      if (!hits.length) return '';
      const papers = hits.filter(entry => kindOf(entry) === 'paper');
      const verdictCounts = qualities.map(value => [value, papers.filter(entry => entry.quality === value).length])
        .filter(([, count]) => count);
      const years = {};
      hits.forEach(entry => { const year = yearOf(entry); if (year) years[year] = (years[year] || 0) + 1; });
      const keys = Object.keys(years).sort();
      const maximum = Math.max(1, ...Object.values(years));
      const resultLabel = kindCountLabel(kind, hits.length);
      const verdicts = papers.length ? '<section class="verdict-stats"><div class="stats-heading">' +
        '<span>Paper verdicts</span></div>' +
        '<div class="verdict-bar" aria-label="Paper verdict distribution">' + verdictCounts.map(([value, count]) =>
          `<i class="${attr(value)}" style="width:${100 * count / papers.length}%;background:${colors[value]}" title="${value}: ${count}"></i>`).join('') + '</div>' +
        '<div class="verdict-key">' + verdictCounts.map(([value, count]) =>
          `<button data-verdict="${attr(value)}" class="${attr(value)}" title="Show ${value} papers">${esc(value)} <b>${count}</b></button>`).join('') + '</div></section>' : '';
      return `<div class="result-count"><strong>${hits.length}</strong> ${esc(resultLabel)}</div>` + verdicts +
        (keys.length > 1 ? '<div class="yr">' + keys.map(year =>
          `<div title="${year}: ${years[year]}"><i style="height:${100 * years[year] / maximum}%"></i><s>${year.slice(2)}</s></div>`).join('') + '</div>' : '');
    };

    const renderScope = members => {
      const available = entries.filter(entry => !members || members.has(entry.key));
      const options = kinds.map(value => [value, kindPlural(value)]);
      const visibleCount = available.filter(entry => kindOf(entry) === kind).length;
      if (members) {
        document.getElementById('collection-count').textContent = `${visibleCount} ${kindCountLabel(kind, visibleCount)} · ${available.length} total`;
      }
      document.getElementById('scope').innerHTML = options.map(([value, label]) => {
        const count = value ? available.filter(entry => kindOf(entry) === value).length : available.length;
        const active = kind === value;
        return `<button class="scope-option${active ? ' on' : ''}" data-scope="${attr(value)}" ` +
          `aria-pressed="${active}"><span>${esc(label)}</span><small>${count}</small></button>`;
      }).join('');
    };

    const collectionOption = item =>
      `<button class="collection-option" data-c="${attr(item.id)}"><strong>${esc(item.title)}</strong>` +
      `<small>${item.entries.length}${item.status === 'draft' ? ' · draft' : ''}</small>` +
      `<span>${esc(item.description)}</span></button>`;
    const publishedCollections = collections.filter(item => item.status === 'published');
    const draftCollections = collections.filter(item => item.status === 'draft');
    document.getElementById('collection-options').innerHTML =
      `<button class="collection-option collection-all" data-c=""><strong>Entire library</strong><small>${entries.length}</small>` +
      '<span>Every source, separated by the Papers, Talks, and Posts views.</span></button>' +
      '<div class="collection-heading">Published collections</div>' +
      publishedCollections.map(collectionOption).join('') +
      `<details class="draft-collections" id="draft-collections"><summary>Draft collections <small>${draftCollections.length}</small></summary>` +
      `<div class="collection-grid">${draftCollections.map(collectionOption).join('')}</div></details>`;
    document.getElementById('bar').innerHTML =
      '<div class="filter-row" id="quality-row"><span class="control-label">Paper verdict</span>' +
      qualities.map(value => `<button data-q="${value}">${value}</button>`).join('') + '</div>' +
      '<div class="filter-row"><span class="control-label">Tags</span><div class="tag-scroll" id="tags"></div></div>' +
      '<div class="filter-row"><label class="control-label" for="s">Search</label><input id="s" placeholder="title, text, editor"></div>' +
      '<div class="filter-row" id="citations-row"><label class="control-label" for="citations">Citations ≥</label>' +
      '<input id="citations" type="number" min="1" step="1" placeholder="e.g. 100"></div>' +
      '<div class="filter-row"><label class="control-label" for="o">Order</label><select id="o">' +
      '<option value="default">Newest</option><option value="newest">Newest</option><option value="quality">Paper verdict</option></select></div>' +
      '<div id="active"><span id="active-text"></span><button id="clear">Clear all</button></div>';

    const syncControls = () => {
      const selected = selectedCollection();
      document.getElementById('collection-title').textContent = selected ? selected.title : 'Entire library';
      document.getElementById('collection-count').textContent = selected ? `${selected.entries.length} entries` : `${entries.length} sources`;
      if (selected && selected.status === 'draft') document.getElementById('draft-collections').open = true;
      document.querySelectorAll('[data-c]').forEach(button => {
        const active = button.dataset.c === (collectionId || '');
        button.classList.toggle('on', active);
        button.setAttribute('aria-pressed', String(active));
      });
      document.getElementById('s').value = search;
      document.getElementById('citations').value = minCitations || '';
      document.querySelector('#o option[value="default"]').textContent = selected ? 'Curated order' : 'Newest';
      document.querySelector('#o option[value="newest"]').hidden = !selected;
      const qualityOption = document.querySelector('#o option[value="quality"]');
      const paperScope = kind === 'paper';
      qualityOption.hidden = !paperScope;
      qualityOption.disabled = !paperScope;
      document.getElementById('quality-row').hidden = !paperScope;
      document.getElementById('citations-row').hidden = !paperScope;
      if (!paperScope) {
        quality = null;
        minCitations = null;
        if (order === 'quality') order = 'default';
      }
      document.getElementById('o').value = order;
      document.querySelectorAll('[data-q]').forEach(button => {
        const active = button.dataset.q === quality;
        button.classList.toggle('on', active);
        button.setAttribute('aria-pressed', String(active));
      });
    };
    const readUrl = () => {
      const params = new URLSearchParams(location.search);
      const nextCollection = params.get('collection');
      const nextKind = params.get('kind');
      const nextQuality = params.get('quality');
      const nextTag = params.get('tag');
      const nextOrder = params.get('order');
      const nextCitations = Number.parseInt(params.get('citations'), 10);
      collectionId = collections.some(item => item.id === nextCollection) ? nextCollection : null;
      kind = kinds.includes(nextKind) ? nextKind : 'paper';
      quality = qualities.includes(nextQuality) ? nextQuality : null;
      tag = tags.includes(nextTag) ? nextTag : null;
      search = (params.get('search') || '').toLowerCase();
      minCitations = Number.isInteger(nextCitations) && nextCitations > 0 ? nextCitations : null;
      order = ['default', 'newest', 'quality'].includes(nextOrder) ? nextOrder : 'default';
      if (!collectionId && order === 'newest') order = 'default';
      limit = pageSize;
      syncControls();
    };
    const setUrl = push => {
      const params = new URLSearchParams();
      if (collectionId) params.set('collection', collectionId);
      if (kind !== 'paper') params.set('kind', kind);
      if (quality) params.set('quality', quality);
      if (tag) params.set('tag', tag);
      if (search) params.set('search', search);
      if (minCitations) params.set('citations', minCitations);
      if (order !== 'default') params.set('order', order);
      const query = params.size ? `?${params}` : '';
      history[push ? 'pushState' : 'replaceState'](null, '', location.pathname + query + location.hash);
    };
    const render = () => {
      const selected = selectedCollection();
      const members = memberMap(selected);
      renderScope(members);
      const tagCounts = new Map();
      entries.filter(entry => matches(entry, members, false)).forEach(entry =>
        list(entry.tags).forEach(value => tagCounts.set(value, (tagCounts.get(value) || 0) + 1)));
      document.getElementById('tags').innerHTML = [...tagCounts].sort(([a], [b]) => a.localeCompare(b)).map(([value, count]) =>
        `<button data-t="${attr(value)}" class="${value === tag ? 'on' : ''}" aria-pressed="${value === tag}">#${esc(value)} <small>${count}</small></button>`).join('');
      const hits = sortEntries(entries.filter(entry => matches(entry, members)), members);
      const visible = hits.slice(0, limit);
      document.getElementById('collection-note').innerHTML = selected ?
        (selected.status === 'draft' ? '<span class="draft-badge">Draft</span> ' : '') + esc(selected.description) +
        (selected.source ? ` · <a href="${attr(safeHref(selected.source))}">source</a>` : '') : '';
      const active = [selected && selected.title, quality, tag && `#${tag}`, search && `“${search}”`, minCitations && `${minCitations}+ citations`, order !== 'default' && order].filter(Boolean);
      const filterCount = [quality, tag, search, minCitations, order !== 'default'].filter(Boolean).length;
      document.querySelector('#filters > summary').textContent = 'Filters and order' + (filterCount ? ` · ${filterCount} active` : '');
      document.getElementById('active-text').textContent = active.join(' · ');
      document.getElementById('active').classList.toggle('on', Boolean(active.length));
      document.getElementById('stats').innerHTML = stats(hits);
      document.getElementById('list').innerHTML = visible.map(entry => {
        const entryKind = kindOf(entry);
        const labels = `<span class="kind-label kind-${attr(entryKind)}">${esc(kindLabel(entry))}</span>` +
          (entryKind === 'paper' ? `<span class="q ${attr(entry.quality)}">${esc(entry.quality)}</span>` : '');
        const metadata = entryKind === 'talk'
          ? [entry.id, entry.published, list(entry.speaker).join(', '), entry.channel]
          : entryKind === 'post'
            ? [entry.id, entry.published, entry.publisher]
            : [entry.id, entry.venue, list(entry.lab).join(', ')];
        return `<article>
        <div class="hd"><span class="entry-labels">${labels}</span>
          <span class="name">${linkedTitle(entry)}</span>
          <span class="tag">${list(entry.tags).map(value => `#${esc(value)}`).join(' ')}</span></div>
        <div class="meta">${metadata.filter(Boolean).map(esc).join(' · ')}<br>
          <span class="editor">editor: ${esc(entry.editor || 'unknown')}</span>${entryKind !== 'paper' || citationOf(entry, citationData) == null ? '' : ` · ${citationOf(entry, citationData).toLocaleString()} citations`}</div>
        ${resourceActions(entry, {review: true})}
        ${markdown(entry.body).replace(/<h3>(?:What it is|What it covers|What it reports)[\s\S]*/, value => `<details><summary>full notes</summary>${value}</details>`)}
      </article>`;
      }).join('') || '<p>nothing matches</p>';
      const remaining = hits.length - visible.length;
      document.getElementById('more-wrap').innerHTML = remaining > 0 ?
        `<button id="more">Show ${Math.min(pageSize, remaining)} more · ${remaining} remaining</button>` : '';
    };

    document.getElementById('bar').onclick = event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.id === 'clear') {
        collectionId = quality = tag = minCitations = null; kind = 'paper'; search = ''; order = 'default'; limit = pageSize;
        syncControls(); render(); setUrl(true); return;
      }
      if (button.dataset.q) quality = quality === button.dataset.q ? null : button.dataset.q;
      if (button.dataset.t) tag = tag === button.dataset.t ? null : button.dataset.t;
      limit = pageSize;
      syncControls(); render(); setUrl(true);
    };
    document.getElementById('scope').onclick = event => {
      const button = event.target.closest('[data-scope]');
      if (!button) return;
      kind = button.dataset.scope;
      quality = minCitations = null;
      if (order === 'quality') order = 'default';
      limit = pageSize;
      syncControls(); render(); setUrl(true);
    };
    document.getElementById('stats').onclick = event => {
      const button = event.target.closest('[data-verdict]');
      if (!button) return;
      kind = 'paper';
      quality = button.dataset.verdict;
      limit = pageSize;
      syncControls(); render(); setUrl(true);
    };
    document.getElementById('s').oninput = event => { search = event.target.value.toLowerCase(); limit = pageSize; render(); setUrl(false); };
    document.getElementById('citations').oninput = event => {
      const value = Number.parseInt(event.target.value, 10);
      minCitations = Number.isInteger(value) && value > 0 ? value : null;
      limit = pageSize; render(); setUrl(false);
    };
    document.getElementById('o').onchange = event => { order = event.target.value; limit = pageSize; render(); setUrl(true); };
    document.getElementById('collection-options').onclick = event => {
      const button = event.target.closest('[data-c]');
      if (!button) return;
      collectionId = button.dataset.c || null; tag = null; limit = pageSize;
      if (!collectionId && order === 'newest') order = 'default';
      document.getElementById('collection-picker').open = false;
      syncControls(); render(); setUrl(true);
    };
    document.getElementById('more-wrap').onclick = event => {
      if (event.target.id !== 'more') return;
      limit += pageSize;
      render();
    };
    window.onpopstate = () => {
      readUrl();
      if (quality || tag || search || minCitations || order !== 'default') document.getElementById('filters').open = true;
      render();
    };

    readUrl();
    if (matchMedia('(max-width:42rem)').matches && kind === 'paper' && !quality && !tag && !search && !minCitations && order === 'default') {
      document.getElementById('filters').open = false;
    }
    render();
  }

  function citationOf(entry, citationData) {
    const value = String(entry.id || '');
    const match = value.match(/^arxiv:([^?#]+)$/i) ||
      value.match(/arxiv\.org\/(?:abs|pdf)\/([^?#]+?)(?:\.pdf)?(?:[?#]|$)/i);
    const paperId = match && match[1].replace(/v\d+$/, '');
    return paperId && citationData && Number.isInteger(citationData[paperId]) ? citationData[paperId] : null;
  }

  const task = document.body.classList.contains('entry-page') ? renderEntry() : renderIndex();
  task.catch(error => {
    const target = document.getElementById('content') || document.getElementById('list') || document.body;
    target.innerHTML = `<p>viewer failed: ${esc(error.message)}</p>`;
  });
})();
