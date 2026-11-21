/**
 * 实时推送（WS /api/v1/stream）：door.update 与 device.update 直接打进 TanStack
 * Query 缓存，REST 轮询只是补充。连接断开自动重连（指数退避），消息类型与
 * 服务端 events 契约一致。
 */

import { type QueryClient } from "@tanstack/react-query";

type StreamMessage =
  | { type: "door.update"; payload: { device_id: string; doors: Record<string, unknown>[] } }
  | { type: "device.update"; payload: Record<string, unknown> & { device_id: string } }
  | { type: "ota.upgrade_available"; payload: Record<string, unknown> }
  | { type: string; payload?: Record<string, unknown> };

let socket: WebSocket | null = null;
let retry = 0;
let timer: ReturnType<typeof setTimeout> | null = null;

export function connectStream(queryClient: QueryClient): void {
  if (socket) return;
  const open = () => {
    const protocol = location.protocol === "https:" ? "wss://" : "ws://";
    socket = new WebSocket(`${protocol}${location.host}/api/v1/stream`);

    socket.onopen = () => {
      retry = 0;
    };

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data as string) as StreamMessage;
      if (message.type === "door.update" && message.payload) {
        const payload = message.payload as { device_id: string; doors: Record<string, unknown>[] };
        queryClient.setQueryData<Record<string, unknown>[]>(["doors"], (old) => {
          const byKey = new Map((old ?? []).map((door) => [`${door.device_id}:${door.door_no}`, door]));
          for (const door of payload.doors) {
            byKey.set(
              `${(door as { device_id: string }).device_id ?? payload.device_id}:${(door as { door_no: number }).door_no}`,
              door,
            );
          }
          return [...byKey.values()];
        });
      } else if (message.type === "device.update" && message.payload) {
        const payload = message.payload;
        queryClient.setQueryData<Record<string, unknown>[]>(["devices"], (old) => {
          const next = (old ?? []).map((device) =>
            device.device_id === payload.device_id ? { ...device, ...payload } : device,
          );
          return next;
        });
      }
    };

    socket.onclose = () => {
      socket = null;
      const delay = Math.min(1000 * 2 ** retry, 30000);
      retry += 1;
      timer = setTimeout(() => connectStream(queryClient), delay);
    };
  };

  open();
}

export function closeStream(): void {
  if (timer) clearTimeout(timer);
  socket?.close();
  socket = null;
}
