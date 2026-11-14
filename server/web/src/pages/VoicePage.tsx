import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { voiceApi } from "../api/queries";
import { ApiError } from "../api/client";
import { fmtFullDateTime } from "../format";

/** 语音页：素材库 + 规则列表 + 播报投递记录（规则编辑器另行深化，先保通）。 */
export function VoicePage() {
  const queryClient = useQueryClient();
  const spec = useQuery({ queryKey: ["voice", "spec"], queryFn: voiceApi.spec });
  const rules = useQuery({ queryKey: ["voice", "rules"], queryFn: voiceApi.rules });
  const deliveries = useQuery({ queryKey: ["voice", "deliveries"], queryFn: voiceApi.deliveries });
  const [error, setError] = useState("");

  const toggle = useMutation({
    mutationFn: (rule: { id: number; enabled: boolean }) =>
      voiceApi.updateRule(rule.id, { enabled: rule.enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["voice", "rules"] }),
    onError: (error) => setError(error instanceof ApiError ? error.message : "操作失败"),
  });

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">规则（{rules.data?.length ?? 0} 条）</h2>
        {(rules.data ?? []).map((rule) => (
          <div key={rule.id} className="mb-2 rounded-xl bg-content2/40 p-3">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">{rule.summary}</span>
              <span className="text-default-500">{rule.time_summary}</span>
              <span
                className={`ml-auto rounded-full px-2 py-0.5 ${
                  rule.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-default-500/20 text-default-400"
                }`}
              >
                {rule.enabled ? "启用" : "停用"}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onPress={() => toggle.mutate({ id: rule.id, enabled: !rule.enabled })}
              >
                {rule.enabled ? "停用" : "启用"}
              </Button>
              <Button
                size="sm"
                variant="danger-soft"
                onPress={() => {
                  if (window.confirm(`删除规则 #${rule.id}？`)) voiceApi.deleteRule(rule.id);
                }}
              >
                删除
              </Button>
            </div>
          </div>
        ))}
        {spec.data && (
          <p className="mt-2 text-xs text-default-500">
            素材 {spec.data.asset_count} 段 · 事件 {spec.data.events.length} 种 · 音量{" "}
            {spec.data.volume.minimum}–{spec.data.volume.maximum}
          </p>
        )}
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </section>

      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">播报投递</h2>
        {(deliveries.data ?? []).map((delivery) => (
          <div key={delivery.id} className="mb-1 flex items-center gap-3 text-xs">
            <span className="text-default-500">{fmtFullDateTime(delivery.occurred_at)}</span>
            <span className="font-mono">{delivery.device_id}</span>
            <span>{delivery.event}</span>
            <span
              className={`rounded-full px-2 py-0.5 ${
                delivery.status === "ok" ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"
              }`}
            >
              {delivery.status}
            </span>
            <span>{fmtFullDateTime(delivery.done_at)}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
