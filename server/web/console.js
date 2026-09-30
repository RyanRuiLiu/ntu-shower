/* 管理台逻辑：段控切换、设备名册、远程执行、配置下发、OTA 发布 */
"use strict";

const CONFIG_KEYS = [
  "heartbeat_interval_s", "server_ping_interval_s", "reconnect_base_ms",
  "reconnect_max_ms", "watchdog_interval_s", "report_debounce_ms",
  "server_response_timeout_ms", "id_verify_wait_ms", "home_budget_ms",
  "poll_interval_ms", "ui_click_delay_ms", "ota_check_interval_s",
];

const RESTART_COMMAND = "(sleep 1; am force-stop com.ryanliu.ntushower; sleep 2; " +
  "am startservice -n com.ryanliu.ntushower/.service.AgentService) >/dev/null 2>&1 &";
const UNINSTALL_COMMAND = "(sleep 1; pm uninstall com.ryanliu.ntushower) >/dev/null 2>&1 &";

let selectedDevice = localStorage.getItem("selectedDevice") || "";

function setState(text, isError = false) {
  const badge = $("state");
  badge.textContent = text;
  badge.className = isError ? "state-flash err" : "state-flash";
}

/* ── 段控切换 ── */

document.querySelectorAll(".tabs button[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll(".tabs button[data-tab]")) {
      other.setAttribute("aria-current", String(other === button));
    }
    for (const sheet of document.querySelectorAll(".sheet")) {
      sheet.hidden = sheet.id !== `tab-${button.dataset.tab}`;
    }
  });
});

/* ── 设备名册 ── */

function rosterRow(device) {
  const id = escapeHtml(device.device_id);
  const row = document.createElement("div");
  row.className = "row-item";
  row.innerHTML =
    `<span class="id">${id}</span>` +
    `<span class="dim">${escapeHtml(device.name) || "未命名"}</span>` +
    `<span class="win ${device.trusted ? "on" : ""}">${device.trusted ? "已信任" : "未信任"}</span>` +
    `<span class="win ${device.online ? "on" : "warn"}">${device.online ? "在线" : "离线"}</span>` +
    `<span class="dim">${escapeHtml(device.app_version)}</span>` +
    `<span class="dim">${escapeHtml(device.last_seen_at)}</span>` +
    `<span class="spacer"></span>` +
    `<button data-act="trust" data-id="${id}">${device.trusted ? "撤销信任" : "信任"}</button>` +
    `<button data-act="rename" data-id="${id}">改名</button>` +
    `<button data-act="restart" data-id="${id}">重启</button>` +
    `<button class="danger" data-act="uninstall" data-id="${id}">卸载</button>`;
  return row;
}

async function loadDevices() {
  const list = await api("GET", "/admin/devices");
  const roster = $("roster");
  roster.innerHTML = "";
  for (const device of list) roster.appendChild(rosterRow(device));

  const options = ['<option value="">（选择设备）</option>']
    .concat(list.map((d) => `<option value="${escapeHtml(d.device_id)}">` +
      `${escapeHtml(d.device_id)}${d.online ? "（在线）" : ""}</option>`))
    .join("");
  for (const select of [$("execDevice"), $("cfgDevice"), $("relTarget")]) {
    select.innerHTML = options;
    if ([...select.options].some((o) => o.value === selectedDevice)) select.value = selectedDevice;
  }
}

$("roster").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-act]");
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.act === "trust") {
      await api("POST", `/admin/devices/${id}/${button.textContent === "撤销信任" ? "untrust" : "trust"}`);
      await loadDevices();
    } else if (button.dataset.act === "rename") {
      const name = prompt("设备名称：", "");
      if (name) {
        await api("PATCH", `/admin/devices/${id}`, { body: { name } });
        await loadDevices();
      }
    } else if (button.dataset.act === "restart") {
      await api("POST", `/admin/devices/${id}/exec`, { body: { command: RESTART_COMMAND } });
      setState(`已下发重启：${id}`);
      setTimeout(loadDevices, 8000);
    } else if (button.dataset.act === "uninstall") {
      if (!confirm(`确定卸载设备 ${id} 上的 APK？卸载后需到现场重装才能恢复。`)) return;
      await api("POST", `/admin/devices/${id}/exec`, { body: { command: UNINSTALL_COMMAND } });
      setState(`已下发卸载：${id}`);
      setTimeout(loadDevices, 8000);
    }
  } catch (error) {
    setState(`操作失败：${error.message}`, true);
  }
});

/* ── 远程执行 ── */

async function runCommand(command) {
  const device = $("execDevice").value;
  if (!device) {
    $("execOut").textContent = "先在上方选择设备。";
    return;
  }
  $("execOut").textContent = "执行中…";
  try {
    const result = await api("POST", `/admin/devices/${device}/exec`,
      { body: { command: command ?? $("execCmd").value } });
    $("execOut").textContent = result.data ? result.data.output : JSON.stringify(result);
  } catch (error) {
    $("execOut").textContent = `错误：${error.message}`;
  }
}

$("runCmd").addEventListener("click", () => runCommand());
$("pullLogcat").addEventListener("click", () => runCommand("logcat -d -t 200"));
$("battery").addEventListener("click", () => runCommand("dumpsys battery"));

/* ── 配置 ── */

async function loadConfig() {
  const device = $("cfgDevice").value;
  const data = device
    ? await api("GET", `/admin/devices/${device}/config`)
    : await api("GET", "/admin/config/global");
  const shown = device ? data.effective : data.config;
  const box = $("cfgFields");
  box.innerHTML = "";
  for (const key of CONFIG_KEYS) {
    const field = document.createElement("label");
    field.innerHTML =
      `<span class="key">${escapeHtml(key)}</span>` +
      `<input id="cfg_${escapeHtml(key)}" value="${escapeHtml(shown[key] ?? "")}">`;
    box.appendChild(field);
  }
}

function collectConfig() {
  const config = {};
  for (const key of CONFIG_KEYS) {
    const value = $(`cfg_${key}`).value;
    if (value !== "") config[key] = Number.isNaN(Number(value)) ? value : Number(value);
  }
  return config;
}

async function saveConfig() {
  const device = $("cfgDevice").value;
  const result = device
    ? await api("PUT", `/admin/devices/${device}/config`, { body: { config: collectConfig() } })
    : await api("PUT", "/admin/config/global", { body: { config: collectConfig() } });
  setState(`配置已下发：${JSON.stringify(result)}`);
  await loadConfig();
}

$("loadConfig").addEventListener("click", () => loadConfig().catch((e) => setState(e.message, true)));
$("saveConfig").addEventListener("click", () => saveConfig().catch((e) => setState(e.message, true)));
$("clearOverride").addEventListener("click", async () => {
  const device = $("cfgDevice").value;
  if (!device) {
    setState("全局默认没有覆盖可清除，请先选择设备。", true);
    return;
  }
  await api("DELETE", `/admin/devices/${device}/config`);
  await loadConfig();
  setState("已清除设备覆盖。");
});
$("cfgDevice").addEventListener("change", () => {
  selectedDevice = $("cfgDevice").value;
  localStorage.setItem("selectedDevice", selectedDevice);
  loadConfig().catch(() => {});
});

/* ── OTA ── */

async function loadReleases() {
  const list = await api("GET", "/ota/releases");
  const box = $("releases");
  box.innerHTML = "";
  for (const release of list) {
    const row = document.createElement("div");
    row.className = "row-item";
    row.innerHTML =
      `<span class="id">${escapeHtml(release.version)}</span>` +
      `<span class="dim">code ${release.version_code}</span>` +
      `<span class="win ${release.mandatory ? "warn" : ""}">${release.mandatory ? "强制" : "常规"}</span>` +
      `<span class="win ${release.force ? "warn" : ""}">${release.force ? "force" : ""}</span>` +
      `<span class="dim">${escapeHtml(release.target)}</span>` +
      `<span class="dim">${escapeHtml(release.created_at)}</span>`;
    box.appendChild(row);
  }
}

async function uploadRelease() {
  const file = $("relFile").files[0];
  if (!file) {
    setState("先选择 APK 文件再发布。", true);
    return;
  }
  const form = new FormData();
  form.append("version", $("relVer").value);
  form.append("version_code", $("relCode").value);
  form.append("mandatory", $("relMand").checked);
  form.append("force", $("relForce").checked);
  form.append("target", $("relTarget").value || "all");
  form.append("file", file);
  $("relOut").textContent = "上传中…";
  try {
    const result = await api("POST", "/admin/releases", { body: form, isForm: true });
    $("relOut").textContent = JSON.stringify(result, null, 2);
    await loadReleases();
  } catch (error) {
    $("relOut").textContent = `错误：${error.message}`;
  }
}

$("uploadRelease").addEventListener("click", () => uploadRelease());
$("execDevice").addEventListener("change", () => {
  selectedDevice = $("execDevice").value;
  localStorage.setItem("selectedDevice", selectedDevice);
});

/* ── 启动 ── */

$("saveToken").addEventListener("click", () => {
  localStorage.setItem("adminToken", $("token").value.trim());
  loadAll();
});

function loadAll() {
  loadDevices().catch((e) => setState(e.message, true));
  loadReleases().catch(() => {});
  loadConfig().catch(() => {});
}

if (adminToken()) {
  $("token").value = adminToken();
  loadAll();
}
