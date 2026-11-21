/**
 * 服务端契约的 TS 类型：与 `/openapi.json` 的响应形状一一对应（字段名原样 snake_case）。
 *
 * 每个类型注明对应端点；服务端改契约时，test_api_contract 对账失败会先红。
 */

// ── 会话与用户 ──

export interface Me {
  username: string;
  name: string;
  scopes: string[];
  device_scope: string[];
  key_name: string | null;
}

export interface UserView {
  id: number;
  username: string;
  name: string;
  disabled: boolean;
  has_password: boolean;
  last_login_at: string;
  created_at: string;
}

export interface ScopeOption {
  scope: string;
  label: string;
}

export interface UserCatalog {
  scopes: ScopeOption[];
  users: UserView[];
}

export interface KeyView {
  id: number;
  user_id: number;
  name: string;
  scopes: string[];
  device_scope: string[];
  token_hint: string;
  last_used_at: string;
  created_at: string;
  revoked_at: string;
}

export interface KeyIssued extends KeyView {
  token: string;
}

// ── 设备 ──

export interface DeviceSummary {
  device_id: string;
  name: string;
  online: boolean;
  retired: boolean;
}

export interface DeviceListItem extends DeviceSummary {
  actions: string[];
}

export interface DeviceRecord extends DeviceSummary {
  trusted: boolean;
  app_version: string;
  version_code: number;
  os_version: string;
  capabilities: string[];
  last_seen_at: string;
  last_ip: string;
  booted_at: string;
  clock_skew_s: number;
  auto_close_time: string;
  auto_open_time: string;
  zone_offset_minutes: number | null;
  facts_at: string;
  sort_order: number;
}

export interface DeviceLogView {
  content: string;
  uploaded_at: string;
}

export interface DeviceLogLiveView {
  content: string;
  kind: string;
  lines: number;
  truncated: boolean;
  present: boolean;
  size_bytes: number;
  read_at: string;
}

// ── 喷头 ──

export interface DoorView {
  device_id: string;
  door_no: number;
  state: string;
  card_no: number;
  cert_no: string;
  acc_no: string;
  open_time: string;
  in_time: string;
  close_time: string;
  bath_time_s: number;
  status_raw: number;
  reported_at: string;
  last_used_at: string;
  last_used_s: number;
  last_used_cert: string;
}

// ── 任务看板 ──

export interface JobStatus {
  key: string;
  title: string;
  enabled: boolean;
  students_count: number;
}

export interface DoorAssignment {
  door_no: number;
  cert_no: string;
}

export interface HoldStatus {
  device_id: string;
  hold_source: string;
  hold_started_at: string;
  hold_offset_s: number | null;
  arming_deadline: string;
  total: number;
  reassigned_count: number;
  pending: DoorAssignment[];
  past_close: boolean;
  idle: boolean;
}

export interface ScheduledAction {
  device_id: string;
  door_no: number;
  cert_no: string;
  job: string;
  at: string;
  state: string;
  session_id: number;
  started_at: string;
  threshold_s: number;
  run_started_at: string;
  max_duration_s: number | null;
}

export interface JobsStatus {
  jobs: JobStatus[];
  holds: HoldStatus[];
  scheduled: ScheduledAction[];
}

// ── 配置 ──

export type ConfigValues = Record<string, number | boolean | string | Record<string, string>>;

export interface ConfigSpecView {
  key: string;
  kind: string;
  scope: string;
  default: number | boolean | string | Record<string, string>;
  title: string;
  description: string;
  minimum: number | null;
  maximum: number | null;
  unit: string;
  choices: string[] | null;
  group: string;
}

export interface ConfigSpecCatalog {
  sections: { key: string; title: string }[];
  groups: { key: string; title: string; section: string }[];
  specs: ConfigSpecView[];
}

export interface GlobalConfigView {
  revision: number;
  config: ConfigValues;
}

export interface GlobalConfigUpdateReport {
  revision: number;
  config: ConfigValues;
  notified: { device_id: string; notified?: boolean; applied?: boolean; reason?: string }[];
}

export interface DeviceConfigView {
  revision: number;
  config: ConfigValues;
  overrides: ConfigValues | null;
}

// ── 语音 ──

export type Segment = { kind: "asset"; asset_id: string } | { kind: "digits"; source: "door_no" | "cert_no"; length?: number | null } | { kind: "pause"; ms: number };

export interface VoiceAssetView {
  asset_id: string;
  name: string;
  digit: number | null;
  unit: string | null;
  duration_ms: number;
  size_bytes: number;
  sha256: string;
  url: string;
  created_at: string;
}

export interface VoiceStatusView {
  asset_count: number;
  digits: number[];
  units: string[];
}

export interface VoiceRuleView {
  id: number;
  devices: string[];
  certs: string[];
  exempt: string[];
  events: string[];
  time_windows: { days: number[]; start: string; end: string }[];
  segments: Segment[];
  summary: string;
  time_summary: string;
  volume: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface VoiceSpecView {
  events: { value: string; label: string; description: string; phase: string }[];
  volume: { minimum: number; maximum: number };
  digits: number[];
  asset_count: number;
  max_segments: number;
  max_time_windows: number;
  max_pause_ms: number;
  min_pause_ms: number;
  max_digits_length: number;
}

export interface VoiceDeliveryView {
  id: number;
  source: string;
  rule_id: number;
  device_id: string;
  cert_no: string;
  event: string;
  door_no: number;
  summary: string;
  segment_count: number;
  duration_ms: number;
  occurred_at: string;
  dispatched_at: string;
  done_at: string;
  status: string;
  detail: string;
  downloaded: boolean;
}

// ── OTA ──

export interface ReleaseRecord {
  version: string;
  version_code: number;
  sha256: string;
  force: boolean;
  target: string[];
  created_at: string;
}

export interface ReleasePublished {
  version: string;
  version_code: number;
  sha256: string;
  size: number;
  notified_devices: string[];
}

// ── 审计 ──

export interface ApiCallRecord {
  kind: string;
  requested_at: string;
  request_id: string;
  trace_id: string;
  actor_kind: string;
  actor: string;
  client_ip: string | null;
  user_agent: string | null;
  method: string;
  path: string;
  query: string | null;
  request_body: string | null;
  status_code: number;
  duration_ms: number;
  device_id: string | null;
  problem_type: string | null;
  detail: string | null;
  response_body: string | null;
}

export interface CommandAuditRecord {
  command_id: string;
  device_id: string | null;
  action: string;
  params: string | null;
  created_at: string;
  finished_at: string | null;
  status: string | null;
  reason_code: string | null;
  terminal_message: string | null;
  trace_id: string | null;
  data: string | null;
}

export interface JobRunRecord {
  id: number;
  occurred_at: string;
  device_id: string | null;
  door_no: number;
  session_id: number | null;
  cert_no: string;
  job: string;
  action: string | null;
  outcome: string;
  reason: string | null;
  threshold_s: number | null;
}

// ── 设备命令的统一结果（assign/settle/exec/capture/…） ──

export interface CommandOutcome {
  status: string;
  reason_code: string;
  terminal_message: string;
  data: Record<string, unknown> | null;
}
