import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { devicesApi } from "../api/queries";
import type { DeviceListItem } from "../api/types";

/** 检查器：ui-dump 节点树 + ui-key/ui-input 注入，目标设备由顶部选择器决定。 */
export function InspectorPage() {
  const devices = useQuery({ queryKey: ["devices"], queryFn: devicesApi.list });
  const [deviceId, setDeviceId] = useState("");
  const [tree, setTree] = useState("");
  const [lastMessage, setLastMessage] = useState("");

  const dump = useMutation({
    mutationFn: () => devicesApi.uiDump(deviceId),
    onSuccess: (outcome) => {
      const xml = (outcome.data as { xml?: string } | null)?.xml ?? "";
      setTree(xml);
      setLastMessage("");
    },
    onError: (error) => setLastMessage(error instanceof Error ? error.message : "ui-dump 失败"),
  });

  const run = (action: () => Promise<unknown>, done: string) => {
    return action()
      .then(() => setLastMessage(done))
      .catch((error: unknown) => setLastMessage(error instanceof Error ? error.message : "操作失败"));
  };

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <select
          className="mr-2 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          value={deviceId}
          onChange={(event) => setDeviceId(event.target.value)}
        >
          <option value="">选择设备</option>
          {(devices.data ?? []).map((device: DeviceListItem) => (
            <option key={device.device_id} value={device.device_id}>
              {device.name || device.device_id}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="primary"
          isDisabled={!deviceId}
          isPending={dump.isPending}
          onPress={() => dump.mutate()}
        >
          读取节点树
        </Button>
        {lastMessage && <span className="ml-3 text-xs text-default-500">{lastMessage}</span>}
        {tree && (
          <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-black/40 p-3 font-mono text-xs">{tree}</pre>
        )}
      </section>

      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">按键注入</h2>
        <div className="flex flex-wrap gap-2">
          {["back", "home", "recents", "dpad_up", "dpad_down", "dpad_center", "enter", "del"].map((key) => (
            <Button
              key={key}
              size="sm"
              variant="ghost"
              isDisabled={!deviceId}
              onPress={() => run(() => devicesApi.uiKey(deviceId, key), `已发送 ${key}`)}
            >
              {key}
            </Button>
          ))}
        </div>
        <TextInputRow deviceId={deviceId} run={run} />
      </section>
    </div>
  );
}

function TextInputRow({
  deviceId,
  run,
}: {
  deviceId: string;
  run: (action: () => Promise<unknown>, done: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<"clipboard" | "keys">("clipboard");
  return (
    <form
      className="mt-3 flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void run(() => devicesApi.uiInput(deviceId, text, channel), "已输入文本");
      }}
    >
      <input
        className="w-72 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="要输入的文本"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <select
        className="rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        value={channel}
        onChange={(event) => setChannel(event.target.value as "clipboard" | "keys")}
      >
        <option value="clipboard">clipboard（支持中文）</option>
        <option value="keys">keys（仅 ASCII）</option>
      </select>
      <Button size="sm" variant="primary" type="submit" isDisabled={!deviceId}>
        输入
      </Button>
    </form>
  );
}
