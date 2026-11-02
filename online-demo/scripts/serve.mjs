// 启动本地/托管的 worker 预览服务。
// 读取 PORT（默认 8787）并绑定 0.0.0.0，避免 wrangler 默认只监听 127.0.0.1 或写死端口。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const configPath = path.join(projectRoot, "dist", "server", "wrangler.json");
const port = process.env.PORT || "8787";
const ip = process.env.HOST || "0.0.0.0";

if (!existsSync(configPath)) {
  console.error("dist/server/wrangler.json 不存在，请先运行 npm run build。");
  process.exit(1);
}

// 本地回环请求不经过容器/系统代理，否则 worker 会因为代理无法连接上游而返回 502。
const noProxy = ["127.0.0.1", "localhost", "::1"].join(",");
const env = {
  ...process.env,
  NO_PROXY: [process.env.NO_PROXY, noProxy].filter(Boolean).join(","),
  no_proxy: [process.env.no_proxy, noProxy].filter(Boolean).join(","),
};

const wrangler = path.join(projectRoot, "node_modules", "wrangler", "bin", "wrangler.js");
const child = spawn(
  process.execPath,
  [
    wrangler,
    "dev",
    "--config",
    configPath,
    "--local",
    "--persist-to",
    path.join(projectRoot, ".wrangler", "state"),
    "--ip",
    ip,
    "--port",
    String(port),
    "--inspector-port",
    "0",
  ],
  { cwd: projectRoot, env, stdio: "inherit" },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => process.exit(code ?? 0));
