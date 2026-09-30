/* 用户页逻辑：门牌墙渲染、登录、分配/结算/拍照 */
"use strict";

const STATE_LABEL = {
  free: "空闲", waiting: "已入门", bathing: "使用中", paused: "已暂停",
  selected: "选中", repair: "报修", disabled: "禁用", unknown: "未知",
};

function showResult(html) {
  $("result").innerHTML = html;
}

async function runAction(button, path, buildBody) {
  button.disabled = true;
  showResult("执行中，最长约 60 秒。");
  try {
    const result = await api("POST", path, { body: buildBody(), auth: "none" });
    if (result.status === "ok") {
      showResult(result.terminal_message
        ? `<strong>完成</strong>　${escapeHtml(result.terminal_message)}`
        : "<strong>完成</strong>");
    } else {
      showResult(`未完成：${escapeHtml(result.reason_code || result.status)} ` +
        `${escapeHtml(result.terminal_message || "")}`);
    }
  } catch (error) {
    showResult(`未完成：${escapeHtml(error.message)}`);
  } finally {
    button.disabled = false;
    loadPlates();
  }
}

function renderPlates(list) {
  const deviceIds = [...new Set(list.map((d) => d.device_id))];
  const selector = $("deviceId");
  if ([...selector.options].map((o) => o.value).join(",") !== deviceIds.join(",")) {
    selector.innerHTML = deviceIds
      .map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("");
    const saved = localStorage.getItem("deviceId");
    if (saved && deviceIds.includes(saved)) selector.value = saved;
  }

  const current = list.filter((d) => d.device_id === selector.value);
  $("offline").hidden = !current.some((d) => !d.device_online);
  $("empty").hidden = list.length > 0;

  const doorNumbers = current.map((d) => d.door_no).sort((a, b) => a - b);
  const doorSelect = $("doorNo");
  if ([...doorSelect.options].map((o) => o.value).join(",") !== doorNumbers.join(",")) {
    doorSelect.innerHTML = doorNumbers.map((n) => `<option>${n}</option>`).join("");
  }

  const wall = $("plates");
  wall.innerHTML = "";
  for (const door of current) {
    const plate = document.createElement("div");
    plate.className = "plate";
    plate.dataset.state = door.state;
    plate.innerHTML =
      `<div class="window">${escapeHtml(STATE_LABEL[door.state] || door.state)}</div>` +
      `<div class="no">${escapeHtml(door.door_no)}</div>`;
    wall.appendChild(plate);
  }
}

async function loadPlates() {
  try {
    const list = await (await fetch("/api/v1/doors")).json();
    renderPlates(list);
  } catch (error) {
    showResult(`状态获取失败：${escapeHtml(error.message)}`);
  }
}

function connectStream() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/api/v1/stream`);
  ws.onopen = () => {
    $("live").textContent = "实时";
    $("live").dataset.live = "on";
  };
  ws.onmessage = () => loadPlates();
  ws.onclose = () => {
    $("live").textContent = "推送断开，3 秒后重连";
    $("live").dataset.live = "off";
    setTimeout(connectStream, 3000);
  };
}

$("deviceId").addEventListener("change", () => {
  localStorage.setItem("deviceId", $("deviceId").value);
  loadPlates();
});
$("assign").addEventListener("click",
  () => runAction($("assign"), "/api/v1/sessions/assign", () => ({
    device_id: $("deviceId").value,
    door_no: Number($("doorNo").value),
    cert_no: $("certNo").value.trim(),
    password: $("password").value,
  })));
$("settle").addEventListener("click",
  () => runAction($("settle"), "/api/v1/sessions/settle", () => ({
    device_id: $("deviceId").value,
    cert_no: $("certNo").value.trim(),
    password: $("password").value,
  })));
$("photo").addEventListener("click", async () => {
  $("photo").disabled = true;
  showResult("正在拍照。");
  try {
    const result = await api("POST", "/api/v1/photos", {
      body: { device_id: $("deviceId").value }, auth: "none",
    });
    if (result.data && result.data.image_base64) {
      $("photoBox").innerHTML =
        `<img class="photo-shot" alt="现场照片" src="data:image/jpeg;base64,${result.data.image_base64}">`;
      showResult("<strong>已获取现场照片</strong>");
    } else {
      showResult(`未完成：${escapeHtml(result.reason_code || result.status)}`);
    }
  } catch (error) {
    showResult(`未完成：${escapeHtml(error.message)}`);
  } finally {
    $("photo").disabled = false;
  }
});

loadPlates();
connectStream();
setInterval(loadPlates, 5000);
