import { useQuery } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { auditApi, devicesApi, otaApi } from "../api/queries";
import { fmtFullDateTime } from "../format";

/** 审计页：调用 / 命令 / 任务 / 安装全局历史，链路按设备切换，全部时间倒序。 */
export function AuditPage() {
  const [kind, setKind] = useState<"calls" | "commands" | "job-runs" | "installs" | "link-events">("calls");
  const [deviceId, setDeviceId] = useState("");
  const devices = useQuery({ queryKey: ["devices"], queryFn: devicesApi.list });
  const calls = useQuery({
    queryKey: ["audit", "calls"],
    queryFn: () => auditApi.calls({ limit: 200 }),
    enabled: kind === "calls",
  });
  const commands = useQuery({
    queryKey: ["audit", "commands"],
    queryFn: () => auditApi.commands({ limit: 200 }),
    enabled: kind === "commands",
  });
  const jobRuns = useQuery({
    queryKey: ["audit", "job-runs"],
    queryFn: () => auditApi.jobRuns({ limit: 200 }),
    enabled: kind === "job-runs",
  });
  const installs = useQuery({
    queryKey: ["audit", "installs"],
    queryFn: () => otaApi.installs({ limit: 200 }),
    enabled: kind === "installs",
  });
  const linkEvents = useQuery({
    queryKey: ["audit", "link-events", deviceId],
    queryFn: () => devicesApi.linkEvents(deviceId, { limit: 200 }),
    enabled: kind === "link-events" && deviceId !== "",
  });

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <div className="mb-3 flex flex-wrap gap-2">
          {(["calls", "commands", "job-runs", "installs", "link-events"] as const).map((option) => (
            <Button
              key={option}
              size="sm"
              variant={kind === option ? "primary" : "ghost"}
              onPress={() => setKind(option)}
            >
              {{ calls: "调用", commands: "命令", "job-runs": "任务", installs: "安装", "link-events": "链路" }[option]}
            </Button>
          ))}
          {kind === "link-events" && (
            <select
              value={deviceId}
              onChange={(event) => setDeviceId(event.target.value)}
              className="rounded border border-default-300 bg-default-50 px-2 text-xs"
            >
              <option value="">选择设备</option>
              {(devices.data ?? []).map((device) => (
                <option key={device.device_id} value={device.device_id}>
                  {device.device_id}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="overflow-auto">
          {kind === "calls" &&
            (calls.data ?? []).map((row, index) => (
              <div key={`${row.request_id}-${index}`} className="mb-1 flex items-center gap-3 text-xs">
                <span className="text-default-500">{fmtFullDateTime(row.requested_at)}</span>
                <span className="font-mono">
                  {row.actor_kind}:{row.actor || "—"}
                </span>
                <span className="font-mono">
                  {row.method} {row.path}
                </span>
                <span>{row.status_code}</span>
                <span>{row.duration_ms}ms</span>
                <span className="font-mono">{row.problem_type ?? "—"}</span>
              </div>
            ))}
          {kind === "commands" &&
            (commands.data ?? []).map((row) => (
              <div key={row.command_id} className="mb-1 flex items-center gap-3 text-xs">
                <span className="text-default-500">{fmtFullDateTime(row.created_at)}</span>
                <span className="font-mono">{row.device_id ?? "—"}</span>
                <span className="font-mono">{row.action}</span>
                <span>{row.status ?? "进行中"}</span>
                <span>{row.terminal_message ?? "—"}</span>
              </div>
            ))}
          {kind === "job-runs" &&
            (jobRuns.data ?? []).map((row) => (
              <div key={row.id} className="mb-1 flex items-center gap-3 text-xs">
                <span className="text-default-500">{fmtFullDateTime(row.occurred_at)}</span>
                <span className="font-mono">{row.job}</span>
                <span className="font-mono">{row.device_id ?? "—"}</span>
                <span>{row.door_no != null ? `#${row.door_no}` : "—"}</span>
                <span>{row.outcome}</span>
                <span>{row.reason ?? "—"}</span>
              </div>
            ))}
          {kind === "installs" &&
            (installs.data ?? []).map((row, index) => (
              <div key={`${row.device_id}-${row.version}-${index}`} className="mb-1 flex items-center gap-3 text-xs">
                <span className="text-default-500">{fmtFullDateTime(row.applied_at)}</span>
                <span className="font-mono">{row.device_id}</span>
                <span className="font-mono">{row.version}</span>
              </div>
            ))}
          {kind === "link-events" &&
            deviceId !== "" &&
            (linkEvents.data ?? []).map((row, index) => (
              <div key={`${row.occurred_at}-${index}`} className="mb-1 flex items-center gap-3 text-xs">
                <span className="text-default-500">{fmtFullDateTime(row.occurred_at)}</span>
                <span className="font-mono">{deviceId}</span>
                <span>{row.event}</span>
                <span>{row.reason || "—"}</span>
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}
