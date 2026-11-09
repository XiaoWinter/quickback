(function () {
  const form = document.getElementById("question-form");
  const list = document.getElementById("question-list");
  let api = null;
  let report = null;
  let answer = null;
  let removed = null;
  let openProgress = null;
  let openStats = null;
  let editingId = null;
  let currentId = "";

  function formatWhen(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
  }

  function metaLine(question) {
    const line = document.createElement("p");
    line.className = "question-meta";
    const status = document.createElement("span");
    status.className = question.closed ? "question-status is-done" : "question-status";
    status.textContent = question.closed ? "归档" : "未归档";
    line.append(status, document.createTextNode(" · "));
    if (question.id === currentId) {
      const mark = document.createElement("span");
      mark.className = "question-current";
      mark.textContent = "当前";
      line.append(mark, document.createTextNode(" · "));
    }
    const created = formatWhen(question.created_at);
    const updated = formatWhen(question.updated_at);
    line.append(document.createTextNode(`创建 ${created} · 修改 ${updated}`));
    return line;
  }

  function field(label, name, value) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.name = name;
    input.value = value;
    input.required = true;
    input.maxLength = 240;
    wrap.appendChild(input);
    return wrap;
  }

  function orderedQuestions(questions) {
    if (!currentId) return questions;
    const current = questions.filter((item) => item.id === currentId);
    return current.concat(questions.filter((item) => item.id !== currentId));
  }

  function render(questions) {
    list.replaceChildren();
    orderedQuestions(questions).forEach((question) => {
      const card = document.createElement("article");
      card.className = "card question-item";
      card.dataset.id = question.id;
      const head = document.createElement("div");
      head.className = "section-head";
      head.appendChild(metaLine(question));
      card.appendChild(head);
      if (editingId === question.id) {
        card.append(field("英文问题", "en", question.en), field("中文意思", "zh", question.zh));
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
      } else {
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
        const actions = document.createElement("div");
        actions.className = "question-actions";
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
        actions.append(progressBtn, statsBtn, remove);
        head.appendChild(actions);
        card.appendChild(open);
      }
      list.appendChild(card);
    });
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

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await api("/api/questions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ en: data.get("en"), zh: data.get("zh") }),
      });
      form.reset();
      report("");
      await reload();
    } catch (error) {
      report(error.message);
    }
  });

  list.addEventListener("click", async (event) => {
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
        await reload();
        return;
      }
      if (cancel) {
        editingId = null;
        await reload();
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
        await api(`/api/questions/${remove.dataset.delete}`, { method: "DELETE" });
        if (editingId === remove.dataset.delete) editingId = null;
        if (removed) await removed(remove.dataset.delete);
        report("");
        await reload();
      }
    } catch (error) {
      report(error.message);
    }
  });

  window.QuickbackQuestions = {
    async show(request, notify, onAnswer, onRemoved, onProgress, onStats) {
      api = request;
      report = notify;
      answer = onAnswer;
      removed = onRemoved;
      openProgress = onProgress;
      openStats = onStats;
      await reload();
    },
    async refresh() {
      if (!api) return;
      await reload();
    },
  };
})();
