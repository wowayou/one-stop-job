import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// 应用图标单一事实源:src-tauri/icons/。dev 时经中间件按需读取,build 时输出到 dist 根目录;
// 不在前端仓库另存副本以免漂移。favicon 用 .ico，应用内侧栏图标用 128px PNG。
const ICON_DIR = new URL("../src-tauri/icons/", import.meta.url);
const APP_ASSETS: Record<string, { file: string; type: string }> = {
  "/favicon.ico": { file: "icon.ico", type: "image/x-icon" },
  "/app-icon.png": { file: "128x128.png", type: "image/png" },
};

function appIcons(): Plugin {
  const read = (name: string) => readFileSync(fileURLToPath(new URL(name, ICON_DIR)));
  return {
    name: "app-icons",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const asset = req.url ? APP_ASSETS[req.url] : undefined;
        if (asset) {
          res.setHeader("Content-Type", asset.type);
          res.end(read(asset.file));
          return;
        }
        next();
      });
    },
    generateBundle() {
      for (const [route, asset] of Object.entries(APP_ASSETS)) {
        this.emitFile({ type: "asset", fileName: route.slice(1), source: read(asset.file) });
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), appIcons()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000"
    }
  }
});
