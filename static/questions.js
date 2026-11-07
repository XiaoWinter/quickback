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
        const en = document.createElement("p");
        en.className = "en-line";
        en.textContent = question.en;
        const zh = document.createElement("p");
        zh.className = "zh-line";
        zh.textContent = question.zh;
        const actions = document.createElement("div");
        actions.className = "question-actions";
        const detail = document.createElement("button");
        detail.type = "button";
        detail.className = "text-btn";
        detail.dataset.answer = question.id;
        detail.textContent = "详情";
        actions.appendChild(detail);
        if (question.closed && question.session_id) {
          const again = document.createElement("button");
          again.type = "button";
          again.className = "text-btn";
          again.dataset.reopen = question.session_id;
          again.dataset.question = question.id;
          again.textContent = "继续回答";
          actions.appendChild(again);
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.delete = question.id;
        remove.textContent = "删除";
        actions.appendChild(remove);
        head.appendChild(actions);
        card.append(en, zh, echoPanel(question));
      }
      list.appendChild(card);
    });
  }

  function echoPanel(question) {
    const details = document.createElement("details");
    details.className = "echoes";
    if (openEchoes.has(question.id)) details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "功能性句组跟读";
    const body = document.createElement("div");
    body.className = "echo-list";
    details.append(summary, body);
    details.addEventListener("toggle", () => {
      if (details.open) {
        openEchoes.add(question.id);
        loadEchoes(question, body);
      } else {
        openEchoes.delete(question.id);
      }
    });
    if (details.open) loadEchoes(question, body);
    return details;
  }

  const CHUNK_ROLES = { flow: "起句", pivot: "转折", refactor: "改口" };
  const VARIANT_LABELS = [["simple", "简单"], ["native", "地道"], ["professional", "专业"]];

  function addLabel(parent, text, rank) {
    const label = document.createElement("p");
    label.className = rank > 1 ? `block-label rank-${rank}` : "block-label";
    label.textContent = text;
    parent.appendChild(label);
  }

  function isFollowUp(line, question) {
    const en = (line.prompt_en || "").trim();
    const zh = (line.prompt_zh || "").trim();
    if (!en && !zh) return false;
    return en !== (question.en || "").trim() || zh !== (question.zh || "").trim();
  }

  function phraseIn(source, en) {
    const needle = String(en || "").replace(/\.\.\./g, "").trim();
    const full = String(source || "");
    const at = full.toLowerCase().indexOf(needle.toLowerCase());
    if (!needle || at < 0 || needle.toLowerCase() === full.trim().toLowerCase()) return "";
    return full.slice(at, at + needle.length);
  }

  function sourceOf(line, scope) {
    const sources = line.sources || {};
    if (scope === "main") {
      return sources.main || { en: line.natural || "", has_speech: Boolean(line.has_speech) };
    }
    return sources[scope] || null;
  }

  function ownedChunks(line, scope) {
    const source = sourceOf(line, scope);
    if (!source || !(source.en || "").trim()) return [];
    return (line.chunks || []).filter((chunk) => {
      return (chunk.scope || "main") === scope && CHUNK_ROLES[chunk.role] && phraseIn(source.en, chunk.en);
    }).map((chunk) => ({
      ...chunk,
      phrase: phraseIn(source.en, chunk.en),
      source,
    }));
  }

  function speechSrc(line, scope) {
    const version = encodeURIComponent(line.created_at || "");
    const base = `/api/sessions/${line.session_id}/speech/${line.index}`;
    const path = scope && scope !== "main" ? `${base}/${scope}` : base;
    return `${path}?v=${version}`;
  }

  function phraseButton(line, chunk) {
    const ratios = window.QuickbackSlice && window.QuickbackSlice.ratios
      ? window.QuickbackSlice.ratios(chunk.source.en, chunk.phrase)
      : null;
    if (!chunk.source.has_speech || !ratios || !window.QuickbackSlice) {
      const text = document.createElement("span");
      text.className = "chunk-phrase";
      text.textContent = chunk.phrase;
      return text;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chunk-speak";
    button.textContent = chunk.phrase;
    button.setAttribute("aria-label", `播放 ${chunk.phrase}`);
    button.addEventListener("click", () => {
      window.QuickbackSlice.play(button, speechSrc(line, chunk.scope), ratios);
    });
    return button;
  }

  function retellControl(line, chunk, label, role) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-btn";
    button.dataset.retell = String(line.index);
    button.dataset.retellSession = line.session_id;
    button.dataset.retellLevel = chunk.scope && chunk.scope !== "main" ? chunk.scope : "";
    button.dataset.retellChunk = String(chunk.number);
    if (role) button.dataset.retellRole = role;
    button.textContent = label;
    return button;
  }

  function chunkRow(line, chunk, archived) {
    const row = document.createElement("div");
    row.className = "echo-head";
    const phrase = document.createElement("div");
    phrase.className = "chunk-line";
    const role = document.createElement("span");
    role.className = `chunk-role is-${chunk.role}`;
    role.textContent = CHUNK_ROLES[chunk.role] || "";
    phrase.append(role, phraseButton(line, chunk));
    row.appendChild(phrase);
    const actions = document.createElement("div");
    actions.className = "echo-actions";
    if (chunk.has_retell && window.QuickbackChunkRetell) {
      const heard = document.createElement("button");
      heard.type = "button";
      heard.className = "chunk-heard";
      heard.setAttribute("aria-label", `播放跟读 ${chunk.phrase}`);
      const src = `/api/sessions/${line.session_id}/retell/${line.index}/${chunk.scope}/chunks/${chunk.number}`;
      heard.addEventListener("click", () => window.QuickbackChunkRetell.play(heard, src));
      actions.appendChild(heard);
    }
    if (!archived) {
      if (chunk.has_retell) {
        actions.appendChild(retellControl(line, chunk, "重录", "redo"));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.retellDelete = String(line.index);
        remove.dataset.retellSession = line.session_id;
        remove.dataset.retellLevel = chunk.scope && chunk.scope !== "main" ? chunk.scope : "";
        remove.dataset.retellChunk = String(chunk.number);
        remove.textContent = "删除";
        actions.appendChild(remove);
      } else {
        actions.appendChild(retellControl(line, chunk, "跟读"));
      }
    }
    if (actions.childElementCount) row.appendChild(actions);
    return row;
  }

  async function loadEchoes(question, body) {
    body.replaceChildren();
    const waiting = document.createElement("p");
    waiting.className = "note";
    waiting.textContent = "正在读取…";
    body.appendChild(waiting);
    try {
      const lines = await api(`/api/questions/${question.id}/echoes`);
      body.replaceChildren();
      let shown = 0;
      lines.forEach((line) => {
        const main = ownedChunks(line, "main");
        const variants = VARIANT_LABELS.map(([key, label]) => [label, ownedChunks(line, key)]).filter(([, group]) => group.length);
        if (!main.length && !variants.length) return;
        const block = document.createElement("div");
        block.className = "echo-turn";
        if (isFollowUp(line, question)) {
          const head = document.createElement("p");
          head.className = "kicker";
          head.textContent = "追问";
          block.appendChild(head);
          if ((line.prompt_en || "").trim()) {
            const en = document.createElement("p");
            en.className = "en-line";
            en.textContent = line.prompt_en;
            block.appendChild(en);
          }
          if ((line.prompt_zh || "").trim()) {
            const zh = document.createElement("p");
            zh.className = "zh-line";
            zh.textContent = line.prompt_zh;
            block.appendChild(zh);
          }
        }
        if (main.length) {
          addLabel(block, "修改版", 2);
          main.forEach((chunk) => block.appendChild(chunkRow(line, chunk, question.closed)));
        }
        variants.forEach(([label, group]) => {
          addLabel(block, label, 2);
          group.forEach((chunk) => block.appendChild(chunkRow(line, chunk, question.closed)));
        });
        body.appendChild(block);
        shown += 1;
      });
      if (!shown) {
        const empty = document.createElement("p");
        empty.className = "note";
        empty.textContent = "这道题还没有功能性句组。";
        body.appendChild(empty);
      }
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
    const retell = event.target.closest("[data-retell]");
    if (retell && window.QuickbackRetell) {
      window.QuickbackRetell.toggle(
        retell.dataset.retellSession,
        Number(retell.dataset.retell),
        retell.dataset.retellLevel || "",
        retell.dataset.retellChunk || "",
      );
      return;
    }
    const removeRetell = event.target.closest("[data-retell-delete]");
    if (removeRetell && window.QuickbackRetell) {
      window.QuickbackRetell.remove(
        removeRetell.dataset.retellSession,
        Number(removeRetell.dataset.retellDelete),
        removeRetell.dataset.retellLevel || "",
        removeRetell.dataset.retellChunk || "",
      );
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
      const reopen = event.target.closest("[data-reopen]");
      if (reopen) {
        await api(`/api/sessions/${reopen.dataset.reopen}/reopen`, { method: "POST" });
        if (answer) await answer(reopen.dataset.question);
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
    async refresh() {
      if (!api) return;
      await reload();
    },
  };
})();
