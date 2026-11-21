import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { devicesApi, doorsApi, jobsApi, sessionsApi } from "../api/queries";
import { fmtDuration, fmtFullDateTime } from "../format";

/** 门状态的语义色（与 DoorState 七值一一对应）。 */
const STATE_STYLE: Record<string, string> = {
  free: "text-emerald-300",
  selected: "text-sky-300",
  waiting: "text-amber-300",
  bathing: "text-indigo-300",
  paused: "text-orange-300",
  repair: "text-rose-300",
  disabled: "text-default-400",
};

const STATE_LABEL: Record<string, string> = {
  free: "空闲",
  selected: "已选中",
  waiting: "等待开阀",
  bathing: "淋浴中",
  paused: "已暂停",
  repair: "维修中",
  disabled: "已禁用",
};

export function OverviewPage() {
  const queryClient = useQueryClient();
  const [selectedDevice, setSelectedDevice] = useState<string>("");

  const devices = useQuery({ queryKey: ["devices"], queryFn: devicesApi.list });
  const doors = useQuery({ queryKey: ["doors"], queryFn: () => doorsApi.list(selectedDevice || undefined) });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: jobsApi.status, refetchInterval: 30_000 });

  const assign = useMutation({
    mutationFn: (value: { door_no: number; cert_no: string; password: string }) =>
      sessionsApi.assign(selectedDevice, value),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["doors"] }),
  });
  const settle = useMutation({
    mutationFn: (value: { cert_no: string; password: string }) =>
      sessionsApi.settle(selectedDevice, value),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["doors"] }),
  });

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <div className="mb-3 flex items-center gap-2">
          <select
            className="rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
            value={selectedDevice}
            onChange={(event) => setSelectedDevice(event.target.value)}
          >
            <option value="">全部设备</option>
            {(devices.data ?? []).map((device) => (
              <option key={device.device_id} value={device.device_id}>
                {device.name || device.device_id}
              </option>
            ))}
          </select>
          <span className="text-xs text-default-500">
            喷头 {doors.data?.length ?? 0} 个
          </span>
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {(doors.data ?? []).map((door) => (
            <div key={`${door.device_id}:${door.door_no}`} className="rounded-xl bg-content2/40 p-3">
              <div className="mb-1 flex items-baseline justify-between">
                <span className="font-mono text-sm">
                  {door.device_id} #{door.door_no}
                </span>
                <span className={`text-sm font-medium ${STATE_STYLE[door.state] ?? ""}`}>
                  {STATE_LABEL[door.state] ?? door.state}
                </span>
              </div>
              <div className="text-xs text-default-500">
                {door.cert_no || "无人使用"} · 累计 {fmtDuration(door.bath_time_s)}
                {door.last_used_at && ` · 上次 ${fmtFullDateTime(door.last_used_at)}`}
              </div>
            </div>
          ))}
        </div>
      </section>

      <JobBoard data={jobs.data} />

      {selectedDevice && (
        <section className="glass-panel p-4">
          <h2 className="mb-3 text-sm font-semibold">手动会话操作（目标：{selectedDevice}）</h2>
          <AssignForm busy={assign.isPending} onSubmit={(v) => assign.mutate(v)} />
          <SettleForm busy={settle.isPending} onSubmit={(v) => settle.mutate(v)} />
          {(assign.error || settle.error) && (
            <p className="mt-2 text-sm text-danger">
              {(assign.error ?? settle.error) instanceof Error
                ? (assign.error ?? settle.error)!.message
                : "操作失败"}
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function AssignForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (value: { door_no: number; cert_no: string; password: string }) => void;
}) {
  const [doorNo, setDoorNo] = useState("1");
  const [certNo, setCertNo] = useState("");
  const [password, setPassword] = useState("");
  return (
    <form
      className="mb-3 flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ door_no: Number(doorNo), cert_no: certNo, password });
      }}
    >
      <span className="text-sm text-default-500">分配：</span>
      <input
        className="w-20 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="喷头号"
        value={doorNo}
        onChange={(event) => setDoorNo(event.target.value)}
      />
      <input
        className="w-40 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="学号"
        value={certNo}
        onChange={(event) => setCertNo(event.target.value)}
      />
      <input
        className="w-40 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="口令"
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Button size="sm" variant="primary" isPending={busy} type="submit">
        分配
      </Button>
    </form>
  );
}

function SettleForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (value: { cert_no: string; password: string }) => void;
}) {
  const [certNo, setCertNo] = useState("");
  const [password, setPassword] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ cert_no: certNo, password });
      }}
    >
      <span className="text-sm text-default-500">结算：</span>
      <input
        className="w-40 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="学号"
        value={certNo}
        onChange={(event) => setCertNo(event.target.value)}
      />
      <input
        className="w-40 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
        placeholder="口令"
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Button size="sm" variant="ghost" isPending={busy} type="submit">
        结算
      </Button>
    </form>
  );
}

/** 任务看板：四任务开关与名单、下次执行倒计时、延长中设备明细。 */
function JobBoard({ data }: { data: Awaited<ReturnType<typeof jobsApi.status>> | undefined }) {
  if (!data) return null;
  const byJob = new Map<string, typeof data.scheduled>();
  for (const row of data.scheduled) {
    const list = byJob.get(row.job) ?? [];
    list.push(row);
    byJob.set(row.job, list);
  }
  return (
    <section className="glass-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">后台任务</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {data.jobs.map((job) => {
          const rows = byJob.get(job.key) ?? [];
          const nextAt = rows.map((row) => row.at).sort()[0];
          return (
            <div key={job.key} className="rounded-xl bg-content2/40 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-sm font-medium">{job.title}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    job.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-default-500/20 text-default-400"
                  }`}
                >
                  {job.enabled ? "启用" : "停用"}
                </span>
                <span className="text-xs text-default-500">名单 {job.students_count} 人</span>
              </div>
              {job.key === "power" ? (
                <HoldSection holds={data.holds} />
              ) : rows.length === 0 ? (
                <p className="text-xs text-default-500">当前没有排定的动作</p>
              ) : (
                rows.map((row) => (
                  <div key={`${row.device_id}:${row.door_no}:${row.session_id}`} className="text-xs text-default-500">
                    <span className="font-mono">{row.device_id}</span> #{row.door_no} {row.cert_no} · 下次动手{" "}
                    {fmtFullDateTime(row.at)} · 阈值 {fmtDuration(row.threshold_s)}
                  </div>
                ))
              )}
              {nextAt && job.key !== "power" && (
                <div className="mt-1 text-xs text-indigo-300">下次执行 {fmtFullDateTime(nextAt)}</div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function HoldSection({ holds }: { holds: NonNullable<Awaited<ReturnType<typeof jobsApi.status>>>["holds"] }) {
  if (holds.length === 0) {
    return <p className="text-xs text-default-500">当前没有设备在延长中</p>;
  }
  return holds.map((hold) => (
    <div key={hold.device_id} className="text-xs text-default-500">
      <span className="font-mono">{hold.device_id}</span> {hold.hold_source === "manual" ? "手动" : "自动"} hold · 已重分配{" "}
      {hold.reassigned_count}/{hold.total} · MCU 布防截止 {hold.arming_deadline || "—"}
      · 恢复条件 {hold.past_close && hold.idle ? "已齐，下一轮生效" : `${hold.past_close ? "恢复时刻已过" : "恢复时刻未到"} / ${hold.idle ? "喷头空闲" : "仍有会话"}`}
    </div>
  ));
}
