# 安全演练平台 · 钓鱼演练系统

面向授权钓鱼演练与安全意识测试的平台：管理演练目标，为每个目标生成独立回传的 EXE。目标执行后，平台收集主机信息、屏幕截图与目录结构。

> 仅限在获得明确授权的演练环境中使用，请遵守当地法律法规与演练授权范围。

![仪表盘](README.assets/screenshot-dashboard.png)

## 功能特性

- 目标管理：单个添加、批量导入（TXT / CSV / XLSX）、分页、服务端搜索、独立 Token
- EXE 生成：基础 EXE + 目标配置注入，每个目标独立回传
- 数据采集：主机名、用户名、MAC、内外网 IP、屏幕截图、桌面与磁盘目录列表
- 数据管理：采集记录查看、截图预览、CSV 导出、数据导入与清理
- 界面：深色 / 浅色主题、响应式布局
- 部署：Windows 本地运行或 Linux + Docker 一键部署
- 扩展：LNK 投递、ClickFix 落地页等演练能力由私有扩展包提供，公开仓库不包含相关代码与依赖

## 快速开始 (本地运行)

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务（Windows 首次运行会在后台自动构建基础 EXE，约 1-3 分钟）
python run.py --host 0.0.0.0
```

启动后终端会输出管理面板地址（路径为随机生成的 10 位字符）：

```
管理面板:     http://0.0.0.0:5000/<随机路径>/
数据监听端口: 8080
```

默认账号密码：

```
用户名: fish
密码:   fishfish@123
```

- 管理面板默认端口 `5000`，回传监听默认端口 `8080`，可通过 `--manage-port` / `--listen-port` 调整
- 需确保防火墙放行这两个端口，且回传端口对目标主机可达

![登录界面](README.assets/screenshot-login.png)

## Docker 部署 (Linux)

```bash
# 克隆仓库
git clone https://github.com/Herbert-555/FUZZTeam_fish.git
cd FUZZTeam_fish

# 部署
bash install_fish.sh

# 使用国内 pip 镜像加速
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash install_fish.sh

# 自定义端口
FUZZTEAM_FISH_MANAGE_PORT=8000 FUZZTEAM_FISH_LISTEN_PORT=9000 bash install_fish.sh

# 查看管理面板地址（部署脚本也会直接打印）
docker logs drill-platform
```

> 国内服务器通常无法直连 Docker Hub，若部署时卡在拉取 `python:3.8-slim`，请先配置镜像加速：
>
> ```bash
> mkdir -p /etc/docker
> cat > /etc/docker/daemon.json <<'EOF'
> {"registry-mirrors": ["https://docker.m.daocloud.io"]}
> EOF
> systemctl restart docker
> ```
>
> 镜像源可用性会变化，如失效请替换为其他 Docker Hub 加速地址。

### 环境变量

| 变量                        | 默认值           | 说明             |
| --------------------------- | ---------------- | ---------------- |
| `FUZZTEAM_FISH_CONTAINER`   | `drill-platform` | 容器名称         |
| `FUZZTEAM_FISH_IMAGE`       | `drill-platform` | 镜像名称         |
| `FUZZTEAM_FISH_MANAGE_PORT` | `5000`           | 管理面板端口     |
| `FUZZTEAM_FISH_LISTEN_PORT` | `8080`           | EXE 回传数据端口 |
| `FUZZTEAM_FISH_DATA`        | `./data`         | 数据库持久化目录 |
| `FUZZTEAM_FISH_UPLOADS`     | `./uploads`      | 截图上传目录     |
| `FUZZTEAM_FISH_OUTPUT`      | `./output`       | 生成文件输出目录 |
| `PIP_INDEX_URL`             | 空               | pip 镜像源地址（也兼容 `FUZZTEAM_PIP_INDEX_URL`） |

## 系统使用

### 使用流程

1. 登录管理面板
2. 在「目标管理」中添加或批量导入目标
3. 在「EXE 配置」中设置图标、文件名模板、回传地址与行为选项
4. 在「EXE 生成」中为目标生成 EXE
5. 将生成的 EXE 分发给目标（邮件、网盘、U 盘等）
6. 目标运行后，在「采集数据」中查看回传的主机信息、截图与目录结构

### 目标管理

支持单个添加、批量导入（TXT / CSV / XLSX 模板），列表支持分页与服务端搜索。每个目标拥有独立 Token，用于区分回传数据。

![目标管理](README.assets/screenshot-targets.png)

### EXE 配置

统一配置基础信息：渲染图标、文件名模板（支持 `{token8}` 等占位符）、回传服务器地址与端口、弹窗提示、自毁等选项。

![EXE 配置](README.assets/screenshot-exe-config.png)

### 生成与分发

在「EXE 生成」中可单个或批量生成：

![生成页面](README.assets/screenshot-exe-generate.png)

生成完成后在「已生成文件」中下载。

### 采集数据

目标运行后，按回传时间展示主机名、用户名、内网 IP、出口 IP、截图与目录信息，支持批量导出与删除。

![采集数据](README.assets/screenshot-collections.png)

## 扩展包机制

平台在启动时检测项目根目录是否存在 `bypass/` 包：存在时自动加载扩展路由、模板、静态资源与依赖；不存在时以纯 EXE 演练版本运行。公开仓库通过 `.gitignore` 与 pre-push 钩子阻止 `bypass/` 目录被推送，克隆后执行 `bash .githooks/install.sh` 启用钩子。

## 架构

```
run.py                    # 启动入口（随机管理路径、双端口服务）
server/                   # 服务端
├── app.py                # Flask 应用工厂（管理端 + 回传端）
├── extensions.py         # 可选扩展包加载器
├── models.py             # SQLite 数据库模型
├── routes.py             # Web 路由 + 回传 API
├── exe_builder.py        # EXE 生成 (footer 注入 / PyInstaller)
├── config_manager.py     # 配置管理
├── icon_extractor.py     # 内置图标生成
├── pe_icon.py            # PE 图标注入
├── static/               # 样式、脚本、图标
└── templates/            # Jinja2 模板
client/
└── collector.py          # 客户端采集脚本 (被打包进 EXE)
```

## 技术要点

- **跨平台 EXE 生成**：Windows 上通过 PyInstaller 构建基础 EXE；Linux 上使用按图标预构建的 base EXE 变体，复制后追加目标配置，无需 Wine
- **配置注入**：EXE footer 中的配置使用 XOR 加密（密钥 `fishfish@aes`），每个目标独立 Token
- **客户端功能**：采集主机信息、截屏、目录扫描、弹窗提示、自毁删除
- **默认账号**：`fish` / `fishfish@123`

## 免责声明

本项目仅用于获得书面授权的钓鱼演练、红蓝对抗与安全意识培训。使用者应确保演练范围、目标与时间窗口均已获得授权，并对使用过程中产生的一切后果负责。禁止将本项目用于任何未经授权的攻击或非法用途。
