import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { otaApi } from "../api/queries";
import { ApiError } from "../api/client";
import { fmtFullDateTime } from "../format";

/** 发布页：版本列表（当前/回滚点）、上传新版本（multipart 直传）。 */
export function ReleasesPage() {
  const queryClient = useQueryClient();
  const releases = useQuery({ queryKey: ["ota", "releases"], queryFn: otaApi.releases });
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const upload = async (form: HTMLFormElement) => {
    setBusy(true);
    setMessage("");
    try {
      const data = new FormData(form);
      const result = await otaApi.upload(data);
      setMessage(`已发布 ${result.version}，已通知 ${result.notified_devices.length} 台在线设备`);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["ota", "releases"] });
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "发布失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (version: string) => {
    if (!window.confirm(`下架 ${version}？发布记录与 APK 文件一起删除。`)) return;
    try {
      await otaApi.remove(version);
      await queryClient.invalidateQueries({ queryKey: ["ota", "releases"] });
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "下架失败");
    }
  };

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">版本历史</h2>
        {(releases.data ?? []).map((release) => (
          <div key={release.version} className="mb-1 flex items-center gap-3 text-xs">
            <span className="font-mono text-sm">{release.version}</span>
            <span className="text-default-500">code {release.version_code}</span>
            <span
              className={`rounded-full px-2 py-0.5 ${
                release.force ? "bg-amber-500/20 text-amber-300" : "bg-indigo-500/20 text-indigo-200"
              }`}
            >
              {release.force ? "强制" : release.mandatory ? "必需" : "可选"}
            </span>
            <span className="text-default-500">{fmtFullDateTime(release.created_at)}</span>
            <Button size="sm" variant="danger-soft" onPress={() => remove(release.version)}>
              下架
            </Button>
          </div>
        ))}
      </section>

      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">上传新版本</h2>
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void upload(event.currentTarget);
          }}
        >
          <input name="version" className="w-32 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm" placeholder="版本号" required />
          <input name="version_code" type="number" className="w-24 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm" placeholder="版本号 code" required />
          <label className="flex items-center gap-1 text-xs">
            <input name="mandatory" type="checkbox" /> 必需
          </label>
          <label className="flex items-center gap-1 text-xs">
            <input name="force" type="checkbox" /> 强制
          </label>
          <input name="file" type="file" accept=".apk" required className="text-xs" />
          <Button size="sm" variant="primary" isPending={busy} type="submit">
            发布
          </Button>
        </form>
        {message && <p className="mt-2 text-xs text-default-500">{message}</p>}
      </section>
    </div>
  );
}
