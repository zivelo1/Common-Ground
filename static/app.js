'use strict';

// Remove the launch credential before a request, a render, or any other work.
const launchFragment = new URLSearchParams(window.location.hash.slice(1));
let launchToken = launchFragment.get('token');
if (launchToken !== null) {
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
}
launchFragment.delete('token');

const $ = (id) => document.getElementById(id);
const LIMITS = Object.freeze({ title: 200, name: 200, body: 16000, source: 4000, evidence: 16000, participantId: 64, checkNote: 4000 });
const CHECK_OUTCOMES = Object.freeze([{ value: 'updated', label: 'Knowledge updated' }, { value: 'nothing_changed', label: 'Nothing changed' }]);
const PRODUCT_NAME = 'Common Ground';
const ACTIVE_TASK_FILTER = 'active';
const TASK_STATUS_LABELS = Object.freeze({ open: 'Open', working: 'In progress', blocked: 'Blocked', done: 'Done' });
const POLL_INTERVAL_MS = 2000;
const SEARCH_DELAY_MS = 300;
const CONVERSATION_TEXT_SIZES = Object.freeze([12, 14, 16, 18, 20, 22]);
const DEFAULT_CONVERSATION_TEXT_SIZE = 14;
const MESSAGE_RENDER_PAGE_SIZE = 50;
const MAX_HISTORY_REQUESTS = 20;
const CONVERSATION_TEXT_SIZE_KEY = 'common-ground.conversation-text-size';
// The resizable message box: the height a reader chose is kept per browser, like the text size.
const COMPOSER_HEIGHT_KEY = 'common-ground.composer-height';
const COMPOSER_RESIZE_STEP = 24;          // px per arrow key press on the grip
// The whole message area (box, tools, Send row) takes at most this share of the window, so Send stays on screen
// and part of the conversation stays visible.
const COMPOSER_MAX_WINDOW_SHARE = 0.6;
const COMPOSER_MAX_PHONE_SHARE = 0.5;     // on a phone-width screen
const PHONE_WIDTH_QUERY = '(max-width: 540px)';
const EMOJI_CHOICES = Object.freeze([
  ['👍', 'Thumbs up'], ['✅', 'Done'], ['❓', 'Question'], ['⚠️', 'Warning'],
  ['🙂', 'Smile'], ['🙏', 'Thank you'], ['💡', 'Idea'], ['📊', 'Chart'],
  ['🎯', 'Target'], ['🚀', 'Launch'], ['👀', 'Watching'], ['❤️', 'Heart'],
]);
let conversationTextSize = DEFAULT_CONVERSATION_TEXT_SIZE;
const app = {
  state: null,
  view: 'conversations',
  channelId: null,
  caches: new Map(),
  bookmarks: new Set(),
  uploadingImage: false,
  uploadingVoice: false,
  voice: null,                 // acquisition/recording belongs to one account and conversation
  voicePlayers: new Map(),
  drafts: new Map(),
  outbox: [],
  draftVersions: new Map(),
  savedDrafts: new Map(),
  unsavedDrafts: new Set(),
  preferenceVersion: 0,
  savedPreferences: {},
  personalQueue: Promise.resolve(),
  personalGeneration: 0,
  pendingPersonal: 0,
  personalErrors: new Map(),
  filters: { decisions: '', tasks: '' },
  taskFilters: { status: ACTIVE_TASK_FILTER, owner: '', query: '' },
  signature: '',
  messageSignature: '',
  timer: null,
  polling: false,
  connectionError: false,
  searchTimer: null,
  searchSequence: 0,
  authenticated: false,
  projectTab: 'joining',
  projectScope: null,
  brief: null,
  briefSignature: '',
  briefRequest: null,
  briefError: '',
  dialogSequence: 0,
  sourceScan: null,
};

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined && text !== null) result.textContent = String(text);
  return result;
}
function button(text, className, action) {
  const result = node('button', className, text);
  result.type = 'button';
  if (action) result.addEventListener('click', action);
  return result;
}
function participantAction(action, participant, handler) {
  const label = `${action === 'rename' ? 'Rename' : 'Remove'} ${participant.name}`;
  const control = button('', `icon-button participant-action participant-${action}`, handler);
  control.setAttribute('aria-label', label);
  control.title = label;
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', action === 'rename'
    ? 'M15 5l4 4M4 20l4-1 12-12a2.8 2.8 0 0 0-4-4L4 15z'
    : 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7');
  icon.append(path);
  control.append(icon);
  return control;
}
function plain(value) {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
function errorText(error) {
  return error instanceof Error ? error.message : 'The request could not be completed.';
}
function announce(text) { $('screen-reader-status').textContent = text; }
function notify(text, isError = false) {
  delete $('notice').dataset.personalSaveError;
  $('notice-text').textContent = text;
  $('notice').classList.toggle('error', isError);
  $('notice').hidden = false;
}
function person(id, name) {
  return app.state?.participants.find((item) => item.id === id) || { id, name: name || id || 'Unknown participant', kind: 'agent' };
}
function workspaceBrand(workspace = app.state?.workspace) { return workspace?.brand_name || PRODUCT_NAME; }
function channelName(id) {
  return app.state?.channels.find((item) => item.id === id)?.title || 'Conversation';
}
function avatar(participant, size = '') {
  const name = plain(participant.name || participant.id);
  const words = name.trim().split(/\s+/).filter(Boolean);
  const initials = words.length > 1 ? words[0][0] + words[words.length - 1][0] : [...(words[0] || '?')].slice(0, 2).join('');
  let hash = 0;
  for (const char of String(participant.id || name)) hash = (hash + char.codePointAt(0)) % 5;
  let result;
  if (participant.logo) {
    // The provider's local logo beside the name (Collaboration); the name text stays next to it, so the image is decorative.
    result = node('span', `avatar logo ${size}`);
    const image = node('img');
    image.src = `/static/logos/${encodeURIComponent(participant.logo)}.svg`;
    image.alt = '';
    result.append(image);
  } else {
    result = node('span', `avatar tone-${hash} ${participant.kind === 'human' ? 'human' : ''} ${size}`, initials.toLocaleUpperCase());
  }
  result.setAttribute('aria-hidden', 'true');
  result.title = name;
  return result;
}
function parseDate(value) {
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
const DISPLAY_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;
const displayDateFormat = new Intl.DateTimeFormat('en-GB', { timeZone: DISPLAY_TIME_ZONE, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
function displayDateParts(value) {
  const date = parseDate(value);
  return date ? Object.fromEntries(displayDateFormat.formatToParts(date).map(part => [part.type, part.value])) : null;
}
function formatDate(value) {
  const parts = displayDateParts(value);
  return parts ? `${parts.day}.${parts.month}.${parts.year}` : plain(value);
}
function formatDateTime(value) {
  const parts = displayDateParts(value);
  return parts ? `${parts.day}.${parts.month}.${parts.year} - ${parts.hour}:${parts.minute}` : plain(value);
}
function dateLabel(value) { return parseDate(value) ? formatDate(value) : 'Messages'; }
function stableMetadata(key, value) { return ['checked_at', 'generated_at', 'age_seconds'].includes(key) ? undefined : value; }
function statusBadge(status) {
  const label = status === 'approved' ? 'Human approved' : status === 'verified' ? 'Independently verified' : String(status).replace(/^./, (char) => char.toUpperCase());
  return node('span', `status-badge status-${status}`, label);
}

async function api(path, data, binary = false) {
  const options = { credentials: 'same-origin', headers: { Accept: binary ? 'application/gzip' : 'application/json' } };
  if (data !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-Collab-Request'] = '1';
    options.body = JSON.stringify(data);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('Cannot reach the local server. Keep this page open and try again.'); }
  if (binary && response.ok) return response.blob();
  let result;
  try { result = await response.json(); }
  catch { throw new Error('The local server returned an unreadable response.'); }
  if (!response.ok) {
    const detail = typeof result.error === 'string' ? result.error : typeof result.message === 'string' ? result.message : `Request failed (${response.status}).`;
    const error = new Error(detail);
    error.status = response.status;
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw error;
  }
  return result;
}

function showLogin() {
  cancelVoiceRecording();
  resetVoicePlayers();
  app.uploadingVoice = false;
  resetAdapters();
  resetBackups();
  resetRules();
  app.authenticated = false;
  app.personalGeneration++;
  app.pendingPersonal = 0;
  clearTimeout(app.timer);
  $('app-shell').hidden = true;
  $('login-screen').hidden = false;
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  $('access-token').focus();
}
async function login(token) {
  $('login-submit').disabled = true;
  $('login-submit').textContent = 'Opening…';
  $('login-error').hidden = true;
  $('access-token').value = '';
  try {
    await api('/api/login', { token });
    await start();
  } catch (error) {
    showLogin();
    $('login-error').textContent = errorText(error);
    $('login-error').hidden = false;
  } finally {
    token = null;
    $('login-submit').disabled = false;
    $('login-submit').textContent = 'Open workspace →';
  }
}
function personalStatus() {
  let status = $('personal-save-status');
  if (!status) {
    status = node('p', 'field-hint'); status.id = 'personal-save-status';
    status.setAttribute('role', 'status'); $('composer-area').append(status);
  }
  const saveError = app.personalErrors.values().next().value || '';
  const notSaved = saveError || app.unsavedDrafts.size || app.outbox.some((item) => !item.durable) || JSON.stringify(preferenceValues()) !== JSON.stringify(app.savedPreferences);
  status.textContent = app.pendingPersonal ? 'Saving to this workspace…' : notSaved ? `Not saved yet${saveError ? ` · ${saveError}` : ''}. Keep this tab open.` : 'Saved to this workspace.';
  status.dataset.saved = String(!app.pendingPersonal && !notSaved);
  if (status.dataset.saved === 'true' && $('notice').dataset.personalSaveError) {
    $('notice').hidden = true;
    delete $('notice').dataset.personalSaveError;
  }
}
function queuePersonal(operation, key) {
  const generation = app.personalGeneration;
  app.pendingPersonal++;
  personalStatus();
  const work = app.personalQueue.then(async () => {
    if (!app.authenticated || generation !== app.personalGeneration) throw new Error('Account changed before saving.');
    const result = await operation();
    if (generation !== app.personalGeneration) throw new Error('Account changed while saving.');
    app.personalErrors.delete(key);
    return result;
  }).finally(() => {
    if (generation === app.personalGeneration) { app.pendingPersonal--; personalStatus(); }
  });
  // Keep later saves usable after a failure, while showing the failure explicitly.
  app.personalQueue = work.catch((error) => {
    if (generation === app.personalGeneration) { app.personalErrors.set(key, errorText(error)); personalStatus(); notify(`Could not save your personal state. ${errorText(error)} Keep this tab open.`, true); $('notice').dataset.personalSaveError = 'true'; }
  });
  return work;
}
function applySavedDraft(draft, replaceLocal = false) {
  if (!draft) return;
  app.draftVersions.set(draft.channel_id, draft.version);
  app.savedDrafts.set(draft.channel_id, draft.body);
  if (replaceLocal) app.drafts.set(draft.channel_id, draft.body);
  if (app.drafts.get(draft.channel_id) === draft.body) app.unsavedDrafts.delete(draft.channel_id);
}
function persistDraft(channelId) {
  return queuePersonal(async () => {
    const body = app.drafts.get(channelId) || '';
    if (body === (app.savedDrafts.get(channelId) || '')) { app.unsavedDrafts.delete(channelId); return; }
    const draft = await api('/api/personal/draft', { channel_id: channelId, body, version: app.draftVersions.get(channelId) || 0 });
    applySavedDraft(draft);
  }, `draft:${channelId}`);
}
function preferenceValues() {
  const values = { theme: themePreference, view: ['conversations', 'brief', 'decisions', 'tasks'].includes(app.view) ? app.view : (app.savedPreferences.view || 'conversations'), project_scope: app.projectScope || '', project_tab: app.projectTab };
  if (app.channelId) values.channel_id = app.channelId;
  return values;
}
function persistPreferences() {
  if (!app.authenticated) return;
  return queuePersonal(async () => {
    const values = preferenceValues();
    if (JSON.stringify(values) === JSON.stringify(app.savedPreferences)) return;
    const saved = await api('/api/personal/preferences', { values, version: app.preferenceVersion });
    app.preferenceVersion = saved.version; app.savedPreferences = saved.values;
  }, 'preferences');
}
function queuedMessage(row) {
  return { ...row, author_id: app.state.me.id, author_name: app.state.me.name, durable: true, status: 'failed', error: 'Delivery not confirmed. Retry to confirm delivery.' };
}
async function importLegacyOutbox() {
  const key = `agent-room.outbox.v1:${app.state.me.id}`;
  let rows;
  try { rows = JSON.parse(sessionStorage.getItem(key) || '[]'); }
  catch { notify('Old retry storage could not be read; do not clear this tab before reviewing it.', true); return; }
  if (!Array.isArray(rows) || !rows.length) return;
  const remaining = [...rows];
  for (const row of rows) {
    if (!row || typeof row.body !== 'string' || typeof row.client_id !== 'string' || !app.state.channels.some((channel) => channel.id === row.channel_id)) {
      notify('An old retry record could not be imported. Its browser copy is retained.', true); continue;
    }
    try {
      const receipt = await api('/api/personal/enqueue', { channel_id: row.channel_id, body: row.body, client_id: row.client_id });
      if (receipt.outbox && !app.outbox.some((item) => item.client_id === row.client_id)) app.outbox.push(queuedMessage(receipt.outbox));
      remaining.splice(remaining.indexOf(row), 1);
      sessionStorage.setItem(key, JSON.stringify(remaining));
    } catch (error) { notify(`An old retry record remains in this tab. ${errorText(error)}`, true); }
  }
}
async function start() {
  cancelVoiceRecording();
  resetVoicePlayers();
  app.uploadingVoice = false;
  resetAdapters();
  resetBackups();
  resetRules();
  await app.personalQueue;
  const state = await api('/api/state');
  const personal = await api('/api/personal');
  const savedMessages = await api('/api/bookmarks');
  app.bookmarks = new Set(savedMessages.messages.map(message => message.id));
  app.personalGeneration++;
  app.pendingPersonal = 0;
  app.personalErrors.clear();
  app.authenticated = true;
  app.state = state;
  app.signature = '';
  app.caches.clear();
  app.drafts.clear();
  app.draftVersions.clear(); app.savedDrafts.clear(); app.unsavedDrafts.clear();
  for (const draft of personal.drafts) applySavedDraft(draft, true);
  app.preferenceVersion = personal.preferences.version; app.savedPreferences = personal.preferences.values;
  app.outbox = personal.outbox.map(queuedMessage);
  app.channelId = state.channels.find((channel) => channel.kind === 'shared')?.id || state.channels[0]?.id || null;
  app.view = state.me.kind === 'agent' ? 'brief' : 'conversations';
  app.projectScope = state.channels.find((channel) => channel.kind === 'shared')?.id || state.channels[0]?.id || '';
  app.projectTab = 'joining';
  const preferences = personal.preferences.values;
  if (preferences.channel_id && state.channels.some((channel) => channel.id === preferences.channel_id)) app.channelId = preferences.channel_id;
  if (preferences.view) app.view = preferences.view;
  if (preferences.project_scope !== undefined) app.projectScope = preferences.project_scope;
  if (preferences.project_tab) app.projectTab = preferences.project_tab;
  if (preferences.theme) { themePreference = preferences.theme; applyTheme(); }
  app.brief = null;
  app.briefSignature = '';
  app.briefError = '';
  app.sourceScan = null;
  app.briefRequest = null;
  $('search-input').value = '';
  $('login-screen').hidden = true;
  $('app-shell').hidden = false;
  $('notice').hidden = true;
  await importLegacyOutbox();
  persistPreferences();
  personalStatus();
  applyState(state, true);
  if (app.outbox.length) notify('You have an unsent message. Open its conversation to retry it.', true);
  if (app.view === 'conversations' && app.channelId) await fetchMessages(app.channelId).catch((error) => notify(errorText(error), true));
  if (app.view === 'brief') await refreshBrief().catch((error) => notify(errorText(error), true));
  schedulePoll();
}
function applyState(state, force = false) {
  const signature = JSON.stringify(state, stableMetadata);
  app.state = state;
  if (!state.channels.some((channel) => channel.id === app.channelId)) app.channelId = state.channels[0]?.id || null;
  if (app.projectScope && !state.channels.some((channel) => channel.id === app.projectScope)) { app.projectScope = state.channels.find((channel) => channel.kind === 'shared')?.id || ''; app.brief = null; app.briefSignature = ''; }
  if (!force && signature === app.signature) return;
  app.signature = signature;
  renderSidebar();
  if (app.view !== 'search') renderView();
  else if ($('search-input').value.trim()) runSearch($('search-input').value.trim());
}
async function refreshState() {
  const state = await api('/api/state');
  applyState(state);
}
function schedulePoll() {
  clearTimeout(app.timer);
  if (app.authenticated) app.timer = setTimeout(poll, POLL_INTERVAL_MS);
}
async function poll() {
  if (!app.authenticated || app.polling) return;
  app.polling = true;
  try {
    await refreshState();
    if (app.view === 'conversations' && app.channelId) await fetchMessages(app.channelId);
    if (app.view === 'brief' && app.projectTab === 'joining') await refreshBrief();
    if (app.view === 'brief' && app.projectTab === 'knowledge') await observeKnowledge();
    if (app.view === 'providers' && !app.adapters.busy) await refreshAdapters();
    if (app.view === 'backups' && !app.backups.busy) await refreshBackups();
    if (app.connectionError) notify('Connected to the local server again.');
    app.connectionError = false;
  } catch (error) {
    if (app.authenticated && !app.connectionError) notify(errorText(error), true);
    app.connectionError = true;
  } finally {
    app.polling = false;
    schedulePoll();
  }
}

function renderSidebar() {
  $('workspace-title').textContent = app.state.workspace.title;
  document.title = `${workspaceBrand()} · ${app.state.workspace.project_label || app.state.workspace.title}`;
  $('brand-name').textContent = workspaceBrand();
  $('project-label').textContent = app.state.workspace.project_label || 'Local workspace';
  $('conversation-count').textContent = app.state.channels.length;
  $('decision-count').textContent = app.state.decisions.length;
  $('knowledge-count').textContent = Array.isArray(app.state.knowledge) ? app.state.knowledge.length : '—';
  $('task-count').textContent = app.state.tasks.filter((task) => task.status !== 'done').length;
  const isHuman = app.state.me.kind === 'human';
  $('new-channel').hidden = !isHuman;
  $('new-participant').hidden = !isHuman;
  $('backups-nav').hidden = !isHuman;
  const channelList = $('channel-list');
  channelList.replaceChildren();
  const groups = [['shared', 'Shared workspace', '#'], ['private', 'Private conversations', '◇'], ['group', 'Group conversations', '◫']];
  for (const [kind, label, symbol] of groups) {
    const channels = app.state.channels.filter((channel) => channel.kind === kind);
    if (!channels.length) continue;
    channelList.append(node('p', 'channel-category', label));
    for (const channel of channels) {
      const item = button('', `channel-button${app.view === 'conversations' && channel.id === app.channelId ? ' active' : ''}`, () => selectChannel(channel.id));
      item.setAttribute('aria-current', app.view === 'conversations' && channel.id === app.channelId ? 'page' : 'false');
      const icon = node('span', 'channel-symbol', symbol);
      icon.setAttribute('aria-hidden', 'true');
      paintRhombus(icon, channel);
      item.append(icon, node('span', 'channel-name', channel.title));
      if (channel.unread > 0) {
        const unread = node('span', 'unread-badge', channel.unread > 99 ? '99+' : channel.unread);
        unread.setAttribute('aria-label', `${channel.unread} unread messages`);
        item.append(unread);
      }
      if (channel.unread_mentions > 0) {
        const mentions = node('span', 'mention-badge', '@' + (channel.unread_mentions > 99 ? '99+' : channel.unread_mentions));
        mentions.setAttribute('aria-label', `${channel.unread_mentions} unread mention${channel.unread_mentions === 1 ? '' : 's'} of you`);
        item.append(mentions);
      }
      channelList.append(item);
    }
  }
  if (!app.state.channels.length) channelList.append(node('p', 'channel-empty', 'Create a conversation to get started.'));
  $('participant-list').replaceChildren();
  // Removed participants keep their history; they are listed apart, and only a human can restore them. Only an
  // explicit active: false counts as removed (a record without the field comes from before removal existed).
  const removed = app.state.participants.filter((participant) => participant.active === false);
  for (const participant of app.state.participants.filter((item) => item.active !== false)) {
    const item = node('div', 'participant');
    const copy = node('div', 'participant-copy');
    const name = node('p', 'participant-name', participant.name + (participant.id === app.state.me.id ? ' (you)' : ''));
    name.dir = 'auto';
    copy.append(name, node('p', 'participant-detail', participant.kind === 'human' ? 'Human · workspace owner' : 'Agent'));
    item.append(avatar(participant, 'small'), copy);
    if (app.state.me.kind === 'human') {
      const actions = node('div', 'participant-actions');
      const rename = participantAction('rename', participant, () => showRenameParticipant(participant));
      actions.append(rename);
      if (participant.id !== app.state.me.id) {
        const remove = participantAction('remove', participant, () => showParticipantStatus(participant, false));
        actions.append(remove);
      }
      item.append(actions);
    }
    $('participant-list').append(item);
  }
  if (removed.length) $('participant-list').append(node('p', 'participant-group', 'Removed'));
  for (const participant of removed) {
    const item = node('div', 'participant removed');
    const copy = node('div', 'participant-copy');
    const name = node('p', 'participant-name', participant.name);
    name.dir = 'auto';
    copy.append(name, node('p', 'participant-detail', 'History kept'));
    item.append(avatar(participant, 'small'), copy);
    if (app.state.me.kind === 'human') {
      const restore = button('Restore', 'button subtle compact participant-restore', () => showParticipantStatus(participant, true));
      restore.setAttribute('aria-label', `Restore ${participant.name}`);
      item.append(restore);
    }
    $('participant-list').append(item);
  }
  const copy = node('div');
  copy.append(node('strong', '', app.state.me.name), node('span', '', app.state.me.kind === 'human' ? 'Your workspace' : 'Agent account'));
  $('current-user').replaceChildren(avatar({ ...app.state.me, logo: person(app.state.me.id).logo }), copy);
  updateNavigation();
}
function updateNavigation() {
  for (const nav of document.querySelectorAll('[data-view]')) {
    const active = nav.dataset.view === app.view;
    nav.classList.toggle('active', active);
    if (active) nav.setAttribute('aria-current', 'page'); else nav.removeAttribute('aria-current');
  }
  for (const item of $('channel-list').querySelectorAll('.channel-button')) {
    // Channel entries are rebuilt when the selected conversation changes.
    if (app.view !== 'conversations') { item.classList.remove('active'); item.removeAttribute('aria-current'); }
  }
  $('view-breadcrumb').textContent = app.view === 'brief' ? 'Project brief' : app.view === 'search' ? 'Search' : app.view[0].toUpperCase() + app.view.slice(1);
}
function rememberDraft() {
  if (app.view === 'conversations' && app.channelId && app.drafts.get(app.channelId) !== $('message-input').value) { app.drafts.set(app.channelId, $('message-input').value); app.unsavedDrafts.add(app.channelId); persistDraft(app.channelId); }
}
function closeSidebar() {
  $('app-shell').classList.remove('sidebar-open');
  $('sidebar-backdrop').hidden = true;
  $('open-sidebar').setAttribute('aria-expanded', 'false');
  $('sidebar').inert = matchMedia('(max-width: 850px)').matches;
  document.querySelector('.main-panel').inert = false;
}
function setView(view) {
  if (view !== 'conversations') resetVoicePlayers();
  rememberDraft();
  app.view = view;
  persistPreferences();
  app.searchSequence++;
  clearTimeout(app.searchTimer);
  $('search-input').value = '';
  closeSidebar();
  renderSidebar();
  renderView();
  if (view === 'conversations' && app.channelId) fetchMessages(app.channelId).catch((error) => notify(errorText(error), true));
  if (view === 'providers') refreshAdapters().catch((error) => { app.adapters.error = errorText(error); renderAdapterView(); });
  if (view === 'rules') refreshRules().catch((error) => { app.rules.error = errorText(error); renderRulesView(); });
  if (view === 'backups') refreshBackups().catch((error) => { app.backups.error = errorText(error); renderBackupView(); });
  if (view === 'brief') (app.projectTab === 'joining' ? refreshBrief() : observeKnowledge()).catch((error) => notify(errorText(error), true));
}
function selectChannel(id) {
  if (id !== app.channelId) resetVoicePlayers();
  rememberDraft();
  app.channelId = id;
  app.view = 'conversations';
  persistPreferences();
  app.searchSequence++;
  clearTimeout(app.searchTimer);
  $('search-input').value = '';
  closeSidebar();
  renderSidebar();
  renderView();
  fetchMessages(id).catch((error) => notify(errorText(error), true));
}
function setHeader(eyebrow, title, description, action) {
  $('content-eyebrow').textContent = eyebrow;
  $('content-title').textContent = title;
  $('content-title').dir = 'auto';
  $('content-description').textContent = description;
  $('header-action').replaceChildren();
  if (action) $('header-action').append(action);
}
function emptyState(symbol, title, description, label, action) {
  const result = node('div', 'empty-state');
  const illustration = node('div', 'empty-illustration', symbol);
  illustration.setAttribute('aria-hidden', 'true');
  result.append(illustration, node('h2', '', title), node('p', '', description));
  if (label && action) result.append(button(label, 'button primary', action));
  return result;
}
function loading(text = 'Loading…') {
  const result = node('div', 'loading');
  const spinner = node('span', 'spinner');
  spinner.setAttribute('aria-hidden', 'true');
  result.append(spinner, node('span', '', text));
  return result;
}
function renderView() {
  if (!app.state || app.view === 'search') return;
  const oldList = $('message-list');
  const retainPosition = oldList && app.renderedChannelId === app.channelId && app.view === 'conversations' ? { top: oldList.scrollTop, bottom: oldList.scrollHeight - oldList.scrollTop - oldList.clientHeight < 100 } : null;
  const oldBoard = $('view-content').querySelector('.board, .project-board');
  const boardTop = oldBoard && app.renderedView === app.view ? oldBoard.scrollTop : 0;
  updateNavigation();
  $('composer-area').hidden = true;
  $('view-content').replaceChildren();
  if (app.view === 'providers') renderAdapterView();
  else if (app.view === 'backups') renderBackupView();
  else if (app.view === 'rules') renderRulesView();
  else if (app.view === 'conversations') renderConversation(retainPosition);
  else { if (app.view === 'brief') renderProjectView(); else renderBoard(app.view); const board = $('view-content').querySelector('.board, .project-board'); if (board) board.scrollTop = boardTop; }
  app.renderedView = app.view;
}
function cacheFor(channelId) {
  if (!app.caches.has(channelId)) app.caches.set(channelId, { messages: [], cursor: 0, editCursor: 0, loaded: false, lastRead: 0, request: null, visibleCount: MESSAGE_RENDER_PAGE_SIZE, renderedCount: 0 });
  return app.caches.get(channelId);
}
function renderConversation(retainPosition) {
  const channel = app.state.channels.find((item) => item.id === app.channelId);
  if (!channel) {
    setHeader('A PLACE TO THINK TOGETHER', 'Conversations', 'Give your work a shared place to start.');
    $('view-content').append(emptyState('◫', 'Your room is ready.', 'Create a private conversation with one agent, or bring several agents into a group.', app.state.me.kind === 'human' ? 'New conversation' : null, showNewChannel));
    return;
  }
  const action = node('div', 'header-action');
  const stack = node('div', 'member-stack');
  for (const id of channel.members.slice(0, 4)) stack.append(avatar(person(id), 'tiny'));
  if (channel.members.length > 4) stack.append(node('span', 'member-count', `+${channel.members.length - 4}`));
  stack.setAttribute('aria-label', `${channel.members.length} participants`);
  action.append(stack, node('span', 'channel-type-badge', channel.kind === 'shared' ? 'Shared' : channel.kind === 'private' ? 'Private' : 'Group'));
  const latest = button('↓ Latest', 'button subtle compact', () => jumpToLatestMessage(channel.id));
  latest.setAttribute('aria-label', 'Jump to latest message');
  action.append(latest);
  setHeader('CONVERSATION', channel.title, `${channel.members.length} participant${channel.members.length === 1 ? '' : 's'} · A shared record of the work`, action);
  const body = node('div', 'conversation-body');
  const context = node('div', 'channel-context');
  const icon = node('span', 'context-icon', channel.kind === 'shared' ? '#' : '◇');
  icon.setAttribute('aria-hidden', 'true');
  paintRhombus(icon, channel);
  context.append(icon, node('span', '', channel.kind === 'shared' ? 'The workspace conversation. All participants can contribute here.' : 'Only the members of this conversation can see its messages, decisions, and tasks.'));
  const list = node('div', 'message-list');
  list.id = 'message-list';
  list.setAttribute('role', 'log');
  list.setAttribute('aria-live', 'polite');
  list.setAttribute('aria-relevant', 'additions');
  list.setAttribute('aria-label', `Messages in ${channel.title}`);
  body.append(context, list);
  $('view-content').append(body);
  $('composer-area').hidden = false;
  $('message-input').placeholder = `Message ${channel.title}…`;
  if ($('message-input').value !== (app.drafts.get(channel.id) || '')) $('message-input').value = app.drafts.get(channel.id) || '';
  resizeComposer();
  app.messageSignature = '';
  renderMessages(!retainPosition);
  if (retainPosition) list.scrollTop = retainPosition.bottom ? list.scrollHeight : retainPosition.top;
  app.renderedChannelId = channel.id;
}
async function fetchMessages(channelId) {
  const cache = cacheFor(channelId);
  if (cache.request) return cache.request;
  cache.request = (async () => {
    const result = await api(`/api/messages?channel_id=${encodeURIComponent(channelId)}&after=${encodeURIComponent(cache.cursor)}&changes_after=${encodeURIComponent(cache.editCursor)}`);
    const messages = new Map(cache.messages.map((message) => [message.id, message]));
    for (const message of [...result.messages, ...(result.message_updates || [])]) messages.set(message.id, message);
    cache.editCursor = result.change_cursor ?? cache.editCursor;
    cache.messages = [...messages.values()].sort((a, b) => Number(a.id) - Number(b.id));
    cache.cursor = result.cursor;
    cache.loaded = true;
    if (app.view === 'conversations' && app.channelId === channelId && !cache.historyLoading) {
      renderMessages();
      await markRead(channelId);
    }
  })();
  try { await cache.request; } finally { cache.request = null; }
}
function resetVoicePlayers() {
  for (const player of app.voicePlayers.values()) player.pause();
  app.voicePlayers.clear();
}
function renderMessages(forceScroll = false) {
  const list = $('message-list');
  if (!list || !app.channelId) return;
  const cache = cacheFor(app.channelId);
  const outbox = app.outbox.filter((message) => message.channel_id === app.channelId);
  const retainedPlayers = new Map();
  const signature = JSON.stringify([cache.loaded, cache.messages, outbox, cache.visibleCount]);
  if (signature === app.messageSignature && !forceScroll) return;
  app.messageSignature = signature;
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 100;
  const previousTop = list.scrollTop;
  // Keep the older messages being read visible when a poll adds newer records.
  if (!nearBottom && !forceScroll && cache.renderedCount) cache.visibleCount += Math.max(0, cache.messages.length - cache.renderedCount);
  cache.renderedCount = cache.messages.length;
  list.setAttribute('aria-busy', 'true');
  list.replaceChildren();
  if (!cache.loaded && !outbox.length) list.append(loading('Opening conversation…'));
  else if (!cache.messages.length && !outbox.length) list.append(emptyState('◫', 'Start the conversation.', 'Share a question, an idea, or the next step. Every message becomes part of the local record.'));
  else {
    const start = Math.max(0, cache.messages.length - cache.visibleCount);
    if (start) {
      const older = button(`Show older messages · ${start} more loaded`, 'button subtle older-messages', () => {
        const oldHeight = list.scrollHeight;
        const oldTop = list.scrollTop;
        cache.visibleCount += MESSAGE_RENDER_PAGE_SIZE;
        renderMessages();
        list.scrollTop = oldTop + list.scrollHeight - oldHeight;
      });
      list.append(older);
    }
    let previousDate = '';
    for (const message of [...cache.messages.slice(start), ...outbox]) {
      const date = dateLabel(message.created_at);
      if (date !== previousDate) { list.append(node('div', 'date-divider', date)); previousDate = date; }
      const element = messageElement(message);
      // Reuse the actual player, including its playback position, when the same message is redrawn.
      element.querySelectorAll('audio.message-audio').forEach((player, index) => {
        const identity = message.id === undefined ? ['queued', message.client_id] : ['stored', message.id];
        const key = JSON.stringify([message.channel_id, identity, player.getAttribute('src'), index]);
        const previous = app.voicePlayers.get(key);
        if (previous) {
          previous.setAttribute('aria-label', player.getAttribute('aria-label'));
          player.replaceWith(previous);
        }
        retainedPlayers.set(key, previous || player);
      });
      list.append(element);
    }
  }
  for (const [key, player] of app.voicePlayers) if (!retainedPlayers.has(key)) player.pause();
  app.voicePlayers = retainedPlayers;
  list.setAttribute('aria-busy', 'false');
  if (nearBottom || forceScroll) list.scrollTop = list.scrollHeight; else list.scrollTop = previousTop;
}
// This intentionally bounded Markdown subset creates trusted DOM elements only.
// Stored bodies stay untouched; unsupported syntax and HTML remain visible text.
const MAX_FORMAT_DEPTH = 8;
function safeMessageUrl(value) {
  if (/^\/api\/(images|audio)\/[a-f0-9]{32}$/.test(value)) return value;
  if (/[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function markdownLink(text, start) {
  if (!text.includes('](', start + 1)) return null;
  let labelEnd = start + 1;
  let brackets = 1;
  for (; labelEnd < text.length; labelEnd++) {
    if (text[labelEnd] === '\\') { labelEnd++; continue; }
    if (text[labelEnd] === '[') brackets++;
    if (text[labelEnd] === ']' && --brackets === 0) break;
  }
  if (text.slice(labelEnd, labelEnd + 2) !== '](') return null;
  let end = labelEnd + 2;
  let parentheses = 1;
  for (; end < text.length; end++) {
    if (text[end] === '\\') { end++; continue; }
    if (text[end] === '(') parentheses++;
    if (text[end] === ')' && --parentheses === 0) break;
  }
  if (parentheses) return null;
  const destination = text.slice(labelEnd + 2, end).trim();
  const match = destination.match(/^(?:<([^<>]+)>|([^\s]+))(?:\s+["'][\s\S]*["'])?$/);
  return { label: text.slice(start + 1, labelEnd), url: match ? safeMessageUrl((match[1] || match[2]).replace(/\\([()\\])/g, '$1')) : null, end: end + 1 };
}
// The next style marker at or after `from` that is not escaped. A marker after an odd number of backslashes is literal
// text (typed "*a\*" stays plain); after an even number the backslashes are escaped and the marker closes ("*a\\*").
function closingIndex(text, marker, from) {
  for (let close = text.indexOf(marker, from); close !== -1; close = text.indexOf(marker, close + 1)) {
    let slashes = 0;
    while (text[close - 1 - slashes] === '\\') slashes++;
    if (slashes % 2 === 0) return close;
  }
  return -1;
}
function appendInline(parent, value, depth = 0, allowLinks = true) {
  const text = String(value);
  if (depth >= MAX_FORMAT_DEPTH) { parent.append(document.createTextNode(text)); return; }
  let buffer = '';
  const flush = () => { if (buffer) { parent.append(document.createTextNode(buffer)); buffer = ''; } };
  for (let index = 0; index < text.length;) {
    const char = text[index];
    if (char === '\\' && /[\\`*_{}\[\]()#+\-.!|>~]/.test(text[index + 1] || '')) { buffer += text[index + 1]; index += 2; continue; }
    if (char === '@') {
      const mention = text.slice(index).match(MENTION_MARKER);
      if (mention && renderMentions.has(mention[1])) {
        flush(); const chip = node('span', `mention${mention[1] === app.state?.me.id ? ' mention-me' : ''}`, '@' + person(mention[1]).name);
        chip.dataset.participantId = mention[1]; parent.append(chip); index += mention[0].length; continue;
      }
    }
    if (char === '`') {
      const marker = text.slice(index).match(/^`+/)[0];
      const close = text.indexOf(marker, index + marker.length);
      if (close !== -1) { flush(); parent.append(node('code', 'inline-code', text.slice(index + marker.length, close))); index = close + marker.length; continue; }
    }
    if (char === '!' && text[index + 1] === '[') {
      const image = markdownLink(text, index + 1);
      if (image) {
        if (/^\/api\/images\/[a-f0-9]{32}$/.test(image.url || '')) {
          flush();
          const preview = node('img', 'message-image'); preview.src = image.url; preview.alt = image.label || 'Attached image'; preview.loading = 'lazy';
          const control = button('', 'image-preview', () => showImage(image.url, image.label));
          control.setAttribute('aria-label', 'Open image ' + (image.label || 'attachment')); control.append(preview);
          preview.addEventListener('error', () => { control.replaceChildren(node('span', '', 'Image unavailable or access denied.')); control.disabled = true; });
          parent.append(control);
        } else buffer += text.slice(index, image.end);
        index = image.end; continue;
      }
    }
    if (char === '[' && allowLinks) {
      const link = markdownLink(text, index);
      if (link) {
        if (/^\/api\/audio\/[a-f0-9]{32}$/.test(link.url || '')) {
          // A voice message from this app () plays in place; any other link stays a link.
          flush(); const voice = node('span', 'voice-message'); const player = node('audio', 'message-audio');
          player.controls = true; player.preload = 'none'; player.src = link.url; player.setAttribute('aria-label', link.label || 'Voice message');
          voice.append(node('span', 'voice-label', link.label || 'Voice message'), player); parent.append(voice); index = link.end; continue;
        }
        if (link.url) { flush(); const anchor = node('a'); anchor.href = link.url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; anchor.referrerPolicy = 'no-referrer'; appendInline(anchor, link.label, depth + 1, false); parent.append(anchor); }
        else buffer += text.slice(index, link.end);
        index = link.end; continue;
      }
    }
    if ((char === '+' || char === '~') && text[index + 1] === char) {
      // ++underline++ and ~~strikethrough~~ (the composer's markers). Like * and _ below, the text right inside a marker
      // must not be a space, so "C++ and C++" or "i++ + j++" stays plain.
      const close = closingIndex(text, char + char, index + 2);
      if (close > index + 2 && !/\s/.test(text[index + 2]) && !/\s/.test(text[close - 1])) {
        flush(); const styled = node(char === '+' ? 'u' : 's'); appendInline(styled, text.slice(index + 2, close), depth + 1, allowLinks); parent.append(styled); index = close + 2; continue;
      }
    }
    if (char === '*' || (char === '_' && !(/[\p{L}\p{N}]/u.test(text[index - 1] || '') && /[\p{L}\p{N}]/u.test(text[index + 1] || '')))) {
      const marker = text[index + 1] === char ? char + char : char;
      const close = closingIndex(text, marker, index + marker.length);
      if (close > index + marker.length && !/\s/.test(text[index + marker.length]) && !/\s/.test(text[close - 1])) {
        flush(); const emphasis = node(marker.length === 2 ? 'strong' : 'em'); appendInline(emphasis, text.slice(index + marker.length, close), depth + 1, allowLinks); parent.append(emphasis); index = close + marker.length; continue;
      }
    }
    buffer += char; index++;
  }
  flush();
}
function tableCells(line) {
  const cells = [];
  let cell = '';
  let codeMarker = '';
  const trimmed = line.trim();
  for (let index = 0; index < trimmed.length; index++) {
    const char = trimmed[index];
    if (char === '\\' && index + 1 < trimmed.length) { cell += char + trimmed[++index]; continue; }
    if (char === '`') {
      const marker = trimmed.slice(index).match(/^`+/)[0];
      if (!codeMarker) codeMarker = marker; else if (marker === codeMarker) codeMarker = '';
      cell += marker; index += marker.length - 1; continue;
    }
    if (char === '|' && !codeMarker) { cells.push(cell.trim()); cell = ''; } else cell += char;
  }
  cells.push(cell.trim());
  if (trimmed.startsWith('|')) cells.shift();
  if (trimmed.endsWith('|') && cells[cells.length - 1] === '') cells.pop();
  return cells;
}
function tableHeader(lines, index) {
  if (!lines[index]?.includes('|') || !lines[index + 1]) return null;
  const headers = tableCells(lines[index]);
  const separators = tableCells(lines[index + 1]);
  return headers.length === separators.length && separators.every(cell => /^:?-{3,}:?$/.test(cell)) ? { headers, separators } : null;
}
function listMarker(line) {
  // Lookahead may reach EOF after an item or a trailing blank line.
  if (typeof line !== 'string') return null;
  const match = line.match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/);
  return match ? { indent: match[1].replace(/\t/g, '    ').length, ordered: /^\d/.test(match[2]), start: parseInt(match[2], 10), body: match[3] } : null;
}
function appendList(parent, lines, start, depth) {
  const first = listMarker(lines[start]);
  const list = node(first.ordered ? 'ol' : 'ul');
  if (first.ordered) list.start = first.start;
  let index = start;
  while (index < lines.length) {
    const marker = listMarker(lines[index]);
    if (!marker || marker.indent !== first.indent || marker.ordered !== first.ordered) break;
    const item = node('li'); item.dir = 'auto';
    const paragraph = node('p'); paragraph.dir = 'auto';
    const task = marker.body.match(/^\[([ xX])\]\s+(.*)$/);
    if (task) { const check = node('input'); check.type = 'checkbox'; check.disabled = true; check.checked = task[1] !== ' '; check.setAttribute('aria-label', check.checked ? 'Completed item' : 'Incomplete item'); paragraph.append(check); appendInline(paragraph, task[2]); }
    else appendInline(paragraph, marker.body);
    item.append(paragraph); index++;
    while (index < lines.length) {
      const nested = listMarker(lines[index]);
      if (nested && nested.indent > first.indent && depth < MAX_FORMAT_DEPTH) { index = appendList(item, lines, index, depth + 1); continue; }
      if (!nested && lines[index].trim() && /^\s+/.test(lines[index]) && lines[index].match(/^\s*/)[0].replace(/\t/g, '    ').length > first.indent) {
        const continuation = node('p'); continuation.dir = 'auto'; appendInline(continuation, lines[index].trim()); item.append(continuation); index++; continue;
      }
      break;
    }
    list.append(item);
    if (!lines[index]?.trim() && listMarker(lines[index + 1])?.indent === first.indent) index++;
  }
  parent.append(list);
  return index;
}
function appendMessageBlocks(parent, lines, depth = 0) {
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const fence = line.match(/^\s{0,3}(`{3,}|~{3,})\s*([^`~]*)$/);
    if (fence) {
      const body = []; index++;
      const closing = new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
      while (index < lines.length && !closing.test(lines[index])) body.push(lines[index++]);
      if (index < lines.length) index++;
      const pre = node('pre', 'message-code'); pre.dir = 'ltr'; pre.tabIndex = 0;
      const label = fence[2].trim(); if (label) pre.setAttribute('aria-label', `${label} code`);
      pre.append(node('code', '', body.join('\n'))); parent.append(pre); continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*$/);
    if (heading) { const element = node(`h${heading[1].length}`); element.dir = 'auto'; appendInline(element, heading[2].replace(/\s+#+\s*$/, '')); parent.append(element); index++; continue; }
    const table = tableHeader(lines, index);
    if (table) {
      const wrapper = node('div', 'message-table-wrap'); wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Message table');
      const element = node('table'); const head = node('thead'); const header = node('tr');
      const alignment = table.separators.map(cell => cell.startsWith(':') && cell.endsWith(':') ? 'align-center' : cell.endsWith(':') ? 'align-end' : 'align-start');
      table.headers.forEach((value, column) => { const cell = node('th', alignment[column]); cell.scope = 'col'; cell.dir = 'auto'; appendInline(cell, value); header.append(cell); }); head.append(header); element.append(head);
      const body = node('tbody'); index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        const values = tableCells(lines[index]); if (values.length !== table.headers.length) break;
        const row = node('tr'); values.forEach((value, column) => { const cell = node('td', alignment[column]); cell.dir = 'auto'; appendInline(cell, value); row.append(cell); }); body.append(row); index++;
      }
      element.append(body); wrapper.append(element); parent.append(wrapper); continue;
    }
    if (listMarker(line)) { index = appendList(parent, lines, index, depth); continue; }
    if (/^\s{0,3}>/.test(line) && depth < MAX_FORMAT_DEPTH) {
      const body = []; while (index < lines.length && /^\s{0,3}>/.test(lines[index])) body.push(lines[index++].replace(/^\s{0,3}> ?/, ''));
      const quote = node('blockquote'); appendMessageBlocks(quote, body, depth + 1); parent.append(quote); continue;
    }
    if (/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { parent.append(node('hr')); index++; continue; }
    const body = [line]; index++;
    while (index < lines.length && lines[index].trim() && !/^\s{0,3}(?:#{1,6}\s|`{3,}|~{3,}|>)/.test(lines[index]) && !listMarker(lines[index]) && !tableHeader(lines, index) && !/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(lines[index])) body.push(lines[index++]);
    const paragraph = node('p'); paragraph.dir = 'auto'; appendInline(paragraph, body.join('\n')); parent.append(paragraph);
  }
}
function formattedMessage(body, mentions = []) {
  const result = node('div', 'message-bubble formatted-message'); result.dir = 'auto';
  renderMentions = new Set(mentions);
  try { appendMessageBlocks(result, plain(body).replace(/\r\n?/g, '\n').split('\n')); }
  finally { renderMentions = new Set(); }
  return result;
}

function messageElement(message) {
  const own = message.author_id === app.state.me.id;
  const pending = message.status === 'pending';
  const failed = message.status === 'failed';
  const result = node('article', `message${own ? ' own' : ''}${pending ? ' pending' : ''}${failed ? ' failed' : ''}`);
  const author = person(message.author_id, message.author_name);
  const content = node('div', 'message-content');
  const meta = node('div', 'message-meta');
  const name = node('strong', '', message.author_name || author.name);
  name.dir = 'auto';
  const time = node('time', '', formatDateTime(message.created_at));
  time.dateTime = message.created_at;
  time.title = `${formatDateTime(message.created_at)} · ${DISPLAY_TIME_ZONE}`;
  meta.append(name, node('span', 'author-kind', author.kind === 'human' ? 'HUMAN' : 'AGENT'), time);
  if (message.id !== undefined) {
    result.dataset.messageId = String(message.id);
    const number = node('span', 'message-number', `#${message.id}`);
    number.setAttribute('aria-label', `Message number ${message.id}`);
    meta.prepend(number);
  }
  let bubble;
  let formattingError;
  const mentions = message.mentions || mentionsIn(message.body, message.channel_id);
  if (!own && mentions.includes(app.state.me.id)) result.classList.add('mentions-me');
  try { bubble = formattedMessage(message.body, mentions); }
  catch (error) {
    // One malformed message must not hide later records or disable sending.
    bubble = node('div', 'message-bubble formatting-fallback', message.body);
    bubble.dir = 'auto';
    formattingError = node('p', 'formatting-error', 'Formatting error · original text shown.');
    formattingError.setAttribute('role', 'note');
    formattingError.title = errorText(error);
  }
  if (message.edited_at) meta.append(node('span', 'edited-marker', 'Edited'));
  if (own && !pending && !failed && message.can_edit) meta.append(button('Edit', 'text-button edit-message', () => showMessageEditor(message)));
  content.append(meta, bubble);
  if (formattingError) content.append(formattingError);
  if (message.id !== undefined) {
    const actions = node('div', 'message-actions');
    actions.append(button('Copy', 'text-button', () => copyMessage(message)),
      button('Reply', 'text-button', () => quoteMessage(message)),
      button('Receipts', 'text-button', () => showReceipts(message)),
      button(app.bookmarks.has(message.id) ? 'Saved ★' : 'Save ☆', 'text-button', async (event) => {
        const control = event.currentTarget; control.disabled = true;
        try {
          const saved = !app.bookmarks.has(message.id);
          await api('/api/bookmarks', { message_id: message.id, saved });
          if (saved) app.bookmarks.add(message.id); else app.bookmarks.delete(message.id);
          control.textContent = saved ? 'Saved ★' : 'Save ☆';
        } catch (error) { notify(errorText(error), true); }
        finally { control.disabled = false; }
      }));
    content.append(actions);
  }
  if (message.id !== undefined && !pending && !failed) {
    const summary = readSummary(message);
    if (summary) content.append(summary);
  }
  if (pending || failed) {
    const delivery = node('div', `delivery-status${failed ? ' error' : ''}`);
    // Red: the server has not acknowledged this message (Collaboration).
    delivery.append(node('span', 'receipt-dot receipt-unacknowledged'), node('span', '', pending ? 'Sending…' : `Not confirmed · ${message.error || 'Try again.'}`));
    if (failed) { delivery.append(button('Retry', 'text-button', () => deliver(message))); if (message.durable) delivery.append(button('Keep as draft', 'text-button', () => restoreDraft(message)), button('Delete', 'text-button', () => showDiscardQueued(message))); }
    content.append(delivery);
  }
  result.append(avatar(author), content);
  return result;
}
// Collaboration: under every stored message, a person's or an agent's, orange while no member other than its author
// has read it, green once one has, with the count and the exact names ("1/2 read: Codex; Atlas pending"). Read = the
// member's read marker is at or past the message: a person had the conversation open on screen, an agent's own tool
// fetched the conversation.
function readSummary(message) {
  const channel = app.state.channels.find((item) => item.id === message.channel_id);
  const others = (channel?.members || []).filter((id) => id !== message.author_id && person(id).active !== false);
  if (!others.length) return null;
  const markers = channel.read_markers || {};
  const read = others.filter((id) => Number(markers[id] || 0) >= Number(message.id));
  const waiting = others.filter((id) => !read.includes(id));
  const names = (ids) => ids.map((id) => person(id).name).join(', ');
  let text = `${read.length}/${others.length} read`;
  if (read.length) text += `: ${names(read)}`;
  if (waiting.length) text += `${read.length ? '; ' : ': '}${names(waiting)} pending`;
  const summary = node('div', `read-summary ${read.length ? 'read-some' : 'read-none'}`);
  summary.append(node('span', 'receipt-dot'), node('span', '', text));
  return summary;
}
// Receipts (): who the app gave this message's text to, and whose read marker has passed it. Neither
// proves that someone read it with attention, so the dialog says what each word means.
async function showReceipts(message) {
  const { dialog, body } = dialogBase('Receipts', 'Received: the app gave them this message. Read: a person had the conversation open on screen past this message, or an agent\'s own tool fetched the conversation past it. Neither proves attention.');
  const status = node('p', 'field-hint', 'Loading receipts…');
  body.append(status);
  dialog.showModal();
  try {
    const result = await api(`/api/messages/${message.id}/receipts`);
    if (!result.recipients.length) { status.textContent = 'Nobody else is in this conversation.'; return; }
    const list = node('ul', 'receipt-list');
    for (const person of result.recipients) {
      const received = !result.received_known ? 'Received: unknown (sent before receipts were kept)'
        : person.received_at ? `Received ${formatDateTime(person.received_at)}` : 'Not received yet';
      const item = node('li', 'receipt-item');
      item.append(node('strong', '', person.name), node('span', '', received),
        node('span', person.read ? 'receipt-read' : 'receipt-unread', person.read ? 'Read' : 'Not read yet'));
      list.append(item);
    }
    status.replaceWith(list);
  } catch (error) {
    status.textContent = `Receipts could not be loaded. ${errorText(error)}`;
    status.classList.add('field-error');
  }
}
function showMessageEditor(message) {
  if (!message.can_edit || message.author_id !== app.state.me.id) return;
  const generation = app.personalGeneration;
  const {dialog, body} = dialogBase('Edit message', 'Editing is allowed before another participant retrieves this message. The local record keeps edit history.');
  const input = node('textarea'); input.maxLength = LIMITS.body; input.rows = 6; input.value = message.body; input.setAttribute('aria-label', 'Edited message');
  const error = node('p', 'form-error'); error.setAttribute('role', 'status');
  const actions = node('div', 'dialog-actions');
  let attempt = null;
  let inFlight = false;
  const save = button('Save edit', 'button primary', async () => {
    if (inFlight || !app.authenticated || generation !== app.personalGeneration) return;
    if (!attempt) {
      if (!input.value.trim() || input.value === message.body) { error.textContent = 'Enter a changed message before saving.'; return; }
      attempt = {body: input.value, version: message.version, edit_id: newClientId()};
    }
    inFlight = true; input.readOnly = true; save.disabled = true; error.textContent = '';
    try {
      const result = await api(`/api/messages/${encodeURIComponent(message.id)}/edit`, attempt);
      if (generation !== app.personalGeneration || !app.authenticated) return;
      const cache = cacheFor(message.channel_id);
      cache.messages = cache.messages.map(row => row.id === result.message.id ? result.message : row);
      if (app.channelId === message.channel_id && app.view === 'conversations') renderMessages();
      dialog.close(); announce(`Message number ${message.id} edited.`);
    } catch (failure) {
      if (generation !== app.personalGeneration || !app.authenticated) return;
      if (failure.status >= 400 && failure.status < 500) {
        input.readOnly = false; attempt = null;
        error.textContent = `${errorText(failure)} Your edit text stays here; copy it before closing.`;
        save.textContent = 'Save edit';
      } else {
        // Preserve the exact operation after an unknown acknowledgement. Retry
        // confirms it; a changed body must never reuse its operation identifier.
        error.textContent = `Edit not confirmed. Retry the same edit. ${errorText(failure)}`;
        save.textContent = 'Retry same edit';
      }
    } finally { inFlight = false; save.disabled = false; }
  });
  actions.append(button('Cancel', 'button subtle', () => dialog.close()), save);
  body.append(input, node('p', 'field-hint', 'Edit text stays in this dialog until Save succeeds. Cancel closes it without saving. Your composer draft stays unchanged.'), error, actions); dialog.showModal(); input.focus();
}

async function markRead(channelId) {
  if (document.visibilityState !== 'visible' || app.view !== 'conversations' || app.channelId !== channelId) return;
  const cache = cacheFor(channelId);
  // Wait for an explicit history navigation to finish before acknowledging it.
  if (cache.historyLoading) return;
  const latest = cache.messages[cache.messages.length - 1]?.id;
  if (!latest || Number(latest) <= Number(cache.lastRead)) return;
  const previous = cache.lastRead;
  cache.lastRead = latest;
  try {
    await api('/api/read', { channel_id: channelId, message_id: latest });
    const channel = app.state.channels.find((item) => item.id === channelId);
    if (channel && channel.unread) { channel.unread = 0; renderSidebar(); }
  } catch (error) {
    cache.lastRead = previous;
    if (app.authenticated) notify(`Could not save the read marker. ${errorText(error)}`, true);
  }
}
function composerLocked(channelId = app.channelId) {
  if (!channelId || app.view !== 'conversations' || app.channelId !== channelId) return true;
  const input = $('message-input');
  return app.uploadingImage || app.uploadingVoice || input.readOnly || app.outbox.some(message => message.channel_id === channelId && !message.durable && message.body === input.value && (message.inFlight || message.status === 'failed'));
}
let composerHeight = null;   // px the reader chose; null = the automatic rows below
function composerHeightLimits() {
  const input = $('message-input');
  const min = parseFloat(getComputedStyle(input).minHeight);
  const share = window.matchMedia(PHONE_WIDTH_QUERY).matches ? COMPOSER_MAX_PHONE_SHARE : COMPOSER_MAX_WINDOW_SHARE;
  // Everything in the message area except the box itself (zero while the area is hidden; resizeComposer re-checks).
  const rest = $('composer-area').getBoundingClientRect().height - input.getBoundingClientRect().height;
  return { min, max: Math.max(min, Math.floor(window.innerHeight * share - Math.max(0, rest))) };
}
function setComposerHeight(next, remember) {
  const input = $('message-input'); const grip = $('composer-resize'); const list = $('message-list');
  const atNewest = Boolean(list) && list.scrollHeight - list.scrollTop - list.clientHeight < 4;   // no list before a conversation is drawn
  const { min, max } = composerHeightLimits();
  composerHeight = next === null ? null : Math.round(Math.min(max, Math.max(min, next)));
  input.closest('.composer-box').classList.toggle('composer-sized', composerHeight !== null);
  input.style.height = composerHeight === null ? '' : `${composerHeight}px`;
  grip.setAttribute('aria-valuemin', String(Math.round(min)));
  grip.setAttribute('aria-valuemax', String(max));
  grip.setAttribute('aria-valuenow', String(Math.round(input.getBoundingClientRect().height)));
  if (atNewest) list.scrollTop = list.scrollHeight;   // a growing box must not hide the newest message
  if (!remember) return;
  try { if (composerHeight === null) localStorage.removeItem(COMPOSER_HEIGHT_KEY); else localStorage.setItem(COMPOSER_HEIGHT_KEY, String(composerHeight)); }
  catch { notify('Message box size changed for this tab. Browser storage is unavailable, so it will reset on reload.'); }
}
function startComposerDrag(event) {
  if (event.button !== 0) return;
  event.preventDefault();
  const grip = $('composer-resize'); const box = grip.closest('.composer-box');
  const startY = event.clientY; const startHeight = $('message-input').getBoundingClientRect().height;
  grip.setPointerCapture(event.pointerId); box.classList.add('composer-resizing');
  const move = (moved) => setComposerHeight(startHeight + startY - moved.clientY, false);
  const end = () => {
    grip.removeEventListener('pointermove', move); box.classList.remove('composer-resizing');
    if (composerHeight !== null) setComposerHeight(composerHeight, true);
  };
  grip.addEventListener('pointermove', move);
  grip.addEventListener('pointerup', end, { once: true });
  grip.addEventListener('pointercancel', end, { once: true });
}
function composerGripKey(event) {
  const current = $('message-input').getBoundingClientRect().height;
  if (event.key === 'ArrowUp') setComposerHeight(current + COMPOSER_RESIZE_STEP, true);
  else if (event.key === 'ArrowDown') setComposerHeight(current - COMPOSER_RESIZE_STEP, true);
  else if (event.key === 'Home') setComposerHeight(null, true);
  else return;
  event.preventDefault();
}
function resizeComposer() {
  if (composerHeight !== null) setComposerHeight(composerHeight, false);   // a chosen size still fits once the area shows
  // CSS sets the maximum height; rows keep the control usable without inline styles.
  const lineCount = $('message-input').value.split('\n').length;
  $('message-input').rows = Math.min(6, Math.max(2, lineCount));
  const locked = composerLocked();
  $('send-message').disabled = !$('message-input').value.trim() || locked;
  $('clear-draft').disabled = !$('message-input').value || locked;
  $('emoji-picker').disabled = locked;
  $('attach-image').disabled = locked;
  $('record-voice').disabled = Boolean(app.voice && !app.voice.recorder) || (locked && !app.voice);
  // Dictation exists only when the server has a local speech program; a recording in progress keeps its button.
  $('record-voice').hidden = !app.state?.capabilities?.dictation && !app.voice;
}
function newClientId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
function sendMessage() {
  closeMentionPicker();
  const typed = $('message-input').value;
  if (!typed.trim() || composerLocked()) return;
  const body = withMentionMarkers(typed, app.channelId);
  if (body !== typed) { $('message-input').value = body; app.drafts.set(app.channelId, body); }
  if (body.length > LIMITS.body) { notify(`Messages can contain up to ${LIMITS.body.toLocaleString()} characters.`, true); return; }
  // If enqueue's acknowledgement was lost, reuse its identifier rather than
  // creating a second message from the same text.
  let message = app.outbox.find((item) => !item.durable && item.channel_id === app.channelId && item.body === body);
  if (!message) {
    message = { channel_id: app.channelId, body, client_id: newClientId(), author_id: app.state.me.id, author_name: app.state.me.name, created_at: new Date().toISOString(), status: 'pending', durable: false };
    app.outbox.push(message);
  }
  app.drafts.set(app.channelId, body); app.unsavedDrafts.add(app.channelId);
  renderMessages(true); deliver(message);
}
function acceptQueue(receipt, message) {
  if (receipt.draft) applySavedDraft(receipt.draft);
  message.durable = true;
  app.personalErrors.delete(`enqueue:${message.client_id}`);
  if (app.drafts.get(message.channel_id) === message.body) {
    const remaining = receipt.draft?.body || '';
    app.drafts.set(message.channel_id, remaining); app.unsavedDrafts.delete(message.channel_id);
    if (app.view === 'conversations' && app.channelId === message.channel_id) { $('message-input').value = remaining; resizeComposer(); }
  }
}
async function deliver(message) {
  if (message.inFlight) return;
  const generation = app.personalGeneration;
  message.inFlight = true; message.status = 'pending'; message.error = '';
  const ownsComposer = app.channelId === message.channel_id && !message.durable;
  if (ownsComposer) { $('message-input').readOnly = true; resizeComposer(); }
  personalStatus(); renderMessages();
  try {
    let delivered;
    if (!message.durable) {
      await persistDraft(message.channel_id);
      const receipt = await queuePersonal(() => api('/api/personal/enqueue', { channel_id: message.channel_id, body: message.body, client_id: message.client_id, draft_version: app.draftVersions.get(message.channel_id) || 0, preserve_draft: (app.drafts.get(message.channel_id) || '') !== message.body }), `enqueue:${message.client_id}`);
      if (generation !== app.personalGeneration) return;
      acceptQueue(receipt, message);
      delivered = receipt.delivered;
    }
    const saved = delivered || await api('/api/personal/send', { client_id: message.client_id });
    if (generation !== app.personalGeneration) return;
    const cache = cacheFor(message.channel_id);
    if (!cache.messages.some((item) => item.id === saved.id)) cache.messages.push(saved);
    cache.messages.sort((a, b) => Number(a.id) - Number(b.id));
    app.outbox = app.outbox.filter((item) => item.client_id !== message.client_id);
    if (app.view === 'conversations' && app.channelId === message.channel_id) { renderMessages(); await markRead(message.channel_id); }
    announce('Message sent.');
  } catch (error) {
    if (generation !== app.personalGeneration) return;
    message.status = 'failed'; message.error = errorText(error);
    if (!message.durable) {
      try {
        const personal = await api('/api/personal');
        if (generation !== app.personalGeneration) return;
        if (personal.outbox.some((item) => item.client_id === message.client_id)) {
          acceptQueue({ draft: personal.drafts.find((item) => item.channel_id === message.channel_id) }, message);
        }
      } catch (observationError) { message.error += ` ${errorText(observationError)}`; }
    }
    renderMessages();
    announce(message.durable ? 'Delivery not confirmed. Retry text is saved to this workspace.' : 'Save not confirmed. Keep this tab open and retry.');
  } finally {
    delete message.inFlight;
    if (generation === app.personalGeneration) { if (ownsComposer) { $('message-input').readOnly = false; resizeComposer(); } personalStatus(); }
  }
}
async function restoreDraft(message) {
  if (message.inFlight || !message.durable) return;
  try {
    if (app.drafts.has(message.channel_id)) await persistDraft(message.channel_id);
    const draft = await queuePersonal(() => api('/api/personal/restore', { client_id: message.client_id, version: app.draftVersions.get(message.channel_id) || 0 }), `draft:${message.channel_id}`);
    app.outbox = app.outbox.filter((item) => item.client_id !== message.client_id);
    applySavedDraft(draft, true); selectChannel(message.channel_id);
    $('message-input').value = draft.body; resizeComposer(); $('message-input').focus();
    notify('Queued text is saved as your private draft. It has not been delivered.');
  } catch (error) { notify(errorText(error), true); }
}
// Delete beside Retry and Keep as draft: asks first, removes only this saved unsent copy; a refusal keeps it listed.
function showDiscardQueued(message) {
  if (message.inFlight || !message.durable) return;
  const { dialog, body } = dialogBase('Delete unsent message?', 'Removes this message from your retry list only. If the board already received it, the delivered message stays.');
  const actions = node('div', 'dialog-actions');
  const keep = button('Keep message', 'button subtle', () => dialog.close());
  const remove = button('Delete message', 'button primary', async () => {
    dialog.close();
    if (message.inFlight) return;
    try {
      await queuePersonal(() => api('/api/personal/discard', { client_id: message.client_id }), `discard:${message.client_id}`);
      app.outbox = app.outbox.filter((item) => item.client_id !== message.client_id);
      renderMessages(); personalStatus();
      announce('Unsent message deleted. Delivered messages are unchanged.');
    } catch (error) { notify(errorText(error), true); }
  });
  actions.append(keep, remove);
  body.append(actions);
  dialog.showModal();
}

function taskStatusLabels() { return app.state.task_options?.statuses || TASK_STATUS_LABELS; }
function channelOptions() { return app.state.channels.map((channel) => ({ value: channel.id, label: channel.title })); }
function renderBoard(kind) {
  const isDecision = kind === 'decisions';
  const records = isDecision ? app.state.decisions : app.state.tasks;
  const create = isDecision ? showNewDecision : showNewTask;
  const createButton = button(isDecision ? '+ Propose decision' : '+ New task', 'button primary compact', create);
  createButton.disabled = !app.state.channels.length;
  setHeader(isDecision ? 'DECIDE WITH CONTEXT' : 'MAKE THE NEXT STEP CLEAR', isDecision ? 'Decisions' : 'Tasks', isDecision ? 'Human approval, implementation evidence, and an independent check.' : 'Keep ownership and progress visible to the conversation.', createButton);
  const board = node('div', 'board');
  const toolbar = node('div', 'board-toolbar');
  toolbar.append(node('p', '', isDecision ? 'Approval does not execute a change.' : 'A clear owner makes a task actionable.'));
  const filter = node('select', 'board-filter');
  filter.setAttribute('aria-label', 'Filter by conversation');
  for (const option of [{ value: '', label: 'All conversations' }, ...channelOptions()]) {
    const element = node('option', '', option.label);
    element.value = option.value;
    filter.append(element);
  }
  filter.value = app.filters[kind];
  if (filter.value !== app.filters[kind]) app.filters[kind] = '';
  filter.addEventListener('change', () => { app.filters[kind] = filter.value; renderView(); });
  toolbar.append(filter);
  board.append(toolbar);
  const filtered = records.filter((record) => !app.filters[kind] || record.channel_id === app.filters[kind]);
  if (!isDecision) {
    renderTaskFilters(board, toolbar, filtered);
    $('view-content').append(board);
    return;
  }
  if (!filtered.length) board.append(emptyState(isDecision ? '◇' : '☑', isDecision ? 'Give decisions a clear record.' : 'Make room for the next step.', isDecision ? 'Capture the proposal, its source, and the evidence. A human can then approve it.' : 'Add a task, choose an owner, and track it from open to done.', app.state.channels.length ? (isDecision ? 'Propose a decision' : 'Create a task') : null, create));
  else {
    const grid = node('div', 'card-grid');
    for (const record of filtered) grid.append(isDecision ? decisionCard(record) : taskCard(record));
    board.append(grid);
  }
  $('view-content').append(board);
}
function renderTaskFilters(board, toolbar, records) {
  toolbar.classList.add('task-toolbar');
  const controls = node('div', 'task-filters');
  const results = node('div', 'task-results');
  const count = node('p', 'task-result-count');
  count.setAttribute('role', 'status');
  const renderResults = () => {
    const query = app.taskFilters.query.trim().toLocaleLowerCase();
    const filtered = records.filter(task =>
      (!app.taskFilters.status || (app.taskFilters.status === ACTIVE_TASK_FILTER ? task.status !== 'done' : task.status === app.taskFilters.status)) &&
      (!app.taskFilters.owner || task.owner_id === app.taskFilters.owner) &&
      (!query || (task.title + '\n' + (task.body || '')).toLocaleLowerCase().includes(query)));
    count.textContent = filtered.length + ' of ' + records.length + ' tasks';
    results.replaceChildren();
    if (!filtered.length) {
      results.append(emptyState('☑', records.length ? 'No tasks match these filters.' : 'Make room for the next step.',
        records.length ? 'Change or reset the filters to see more tasks.' : 'Add a task, choose an owner, and track it from open to done.',
        records.length ? null : (app.state.channels.length ? 'Create a task' : null), showNewTask));
    } else {
      const grid = node('div', 'card-grid');
      for (const task of filtered) grid.append(taskCard(task));
      results.append(grid);
    }
  };
  const addFilter = (label, key, options) => {
    const wrapper = node('label', 'task-filter-field', label);
    const select = node('select', 'board-filter');
    select.setAttribute('aria-label', label);
    for (const { value, label: text } of options) {
      const option = node('option', '', text);
      option.value = value;
      select.append(option);
    }
    select.value = app.taskFilters[key];
    if (select.value !== app.taskFilters[key]) app.taskFilters[key] = '';
    select.addEventListener('change', () => { app.taskFilters[key] = select.value; renderResults(); });
    wrapper.append(select);
    controls.append(wrapper);
  };
  addFilter('Filter by status', 'status', [{ value: ACTIVE_TASK_FILTER, label: 'Active tasks' }, { value: '', label: 'All statuses' },
    ...Object.entries(taskStatusLabels()).map(([value, label]) => ({ value, label }))]);
  const owners = [...new Set(records.map(task => task.owner_id))].map(id => person(id)).sort((a, b) => a.name.localeCompare(b.name));
  addFilter('Filter by owner', 'owner', [{ value: '', label: 'All owners' }, ...owners.map(owner => ({ value: owner.id, label: owner.name }))]);
  const searchField = node('label', 'task-filter-field task-search-field', 'Search tasks');
  const search = node('input', 'task-search');
  search.type = 'search';
  search.setAttribute('aria-label', 'Search tasks');
  search.placeholder = 'Title or description';
  search.value = app.taskFilters.query;
  search.addEventListener('input', () => { app.taskFilters.query = search.value; renderResults(); });
  searchField.append(search);
  controls.append(searchField, button('Reset filters', 'text-button', () => {
    app.filters.tasks = '';
    app.taskFilters = { status: ACTIVE_TASK_FILTER, owner: '', query: '' };
    renderView();
  }));
  board.append(controls, count, results);
  renderResults();
}

// Each private conversation's rhombus has its own colour (Collaboration). The colour follows the conversation's id, never its
// title or position: an FNV-1a hash picks one of the palette slots, and a slot already taken by an earlier private
// conversation (in id order) moves to the next free one, so up to CONVERSATION_COLOURS conversations never share one.
const CONVERSATION_COLOURS = 8;
function conversationColours() {
  const used = new Set();
  const colours = new Map();
  const ids = app.state.channels.filter((channel) => channel.kind === 'private').map((channel) => channel.id).sort();
  for (const id of ids) {
    let hash = 0x811c9dc5;
    for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 0x01000193) >>> 0;
    let slot = hash % CONVERSATION_COLOURS;
    for (let step = 0; step < CONVERSATION_COLOURS && used.has(slot); step += 1) slot = (slot + 1) % CONVERSATION_COLOURS;
    used.add(slot);
    colours.set(id, `var(--conversation-${slot + 1})`);
  }
  return colours;
}

function paintRhombus(icon, channel) {
  if (channel?.kind === 'private') icon.style.color = conversationColours().get(channel.id);
}

function recordBase(record) {
  const card = node('article', 'record-card');
  const top = node('div', 'card-topline');
  const recordChannel = app.state.channels.find((item) => item.id === record.channel_id);
  const channel = node('span', 'card-channel');
  const glyph = node('span', 'card-channel-symbol', recordChannel?.kind === 'shared' ? '#' : '◇');
  paintRhombus(glyph, recordChannel);
  channel.append(glyph, ` ${channelName(record.channel_id)}`);
  channel.dir = 'auto';
  top.append(channel, statusBadge(record.status));
  const title = node('h3', '', record.title);
  title.dir = 'auto';
  card.append(top, title);
  if (record.body) {
    const body = node('p', 'card-body', record.body);
    body.dir = 'auto';
    card.append(body);
  }
  if (record.truncated_fields?.length) card.append(node('p', 'projection-note', `Brief shortened: ${record.truncated_fields.join(', ')}. Open the full record or history for the complete evidence.`));
  return card;
}
function evidenceSection(label, value) {
  const section = node('div', 'evidence-section');
  const text = node('p', 'evidence-text', plain(value));
  text.dir = 'auto';
  section.append(node('p', 'evidence-label', label), text);
  return section;
}
function decisionCard(decision) {
  const card = recordBase(decision);
  if (decision.source) card.append(evidenceSection('Source', decision.source));
  if (decision.evidence) card.append(evidenceSection('Evidence', decision.evidence));
  const footer = node('div', 'card-footer');
  const author = person(decision.author_id);
  const owner = node('div', 'card-person');
  owner.append(avatar(author, 'tiny'), node('span', '', author.name));
  footer.append(owner, node('time', '', formatDateTime(decision.updated_at)));
  card.append(footer);
  const actions = node('div', 'card-actions');
  const human = app.state.me.kind === 'human';
  if (decision.status === 'proposed' && human) actions.append(button('Approve', 'button primary', () => showDecisionStatus(decision, 'approved')));
  if (decision.status === 'approved') actions.append(button('Mark implemented', 'button', () => showDecisionStatus(decision, 'implemented')));
  if (decision.status === 'implemented' && decision.implemented_by !== app.state.me.id) actions.append(button('Verify independently', 'button primary', () => showDecisionStatus(decision, 'verified')));
  if (human && decision.status !== 'revoked') actions.append(button('Revoke', 'button danger', () => showDecisionStatus(decision, 'revoked')));
  actions.append(button('History', 'text-button', () => showDecisionHistory(decision)));
  card.append(actions);
  if (decision.status === 'proposed' && !human) card.append(node('p', 'workflow-note', 'Waiting for human approval.'));
  if (decision.status === 'implemented' && decision.implemented_by === app.state.me.id) card.append(node('p', 'workflow-note', 'Another participant must verify your implementation.'));
  if (decision.approved_by) card.append(node('p', 'workflow-note', `Approved by ${person(decision.approved_by).name}${decision.verified_by ? ` · Verified by ${person(decision.verified_by).name}` : ''}`));
  return card;
}

async function showFileClaims(task) {
  const generation = app.personalGeneration;
  const { dialog, body } = dialogBase('File ownership', task.title);
  body.append(loading('Reading scoped ownership records…')); dialog.showModal();
  let permissions = null;
  let rows = [];
  let next = null;
  let error = '';
  let busy = false;
  let pending = null;
  const values = { root: '', path: '', kind: '' };
  const releases = new Map();
  const releaseInputs = new Map();
  const histories = new Map();
  const current = () => dialog.isConnected && app.authenticated && generation === app.personalGeneration;
  const taskQuery = '?task_id=' + encodeURIComponent(task.id);
  async function load(older = false) {
    const result = await api('/api/file-claims' + taskQuery + (older && next ? '&before=' + encodeURIComponent(next) : ''));
    const policy = await api('/api/work-permissions' + taskQuery);
    if (!current()) return;
    permissions = policy;
    rows = older ? [...rows, ...result.claims.filter((row) => !rows.some((old) => old.id === row.id))] : result.claims;
    next = result.next_before;
    if (!values.root) values.root = policy.rules[0]?.root_id || '';
    if (!values.kind) values.kind = policy.kinds[0]?.value || '';
    render();
  }
  async function mutation(path, payload, onSuccess) {
    if (busy) return;
    busy = true; error = ''; render();
    try {
      const result = await api(path, payload);
      if (!current()) return;
      onSuccess(result);
      await load();
    } catch (failure) {
      if (current()) { error = errorText(failure) + ' Confirm the same request, or close and review its record before changing it.'; }
    } finally { if (current()) { busy = false; render(); } }
  }
  function render() {
    if (!current() || !permissions) return;
    body.replaceChildren(node('p', 'workflow-note', 'Claims coordinate ownership on this board. Each external writer must also check its own file permissions before editing. Closing this dialog does not cancel a submitted claim.'));
    if (error) { const text = node('p', 'field-error', error); text.id = 'file-claim-error'; text.setAttribute('role', 'alert'); body.append(text); }
    const own = task.owner_id === app.state.me.id;
    if (own && permissions.configured && permissions.rules.length) {
      const form = node('form');
      const roots = [...new Set(permissions.rules.map((rule) => rule.root_id))];
      const root = field(form, 'Approved project root', 'claim-root', 'select', { options: roots.map((id) => ({ value: id, label: id })), value: values.root, required: true });
      const path = field(form, 'Relative work path', 'claim-path', 'text', { value: values.path, required: true, maxLength: permissions.limits.path });
      const kind = field(form, 'Claim scope', 'claim-kind', 'select', { options: permissions.kinds, value: values.kind, required: true });
      for (const [control, name] of [[root, 'root'], [path, 'path'], [kind, 'kind']]) {
        control.disabled = busy || pending !== null;
        control.addEventListener('input', () => { values[name] = control.value; });
        control.addEventListener('change', () => { values[name] = control.value; });
      }
      const approved = node('details'); approved.append(node('summary', '', 'Approved paths for this task'));
      for (const rule of permissions.rules) approved.append(node('p', 'workflow-note', rule.root_id + '/' + rule.path + ' · ' + rule.kind));
      form.append(approved);
      const submit = node('button', 'button primary', pending ? 'Confirm same claim' : 'Claim work path');
      submit.type = 'submit'; submit.id = 'file-claim-submit'; submit.disabled = busy; form.append(submit);
      form.addEventListener('submit', (event) => {
        event.preventDefault(); if (busy || !form.reportValidity()) return;
        pending = pending || { task_id: task.id, task_version: task.version, root_id: values.root, path: values.path, kind: values.kind, client_id: newClientId() };
        mutation('/api/file-claims', pending, () => { pending = null; values.path = ''; });
      });
      body.append(form);
    } else body.append(node('p', 'workflow-note', own ? 'No work paths are configured for this task.' : 'Only this task’s owner can claim its files.'));
    for (const row of rows) {
      const record = node('section', 'history-entry'); record.dataset.claimId = row.id;
      record.append(node('h3', '', row.root_id + '/' + row.path), node('p', 'workflow-note', person(row.owner_id).name + ' · ' + row.kind + ' · ' + row.status + ' · version ' + row.version));
      const history = button('Ownership history', 'button subtle', async () => {
        if (busy) return; busy = true; render();
        try { const result = await api('/api/file-claims/' + encodeURIComponent(row.id) + '/history'); if (current()) histories.set(row.id, result.history); }
        catch (failure) { if (current()) error = errorText(failure); }
        finally { if (current()) { busy = false; render(); } }
      });
      history.disabled = busy; record.append(history);
      for (const event of histories.get(row.id) || []) record.append(node('p', 'workflow-note', person(event.actor_id).name + ' · ' + event.action + ' · ' + formatDateTime(event.created_at) + (event.reason ? ' · ' + event.reason : '')));
      if (row.status === 'active' && (row.owner_id === app.state.me.id || app.state.me.kind === 'human')) {
        const form = node('form');
        const release = releases.get(row.id);
        const reason = field(form, 'Release reason', 'release-' + row.id, 'text', { required: true, maxLength: permissions.limits.reason, value: release?.reason || releaseInputs.get(row.id) || '' });
        reason.disabled = busy || !!release;
        reason.addEventListener('input', () => releaseInputs.set(row.id, reason.value));
        const submit = node('button', 'button', release ? 'Confirm same release' : 'Release ownership');
        submit.type = 'submit'; submit.disabled = busy; form.append(submit);
        form.addEventListener('submit', (event) => {
          event.preventDefault(); if (busy || !form.reportValidity()) return;
          const data = release || { version: row.version, reason: reason.value };
          releases.set(row.id, data);
          mutation('/api/file-claims/' + encodeURIComponent(row.id) + '/release', data, () => { releases.delete(row.id); releaseInputs.delete(row.id); });
        });
        record.append(form);
      }
      body.append(record);
    }
    if (!rows.length) body.append(node('p', '', 'No ownership records for this task.'));
    if (next) { const older = button('Older ownership records', 'button subtle', () => load(true).catch((failure) => { if (current()) { error = errorText(failure); render(); } })); older.disabled = busy; body.append(older); }
  }
  try { await load(); }
  catch (failure) { if (current()) body.replaceChildren(node('p', 'field-error', errorText(failure))); }
}

function taskCard(task) {
  const card = recordBase(task);
  const footer = node('div', 'card-footer');
  const owner = node('div', 'card-person');
  const participant = person(task.owner_id);
  owner.append(avatar(participant, 'tiny'), node('span', '', participant.name));
  const select = node('select', 'task-status-control');
  select.setAttribute('aria-label', `Status of ${task.title}`);
  for (const [status, label] of Object.entries(taskStatusLabels())) {
    const option = node('option', '', label);
    option.value = status;
    select.append(option);
  }
  select.value = task.status;
  select.addEventListener('change', async () => {
    const status = select.value;
    select.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(task.id)}/status`, { status, version: task.version });
      await refreshState();
      announce('Task status updated.');
    } catch (error) {
      select.value = task.status;
      notify(errorText(error), true);
      await refreshState().catch(() => {});
    } finally { select.disabled = false; }
  });
  footer.append(owner, select);
  if (app.state.task_options) {
    const planning = node('div', 'card-actions');
    const priority = app.state.task_options.priorities[task.priority];
    planning.append(node('span', 'status-badge', priority + ' priority'));
    if (task.due_date) planning.append(node('span', 'workflow-note', 'Due ' + task.due_date.split('-').reverse().join('.')));
    planning.append(button('Edit details', 'text-button', () => showTaskDetails(task)));
    planning.append(button('Move task', 'text-button', () => showTaskMove(task)));
    card.append(planning);
  }
  card.append(footer, node('p', 'workflow-note', `Updated ${formatDateTime(task.updated_at)}`));
  if (app.state.capabilities?.file_claims) card.append(button('File ownership', 'button subtle', () => showFileClaims(task)));
  return card;
}

function field(form, label, name, type = 'text', options = {}) {
  const wrapper = node('div', 'form-field');
  const id = `dialog-${name}`;
  const labelElement = node('label', '', label);
  labelElement.htmlFor = id;
  const control = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
  control.id = id;
  control.name = name;
  control.dir = 'auto';
  if (type !== 'textarea' && type !== 'select') control.type = type;
  if (options.required) control.required = true;
  if (options.maxLength) control.maxLength = options.maxLength;
  if (options.placeholder) control.placeholder = options.placeholder;
  if (options.pattern) control.pattern = options.pattern;
  if (type === 'select') {
    for (const option of options.options || []) {
      const item = node('option', '', option.label);
      item.value = option.value;
      control.append(item);
    }
  }
  if (options.value !== undefined) control.value = options.value;
  wrapper.append(labelElement, control);
  if (options.hint) {
    const hint = node('p', 'field-hint', options.hint);
    hint.id = `${id}-hint`;
    control.setAttribute('aria-describedby', hint.id);
    wrapper.append(hint);
  }
  form.append(wrapper);
  return control;
}
function dialogBase(title, description) {
  const dialog = node('dialog');
  const heading = node('div', 'dialog-header');
  const copy = node('div');
  const titleElement = node('h2', '', title);
  titleElement.id = `dialog-heading-${++app.dialogSequence}`;
  copy.append(titleElement);
  if (description) { const text = node('p', 'dialog-description', description); text.id = `dialog-description-${app.dialogSequence}`; copy.append(text); dialog.setAttribute('aria-describedby', text.id); }
  heading.append(copy, button('×', 'icon-button', () => dialog.close()));
  heading.lastElementChild.setAttribute('aria-label', 'Close dialog');
  dialog.setAttribute('aria-labelledby', titleElement.id);
  dialog.append(heading);
  const body = node('div', 'dialog-body');
  dialog.append(body);
  $('dialog-root').append(dialog);
  const previouslyFocused = document.activeElement;
  dialog.addEventListener('close', () => {
    const focused = document.activeElement;
    // A queued close event can arrive after the user resumes typing elsewhere.
    // Restore ordinary dialog focus only if no other control already has it.
    const restoreFocus = !focused || focused === document.body || dialog.contains(focused);
    dialog.remove();
    if (restoreFocus && previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) previouslyFocused.focus();
  });
  return { dialog, body };
}
function formDialog(title, description, setup, submitLabel, submitAction) {
  const { dialog, body } = dialogBase(title, description);
  const form = node('form');
  const controls = setup(form);
  const error = node('p', 'field-error');
  error.hidden = true;
  error.setAttribute('role', 'alert');
  const actions = node('div', 'dialog-actions');
  const cancel = button('Cancel', 'button subtle', () => dialog.close());
  const submit = node('button', 'button primary', submitLabel);
  submit.type = 'submit';
  actions.append(cancel, submit);
  form.append(error, actions);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    submit.disabled = true;
    cancel.disabled = true;
    error.hidden = true;
    try {
      await submitAction(controls);
      dialog.close();
    } catch (failure) {
      error.textContent = errorText(failure);
      if (failure.status === 409) {
        error.textContent += ' Close this dialog and review the latest version.';
        await refreshState().catch(() => {});
      }
      error.hidden = false;
      error.tabIndex = -1;
      error.focus();
    } finally { submit.disabled = false; cancel.disabled = false; }
  });
  body.append(form);
  dialog.showModal();
  const first = form.querySelector('input, textarea, select');
  if (first) first.focus();
}
function showNewParticipant() {
  formDialog('Add an agent', 'Give the agent a name and a stable ID. Its access credential is stored by the local server.', (form) => ({
    name: field(form, 'Display name', 'name', 'text', { required: true, maxLength: LIMITS.name, placeholder: 'For example, Research agent' }),
    id: field(form, 'Agent ID', 'id', 'text', { required: true, maxLength: LIMITS.participantId, pattern: '[A-Za-z0-9](?:[A-Za-z0-9_]|-)*', placeholder: 'research-agent', hint: 'Start with a letter or number. Use letters, numbers, hyphens, or underscores.' }),
  }), 'Add agent', async (controls) => {
    await api('/api/participants', { id: controls.id.value.trim(), name: controls.name.value.trim(), kind: 'agent' });
    await refreshState();
    notify('Agent added. Its joining brief is ready in Project brief. Adding an agent does not start its model.');
  });
}
function showRenameParticipant(participant) {
  formDialog(`Rename ${participant.name}`, 'Only the shown name changes. The ID, messages and history stay the same, and the old name is kept in the rename history.', (form) => ({
    name: field(form, 'New display name', 'name', 'text', { required: true, maxLength: LIMITS.name, value: participant.name }),
  }), 'Rename', async (controls) => {
    // seen_name is the name this list showed, so a rename made meanwhile by someone else is refused, not overwritten.
    const renamed = await api('/api/participants/rename', { id: participant.id, name: controls.name.value, seen_name: participant.name });
    await refreshState();
    notify(`Renamed to ${renamed.name}.`);
  });
}
function showParticipantStatus(participant, restore) {
  // An in-page form, not a browser pop-up: the reason is recorded in the history, and seen_name refuses a stale view.
  formDialog(`${restore ? 'Restore' : 'Remove'} ${participant.name}`, restore
    ? 'They can sign in and act again with their existing access. Everything they wrote is still there.'
    : 'They can no longer sign in or act, from any browser, tool or provider module. Every message and record they made stays, and you can restore them later.',
  (form) => ({
    reason: field(form, 'Reason', 'reason', 'text', { required: true, maxLength: LIMITS.name }),
  }), restore ? 'Restore' : 'Remove', async (controls) => {
    await api(`/api/participants/${restore ? 'restore' : 'remove'}`, { id: participant.id, seen_name: participant.name, reason: controls.reason.value });
    await refreshState();
    notify(restore ? `${participant.name} was restored.` : `${participant.name} was removed. Their messages stay.`);
  });
}
function showNewChannel() {
  const agents = app.state.participants.filter((participant) => participant.kind === 'agent' && participant.active !== false);
  if (!agents.length) { showNewParticipant(); return; }
  formDialog('New conversation', 'Choose one agent for a private conversation, or several for a group. You are included automatically.', (form) => {
    const title = field(form, 'Conversation name', 'title', 'text', { required: true, maxLength: LIMITS.title, placeholder: 'For example, Planning the next step' });
    const wrapper = node('fieldset', 'form-field');
    const legend = node('legend', '', 'Choose agents');
    const list = node('div', 'check-list');
    for (const agent of agents) {
      const label = node('label', 'check-option');
      const checkbox = node('input');
      checkbox.type = 'checkbox';
      checkbox.name = 'members';
      checkbox.value = agent.id;
      const copy = node('span', 'check-option-copy');
      copy.append(node('strong', '', agent.name), node('span', '', agent.id));
      label.append(checkbox, avatar(agent, 'small'), copy);
      list.append(label);
    }
    wrapper.append(legend, list);
    form.append(wrapper);
    return { title, list };
  }, 'Create conversation', async (controls) => {
    const members = [...controls.list.querySelectorAll('input:checked')].map((input) => input.value);
    if (!members.length) throw new Error('Choose at least one agent.');
    const channel = await api('/api/channels', { title: controls.title.value.trim(), members: [app.state.me.id, ...members] });
    await refreshState();
    selectChannel(channel.id);
    notify('Conversation created.');
  });
}
function defaultChannel() { return app.channelId || app.state.channels[0]?.id; }
function showNewDecision() {
  formDialog('Propose a decision', 'Record what should happen and why. A human must approve the proposal before it can be marked implemented.', (form) => ({
    channel: field(form, 'Conversation', 'channel', 'select', { options: channelOptions(), value: defaultChannel(), required: true }),
    title: field(form, 'Decision title', 'title', 'text', { required: true, maxLength: LIMITS.title, placeholder: 'What are we deciding?' }),
    body: field(form, 'Proposal', 'body', 'textarea', { required: true, maxLength: LIMITS.body, placeholder: 'Describe the change and the reason.' }),
    source: field(form, 'Source', 'source', 'textarea', { maxLength: LIMITS.source, placeholder: 'A message, document, file path, or reference.' }),
    evidence: field(form, 'Evidence', 'evidence', 'textarea', { maxLength: LIMITS.evidence, placeholder: 'What supports this proposal?' }),
  }), 'Save proposal', async (controls) => {
    await api('/api/decisions', { channel_id: controls.channel.value, title: controls.title.value.trim(), body: controls.body.value, source: controls.source.value, evidence: controls.evidence.value });
    await refreshState();
    notify('Proposal saved. It is waiting for human approval.');
  });
}
function taskPlanningFields(form, task = {}) {
  const options = app.state.task_options;
  if (!options) return {};
  return {
    priority: field(form, 'Priority', 'priority', 'select', { options: Object.entries(options.priorities).map(([value, label]) => ({ value, label })), value: task.priority || options.default_priority }),
    dueDate: field(form, 'Due date (optional)', 'due-date', 'date', { value: task.due_date || '', hint: 'A calendar date. Leave blank when there is no deadline.' }),
  };
}
function showTaskDetails(task) {
  formDialog('Edit task', task.title, form => ({
    title: field(form, 'Task title', 'title', 'text', { required: true, maxLength: LIMITS.title, value: task.title }),
    body: field(form, 'Details', 'body', 'textarea', { maxLength: LIMITS.body, value: task.body || '' }),
    ...taskPlanningFields(form, task),
  }), 'Save details', async controls => {
    await api('/api/tasks/' + encodeURIComponent(task.id) + '/details', { title: controls.title.value.trim(), body: controls.body.value, priority: controls.priority.value, due_date: controls.dueDate.value || null, version: task.version });
    await refreshState();
    announce('Task details updated.');
  });
}
function ownerOptions(owner, channelId, preferred) {
  const members = app.state.channels.find((item) => item.id === channelId)?.members || [];
  owner.replaceChildren();
  // A removed member is offered only as the task's current owner, so editing a task never changes its owner silently.
  for (const participant of app.state.participants.filter((item) => members.includes(item.id) && (item.active !== false || item.id === preferred))) {
    const option = node('option', '', participant.name);
    option.value = participant.id;
    owner.append(option);
  }
  owner.value = members.includes(preferred) ? preferred : app.state.me.id;
}
function showTaskMove(task) {
  formDialog('Move task', task.title, (form) => {
    const channel = field(form, 'Conversation', 'move-channel', 'select', { options: channelOptions(), value: task.channel_id, required: true });
    const owner = field(form, 'Owner', 'move-owner', 'select', { required: true, options: [] });
    form.append(node('p', 'workflow-note', 'Everyone in the chosen conversation will see this task.'));
    channel.addEventListener('change', () => ownerOptions(owner, channel.value, owner.value));
    ownerOptions(owner, channel.value, task.owner_id);
    return { channel, owner };
  }, 'Move task', async (controls) => {
    await api('/api/tasks/' + encodeURIComponent(task.id) + '/move', { channel_id: controls.channel.value, owner_id: controls.owner.value, version: task.version });
    await refreshState();
    announce('Task moved.');
  });
}
function showNewTask() {
  formDialog('Create a task', 'Choose one owner and describe a concrete next step.', (form) => {
    const channel = field(form, 'Conversation', 'channel', 'select', { options: channelOptions(), value: defaultChannel(), required: true });
    const title = field(form, 'Task title', 'title', 'text', { required: true, maxLength: LIMITS.title, placeholder: 'What needs to happen?' });
    const body = field(form, 'Details', 'body', 'textarea', { maxLength: LIMITS.body, placeholder: 'Add the context and the expected result.' });
    const owner = field(form, 'Owner', 'owner', 'select', { required: true, options: [] });
    const updateOwners = () => {
      const selected = owner.value;
      const members = app.state.channels.find((item) => item.id === channel.value)?.members || [];
      owner.replaceChildren();
      for (const participant of app.state.participants.filter((item) => members.includes(item.id) && item.active !== false)) {
        const option = node('option', '', participant.name);
        option.value = participant.id;
        owner.append(option);
      }
      owner.value = members.includes(selected) ? selected : app.state.me.id;
    };
    channel.addEventListener('change', updateOwners);
    updateOwners();
    return { channel, title, body, owner, ...taskPlanningFields(form) };
  }, 'Create task', async (controls) => {
    const planning = controls.priority ? { priority: controls.priority.value, due_date: controls.dueDate.value || null } : {};
    await api('/api/tasks', { channel_id: controls.channel.value, title: controls.title.value.trim(), body: controls.body.value, owner_id: controls.owner.value, ...planning });
    await refreshState();
    notify('Task created.');
  });
}

function showDecisionStatus(decision, status) {
  const labels = { approved: 'Approve decision', implemented: 'Mark implemented', verified: 'Verify independently', revoked: 'Revoke decision' };
  const descriptions = {
    approved: 'Record your human approval. This updates the decision record; it does not execute the proposal.',
    implemented: 'Record a completed implementation and the evidence that shows what changed.',
    verified: 'Record your independent check. The person who implemented a decision cannot verify it.',
    revoked: 'Withdraw this decision. Its record and history remain available.',
  };
  const required = status === 'implemented' || status === 'verified';
  formDialog(labels[status], descriptions[status], (form) => {
    const title = node('p', 'card-body', decision.title);
    title.dir = 'auto';
    form.append(title);
    return { evidence: field(form, required ? 'Evidence (required)' : 'Evidence or note (optional)', 'evidence', 'textarea', { required, maxLength: LIMITS.evidence, placeholder: required ? 'Describe the check and reference its result.' : 'Add any relevant context.' }) };
  }, labels[status], async (controls) => {
    const payload = { status, version: decision.version };
    if (required || controls.evidence.value.trim()) payload.evidence = controls.evidence.value;
    await api(`/api/decisions/${encodeURIComponent(decision.id)}/status`, payload);
    await refreshState();
    notify(`Decision marked ${status}.`);
  });
}
async function showDecisionHistory(decision) {
  const { dialog, body } = dialogBase('Decision history', decision.title);
  body.append(loading('Loading the local history…'));
  dialog.showModal();
  try {
    const result = await api(`/api/decisions/${encodeURIComponent(decision.id)}/history`);
    if (!dialog.isConnected) return;
    body.replaceChildren();
    if (!result.history.length) { body.append(node('p', 'card-body', 'No history is available for this decision.')); return; }
    for (const entry of result.history) {
      const item = node('div', 'history-entry');
      const snapshot = entry.after || entry;
      const meta = node('div', 'history-meta');
      if (snapshot.status || entry.to_status) meta.append(statusBadge(snapshot.status || entry.to_status));
      const actor = entry.actor_name || (entry.actor_id ? person(entry.actor_id).name : '');
      if (actor) meta.append(node('span', '', actor));
      if (entry.created_at || entry.updated_at) meta.append(node('time', '', formatDateTime(entry.created_at || entry.updated_at)));
      if (snapshot.version !== undefined) meta.append(node('span', '', `Version ${snapshot.version}`));
      item.append(meta);
      if (snapshot.evidence) item.append(node('p', 'evidence-label', 'Evidence'), node('p', '', plain(snapshot.evidence)));
      if (snapshot.source) item.append(node('p', 'evidence-label', 'Source'), node('p', '', plain(snapshot.source)));
      if (snapshot.body) item.append(node('p', '', plain(snapshot.body)));
      for (const text of item.querySelectorAll('p')) text.dir = 'auto';
      // Preserve any backend history fields rather than concealing a detail we do not recognize.
      const known = new Set(['id', 'decision_id', 'status', 'from_status', 'to_status', 'actor_id', 'actor_name', 'created_at', 'updated_at', 'version', 'evidence', 'source', 'body', 'before', 'after']);
      for (const [key, value] of Object.entries(entry)) if (!known.has(key) && value !== null && value !== '') item.append(node('p', 'evidence-label', key.replaceAll('_', ' ')), node('p', '', plain(value)));
      body.append(item);
    }
  } catch (error) { if (dialog.isConnected) body.replaceChildren(node('p', 'field-error', errorText(error))); }
}


function memoryAvailable() { return Array.isArray(app.state.knowledge) && Array.isArray(app.state.sources); }
function projectChannelLabel(channel) {
  return `${channel.kind === 'shared' ? 'Shared' : channel.kind === 'private' ? 'Private' : 'Group'} · ${channel.title}`;
}
function projectChannelOptions() { return app.state.channels.map((channel) => ({ value: channel.id, label: projectChannelLabel(channel) })); }
function visibleMemory() { return app.state.knowledge.filter((entry) => !app.projectScope || entry.channel_id === app.projectScope); }
function visibleSources() { return app.state.sources.filter((source) => !app.projectScope || source.channel_id === app.projectScope); }
async function refreshBrief() {
  if (!memoryAvailable()) return;
  const scope = app.projectScope;
  if (app.briefRequest?.scope === scope) return app.briefRequest.promise;
  const request = { scope, promise: null };
  request.promise = (async () => {
    try {
      const result = await api(`/api/brief${scope ? `?channel_id=${encodeURIComponent(scope)}` : ''}`);
      if (scope !== app.projectScope) return;
      app.brief = result;
      app.sourceScan = result.source_scan;
      app.briefError = '';
      await refreshState();
      const { generated_at, ...content } = result;
      const signature = JSON.stringify(content, stableMetadata);
      if (signature !== app.briefSignature) {
        app.briefSignature = signature;
        if (app.view === 'brief' && app.projectTab === 'joining') renderView();
      } else if ($('brief-generated')) $('brief-generated').textContent = `Built ${formatDateTime(generated_at)}`;
      updateSourceScan();
    } catch (error) {
      if (scope === app.projectScope) { app.briefError = errorText(error); if (app.view === 'brief') renderView(); }
      throw error;
    }
  })();
  app.briefRequest = request;
  try { await request.promise; } finally { if (app.briefRequest === request) app.briefRequest = null; }
}
async function observeKnowledge() {
  if (!memoryAvailable()) return;
  const scope = app.projectScope;
  const result = await api(`/api/knowledge${scope ? `?channel_id=${encodeURIComponent(scope)}` : ''}`);
  if (scope !== app.projectScope) return;
  const previousScan = JSON.stringify(app.sourceScan, stableMetadata);
  app.sourceScan = result.source_scan;
  await refreshState();
  if (app.view === 'brief' && app.projectTab === 'knowledge' && previousScan !== JSON.stringify(app.sourceScan, stableMetadata)) renderView();
  else updateSourceScan();
}
function sourceScanText(scan = app.sourceScan) {
  if (!scan?.checked_at) return 'Source freshness has not been checked yet.';
  const seconds = Math.floor(scan.age_seconds);
  const age = Number.isFinite(scan.age_seconds) ? `${seconds} ${seconds === 1 ? 'second' : 'seconds'} old` : 'age unknown';
  return `Last source scan ${formatDateTime(scan.checked_at)} · ${age} · ${scan.available_count} of ${scan.configured_count} configured sources indexed${scan.issues?.length ? ` · ${scan.issues.length} need attention` : ''}. Indexing does not validate a claim.`;
}
function updateSourceScan() { if ($('source-scan-summary')) $('source-scan-summary').textContent = sourceScanText(); }

function changeProjectScope(scope) {
  app.projectScope = scope;
  persistPreferences();
  app.brief = null;
  app.briefSignature = '';
  app.briefError = '';
  app.sourceScan = null;
  renderView();
  (app.projectTab === 'joining' ? refreshBrief() : observeKnowledge()).catch((error) => notify(errorText(error), true));
}
function projectTabs() {
  const tabs = node('div', 'project-tabs');
  tabs.setAttribute('aria-label', 'Project brief views');
  for (const [value, label] of [['joining', 'Joining brief'], ['knowledge', 'Knowledge']]) {
    const control = button(label, `project-tab${app.projectTab === value ? ' active' : ''}`, () => {
      app.projectTab = value;
      persistPreferences();
      renderView();
      (value === 'joining' ? refreshBrief() : observeKnowledge()).catch((error) => notify(errorText(error), true));
    });
    control.setAttribute('aria-pressed', String(app.projectTab === value));
    tabs.append(control);
  }
  return tabs;
}
function renderProjectView() {
  const actions = node('div', 'header-action project-header-actions');
  if (memoryAvailable()) {
    if (app.projectTab === 'joining') {
      const copy = button('Copy brief', 'button compact', copyJoiningBrief);
      const download = button('Export brief', 'button subtle compact', exportJoiningBrief);
      copy.disabled = !app.brief;
      download.disabled = !app.brief;
      actions.append(copy, download);
    } else actions.append(button('+ Add knowledge', 'button primary compact', () => showKnowledgeForm()));
  }
  setHeader('ONE SHARED STARTING POINT', app.projectTab === 'joining' ? 'Project brief' : 'Knowledge', app.projectTab === 'joining' ? 'Context, rules, decisions, and lessons for the next person or agent to join.' : 'Keep what you learned, its evidence, and what still needs a check.', actions);
  const board = node('div', 'project-board');
  board.append(projectTabs());
  if (!memoryAvailable()) {
    board.append(emptyState('◎', 'Project memory is unavailable.', 'Restart the local app to load its updated memory service. Conversations remain available.'));
    $('view-content').append(board);
    return;
  }
  const toolbar = node('div', 'project-toolbar');
  const scope = node('select', 'board-filter');
  scope.id = 'project-scope';
  scope.setAttribute('aria-label', 'Project brief conversation scope');
  for (const option of [{ value: '', label: 'All visible conversations' }, ...projectChannelOptions()]) {
    const item = node('option', '', option.label); item.value = option.value; scope.append(item);
  }
  scope.value = app.projectScope || '';
  scope.addEventListener('change', () => changeProjectScope(scope.value));
  toolbar.append(scope);
  if (app.state.me.kind === 'human') toolbar.append(button('Refresh sources', 'button subtle compact', refreshSources));
  toolbar.append(node('span', 'scope-note', app.projectScope ? 'Limited to this conversation' : 'Includes your private conversations'));
  board.append(toolbar);
  const scan = node('p', 'source-scan-summary', sourceScanText()); scan.id = 'source-scan-summary'; board.append(scan);
  if (app.sourceScan?.issues?.length) { const issues = node('div', 'brief-warning scan-issues'); issues.append(node('strong', '', 'Some configured sources are unavailable or unknown.')); for (const issue of app.sourceScan.issues) issues.append(node('p', '', `${app.state.sources.find((source) => source.id === issue.source_id)?.title || issue.source_id}: ${issue.status}${issue.error ? ` · ${issue.error}` : ''}`)); board.append(issues); }
  if (app.projectTab === 'knowledge') {
    const explanation = node('div', 'memory-explanation');
    explanation.append(node('strong', '', 'A sourced record is not approval.'), node('p', '', '“Documented” means evidence is recorded. Human approval and independent verification belong in the decision register. Existing project documents and Git remain the authoritative evidence.'));
    board.append(explanation);
    const entries = visibleMemory();
    if (!entries.length) board.append(emptyState('◎', 'Leave the next agent a useful starting point.', 'Add a fact, working context, or a lesson. Include its source and what worked or failed.', 'Add knowledge', () => showKnowledgeForm()));
    else { const grid = node('div', 'card-grid knowledge-grid'); for (const entry of entries) grid.append(knowledgeCard(entry)); board.append(grid); }
    const sources = visibleSources();
    if (sources.length) { const section = briefSection('Indexed sources & freshness', 'These are bounded local snapshots. Refreshing sources checks whether their evidence has changed.'); const grid = node('div', 'source-grid'); for (const source of sources) grid.append(sourceCard(source)); section.append(grid); board.append(section); }
  } else if (app.briefError) board.append(emptyState('◎', 'The brief could not be loaded.', app.briefError, 'Try again', () => refreshBrief().catch((error) => notify(errorText(error), true))));
  else if (!app.brief) board.append(loading('Building the brief from the local record…'));
  else renderJoiningBrief(board, app.brief);
  $('view-content').append(board);
}
function briefSection(title, description) {
  const section = node('section', 'brief-section');
  const heading = node('div', 'brief-section-heading');
  heading.append(node('h2', '', title));
  if (description) heading.append(node('p', '', description));
  section.append(heading);
  return section;
}
function appendBriefRecords(board, title, description, records, renderer) {
  if (!records.length) return;
  const section = briefSection(title, description);
  const grid = node('div', 'card-grid');
  for (const record of records) grid.append(renderer(record));
  section.append(grid);
  board.append(section);
}
// The daily knowledge check of the selected conversation (unit 3): the current check day's state, the last check and
// every missed day stay visible; recording it is one dialog. The server decides the check day and refuses a second one.
function knowledgeCheckSection(check) {
  const section = briefSection('Daily knowledge check', `Each check day runs from ${check.time} to ${check.time} the next day (${check.timezone}). A check recorded after midnight completes the previous day, marked late.`);
  const status = node('div', 'knowledge-check');
  status.dataset.state = check.state;
  status.append(node('strong', '', `${check.check_date}: ${check.state === 'done' ? 'done' : 'due'}`));
  if (check.last) {
    const outcome = CHECK_OUTCOMES.find((item) => item.value === check.last.outcome)?.label || check.last.outcome;
    const last = node('p', 'workflow-note', `Last check ${check.last.check_date} by ${person(check.last.actor_id).name} · ${outcome}${check.last.late ? ' · completed late' : ''}${check.last.note ? ` · ${check.last.note}` : ''}`);
    last.dir = 'auto';
    status.append(last);
  }
  if (check.missed.length) status.append(node('p', 'knowledge-check-missed', `Missed: ${check.missed.join(', ')}`));
  if (check.state === 'due') status.append(button('Record the check', 'button primary', () => showKnowledgeCheck(check)));
  section.append(status);
  return section;
}
function showKnowledgeCheck(check) {
  formDialog('Record the daily knowledge check', `Check day ${check.check_date}. Record it after reconciling the brief with the project documents.`, (form) => {
    const outcome = field(form, 'Outcome', 'check-outcome', 'select', { options: CHECK_OUTCOMES, value: 'updated', required: true });
    const note = field(form, 'Note', 'check-note', 'textarea', { maxLength: LIMITS.checkNote, placeholder: 'What changed, or what you checked.' });
    return { outcome, note };
  }, 'Record check', async (controls) => {
    await api('/api/knowledge-checks', { channel_id: app.projectScope, outcome: controls.outcome.value, note: controls.note.value });
    await refreshBrief();
    announce('Knowledge check recorded.');
  });
}
function renderJoiningBrief(board, brief) {
  const intro = node('div', 'brief-intro');
  const mark = node('img', 'brief-mark'); mark.src = '/static/common-ground.svg'; mark.alt = ''; mark.width = 48; mark.height = 48;
  const copy = node('div');
  copy.append(node('h2', '', app.state.me.kind === 'agent' ? `Welcome, ${app.state.me.name}. Start here.` : 'A useful starting point for every agent.'), node('p', '', 'Read the sources, check the decision register, and bring evidence to the conversation. Record lessons so the next agent can build on the work.'));
  intro.append(mark, copy);
  board.append(intro);
  const overview = node('div', 'brief-overview');
  const activeTasks = (brief.tasks || []).filter((task) => task.status !== 'done');
  const owners = [...new Set(activeTasks.map((task) => person(task.owner_id).name))];
  const approvedCount = (brief.decisions || []).filter((item) => ['approved', 'implemented', 'verified'].includes(item.status)).length;
  const allClaims = [...new Map([...(brief.context || []), ...(brief.knowledge || []), ...(brief.lessons || []), ...(brief.disputed || []), ...(brief.stale || [])].map((entry) => [entry.id, entry])).values()];
  const needsReview = allClaims.filter((entry) => entry.stale || ['proposed', 'disputed'].includes(entry.status));
  for (const [label, value, detail] of [['Human approval', approvedCount, 'decisions in the approved workflow'], ['Open tasks', activeTasks.length, owners.length ? `Owners: ${owners.join(', ')}` : 'No open tasks'], ['Claims to review', needsReview.length, 'proposed, disputed, or stale'], ['Indexed sources', brief.source_scan?.available_count ?? 'Unknown', brief.source_scan ? `of ${brief.source_scan.configured_count} configured sources` : 'freshness unknown']]) {
    const item = node('div', 'brief-overview-item'); item.append(node('p', 'evidence-label', label), node('strong', '', value), node('p', 'workflow-note', detail)); overview.append(item);
  }
  board.append(overview);
  const steps = node('ol', 'brief-steps');
  for (const text of ['Read the project context and source dates.', 'Follow human-approved decisions and the agreed working rules.', 'Treat proposals, conflicts, and stale claims as open questions.', 'Add sources and outcomes when you contribute.']) steps.append(node('li', '', text));
  board.append(steps);
  const metadata = node('div', 'brief-metadata');
  const generated = node('span', '', `Built ${formatDateTime(brief.generated_at)}`); generated.id = 'brief-generated';
  metadata.append(generated, node('span', '', app.projectScope ? projectChannelLabel(app.state.channels.find((item) => item.id === app.projectScope)) : 'Only data visible to your account'));
  board.append(metadata);
  if (app.projectScope) board.append(button('Browse conversation index', 'button',
    () => showConversationIndex(app.projectScope)));
  if (brief.knowledge_check?.configured) board.append(knowledgeCheckSection(brief.knowledge_check));
  const stale = brief.stale || [];
  const disputed = brief.disputed || [];
  if (stale.length || disputed.length) {
    const warning = node('div', 'brief-warning');
    warning.append(node('strong', '', 'Some claims need another look.'), node('p', '', `${stale.length} stale record${stale.length === 1 ? '' : 's'} · ${disputed.length} disputed record${disputed.length === 1 ? '' : 's'}. Check their sources and conflicts before using them.`));
    board.append(warning);
  }
  const sourceContext = brief.source_context || [];
  appendBriefRecords(board, 'What the project documents say', 'Attributed headings and selected metadata from indexed evidence. A current snapshot does not make every statement current or approved.', sourceContext, sourceContextCard);
  if (Object.keys(brief.truncation || {}).length) { const warning = node('div', 'brief-warning'); warning.append(node('strong', '', 'This brief is shortened.'), node('p', '', truncationText(brief.truncation))); board.append(warning); }
  const context = brief.context || [];
  appendBriefRecords(board, 'Project context & working rules', 'Documented context is evidence. Approval is shown separately in the decision register.', context, knowledgeCard);
  const decisions = brief.decisions || [];
  const approved = decisions.filter((item) => ['approved', 'implemented', 'verified'].includes(item.status));
  appendBriefRecords(board, 'Approved decisions', 'Approval records the human’s decision. “Verified” means another participant checked the implementation.', approved, decisionCard);
  appendBriefRecords(board, 'Knowledge claims', 'Source attribution and the exact review status stay attached to each claim.', brief.knowledge || [], knowledgeCard);
  appendBriefRecords(board, 'Lessons: what worked and what failed', 'Outcomes are recorded claims with evidence, not automatic learning or proof of a strategy.', brief.lessons || [], knowledgeCard);
  appendBriefRecords(board, 'Proposed decisions & open questions', 'These proposals have not received human approval.', decisions.filter((item) => item.status === 'proposed'), decisionCard);
  const attention = [...new Map([...disputed, ...stale].map((entry) => [entry.id, entry])).values()];
  appendBriefRecords(board, 'Needs attention', 'Stale evidence and conflicting claims remain visible until someone resolves them.', attention, knowledgeCard);
  appendBriefRecords(board, 'Open tasks', 'Current ownership and status from this conversation scope.', (brief.tasks || []).filter((task) => task.status !== 'done'), taskCard);
  appendBriefRecords(board, 'Sources & freshness', 'Indexed snapshots retain provenance and dates. Read them before relying on a claim.', brief.sources || [], sourceCard);
  if (!context.length && !(brief.knowledge || []).length && !(brief.lessons || []).length && !decisions.length && !(brief.sources || []).length) board.append(emptyState('◎', 'The project record is ready to grow.', 'Add sourced context, decisions, and lessons. New agents will see the same scoped record.', 'Add knowledge', () => showKnowledgeForm()));
  const limitations = briefSection('How to use this brief');
  const notes = node('ul', 'brief-limitations');
  for (const text of ['Existing project documents and Git remain the authoritative evidence.', 'A documented claim is not a human-approved action. No AI model is started or trained by this board.', ...(brief.limitations || [])]) notes.append(node('li', '', text));
  limitations.append(notes); board.append(limitations);
}
function allowedReferenceChannel(targetChannelId, referenceChannelId) {
  const target = app.state.channels.find((channel) => channel.id === targetChannelId);
  const reference = app.state.channels.find((channel) => channel.id === referenceChannelId);
  return Boolean(target && reference && target.members.every((member) => reference.members.includes(member)));
}
function safeReferenceLink(value, label) {
  try {
    const url = new URL(value.trim());
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    const link = node('a', 'text-link', label); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  } catch { return null; }
}
function knowledgeCard(entry) {
  const card = recordBase(entry);
  card.classList.add('knowledge-card');
  const flags = node('div', 'knowledge-flags');
  flags.append(node('span', 'kind-badge', entry.kind === 'context' ? 'Working context' : entry.kind === 'lesson' ? 'Lesson' : 'Fact'));
  if (entry.kind === 'lesson') flags.append(node('span', `outcome-badge outcome-${entry.outcome}`, entry.outcome === 'not_applicable' ? 'No outcome recorded' : `Outcome: ${entry.outcome}`));
  if (entry.stale) flags.append(node('span', 'status-badge status-stale', 'Stale evidence'));
  card.insertBefore(flags, card.children[1]);
  if (entry.source) { const section = evidenceSection('Source', entry.source); const link = safeReferenceLink(entry.source, 'Open source reference ↗'); if (link) section.append(link); card.append(section); }
  if (entry.evidence) { const section = evidenceSection('Evidence', entry.evidence); const link = safeReferenceLink(entry.evidence, 'Open evidence reference ↗'); if (link) section.append(link); card.append(section); }
  if ((entry.source_refs || []).length) {
    const citations = node('div', 'indexed-citations');
    citations.append(node('p', 'evidence-label', 'Indexed source snapshots'));
    for (const ref of entry.source_refs) {
      const source = app.state.sources.find((item) => item.id === ref.source_id);
      const citation = button(`${source?.title || ref.source_id} · ${ref.sha256.slice(0, 12)}`, 'citation-button', () => showSourceHistory(source || { id: ref.source_id, title: ref.source_id }));
      citation.title = `Read source history. Referenced SHA-256: ${ref.sha256}`; citations.append(citation);
    }
    card.append(citations);
  }
  if (entry.stale) {
    const reason = node('div', 'record-warning');
    reason.append(node('strong', '', 'Check this evidence before using the claim.'));
    for (const text of entry.stale_reasons || []) reason.append(node('p', '', text));
    card.append(reason);
  }
  if ((entry.conflicts_with || []).length) {
    const conflicts = node('div', 'record-warning conflict-warning');
    conflicts.append(node('strong', '', 'Conflicting records'));
    for (const id of entry.conflicts_with) {
      const other = app.state.knowledge.find((item) => item.id === id);
      conflicts.append(button(other?.title || 'Referenced record', 'text-button', () => showKnowledgeDetails(other || { id, title: 'Referenced record' })));
    }
    card.append(conflicts);
  }
  const footer = node('div', 'card-footer');
  const author = person(entry.updated_by || entry.author_id);
  const owner = node('div', 'card-person'); owner.append(avatar(author, 'tiny'), node('span', '', `${entry.updated_by && entry.updated_by !== entry.author_id ? 'Updated by' : 'By'} ${author.name}`));
  footer.append(owner, node('time', '', formatDateTime(entry.updated_at))); card.append(footer);
  const actions = node('div', 'card-actions');
  actions.append(button('Edit note', 'button', () => showKnowledgeForm(app.state.knowledge.find((item) => item.id === entry.id) || entry)), button('History', 'text-button', () => showKnowledgeHistory(entry)));
  if (entry.truncated_fields?.length) actions.prepend(button('Read full note', 'button', () => showKnowledgeDetails(app.state.knowledge.find((item) => item.id === entry.id) || entry)));
  card.append(actions);
  if (entry.status === 'documented') card.append(node('p', 'workflow-note', 'Documented evidence · approval is separate.'));
  if (entry.status === 'superseded') card.append(node('p', 'workflow-note', 'Historical record · superseded by later work.'));
  return card;
}
function truncationText(truncation) {
  return Object.entries(truncation).map(([section, count]) => `${section.replaceAll('_', ' ')}: ${count.shown} of ${count.total} records shown${count.clipped_fields ? `; ${count.clipped_fields} field${count.clipped_fields === 1 ? '' : 's'} shortened` : ''}`).join(' · ');
}
function sourceContextCard(source) {
  const card = node('article', 'source-card source-context-card');
  const top = node('div', 'card-topline'); top.append(node('span', 'card-channel', 'Attributed source context'), statusBadge(source.status));
  card.append(top, node('h3', '', source.title), node('p', 'source-path', source.path), node('p', 'workflow-note', `Checked ${formatDateTime(source.checked_at)}`));
  if (source.sha256) { const hash = node('p', 'source-fingerprint', `SHA-256 ${source.sha256.slice(0, 16)}…`); hash.title = source.sha256; card.append(hash); }
  if (source.headings?.length) {
    card.append(node('p', 'evidence-label source-context-label', 'Document headings'));
    const headings = node('ul', 'source-headings'); for (const heading of source.headings) headings.append(node('li', '', heading)); card.append(headings);
  }
  if (source.projection !== undefined && source.projection !== null) {
    card.append(node('p', 'evidence-label source-context-label', 'Selected metadata · source claims'));
    const projection = node('pre', 'source-projection', plain(source.projection)); projection.dir = 'auto'; card.append(projection);
  }
  for (const warning of source.projection_warnings || []) card.append(node('p', 'projection-note', warning));
  if (source.unindexed_fields) card.append(node('p', 'projection-note', `${plain(source.unindexed_fields)} fields excluded from this projection.`));
  if (source.truncated_fields?.length) card.append(node('p', 'projection-note', `Brief shortened: ${source.truncated_fields.join(', ')}. Read the indexed source for more detail.`));
  card.append(node('p', 'workflow-note', 'Source context is evidence. Approval stays in the decision register.'), button('Read indexed source', 'text-button', () => showSourceHistory({ id: source.source_id, title: source.title })));
  return card;
}

function sourceCard(source) {
  const card = node('article', 'source-card');
  const top = node('div', 'card-topline'); top.append(node('span', 'card-channel', channelName(source.channel_id)), statusBadge(source.status));
  card.append(top, node('h3', '', source.title), node('p', 'source-path', source.path || 'No configured path'));
  if (source.sha256) { const hash = node('p', 'source-fingerprint', `SHA-256 ${source.sha256.slice(0, 16)}… · snapshot ${source.version}`); hash.title = source.sha256; card.append(hash); }
  card.append(node('p', 'workflow-note', source.checked_at ? `Checked ${formatDateTime(source.checked_at)}` : 'No source check recorded'));
  if (source.git_revision) { const git = node('p', 'workflow-note', `Git revision ${source.git_revision.slice(0, 12)}`); git.title = source.git_revision; card.append(git); }
  if (source.error) card.append(node('p', 'record-warning', source.error));
  if (source.projection_warnings?.length) for (const text of source.projection_warnings) card.append(node('p', 'projection-note', plain(text)));
  if (Number.isInteger(source.unindexed_fields) && source.unindexed_fields > 0) card.append(node('p', 'projection-note', `${source.unindexed_fields} fields excluded by the configured projection.`));
  else if (Array.isArray(source.unindexed_fields) && source.unindexed_fields.length) card.append(node('p', 'projection-note', `Unindexed fields: ${source.unindexed_fields.map(plain).join(', ')}`));
  card.append(button('Read indexed source', 'text-button', () => showSourceHistory(source)));
  return card;
}
async function refreshSources() {
  const controls = document.querySelectorAll('.project-toolbar button');
  for (const control of controls) control.disabled = true;
  try {
    const result = await api('/api/sources/refresh', {});
    app.sourceScan = result.source_scan;
    await refreshState();
    if (app.projectTab === 'joining') await refreshBrief();
    notify(`Sources checked. ${result.stale_knowledge.length} knowledge record${result.stale_knowledge.length === 1 ? '' : 's'} marked stale.`);
  } catch (error) { notify(errorText(error), true); }
  finally { for (const control of controls) control.disabled = false; }
}
function knowledgeReferences(form, channelControl, entry) {
  const sourceField = node('fieldset', 'form-field'); sourceField.append(node('legend', '', 'Indexed source citations'));
  const sourceList = node('div', 'check-list source-check-list'); sourceField.append(sourceList);
  const hint = node('p', 'field-hint', 'Citation hashes stay pinned. If a source changed, read its new snapshot before choosing “Use current snapshot”.'); sourceField.append(hint); form.append(sourceField);
  const conflictField = node('fieldset', 'form-field'); conflictField.append(node('legend', '', 'Conflicts with existing knowledge'));
  const conflictList = node('div', 'check-list'); conflictField.append(conflictList); form.append(conflictField);
  const update = () => {
    sourceList.replaceChildren(); conflictList.replaceChildren();
    const sources = app.state.sources.filter((source) => allowedReferenceChannel(channelControl.value, source.channel_id));
    for (const source of sources) {
      const existing = (entry?.source_refs || []).find((ref) => ref.source_id === source.id);
      if (source.status !== 'current' && !existing) continue;
      const row = node('div', 'source-check-row');
      const label = node('label', 'check-option');
      const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = source.id; checkbox.checked = Boolean(existing); checkbox.dataset.sha256 = existing?.sha256 || source.sha256;
      const copy = node('span', 'check-option-copy'); copy.append(node('strong', '', source.title), node('span', '', `${source.path} · ${source.status}`));
      label.append(checkbox, copy); row.append(label);
      row.append(button('Read source', 'text-button', () => showSourceHistory(source)));
      if (existing && source.sha256 !== existing.sha256) {
        const pinned = node('p', 'field-hint', `Pinned: ${existing.sha256.slice(0, 12)} · source changed`); row.append(pinned);
        if (source.status === 'current') row.append(button('Use current snapshot', 'text-button', () => { checkbox.checked = true; checkbox.dataset.sha256 = source.sha256; pinned.textContent = `Citation selected: ${source.sha256.slice(0, 12)}`; announce('Current source snapshot selected. Review the claim before saving.'); }));
      }
      sourceList.append(row);
    }
    if (!sources.length) sourceList.append(node('p', 'channel-empty', 'No indexed sources are available for this audience. Add evidence or a source reference below.'));
    for (const other of app.state.knowledge.filter((item) => item.id !== entry?.id && allowedReferenceChannel(channelControl.value, item.channel_id))) {
      const label = node('label', 'check-option'); const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = other.id; checkbox.checked = (entry?.conflicts_with || []).includes(other.id);
      const copy = node('span', 'check-option-copy'); copy.append(node('strong', '', other.title), node('span', '', `${other.status}${other.stale ? ' · stale' : ''}`)); label.append(checkbox, copy); conflictList.append(label);
    }
    if (!conflictList.children.length) conflictList.append(node('p', 'channel-empty', 'No other records in this audience.'));
  };
  channelControl.addEventListener('change', update); update();
  return { sourceList, conflictList };
}
function showKnowledgeForm(entry) {
  formDialog(entry ? 'Edit knowledge' : 'Add knowledge', 'Save a sourced record for the project. Documenting a claim does not grant human approval.', (form) => {
    const channel = field(form, 'Conversation audience', 'channel', 'select', { required: true, options: projectChannelOptions(), value: entry?.channel_id || app.projectScope || defaultChannel(), hint: 'Private knowledge stays with the members of that conversation.' });
    if (entry) channel.disabled = true;
    const kind = field(form, 'Record type', 'kind', 'select', { options: [{ value: 'context', label: 'Working context or rules' }, { value: 'fact', label: 'Fact or finding' }, { value: 'lesson', label: 'Lesson: worked or failed' }], value: entry?.kind || 'fact' });
    const title = field(form, 'Title', 'title', 'text', { required: true, maxLength: LIMITS.title, value: entry?.title || '', placeholder: 'What should the next agent know?' });
    const body = field(form, 'Context or claim', 'body', 'textarea', { required: true, maxLength: LIMITS.body, value: entry?.body || '', placeholder: 'State the claim, its limits, and any open question.' });
    const outcome = field(form, 'Outcome', 'outcome', 'select', { options: [{ value: 'not_applicable', label: 'Not applicable' }, { value: 'unknown', label: 'Unknown / not tested' }, { value: 'worked', label: 'Worked' }, { value: 'failed', label: 'Failed' }], value: entry?.outcome || 'not_applicable', hint: 'An outcome is a recorded observation. Its evidence must explain the result.' });
    kind.addEventListener('change', () => { if (kind.value === 'lesson' && outcome.value === 'not_applicable') outcome.value = 'unknown'; });
    const status = field(form, 'Record status', 'status', 'select', { options: [{ value: 'proposed', label: 'Proposed · needs review' }, { value: 'documented', label: 'Documented · evidence recorded' }, { value: 'disputed', label: 'Disputed · unresolved claim' }, { value: 'superseded', label: 'Superseded · historical record' }], value: entry?.status || 'proposed' });
    const source = field(form, 'Source reference', 'source', 'textarea', { maxLength: LIMITS.source, value: entry?.source || '', placeholder: 'A document, message, file path, or URL.' });
    const evidence = field(form, 'Evidence', 'evidence', 'textarea', { maxLength: LIMITS.evidence, value: entry?.evidence || '', placeholder: 'What supports the claim or the recorded outcome?' });
    const refs = knowledgeReferences(form, channel, entry);
    return { channel, kind, title, body, outcome, status, source, evidence, ...refs };
  }, entry ? 'Save changes' : 'Save knowledge', async (controls) => {
    const payload = { title: controls.title.value.trim(), body: controls.body.value, kind: controls.kind.value, outcome: controls.outcome.value, status: controls.status.value, source: controls.source.value, evidence: controls.evidence.value, source_refs: [...controls.sourceList.querySelectorAll('input:checked')].map((input) => ({ source_id: input.value, sha256: input.dataset.sha256 })), conflicts_with: [...controls.conflictList.querySelectorAll('input:checked')].map((input) => input.value) };
    if (payload.status === 'documented' && !payload.evidence.trim() && !payload.source_refs.length) throw new Error('A documented record needs evidence or an indexed source citation.');
    if (entry) payload.version = entry.version; else payload.channel_id = controls.channel.value;
    await api(entry ? `/api/knowledge/${encodeURIComponent(entry.id)}/update` : '/api/knowledge', payload);
    await refreshState();
    if (app.view === 'brief' && app.projectTab === 'joining') await refreshBrief();
    notify(`Knowledge ${entry ? 'updated' : 'saved'}. Its evidence and status stay attached to the record.`);
  });
}
function showKnowledgeDetails(entry) {
  if (!entry.body) { showKnowledgeHistory(entry); return; }
  const { dialog, body } = dialogBase('Knowledge record', entry.title); body.append(knowledgeCard(entry)); dialog.showModal();
}
async function showConversationRecord(channelId, messageId) {
  const { dialog, body } = dialogBase('Original message and edits', `Message #${messageId}`);
  body.append(loading('Reading the retained record…')); dialog.showModal();
  try {
    const result = await api(`/api/conversation-record?channel_id=${encodeURIComponent(channelId)}&message_id=${messageId}`);
    if (!dialog.isConnected) return;
    body.replaceChildren(node('h3', '', 'Current version'), formattedMessage(result.message.body));
    body.append(node('h3', '', result.original_recorded ? 'Retained original' : 'Earliest available body'),
      node('p', 'workflow-note', result.original_recorded ? 'The original remains unchanged.' : 'This message predates original-version tracking.'),
      formattedMessage(result.original_body));
    for (const edit of result.edits) {
      const item = node('section', 'history-entry');
      item.append(node('h3', '', `Version ${edit.version_after} · ${formatDateTime(edit.edited_at)}`),
        formattedMessage(edit.body_after)); body.append(item);
    }
    body.append(node('p', 'workflow-note', 'Retrieving this record does not acknowledge it as read.'));
  } catch (error) { if (dialog.isConnected) body.replaceChildren(node('p', 'field-error', errorText(error))); }
}
async function showConversationIndex(channelId) {
  const { dialog, body } = dialogBase('Conversation index', 'Originals stay retained. Review suggestions never approve or delete records.');
  dialog.classList.add('conversation-index-dialog');
  const summary = node('p', 'workflow-note');
  const suggestions = node('section');
  const records = node('section');
  let after = 0;
  let snapshot = null;
  const more = button('Read first page', 'button', async () => {
    more.disabled = true;
    try {
      const query = new URLSearchParams({ channel_id: channelId, after: String(after) });
      if (snapshot) query.set('snapshot', snapshot);
      const result = await api(`/api/conversation-index?${query}`);
      if (!dialog.isConnected) return;
      if (!snapshot) {
        suggestions.append(node('h3', '', 'Review suggestions'));
        for (const suggestion of result.suggestions) {
          const text = suggestion.kind === 'exact_repetition'
            ? `Repeated text in messages ${suggestion.message_ids.map((id) => `#${id}`).join(', ')}. Originals remain retained.`
            : suggestion.kind === 'inactive_task_owner' ? 'An unfinished task has an inactive owner.'
              : 'A knowledge record has stale evidence, a dispute or a conflict.';
          suggestions.append(node('p', '', text));
        }
        if (!result.suggestions.length) suggestions.append(node('p', '', 'No structural review suggestions in this snapshot.'));
      }
      snapshot = result.snapshot;
      after = result.next_after;
      for (const message of result.messages) {
        const item = node('article', 'history-entry conversation-index-record');
        const timestamp = node('time', 'history-meta', formatDateTime(message.created_at));
        timestamp.dateTime = message.created_at;
        item.append(node('h3', '', `#${message.id} · ${message.author_name} · version ${message.version}`),
          timestamp, node('p', 'index-excerpt', message.excerpt), button('Read original and edits', 'text-button',
            () => showConversationRecord(channelId, message.id))); records.append(item);
      }
      const shown = records.children.length;
      const complete = result.coverage.page_reaches_end && shown === result.coverage.total;
      summary.textContent = `${shown} of ${result.coverage.total} messages shown. ${complete ? 'All current messages in this snapshot are covered.' : 'More messages remain.'} Excerpts are navigation aids.`;
      more.textContent = result.coverage.page_reaches_end ? 'All messages shown' : 'Read next page';
      more.disabled = result.coverage.page_reaches_end;
    } catch (error) {
      if (dialog.isConnected) {
        summary.textContent = errorText(error);
        body.append(button('Restart index', 'button', () => { dialog.close(); showConversationIndex(channelId); }));
      }
    }
  });
  const archive = button('Download retained conversation', 'button subtle', async () => {
    archive.disabled = true;
    try {
      const blob = await api(`/api/conversation-archive?channel_id=${encodeURIComponent(channelId)}`, undefined, true);
      const url = URL.createObjectURL(blob);
      const link = node('a'); link.href = url; link.download = 'conversation-history.json.gz'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) { if (dialog.isConnected) summary.textContent = errorText(error); }
    finally { archive.disabled = false; }
  });
  body.append(summary, suggestions, records, more,
    node('p', 'workflow-note', 'The compressed download retains this conversation’s originals, accepted edits, attachments and structured records. It excludes credentials, personal state, other conversations and external files.'), archive);
  dialog.showModal(); more.click();
}
async function showKnowledgeHistory(entry) {
  const { dialog, body } = dialogBase('Knowledge history', entry.title); body.append(loading('Reading the local history…')); dialog.showModal();
  try {
    const result = await api(`/api/knowledge/${encodeURIComponent(entry.id)}/history`); if (!dialog.isConnected) return; body.replaceChildren();
    for (const item of result.history) {
      const snapshot = item.after;
      const record = node('div', 'history-entry'); const meta = node('div', 'history-meta'); meta.append(statusBadge(snapshot.status), node('span', '', person(item.actor_id).name), node('span', '', `Version ${snapshot.version}`), node('time', '', formatDateTime(item.created_at)));
      record.append(meta, node('p', '', snapshot.body));
      for (const [label, value] of [['Evidence', snapshot.evidence], ['Source', snapshot.source], ['Citations', snapshot.source_refs], ['Conflicts', snapshot.conflicts_with]]) if (value && (!Array.isArray(value) || value.length)) record.append(node('p', 'evidence-label', label), node('p', '', plain(value)));
      for (const text of record.querySelectorAll('p')) text.dir = 'auto'; body.append(record);
    }
    if (!result.history.length) body.append(node('p', 'card-body', 'No history is available for this record.'));
  } catch (error) { if (dialog.isConnected) body.replaceChildren(node('p', 'field-error', errorText(error))); }
}
async function showSourceHistory(source) {
  const { dialog, body } = dialogBase('Indexed source history', source.title); body.append(loading('Reading the permitted source snapshots…')); dialog.showModal();
  try {
    const result = await api(`/api/sources/${encodeURIComponent(source.id)}/history`); if (!dialog.isConnected) return; body.replaceChildren();
    for (const item of [...result.history].reverse()) {
      const record = node('div', 'history-entry source-history-entry');
      const meta = node('div', 'history-meta'); meta.append(statusBadge(item.status), node('span', '', `Snapshot ${item.version}`), node('time', '', formatDateTime(item.checked_at || item.created_at))); if (item.actor_id) meta.append(node('span', '', `Indexed by ${person(item.actor_id).name}`)); record.append(meta);
      for (const [label, value] of [['Path', item.path || source.path], ['SHA-256', item.sha256], ['Git revision', item.git_revision], ['Git blob', item.git_blob], ['Source error', item.error], ['Projection warnings', item.projection_warnings], ['Unindexed fields', item.unindexed_fields]]) if (value && (!Array.isArray(value) || value.length)) record.append(node('p', 'evidence-label', label), node('p', 'source-history-metadata', plain(value)));
      if (item.content !== undefined && item.content !== null) { const text = node('pre', 'source-content', plain(item.content)); text.dir = 'auto'; record.append(node('p', 'evidence-label', 'Indexed content'), text); }
      body.append(record);
    }
    if (!result.history.length) body.append(node('p', 'card-body', 'No indexed snapshots are available.'));
  } catch (error) { if (dialog.isConnected) body.replaceChildren(node('p', 'field-error', errorText(error))); }
}
function joiningBriefText() {
  const brief = app.brief;
  if (!brief) throw new Error('Load the joining brief first.');
  const lines = [`# ${workspaceBrand(brief.workspace)} — ${brief.workspace.project_label || brief.workspace.title}`, 'Joining brief', `Built: ${brief.generated_at}`, `Scope: ${app.projectScope ? projectChannelLabel(app.state.channels.find((channel) => channel.id === app.projectScope)) : 'All conversations visible to this account'}`, '', 'Read project context and source dates first. Follow human-approved decisions. Treat proposals, conflicts, and stale claims as open questions. Add sources and outcomes when contributing.', '', 'Existing project documents and Git are the authoritative evidence. Documented knowledge is not human approval. This board does not start or train an AI model.', ''];
  const record = (entry) => {
    lines.push(`### ${entry.title}`, `Status: ${entry.status}${entry.stale ? ' · STALE' : ''}${entry.outcome && entry.kind === 'lesson' ? ` · Outcome: ${entry.outcome}` : ''}`, entry.body || '');
    lines.push(`Updated: ${entry.updated_at || 'No date recorded'} · version ${entry.version || 'Not recorded'}`, `Author: ${person(entry.author_id).name}`);
    if (entry.approved_by) lines.push(`Human approved by: ${person(entry.approved_by).name}`);
    if (entry.implemented_by) lines.push(`Implemented by: ${person(entry.implemented_by).name}`);
    if (entry.verified_by) lines.push(`Independently verified by: ${person(entry.verified_by).name}`);
    if (entry.truncated_fields?.length) lines.push(`SHORTENED FIELDS: ${entry.truncated_fields.join(', ')}. Read the full record before relying on it.`);
    if (entry.source) lines.push(`Source: ${entry.source}`);
    if (entry.evidence) lines.push(`Evidence: ${entry.evidence}`);
    for (const ref of entry.source_refs || []) { const source = brief.sources.find((item) => item.id === ref.source_id); lines.push(`Indexed source: ${source?.title || ref.source_id} · ${source?.path || ''} · SHA-256 ${ref.sha256}`); }
    if (entry.stale_reasons?.length) lines.push(`Stale reasons: ${entry.stale_reasons.join('; ')}`);
    if (entry.conflicts_with?.length) lines.push(`Conflicts with: ${entry.conflicts_with.map((id) => app.state.knowledge.find((item) => item.id === id)?.title || id).join('; ')}`);
    lines.push('');
  };
  for (const [title, records] of [['Project context and working rules', brief.context || []], ['Knowledge', brief.knowledge || []], ['Lessons', brief.lessons || []], ['Disputed claims', brief.disputed || []], ['Stale claims', brief.stale || []], ['Decision register', brief.decisions || []]]) {
    lines.push(`## ${title}`); if (!records.length) lines.push('No records in this scope.'); for (const entry of records) record(entry); lines.push('');
  }
  lines.push('## Open tasks'); for (const task of (brief.tasks || []).filter((item) => item.status !== 'done')) lines.push(`${task.title} · ${task.status} · owner ${person(task.owner_id).name}`, task.body || '', '');
  lines.push('## What the project documents say'); for (const source of brief.source_context || []) { lines.push(`### ${source.title}`, `Source: ${source.path} · ${source.status} · checked ${source.checked_at}`, `SHA-256: ${source.sha256 || 'No snapshot recorded'}`, 'Document headings:', ...(source.headings || [])); if (source.projection !== null && source.projection !== undefined) lines.push('Selected metadata (source claims, not human approval):', plain(source.projection)); if (source.projection_warnings?.length) lines.push('Projection warnings:', ...source.projection_warnings); if (source.unindexed_fields) lines.push(`Excluded fields: ${plain(source.unindexed_fields)}`); if (source.truncated_fields?.length) lines.push(`SHORTENED FIELDS: ${source.truncated_fields.join(', ')}`); lines.push(''); }
  if (Object.keys(brief.truncation || {}).length) lines.push('## Brief limits', truncationText(brief.truncation), '');
  lines.push('## Indexed sources'); for (const source of brief.sources || []) lines.push(`${source.title} · ${source.status}`, `Path: ${source.path}`, `Checked: ${source.checked_at || 'No check recorded'}`, `SHA-256: ${source.sha256 || 'No snapshot recorded'}`, ...(source.error ? [`Error: ${source.error}`] : []), ...(source.projection_warnings?.length ? ['Projection warnings:', ...source.projection_warnings] : []), ...(source.unindexed_fields ? [`Excluded fields: ${plain(source.unindexed_fields)}`] : []), '');
  lines.push('## Source freshness', brief.source_scan ? sourceScanText(brief.source_scan) : 'Source freshness is unknown.', ...(brief.source_scan?.issues || []).map((issue) => `${issue.source_id}: ${issue.status} · ${issue.error || ''}`), '', '## Limits', ...(brief.limitations || []));
  return lines.join('\n');
}
async function copyJoiningBrief() {
  try {
    const text = joiningBriefText();
    try { await navigator.clipboard.writeText(text); notify('Joining brief copied. Its conversation scope is included.'); }
    catch {
      const { dialog, body } = dialogBase('Copy joining brief', 'Select the brief below and copy it with your keyboard. Its conversation scope is included.');
      const textarea = node('textarea', 'copy-brief-text'); textarea.value = text; textarea.readOnly = true; textarea.setAttribute('aria-label', 'Joining brief text'); body.append(textarea); dialog.showModal(); textarea.focus(); textarea.select();
    }
  } catch (error) { notify(errorText(error), true); }
}
function exportJoiningBrief() {
  try {
    const text = joiningBriefText();
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const link = node('a'); link.href = url; link.download = `common-ground-joining-brief-${app.state.me.id}-${new Date().toISOString().slice(0, 10)}.md`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Joining brief exported with its source attribution and status.');
  } catch (error) { notify(errorText(error), true); }
}

async function fetchMessageHistory(channelId, targetId = null) {
  const cache = cacheFor(channelId);
  // Serialize navigation requests so one completion cannot reopen read admission
  // while another request is still loading hidden pages.
  while (cache.historyRequest) await cache.historyRequest;
  cache.historyRequest = (async () => {
    cache.historyLoading = true;
    try {
      for (let page = 0; page < MAX_HISTORY_REQUESTS; page++) {
        if (app.view !== 'conversations' || app.channelId !== channelId) return false;
        if (targetId !== null && cache.messages.some(message => String(message.id) === String(targetId))) return true;
        const cursor = cache.cursor;
        await fetchMessages(channelId);
        if (cache.cursor === cursor) return targetId === null || cache.messages.some(message => String(message.id) === String(targetId));
      }
      notify('History loading reached its request limit. Try again to continue.', true);
      return false;
    } finally { cache.historyLoading = false; }
  })();
  try { return await cache.historyRequest; }
  finally { cache.historyRequest = null; }
}
async function jumpToLatestMessage(channelId) {
  try {
    if (!await fetchMessageHistory(channelId)) return;
    if (app.view !== 'conversations' || app.channelId !== channelId) return;
    cacheFor(channelId).visibleCount = MESSAGE_RENDER_PAGE_SIZE;
    renderMessages(true);
    await markRead(channelId);
    announce('Showing the latest message.');
  } catch (error) { notify(errorText(error), true); }
}
async function openSearchMessage(message) {
  selectChannel(message.channel_id);
  try {
    if (!await fetchMessageHistory(message.channel_id, message.id)) { notify('This message could not be loaded. Try the search again.', true); return; }
    const cache = cacheFor(message.channel_id);
    const index = cache.messages.findIndex(item => String(item.id) === String(message.id));
    cache.visibleCount = Math.max(cache.visibleCount, cache.messages.length - index);
    renderMessages();
    if (app.view !== 'conversations' || app.channelId !== message.channel_id) return;
    const target = [...$('message-list').querySelectorAll('[data-message-id]')].find(item => item.dataset.messageId === String(message.id));
    if (!target) { notify('This message is not loaded. Try the search again.', true); return; }
    target.tabIndex = -1;
    target.classList.add('search-highlight');
    target.scrollIntoView({ block: 'center' });
    target.focus({ preventScroll: true });
    announce(`Showing message number ${message.id}.`);
  } catch (error) { notify(errorText(error), true); }
}
function applyConversationTextSize() {
  document.documentElement.dataset.conversationTextSize = String(conversationTextSize);
  $('conversation-text-size').textContent = `${conversationTextSize} px`;
  $('smaller-text').disabled = conversationTextSize === CONVERSATION_TEXT_SIZES[0];
  $('larger-text').disabled = conversationTextSize === CONVERSATION_TEXT_SIZES.at(-1);
}
function changeConversationTextSize(direction) {
  const next = CONVERSATION_TEXT_SIZES[CONVERSATION_TEXT_SIZES.indexOf(conversationTextSize) + direction];
  if (next === undefined) return;
  conversationTextSize = next;
  applyConversationTextSize();
  try { localStorage.setItem(CONVERSATION_TEXT_SIZE_KEY, String(next)); }
  catch { notify('Text size changed for this tab. Browser storage is unavailable, so it will reset on reload.'); }
}
// @mentions. A mention is the plain-text marker @{participant-id}: the id, so a rename never
// misdirects it. The server lists a message's mentions (current members of that conversation only); the conversation
// draws those markers as chips. In the composer, "@" offers the conversation's other members, and sending turns a
// chosen or typed @Name of such a member into its marker. A mention only shows; it wakes nobody and calls no model.
const MENTION_MARKER = /^@\{([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})\}/;
const MENTION_MARKERS = /@\{([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})\}/g;
const MENTION_QUERY = /(?:^|[^\p{L}\p{N}_@.])@([\p{L}\p{N}_-][\p{L}\p{N}_ -]{0,31})?$/u;
const MENTION_PICKER_LIMIT = 8;
let renderMentions = new Set();
function channelMembers(channelId) {
  return app.state?.channels.find((item) => item.id === channelId)?.members || [];
}
function mentionCandidates(channelId) {
  return channelMembers(channelId).filter((id) => id !== app.state.me.id && person(id).active !== false).map((id) => person(id))
    .sort((a, b) => a.name.localeCompare(b.name));
}
// The server's rule, for a message the server has not listed yet (still sending).
function mentionsIn(body, channelId) {
  const members = new Set(channelMembers(channelId));
  return [...new Set([...String(body).matchAll(MENTION_MARKERS)].map((match) => match[1]).filter((id) => members.has(id)))];
}
function withMentionMarkers(body, channelId) {
  let result = body;
  // longest names first, so "@Codex Reviewer" is not cut short by a member named "Codex"
  for (const member of mentionCandidates(channelId).sort((a, b) => b.name.length - a.name.length)) {
    const name = member.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    result = result.replace(new RegExp(`(^|[^\\p{L}\\p{N}_@.])@${name}(?![\\p{L}\\p{N}_])`, 'giu'), (_, before) => `${before}@{${member.id}}`);
  }
  return result;
}
function closeMentionPicker() {
  app.mentionPicker = null;
  const picker = $('mention-picker'); picker.hidden = true; picker.replaceChildren();
  $('message-input').setAttribute('aria-expanded', 'false'); $('message-input').removeAttribute('aria-activedescendant');
}
function updateMentionPicker() {
  const input = $('message-input');
  if (!app.channelId || input.readOnly || input.selectionStart !== input.selectionEnd) { closeMentionPicker(); return; }
  const match = input.value.slice(0, input.selectionStart).match(MENTION_QUERY);
  const typed = (match?.[1] || '').toLowerCase();
  const choices = match ? mentionCandidates(app.channelId).filter((member) => member.name.toLowerCase().startsWith(typed)).slice(0, MENTION_PICKER_LIMIT) : [];
  if (!choices.length) { closeMentionPicker(); return; }
  app.mentionPicker = { start: input.selectionStart - typed.length - 1, end: input.selectionStart, choices, index: 0 };
  renderMentionPicker();
}
function renderMentionPicker() {
  const state = app.mentionPicker; const picker = $('mention-picker'); const input = $('message-input');
  picker.replaceChildren(...state.choices.map((member, index) => {
    const option = node('button', 'mention-option', member.name); option.type = 'button'; option.id = `mention-option-${index}`;
    option.setAttribute('role', 'option'); option.setAttribute('aria-selected', String(index === state.index));
    // keep the composer focused: the choice is made on mousedown, before a blur
    option.addEventListener('mousedown', (event) => { event.preventDefault(); chooseMention(index); });
    return option;
  }));
  picker.hidden = false;
  input.setAttribute('aria-expanded', 'true'); input.setAttribute('aria-activedescendant', `mention-option-${state.index}`);
}
function chooseMention(index) {
  const state = app.mentionPicker; const input = $('message-input');
  if (!state || composerLocked()) { closeMentionPicker(); return; }
  const text = '@' + state.choices[index].name + ' ';
  if (input.value.length - (state.end - state.start) + text.length > LIMITS.body) { notify('The message is too long to add this mention.', true); return; }
  input.setRangeText(text, state.start, state.end, 'end');
  closeMentionPicker();
  input.dispatchEvent(new Event('input', { bubbles: true }));
  closeMentionPicker();   // the input event reopens it for the text before the cursor; the choice is made
  input.focus();
}
function mentionPickerKey(event) {
  const state = app.mentionPicker;
  if (!state || event.metaKey || event.ctrlKey || event.altKey) return false;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    state.index = (state.index + (event.key === 'ArrowDown' ? 1 : state.choices.length - 1)) % state.choices.length;
    renderMentionPicker(); return true;
  }
  if (event.key === 'Enter' || event.key === 'Tab') { chooseMention(state.index); return true; }
  if (event.key === 'Escape') { closeMentionPicker(); return true; }
  return false;
}
function showEmojiPicker() {
  if (composerLocked()) return;
  const input = $('message-input');
  const channelId = app.channelId;
  const start = input.selectionStart;
  const end = input.selectionEnd;
  const { dialog, body } = dialogBase('Choose an emoji', 'Insert an emoji at the cursor in your unsent message.');
  const choices = node('div', 'emoji-choices');
  for (const [emoji, label] of EMOJI_CHOICES) {
    const choice = button(emoji, 'emoji-choice', () => {
      if (composerLocked(channelId)) { dialog.close(); return; }
      if (input.value.length - (end - start) + emoji.length > LIMITS.body) { notify('The message is too long to insert this emoji.', true); return; }
      input.setRangeText(emoji, start, end, 'end');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      // The general close listener restores its opener first; restore the
      // composer afterwards so typing continues at the inserted emoji.
      dialog.addEventListener('close', () => input.focus(), { once: true });
      dialog.close();
    });
    choice.setAttribute('aria-label', label);
    choices.append(choice);
  }
  body.append(choices);
  dialog.showModal();
}
// Text formatting in the composer. Messages stay plain text: each style is a marker pair that agents read as written
// and appendInline draws. Italic uses _ rather than *, so bold and italic nest ("**_word_**") without a "***" run.
const FORMAT_MARKERS = { bold: '**', italic: '_', underline: '++', strikethrough: '~~' };
const FORMAT_SHORTCUTS = { b: 'bold', i: 'italic', u: 'underline' };
function applyFormat(kind) {
  if (composerLocked()) return;
  const input = $('message-input');
  const marker = FORMAT_MARKERS[kind];
  let start = input.selectionStart;
  let end = input.selectionEnd;
  // Spaces at the selection's edges stay outside the markers: "** word**" would not draw.
  while (start < end && /\s/.test(input.value[start])) start++;
  while (end > start && /\s/.test(input.value[end - 1])) end--;
  if (input.value.length + 2 * marker.length > LIMITS.body) { notify('The message is too long to add formatting.', true); return; }
  input.setRangeText(marker + input.value.slice(start, end) + marker, start, end);
  // Keep the same words selected (or the cursor between empty markers) so styles can be stacked or typed into.
  input.setSelectionRange(start + marker.length, end + marker.length);
  input.focus();
  input.dispatchEvent(new Event('input', { bubbles: true }));
}
function showClearDraft() {
  if (composerLocked()) return;
  const channelId = app.channelId;
  const { dialog, body } = dialogBase('Clear unsent draft?', 'Clear only this conversation’s unsent draft. Delivered messages and messages waiting for a retry stay unchanged.');
  const actions = node('div', 'dialog-actions');
  const cancel = button('Keep draft', 'button subtle', () => dialog.close());
  const clear = button('Clear draft', 'button primary', async () => {
    if (composerLocked(channelId)) { dialog.close(); return; }
    // Use the existing versioned draft queue, including any preceding save.
    app.drafts.set(channelId, '');
    app.unsavedDrafts.add(channelId);
    if (app.channelId === channelId) { $('message-input').value = ''; resizeComposer(); }
    dialog.close();
    try { await persistDraft(channelId); announce('Unsent draft cleared and saved.'); }
    catch (error) {
      notify(`The empty draft is not saved yet. ${errorText(error)} Keep this tab open.`, true);
      $('notice').dataset.personalSaveError = 'true';
    }
  });
  actions.append(cancel, clear);
  body.append(actions);
  dialog.showModal();
  cancel.focus();
}
function scheduleSearch() {
  clearTimeout(app.searchTimer);
  const query = $('search-input').value.trim();
  if (!query) { if (app.view === 'search') setView('conversations'); return; }
  app.searchTimer = setTimeout(() => runSearch(query), SEARCH_DELAY_MS);
}
async function runSearch(query) {
  rememberDraft();
  resetVoicePlayers();
  const sequence = ++app.searchSequence;
  app.view = 'search';
  renderSidebar();
  updateNavigation();
  $('composer-area').hidden = true;
  setHeader('FIND THE CONTEXT', 'Search results', `Searching for “${query}”`);
  $('view-content').replaceChildren(loading('Searching the local store…'));
  try {
    const result = await api(`/api/search?q=${encodeURIComponent(query)}`);
    if (sequence !== app.searchSequence || app.view !== 'search' || $('search-input').value.trim() !== query) return;
    const messages = result.messages || [];
    const decisions = result.decisions || [];
    const tasks = result.tasks || [];
    const knowledge = result.knowledge || [];
    const sources = result.sources || [];
    const count = messages.length + decisions.length + tasks.length + knowledge.length + sources.length;
    setHeader('FIND THE CONTEXT', 'Search results', `${count} result${count === 1 ? '' : 's'} for “${query}”`);
    const content = node('div', 'search-results');
    if (!count) content.append(emptyState('⌕', 'No matches yet.', 'Try another name, phrase, or task title. Search only includes conversations you can access.'));
    if (messages.length) {
      content.append(node('h2', '', `Messages · ${messages.length}`));
      for (const message of messages) {
        const item = button('', 'search-message', () => openSearchMessage(message));
        const meta = node('div', 'search-result-meta');
        meta.append(node('span', '', `${message.author_name || person(message.author_id).name} · ${channelName(message.channel_id)}`), node('time', '', formatDateTime(message.created_at)));
        const body = node('p', 'card-body', message.body);
        body.dir = 'auto';
        item.append(meta, body);
        content.append(item);
      }
    }
    for (const [name, records, renderer] of [['Knowledge', knowledge, knowledgeCard], ['Sources', sources, sourceCard], ['Decisions', decisions, decisionCard], ['Tasks', tasks, taskCard]]) {
      if (!records.length) continue;
      content.append(node('h2', '', `${name} · ${records.length}`));
      const grid = node('div', 'card-grid');
      for (const record of records) grid.append(renderer(record));
      content.append(grid);
    }
    $('view-content').replaceChildren(content);
  } catch (error) {
    if (sequence === app.searchSequence && app.view === 'search') $('view-content').replaceChildren(emptyState('⌕', 'Search could not finish.', errorText(error), 'Try again', () => runSearch(query)));
  }
}
async function exportData() {
  const control = $('export-button');
  control.disabled = true;
  try {
    const data = await api('/api/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const link = node('a');
    link.href = url;
    link.download = `common-ground-${String(app.state.me.id).replace(/[^A-Za-z0-9_-]/g, '_')}-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Exported the records visible to your account.');
  } catch (error) { notify(errorText(error), true); }
  finally { control.disabled = false; }
}


// Provider jobs use server records only. Reload and polling never invoke a run.
const ADAPTER_RECENT_LIMIT = 20;
const MICRO_USD_PER_USD = 1000000;
app.adapters = { descriptors: null, moduleId: '', channelId: '', prompt: '', importText: '', row: null, runs: [], next: null, error: '', busy: false, signature: '' };
function resetAdapters() {
  app.adapters = { descriptors: null, moduleId: '', channelId: '', prompt: '', importText: '', row: null, runs: [], next: null, error: '', busy: false, signature: '' };
}
function selectedAdapter() { return app.adapters.descriptors?.modules.find((module) => module.id === app.adapters.moduleId); }
async function refreshAdapters(older = false) {
  const generation = app.personalGeneration;
  const descriptors = await api('/api/adapters');
  if (!app.authenticated || generation !== app.personalGeneration) return;
  app.adapters.descriptors = descriptors;
  if (!descriptors.modules.some((module) => module.id === app.adapters.moduleId)) {
    app.adapters.moduleId = descriptors.modules[0]?.id || ''; app.adapters.row = null;
  }
  const module = selectedAdapter();
  if (module && !module.allowed_channel_ids.includes(app.adapters.channelId)) app.adapters.channelId = module.allowed_channel_ids[0] || '';
  if (module) {
    const selectedChannel = app.adapters.channelId;
    const cursor = older && app.adapters.next ? '&before=' + encodeURIComponent(app.adapters.next) : '';
    const scope = app.adapters.channelId ? '&channel_id=' + encodeURIComponent(app.adapters.channelId) : '';
    const recent = await api('/api/adapters/runs?module_id=' + encodeURIComponent(module.id) + '&limit=' + ADAPTER_RECENT_LIMIT + scope + cursor);
    if (!app.authenticated || generation !== app.personalGeneration || module.id !== app.adapters.moduleId || selectedChannel !== app.adapters.channelId) return;
    app.adapters.runs = older ? [...app.adapters.runs, ...recent.runs.filter((row) => !app.adapters.runs.some((old) => old.run_id === row.run_id))] : recent.runs;
    app.adapters.next = recent.next_before;
    if (app.adapters.row) {
      const row = await api('/api/adapters/runs/' + encodeURIComponent(app.adapters.row.run_id) + '?module_id=' + encodeURIComponent(module.id));
      if (!app.authenticated || generation !== app.personalGeneration || module.id !== app.adapters.moduleId || selectedChannel !== app.adapters.channelId) return;
      app.adapters.row = row;
    }
  } else { app.adapters.runs = []; app.adapters.row = null; app.adapters.next = null; }
  const signature = JSON.stringify({ descriptors, runs: app.adapters.runs, row: app.adapters.row });
  if (signature !== app.adapters.signature) {
    app.adapters.signature = signature;
    if (app.view === 'providers') renderAdapterView();
  }
}
function adapterActionBody() {
  const row = app.adapters.row;
  return { module_id: row.module_id, run_id: row.run_id, prepared_sha256: row.prepared_sha256 };
}
async function adapterMutation(path, data) {
  if (app.adapters.busy) return;
  const generation = app.personalGeneration;
  app.adapters.busy = true; app.adapters.error = ''; renderAdapterView();
  try {
    const row = await api(path, data);
    if (!app.authenticated || generation !== app.personalGeneration) return;
    app.adapters.row = row; app.adapters.prompt = '';
    await refreshAdapters();
  } catch (error) {
    if (generation === app.personalGeneration) app.adapters.error = errorText(error) + ' Review recent runs before repeating any action.';
  } finally {
    if (generation === app.personalGeneration) { app.adapters.busy = false; if (app.view === 'providers') renderAdapterView(); }
  }
}
async function openAdapterRun(summary) {
  const generation = app.personalGeneration;
  try {
    const row = await api('/api/adapters/runs/' + encodeURIComponent(summary.run_id) + '?module_id=' + encodeURIComponent(summary.module_id));
    if (!app.authenticated || generation !== app.personalGeneration) return;
    if (app.adapters.row?.run_id !== row.run_id) app.adapters.importText = '';
    app.adapters.row = row; app.adapters.error = ''; renderAdapterView();
  } catch (error) { if (generation === app.personalGeneration) { app.adapters.error = errorText(error); renderAdapterView(); } }
}
function adapterDetails(label, value, open = false) {
  const details = node('details', 'adapter-details'); details.open = open;
  details.append(node('summary', '', label), node('pre', 'adapter-json', plain(value)));
  return details;
}
function renderAdapterView() {
  if (!app.authenticated || app.view !== 'providers') return;
  setHeader('EXPLICIT PROVIDER JOBS', 'Provider workspace', 'Prepare a preview. Run once. Review the result before publishing.');
  $('composer-area').hidden = true;
  const board = node('div', 'project-board adapter-board');
  const state = app.adapters;
  board.append(node('p', 'workflow-note', 'Manual conversations stay available. Reading, polling and restarting never start generation. Raw prompt typing is not saved; Prepare acknowledges a durable request.'));
  if (state.error) { const error = node('p', 'field-error', state.error); error.id = 'adapter-error'; error.setAttribute('role', 'alert'); board.append(error); }
  if (!state.descriptors) { board.append(loading('Reading available modules…')); $('view-content').replaceChildren(board); return; }
  if (!state.descriptors.configured || !state.descriptors.modules.length) {
    board.append(emptyState('◎', 'Provider controls are disabled.', state.descriptors.configured ? 'No module is authorized for this account. Human launching needs an explicit configured grant.' : 'No provider configuration is loaded. Models, export permissions and limits must be chosen before activation.'));
    $('view-content').replaceChildren(board); return;
  }
  const selectors = node('div', 'adapter-selectors');
  const moduleLabel = node('label', '', 'Module');
  const picker = node('select'); picker.id = 'adapter-module';
  for (const module of state.descriptors.modules) { const option = node('option', '', module.id + ' · ' + module.provider + (module.blocked_reasons.length ? ' · blocked' : '')); option.value = module.id; picker.append(option); }
  picker.value = state.moduleId; picker.disabled = state.busy;
  picker.addEventListener('change', () => { state.moduleId = picker.value; state.row = null; state.runs = []; state.next = null; state.signature = ''; refreshAdapters().catch((error) => { state.error = errorText(error); renderAdapterView(); }); });
  moduleLabel.append(picker); selectors.append(moduleLabel);
  const module = selectedAdapter();
  const channelLabel = node('label', '', 'Conversation audience');
  const channels = node('select'); channels.id = 'adapter-channel';
  for (const id of module.allowed_channel_ids) { const option = node('option', '', channelName(id)); option.value = id; channels.append(option); }
  channels.value = state.channelId; channels.disabled = state.busy;
  channels.addEventListener('change', () => { state.channelId = channels.value; state.row = null; state.signature = ''; refreshAdapters().catch((error) => { state.error = errorText(error); renderAdapterView(); }); });
  channelLabel.append(channels); selectors.append(channelLabel); board.append(selectors);
  board.append(node('p', 'adapter-route', 'Identity: ' + person(module.participant_id).name + ' · ' + module.provider + ' · Model: ' + (module.model || 'not configured / manual') + ' · Endpoint: ' + (module.endpoint || 'no provider endpoint')));
  if (module.human_launch) board.append(node('p', 'workflow-note', 'Your explicit grant operates this registered agent. Both of you must have access to the conversation.'));
  for (const reason of module.blocked_reasons) board.append(node('p', 'field-error adapter-blocked', reason));
  const promptLabel = node('label', 'adapter-prompt-label', 'Request to prepare');
  const prompt = node('textarea'); prompt.id = 'adapter-prompt'; prompt.rows = 4; prompt.maxLength = LIMITS.body; prompt.value = state.prompt; prompt.disabled = state.busy;
  prompt.addEventListener('input', () => { state.prompt = prompt.value; });
  promptLabel.append(prompt); board.append(promptLabel);
  const prepare = button('Prepare preview', 'button primary', () => adapterMutation('/api/adapters/prepare', { module_id: state.moduleId, channel_id: state.channelId, prompt: state.prompt }));
  prepare.id = 'adapter-prepare'; prepare.disabled = state.busy || !!module.blocked_reasons.length || !state.channelId; board.append(prepare);
  if (state.row) {
    const row = state.row; const panel = node('section', 'adapter-preview'); panel.id = 'adapter-preview';
    const top = node('div', 'card-topline'); top.append(node('h2', '', 'Prepared request & result'), statusBadge(row.status)); panel.append(top);
    panel.append(node('p', 'workflow-note', 'Run ' + row.run_id + ' · ' + channelName(row.channel_id) + ' · ' + formatDateTime(row.updated_at)));
    panel.append(node('p', 'adapter-hash', 'Prepared SHA256: ' + row.prepared_sha256));
    panel.append(node('p', 'adapter-quote', 'Maximum configured reservation: $' + (row.prepared.reserved_quote_microusd / MICRO_USD_PER_USD).toFixed(6) + ' USD. Actual and unknown external account costs may differ.'));
    panel.append(adapterDetails('Exact outbound request and exported evidence', row.prepared, row.status === 'prepared'));
    if (row.operation?.error) panel.append(node('p', 'field-error', row.operation.error));
    const active = state.busy || row.operation?.active || ['running', 'publishing'].includes(row.status) || row.publication_status === 'publishing';
    if (row.operation?.active) panel.append(node('p', 'workflow-note', 'Operation in progress. Reloading keeps its durable record.'));
    if (row.status === 'prepared' && !row.operation?.error) {
      const run = button(module.provider === 'manual' ? 'Open manual handoff' : 'Run once', 'button primary', () => adapterMutation('/api/adapters/run', adapterActionBody())); run.id = 'adapter-run'; run.disabled = active || !!module.blocked_reasons.length; panel.append(run);
    }
    if (row.status === 'unknown') panel.append(node('p', 'field-error', 'Generation outcome is unknown. Do not retry this call. Review the request and held budget.'));
    if (row.status === 'awaiting_manual') {
      const label = node('label', '', 'Paste the manually obtained text result · unsaved until Import');
      const text = node('textarea'); text.id = 'adapter-import-text'; text.rows = 5; text.maxLength = LIMITS.body; text.disabled = active; text.value = state.importText;
      text.addEventListener('input', () => { state.importText = text.value; });
      label.append(text); panel.append(label);
      const importButton = button('Import manual result', 'button primary', () => adapterMutation('/api/adapters/import-result', { ...adapterActionBody(), text: text.value }));
      importButton.id = 'adapter-import'; importButton.disabled = active; panel.append(importButton);
    }
    if (row.result) panel.append(node('h3', '', 'Provider draft'), node('pre', 'adapter-result', row.result.text || '(No completed text result)'), adapterDetails('Usage, warning and result status', row.result));
    panel.append(node('p', 'workflow-note', 'Publication: ' + row.publication_status + '. Results are evidence, not approval.'));
    if (row.status === 'completed' && row.publication_status !== 'published') {
      const publish = button(row.publication_status === 'unconfirmed' ? 'Confirm delivery · same result and ID' : 'Publish reviewed result', 'button primary', () => adapterMutation('/api/adapters/publish', adapterActionBody()));
      publish.id = 'adapter-publish'; publish.disabled = active; panel.append(publish);
    }
    board.append(panel);
  }
  const recent = node('section', 'adapter-recent'); recent.append(node('h2', '', 'Recent durable runs'), node('p', 'workflow-note', 'Only runs available to this account in this conversation. Open a record after reload or a server port change.'));
  for (const row of state.runs) {
    const item = button(formatDateTime(row.created_at) + ' · ' + row.status + ' · ' + row.publication_status + ' · ' + row.run_id.slice(0, 8), 'button subtle adapter-recent-run', () => openAdapterRun(row)); item.dataset.runId = row.run_id; item.disabled = state.busy; recent.append(item);
  }
  if (!state.runs.length) recent.append(node('p', '', 'No prepared requests in this conversation.'));
  if (state.next) recent.append(button('Older runs', 'button subtle', () => refreshAdapters(true).catch((error) => { state.error = errorText(error); renderAdapterView(); })));
  board.append(recent); $('view-content').replaceChildren(board);
}

// Backups: the human's view of private database copies. Status is read from the server every poll; a backup
// starts only from the explicit button or the server's own schedule.
const BYTES_PER_KB = 1024;
function resetBackups() { app.backups = { status: null, interval: null, error: '', busy: false, signature: '' }; }
resetBackups();
function bytesText(bytes) {
  if (bytes < BYTES_PER_KB) return bytes + ' B';
  if (bytes < BYTES_PER_KB * BYTES_PER_KB) return (bytes / BYTES_PER_KB).toFixed(1) + ' KB';
  return (bytes / BYTES_PER_KB / BYTES_PER_KB).toFixed(1) + ' MB';
}
async function refreshBackups() {
  const generation = app.personalGeneration;
  const status = await api('/api/backups');
  if (!app.authenticated || generation !== app.personalGeneration) return;
  app.backups.status = status;
  const signature = JSON.stringify(status);
  if (signature !== app.backups.signature) { app.backups.signature = signature; if (app.view === 'backups') renderBackupView(); }
}
async function backupMutation(path, data) {
  if (app.backups.busy) return;
  const generation = app.personalGeneration;
  app.backups.busy = true; app.backups.error = ''; renderBackupView();
  try {
    const status = await api(path, data);
    if (!app.authenticated || generation !== app.personalGeneration) return;
    app.backups.status = status; app.backups.interval = null;
    notify(path === '/api/backups/run' ? 'Backup made and checked.' : 'Backup interval saved.');
  } catch (error) {
    if (generation !== app.personalGeneration) return;
    app.backups.error = errorText(error);
    // A failed attempt is recorded on the server; show the recorded status too.
    await refreshBackups().catch((reloadError) => { app.backups.error += ' The status could not be reloaded: ' + errorText(reloadError); });
  } finally {
    if (generation === app.personalGeneration) { app.backups.busy = false; app.backups.signature = ''; if (app.view === 'backups') renderBackupView(); }
  }
}
function backupRunText(run) {
  return formatDateTime(run.finished_at || run.started_at) + ' · ' + (run.trigger === 'manual' ? 'manual' : 'scheduled');
}
function renderBackupView() {
  if (!app.authenticated || app.view !== 'backups') return;
  setHeader('PRIVATE COPIES ON THIS MAC', 'Backups', 'Consistent copies of this workspace’s database, kept privately on this computer. Old copies are never deleted.');
  $('composer-area').hidden = true;
  const board = node('div', 'project-board backup-board');
  const state = app.backups;
  if (state.error) { const error = node('p', 'field-error', state.error); error.id = 'backup-error'; error.setAttribute('role', 'alert'); board.append(error); }
  const status = state.status;
  if (!status) { board.append(loading('Reading backup status…')); $('view-content').replaceChildren(board); return; }
  const summary = node('section', 'backup-summary'); summary.id = 'backup-summary';
  summary.append(node('h2', '', 'Status'));
  if (status.folder_error) { const problem = node('p', 'field-error backup-folder-error', 'The backup folder cannot be used: ' + status.folder_error); problem.setAttribute('role', 'alert'); summary.append(problem); }
  const success = status.last_success;
  summary.append(node('p', 'backup-last-success', success ? 'Last successful backup: ' + backupRunText(success) : 'No successful backup yet.'));
  const failure = status.last_error;
  if (failure) {
    const current = !success || failure.id > success.id;
    const text = node('p', current ? 'field-error backup-last-error' : 'workflow-note backup-last-error', (current ? 'Last attempt failed: ' : 'Earlier failed attempt: ') + backupRunText(failure) + ' · ' + failure.error);
    if (current) text.setAttribute('role', 'alert');
    summary.append(text);
  }
  if (status.running) summary.append(node('p', 'workflow-note', 'A backup is being made now.'));
  summary.append(node('p', 'backup-next', status.next_due_at ? 'Next scheduled backup: ' + formatDateTime(status.next_due_at) : 'A backup is due now; the app makes it within a minute.'));
  if (status.retry_not_before) summary.append(node('p', 'workflow-note', 'After a failed scheduled attempt, the next try waits until ' + formatDateTime(status.retry_not_before) + '.'));
  if (status.interrupted_runs.length) summary.append(node('p', 'field-error backup-interrupted', status.interrupted_runs.length + ' attempt(s) started but never finished (for example, the app stopped during a backup).'));
  if (status.missing_from_disk.length) summary.append(node('p', 'field-error backup-missing', 'Recorded but no longer on disk: ' + status.missing_from_disk.map((run) => run.file_name).join(', ')));
  if (status.incomplete_files.length) summary.append(node('p', 'workflow-note backup-incomplete', 'Incomplete files in the folder (kept, not backups): ' + status.incomplete_files.map((item) => item.name + ' (' + item.reason + ')').join(', ')));
  board.append(summary);
  const settings = node('section', 'backup-settings');
  settings.append(node('h2', '', 'Schedule'));
  const label = node('label', 'backup-interval-label', 'Back up every');
  const input = node('input'); input.id = 'backup-interval'; input.type = 'number'; input.inputMode = 'numeric'; input.step = '1';
  input.min = String(status.settings.min_interval_days); input.max = String(status.settings.max_interval_days);
  input.value = state.interval ?? String(status.settings.interval_days); input.disabled = state.busy;
  input.addEventListener('input', () => { state.interval = input.value; });
  label.append(input, node('span', '', 'days'));
  const save = button('Save interval', 'button subtle', () => {
    const days = Number(input.value);
    if (!Number.isInteger(days) || days < status.settings.min_interval_days || days > status.settings.max_interval_days) { state.error = 'Enter whole days from ' + status.settings.min_interval_days + ' to ' + status.settings.max_interval_days + '.'; renderBackupView(); return; }
    backupMutation('/api/backups/settings', { interval_days: days, version: status.settings.version });
  });
  save.id = 'backup-save'; save.disabled = state.busy;
  const run = button('Back up now', 'button primary', () => backupMutation('/api/backups/run', {}));
  run.id = 'backup-run'; run.disabled = state.busy || status.running;
  const actions = node('div', 'backup-actions'); actions.append(label, save, run);
  settings.append(actions, node('p', 'workflow-note', 'Default: every ' + status.settings.default_interval_days + ' days. Folder: ' + status.folder));
  settings.append(node('p', 'workflow-note', 'To restore: stop the app, run “python -B backups.py restore <backup file> <new file>”, then put the new file in place of collaboration.sqlite3. Restore never overwrites an existing file.'));
  board.append(settings);
  const list = node('section', 'backup-list'); list.id = 'backup-list';
  list.append(node('h2', '', 'Backups on disk (' + status.backups.length + ')'));
  for (const item of status.backups) list.append(node('p', 'backup-item', formatDateTime(item.created_at) + ' · ' + bytesText(item.bytes) + ' · SHA256 ' + item.sha256.slice(0, 12) + '… · ' + item.name));
  if (!status.backups.length) list.append(node('p', '', 'No complete backup on disk.'));
  board.append(list);
  $('view-content').replaceChildren(board);
}

function resetRules() { app.rules = { data: null, draft: null, base: 0, error: '', busy: false }; }
resetRules();
async function refreshRules() {
  const generation = app.personalGeneration;
  const data = await api('/api/rules');
  if (!app.authenticated || generation !== app.personalGeneration) return;
  app.rules.data = data;
  if (app.view === 'rules') renderRulesView();
}
async function saveRules() {
  const state = app.rules;
  if (state.busy) return;
  const generation = app.personalGeneration;
  state.busy = true; state.error = ''; renderRulesView();
  try {
    // base is the version the edit started from: if someone saved meanwhile, the server refuses this save.
    const data = await api('/api/rules', { body: state.draft, base_version: state.base });
    if (!app.authenticated || generation !== app.personalGeneration) return;
    state.data = data; state.draft = null;
    notify('Rules saved as version ' + data.current.version + '.');
  } catch (error) {
    if (generation !== app.personalGeneration) return;
    state.error = errorText(error);   // the typed text stays in the editor
  } finally {
    if (generation === app.personalGeneration) { state.busy = false; if (app.view === 'rules') renderRulesView(); }
  }
}
function rulesText(body) {
  try { return formattedMessage(body); }
  catch (error) {
    // Same isolation as a message: a formatting failure shows the original text, never a blank panel.
    const fallback = node('div', 'message-bubble formatting-fallback', body); fallback.dir = 'auto';
    const note = node('p', 'formatting-error', 'Formatting error · original text shown.'); note.setAttribute('role', 'note'); note.title = errorText(error);
    const result = node('div'); result.append(fallback, note); return result;
  }
}
function renderRulesView() {
  if (!app.authenticated || app.view !== 'rules') return;
  const state = app.rules;
  const human = app.state.me.kind === 'human';
  setHeader('SHARED BY EVERYONE', 'Rules', human ? 'The rules every participant works by. People edit them; every saved version is kept.' : 'The rules every participant works by. People edit them; agents read them.');
  $('composer-area').hidden = true;
  const board = node('div', 'project-board rules-board');
  if (state.error) { const error = node('p', 'field-error', state.error); error.id = 'rules-error'; error.setAttribute('role', 'alert'); board.append(error); }
  if (!state.data) { board.append(loading('Reading the Rules…')); $('view-content').replaceChildren(board); return; }
  const current = state.data.current;
  if (state.draft !== null) {
    const editor = node('section', 'rules-editor');
    const input = node('textarea', 'rules-input'); input.id = 'rules-input'; input.value = state.draft; input.maxLength = state.data.max_chars;
    input.disabled = state.busy; input.setAttribute('aria-label', 'Rules text');
    input.addEventListener('input', () => { state.draft = input.value; });
    const save = button('Save Rules', 'button primary', saveRules); save.id = 'rules-save'; save.disabled = state.busy;
    const cancel = button('Cancel', 'button subtle', () => { state.draft = null; state.error = ''; renderRulesView(); }); cancel.id = 'rules-cancel'; cancel.disabled = state.busy;
    const actions = node('div', 'rules-actions'); actions.append(save, cancel);
    editor.append(input, actions, node('p', 'workflow-note', 'Saving creates version ' + (state.base + 1) + '; earlier versions stay in the history. If someone saves first, your save is refused and your text stays here.'));
    board.append(editor);
  } else {
    const section = node('section', 'rules-current'); section.id = 'rules-current';
    if (current) {
      section.append(node('p', 'rules-meta', 'Version ' + current.version + ' · saved by ' + current.author_name + ' · ' + formatDateTime(current.created_at)));
      const text = node('div', 'rules-text'); text.append(rulesText(current.body)); section.append(text);
    } else section.append(emptyState('§', 'No rules saved yet', human ? 'Write the first version; everyone will see it here.' : 'A person writes the first version; you will see it here.'));
    if (human) {
      const edit = button(current ? 'Edit Rules' : 'Write the Rules', 'button primary', () => {
        state.draft = current ? current.body : ''; state.base = current ? current.version : 0; state.error = ''; renderRulesView(); $('rules-input').focus();
      });
      edit.id = 'rules-edit'; section.append(edit);
    }
    board.append(section);
  }
  if (state.data.history.length) {
    const history = node('section', 'rules-history'); history.id = 'rules-history';
    history.append(node('h2', '', 'History (' + state.data.history.length + ')'));
    for (const item of state.data.history) history.append(node('p', 'rules-history-item', 'Version ' + item.version + ' · ' + item.author_name + ' · ' + formatDateTime(item.created_at)));
    board.append(history);
  }
  $('view-content').replaceChildren(board);
}

function showImage(url, label) {
  const { dialog, body } = dialogBase(label || 'Image attachment', 'Stored locally; visible only to members of this conversation.');
  dialog.classList.add('image-dialog');
  const image = node('img', 'full-image'); image.src = url; image.alt = label || 'Attached image';
  const download = node('a', 'button compact', 'Download image'); download.href = url; download.download = label || 'image';
  body.append(image, download); dialog.showModal();
}
async function copyMessage(message) {
  try { await navigator.clipboard.writeText(message.body); notify('Message copied.'); }
  catch {
    const { dialog, body } = dialogBase('Copy message', 'Select the text and copy it with your keyboard.');
    const input = node('textarea', 'copy-brief-text'); input.value = message.body; input.readOnly = true;
    body.append(input); dialog.showModal(); input.focus(); input.select();
  }
}
function quoteMessage(message) {
  if (composerLocked()) { notify('Wait for the current message or image to finish.', true); return; }
  const quoted = '> ' + message.author_name + ' · #' + message.id + '\n' + message.body.split('\n').map(line => '> ' + line).join('\n') + '\n\n';
  const input = $('message-input'); const next = input.value ? input.value + '\n\n' + quoted : quoted;
  if (next.length > LIMITS.body) { notify('This quote would exceed the message limit. Copy a shorter excerpt instead.', true); return; }
  input.value = next; input.dispatchEvent(new Event('input')); input.focus(); resizeComposer();
}
async function showSavedMessages() {
  const { dialog, body } = dialogBase('Saved messages', 'Private to your account. Open a message to return to its conversation.');
  dialog.showModal();
  try {
    const result = await api('/api/bookmarks');
    app.bookmarks = new Set(result.messages.map(message => message.id));
    if (!result.messages.length) body.append(node('p', '', 'No saved messages yet. Use Save beside a message.'));
    for (const message of result.messages) {
      const item = button('#' + message.id + ' · ' + message.author_name + '\n' + message.body, 'saved-message-item', () => { dialog.close(); openSearchMessage(message); });
      body.append(item);
    }
  } catch (error) { body.append(node('p', 'form-error', errorText(error))); }
}
const ATTACHMENT_MAX_BYTES = 8 * 1024 * 1024;   // the server's attachment limit (attachments.MAX_IMAGE_BYTES)
function fileData(file, failure) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error(failure));
    reader.readAsDataURL(file);
  });
}
// Append an uploaded attachment's markup to the draft of the conversation it was made for, through the draft save.
async function addToDraft(channel, markup, what) {
  const previous = app.drafts.get(channel) || '';
  if ((previous + '\n' + markup).length > LIMITS.body) { notify(`${what} uploaded, but the draft is too long to add it.`, true); return; }
  const next = previous ? previous + '\n' + markup : markup;
  app.drafts.set(channel, next); app.unsavedDrafts.add(channel);
  if (app.channelId === channel) { $('message-input').value = next; $('message-input').dispatchEvent(new Event('input')); }
  await persistDraft(channel);
  notify(`${what} attached to your draft. Send when ready.`);
}
async function attachImage(file) {
  if (!file || composerLocked()) return;
  const channel = app.channelId, generation = app.personalGeneration;
  if (file.size > ATTACHMENT_MAX_BYTES || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { notify('Choose PNG, JPEG, or WebP of at most 8 MB.', true); return; }
  app.uploadingImage = true; resizeComposer();
  try {
    const encoded = await fileData(file, 'Could not read this image.');
    const result = await api('/api/images', { channel_id: channel, client_id: newClientId(), name: file.name || 'Pasted image', data: encoded });
    if (!app.authenticated || generation !== app.personalGeneration) return;
    const label = result.name.replace(/[\\[\]\r\n]/g, ' ');
    await addToDraft(channel, '![' + label + '](' + result.url + ')', 'Image');
  } catch (error) { notify(errorText(error), true); }
  finally { app.uploadingImage = false; $('image-file').value = ''; resizeComposer(); }
}
// Dictation: the microphone records a memo in this browser, the server's local speech program turns it into text, and
// the text joins the draft for the human to check and send. The sound is never stored or posted; voice messages sent
// earlier still play in place.
const VOICE_TYPES = ['audio/webm', 'audio/ogg', 'audio/mp4'];
const DICTATION_SAMPLE_RATE = 16000;   // the only rate the server accepts: its speech program's input rate
function renderVoiceButton() {
  const control = $('record-voice');
  const recording = Boolean(app.voice?.recorder);
  control.classList.toggle('recording', recording);
  control.setAttribute('aria-label', recording ? 'Stop recording' : app.uploadingVoice ? 'Turning speech into text' : 'Dictate a message');
  control.title = app.voice && !recording ? 'Waiting for microphone permission' : recording ? 'Stop and turn the speech into text'
    : app.uploadingVoice ? 'Turning speech into text' : 'Dictate: speak, then check the text before sending';
  resizeComposer();
}
function releaseVoiceStream(session) {
  clearTimeout(session.limit);
  if (session.stream && !session.released) {
    session.released = true;
    for (const track of session.stream.getTracks()) track.stop();
  }
}
function clearVoiceSession(session) {
  releaseVoiceStream(session);
  if (app.voice === session) { app.voice = null; renderVoiceButton(); }
}
function cancelVoiceSession(session) {
  if (!session) return;
  session.cancelled = true;
  if (session.recorder?.state === 'recording' && !session.stopping) {
    session.stopping = true;
    try { session.recorder.stop(); }
    catch (error) { notify(errorText(error), true); }
  }
  clearVoiceSession(session);
}
function cancelVoiceRecording() { cancelVoiceSession(app.voice); }
async function toggleVoiceRecording() {
  if (app.voice) {
    const session = app.voice;
    if (session.recorder?.state === 'recording' && !session.stopping) {
      session.stopping = true;
      try { session.recorder.stop(); }
      catch (error) { cancelVoiceRecording(); notify(errorText(error), true); }
    }
    return;
  }
  const limit = app.state?.capabilities?.dictation?.max_seconds;
  if (!app.authenticated || composerLocked() || !limit) return;
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') { notify('This browser cannot record audio.', true); return; }
  // Reserve ownership before permission can resolve. The asynchronous result cannot acquire a new account's session.
  const session = { channel: app.channelId, generation: app.personalGeneration, stream: null, recorder: null, cancelled: false, stopping: false, released: false, limit: null };
  app.voice = session; renderVoiceButton();
  try {
    session.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (app.voice !== session || session.cancelled || !app.authenticated || session.generation !== app.personalGeneration) { clearVoiceSession(session); return; }
    const type = VOICE_TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate));
    const recorder = new MediaRecorder(session.stream, type ? { mimeType: type } : undefined);
    const chunks = [];
    session.recorder = recorder;
    recorder.addEventListener('dataavailable', (event) => { if (event.data.size && !session.cancelled) chunks.push(event.data); });
    recorder.addEventListener('error', () => {
      cancelVoiceSession(session);
      if (session.generation === app.personalGeneration) notify('The recording failed. Please try again.', true);
    }, { once: true });
    recorder.addEventListener('stop', () => {
      clearVoiceSession(session);
      if (session.cancelled || !app.authenticated || session.generation !== app.personalGeneration) return;
      dictate(new Blob(chunks, { type: recorder.mimeType || type || '' }), session.channel, session.generation);
    }, { once: true });
    recorder.start(); renderVoiceButton();
    // The server refuses a longer memo, so the recording stops itself at the limit and is turned into text.
    session.limit = setTimeout(() => { if (app.voice === session) toggleVoiceRecording(); }, limit * 1000);
  } catch (error) {
    session.cancelled = true;
    clearVoiceSession(session);
    if (session.generation === app.personalGeneration && app.authenticated) notify(`The microphone is not available. ${errorText(error)}`, true);
  }
}
// The browser decodes its own recording (WebM, Ogg or MP4) and writes 16 kHz mono 16-bit WAV, so the server needs no
// audio decoder. An offline context renders without touching any sound device.
async function speechWav(blob) {
  const decoded = await new OfflineAudioContext(1, 1, DICTATION_SAMPLE_RATE).decodeAudioData(await blob.arrayBuffer());
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * DICTATION_SAMPLE_RATE)), DICTATION_SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded; source.connect(offline.destination); source.start();
  const samples = (await offline.startRendering()).getChannelData(0);
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const word = (offset, value) => { for (let index = 0; index < 4; index += 1) view.setUint8(offset + index, value.charCodeAt(index)); };
  word(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); word(8, 'WAVE'); word(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, DICTATION_SAMPLE_RATE, true); view.setUint32(28, DICTATION_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); word(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 0x7fff), true));
  return new Blob([view], { type: 'audio/wav' });
}
async function dictate(blob, channel, generation) {
  if (!app.authenticated || generation !== app.personalGeneration) return;
  if (!blob.size) { notify('The recording is empty.', true); return; }
  app.uploadingVoice = true; renderVoiceButton();
  try {
    const wav = await speechWav(blob);
    if (!app.authenticated || generation !== app.personalGeneration) return;
    const encoded = await fileData(wav, 'Could not read the recording.');
    if (!app.authenticated || generation !== app.personalGeneration) return;
    const result = await api('/api/dictation', { data: encoded });
    if (!app.authenticated || generation !== app.personalGeneration) return;
    await addToDraft(channel, result.text, 'Dictated text');
  } catch (error) { if (generation === app.personalGeneration) notify(errorText(error), true); }
  finally { if (generation === app.personalGeneration) { app.uploadingVoice = false; renderVoiceButton(); } }
}
$('attach-image').addEventListener('click', () => $('image-file').click());
$('record-voice').addEventListener('click', toggleVoiceRecording);
$('image-file').addEventListener('change', () => attachImage($('image-file').files[0]));
$('saved-messages').addEventListener('click', showSavedMessages);
$('message-input').addEventListener('paste', (event) => {
  const item = [...(event.clipboardData?.items || [])].find(item => item.type.startsWith('image/'));
  if (item && !composerLocked()) { event.preventDefault(); attachImage(item.getAsFile()); }
});
$('composer-area').addEventListener('dragover', (event) => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); });
$('composer-area').addEventListener('drop', (event) => {
  if (event.dataTransfer?.files.length) { event.preventDefault(); attachImage(event.dataTransfer.files[0]); }
});

$('login-form').addEventListener('submit', (event) => { event.preventDefault(); const token = $('access-token').value.trim(); if (token) login(token); });
for (const nav of document.querySelectorAll('[data-view]')) nav.addEventListener('click', () => setView(nav.dataset.view));
$('new-channel').addEventListener('click', showNewChannel);
$('new-participant').addEventListener('click', showNewParticipant);
$('send-message').addEventListener('click', sendMessage);
$('smaller-text').addEventListener('click', () => changeConversationTextSize(-1));
$('larger-text').addEventListener('click', () => changeConversationTextSize(1));
$('emoji-picker').addEventListener('click', showEmojiPicker);
for (const control of document.querySelectorAll('.format-tool')) control.addEventListener('click', () => applyFormat(control.dataset.format));
$('clear-draft').addEventListener('click', showClearDraft);
$('composer-resize').addEventListener('pointerdown', startComposerDrag);
$('composer-resize').addEventListener('keydown', composerGripKey);
$('composer-resize').addEventListener('dblclick', () => setComposerHeight(null, true));
window.addEventListener('resize', () => { if (composerHeight !== null) setComposerHeight(composerHeight, false); });
try {
  const savedSize = Number(localStorage.getItem(CONVERSATION_TEXT_SIZE_KEY));
  if (CONVERSATION_TEXT_SIZES.includes(savedSize)) conversationTextSize = savedSize;
} catch { conversationTextSize = DEFAULT_CONVERSATION_TEXT_SIZE; }
applyConversationTextSize();
try {
  const savedHeight = Number(localStorage.getItem(COMPOSER_HEIGHT_KEY));
  setComposerHeight(savedHeight > 0 ? savedHeight : null, false);
} catch { setComposerHeight(null, false); }
$('message-input').maxLength = LIMITS.body;
$('search-input').maxLength = LIMITS.title;
$('message-input').addEventListener('input', () => { if (app.channelId) { app.drafts.set(app.channelId, $('message-input').value); app.unsavedDrafts.add(app.channelId); persistDraft(app.channelId); } resizeComposer(); updateMentionPicker(); });
$('message-input').addEventListener('click', updateMentionPicker);
$('message-input').addEventListener('blur', closeMentionPicker);
window.addEventListener('beforeunload', (event) => { if (app.authenticated && (app.voice || app.uploadingVoice || app.pendingPersonal || app.personalErrors.size || app.unsavedDrafts.size || app.outbox.some((item) => !item.durable))) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', cancelVoiceRecording);
$('message-input').addEventListener('keydown', (event) => {
  if (mentionPickerKey(event)) { event.preventDefault(); return; }
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); sendMessage(); return; }
  const format = FORMAT_SHORTCUTS[event.key.toLowerCase()];
  if (format && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey) { event.preventDefault(); applyFormat(format); }
});
$('search-input').addEventListener('input', scheduleSearch);
$('search-form').addEventListener('submit', (event) => { event.preventDefault(); clearTimeout(app.searchTimer); const query = $('search-input').value.trim(); if (query) runSearch(query); });
$('search-input').addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); setView('conversations'); } });
$('export-button').addEventListener('click', exportData);
$('dismiss-notice').addEventListener('click', () => { $('notice').hidden = true; });
$('open-sidebar').addEventListener('click', () => { $('app-shell').classList.add('sidebar-open'); $('sidebar').inert = false; document.querySelector('.main-panel').inert = true; $('sidebar-backdrop').hidden = false; $('open-sidebar').setAttribute('aria-expanded', 'true'); $('close-sidebar').focus(); });
$('close-sidebar').addEventListener('click', () => { closeSidebar(); $('open-sidebar').focus(); });
$('sidebar-backdrop').addEventListener('click', closeSidebar);
document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && app.authenticated && !document.querySelector('dialog[open]')) { event.preventDefault(); $('search-input').focus(); $('search-input').select(); }
  if (event.key === 'Escape' && $('app-shell').classList.contains('sidebar-open')) { closeSidebar(); $('open-sidebar').focus(); }
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && app.authenticated && app.view === 'conversations' && app.channelId) markRead(app.channelId); });
const mobileNavigation = matchMedia('(max-width: 850px)');
$('sidebar').inert = mobileNavigation.matches;
mobileNavigation.addEventListener('change', () => { closeSidebar(); });
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
$('send-shortcut').textContent = isMac ? '⌘ Enter' : 'Ctrl Enter';
for (const [key, kind] of Object.entries(FORMAT_SHORTCUTS)) {
  const control = document.querySelector(`.format-tool[data-format="${kind}"]`);
  control.title = `${control.getAttribute('aria-label')} (${isMac ? '⌘' : 'Ctrl '}${key.toUpperCase()})`;
}
$('search-form').querySelector('kbd').textContent = isMac ? '⌘ K' : 'Ctrl K';
const themeMedia = matchMedia('(prefers-color-scheme: dark)');
let themePreference;
try { themePreference = localStorage.getItem('agent-room.theme'); } catch { themePreference = null; }
if (!['light', 'dark', 'system'].includes(themePreference)) themePreference = 'system';
function applyTheme() {
  const resolved = themePreference === 'system' ? (themeMedia.matches ? 'dark' : 'light') : themePreference;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = themePreference;
  $('theme-toggle').value = themePreference;
  $('theme-toggle').title = `Appearance: ${themePreference} · currently ${resolved}`;
}
applyTheme();
$('theme-toggle').addEventListener('change', () => {
  themePreference = $('theme-toggle').value;
  applyTheme();
  persistPreferences();
  try { localStorage.setItem('agent-room.theme', themePreference); }
  catch { notify('Appearance changed for this tab. Browser storage is unavailable, so it will reset on reload.'); }
});
themeMedia.addEventListener('change', () => { if (themePreference === 'system') applyTheme(); });
if (launchToken !== null) {
  const token = launchToken;
  launchToken = null;
  login(token);
} else {
  start().catch((error) => { showLogin(); if (error.status !== 401) { $('login-error').textContent = errorText(error); $('login-error').hidden = false; } });
}
