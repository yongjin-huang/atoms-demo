# atoms-demo 中文说明

## 是什么

用自然语言描述一个应用。智能体把它写成一个自包含的 HTML 文档。页面在沙箱 iframe 里实时运行。用户可以在原地迭代改进——每一版都被保留下来，还记录着用哪个模型生成的。

## 跑起来

```bash
# 准备
cp .env.example .env.local
npx auth secret                    # 写入 AUTH_SECRET
openssl rand -hex 32               # 粘贴到 INTERNAL_API_KEY
# 填入 AUTH_GOOGLE_ID / AUTH_GOOGLE_SECRET 和 DEEPSEEK_API_KEY

# 启动（选一个）
docker compose up                  # 用 Docker
npm install && npm run dev         # 或在你的机器上跑
```

- 应用：http://localhost:3000
- API 文档：http://localhost:8000/docs（Docker 下）

Google OAuth 本地回调 URI：
`http://localhost:3000/api/auth/callback/google`

## 架构

```
浏览器 ──cookie──► Next.js (BFF)  ──X-User-Id + X-Internal-Key──►  FastAPI  ──►  Postgres
                  · Google 登录                                    · 提供商
                  · 服务 UI                                       · 构建
                  · 代理 /api/*                                   · 拥有每张表
```

**Next 仅拥有会话。** Auth.js 用 JWT 会话跑，没有数据库适配器——它做 Google OAuth，把 `sub` claim 放进一个签名的 httpOnly cookie，完事。没有 Postgres 连接。

**Python 拥有产品。** 提供商注册、构建、HTML 提取、验证重试，三张表全拿。

**浏览器永远不和 FastAPI 对话。** 每个 `/api/*` 调用都经过 Next 代理，服务端转发。同源，所以无 CORS、无 preflight、无第二个认证东西。FastAPI 信任 `X-User-Id` 仅因为 `X-Internal-Key` 证明了请求来自 BFF。

如果你找自己在给 API 加 CORS 中间件，某个东西在从浏览器调用它，信任模型破了。

## 为什么是一份 HTML 文件

Atoms 级产品生成多文件项目并在容器或浏览器虚拟机里执行。大多数工作是基础设施。

把智能体限制在一份自包含的 HTML 文件（CSS 和 JS 内联）意味着"运行时"是一个 `<iframe srcdoc sandbox>` — 浏览器本身就是沙箱。没有容器操作。

**代价是实在的：没有 npm 包、没有服务器代码、没有多页面应用。** 这是有意识的权衡。它为一秒内看到一个真实的交互式应用买来了头条体验，代价是通用性。详见 `docs/ARCHITECTURE_ZH.md`。

## 布局

```
app/              Next: 页面 + /api/* 代理
components/       工作台 UI
lib/api.ts        接缝 — 唯一去 Python 的路径
auth.ts           Google 登录、JWT 会话、没有数据库
api/              FastAPI: 提供商、构建、持久化
  agent.py          调用模型、提取 HTML、一次严格重试
  providers.py      注册 — 每个模型一行
  models.py         SQLAlchemy：users、projects、versions
docs/             架构和后端决策
```

## 已知的缝隙

- **用 `create_all()` 而不是 Alembic 创建模式。** 演示可以；一个正式部署应该升级到 Alembic。
- 构建是非流式的 10-30 秒请求。
- 没有多文件生成，没有 npm 包。
- 没有分享 — 每个项目对它的所有者私密。

## 部署

### 在 Vercel 上

```bash
# 1. Vercel 上的 Next 应用（这个仓库的根）
#    会自动检测到 Next，构建成功
#    Env vars:
#      AUTH_SECRET
#      AUTH_GOOGLE_ID
#      AUTH_GOOGLE_SECRET
#      INTERNAL_API_KEY
#      API_URL=https://<你的-api>.up.railway.app

# 2. Railway 上的 FastAPI 应用 (api/ 目录)
#    创建一个新的 Railway 服务，部署这个仓库
#    设置根目录为 /api
#    Env vars:
#      DATABASE_URL
#      INTERNAL_API_KEY (必须和 Vercel 一样)
#      DEEPSEEK_API_KEY, OPENAI_API_KEY, …

# 3. Google Cloud
#    添加回调 URI:
#    https://<你的-app>.vercel.app/api/auth/callback/google
```

Vercel 自动处理 `AUTH_URL` 和主机信任，所以 Next 只要 `AUTH_SECRET`、Google key，还有 `API_URL` 指向 Railway。

### 在本地开发

Docker 时，数据库网络内的主机名是 `db`，API URL 对内部是 `http://api:8000`，对 Vercel 来说是公网的 Railway URL。Compose 处理好这些。

不用 Docker，自己启动 Postgres：

```bash
npm install
pip install -r api/requirements.txt
export DATABASE_URL="postgresql://user:pass@localhost/atoms"
export DEEPSEEK_API_KEY="..."
python -m uvicorn api.main:app --reload &
npm run dev
```

## 工作流

1. **键入提示**。任何语言、任何描述。
2. **模型回复**。先散文说它会做什么。
3. **可选：构建**。如果你问了一个应用，它在 `===APP===` 后发送代码。
4. **看到 HTML 流进去**。预览会等到 `<style>` 关闭再显示，避免看起来破碎。
5. **迭代**。问"加一个深色模式"，它会改进版本 1 → 版本 2。
6. **保存历史**。每个对话轮和它生成的应用都存在了，可以回到任何时刻。

## 模型选择

下拉菜单显示什么 API key 配置了。DeepSeek（默认）、OpenAI、Anthropic、OpenRouter 都可以。缺失的供应商干脆不出现。

每个版本记录了生成它的模型，所以你可以用 DeepSeek 构建 v1，用 Claude 构建 v2，同一个应用里对比。

## 安全性

- **生成的 HTML 是不可信的。** iframe 带 `sandbox="allow-scripts …"` 但没有 `allow-same-origin`，所以页面得到一个不透明原点、没法碰父文档。
- **项目 id 不是授权。** 每个读和写都在服务端检查所有权。
- **API key 只在服务端。** 浏览器永远看不到它们。

## 接口契约

如果你想替换任何层：

**GET /api/models** — 哪个模型现在可用
```json
{ "models": [...], "default": "deepseek/deepseek-chat" }
```

**POST /api/generate** — 构建或修改
```json
// 请求
{ "prompt": "一个番茄计时器", "modelId": "deepseek/deepseek-chat", "projectId": "uuid?" }
// 回应 (200)
{ "projectId": "uuid", "version": {...} }
```

**GET /api/projects** — 你的所有项目

**GET /api/projects/:id** — 一个项目和它的所有版本

完整的规范见 `docs/ARCHITECTURE_ZH.md` §4。
