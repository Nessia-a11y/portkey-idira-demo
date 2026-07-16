# PANW 产品助手

基于 AI 的 Palo Alto Networks 产品问答助手，支持直接对话和技能工具调用。

## 功能概述

- 直接对话解答 PANW 产品问题（Cortex、Strata、Prisma SASE、Prisma AIRS、Idira）
- 每条回复附带参考文档链接和准确性评分（0-10）
- 6 项技能工具（Datasheet 下载、内外部演示、SKU 计算、技术文档）
- MCP 协议扩展支持
- HTTPS 加密通信（Nginx + 自签证书）
- 双因素认证（密码 + 邮箱验证码）
- 域名白名单注册控制
- 管理员后台（用户管理、域名管理、登录审计）

## 技能列表

| # | 技能 | 函数名 | 权限 | 说明 |
|---|------|--------|------|------|
| 1 | Datasheet 下载 | `search_datasheet` | 全部 | 从 PANW 官网下载产品规格书（优先中文） |
| 2 | 内部演示 | `query_internal_demos` | 内部 | 查询 G-Drive 演示视频/幻灯片链接 |
| 3 | 外部演示 | `query_external_demos` | 外部 | 查询公开演示文件并提供下载 |
| 4 | SKU 计算 | `query_sku` | 内部 | 查询产品 SKU 及许可计算规则 |
| 5 | 技术文档 | `query_techdocs` | 全部 | 查询官方 TechDocs 和内部部署文档 |
| 6 | MCP 扩展 | `mcp_extension` | 全部 | 预留 MCP 服务扩展位 |

---

## 一键部署

### 前置要求

- VM（Ubuntu 22.04+ / Debian 12+）
- Docker + Docker Compose
- 有效的 Portkey API Key
- 公网 IP（入站 443/80，出站 587）

### 部署步骤

```bash
# 1. 克隆项目
git clone <repo-url> && cd PortkeyIdira

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少设置：
#   PORTKEY_API_KEY=你的key
#   ADMIN_TOKEN=管理密钥
#   INITIAL_ADMIN_EMAIL=首个管理员邮箱
#   SMTP_HOST / SMTP_USER / SMTP_PASS（邮件验证码）

# 3. 一键部署
chmod +x deploy.sh
./deploy.sh
```

部署脚本会自动完成：
1. 检测公网 IP
2. 生成自签 SSL 证书（10年有效）
3. 构建 App 容器镜像
4. 启动 App + Nginx 容器
5. 验证服务健康

部署成功后访问：**`https://<your-ip>`**

> HTTP :80 自动跳转 HTTPS :443。自签证书浏览器会提示"不安全"，点击"继续访问"即可，加密通道有效。

### 手动部署（无 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 编辑配置

# 生成证书
./nginx/generate-cert.sh <你的公网IP>

# 启动
uvicorn main:app --host 0.0.0.0 --port 3587 \
  --ssl-keyfile=nginx/certs/server.key \
  --ssl-certfile=nginx/certs/server.crt
```

---

## VM 配置推荐

### 最低配置（10 并发用户以内）

| 项目 | 规格 |
|------|------|
| CPU | 2 vCPU |
| 内存 | 4 GB RAM |
| 磁盘 | 20 GB SSD |
| 系统 | Ubuntu 22.04+ / Debian 12+ |
| 网络 | 入站：TCP 443, 80；出站：TCP 587（SMTP） |

### 推荐配置（50 并发用户）

| 项目 | 规格 |
|------|------|
| CPU | 4 vCPU |
| 内存 | 8 GB RAM |
| 磁盘 | 40 GB SSD |
| 系统 | Ubuntu 22.04+ |
| 网络 | 入站：TCP 443, 80；出站：TCP 587（SMTP） |

### 资源说明

- 本应用**不做 AI 推理**，所有推理通过 Portkey API 远程调用，VM 本身负载极低
- 主要资源消耗：Nginx + FastAPI 进程 + SQLite 数据库 + 文件存储
- 磁盘空间视上传演示文件大小增长，建议预留 20GB+
- HTTPS 已内置（Nginx 自签证书），无需额外配置

---

## 首次使用流程

1. **部署服务**
   ```bash
   ./deploy.sh
   ```

2. **管理员设置域名白名单**（通过 API，因为此时还没有管理员账号）
   ```bash
   curl -sk -X POST https://localhost/admin/domains \
     -F "token=你的ADMIN_TOKEN" -F "domain=yourcompany.com"
   ```

3. **管理员注册** — 浏览器访问 `https://<your-ip>`，用 `INITIAL_ADMIN_EMAIL` 对应邮箱注册

4. **重启服务** — 让 bootstrap 逻辑自动提升该用户为管理员
   ```bash
   docker compose restart
   ```

5. **后续管理** — 管理员登录后，界面顶部会出现"管理后台"入口，可以：
   - 添加/删除允许注册的域名
   - 管理用户（启用/禁用/提升管理员）
   - 查看登录日志和活跃会话
   - 管理技能数据（演示、SKU、文档）

> 普通用户看不到"管理后台"入口。

---

## SSL 证书管理

```bash
# 生成/更换自签证书（deploy.sh 首次自动执行）
./nginx/generate-cert.sh <你的公网IP或域名>

# 证书位置
nginx/certs/server.crt  # 证书文件
nginx/certs/server.key  # 私钥文件

# 更换后重启 Nginx 生效
docker compose restart nginx
```

证书有效期 10 年，支持 IP 地址和域名作为 Subject。

---

## 认证机制

| 步骤 | 说明 |
|------|------|
| 注册 | 邮箱域名必须在白名单内，设置密码（>=8位） |
| 登录 | 输入邮箱+密码 → 验证通过后发送6位验证码到邮箱 |
| 验证码 | 5分钟有效，5次错误锁定 |
| 密码 | 5次错误后锁定15分钟 |
| 会话 | httpOnly + secure cookie，7天有效 |

### SMTP 配置

如未配置 SMTP，验证码会打印到容器日志（开发模式）：

```bash
docker compose logs -f | grep OTP
```

生产环境建议配置 SMTP（Gmail App Password / SendGrid / 企业邮箱）。

---

## 数据备份与环境迁移

所有 RAG 知识库和用户数据都在 `data/` 目录中，通过 Docker volume 挂载持久化。

### 备份

```bash
# 仅备份数据（知识库 + 用户）
./backup.sh

# 完整打包（代码 + 配置 + 数据，可直接迁移）
./backup.sh --full
```

### 迁移到新 VM

```bash
# 在旧 VM 上
./backup.sh --full
# 生成：panw-helper-full-20260713_xxxxxx.tar.gz

# 传输到新 VM
scp panw-helper-full-*.tar.gz user@new-vm:~/

# 在新 VM 上
tar -xzf panw-helper-full-*.tar.gz
cd PortkeyIdira
vim .env  # 确认配置（新 IP 下证书会自动重新生成）
./deploy.sh
```

### data/ 目录内容

| 路径 | 内容 | 说明 |
|------|------|------|
| `data/auth.db` | SQLite 数据库 | 用户账号、密码、会话、域名白名单、登录日志 |
| `data/datasheets/` | PDF 文件 | 用户请求后自动下载的产品规格书缓存 |
| `data/internal_demos/` | JSON 索引 | 管理员维护的 G-Drive 演示链接 |
| `data/external_demos/` | 文件 + JSON 索引 | 管理员上传的公开演示文件 |
| `data/sku/` | JSON 文件 | SKU 计算规则 |
| `data/techdocs/` | JSON 文件 | 内部部署文档 |

> 迁移时只要完整复制 `data/` 目录，所有知识库、用户、配置即恢复。

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `PORTKEY_API_KEY` | 是 | Portkey 平台 API Key |
| `PORTKEY_VIRTUAL_KEY` | 否 | Portkey Virtual Key |
| `PORTKEY_CONFIG` | 否 | Portkey Config ID |
| `MODEL` | 否 | 模型名称（默认 `claude-opus-4-8`） |
| `ADMIN_TOKEN` | 是 | 技能管理 API 令牌 |
| `INITIAL_ADMIN_EMAIL` | 建议 | 首个管理员邮箱（注册后自动提升） |
| `SMTP_HOST` | 生产必填 | SMTP 服务器地址 |
| `SMTP_PORT` | 否 | SMTP 端口（默认 587） |
| `SMTP_USER` | 生产必填 | SMTP 用户名 |
| `SMTP_PASS` | 生产必填 | SMTP 密码/App Password |
| `SMTP_FROM` | 否 | 发件人地址 |
| `PORT` | 否 | App 内部端口（默认 3587，外部统一走 443） |

---

## 架构图

```
                    ┌──────────────────────────────────────┐
                    │            VM (公网 IP)               │
                    │                                      │
用户 ──HTTPS:443──→ │  Nginx (SSL termination)             │
                    │       ↓ proxy_pass                   │
                    │  App :3587 (FastAPI + Agent Loop)    │
                    │       ↓              ↓               │
                    │  技能工具          MCP 工具           │
                    │  (skills/)        (mcp_client.py)    │
                    │       ↓              ↓               │
                    │  本地数据          外部 MCP 服务       │
                    │  (data/)                             │
                    └──────────────────────────────────────┘
                              ↓ outbound
                    Portkey API (AI 推理)
                    SMTP (邮件验证码)
```

### 请求流程

```
用户浏览器
  → HTTPS :443 (Nginx SSL 终止)
    → HTTP :3587 (FastAPI)
      → 认证检查 (session cookie)
      → Agent Loop (Portkey API 调用)
        → 技能工具 / MCP 工具
      → 返回回复 (含参考链接 + 置信度评分)
```

---

## 安全审计总结

### 已实施的防护

| 防护项 | 状态 |
|--------|------|
| HTTPS 加密传输 | Nginx + 自签证书，HTTP 强制跳转 |
| Cookie 安全 | httpOnly + secure + SameSite=Lax |
| 密码存储 | PBKDF2-SHA256 + 随机 salt |
| 双因素认证 | 密码 + 邮箱 OTP |
| 密码暴力破解防护 | 5次错误锁定15分钟 |
| OTP 暴力破解防护 | 5次错误锁定（窗口期内） |
| OTP 重放防护 | 一次性使用，5分钟过期 |
| 路径穿越防护 | 文件下载接口严格校验 |
| 管理员权限控制 | 仅管理员可见后台入口 |
| 管理员自保护 | 不能撤销自己的管理员权限 |
| 重复上传处理 | 同名资源 upsert（覆盖更新） |

### 可选优化

| 项目 | 说明 |
|------|------|
| 数据库备份 | 建议 cron 定时备份 `data/auth.db` |
| 日志清理 | login_logs 持续增长，建议定期归档 |
| 用户身份自动判定 | 当前 user_type 用户自选，可改为基于域名自动判定 |
| 正式 CA 证书 | 如有域名可换 Let's Encrypt，消除浏览器警告 |

---

## 文件结构

```
PortkeyIdira/
├── main.py                 FastAPI 应用 + Agent 循环 + 认证 + Admin API
├── auth.py                 认证模块（注册/登录/OTP/会话/域名/管理员）
├── system_prompt.py        系统提示词（含参考链接和评分要求）
├── mcp_client.py           MCP 服务管理器
├── mcp_servers.json        MCP 服务配置
├── skills/
│   ├── skill_datasheet.py      技能1: PANW Datasheet 下载
│   ├── skill_internal_demos.py 技能2: 内部演示链接
│   ├── skill_external_demos.py 技能3: 外部演示文件
│   ├── skill_sku.py            技能4: SKU 计算
│   ├── skill_techdocs.py       技能5: 技术文档查询
│   └── skill_mcp_reserved.py   技能6: MCP 占位
├── static/
│   ├── index.html          对话界面（含登录/注册，中文）
│   └── admin.html          管理后台界面（中文）
├── nginx/
│   ├── nginx.conf          Nginx HTTPS 反代配置
│   ├── generate-cert.sh    自签证书生成脚本
│   └── certs/              证书目录（自动生成，git 忽略）
├── data/                   持久化数据（Docker volume 挂载）
├── deploy.sh              一键部署脚本
├── backup.sh              数据备份/迁移打包脚本
├── Dockerfile             App 容器构建文件
├── docker-compose.yml     编排配置（App + Nginx）
├── requirements.txt       Python 依赖
├── .env.example           环境变量模板
└── .gitignore             Git 忽略规则
```
