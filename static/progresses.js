(function () {
  const root = document.getElementById("progresses-root");
  let api = null;
  let report = null;
  let openStats = null;
  let items = null;

  function line(className, text) {
    const p = document.createElement("p");
    p.className = className;
    p.textContent = text;
    return p;
  }

  function formatWhen(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
  }

  function latestRun(progress) {
    if (!progress || !progress.length) return null;
    return progress[progress.length - 1] || null;
  }

  function render() {
    if (!root) return;
    root.replaceChildren();
    if (!items) {
      root.appendChild(line("note", "正在读取…"));
      return;
    }
    const head = document.createElement("div");
    head.className = "section-head";
    const title = document.createElement("p");
    title.className = "kicker";
    title.textContent = "进展统计";
    head.appendChild(title);
    root.appendChild(head);
    root.appendChild(line("note", "按最近练习时间排序。点进去看该题的详细统计。"));
    if (!items.length) {
      root.appendChild(line("empty", "还没有统计。先在题目里点「进展」练完一轮，再回来看。"));
      return;
    }
    const list = document.createElement("section");
    list.className = "question-list";
    items.forEach((question) => {
      const progress = question.progress || [];
      const latest = latestRun(progress);
      const card = document.createElement("article");
      card.className = "card question-item has-card-when";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "question-open";
      open.dataset.progressStats = question.id;
      const meta = document.createElement("p");
      meta.className = "question-meta";
      const tag = document.createElement("span");
      tag.className = "question-tag";
      tag.textContent = `${progress.length} 次`;
      meta.appendChild(tag);
      if (latest && latest.average != null) {
        meta.append(document.createTextNode(` · 最近平均 ${latest.average}`));
      }
      if (question.category) {
        const cat = document.createElement("span");
        cat.className = "question-tag";
        cat.textContent = question.category;
        meta.append(document.createTextNode(" · "), cat);
      }
      const whenText = latest ? formatWhen(latest.finished_at) : "";
      if (whenText) {
        const when = document.createElement("span");
        when.className = "card-when";
        when.textContent = whenText;
        meta.appendChild(when);
      }
      const en = document.createElement("p");
      en.className = "en-line";
      en.textContent = question.en || "";
      const zh = document.createElement("p");
      zh.className = "zh-line";
      zh.textContent = question.zh || "";
      open.append(meta, en, zh);
      card.appendChild(open);
      list.appendChild(card);
    });
    root.appendChild(list);
  }

  async function reload() {
    const questions = await api("/api/questions");
    items = (questions || [])
      .filter((item) => (item.progress || []).length > 0)
      .sort((a, b) => {
        const aLast = (a.progress[a.progress.length - 1] || {}).finished_at || "";
        const bLast = (b.progress[b.progress.length - 1] || {}).finished_at || "";
        return aLast < bLast ? 1 : aLast > bLast ? -1 : 0;
      });
    render();
  }

  if (root) {
    root.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-progress-stats]");
      if (!target || !openStats) return;
      try {
        await openStats(target.dataset.progressStats);
      } catch (error) {
        if (report) report(error.message);
      }
    });
  }

  window.QuickbackProgresses = {
    async show(request, notify, onOpen) {
      api = request;
      report = notify;
      openStats = onOpen;
      items = null;
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
  };
})();
