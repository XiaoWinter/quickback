(function () {
  const form = document.getElementById("question-form");
  const list = document.getElementById("question-list");
  const ieltsList = document.getElementById("question-ielts-list");
  const openFormBtn = document.getElementById("question-open-form");
  const openIeltsBtn = document.getElementById("question-open-ielts");
  const openHistoryBtn = document.getElementById("question-open-history");
  const ieltsPanel = document.getElementById("question-ielts-panel");
  const ieltsGenBtn = document.getElementById("question-ielts-gen");
  const ieltsReshuffleBtn = document.getElementById("question-ielts-reshuffle");
  const ieltsCommitBtn = document.getElementById("question-ielts-commit");
  const ieltsFooter = document.getElementById("question-ielts-footer");
  const saveBtn = form ? form.querySelector('button[type="submit"]') : null;
  const IELTS_BATCH_KEY = "quickback.ieltsBatch";
  const IELTS_ORDER = ["口语1", "口语2", "口语3"];
  const IELTS_PART = { 口语1: "part1", 口语2: "part2", 口语3: "part3" };
  let api = null;
  let report = null;
  let answer = null;
  let removed = null;
  let openProgress = null;
  let openStats = null;
  let openHistory = null;
  let editingId = null;
  let currentId = "";
  let busy = false;
  let generatingIelts = false;
  let refreshingIeltsId = "";
  let lastIeltsIds = [];
  let cachedQuestions = [];
  let closedFilter = "all";
  const CLOSED_FILTER_KEY = "quickback.questionClosedFilter";
  const closedFilterEl = document.getElementById("question-closed-filter");

  function readClosedFilter() {
    try {
      const raw = sessionStorage.getItem(CLOSED_FILTER_KEY) || "all";
      return raw === "open" || raw === "closed" ? raw : "all";
    } catch (error) {
      return "all";
    }
  }

  function writeClosedFilter(next) {
    closedFilter = next === "open" || next === "closed" ? next : "all";
    try {
      sessionStorage.setItem(CLOSED_FILTER_KEY, closedFilter);
    } catch (error) {
      /* ignore */
    }
    syncClosedFilter();
  }

  function syncClosedFilter() {
    if (!closedFilterEl) return;
    closedFilterEl.querySelectorAll("[data-closed-filter]").forEach((btn) => {
      const on = btn.dataset.closedFilter === closedFilter;
      btn.classList.toggle("is-on", on);
      if (on) btn.setAttribute("aria-pressed", "true");
      else btn.setAttribute("aria-pressed", "false");
    });
  }

  function filterLibrary(library) {
    if (closedFilter === "open") return library.filter((item) => !item.closed);
    if (closedFilter === "closed") return library.filter((item) => item.closed);
    return library;
  }

  function readIeltsBatch() {
    try {
      const raw = sessionStorage.getItem(IELTS_BATCH_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.map((item) => String(item || "")).filter(Boolean) : [];
    } catch (error) {
      return [];
    }
  }

  function writeIeltsBatch(ids) {
    lastIeltsIds = Array.isArray(ids) ? ids.map((item) => String(item || "")).filter(Boolean) : [];
    try {
      if (lastIeltsIds.length) sessionStorage.setItem(IELTS_BATCH_KEY, JSON.stringify(lastIeltsIds));
      else sessionStorage.removeItem(IELTS_BATCH_KEY);
    } catch (error) {
      /* ignore quota / private mode */
    }
    syncIeltsButtons();
  }

  function syncIeltsButtons() {
    const hasBatch = lastIeltsIds.length > 0;
    if (ieltsFooter) ieltsFooter.hidden = !(hasBatch || generatingIelts);
    if (ieltsReshuffleBtn) ieltsReshuffleBtn.disabled = busy;
    if (ieltsCommitBtn) {
      ieltsCommitBtn.hidden = !hasBatch || generatingIelts;
      ieltsCommitBtn.disabled = busy;
    }
    if (ieltsGenBtn) ieltsGenBtn.disabled = busy;
  }

  function toast(text) {
    if (window.quickbackToast) window.quickbackToast(text);
    else if (report) report(text);
  }

  function commitIeltsBatch() {
    if (busy || !lastIeltsIds.length) return;
    const count = lastIeltsIds.length;
    writeIeltsBatch([]);
    if (report) report("");
    toast(`已将 ${count} 道口语题加入已有题目。`);
    render(cachedQuestions);
    closeIeltsPanel();
  }

  function syncQuestionFormBtn() {
    if (!openFormBtn) return;
    const open = Boolean(form && !form.hidden);
    openFormBtn.classList.toggle("is-active", open);
    openFormBtn.setAttribute("aria-expanded", open ? "true" : "false");
    openFormBtn.setAttribute("aria-pressed", open ? "true" : "false");
  }

  function openQuestionForm() {
    if (!form || busy) return;
    closeIeltsPanel();
    form.hidden = false;
    syncQuestionFormBtn();
    form.querySelector('[name="en"]')?.focus();
  }

  function closeQuestionForm() {
    if (!form) return;
    form.hidden = true;
    form.reset();
    syncQuestionFormBtn();
  }

  function toggleQuestionForm() {
    if (!form || busy) return;
    if (form.hidden) openQuestionForm();
    else {
      closeQuestionForm();
      if (report) report("");
    }
  }

  function syncIeltsBtn() {
    if (!openIeltsBtn) return;
    const open = Boolean(ieltsPanel && !ieltsPanel.hidden);
    openIeltsBtn.classList.toggle("is-active", open);
    openIeltsBtn.setAttribute("aria-expanded", open ? "true" : "false");
    openIeltsBtn.setAttribute("aria-pressed", open ? "true" : "false");
  }

  function openIeltsPanel() {
    if (!ieltsPanel || busy) return;
    closeQuestionForm();
    ieltsPanel.hidden = false;
    syncIeltsBtn();
  }

  function closeIeltsPanel() {
    if (!ieltsPanel || busy) return;
    ieltsPanel.hidden = true;
    syncIeltsBtn();
  }

  function toggleIeltsPanel() {
    if (!ieltsPanel || busy) return;
    if (ieltsPanel.hidden) {
      openIeltsPanel();
      if (report) report("");
    } else {
      closeIeltsPanel();
      if (report) report("");
    }
  }

  function formatWhen(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
  }

  function categoryTag(category) {
    const tag = document.createElement("span");
    const text = String(category || "").trim();
    tag.className = "question-tag" + (text ? "" : " is-empty");
    tag.textContent = text || "无类别";
    return tag;
  }

  function cardWhen(question) {
    const when = document.createElement("span");
    when.className = "card-when";
    const created = formatWhen(question.created_at);
    const updated = formatWhen(question.updated_at);
    when.textContent = `创建 ${created} · 修改 ${updated}`;
    return when;
  }

  function metaLine(question) {
    const line = document.createElement("p");
    line.className = "question-meta";
    line.append(categoryTag(question.category), document.createTextNode(" · "));
    const status = document.createElement("span");
    status.className = question.closed ? "question-status is-done" : "question-status";
    status.textContent = question.closed ? "归档" : "未归档";
    line.append(status);
    if (question.id === currentId) {
      const mark = document.createElement("span");
      mark.className = "question-current";
      mark.textContent = "当前";
      line.append(document.createTextNode(" · "), mark);
    }
    line.appendChild(cardWhen(question));
    return line;
  }

  function field(label, name, value, options) {
    const opts = options || {};
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.name = name;
    input.value = value || "";
    input.required = Boolean(opts.required);
    input.maxLength = opts.maxLength || 240;
    if (opts.placeholder) input.placeholder = opts.placeholder;
    if (opts.autocomplete) input.setAttribute("autocomplete", opts.autocomplete);
    wrap.appendChild(input);
    return wrap;
  }

  function categoryChoices() {
    const found = new Set(IELTS_ORDER);
    cachedQuestions.forEach((item) => {
      const text = String(item.category || "").trim();
      if (text) found.add(text);
    });
    return Array.from(found).sort((a, b) => {
      const ai = IELTS_ORDER.indexOf(a);
      const bi = IELTS_ORDER.indexOf(b);
      if (ai >= 0 || bi >= 0) {
        if (ai < 0) return 1;
        if (bi < 0) return -1;
        return ai - bi;
      }
      return a.localeCompare(b, "zh");
    });
  }

  function paintCategoryPicks(root, input) {
    if (!root || !input) return;
    const current = String(input.value || "").trim();
    root.replaceChildren();
    const choices = categoryChoices();
    if (!choices.length) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    choices.forEach((text) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "question-category-pick" + (current === text ? " is-on" : "");
      btn.textContent = text;
      btn.addEventListener("click", () => {
        input.value = current === text ? "" : text;
        paintCategoryPicks(root, input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      root.appendChild(btn);
    });
  }

  function bindCategoryPicks(root, input) {
    if (!root || !input || input.dataset.categoryPicksBound) return;
    input.dataset.categoryPicksBound = "1";
    input.addEventListener("input", () => paintCategoryPicks(root, input));
    paintCategoryPicks(root, input);
  }

  function refreshCategoryOptions() {
    const formInput = form ? form.querySelector('[name="category"]') : null;
    const formPicks = document.getElementById("question-category-picks");
    if (formInput && formPicks) {
      bindCategoryPicks(formPicks, formInput);
      paintCategoryPicks(formPicks, formInput);
    }
    if (list) {
      list.querySelectorAll(".question-item [name='category']").forEach((input) => {
        const picks = input.closest(".question-item")?.querySelector(".question-category-picks");
        if (picks) paintCategoryPicks(picks, input);
      });
    }
  }

  function questionBody(question) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "question-open";
    open.dataset.answer = question.id;
    const en = document.createElement("p");
    en.className = "en-line";
    en.textContent = question.en;
    const zh = document.createElement("p");
    zh.className = "zh-line";
    zh.textContent = question.zh;
    open.append(en, zh);
    return open;
  }

  function ieltsBody(question) {
    const box = document.createElement("div");
    box.className = "question-ielts-body";
    const en = document.createElement("p");
    en.className = "en-line";
    en.textContent = question.en;
    const zh = document.createElement("p");
    zh.className = "zh-line";
    zh.textContent = question.zh;
    box.append(en, zh);
    return box;
  }

  function buildIeltsCard(question) {
    const card = document.createElement("article");
    card.className = "card question-item question-ielts-item";
    card.dataset.id = question.id;
    const head = document.createElement("div");
    head.className = "section-head question-ielts-card-head";
    head.appendChild(categoryTag(question.category));
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "text-btn question-ielts-refresh";
    refresh.dataset.ieltsRefresh = question.id;
    refresh.dataset.ieltsPart = IELTS_PART[question.category] || "";
    const pending = refreshingIeltsId === question.id;
    refresh.disabled = busy || pending || generatingIelts;
    refresh.textContent = pending ? "刷新中…" : "刷新";
    head.appendChild(refresh);
    card.append(head, ieltsBody(question));
    return card;
  }

  function currentIeltsBatch() {
    return splitQuestions(cachedQuestions).batch;
  }

  function paintIelts() {
    renderIeltsBatch(currentIeltsBatch());
  }

  function ieltsRefreshButtons() {
    return ieltsList ? Array.from(ieltsList.querySelectorAll("[data-ielts-refresh]")) : [];
  }

  function markIeltsRefreshing(questionId) {
    ieltsRefreshButtons().forEach((btn) => {
      const active = btn.dataset.ieltsRefresh === questionId;
      if (active) btn.blur();
      btn.disabled = true;
      btn.textContent = active ? "刷新中…" : "刷新";
    });
  }

  function clearIeltsRefreshing() {
    ieltsRefreshButtons().forEach((btn) => {
      btn.disabled = false;
      btn.textContent = "刷新";
    });
  }

  function patchIeltsCard(oldId, question) {
    if (!ieltsList || !question) return false;
    const card = ieltsList.querySelector(`.question-ielts-item[data-id="${oldId}"]`);
    if (!card) return false;
    card.dataset.id = question.id;
    const tag = card.querySelector(".question-tag");
    if (tag) {
      const text = String(question.category || "").trim();
      tag.className = "question-tag" + (text ? "" : " is-empty");
      tag.textContent = text || "无类别";
    }
    const refresh = card.querySelector("[data-ielts-refresh]");
    if (refresh) {
      refresh.dataset.ieltsRefresh = question.id;
      refresh.dataset.ieltsPart = IELTS_PART[question.category] || "";
      refresh.disabled = false;
      refresh.textContent = "刷新";
    }
    const en = card.querySelector(".en-line");
    const zh = card.querySelector(".zh-line");
    if (en) en.textContent = question.en;
    if (zh) zh.textContent = question.zh;
    return true;
  }

  function buildCard(question) {
    const card = document.createElement("article");
    card.className = "card question-item has-card-when";
    card.dataset.id = question.id;
    const head = document.createElement("div");
    head.className = "section-head";
    head.appendChild(metaLine(question));
    card.appendChild(head);
    if (editingId === question.id) {
      card.append(
        field("英文问题", "en", question.en, { required: true }),
        field("中文意思", "zh", question.zh, { required: true }),
        field("类别", "category", question.category || "", {
          required: false,
          maxLength: 20,
          placeholder: "可空，点选或自行输入",
          autocomplete: "off",
        }),
      );
      const categoryInput = card.querySelector('[name="category"]');
      const picks = document.createElement("div");
      picks.className = "question-category-picks";
      if (categoryInput && categoryInput.parentElement) {
        categoryInput.parentElement.insertAdjacentElement("afterend", picks);
        bindCategoryPicks(picks, categoryInput);
      }
      const actions = document.createElement("div");
      actions.className = "question-actions";
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.dataset.save = question.id;
      save.textContent = "保存";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "text-btn";
      cancel.dataset.cancel = question.id;
      cancel.textContent = "取消";
      actions.append(save, cancel);
      card.appendChild(actions);
      return card;
    }
    const actions = document.createElement("div");
    actions.className = "question-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "text-btn";
    edit.dataset.edit = question.id;
    edit.textContent = "编辑";
    const progressBtn = document.createElement("button");
    progressBtn.type = "button";
    progressBtn.className = "text-btn";
    progressBtn.dataset.progress = question.id;
    progressBtn.textContent = "进展";
    const statsBtn = document.createElement("button");
    statsBtn.type = "button";
    statsBtn.className = "text-btn";
    statsBtn.dataset.progressStats = question.id;
    statsBtn.textContent = `进展统计 ${(question.progress || []).length}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-btn";
    remove.dataset.delete = question.id;
    remove.textContent = "删除";
    actions.append(edit, progressBtn, statsBtn, remove);
    head.appendChild(actions);
    card.appendChild(questionBody(question));
    return card;
  }

  function splitQuestions(questions) {
    const batchIds = new Set(lastIeltsIds);
    const byId = new Map(questions.map((item) => [item.id, item]));
    const batch = lastIeltsIds.map((id) => byId.get(id)).filter(Boolean);
    const rest = questions.filter((item) => !batchIds.has(item.id));
    if (currentId) {
      const current = rest.filter((item) => item.id === currentId);
      return {
        batch,
        library: current.concat(rest.filter((item) => item.id !== currentId)),
      };
    }
    return { batch, library: rest };
  }

  function renderIeltsPending() {
    if (!ieltsList) return;
    const box = document.createElement("div");
    box.className = "question-ielts-pending";
    IELTS_ORDER.forEach((label) => {
      const row = document.createElement("p");
      row.className = "question-ielts-pending-item";
      row.append(categoryTag(label), document.createTextNode("生成中…"));
      box.appendChild(row);
    });
    ieltsList.replaceChildren(box);
  }

  function renderIeltsEmpty() {
    if (!ieltsList) return;
    const empty = document.createElement("p");
    empty.className = "question-ielts-empty";
    empty.textContent = "点「生成口语123」后，本批题目会出现在这里。";
    ieltsList.replaceChildren(empty);
  }

  function renderIeltsBatch(batch) {
    if (!ieltsList) return;
    if (generatingIelts) {
      renderIeltsPending();
      return;
    }
    if (!batch.length) {
      renderIeltsEmpty();
      return;
    }
    const ordered = IELTS_ORDER.map((label) => batch.find((item) => item.category === label))
      .filter(Boolean)
      .concat(batch.filter((item) => !IELTS_ORDER.includes(item.category)));
    const seen = new Set();
    ieltsList.replaceChildren();
    ordered.forEach((question) => {
      if (seen.has(question.id)) return;
      seen.add(question.id);
      ieltsList.appendChild(buildIeltsCard(question));
    });
  }

  function render(questions) {
    cachedQuestions = questions || [];
    if (lastIeltsIds.length) {
      const alive = new Set(cachedQuestions.map((item) => item.id));
      const next = lastIeltsIds.filter((id) => alive.has(id));
      if (next.length !== lastIeltsIds.length) writeIeltsBatch(next);
    }
    refreshCategoryOptions();
    const { batch, library } = splitQuestions(cachedQuestions);
    renderIeltsBatch(batch);
    if (!list) return;
    list.replaceChildren();
    const shown = filterLibrary(library);
    if (!shown.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      if (!library.length) empty.textContent = "还没有题目。";
      else if (closedFilter === "open") empty.textContent = "没有未归档题目。";
      else if (closedFilter === "closed") empty.textContent = "没有归档题目。";
      else empty.textContent = "还没有题目。";
      list.appendChild(empty);
      return;
    }
    shown.forEach((question) => list.appendChild(buildCard(question)));
  }

  async function reload() {
    const questions = await api("/api/questions");
    try {
      const practice = await api("/api/practice");
      const sessionQuestion = practice.session && practice.session.question;
      currentId = (sessionQuestion && sessionQuestion.id) || (practice.question && practice.question.id) || "";
    } catch (error) {
      currentId = "";
    }
    render(questions);
  }

  async function generateIeltsSpeaking(replace) {
    if (busy) return;
    const scrollY = window.scrollY;
    busy = true;
    generatingIelts = true;
    refreshingIeltsId = "";
    renderIeltsPending();
    syncIeltsButtons();
    if (ieltsGenBtn && !replace) ieltsGenBtn.textContent = "生成中…";
    if (ieltsReshuffleBtn && replace) ieltsReshuffleBtn.textContent = "换一批中…";
    try {
      const body = replace && lastIeltsIds.length
        ? { replace_ids: lastIeltsIds.slice() }
        : {};
      const made = await api("/api/questions/ielts-speaking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      writeIeltsBatch((made || []).map((item) => item.id));
      if (report) report("");
      toast(replace ? "已换一批口语1、口语2、口语3。" : "已生成口语1、口语2、口语3 各一道。");
      generatingIelts = false;
      busy = false;
      await reload();
      window.scrollTo(0, scrollY);
    } catch (error) {
      generatingIelts = false;
      report(error.message);
      render(cachedQuestions);
    } finally {
      busy = false;
      generatingIelts = false;
      if (ieltsGenBtn) {
        ieltsGenBtn.disabled = false;
        ieltsGenBtn.textContent = "生成口语123";
      }
      if (ieltsReshuffleBtn) ieltsReshuffleBtn.textContent = "换一批";
      syncIeltsButtons();
      paintIelts();
      window.scrollTo(0, scrollY);
    }
  }

  async function refreshIeltsOne(questionId, part) {
    if (busy || !questionId || !part) return;
    busy = true;
    refreshingIeltsId = questionId;
    markIeltsRefreshing(questionId);
    syncIeltsButtons();
    try {
      const made = await api("/api/questions/ielts-speaking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ part, replace_id: questionId }),
      });
      const next = made && made[0];
      if (next && next.id) {
        cachedQuestions = [next].concat(cachedQuestions.filter((item) => item.id !== questionId));
        writeIeltsBatch(lastIeltsIds.map((id) => (id === questionId ? next.id : id)));
        if (!patchIeltsCard(questionId, next)) paintIelts();
        if (report) report("");
        toast(`已刷新${next.category || "这道题"}。`);
      }
    } catch (error) {
      report(error.message);
    } finally {
      busy = false;
      refreshingIeltsId = "";
      clearIeltsRefreshing();
      syncIeltsButtons();
    }
  }

  lastIeltsIds = readIeltsBatch();
  syncIeltsButtons();
  renderIeltsEmpty();

  if (openFormBtn) {
    openFormBtn.addEventListener("click", () => toggleQuestionForm());
    syncQuestionFormBtn();
  }
  if (openIeltsBtn) {
    openIeltsBtn.addEventListener("click", () => toggleIeltsPanel());
    syncIeltsBtn();
  }
  if (openHistoryBtn) {
    openHistoryBtn.addEventListener("click", async () => {
      if (busy || !openHistory) return;
      try {
        await openHistory();
      } catch (error) {
        if (report) report(error.message);
      }
    });
  }
  if (ieltsGenBtn) {
    ieltsGenBtn.addEventListener("click", () => generateIeltsSpeaking(false));
  }
  if (ieltsReshuffleBtn) {
    ieltsReshuffleBtn.addEventListener("click", () => generateIeltsSpeaking(true));
  }
  if (ieltsCommitBtn) {
    ieltsCommitBtn.addEventListener("click", () => commitIeltsBatch());
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const data = new FormData(form);
    busy = true;
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
    }
    try {
      await api("/api/questions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          en: data.get("en"),
          zh: data.get("zh"),
          category: data.get("category") || "",
        }),
      });
      report("");
      closeQuestionForm();
      await reload();
    } catch (error) {
      report(error.message);
    } finally {
      busy = false;
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "保存";
      }
    }
  });

  async function onLibraryClick(event) {
    const stats = event.target.closest("[data-progress-stats]");
    if (stats) {
      if (openStats) await openStats(stats.dataset.progressStats);
      return;
    }
    const progress = event.target.closest("[data-progress]");
    if (progress) {
      if (openProgress) await openProgress(progress.dataset.progress);
      return;
    }
    const use = event.target.closest("[data-answer]");
    const edit = event.target.closest("[data-edit]");
    const remove = event.target.closest("[data-delete]");
    const save = event.target.closest("[data-save]");
    const cancel = event.target.closest("[data-cancel]");
    try {
      if (use) {
        if (answer) await answer(use.dataset.answer);
        return;
      }
      if (edit) {
        editingId = edit.dataset.edit;
        render(cachedQuestions);
        return;
      }
      if (cancel) {
        editingId = null;
        render(cachedQuestions);
        return;
      }
      if (save) {
        const card = save.closest(".question-item");
        await api(`/api/questions/${save.dataset.save}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            en: card.querySelector('[name="en"]').value,
            zh: card.querySelector('[name="zh"]').value,
            category: (card.querySelector('[name="category"]') || {}).value || "",
          }),
        });
        editingId = null;
        report("");
        await reload();
        return;
      }
      if (remove) {
        const ask = window.quickbackConfirm || ((text) => Promise.resolve(window.confirm(text)));
        if (!await ask("删掉这道题？它的追问、进展和录音也会一起删掉。")) return;
        const deletedId = remove.dataset.delete;
        await api(`/api/questions/${deletedId}`, { method: "DELETE" });
        if (editingId === deletedId) editingId = null;
        if (removed) await removed(deletedId);
        report("");
        await reload();
      }
    } catch (error) {
      report(error.message);
    }
  }

  async function onIeltsClick(event) {
    const refresh = event.target.closest("[data-ielts-refresh]");
    if (!refresh) return;
    event.preventDefault();
    event.stopPropagation();
    await refreshIeltsOne(refresh.dataset.ieltsRefresh, refresh.dataset.ieltsPart);
  }

  if (list) list.addEventListener("click", onLibraryClick);
  if (ieltsList) ieltsList.addEventListener("click", onIeltsClick);
  if (closedFilterEl) {
    closedFilter = readClosedFilter();
    syncClosedFilter();
    closedFilterEl.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-closed-filter]");
      if (!btn) return;
      writeClosedFilter(btn.dataset.closedFilter || "all");
      render(cachedQuestions);
    });
  }

  window.QuickbackQuestions = {
    async show(request, notify, onAnswer, onRemoved, onProgress, onStats, onHistory) {
      api = request;
      report = notify;
      answer = onAnswer;
      removed = onRemoved;
      openProgress = onProgress;
      openStats = onStats;
      openHistory = onHistory;
      await reload();
      closeIeltsPanel();
    },
    async refresh() {
      if (!api) return;
      await reload();
    },
  };
})();
