/**
 * 服务端 API 的逐端点封装：路径与参数只有这里出现。
 *
 * 页面用 TanStack Query 的 useQuery/useMutation 消费这些函数；查询键按域分层，
 * WS 推送到达时按域失效（见 queries 内的 key 工厂）。
 */

import {
  type ApiCallRecord,
  type VoiceAssetView,
  type CommandAuditRecord,
  type CommandOutcome,
  type ConfigSpecCatalog,
  type ConfigValues,
  type DeviceConfigView,
  type DeviceLinkEventRecord,
  type DeviceListItem,
  type DeviceLogHistoryRecord,
  type DeviceLogLiveView,
  type DeviceLogView,
  type DeviceRecord,
  type DoorView,
  type GlobalConfigUpdateReport,
  type GlobalConfigView,
  type JobRunRecord,
  type JobsStatus,
  type KeyIssued,
  type KeyView,
  type Me,
  type OtaInstallRecord,
  type ReleaseRecord,
  type UserCatalog,
  type UserView,
  type VoiceDeliveryView,
  type VoiceRuleView,
  type VoiceSpecView,
  type VoiceStatusView,
} from "./types";
import { API_V1, del, devicePath, get, patch, post, postForm, put } from "./client";

// ── 会话 ──

export const authApi = {
  login: (username: string, password: string) =>
    post<void>(`${API_V1}/auth/login`, { username, password }),
  logout: () => post<void>(`${API_V1}/auth/logout`),
  me: () => get<Me>(`${API_V1}/users/me`),
};

// ── 用户与密钥 ──

export const usersApi = {
  catalog: () => get<UserCatalog>(`${API_V1}/users`),
  create: (value: { username: string; name?: string; password?: string }) =>
    post<UserView>(`${API_V1}/users`, value),
  update: (username: string, value: { name?: string; disabled?: boolean; password?: string }) =>
    put<UserView>(`${API_V1}/users/${encodeURIComponent(username)}`, value),
  remove: (username: string) =>
    del<void>(`${API_V1}/users/${encodeURIComponent(username)}`),
  keys: (username: string) =>
    get<{ keys: KeyView[] }>(`${API_V1}/users/${encodeURIComponent(username)}/keys`),
  createKey: (
    username: string,
    value: { name?: string; scopes: string[]; device_scope: string[] },
  ) => post<KeyIssued>(`${API_V1}/users/${encodeURIComponent(username)}/keys`, value),
  updateKey: (
    username: string,
    keyId: number,
    value: { name?: string; scopes?: string[]; device_scope?: string[] },
  ) =>
    put<KeyView>(
      `${API_V1}/users/${encodeURIComponent(username)}/keys/${keyId}`,
      value,
    ),
  revokeKey: (username: string, keyId: number) =>
    del<void>(`${API_V1}/users/${encodeURIComponent(username)}/keys/${keyId}`),
};

// ── 设备 ──

export const devicesApi = {
  list: () => get<DeviceListItem[]>(`${API_V1}/devices`),
  runtime: (deviceId: string) => get<DeviceRecord>(devicePath(deviceId)),
  linkEvents: (deviceId: string, params?: { limit?: number }) =>
    get<DeviceLinkEventRecord[]>(devicePath(deviceId, "link-events") + toSearch(params)),
  logHistory: (deviceId: string, kind: string, params?: { limit?: number }) =>
    get<DeviceLogHistoryRecord[]>(devicePath(deviceId, "logs/history") + toSearch({ ...params, kind })),
  patch: (deviceId: string, value: { name?: string; trusted?: boolean; retired?: boolean }) =>
    patch<DeviceRecord>(devicePath(deviceId), value),
  order: (order: string[]) => patch<{ order: string[] }>(`${API_V1}/devices`, { order }),
  logs: (deviceId: string, kind: string) =>
    get<DeviceLogView>(devicePath(deviceId, `/logs?kind=${encodeURIComponent(kind)}`)),
  logsLive: (deviceId: string, kind: string) =>
    get<DeviceLogLiveView>(devicePath(deviceId, `/logs/live?kind=${encodeURIComponent(kind)}`)),
  exec: (deviceId: string, command: string) =>
    post<CommandOutcome>(devicePath(deviceId, "/exec"), { command }),
  capture: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/capture")),
  restartApp: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/restart-app")),
  restartBath: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/restart-bath")),
  reboot: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/reboot")),
  clockSet: (deviceId: string, datetime: string) =>
    post<CommandOutcome>(devicePath(deviceId, "/clock-set"), { datetime }),
  hold: (deviceId: string) =>
    post<{ device_id: string; hold_source: string; hold_started_at: string }>(devicePath(deviceId, "/hold")),
  resume: (deviceId: string) =>
    post<{ device_id: string; resumed: boolean }>(devicePath(deviceId, "/resume")),
  uiDump: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/ui-dump")),
  uiTouch: (deviceId: string, value: { x: number; y: number; to_x?: number; to_y?: number; duration_ms?: number }) =>
    post<CommandOutcome>(devicePath(deviceId, "/ui-touch"), value),
  uiKey: (deviceId: string, key: string) => post<CommandOutcome>(devicePath(deviceId, "/ui-key"), { key }),
  uiInput: (deviceId: string, text: string, channel: "clipboard" | "keys") =>
    post<CommandOutcome>(devicePath(deviceId, "/ui-input"), { text, channel }),
  enroll: (body: { device_id: string; app_version: string; version_code: number; os_version: string; capabilities: string[] }, claim: string) =>
    post<{ device_id: string; token: string }>(
      `${API_V1}/devices/enroll`,
      body,
      { headers: { Authorization: `Bearer ${claim}` } },
    ),
};

// ── 会话操作 ──

export const sessionsApi = {
  assign: (deviceId: string, value: { door_no: number; cert_no: string; password: string }) =>
    post<CommandOutcome>(devicePath(deviceId, "/assign"), value),
  settle: (deviceId: string, value: { cert_no: string; password: string }) =>
    post<CommandOutcome>(devicePath(deviceId, "/settle"), value),
  photo: (deviceId: string) => post<CommandOutcome>(devicePath(deviceId, "/photo")),
};

// ── 喷头 ──

export const doorsApi = {
  list: (deviceId?: string) =>
    get<DoorView[]>(`${API_V1}/doors${deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ""}`),
};

// ── 任务看板 ──

export const jobsApi = {
  status: () => get<JobsStatus>(`${API_V1}/jobs/status`),
};

// ── 配置 ──

export const configApi = {
  spec: () => get<ConfigSpecCatalog>(`${API_V1}/config/spec`),
  global: () => get<GlobalConfigView>(`${API_V1}/config`),
  saveGlobal: (config: ConfigValues) =>
    put<GlobalConfigUpdateReport>(`${API_V1}/config`, { config }),
  device: (deviceId: string) => get<DeviceConfigView>(devicePath(deviceId, "/config")),
  saveDevice: (deviceId: string, config: ConfigValues) =>
    put<{ device_id: string; revision: number; applied: boolean; reason: string }>(
      devicePath(deviceId, "/config"),
      { config },
    ),
};

// ── 语音 ──

export const voiceApi = {
  spec: () => get<VoiceSpecView>(`${API_V1}/voice/spec`),
  status: () => get<VoiceStatusView>(`${API_V1}/voice/status`),
  assets: () => get<VoiceAssetView[]>(`${API_V1}/voice/assets`),
  uploadAsset: (form: FormData) => postForm<VoiceAssetView>(`${API_V1}/voice/assets`, form),
  renameAsset: (assetId: string, name: string) =>
    patch<VoiceAssetView>(`${API_V1}/voice/assets/${encodeURIComponent(assetId)}`, { name }),
  deleteAsset: (assetId: string) =>
    del<void>(`${API_V1}/voice/assets/${encodeURIComponent(assetId)}`),
  rules: () => get<VoiceRuleView[]>(`${API_V1}/voice/rules`),
  createRule: (value: unknown) => post<VoiceRuleView>(`${API_V1}/voice/rules`, value),
  updateRule: (ruleId: number, value: unknown) =>
    put<VoiceRuleView>(`${API_V1}/voice/rules/${ruleId}`, value),
  deleteRule: (ruleId: number) => del<void>(`${API_V1}/voice/rules/${ruleId}`),
  preview: (value: unknown) => post(`${API_V1}/voice/preview`, value),
  push: (ruleId: number, deviceId: string) =>
    post(`${API_V1}/voice/rules/${ruleId}/push`, { device_id: deviceId }),
  deliveries: () => get<VoiceDeliveryView[]>(`${API_V1}/voice/deliveries`),
};

// ── 发布 ──

export const otaApi = {
  releases: () => get<ReleaseRecord[]>(`${API_V1}/ota/releases`),
  installs: (params?: { device_id?: string; limit?: number }) =>
    get<OtaInstallRecord[]>(`${API_V1}/ota/installs${toSearch(params)}`),
  upload: (form: FormData) => postForm<{ version: string; notified_devices: string[] }>(
    `${API_V1}/ota/releases`,
    form,
  ),
  remove: (version: string) =>
    del<{ version: string; deleted: boolean }>(
      `${API_V1}/ota/releases/${encodeURIComponent(version)}`,
    ),
};

// ── 审计 ──

export const auditApi = {
  calls: (params?: { actor_kind?: string; limit?: number }) =>
    get<ApiCallRecord[]>(`${API_V1}/audit/calls${toSearch(params)}`),
  commands: (params?: { device_id?: string; limit?: number }) =>
    get<CommandAuditRecord[]>(`${API_V1}/audit/commands${toSearch(params)}`),
  jobRuns: (params?: { device_id?: string; limit?: number }) =>
    get<JobRunRecord[]>(`${API_V1}/audit/job-runs${toSearch(params)}`),
};

function toSearch(params?: Record<string, unknown>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
