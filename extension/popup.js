const $ = (selector) => document.querySelector(selector);

const elements = {
  refresh: $('#refresh-button'),
  notice: $('#notice'),
  connectionDot: $('#connection-dot'),
  connectionText: $('#connection-text'),
  identityText: $('#identity-text'),
  reelReadiness: $('#reel-readiness'),
  photoReadiness: $('#photo-readiness'),
  postKind: $('#post-kind'),
  accountSelect: $('#account-select'),
  accountHelp: $('#account-help'),
  managedAccountCount: $('#managed-account-count'),
  managedAccountList: $('#managed-account-list'),
  newAccount: $('#new-account-button'),
  prefillIdentity: $('#prefill-identity-button'),
  accountForm: $('#account-form'),
  accountId: $('#account-id-input'),
  accountName: $('#account-name-input'),
  accountFacebookId: $('#account-facebook-id-input'),
  accountExtension: $('#account-extension-select'),
  accountNotes: $('#account-notes-input'),
  accountEnabled: $('#account-enabled-input'),
  accountCancel: $('#account-cancel-button'),
  accountSave: $('#account-save-button'),
  mediaInput: $('#media-input'),
  mediaHelp: $('#media-help'),
  caption: $('#caption-input'),
  queueToggle: $('#queue-toggle'),
  scheduleInput: $('#schedule-input'),
  scheduleHelp: $('#schedule-help'),
  submit: $('#submit-button'),
  queueCount: $('#queue-count'),
  queueList: $('#queue-list'),
};

const state = {
  baseUrl: 'https://fb.shopcongngheso5.io.vn',
  extensionId: null,
  health: null,
  accounts: [],
  extensions: [],
  isSubmitting: false,
};

function normalizeBaseUrl(value) {
  return String(value || state.baseUrl).replace(/\/+$/, '');
}

function apiUrl(path, query = {}) {
  const url = new URL(path, `${state.baseUrl}/`);
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  });
  return url.toString();
}

async function request(path, options = {}, query = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(apiUrl(path, query), { ...options, headers });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
  if (!response.ok) {
    const detail = data?.detail || data?.error || `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function setNotice(message, kind = 'info') {
  if (!message) {
    elements.notice.hidden = true;
    elements.notice.textContent = '';
    elements.notice.className = 'notice';
    return;
  }
  elements.notice.hidden = false;
  elements.notice.textContent = message;
  elements.notice.className = `notice is-${kind}`;
}

function setBadge(element, label, ready) {
  element.textContent = `${label}: ${ready ? 'sẵn sàng' : 'cần capture tay'}`;
  element.className = `badge ${ready ? 'is-ok' : 'is-warn'}`;
}

function isConnected() {
  return Boolean(state.health?.extension_connected);
}

function currentPostKind() {
  // Keep the popup usable while Chrome reconstructs its DOM after an extension reload.
  return elements.postKind?.value || 'post_reel';
}

function readyForCurrentKind() {
  return currentPostKind() === 'post_reel'
    ? Boolean(state.health?.has_template)
    : Boolean(state.health?.has_photo_template);
}

function updateComposerState() {
  const isPhoto = currentPostKind() === 'post_photos';
  elements.mediaInput.accept = isPhoto ? 'image/jpeg,image/png' : 'video/mp4,video/quicktime';
  elements.mediaInput.multiple = isPhoto;
  elements.mediaHelp.textContent = isPhoto
    ? 'Chọn một hoặc nhiều ảnh .jpg, .jpeg hoặc .png để tạo album.'
    : 'Chọn đúng một video .mp4 hoặc .mov cho Reel.';

  const queued = elements.queueToggle.checked;
  elements.scheduleInput.disabled = !queued;
  elements.submit.textContent = queued ? 'Tạo lịch trong hàng đợi' : 'Đăng ngay';
  elements.scheduleHelp.textContent = queued
    ? 'Chọn thời điểm chạy. Hàng đợi yêu cầu Fanpage đã được lưu trong FBEM.'
    : 'Đăng trực tiếp sau khi upload media. Cần capture mẫu thủ công cho loại bài này.';
}

function renderHealth(health, identity) {
  state.health = health;
  const connected = Boolean(health?.extension_connected);
  elements.connectionDot.className = `status-dot ${connected ? 'is-ok' : 'is-error'}`;
  elements.connectionText.textContent = connected ? 'Extension đã kết nối bridge' : 'Extension chưa kết nối bridge';
  setBadge(elements.reelReadiness, 'Reel', Boolean(health?.has_template));
  setBadge(elements.photoReadiness, 'Ảnh', Boolean(health?.has_photo_template));

  const name = identity?.name || health?.fb_user?.name;
  const id = identity?.id || health?.fb_user?.id;
  elements.identityText.textContent = id
    ? `Đang hoạt động: ${name || `Fanpage ${id}`} (${id})`
    : 'Chưa xác định Fanpage đang hoạt động. Hãy mở một tab facebook.com đã đăng nhập.';
}

function accountMatchesCurrentExtension(account) {
  return !state.extensionId || !account.extension_id || account.extension_id === state.extensionId;
}

function renderExtensionOptions(selectedId = state.extensionId) {
  elements.accountExtension.replaceChildren();
  const current = state.extensions.find((item) => item.id === state.extensionId);
  const all = [...state.extensions];
  if (state.extensionId && !all.some((item) => item.id === state.extensionId)) {
    all.unshift({ id: state.extensionId, connected: false });
  }
  if (!all.length) {
    elements.accountExtension.add(new Option('Extension hiện tại', state.extensionId || ''));
  } else {
    all.forEach((extension) => {
      const suffix = extension.connected ? 'đang kết nối' : 'không kết nối';
      elements.accountExtension.add(new Option(`${extension.id} · ${suffix}`, extension.id));
    });
  }
  elements.accountExtension.value = selectedId || current?.id || state.extensionId || '';
}

function renderManagedAccounts() {
  const accounts = state.accounts.filter(accountMatchesCurrentExtension);
  elements.managedAccountCount.textContent = `${accounts.length} Page`;
  elements.managedAccountList.replaceChildren();
  if (!accounts.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Chưa có Fanpage. Bấm “Thêm Fanpage” để bắt đầu.';
    elements.managedAccountList.append(empty);
    return;
  }

  accounts.forEach((account) => {
    const item = document.createElement('article');
    item.className = `managed-account${account.enabled ? '' : ' is-disabled'}`;
    const title = document.createElement('div');
    title.className = 'managed-account-title';
    title.innerHTML = `<span>${account.name || 'Fanpage'}</span><span>${account.enabled ? 'Đang bật' : 'Đã tắt'}</span>`;
    const meta = document.createElement('p');
    meta.className = 'managed-account-meta';
    meta.textContent = `ID: ${account.facebook_id || '—'} · Extension: ${account.extension_id || 'chưa gán'}${account.notes ? ` · ${account.notes}` : ''}`;
    const actions = document.createElement('div');
    actions.className = 'account-actions';
    actions.append(
      createJobAction('Sửa', '', () => openAccountForm(account)),
      createJobAction(account.enabled ? 'Tắt Page' : 'Bật Page', '', () => toggleAccount(account)),
      createJobAction('Xóa', 'danger', () => deleteAccount(account)),
    );
    item.append(title, meta, actions);
    elements.managedAccountList.append(item);
  });
}

function renderAccounts(accounts) {
  state.accounts = accounts;
  const previous = elements.accountSelect.value;
  const selectable = accounts.filter((account) => account.enabled && accountMatchesCurrentExtension(account));
  elements.accountSelect.replaceChildren();
  elements.accountSelect.add(new Option(
    selectable.length ? 'Chọn Fanpage để đăng / xếp lịch' : 'Chưa có Fanpage đang bật',
    '',
  ));
  selectable.forEach((account) => {
    const label = `${account.name || 'Fanpage'}${account.facebook_id ? ` (${account.facebook_id})` : ''}`;
    const option = new Option(label, account.id);
    option.dataset.extensionId = account.extension_id || '';
    elements.accountSelect.add(option);
  });
  elements.accountSelect.value = selectable.some((account) => account.id === previous) ? previous : '';
  elements.accountHelp.textContent = selectable.length
    ? 'Chọn Fanpage đang bật. Bài đăng ngay dùng Page ID của lựa chọn này; hàng đợi bắt buộc chọn một Page.'
    : 'Chưa có Fanpage đang bật cho extension này. Mở “Quản lý Fanpage” để thêm hoặc bật Page.';
  renderManagedAccounts();
}

function formatWhen(seconds) {
  if (!seconds) return 'chạy ngay';
  const date = new Date(Number(seconds) * 1000);
  return Number.isNaN(date.valueOf()) ? 'không xác định' : date.toLocaleString('vi-VN');
}

function jobDescription(job) {
  const input = job.input || {};
  const media = job.kind === 'post_photos'
    ? `${(input.imageUrls || []).length} ảnh`
    : 'Reel';
  const caption = String(input.caption || '').trim();
  return `${media}${caption ? ` · ${caption.slice(0, 70)}` : ''}`;
}

function renderQueue(jobs) {
  elements.queueCount.textContent = jobs.length ? `${jobs.length} mục` : '';
  elements.queueList.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Chưa có bài trong hàng đợi gần đây.';
    elements.queueList.append(empty);
    return;
  }

  jobs.slice(0, 12).forEach((job) => {
    const item = document.createElement('article');
    item.className = 'queue-item';
    const title = document.createElement('div');
    title.className = 'queue-title';
    title.innerHTML = `<span>${job.kind === 'post_photos' ? 'Ảnh / Album' : 'Reel'}</span><span>${job.status || 'unknown'}</span>`;
    const meta = document.createElement('p');
    meta.className = 'queue-meta';
    meta.textContent = `${jobDescription(job)} · ${formatWhen(job.run_at || job.runAt)}${job.error ? ` · Lỗi: ${job.error}` : ''}`;
    item.append(title, meta);

    if (job.status === 'queued' || job.status === 'failed' || job.status === 'canceled') {
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      if (job.status === 'queued') {
        actions.append(createJobAction('Hủy', 'danger', () => mutateJob(job.id, 'cancel')));
      }
      if (job.status === 'failed' || job.status === 'canceled') {
        actions.append(createJobAction('Thử lại', '', () => mutateJob(job.id, 'retry')));
      }
      item.append(actions);
    }
    elements.queueList.append(item);
  });
}

function createJobAction(label, className, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `queue-action ${className}`;
  button.textContent = label;
  button.addEventListener('click', action);
  return button;
}

async function mutateJob(jobId, action) {
  try {
    setNotice(action === 'cancel' ? 'Đang hủy tác vụ…' : 'Đang đưa tác vụ vào hàng đợi lại…');
    await request(`/api/jobs/${encodeURIComponent(jobId)}/${action}`, { method: 'POST', body: '{}' });
    setNotice(action === 'cancel' ? 'Đã hủy tác vụ đang chờ.' : 'Đã đưa tác vụ vào hàng đợi lại.', 'success');
    await refreshData();
  } catch (error) {
    setNotice(`Không thể cập nhật tác vụ: ${error.message}`, 'error');
  }
}

function openAccountForm(account = null) {
  elements.accountForm.hidden = false;
  elements.accountId.value = account?.id || '';
  elements.accountName.value = account?.name || '';
  elements.accountFacebookId.value = account?.facebook_id || '';
  elements.accountNotes.value = account?.notes || '';
  elements.accountEnabled.checked = account ? Boolean(account.enabled) : true;
  renderExtensionOptions(account?.extension_id || state.extensionId);
  elements.accountSave.textContent = account ? 'Cập nhật Fanpage' : 'Lưu Fanpage';
  elements.accountName.focus();
}

function closeAccountForm() {
  elements.accountForm.reset();
  elements.accountId.value = '';
  elements.accountForm.hidden = true;
}

function accountPayload() {
  return {
    name: elements.accountName.value.trim(),
    facebookId: elements.accountFacebookId.value.trim(),
    extensionId: elements.accountExtension.value || state.extensionId || '',
    accountType: 'page',
    notes: elements.accountNotes.value.trim(),
    enabled: elements.accountEnabled.checked,
  };
}

async function saveAccount(event) {
  event.preventDefault();
  const payload = accountPayload();
  if (!payload.name || !payload.facebookId) {
    setNotice('Hãy nhập tên và Facebook Page ID.', 'error');
    return;
  }
  const accountId = elements.accountId.value;
  try {
    elements.accountSave.disabled = true;
    setNotice(accountId ? 'Đang cập nhật Fanpage…' : 'Đang lưu Fanpage…', 'info');
    await request(accountId ? `/api/accounts/${encodeURIComponent(accountId)}` : '/api/accounts', {
      method: accountId ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    });
    closeAccountForm();
    setNotice(accountId ? 'Đã cập nhật Fanpage.' : 'Đã thêm Fanpage.', 'success');
    await refreshData();
  } catch (error) {
    setNotice(`Không thể lưu Fanpage: ${error.message}`, 'error');
  } finally {
    elements.accountSave.disabled = false;
  }
}

async function toggleAccount(account) {
  try {
    setNotice(`Đang ${account.enabled ? 'tắt' : 'bật'} Fanpage…`, 'info');
    await request(`/api/accounts/${encodeURIComponent(account.id)}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: account.name,
        facebookId: account.facebook_id,
        extensionId: account.extension_id || state.extensionId || '',
        accountType: account.account_type || 'page',
        notes: account.notes || '',
        enabled: !account.enabled,
      }),
    });
    setNotice(`Đã ${account.enabled ? 'tắt' : 'bật'} Fanpage.`, 'success');
    await refreshData();
  } catch (error) {
    setNotice(`Không thể cập nhật Fanpage: ${error.message}`, 'error');
  }
}

async function deleteAccount(account) {
  if (!confirm(`Xóa Fanpage “${account.name || account.facebook_id}”? Các tác vụ đang chờ/running sẽ chặn thao tác này.`)) return;
  try {
    setNotice('Đang xóa Fanpage…', 'info');
    await request(`/api/accounts/${encodeURIComponent(account.id)}`, { method: 'DELETE' });
    if (elements.accountId.value === account.id) closeAccountForm();
    setNotice('Đã xóa Fanpage.', 'success');
    await refreshData();
  } catch (error) {
    setNotice(`Không thể xóa Fanpage: ${error.message}`, 'error');
  }
}

async function prefillActiveIdentity() {
  const extensionId = elements.accountExtension.value || state.extensionId;
  if (!extensionId) {
    setNotice('Không xác định extension đang dùng. Hãy reload extension rồi thử lại.', 'error');
    return;
  }
  try {
    setNotice('Đang đọc Fanpage đang hoạt động…', 'info');
    const identity = await request(`/api/extensions/${encodeURIComponent(extensionId)}/identity`);
    if (!identity?.id) throw new Error('Không tìm thấy Fanpage hiện tại. Hãy mở facebook.com và chuyển sang Page cần dùng.');
    openAccountForm();
    elements.accountExtension.value = extensionId;
    elements.accountFacebookId.value = identity.id;
    elements.accountName.value = identity.name || `Fanpage ${identity.id}`;
    setNotice('Đã điền Fanpage đang hoạt động. Hãy kiểm tra và bấm Lưu Fanpage.', 'success');
  } catch (error) {
    setNotice(`Không thể lấy Fanpage đang hoạt động: ${error.message}`, 'error');
  }
}

async function loadConfig() {
  const saved = await chrome.storage.local.get(['serverUrl', 'extensionId']);
  state.baseUrl = normalizeBaseUrl(saved.serverUrl || state.baseUrl);
  state.extensionId = saved.extensionId || null;

  // Ask the background service worker for its live WS state. The remote health
  // endpoint may be behind a tunnel or report a different extension session.
  try {
    const live = await chrome.runtime.sendMessage({ type: 'get_popup_status' });
    if (live?.serverUrl) state.baseUrl = normalizeBaseUrl(live.serverUrl);
    if (live?.extensionId) state.extensionId = live.extensionId;
    return live || null;
  } catch (_) {
    return null;
  }
}

async function refreshData() {
  elements.refresh.disabled = true;
  try {
    const liveStatus = await loadConfig();
    const health = await request('/api/health', {}, { extension_id: state.extensionId });
    // A proxy/tunnel can return stale extension health even though this popup's
    // own background service worker has an open WebSocket to the bridge.
    if (liveStatus?.connected) health.extension_connected = true;
    // Some older bridge deployments omit optional fields. Normalize before any
    // renderer reads them so status rendering never masks a healthy connection.
    health.has_template = Boolean(health.has_template);
    health.has_photo_template = Boolean(health.has_photo_template);
    health.extensions = Array.isArray(health.extensions) ? health.extensions : [];
    health.fb_user = health.fb_user && typeof health.fb_user === 'object' ? health.fb_user : null;
    let identity = null;
    if (state.extensionId && health.extension_connected) {
      try {
        identity = await request(`/api/extensions/${encodeURIComponent(state.extensionId)}/identity`);
      } catch (_) {
        // Health has the last known identity; do not hide the rest of the popup on a transient identity failure.
      }
    }
    const [accountsResponse, jobsResponse, extensionsResponse] = await Promise.all([
      request('/api/accounts'),
      request('/api/jobs', {}, { limit: 30 }),
      request('/api/extensions'),
    ]);
    state.extensions = Array.isArray(extensionsResponse.items) ? extensionsResponse.items : [];
    const accounts = Array.isArray(accountsResponse.items) ? accountsResponse.items : [];
    renderHealth(health, identity);
    renderExtensionOptions(elements.accountExtension.value || state.extensionId);
    renderAccounts(accounts);
    renderQueue(Array.isArray(jobsResponse.items) ? jobsResponse.items : []);
    if (!health.extension_connected) {
      setNotice('Bridge chưa nhận extension trên server. Hãy kiểm tra URL server cấu hình và đảm bảo bridge đang chạy.', 'error');
    } else if (!readyForCurrentKind()) {
      setNotice('Loại bài này chưa có mẫu capture. Hãy đăng tay một bài cùng loại trên Facebook trước.', 'info');
    } else {
      setNotice('');
    }
  } catch (error) {
    state.extensions = [];
    renderHealth(null, null);
    renderExtensionOptions(state.extensionId);
    renderAccounts([]);
    renderQueue([]);
    setNotice(`Không thể kết nối server FBEM (${state.baseUrl}): ${error.message}`, 'error');
  } finally {
    elements.refresh.disabled = false;
  }
}

function selectedAccount() {
  return state.accounts.find((account) => account.id === elements.accountSelect.value) || null;
}

function validateFiles(kind, files) {
  if (!files.length) throw new Error('Hãy chọn tệp media để đăng.');
  const names = Array.from(files).map((file) => file.name.toLowerCase());
  if (kind === 'post_reel') {
    if (files.length !== 1 || !names.every((name) => name.endsWith('.mp4') || name.endsWith('.mov'))) {
      throw new Error('Reel yêu cầu đúng một tệp .mp4 hoặc .mov.');
    }
  } else if (!names.every((name) => /\.(jpe?g|png)$/.test(name))) {
    throw new Error('Ảnh/Album chỉ hỗ trợ các tệp .jpg, .jpeg hoặc .png.');
  }
}

async function stageFiles(files) {
  const uploaded = [];
  for (const file of files) {
    const body = new FormData();
    body.set('file', file, file.name);
    const response = await request('/api/media/upload', { method: 'POST', body });
    uploaded.push(response);
  }
  return uploaded;
}

function runAtFromInput() {
  const value = elements.scheduleInput.value;
  if (!value) throw new Error('Hãy chọn thời gian chạy trong hàng đợi.');
  const time = new Date(value).getTime();
  if (!Number.isFinite(time) || time <= Date.now()) throw new Error('Thời gian chạy phải ở trong tương lai.');
  return Math.floor(time / 1000);
}

function createIdempotencyKey(files) {
  const fingerprint = Array.from(files).map((file) => `${file.name}:${file.size}:${file.lastModified}`).join('|');
  return `popup-${Date.now()}-${crypto.randomUUID?.() || Math.random().toString(36).slice(2)}-${fingerprint.length}`;
}

async function submitPost() {
  if (state.isSubmitting) return;
  const kind = currentPostKind();
  const files = elements.mediaInput.files;
  const queued = elements.queueToggle.checked;
  const account = selectedAccount();

  try {
    if (!isConnected()) throw new Error('Extension chưa kết nối bridge.');
    if (!readyForCurrentKind()) throw new Error('Chưa có mẫu capture cho loại bài này. Hãy đăng tay một bài cùng loại trước.');
    validateFiles(kind, files);
    if (!account) throw new Error('Hãy chọn Fanpage đã lưu trước khi đăng hoặc tạo lịch.');
    if (queued) runAtFromInput();

    state.isSubmitting = true;
    elements.submit.disabled = true;
    setNotice('Đang tải media vào FBEM…', 'info');
    const uploaded = await stageFiles(files);
    const input = kind === 'post_reel'
      ? { videoUrl: uploaded[0].url, caption: elements.caption.value }
      : { imageUrls: uploaded.map((item) => item.url), caption: elements.caption.value };
    const selectedExtensionId = account.extension_id || state.extensionId || undefined;

    if (queued) {
      setNotice('Đang tạo tác vụ hàng đợi…', 'info');
      await request('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          accountId: account.id,
          extensionId: selectedExtensionId,
          kind,
          input,
          runAt: runAtFromInput(),
          idempotencyKey: createIdempotencyKey(files),
        }),
      });
      setNotice('Đã tạo lịch trong hàng đợi.', 'success');
    } else {
      setNotice('Đang gửi bài đăng tới Facebook…', 'info');
      const endpoint = kind === 'post_reel' ? '/post-reel' : '/post-photos';
      const payload = kind === 'post_reel'
        ? { ...input, pageId: account.facebook_id, extensionId: selectedExtensionId }
        : { ...input, pageId: account.facebook_id, extensionId: selectedExtensionId };
      const response = await request(endpoint, { method: 'POST', body: JSON.stringify(payload) });
      const link = response.permalinkUrl ? ` Xem bài: ${response.permalinkUrl}` : '';
      setNotice(`Đăng bài thành công.${link}`, 'success');
    }
    elements.mediaInput.value = '';
    elements.caption.value = '';
    await refreshData();
  } catch (error) {
    setNotice(`Không thể xử lý bài đăng: ${error.message}`, 'error');
  } finally {
    state.isSubmitting = false;
    elements.submit.disabled = false;
  }
}

elements.postKind.addEventListener('change', () => {
  elements.mediaInput.value = '';
  updateComposerState();
  if (state.health && !readyForCurrentKind()) {
    setNotice('Loại bài này chưa có mẫu capture. Hãy đăng tay một bài cùng loại trên Facebook trước.', 'info');
  }
});
elements.queueToggle.addEventListener('change', updateComposerState);
elements.refresh.addEventListener('click', refreshData);
elements.submit.addEventListener('click', submitPost);
elements.newAccount.addEventListener('click', () => openAccountForm());
elements.prefillIdentity.addEventListener('click', prefillActiveIdentity);
elements.accountCancel.addEventListener('click', closeAccountForm);
elements.accountForm.addEventListener('submit', saveAccount);

updateComposerState();
renderExtensionOptions();
refreshData();
