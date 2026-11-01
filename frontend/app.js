"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  statusText: $("status-text"),
  empty: $("empty"),
  transcript: $("transcript"),
  chat: $("chat"),
  form: $("composer"),
  prompt: $("prompt"),
  send: $("send"),
  stop: $("stop"),
  newChat: $("new-chat"),
  newChatSidebar: $("new-chat-sidebar"),
  sidebar: $("sidebar"),
  sidebarToggle: $("sidebar-toggle"),
  sidebarBackdrop: $("sidebar-backdrop"),
  searchInput: $("search-input"),
  tabActive: $("tab-active"),
  tabArchived: $("tab-archived"),
  convList: $("conversation-list"),
  sidebarEmpty: $("sidebar-empty"),
  activeConvTitle: $("active-conv-title"),
  renameConvBtn: $("rename-conv-btn"),
  historyBanner: $("history-disabled-banner"),
  networkModeIndicator: $("network-mode-indicator"),
  networkModeText: $("network-mode-text"),
  memoryIndicator: $("memory-indicator"),
  memoryIndicatorText: $("memory-indicator-text"),
  openToolsBtn: $("open-tools-btn"),
  pendingApprovalsBadge: $("pending-approvals-badge"),
  openTasksBtn: $("open-tasks-btn"),
  activeTasksBadge: $("active-tasks-badge"),
  openNotificationsBtn: $("open-notifications-btn"),
  unreadNotificationsBadge: $("unread-notifications-badge"),
  openMemoriesBtn: $("open-memories-btn"),
  memoryCountBadge: $("memory-count-badge"),
  openSettingsBtn: $("open-settings-btn"),
  settingsReloadBadge: $("settings-reload-badge"),
  activityBar: $("activity-bar"),
  activityText: $("activity-text"),
  // Rename modal
  renameModal: $("rename-modal"),
  renameForm: $("rename-form"),
  renameInput: $("rename-input"),
  // Delete modal
  deleteModal: $("delete-modal"),
  deleteModalDesc: $("delete-modal-desc"),
  confirmDeleteBtn: $("confirm-delete-btn"),
  // Memories modal
  memoriesModal: $("memories-modal"),
  memoryEnabledToggle: $("memory-enabled-toggle"),
  memorySearchInput: $("memory-search-input"),
  memoryCategoryFilter: $("memory-category-filter"),
  memoryForm: $("memory-form"),
  memoryEditId: $("memory-edit-id"),
  memoryContentInput: $("memory-content-input"),
  memoryCategoryInput: $("memory-category-input"),
  memoryImportanceInput: $("memory-importance-input"),
  memorySubmitBtn: $("memory-submit-btn"),
  memoryCancelEditBtn: $("memory-cancel-edit-btn"),
  memoryFormError: $("memory-form-error"),
  memoriesList: $("memories-list"),
  memoriesEmpty: $("memories-empty"),
  // Settings modal
  settingsModal: $("settings-modal"),
  settingsForm: $("settings-form"),
  settingsReloadBanner: $("settings-reload-banner"),
  settingsReloadFieldsText: $("settings-reload-fields-text"),
  reloadModelNowBtn: $("reload-model-now-btn"),
  runtimeSummary: $("runtime-summary"),
  setTheme: $("set-theme"),
  setNewConvBehavior: $("set-new-conv-behavior"),
  setStreamResponses: $("set-stream-responses"),
  setHistoryEnabled: $("set-history-enabled"),
  setMemoryEnabled: $("set-memory-enabled"),
  setToolsEnabled: $("set-tools-enabled"),
  setOfflineMode: $("set-offline-mode"),
  setMaxToolCalls: $("set-max-tool-calls"),
  setTemperature: $("set-temperature"),
  setTopP: $("set-top-p"),
  setMaxTokens: $("set-max-tokens"),
  setRepeatPenalty: $("set-repeat-penalty"),
  setSystemPrompt: $("set-system-prompt"),
  setModelPath: $("set-model-path"),
  setNGpuLayers: $("set-n-gpu-layers"),
  setNCtx: $("set-n-ctx"),
  settingsConfirmBox: $("settings-confirm-box"),
  confirmFieldsList: $("confirm-fields-list"),
  saveWithoutReloadBtn: $("save-without-reload-btn"),
  saveAndReloadBtn: $("save-and-reload-btn"),
  settingsFormError: $("settings-form-error"),
  settingsFormOk: $("settings-form-ok"),
  // Tools & Workspace modal (Phase 3)
  toolsModal: $("tools-modal"),
  toolsModalModeStatus: $("tools-modal-mode-status"),
  refreshToolsBtn: $("refresh-tools-btn"),
  pendingApprovalsSection: $("pending-approvals-section"),
  modalApprovalsList: $("modal-approvals-list"),
  workspaceFilesList: $("workspace-files-list"),
  workspaceFilesEmpty: $("workspace-files-empty"),
  toolExecutionsList: $("tool-executions-list"),
  toolExecutionsEmpty: $("tool-executions-empty"),
  toolCatalogList: $("tool-catalog-list"),
  // Tasks & Notifications modals (Phase 4)
  tasksModal: $("tasks-modal"),
  tasksTimezoneLabel: $("tasks-timezone-label"),
  tickSchedulerBtn: $("tick-scheduler-btn"),
  refreshTasksBtn: $("refresh-tasks-btn"),
  taskCreateForm: $("task-create-form"),
  taskTitleInput: $("task-title-input"),
  taskTypeInput: $("task-type-input"),
  taskScheduledInput: $("task-scheduled-input"),
  taskRecurrenceInput: $("task-recurrence-input"),
  taskPriorityInput: $("task-priority-input"),
  taskDescInput: $("task-desc-input"),
  taskToolConfig: $("task-tool-config"),
  taskToolNameInput: $("task-tool-name-input"),
  taskToolArgsInput: $("task-tool-args-input"),
  taskFormError: $("task-form-error"),
  tasksList: $("tasks-list"),
  tasksEmpty: $("tasks-empty"),
  cancelTaskModal: $("cancel-task-modal"),
  cancelTaskModalDesc: $("cancel-task-modal-desc"),
  confirmCancelTaskBtn: $("confirm-cancel-task-btn"),
  notificationsModal: $("notifications-modal"),
  notificationsUnreadOnly: $("notifications-unread-only"),
  markAllNotificationsReadBtn: $("mark-all-notifications-read-btn"),
  refreshNotificationsBtn: $("refresh-notifications-btn"),
  notificationsList: $("notifications-list"),
  notificationsEmpty: $("notifications-empty"),
  // Phase 5: Voice & Wake-Word UI
  wakeWordIndicator: $("wake-word-indicator"),
  wakeWordIndicatorText: $("wake-word-indicator-text"),
  voiceStatePill: $("voice-state-pill"),
  voiceStateText: $("voice-state-text"),
  topbarStopSpeakingBtn: $("topbar-stop-speaking-btn"),
  voiceErrorBar: $("voice-error-bar"),
  voiceErrorText: $("voice-error-text"),
  voiceErrorDismissBtn: $("voice-error-dismiss-btn"),
  voiceRecordingBar: $("voice-recording-bar"),
  voiceRecordingLabel: $("voice-recording-label"),
  voiceRecordingTimer: $("voice-recording-timer"),
  voiceWaveform: $("voice-waveform"),
  voiceStopRecordBtn: $("voice-stop-record-btn"),
  voiceCancelRecordBtn: $("voice-cancel-record-btn"),
  voiceTranscriptBar: $("voice-transcript-bar"),
  voiceConfidenceBadge: $("voice-confidence-badge"),
  voiceTranscriptInput: $("voice-transcript-input"),
  voiceSubmitTranscriptBtn: $("voice-submit-transcript-btn"),
  voiceDiscardTranscriptBtn: $("voice-discard-transcript-btn"),
  voiceSpeakingBar: $("voice-speaking-bar"),
  voiceSpeakingText: $("voice-speaking-text"),
  voiceStopSpeakingBtn: $("voice-stop-speaking-btn"),
  micBtn: $("mic-btn"),
  micBtnLabel: $("mic-btn-label"),
  // Phase 5 Settings controls
  setVoiceInputEnabled: $("set-voice-input-enabled"),
  setVoiceOutputEnabled: $("set-voice-output-enabled"),
  setAutoSubmitTranscript: $("set-auto-submit-transcript"),
  setStopSpeakingOnInput: $("set-stop-speaking-on-input"),
  setWakeWordEnabled: $("set-wake-word-enabled"),
  setSelectedMicrophone: $("set-selected-microphone"),
  setSelectedSpeaker: $("set-selected-speaker"),
  setSelectedTtsVoice: $("set-selected-tts-voice"),
  setListeningDuration: $("set-listening-duration"),
  setSpeechSpeed: $("set-speech-speed"),
  setSpeechVolume: $("set-speech-volume"),
  setWakeWordSensitivity: $("set-wake-word-sensitivity"),
  setWakeWordTimeout: $("set-wake-word-timeout"),
  refreshAudioDevicesBtn: $("refresh-audio-devices-btn"),
  testTtsVoiceBtn: $("test-tts-voice-btn"),
};

const MAX_HISTORY = 40;
let history = [];
let conversationId = null;
let conversationTitle = "New conversation";
let showingArchived = false;
let searchDebounce = null;
let pendingRenameId = null;
let pendingDeleteId = null;

let modelReady = false;
let busy = false;
let controller = null;

let settingsState = {
  saved: {},
  active: {
    theme: "dark",
    stream_responses: true,
    conversation_history_enabled: true,
    memory_enabled: true,
    default_new_conversation_behavior: "resume_last",
    offline_mode: true,
    tools_enabled: true,
    max_tool_calls_per_turn: 3,
  },
  reload_required: false,
  pending_reload_fields: [],
};

function makeClientMsgId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 14)}`;
}

/* ---- modal helpers ----------------------------------------------------- */
function openModal(dialog) {
  if (!dialog) return;
  if (typeof dialog.showModal === "function" && !dialog.open) {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
}

function closeModal(dialog) {
  if (!dialog) return;
  if (typeof dialog.close === "function" && dialog.open) {
    dialog.close();
  } else {
    dialog.removeAttribute("open");
  }
}

document.querySelectorAll("[data-close-modal]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal($(btn.dataset.closeModal)));
});

/* ---- mobile sidebar toggle --------------------------------------------- */
function setSidebarOpen(open) {
  els.sidebar.classList.toggle("open", open);
  els.sidebarBackdrop.hidden = !open;
  els.sidebarToggle.setAttribute("aria-expanded", String(open));
}

els.sidebarToggle.addEventListener("click", () => {
  setSidebarOpen(!els.sidebar.classList.contains("open"));
});
els.sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false));

/* ---- status polling ---------------------------------------------------- */
function describe(s) {
  if (s.state === "ready") {
    const where = s.n_gpu_layers === 0 ? "on CPU"
      : s.n_gpu_layers < 0 || (s.model && s.n_gpu_layers >= s.model.layers) ? "on GPU"
      : `with ${s.n_gpu_layers} of ${s.model?.layers ?? "?"} layers on GPU`;
    return `${s.model?.name ?? "Model"} ready ${where}`;
  }
  if (s.state === "loading") return "Loading the model…";
  if (s.state === "error") return `Model failed to load: ${s.error}`;
  return `Model is ${s.state}`;
}

function setStatus(state, text) {
  els.status.dataset.state = state;
  els.statusText.textContent = text;
  els.statusText.title = text;
}

async function pollStatus() {
  let delay = 1000;
  try {
    const res = await fetch("/api/status");
    const s = await res.json();
    setStatus(s.state, describe(s));
    modelReady = s.state === "ready";
    if (s.settings) {
      applyRuntimeIndicators(s.settings, Boolean(s.reload_required));
    }
    if (typeof s.pending_approvals_count === "number") {
      updatePendingApprovalsBadge(s.pending_approvals_count);
    }
    if (typeof s.active_tasks_count === "number" && els.activeTasksBadge) {
      els.activeTasksBadge.textContent = String(s.active_tasks_count);
    }
    if (typeof s.unread_notifications_count === "number" && els.unreadNotificationsBadge) {
      els.unreadNotificationsBadge.textContent = String(s.unread_notifications_count);
      els.unreadNotificationsBadge.hidden = !(s.unread_notifications_count > 0);
    }
    if (s.system_timezone && els.tasksTimezoneLabel) {
      els.tasksTimezoneLabel.textContent = `Timezone: ${s.system_timezone}`;
    }
    if (s.voice) {
      syncVoiceStatusFromServer(s.voice);
    } else if (s.voice_state) {
      syncVoiceStatusFromServer({
        state: s.voice_state,
        wake_word: { enabled: Boolean(s.wake_word_enabled) },
      });
    }
    if (modelReady || s.state === "error") delay = 15000;
  } catch {
    modelReady = false;
    setStatus("offline", "Cannot reach the Jarvis server. Is it running?");
    delay = 3000;
  }
  updateControls();
  setTimeout(pollStatus, delay);
}

function updatePendingApprovalsBadge(count) {
  if (!els.pendingApprovalsBadge) return;
  els.pendingApprovalsBadge.textContent = String(count);
  els.pendingApprovalsBadge.hidden = !(count > 0);
}

function applyRuntimeIndicators(active, reloadRequired) {
  const theme = active.theme || "dark";
  document.documentElement.setAttribute("data-theme", theme);
  const memOn = Boolean(active.memory_enabled);
  els.memoryIndicator.dataset.enabled = String(memOn);
  els.memoryIndicatorText.textContent = memOn ? "Memory: On" : "Memory: Off";
  els.memoryEnabledToggle.checked = memOn;

  const isOffline = active.offline_mode !== false;
  if (els.networkModeIndicator && els.networkModeText) {
    els.networkModeIndicator.dataset.online = String(!isOffline);
    els.networkModeText.textContent = isOffline ? "Offline" : "Online";
  }

  const wakeOn = Boolean(active.wake_word_enabled);
  if (els.wakeWordIndicator && els.wakeWordIndicatorText) {
    els.wakeWordIndicator.dataset.enabled = String(wakeOn);
    els.wakeWordIndicatorText.textContent = wakeOn ? "Wake: On" : "Wake: Off";
  }
  if (els.micBtn) {
    els.micBtn.hidden = active.voice_input_enabled === false;
  }

  els.historyBanner.hidden = Boolean(active.conversation_history_enabled);
  els.settingsReloadBadge.hidden = !reloadRequired;
}

function setActivityStatus(text) {
  if (!els.activityBar || !els.activityText) return;
  if (!text) {
    els.activityBar.hidden = true;
    els.activityText.textContent = "";
  } else {
    els.activityText.textContent = text;
    els.activityBar.hidden = false;
  }
}

/* ---- transcript & messages --------------------------------------------- */
function updateHeaderTitle(title, hasPersistedId) {
  conversationTitle = title || "New conversation";
  els.activeConvTitle.textContent = conversationTitle;
  els.renameConvBtn.hidden = !hasPersistedId;
}

function addMessage(role, text = "") {
  els.empty.hidden = true;
  const li = document.createElement("li");
  li.className = `msg msg-${role}`;
  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  li.append(body);
  els.transcript.append(li);
  scrollToEnd();
  return li;
}

function setMeta(li, text) {
  if (text) {
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.textContent = text;
    li.append(meta);
  }
  if (li.classList.contains("msg-assistant")) {
    renderMessageVoiceControls(li);
  }
}

function renderMessageVoiceControls(li) {
  if (li.querySelector(".msg-voice-actions")) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-voice-actions";
  const speakBtn = document.createElement("button");
  speakBtn.type = "button";
  speakBtn.className = "mini-btn msg-speak-btn";
  speakBtn.textContent = "🔊 Speak";
  speakBtn.addEventListener("click", () => {
    const bodyEl = li.querySelector(".msg-body");
    const content = bodyEl ? bodyEl.textContent.trim() : "";
    if (content) speakText(content);
  });
  wrap.append(speakBtn);
  li.append(wrap);
}

function renderMessageMemories(li, retrievedMemories, savedMemory) {
  const retrieved = Array.isArray(retrievedMemories) ? retrievedMemories : [];
  if (!retrieved.length && !savedMemory) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-memories";

  if (savedMemory && savedMemory.content) {
    const savedPill = document.createElement("span");
    savedPill.className = "memory-pill memory-pill-saved";
    savedPill.textContent = `Saved to memory: [${savedMemory.category || "general"}] ${savedMemory.content}`;
    wrap.append(savedPill);
  }
  for (const m of retrieved) {
    const pill = document.createElement("span");
    pill.className = "memory-pill";
    pill.textContent = `Memory used: [${m.category || "general"}] ${m.content}`;
    wrap.append(pill);
  }
  li.append(wrap);
}

function renderMessageTools(li, toolEvents) {
  const events = Array.isArray(toolEvents) ? toolEvents : [];
  if (!events.length) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-tools";

  for (const ev of events) {
    const pill = document.createElement("div");
    pill.className = "tool-event-pill";
    pill.dataset.status = ev.status || "info";

    const left = document.createElement("span");
    const dur = ev.duration_ms != null ? ` (${Number(ev.duration_ms).toFixed(1)} ms)` : "";
    left.textContent = `Tool: ${ev.tool_name} · ${ev.status || "executed"}${dur}`;

    const right = document.createElement("span");
    right.className = "tool-event-summary";
    if (ev.error) {
      right.textContent = ev.error;
    } else if (ev.result !== undefined) {
      const compact = typeof ev.result === "object" ? JSON.stringify(ev.result) : String(ev.result);
      right.textContent = compact.length > 140 ? `${compact.slice(0, 140)}…` : compact;
    } else if (ev.explanation) {
      right.textContent = ev.explanation;
    }

    pill.append(left, right);
    wrap.append(pill);
  }
  li.append(wrap);
}

function buildApprovalCard(approval, onDecided) {
  const card = document.createElement("div");
  card.className = "approval-card";
  card.dataset.approvalId = approval.id;

  const header = document.createElement("div");
  header.className = "approval-card-header";
  const titleSpan = document.createElement("span");
  titleSpan.textContent = `Approval Request · ${approval.tool_name}`;
  const permSpan = document.createElement("span");
  permSpan.className = "perm-badge";
  permSpan.dataset.level = approval.permission_level || "level_2_mutating_local";
  permSpan.textContent = approval.permission_level === "level_2_mutating_local"
    ? "Level 2 · Mutating Local"
    : approval.permission_level;
  header.append(titleSpan, permSpan);

  const desc = document.createElement("div");
  desc.className = "approval-card-desc";
  desc.textContent = approval.explanation || `Execute ${approval.tool_name}`;

  const argsPre = document.createElement("pre");
  argsPre.className = "approval-card-args";
  argsPre.textContent = JSON.stringify(approval.arguments || {}, null, 2);

  const footer = document.createElement("div");
  footer.className = "approval-card-footer";

  const exp = document.createElement("span");
  exp.className = "approval-card-exp";
  exp.textContent = approval.status === "pending"
    ? `Status: Pending user confirmation · Expires: ${approval.expires_at || "soon"}`
    : `Status: ${approval.status}`;

  const actions = document.createElement("div");
  actions.className = "approval-card-actions";

  if (approval.status === "pending") {
    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "btn btn-primary btn-sm";
    approveBtn.textContent = "Approve";

    const rejectBtn = document.createElement("button");
    rejectBtn.type = "button";
    rejectBtn.className = "btn btn-danger btn-sm";
    rejectBtn.textContent = "Reject";

    approveBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      exp.textContent = "Executing approved action…";
      try {
        const res = await fetch(`/api/approvals/${approval.id}/approve`, { method: "POST" });
        if (!res.ok) throw new Error(await errorText(res));
        const data = await res.json();
        actions.remove();
        exp.textContent = "Status: Approved & executed";
        if (data.assistant_message && data.assistant_message.content) {
          history.push({ role: "assistant", content: data.assistant_message.content });
          const followLi = addMessage("assistant", data.assistant_message.content);
          renderMessageTools(followLi, data.assistant_message.metadata?.tool_events);
        }
        if (onDecided) onDecided(data);
        refreshToolsModalData();
      } catch (err) {
        exp.textContent = `Approval failed: ${err.message}`;
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    rejectBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      try {
        const res = await fetch(`/api/approvals/${approval.id}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "Rejected by user in UI" }),
        });
        if (!res.ok) throw new Error(await errorText(res));
        const data = await res.json();
        actions.remove();
        exp.textContent = "Status: Rejected by user";
        if (data.assistant_message && data.assistant_message.content) {
          history.push({ role: "assistant", content: data.assistant_message.content });
          const followLi = addMessage("assistant", data.assistant_message.content);
          renderMessageTools(followLi, data.assistant_message.metadata?.tool_events);
        }
        if (onDecided) onDecided(data);
        refreshToolsModalData();
      } catch (err) {
        exp.textContent = `Rejection failed: ${err.message}`;
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    actions.append(rejectBtn, approveBtn);
  }

  footer.append(exp, actions);
  card.append(header, desc, argsPre, footer);
  return card;
}

function renderInlineApproval(li, approval) {
  if (!approval || !approval.id) return;
  const existing = li.querySelector(`[data-approval-id="${approval.id}"]`);
  if (existing) existing.remove();
  li.append(buildApprovalCard(approval));
  scrollToEnd();
}

function renderMessageCitations(li, citations) {
  const list = Array.isArray(citations) ? citations : [];
  if (!list.length) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-citations";

  const seen = new Set();
  for (const c of list) {
    if (!c || !c.url || seen.has(c.url)) continue;
    seen.add(c.url);
    const card = document.createElement("div");
    card.className = "citation-card";

    const link = document.createElement("a");
    link.className = "citation-title";
    link.href = c.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = c.title || c.url;

    const meta = document.createElement("div");
    meta.className = "citation-meta";
    meta.textContent = `${c.url} · Retrieved: ${c.retrieved_at || "recent"}`;

    card.append(link, meta);
    if (c.snippet) {
      const snip = document.createElement("div");
      snip.className = "citation-snippet";
      snip.textContent = c.snippet;
      card.append(snip);
    }
    wrap.append(card);
  }
  li.append(wrap);
}

function scrollToEnd() {
  els.chat.scrollTop = els.chat.scrollHeight;
}

function formatStats(d) {
  if (d.seconds == null && d.completion_tokens == null) return "";
  const speed = d.decode_tokens_per_second ?? d.tokens_per_second;
  const parts = [];
  if (d.completion_tokens != null && d.seconds != null) {
    parts.push(`${d.completion_tokens} tokens in ${Number(d.seconds).toFixed(1)} s`);
  }
  if (speed) parts.push(`${Number(speed).toFixed(1)} tokens/s`);
  if (d.context_messages_omitted > 0) {
    parts.push(`${d.context_messages_omitted} older messages trimmed from context`);
  }
  if (d.finish_reason === "length") parts.push("stopped at length limit");
  return parts.join(", ");
}

/* ---- conversations sidebar --------------------------------------------- */
async function refreshConversations() {
  const q = els.searchInput.value.trim();
  try {
    let url;
    if (q) {
      url = `/api/conversations/search?q=${encodeURIComponent(q)}&include_archived=${showingArchived}`;
    } else {
      url = `/api/conversations?archived_only=${showingArchived}`;
    }
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    const list = data.conversations || [];
    renderConversationList(list, Boolean(q));
    return list;
  } catch {
    return [];
  }
}

function renderConversationList(conversations, isSearch) {
  els.convList.replaceChildren();
  els.sidebarEmpty.hidden = conversations.length > 0;
  if (!conversations.length) {
    els.sidebarEmpty.textContent = isSearch
      ? "No matching conversations."
      : showingArchived
      ? "No archived conversations."
      : "No conversations yet.";
    return;
  }

  for (const c of conversations) {
    const li = document.createElement("li");
    li.className = "conv-item" + (c.id === conversationId ? " active" : "");
    li.dataset.id = c.id;

    const main = document.createElement("div");
    main.className = "conv-item-main";

    const title = document.createElement("div");
    title.className = "conv-item-title";
    title.textContent = c.title || "New conversation";

    const sub = document.createElement("div");
    sub.className = "conv-item-sub";
    if (isSearch && c.snippet) {
      sub.textContent = `${c.match_type === "title" ? "Title match" : "Match"}: ${c.snippet}`;
    } else {
      const count = c.message_count ?? 0;
      sub.textContent = c.last_message_preview
        ? `${count} msg · ${c.last_message_preview}`
        : `${count} messages`;
    }

    main.append(title, sub);

    const actions = document.createElement("div");
    actions.className = "conv-item-actions";

    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "mini-btn";
    renameBtn.textContent = "Rename";
    renameBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openRenameModal(c.id, c.title);
    });

    const archBtn = document.createElement("button");
    archBtn.type = "button";
    archBtn.className = "mini-btn";
    archBtn.textContent = c.archived ? "Restore" : "Archive";
    archBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleArchiveConversation(c.id, !c.archived);
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "mini-btn mini-btn-danger";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openDeleteModal(c.id, c.title);
    });

    actions.append(renameBtn, archBtn, delBtn);
    li.append(main, actions);
    li.addEventListener("click", () => selectConversation(c.id));
    els.convList.append(li);
  }
}

async function selectConversation(id) {
  if (busy) controller?.abort();
  try {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) return;
    const data = await res.json();
    conversationId = data.conversation_id;
    const conv = data.conversation || {};
    updateHeaderTitle(conv.title || "Conversation", true);

    history = [];
    els.transcript.replaceChildren();
    const msgs = data.messages || [];
    if (!msgs.length) {
      els.empty.hidden = false;
    } else {
      els.empty.hidden = true;
      for (const m of msgs) {
        if (m.role === "system") continue;
        history.push({ role: m.role, content: m.content });
        const li = addMessage(m.role, m.content);
        if (m.role === "assistant") {
          setMeta(li, formatStats({
            completion_tokens: m.completion_tokens,
            seconds: m.seconds,
            tokens_per_second: m.tokens_per_second,
            decode_tokens_per_second: m.metadata?.decode_tokens_per_second,
            context_messages_omitted: m.metadata?.context_messages_omitted,
            finish_reason: m.finish_reason,
          }));
          renderMessageMemories(
            li,
            m.metadata?.retrieved_memories,
            m.metadata?.saved_memory
          );
          renderMessageTools(li, m.metadata?.tool_events);
          if (m.metadata?.pending_approval) {
            renderInlineApproval(li, m.metadata.pending_approval);
          }
          renderMessageCitations(li, m.metadata?.citations);
        }
      }
    }
    setSidebarOpen(false);
    await refreshConversations();
  } catch {
    // ignore transient load failure
  }
}

function startFreshView() {
  controller?.abort();
  history = [];
  conversationId = null;
  updateHeaderTitle("New conversation", false);
  els.transcript.replaceChildren();
  els.empty.hidden = false;
  setSidebarOpen(false);
  refreshConversations();
  els.prompt.focus();
}

function openRenameModal(id, currentTitle) {
  pendingRenameId = id;
  els.renameInput.value = currentTitle || "";
  openModal(els.renameModal);
  els.renameInput.focus();
  els.renameInput.select();
}

els.renameForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = els.renameInput.value.trim();
  if (!pendingRenameId || !title) return;
  const res = await fetch(`/api/conversations/${pendingRenameId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (res.ok) {
    if (pendingRenameId === conversationId) {
      updateHeaderTitle(title, true);
    }
    closeModal(els.renameModal);
    await refreshConversations();
  }
});

els.renameConvBtn.addEventListener("click", () => {
  if (conversationId) openRenameModal(conversationId, conversationTitle);
});

async function toggleArchiveConversation(id, archive) {
  const endpoint = archive ? "archive" : "restore";
  const res = await fetch(`/api/conversations/${id}/${endpoint}`, { method: "POST" });
  if (res.ok) {
    if (archive && id === conversationId) {
      startFreshView();
    } else {
      await refreshConversations();
    }
  }
}

function openDeleteModal(id, title) {
  pendingDeleteId = id;
  els.deleteModalDesc.textContent = `Delete "${title || "New conversation"}"? This removes its messages from local storage. Saved long-term memories will not be deleted.`;
  openModal(els.deleteModal);
}

els.confirmDeleteBtn.addEventListener("click", async () => {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  pendingDeleteId = null;
  const res = await fetch(`/api/conversations/${id}?confirm=true`, { method: "DELETE" });
  closeModal(els.deleteModal);
  if (res.ok) {
    if (id === conversationId) {
      startFreshView();
    } else {
      await refreshConversations();
    }
  }
});

els.tabActive.addEventListener("click", () => {
  showingArchived = false;
  els.tabActive.classList.add("active");
  els.tabActive.setAttribute("aria-selected", "true");
  els.tabArchived.classList.remove("active");
  els.tabArchived.setAttribute("aria-selected", "false");
  refreshConversations();
});

els.tabArchived.addEventListener("click", () => {
  showingArchived = true;
  els.tabArchived.classList.add("active");
  els.tabArchived.setAttribute("aria-selected", "true");
  els.tabActive.classList.remove("active");
  els.tabActive.setAttribute("aria-selected", "false");
  refreshConversations();
});

els.searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(refreshConversations, 160);
});

/* ---- sending chat messages --------------------------------------------- */
function updateControls() {
  els.send.disabled = busy || !modelReady;
  els.send.hidden = busy;
  els.stop.hidden = !busy;
}

async function errorText(res) {
  try {
    const body = await res.json();
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
    if (body.detail && typeof body.detail === "object") return body.detail.message || JSON.stringify(body.detail);
    return body.detail || `Request failed (${res.status})`;
  } catch {
    return `Request failed (${res.status})`;
  }
}

async function send(text) {
  if (settingsState.active?.stop_speaking_on_input !== false && currentVoiceState === "Speaking") {
    stopSpeaking();
  }
  busy = true;
  updateControls();
  setActivityStatus("Thinking…");
  setVoiceUiState("Thinking", "Thinking…");
  history.push({ role: "user", content: text });
  addMessage("user", text);
  const li = addMessage("assistant");
  li.classList.add("pending");
  const body = li.querySelector(".msg-body");
  let reply = "";
  controller = new AbortController();
  const useStream = settingsState.active.stream_responses !== false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: history.slice(-MAX_HISTORY),
        stream: useStream,
        conversation_id: conversationId,
        client_msg_id: makeClientMsgId(),
      }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await errorText(res));
    const newCid = res.headers.get("X-Conversation-Id");
    if (newCid) conversationId = newCid;

    if (!useStream) {
      const data = await res.json();
      reply = data.content || "";
      body.textContent = reply;
      setMeta(li, formatStats(data));
      renderMessageMemories(li, data.retrieved_memories, data.saved_memory);
      renderMessageTools(li, data.tool_events);
      if (data.pending_approval) renderInlineApproval(li, data.pending_approval);
      renderMessageCitations(li, data.citations);
      if (data.saved_memory) refreshMemories();
      if (data.tool_events?.length || data.pending_approval) refreshToolsModalData();
    } else {
      setActivityStatus("Generating response…");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!line.startsWith("data:")) continue;
          const event = JSON.parse(line.slice(5));
          if (event.error) throw new Error(event.error);
          if (event.tool_activity) {
            const act = event.tool_activity;
            if (act.state === "executing") {
              setActivityStatus(`Executing tool: ${act.tool_name}…`);
            } else if (act.state === "awaiting_approval") {
              setActivityStatus(`Requesting user approval for ${act.tool_name}…`);
            } else if (act.state === "completed") {
              setActivityStatus(`Waiting for result from ${act.tool_name}…`);
            } else if (act.state === "generating_response") {
              setActivityStatus("Generating response…");
            }
          }
          if (event.delta) {
            reply += event.delta;
            body.textContent = reply;
            scrollToEnd();
          }
          if (event.done) {
            if (event.conversation_id) conversationId = event.conversation_id;
            setMeta(li, formatStats(event));
            renderMessageMemories(li, event.retrieved_memories, event.saved_memory);
            renderMessageTools(li, event.tool_events);
            if (event.pending_approval) renderInlineApproval(li, event.pending_approval);
            renderMessageCitations(li, event.citations);
            if (event.saved_memory) refreshMemories();
            if (event.tool_events?.length || event.pending_approval) refreshToolsModalData();
          }
        }
      }
    }
    history.push({ role: "assistant", content: reply });
    if (settingsState.active.conversation_history_enabled !== false) {
      const convs = await refreshConversations();
      const current = convs.find((c) => c.id === conversationId);
      if (current) updateHeaderTitle(current.title, true);
    }
    if (settingsState.active.voice_output_enabled && reply.trim()) {
      speakText(reply.trim());
    } else if (currentVoiceState === "Thinking") {
      setVoiceUiState(settingsState.active.wake_word_enabled ? "Listening" : "Idle");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      if (reply) history.push({ role: "assistant", content: reply });
      setMeta(li, "Stopped");
      setVoiceUiState("Interrupted");
    } else {
      history.pop();
      li.classList.add("msg-error");
      body.textContent = reply ? `${reply}\n\n${err.message}` : err.message;
      setVoiceUiState("Error", err.message);
    }
  } finally {
    setActivityStatus("");
    li.classList.remove("pending");
    busy = false;
    controller = null;
    updateControls();
    els.prompt.focus();
  }
}

/* ---- long-term memory panel -------------------------------------------- */
async function refreshMemories() {
  const q = els.memorySearchInput.value.trim();
  const cat = els.memoryCategoryFilter.value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (cat) params.set("category", cat);
  try {
    const res = await fetch(`/api/memories?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    const list = data.memories || [];
    if (!q && !cat) {
      els.memoryCountBadge.textContent = String(list.length);
    }
    renderMemoriesList(list);
  } catch {
    // ignore
  }
}

function renderMemoriesList(memories) {
  els.memoriesList.replaceChildren();
  els.memoriesEmpty.hidden = memories.length > 0;
  for (const m of memories) {
    const li = document.createElement("li");
    li.className = "memory-item";
    li.dataset.id = m.id;

    const main = document.createElement("div");
    main.className = "memory-item-main";

    const content = document.createElement("div");
    content.className = "memory-item-content";
    content.textContent = m.content;

    const meta = document.createElement("div");
    meta.className = "memory-item-meta";
    meta.textContent = `Category: ${m.category} · Importance: ${m.importance}/5`;

    main.append(content, meta);

    const actions = document.createElement("div");
    actions.className = "memory-item-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "mini-btn";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => {
      els.memoryEditId.value = m.id;
      els.memoryContentInput.value = m.content;
      els.memoryCategoryInput.value = m.category || "general";
      els.memoryImportanceInput.value = String(m.importance || 3);
      els.memorySubmitBtn.textContent = "Update memory";
      els.memoryCancelEditBtn.hidden = false;
      els.memoryContentInput.focus();
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "mini-btn mini-btn-danger";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      const r = await fetch(`/api/memories/${m.id}`, { method: "DELETE" });
      if (r.ok) await refreshMemories();
    });

    actions.append(editBtn, delBtn);
    li.append(main, actions);
    els.memoriesList.append(li);
  }
}

function resetMemoryForm() {
  els.memoryEditId.value = "";
  els.memoryContentInput.value = "";
  els.memoryCategoryInput.value = "general";
  els.memoryImportanceInput.value = "3";
  els.memorySubmitBtn.textContent = "Save memory";
  els.memoryCancelEditBtn.hidden = true;
  els.memoryFormError.hidden = true;
}

els.memoryCancelEditBtn.addEventListener("click", resetMemoryForm);

els.memoryForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.memoryFormError.hidden = true;
  const editId = els.memoryEditId.value;
  const payload = {
    content: els.memoryContentInput.value.trim(),
    category: els.memoryCategoryInput.value,
    importance: Number(els.memoryImportanceInput.value),
  };
  if (!payload.content) return;
  const url = editId ? `/api/memories/${editId}` : "/api/memories";
  const method = editId ? "PATCH" : "POST";
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    els.memoryFormError.textContent = await errorText(res);
    els.memoryFormError.hidden = false;
    return;
  }
  resetMemoryForm();
  await refreshMemories();
});

els.memoryEnabledToggle.addEventListener("change", async () => {
  const enabled = els.memoryEnabledToggle.checked;
  const res = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ memory_enabled: enabled }),
  });
  if (res.ok) {
    settingsState = await res.json();
    applyRuntimeIndicators(settingsState.active, settingsState.reload_required);
  }
});

els.memorySearchInput.addEventListener("input", refreshMemories);
els.memoryCategoryFilter.addEventListener("change", refreshMemories);

function openMemoriesModal() {
  resetMemoryForm();
  refreshMemories();
  openModal(els.memoriesModal);
}
els.openMemoriesBtn.addEventListener("click", openMemoriesModal);
els.memoryIndicator.addEventListener("click", openMemoriesModal);

/* ---- settings panel ---------------------------------------------------- */
async function fetchSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return settingsState;
    settingsState = await res.json();
    applyRuntimeIndicators(settingsState.active, settingsState.reload_required);
    return settingsState;
  } catch {
    return settingsState;
  }
}

function populateSettingsForm(state) {
  const s = state.saved || {};
  const a = state.active || {};
  els.setTheme.value = s.theme ?? "dark";
  els.setNewConvBehavior.value = s.default_new_conversation_behavior ?? "resume_last";
  els.setStreamResponses.checked = s.stream_responses !== false;
  els.setHistoryEnabled.checked = s.conversation_history_enabled !== false;
  els.setMemoryEnabled.checked = s.memory_enabled !== false;
  if (els.setToolsEnabled) els.setToolsEnabled.checked = s.tools_enabled !== false;
  if (els.setOfflineMode) els.setOfflineMode.checked = s.offline_mode !== false;
  if (els.setMaxToolCalls) els.setMaxToolCalls.value = s.max_tool_calls_per_turn ?? 3;
  els.setTemperature.value = s.temperature ?? 0.7;
  els.setTopP.value = s.top_p ?? 0.9;
  els.setMaxTokens.value = s.max_tokens ?? 512;
  els.setRepeatPenalty.value = s.repeat_penalty ?? 1.1;
  els.setSystemPrompt.value = s.system_prompt ?? "";
  els.setModelPath.value = s.model_path ?? "";
  els.setNGpuLayers.value = String(s.n_gpu_layers ?? "auto");
  els.setNCtx.value = s.n_ctx ?? 4096;

  // Phase 5 Voice & Wake-Word settings
  if (els.setVoiceInputEnabled) els.setVoiceInputEnabled.checked = s.voice_input_enabled !== false;
  if (els.setVoiceOutputEnabled) els.setVoiceOutputEnabled.checked = Boolean(s.voice_output_enabled);
  if (els.setAutoSubmitTranscript) els.setAutoSubmitTranscript.checked = Boolean(s.auto_submit_transcript);
  if (els.setStopSpeakingOnInput) els.setStopSpeakingOnInput.checked = s.stop_speaking_on_input !== false;
  if (els.setWakeWordEnabled) els.setWakeWordEnabled.checked = Boolean(a.wake_word_enabled);
  if (els.setSelectedMicrophone) els.setSelectedMicrophone.value = s.selected_microphone ? String(s.selected_microphone) : "default";
  if (els.setSelectedSpeaker) els.setSelectedSpeaker.value = s.selected_speaker ? String(s.selected_speaker) : "default";
  if (els.setSelectedTtsVoice && s.selected_tts_voice) els.setSelectedTtsVoice.value = s.selected_tts_voice;
  if (els.setListeningDuration) els.setListeningDuration.value = s.listening_duration ?? 8.0;
  if (els.setSpeechSpeed) els.setSpeechSpeed.value = s.speech_speed ?? 1.0;
  if (els.setSpeechVolume) els.setSpeechVolume.value = s.speech_volume ?? 1.0;
  if (els.setWakeWordSensitivity) els.setWakeWordSensitivity.value = s.wake_word_sensitivity ?? 0.5;
  if (els.setWakeWordTimeout) els.setWakeWordTimeout.value = s.wake_word_timeout_seconds ?? 10;

  els.runtimeSummary.textContent =
    `Active runtime: n_gpu_layers=${a.n_gpu_layers}, n_ctx=${a.n_ctx}, ` +
    `temperature=${a.temperature}, max_tokens=${a.max_tokens}, offload_kqv=${a.offload_kqv}`;

  const pending = state.pending_reload_fields || [];
  els.settingsReloadBanner.hidden = !state.reload_required;
  els.settingsReloadFieldsText.textContent = pending.length
    ? `Saved changes to (${pending.join(", ")}) will apply when the model reloads.`
    : "";

  els.settingsConfirmBox.hidden = true;
  els.settingsFormError.hidden = true;
  els.settingsFormOk.hidden = true;
}

function collectSettingsFormValues() {
  const rawLayers = els.setNGpuLayers.value.trim();
  const nGpuLayers = rawLayers.toLowerCase() === "auto" ? "auto" : Number(rawLayers);
  const micVal = els.setSelectedMicrophone ? els.setSelectedMicrophone.value : "default";
  const spkVal = els.setSelectedSpeaker ? els.setSelectedSpeaker.value : "default";
  return {
    theme: els.setTheme.value,
    default_new_conversation_behavior: els.setNewConvBehavior.value,
    stream_responses: els.setStreamResponses.checked,
    conversation_history_enabled: els.setHistoryEnabled.checked,
    memory_enabled: els.setMemoryEnabled.checked,
    tools_enabled: els.setToolsEnabled ? els.setToolsEnabled.checked : true,
    offline_mode: els.setOfflineMode ? els.setOfflineMode.checked : true,
    max_tool_calls_per_turn: els.setMaxToolCalls ? Number(els.setMaxToolCalls.value) : 3,
    temperature: Number(els.setTemperature.value),
    top_p: Number(els.setTopP.value),
    max_tokens: Number(els.setMaxTokens.value),
    repeat_penalty: Number(els.setRepeatPenalty.value),
    system_prompt: els.setSystemPrompt.value.trim(),
    model_path: els.setModelPath.value.trim(),
    n_gpu_layers: nGpuLayers,
    n_ctx: Number(els.setNCtx.value),
    voice_input_enabled: els.setVoiceInputEnabled ? els.setVoiceInputEnabled.checked : true,
    voice_output_enabled: els.setVoiceOutputEnabled ? els.setVoiceOutputEnabled.checked : false,
    auto_submit_transcript: els.setAutoSubmitTranscript ? els.setAutoSubmitTranscript.checked : false,
    stop_speaking_on_input: els.setStopSpeakingOnInput ? els.setStopSpeakingOnInput.checked : true,
    wake_word_enabled: els.setWakeWordEnabled ? els.setWakeWordEnabled.checked : false,
    selected_microphone: micVal || "default",
    selected_speaker: spkVal || "default",
    selected_tts_voice: els.setSelectedTtsVoice ? els.setSelectedTtsVoice.value : "en_GB-alan-medium",
    listening_duration: els.setListeningDuration ? Number(els.setListeningDuration.value) : 8.0,
    speech_speed: els.setSpeechSpeed ? Number(els.setSpeechSpeed.value) : 1.0,
    speech_volume: els.setSpeechVolume ? Number(els.setSpeechVolume.value) : 1.0,
    wake_word_sensitivity: els.setWakeWordSensitivity ? Number(els.setWakeWordSensitivity.value) : 0.5,
    wake_word_timeout_seconds: els.setWakeWordTimeout ? Number(els.setWakeWordTimeout.value) : 10,
  };
}

function changedModelReloadFields(candidate) {
  const a = settingsState.active || {};
  const fields = [];
  if (candidate.model_path !== a.model_path) fields.push("model_path");
  if (String(candidate.n_gpu_layers) !== String(a.n_gpu_layers)) fields.push("n_gpu_layers");
  if (Number(candidate.n_ctx) !== Number(a.n_ctx)) fields.push("n_ctx");
  return fields;
}

async function submitSettingsUpdate(options = {}) {
  els.settingsFormError.hidden = true;
  els.settingsFormOk.hidden = true;
  const candidate = collectSettingsFormValues();
  const reloadFields = changedModelReloadFields(candidate);

  if (reloadFields.length > 0 && !options.confirm_model_reload && !options.save_only) {
    els.confirmFieldsList.textContent = reloadFields.join(", ");
    els.settingsConfirmBox.hidden = false;
    return;
  }

  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      settings: candidate,
      confirm_model_reload: Boolean(options.confirm_model_reload),
      apply_model_reload: Boolean(options.apply_model_reload),
      save_only: Boolean(options.save_only),
    }),
  });
  if (!res.ok) {
    els.settingsFormError.textContent = await errorText(res);
    els.settingsFormError.hidden = false;
    return;
  }
  settingsState = await res.json();
  applyRuntimeIndicators(settingsState.active, settingsState.reload_required);
  populateSettingsForm(settingsState);
  els.settingsFormOk.textContent = options.apply_model_reload
    ? "Settings saved and model reloaded."
    : options.save_only
    ? "Settings saved. Model reload deferred until you confirm or restart."
    : "Settings saved and applied.";
  els.settingsFormOk.hidden = false;
}

els.settingsForm.addEventListener("submit", (e) => {
  e.preventDefault();
  submitSettingsUpdate();
});

els.saveWithoutReloadBtn.addEventListener("click", () => {
  submitSettingsUpdate({ save_only: true });
});

els.saveAndReloadBtn.addEventListener("click", () => {
  submitSettingsUpdate({ confirm_model_reload: true, apply_model_reload: true });
});

els.reloadModelNowBtn.addEventListener("click", async () => {
  els.settingsFormError.hidden = true;
  const res = await fetch("/api/settings/reload-model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true }),
  });
  if (!res.ok) {
    els.settingsFormError.textContent = await errorText(res);
    els.settingsFormError.hidden = false;
    return;
  }
  settingsState = await res.json();
  applyRuntimeIndicators(settingsState.active, settingsState.reload_required);
  populateSettingsForm(settingsState);
  els.settingsFormOk.textContent = "Model reloaded with saved settings.";
  els.settingsFormOk.hidden = false;
});

els.openSettingsBtn.addEventListener("click", async () => {
  await refreshVoiceDevicesAndVoices();
  const st = await fetchSettings();
  populateSettingsForm(st);
  openModal(els.settingsModal);
});

/* ---- Phase 3: Tools, Workspace & Network Mode -------------------------- */
if (els.networkModeIndicator) {
  els.networkModeIndicator.addEventListener("click", async () => {
    const currentlyOffline = settingsState.active?.offline_mode !== false;
    const res = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offline_mode: !currentlyOffline }),
    });
    if (res.ok) {
      settingsState = await res.json();
      applyRuntimeIndicators(settingsState.active, settingsState.reload_required);
    }
  });
}

async function refreshToolsModalData() {
  try {
    const res = await fetch("/api/tools?limit=30");
    if (!res.ok) return;
    const data = await res.json();
    const pending = data.pending_approvals || [];
    updatePendingApprovalsBadge(pending.length);

    if (els.toolsModalModeStatus) {
      const isOff = data.offline_mode !== false;
      els.toolsModalModeStatus.textContent = isOff
        ? "Mode: Offline (Local tools enabled; External web research blocked)"
        : "Mode: Online (Local tools + SSRF-guarded Web Research enabled)";
    }

    // Pending approvals
    if (els.pendingApprovalsSection && els.modalApprovalsList) {
      els.modalApprovalsList.replaceChildren();
      els.pendingApprovalsSection.hidden = pending.length === 0;
      for (const app of pending) {
        els.modalApprovalsList.append(buildApprovalCard(app, () => refreshToolsModalData()));
      }
    }

    // Workspace files
    const wsFiles = data.workspace?.files || [];
    if (els.workspaceFilesList && els.workspaceFilesEmpty) {
      els.workspaceFilesList.replaceChildren();
      els.workspaceFilesEmpty.hidden = wsFiles.length > 0;
      for (const f of wsFiles) {
        const li = document.createElement("li");
        li.className = "workspace-file-item";
        const top = document.createElement("div");
        top.className = "item-top-row";
        const nameSpan = document.createElement("span");
        nameSpan.textContent = `workspace/${f.path}`;
        const sizeSpan = document.createElement("span");
        sizeSpan.className = "perm-badge";
        sizeSpan.textContent = `${f.size_bytes} bytes`;
        top.append(nameSpan, sizeSpan);

        const sub = document.createElement("div");
        sub.className = "item-sub-row";
        sub.textContent = `Modified: ${f.modified_at}`;
        li.append(top, sub);
        els.workspaceFilesList.append(li);
      }
    }

    // Recent executions
    const execs = data.executions || [];
    if (els.toolExecutionsList && els.toolExecutionsEmpty) {
      els.toolExecutionsList.replaceChildren();
      els.toolExecutionsEmpty.hidden = execs.length > 0;
      for (const ex of execs) {
        const li = document.createElement("li");
        li.className = "tool-exec-item";
        const top = document.createElement("div");
        top.className = "item-top-row";
        const title = document.createElement("span");
        const dur = ex.duration_ms != null ? ` (${Number(ex.duration_ms).toFixed(1)} ms)` : "";
        title.textContent = `${ex.tool_name} · ${ex.status}${dur}`;
        const lvl = document.createElement("span");
        lvl.className = "perm-badge";
        lvl.dataset.level = ex.permission_level;
        lvl.textContent = ex.permission_level;
        top.append(title, lvl);

        const sub = document.createElement("div");
        sub.className = "item-sub-row";
        const argsStr = JSON.stringify(ex.arguments || {});
        const outcome = ex.error ? `Error: ${ex.error}` : (ex.result_summary || "");
        sub.textContent = `Args: ${argsStr}${outcome ? " · " + outcome : ""}`;
        li.append(top, sub);
        els.toolExecutionsList.append(li);
      }
    }

    // Registered tool catalog
    const catalog = data.tools || [];
    if (els.toolCatalogList) {
      els.toolCatalogList.replaceChildren();
      for (const t of catalog) {
        const li = document.createElement("li");
        li.className = "tool-catalog-item";
        const top = document.createElement("div");
        top.className = "item-top-row";
        const name = document.createElement("span");
        name.textContent = t.name;
        const badge = document.createElement("span");
        badge.className = "perm-badge";
        badge.dataset.level = t.permission_level;
        badge.textContent = t.permission_label || t.permission_level;
        top.append(name, badge);

        const desc = document.createElement("div");
        desc.className = "item-sub-row";
        desc.textContent = `${t.description} (timeout: ${t.timeout_s}s)`;
        li.append(top, desc);
        els.toolCatalogList.append(li);
      }
    }
  } catch {
    // ignore transient error
  }
}

if (els.openToolsBtn) {
  els.openToolsBtn.addEventListener("click", async () => {
    await refreshToolsModalData();
    openModal(els.toolsModal);
  });
}
if (els.refreshToolsBtn) {
  els.refreshToolsBtn.addEventListener("click", refreshToolsModalData);
}

/* ---- Tasks, Scheduler & Notifications (Phase 4) ------------------------ */
let activeTaskFilter = "all";
let pendingCancelTaskId = null;

function updateTaskToolVisibility() {
  if (!els.taskTypeInput || !els.taskToolConfig) return;
  const ttype = els.taskTypeInput.value;
  els.taskToolConfig.hidden = !(ttype === "local_task" || ttype === "research_task");
  if (ttype === "research_task" && els.taskToolNameInput) {
    if (!["fetch_webpage", "web_search"].includes(els.taskToolNameInput.value)) {
      els.taskToolNameInput.value = "web_search";
      els.taskToolArgsInput.value = '{"query": "Python 3.12 zoneinfo"}';
    }
  }
}

if (els.taskTypeInput) {
  els.taskTypeInput.addEventListener("change", updateTaskToolVisibility);
}

async function refreshTasksModalData() {
  if (!els.tasksList) return;
  try {
    const fgParam = activeTaskFilter && activeTaskFilter !== "all" ? `&filter_group=${encodeURIComponent(activeTaskFilter)}` : "";
    const res = await fetch(`/api/tasks?include_executions=true&limit=100${fgParam}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.system_timezone && els.tasksTimezoneLabel) {
      els.tasksTimezoneLabel.textContent = `Timezone: ${data.system_timezone}`;
    }
    renderTasksList(data.tasks || []);
  } catch {
    // ignore transient error
  }
}

function renderTasksList(tasks) {
  if (!els.tasksList || !els.tasksEmpty) return;
  els.tasksList.replaceChildren();
  els.tasksEmpty.hidden = tasks.length > 0;
  for (const t of tasks) {
    const li = document.createElement("li");
    li.className = "task-card";
    li.dataset.state = t.state;
    li.dataset.taskId = t.id;

    const header = document.createElement("div");
    header.className = "task-card-header";
    const titleEl = document.createElement("div");
    titleEl.className = "task-card-title";
    titleEl.textContent = t.title;

    const badges = document.createElement("div");
    badges.className = "task-badges";
    const typePill = document.createElement("span");
    typePill.className = "task-type-pill";
    typePill.textContent = t.task_type;
    const statePill = document.createElement("span");
    statePill.className = "task-state-pill";
    statePill.dataset.state = t.state;
    statePill.textContent = t.state;
    badges.append(typePill, statePill);
    header.append(titleEl, badges);
    li.append(header);

    if (t.description) {
      const desc = document.createElement("div");
      desc.className = "item-sub-row";
      desc.textContent = t.description;
      li.append(desc);
    }

    const metaGrid = document.createElement("div");
    metaGrid.className = "task-meta-grid";
    const recType = t.recurrence?.type || "none";
    const nextRun = t.next_run_at_local || t.scheduled_at_local || "—";
    metaGrid.innerHTML = "";
    const spans = [
      `Next run (${t.timezone}): ${nextRun}`,
      `Recurrence: ${recType}`,
      `Priority: ${t.priority}`,
      `Retries: ${t.retry_count}/${t.max_retries}`,
      `Timeout: ${t.timeout_seconds}s`,
    ];
    for (const sText of spans) {
      const sp = document.createElement("span");
      sp.textContent = sText;
      metaGrid.append(sp);
    }
    li.append(metaGrid);

    if (t.last_error || t.state === "AwaitingApproval" || t.state === "Paused" || t.state === "WaitingForUser") {
      const banner = document.createElement("div");
      banner.className = "task-reason-banner";
      if (t.state === "Failed") banner.dataset.error = "true";
      if (t.state === "AwaitingApproval" && t.approval) {
        banner.textContent = `Awaiting user approval: ${t.approval.explanation}`;
      } else if (t.last_error) {
        banner.textContent = t.last_error;
      } else {
        banner.textContent = `Task is currently in state ${t.state}.`;
      }
      li.append(banner);
    }

    const actions = document.createElement("div");
    actions.className = "task-card-actions";

    if (t.state === "AwaitingApproval") {
      const appBtn = document.createElement("button");
      appBtn.type = "button";
      appBtn.className = "btn btn-primary btn-sm";
      appBtn.textContent = "Approve";
      appBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/approve`, { method: "POST" });
        await refreshTasksModalData();
        pollStatus();
      });
      const rejBtn = document.createElement("button");
      rejBtn.type = "button";
      rejBtn.className = "btn btn-danger btn-sm";
      rejBtn.textContent = "Reject";
      rejBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "Rejected from Tasks panel" }),
        });
        await refreshTasksModalData();
        pollStatus();
      });
      actions.append(appBtn, rejBtn);
    }

    if (t.state === "Scheduled" || t.state === "Queued") {
      const runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.className = "btn btn-primary btn-sm";
      runBtn.textContent = "Run now";
      runBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/run`, { method: "POST" });
        await refreshTasksModalData();
        await refreshNotificationsModalData();
        pollStatus();
      });
      const pauseBtn = document.createElement("button");
      pauseBtn.type = "button";
      pauseBtn.className = "btn btn-secondary btn-sm";
      pauseBtn.textContent = "Pause";
      pauseBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/pause`, { method: "POST" });
        await refreshTasksModalData();
      });
      actions.append(runBtn, pauseBtn);
    }

    if (t.state === "Paused" || t.state === "WaitingForUser" || t.state === "Failed") {
      const resumeBtn = document.createElement("button");
      resumeBtn.type = "button";
      resumeBtn.className = "btn btn-primary btn-sm";
      resumeBtn.textContent = "Resume";
      resumeBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/resume`, { method: "POST" });
        await refreshTasksModalData();
      });
      actions.append(resumeBtn);
    }

    if ((t.task_type === "reminder" || t.task_type === "followup") && t.state !== "Cancelled") {
      if (t.state !== "Completed") {
        const doneBtn = document.createElement("button");
        doneBtn.type = "button";
        doneBtn.className = "btn btn-secondary btn-sm";
        doneBtn.textContent = "Complete";
        doneBtn.addEventListener("click", async () => {
          await fetch(`/api/tasks/${t.id}/complete`, { method: "POST" });
          await refreshTasksModalData();
          pollStatus();
        });
        actions.append(doneBtn);
      }
      const snoozeBtn = document.createElement("button");
      snoozeBtn.type = "button";
      snoozeBtn.className = "btn btn-secondary btn-sm";
      snoozeBtn.textContent = "Snooze +10m";
      snoozeBtn.addEventListener("click", async () => {
        await fetch(`/api/tasks/${t.id}/snooze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ minutes: 10 }),
        });
        await refreshTasksModalData();
      });
      actions.append(snoozeBtn);
    }

    if (!["Completed", "Cancelled"].includes(t.state)) {
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btn-danger btn-sm";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", () => {
        pendingCancelTaskId = t.id;
        if (els.cancelTaskModalDesc) {
          els.cancelTaskModalDesc.textContent =
            t.state === "Running"
              ? `Task "${t.title}" is currently RUNNING. Cancelling will abort its active execution. Confirm cancellation?`
              : `Cancel task "${t.title}"? Once cancelled, it will not run.`;
        }
        openModal(els.cancelTaskModal);
      });
      actions.append(cancelBtn);
    }

    const execs = t.executions || [];
    const execDrawer = document.createElement("div");
    execDrawer.className = "task-executions-drawer";
    execDrawer.hidden = true;
    for (const ex of execs) {
      const row = document.createElement("div");
      row.className = "task-exec-row";
      const dur = ex.resource_usage?.duration_ms != null ? ` (${ex.resource_usage.duration_ms} ms)` : "";
      row.textContent = `[${ex.outcome}${dur}] Started: ${ex.started_at} · Retry #${ex.retry_number} · ${ex.output_summary || ex.error_details || ""}`;
      execDrawer.append(row);
    }

    if (execs.length > 0) {
      const histBtn = document.createElement("button");
      histBtn.type = "button";
      histBtn.className = "btn btn-secondary btn-sm";
      histBtn.textContent = `History (${execs.length})`;
      histBtn.addEventListener("click", () => {
        execDrawer.hidden = !execDrawer.hidden;
      });
      actions.append(histBtn);
    }

    li.append(actions, execDrawer);
    els.tasksList.append(li);
  }
}

document.querySelectorAll("[data-task-filter]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    activeTaskFilter = btn.dataset.taskFilter || "all";
    document.querySelectorAll("[data-task-filter]").forEach((b) => {
      const isSelected = b === btn;
      b.classList.toggle("active", isSelected);
      b.setAttribute("aria-selected", String(isSelected));
    });
    await refreshTasksModalData();
  });
});

if (els.confirmCancelTaskBtn) {
  els.confirmCancelTaskBtn.addEventListener("click", async () => {
    if (!pendingCancelTaskId) return;
    const tid = pendingCancelTaskId;
    pendingCancelTaskId = null;
    closeModal(els.cancelTaskModal);
    await fetch(`/api/tasks/${tid}/cancel?confirm=true`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true, reason: "Cancelled by user after confirmation" }),
    });
    await refreshTasksModalData();
    pollStatus();
  });
}

if (els.taskCreateForm) {
  els.taskCreateForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (els.taskFormError) els.taskFormError.hidden = true;
    const title = els.taskTitleInput.value.trim();
    const taskType = els.taskTypeInput.value;
    const schedRaw = els.taskScheduledInput.value;
    const recType = els.taskRecurrenceInput.value;
    const priority = parseInt(els.taskPriorityInput.value, 10) || 3;
    const description = els.taskDescInput.value.trim();

    let scheduledAt = schedRaw ? schedRaw : null;
    if (!scheduledAt && (taskType === "reminder" || taskType === "followup")) {
      const dt = new Date(Date.now() + 3600 * 1000);
      scheduledAt = dt.toISOString();
    }

    let action = {};
    if (taskType === "local_task" || taskType === "research_task") {
      try {
        const parsedArgs = JSON.parse(els.taskToolArgsInput.value || "{}");
        action = {
          tool_name: els.taskToolNameInput.value,
          arguments: parsedArgs,
        };
      } catch (err) {
        if (els.taskFormError) {
          els.taskFormError.textContent = `Invalid Tool Arguments JSON: ${err.message}`;
          els.taskFormError.hidden = false;
        }
        return;
      }
    }

    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          description,
          task_type: taskType,
          priority,
          scheduled_at: scheduledAt,
          recurrence: { type: recType, interval: 1 },
          action,
          conversation_id: conversationId,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to schedule task");
      }
      els.taskTitleInput.value = "";
      els.taskDescInput.value = "";
      await refreshTasksModalData();
      await refreshNotificationsModalData();
      pollStatus();
    } catch (err) {
      if (els.taskFormError) {
        els.taskFormError.textContent = err.message;
        els.taskFormError.hidden = false;
      }
    }
  });
}

if (els.openTasksBtn) {
  els.openTasksBtn.addEventListener("click", async () => {
    await refreshTasksModalData();
    openModal(els.tasksModal);
  });
}
if (els.refreshTasksBtn) {
  els.refreshTasksBtn.addEventListener("click", refreshTasksModalData);
}
if (els.tickSchedulerBtn) {
  els.tickSchedulerBtn.addEventListener("click", async () => {
    await fetch("/api/scheduler/tick", { method: "POST" });
    await refreshTasksModalData();
    await refreshNotificationsModalData();
    pollStatus();
  });
}

async function refreshNotificationsModalData() {
  if (!els.notificationsList) return;
  try {
    const unreadOnly = Boolean(els.notificationsUnreadOnly?.checked);
    const res = await fetch(`/api/notifications?unread_only=${unreadOnly}&limit=100`);
    if (!res.ok) return;
    const data = await res.json();
    if (typeof data.unread_count === "number" && els.unreadNotificationsBadge) {
      els.unreadNotificationsBadge.textContent = String(data.unread_count);
      els.unreadNotificationsBadge.hidden = !(data.unread_count > 0);
    }
    const items = data.notifications || [];
    els.notificationsList.replaceChildren();
    if (els.notificationsEmpty) els.notificationsEmpty.hidden = items.length > 0;

    for (const n of items) {
      const li = document.createElement("li");
      li.className = "notification-card";
      li.dataset.unread = String(!n.read);

      const header = document.createElement("div");
      header.className = "notification-card-header";
      const title = document.createElement("div");
      title.className = "task-card-title";
      title.textContent = n.title;
      const badge = document.createElement("span");
      badge.className = "task-type-pill";
      badge.textContent = `${n.kind} · ${n.severity}`;
      header.append(title, badge);

      const body = document.createElement("div");
      body.className = "notification-card-body";
      body.textContent = n.message;

      const meta = document.createElement("div");
      meta.className = "notification-card-meta";
      meta.textContent = `Created: ${n.created_at}${n.read ? " · Read" : " · Unread"}`;

      const actions = document.createElement("div");
      actions.className = "notification-card-actions";
      if (!n.read) {
        const readBtn = document.createElement("button");
        readBtn.type = "button";
        readBtn.className = "btn btn-secondary btn-sm";
        readBtn.textContent = "Mark as read";
        readBtn.addEventListener("click", async () => {
          await fetch(`/api/notifications/${n.id}/read`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ read: true }),
          });
          await refreshNotificationsModalData();
        });
        actions.append(readBtn);
      }

      const dismissBtn = document.createElement("button");
      dismissBtn.type = "button";
      dismissBtn.className = "btn btn-secondary btn-sm";
      dismissBtn.textContent = "Dismiss";
      dismissBtn.addEventListener("click", async () => {
        await fetch(`/api/notifications/${n.id}/dismiss`, { method: "POST" });
        await refreshNotificationsModalData();
      });
      actions.append(dismissBtn);

      if (n.task_id) {
        const viewTaskBtn = document.createElement("button");
        viewTaskBtn.type = "button";
        viewTaskBtn.className = "btn btn-primary btn-sm";
        viewTaskBtn.textContent = "View related task";
        viewTaskBtn.addEventListener("click", async () => {
          closeModal(els.notificationsModal);
          activeTaskFilter = "all";
          await refreshTasksModalData();
          openModal(els.tasksModal);
        });
        actions.append(viewTaskBtn);
      }

      li.append(header, body, meta, actions);
      els.notificationsList.append(li);
    }
  } catch {
    // ignore transient error
  }
}

if (els.openNotificationsBtn) {
  els.openNotificationsBtn.addEventListener("click", async () => {
    await refreshNotificationsModalData();
    openModal(els.notificationsModal);
  });
}
if (els.refreshNotificationsBtn) {
  els.refreshNotificationsBtn.addEventListener("click", refreshNotificationsModalData);
}
if (els.notificationsUnreadOnly) {
  els.notificationsUnreadOnly.addEventListener("change", refreshNotificationsModalData);
}
if (els.markAllNotificationsReadBtn) {
  els.markAllNotificationsReadBtn.addEventListener("click", async () => {
    await fetch("/api/notifications/read-all", { method: "POST" });
    await refreshNotificationsModalData();
  });
}

/* ---- Phase 5: Local Voice Input, Speech Output & Wake-Word ------------ */
let currentVoiceState = "Idle";
let recordingTimerInterval = null;
let recordingSecondsElapsed = 0;
let activeRecordAbortController = null;

const VOICE_STATE_LABELS = {
  Idle: "Voice: Idle",
  Listening: "Voice: Listening (Wake-Word)",
  Recording: "Voice: Recording",
  Transcribing: "Voice: Transcribing",
  WaitingForConfirmation: "Voice: Waiting for confirmation",
  Thinking: "Voice: Thinking",
  Speaking: "Voice: Speaking",
  Interrupted: "Voice: Interrupted",
  Error: "Voice: Error",
};

function setVoiceUiState(state, detail = "") {
  currentVoiceState = state || "Idle";
  if (els.voiceStatePill && els.voiceStateText) {
    els.voiceStatePill.dataset.voiceState = currentVoiceState;
    els.voiceStateText.textContent = VOICE_STATE_LABELS[currentVoiceState] || `Voice: ${currentVoiceState}`;
  }
  if (els.topbarStopSpeakingBtn) {
    els.topbarStopSpeakingBtn.hidden = currentVoiceState !== "Speaking";
  }
  if (els.voiceSpeakingBar) {
    els.voiceSpeakingBar.hidden = currentVoiceState !== "Speaking";
    if (detail && els.voiceSpeakingText && currentVoiceState === "Speaking") {
      els.voiceSpeakingText.textContent = detail;
    }
  }
  if (els.micBtn) {
    const isRec = currentVoiceState === "Recording" || currentVoiceState === "Transcribing";
    els.micBtn.dataset.recording = String(isRec);
    if (els.micBtnLabel) {
      els.micBtnLabel.textContent = currentVoiceState === "Recording" ? "Recording…" : "Voice";
    }
  }
  if (currentVoiceState === "Error" && detail) {
    showVoiceError(detail);
  }
}

function showVoiceError(msg) {
  if (!els.voiceErrorBar || !els.voiceErrorText) return;
  els.voiceErrorText.textContent = msg;
  els.voiceErrorBar.hidden = false;
}

function hideVoiceError() {
  if (!els.voiceErrorBar) return;
  els.voiceErrorBar.hidden = true;
  if (els.voiceErrorText) els.voiceErrorText.textContent = "";
}

if (els.voiceErrorDismissBtn) {
  els.voiceErrorDismissBtn.addEventListener("click", () => {
    hideVoiceError();
    if (currentVoiceState === "Error") {
      setVoiceUiState(settingsState.active?.wake_word_enabled ? "Listening" : "Idle");
    }
  });
}

function formatRecordingTimer(sec, maxSec) {
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(Math.floor(sec % 60)).padStart(2, "0");
  const maxM = String(Math.floor(maxSec / 60)).padStart(2, "0");
  const maxS = String(Math.floor(maxSec % 60)).padStart(2, "0");
  return `${mm}:${ss} / ${maxM}:${maxS}`;
}

function stopRecordingTimer() {
  if (recordingTimerInterval) {
    clearInterval(recordingTimerInterval);
    recordingTimerInterval = null;
  }
}

async function startVoiceRecording() {
  hideVoiceError();
  if (settingsState.active?.voice_input_enabled === false) {
    showVoiceError("Voice input is currently disabled in Settings.");
    return;
  }
  if (settingsState.active?.stop_speaking_on_input !== false && currentVoiceState === "Speaking") {
    await stopSpeaking();
  }
  const durationSec = Number(settingsState.active?.listening_duration || 5.0);
  recordingSecondsElapsed = 0;
  setVoiceUiState("Recording");
  if (els.voiceRecordingBar) els.voiceRecordingBar.hidden = false;
  if (els.voiceRecordingLabel) els.voiceRecordingLabel.textContent = "Recording microphone…";
  if (els.voiceRecordingTimer) {
    els.voiceRecordingTimer.textContent = formatRecordingTimer(0, durationSec);
  }

  stopRecordingTimer();
  recordingTimerInterval = setInterval(() => {
    recordingSecondsElapsed += 0.25;
    if (els.voiceRecordingTimer) {
      els.voiceRecordingTimer.textContent = formatRecordingTimer(
        Math.min(recordingSecondsElapsed, durationSec),
        durationSec
      );
    }
    if (recordingSecondsElapsed >= durationSec && els.voiceRecordingLabel) {
      els.voiceRecordingLabel.textContent = "Transcribing audio on CPU…";
      setVoiceUiState("Transcribing");
    }
  }, 250);

  activeRecordAbortController = new AbortController();
  try {
    const res = await fetch("/api/voice/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ duration_seconds: durationSec }),
      signal: activeRecordAbortController.signal,
    });
    stopRecordingTimer();
    if (els.voiceRecordingBar) els.voiceRecordingBar.hidden = true;

    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || "Microphone recording or transcription failed.";
      setVoiceUiState("Error", msg);
      return;
    }
    handleTranscriptionResult(data);
  } catch (err) {
    stopRecordingTimer();
    if (els.voiceRecordingBar) els.voiceRecordingBar.hidden = true;
    if (err.name === "AbortError") {
      setVoiceUiState("Interrupted");
    } else {
      setVoiceUiState("Error", err.message || "Voice recording failed.");
    }
  } finally {
    activeRecordAbortController = null;
  }
}

function handleTranscriptionResult(data) {
  const transcript = (data.text || data.transcript || "").trim();
  if (!transcript) {
    setVoiceUiState("Error", "No speech was recognized in the recording.");
    return;
  }
  if (data.auto_submit || settingsState.active?.auto_submit_transcript) {
    if (els.voiceTranscriptBar) els.voiceTranscriptBar.hidden = true;
    send(transcript);
    return;
  }
  setVoiceUiState("WaitingForConfirmation");
  if (els.voiceTranscriptInput) {
    els.voiceTranscriptInput.value = transcript;
  }
  if (els.voiceConfidenceBadge) {
    const conf = data.confidence || {};
    const logprob = conf.avg_logprob != null ? `avg_logprob: ${conf.avg_logprob}` : "uncalibrated";
    els.voiceConfidenceBadge.textContent = `Confidence (${logprob} · uncalibrated)`;
  }
  if (els.voiceTranscriptBar) {
    els.voiceTranscriptBar.hidden = false;
    els.voiceTranscriptInput?.focus();
  }
}

async function stopVoiceRecordingEarly() {
  if (els.voiceRecordingLabel) {
    els.voiceRecordingLabel.textContent = "Stopping & transcribing…";
  }
  setVoiceUiState("Transcribing");
  try {
    await fetch("/api/voice/stop-recording", { method: "POST" });
  } catch {
    // ignore
  }
}

async function cancelVoiceRecording() {
  stopRecordingTimer();
  activeRecordAbortController?.abort();
  if (els.voiceRecordingBar) els.voiceRecordingBar.hidden = true;
  if (els.voiceTranscriptBar) els.voiceTranscriptBar.hidden = true;
  try {
    await fetch("/api/voice/cancel", { method: "POST" });
  } catch {
    // ignore
  }
  setVoiceUiState("Interrupted");
}

async function submitConfirmedTranscript() {
  if (!els.voiceTranscriptInput) return;
  const text = els.voiceTranscriptInput.value.trim();
  if (!text) {
    showVoiceError("Transcript cannot be empty. Edit the text or click Cancel.");
    return;
  }
  try {
    await fetch("/api/voice/transcript/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch {
    // ignore non-critical sync error
  }
  if (els.voiceTranscriptBar) els.voiceTranscriptBar.hidden = true;
  els.voiceTranscriptInput.value = "";
  await send(text);
}

async function discardPendingTranscript() {
  if (els.voiceTranscriptBar) els.voiceTranscriptBar.hidden = true;
  if (els.voiceTranscriptInput) els.voiceTranscriptInput.value = "";
  try {
    await fetch("/api/voice/cancel", { method: "POST" });
  } catch {
    // ignore
  }
  setVoiceUiState(settingsState.active?.wake_word_enabled ? "Listening" : "Idle");
}

async function speakText(text) {
  if (!text || !text.trim()) return;
  hideVoiceError();
  const preview = text.length > 60 ? `${text.slice(0, 60)}…` : text;
  setVoiceUiState("Speaking", `Speaking: "${preview}"`);
  try {
    const res = await fetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text.trim(),
        force: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      setVoiceUiState("Error", data.detail || "Text-to-speech playback failed.");
      return;
    }
    if (data.interrupted) {
      setVoiceUiState("Interrupted");
    } else {
      setVoiceUiState(settingsState.active?.wake_word_enabled ? "Listening" : "Idle");
    }
  } catch (err) {
    setVoiceUiState("Error", err.message || "Text-to-speech error.");
  }
}

async function stopSpeaking() {
  try {
    await fetch("/api/voice/stop-speaking", { method: "POST" });
  } catch {
    // ignore
  }
  setVoiceUiState("Interrupted");
}

async function refreshVoiceDevicesAndVoices() {
  try {
    const [devRes, stRes] = await Promise.all([
      fetch("/api/voice/devices"),
      fetch("/api/voice/status"),
    ]);
    if (devRes.ok) {
      const devData = await devRes.json();
      if (els.setSelectedMicrophone) {
        const prevMic = els.setSelectedMicrophone.value;
        els.setSelectedMicrophone.replaceChildren();
        const defOpt = document.createElement("option");
        defOpt.value = "default";
        defOpt.textContent = "System Default Microphone";
        els.setSelectedMicrophone.append(defOpt);
        for (const mic of devData.microphones || []) {
          const opt = document.createElement("option");
          opt.value = String(mic.id);
          opt.textContent = `[#${mic.id}] ${mic.name}${mic.is_default ? " (Default)" : ""}`;
          els.setSelectedMicrophone.append(opt);
        }
        if (prevMic) els.setSelectedMicrophone.value = prevMic;
      }
      if (els.setSelectedSpeaker) {
        const prevSpk = els.setSelectedSpeaker.value;
        els.setSelectedSpeaker.replaceChildren();
        const defOpt = document.createElement("option");
        defOpt.value = "default";
        defOpt.textContent = "System Default Speaker";
        els.setSelectedSpeaker.append(defOpt);
        for (const spk of devData.speakers || []) {
          const opt = document.createElement("option");
          opt.value = String(spk.id);
          opt.textContent = `[#${spk.id}] ${spk.name}${spk.is_default ? " (Default)" : ""}`;
          els.setSelectedSpeaker.append(opt);
        }
        if (prevSpk) els.setSelectedSpeaker.value = prevSpk;
      }
    }
    if (stRes.ok) {
      const stData = await stRes.json();
      syncVoiceStatusFromServer(stData);
      const voices = stData.models?.tts?.voices || [];
      if (els.setSelectedTtsVoice && voices.length > 0) {
        const prevVoice = els.setSelectedTtsVoice.value;
        els.setSelectedTtsVoice.replaceChildren();
        for (const v of voices) {
          const opt = document.createElement("option");
          opt.value = v.id;
          opt.textContent = `${v.name} (${v.sample_rate || 22050} Hz)`;
          els.setSelectedTtsVoice.append(opt);
        }
        if (prevVoice) els.setSelectedTtsVoice.value = prevVoice;
      }
    }
  } catch {
    // ignore transient device query error
  }
}

function syncVoiceStatusFromServer(voiceStatus) {
  if (!voiceStatus) return;
  const wakeStatus = voiceStatus.wake_word || {};
  const wakeEnabled = Boolean(wakeStatus.enabled);
  if (els.wakeWordIndicator && els.wakeWordIndicatorText) {
    els.wakeWordIndicator.dataset.enabled = String(wakeEnabled);
    els.wakeWordIndicatorText.textContent = wakeEnabled ? "Wake: On" : "Wake: Off";
  }
  if (
    !busy &&
    currentVoiceState !== "Recording" &&
    currentVoiceState !== "Transcribing" &&
    currentVoiceState !== "WaitingForConfirmation" &&
    currentVoiceState !== "Speaking"
  ) {
    if (voiceStatus.state && VOICE_STATE_LABELS[voiceStatus.state]) {
      setVoiceUiState(voiceStatus.state);
    }
  }
}

if (els.micBtn) {
  els.micBtn.addEventListener("click", () => {
    if (currentVoiceState === "Recording") {
      stopVoiceRecordingEarly();
    } else {
      startVoiceRecording();
    }
  });
}
if (els.voiceStopRecordBtn) {
  els.voiceStopRecordBtn.addEventListener("click", stopVoiceRecordingEarly);
}
if (els.voiceCancelRecordBtn) {
  els.voiceCancelRecordBtn.addEventListener("click", cancelVoiceRecording);
}
if (els.voiceSubmitTranscriptBtn) {
  els.voiceSubmitTranscriptBtn.addEventListener("click", submitConfirmedTranscript);
}
if (els.voiceDiscardTranscriptBtn) {
  els.voiceDiscardTranscriptBtn.addEventListener("click", discardPendingTranscript);
}
if (els.voiceTranscriptInput) {
  els.voiceTranscriptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitConfirmedTranscript();
    } else if (e.key === "Escape") {
      e.preventDefault();
      discardPendingTranscript();
    }
  });
}
if (els.voiceStopSpeakingBtn) {
  els.voiceStopSpeakingBtn.addEventListener("click", stopSpeaking);
}
if (els.topbarStopSpeakingBtn) {
  els.topbarStopSpeakingBtn.addEventListener("click", stopSpeaking);
}
if (els.wakeWordIndicator) {
  els.wakeWordIndicator.addEventListener("click", async () => {
    const currentlyOn = els.wakeWordIndicator.dataset.enabled === "true";
    try {
      const res = await fetch("/api/voice/wake-word", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentlyOn }),
      });
      const data = await res.json();
      if (!res.ok) {
        showVoiceError(data.detail || "Could not toggle wake-word detection.");
        return;
      }
      await fetchSettings();
      if (data.enabled) {
        setVoiceUiState("Listening");
      } else {
        setVoiceUiState("Idle");
      }
    } catch (err) {
      showVoiceError(err.message || "Wake-word request failed.");
    }
  });
}
if (els.refreshAudioDevicesBtn) {
  els.refreshAudioDevicesBtn.addEventListener("click", refreshVoiceDevicesAndVoices);
}
if (els.testTtsVoiceBtn) {
  els.testTtsVoiceBtn.addEventListener("click", () => {
    speakText("Hello, I am Jarvis. Local voice synthesis is ready.");
  });
}

/* ---- composer & startup ------------------------------------------------ */
function autoGrow() {
  els.prompt.style.height = "auto";
  els.prompt.style.height = `${els.prompt.scrollHeight + 2}px`;
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.prompt.value.trim();
  if (!text || busy || !modelReady) return;
  els.prompt.value = "";
  autoGrow();
  send(text);
});

els.prompt.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});
els.prompt.addEventListener("input", () => {
  autoGrow();
  if (settingsState.active?.stop_speaking_on_input !== false && currentVoiceState === "Speaking") {
    stopSpeaking();
  }
});
els.stop.addEventListener("click", () => {
  controller?.abort();
  fetch("/api/tools/cancel", { method: "POST" }).catch(() => {});
});
els.newChat.addEventListener("click", startFreshView);
els.newChatSidebar.addEventListener("click", startFreshView);

async function bootstrap() {
  pollStatus();
  const st = await fetchSettings();
  await refreshMemories();
  await refreshToolsModalData();
  await refreshTasksModalData();
  await refreshNotificationsModalData();
  const convs = await refreshConversations();
  if (
    st.active?.default_new_conversation_behavior === "resume_last" &&
    convs.length > 0 &&
    !conversationId &&
    history.length === 0
  ) {
    await selectConversation(convs[0].id);
  }
}

bootstrap();
