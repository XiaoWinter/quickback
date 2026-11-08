const MAX_MS = 3 * 60 * 1000;

const state = {
  hasKey: true,
  session: null,
  question: null,
  recording: false,
  busy: false,
  stream: null,
  recorder: null,
  chunks: [],
  startedAt: 0,
  timer: null,
  mimeType: "",
  pending: null,
  editingPrompt: null,
  editingTurn: null,
  retake: null,
  retell: null,
  retellNote: null,
  drill: null,
  coreNote: null,
  addingCore: null,
  coreDraft: null,
  fillingCore: false,
};

let questionToken = 0;

const practiceEl = document.getElementById("practice");
const historyEl = document.getElementById("history");
const questionsEl = document.getElementById("questions");
const bannerEl = document.getElementById("banner");
const topicEl = document.getElementById("topic");
const turnsEl = document.getElementById("turns");
const nextEl = document.getElementById("next-prompt");
const recordBtn = document.getElementById("record");
const finishBtn = document.getElementById("finish-practice");
const newBtn = document.getElementById("new-question");
const providerSelect = document.getElementById("provider");
const reviewEl = document.getElementById("take-review");
const PLAY_ICON = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4.1 2.15 10 6 4.1 9.85z" fill="currentColor"/></svg>';
const PAUSE_ICON = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2h2.1v8H3zm3.9 0H9v8H6.9z" fill="currentColor"/></svg>';

function setPlayIcon(button, playing) {
  button.innerHTML = playing ? PAUSE_ICON : PLAY_ICON;
  button.classList.toggle("is-playing", playing);
  button.setAttribute("aria-label", playing ? "暂停" : "播放");
}

const takeAudio = mountPlayer(document.getElementById("take-audio"), "", "metadata");
const sendTakeBtn = document.getElementById("send-take");
const redoTakeBtn = document.getElementById("redo-take");
const recorderEl = document.getElementById("recorder");
const cancelRetakeBtn = document.getElementById("cancel-retake");
const recHint = document.getElementById("rec-hint");
const takeHint = document.getElementById("take-hint");

function showBanner(text) {
  if (!text) {
    bannerEl.hidden = true;
    bannerEl.textContent = "";
    return;
  }
  bannerEl.hidden = false;
  bannerEl.textContent = text;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "请求失败");
  }
  return data;
}

function formatWhen(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
}

function formatClock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function retellButton(turn, label, role, level, chunk) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "text-btn";
  button.dataset.retell = String(turn.index);
  button.dataset.retellLevel = level || "";
  button.dataset.retellChunk = chunk == null || chunk === "" ? "" : String(chunk);
  if (role) button.dataset.retellRole = role;
  button.textContent = label;
  return button;
}

function coreActive(index, level, number, slot) {
  const current = state.drill;
  return Boolean(current)
    && state.recording
    && current.index === Number(index)
    && (current.level || "") === (level || "")
    && String(current.number) === String(number)
    && String(current.slot) === String(slot);
}

function coreAttempts(core) {
  if (Array.isArray(core.attempts)) return core.attempts;
  const old = core.attempt;
  if (old && String(old.transcript || "").trim()) return [old];
  return [];
}

function retellActive(index, level, chunk, sessionId) {
  const current = state.retell;
  const wanted = chunk == null || chunk === "" ? "" : String(chunk);
  const have = !current || current.chunk == null || current.chunk === "" ? "" : String(current.chunk);
  const session = sessionId || (state.session && state.session.id) || "";
  const currentSession = (current && current.sessionId) || (state.session && state.session.id) || "";
  return Boolean(current)
    && current.index === Number(index)
    && (current.level || "") === (level || "")
    && have === wanted
    && session === currentSession
    && state.recording;
}

function addLine(parent, className, text) {
  const p = document.createElement("p");
  p.className = className;
  p.textContent = text;
  parent.appendChild(p);
  return p;
}

function addLabel(parent, text, rank) {
  return addLine(parent, rank > 1 ? `block-label rank-${rank}` : "block-label", text);
}

function sectionHead(label, actions) {
  const head = document.createElement("div");
  head.className = "section-head";
  addLine(head, "kicker", label);
  if (actions) head.appendChild(actions);
  return head;
}

function sessionArchived() {
  return Boolean(state.session && state.session.closed);
}

function continueActions() {
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const again = document.createElement("button");
  again.type = "button";
  again.className = "text-btn";
  again.dataset.reopenPractice = "1";
  again.textContent = "继续回答";
  actions.appendChild(again);
  return actions;
}

function renderPrompt(target, label, prompt, kind) {
  target.replaceChildren();
  const archived = sessionArchived() && label === "本题";
  const editing = kind && state.editingPrompt === kind && !archived;
  const actions = editing ? null : (archived ? continueActions() : (kind ? promptActions(kind) : null));
  target.appendChild(sectionHead(label, actions));
  if (editing) {
    target.appendChild(promptEditor(kind));
    return;
  }
  addLine(target, "en-line", prompt.en || "");
  if (prompt.zh) addLine(target, "zh-line", prompt.zh);
}

function promptField(label, name, value, maxLength) {
  const wrap = document.createElement("label");
  wrap.textContent = label;
  const input = document.createElement("input");
  input.type = "text";
  input.name = name;
  input.value = value || "";
  input.required = true;
  input.maxLength = maxLength || 240;
  input.autocomplete = "off";
  input.addEventListener("input", () => {
    if (state.editDraft) state.editDraft[name] = input.value;
  });
  wrap.appendChild(input);
  return wrap;
}

function promptEditor(kind) {
  const draft = state.editDraft || { en: "", zh: "" };
  const box = document.createElement("div");
  box.className = "prompt-edit";
  box.append(promptField("英文", "en", draft.en), promptField("中文", "zh", draft.zh));
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "primary";
  save.dataset.promptSave = kind;
  save.textContent = "保存";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "text-btn";
  cancel.dataset.promptCancel = kind;
  cancel.textContent = "取消";
  actions.append(save, cancel);
  box.appendChild(actions);
  return box;
}

function promptActions(kind) {
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "text-btn";
  edit.dataset.promptEdit = kind;
  edit.textContent = "修改";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "text-btn";
  remove.dataset.promptDelete = kind;
  remove.textContent = "删除";
  actions.append(edit, remove);
  return actions;
}

function beginPromptEdit(kind, prompt) {
  state.editingTurn = null;
  state.retake = null;
  state.editingPrompt = kind;
  state.editDraft = { en: (prompt && prompt.en) || "", zh: (prompt && prompt.zh) || "" };
  renderPractice();
}

function renderTranscript(parent, turn) {
  const p = document.createElement("p");
  p.className = "transcript";
  const segments = turn.segments && turn.segments.length
    ? turn.segments
    : [{ lang: "other", text: turn.transcript || "" }];
  if (!segments[0].text) {
    addLine(parent, "meta", "还没有转写。");
    return;
  }
  segments.forEach((seg) => {
    const span = document.createElement("span");
    span.className = `seg ${seg.lang || "other"}`;
    span.textContent = seg.text;
    p.appendChild(span);
  });
  parent.appendChild(p);
}

const speechRequests = new Map();
const keptSpeech = new Set();

function forgetSpeech(sessionId, index) {
  const prefix = `${sessionId}:${index}`;
  for (const key of [...speechRequests.keys()]) {
    if (key === prefix || String(key).startsWith(`${prefix}:`)) speechRequests.delete(key);
  }
  for (const key of [...keptSpeech]) {
    if (key === prefix || key.startsWith(`${prefix}:`)) keptSpeech.delete(key);
  }
}

function forgetSessionSpeech(sessionId) {
  const prefix = `${sessionId}:`;
  for (const key of [...speechRequests.keys()]) {
    if (String(key).startsWith(prefix)) speechRequests.delete(key);
  }
  for (const key of [...keptSpeech]) {
    if (key.startsWith(prefix)) keptSpeech.delete(key);
  }
}

function mediaSrc(kind, turn, level) {
  const version = encodeURIComponent(turn.created_at || "");
  const tail = level ? `/${level}` : "";
  return `/api/sessions/${state.session.id}/${kind}/${turn.index}${tail}?v=${version}`;
}

function clock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const chunkRetell = {
  audio: null,
  button: null,
  stop() {
    if (this.audio) this.audio.pause();
    if (this.button) this.button.classList.remove("is-playing");
    this.button = null;
  },
  play(button, src) {
    if (this.button === button && this.audio && !this.audio.paused) {
      this.stop();
      return;
    }
    this.stop();
    if (window.QuickbackSlice) window.QuickbackSlice.stop();
    document.querySelectorAll(".player audio").forEach((other) => other.pause());
    if (!this.audio) {
      this.audio = new Audio();
      this.audio.preload = "auto";
      this.audio.addEventListener("ended", () => this.stop());
    }
    const same = this.audio.getAttribute("src") === src;
    this.button = button;
    button.classList.add("is-playing");
    if (!same) this.audio.src = src;
    else this.audio.currentTime = 0;
    this.audio.play().catch(() => this.stop());
  },
};
window.QuickbackChunkRetell = chunkRetell;

function mountPlayer(bar, src, preload, durationHint) {
  const hint = Number(durationHint) > 0 ? Number(durationHint) : 0;
  const audio = document.createElement("audio");
  audio.preload = preload || "metadata";
  const play = document.createElement("button");
  play.type = "button";
  play.className = "player-play";
  setPlayIcon(play, false);
  const range = document.createElement("input");
  range.type = "range";
  range.className = "player-range";
  range.min = "0";
  range.max = "0";
  range.step = "0.1";
  range.value = "0";
  range.setAttribute("aria-label", "进度");
  const time = document.createElement("span");
  time.className = "player-time";
  time.textContent = "0:00 / 0:00";
  bar.classList.add("player");
  bar.replaceChildren(audio, play, range, time);

  const usableDuration = () => {
    const live = audio.duration;
    if (Number.isFinite(live) && live > 0) return live;
    return hint;
  };
  const paint = () => {
    const duration = usableDuration();
    const nextMax = duration ? String(duration) : "0";
    if (range.max !== nextMax) range.max = nextMax;
    if (duration && document.activeElement !== range) {
      range.value = String(Math.min(audio.currentTime || 0, duration));
    }
    time.textContent = duration
      ? `${clock(audio.currentTime)} / ${clock(duration)}`
      : clock(audio.currentTime);
  };
  const setSrc = (url) => {
    audio.pause();
    if (url) audio.src = url;
    else {
      audio.removeAttribute("src");
      audio.load();
    }
    range.value = "0";
    range.max = "0";
    paint();
  };

  play.addEventListener("click", () => {
    if (!audio.paused) {
      audio.pause();
      return;
    }
    if (window.QuickbackSlice) window.QuickbackSlice.stop();
    chunkRetell.stop();
    document.querySelectorAll(".player audio").forEach((other) => {
      if (other !== audio) other.pause();
    });
    audio.play().catch(() => {});
  });
  audio.addEventListener("play", () => setPlayIcon(play, true));
  audio.addEventListener("pause", () => setPlayIcon(play, false));
  audio.addEventListener("loadedmetadata", paint);
  audio.addEventListener("durationchange", paint);
  audio.addEventListener("timeupdate", paint);
  range.addEventListener("input", () => {
    audio.currentTime = Number(range.value) || 0;
    paint();
  });
  audio.addEventListener("ended", () => {
    audio.currentTime = 0;
    range.value = "0";
    paint();
  });
  bar.setSrc = setSrc;
  if (src) setSrc(src);
  else paint();
  return bar;
}

window.QuickbackPlayer = {
  create(src, preload, durationHint) {
    return mountPlayer(document.createElement("div"), src, preload, durationHint);
  },
};

function renderSpeech(parent, turn, options) {
  const level = (options && options.level) || "";
  const text = options && options.text != null
    ? String(options.text).trim()
    : ((turn.feedback || {}).natural || "").trim();
  const file = options && Object.prototype.hasOwnProperty.call(options, "file") ? options.file : turn.speech_file;
  const key = level ? `${state.session.id}:${turn.index}:${level}` : `${state.session.id}:${turn.index}`;
  if (file) keptSpeech.add(key);
  if (file || keptSpeech.has(key)) {
    parent.appendChild(window.QuickbackPlayer.create(mediaSrc("speech", turn, level), "metadata"));
    return;
  }
  const note = addLine(parent, "note", "正在准备发音…");
  const sessionId = state.session.id;
  const index = turn.index;
  if (speechRequests.has(key)) return;
  const path = level
    ? `/api/sessions/${sessionId}/turns/${index}/speech/${level}`
    : `/api/sessions/${sessionId}/turns/${index}/speech`;
  const job = api(path, { method: "POST" })
    .then((session) => {
      if (!state.session || state.session.id !== sessionId) return;
      const current = (state.session.turns || []).find((item) => item.index === index);
      const feedback = (current && current.feedback) || {};
      const still = level
        ? ((((feedback.variants || []).find((item) => item.key === level)) || {}).en || "").trim()
        : (feedback.natural || "").trim();
      if (still !== text) return;
      state.session = session;
      if (!state.recording) renderPractice();
    })
    .catch((error) => {
      speechRequests.delete(key);
      if (note.isConnected) note.textContent = error.message;
    });
  speechRequests.set(key, job);
}

function addMeaning(parent, text) {
  const value = (text || "").trim();
  if (value) addLine(parent, "meaning", value);
}

function turnActions(index) {
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const retake = document.createElement("button");
  retake.type = "button";
  retake.className = "text-btn";
  retake.dataset.turnRetake = String(index);
  retake.textContent = state.retake === index ? "取消重答" : "重答";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "text-btn";
  edit.dataset.turnEdit = String(index);
  edit.textContent = "修改";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "text-btn";
  remove.dataset.turnDelete = String(index);
  remove.textContent = "删除";
  actions.append(retake, edit, remove);
  return actions;
}

function turnEditor() {
  const draft = state.editDraft || {};
  const box = document.createElement("div");
  box.className = "prompt-edit";
  box.append(promptField("问题英文", "en", draft.en), promptField("问题中文", "zh", draft.zh));
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "primary";
  save.dataset.turnSave = String(state.editingTurn);
  save.textContent = "保存";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "text-btn";
  cancel.dataset.turnCancel = "1";
  cancel.textContent = "取消";
  actions.append(save, cancel);
  box.appendChild(actions);
  return box;
}

function looseWords(text) {
  return (text || "").toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]+/g, " ").trim();
}

function shownChanges(turn, feedback) {
  const transcript = turn.transcript || "";
  if (looseWords(feedback.natural) && looseWords(feedback.natural) === looseWords(transcript)) return [];
  const words = looseWords(transcript);
  return (feedback.changes || []).filter((change) => {
    const spoken = looseWords(change.spoken);
    return spoken && words.includes(spoken);
  });
}

function renderTurn(turn) {
  const card = document.createElement("article");
  card.className = "card turn";
  card.dataset.turn = String(turn.index);
  if (state.retake === turn.index) card.classList.add("is-retake");
  const archived = sessionArchived();
  card.appendChild(sectionHead(`第 ${turn.index} 段`, archived || state.editingTurn === turn.index ? null : turnActions(turn.index)));
  card.appendChild(renderTurnQuestion(turn));

  const feedback = turn.feedback || null;
  const said = document.createElement("div");
  addLine(said, "block-label", "原版");
  renderTranscript(said, turn);
  if (feedback) addMeaning(said, feedback.spoken_zh);
  const seconds = turn.duration_ms ? ` · ${Math.round(turn.duration_ms / 1000)} 秒` : "";
  addLine(said, "meta", `${formatWhen(turn.created_at)}${seconds}`);
  said.appendChild(window.QuickbackPlayer.create(mediaSrc("audio", turn), "metadata", (turn.duration_ms || 0) / 1000));
  card.appendChild(said);

  if (turn.status === "error") {
    addLine(card, "error-line", turn.error || "这次没有生成反馈。");
    if (archived) return card;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry";
    retry.dataset.retry = String(turn.index);
    retry.textContent = "重试这一段";
    card.appendChild(retry);
    return card;
  }

  if (!feedback) return card;

  const natural = document.createElement("div");
  natural.className = "turn-blocks";
  const intent = (feedback.intent || "").trim();
  if (intent) {
    const box = document.createElement("div");
    box.className = "framed";
    addLine(box, "block-label", "意图");
    addLine(box, "natural", intent);
    natural.appendChild(box);
  }
  const corrected = document.createElement("div");
  corrected.className = "framed";
  addLine(corrected, "block-label", "修正版");
  renderFramedLine(corrected, feedback.natural || "", feedback.chunks || []);
  addMeaning(corrected, feedback.natural_zh);
  renderSpeech(corrected, turn);
  natural.appendChild(corrected);
  const variants = renderVariants(turn);
  if (variants) natural.appendChild(variants);
  card.appendChild(natural);

  renderEcho(corrected, turn, "");

  const edits = document.createElement("div");
  edits.className = "framed-part";
  corrected.appendChild(edits);
  addLabel(edits, "改动", 2);
  const list = document.createElement("ul");
  list.className = "changes";
  const changes = shownChanges(turn, feedback);
  if (!changes.length) {
    addLine(edits, "note", "这句已经比较顺，没有硬改。");
  } else {
    changes.forEach((change) => {
      const li = document.createElement("li");
      addLine(li, "spoken", `你说：${change.spoken}`);
      addLine(li, "better", `换成：${change.better}`);
      if (change.note) addLine(li, "note", change.note);
      list.appendChild(li);
    });
    edits.appendChild(list);
  }

  const extras = document.createElement("div");
  addLine(extras, "block-label", "可以补上的说法");
  const extList = document.createElement("ul");
  extList.className = "extensions";
  (feedback.extensions || []).forEach((item) => {
    const li = document.createElement("li");
    addLine(li, "better", item.line);
    if (item.note) addLine(li, "note", item.note);
    extList.appendChild(li);
  });
  if (extList.childElementCount) extras.appendChild(extList);
  else addLine(extras, "note", "这次没有延伸。");
  card.appendChild(extras);
  renderReview(card, feedback);
  return card;
}

function renderEcho(parent, turn, level) {
  const echo = document.createElement("div");
  echo.className = "framed-part";
  parent.appendChild(echo);
  const head = document.createElement("div");
  head.className = "echo-head";
  echo.appendChild(head);
  addLabel(head, "跟读", level ? 3 : 2);
  const item = level
    ? (((turn.feedback || {}).variants || []).find((entry) => entry.key === level) || {})
    : null;
  const file = level ? item.retell_file : turn.retell_file;
  if (!sessionArchived()) {
    const actions = document.createElement("div");
    actions.className = "echo-actions";
    head.appendChild(actions);
    if (file) {
      actions.appendChild(retellButton(turn, "重录", "redo", level));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-btn";
      remove.dataset.retellDelete = String(turn.index);
      remove.dataset.retellLevel = level || "";
      remove.textContent = "删除";
      actions.appendChild(remove);
    } else {
      actions.appendChild(retellButton(turn, "跟读", "", level));
    }
  }
  if (file) {
    const bar = window.QuickbackPlayer.create(mediaSrc("retell", turn, level), "metadata");
    bar.classList.add("is-retell");
    echo.appendChild(bar);
  }
  const note = state.retellNote;
  if (note && note.index === turn.index && (note.level || "") === (level || "") && !(note.chunk || "")) {
    addLine(echo, "error-line", note.message);
  }
  renderChunkEcho(echo, turn, level);
  if (level) renderCores(echo, turn, level);
}

const CHUNK_ROLES = { flow: "起句", pivot: "转折", refactor: "改口" };
const CHUNK_SLOT = "[ _____ ]";
const CHUNK_FUNCTION = new Set("i you he she it we they me him us them my your his her our their a an the this that these those am is are was were be been being do does did have has had will would can could shall should may might must so but and or nor because though although if when while as of to in on at for with from about into over by not just really honestly actually consistently very too also".split(" "));
const CHUNK_VERBS = new Set("like love want need spend spent stay stayed shape shaped help helped make made get got take took go went come came see saw know knew think thought feel felt say said leave left keep kept seem call work talk live grow grew tell told give gave find found become became let".split(" "));

function chunkNeedsSlot(phrase) {
  const words = String(phrase || "").match(/[A-Za-z]+(?:['’][A-Za-z]+)?/g) || [];
  if (words.length < 2) return false;
  const verbAt = words.findIndex((word) => {
    const token = word.toLowerCase();
    if (CHUNK_VERBS.has(token)) return true;
    if (CHUNK_FUNCTION.has(token)) return "am is are was were be been being do does did have has had will would can could shall should may might must".split(" ").includes(token);
    return token.length > 4 && /(?:ed|ing)$/.test(token);
  });
  if (verbAt < 0) return true;
  return !words.slice(verbAt + 1).some((word) => !CHUNK_FUNCTION.has(word.toLowerCase()));
}

function appendChunkSlot(node, phrase) {
  const slot = document.createElement("span");
  slot.className = "chunk-slot";
  slot.textContent = CHUNK_SLOT;
  node.append(document.createTextNode(`${phrase} `), slot);
}
window.QuickbackChunkSlot = appendChunkSlot;
window.QuickbackChunkNeedsSlot = chunkNeedsSlot;

function chunkList(turn, level) {
  const raw = level
    ? ((((turn.feedback || {}).variants || []).find((entry) => entry.key === level) || {}).chunks || [])
    : ((turn.feedback || {}).chunks || []);
  return raw.filter((item) => CHUNK_ROLES[item.role] && (item.en || "").trim());
}

function renderFramedLine(parent, text, chunks, cores) {
  const p = document.createElement("p");
  p.className = "natural";
  const source = text || "";
  const lower = source.toLowerCase();
  const ranges = [];
  chunkList({ feedback: { chunks } }, "").forEach((chunk) => {
    const en = chunk.en.replace(/\.\.\./g, "").trim();
    const at = lower.indexOf(en.toLowerCase());
    if (at >= 0) ranges.push({ start: at, end: at + en.length, core: false });
  });
  ranges.sort((a, b) => a.start - b.start || b.end - a.end);
  const marks = [];
  let cursor = 0;
  ranges.forEach((range) => {
    if (range.start < cursor) return;
    marks.push(range);
    cursor = range.end;
  });
  (cores || []).forEach((core) => {
    const en = (core.en || "").trim();
    const at = en ? lower.indexOf(en.toLowerCase()) : -1;
    if (at < 0) return;
    const range = { start: at, end: at + en.length, core: true };
    if (marks.some((item) => range.start < item.end && item.start < range.end)) return;
    marks.push(range);
  });
  marks.sort((a, b) => a.start - b.start || b.end - a.end);
  let pos = 0;
  marks.forEach((range) => {
    if (range.start < pos) return;
    if (range.start > pos) p.appendChild(document.createTextNode(source.slice(pos, range.start)));
    const mark = document.createElement("mark");
    mark.className = range.core ? "core-mark" : "chunk-mark";
    mark.textContent = source.slice(range.start, range.end);
    p.appendChild(mark);
    pos = range.end;
  });
  if (pos < source.length) p.appendChild(document.createTextNode(source.slice(pos)));
  parent.appendChild(p);
}

function speechLine(turn, level) {
  if (!level) return ((turn.feedback || {}).natural || "").trim();
  const item = ((turn.feedback || {}).variants || []).find((entry) => entry.key === level);
  return ((item && item.en) || "").trim();
}

function speechReady(turn, level) {
  if (!level) return Boolean(turn.speech_file);
  const item = ((turn.feedback || {}).variants || []).find((entry) => entry.key === level);
  return Boolean(item && item.speech_file);
}

function sliceRatios(full, piece) {
  const needle = (piece || "").replace(/\.\.\./g, "").trim();
  const at = full.toLowerCase().indexOf(needle.toLowerCase());
  if (!needle || at < 0 || !full.length) return null;
  return { start: at / full.length, end: (at + needle.length) / full.length };
}

const slicePlayer = {
  audio: null,
  button: null,
  stopAt: 0,
  stop() {
    if (this.audio) this.audio.pause();
    if (this.button) this.button.classList.remove("is-playing");
    this.button = null;
  },
  play(button, src, ratios) {
    if (this.button === button && this.audio && !this.audio.paused) {
      this.stop();
      return;
    }
    this.stop();
    chunkRetell.stop();
    document.querySelectorAll(".player audio").forEach((other) => other.pause());
    if (!this.audio) {
      this.audio = new Audio();
      this.audio.preload = "auto";
      this.audio.addEventListener("timeupdate", () => {
        if (this.audio.currentTime >= this.stopAt) this.stop();
      });
    }
    const same = this.audio.getAttribute("src") === src;
    this.stopAt = Infinity;
    this.button = button;
    button.classList.add("is-playing");
    const begin = () => {
      const duration = this.audio.duration;
      if (!Number.isFinite(duration) || duration <= 0) return;
      const start = Math.max(0, duration * ratios.start - 0.05);
      this.stopAt = Math.min(duration, Math.max(start + 0.28, duration * ratios.end + 0.08));
      this.audio.currentTime = start;
      this.audio.play().catch(() => this.stop());
    };
    if (!same) {
      this.audio.src = src;
      this.audio.addEventListener("loadedmetadata", begin, { once: true });
    } else if (this.audio.readyState >= 1) begin();
    else this.audio.addEventListener("loadedmetadata", begin, { once: true });
  },
};
slicePlayer.ratios = sliceRatios;
window.QuickbackSlice = slicePlayer;

function chunkSpeakButton(turn, level, text, slot) {
  const full = speechLine(turn, level);
  const ratios = sliceRatios(full, text);
  const label = slot ? `${text} ${CHUNK_SLOT}` : text;
  if (!speechReady(turn, level) || !ratios) {
    const line = document.createElement("p");
    line.className = "natural";
    if (slot) appendChunkSlot(line, text);
    else line.textContent = text;
    return line;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chunk-speak";
  if (slot) appendChunkSlot(button, text);
  else button.textContent = text;
  button.setAttribute("aria-label", `播放 ${label}`);
  button.addEventListener("click", () => {
    slicePlayer.play(button, mediaSrc("speech", turn, level), ratios);
  });
  return button;
}

function chunkMedia(turn, level, number) {
  const version = encodeURIComponent(turn.created_at || "");
  const scope = level || "main";
  return `/api/sessions/${state.session.id}/retell/${turn.index}/${scope}/chunks/${number}?v=${version}`;
}

function renderChunkEcho(parent, turn, level) {
  const chunks = chunkList(turn, level);
  if (!chunks.length) return;
  const block = document.createElement("div");
  block.className = "chunk-echo";
  parent.appendChild(block);
  addLabel(block, "功能性句组跟读", level ? 3 : 2).classList.add("is-highlight");
  chunks.forEach((chunk, number) => {
    const part = document.createElement("div");
    part.className = "chunk";
    const head = document.createElement("div");
    head.className = "echo-head";
    part.appendChild(head);
    const line = document.createElement("div");
    line.className = "chunk-line";
    const role = document.createElement("span");
    role.className = `chunk-role is-${chunk.role}`;
    role.textContent = CHUNK_ROLES[chunk.role];
    const phrase = chunk.en.trim();
    const slot = chunkNeedsSlot(phrase);
    line.append(role, chunkSpeakButton(turn, level, phrase, slot));
    head.appendChild(line);
    const file = chunk.retell_file || "";
    const actions = document.createElement("div");
    actions.className = "echo-actions";
    if (file) {
      const heard = document.createElement("button");
      heard.type = "button";
      heard.className = "chunk-heard";
      heard.setAttribute("aria-label", `播放跟读 ${phrase}${slot ? ` ${CHUNK_SLOT}` : ""}`);
      heard.addEventListener("click", () => chunkRetell.play(heard, chunkMedia(turn, level, number)));
      actions.appendChild(heard);
    }
    if (!sessionArchived()) {
      if (file) {
        actions.appendChild(retellButton(turn, "重录", "redo", level, number));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.retellDelete = String(turn.index);
        remove.dataset.retellLevel = level || "";
        remove.dataset.retellChunk = String(number);
        remove.textContent = "删除";
        actions.appendChild(remove);
      } else {
        actions.appendChild(retellButton(turn, "跟读", "", level, number));
      }
    }
    if (actions.childElementCount) head.appendChild(actions);
    const note = state.retellNote;
    if (note && note.index === turn.index && (note.level || "") === (level || "") && String(note.chunk || "") === String(number)) {
      addLine(part, "error-line", note.message);
    }
    block.appendChild(part);
  });
}

function coreEntries(turn, level) {
  const item = ((turn.feedback || {}).variants || []).find((entry) => entry.key === level);
  return ((item && item.cores) || [])
    .map((core, number) => ({ core, number }))
    .filter(({ core }) => core && String(core.en || "").trim());
}

function coreKey(index, level) {
  return `${index}:${level}`;
}

function coreField(label, name, value, maxLength, required) {
  const wrap = document.createElement("label");
  wrap.textContent = label;
  const input = document.createElement("input");
  input.type = "text";
  input.name = name;
  input.value = value || "";
  input.required = required;
  input.maxLength = maxLength;
  input.autocomplete = "off";
  input.addEventListener("input", () => {
    if (state.coreDraft) state.coreDraft[name] = input.value;
  });
  wrap.appendChild(input);
  return wrap;
}

function coreEditor(index, level) {
  const draft = state.coreDraft || { en: "" };
  const box = document.createElement("div");
  box.className = "prompt-edit";
  box.append(coreField("语块", "en", draft.en, 80, true));
  const actions = document.createElement("div");
  actions.className = "question-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "primary";
  save.dataset.coreSave = String(index);
  save.dataset.coreLevel = level;
  save.textContent = "添加";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "text-btn";
  cancel.dataset.coreCancel = String(index);
  cancel.dataset.coreLevel = level;
  cancel.textContent = "取消";
  actions.append(save, cancel);
  box.appendChild(actions);
  return box;
}

function renderCores(parent, turn, level) {
  const cores = coreEntries(turn, level);
  const block = document.createElement("div");
  block.className = "core-block";
  parent.appendChild(block);
  addLabel(block, "核心语块内化", 3).classList.add("is-highlight");
  cores.forEach(({ core, number }) => {
    const part = document.createElement("div");
    part.className = "core";
    const head = document.createElement("div");
    head.className = "echo-head";
    part.appendChild(head);
    head.appendChild(chunkSpeakButton(turn, level, core.en.trim()));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-btn";
    remove.dataset.coreDelete = String(turn.index);
    remove.dataset.coreLevel = level;
    remove.dataset.coreNumber = String(number);
    remove.textContent = "删除";
    head.appendChild(remove);
    if ((core.usage || "").trim()) addLine(part, "note", `用法：${core.usage.trim()}`);
    if ((core.cue || "").trim()) addLine(part, "note", `照这个情境造三句：${core.cue.trim()}`);
    const attempts = coreAttempts(core);
    for (let slot = 0; slot < 3; slot += 1) {
      const attempt = attempts[slot] || {};
      const filled = Boolean(String(attempt.transcript || "").trim());
      const row = document.createElement("div");
      row.className = "core-try";
      const rowHead = document.createElement("div");
      rowHead.className = "echo-head";
      row.appendChild(rowHead);
      const which = document.createElement("span");
      which.className = "core-try-label";
      which.textContent = `第 ${slot + 1} 句`;
      const say = document.createElement("button");
      say.type = "button";
      say.className = "text-btn";
      say.dataset.coreSay = String(turn.index);
      say.dataset.coreLevel = level;
      say.dataset.coreNumber = String(number);
      say.dataset.coreSlot = String(slot);
      if (filled) say.dataset.coreRole = "redo";
      say.textContent = filled ? "重说" : "说一句";
      rowHead.append(which, say);
      if (filled) addLine(row, "spoken", `你说：${attempt.transcript.trim()}`);
      if ((attempt.note || "").trim()) addLine(row, "note", attempt.note.trim());
      const note = state.coreNote;
      if (note && note.index === turn.index && note.level === level && String(note.number) === String(number) && String(note.slot) === String(slot)) {
        addLine(row, "error-line", note.message);
      }
      part.appendChild(row);
    }
    block.appendChild(part);
  });
  if (state.addingCore === coreKey(turn.index, level)) {
    block.appendChild(coreEditor(turn.index, level));
    return;
  }
  const add = document.createElement("button");
  add.type = "button";
  add.className = "text-btn core-add";
  add.dataset.coreAdd = String(turn.index);
  add.dataset.coreLevel = level;
  add.textContent = "添加语块";
  block.appendChild(add);
}

function renderVariants(turn) {
  const order = [["native", "地道"], ["simple", "简单"], ["professional", "专业"]];
  const byKey = new Map(((turn.feedback || {}).variants || []).map((item) => [item.key, item]));
  const box = document.createElement("div");
  box.className = "framed";
  addLine(box, "block-label", "多样表达");
  let count = 0;
  order.forEach(([key, label]) => {
    const item = byKey.get(key);
    const en = item && (item.en || "").trim();
    if (!en) return;
    const part = document.createElement("div");
    part.className = "variant";
    addLabel(part, label, 2);
    renderFramedLine(part, en, item.chunks || [], item.cores || []);
    if ((item.zh || "").trim()) addMeaning(part, item.zh.trim());
    renderSpeech(part, turn, { level: key, text: en, file: item.speech_file || "" });
    renderEcho(part, turn, key);
    box.appendChild(part);
    count += 1;
  });
  return count ? box : null;
}

function renderTurnQuestion(turn) {
  const block = document.createElement("div");
  block.className = "turn-question";
  if (state.editingTurn === turn.index && !sessionArchived()) {
    block.appendChild(turnEditor());
    return block;
  }
  const prompt = turn.prompt || {};
  if (prompt.en) addLine(block, "en-line", prompt.en);
  if (prompt.zh) addLine(block, "zh-line", prompt.zh);
  if (state.retake === turn.index) addLine(block, "note", "接下来的录音会替换这一段，修正版会按新回答重写。");
  const slot = document.createElement("div");
  slot.className = "retake-slot";
  slot.dataset.retakeSlot = String(turn.index);
  block.appendChild(slot);
  return block;
}

function renderReview(card, feedback) {
  const review = (feedback.review || "").trim();
  if (!review) return;
  const details = document.createElement("details");
  details.className = "review";
  const summary = document.createElement("summary");
  summary.textContent = "改进点评";
  const body = document.createElement("p");
  body.textContent = review;
  details.append(summary, body);
  card.appendChild(details);
}

function renderPractice() {
  const session = state.session;
  const turns = session ? session.turns || [] : [];
  if (session && turns.length) {
    topicEl.hidden = false;
    renderPrompt(topicEl, "本题", session.question, "question");
  } else {
    topicEl.hidden = true;
    topicEl.replaceChildren();
  }

  if (recorderEl.parentElement !== practiceEl) practiceEl.appendChild(recorderEl);
  turnsEl.replaceChildren(...turns.map(renderTurn));

  const closed = Boolean(session && session.closed && turns.length && state.retake == null);
  if (closed) {
    nextEl.hidden = true;
    nextEl.replaceChildren();
  } else if (!session) {
    nextEl.hidden = false;
    renderPrompt(nextEl, "现在回答", state.question || { en: "正在出题…", zh: "" }, state.question ? "question" : null);
  } else if (!turns.length) {
    nextEl.hidden = false;
    renderPrompt(nextEl, "现在回答", session.pending_prompt, "question");
  } else {
    nextEl.hidden = false;
    const last = turns[turns.length - 1];
    const follow = followUpPrompt(session);
    if (follow) renderPrompt(nextEl, "追问", follow, "follow");
    else if (last.status === "error") renderPrompt(nextEl, "再答一次", session.pending_prompt || last.prompt);
    else renderEmptyFollow();
  }
  parkRecorder();
  syncControls();
}

function parkRecorder() {
  const slot = state.retake == null ? null : turnsEl.querySelector(`[data-retake-slot="${state.retake}"]`);
  if (slot) {
    slot.appendChild(recorderEl);
    recorderEl.classList.add("is-inline");
  } else if (recorderEl.previousElementSibling !== nextEl) {
    nextEl.insertAdjacentElement("afterend", recorderEl);
    recorderEl.classList.remove("is-inline");
  } else {
    recorderEl.classList.remove("is-inline");
  }
  newBtn.hidden = state.retake != null;
}

function renderEmptyFollow() {
  nextEl.replaceChildren();
  addLine(nextEl, "kicker", "追问");
  if (state.editingPrompt === "follow") {
    nextEl.appendChild(promptEditor("follow"));
    return;
  }
  addLine(nextEl, "note", "这次没有新的追问。可以自己写一句，也可以换一题或再录一段。");
  const write = document.createElement("button");
  write.type = "button";
  write.className = "text-btn";
  write.dataset.promptEdit = "follow";
  write.dataset.promptBlank = "1";
  write.textContent = "写一句追问";
  nextEl.appendChild(write);
}

function clearPending() {
  if (state.pending && state.pending.url) URL.revokeObjectURL(state.pending.url);
  state.pending = null;
  takeAudio.setSrc("");
}

function holdTake(blob, durationMs) {
  clearPending();
  const url = URL.createObjectURL(blob);
  state.pending = { blob, durationMs, url };
  takeAudio.setSrc(url);
  showBanner("");
  syncControls();
}

function discardTake() {
  clearPending();
  syncControls();
}

function syncControls() {
  const reviewing = Boolean(state.pending) && !state.busy;
  const closed = Boolean(state.session && state.session.closed);
  recordBtn.hidden = reviewing || (closed && state.retake == null);
  finishBtn.hidden = reviewing || state.retake != null || closed || !((state.session && state.session.turns) || []).length;
  recHint.hidden = closed && state.retake == null;
  reviewEl.hidden = !reviewing;
  recordBtn.disabled = state.busy || Boolean(state.editingPrompt) || state.editingTurn != null || state.retell != null || state.drill != null || (window.QuickbackProgress && window.QuickbackProgress.busy());
  cancelRetakeBtn.hidden = state.retake == null || state.recording || state.busy;
  document.querySelectorAll("[data-prompt-edit], [data-prompt-delete], [data-prompt-save]").forEach((button) => {
    button.disabled = state.busy || state.recording;
  });
  document.querySelectorAll("[data-turn-edit], [data-turn-delete], [data-turn-save], [data-turn-retake]").forEach((button) => {
    button.disabled = state.busy || state.recording || Boolean(state.pending);
  });
  document.querySelectorAll("[data-retell]").forEach((button) => {
    const active = retellActive(button.dataset.retell, button.dataset.retellLevel || "", button.dataset.retellChunk || "", button.dataset.retellSession || "");
    button.disabled = state.busy || Boolean(state.pending) || (state.recording && !active);
    const idle = button.dataset.retellRole === "redo" ? "重录" : "跟读";
    button.textContent = active ? `说完了 ${formatClock(Date.now() - state.startedAt)}` : idle;
  });
  document.querySelectorAll("[data-retell-delete]").forEach((button) => {
    button.disabled = state.busy || state.recording || Boolean(state.pending);
  });
  document.querySelectorAll("[data-core-add], [data-core-delete], [data-core-save], [data-core-cancel]").forEach((button) => {
    button.disabled = state.busy || state.recording;
    if ("coreSave" in button.dataset) button.textContent = state.fillingCore ? "正在补用法…" : "添加";
  });
  document.querySelectorAll("[data-core-say]").forEach((button) => {
    const active = coreActive(button.dataset.coreSay, button.dataset.coreLevel || "", button.dataset.coreNumber || "", button.dataset.coreSlot || "");
    button.disabled = state.busy || Boolean(state.pending) || (state.recording && !active);
    const idle = button.dataset.coreRole === "redo" ? "重说" : "说一句";
    button.textContent = active ? `说完了 ${formatClock(Date.now() - state.startedAt)}` : idle;
  });
  sendTakeBtn.disabled = state.busy;
  redoTakeBtn.disabled = state.busy;
  newBtn.disabled = state.busy || state.recording || Boolean(state.pending);
  finishBtn.disabled = state.busy || state.recording || Boolean(state.pending);
  if (providerSelect) providerSelect.disabled = state.busy || state.recording;
  recordBtn.classList.toggle("is-recording", state.recording);
  if (state.busy && !state.fillingCore) {
    recordBtn.textContent = "正在转写，并生成反馈…";
    return;
  }
  const retakeLabel = state.retake != null ? `重答第 ${state.retake} 段` : "开始说";
  recHint.textContent = state.retake != null
    ? `正在重答第 ${state.retake} 段。点一下开始，说完再点一下。送出后这一段的原版和修正版都会按新回答重写。`
    : "点一下开始，说完再点一下。听完再决定要不要送出。中文卡住了就直接说中文。最长 3 分钟。";
  takeHint.textContent = state.retake != null
    ? `听一遍。送出后会替换第 ${state.retake} 段，修正版也会重写。`
    : "这段还在本机。听一遍，满意再送出。";
  if (state.recording && !state.retell && !state.drill) {
    recordBtn.textContent = `说完了 ${formatClock(Date.now() - state.startedAt)}`;
    return;
  }
  recordBtn.textContent = retakeLabel;
}

async function finishPractice() {
  const turns = (state.session && state.session.turns) || [];
  if (!state.session || !turns.length || state.session.closed) return;
  if (state.busy || state.recording || state.pending || state.retake != null) return;
  finishBtn.disabled = true;
  try {
    state.session = await api(`/api/sessions/${state.session.id}/finish`, { method: "POST" });
    state.editingPrompt = null;
    showBanner("");
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  } finally {
    syncControls();
  }
}

function activate(tab) {
  if (tab !== "progress" && window.QuickbackProgress) window.QuickbackProgress.leave();
  practiceEl.hidden = tab !== "practice";
  historyEl.hidden = tab !== "history";
  questionsEl.hidden = tab !== "questions";
  const progressEl = document.getElementById("progress");
  if (progressEl) progressEl.hidden = tab !== "progress";
  const statsEl = document.getElementById("progress-stats");
  if (statsEl) statsEl.hidden = tab !== "progress-stats";
  ["practice", "history", "questions", "progress"].forEach((name) => {
    const tabButton = document.getElementById(`tab-${name}`);
    if (tabButton) tabButton.classList.toggle("is-active", name === tab);
  });
}

function showPractice() {
  activate("practice");
}

async function showHistory() {
  activate("history");
  const list = document.getElementById("history-list");
  list.replaceChildren();
  addLine(list, "note", "正在读取…");
  try {
    const sessions = await api("/api/sessions");
    list.replaceChildren();
    if (!sessions.length) {
      addLine(list, "empty", "还没有记录。说完一段，文字和录音会留在这里。");
      return;
    }
    sessions.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      button.dataset.session = item.id;
      const title = document.createElement("strong");
      title.textContent = item.question.en || "未命名";
      const meta = document.createElement("span");
      const preview = item.preview ? ` · ${item.preview}` : "";
      meta.textContent = `${item.turn_count} 段 · ${formatWhen(item.updated_at)}${preview}`;
      button.append(title, meta);
      list.appendChild(button);
    });
  } catch (error) {
    list.replaceChildren();
    addLine(list, "error-line", error.message);
  }
}

async function rememberPractice(body) {
  await api("/api/practice", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function pickQuestion(exclude) {
  state.editingPrompt = null;
  state.editingTurn = null;
  state.retake = null;
  discardTake();
  const token = ++questionToken;
  try {
    const question = await api("/api/questions/next" + (exclude ? `?exclude=${encodeURIComponent(exclude)}` : ""));
    if (token !== questionToken) return;
    await rememberPractice({ question_id: question.id });
    if (token !== questionToken) return;
    state.question = question;
    state.session = null;
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  }
}

async function restorePractice() {
  const token = ++questionToken;
  try {
    const practice = await api("/api/practice");
    if (token !== questionToken) return;
    if (practice.session) {
      state.session = practice.session;
      state.question = null;
    } else {
      state.session = null;
      state.question = practice.question;
    }
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  }
}

function stopStream() {
  state.stream?.getTracks().forEach((track) => track.stop());
  state.stream = null;
}

function stopTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
}

async function uploadTake(blob, durationMs) {
  const form = new FormData();
  const ext = (blob.type || "").includes("mp4") ? "m4a" : "webm";
  form.append("audio", blob, `take.${ext}`);
  form.append("mime_type", blob.type || "audio/webm");
  form.append("duration_ms", String(durationMs));
  const retake = state.retake;
  let path = "/api/turns";
  if (retake != null) {
    if (!state.session) {
      showBanner("找不到要替换的这段。");
      return;
    }
    path = `/api/sessions/${state.session.id}/turns/${retake}/retake`;
  } else if (state.session) form.append("session_id", state.session.id);
  else if (state.question) form.append("question_id", state.question.id);
  state.busy = true;
  syncControls();
  try {
    state.session = await api(path, { method: "POST", body: form });
    if (retake != null) forgetSpeech(state.session.id, retake);
    state.retake = null;
    clearPending();
    state.question = null;
    showBanner("");
    renderPractice();
    if (retake == null) recorderEl.scrollIntoView({ behavior: "smooth", block: "center" });
    else turnsEl.querySelector(`[data-turn="${retake}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

function recordingBlock() {
  const onThisComputer = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  if (!window.isSecureContext || !navigator.mediaDevices) {
    if (onThisComputer) return "这个浏览器不能录音。请用 Chrome 或 Edge 打开。";
    return "这个地址不能录音。请改用 http://127.0.0.1:8765 打开。";
  }
  if (!window.MediaRecorder) return "这个浏览器不能录音。请用 Chrome 或 Edge 打开。";
  return "";
}

async function startRecording(retellIndex, retellLevel, retellChunk, sessionId, drill) {
  if (window.QuickbackProgress && window.QuickbackProgress.busy()) {
    showBanner("先说完进展里的这一段。");
    return;
  }
  const echoing = Number.isInteger(retellIndex) && !drill;
  const level = retellLevel || "";
  const chunk = retellChunk == null || retellChunk === "" ? "" : String(retellChunk);
  const targetSession = sessionId || (state.session && state.session.id) || "";
  const fail = (message) => {
    if (drill) {
      state.coreNote = { index: drill.index, level: drill.level, number: String(drill.number), slot: String(drill.slot), message };
      renderPractice();
    } else if (echoing) {
      state.retellNote = { index: retellIndex, level, chunk, message };
      renderPractice();
    }
    showBanner(message);
  };
  if (state.busy || state.recording || state.pending || state.editingPrompt || state.editingTurn != null) {
    if (!echoing && !drill) return;
    if (state.pending) fail("上一段录音还没送出。先点上面的「就用这段」或「重说」。");
    else if (state.recording) fail("先说完正在录的这一段。");
    return;
  }
  if (echoing && !targetSession) {
    fail("先有一段回答，才能跟读。");
    return;
  }
  if (!echoing && !state.session && !state.question) {
    showBanner("题目还没准备好。");
    return;
  }
  const blocked = recordingBlock();
  if (blocked) {
    fail(blocked);
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    fail("没有拿到麦克风权限。");
    return;
  }
  state.retellNote = null;
  state.coreNote = null;
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  state.mimeType = candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
  state.chunks = [];
  state.recorder = state.mimeType
    ? new MediaRecorder(state.stream, { mimeType: state.mimeType })
    : new MediaRecorder(state.stream);
  state.recorder.ondataavailable = (event) => {
    if (event.data.size) state.chunks.push(event.data);
  };
  state.drill = drill || null;
  state.retell = echoing ? { index: retellIndex, level, chunk, sessionId: targetSession } : null;
  state.recorder.onstop = () => {
    const durationMs = Date.now() - state.startedAt;
    const echo = state.retell;
    const practice = state.drill;
    const blob = new Blob(state.chunks, { type: state.recorder.mimeType || state.mimeType || "audio/webm" });
    stopStream();
    state.recording = false;
    state.retell = null;
    state.drill = null;
    stopTimer();
    syncControls();
    if (durationMs < 400 || blob.size < 400) {
      const message = "录音太短了，再说长一点。";
      if (practice) state.coreNote = { index: practice.index, level: practice.level, number: String(practice.number), slot: String(practice.slot), message };
      else if (echo != null) state.retellNote = { index: echo.index, level: echo.level || "", chunk: echo.chunk || "", message };
      showBanner(message);
      renderPractice();
      return;
    }
    if (practice) {
      uploadDrill(practice.index, practice.level, practice.number, practice.slot, blob);
      return;
    }
    if (echo != null) {
      uploadRetell(echo.index, echo.level || "", echo.chunk || "", blob, echo.sessionId);
      return;
    }
    holdTake(blob, durationMs);
  };
  state.recorder.start();
  state.recording = true;
  state.startedAt = Date.now();
  showBanner("");
  syncControls();
  state.timer = setInterval(() => {
    syncControls();
    if (Date.now() - state.startedAt >= MAX_MS) stopRecording();
  }, 250);
}

async function saveCore(index, level) {
  if (!state.session || state.busy || state.recording) return;
  const draft = state.coreDraft || { en: "" };
  state.busy = true;
  state.fillingCore = true;
  syncControls();
  try {
    state.session = await api(`/api/sessions/${state.session.id}/turns/${index}/cores/${level}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ en: draft.en || "" }),
    });
    state.addingCore = null;
    state.coreDraft = null;
    showBanner("");
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    state.fillingCore = false;
    syncControls();
  }
}

async function removeCore(index, level, number) {
  if (!state.session || state.busy || state.recording) return;
  if (!await askConfirm("删掉这个语块？造过的句子也会一起删掉。")) return;
  state.busy = true;
  syncControls();
  try {
    state.session = await api(`/api/sessions/${state.session.id}/turns/${index}/cores/${level}/${number}`, { method: "DELETE" });
    state.addingCore = null;
    showBanner("");
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

async function uploadDrill(index, level, number, slot, blob) {
  const id = state.session && state.session.id;
  if (!id) return;
  const form = new FormData();
  form.append("audio", blob, "core.webm");
  form.append("mime_type", blob.type || "audio/webm");
  state.busy = true;
  state.coreNote = null;
  syncControls();
  try {
    state.session = await api(`/api/sessions/${id}/turns/${index}/cores/${level}/${number}/${slot}`, { method: "POST", body: form });
    showBanner("");
    renderPractice();
  } catch (error) {
    state.coreNote = { index, level, number: String(number), slot: String(slot), message: error.message };
    showBanner(error.message);
    renderPractice();
  } finally {
    state.busy = false;
    syncControls();
  }
}

async function uploadRetell(index, level, chunk, blob, sessionId) {
  const id = sessionId || (state.session && state.session.id);
  if (!id) return;
  const form = new FormData();
  form.append("audio", blob, "retell.webm");
  form.append("mime_type", blob.type || "audio/webm");
  state.busy = true;
  syncControls();
  const path = chunk !== "" && chunk != null
    ? `/api/sessions/${id}/turns/${index}/retell/${level || "main"}/chunks/${chunk}`
    : (level
      ? `/api/sessions/${id}/turns/${index}/retell/${level}`
      : `/api/sessions/${id}/turns/${index}/retell`);
  try {
    const saved = await api(path, { method: "POST", body: form });
    if (state.session && saved.id === state.session.id) state.session = saved;
    showBanner("");
    renderPractice();
    if (window.QuickbackQuestions && window.QuickbackQuestions.refresh) await window.QuickbackQuestions.refresh();
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

function stopRecording() {
  if (state.recorder && state.recorder.state === "recording") state.recorder.stop();
}

async function retry(index) {
  if (!state.session || state.busy) return;
  state.busy = true;
  syncControls();
  try {
    state.session = await api(`/api/sessions/${state.session.id}/turns/${index}/retry`, { method: "POST" });
    showBanner("");
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

function askConfirm(message) {
  const root = document.getElementById("confirm-dialog");
  const text = document.getElementById("confirm-text");
  const ok = document.getElementById("confirm-ok");
  const cancel = document.getElementById("confirm-cancel");
  text.textContent = message;
  root.hidden = false;
  cancel.focus();
  return new Promise((resolve) => {
    function finish(value) {
      root.hidden = true;
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      root.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      resolve(value);
    }
    function onOk() { finish(true); }
    function onCancel() { finish(false); }
    function onBackdrop(event) {
      if (event.target === root) finish(false);
    }
    function onKey(event) {
      if (event.key === "Escape") finish(false);
    }
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    root.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
  });
}

window.quickbackConfirm = askConfirm;
window.QuickbackPracticeBusy = function () {
  return state.busy || state.recording || Boolean(state.pending) || state.retell != null || state.drill != null;
};
window.QuickbackSync = syncControls;

function promptBody(card) {
  return {
    en: card.querySelector('[name="en"]').value,
    zh: card.querySelector('[name="zh"]').value,
  };
}

async function reopenPractice() {
  if (!state.session || state.busy || state.recording) return;
  try {
    state.session = await api(`/api/sessions/${state.session.id}/reopen`, { method: "POST" });
    state.editingPrompt = null;
    state.editingTurn = null;
    state.retake = null;
    showBanner("");
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  }
}

practiceEl.addEventListener("click", async (event) => {
  if (event.target.closest("[data-reopen-practice]")) {
    await reopenPractice();
    return;
  }
  const edit = event.target.closest("[data-prompt-edit]");
  const save = event.target.closest("[data-prompt-save]");
  const cancel = event.target.closest("[data-prompt-cancel]");
  const remove = event.target.closest("[data-prompt-delete]");
  const turnEdit = event.target.closest("[data-turn-edit]");
  const turnSave = event.target.closest("[data-turn-save]");
  const turnCancel = event.target.closest("[data-turn-cancel]");
  const turnDelete = event.target.closest("[data-turn-delete]");
  const turnRetake = event.target.closest("[data-turn-retake]");
  if (!edit && !save && !cancel && !remove && !turnEdit && !turnSave && !turnCancel && !turnDelete && !turnRetake) return;
  if (state.recording || state.busy) return;
  if (state.pending && !edit && !save && !cancel && !remove) return;
  if (turnRetake) {
    beginRetake(Number(turnRetake.dataset.turnRetake));
    return;
  }
  if (turnCancel) {
    state.editingTurn = null;
    renderPractice();
    return;
  }
  if (turnEdit) {
    beginTurnEdit(Number(turnEdit.dataset.turnEdit));
    return;
  }
  try {
    if (turnSave) await saveTurn();
    else if (turnDelete) await deleteTurn(Number(turnDelete.dataset.turnDelete));
  } catch (error) {
    showBanner(error.message);
    return;
  }
  if (turnSave || turnDelete) return;
  if (cancel) {
    state.editingPrompt = null;
    renderPractice();
    return;
  }
  if (edit) {
    const kind = edit.dataset.promptEdit;
    const prompt = edit.dataset.promptBlank ? { en: "", zh: "" } : currentPrompt(kind);
    beginPromptEdit(kind, prompt);
    return;
  }
  const card = event.target.closest(".card");
  try {
    if (save) await savePrompt(save.dataset.promptSave, card);
    else await deletePrompt(remove.dataset.promptDelete);
  } catch (error) {
    showBanner(error.message);
  }
});

function beginRetake(index) {
  const turn = ((state.session && state.session.turns) || []).find((item) => item.index === index);
  if (!turn) return;
  discardTake();
  state.editingPrompt = null;
  state.editingTurn = null;
  state.retake = state.retake === index ? null : index;
  showBanner("");
  renderPractice();
  if (state.retake != null) recorderEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function beginTurnEdit(index) {
  const turn = ((state.session && state.session.turns) || []).find((item) => item.index === index);
  if (!turn) return;
  state.editingPrompt = null;
  state.retake = null;
  state.editingTurn = index;
  state.editDraft = {
    en: (turn.prompt && turn.prompt.en) || "",
    zh: (turn.prompt && turn.prompt.zh) || "",
  };
  renderPractice();
}

async function saveTurn() {
  const draft = state.editDraft || {};
  const body = { en: draft.en, zh: draft.zh };
  state.session = await api(`/api/sessions/${state.session.id}/turns/${state.editingTurn}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  state.editingTurn = null;
  showBanner("");
  renderPractice();
}

async function deleteRetell(index, level, chunk, sessionId) {
  const id = sessionId || (state.session && state.session.id);
  if (!id || state.busy || state.recording || state.pending) return;
  if (!await askConfirm("删掉这段跟读？")) return;
  state.busy = true;
  syncControls();
  const path = chunk !== "" && chunk != null
    ? `/api/sessions/${id}/turns/${index}/retell/${level || "main"}/chunks/${chunk}`
    : (level
      ? `/api/sessions/${id}/turns/${index}/retell/${level}`
      : `/api/sessions/${id}/turns/${index}/retell`);
  try {
    const saved = await api(path, { method: "DELETE" });
    if (state.session && saved.id === state.session.id) state.session = saved;
    showBanner("");
    renderPractice();
    if (window.QuickbackQuestions && window.QuickbackQuestions.refresh) await window.QuickbackQuestions.refresh();
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

window.QuickbackRetell = {
  toggle(sessionId, index, level, chunk) {
    if (retellActive(index, level, chunk, sessionId)) {
      stopRecording();
      return;
    }
    if (state.recording) return;
    startRecording(index, level, chunk, sessionId);
  },
  remove(sessionId, index, level, chunk) {
    deleteRetell(index, level, chunk, sessionId);
  },
};

async function deleteTurn(index) {
  if (!await askConfirm("删掉这一段回答？录音也会一起删掉。")) return;
  const sessionId = state.session.id;
  state.session = await api(`/api/sessions/${sessionId}/turns/${index}`, { method: "DELETE" });
  forgetSessionSpeech(sessionId);
  state.editingTurn = null;
  state.retake = null;
  showBanner("");
  renderPractice();
  if (window.QuickbackQuestions && window.QuickbackQuestions.refresh) await window.QuickbackQuestions.refresh();
}

function followUpPrompt(session) {
  const turns = (session && session.turns) || [];
  const last = turns[turns.length - 1];
  const feedback = last && last.feedback;
  const en = feedback && String(feedback.follow_up || "").trim();
  if (!last || last.status !== "ready" || !en) return null;
  return { en, zh: String(feedback.follow_up_zh || "").trim() };
}

function currentPrompt(kind) {
  if (kind === "follow") return followUpPrompt(state.session) || { en: "", zh: "" };
  if (state.session) return state.session.question;
  return state.question || { en: "", zh: "" };
}

async function savePrompt(kind, card) {
  const body = JSON.stringify(promptBody(card));
  const options = { method: "PUT", headers: { "Content-Type": "application/json" }, body };
  if (kind === "question" && state.session) {
    state.session = await api(`/api/sessions/${state.session.id}/question`, options);
  } else if (kind === "question") {
    state.question = await api(`/api/questions/${state.question.id}`, options);
  } else {
    state.session = await api(`/api/sessions/${state.session.id}/follow-up`, options);
  }
  state.editingPrompt = null;
  showBanner("");
  renderPractice();
}

async function dropQuestion(id) {
  if (window.QuickbackProgress && window.QuickbackProgress.questionId() === id) window.QuickbackProgress.clear();
  const current = state.session ? state.session.question.id : state.question && state.question.id;
  if (current !== id) return;
  if (state.session) forgetSessionSpeech(state.session.id);
  state.editingPrompt = null;
  state.editingTurn = null;
  state.retake = null;
  state.retell = null;
  state.retellNote = null;
  discardTake();
  state.session = null;
  state.question = null;
  showBanner("");
  await pickQuestion(id);
}

async function deletePrompt(kind) {
  if (kind === "follow") {
    if (!await askConfirm("删掉这句追问？")) return;
    state.session = await api(`/api/sessions/${state.session.id}/follow-up`, { method: "DELETE" });
    state.editingPrompt = null;
    showBanner("");
    renderPractice();
    return;
  }
  if (!await askConfirm("删掉这道题？它的追问和录音也会一起删掉。")) return;
  const id = state.session ? state.session.question.id : state.question.id;
  await api(`/api/questions/${id}`, { method: "DELETE" });
  await dropQuestion(id);
}

finishBtn.addEventListener("click", () => {
  finishPractice();
});

recordBtn.addEventListener("click", () => {
  if (state.recording) stopRecording();
  else startRecording();
});

sendTakeBtn.addEventListener("click", () => {
  if (!state.pending || state.busy) return;
  uploadTake(state.pending.blob, state.pending.durationMs);
});

redoTakeBtn.addEventListener("click", () => {
  if (state.busy) return;
  discardTake();
  showBanner("");
});

cancelRetakeBtn.addEventListener("click", () => {
  if (state.recording || state.busy) return;
  state.retake = null;
  discardTake();
  showBanner("");
  renderPractice();
});

newBtn.addEventListener("click", () => {
  if (state.recording || state.busy) return;
  const exclude = state.session ? state.session.question.id : state.question && state.question.id;
  pickQuestion(exclude);
});

async function answerQuestion(id) {
  if (state.recording || state.busy) return;
  state.editingPrompt = null;
  state.editingTurn = null;
  state.retake = null;
  discardTake();
  questionToken += 1;
  const practice = await api("/api/practice", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: id }),
  });
  state.session = practice.session;
  state.question = practice.question;
  showBanner("");
  showPractice();
  renderPractice();
}

async function openProgressStats(questionId) {
  activate("progress-stats");
  window.scrollTo(0, 0);
  try {
    await window.QuickbackProgress.showStats(api, showBanner, questionId || "", showQuestions);
  } catch (error) {
    showBanner(error.message);
  }
}

async function openProgress(questionId) {
  activate("progress");
  window.scrollTo(0, 0);
  try {
    await window.QuickbackProgress.show(api, showBanner, questionId || "", openProgressStats);
  } catch (error) {
    showBanner(error.message);
  }
}

async function showQuestions() {
  activate("questions");
  try {
    await window.QuickbackQuestions.show(api, showBanner, answerQuestion, dropQuestion, openProgress, openProgressStats);
    window.scrollTo(0, 0);
  } catch (error) {
    showBanner(error.message);
  }
}

document.getElementById("tab-practice").addEventListener("click", showPractice);
document.getElementById("tab-history").addEventListener("click", showHistory);
document.getElementById("tab-questions").addEventListener("click", showQuestions);
document.getElementById("tab-progress").addEventListener("click", () => {
  const current = window.QuickbackProgress ? window.QuickbackProgress.questionId() : "";
  openProgress(current);
});

turnsEl.addEventListener("click", (event) => {
  const button = event.target.closest("[data-retry]");
  if (button) retry(Number(button.dataset.retry));
  const removeRetell = event.target.closest("[data-retell-delete]");
  if (removeRetell) {
    deleteRetell(Number(removeRetell.dataset.retellDelete), removeRetell.dataset.retellLevel || "", removeRetell.dataset.retellChunk || "");
    return;
  }
  const coreDelete = event.target.closest("[data-core-delete]");
  if (coreDelete) {
    removeCore(Number(coreDelete.dataset.coreDelete), coreDelete.dataset.coreLevel || "", Number(coreDelete.dataset.coreNumber));
    return;
  }
  const coreAdd = event.target.closest("[data-core-add]");
  if (coreAdd) {
    if (state.recording || state.busy) return;
    state.addingCore = coreKey(Number(coreAdd.dataset.coreAdd), coreAdd.dataset.coreLevel || "");
    state.coreDraft = { en: "" };
    renderPractice();
    return;
  }
  const coreCancel = event.target.closest("[data-core-cancel]");
  if (coreCancel) {
    state.addingCore = null;
    state.coreDraft = null;
    renderPractice();
    return;
  }
  const coreSave = event.target.closest("[data-core-save]");
  if (coreSave) {
    saveCore(Number(coreSave.dataset.coreSave), coreSave.dataset.coreLevel || "");
    return;
  }
  const core = event.target.closest("[data-core-say]");
  if (core) {
    const index = Number(core.dataset.coreSay);
    const level = core.dataset.coreLevel || "";
    const number = Number(core.dataset.coreNumber);
    const slot = Number(core.dataset.coreSlot);
    if (coreActive(index, level, number, slot)) {
      stopRecording();
      return;
    }
    if (state.recording) return;
    startRecording(null, "", "", "", { index, level, number, slot });
    return;
  }
  const retell = event.target.closest("[data-retell]");
  if (!retell) return;
  const index = Number(retell.dataset.retell);
  const level = retell.dataset.retellLevel || "";
  const chunk = retell.dataset.retellChunk || "";
  if (retellActive(index, level, chunk)) {
    stopRecording();
    return;
  }
  if (state.recording) return;
  startRecording(index, level, chunk);
});

document.getElementById("history-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-session]");
  if (!button) return;
  try {
    questionToken += 1;
    state.editingPrompt = null;
    state.editingTurn = null;
    state.retake = null;
    state.retell = null;
    state.drill = null;
    state.coreNote = null;
    state.addingCore = null;
    state.coreDraft = null;
    discardTake();
    state.session = await api(`/api/sessions/${button.dataset.session}`);
    state.question = null;
    await rememberPractice({ session_id: state.session.id });
    showPractice();
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  }
});

function missingKeyMessage(provider) {
  if (provider === "qwen") {
    return "还没填千问 Key。把 Key 写进 .env 的 DASHSCOPE_API_KEY，保存后重启。录音仍会留下。";
  }
  return "还没填 Gemini Key。把 Key 写进项目目录的 .env，保存后重启。录音仍会留下。";
}

function showProvider(view) {
  state.provider = view.provider;
  state.hasKey = view.has_key;
  if (providerSelect) providerSelect.value = view.provider;
  if (!view.has_key) showBanner(missingKeyMessage(view.provider));
}

async function init() {
  try {
    showProvider(await api("/api/provider"));
  } catch (error) {
    showBanner(error.message);
  }
  renderPractice();
  restorePractice();
}

providerSelect.addEventListener("change", async () => {
  const next = providerSelect.value;
  try {
    const view = await api("/api/provider", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: next }),
    });
    showProvider(view);
    if (view.has_key) showBanner("");
  } catch (error) {
    providerSelect.value = state.provider || "gemini";
    showBanner(error.message);
  }
});

init();
