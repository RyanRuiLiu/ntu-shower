/* ntu-shower 共享脚本：DOM 工具 + API 客户端（零构建，原生 JS） */
"use strict";

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function adminToken() {
  return localStorage.getItem("adminToken") || "";
}

/**
 * 统一 API 客户端。
 * auth: "admin"（X-Admin-Token，默认）| "none"（匿名）。
 * 非 2xx 抛 Error，消息取 RFC 9457 的 detail。
 */
async function api(method, url, { body, isForm, auth = "admin" } = {}) {
  const headers = {};
  if (auth === "admin" && adminToken()) headers["X-Admin-Token"] = adminToken();
  let payload = body;
  if (body !== undefined && !isForm) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(url, { method, headers, body: payload });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}
