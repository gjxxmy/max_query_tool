const $ = id => document.getElementById(id);
const form = $('query-form');
let querying = false;
let activeController = null;
let stopRequested = false;
let batchContext = null;
let preferencesState = {profiles: [], apps: {}};
const platformNames = {android: 'Android', ios: 'iOS', fireos: 'Fire OS'};
const now = new Date();
const utcToday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
const iso = d => d.toISOString().slice(0, 10);
const oldest = new Date(utcToday.getTime() - 45 * 86400000);
const latestReady = new Date(utcToday.getTime() - (now.getUTCHours() >= 8 ? 1 : 2) * 86400000);
for (const id of ['date', 'start-date', 'end-date']) {
  $(id).min = iso(oldest);
  $(id).max = iso(utcToday);
  $(id).value = iso(latestReady);
}
$('start-date').value = iso(new Date(latestReady.getTime() - 6 * 86400000));
$('date-hint').textContent = `最近可用日期：${iso(latestReady)}（UTC）`;

function updateReadiness() {
  const summary = document.querySelector('input[name="mode"]:checked').value === 'summary';
  const selected = $('date-scope').value === 'range' ? $('end-date').value : $('date').value;
  const unavailable = selected && selected > (summary ? iso(new Date(utcToday.getTime() - 86400000)) : iso(latestReady));
  $('readiness').hidden = !unavailable;
  $('readiness').textContent = summary ? '包含今天的数据。当天数据持续更新，最近 1–2 小时可能不完整；对账建议选择完整 UTC 日。' : '所选日期的数据可能尚未就绪。数据在北京时间次日 16:00 后提供，也可以稍后重试。';
}
$('date').addEventListener('change', updateReadiness);
for (const id of ['start-date', 'end-date']) $(id).addEventListener('change', () => {
  $('end-date').setCustomValidity('');
  updateReadiness();
});
function changeDateScope() {
  const range = $('date-scope').value === 'range';
  $('single-date').hidden = range;
  $('range-date').hidden = !range;
  $('date').disabled = range;
  $('date').required = !range;
  for (const id of ['start-date', 'end-date']) { $(id).disabled = !range; $(id).required = range; }
  $('end-date').setCustomValidity('');
  updateReadiness();
}
$('date-scope').addEventListener('change', changeDateScope);
changeDateScope();
function identifyPlatform() {
  const identifier = $('identifier').value.trim();
  const known = preferencesState.apps[identifier] || [];
  if (/^\d+$/.test(identifier)) {
    $('identifier-type').value = 'store_id';
    $('platform').value = 'ios';
    $('platform-hint').textContent = '数字 Store ID，已自动选择 iOS。';
  } else if (known.length === 1) {
    $('identifier-type').value = 'application';
    $('platform').value = known[0];
    $('platform-hint').textContent = `已记录为 ${platformNames[known[0]]}，已自动选择。`;
  } else {
    $('platform').value = '';
    if (identifier && $('identifier-type').value === 'store_id') $('identifier-type').value = 'application';
    $('platform-hint').textContent = known.length > 1 ? '这个标识对应多个平台，请选择本次要查的平台。' : identifier ? '仅凭包名无法确定平台，请选一次；查询成功后自动记住。' : '输入已记录的包名可自动选择平台。';
  }
}
$('identifier').addEventListener('input', identifyPlatform);
$('identifier-type').addEventListener('change', () => {
  $('identifier-hint').textContent = $('identifier-type').value === 'store_id' ? 'iOS 填数字商店 ID，Android 填应用包名。' : 'Android 包名和 iOS Bundle ID 格式可能相同，不能只凭格式判断。';
});
$('platform').addEventListener('change', () => {
  $('platform-hint').textContent = $('platform').value ? `本次选择 ${platformNames[$('platform').value]}，查询成功后记住。` : '请选择本次要查询的平台。';
});

function message(text, error = false) {
  $('preferences-message').hidden = !text;
  $('preferences-message').textContent = text;
  $('preferences-message').className = 'inline-message' + (error ? ' message-error' : '');
}

function updateKeyEditor() {
  const saved = Boolean($('key-profile').value);
  $('key-editor').hidden = saved;
  $('api-key').required = !saved;
  if (saved) {
    $('api-key').value = '';
    $('api-key').type = 'password';
    $('toggle-key').textContent = '显示';
    $('toggle-key').setAttribute('aria-pressed', 'false');
    $('toggle-key').setAttribute('aria-label', '显示 Report Key');
  }
  const cloud = preferencesState.storage === 'encrypted-cookie';
  $('key-hint').textContent = cloud ?
    (saved ? '使用此浏览器已保存的账号。Key 以加密 Cookie 保存，页面不会读取密钥原文。' : '填写一次并保存，之后直接选择。账号仅在此浏览器中记住，不与其他访问者共享。') :
    (saved ? '使用 Mac 钥匙串中保存的 Key，无需再次输入；页面不会读取密钥原文。' : '填写一次并保存，之后直接选择。密钥保存到这台 Mac 的钥匙串。');
}

function renderPreferences(state, selected) {
  preferencesState = state;
  const cloud = state.storage === 'encrypted-cookie';
  document.querySelector('.local-badge').lastChild.textContent = cloud ? ' 在线工具' : ' 本地工具';
  document.querySelector('.form-footnote').textContent = cloud ? '通过 HTTPS 连接报表服务，再查询 AppLovin 官方接口' : '通过本机服务连接 AppLovin 官方报表接口';
  document.querySelector('footer span:last-child').textContent = cloud ? '在线查询 · 各浏览器独立保存账号' : '本机运行 · 钥匙串保存密钥';
  $('key-profile').replaceChildren();
  for (const profile of state.profiles) {
    const option = document.createElement('option');
    option.value = profile.id; option.textContent = profile.name;
    $('key-profile').append(option);
  }
  const newKey = document.createElement('option');
  newKey.value = ''; newKey.textContent = state.profiles.length ? '＋ 添加另一个 Report Key' : '首次使用 · 添加 Report Key';
  $('key-profile').append(newKey);
  $('key-profile').value = selected || '';
  $('key-profile').disabled = false;
  $('saved-apps').replaceChildren();
  for (const [identifier, platforms] of Object.entries(state.apps)) {
    const option = document.createElement('option');
    option.value = identifier; option.label = platforms.map(p => platformNames[p]).join(' / ');
    $('saved-apps').append(option);
  }
  updateKeyEditor();
}

async function localJSON(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '设置操作未完成。');
  return value;
}

async function loadPreferences() {
  try {
    const state = await localJSON('/api/preferences');
    renderPreferences(state, state.last_profile);
    if (!$('identifier').value && state.last_identifier) $('identifier').value = state.last_identifier;
    identifyPlatform();
  } catch {
    $('key-profile').disabled = false;
    $('key-profile').replaceChildren(new Option('临时输入 Report Key', ''));
    updateKeyEditor();
    message('已保存的设置读取失败，请刷新后重试；仍可临时输入 Key 查询。', true);
  }
}

$('key-profile').addEventListener('change', () => { updateKeyEditor(); message(''); });
$('save-key').addEventListener('click', async () => {
  if (!$('api-key').reportValidity()) return;
  if (!$('profile-name').value.trim()) { message('请先给这个 Key 起一个名称。', true); $('profile-name').focus(); return; }
  $('save-key').disabled = true;
  $('key-profile').disabled = true;
  $('save-key').textContent = '保存中…';
  try {
    const state = await localJSON('/api/profiles', {name: $('profile-name').value.trim(), api_key: $('api-key').value.trim()});
    renderPreferences(state, state.last_profile);
    $('profile-name').value = '';
    message(preferencesState.storage === 'encrypted-cookie' ? '已为此浏览器加密保存。以后打开直接选择；清除 Cookie 或换浏览器后需重新输入。' : '已保存到 Mac 钥匙串。以后打开直接选择，无需重新输入。');
  } catch (error) {
    message(error instanceof TypeError ? '无法连接报表服务，请稍后重试。' : error.message, true);
  } finally {
    $('save-key').disabled = false;
    $('key-profile').disabled = false;
    $('save-key').textContent = '保存并使用';
  }
});
loadPreferences();
$('toggle-key').addEventListener('click', () => {
  const visible = $('api-key').type === 'password';
  $('api-key').type = visible ? 'text' : 'password';
  $('toggle-key').textContent = visible ? '隐藏' : '显示';
  $('toggle-key').setAttribute('aria-pressed', String(visible));
  $('toggle-key').setAttribute('aria-label', `${visible ? '隐藏' : '显示'} Report Key`);
});

function showState(name) {
  for (const state of ['empty', 'loading', 'error', 'success']) $(state + '-state').hidden = state !== name;
  if (name !== 'success') $('summary-panel').hidden = true;
  const labels = {empty: '等待查询', loading: '查询中', error: '未完成', success: '已就绪'};
  $('status-badge').textContent = labels[name];
  $('status-badge').className = 'status-badge' + (name === 'success' ? ' ready' : name === 'error' ? ' failed' : '');
  $('live-status').textContent = labels[name];
}

function chosenDays() {
  if ($('date-scope').value === 'single') return [$('date').value];
  const start = new Date($('start-date').value + 'T00:00:00Z');
  const end = new Date($('end-date').value + 'T00:00:00Z');
  const count = Math.round((end - start) / 86400000) + 1;
  if (!Number.isFinite(count) || count < 1 || count > 45) {
    $('end-date').setCustomValidity(count < 1 ? '结束日期不能早于开始日期。' : '请填写有效日期；一次最多查询 45 天。');
    $('end-date').reportValidity();
    return null;
  }
  return Array.from({length: count}, (_, index) => iso(new Date(start.getTime() + index * 86400000)));
}

function renderDaily(context) {
  $('daily-results').replaceChildren();
  const states = {pending: '等待查询', loading: '查询中…', ready: '已就绪', failed: '未成功', stopped: '已停止'};
  for (const record of context.rows) {
    const row = document.createElement('div'); row.className = 'day-row ' + record.state;
    const line = document.createElement('div'); line.className = 'day-line';
    const date = document.createElement('time'); date.textContent = record.date;
    const status = document.createElement('span'); status.textContent = states[record.state];
    line.append(date, status);
    if (record.result) {
      const link = document.createElement('a'); link.href = record.result.download_url;
      link.textContent = 'CSV ↓'; link.download = record.result.filename;
      link.target = '_blank'; link.rel = 'noopener'; line.append(link);
    }
    row.append(line);
    if (record.error) { const note = document.createElement('small'); note.textContent = record.error; row.append(note); }
    $('daily-results').append(row);
  }
  const complete = context.rows.filter(row => !['pending', 'loading'].includes(row.state)).length;
  const ready = context.rows.filter(row => row.state === 'ready').length;
  $('batch-progress').max = context.rows.length;
  $('batch-progress').value = complete;
  $('batch-summary').textContent = `已处理 ${complete}/${context.rows.length} 天 · 可下载 ${ready} 天`;
}

function errorText(error) {
  if (error.name === 'AbortError') return stopRequested ? '已停止查询。' : '查询超时，请检查网络后重试。';
  return error instanceof TypeError ? '无法连接报表服务，请稍后重试。' : error.message;
}

async function fetchReport(path, data) {
  activeController = new AbortController();
  const timer = setTimeout(() => activeController?.abort(), 70000);
  try {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
      signal: activeController.signal, body: JSON.stringify(data)});
    let result;
    try { result = await response.json(); } catch { throw new Error('报表服务响应异常，请稍后刷新重试。'); }
    if (!response.ok) throw new Error(result.error || '查询失败，请稍后重试。');
    return result;
  } finally {
    clearTimeout(timer);
    activeController = null;
  }
}

const fetchDay = (query, day) => fetchReport('/api/query', {...query, date: day});
const formatNames = {INTER: '插屏', REWARD: '激励', BANNER: 'Banner', MREC: 'MREC', NATIVE: '原生', APPOPEN: '开屏', UNKNOWN: '其他'};
const countText = value => new Intl.NumberFormat('zh-CN').format(value);
const moneyText = value => value === null ? '—' : new Intl.NumberFormat('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(value));

function tableRow(body, values) {
  const row = document.createElement('tr');
  for (const value of values) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
  body.append(row);
}

function renderSummary(result) {
  $('summary-impressions').textContent = countText(result.total.impressions);
  $('summary-revenue').textContent = moneyText(result.total.revenue);
  $('summary-ecpm').textContent = moneyText(result.total.ecpm);
  $('summary-fullscreen').textContent = `插屏＋激励：${countText(result.fullscreen_total.impressions)} 次 · 收益 $${moneyText(result.fullscreen_total.revenue)}`;
  for (const id of ['summary-types', 'summary-total', 'summary-days']) $(id).replaceChildren();
  const fields = row => [countText(row.impressions), moneyText(row.revenue), moneyText(row.ecpm)];
  for (const row of result.totals) tableRow($('summary-types'), [formatNames[row.ad_format] || row.ad_format, ...fields(row)]);
  tableRow($('summary-total'), ['合计', ...fields(result.total)]);
  for (const row of result.rows) tableRow($('summary-days'), [row.date, formatNames[row.ad_format] || row.ad_format, ...fields(row)]);
  if (!result.rows.length) {
    const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 5;
    cell.textContent = '所选应用和日期没有匹配记录。'; row.append(cell); $('summary-days').append(row);
  }
  $('summary-empty-days').hidden = !result.empty_days.length;
  $('summary-empty-days').textContent = `以下日期无匹配记录：${result.empty_days.join('、')}`;
  $('summary-as-of').textContent = `查询时间：${new Date(result.as_of).toLocaleString('zh-CN', {hour12: false})}（本机时间）。数据可能随后更新。`;
  $('summary-panel').hidden = false;
}

function lockModes(disabled) {
  for (const radio of document.querySelectorAll('input[name="mode"]')) radio.disabled = disabled;
}

async function runSummary(query, days) {
  querying = true; stopRequested = false; lockModes(true);
  $('submit').disabled = true; $('save-key').disabled = true;
  $('stop-query').hidden = false; $('daily-panel').hidden = true; $('retry-failed').hidden = true;
  $('download').removeAttribute('href'); $('submit-label').textContent = '正在查询…'; $('submit-icon').textContent = '…';
  showState('loading');
  try {
    const result = await fetchReport('/api/summary', {...query, start_date: days[0], end_date: days.at(-1)});
    $('result-mode').textContent = '展示与收益汇总';
    $('result-app').textContent = result.identifier; $('result-platform').textContent = platformNames[result.platform];
    $('result-date').textContent = result.start_date === result.end_date ? result.start_date : `${result.start_date} 至 ${result.end_date}`;
    $('result-format').textContent = 'CSV · 按日期与广告类型汇总';
    $('success-label').textContent = result.rows.length ? '汇总已就绪' : '暂无匹配记录';
    $('download-label').textContent = '下载汇总 CSV';
    $('download-note').textContent = 'CSV 包含逐日、分广告类型的数据，收益保留 6 位小数；跨日总计见下方。链接失效时重新查询。';
    $('download').href = result.download_url; $('download').setAttribute('download', result.filename);
    $('download-warning').hidden = !result.warning; $('download-warning').textContent = result.warning;
    showState('success'); renderSummary(result);
    try { renderPreferences(await localJSON('/api/preferences'), query.profile_id); } catch { /* Keep the completed report. */ }
  } catch (error) {
    $('error-message').textContent = errorText(error); showState('error');
  } finally {
    querying = false; lockModes(false); $('submit').disabled = false; $('save-key').disabled = false;
    $('stop-query').hidden = true; $('submit-label').textContent = '查询报表'; $('submit-icon').textContent = '↗';
  }
}

async function runQuery(context) {
  querying = true;
  lockModes(true);
  stopRequested = false;
  $('download').removeAttribute('href');
  $('submit').disabled = true;
  $('save-key').disabled = true;
  $('retry-failed').hidden = true;
  $('stop-query').hidden = false;
  $('submit-label').textContent = '正在查询…';
  $('submit-icon').textContent = '…';
  $('daily-panel').hidden = !context.range;
  showState('loading');
  try {
    if (context.range) renderDaily(context);
    for (const row of context.rows) {
      if (row.state === 'ready') continue;
      if (stopRequested) { row.state = 'stopped'; row.error = '未执行，已停止查询。'; continue; }
      row.state = 'loading'; row.error = '';
      if (context.range) renderDaily(context);
      try {
        row.result = await fetchDay(context.query, row.date);
        row.state = 'ready';
      } catch (error) {
        row.state = stopRequested ? 'stopped' : 'failed';
        row.error = errorText(error);
        if (row.error.includes('认证失败') || row.error.includes('请求过于频繁')) stopRequested = true;
      }
      if (context.range) renderDaily(context);
    }
    if (context.range) renderDaily(context);
    const ready = context.rows.filter(row => row.state === 'ready');
    if (!ready.length) throw new Error(context.range ? '所有日期均未获取到报表，请查看逐日结果后重试。' : context.rows[0].error);
    const result = context.range ? await localJSON('/api/bundle', {
      start_date: context.rows[0].date, end_date: context.rows.at(-1).date,
      results: context.rows.map(row => ({date: row.date, download_url: row.result?.download_url || ''}))
    }) : ready[0].result;
    const missing = context.rows.length - ready.length;
    $('result-mode').textContent = result.aggregated ? '按用户汇总' : '逐次展示明细';
    $('result-app').textContent = result.identifier;
    $('result-platform').textContent = platformNames[result.platform];
    $('result-date').textContent = context.range ? `${result.start_date} 至 ${result.end_date}` : result.date;
    $('result-format').textContent = context.range ? `ZIP · ${ready.length} 个每日 CSV` : 'CSV · 原始报表';
    $('success-label').textContent = missing ? `部分日期可用 · ${ready.length}/${context.rows.length} 天` : '报表已就绪';
    $('download-label').textContent = context.range ? `下载 ZIP（${ready.length} 天）` : '下载 CSV';
    $('download-note').textContent = context.range ? 'ZIP 内每天一个原始 CSV，不跨日汇总。含查询与下载结果说明；链接过期或下载失败的日期会列在说明中，不会混入不完整 CSV。' : '可用 Excel 或 Numbers 打开。链接失效时请重新查询。';
    $('download').href = result.download_url;
    $('download').setAttribute('download', result.filename);
    showState('success');
    if (missing) { $('status-badge').textContent = '部分就绪'; $('status-badge').className = 'status-badge partial'; }
    const warning = missing ? `${missing} 天未获取报表，ZIP 只包含可下载日期。可在下方重试。` : ready.map(row => row.result.warning).filter(Boolean)[0] || '';
    $('download-warning').hidden = !warning;
    $('download-warning').textContent = warning;
    try {
      const selected = $('key-profile').value;
      renderPreferences(await localJSON('/api/preferences'), selected);
      $('platform-hint').textContent = '已记住此应用的平台，下次输入包名自动选择。';
    } catch { /* The report is still available when preference refresh fails. */ }
  } catch (error) {
    $('error-message').textContent = errorText(error);
    showState('error');
  } finally {
    querying = false;
    lockModes(false);
    $('submit').disabled = false;
    $('save-key').disabled = false;
    $('stop-query').hidden = true;
    $('retry-failed').hidden = !context.range || context.rows.every(row => row.state === 'ready');
    $('submit-label').textContent = '查询报表';
    $('submit-icon').textContent = '↗';
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (querying) return;
  const days = chosenDays();
  if (!days || !form.reportValidity()) return;
  const context = {range: $('date-scope').value === 'range', query: {
    api_key: $('key-profile').value ? '' : $('api-key').value.trim(), profile_id: $('key-profile').value,
    platform: $('platform').value, identifier_type: $('identifier-type').value,
    identifier: $('identifier').value.trim(),
    aggregated: document.querySelector('input[name="mode"]:checked').value === 'users'
  }, rows: days.map(date => ({date, state: 'pending'}))};
  batchContext = context;
  if (document.querySelector('input[name="mode"]:checked').value === 'summary') {
    batchContext = null;
    await runSummary(context.query, days);
    return;
  }
  await runQuery(context);
});
$('stop-query').addEventListener('click', () => { stopRequested = true; activeController?.abort(); });
$('retry-failed').addEventListener('click', async () => {
  if (querying || !batchContext) return;
  for (const row of batchContext.rows) if (row.state !== 'ready') { row.state = 'pending'; row.error = ''; row.result = null; }
  await runQuery(batchContext);
});

// Saved keys never return to the page. Unsaved keys are cleared on navigation.
window.addEventListener('pagehide', () => { $('api-key').value = ''; });

function changeReportMode() {
  const summary = document.querySelector('input[name="mode"]:checked').value === 'summary';
  document.querySelector('.workspace').classList.toggle('summary-view', summary);
  $('date-hint').textContent = summary ? `汇总可查当天；最近完整日：${iso(new Date(utcToday.getTime() - 86400000))}（UTC）` : `最近可用日期：${iso(latestReady)}（UTC）`;
  $('availability-note').textContent = summary ? '汇总报表可查询当天，最近 1–2 小时的数据可能尚未完整，建议对账时选择完整 UTC 日。' : '用户级报表在 UTC 日结束 8 小时后提供，即北京时间次日 16:00 后。';
  $('official-docs').href = `https://support.applovin.com/zh/max/reporting-apis/${summary ? 'revenue-reporting-api' : 'user-level-ad-revenue-api'}`;
  $('daily-panel').hidden = true; batchContext = null; showState('empty'); updateReadiness();
}
for (const radio of document.querySelectorAll('input[name="mode"]')) radio.addEventListener('change', changeReportMode);
changeReportMode();
