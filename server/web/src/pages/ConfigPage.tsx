import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { configApi, devicesApi } from "../api/queries";
import { ApiError } from "../api/client";
import type {
  ConfigSpecCatalog,
  ConfigSpecView,
  ConfigValues,
  DeviceConfigView,
} from "../api/types";

/** 配置页：全局与单台设备两级；控件形状由登记表驱动（kind/scope/边界来自服务端）。 */
export function ConfigPage() {
  const [deviceId, setDeviceId] = useState("");
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => devicesApi.list() });
  const spec = useQuery<ConfigSpecCatalog>({ queryKey: ["config", "spec"], queryFn: configApi.spec });
  const global = useQuery<GlobalConfigViewLike>({ queryKey: ["config", "global"], queryFn: configApi.global });
  const deviceConfig = useQuery<DeviceConfigView>({
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
        <GlobalForm
          key={`global-${global.data.revision}`}
          spec={spec.data}
          view={global.data}
        />
      )}

      {deviceId !== "" && deviceConfig.data && spec.data && global.data && (
        <DeviceForm
          key={`device-${deviceId}-${deviceConfig.data.revision}`}
          deviceId={deviceId}
          spec={spec.data}
          view={deviceConfig.data}
          globalValues={global.data.config}
        />
      )}
    </div>
  );
}

type GlobalConfigViewLike = { revision: number; config: ConfigValues };

function GlobalForm({
  spec,
  view,
}: {
  spec: ConfigSpecCatalog;
  view: GlobalConfigViewLike;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ConfigValues>({ ...view.config });
  const [message, setMessage] = useState("");

  const mutate = useMutation({
    mutationFn: (config: ConfigValues) => configApi.saveGlobal(config),
    onSuccess: () => {
      setMessage("已保存");
      void queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error) => setMessage(error instanceof ApiError ? error.message : "保存失败"),
  });

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => view.config[key] !== value),
  );

  return (
    <FormShell
      spec={spec}
      sections={spec.sections}
      draft={draft}
      setDraft={setDraft}
      globalValues={undefined}
      overrides={null}
      revision={view.revision}
      message={message}
      isPending={mutate.isPending}
      canSave={Object.keys(changed).length > 0}
      onSave={() => mutate.mutate(changed)}
    />
  );
}

function DeviceForm({
  deviceId,
  spec,
  view,
  globalValues,
}: {
  deviceId: string;
  spec: ConfigSpecCatalog;
  view: DeviceConfigView;
  globalValues: ConfigValues;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ConfigValues>({ ...view.config });
  const [message, setMessage] = useState("");

  const mutate = useMutation({
    mutationFn: (config: ConfigValues) => configApi.saveDevice(deviceId, config),
    onSuccess: () => {
      setMessage("已保存");
      void queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error) => setMessage(error instanceof ApiError ? error.message : "保存失败"),
  });

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => view.config[key] !== value),
  );

  const overrides = view.overrides ?? {};

  const revertKey = (key: string) => {
    if (!(key in globalValues)) return;
    setDraft((current) => ({ ...current, [key]: globalValues[key] }));
  };

  return (
    <FormShell
      spec={deviceSpec(spec)}
      sections={spec.sections.filter((section) => section.key === "device")}
      draft={draft}
      setDraft={setDraft}
      globalValues={globalValues}
      overrides={overrides}
      revision={view.revision}
      message={message}
      isPending={mutate.isPending}
      canSave={Object.keys(changed).length > 0}
      onSave={() => mutate.mutate(changed)}
      onRevert={revertKey}
    />
  );
}

/** 设备表单只含 DEVICE 键：SERVER 键不按设备区分，混进来会是存不了值的空输入。 */
function deviceSpec(spec: ConfigSpecCatalog): ConfigSpecCatalog {
  return {
    ...spec,
    specs: spec.specs.filter((item) => item.scope === "device"),
    groups: spec.groups.filter((group) =>
      spec.specs.some((item) => item.group === group.key && item.scope === "device"),
    ),
  };
}

function FormShell({
  spec,
  sections,
  draft,
  setDraft,
  globalValues,
  overrides,
  revision,
  message,
  isPending,
  canSave,
  onSave,
  onRevert,
}: {
  spec: ConfigSpecCatalog;
  sections: { key: string; title: string }[];
  draft: ConfigValues;
  setDraft: (update: (current: ConfigValues) => ConfigValues) => void;
  globalValues: ConfigValues | undefined;
  overrides: ConfigValues | null;
  revision: number;
  message: string;
  isPending: boolean;
  canSave: boolean;
  onSave: () => void;
  onRevert?: (key: string) => void;
}) {
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

  const isOverridden = (key: string): boolean =>
    overrides !== null && Object.prototype.hasOwnProperty.call(overrides, key);

  return (
    <section className="glass-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">
        配置
        <span className="ml-2 text-xs text-default-500">修订 {revision}</span>
        {overrides !== null && Object.keys(overrides).length > 0 && (
          <span className="ml-2 text-xs text-default-500">
            {Object.keys(overrides).length} 项单独设置，其余跟随全局
          </span>
        )}
      </h2>
      {sections.map((section) => {
        const groups = spec.groups.filter((group) => group.section === section.key);
        const items = (groupKey: string) =>
          spec.specs.filter((item) => item.group === groupKey);
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
                      {isOverridden(item.key) && (
                        <span className="ml-1 text-warning-400">已覆盖</span>
                      )}
                    </label>
                    {renderControl(item)}
                    {onRevert && isOverridden(item.key) && globalValues && item.key in globalValues && (
                      <Button
                        size="sm"
                        variant="secondary"
                        onPress={() => onRevert(item.key)}
                      >
                        {'恢复全局'}
                      </Button>
                    )}
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
        isPending={isPending}
        isDisabled={!canSave}
        onPress={onSave}
      >
        保存改动
      </Button>
      {message && <p className="mt-2 text-xs text-default-500">{message}</p>}
    </section>
  );
}
