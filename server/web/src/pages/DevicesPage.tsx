import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { devicesApi } from "../api/queries";
import { ApiError } from "../api/client";
import { fmtDuration, fmtFullDateTime } from "../format";

/** 设备页：档案表（单设备完整档案按需取）+ 远程操作（命令结果行内展示）。 */
export function DevicesPage() {
  const devices = useQuery({ queryKey: ["devices"], queryFn: devicesApi.list });
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">设备清单</h2>
        <div className="grid gap-2">
          {(devices.data ?? []).map((device) => (
            <div key={device.device_id} className="rounded-xl bg-content2/40 p-3">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm">{device.device_id}</span>
                <span className="text-sm">{device.name}</span>
                <span
                  className={`ml-auto rounded-full px-2 py-0.5 text-xs ${
                    device.online ? "bg-emerald-500/20 text-emerald-300" : "bg-default-500/20 text-default-400"
                  }`}
                >
                  {device.online ? "在线" : "离线"}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onPress={() => setExpanded(expanded === device.device_id ? null : device.device_id)}
                >
                  {expanded === device.device_id ? "收起" : "详情"}
                </Button>
              </div>
              {expanded === device.device_id && <DeviceDetail deviceId={device.device_id} />}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

/** 单设备详情：完整档案 + 远程操作。命令结果行内展示。 */
function DeviceDetail({ deviceId }: { deviceId: string }) {
  const queryClient = useQueryClient();
  const runtime = useQuery({
    queryKey: ["device", deviceId],
    queryFn: () => devicesApi.runtime(deviceId),
  });
  const [lastOutcome, setLastOutcome] = useState<string>("");

  const run = (action: (id: string) => Promise<unknown>) => {
    return action(deviceId)
      .then(() => setLastOutcome(`${new Date().toLocaleTimeString()} 命令已执行`))
      .catch((error: unknown) =>
        setLastOutcome(
          `${new Date().toLocaleTimeString()} 失败：${error instanceof ApiError ? error.message : String(error)}`,
        ),
      )
      .finally(() => void queryClient.invalidateQueries({ queryKey: ["devices"] }));
  };

  return (
    <div className="mt-3 border-t border-white/5 pt-3">
      {runtime.data && (
        <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs md:grid-cols-3">
          <span>版本：{runtime.data.app_version}（{runtime.data.version_code}）</span>
          <span>系统：{runtime.data.os_version}</span>
          <span>信任：{runtime.data.trusted ? "已信任" : "未信任"}</span>
          <span>IP：{runtime.data.last_ip}</span>
          <span>最后上报：{fmtFullDateTime(runtime.data.last_seen_at)}</span>
          <span>钟偏差：{runtime.data.clock_skew_s.toFixed(2)}s</span>
          <span>已运行：{fmtDuration(runtime.data.booted_at ? runtime.data.clock_skew_s : 0)}</span>
          <span>开机：{fmtFullDateTime(runtime.data.booted_at)}</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" onPress={() => run(devicesApi.capture)}>
          截图
        </Button>
        <Button size="sm" variant="ghost" onPress={() => run(devicesApi.restartApp)}>
          重启应用
        </Button>
        <Button size="sm" variant="ghost" onPress={() => run(devicesApi.restartBath)}>
          重启浴室 App
        </Button>
        <Button size="sm" variant="danger-soft" onPress={() => run(devicesApi.reboot)}>
          重启设备
        </Button>
      </div>
      {lastOutcome && <p className="mt-2 text-xs text-default-500">{lastOutcome}</p>}
    </div>
  );
}
