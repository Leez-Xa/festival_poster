(function () {
  const TASK_STATUSES = new Set(["pending", "processing", "success", "failed"]);
  const POLL_INTERVAL_MS = 2500;
  const POLL_INTERVAL_SLOW_MS = 5000;
  const WARNING_MS = 10000;
  const MAX_POLL_MS = 600000;

  const fallbackApiBase = (() => {
    const isHttpPage = location.protocol === "http:" || location.protocol === "https:";
    if (isHttpPage && !/:5173$/.test(location.origin)) {
      return `${location.origin}/api/v1`;
    }
    return "http://127.0.0.1:8000/api/v1";
  })();

  const state = {
    mode: "one-click",
    apiBase: fallbackApiBase,
    apiToken: localStorage.getItem("festivalPoster.apiToken") || "",
    intent: null,
    task: null,
    poster: null,
    pollTimer: null,
  };

  const els = {
    oneClickPage: document.querySelector("#oneClickPage"),
    modeTabs: [...document.querySelectorAll("[data-mode-target]")],
    manualBlocks: [
      document.querySelector(".workflow-card"),
      document.querySelector(".page-grid"),
    ].filter(Boolean),
    apiBaseInput: document.querySelector("#apiBaseInput"),
    apiTokenInput: document.querySelector("#apiTokenInput"),
    prompt: document.querySelector("#oneClickPrompt"),
    parseBtn: document.querySelector("#oneClickParseBtn"),
    generateBtn: document.querySelector("#oneClickGenerateBtn"),
    message: document.querySelector("#oneClickMessage"),
    intentState: document.querySelector("#oneClickIntentState"),
    tags: document.querySelector("#oneClickTags"),
    details: document.querySelector("#oneClickIntentDetails"),
    taskId: document.querySelector("#oneClickTaskId"),
    progressBar: document.querySelector("#oneClickProgressBar"),
    status: document.querySelector("#oneClickStatus"),
    step: document.querySelector("#oneClickStep"),
    posterFrame: document.querySelector("#oneClickPosterFrame"),
    posterEmpty: document.querySelector("#oneClickPosterEmpty"),
    posterImage: document.querySelector("#oneClickPosterImage"),
    copy: document.querySelector("#oneClickCopy"),
    posterUrl: document.querySelector("#oneClickPosterUrl"),
    error: document.querySelector("#oneClickError"),
    downloadBtn: document.querySelector("#oneClickDownloadBtn"),
    openBtn: document.querySelector("#oneClickOpenBtn"),
  };

  function normalizeApiBase(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function syncRuntimeConfig() {
    state.apiBase = normalizeApiBase(els.apiBaseInput?.value) || fallbackApiBase;
    state.apiToken = els.apiTokenInput?.value?.trim() || localStorage.getItem("festivalPoster.apiToken") || "";
  }

  function apiPath(path) {
    syncRuntimeConfig();
    return `${state.apiBase}${path.startsWith("/") ? path : `/${path}`}`;
  }

  function apiOrigin() {
    try {
      return new URL(state.apiBase).origin;
    } catch {
      return location.origin;
    }
  }

  function resolveReturnedUrl(url) {
    if (!url) return "";
    try {
      return new URL(url, apiOrigin()).href;
    } catch {
      return url;
    }
  }

  function headers(extra = {}) {
    const auth = state.apiToken ? { "X-API-Token": state.apiToken } : {};
    return {
      "X-Demo-User-Id": "demo_sales_user",
      ...auth,
      ...extra,
    };
  }

  async function request(path, options = {}) {
    const fetchOptions = {
      method: options.method || "GET",
      headers: headers(options.body === undefined ? options.headers : {
        "Content-Type": "application/json; charset=utf-8",
        ...(options.headers || {}),
      }),
    };
    if (options.body !== undefined) {
      fetchOptions.body = JSON.stringify(options.body);
    }

    let response;
    try {
      response = await fetch(apiPath(path), fetchOptions);
    } catch (error) {
      throw makeError("NETWORK_ERROR", `无法连接后端：${error.message}`);
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      throw makeError("BAD_RESPONSE", "后端未返回 JSON 响应。");
    }

    if (!response.ok) {
      const error = payload?.error || {};
      throw makeError(error.code || `HTTP_${response.status}`, error.message || `接口请求失败：HTTP ${response.status}`, error.details, payload?.request_id);
    }

    if (payload && typeof payload === "object" && "success" in payload) {
      if (payload.success === false) {
        const error = payload.error || {};
        throw makeError(error.code || "API_ERROR", error.message || "接口请求失败。", error.details, payload.request_id);
      }
      return payload.data;
    }

    return payload;
  }

  function makeError(code, message, details, requestId) {
    const error = new Error(message);
    error.code = code;
    error.details = details || null;
    error.requestId = requestId || "";
    return error;
  }

  function showMessage(message, tone = "") {
    els.message.hidden = !message;
    els.message.textContent = message || "";
    els.message.className = `state-message ${tone}`.trim();
  }

  function setMode(mode) {
    state.mode = mode;
    document.body.dataset.generationMode = mode;
    els.oneClickPage.hidden = mode !== "one-click";
    els.manualBlocks.forEach((block) => {
      block.hidden = mode !== "manual";
    });
    els.modeTabs.forEach((tab) => {
      const active = tab.dataset.modeTarget === mode;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function promptValue() {
    return els.prompt.value.trim();
  }

  function requirePrompt() {
    const value = promptValue();
    if (!value) {
      showMessage("请先输入一句话生图指令。", "warning");
      els.prompt.focus();
      return "";
    }
    return value;
  }

  async function parseIntent({ silent = false } = {}) {
    const instruction = requirePrompt();
    if (!instruction) return null;

    els.parseBtn.disabled = true;
    els.generateBtn.disabled = true;
    els.intentState.textContent = "解析中";
    if (!silent) showMessage("正在解析节日、产品、风格、画面元素和文案方向...");

    try {
      const data = await request("/one-click-intent", {
        method: "POST",
        body: { raw_instruction: instruction },
      });
      state.intent = data || null;
      renderIntent(state.intent);
      if (state.intent?.blocked) {
        showMessage(formatBlockedIntent(state.intent), "error");
      } else {
        showMessage("解析完成，可以直接一键生成。", "success");
      }
      return state.intent;
    } catch (error) {
      state.intent = null;
      renderIntent(null);
      showMessage(formatError(error, "预解析失败"), "error");
      return null;
    } finally {
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = Boolean(state.intent?.blocked);
    }
  }

  async function createOneClickTask() {
    const instruction = requirePrompt();
    if (!instruction) return;

    stopPolling();
    resetTaskView();
    els.parseBtn.disabled = true;
    els.generateBtn.disabled = true;
    showMessage("正在创建一键生图任务...");
    updateProgress({ status: "pending", progress: 0, current_step: "任务创建中" });

    let intent = state.intent;
    if (!intent) {
      intent = await parseIntent({ silent: true });
      if (!intent) {
        els.parseBtn.disabled = false;
        els.generateBtn.disabled = false;
        return;
      }
      els.parseBtn.disabled = true;
      els.generateBtn.disabled = true;
    }
    if (intent?.blocked) {
      showMessage(formatBlockedIntent(intent), "error");
      renderTaskError({
        code: "ONE_CLICK_INPUT_BLOCKED",
        message: "一键生图指令未通过生成前检测。",
        details: intent,
      });
      updateProgress({ status: "failed", progress: 0, current_step: "输入被阻断，请修改后再生成" });
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = true;
      return;
    }

    try {
      const data = await request("/one-click-poster-tasks", {
        method: "POST",
        body: {
          raw_instruction: instruction,
        },
      });
      if (!data?.task_id) {
        throw makeError("BAD_RESPONSE", "后端创建任务响应缺少 task_id。");
      }
      if (data.status && !TASK_STATUSES.has(data.status)) {
        showMessage(`任务已创建，但后端返回未知状态：${data.status}`, "warning");
      } else {
        showMessage(`任务已创建：${data.task_id}`, "success");
      }
      state.task = data;
      els.taskId.textContent = data.task_id;
      updateProgress(data);
      startPolling(data.task_id);
    } catch (error) {
      showMessage(formatError(error, "创建一键生图任务失败"), "error");
      renderTaskError(error);
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = false;
    }
  }

  function startPolling(taskId) {
    stopPolling();
    pollTask(taskId, Date.now());
  }

  async function pollTask(taskId, startedAt) {
    if (Date.now() - startedAt > MAX_POLL_MS) {
      stopPolling();
      showMessage("生成已超过4分钟，前端已暂停轮询。任务可能仍在后端处理中。", "warning");
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = false;
      return;
    }

    try {
      const data = await request(`/poster-tasks/${encodeURIComponent(taskId)}`);
      handleTaskUpdate(data, startedAt);
    } catch (error) {
      if (error.code === "TASK_NOT_READY") {
        updateProgress({
          status: "processing",
          progress: state.task?.progress || 0,
          current_step: "任务未完成，继续轮询",
        });
        scheduleNextPoll(taskId, startedAt);
        return;
      }
      stopPolling();
      showMessage(formatError(error, "轮询任务失败"), "error");
      renderTaskError(error);
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = false;
    }
  }

  function scheduleNextPoll(taskId, startedAt) {
    const elapsed = Date.now() - startedAt;
    const delay = elapsed > WARNING_MS ? POLL_INTERVAL_SLOW_MS : POLL_INTERVAL_MS;
    state.pollTimer = window.setTimeout(() => pollTask(taskId, startedAt), delay);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function handleTaskUpdate(data, startedAt) {
    state.task = data;
    updateProgress(data);
    renderTaskResult(data);

    if (data.status === "success") {
      stopPolling();
      state.poster = data.poster || null;
      renderPoster();
      showMessage("海报生成完成。", "success");
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = false;
      return;
    }

    if (data.status === "failed") {
      stopPolling();
      showMessage(data.error?.message || "海报生成失败，请查看错误提示。", "error");
      renderTaskError(data.error);
      renderPoster();
      els.parseBtn.disabled = false;
      els.generateBtn.disabled = false;
      return;
    }

    if (Date.now() - startedAt > WARNING_MS) {
      showMessage("生图耗时较长，请稍等；完成后会自动显示预览。", "warning");
    }
    scheduleNextPoll(data.task_id, startedAt);
  }

  function updateProgress(task) {
    const progress = clamp(Number(task?.progress || 0), 0, 100);
    els.progressBar.style.width = `${progress}%`;
    els.status.textContent = `${statusLabel(task?.status)} · ${progress}%`;
    els.step.textContent = task?.current_step || "";
    if (task?.task_id) els.taskId.textContent = task.task_id;
  }

  function resetTaskView() {
    state.task = null;
    state.poster = null;
    els.taskId.textContent = "未创建";
    els.progressBar.style.width = "0%";
    els.status.textContent = "等待创建任务";
    els.step.textContent = "";
    els.copy.textContent = "等待后端返回";
    els.posterUrl.textContent = "等待后端返回";
    els.error.textContent = "无";
    els.downloadBtn.disabled = true;
    els.openBtn.hidden = true;
    els.openBtn.href = "#";
    els.posterImage.hidden = true;
    els.posterImage.removeAttribute("src");
    els.posterEmpty.hidden = false;
    els.posterEmpty.textContent = "任务完成后显示海报";
    els.posterFrame.classList.remove("has-poster");
  }

  function renderIntent(intent) {
    const normalized = normalizeIntent(intent);
    els.intentState.textContent = intent ? "已解析" : "等待输入";
    els.tags.innerHTML = "";
    normalized.tags.forEach((tag) => {
      const item = document.createElement("span");
      item.textContent = tag;
      if (tag === "已阻断") item.classList.add("blocked");
      els.tags.append(item);
    });
    els.details.innerHTML = "";
    normalized.details.forEach(([label, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = formatValue(value);
      row.append(dt, dd);
      els.details.append(row);
    });
  }

  function normalizeIntent(intent) {
    if (!intent || typeof intent !== "object") {
      return {
        tags: ["节日活动", "产品", "风格", "画面元素", "文案方向"],
        details: [],
      };
    }
    const source = intent.intent || intent;
    const activity = formatValue(pick(source, ["node", "festival", "festival_name", "activity", "activity_name", "activity_hint", "node_name", "marketing_node"])) || "待补充";
    const product = formatValue(pick(source, ["product", "product_hint", "product_name", "product_id"])) || "待补充";
    const style = formatValue(pick(source, ["style_keywords", "style", "style_preference", "visual_style"])) || "由系统补齐";
    const elements = formatValue(pick(source, ["visual_elements", "scene_elements", "elements"])) || "由系统补齐";
    const copyDirection = formatValue(pick(source, ["copy_direction", "copy", "slogan_direction"])) || "节日传播";
    const tags = [activity, product, style, elements, copyDirection].filter(Boolean);
    if (intent.blocked) tags.unshift("已阻断");

    const safetyText = formatGuardrailIssues(intent.safety?.issues);
    const validationText = formatGuardrailIssues(intent.validation?.issues);
    const detailPairs = [
      ["生成前检测", intent.blocked ? formatBlockedIntent(intent) : "可以生成"],
      ["节日活动", activity],
      ["产品", product],
      ["风格方向", style],
      ["画面元素", elements],
      ["文案方向", copyDirection],
      ["需要修改", [safetyText, validationText].filter(Boolean).join("；")],
    ].filter(([, value]) => value !== undefined && value !== null && formatValue(value));

    return {
      tags: tags.length ? tags.slice(0, 8) : ["已解析"],
      details: detailPairs,
    };
  }

  function pick(source, keys) {
    for (const key of keys) {
      if (source?.[key] !== undefined && source[key] !== null && source[key] !== "") {
        return source[key];
      }
    }
    return "";
  }

  function formatValue(value) {
    if (Array.isArray(value)) {
      return value.map(formatValue).filter(Boolean).join("、");
    }
    if (value && typeof value === "object") {
      const label = value.name || value.title || value.label || value.id;
      if (label) return String(label);
      return "";
    }
    return String(value || "").trim();
  }

  function renderTaskResult(task) {
    const copy = extractCopy(task);
    els.copy.textContent = copy.title || copy.subtitle
      ? `主标题：${copy.title || "未返回"}；副标题：${copy.subtitle || "未返回"}`
      : ["pending", "processing"].includes(task?.status)
        ? "AI 文案生成中"
        : "后端未返回显式文案字段";
    const posterUrl = task?.poster?.jpg_url || state.poster?.jpg_url || "";
    els.posterUrl.textContent = posterUrl || "等待后端返回";
    if (posterUrl) {
      const resolved = resolveReturnedUrl(posterUrl);
      els.openBtn.href = resolved;
      els.openBtn.hidden = false;
    }
    renderTaskError(task?.error);
  }

  function renderTaskError(error) {
    if (!error) {
      els.error.textContent = "无";
      return;
    }
    const reason = formatBlockReasons(error.details);
    els.error.textContent = `${error.code || "TASK_ERROR"}: ${error.message || error.message || "任务异常"}${reason ? ` ${reason}` : ""}`;
  }

  function renderPoster() {
    const poster = state.poster || state.task?.poster;
    const url = poster?.thumbnail_url || poster?.jpg_url;
    if (!url) {
      els.posterImage.hidden = true;
      els.posterEmpty.hidden = false;
      els.posterEmpty.textContent = state.task?.status === "failed" ? "任务失败，暂无可预览海报" : "任务完成后显示海报";
      els.downloadBtn.disabled = true;
      els.posterFrame.classList.remove("has-poster");
      return;
    }
    const resolved = resolveReturnedUrl(url);
    els.posterImage.src = resolved;
    els.posterImage.hidden = false;
    els.posterEmpty.hidden = true;
    els.posterFrame.classList.add("has-poster");
    els.downloadBtn.disabled = false;
    els.openBtn.href = resolveReturnedUrl(poster.jpg_url || url);
    els.openBtn.hidden = false;
  }

  function extractCopy(task) {
    const source = task?.copy || task?.generated_copy || task?.poster?.copy || {};
    return {
      title: source.title || source.main_title || "",
      subtitle: source.subtitle || source.sub_title || "",
    };
  }

  async function downloadPoster() {
    const poster = state.poster || state.task?.poster;
    if (!poster?.jpg_url) return;
    els.downloadBtn.disabled = true;
    try {
      const response = await fetch(resolveReturnedUrl(poster.jpg_url), { headers: headers() });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `${poster.title || poster.id || "one-click-poster"}.jpg`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      showMessage(`下载失败：${error.message}`, "error");
    } finally {
      els.downloadBtn.disabled = !poster?.jpg_url;
    }
  }

  function statusLabel(status) {
    const map = {
      pending: "等待中",
      processing: "生成中",
      success: "生成成功",
      failed: "生成失败",
    };
    return map[status] || status || "未创建";
  }

  function formatError(error, prefix) {
    const requestPart = error.requestId ? `（request_id: ${error.requestId}）` : "";
    const reason = formatBlockReasons(error.details);
    return `${prefix}：${error.code || "ERROR"} ${error.message || "未知错误"}${reason ? ` ${reason}` : ""}${requestPart}`;
  }

  function formatBlockedIntent(intent) {
    const reason = formatBlockReasons(intent);
    return reason || intent?.user_message || "一键生图指令未通过生成前检测，请修改后再提交。";
  }

  function formatBlockReasons(details) {
    if (!details || typeof details !== "object") return "";
    const reasons = details.block_reasons
      || [
        ...(details.safety?.issues || []).map((item) => item.message),
        ...(details.validation?.issues || []).map((item) => item.message),
      ];
    const clean = [...new Set((reasons || []).map((item) => String(item || "").trim()).filter(Boolean))];
    if (!clean.length) return "";
    return `阻断原因：${clean.slice(0, 4).join("；")}`;
  }

  function formatGuardrailIssues(issues) {
    if (!Array.isArray(issues) || !issues.length) return "";
    return issues.map((item) => item.message || item.suggestion || item.term).filter(Boolean).join("；");
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(Number.isFinite(value) ? value : min, min), max);
  }

  function bindEvents() {
    els.modeTabs.forEach((tab) => {
      tab.addEventListener("click", () => setMode(tab.dataset.modeTarget));
    });
    els.parseBtn.addEventListener("click", () => parseIntent());
    els.generateBtn.addEventListener("click", createOneClickTask);
    els.downloadBtn.addEventListener("click", downloadPoster);
    els.prompt.addEventListener("input", () => {
      state.intent = null;
      els.intentState.textContent = "已修改，待解析";
      els.generateBtn.disabled = false;
      els.error.textContent = "无";
    });
  }

  function init() {
    if (!els.oneClickPage) return;
    bindEvents();
    setMode("one-click");
    renderIntent(null);
    resetTaskView();
  }

  init();
})();
