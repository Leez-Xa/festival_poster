const TASK_STATUSES = new Set(["pending", "processing", "success", "failed"]);
const API_BASE_FALLBACK = (() => {
  if (location.origin && /:8000$/.test(location.origin)) {
    return `${location.origin}/api/v1`;
  }
  return "http://127.0.0.1:8000/api/v1";
})();

const state = {
  currentScreen: "nodes",
  apiBase: localStorage.getItem("festivalPoster.apiBase") || API_BASE_FALLBACK,
  nodes: [],
  selectedNode: null,
  customNodeDraft: null,
  products: [],
  selectedProductId: "",
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
  activeTask: null,
  poster: null,
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
  els.summaryProductAssets.textContent = `${state.productAssets.length} / 1`;

  const pieces = [];
  pieces.push(state.selectedAssets.logo ? "Logo已选" : "Logo未选");
  pieces.push(state.selectedAssets.qrcode ? "二维码已选" : "二维码未选");
  pieces.push(state.selectedAssets.bottom_bar ? "底部条已选" : "底部条未选");
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

function canGoConfig() {
  return (
    state.productAssets.length === 1 &&
    Boolean(state.selectedAssets.logo) &&
    Boolean(state.selectedAssets.qrcode) &&
    Boolean(state.selectedAssets.bottom_bar)
  );
}

function canCreateTask() {
  return (
    Boolean(state.selectedNode) &&
    !state.selectedNode?.isCustom &&
    Boolean(state.selectedProductId) &&
    canGoConfig()
  );
}

function canDownload() {
  return Boolean(state.poster?.jpg_url) && state.compliance.status === "passed";
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
  showMessage(els.uploadMessage, "正在读取系统 Logo、二维码和底部宣传条...");
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
    selectFirstMissing("qrcode");
    selectFirstMissing("bottom_bar");

    const missing = [];
    if (!logos.length) missing.push("Logo");
    if (!qrcodes.length) missing.push("二维码");
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
  if (!assets.length) {
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

  const file = files[0];
  if (files.length > 1) {
    showMessage(els.uploadMessage, "MVP 当前仅使用 1 张透明产品 PNG，已取第一张。", "warning");
  } else {
    showMessage(els.uploadMessage, "正在上传透明产品 PNG...");
  }

  if (!file.type.startsWith("image/")) {
    showMessage(els.uploadMessage, `${file.name} 不是支持的图片类型。`, "warning");
    return;
  }
  const isPng = file.type === "image/png" || file.name.toLowerCase().endsWith(".png");
  if (!isPng) {
    showMessage(els.uploadMessage, `${file.name} 不是透明 PNG，请上传已经抠好的透明产品 PNG。`, "warning");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showMessage(els.uploadMessage, `${file.name} 超过 10MB，请重新选择。`, "warning");
    return;
  }

  try {
    const asset = await uploadAsset(file, "product_image");
    requiredFields("upload product asset", asset, ["id", "asset_type", "public_url"]);
    state.productAssets = [asset];
    renderProductAssets();
    renderSummary();
    showMessage(els.uploadMessage, "透明产品 PNG 上传完成。", "success");
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

async function uploadAsset(file, assetType) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("asset_type", assetType);
  formData.append("name", file.name);
  formData.append("tags", JSON.stringify(["frontend_mvp"]));
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
    scene_image: "场景参考图",
    product_image: "透明产品 PNG",
  };
  return map[assetType] || assetType;
}

function renderProductAssets() {
  els.productAssetGrid.innerHTML = "";
  state.productAssets.forEach((asset, index) => {
    const card = document.createElement("article");
    card.className = `product-card ${index === 0 ? "recommended" : ""}`;
    const img = document.createElement("img");
    img.src = resolveReturnedUrl(asset.public_url);
    img.alt = asset.name || asset.file_name || `融合场景图${index + 1}`;
    img.loading = "lazy";
    img.onerror = () => {
      img.alt = "融合场景图不可访问";
    };
    const body = document.createElement("div");
    body.className = "product-card-body";
    const title = document.createElement("strong");
    title.textContent = index === 0 ? `${asset.name || asset.file_name || asset.id} · 当前透明产品 PNG` : asset.name || asset.file_name || asset.id;
    const meta = document.createElement("span");
    if (asset.width && asset.height) {
      meta.textContent = `产品图尺寸：${asset.width}x${asset.height}`;
    } else {
      meta.textContent = "等待后端返回产品图尺寸";
    }
    const actions = document.createElement("div");
    actions.className = "product-card-actions";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost-button small";
    removeButton.textContent = "移出本次任务";
    removeButton.addEventListener("click", () => {
      state.productAssets.splice(index, 1);
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
    renderSummary();
  }
}

function renderProducts() {
  els.productSelect.innerHTML = "";
  if (!state.products.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "后端暂无产品";
    els.productSelect.append(option);
    state.selectedProductId = "";
    return;
  }
  state.products.forEach((product) => {
    const option = document.createElement("option");
    option.value = product.id;
    option.textContent = `${product.name}${product.model ? `（${product.model}）` : ""}`;
    els.productSelect.append(option);
  });
  els.productSelect.value = state.selectedProductId || state.products[0].id;
  state.selectedProductId = els.productSelect.value;
}

function buildCustomRequirement() {
  const pieces = [
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
    showMessage(els.taskMessage, "请先完成节点、产品、透明产品 PNG、Logo、二维码和底部条选择。", "warning");
    return;
  }

  const payload = {
    node_id: state.selectedNode.id,
    product_id: state.selectedProductId,
    template_id: els.templateInput.value.trim() || "template_v1_vertical_standard",
    scene_asset_id: null,
    product_asset_ids: state.productAssets.map((asset) => asset.id),
    logo_asset_id: state.selectedAssets.logo.id,
    qrcode_asset_id: state.selectedAssets.qrcode.id,
    bottom_bar_asset_id: state.selectedAssets.bottom_bar.id,
    contact_text: els.contactInput.value.trim(),
    scene_prompt: els.customRequirementInput.value.trim(),
    custom_requirement: buildCustomRequirement(),
    copy_preference: {
      title: els.titlePreferenceInput.value.trim(),
      subtitle: els.subtitlePreferenceInput.value.trim(),
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
  els.pollingStep.textContent = task.current_step || "";
}

function startPolling(taskId) {
  stopPolling();
  const startedAt = Date.now();
  pollTask(taskId, startedAt);
}

async function pollTask(taskId, startedAt) {
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
  state.pollingTimer = window.setTimeout(() => pollTask(taskId, startedAt), 2500);
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
      setScreen("preview");
      renderPoster();
    }
    els.createTaskBtn.disabled = false;
    renderSummary();
    return;
  }

  if (Date.now() - startedAt > 90000) {
    showMessage(els.taskMessage, "生成已超过90秒，任务仍在轮询中，可继续等待。", "warning");
  }
  scheduleNextPoll(data.task_id, startedAt);
}

function renderTaskError(error) {
  const details = error.details ? ` ${JSON.stringify(error.details)}` : "";
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
  els.resultCopy.textContent = copy.title || copy.subtitle ? `主标题：${copy.title || "未返回"}；副标题：${copy.subtitle || "未返回"}` : "后端未返回显式文案字段";
  els.resultPosterUrl.textContent = state.poster?.jpg_url || state.activeTask?.poster?.jpg_url || "等待后端返回";

  const taskError = state.activeTask?.error;
  if (!taskError) {
    els.resultError.textContent = "无";
  } else {
    els.resultError.textContent = `${taskError.code || "TASK_ERROR"}: ${taskError.message || "任务异常"}`;
  }
}

async function runComplianceCheck() {
  if (!state.selectedNode || !state.selectedProductId) {
    addProtocolIssue("合规检查需要 product_id 和 node_id，当前选择不完整。");
    return;
  }

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
    requiredFields("compliance check", data, ["status", "risk_level", "issues", "suggested_title", "suggested_subtitle"]);
    state.compliance = {
      status: data.status || "unknown",
      risk_level: data.risk_level || "",
      issues: normalizeIssues(data.issues || []),
      suggested_title: data.suggested_title || "",
      suggested_subtitle: data.suggested_subtitle || "",
    };
  } catch (error) {
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
    els.runComplianceBtn.disabled = false;
    renderCompliance();
    renderSummary();
  }
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

  if (status === "checking") {
    els.complianceCard.innerHTML = "<strong>正在检查</strong><p>正在调用合规检查接口。</p>";
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
  renderSelectFromAssets(els.previewProductAssetSelect, state.productAssets, "融合场景图");
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

function downloadPoster() {
  if (!canDownload()) return;
  const link = document.createElement("a");
  link.href = resolveReturnedUrl(state.poster.jpg_url);
  link.download = `${state.poster.title || state.poster.id || "poster"}.jpg`;
  document.body.append(link);
  link.click();
  link.remove();
}

function newTask() {
  stopPolling();
  state.activeTask = null;
  state.poster = null;
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
  els.apiBaseInput.addEventListener("change", () => {
    state.apiBase = normalizeApiBase(els.apiBaseInput.value) || API_BASE_FALLBACK;
    els.apiBaseInput.value = state.apiBase;
    localStorage.setItem("festivalPoster.apiBase", state.apiBase);
    loadNodes();
    loadSystemAssets();
    loadProducts();
  });

  els.reloadNodesBtn.addEventListener("click", loadNodes);
  els.reloadAssetsBtn.addEventListener("click", loadSystemAssets);
  els.reloadProductsBtn.addEventListener("click", loadProducts);
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
  document.querySelector("[data-open-history]").addEventListener("click", () => setScreen("history"));

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

  els.productSelect.addEventListener("change", () => {
    state.selectedProductId = els.productSelect.value;
    renderSummary();
  });
  els.createTaskBtn.addEventListener("click", createPosterTask);
  els.runComplianceBtn.addEventListener("click", runComplianceCheck);
  els.previewTitleInput.addEventListener("input", scheduleComplianceCheck);
  els.previewSubtitleInput.addEventListener("input", scheduleComplianceCheck);
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
  renderSummary();
  renderCompliance();
  await Promise.all([loadNodes(), loadSystemAssets(), loadProducts()]);
}

init();
