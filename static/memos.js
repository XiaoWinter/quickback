(function () {
  const form = document.getElementById("memo-form");
  const list = document.getElementById("memo-list");
  let api = null;
  let report = null;
  let editingId = null;
  let busy = false;

  function formatWhen(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
  }

  function parts(memo) {
    const title = String(memo.title || "").trim();
    const text = String(memo.text || "");
    if (title) return { title, body: text };
    const lines = text.split("\n");
    const first = (lines[0] || "").trim();
    return { title: first, body: lines.slice(1).join("\n").trim() };
  }

  function render(memos) {
    if (!list) return;
    list.replaceChildren();
    if (!memos.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "还没有备忘。";
      list.appendChild(empty);
      return;
    }
    memos.forEach((memo) => {
      const card = document.createElement("article");
      card.className = "card memo-item";
      card.dataset.id = memo.id;
      const meta = document.createElement("p");
      meta.className = "question-meta";
      const created = formatWhen(memo.created_at);
      const updated = formatWhen(memo.updated_at);
      meta.textContent = created === updated ? `写于 ${created}` : `写于 ${created} · 修改 ${updated}`;
      if (editingId === memo.id) {
        card.appendChild(meta);
        const titleLabel = document.createElement("label");
        titleLabel.textContent = "标题";
        const titleInput = document.createElement("input");
        titleInput.type = "text";
        titleInput.name = "title";
        titleInput.maxLength = 80;
        titleInput.value = memo.title || "";
        titleLabel.appendChild(titleInput);
        const label = document.createElement("label");
        label.textContent = "备忘";
        const input = document.createElement("textarea");
        input.name = "text";
        input.required = true;
        input.maxLength = 2000;
        input.rows = 4;
        input.value = memo.text;
        label.appendChild(input);
        const actions = document.createElement("div");
        actions.className = "question-actions";
        const save = document.createElement("button");
        save.type = "button";
        save.className = "primary";
        save.dataset.save = memo.id;
        save.textContent = "保存";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "text-btn";
        cancel.dataset.cancel = memo.id;
        cancel.textContent = "取消";
        actions.append(save, cancel);
        card.append(titleLabel, label, actions);
      } else {
        const shown = parts(memo);
        const fold = document.createElement("details");
        fold.className = "memo-fold";
        const summary = document.createElement("summary");
        const title = document.createElement("h2");
        title.className = "memo-title";
        title.textContent = shown.title;
        summary.appendChild(title);
        fold.appendChild(summary);
        fold.appendChild(meta);
        if (shown.body) {
          const body = document.createElement("p");
          body.className = "memo-text";
          body.textContent = shown.body;
          fold.appendChild(body);
        }
        const actions = document.createElement("div");
        actions.className = "question-actions";
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "text-btn";
        edit.dataset.edit = memo.id;
        edit.textContent = "修改";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.delete = memo.id;
        remove.textContent = "删除";
        actions.append(edit, remove);
        fold.appendChild(actions);
        card.appendChild(fold);
      }
      list.appendChild(card);
    });
  }

  async function reload() {
    render(await api("/api/memos"));
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy) return;
      const text = new FormData(form).get("text");
      busy = true;
      try {
        await api("/api/memos", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: new FormData(form).get("title"), text }),
        });
        form.reset();
        report("");
        await reload();
      } catch (error) {
        report(error.message);
      } finally {
        busy = false;
      }
    });
  }

  if (list) {
    list.addEventListener("click", async (event) => {
      const edit = event.target.closest("[data-edit]");
      const remove = event.target.closest("[data-delete]");
      const save = event.target.closest("[data-save]");
      const cancel = event.target.closest("[data-cancel]");
      try {
        if (edit) {
          editingId = edit.dataset.edit;
          await reload();
          list.querySelector('[name="title"]')?.focus();
          return;
        }
        if (cancel) {
          editingId = null;
          await reload();
          return;
        }
        if (save) {
          if (busy) return;
          const card = save.closest(".memo-item");
          busy = true;
          try {
            await api(`/api/memos/${save.dataset.save}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                title: card.querySelector('[name="title"]').value,
                text: card.querySelector('[name="text"]').value,
              }),
            });
            editingId = null;
            report("");
            await reload();
          } finally {
            busy = false;
          }
          return;
        }
        if (remove) {
          const ask = window.quickbackConfirm || ((text) => Promise.resolve(window.confirm(text)));
          if (!await ask("删掉这条备忘？")) return;
          if (busy) return;
          busy = true;
          try {
            await api(`/api/memos/${remove.dataset.delete}`, { method: "DELETE" });
            if (editingId === remove.dataset.delete) editingId = null;
            report("");
            await reload();
          } finally {
            busy = false;
          }
        }
      } catch (error) {
        report(error.message);
      }
    });
  }

  window.QuickbackMemos = {
    async show(request, notify) {
      api = request;
      report = notify;
      await reload();
    },
  };
})();
