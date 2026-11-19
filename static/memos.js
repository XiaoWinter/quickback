(function () {
  const form = document.getElementById("memo-form");
  const list = document.getElementById("memo-list");
  const fileInput = form ? form.querySelector('[name="files"]') : null;
  const fileHint = document.getElementById("memo-file-hint");
  const formPicked = document.getElementById("memo-picked");
  const uploadBox = document.getElementById("memo-upload");
  const uploadText = document.getElementById("memo-upload-text");
  const uploadFill = document.getElementById("memo-upload-fill");
  const lightbox = document.getElementById("memo-lightbox");
  const lightboxImg = document.getElementById("memo-lightbox-img");
  const lightboxFrame = document.getElementById("memo-lightbox-frame");
  const lightboxClose = document.getElementById("memo-lightbox-close");
  const videoFs = document.getElementById("memo-video-fs");
  const videoFsFrame = document.getElementById("memo-video-fs-frame");
  const videoFsUi = document.getElementById("memo-video-fs-ui");
  const videoFsClose = document.getElementById("memo-video-fs-close");
  const videoFsPlay = document.getElementById("memo-video-fs-play");
  const videoFsToggle = document.getElementById("memo-video-fs-toggle");
  const videoFsSeek = document.getElementById("memo-video-fs-seek");
  const videoFsTime = document.getElementById("memo-video-fs-time");
  const videoFsSpeeds = document.getElementById("memo-video-fs-speeds");
  const videoFsHint = document.getElementById("memo-video-fs-hint");
  const addBtn = form ? form.querySelector('button[type="submit"]') : null;
  const openFormBtn = document.getElementById("memo-open-form");
  const closeFormBtn = document.getElementById("memo-close-form");
  let videoFsHome = null;
  let videoFsPlayer = null;
  let videoFsHadControls = true;
  let videoFsSeeking = false;
  let videoFsHideTimer = 0;
  const VIDEO_FS_HIDE_MS = 2800;
  const ACCEPT = "audio/*,video/*,image/*,.pdf,.epub,.mobi,.azw,.azw3,.txt,.md,.zip,.doc,.docx,.rtf,.odt";
  const EMPTY_HINT = "可选文件、音频、视频、文档或电子书，最多 8 个。";
  const EMPTY_HINT_EDIT = "可选文件、音频、视频、文档或电子书。";
  const FILE_LIMIT = 8;
  let api = null;
  let report = null;
  let editingId = null;
  let busy = false;
  let formPicker = null;

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

  function fileKey(file) {
    return `${file.name}\0${file.size}\0${file.lastModified}`;
  }

  function syncInputFiles(input, files) {
    if (!input) return;
    try {
      const transfer = new DataTransfer();
      files.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    } catch (_error) {
      /* FormData 提交时会改用手写附件列表 */
    }
  }

  function renderPicked(files, hint, pickedBox, emptyText, onClear, onRemove) {
    const empty = emptyText || EMPTY_HINT;
    if (!files.length) {
      if (hint) {
        hint.hidden = false;
        hint.textContent = empty;
      }
      if (pickedBox) {
        pickedBox.hidden = true;
        pickedBox.replaceChildren();
      }
      return;
    }
    if (hint) hint.hidden = true;
    if (!pickedBox) {
      if (hint) {
        hint.hidden = false;
        hint.textContent = files.map((file) => `${file.name}（${formatSize(file.size)}）`).join(" · ");
      }
      return;
    }
    pickedBox.hidden = false;
    pickedBox.replaceChildren();
    const list = document.createElement("ul");
    list.className = "memo-picked-list";
    files.forEach((file, index) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.className = "memo-picked-name";
      name.textContent = `${file.name}（${formatSize(file.size)}）`;
      item.appendChild(name);
      if (onRemove) {
        const drop = document.createElement("button");
        drop.type = "button";
        drop.className = "text-btn memo-picked-drop";
        drop.textContent = "去掉";
        drop.addEventListener("click", () => onRemove(index));
        item.appendChild(drop);
      }
      list.appendChild(item);
    });
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "text-btn memo-picked-clear";
    clear.textContent = "取消所选";
    clear.addEventListener("click", () => onClear && onClear());
    pickedBox.append(list, clear);
  }

  function createPicker(input, hint, pickedBox, emptyText) {
    let bag = [];
    const empty = emptyText || EMPTY_HINT;

    function paint() {
      renderPicked(
        bag,
        hint,
        pickedBox,
        empty,
        () => {
          bag = [];
          if (input) input.value = "";
          paint();
        },
        (index) => {
          bag.splice(index, 1);
          syncInputFiles(input, bag);
          paint();
        },
      );
    }

    function absorb(incoming) {
      let overflow = false;
      (incoming || []).forEach((file) => {
        const key = fileKey(file);
        if (bag.some((item) => fileKey(item) === key)) return;
        if (bag.length >= FILE_LIMIT) {
          overflow = true;
          return;
        }
        bag.push(file);
      });
      syncInputFiles(input, bag);
      paint();
      if (overflow && report) report(`最多 ${FILE_LIMIT} 个附件。`);
    }

    if (input) {
      input.addEventListener("change", () => {
        absorb([...(input.files || [])]);
      });
    }
    paint();
    return {
      getFiles: () => bag.slice(),
      clear: () => {
        bag = [];
        if (input) input.value = "";
        paint();
      },
    };
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
      setUploadProgress(0, 0, label || "正在上传…");
      xhr.upload.onloadstart = () => setUploadProgress(0, 0, label || "正在上传…");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) setUploadProgress(event.loaded, event.total, label);
        else setUploadProgress(event.loaded, 0, label);
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

  function videoIsLandscape(player) {
    return Boolean(player && player.videoWidth > 0 && player.videoWidth > player.videoHeight);
  }

  function viewportSize() {
    const vv = window.visualViewport;
    return {
      w: Math.round((vv && vv.width) || window.innerWidth || document.documentElement.clientWidth),
      h: Math.round((vv && vv.height) || window.innerHeight || document.documentElement.clientHeight),
    };
  }

  function formatPlayTime(sec) {
    const n = Math.max(0, Math.floor(Number(sec) || 0));
    const m = Math.floor(n / 60);
    const s = String(n % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function syncVideoFsUi() {
    if (!videoFsPlayer) return;
    if (videoFsPlay) videoFsPlay.textContent = videoFsPlayer.paused ? "播放" : "暂停";
    const duration = Number.isFinite(videoFsPlayer.duration) ? videoFsPlayer.duration : 0;
    if (videoFsSeek && !videoFsSeeking) {
      videoFsSeek.max = String(duration || 0);
      videoFsSeek.value = String(videoFsPlayer.currentTime || 0);
    }
    if (videoFsTime) {
      videoFsTime.textContent = `${formatPlayTime(videoFsPlayer.currentTime)} / ${formatPlayTime(duration)}`;
    }
    if (videoFsSpeeds) {
      const rate = videoFsPlayer.playbackRate || 1;
      [...videoFsSpeeds.querySelectorAll("[data-rate]")].forEach((btn) => {
        btn.classList.toggle("is-on", Math.abs(Number(btn.dataset.rate) - rate) < 0.001);
      });
    }
  }

  function clearVideoFsHideTimer() {
    if (videoFsHideTimer) {
      window.clearTimeout(videoFsHideTimer);
      videoFsHideTimer = 0;
    }
  }

  function hideVideoFsChrome() {
    if (!videoFsUi || videoFsSeeking) return;
    if (videoFsPlayer && videoFsPlayer.paused) return;
    videoFsUi.classList.add("is-idle");
  }

  function showVideoFsChrome(autoHide) {
    if (!videoFsUi) return;
    videoFsUi.classList.remove("is-idle");
    clearVideoFsHideTimer();
    if (autoHide === false) return;
    if (videoFsPlayer && videoFsPlayer.paused) return;
    videoFsHideTimer = window.setTimeout(hideVideoFsChrome, VIDEO_FS_HIDE_MS);
  }

  function layoutCssLandFs() {
    if (!videoFs || videoFs.hidden) return;
    const { w, h } = viewportSize();
    videoFs.style.setProperty("--memo-fs-w", `${w}px`);
    videoFs.style.setProperty("--memo-fs-h", `${h}px`);
  }

  function exitVideoFs() {
    if (!videoFs || videoFs.hidden) return;
    clearVideoFsHideTimer();
    if (videoFsPlayer) {
      videoFsPlayer.controls = videoFsHadControls;
      videoFsPlayer.ontimeupdate = null;
      videoFsPlayer.onplay = null;
      videoFsPlayer.onpause = null;
      videoFsPlayer.onloadedmetadata = null;
      if (videoFsHome) videoFsHome.appendChild(videoFsPlayer);
    }
    if (videoFsFrame && videoFsUi) videoFsFrame.appendChild(videoFsUi);
    videoFs.classList.remove("is-css-land");
    if (videoFsUi) videoFsUi.classList.remove("is-idle");
    if (videoFsHint) videoFsHint.hidden = true;
    videoFs.hidden = true;
    videoFsPlayer = null;
    videoFsHome = null;
    unlockPageScroll();
  }

  function enterVideoFs(player, home) {
    if (!videoFs || !videoFsFrame || !player) return;
    if (!videoFs.hidden) exitVideoFs();
    videoFsPlayer = player;
    videoFsHome = home;
    videoFsHadControls = player.controls;
    const landscape = videoIsLandscape(player);
    const portraitUi = window.matchMedia("(orientation: portrait)").matches;
    const cssLand = Boolean(landscape && portraitUi);
    // 横屏视频 + 竖持（含竖屏锁定）：CSS 横看；竖屏视频只铺满，不旋转
    videoFs.classList.toggle("is-css-land", cssLand);
    if (videoFsHint) videoFsHint.hidden = !cssLand;
    // 关闭原生 controls，改用旋转层内自制控件
    player.controls = false;
    videoFsFrame.insertBefore(player, videoFsUi);
    if (videoFsUi) videoFsFrame.appendChild(videoFsUi);
    player.ontimeupdate = syncVideoFsUi;
    player.onplay = () => {
      syncVideoFsUi();
      showVideoFsChrome(true);
    };
    player.onpause = () => {
      syncVideoFsUi();
      showVideoFsChrome(false);
    };
    player.onloadedmetadata = syncVideoFsUi;
    lockPageScroll();
    videoFs.hidden = false;
    layoutCssLandFs();
    syncVideoFsUi();
    showVideoFsChrome(true);
    player.play().catch(() => {});
  }

  function mountVideo(file) {
    const shell = document.createElement("div");
    shell.className = "memo-video";
    const home = document.createElement("div");
    home.className = "memo-video-home";
    const player = document.createElement("video");
    player.controls = true;
    player.preload = "metadata";
    player.playsInline = true;
    player.setAttribute("playsinline", "");
    player.setAttribute("webkit-playsinline", "");
    player.controlsList = "nofullscreen";
    player.setAttribute("controlslist", "nofullscreen");
    player.src = file.url;
    const actions = document.createElement("div");
    actions.className = "memo-video-actions";
    const fsBtn = document.createElement("button");
    fsBtn.type = "button";
    fsBtn.className = "text-btn memo-video-fs-btn";
    fsBtn.textContent = "横屏全屏";
    fsBtn.hidden = true;
    fsBtn.addEventListener("click", () => enterVideoFs(player, home));
    const syncFsBtn = () => {
      const land = videoIsLandscape(player);
      fsBtn.hidden = !land;
      actions.hidden = !land;
    };
    player.addEventListener("loadedmetadata", syncFsBtn);
    if (player.readyState >= 1) syncFsBtn();
    actions.appendChild(fsBtn);
    home.appendChild(player);
    shell.append(home, actions);
    return shell;
  }

  function docTypeIcon(file) {
    const icon = document.createElement("div");
    icon.className = "memo-doc-icon" + (file.kind === "ebook" ? " is-ebook" : file.kind === "doc" ? " is-doc" : "");
    const ext = document.createElement("span");
    ext.className = "memo-doc-icon-ext";
    ext.textContent = String(file.ext || kindLabel(file.kind)).toUpperCase().slice(0, 5);
    const label = document.createElement("span");
    label.className = "memo-doc-icon-kind";
    label.textContent = kindLabel(file.kind);
    icon.append(ext, label);
    return icon;
  }

  function fileViewHref(file) {
    const query = new URLSearchParams({
      src: file.url,
      name: file.name || "文件",
      mime: file.mime || "",
      size: String(file.size || 0),
    });
    return `/file-view?${query}`;
  }

  function docThumb(file) {
    const wrap = document.createElement("div");
    wrap.className = "memo-doc";
    const link = document.createElement("a");
    link.className = "memo-doc-link";
    link.href = fileViewHref(file);
    link.title = file.name;
    const caption = document.createElement("span");
    caption.className = "memo-doc-name";
    caption.textContent = file.name;
    link.append(docTypeIcon(file), caption);
    wrap.appendChild(link);
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
        const preview = document.createElement("button");
        preview.type = "button";
        preview.className = "memo-image";
        preview.dataset.lightbox = file.url;
        preview.dataset.lightboxAlt = file.name;
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
        row.appendChild(mountVideo(file));
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
      const picked = document.createElement("div");
      picked.className = "memo-picked";
      picked.hidden = true;
      const hint = document.createElement("p");
      hint.className = "note memo-file-hint";
      hint.textContent = EMPTY_HINT_EDIT;
      input._memoPicker = createPicker(input, hint, picked, EMPTY_HINT_EDIT);
      label.appendChild(input);
      box.append(label, picked, hint);
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

  function openMemoForm() {
    if (!form) return;
    form.hidden = false;
    if (openFormBtn) openFormBtn.hidden = true;
    form.querySelector('[name="title"]')?.focus();
  }

  function closeMemoForm() {
    if (!form) return;
    form.hidden = true;
    form.reset();
    if (formPicker) formPicker.clear();
    if (openFormBtn) openFormBtn.hidden = false;
  }

  if (fileInput) {
    fileInput.accept = ACCEPT;
    formPicker = createPicker(fileInput, fileHint, formPicked, EMPTY_HINT);
  }

  if (openFormBtn) {
    openFormBtn.addEventListener("click", () => openMemoForm());
  }
  if (closeFormBtn) {
    closeFormBtn.addEventListener("click", () => {
      if (busy) return;
      closeMemoForm();
      report("");
    });
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy) return;
      const text = String((form.querySelector('[name="text"]') || {}).value || "").trim();
      const title = String((form.querySelector('[name="title"]') || {}).value || "");
      const files = formPicker ? formPicker.getFiles() : (fileInput ? [...fileInput.files] : []);
      if (!text && !files.length) {
        report("先写备忘，或选一个文件。");
        return;
      }
      const data = new FormData();
      data.set("title", title);
      data.set("text", text);
      files.forEach((file) => data.append("files", file));
      busy = true;
      if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = files.length ? "上传中…" : "保存中…";
      }
      try {
        if (files.length) {
          setUploadProgress(0, Math.max(1, files.reduce((sum, file) => sum + file.size, 0)), "正在上传");
          await uploadWithProgress("/api/memos", data, "正在上传");
        } else {
          await api("/api/memos", { method: "POST", body: data });
        }
        clearUploadProgress();
        report("");
        closeMemoForm();
        await reload();
      } catch (error) {
        clearUploadProgress();
        report(error.message);
      } finally {
        busy = false;
        if (addBtn) {
          addBtn.disabled = false;
          addBtn.textContent = "保存";
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
            const moreFiles = more && more._memoPicker
              ? more._memoPicker.getFiles()
              : (more && more.files ? [...more.files] : []);
            if (moreFiles.length) {
              const body = new FormData();
              moreFiles.forEach((file) => body.append("files", file));
              saveBtn.textContent = "上传中…";
              setUploadProgress(0, Math.max(1, moreFiles.reduce((sum, file) => sum + file.size, 0)), "正在上传");
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

  const VIEWPORT_BASE = "width=device-width, initial-scale=1";
  let zoomTimer = 0;

  function isMemoField(el) {
    return Boolean(
      el
      && /^(INPUT|TEXTAREA)$/.test(el.tagName)
      && el.type !== "file"
      && el.type !== "button"
      && el.type !== "submit"
      && el.type !== "checkbox"
      && el.type !== "radio",
    );
  }

  function restoreZoom() {
    const meta = document.querySelector('meta[name="viewport"]');
    if (!meta) return;
    /* iOS 聚焦放大后 visualViewport.scale 常常仍是 1，不能靠它判断 */
    meta.setAttribute("content", `${VIEWPORT_BASE}, maximum-scale=1`);
    window.clearTimeout(zoomTimer);
    zoomTimer = window.setTimeout(() => {
      meta.setAttribute("content", VIEWPORT_BASE);
      window.scrollTo(0, Math.max(0, window.scrollY));
    }, 300);
  }

  function scheduleZoomRestore() {
    window.setTimeout(() => {
      if (isMemoField(document.activeElement)) return;
      restoreZoom();
    }, 50);
  }

  function bindZoomRestore(root) {
    if (!root) return;
    root.addEventListener("focusout", (event) => {
      if (!isMemoField(event.target)) return;
      scheduleZoomRestore();
    }, true);
    root.addEventListener("blur", (event) => {
      if (!isMemoField(event.target)) return;
      scheduleZoomRestore();
    }, true);
  }

  bindZoomRestore(form);
  bindZoomRestore(list);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => {
      if (isMemoField(document.activeElement)) return;
      scheduleZoomRestore();
    });
  }

  let lightboxScrollY = 0;

  function lockPageScroll() {
    lightboxScrollY = window.scrollY || window.pageYOffset || 0;
    document.body.classList.add("memo-scroll-lock");
    document.body.style.top = `-${lightboxScrollY}px`;
  }

  function unlockPageScroll() {
    document.body.classList.remove("memo-scroll-lock");
    document.body.style.top = "";
    window.scrollTo(0, lightboxScrollY);
  }

  function closeLightbox() {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    if (lightboxImg) {
      lightboxImg.removeAttribute("src");
      lightboxImg.alt = "";
    }
    if (lightboxFrame) {
      lightboxFrame.hidden = true;
      lightboxFrame.removeAttribute("src");
    }
    unlockPageScroll();
  }

  function openLightbox(url, alt) {
    if (!lightbox || !lightboxImg || !url) return;
    if (lightboxFrame) {
      lightboxFrame.hidden = true;
      lightboxFrame.removeAttribute("src");
    }
    lightboxImg.hidden = false;
    lightboxImg.src = url;
    lightboxImg.alt = alt || "";
    lockPageScroll();
    lightbox.hidden = false;
  }

  if (list) {
    list.addEventListener("click", (event) => {
      const shot = event.target.closest("[data-lightbox]");
      if (!shot || !list.contains(shot)) return;
      event.preventDefault();
      openLightbox(shot.dataset.lightbox, shot.dataset.lightboxAlt || "");
    });
  }
  if (lightbox) {
    lightbox.addEventListener("click", (event) => {
      if (event.target === lightbox || event.target === lightboxImg) closeLightbox();
    });
    lightbox.addEventListener("touchmove", (event) => {
      event.preventDefault();
    }, { passive: false });
  }
  if (lightboxClose) {
    lightboxClose.addEventListener("click", (event) => {
      event.stopPropagation();
      closeLightbox();
    });
  }
  function toggleVideoFsPlay() {
    if (!videoFsPlayer) return;
    if (videoFsPlayer.paused) videoFsPlayer.play().catch(() => {});
    else videoFsPlayer.pause();
  }

  if (videoFsClose) {
    videoFsClose.addEventListener("click", (event) => {
      event.stopPropagation();
      exitVideoFs();
    });
  }
  if (videoFsPlay) {
    videoFsPlay.addEventListener("click", (event) => {
      event.stopPropagation();
      showVideoFsChrome(true);
      toggleVideoFsPlay();
    });
  }
  if (videoFsToggle) {
    videoFsToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!videoFsUi) return;
      if (videoFsUi.classList.contains("is-idle")) {
        showVideoFsChrome(true);
        return;
      }
      showVideoFsChrome(true);
      hideVideoFsChrome();
    });
  }
  if (videoFsSeek) {
    videoFsSeek.addEventListener("pointerdown", () => {
      videoFsSeeking = true;
      showVideoFsChrome(false);
    });
    videoFsSeek.addEventListener("pointerup", () => {
      videoFsSeeking = false;
      syncVideoFsUi();
      showVideoFsChrome(true);
    });
    videoFsSeek.addEventListener("change", () => {
      if (!videoFsPlayer) return;
      videoFsPlayer.currentTime = Number(videoFsSeek.value) || 0;
      videoFsSeeking = false;
      syncVideoFsUi();
      showVideoFsChrome(true);
    });
    videoFsSeek.addEventListener("input", () => {
      if (!videoFsPlayer) return;
      videoFsPlayer.currentTime = Number(videoFsSeek.value) || 0;
      if (videoFsTime) {
        const duration = Number.isFinite(videoFsPlayer.duration) ? videoFsPlayer.duration : 0;
        videoFsTime.textContent = `${formatPlayTime(videoFsSeek.value)} / ${formatPlayTime(duration)}`;
      }
    });
  }
  if (videoFsSpeeds) {
    videoFsSpeeds.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-rate]");
      if (!btn || !videoFsPlayer) return;
      videoFsPlayer.playbackRate = Number(btn.dataset.rate) || 1;
      syncVideoFsUi();
      showVideoFsChrome(true);
    });
  }
  if (videoFs) {
    videoFs.addEventListener("touchmove", (event) => {
      if (event.target.closest(".memo-video-fs-bottom, .memo-video-fs-top")) return;
      event.preventDefault();
    }, { passive: false });
  }
  window.addEventListener("resize", layoutCssLandFs);
  window.addEventListener("orientationchange", () => {
    window.setTimeout(() => {
      if (!videoFs || videoFs.hidden || !videoFsPlayer) return;
      const landscape = videoIsLandscape(videoFsPlayer);
      const portraitUi = window.matchMedia("(orientation: portrait)").matches;
      videoFs.classList.toggle("is-css-land", Boolean(landscape && portraitUi));
      layoutCssLandFs();
    }, 120);
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", layoutCssLandFs);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (videoFs && !videoFs.hidden) {
      exitVideoFs();
      return;
    }
    if (lightbox && !lightbox.hidden) closeLightbox();
  });

  window.QuickbackMemos = {
    async show(request, notify) {
      api = request;
      report = notify;
      await reload();
    },
  };
})();
