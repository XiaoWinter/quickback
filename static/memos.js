(function () {
  const form = document.getElementById("memo-form");
  const list = document.getElementById("memo-list");
  const fileInput = form ? form.querySelector('[name="files"]') : null;
  const fileHint = document.getElementById("memo-file-hint");
  const uploadBox = document.getElementById("memo-upload");
  const uploadText = document.getElementById("memo-upload-text");
  const uploadFill = document.getElementById("memo-upload-fill");
  const addBtn = form ? form.querySelector('button[type="submit"]') : null;
  const ACCEPT = "audio/*,video/*,image/*,.pdf,.epub,.mobi,.azw,.azw3,.txt,.md,.zip,.doc,.docx,.rtf,.odt";
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

  function formatSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  function parts(memo) {
    const title = String(memo.title || "").trim();
    const text = String(memo.text || "");
    if (title) return { title, body: text };
    const lines = text.split("\n");
    const first = (lines[0] || "").trim();
    if (first) return { title: first, body: lines.slice(1).join("\n").trim() };
    const firstFile = ((memo.files || [])[0] || {}).name || "附件";
    return { title: firstFile, body: "" };
  }

  function kindLabel(kind) {
    if (kind === "audio") return "音频";
    if (kind === "video") return "视频";
    if (kind === "image") return "照片";
    if (kind === "ebook") return "电子书";
    if (kind === "doc") return "文档";
    return "文件";
  }

  function showPicked(input, hint) {
    if (!hint) return;
    const files = [...(input && input.files) || []];
    if (!files.length) {
      hint.textContent = "可选文件、音频、视频、文档或电子书，最多 8 个。";
      return;
    }
    hint.textContent = files.map((file) => `${file.name}（${formatSize(file.size)}）`).join(" · ");
  }

  function setUploadProgress(loaded, total, label) {
    if (!uploadBox) {
      if (report && total) report(`${label || "正在上传"} ${Math.round((loaded / total) * 100)}%`);
      return;
    }
    uploadBox.hidden = false;
    const pct = total ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
    if (uploadFill) uploadFill.style.width = `${pct}%`;
    if (uploadText) {
      uploadText.textContent = total
        ? `${label || "正在上传"} ${pct}% · ${formatSize(loaded)} / ${formatSize(total)}`
        : (label || "正在上传…");
    }
  }

  function clearUploadProgress() {
    if (uploadBox) uploadBox.hidden = true;
    if (uploadFill) uploadFill.style.width = "0%";
    if (uploadText) uploadText.textContent = "";
  }

  function uploadWithProgress(url, body, label) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) setUploadProgress(event.loaded, event.total, label);
      };
      xhr.onload = () => {
        const data = xhr.response || {};
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
          return;
        }
        const detail = data && data.detail;
        reject(new Error(typeof detail === "string" ? detail : "上传失败"));
      };
      xhr.onerror = () => reject(new Error("上传失败"));
      xhr.send(body);
    });
  }

  function docThumb(file) {
    const wrap = document.createElement("div");
    wrap.className = "memo-doc";
    const link = document.createElement("a");
    link.className = "memo-doc-link";
    link.href = file.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = file.name;
    if (file.cover_url) {
      const img = document.createElement("img");
      img.className = "memo-doc-cover";
      img.src = file.cover_url;
      img.alt = file.name;
      img.loading = "lazy";
      link.appendChild(img);
    } else if ((file.mime || "") === "application/pdf" || (file.ext || "") === "pdf") {
      const frame = document.createElement("iframe");
      frame.className = "memo-doc-pdf";
      frame.src = `${file.url}#page=1&view=FitH`;
      frame.title = file.name;
      frame.loading = "lazy";
      link.appendChild(frame);
    } else {
      const badge = document.createElement("div");
      badge.className = "memo-doc-badge";
      const ext = document.createElement("span");
      ext.className = "memo-doc-ext";
      ext.textContent = String(file.ext || kindLabel(file.kind)).toUpperCase();
      const label = document.createElement("span");
      label.className = "memo-doc-label";
      label.textContent = kindLabel(file.kind);
      badge.append(ext, label);
      link.appendChild(badge);
    }
    const caption = document.createElement("span");
    caption.className = "memo-doc-name";
    caption.textContent = file.name;
    wrap.append(link, caption);
    return wrap;
  }

  function fileList(memo, editing) {
    const files = memo.files || [];
    if (!files.length && !editing) return null;
    const box = document.createElement("div");
    box.className = "memo-files";
    if (!files.length) {
      const empty = document.createElement("p");
      empty.className = "note";
      empty.textContent = "还没有附件。";
      box.appendChild(empty);
    }
    files.forEach((file) => {
      const row = document.createElement("div");
      row.className = "memo-file";
      const mark = document.createElement("span");
      mark.className = "memo-file-kind";
      mark.textContent = kindLabel(file.kind);
      const name = document.createElement("span");
      name.className = "memo-file-name";
      name.textContent = `${file.name} · ${formatSize(file.size)}`;
      const download = document.createElement("a");
      download.className = "text-btn";
      download.href = file.url;
      download.download = file.name;
      download.textContent = "下载";
      row.append(mark, name, download);
      if (file.kind === "image" || (file.mime || "").startsWith("image/")) {
        const preview = document.createElement("a");
        preview.className = "memo-image";
        preview.href = file.url;
        preview.target = "_blank";
        preview.rel = "noopener";
        const img = document.createElement("img");
        img.src = file.url;
        img.alt = file.name;
        img.loading = "lazy";
        preview.appendChild(img);
        row.appendChild(preview);
      } else if (file.kind === "ebook" || file.kind === "doc") {
        row.appendChild(docThumb(file));
      } else if (file.kind === "audio") {
        const player = document.createElement("audio");
        player.controls = true;
        player.preload = "metadata";
        player.src = file.url;
        row.appendChild(player);
      } else if (file.kind === "video") {
        const player = document.createElement("video");
        player.controls = true;
        player.preload = "metadata";
        player.src = file.url;
        row.appendChild(player);
      }
      if (editing) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-btn";
        remove.dataset.fileDelete = file.id;
        remove.dataset.memo = memo.id;
        remove.textContent = "去掉";
        row.appendChild(remove);
      }
      box.appendChild(row);
    });
    if (editing) {
      const label = document.createElement("label");
      label.className = "memo-file-pick";
      label.textContent = "继续添加";
      const input = document.createElement("input");
      input.type = "file";
      input.name = "files";
      input.multiple = true;
      input.accept = ACCEPT;
      const hint = document.createElement("p");
      hint.className = "note memo-file-hint";
      hint.textContent = "可选文件、音频、视频、文档或电子书。";
      input.addEventListener("change", () => showPicked(input, hint));
      label.appendChild(input);
      box.append(label, hint);
    }
    return box;
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
      const count = (memo.files || []).length;
      const fileNote = count ? ` · ${count} 个附件` : "";
      meta.textContent = (created === updated ? `写于 ${created}` : `写于 ${created} · 修改 ${updated}`) + fileNote;
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
        input.maxLength = 2000;
        input.rows = 4;
        input.value = memo.text;
        if (!(memo.files || []).length) input.required = true;
        label.appendChild(input);
        const files = fileList(memo, true);
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
        card.append(titleLabel, label);
        if (files) card.appendChild(files);
        card.appendChild(actions);
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
        const files = fileList(memo, false);
        if (files) fold.appendChild(files);
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

  if (fileInput) {
    fileInput.accept = ACCEPT;
    fileInput.addEventListener("change", () => showPicked(fileInput, fileHint));
    showPicked(fileInput, fileHint);
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy) return;
      const data = new FormData(form);
      const text = String(data.get("text") || "").trim();
      const files = fileInput ? [...fileInput.files] : [];
      if (!text && !files.length) {
        report("先写备忘，或选一个文件。");
        return;
      }
      busy = true;
      if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = files.length ? "上传中…" : "添加中…";
      }
      try {
        if (files.length) {
          setUploadProgress(0, Math.max(1, files.reduce((sum, file) => sum + file.size, 0)), "正在上传");
          await uploadWithProgress("/api/memos", data, "正在上传");
        } else {
          await api("/api/memos", { method: "POST", body: data });
        }
        form.reset();
        showPicked(fileInput, fileHint);
        clearUploadProgress();
        report("");
        await reload();
      } catch (error) {
        clearUploadProgress();
        report(error.message);
      } finally {
        busy = false;
        if (addBtn) {
          addBtn.disabled = false;
          addBtn.textContent = "添加";
        }
      }
    });
  }

  if (list) {
    list.addEventListener("click", async (event) => {
      const edit = event.target.closest("[data-edit]");
      const remove = event.target.closest("[data-delete]");
      const save = event.target.closest("[data-save]");
      const cancel = event.target.closest("[data-cancel]");
      const dropFile = event.target.closest("[data-file-delete]");
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
        if (dropFile) {
          const ask = window.quickbackConfirm || ((text) => Promise.resolve(window.confirm(text)));
          if (!await ask("去掉这个附件？")) return;
          if (busy) return;
          busy = true;
          try {
            await api(`/api/memos/${dropFile.dataset.memo}/files/${dropFile.dataset.fileDelete}`, { method: "DELETE" });
            report("");
            await reload();
          } finally {
            busy = false;
          }
          return;
        }
        if (save) {
          if (busy) return;
          const card = save.closest(".memo-item");
          const saveBtn = save;
          busy = true;
          saveBtn.disabled = true;
          saveBtn.textContent = "保存中…";
          try {
            await api(`/api/memos/${save.dataset.save}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                title: card.querySelector('[name="title"]').value,
                text: card.querySelector('[name="text"]').value,
              }),
            });
            const more = card.querySelector('.memo-files input[type="file"]');
            if (more && more.files && more.files.length) {
              const body = new FormData();
              [...more.files].forEach((file) => body.append("files", file));
              saveBtn.textContent = "上传中…";
              setUploadProgress(0, Math.max(1, [...more.files].reduce((sum, file) => sum + file.size, 0)), "正在上传");
              await uploadWithProgress(`/api/memos/${save.dataset.save}/files`, body, "正在上传");
            }
            editingId = null;
            clearUploadProgress();
            report("");
            await reload();
          } catch (error) {
            clearUploadProgress();
            report(error.message);
            saveBtn.disabled = false;
            saveBtn.textContent = "保存";
          } finally {
            busy = false;
          }
          return;
        }
        if (remove) {
          const ask = window.quickbackConfirm || ((text) => Promise.resolve(window.confirm(text)));
          if (!await ask("删掉这条备忘？附件也会一起删掉。")) return;
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
