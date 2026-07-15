# 后端选择：Next.js 独立，还是 Python/FastAPI？

在决定前写下来，这样选择就是有意识的，而不是漂流。

---

## 正在做什么选择

`ARCHITECTURE_ZH.md` §4 中的接口契约是固定的。问题只是*谁来服务它*。三个选项：

| | A. Next.js 独立 | B. Next + FastAPI（代理） | C. Next + FastAPI（直连） |
|---|---|---|---|
| 浏览器连接到 | Next | 仅 Next | Next **和** FastAPI |
| 构建在 | Next 路由处理器 | Python | Python |
| 拥有模式 | Drizzle (TS) | SQLAlchemy (Python) | SQLAlchemy (Python) |
| CORS | 无 | 无 | 是，需要配置 |
| 跨越边界的认证 | 不适用 | 服务器间，无需转发 | 需要转发和验证 token |
| 可部署件 | 1 | 2 | 2 |
| 主机 | Vercel | Vercel + Railway/Render/Fly | Vercel + Railway/Render/Fly |
| 额外构建时间 | — | ~1.5–2 小时 | ~3 小时 |

**C 是要避免的。** 人们默认会选它，但它最贵：浏览器持一个 token，FastAPI 得验证它，你在第 6 小时调试 preflight。比 B 没多赚什么。

---

## B 的细节——值得构建的版本

诀窍是停止把 Next 当"前端"，而是把它当 **BFF**（后端即前端）：它仅仅拥有浏览器会话。Python 拥有产品。

```
                    ┌──────────────────────────────────┐
  浏览器 ──────────►│  Next.js  (Vercel)               │
   (仅 cookie)      │                                  │
                    │  • Google 登录 (Auth.js, JWT)    │
                    │  • 服务 UI                       │
                    │  • /api/* → 瘦代理              │
                    └───────────┬──────────────────────┘
                                │  服务器间
                                │  X-User-Id: <google sub>
                                │  X-Internal-Key: <共享秘密>
                                ▼
                    ┌──────────────────────────────────┐
                    │  FastAPI  (Railway / Render / Fly)
                    │                                  │
                    │  • 提供商注册 + 构建              │
                    │  • HTML 提取 + 重试              │
                    │  • 拥有所有产品表                │
                    └───────────┬──────────────────────┘
                                ▼
                        ┌───────────────┐
                        │   Postgres    │
                        └───────────────┘
```

### 让它便宜的三件事

**1. Auth.js 删掉它的数据库适配器。**

目前 Auth.js 用数据库会话并拥有四张表。在这个分割中，它切换到 **JWT 会话** — 不要适配器，不要表，不要 Postgres 访问。它做 Google OAuth 舞蹈，获得用户稳定的 Google `sub` 和邮箱，放进一个签名的 cookie。那就是它的全部工作。

Python 然后拥有*每一张*表，包括 `users`，用 Google `sub` 作键。一个模式，一个迁移工具，一种语言。这是比现在拥有的更清晰的分离，不是更混乱的。

**2. 浏览器永远不和 FastAPI 对话。**

Next 的 `/api/*` 路由变成了代理：读会话，把用户 id 和一个内部共享秘密作为 header 附加，转发给 FastAPI，流回响应。同源，所以没有 CORS，没有 preflight，没有第二个认证的东西。

FastAPI 信任 `X-User-Id` **仅仅是因为**它也验证了 `X-Internal-Key`，并且它在任何 UI 用的路径上都不可以从公网接触。那是整个跨越边界的认证故事——大概十五行。

```python
# api/deps.py
def current_user(
    x_user_id: str = Header(...),
    x_internal_key: str = Header(...),
) -> str:
    if not secrets.compare_digest(x_internal_key, settings.INTERNAL_KEY):
        raise HTTPException(401)
    return x_user_id          # 第一次见到时插入 users
```

**3. Docker Compose 吸收了额外服务。**

本地，"两个服务"只是你已经有的文件里多一块：

```yaml
services:
  db:   { … }                     # 不变
  api:  { build: ./api, … }       # FastAPI，端口 8000
  web:  { build: ., … }           # Next，端口 3000，API_URL=http://api:8000
```

`docker compose up` 仍然用一个命令启动整个东西。本地的故事不会变更差。

### 代价在哪儿

- **第二个生产部署。** Vercel 舒服地 host FastAPI 不太行。你需要 Railway、Render 或 Fly 作为 API，有它自己的 env vars 和它自己的 URL。第一次成功部署要预算一小时，加上必然的"本地管用"一轮。
- **第二个依赖树。** `requirements.txt`/`pyproject.toml` 并列着 `package.json`。两个 lockfile，如果你加 CI 两个 CI 路径。
- **延迟。** 每次调用多一跳网络。在 10-30 秒的模型调用旁边无关。

---

## Python 正经买来什么

对此要诚实，因为这是中心问题。

**它不让现在的功能集更好。** 调用 DeepSeek 的 OpenAI 兼容端点和插入两行在两种语言里一样简单。如果今天船出去的是最终状态，选项 A 在每个轴上赢。

**它给下一步这类产品去的地方买房间：**

- **多步智能体循环。** 生成 → 运行 → 读错误 → 修复，带 tool call。Python 的智能体工具（LangGraph、Pydantic AI、纯 asyncio）比 TS 等价物更成熟，这是自然的下个功能。
- **后台工作。** 构建是 10-30 秒的操作。现在它是个阻塞请求，很好。一旦变成一个有进度流的队列化工作，Python 有更好的答案（Celery、ARQ 或 asyncio 任务）比一个 Vercel 路由处理器，它有个硬的执行上限。
- **任何数值或 ML 附近的东西** — 评估生成的应用、嵌入和搜索过去的构建、对比模型输出。

**它也买了一个演示。** 这个角色是全栈。一个提交显示了有意识的服务边界、两种语言间的有类型契约和一个工作的 Compose 文件，演示了比显示一个单 Next 应用更多的工程表面 — *如果完成了*。一个未完成的分割在每个评分标准上都比一个完成的单体更差。

---

## 建议

**构建选项 B，但序列化它使分割是可逆的。**

洞察是接口契约是接缝。所以：

1. **先在 Next 上发货整个垂直切片**（脚手架已经这样做了）。提示 → 生成 → iframe → 保留 → 修改，端到端工作。
2. **然后把两个端点移植到 FastAPI** （`/generate`、`/projects`）在同样的契约后面，把 Next 的路由变成代理。

如果你在第 2 步超时了，你发货一个完整、工作的 Next 应用并在文档里说 Python 移植被界定了。那是一个防守住的、完成的提交。

如果你先构建分割然后超时了，你发货一个半导线的应用、跑不起来。那是上面的序列化存在防止的失败模式。

**规则：永远别让架构决策是未完成的那个东西。**

---

## 如果你承诺 Python，具体形状

```
api/
  main.py           FastAPI 应用，无 CORS，路由挂载
  deps.py           current_user() — 上面的 15 行
  models.py         SQLAlchemy：users、projects、versions
  schemas.py        Pydantic 请求/响应 — 镜像 §4
  providers.py      注册 (DeepSeek / OpenAI / OpenRouter / Anthropic)
  agent.py          generate_app()：调用、提取 HTML、一次严格重试
  routes/
    generate.py
    projects.py
    models.py
  alembic/          迁移
  pyproject.toml
```

`providers.py` 几乎逐字移植 — Python `openai` SDK 的 `base_url` 就和 TS 的一样，所以 DeepSeek/OpenAI/OpenRouter 仍然共享一个客户端，Anthropic 仍然得到第二个适配器。注册表模式跨越语言改变不变，这是它是对的抽象的一个不错的迹象。

前端根本不变。那就是这样写约定下来的整个意义。
