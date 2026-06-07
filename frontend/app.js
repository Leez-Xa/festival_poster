const TASK_STATUSES = new Set(["pending", "processing", "success", "failed"]);
const AI_IMAGE_WARNING_MS = 10000;
const AI_IMAGE_MAX_POLL_MS = 240000;
const POLL_INTERVAL_MS = 2500;
const POLL_INTERVAL_SLOW_MS = 5000;
const TASK_STEP_LABELS = {
  "Waiting for generation": "等待生成",
  "Reading uploaded assets": "正在读取上传素材",
  "Generating scene prompt and copy": "正在生成场景提示词和文案",
  "Fusing product into festival scene": "正在融合产品与节日场景",
  "Generation completed": "生成完成",
  "Generation failed": "生成失败",
  "Preview copy rerendered": "预览文案已重新合成",
};
const API_BASE_FALLBACK = (() => {
  const isHttpPage = location.protocol === "http:" || location.protocol === "https:";
  if (isHttpPage && location.hostname && !["localhost", "127.0.0.1"].includes(location.hostname)) {
    return `${location.origin}/api/v1`;
  }
  if (isHttpPage && /:8000$/.test(location.origin)) {
    return `${location.origin}/api/v1`;
  }
  return "http://127.0.0.1:8000/api/v1";
})();

const state = {
  currentScreen: "nodes",
  apiBase: API_BASE_FALLBACK,
  apiToken: localStorage.getItem("festivalPoster.apiToken") || "",
  nodes: [],
  selectedNode: null,
  customNodeDraft: null,
  products: [],
  selectedProductId: "",
  sourceMode: "system_product",
  systemProductAssets: [],
  systemAssets: {
    logo: [],
    qrcode: [],
    bottom_bar: [],
  },
  selectedAssets: {
    logo: null,
    qrcode: null,
    bottom_bar: null,
  },
  productAssets: [],
  sceneAsset: null,
  activeTask: null,
  poster: null,
  copyDirty: false,
  copyRevision: 0,
  rerendering: false,
  compliance: {
    status: "unknown",
    issues: [],
    risk_level: "",
    suggested_title: "",
    suggested_subtitle: "",
  },
  protocolIssues: [],
  pollingTimer: null,
  complianceTimer: null,
};

const els = {
  apiBaseInput: document.querySelector("#apiBaseInput"),
  apiTokenInput: document.querySelector("#apiTokenInput"),
  resetApiBaseBtn: document.querySelector("#resetApiBaseBtn"),
  stepper: document.querySelector("#stepper"),
  screens: [...document.querySelectorAll(".screen")],
  nodeGrid: document.querySelector("#nodeGrid"),
  nodesMessage: document.querySelector("#nodesMessage"),
  reloadNodesBtn: document.querySelector("#reloadNodesBtn"),
  goAssetsBtn: document.querySelector("#goAssetsBtn"),
  customNodeName: document.querySelector("#customNodeName"),
  customNodeDate: document.querySelector("#customNodeDate"),
  useCustomNodeBtn: document.querySelector("#useCustomNodeBtn"),
  dropZone: document.querySelector("#dropZone"),
  assetProductSelect: document.querySelector("#assetProductSelect"),
  sourceModeInputs: [...document.querySelectorAll('input[name="sourceMode"]')],
  systemProductSection: document.querySelector("#systemProductSection"),
  reloadProductAssetsBtn: document.querySelector("#reloadProductAssetsBtn"),
  systemProductAssetsGrid: document.querySelector("#systemProductAssetsGrid"),
  uploadZoneTitle: document.querySelector("#uploadZoneTitle"),
  uploadZoneHint: document.querySelector("#uploadZoneHint"),
  productFileInput: document.querySelector("#productFileInput"),
  pickProductFilesBtn: document.querySelector("#pickProductFilesBtn"),
  productAssetGrid: document.querySelector("#productAssetGrid"),
  uploadMessage: document.querySelector("#uploadMessage"),
  reloadAssetsBtn: document.querySelector("#reloadAssetsBtn"),
  logoGrid: document.querySelector("#logoGrid"),
  qrcodeGrid: document.querySelector("#qrcodeGrid"),
  bottomBarGrid: document.querySelector("#bottomBarGrid"),
  bottomBarHint: document.querySelector("#bottomBarHint"),
  contactInput: document.querySelector("#contactInput"),
  goConfigBtn: document.querySelector("#goConfigBtn"),
  reloadProductsBtn: document.querySelector("#reloadProductsBtn"),
  productSelect: document.querySelector("#productSelect"),
  copyDirectionSelect: document.querySelector("#copyDirectionSelect"),
  styleSelect: document.querySelector("#styleSelect"),
  templateInput: document.querySelector("#templateInput"),
  copyModeInputs: [...document.querySelectorAll('input[name="copyMode"]')],
  titlePreferenceLabel: document.querySelector("#titlePreferenceLabel"),
  subtitlePreferenceLabel: document.querySelector("#subtitlePreferenceLabel"),
  titlePreferenceInput: document.querySelector("#titlePreferenceInput"),
  subtitlePreferenceInput: document.querySelector("#subtitlePreferenceInput"),
  customRequirementInput: document.querySelector("#customRequirementInput"),
  createTaskBtn: document.querySelector("#createTaskBtn"),
  taskMessage: document.querySelector("#taskMessage"),
  pollingPanel: document.querySelector("#pollingPanel"),
  progressBar: document.querySelector("#progressBar"),
  pollingStatus: document.querySelector("#pollingStatus"),
  pollingStep: document.querySelector("#pollingStep"),
  posterEmpty: document.querySelector("#posterEmpty"),
  posterImage: document.querySelector("#posterImage"),
  resultTaskStatus: document.querySelector("#resultTaskStatus"),
  resultCopy: document.querySelector("#resultCopy"),
  resultPosterUrl: document.querySelector("#resultPosterUrl"),
  resultFusion: document.querySelector("#resultFusion"),
  resultError: document.querySelector("#resultError"),
  previewTitleInput: document.querySelector("#previewTitleInput"),
  previewSubtitleInput: document.querySelector("#previewSubtitleInput"),
  previewProductAssetSelect: document.querySelector("#previewProductAssetSelect"),
  logoPositionSelect: document.querySelector("#logoPositionSelect"),
  previewQrcodeSelect: document.querySelector("#previewQrcodeSelect"),
  previewBottomBarSelect: document.querySelector("#previewBottomBarSelect"),
  runComplianceBtn: document.querySelector("#runComplianceBtn"),
  complianceCard: document.querySelector("#complianceCard"),
  downloadBtn: document.querySelector("#downloadBtn"),
  newTaskBtn: document.querySelector("#newTaskBtn"),
  summaryNode: document.querySelector("#summaryNode"),
  summaryProduct: document.querySelector("#summaryProduct"),
  summaryProductAssets: document.querySelector("#summaryProductAssets"),
  summarySystemAssets: document.querySelector("#summarySystemAssets"),
  summaryTask: document.querySelector("#summaryTask"),
  protocolBox: document.querySelector("#protocolBox"),
  protocolIssues: document.querySelector("#protocolIssues"),
};

const nodeCardTemplate = document.querySelector("#nodeCardTemplate");
const assetChoiceTemplate = document.querySelector("#assetChoiceTemplate");

function normalizeApiBase(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function apiPath(path) {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${state.apiBase}${cleanPath}`;
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

function authHeaders() {
  return state.apiToken ? { "X-API-Token": state.apiToken } : {};
}

function addProtocolIssue(issue) {
  if (!issue || state.protocolIssues.includes(issue)) return;
  state.protocolIssues.push(issue);
  renderProtocolIssues();
}

function renderProtocolIssues() {
  els.protocolBox.hidden = state.protocolIssues.length === 0;
  els.protocolIssues.innerHTML = "";
  state.protocolIssues.forEach((issue) => {
    const li = document.createElement("li");
    li.textContent = issue;
    els.protocolIssues.append(li);
  });
}

function requiredFields(label, obj, fields) {
  if (!obj || typeof obj !== "object") {
    addProtocolIssue(`${label} 未返回对象数据。`);
    return false;
  }
  let ok = true;
  fields.forEach((field) => {
    if (!(field in obj) || obj[field] === undefined || obj[field] === null || obj[field] === "") {
      ok = false;
      addProtocolIssue(`${label} 缺少字段 \`${field}\`。`);
    }
  });
  return ok;
}

async function request(path, options = {}) {
  const fetchOptions = {
    method: options.method || "GET",
    headers: {
      "X-Demo-User-Id": "demo_sales_user",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  };

  if (options.body instanceof FormData) {
    fetchOptions.body = options.body;
  } else if (options.body !== undefined) {
    fetchOptions.headers["Content-Type"] = "application/json; charset=utf-8";
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

  if (!("success" in payload) || !("data" in payload) || !("error" in payload) || !("request_id" in payload)) {
    addProtocolIssue(`${path} 响应不符合统一结构 success/data/error/request_id。`);
  }

  if (!response.ok || payload.success === false) {
    const error = payload.error || {};
    throw makeError(error.code || `HTTP_${response.status}`, error.message || "接口请求失败", error.details, payload.request_id);
  }

  return payload.data;
}

function makeError(code, message, details, requestId) {
  const error = new Error(message);
  error.code = code;
  error.details = details || null;
  error.requestId = requestId || "";
  return error;
}

function showMessage(el, message, tone = "") {
  el.hidden = !message;
  el.textContent = message || "";
  el.className = `state-message ${tone}`.trim();
}

function setScreen(screen) {
  state.currentScreen = screen;
  els.screens.forEach((el) => {
    el.classList.toggle("active", el.dataset.screen === screen);
  });

  const order = ["nodes", "assets", "config", "preview"];
  els.stepper.querySelectorAll("li").forEach((item) => {
    const itemStep = item.dataset.stepIndicator;
    const itemIndex = order.indexOf(itemStep);
    const activeIndex = order.indexOf(screen);
    item.classList.toggle("active", itemStep === screen);
    item.classList.toggle("done", activeIndex > itemIndex && activeIndex !== -1);
  });

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelector(".main-panel")?.scrollIntoView({
    behavior: reduceMotion ? "auto" : "smooth",
    block: "start",
  });
}

function renderSummary() {
  const selectedProduct = state.products.find((product) => product.id === state.selectedProductId);
  els.summaryNode.textContent = state.selectedNode
    ? `${state.selectedNode.name}${state.selectedNode.type ? ` · ${typeLabel(state.selectedNode.type)}` : ""}`
    : "未选择";
  els.summaryProduct.textContent = selectedProduct ? `${selectedProduct.name}${selectedProduct.model ? ` · ${selectedProduct.model}` : ""}` : "未选择";
  els.summaryProductAssets.textContent = sourceSelectionSummary();

  const pieces = [];
  pieces.push(state.selectedAssets.logo ? "Logo已选" : "Logo未选");
  pieces.push(state.selectedAssets.bottom_bar ? "底部条已选" : "底部条未选");
  pieces.push(state.selectedAssets.qrcode ? "已额外添加二维码" : "二维码默认不单独添加");
  els.summarySystemAssets.textContent = pieces.join("，");

  if (!state.activeTask) {
    els.summaryTask.textContent = "未创建";
  } else {
    els.summaryTask.textContent = `${statusLabel(state.activeTask.status)}${state.activeTask.task_id ? ` · ${state.activeTask.task_id}` : ""}`;
  }

  els.goAssetsBtn.disabled = !state.selectedNode || state.selectedNode.isCustom;
  els.goConfigBtn.disabled = !canGoConfig();
  els.createTaskBtn.disabled = !canCreateTask();
  els.downloadBtn.disabled = !canDownload();
}

function typeLabel(type) {
  const map = {
    solar_term: "节气",
    festival: "节日",
    ecommerce: "电商活动",
    company_event: "企业活动",
    custom: "自定义",
  };
  return map[type] || type || "节点";
}

function statusLabel(status) {
  const map = {
    pending: "等待中",
    processing: "生成中",
    success: "生成成功",
    failed: "生成失败",
  };
  return map[status] || status || "未知";
}

function taskStepLabel(step) {
  return TASK_STEP_LABELS[step] || step || "";
}

function canGoConfig() {
  return (
    hasRequiredSourceAsset() &&
    Boolean(state.selectedAssets.logo) &&
    Boolean(state.selectedAssets.bottom_bar)
  );
}

function canCreateTask() {
  const taskRunning = ["pending", "processing"].includes(state.activeTask?.status);
  return (
    !taskRunning &&
    Boolean(state.selectedNode) &&
    !state.selectedNode?.isCustom &&
    Boolean(state.selectedProductId) &&
    canGoConfig()
  );
}

function canDownload() {
  return Boolean(state.poster?.jpg_url) && state.compliance.status === "passed" && !state.copyDirty && !state.rerendering;
}

function hasRequiredSourceAsset() {
  if (state.sourceMode === "scene_image") {
    return Boolean(state.sceneAsset);
  }
  return state.productAssets.length >= 1 && state.productAssets.length <= 5;
}

function sourceSelectionSummary() {
  if (state.sourceMode === "scene_image") {
    return state.sceneAsset ? "场景图已选" : "场景图未选";
  }
  if (state.sourceMode === "system_product") {
    return state.productAssets.length ? `系统产品图 ${state.productAssets.length} 张` : "系统产品图未选";
  }
  return state.productAssets.length ? `上传产品图 ${state.productAssets.length} 张` : "上传产品图未选";
}

async function loadNodes() {
  showMessage(els.nodesMessage, "正在从后端读取营销节点...");
  els.reloadNodesBtn.disabled = true;
  try {
    const data = await request("/marketing-nodes");
    const items = data?.items;
    if (!Array.isArray(items)) {
      addProtocolIssue("GET /marketing-nodes 缺少 data.items 数组。");
      state.nodes = [];
      showMessage(els.nodesMessage, "后端未返回节点列表，请检查 seed 数据。", "warning");
    } else {
      state.nodes = items;
      showMessage(els.nodesMessage, items.length ? "" : "节点列表为空，请检查后端 seed 数据。", items.length ? "" : "warning");
    }
  } catch (error) {
    state.nodes = [];
    showMessage(els.nodesMessage, `${error.code}: ${error.message}`, "error");
  } finally {
    els.reloadNodesBtn.disabled = false;
    renderNodes();
    renderSummary();
  }
}

function renderNodes() {
  els.nodeGrid.innerHTML = "";
  state.nodes.forEach((node) => {
    requiredFields("marketing-node", node, ["id", "name", "type"]);
    const card = nodeCardTemplate.content.firstElementChild.cloneNode(true);
    card.classList.toggle("selected", state.selectedNode?.id === node.id);
    card.querySelector(".node-type").textContent = typeLabel(node.type);
    card.querySelector("strong").textContent = node.name || "未命名节点";
    card.querySelector("p").textContent = node.visual_direction || node.copy_direction || "后端未返回风格方向";
    card.querySelector("small").textContent = Array.isArray(node.keywords) ? node.keywords.join(" / ") : "关键词待补充";

    const swatches = card.querySelector(".swatches");
    swatches.innerHTML = "";
    (Array.isArray(node.colors) ? node.colors : []).slice(0, 4).forEach((color) => {
      const swatch = document.createElement("span");
      swatch.style.background = color;
      swatch.title = color;
      swatches.append(swatch);
    });

    card.addEventListener("click", () => {
      state.selectedNode = node;
      state.customNodeDraft = null;
      prefillStyleFromNode(node);
      renderNodes();
      renderSummary();
    });
    els.nodeGrid.append(card);
  });
}

function prefillStyleFromNode(node) {
  const text = `${node?.copy_direction || ""} ${node?.visual_direction || ""} ${(node?.keywords || []).join(" ")}`;
  if (/红|金|春节|国庆|中秋|元宵/.test(text)) {
    els.styleSelect.value = "红金喜庆";
  } else if (/暖|冬|温馨|立冬|冬至/.test(text)) {
    els.styleSelect.value = "暖色温馨";
  } else if (/科技|环保|周年|蓝|绿/.test(text)) {
    els.styleSelect.value = "科技蓝+环保绿";
  } else {
    els.styleSelect.value = "极简高级";
  }
}

function useCustomNode() {
  const name = els.customNodeName.value.trim();
  const date = els.customNodeDate.value;
  if (!name) {
    showMessage(els.nodesMessage, "请先填写自定义活动名称。", "warning");
    return;
  }

  const draft = {
    id: "",
    name,
    date,
    type: "custom",
    isCustom: true,
  };
  state.selectedNode = draft;
  state.customNodeDraft = draft;
  addProtocolIssue("PRD 10.1 需要自定义节点名称和日期，但 12.13 创建任务请求体未定义 custom_node_name/custom_node_date 字段。当前不向 /poster-tasks 提交自定义节点。");
  showMessage(els.nodesMessage, "已记录自定义节点协议缺口。请选择后端 seed 节点继续联调生成链路。", "warning");
  renderNodes();
  renderSummary();
}

async function loadSystemAssets() {
  els.reloadAssetsBtn.disabled = true;
  showMessage(els.uploadMessage, "正在读取系统 Logo、额外二维码和底部宣传条...");
  try {
    const [logos, qrcodes, bottomBars] = await Promise.all([
      loadAssetsByType("logo"),
      loadAssetsByType("qrcode"),
      loadAssetsByType("bottom_bar"),
    ]);
    state.systemAssets.logo = logos;
    state.systemAssets.qrcode = qrcodes;
    state.systemAssets.bottom_bar = bottomBars;

    selectFirstMissing("logo");
    selectFirstMissing("bottom_bar");

    const missing = [];
    if (!logos.length) missing.push("Logo");
    if (!bottomBars.length) missing.push("底部宣传条");
    showMessage(
      els.uploadMessage,
      missing.length ? `后端未返回${missing.join("、")}系统素材，请上传自定义素材或检查 seed 数据。` : "",
      missing.length ? "warning" : "",
    );
  } catch (error) {
    showMessage(els.uploadMessage, `${error.code}: ${error.message}`, "error");
  } finally {
    els.reloadAssetsBtn.disabled = false;
    renderAssetChoices();
    renderSummary();
  }
}

async function loadAssetsByType(assetType) {
  const data = await request(`/assets?asset_type=${encodeURIComponent(assetType)}&source=system`);
  if (!Array.isArray(data?.items)) {
    addProtocolIssue(`GET /assets?asset_type=${assetType}&source=system 缺少 data.items 数组。`);
    return [];
  }
  data.items.forEach((asset) => requiredFields(`${assetType} asset`, asset, ["id", "asset_type", "public_url"]));
  return data.items;
}

function selectFirstMissing(assetType) {
  if (!state.selectedAssets[assetType] && state.systemAssets[assetType][0]) {
    state.selectedAssets[assetType] = state.systemAssets[assetType][0];
  }
}

function renderAssetChoices() {
  renderChoiceGrid("logo", els.logoGrid);
  renderChoiceGrid("qrcode", els.qrcodeGrid);
  renderChoiceGrid("bottom_bar", els.bottomBarGrid);
  renderPreviewSelects();
  els.bottomBarHint.textContent = state.selectedAssets.bottom_bar
    ? "已选择底部宣传条。"
    : "未选择底部条。MVP不会伪造默认素材，请上传或等待后端 seed 默认底部条。";
}

function renderChoiceGrid(assetType, grid) {
  grid.innerHTML = "";
  const assets = state.systemAssets[assetType];
  if (assetType === "qrcode") {
    const noneCard = assetChoiceTemplate.content.firstElementChild.cloneNode(true);
    noneCard.classList.toggle("selected", !state.selectedAssets.qrcode);
    noneCard.querySelector(".choice-thumb").textContent = "默认";
    noneCard.querySelector("strong").textContent = "不单独添加二维码";
    noneCard.querySelector("small").textContent = "使用底部宣传条中的二维码信息";
    noneCard.addEventListener("click", () => {
      state.selectedAssets.qrcode = null;
      renderAssetChoices();
      renderSummary();
    });
    grid.append(noneCard);
  }
  if (!assets.length) {
    if (assetType === "qrcode") return;
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "后端暂无素材。";
    grid.append(empty);
    return;
  }

  assets.forEach((asset) => {
    const card = assetChoiceTemplate.content.firstElementChild.cloneNode(true);
    card.classList.toggle("selected", state.selectedAssets[assetType]?.id === asset.id);
    const thumb = card.querySelector(".choice-thumb");
    const img = document.createElement("img");
    img.src = resolveReturnedUrl(asset.public_url);
    img.alt = asset.name || asset.file_name || `${assetType}素材`;
    img.loading = "lazy";
    img.onerror = () => {
      thumb.textContent = "图片不可访问";
    };
    thumb.innerHTML = "";
    thumb.append(img);
    card.querySelector("strong").textContent = asset.name || asset.file_name || asset.id;
    card.querySelector("small").textContent = asset.asset_type || assetType;
    card.addEventListener("click", () => {
      state.selectedAssets[assetType] = asset;
      renderAssetChoices();
      renderSummary();
    });
    grid.append(card);
  });
}

async function uploadProductFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;

  const uploadMode = state.sourceMode === "scene_image" ? "scene_image" : "upload_product";
  const assetType = uploadMode === "scene_image" ? "scene_image" : "product_image";
  if (state.sourceMode === "system_product") {
    setSourceMode("upload_product");
  }

  const selectedFiles = assetType === "scene_image" ? files.slice(0, 1) : files.slice(0, Math.max(0, 5 - state.productAssets.length));
  if (!selectedFiles.length) {
    showMessage(els.uploadMessage, "本次任务最多支持 5 张产品图，请先移出不需要的图片。", "warning");
    return;
  }
  if (assetType === "scene_image" && files.length > 1) {
    showMessage(els.uploadMessage, "整张场景图模式只使用 1 张图片，已取第一张。", "warning");
  } else if (assetType === "product_image" && selectedFiles.length < files.length) {
    showMessage(els.uploadMessage, `MVP 单次最多 5 张产品图，本次将上传前 ${selectedFiles.length} 张。`, "warning");
  } else {
    showMessage(els.uploadMessage, `正在上传 ${selectedFiles.length} 张${assetLabel(assetType)}...`);
  }

  const invalidFile = selectedFiles.find((file) => !file.type.startsWith("image/"));
  if (invalidFile) {
    showMessage(els.uploadMessage, `${invalidFile.name} 不是支持的图片类型。`, "warning");
    return;
  }
  const oversizedFile = selectedFiles.find((file) => file.size > 10 * 1024 * 1024);
  if (oversizedFile) {
    showMessage(els.uploadMessage, `${oversizedFile.name} 超过 10MB，请重新选择。`, "warning");
    return;
  }

  try {
    const uploadedAssets = [];
    for (const [index, file] of selectedFiles.entries()) {
      showMessage(els.uploadMessage, `正在上传 ${index + 1} / ${selectedFiles.length}：${file.name}`);
      const asset = await uploadAsset(file, assetType, { product_id: state.selectedProductId });
      requiredFields(`upload ${assetType} asset`, asset, ["id", "asset_type", "public_url"]);
      uploadedAssets.push(asset);
    }
    if (assetType === "scene_image") {
      state.sceneAsset = uploadedAssets[0];
      state.productAssets = [];
    } else {
      state.productAssets = [...state.productAssets, ...uploadedAssets].slice(0, 5);
      state.sceneAsset = null;
    }
    renderProductAssets();
    renderSummary();
    showMessage(els.uploadMessage, `${uploadedAssets.length} 张${assetLabel(assetType)}上传完成。`, "success");
  } catch (error) {
    showMessage(els.uploadMessage, `${error.code}: ${error.message}`, "error");
  }
}

async function uploadSystemAsset(file, assetType) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    showMessage(els.uploadMessage, `${file.name} 不是支持的图片类型。`, "warning");
    return;
  }
  try {
    showMessage(els.uploadMessage, `正在上传${assetLabel(assetType)}...`);
    const asset = await uploadAsset(file, assetType);
    state.systemAssets[assetType].unshift(asset);
    state.selectedAssets[assetType] = asset;
    showMessage(els.uploadMessage, `${assetLabel(assetType)}上传完成。`, "success");
    renderAssetChoices();
    renderSummary();
  } catch (error) {
    showMessage(els.uploadMessage, `${error.code}: ${error.message}`, "error");
  }
}

async function uploadAsset(file, assetType, extra = {}) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("asset_type", assetType);
  formData.append("name", file.name);
  formData.append("tags", JSON.stringify(["frontend_mvp"]));
  if (extra.product_id) {
    formData.append("product_id", extra.product_id);
  }
  return request("/assets", {
    method: "POST",
    body: formData,
  });
}

function assetLabel(assetType) {
  const map = {
    logo: "Logo",
    qrcode: "二维码",
    bottom_bar: "底部宣传条",
    scene_image: "整张场景图",
    product_image: "产品图",
  };
  return map[assetType] || assetType;
}

function renderProductAssets() {
  els.productAssetGrid.innerHTML = "";
  const assets = state.sourceMode === "scene_image" ? (state.sceneAsset ? [state.sceneAsset] : []) : state.productAssets;
  if (!assets.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = state.sourceMode === "scene_image" ? "还没有选择整张场景图。" : "还没有选择产品图。";
    els.productAssetGrid.append(empty);
    renderPreviewSelects();
    return;
  }
  assets.forEach((asset, index) => {
    const card = document.createElement("article");
    card.className = `product-card ${index === 0 ? "recommended" : ""}`;
    const img = document.createElement("img");
    img.src = resolveReturnedUrl(asset.public_url);
    img.alt = asset.name || asset.file_name || `${assetLabel(asset.asset_type)} ${index + 1}`;
    img.loading = "lazy";
    img.onerror = () => {
      img.alt = `${assetLabel(asset.asset_type)}不可访问`;
    };
    const body = document.createElement("div");
    body.className = "product-card-body";
    const title = document.createElement("strong");
    title.textContent = `${asset.name || asset.file_name || asset.id} · 当前${assetLabel(asset.asset_type)}`;
    const meta = document.createElement("span");
    if (asset.width && asset.height) {
      meta.textContent = `${assetLabel(asset.asset_type)}尺寸：${asset.width}x${asset.height}`;
    } else {
      meta.textContent = `等待后端返回${assetLabel(asset.asset_type)}尺寸`;
    }
    const actions = document.createElement("div");
    actions.className = "product-card-actions";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost-button small";
    removeButton.textContent = "移出本次任务";
    removeButton.addEventListener("click", () => {
      if (state.sourceMode === "scene_image") {
        state.sceneAsset = null;
      } else {
        state.productAssets.splice(index, 1);
      }
      renderProductAssets();
      renderSummary();
    });
    actions.append(removeButton);
    body.append(title, meta, actions);
    card.append(img, body);
    els.productAssetGrid.append(card);
  });
  renderPreviewSelects();
}

async function loadProducts() {
  els.reloadProductsBtn.disabled = true;
  try {
    const data = await request("/products");
    if (!Array.isArray(data?.items)) {
      addProtocolIssue("GET /products 缺少 data.items 数组。");
      state.products = [];
    } else {
      state.products = data.items;
      state.products.forEach((product) => requiredFields("product", product, ["id", "name"]));
      if (!state.selectedProductId && state.products[0]) {
        state.selectedProductId = state.products[0].id;
      }
    }
  } catch (error) {
    showMessage(els.taskMessage, `${error.code}: ${error.message}`, "error");
  } finally {
    els.reloadProductsBtn.disabled = false;
    renderProducts();
    await loadSystemProductAssets();
    renderSummary();
  }
}

function renderProducts() {
  renderProductSelect(els.productSelect);
  renderProductSelect(els.assetProductSelect);
  syncProductSelects();
}

function renderProductSelect(select) {
  select.innerHTML = "";
  if (!state.products.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "后端暂无产品";
    select.append(option);
    state.selectedProductId = "";
    return;
  }
  if (!state.products.some((product) => product.id === state.selectedProductId)) {
    state.selectedProductId = state.products[0].id;
  }
  state.products.forEach((product) => {
    const option = document.createElement("option");
    option.value = product.id;
    option.textContent = `${product.name}${product.model ? `（${product.model}）` : ""}`;
    select.append(option);
  });
  select.value = state.selectedProductId || state.products[0].id;
  state.selectedProductId = select.value;
}

function syncProductSelects() {
  if (els.productSelect.value !== state.selectedProductId) {
    els.productSelect.value = state.selectedProductId;
  }
  if (els.assetProductSelect.value !== state.selectedProductId) {
    els.assetProductSelect.value = state.selectedProductId;
  }
}

async function loadSystemProductAssets() {
  if (!state.selectedProductId) {
    state.systemProductAssets = [];
    renderSystemProductAssets();
    return;
  }
  els.reloadProductAssetsBtn.disabled = true;
  const query = new URLSearchParams({
    asset_type: "product_image",
    source: "product_material",
    product_id: state.selectedProductId,
  });
  try {
    const data = await request(`/assets?${query.toString()}`);
    if (!Array.isArray(data?.items)) {
      addProtocolIssue("GET /assets?asset_type=product_image&source=product_material&product_id=... 缺少 data.items 数组。");
      state.systemProductAssets = [];
    } else {
      state.systemProductAssets = data.items;
      state.systemProductAssets.forEach((asset) => requiredFields("system product asset", asset, ["id", "asset_type", "public_url"]));
      if (state.sourceMode === "system_product" && !state.productAssets.length && state.systemProductAssets[0]) {
        selectSystemProductAsset(state.systemProductAssets[0], { silent: true });
      }
    }
  } catch (error) {
    state.systemProductAssets = [];
    showMessage(els.uploadMessage, `${error.code}: ${error.message}`, "error");
  } finally {
    els.reloadProductAssetsBtn.disabled = false;
    renderSystemProductAssets();
    renderProductAssets();
    renderSummary();
  }
}

function renderSystemProductAssets() {
  els.systemProductAssetsGrid.innerHTML = "";
  if (!state.systemProductAssets.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "该产品暂未索引到可用产品图，可切换为上传产品图或上传整张场景图。";
    els.systemProductAssetsGrid.append(empty);
    return;
  }

  state.systemProductAssets.forEach((asset, index) => {
    const card = assetChoiceTemplate.content.firstElementChild.cloneNode(true);
    card.classList.toggle("selected", state.productAssets.some((item) => item.id === asset.id));
    const thumb = card.querySelector(".choice-thumb");
    const img = document.createElement("img");
    img.src = resolveReturnedUrl(asset.public_url);
    img.alt = asset.name || asset.file_name || `系统产品图 ${index + 1}`;
    img.loading = "lazy";
    img.onerror = () => {
      thumb.textContent = "图片不可访问";
    };
    thumb.innerHTML = "";
    thumb.append(img);
    card.querySelector("strong").textContent = asset.name || asset.file_name || asset.id;
    card.querySelector("small").textContent = asset.width && asset.height ? `${asset.width}x${asset.height}` : "系统素材库";
    card.addEventListener("click", () => selectSystemProductAsset(asset));
    els.systemProductAssetsGrid.append(card);
  });
}

function selectSystemProductAsset(asset, options = {}) {
  state.sourceMode = "system_product";
  state.productAssets = [asset];
  state.sceneAsset = null;
  if (!options.silent) {
    showMessage(els.uploadMessage, "已选择系统产品图。", "success");
  }
  renderSourceMode();
  renderSystemProductAssets();
  renderProductAssets();
  renderSummary();
}

function setSourceMode(mode) {
  const previousMode = state.sourceMode;
  state.sourceMode = mode;
  if (mode === "scene_image") {
    state.productAssets = [];
  } else if (mode === "upload_product") {
    state.sceneAsset = null;
    if (previousMode !== "upload_product") {
      state.productAssets = [];
    }
  } else if (mode === "system_product") {
    state.sceneAsset = null;
    const selectedFromSystemLibrary = state.productAssets.some((asset) =>
      state.systemProductAssets.some((systemAsset) => systemAsset.id === asset.id),
    );
    if (!selectedFromSystemLibrary) {
      state.productAssets = state.systemProductAssets[0] ? [state.systemProductAssets[0]] : [];
    }
  }
  renderSourceMode();
  renderSystemProductAssets();
  renderProductAssets();
  renderSummary();
}

function renderSourceMode() {
  els.sourceModeInputs.forEach((input) => {
    input.checked = input.value === state.sourceMode;
  });
  els.systemProductSection.hidden = state.sourceMode !== "system_product";
  const isScene = state.sourceMode === "scene_image";
  const isSystem = state.sourceMode === "system_product";
  els.dropZone.classList.toggle("muted", isSystem);
  els.uploadZoneTitle.textContent = isScene ? "拖拽或选择整张场景图" : "拖拽或选择产品图";
  els.uploadZoneHint.textContent = isScene
    ? "用于已经完成构图的整张场景图，单张限制 10MB，支持 JPG、PNG、WEBP。"
    : isSystem
      ? "当前优先使用素材库产品图；也可以直接上传产品图切换到上传模式。"
      : "产品图可以是透明 PNG，也可以是清爽 JPG/WEBP，单张限制 10MB。";
}

function currentCopyMode() {
  return els.copyModeInputs.find((input) => input.checked)?.value || "ai";
}

function renderCopyMode() {
  const isManual = currentCopyMode() === "manual";
  els.titlePreferenceLabel.textContent = isManual ? "主标题（必填）" : "主标题偏好";
  els.subtitlePreferenceLabel.textContent = isManual ? "副标题（必填）" : "副标题偏好";
  els.titlePreferenceInput.placeholder = isManual ? "必填，不超过18字" : "可留空，不超过18字";
  els.subtitlePreferenceInput.placeholder = isManual ? "必填，不超过42字" : "可留空，不超过42字";
  els.titlePreferenceInput.required = isManual;
  els.subtitlePreferenceInput.required = isManual;
}

function buildCustomRequirement() {
  const copyModeLabel = currentCopyMode() === "manual" ? "手动填写文案" : "AI生成文案";
  const pieces = [
    `文案模式：${copyModeLabel}`,
    `文案方向：${els.copyDirectionSelect.value}`,
    `风格偏好：${els.styleSelect.value}`,
  ];
  if (els.customRequirementInput.value.trim()) {
    pieces.push(`补充需求：${els.customRequirementInput.value.trim()}`);
  }
  if (!state.selectedAssets.bottom_bar) {
    pieces.push("缺少底部条时请使用默认底条。");
  }
  return pieces.join("；");
}

async function createPosterTask() {
  if (!canCreateTask()) {
    showMessage(els.taskMessage, "请先完成节点、产品、产品图或场景图、Logo和底部条选择。", "warning");
    return;
  }
  const copyMode = currentCopyMode();
  const titlePreference = els.titlePreferenceInput.value.trim();
  const subtitlePreference = els.subtitlePreferenceInput.value.trim();
  if (copyMode === "manual" && (!titlePreference || !subtitlePreference)) {
    showMessage(els.taskMessage, "手动填写文案时，主标题和副标题都不能为空。", "warning");
    (!titlePreference ? els.titlePreferenceInput : els.subtitlePreferenceInput).focus();
    return;
  }
  const assetPayload =
    state.sourceMode === "scene_image"
      ? { scene_asset_id: state.sceneAsset.id, product_asset_ids: [] }
      : { scene_asset_id: null, product_asset_ids: state.productAssets.map((asset) => asset.id) };

  const payload = {
    node_id: state.selectedNode.id,
    product_id: state.selectedProductId,
    template_id: els.templateInput.value.trim() || "template_v1_vertical_standard",
    ...assetPayload,
    logo_asset_id: state.selectedAssets.logo.id,
    qrcode_asset_id: state.selectedAssets.qrcode?.id || null,
    bottom_bar_asset_id: state.selectedAssets.bottom_bar.id,
    contact_text: els.contactInput.value.trim(),
    scene_prompt: els.customRequirementInput.value.trim(),
    custom_requirement: buildCustomRequirement(),
    copy_preference: {
      mode: copyMode,
      title: titlePreference,
      subtitle: subtitlePreference,
    },
  };

  els.createTaskBtn.disabled = true;
  showMessage(els.taskMessage, "正在创建海报生成任务...");
  updatePollingPanel({ status: "pending", progress: 0, current_step: "任务创建中" });

  try {
    const data = await request("/poster-tasks", {
      method: "POST",
      body: payload,
    });
    requiredFields("create poster task", data, ["task_id", "status"]);
    if (data.status && !TASK_STATUSES.has(data.status)) {
      addProtocolIssue(`POST /poster-tasks 返回未知任务状态 \`${data.status}\`。`);
    }
    state.activeTask = data;
    state.poster = null;
    state.copyDirty = false;
    state.rerendering = false;
    state.compliance = { status: "unknown", issues: [], risk_level: "", suggested_title: "", suggested_subtitle: "" };
    renderSummary();
    showMessage(els.taskMessage, `任务已创建：${data.task_id}`, "success");
    startPolling(data.task_id);
  } catch (error) {
    showMessage(els.taskMessage, `${error.code}: ${error.message}`, "error");
    els.createTaskBtn.disabled = false;
    renderSummary();
  }
}

function updatePollingPanel(task) {
  els.pollingPanel.hidden = false;
  const progress = clamp(Number(task.progress || 0), 0, 100);
  els.progressBar.style.width = `${progress}%`;
  els.pollingStatus.textContent = `${statusLabel(task.status)} · ${progress}%`;
  els.pollingStep.textContent = taskStepLabel(task.current_step);
}

function startPolling(taskId) {
  stopPolling();
  const startedAt = Date.now();
  pollTask(taskId, startedAt);
}

async function pollTask(taskId, startedAt) {
  if (Date.now() - startedAt > AI_IMAGE_MAX_POLL_MS) {
    stopPolling();
    updatePollingPanel({
      status: "processing",
      progress: state.activeTask?.progress || 0,
      current_step: "轮询已暂停，任务可能仍在后端处理中",
    });
    showMessage(els.taskMessage, "生成已超过4分钟，前端已暂停轮询。可稍后重新创建任务，或检查模型服务与后端日志。", "warning");
    els.createTaskBtn.disabled = false;
    renderSummary();
    return;
  }
  try {
    const data = await request(`/poster-tasks/${encodeURIComponent(taskId)}`);
    handleTaskUpdate(data, startedAt);
  } catch (error) {
    if (error.code === "TASK_NOT_READY") {
      updatePollingPanel({
        status: "processing",
        progress: state.activeTask?.progress || 0,
        current_step: "任务未完成，继续轮询",
      });
      scheduleNextPoll(taskId, startedAt);
      return;
    }
    showMessage(els.taskMessage, `${error.code}: ${error.message}`, "error");
    stopPolling();
    els.createTaskBtn.disabled = false;
    renderBackendResult();
    renderSummary();
  }
}

function scheduleNextPoll(taskId, startedAt) {
  const elapsed = Date.now() - startedAt;
  const delay = elapsed > AI_IMAGE_WARNING_MS ? POLL_INTERVAL_SLOW_MS : POLL_INTERVAL_MS;
  state.pollingTimer = window.setTimeout(() => pollTask(taskId, startedAt), delay);
}

function stopPolling() {
  if (state.pollingTimer) {
    window.clearTimeout(state.pollingTimer);
    state.pollingTimer = null;
  }
}

function handleTaskUpdate(data, startedAt) {
  requiredFields("poster task", data, ["task_id", "status", "progress", "current_step"]);
  if (data.status && !TASK_STATUSES.has(data.status)) {
    addProtocolIssue(`GET /poster-tasks/{task_id} 返回未知任务状态 \`${data.status}\`。`);
  }

  state.activeTask = data;
  updatePollingPanel(data);
  renderBackendResult();
  renderSummary();

  if (data.error) {
    renderTaskError(data.error);
    if (data.error.code === "COMPLIANCE_BLOCKED") {
      applyComplianceBlocked(data.error);
      stopPolling();
      setScreen("preview");
      renderPoster();
      return;
    }
  }

  if (data.status === "success") {
    stopPolling();
    state.poster = data.poster;
    state.copyDirty = false;
    state.rerendering = false;
    if (!state.poster) {
      addProtocolIssue("任务成功响应缺少 poster 对象。");
    } else {
      requiredFields("poster", state.poster, ["id", "jpg_url", "width", "height"]);
    }
    showMessage(els.taskMessage, "海报生成完成。", "success");
    setScreen("preview");
    renderPoster();
    const backendCopy = seedPreviewCopyFromBackend(data);
    if (backendCopy.title || backendCopy.subtitle) {
      runComplianceCheck();
    } else {
      state.compliance = {
        status: "failed",
        issues: ["后端未返回可用于合规检查的文案，下载已禁用。"],
        risk_level: "unknown",
        suggested_title: "",
        suggested_subtitle: "",
      };
      renderCompliance();
    }
    els.createTaskBtn.disabled = false;
    renderSummary();
    return;
  }

  if (data.status === "failed") {
    stopPolling();
    showMessage(els.taskMessage, data.error?.message || "海报生成失败，请查看后端错误信息。", "error");
    if (data.error?.code === "COMPLIANCE_BLOCKED") {
      applyComplianceBlocked(data.error);
    }
    setScreen("preview");
    renderPoster();
    els.createTaskBtn.disabled = false;
    renderSummary();
    return;
  }

  if (Date.now() - startedAt > AI_IMAGE_WARNING_MS) {
    showMessage(els.taskMessage, "生图时长较长，请稍后。AI 正在处理海报画面，完成后会自动进入预览。", "warning");
  }
  scheduleNextPoll(data.task_id, startedAt);
}

function renderTaskError(error) {
  const details = formatTaskErrorDetails(error.details);
  showMessage(els.taskMessage, `${error.code || "TASK_ERROR"}: ${error.message || "任务异常"}${details}`, "error");
}

function applyComplianceBlocked(error) {
  const issues = normalizeIssues(error.details?.issues || error.details?.suggestions || [error.message || "合规不通过"]);
  state.compliance = {
    status: "blocked",
    issues,
    risk_level: error.details?.risk_level || "high",
    suggested_title: error.details?.suggested_title || "",
    suggested_subtitle: error.details?.suggested_subtitle || "",
  };
  renderCompliance();
}

function renderPoster() {
  if (state.poster?.jpg_url) {
    els.posterImage.src = resolveReturnedUrl(state.poster.thumbnail_url || state.poster.jpg_url);
    els.posterImage.hidden = false;
    els.posterEmpty.hidden = true;
  } else {
    els.posterImage.hidden = true;
    els.posterEmpty.hidden = false;
    els.posterEmpty.textContent = state.activeTask?.status === "failed" ? "任务失败，暂无可预览海报" : "任务完成后显示海报";
  }
  renderPreviewSelects();
  renderCompliance();
  renderBackendResult();
  renderSummary();
}

function seedPreviewCopyFromBackend(taskData) {
  const copy = extractBackendCopy(taskData);
  els.previewTitleInput.value = copy.title || "";
  els.previewSubtitleInput.value = copy.subtitle || "";
  state.copyDirty = false;
  if (!copy.title && !copy.subtitle) {
    addProtocolIssue("任务成功响应未返回显式文案字段 copy/generated_copy/poster.copy，前端不使用海报文件名代替文案。");
  }
  return copy;
}

function extractBackendCopy(taskData = state.activeTask) {
  const source = taskData?.copy || taskData?.generated_copy || taskData?.poster?.copy || {};
  return {
    title: source.title || source.main_title || "",
    subtitle: source.subtitle || source.sub_title || "",
  };
}

function renderBackendResult() {
  els.resultTaskStatus.textContent = state.activeTask
    ? `${statusLabel(state.activeTask.status)}${state.activeTask.progress !== undefined ? ` · ${state.activeTask.progress}%` : ""}`
    : "未创建";

  const copy = extractBackendCopy();
  els.resultCopy.textContent = copy.title || copy.subtitle
    ? `主标题：${copy.title || "未返回"}；副标题：${copy.subtitle || "未返回"}`
    : ["pending", "processing"].includes(state.activeTask?.status)
      ? "AI 文案生成中"
      : "后端未返回显式文案字段";
  els.resultPosterUrl.textContent = state.poster?.jpg_url || state.activeTask?.poster?.jpg_url || "等待后端返回";
  els.resultFusion.textContent = formatFusionResult(state.activeTask?.fusion);

  const taskError = state.activeTask?.error;
  if (!taskError) {
    els.resultError.textContent = "无";
  } else {
    els.resultError.textContent = `${taskError.code || "TASK_ERROR"}: ${taskError.message || "任务异常"}${formatTaskErrorDetails(taskError.details)}`;
  }
}

function formatFusionResult(fusion) {
  if (!fusion) {
    if (state.activeTask?.status === "failed" && state.activeTask?.error?.code === "AI_IMAGE_FUSION_FAILED") {
      return "图片模型未成功调用，未生成本地堆叠图";
    }
    return ["pending", "processing"].includes(state.activeTask?.status) ? "AI 图片融合中" : "等待后端返回";
  }

  const fallback = fusion.fallback || {};
  const pieces = [
    `provider=${fusion.provider || "unknown"}`,
    `mode=${fusion.mode || "unknown"}`,
    `model=${fusion.model || "unknown"}`,
    `fallback.used=${fallback.used ? "true" : "false"}`,
  ];
  if (fusion.rerender_reused_scene) {
    pieces.push("rerender.reused_scene=true");
  }
  if (fallback.reason) {
    pieces.push(`fallback.reason=${fallback.reason}`);
  }
  return pieces.join("；");
}

function formatTaskErrorDetails(details) {
  if (!details) return "";
  const pieces = [];
  if (details.reason) pieces.push(`reason=${details.reason}`);
  if (details.fusion_policy) pieces.push(`policy=${details.fusion_policy}`);
  if (details.local_fallback_used !== undefined) pieces.push(`local_fallback_used=${details.local_fallback_used}`);
  return pieces.length ? `（${pieces.join("；")}）` : "";
}

async function runComplianceCheck() {
  if (!state.selectedNode || !state.selectedProductId) {
    addProtocolIssue("合规检查需要 product_id 和 node_id，当前选择不完整。");
    return;
  }

  const revision = state.copyRevision;
  const payload = {
    title: els.previewTitleInput.value.trim(),
    subtitle: els.previewSubtitleInput.value.trim(),
    product_id: state.selectedProductId,
    node_id: state.selectedNode.id,
  };

  els.runComplianceBtn.disabled = true;
  state.compliance = { status: "checking", issues: [], risk_level: "", suggested_title: "", suggested_subtitle: "" };
  renderCompliance();

  try {
    const data = await request("/compliance/check", {
      method: "POST",
      body: payload,
    });
    if (revision !== state.copyRevision) return;
    requiredFields("compliance check", data, ["status", "risk_level", "issues", "suggested_title", "suggested_subtitle"]);
    state.compliance = {
      status: data.status || "unknown",
      risk_level: data.risk_level || "",
      issues: normalizeIssues(data.issues || []),
      suggested_title: data.suggested_title || "",
      suggested_subtitle: data.suggested_subtitle || "",
    };
    if (state.compliance.status === "passed" && state.copyDirty && state.poster?.jpg_url) {
      await rerenderPosterWithPreviewCopy(payload, revision);
    }
  } catch (error) {
    if (revision !== state.copyRevision) return;
    if (error.code === "COMPLIANCE_BLOCKED") {
      applyComplianceBlocked(error);
    } else {
      state.compliance = {
        status: "failed",
        issues: [`${error.code}: ${error.message}`],
        risk_level: "unknown",
        suggested_title: "",
        suggested_subtitle: "",
      };
    }
  } finally {
    if (revision !== state.copyRevision) return;
    els.runComplianceBtn.disabled = false;
    renderCompliance();
    renderSummary();
  }
}

async function rerenderPosterWithPreviewCopy(payload, revision) {
  if (!state.activeTask?.task_id || !state.poster?.jpg_url) return;

  state.rerendering = true;
  renderCompliance();
  renderSummary();

  try {
    const data = await request(`/poster-tasks/${encodeURIComponent(state.activeTask.task_id)}/rerender`, {
      method: "POST",
      body: {
        title: payload.title,
        subtitle: payload.subtitle,
      },
    });
    if (revision !== state.copyRevision) return;
    requiredFields("rerender poster task", data, ["task_id", "status", "poster", "copy"]);
    state.activeTask = data;
    if (data.poster) {
      requiredFields("rerender poster", data.poster, ["id", "jpg_url", "width", "height"]);
      state.poster = data.poster;
    }
    const copy = extractBackendCopy(data);
    els.previewTitleInput.value = copy.title || payload.title;
    els.previewSubtitleInput.value = copy.subtitle || payload.subtitle;
    state.compliance = complianceStateFromBackend(data.compliance || state.compliance);
    state.copyDirty = false;
    renderPoster();
  } catch (error) {
    if (revision !== state.copyRevision) return;
    state.compliance = {
      status: "failed",
      issues: [`重新合成 JPG 失败：${error.code || "RERENDER_FAILED"} ${error.message}`],
      risk_level: "unknown",
      suggested_title: "",
      suggested_subtitle: "",
    };
  } finally {
    if (revision === state.copyRevision) {
      state.rerendering = false;
      renderCompliance();
      renderBackendResult();
      renderSummary();
    }
  }
}

function complianceStateFromBackend(data) {
  return {
    status: data?.status || "unknown",
    risk_level: data?.risk_level || "",
    issues: normalizeIssues(data?.issues || []),
    suggested_title: data?.suggested_title || "",
    suggested_subtitle: data?.suggested_subtitle || "",
  };
}

function normalizeIssues(issues) {
  if (!Array.isArray(issues)) return [];
  return issues
    .map((issue) => {
      if (typeof issue === "string") return issue;
      if (issue?.message) return issue.message;
      if (issue?.suggestion) return issue.suggestion;
      try {
        return JSON.stringify(issue);
      } catch {
        return String(issue);
      }
    })
    .filter(Boolean);
}

function renderCompliance() {
  els.complianceCard.className = "compliance-card";
  const status = state.compliance.status;

  if (state.rerendering) {
    els.complianceCard.innerHTML = "<strong>正在更新海报</strong><p>文案已通过检查，正在重新合成 JPG，完成前下载保持禁用。</p>";
    return;
  }

  if (status === "checking") {
    els.complianceCard.innerHTML = "<strong>正在检查</strong><p>正在调用合规检查接口。</p>";
    return;
  }

  if (state.copyDirty && state.poster?.jpg_url && status === "unknown") {
    els.complianceCard.innerHTML = "<strong>文案已修改</strong><p>正在等待合规检查和 JPG 重渲染，完成前下载保持禁用。</p>";
    return;
  }

  if (status === "passed") {
    els.complianceCard.classList.add("passed");
    els.complianceCard.innerHTML = `<strong>合规通过</strong><p>风险等级：${escapeHtml(state.compliance.risk_level || "low")}</p>`;
    return;
  }

  if (status === "blocked" || status === "failed" || status !== "unknown") {
    els.complianceCard.classList.add("failed");
    const issues = state.compliance.issues.length ? state.compliance.issues : ["后端返回合规不通过，但未提供 issues。"];
    if (!state.compliance.issues.length) {
      addProtocolIssue("合规不通过时未返回 issues，前端无法展示具体修改建议。");
    }
    const suggestions = [
      state.compliance.suggested_title ? `建议主标题：${state.compliance.suggested_title}` : "",
      state.compliance.suggested_subtitle ? `建议副标题：${state.compliance.suggested_subtitle}` : "",
    ].filter(Boolean);
    els.complianceCard.innerHTML = `
      <strong>合规未通过，已禁用下载</strong>
      <p>风险等级：${escapeHtml(state.compliance.risk_level || "unknown")}</p>
      <ul>${issues.map((issue) => `<li>${escapeHtml(issue)}</li>`).join("")}</ul>
      ${suggestions.length ? `<p>${suggestions.map(escapeHtml).join("；")}</p>` : ""}
    `;
    return;
  }

  els.complianceCard.innerHTML = "<strong>等待检查</strong><p>生成成功或修改文案后会调用合规检查接口。</p>";
}

function renderPreviewSelects() {
  const sourceAssets = state.sourceMode === "scene_image" ? (state.sceneAsset ? [state.sceneAsset] : []) : state.productAssets;
  renderSelectFromAssets(els.previewProductAssetSelect, sourceAssets, state.sourceMode === "scene_image" ? "整张场景图" : "产品图");
  renderSelectFromAssets(els.previewQrcodeSelect, state.systemAssets.qrcode, "二维码", state.selectedAssets.qrcode?.id);
  renderSelectFromAssets(els.previewBottomBarSelect, state.systemAssets.bottom_bar, "底部条", state.selectedAssets.bottom_bar?.id);
}

function renderSelectFromAssets(select, assets, label, selectedId) {
  select.innerHTML = "";
  if (!assets.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = `暂无${label}`;
    select.append(option);
    return;
  }
  assets.forEach((asset, index) => {
    const option = document.createElement("option");
    option.value = asset.id;
    option.textContent = asset.name || asset.file_name || `${label}${index + 1}`;
    select.append(option);
  });
  select.value = selectedId || assets[0]?.id || "";
}

function scheduleComplianceCheck() {
  if (!state.poster?.jpg_url) return;
  window.clearTimeout(state.complianceTimer);
  state.complianceTimer = window.setTimeout(runComplianceCheck, 500);
}

function clearComplianceTimer() {
  if (state.complianceTimer) {
    window.clearTimeout(state.complianceTimer);
    state.complianceTimer = null;
  }
}

function markPreviewCopyDirty() {
  if (!state.poster?.jpg_url) return;
  state.copyDirty = true;
  state.copyRevision += 1;
  state.compliance = { status: "unknown", issues: [], risk_level: "", suggested_title: "", suggested_subtitle: "" };
  renderCompliance();
  renderSummary();
  scheduleComplianceCheck();
}

async function downloadPoster() {
  if (!canDownload()) return;
  els.downloadBtn.disabled = true;
  try {
    const response = await fetch(resolveReturnedUrl(state.poster.jpg_url), {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `${state.poster.title || state.poster.id || "poster"}.jpg`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    showMessage(els.taskMessage, `下载失败：${error.message}`, "error");
  } finally {
    renderSummary();
  }
}

function newTask() {
  stopPolling();
  clearComplianceTimer();
  state.activeTask = null;
  state.poster = null;
  state.copyDirty = false;
  state.rerendering = false;
  state.compliance = { status: "unknown", issues: [], risk_level: "", suggested_title: "", suggested_subtitle: "" };
  els.taskMessage.hidden = true;
  els.pollingPanel.hidden = true;
  els.progressBar.style.width = "0";
  els.posterImage.removeAttribute("src");
  els.posterImage.hidden = true;
  els.posterEmpty.hidden = false;
  els.previewTitleInput.value = "";
  els.previewSubtitleInput.value = "";
  setScreen("nodes");
  renderBackendResult();
  renderSummary();
  renderCompliance();
}

function resetRuntimeTaskState() {
  stopPolling();
  clearComplianceTimer();
  state.activeTask = null;
  state.poster = null;
  state.copyDirty = false;
  state.rerendering = false;
  state.compliance = { status: "unknown", issues: [], risk_level: "", suggested_title: "", suggested_subtitle: "" };
  els.pollingPanel.hidden = true;
  els.progressBar.style.width = "0";
  els.posterImage.removeAttribute("src");
  els.posterImage.hidden = true;
  els.posterEmpty.hidden = false;
  renderBackendResult();
  renderSummary();
  renderCompliance();
}

function clamp(value, min, max) {
  return Math.min(Math.max(Number.isFinite(value) ? value : min, min), max);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bindEvents() {
  els.apiBaseInput.value = state.apiBase;
  if (els.apiTokenInput) {
    els.apiTokenInput.value = state.apiToken;
    els.apiTokenInput.addEventListener("change", () => {
      state.apiToken = els.apiTokenInput.value.trim();
      if (state.apiToken) {
        localStorage.setItem("festivalPoster.apiToken", state.apiToken);
      } else {
        localStorage.removeItem("festivalPoster.apiToken");
      }
      resetRuntimeTaskState();
      showMessage(els.taskMessage, "访问令牌已更新，当前任务状态已重置，请重新创建或查询任务。", "warning");
    });
  }
  els.apiBaseInput.addEventListener("change", () => {
    state.apiBase = normalizeApiBase(els.apiBaseInput.value) || API_BASE_FALLBACK;
    els.apiBaseInput.value = state.apiBase;
    localStorage.setItem("festivalPoster.apiBase", state.apiBase);
    resetRuntimeTaskState();
    loadNodes();
    loadSystemAssets();
    loadProducts();
  });
  els.resetApiBaseBtn.addEventListener("click", () => {
    localStorage.removeItem("festivalPoster.apiBase");
    state.apiBase = API_BASE_FALLBACK;
    els.apiBaseInput.value = state.apiBase;
    state.protocolIssues = [];
    resetRuntimeTaskState();
    renderProtocolIssues();
    loadNodes();
    loadSystemAssets();
    loadProducts();
  });

  els.reloadNodesBtn.addEventListener("click", loadNodes);
  els.reloadAssetsBtn.addEventListener("click", loadSystemAssets);
  els.reloadProductsBtn.addEventListener("click", loadProducts);
  els.reloadProductAssetsBtn.addEventListener("click", loadSystemProductAssets);
  els.useCustomNodeBtn.addEventListener("click", useCustomNode);

  els.goAssetsBtn.addEventListener("click", () => setScreen("assets"));
  els.goConfigBtn.addEventListener("click", () => setScreen("config"));
  els.stepper.querySelectorAll("[data-step-indicator]").forEach((item) => {
    item.setAttribute("role", "button");
    item.setAttribute("tabindex", "0");
    item.setAttribute("aria-label", `前往${item.querySelector("strong")?.textContent || "步骤"}`);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        item.click();
      }
    });
    item.addEventListener("click", () => {
      const target = item.dataset.stepIndicator;
      if (target === "nodes") setScreen("nodes");
      if (target === "assets" && state.selectedNode && !state.selectedNode.isCustom) setScreen("assets");
      if (target === "config" && canGoConfig()) setScreen("config");
      if (target === "preview" && (state.poster || state.activeTask?.status === "failed")) setScreen("preview");
    });
  });
  document.querySelectorAll("[data-back]").forEach((button) => {
    button.addEventListener("click", () => setScreen(button.dataset.back));
  });
  const historyButton = document.querySelector("[data-open-history]");
  historyButton?.addEventListener("click", () => {
    if (!historyButton.disabled) {
      setScreen("history");
    }
  });

  els.pickProductFilesBtn.addEventListener("click", () => els.productFileInput.click());
  els.productFileInput.addEventListener("change", () => uploadProductFiles(els.productFileInput.files));
  els.dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.dropZone.classList.add("dragging");
  });
  els.dropZone.addEventListener("dragleave", () => els.dropZone.classList.remove("dragging"));
  els.dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("dragging");
    uploadProductFiles(event.dataTransfer.files);
  });

  document.querySelectorAll("[data-upload-trigger]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector(`[data-upload-input="${button.dataset.uploadTrigger}"]`).click();
    });
  });
  document.querySelectorAll("[data-upload-input]").forEach((input) => {
    input.addEventListener("change", () => uploadSystemAsset(input.files[0], input.dataset.uploadInput));
  });

  els.sourceModeInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) {
        setSourceMode(input.value);
      }
    });
  });
  els.copyModeInputs.forEach((input) => {
    input.addEventListener("change", renderCopyMode);
  });
  els.assetProductSelect.addEventListener("change", () => {
    state.selectedProductId = els.assetProductSelect.value;
    syncProductSelects();
    state.productAssets = [];
    state.sceneAsset = null;
    loadSystemProductAssets();
    renderProductAssets();
    renderSummary();
  });
  els.productSelect.addEventListener("change", () => {
    state.selectedProductId = els.productSelect.value;
    syncProductSelects();
    state.productAssets = [];
    state.sceneAsset = null;
    loadSystemProductAssets();
    renderProductAssets();
    renderSummary();
  });
  els.createTaskBtn.addEventListener("click", createPosterTask);
  els.runComplianceBtn.addEventListener("click", runComplianceCheck);
  els.previewTitleInput.addEventListener("input", markPreviewCopyDirty);
  els.previewSubtitleInput.addEventListener("input", markPreviewCopyDirty);
  els.downloadBtn.addEventListener("click", downloadPoster);
  els.newTaskBtn.addEventListener("click", newTask);

  [els.previewProductAssetSelect, els.logoPositionSelect, els.previewQrcodeSelect, els.previewBottomBarSelect].forEach((control) => {
    control.addEventListener("change", () => {
      addProtocolIssue("预览页场景图、Logo位置、二维码、底部条切换需要重新合成接口，但当前 MVP 仅支持固定模板生成结果展示。");
    });
  });
}

async function init() {
  bindEvents();
  renderSourceMode();
  renderCopyMode();
  renderProductAssets();
  renderSummary();
  renderCompliance();
  await Promise.all([loadNodes(), loadSystemAssets(), loadProducts()]);
}

init();
