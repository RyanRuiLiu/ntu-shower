import { useQuery } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { auditApi } from "../api/queries";
import { fmtFullDateTime } from "../format";

/** 审计页：三类记录（调用 / 命令 / 任务）按需切换，时间倒序。 */
export function AuditPage() {
  const [kind, setKind] = useState<"calls" | "commands" | "job-runs">("calls");
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

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <div className="mb-3 flex gap-2">
          {(["calls", "commands", "job-runs"] as const).map((option) => (
            <Button
              key={option}
              size="sm"
              variant={kind === option ? "primary" : "ghost"}
              onPress={() => setKind(option)}
            >
              {{ calls: "调用", commands: "命令", "job-runs": "任务" }[option]}
            </Button>
          ))}
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
                <span className="font-mono">{row.device_id ?? "—"}</span>
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
                <span>#{row.door_no}</span>
                <span>{row.outcome}</span>
                <span>{row.reason ?? "—"}</span>
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}
