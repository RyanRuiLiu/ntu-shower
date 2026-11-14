import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // 开发期把 API 与设备网关代理到本地服务端；生产由 FastAPI 直接挂载 dist
      "/api": "http://127.0.0.1:8110",
      "/gateway": { target: "ws://127.0.0.1:8110", ws: true },
    },
  },
});
