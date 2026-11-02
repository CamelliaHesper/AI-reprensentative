# AI课代表 · 通知与申请材料助手

[GitHub 源码仓库](https://github.com/CamelliaHesper/AI-reprensentative)

把通知和附件整理成办理事项：选择申报类别、填写信息，人工确认后按原 Word 模板生成文件。

提供两种使用方式：**本地完整版本**和**在线体验 Demo**。本地版使用 FastAPI、LangChain、LangGraph 和 SQLite；在线版是 `online-demo/` 中独立的浏览器/Worker 实现，两者功能和数据不互通。

> **附加说明：** 受开发时间限制，当前在线 Demo 主要用于展示核心流程。后续计划将相关功能统一集成到飞书机器人并部署上线，让处于应用可用范围内、拥有机器人使用权限的用户直接通过飞书使用，无需自行部署本地服务。具体开放时间与功能范围以后续版本为准。

## 方式一：直接体验在线 Demo

[打开最新在线 Demo（WorkBuddy）](https://2117123839966674944.app.workbuddy.host/?sharecode=CHTW6F55E5)

无需安装、不需要填写 API Key。访问者使用维护者配置的共享模型。

**在线 Demo 目前仅提供网页内的材料办理流程，不支持读取飞书消息/附件、将生成文档回传飞书，也不支持整份 Word 在线编辑。** 需要这些功能时，请运行下方的本地完整版本：本地版可连接飞书读取消息和附件、回传生成文档，并在启动 Docker/ONLYOFFICE 后在线编辑 Word。

1. 粘贴通知正文，上传附件或 ZIP；也可以点击「体验示例」。
2. 检查解析出的事项，选择对应类别和模板。
3. 填写信息，核对内容及文件名，确认生成并下载 Word。

支持 DOC、DOCX、TXT、CSV、MD 和包含这些文件的 ZIP；混合 ZIP 中不支持的文件会单独提示。旧 DOC 在浏览器中转为 DOCX，首次约下载30MB引擎，建议新版电脑版 Chrome/Edge，转换后核对复杂排版。

在线版每份上传文件最多10MB，最多导入12份材料；ZIP解压后累计最多40MB。暂不支持 PDF、Excel填写、整份Word在线编辑或飞书回传。示例是预置分析，真实材料才调用模型。

材料和生成文件留在当前浏览器页面，刷新会清空；点击解析时，正文和附件提取文字会发送至模型服务。共享额度由维护者承担。维护者运行方法见 [在线版说明](online-demo/README.md)。

## 方式二：本地运行完整版本

本地版支持网页直接上传材料，也支持配置飞书后自动读取消息/附件和回传 Word；整份 Word 在线编辑需额外启动文档编辑服务。这些功能不包含在上面的在线 Demo 中。

### 环境与安装

- Windows，建议 Python 3.11 / 3.12。
- 处理旧 DOC、文档转换及 PDF 预览需安装可用的 Word/WPS；部分转换可使用 LibreOffice。
- 只有「在线编辑整份 Word」需要 Docker Desktop，基础流程不需要 Docker。

在项目根目录的 **PowerShell 终端**运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

填写模型配置，文件名必须为 `.env`，不能是 `.env.txt`：

```dotenv
MODEL_BASE_URL=模型服务的Chat-Completions基础地址
MODEL_API_KEY=你的密钥
MODEL_NAME=模型名称
```

启动网页：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode web
```

打开 **http://127.0.0.1:8000**，注册网页账号并登录，密码至少10位。保持终端运行；`Ctrl+C` 停止。修改 `.env` 后重启对应服务。

不接飞书时，直接点击「新建本地任务」，粘贴通知、上传附件，完成「浏览材料 → 选择事项 → 填写信息 → 确认生成 → 获取文件」。生成前必须人工确认；结果页可另存 Word、确认完成。

### 可选：接入飞书企业机器人

#### 1. 应用和测试企业

1. 在 [飞书开放平台](https://open.feishu.cn/) 创建**企业自建应用**并启用机器人能力；不是群内 Webhook 自定义机器人。
2. 在「测试企业和人员」创建/关联测试企业，添加测试人员。客户端切换到该测试企业，在企业内建群并添加机器人。
3. 凭证、权限、事件和群必须属于同一应用环境；测试应用与正式应用不要混用。正式环境修改按平台要求发布生效。

测试版应用的访问范围受创建者、测试人员等配置限制，应用创建者身份与另一个账号的测试资格不能混为一谈。添加人员出现「所属企业找不到该成员」时，先核对当前所属企业与该账号成员身份，不是填写网页账号，也不是使用企业邀请码代替测试人员配置。参见 [官方测试应用说明](https://open.feishu.cn/document/integrating-web-apps-in-5-minutes/step-7-experience-the-web-application)。

在根目录 `.env` 填写**实际使用环境的应用凭证**：

```dotenv
FEISHU_APP_ID=应用AppID
FEISHU_APP_SECRET=应用AppSecret
FEISHU_REPLY=true
WEB_URL=http://127.0.0.1:8000
```

这些不是飞书账号密码；`FEISHU_BOT_OPEN_ID` 通常留空。别人仅在网页体验 Demo，不需要加入你的测试企业。

#### 2. 权限与事件

在应用身份权限中开通以下能力，以后台显示的权限名称、当前接口要求及数据范围为准：

- 群内 @机器人消息：`im:message.group_at_msg:readonly`。
- 群内普通文件消息：`im:message.group_msg`；普通文件无法 @机器人，因此只有 @消息权限不足以完成上传流程。
- 文件资源：`im:resource`。
- 机器人发送消息/文件：`im:message:send_as_bot`。
- 读取关联消息：`im:message:readonly`。
- 发起人姓名：在通讯录权限中搜索「获取用户基本信息」等相关能力，并核对读取用户接口要求和可访问数据范围；姓名暂未获取不影响绑定。
- 单聊测试可选：`im:message.p2p_msg:readonly`。

另开项目终端启动长连接：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode feishu
```

进入「事件与回调」，选择**使用长连接接收事件**，订阅 **接收消息 v2.0：`im.message.receive_v1`**。保存订阅方式时需有客户端在线。不需要公网回调地址或内网穿透，网页和长连接进程都需保持运行。参见 [官方长连接说明](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-) 和 [消息接口说明](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1)。

#### 3. 绑定与群聊流程

1. 登录网页，在「个人资料与账号」获取绑定码，用本人飞书账号在群里 @机器人发送该码。支持直接验证码或「绑定＋验证码」，不要求固定空格；10分钟内有效、仅用一次，绑定消息不应创建任务。
2. @机器人发送完整通知，等待材料收集提示。
3. **回复该提示发送「开始上传」**，收到确认后将文件普通发送到当前群，可分多次上传。补充文字回复同一任务提示。
4. 收齐后回复「开始解析」，在网页选择事项、填写并确认生成。
5. 检查最新 Word，点击「发送到原群聊」，机器人回传附件。

上传会话按同一群、同一发送者匹配，有效期30分钟。每人分别注册和绑定网页账号，只能查看自己的任务、文件和资料；群聊中发送的附件仍对该群成员可见。

`127.0.0.1` 任务链接只能在运行服务的电脑打开。网页账号隔离不等于已完成公网部署，不要直接把本机端口开放到互联网。

### 可选：在线编辑整份 Word

启动 Docker Desktop（Linux容器模式），再运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-editor.ps1
```

等待 **http://127.0.0.1:8082/healthcheck** 返回 `true`。结果页点击「在线编辑整份 Word」，修改后「保存完成，返回结果页」；检查最新预览，再下载或发往飞书。放弃修改时使用「放弃本次编辑」。默认端口为网页8000、编辑桥接8001、ONLYOFFICE8082。

### 支持范围和配置

- 本地版读取 DOCX、常见DOC、PDF、XLSX、XLS、CSV、TXT及单层ZIP；当前生成流程以 Word 原模板填写为主，Excel填写生成和腾讯在线表单自动提交尚未实现。
- 扫描PDF需额外配置支持图片的 `VISION_BASE_URL`、`VISION_API_KEY`、`VISION_MODEL`，由视觉模型读取，不使用OCR；未配置会提示未读取。
- 本地默认单文件25MB，ZIP解压累计100MB / 100项。模型分析和填写助手会将相关材料发往配置的模型服务。
- 飞书及本地文档服务默认直连；`MODEL_PROXY_URL`、`VISION_PROXY_URL` 留空直连，只在服务商确实需要代理时填写。
- `data/` 保存数据库、附件和生成结果。分享代码勿附带 `.env`、`.venv/`、`data/` 或日志。

## 测试与验收

生成一套虚构通知、两类申请表、CSV和ZIP测试材料：

```powershell
.\.venv\Scripts\python.exe .\scripts\create_test_materials.py
```

材料生成在 `test-materials/`。已安装可用的 Word/WPS 时，可加 `--with-doc` 生成旧DOC及对应ZIP。

运行本地自动测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode test
```

完整操作用例、账号隔离测试、建议指标和结果记录见 **[测试与验收说明](docs/TESTING.md)**。自动测试不等于真实模型、飞书和在线编辑联调全部通过。

常见问题：模型未配置先检查 `.env` 并重启；机器人没回复检查应用环境、长连接和事件；附件未收到先「开始上传」并检查普通群消息/资源权限；在线编辑打不开先检查 Docker 和健康接口。在线Demo首次DOC转换需下载引擎，长时间停滞请记录提示阶段，不要反复上传或刷新造成材料丢失。

## 打包源码

在已安装本地依赖的项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\package_github.py
```

生成 `.release/AI-class-representative-source.zip`，按白名单打包源码，排除密钥、个人材料、运行数据、依赖目录和嵌套Git仓库，并检查是否意外包含本机已知密钥。包含DOC浏览器引擎，源码包约30MB；上传GitHub建议使用Git推送源码，不把整个ZIP作为代码文件提交。
