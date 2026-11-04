(function () {
  const form = document.getElementById("question-form");
  const list = document.getElementById("question-list");
  let api = null;
  let report = null;
  let answer = null;
  let removed = null;
  let editingId = null;
  let currentId = "";
  const openEchoes = new Set();

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

  function render(questions) {
    list.replaceChildren();
    questions.forEach((question) => {
      const card = document.createElement("article");
      card.className = "card question-item";
      card.dataset.id = question.id;
      card.appendChild(metaLine(question));
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
        const en = document.createElement("p");
        en.className = "en-line";
        en.textContent = question.en;
        const zh = document.createElement("p");
        zh.className = "zh-line";
        zh.textContent = question.zh;
        const actions = document.createElement("div");
        actions.className = "question-actions";
        const use = document.createElement("button");
        use.type = "button";
        use.className = "text-btn";
        use.dataset.answer = question.id;
        use.textContent = "回答";
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "text-btn";
        edit.dataset.edit = question.id;
        edit.textContent = "修改";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.delete = question.id;
        remove.textContent = "删除";
        actions.append(use, edit, remove);
        card.append(en, zh, actions, echoPanel(question.id));
      }
      list.appendChild(card);
    });
  }

  function echoPanel(questionId) {
    const details = document.createElement("details");
    details.className = "echoes";
    if (openEchoes.has(questionId)) details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "跟读";
    const body = document.createElement("div");
    body.className = "echo-list";
    details.append(summary, body);
    details.addEventListener("toggle", () => {
      if (details.open) {
        openEchoes.add(questionId);
        loadEchoes(questionId, body);
      } else {
        openEchoes.delete(questionId);
      }
    });
    if (details.open) loadEchoes(questionId, body);
    return details;
  }

  function audioLine(src) {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = src;
    return audio;
  }

  async function loadEchoes(questionId, body) {
    body.replaceChildren();
    const waiting = document.createElement("p");
    waiting.className = "note";
    waiting.textContent = "正在读取…";
    body.appendChild(waiting);
    try {
      const lines = await api(`/api/questions/${questionId}/echoes`);
      body.replaceChildren();
      if (!lines.length) {
        const empty = document.createElement("p");
        empty.className = "note";
        empty.textContent = "这道题还没有地道版。";
        body.appendChild(empty);
        return;
      }
      lines.forEach((line) => {
        const item = document.createElement("div");
        item.className = "echo";
        if (line.prompt_en) {
          const en = document.createElement("p");
          en.className = "en-line";
          en.textContent = line.prompt_en;
          item.appendChild(en);
        }
        if (line.prompt_zh) {
          const asked = document.createElement("p");
          asked.className = "zh-line";
          asked.textContent = line.prompt_zh;
          item.appendChild(asked);
        }
        const natural = document.createElement("p");
        natural.className = "natural";
        natural.textContent = line.natural;
        item.appendChild(natural);
        if (line.natural_zh) {
          const zh = document.createElement("p");
          zh.className = "meaning";
          zh.textContent = line.natural_zh;
          item.appendChild(zh);
        }
        if (line.has_speech) {
          const label = document.createElement("p");
          label.className = "block-label";
          label.textContent = "地道版";
          item.append(label, audioLine(`/api/sessions/${line.session_id}/speech/${line.index}`));
        }
        const retellLabel = document.createElement("p");
        retellLabel.className = "block-label";
        retellLabel.textContent = "复述";
        item.appendChild(retellLabel);
        if (line.has_retell) {
          item.appendChild(audioLine(`/api/sessions/${line.session_id}/retell/${line.index}`));
        } else {
          const missing = document.createElement("p");
          missing.className = "note";
          missing.textContent = "还没有复述。在练习页的地道版下面说一遍。";
          item.appendChild(missing);
        }
        body.appendChild(item);
      });
    } catch (error) {
      body.replaceChildren();
      const failed = document.createElement("p");
      failed.className = "note";
      failed.textContent = error.message;
      body.appendChild(failed);
    }
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
        if (!await ask("删掉这道题？它的追问和录音也会一起删掉。")) return;
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
    async show(request, notify, onAnswer, onRemoved) {
      api = request;
      report = notify;
      answer = onAnswer;
      removed = onRemoved;
      await reload();
    },
  };
})();
