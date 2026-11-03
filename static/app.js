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
const hintEl = document.getElementById("rec-hint");
const newBtn = document.getElementById("new-question");

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

function addLine(parent, className, text) {
  const p = document.createElement("p");
  p.className = className;
  p.textContent = text;
  parent.appendChild(p);
  return p;
}

function renderPrompt(target, label, prompt) {
  target.replaceChildren();
  addLine(target, "kicker", label);
  addLine(target, "en-line", prompt.en || "");
  if (prompt.zh) addLine(target, "zh-line", prompt.zh);
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

function renderSpeech(parent, turn) {
  addLine(parent, "block-label", "发音");
  if (turn.speech_file) {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = `/api/sessions/${state.session.id}/speech/${turn.index}`;
    parent.appendChild(audio);
    return;
  }
  const note = addLine(parent, "note", "正在准备发音…");
  const sessionId = state.session.id;
  const index = turn.index;
  const key = `${sessionId}:${index}`;
  if (speechRequests.has(key)) return;
  const job = api(`/api/sessions/${sessionId}/turns/${index}/speech`, { method: "POST" })
    .then((session) => {
      if (!state.session || state.session.id !== sessionId) return;
      state.session = session;
      if (!state.recording) renderPractice();
    })
    .catch((error) => {
      speechRequests.delete(key);
      if (note.isConnected) note.textContent = error.message;
    });
  speechRequests.set(key, job);
}

function renderTurn(turn) {
  const card = document.createElement("article");
  card.className = "card turn";
  addLine(card, "kicker", `第 ${turn.index} 段`);
  if (turn.prompt && turn.prompt.en) addLine(card, "meta", `在回答：${turn.prompt.en}`);

  const said = document.createElement("div");
  addLine(said, "block-label", "你说的");
  renderTranscript(said, turn);
  const seconds = turn.duration_ms ? ` · ${Math.round(turn.duration_ms / 1000)} 秒` : "";
  addLine(said, "meta", `${formatWhen(turn.created_at)}${seconds}`);
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.src = `/api/sessions/${state.session.id}/audio/${turn.index}`;
  said.appendChild(audio);
  card.appendChild(said);

  if (turn.status === "error") {
    addLine(card, "error-line", turn.error || "这次没有生成反馈。");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry";
    retry.dataset.retry = String(turn.index);
    retry.textContent = "重试这一段";
    card.appendChild(retry);
    return card;
  }

  const feedback = turn.feedback;
  if (!feedback) return card;

  const natural = document.createElement("div");
  addLine(natural, "block-label", "地道版");
  addLine(natural, "natural", feedback.natural || "");
  renderSpeech(natural, turn);
  card.appendChild(natural);

  const edits = document.createElement("div");
  addLine(edits, "block-label", "改动");
  const list = document.createElement("ul");
  list.className = "changes";
  const changes = feedback.changes || [];
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
  card.appendChild(edits);

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
    renderPrompt(topicEl, "本题", session.question);
  } else {
    topicEl.hidden = true;
    topicEl.replaceChildren();
  }

  turnsEl.replaceChildren(...turns.map(renderTurn));

  if (!session) {
    renderPrompt(nextEl, "现在回答", state.question || { en: "正在出题…", zh: "" });
  } else if (!turns.length) {
    renderPrompt(nextEl, "现在回答", session.pending_prompt);
  } else {
    const last = turns[turns.length - 1];
    const pending = session.pending_prompt || last.prompt;
    const isNew = pending.en && pending.en !== last.prompt.en;
    if (isNew) renderPrompt(nextEl, "追问", pending);
    else if (last.status === "error") renderPrompt(nextEl, "再答一次", pending);
    else {
      nextEl.replaceChildren();
      addLine(nextEl, "kicker", "追问");
      addLine(nextEl, "note", "这次没有新的追问。可以换一题，也可以再录一段。");
    }
  }
  syncControls();
}

function syncControls() {
  recordBtn.disabled = state.busy;
  newBtn.disabled = state.busy || state.recording;
  recordBtn.classList.toggle("is-recording", state.recording);
  if (state.busy) {
    recordBtn.textContent = "正在转写，并生成反馈…";
    return;
  }
  if (state.recording) {
    recordBtn.textContent = `说完了 ${formatClock(Date.now() - state.startedAt)}`;
    return;
  }
  recordBtn.textContent = "开始说";
}

function activate(tab) {
  practiceEl.hidden = tab !== "practice";
  historyEl.hidden = tab !== "history";
  questionsEl.hidden = tab !== "questions";
  ["practice", "history", "questions"].forEach((name) => {
    document.getElementById(`tab-${name}`).classList.toggle("is-active", name === tab);
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

async function pickQuestion(exclude) {
  const token = ++questionToken;
  try {
    const question = await api("/api/questions/next" + (exclude ? `?exclude=${encodeURIComponent(exclude)}` : ""));
    if (token !== questionToken) return;
    state.question = question;
    state.session = null;
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
  if (state.session) form.append("session_id", state.session.id);
  else if (state.question) form.append("question_id", state.question.id);
  state.busy = true;
  syncControls();
  try {
    state.session = await api("/api/turns", { method: "POST", body: form });
    state.question = null;
    showBanner("");
    renderPractice();
    document.getElementById("recorder").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    syncControls();
  }
}

async function startRecording() {
  if (state.busy || state.recording) return;
  if (!state.session && !state.question) {
    showBanner("题目还没准备好。");
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showBanner("这个浏览器不能录音。请用 Chrome 或 Edge 打开。");
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    showBanner("没有拿到麦克风权限。");
    return;
  }
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  state.mimeType = candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
  state.chunks = [];
  state.recorder = state.mimeType
    ? new MediaRecorder(state.stream, { mimeType: state.mimeType })
    : new MediaRecorder(state.stream);
  state.recorder.ondataavailable = (event) => {
    if (event.data.size) state.chunks.push(event.data);
  };
  state.recorder.onstop = () => {
    const durationMs = Date.now() - state.startedAt;
    const blob = new Blob(state.chunks, { type: state.recorder.mimeType || state.mimeType || "audio/webm" });
    stopStream();
    state.recording = false;
    stopTimer();
    syncControls();
    if (durationMs < 400 || blob.size < 400) {
      showBanner("录音太短了，再说长一点。");
      return;
    }
    uploadTake(blob, durationMs);
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

recordBtn.addEventListener("click", () => {
  if (state.recording) stopRecording();
  else startRecording();
});

newBtn.addEventListener("click", () => {
  if (state.recording || state.busy) return;
  const exclude = state.session ? state.session.question.id : state.question && state.question.id;
  pickQuestion(exclude);
});

async function showQuestions() {
  activate("questions");
  try {
    await window.QuickbackQuestions.show(api, showBanner);
  } catch (error) {
    showBanner(error.message);
  }
}

document.getElementById("tab-practice").addEventListener("click", showPractice);
document.getElementById("tab-history").addEventListener("click", showHistory);
document.getElementById("tab-questions").addEventListener("click", showQuestions);

turnsEl.addEventListener("click", (event) => {
  const button = event.target.closest("[data-retry]");
  if (button) retry(Number(button.dataset.retry));
});

document.getElementById("history-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-session]");
  if (!button) return;
  try {
    questionToken += 1;
    state.session = await api(`/api/sessions/${button.dataset.session}`);
    state.question = null;
    showPractice();
    renderPractice();
  } catch (error) {
    showBanner(error.message);
  }
});

async function init() {
  try {
    const health = await api("/api/health");
    state.hasKey = health.has_key;
    if (!health.has_key) {
      showBanner("还没填 Gemini Key。把 Key 写进项目目录的 .env，保存后重启。录音仍会留下。");
    }
  } catch (error) {
    showBanner(error.message);
  }
  renderPractice();
  pickQuestion(null);
}

init();
