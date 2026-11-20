(function () {
  const root = document.getElementById("tasks-root");
  let api = null;
  let report = null;
  let openTask = null;
  let view = null;

  function line(className, text) {
    const p = document.createElement("p");
    p.className = className;
    p.textContent = text;
    return p;
  }

  function render() {
    if (!root) return;
    root.replaceChildren();
    if (!view) {
      root.appendChild(line("note", "正在读取…"));
      return;
    }
    const head = document.createElement("div");
    head.className = "section-head";
    const title = document.createElement("p");
    title.className = "kicker";
    title.textContent = "今日任务";
    head.appendChild(title);
    root.appendChild(head);
    const pending = view.pending_count != null
      ? view.pending_count
      : (view.items || []).filter((item) => !item.done).length;
    const done = view.done_count != null
      ? view.done_count
      : (view.items || []).filter((item) => item.done).length;
    root.appendChild(
      line(
        "note",
        `从已归档题目里按遗忘曲线安排，每天锁定 ${view.min || 3}–${view.max || 5} 道；平均分达 60 才算完成。已完成 ${done}，待做 ${pending}。`,
      ),
    );
    const items = view.items || [];
    if (!items.length) {
      if ((view.archived_count || 0) <= 0) {
        root.appendChild(line("empty", "还没有归档题目。先在练习里归档一道题，再回来做任务。"));
      } else {
        root.appendChild(line("empty", "今天还没有可做的任务。"));
      }
      return;
    }
    if (pending <= 0) {
      root.appendChild(line("empty", "今天的任务都做完了。明天再按遗忘曲线安排下一批。"));
    }
    const list = document.createElement("section");
    list.className = "question-list";
    items.forEach((item, index) => {
      const card = document.createElement("article");
      card.className = "card question-item task-item" + (item.done ? " is-done" : "");
      const top = document.createElement("div");
      top.className = "section-head";
      const meta = document.createElement("p");
      meta.className = "question-meta";
      const tag = document.createElement("span");
      tag.className = "question-tag" + (item.done ? "" : "");
      if (item.done) tag.textContent = "已完成";
      else tag.textContent = item.kind === "new" ? "新任务" : "到期复习";
      meta.append(tag, document.createTextNode(` · 第 ${index + 1} 题`));
      if (item.category) {
        const cat = document.createElement("span");
        cat.className = "question-tag";
        cat.textContent = item.category;
        meta.append(document.createTextNode(" · "), cat);
      }
      top.appendChild(meta);
      card.appendChild(top);
      if (item.done) {
        const body = document.createElement("div");
        body.className = "question-ielts-body";
        const en = document.createElement("p");
        en.className = "en-line";
        en.textContent = item.en || "";
        const zh = document.createElement("p");
        zh.className = "zh-line";
        zh.textContent = item.zh || "";
        body.append(en, zh);
        card.appendChild(body);
      } else {
        const open = document.createElement("button");
        open.type = "button";
        open.className = "question-open";
        open.dataset.task = item.id;
        const en = document.createElement("p");
        en.className = "en-line";
        en.textContent = item.en || "";
        const zh = document.createElement("p");
        zh.className = "zh-line";
        zh.textContent = item.zh || "";
        open.append(en, zh);
        card.appendChild(open);
      }
      list.appendChild(card);
    });
    root.appendChild(list);
  }

  async function reload() {
    view = await api("/api/tasks/today");
    render();
  }

  if (root) {
    root.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-task]");
      if (!target || !openTask) return;
      try {
        await openTask(target.dataset.task);
      } catch (error) {
        if (report) report(error.message);
      }
    });
  }

  window.QuickbackTasks = {
    async show(request, notify, onOpen) {
      api = request;
      report = notify;
      openTask = onOpen;
      view = null;
      render();
      try {
        await reload();
        if (report) report("");
      } catch (error) {
        if (report) report(error.message);
        if (root) {
          root.replaceChildren();
          root.appendChild(line("error-line", error.message));
        }
      }
    },
    async refresh() {
      if (!api) return;
      await reload();
    },
  };
})();
