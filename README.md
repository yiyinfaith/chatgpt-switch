# ChatGPT Switch

跨平台、本地运行的 ChatGPT / Codex 配置切换工具。当前文件夹中的 `ChatGPT Switch.exe` 是 Windows 版本；macOS / Linux 使用 `Start.command` 或 `start.sh`。Windows / Linux 桌面环境支持系统托盘快捷菜单。

## 备份

每次实际修改 `config.toml` 或 `.env` 时，程序把修改前的完整文件备份到软件目录内：

```text
<软件目录>\backups\config\
<软件目录>\backups\env\
```

每类文件只保留最近 5 份，最多 10 份。重复点击当前模式不会产生备份；旧版本旁边的 `config.toml.switch-backup-*` 会在首次运行时迁移到这里。其他名称的 `.bak` 不会删除。备份不会再保存到 `.codex`，软件目录移动时备份也随之移动。

## API 配置

底部「设置」→「API 配置」打开多套配置管理窗口。点击「新增配置」，填写配置名称（选填）、`base URL`、`model`、`env_key` 和 API 密钥；每次新建时 `env_key` 自动填入 `MY_API_KEY`，可以修改。保存后回到列表，可以继续新增或编辑多套配置。

主面板的「我的 API 配置」自动填满窗口剩余纵向空间，状态提示和底部按钮靠近窗口底边排列。面板高度只随窗口尺寸调整，不随配置数量增加；配置较多时仅在列表内部滚动。

列表使用单选勾选，每次只能选择一套。新增、编辑和勾选不会立即改动当前连接；点击「保存所选配置并生效」才写入该套服务地址、模型和密钥，按所选模式启用或注释 API 参数，并重启 ChatGPT。默认使用第三方 API，也可选择账号额度。配置库保存在软件目录 `data/profiles.json`，重新打开软件仍可使用；与主页「我的 API 配置」和托盘菜单共用同一份数据。

每套配置单独保存密钥，即使多套都使用 `MY_API_KEY`，切换时也会恢复各自的密钥。编辑时密钥留空会保留本套已保存的密钥，不会从另一套配置取值。`review_model`、`model_reasoning_effort`、`wire_api`、`requires_openai_auth` 仍是选填，空白保留当前设置。兼容旧配置库中未填写 `env_key` 或密钥的记录，这些旧记录应用时沿用当前变量名或 `.env` 中已有密钥。

如果目标 provider 缺少连接信息，会打开配置管理窗口，引导新增并应用配置。应用时自动补齐 `config.toml`，并在 `.codex/.env` 创建或更新所选变量。主页和托盘仍支持快捷应用已有配置。

## 安装与代理

从底部「便捷功能」→「安装与代理」进入，安装窗口左上角可返回便捷功能。

程序检测系统、发行版和架构，并支持不使用代理、跟随系统代理、手动 HTTP(S) 代理。代理只作用于本次安装，设置保存在软件目录 `data/preferences.json`，不会修改系统代理或保存账号密码。

- Windows：Codex CLI 使用 OpenAI 官方 PowerShell 安装脚本；ChatGPT 使用 WinGet 官方 Microsoft Store 包。
- macOS：Codex CLI 使用官方安装脚本；Apple Silicon 使用官方签名 DMG。Intel Mac 显示桌面端不适用，但仍可安装 CLI。
- Ubuntu / Debian：使用官方对应架构 `.deb` 和 `apt`。
- Fedora：使用官方对应架构 `.rpm` 和 `dnf`。
- Arch、Alpine、NixOS、Gentoo、Void 等没有本工具假定的官方 ChatGPT 桌面包，只提供 Codex CLI。Debian / Fedora 衍生版默认不安装兼容包，勾选后才尝试。

Linux 桌面端官方预览支持 Ubuntu 24.04 / 26.04、Debian 13、Fedora 43 / 44，x64 和 ARM64。没有适用桌面包时，程序不会误报安装成功。

### 组件更新

「安装与代理」底部新增独立的「组件更新」区。点击「检测更新」，查看当前版本、安装渠道和可用版本，勾选后点击「一键更新所选组件」。检测与更新都使用上方当前选中的代理方式，不需要先保存代理偏好。

- Codex CLI 先识别当前 PATH 选中的程序所属渠道：npm 使用对应全局安装前缀，WinGet 查询并升级 OpenAI.Codex，Scoop 使用原安装的官方 main bucket，Homebrew 使用原 formula/cask，官方独立安装版使用官方安装脚本更新。不会因为发现另一套包管理器，就把现有 CLI 换渠道覆盖。
- Windows 桌面端依据已注册的应用和 WinGet 商店记录检测、更新，核验包和已安装版本一致。其他 ChatGPT 商店发行版从 WinGet 查询其包 ID，不套用本机版本号或用户名。
- macOS 的 Homebrew codex-app 可通过原渠道更新；手动安装的桌面版提示应用内更新。Linux 系统包和不能可靠识别的安装方式显示原渠道提示，不执行猜测性的覆盖安装。
- 检测失败、无法识别渠道和无需更新分别显示；更新命令结束后还会核验已安装版本。只更新勾选的组件，不使用更新全部软件的命令。
- WinGet 的 `--proxy` / `--no-proxy` 在部分电脑上需要管理员启用 `ProxyCommandLineOptions`。功能未启用时会提示改选「跟随系统」或由管理员启用；工具不会静默忽略代理选择或修改系统代理。

### 便捷功能

主面板底部「便捷功能」左侧、便捷功能窗口内和托盘右键菜单均提供「打开 config.toml」，使用系统为 `.toml` 关联的默认应用打开当前用户的配置文件。三个入口使用程序当前绑定的同一路径；文件不存在时会创建空文件，已有内容和修改时间保持不变。此操作不会切换模式或重启 ChatGPT；原有「配置详情 → 打开配置文件夹」仍打开目录。

底部「便捷功能」集中提供「安装与代理」和创建桌面图标。Windows 可一键创建 `ChatGPT.lnk`：自动读取当前用户桌面（包括 OneDrive 重定向），通过注册 AppID 启动 ChatGPT，图标复制到工具的 `data/icons`，避免引用会随 Store 更新变化的 EXE 图标路径。已存在的同应用图标可修复；指向其他应用的同名快捷方式会保留并提示。

图标直接保留应用 EXE 内的完整多尺寸资源（当前 ChatGPT 包括 16–256 像素的八档图标和原始透明度），由 Windows 按桌面缩放选择合适尺寸。图标文件名含内容摘要，内容变化时更新快捷方式引用并通知 Explorer 刷新，避免继续显示旧的低清缓存。

## 启动和构建

Windows 使用原生无标题栏 WebView2 窗口：拖动顶部品牌或空白区域可移动，拖动四边和四角可调整大小，双击顶部可最大化/还原。右上角提供全屏、最小化、最大化/还原和关闭按钮；F11 切换全屏，Esc 退出全屏。最小化或收起到后台后重新运行程序会唤起原窗口。

「设置」中的「关闭主面板时」可选择「直接退出」或「在后台运行」，点击「保存设置」后持久保存到软件目录 `data/settings.json`。首次使用默认选择「在后台运行」；如果用户已经保存过选择，程序不会覆盖原来的设置。选择后台运行后，右上角 × 和 Alt+F4 都只隐藏窗口，程序与托盘继续运行。此设置与 API 配置、安装代理偏好独立保存。

「设置」新增「开机自启」开关，首次正常运行默认开启，旧版升级也会补上该默认值，并保留原来的关闭行为。关闭开关后点击「保存设置」，以后启动程序会继续保留关闭状态。Windows 写入当前用户的登录启动项（`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，名称 `ChatGPT Switch`）；macOS 使用用户 LaunchAgent，Linux 使用桌面自启动文件。无需管理员权限，自启只启动本工具，账号/API 模式保持不变。这里的开机自启是登录桌面后启动；Windows 任务管理器里的启动应用禁用状态仍由系统控制。

「开机自启」下面的「开机后仅后台运行」首次使用默认开启，旧版升级也会补上默认值。开启后，登录电脑时只显示托盘图标，不弹出主面板；关闭后，自启会正常显示主面板。手动双击程序始终打开或唤起主面板。关闭开机自启时，该选项暂时不可操作，但会保留选择；它与「关闭主面板时」的行为相互独立。托盘初始化失败时会显示主面板，避免程序隐藏后无法找回。

移动软件目录后，手动运行新位置的程序一次，即会更新启动路径。系统启动命令附带 `--autostart`，程序据此读取后台启动偏好；重复触发自启不会唤起已经运行的窗口。`--diagnose` 不注册自启；`--test-root` 使用测试目录中的模拟启动项，不改真实系统启动设置。保存失败会提示并恢复先前的启动项。构建临时目录在构建结束或失败后自动清理。

Windows 在前台和后台运行时都会显示托盘图标。右键菜单采用与主面板一致的淡紫、浅蓝渐变、圆角、线条图标和当前模式标记，提供「使用账号额度」「使用第三方api」「打开设置」「打开主面板」「退出程序」。左键点击图标也可唤起主面板；托盘打开面板时复用原窗口。托盘的「退出程序」始终结束程序，忽略后台运行偏好。操作进行中会禁用切换与退出，托盘未就绪时不会将窗口隐藏到无法找回的状态。

Windows 双击 `ChatGPT Switch.exe` 或 `Start.cmd`。macOS / Linux：

```sh
chmod +x start.sh Start.command
./start.sh
```

`start.sh` 使用系统 Python 3.9+。若没有 Python，请先用系统包管理器安装。重新构建当前平台（需要先安装 `requirements.txt` 中的依赖）：

```powershell
python build.py
```

macOS / Linux 可使用 `python3 -m pip install -r requirements.txt pyinstaller && python3 build.py`。发布版直接运行当前目录的 `ChatGPT Switch.exe`（Windows）或 `start.sh`（macOS / Linux）。

构建会预检 GUI 依赖，缺少 pywebview 等模块时在生成 EXE 前失败。可以用 `python build.py --deps <依赖目录> --dist <暂存目录>` 生成暂存版；`--diagnose` 只验证配置/环境读取，不证明 GUI 能加载。

回归检查可运行 `python -B -m unittest discover -s tests -v`。Windows 上的原生托盘测试会使用系统 .NET Framework C# 编译器，检查四周等距布局、100%～200% 缩放、反复打开菜单以及六个菜单动作；非 Windows 或缺少编译器时跳过该项。测试只创建临时菜单，不切换真实 API 配置。

## 动效与界面范围

主面板和「设置」「API 配置」「新增/编辑 API」「安装与代理」「便捷功能」「配置与备份」等子界面共用 `ui/motion.css`。按钮、主模式卡片、状态卡片、API 配置行、安装/更新行、设置选项、详情项、分段选择器、输入框、下拉框、复选框和单选框都提供一致的悬停、按下、聚焦反馈；禁用控件不会移动。两个主模式按钮的悬停效果也是上浮 2 像素，而不是只改变亮度。系统开启“减少动态效果”时，动画会自动降为近乎即时。

## 配置文件和环境变量

程序不会把 API 密钥写进仓库。运行时配置位于当前用户的 Codex 目录（Windows 通常是 `%USERPROFILE%\\.codex`，macOS/Linux 通常是 `~/.codex`）：

```text
~/.codex/config.toml   # provider、model_provider、base_url、env_key
~/.codex/.env          # env_key 对应的实际密钥
软件目录/data/profiles.json    # 本地 API 配置库，含加密前的本地密钥字段，不上传
```

最小的第三方 provider 示例（provider 名称可自定义）：

```toml
model = "your-model-id"
model_provider = "my_provider"

[model_providers.my_provider]
name = "My API"
base_url = "https://api.example.com/v1"
env_key = "MY_API_KEY"
```

对应的 `.env` 使用同一个变量名，等号两侧不要加额外说明文字：

```dotenv
MY_API_KEY=替换成你的真实密钥
```

`base_url` 必须是完整的 `http://` 或 `https://` 地址；`env_key` 必须是字母或下划线开头、只含字母/数字/下划线的变量名，例如 `MY_API_KEY`。不要把密钥写入 `config.toml`、`README.md`、截图、Issue 或提交记录。也不要使用 `PATH`、`HOME`、`USERPROFILE` 等系统变量名。推荐先复制一份 `.env` 作为离线备份，再通过页面「设置 → API 配置」保存和应用。

页面中的 `base URL`、`model`、`env_key` 和密钥字段会写入本机的 `data/profiles.json`；应用配置时，程序才会更新 `config.toml` 和 `.env`，并保留修改前的备份。编辑已有配置时密钥留空表示保留原密钥。`.env` 不存在时，应用配置会按填写的 `env_key` 创建它。

## 仓库目录分类

下面这些是应进入 GitHub **main 分支**的源码和项目文件：

```text
main.py                 # 程序入口
switcher/*.py           # 服务、配置、托盘、窗口和安装逻辑
switcher/native_*.cs    # 运行时编译的 Windows 原生窗口/托盘资源
ui/                     # HTML、CSS、JavaScript、图标
tests/                  # Python 与 Windows 原生托盘回归测试
assets/                 # 构建图标和 Windows 版本清单
build.py                # PyInstaller 构建入口
requirements.txt        # 构建/运行依赖
Start.cmd/start.sh/Start.command
README.md/.gitignore/.github/workflows/ci.yml
data/.gitkeep/backups/.gitkeep
```

下面这些是本机运行数据，**不应上传 main，也不应放进 Release**：

```text
data/*.json             # API 配置、设置、会话和活动 profile
data/browser/           # 浏览器用户数据和缓存
data/icons/             # 当前机器生成的快捷方式图标缓存
backups/                # config.toml/.env 的历史备份
~/.codex/config.toml
~/.codex/.env
```

其中 `config.toml`、`.env`、`data/profiles.json` 可能包含服务地址、账号信息或密钥；发布前应检查 Git 暂存区和 `git diff --cached`，确认没有任何真实配置。`.gitignore` 已覆盖这些运行数据，但仍要在提交前检查文件名和构建脚本生成的临时文件。

下面这些是 GitHub **Release 附件**，不要提交到 main：

```text
ChatGPT Switch.exe      # Windows x64 的 PyInstaller one-file 包
SHA256SUMS.txt          # Release 对应的校验值
```

Release 说明中应写明版本号、支持的 Windows 架构、构建日期、变更内容和 SHA-256。源码用户从 main 克隆后运行 `Start.cmd` 或 `start.sh`；普通 Windows 用户下载 Release 中的 `ChatGPT Switch.exe` 即可。EXE 不包含你的 `data/`、`backups/`、`config.toml` 或 `.env`。

## 贡献和发布检查

提交前运行：

```powershell
python -B -m unittest discover -s tests -v
node --check ui/app.js
node --check ui/features.js
node --check ui/settings.js
node --check ui/window.js
python -B -c "import ast,pathlib; files=[pathlib.Path('main.py'),pathlib.Path('build.py'),*pathlib.Path('switcher').glob('*.py'),*pathlib.Path('tests').glob('*.py')]; [ast.parse(p.read_text(encoding='utf-8'),filename=str(p)) for p in files]"
```

Windows 发布构建建议先输出到隔离目录，确认成功后再替换本地 EXE：

```powershell
python -B build.py --deps <依赖目录> --dist <暂存目录>
```

在替换前确认旧版进程已退出。构建后至少检查 EXE 的大小和 SHA-256，运行 `ChatGPT Switch.exe --diagnose <报告路径>`，再启动 EXE 检查 `/api/state` 中 `tray` 为 `ready`。托盘和 UI 的真实检查应覆盖主界面、四个子界面、二级 API 菜单、悬停上浮、按下回弹、输入框聚焦和“减少动态效果”模式。不要用包含真实密钥的工作目录执行测试，也不要把测试截图、`output/`、临时依赖目录或 `.playwright-cli/` 提交到仓库。
