import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { configApi } from "../api/queries";
import { ApiError } from "../api/client";
import type { GlobalConfigView } from "../api/types";
import type { ConfigSpecCatalog, ConfigValues, ConfigSpecView } from "../api/types";

/** 配置页：全局与单台设备两级；控件形状由登记表驱动（kind/scope/边界来自服务端）。 */
export function ConfigPage() {
  const [deviceId, setDeviceId] = useState("");
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => configDevices() });
  const spec = useQuery<ConfigSpecCatalog>({ queryKey: ["config", "spec"], queryFn: configApi.spec });
  const global = useQuery<GlobalConfigView>({ queryKey: ["config", "global"], queryFn: configApi.global });
  const deviceConfig = useQuery({
    queryKey: ["config", "device", deviceId],
    queryFn: () => configApi.device(deviceId),
    enabled: deviceId !== "",
  });

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">目标</h2>
        <select
          className="rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          value={deviceId}
          onChange={(event) => setDeviceId(event.target.value)}
        >
          <option value="">全局</option>
          {(devices.data ?? []).map((device) => (
            <option key={device.device_id} value={device.device_id}>
              {device.name || device.device_id}
            </option>
          ))}
        </select>
      </section>

      {spec.data && global.data && (
        <ConfigForm
          key={`global-${global.data.revision}`}
          spec={spec.data}
          values={global.data.config}
          save={(config) => configApi.saveGlobal(config)}
        />
      )}

      {deviceId !== "" && deviceConfig.data && spec.data && (
        <ConfigForm
          key={`device-${deviceId}-${deviceConfig.data.revision}`}
          spec={spec.data}
          values={deviceConfig.data.config}
          save={(config) => configApi.saveDevice(deviceId, config)}
        />
      )}
    </div>
  );
}

async function configDevices() {
  const res = await fetch("/api/v1/devices");
  if (!res.ok) throw new Error("设备清单获取失败");
  return (await res.json()) as { device_id: string; name: string; online: boolean }[];
}

function ConfigForm({
  spec,
  values,
  save,
}: {
  spec: ConfigSpecCatalog;
  values: ConfigValues;
  save: (config: ConfigValues) => Promise<unknown>;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ConfigValues>({ ...values });
  const [message, setMessage] = useState("");

  const mutate = useMutation({
    mutationFn: save,
    onSuccess: () => {
      setMessage("已保存");
      void queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error) => setMessage(error instanceof ApiError ? error.message : "保存失败"),
  });

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => values[key] !== value),
  );

  const renderControl = (item: ConfigSpecView) => {
    const value = draft[item.key];
    const set = (next: number | boolean | string | Record<string, string>) =>
      setDraft((current) => ({ ...current, [item.key]: next }));
    if (item.kind === "bool") {
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => set(event.target.checked)}
        />
      );
    }
    if (item.kind === "int") {
      return (
        <input
          type="number"
          className="w-40 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          value={String(value ?? "")}
          min={item.minimum ?? undefined}
          max={item.maximum ?? undefined}
          onChange={(event) => set(Number(event.target.value))}
        />
      );
    }
    if (item.choices) {
      return (
        <select
          className="rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          value={String(value ?? "")}
          onChange={(event) => set(event.target.value)}
        >
          {item.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        className="w-64 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        value={String(value ?? "")}
        onChange={(event) => set(event.target.value)}
      />
    );
  };

  return (
    <section className="glass-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">
        配置<span className="ml-2 text-xs text-default-500">rev {values === values ? "" : ""}</span>
      </h2>
      {spec.sections.map((section) => {
        const groups = spec.groups.filter((group) => group.section === section.key);
        const items = (groupKey: string) => spec.specs.filter((item) => item.group === groupKey);
        return (
          <div key={section.key} className="mb-4">
            <h3 className="mb-2 text-xs font-semibold text-default-500">{section.title}</h3>
            {groups.map((group) => (
              <div key={group.key} className="mb-3 rounded-xl bg-content2/40 p-3">
                <div className="mb-2 text-xs font-medium">{group.title}</div>
                {items(group.key).map((item) => (
                  <div key={item.key} className="mb-2 flex items-center gap-3">
                    <label className="w-64 text-xs" title={item.description}>
                      {item.title}
                      {item.unit && <span className="ml-1 text-default-400">（{item.unit}）</span>}
                    </label>
                    {renderControl(item)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        );
      })}
      <Button
        size="sm"
        variant="primary"
        isPending={mutate.isPending}
        isDisabled={Object.keys(changed).length === 0}
        onPress={() => mutate.mutate(changed)}
      >
        保存改动
      </Button>
      {message && <p className="mt-2 text-xs text-default-500">{message}</p>}
    </section>
  );
}
