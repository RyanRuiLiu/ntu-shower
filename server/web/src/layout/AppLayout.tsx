import { Outlet, useLocation, useNavigate } from "react-router";
import { Button } from "@heroui/react";
import { useEffect } from "react";

import { sessionActions, useSession } from "../session";

const NAV = [
  { to: "/", label: "概览" },
  { to: "/devices", label: "设备" },
  { to: "/inspector", label: "检查器" },
  { to: "/config", label: "配置" },
  { to: "/voice", label: "语音" },
  { to: "/users", label: "用户" },
  { to: "/releases", label: "发布" },
  { to: "/audit", label: "审计" },
];

/** 应用骨架：玻璃侧边栏 + 玻璃顶栏，内容区是唯一的滚动容器。 */
export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { hasSession } = useSession();

  useEffect(() => {
    if (!hasSession) navigate("/login", { replace: true });
  }, [hasSession, navigate]);

  return (
    <div className="flex min-h-screen">
      <aside className="glass-panel m-3 mr-0 flex w-48 shrink-0 flex-col gap-1 rounded-2xl p-3">
        <div className="px-3 py-4">
          <div className="text-lg font-semibold tracking-wide">NTU Shower</div>
          <div className="text-xs text-default-500">管理台</div>
        </div>
        {NAV.map((item) => {
          const active = location.pathname === item.to;
          return (
            <button
              key={item.to}
              type="button"
              onClick={() => navigate(item.to)}
              className={`rounded-xl px-3 py-2 text-left text-sm transition-colors ${
                active
                  ? "bg-indigo-500/20 text-indigo-200 shadow-[0_0_12px_rgb(99_102_241_/_0.25)]"
                  : "text-default-600 hover:bg-content2 hover:text-foreground"
              }`}
            >
              {item.label}
            </button>
          );
        })}
        <div className="mt-auto">
          <Button
            variant="danger-soft"
            size="sm"
            className="w-full"
            onPress={async () => {
              await sessionActions.logout();
              navigate("/login", { replace: true });
            }}
          >
            退出登录
          </Button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto p-3">
        <Outlet />
      </main>
    </div>
  );
}
