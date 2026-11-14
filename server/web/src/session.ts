/**
 * 会话状态：hasSession 是登录态的唯一前端事实源。
 *
 * 服务端权威在会话 Cookie；这里只缓存一个布尔供守卫与布局读取。
 * 刷新与 401 都走 authApi.refresh。
 */

import { useSyncExternalStore } from "react";

import { authApi } from "./api/queries";
import { ApiError } from "./api/client";

let hasSession = false;
const listeners = new Set<() => void>();

function setSession(value: boolean) {
  hasSession = value;
  for (const notify of listeners) notify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 用服务端回答校准登录态：页面打开时调一次（React hook）。 */
export function useSessionRefresh() {
  return useSyncExternalStore(subscribe, () => hasSession, () => hasSession);
}

export function useSession() {
  return { hasSession: useSessionRefresh(), setSession };
}

export const sessionActions = {
  async refresh(): Promise<boolean> {
    try {
      await authApi.me();
      setSession(true);
    } catch (error) {
      setSession(false);
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
    }
    return hasSession;
  },
  async login(username: string, password: string) {
    await authApi.login(username, password);
    setSession(true);
  },
  async logout() {
    try {
      await authApi.logout();
    } finally {
      setSession(false);
    }
  },
};
