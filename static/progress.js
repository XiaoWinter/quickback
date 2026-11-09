(function () {
  const MAX_MS = 3 * 60 * 1000;
  const DIMENSIONS = [
    ["comprehensibility", "可理解性"],
    ["relevance", "切题"],
    ["fluency", "流利度"],
  ];
  const root = document.getElementById("progress-root");
  const statsRoot = document.getElementById("progress-stats-root");
  let api = null;
  let report = null;
  let questionId = "";
  let view = null;
  let recording = false;
  let uploading = false;
  let take = null;
  let takeUrl = "";
  let stream = null;
  let recorder = null;
  let chunks = [];
  let timer = null;
  let startedAt = 0;
  let mimeType = "";
  let ignoreStop = false;
  let generation = 0;
  let afterFinish = null;
  let retake = null;

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

  function poke() {
    if (window.QuickbackSync) window.QuickbackSync();
  }

  function setTake(blob, durationMs) {
    if (takeUrl) URL.revokeObjectURL(takeUrl);
    takeUrl = blob ? URL.createObjectURL(blob) : "";
    take = blob ? { blob, durationMs } : null;
  }

  function stopStream() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
  }

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  function cssColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function chartRows(runs) {
    return (runs || []).map((item) => {
      const stats = item && item.stats ? item.stats : item || {};
      const raw = Number(stats.raw);
      const average = Number(stats.average);
      if (!Number.isFinite(raw)) return null;
      return {
        raw,
        average: Number.isFinite(average) ? average : 0,
        count: Number(stats.count) || 0,
        when: item && item.finished_at ? formatWhen(item.finished_at) : "",
      };
    }).filter(Boolean);
  }

  function releaseCharts(parent) {
    if (!parent || !window.Chart) return;
    parent.querySelectorAll("canvas").forEach((node) => {
      const drawn = window.Chart.getChart(node);
      if (drawn) drawn.destroy();
    });
  }

  function barLayout(frame, groups, compact) {
    const axisWidth = Math.max(140, (frame.clientWidth || 320) - 52);
    const room = axisWidth / Math.max(1, groups);
    const groupPx = Math.min(room * 0.8, compact ? 52 : Math.max(108, room * 0.36));
    const gapPx = compact ? 2 : 4;
    const slot = groupPx / 2;
    return {
      categoryPercentage: Math.min(0.9, groupPx / room),
      barPercentage: Math.min(1, (slot - gapPx) / slot),
    };
  }

  function chart(runs, compact) {
    const list = chartRows(runs);
    if (!list.length || !window.Chart) return null;
    const box = document.createElement("div");
    box.className = compact ? "progress-chart is-compact" : "progress-chart";
    const frame = document.createElement("div");
    frame.className = "progress-plot";
    const canvas = document.createElement("canvas");
    const summary = list.map((item) => `${item.when || `${item.count} 题`}：原始分 ${item.raw}，平均每题 ${item.average}`).join("；");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", summary);
    frame.appendChild(canvas);
    const last = list[list.length - 1];
    const latest = document.createElement("p");
    latest.className = "note progress-latest";
    latest.textContent = `最近一次：原始 ${last.raw} · 平均 ${last.average}`;
    box.append(frame, latest);
    queueMicrotask(() => {
      if (!canvas.isConnected || window.Chart.getChart(canvas)) return;
      window.Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
      const peak = Math.max(...list.flatMap((item) => [item.raw, item.average]));
      new window.Chart(canvas, {
        type: "bar",
        data: {
          labels: list.map((item) => item.when || `${item.count} 题`),
          datasets: [
            {
              label: "原始分",
              data: list.map((item) => item.raw),
              backgroundColor: cssColor("--label", "#3a322b"),
              borderRadius: 6,
            },
            {
              label: "平均每题",
              data: list.map((item) => item.average),
              backgroundColor: cssColor("--zh", "#9a4510"),
              borderRadius: 6,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          datasets: {
            bar: barLayout(frame, list.length, compact),
          },
          plugins: {
            legend: {
              position: "top",
              align: "start",
              labels: { color: cssColor("--muted", "#6f675e"), boxWidth: 10, boxHeight: 10, font: { size: compact ? 11 : 12 } },
            },
          },
          scales: {
            x: {
              ticks: { color: cssColor("--muted", "#6f675e"), font: { size: compact ? 10 : 12 } },
              grid: { display: false },
              border: { color: cssColor("--line", "#e4dccf") },
            },
            y: {
              beginAtZero: true,
              suggestedMax: peak <= 100 ? 100 : undefined,
              ticks: { color: cssColor("--muted", "#6f675e"), precision: 0, font: { size: compact ? 10 : 11 } },
              grid: { color: cssColor("--line", "#e4dccf") },
              border: { display: false },
            },
          },
        },
      });
    });
    return box;
  }

  function renderFixes(fixes) {
    if (!Array.isArray(fixes)) return null;
    const box = document.createElement("div");
    box.className = "progress-fixes";
    const label = document.createElement("p");
    label.className = "block-label rank-2";
    label.textContent = "纠错";
    box.appendChild(label);
    const items = fixes.filter((item) => item && (item.spoken || "").trim() && (item.better || "").trim());
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "note";
      empty.textContent = "这句没有要改的地方。";
      box.appendChild(empty);
      return box;
    }
    const list = document.createElement("ul");
    list.className = "changes";
    items.forEach((item) => {
      const li = document.createElement("li");
      const spoken = document.createElement("p");
      spoken.className = "spoken";
      spoken.textContent = `你说：${item.spoken.trim()}`;
      const better = document.createElement("p");
      better.className = "better";
      better.textContent = `换成：${item.better.trim()}`;
      li.append(spoken, better);
      if ((item.note || "").trim()) {
        const note = document.createElement("p");
        note.className = "note";
        note.textContent = item.note.trim();
        li.appendChild(note);
      }
      list.appendChild(li);
    });
    box.appendChild(list);
    return box;
  }

  function answerCard(run, item, canRetake) {
    const card = document.createElement("article");
    card.className = "card progress-answer";
    if (retake === item.number) card.classList.add("is-retake");
    const head = document.createElement("div");
    head.className = "section-head";
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = `问题 ${item.number}`;
    head.appendChild(kicker);
    if (canRetake) {
      const actions = document.createElement("div");
      actions.className = "question-actions";
      const again = button(retake === item.number ? "取消重答" : "重答", "retake", "text-btn");
      again.dataset.retake = String(item.number);
      actions.appendChild(again);
      head.appendChild(actions);
    }
    card.appendChild(head);
    const prompt = item.prompt || {};
    if ((prompt.en || "").trim()) {
      const en = document.createElement("p");
      en.className = "en-line";
      en.textContent = prompt.en;
      card.appendChild(en);
    }
    if ((prompt.zh || "").trim()) {
      const zh = document.createElement("p");
      zh.className = "zh-line";
      zh.textContent = prompt.zh;
      card.appendChild(zh);
    }
    const drafts = item.drafts || [];
    if (drafts.length) {
      const title = document.createElement("p");
      title.className = "kicker";
      title.textContent = "草稿";
      card.appendChild(title);
      drafts.forEach((text) => {
        const line = document.createElement("p");
        line.className = "note";
        line.textContent = text;
        card.appendChild(line);
      });
    }
    if (retake === item.number) {
      const slot = document.createElement("div");
      slot.className = "retake-slot";
      slot.dataset.retakeSlot = String(item.number);
      card.appendChild(slot);
    }
    if ((item.transcript || "").trim()) {
      const said = document.createElement("p");
      said.className = "progress-said";
      said.textContent = item.transcript.trim();
      card.appendChild(said);
    }
    DIMENSIONS.forEach(([key, label]) => {
      const scores = item.scores || {};
      if (scores[key] == null || scores[key] === "") return;
      const notes = item.notes || {};
      const line = document.createElement("p");
      line.className = "note";
      line.textContent = notes[key] ? `${label} ${scores[key]}。${notes[key]}` : `${label} ${scores[key]}`;
      card.appendChild(line);
    });
    const fixes = renderFixes(item.fixes);
    if (fixes) card.appendChild(fixes);
    const total = document.createElement("p");
    total.className = "progress-total";
    const seconds = item.duration_ms ? ` · ${Math.max(1, Math.round(item.duration_ms / 1000))} 秒` : "";
    total.textContent = `这题 ${item.raw} 分${seconds}`;
    card.appendChild(total);
    if (item.has_audio && run && run.id) {
      const player = recordingPlayer(`/api/progress/${run.id}/audio/${item.number}`, item.duration_ms);
      if (player) card.appendChild(player);
    }
    return card;
  }

  function recordingPlayer(src, durationMs) {
    if (!window.QuickbackPlayer) return null;
    return window.QuickbackPlayer.create(src, "metadata", (durationMs || 0) / 1000);
  }

  function button(label, action, className) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = className;
    el.dataset.progressAction = action;
    el.textContent = label;
    return el;
  }

  function holdScroll(y) {
    const pin = () => window.scrollTo(0, y);
    pin();
    requestAnimationFrame(() => {
      pin();
      requestAnimationFrame(pin);
    });
  }

  function render(options) {
    if (!root) return;
    const scrollY = window.scrollY;
    if (document.activeElement && root.contains(document.activeElement)) document.activeElement.blur();
    root.replaceChildren();
    if (!questionId) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "在题目卡片上点「进展」。";
      root.appendChild(empty);
      poke();
      holdScroll(scrollY);
      return;
    }
    if (!view) {
      holdScroll(scrollY);
      return;
    }
    const question = view.question || {};
    const head = document.createElement("section");
    head.className = "card";
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = "进展";
    head.appendChild(kicker);
    if ((question.en || "").trim()) {
      const en = document.createElement("p");
      en.className = "en-line";
      en.textContent = question.en;
      head.appendChild(en);
    }
    if ((question.zh || "").trim()) {
      const zh = document.createElement("p");
      zh.className = "zh-line";
      zh.textContent = question.zh;
      head.appendChild(zh);
    }
    const runs = view.runs || [];
    root.appendChild(head);

    const open = view.open;
    if (!open) {
      const start = button(uploading ? "正在开始…" : "开始这次", "start", "primary");
      start.disabled = uploading;
      root.appendChild(start);
    } else {
      (open.answers || []).forEach((item) => root.appendChild(answerCard(open, item, true)));
      if (!open.pending_error && !take && open.pending_prompt) {
        const prompt = document.createElement("section");
        prompt.className = "card prompt";
        const label = document.createElement("p");
        label.className = "kicker";
        label.textContent = `问题 ${((open.answers || []).length || 0) + 1}`;
        prompt.appendChild(label);
        const asked = open.pending_prompt;
        if ((asked.en || "").trim()) {
          const en = document.createElement("p");
          en.className = "en-line";
          en.textContent = asked.en;
          prompt.appendChild(en);
        }
        if ((asked.zh || "").trim()) {
          const zh = document.createElement("p");
          zh.className = "zh-line";
          zh.textContent = asked.zh;
          prompt.appendChild(zh);
        }
        if (!retake) prompt.appendChild(draftPanel(open));
        root.appendChild(prompt);
      }
      if (retake) {
        const slot = root.querySelector(`[data-retake-slot="${retake}"]`);
        if (slot) slot.appendChild(retakeControls(open));
      } else if (open.pending_error) {
        const failed = document.createElement("p");
        failed.className = "error-line";
        failed.textContent = open.pending_error;
        root.appendChild(failed);
        const row = document.createElement("div");
        row.className = "progress-actions";
        row.append(button("重试", "retry", "primary"), button("重说", "clear", "text-btn"));
        root.appendChild(row);
      } else if (take) {
        root.appendChild(takeReview());
      } else if (!retake && open.pending_prompt) {
        const recorder = document.createElement("section");
        recorder.className = "recorder";
        const row = document.createElement("div");
        row.className = "record-row";
        const record = button(recording ? `说完了 ${formatClock(Date.now() - startedAt)}` : "开始说", "record", "record");
        record.classList.toggle("is-recording", recording);
        record.disabled = uploading;
        row.appendChild(record);
        if ((open.answers || []).length) {
          const finish = button("结束这次", "finish", "finish");
          finish.disabled = recording || uploading;
          row.appendChild(finish);
        }
        const quit = button("放弃这次", "discard", "progress-quit");
        quit.disabled = recording || uploading;
        row.appendChild(quit);
        const hint = document.createElement("p");
        hint.className = "hint";
        hint.textContent = "点一下开始，说完再点一下。听完再决定要不要送出。中文卡住了就直接说中文。最长 3 分钟。";
        recorder.append(row, hint);
        root.appendChild(recorder);
      } else if (!retake && (open.answers || []).length) {
        const done = document.createElement("p");
        done.className = "note";
        done.textContent = "这次可以结束了。";
        const row = document.createElement("div");
        row.className = "record-row";
        const finish = button("结束这次", "finish", "finish is-wide");
        const quit = button("放弃这次", "discard", "progress-quit");
        finish.disabled = uploading;
        quit.disabled = uploading;
        row.append(finish, quit);
        const recorder = document.createElement("section");
        recorder.className = "recorder";
        recorder.append(done, row);
        root.appendChild(recorder);
      }
      const actions = document.createElement("div");
      actions.className = "progress-actions";
      const finishBesideRecord = !retake && !open.pending_error && !take && open.pending_prompt && (open.answers || []).length;
      const readyToFinish = !retake && !open.pending_error && !take && !open.pending_prompt && (open.answers || []).length;
      if (!retake && (open.answers || []).length && !finishBesideRecord && !readyToFinish) actions.appendChild(button("结束这次", "finish", "text-btn"));
      if (!retake && !readyToFinish && (open.pending_error || take || !open.pending_prompt)) {
        actions.appendChild(button("放弃这次", "discard", "text-btn"));
      }
      actions.querySelectorAll("button").forEach((el) => {
        el.disabled = recording || uploading || Boolean(take);
      });
      if (actions.childElementCount) root.appendChild(actions);
    }

    appendHistory(root, runs);
    poke();
    if (options && options.focusDraft) {
      const input = root.querySelector(".draft-form input");
      if (input) input.focus({ preventScroll: true });
    }
    holdScroll(scrollY);
  }

  function takeReview() {
    const review = document.createElement("div");
    review.className = "take-review";
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = retake ? "听一遍。送出后会替换这一题的回答和分数。" : "这段还在本机。听一遍，满意再送出。";
    const player = recordingPlayer(takeUrl, take.durationMs);
    const actions = document.createElement("div");
    actions.className = "take-actions";
    actions.append(button(uploading ? "正在评分…" : "就用这段", "send", "primary"), button("重说", "local-redo", "text-btn"));
    review.append(hint, player, actions);
    return review;
  }

  function retakeControls(open) {
    const box = document.createElement("div");
    if (open.pending_error && open.pending_retake === retake) {
      const failed = document.createElement("p");
      failed.className = "error-line";
      failed.textContent = open.pending_error;
      const row = document.createElement("div");
      row.className = "progress-actions";
      row.append(button("重试", "retry", "primary"), button("重说", "clear", "text-btn"));
      box.append(failed, row);
      return box;
    }
    if (take) {
      box.appendChild(takeReview());
      return box;
    }
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "接下来的录音会替换这一题。";
    const recorder = document.createElement("section");
    recorder.className = "recorder";
    const row = document.createElement("div");
    row.className = "record-row";
    const record = button(recording ? `说完了 ${formatClock(Date.now() - startedAt)}` : "开始说", "record", "record");
    record.classList.toggle("is-recording", recording);
    record.disabled = uploading;
    row.appendChild(record);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "点一下开始，说完再点一下。送出后会替换这一题的回答和分数。";
    recorder.append(row, hint);
    box.append(note, recorder);
    return box;
  }

  function draftPanel(open) {
    const box = document.createElement("div");
    box.className = "progress-drafts";
    const label = document.createElement("p");
    label.className = "kicker";
    label.textContent = "草稿";
    box.appendChild(label);
    const drafts = open.drafts || [];
    if (drafts.length) {
      const list = document.createElement("ul");
      list.className = "draft-list";
      drafts.forEach((text, index) => {
        const item = document.createElement("li");
        item.className = "draft-item";
        const line = document.createElement("p");
        line.textContent = text;
        const remove = button("删除", "draft-remove", "text-btn");
        remove.dataset.draft = String(index);
        item.append(line, remove);
        list.appendChild(item);
      });
      box.appendChild(list);
    }
    if (drafts.length < 8) {
      const form = document.createElement("form");
      form.className = "draft-form";
      form.dataset.draftForm = "1";
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 120;
      input.placeholder = "写一条提示";
      input.setAttribute("aria-label", "提示");
      const add = document.createElement("button");
      add.type = "submit";
      add.className = "primary";
      add.textContent = "添加";
      form.append(input, add);
      box.appendChild(form);
    }
    return box;
  }

  function appendHistory(parent, runs) {
    (runs || []).forEach((run) => {
      const details = document.createElement("details");
      details.className = "echoes";
      const summary = document.createElement("summary");
      const stats = run.stats || {};
      const when = formatWhen(run.finished_at);
      summary.textContent = `${when} · 原始 ${stats.raw} · 平均 ${stats.average} · ${stats.count} 题`;
      details.appendChild(summary);
      (run.answers || []).forEach((item) => details.appendChild(answerCard(run, item)));
      parent.appendChild(details);
    });
  }

  function renderStats(page, onBack) {
    if (!statsRoot) return;
    releaseCharts(statsRoot);
    statsRoot.replaceChildren();
    const question = (page && page.question) || {};
    const head = document.createElement("section");
    head.className = "card";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "text-btn progress-back";
    back.textContent = "返回题目";
    back.addEventListener("click", () => {
      if (onBack) onBack();
    });
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = "进展统计";
    head.append(back, kicker);
    if ((question.en || "").trim()) {
      const en = document.createElement("p");
      en.className = "en-line";
      en.textContent = question.en;
      head.appendChild(en);
    }
    if ((question.zh || "").trim()) {
      const zh = document.createElement("p");
      zh.className = "zh-line";
      zh.textContent = question.zh;
      head.appendChild(zh);
    }
    const runs = (page && page.runs) || [];
    const drawn = chart(runs.filter((item) => item.stats), false);
    if (drawn) head.appendChild(drawn);
    else {
      const empty = document.createElement("p");
      empty.className = "note";
      empty.textContent = "还没有进展。";
      head.appendChild(empty);
    }
    statsRoot.appendChild(head);
    appendHistory(statsRoot, runs);
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

  function stop() {
    if (recorder && recorder.state === "recording") recorder.stop();
  }

  async function start() {
    if (recording || uploading || take) return;
    if (window.QuickbackPracticeBusy && window.QuickbackPracticeBusy()) {
      report("先说完正在录的这一段。");
      return;
    }
    const blocked = recordingBlock();
    if (blocked) {
      report(blocked);
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      report("没有拿到麦克风权限。");
      return;
    }
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    mimeType = candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
    chunks = [];
    recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    recorder.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data);
    };
    const mine = generation;
    recorder.onstop = () => {
      const durationMs = Date.now() - startedAt;
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
      stopStream();
      recording = false;
      stopTimer();
      if (ignoreStop || mine !== generation) {
        ignoreStop = false;
        poke();
        return;
      }
      if (durationMs < 400 || blob.size < 400) {
        report("录音太短了，再说长一点。");
        render();
        return;
      }
      setTake(blob, durationMs);
      report("");
      render();
    };
    ignoreStop = false;
    recorder.start();
    recording = true;
    startedAt = Date.now();
    report("");
    render();
    timer = setInterval(() => {
      const label = root && root.querySelector("[data-progress-action='record']");
      if (label && recording) label.textContent = `说完了 ${formatClock(Date.now() - startedAt)}`;
      if (Date.now() - startedAt >= MAX_MS) stop();
    }, 250);
  }

  async function reload(refreshCards) {
    view = await api(`/api/questions/${encodeURIComponent(questionId)}/progress`);
    render();
    if (refreshCards && window.QuickbackQuestions && window.QuickbackQuestions.refresh) {
      await window.QuickbackQuestions.refresh();
    }
  }

  async function send() {
    if (!take || uploading || !view || !view.open) return;
    const form = new FormData();
    const ext = (take.blob.type || "").includes("mp4") ? "m4a" : "webm";
    form.append("audio", take.blob, `take.${ext}`);
    form.append("mime_type", take.blob.type || "audio/webm");
    form.append("duration_ms", String(take.durationMs || 0));
    uploading = true;
    render();
    try {
      const path = retake
        ? `/api/progress/${view.open.id}/answers/${retake}/retake`
        : `/api/progress/${view.open.id}/answers`;
      view = await api(path, { method: "POST", body: form });
      retake = null;
      setTake(null, 0);
      report("");
      render();
    } catch (error) {
      report(error.message);
      try {
        view = await api(`/api/questions/${encodeURIComponent(questionId)}/progress`);
      } catch (loadError) {
        report(loadError.message);
      }
      if (view && view.open && view.open.pending_error) setTake(null, 0);
      render();
    } finally {
      uploading = false;
      render();
    }
  }

  async function act(action) {
    if (!view || !view.open || recording || uploading) return;
    if (action === "local-redo") {
      setTake(null, 0);
      report("");
      render();
      return;
    }
    if (take) return;
    if (action === "retry") {
      uploading = true;
      render();
      try {
        view = await api(`/api/progress/${view.open.id}/retry`, { method: "POST" });
        report("");
      } catch (error) {
        report(error.message);
      } finally {
        uploading = false;
        render();
      }
      return;
    }
    if (action === "clear") {
      try {
        view = await api(`/api/progress/${view.open.id}/redo`, { method: "POST" });
      } catch (error) {
        report(error.message);
        return;
      }
      setTake(null, 0);
      report("");
      render();
      return;
    }
    if (action === "finish") {
      try {
        view = await api(`/api/progress/${view.open.id}/finish`, { method: "POST" });
        report("");
        const finishedId = questionId;
        if (afterFinish) await afterFinish(finishedId);
        else render();
        if (window.QuickbackQuestions && window.QuickbackQuestions.refresh) await window.QuickbackQuestions.refresh();
      } catch (error) {
        report(error.message);
      }
      return;
    }
    if (action === "discard") {
      const ask = window.quickbackConfirm || ((text) => Promise.resolve(window.confirm(text)));
      if (!await ask("放弃这次进展？已经答过的题不会记入统计。")) return;
      try {
        view = await api(`/api/progress/${view.open.id}`, { method: "DELETE" });
        setTake(null, 0);
        report("");
        render();
      } catch (error) {
        report(error.message);
      }
    }
  }

  async function saveDraft(text) {
    if (!view || !view.open || uploading) return;
    try {
      view = await api(`/api/progress/${view.open.id}/drafts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      report("");
      render({ focusDraft: true });
    } catch (error) {
      report(error.message);
    }
  }

  async function removeDraft(index) {
    if (!view || !view.open || uploading) return;
    try {
      view = await api(`/api/progress/${view.open.id}/drafts/${index}`, { method: "DELETE" });
      report("");
      render({ focusDraft: true });
    } catch (error) {
      report(error.message);
    }
  }

  if (root) {
    root.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-draft-form]");
      if (!form) return;
      event.preventDefault();
      const input = form.querySelector("input");
      saveDraft(input ? input.value : "");
    });
    root.addEventListener("click", (event) => {
      const target = event.target.closest("[data-progress-action]");
      if (!target || target.disabled) return;
      const action = target.dataset.progressAction;
      if (action === "draft-remove") {
        removeDraft(Number(target.dataset.draft));
        return;
      }
      if (action === "retake") {
        if (recording || uploading) return;
        const number = Number(target.dataset.retake);
        setTake(null, 0);
        retake = retake === number ? null : number;
        report("");
        render();
        return;
      }
      if (action === "record") {
        if (recording) stop();
        else start();
        return;
      }
      if (action === "start") {
        if (recording || uploading || take) return;
        uploading = true;
        render();
        api(`/api/questions/${encodeURIComponent(questionId)}/progress`, { method: "POST" })
          .then((page) => {
            view = page;
            report("");
          })
          .catch((error) => report(error.message))
          .finally(() => {
            uploading = false;
            render();
          });
        return;
      }
      if (action === "send") {
        send();
        return;
      }
      act(action);
    });
  }

  window.QuickbackProgressChart = chart;
  window.QuickbackProgress = {
    busy() {
      return recording || uploading || Boolean(take);
    },
    questionId() {
      return questionId;
    },
    async show(request, notify, id, done) {
      const same = id === questionId && view;
      api = request;
      report = notify;
      afterFinish = typeof done === "function" ? done : null;
      if (id !== questionId) {
        generation += 1;
        ignoreStop = recording;
        if (recording) stop();
        setTake(null, 0);
        retake = null;
      }
      questionId = id || "";
      if (!questionId) {
        view = null;
        render();
        return;
      }
      if (same && (take || recording)) {
        render();
        return;
      }
      setTake(null, 0);
      view = null;
      render();
      const waiting = document.createElement("p");
      waiting.className = "note";
      waiting.textContent = "正在读取…";
      root.appendChild(waiting);
      try {
        await reload(false);
        report("");
      } catch (error) {
        report(error.message);
      }
    },
    clear() {
      generation += 1;
      if (recording) {
        ignoreStop = true;
        if (recorder && recorder.state === "recording") recorder.stop();
      }
      stopStream();
      stopTimer();
      recording = false;
      uploading = false;
      questionId = "";
      view = null;
      retake = null;
      setTake(null, 0);
      render();
    },
    leave() {
      if (recording) stop();
    },
    async showStats(request, notify, id, onBack) {
      if (!id) {
        renderStats(null, onBack);
        return;
      }
      if (statsRoot) {
        statsRoot.replaceChildren();
        const waiting = document.createElement("p");
        waiting.className = "note";
        waiting.textContent = "正在读取…";
        statsRoot.appendChild(waiting);
      }
      try {
        const page = await request(`/api/questions/${encodeURIComponent(id)}/progress`);
        renderStats(page, onBack);
        if (notify) notify("");
      } catch (error) {
        if (notify) notify(error.message);
      }
    },
  };
})();
