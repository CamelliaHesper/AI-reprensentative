# AI课代表 · 在线 Demo

[最新在线 Demo（WorkBuddy）](https://2117123839966674944.app.workbuddy.host/?sharecode=CHTW6F55E5) · [完整项目说明](../README.md) · [测试与指标](../docs/TESTING.md)

> **附加说明：** 受开发时间限制，当前版本主要展示核心流程。后续计划将相关功能统一集成到飞书机器人并部署上线，让处于应用可用范围内、拥有机器人使用权限的用户直接通过飞书使用，无需自行部署本地服务。具体开放时间与功能范围以后续版本为准。

粘贴通知 → 上传DOC/DOCX或ZIP → 选择事项 → 填写信息 → 人工确认 → 下载Word。网页使用维护者提供的共享模型，不接飞书，不要求体验者填写Key；「体验示例」使用预置分析。

**此在线 Demo 不支持飞书消息/附件读取、飞书文档回传和整份 Word 在线编辑。** 本地完整版本支持这些功能：飞书能力需配置应用并启动长连接；在线编辑需启动 Docker/ONLYOFFICE。下载 Word 后，在线 Demo 用户仍可在自己电脑上的 Word/WPS 中修改。

## 使用范围

- 支持DOC、DOCX、TXT、CSV、MD及混合ZIP；不支持的成员逐文件提示，不影响其余成员。
- DOC使用浏览器端LibreOffice转换为DOCX。首次约下载30MB，建议新版桌面Chrome/Edge和约1GB可用内存。转换后检查复杂表格、字体和分页。
- 每份上传最多10MB、最多12份材料；ZIP累计解压最多40MB，单个解压成员最多12MB。
- 不提供PDF解析、Excel填写、ONLYOFFICE整页编辑、飞书回传或在线表单提交；完整本机功能见上级README。
- 材料仅保留在当前页面，刷新会清空；分析会将正文和附件提取文字发给配置的模型服务。

## 开发者本地运行

需要Node.js 22.13+。在此目录运行：

```powershell
npm install
npm run dev
```

打开终端打印的地址，默认 **http://localhost:5173**。这运行的是独立的Demo源码，不是上级FastAPI服务。

「体验示例」无需配置模型。解析真实材料需由维护者配置 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`。当前本地构建配置支持本目录的 `model.env.local`，该文件已忽略，不能提交；修改后重启/重建。公开体验地址由维护者统一管理凭证，不要把本机根目录 `.env` 整份拷贝到公开目录。

构建与预览：

```powershell
npm run build
npm start
```

生产预览默认端口8787，以终端输出为准。发布使用本项目的Sites发布流程；仅执行 `npm run build` 不会自动更新公网Demo。当前线上配置和本地配置不会自动同步。

DOC引擎需要HTTPS或localhost、Service Worker及跨源隔离响应头。部署须保留 `worker/index.ts` 中的COOP/COEP设置、`public/office-sw.js` 和完整 `public/office/` 资源。引擎来源及许可证见 [NOTICE](public/office/NOTICE.txt)。

## 自动检查

```powershell
node --experimental-strip-types scripts/check-demo.mjs
node --experimental-strip-types scripts/check-uploads.mjs
node --experimental-strip-types scripts/check-conversion-timeout.mjs
node node_modules/typescript/bin/tsc --noEmit
```

这些检查覆盖ZIP、接口校验和超时等逻辑，不能替代真实浏览器DOC转换、弱网络测试或模型质量验收。
