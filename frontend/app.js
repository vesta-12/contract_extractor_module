const fileInput = document.querySelector("#file-input");
const dropZone = document.querySelector("#drop-zone");
const selection = document.querySelector("#selection");
const selectedFilesList = document.querySelector("#selected-files");
const selectionSummary = document.querySelector("#selection-summary");
const processButton = document.querySelector("#process-button");
const formError = document.querySelector("#form-error");
const uploadSection = document.querySelector("#upload-section");
const resultsSection = document.querySelector("#results-section");
const documentResults = document.querySelector("#document-results");
const jobStatus = document.querySelector("#job-status");
const jobCount = document.querySelector("#job-count");
const progressValue = document.querySelector("#progress-value");
const downloadAll = document.querySelector("#download-all");
const newJobButton = document.querySelector("#new-job-button");
const jobError = document.querySelector("#job-error");
const health = document.querySelector("#health");
const limits = document.querySelector("#limits");

const terminalStatuses = new Set([
  "completed",
  "partially_completed",
  "failed",
]);

const statusLabels = {
  queued: "В очереди",
  processing: "Обрабатывается",
  completed: "Готово",
  partially_completed: "Готово частично",
  failed: "Ошибка",
};

let selectedFiles = [];
let pollingGeneration = 0;

function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3);
  const amount = value / 1024 ** index;
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showFormError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function clearFormError() {
  formError.hidden = true;
  formError.textContent = "";
}

function addFiles(files) {
  clearFormError();
  selectedFiles.push(...Array.from(files));
  renderSelection();
}

function renderSelection() {
  selectedFilesList.replaceChildren();
  selectedFiles.forEach((file, index) => {
    const row = element("li");
    const details = element("div");
    details.append(
      element("div", "file-name", file.name),
      element("span", "file-size", formatBytes(file.size)),
    );
    const remove = element("button", "remove-button", "Удалить");
    remove.type = "button";
    remove.setAttribute("aria-label", `Удалить ${file.name}`);
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      renderSelection();
    });
    row.append(details, remove);
    selectedFilesList.append(row);
  });

  const totalSize = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  selectionSummary.textContent = `${selectedFiles.length} · ${formatBytes(totalSize)}`;
  selection.hidden = selectedFiles.length === 0;
  processButton.disabled = selectedFiles.length === 0;
}

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.message || "Не удалось выполнить запрос";
  } catch {
    return "Не удалось выполнить запрос";
  }
}

async function startProcessing() {
  if (!selectedFiles.length) return;
  clearFormError();
  processButton.disabled = true;
  processButton.textContent = "Загружаем…";
  const formData = new FormData();
  selectedFiles.forEach((file) => formData.append("files", file, file.name));

  try {
    const response = await fetch("/api/documents/process", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) throw new Error(await readError(response));
    const job = await response.json();
    uploadSection.hidden = true;
    resultsSection.hidden = false;
    renderJob(job);
    const generation = ++pollingGeneration;
    await pollJob(job.job_id, generation);
  } catch (error) {
    const message = error.message || "Не удалось начать обработку";
    if (uploadSection.hidden) {
      jobError.textContent = message;
      jobError.hidden = false;
    } else {
      showFormError(message);
    }
    processButton.disabled = false;
  } finally {
    processButton.textContent = "Запустить обработку";
  }
}

async function pollJob(jobId, generation) {
  while (generation === pollingGeneration) {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    if (!response.ok) {
      jobError.textContent = await readError(response);
      jobError.hidden = false;
      return;
    }
    const job = await response.json();
    renderJob(job);
    if (terminalStatuses.has(job.status)) return;
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
  }
}

function renderJob(job) {
  const documents = job.documents || [];
  const completedCount = documents.filter((document) =>
    terminalStatuses.has(document.status),
  ).length;
  const percentage = documents.length
    ? Math.round((completedCount / documents.length) * 100)
    : 0;

  jobStatus.textContent = statusLabels[job.status] || job.status;
  jobCount.textContent = `${completedCount} из ${documents.length}`;
  progressValue.style.width = `${percentage}%`;
  documentResults.replaceChildren(...documents.map(renderDocument));

  if (job.download_url) {
    downloadAll.href = job.download_url;
    downloadAll.hidden = false;
  } else {
    downloadAll.hidden = true;
    downloadAll.removeAttribute("href");
  }
}

function renderDocument(document) {
  const card = element("article", "document-card");
  const header = element("div", "document-card-header");
  const titleGroup = element("div");
  titleGroup.append(element("h3", "", document.source_file_name));
  const meta = element("div", "document-meta");
  meta.append(element("span", "", formatBytes(document.size_bytes)));
  if (document.page_count) {
    meta.append(element("span", "", `${document.page_count} стр.`));
  }
  if (Number.isFinite(document.entity_count)) {
    meta.append(element("span", "", `${document.entity_count} сущностей`));
  }
  titleGroup.append(meta);
  const status = element(
    "span",
    `status-pill ${document.status}`,
    statusLabels[document.status] || document.status,
  );
  header.append(titleGroup, status);
  card.append(header);

  const entities = document.entities || [];
  if (entities.length) {
    const entityList = element("ul", "entity-list");
    entities.slice(0, 12).forEach((entityData) => {
      const item = element("li");
      item.append(
        element("span", "entity-type", entityData.type),
        element("span", "entity-value", entityData.value),
      );
      entityList.append(item);
    });
    if (entities.length > 12) {
      const item = element("li");
      item.append(
        element("span", "entity-type", "Ещё"),
        element("span", "entity-value", `${entities.length - 12} сущностей`),
      );
      entityList.append(item);
    }
    card.append(entityList);
  }

  appendMessages(card, document.warnings, "message-list", "Предупреждение");
  appendMessages(card, document.errors, "message-list errors", "Ошибка");

  const downloads = document.downloads || {};
  if (downloads.annotated_document || downloads.result_json) {
    const actions = element("div", "document-actions");
    if (downloads.annotated_document) {
      const link = element("a", "", "PDF с подсветкой");
      link.href = downloads.annotated_document;
      actions.append(link);
    }
    if (downloads.result_json) {
      const link = element("a", "", "Итоговый JSON");
      link.href = downloads.result_json;
      actions.append(link);
    }
    card.append(actions);
  }
  return card;
}

function appendMessages(card, messages, className, fallback) {
  if (!messages || !messages.length) return;
  const list = element("ul", className);
  messages.forEach((message) => {
    const text = typeof message === "string" ? message : message.message;
    list.append(element("li", "", text || fallback));
  });
  card.append(list);
}

function resetApplication() {
  pollingGeneration += 1;
  selectedFiles = [];
  fileInput.value = "";
  renderSelection();
  clearFormError();
  jobError.hidden = true;
  jobError.textContent = "";
  documentResults.replaceChildren();
  resultsSection.hidden = true;
  uploadSection.hidden = false;
  downloadAll.hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error();
    const payload = await response.json();
    const ready = payload.status === "ready";
    health.classList.toggle("ready", ready);
    health.classList.toggle("error", !ready);
    health.querySelector("span:last-child").textContent = ready
      ? "OCR, извлечение и подсветка готовы"
      : "Сервер запущен, но OCR требует настройки";
    if (payload.limits) {
      limits.textContent = `PDF, до ${formatBytes(payload.limits.max_file_size_bytes)} на файл · максимум ${payload.limits.max_files}`;
    }
  } catch {
    health.classList.add("error");
    health.querySelector("span:last-child").textContent =
      "Не удалось связаться с сервером";
  }
}

fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});
processButton.addEventListener("click", startProcessing);
newJobButton.addEventListener("click", resetApplication);

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});

dropZone.addEventListener("drop", (event) => {
  addFiles(event.dataTransfer.files);
});

limits.textContent = "PDF, до 25 МБ на файл";
checkHealth();
