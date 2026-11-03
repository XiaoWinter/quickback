(function () {
  const form = document.getElementById("question-form");
  const list = document.getElementById("question-list");
  let api = null;
  let report = null;
  let editingId = null;

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
        actions.append(edit, remove);
        card.append(en, zh, actions);
      }
      list.appendChild(card);
    });
  }

  async function reload() {
    const questions = await api("/api/questions");
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
    const edit = event.target.closest("[data-edit]");
    const remove = event.target.closest("[data-delete]");
    const save = event.target.closest("[data-save]");
    const cancel = event.target.closest("[data-cancel]");
    try {
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
        if (!window.confirm("删掉这道题？已经练过的记录还会留着。")) return;
        await api(`/api/questions/${remove.dataset.delete}`, { method: "DELETE" });
        if (editingId === remove.dataset.delete) editingId = null;
        report("");
        await reload();
      }
    } catch (error) {
      report(error.message);
    }
  });

  window.QuickbackQuestions = {
    async show(request, notify) {
      api = request;
      report = notify;
      await reload();
    },
  };
})();
