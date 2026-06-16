# 🚀 部署到 HuggingFace Spaces — 5 步操作手册

> **目标**: 把你本地仓库推上 HF Spaces, 拿到一个公网 URL 给 Lovable 前端用
> **预计时间**: 15-30 分钟
> **需要**: HF 账号 (huggingface.co, GitHub 注册即可)

---

## 步骤 1: 注册 + 创建 Space (2 分钟)

1. 去 https://huggingface.co/join 注册账号
2. 去 https://huggingface.co/new-space 创建新 Space
   - **Space name**: `green-logistics-ai` (URL 会是 `你的用户名-green-logistics-ai.hf.space`)
   - **License**: MIT
   - **SDK**: **Docker** ⚠️ 不是 Gradio / Streamlit
   - **Space hardware**: CPU basic (免费)
   - **Visibility**: Public
3. 点 Create

---

## 步骤 2: 准备推送代码 (3 分钟)

在项目根目录执行:

```bash
cd green-logistics-ai

# (可选) 清理大文件
rm -rf venv/ web/frontend/node_modules/ data/*.db cache/

# 添加 git remote (替换 你的用户名)
git remote add hf https://huggingface.co/spaces/你的用户名/green-logistics-ai

# 验证 remote
git remote -v
```

---

## 步骤 3: 配置环境变量 (2 分钟)

在 HF Space 页面 → **Settings** → **Variables and secrets**, 加:

| 名字 | 类型 | 值 | 说明 |
|------|------|-----|------|
| `GOOGLE_API_KEY` | secret | `AIza...你的key...` | Gemini LLM (从 .env 复制) |
| `GL_DB_PATH` | variable | `/data/simulation.db` | HF 持久化路径 |
| `EXTERNAL_SIGNALS_CACHE` | variable | `/data/cache` | 外部信号缓存 |
| `OSM_CACHE` | variable | `/data/osm_cache` | OSM 缓存 |

> 注意: `GOOGLE_API_KEY` 用 secret (加密), 其他用普通 variable

---

## 步骤 4: 推送代码 (5-10 分钟)

```bash
# 先 commit
git add Dockerfile .dockerignore README.md web/backend/main.py
git commit -m "deploy: HF Spaces Docker support"

# 推送 (第一次需要 HF 用户名密码 / token)
git push hf main
```

**可能遇到的问题**:
- `fatal: could not read Username`: HF 早期需要密码, 现在用 access token
  - 去 https://huggingface.co/settings/tokens 创建一个 (read + write 权限)
  - 推送时 Username = 你的 HF 用户名, Password = token 字符串
- `repository not found`: 检查 remote URL, 用户名大小写要匹配

---

## 步骤 5: 等构建 + 验证 (5-15 分钟)

1. HF Space 页面 → **Build** tab 看实时构建日志
2. 第一次构建要装 Python 依赖 (~5-10 分钟)
3. 看到 "Running" 状态后, **Logs** tab 应该有:
   ```
   INFO:     Started server process
   INFO:     Uvicorn running on http://0.0.0.0:8000
   INFO:     Application startup complete
   ```
4. 测一下:
   ```bash
   curl https://你的用户名-green-logistics-ai.hf.space/health
   # 期望: {"status":"healthy","timestamp":"..."}
   ```

5. 拿这个 URL 去 Lovable:
   - Lovable 项目 → Settings → API base URL
   - 改成: `https://你的用户名-green-logistics-ai.hf.space/api`
   - 保存, Lovable 会自动重新构建

---

## 🔧 常见问题

### 构建失败: `ModuleNotFoundError: No module named 'loguru'`
- 确认 `requirements.txt` 在仓库根目录
- HF 默认 `pip install -r requirements.txt`, 看 Build log

### 启动失败: `Address already in use`
- HF 强制用 8000 端口 (我们设的)
- 看 README.md 里 `app_port: 8000`

### CORS 错误 (Lovable 调 API 失败)
- 后端 main.py 已有 `allow_origins=["*"]`, 应该没问题
- 如果 Lovable 还报错, 在 HF Space Logs 看具体错误

### 数据丢失 (重启后 db 没了)
- 确认环境变量 `GL_DB_PATH=/data/simulation.db`
- HF `/data` 路径是持久化卷, 重启不丢
- 但**重建 (Rebuild) 会清空** — 注意 "Factory reboot" 按钮

### 冷启动慢 (30-60s)
- 免费 tier 长时间不用会进入 sleep
- 第一次访问会等 30s
- 论文里可以说"冷启动 30s"作为 trade-off

### 想看实时日志
- HF Space → Logs tab, 实时输出

---

## 📊 部署后验证清单

- [ ] HF Space 状态显示 "Running"
- [ ] `curl https://...hf.space/health` 返回 healthy
- [ ] `curl https://...hf.space/api/persistence/summary` 返回 30 cycles
- [ ] `curl https://...hf.space/api/optimize/pareto?n_points=5` 返回 5 点
- [ ] Lovable 前端配置 API URL, 刷新后不显示 "Demo mode"
- [ ] Lovable 显示真实数据 (4 supply / 4 demand / 6 vehicles 不见了, 看到 20/10/30)

---

## 💰 成本

| 项目 | 成本 |
|------|------|
| HF Space CPU basic | **免费** |
| Gemini API (Flash) | 几乎免费, <$1/月 |
| 域名 (可选) | hf.space 子域名免费 |
| **总成本** | **$0** |

---

## 🔄 后续更新代码

```bash
git push hf main
# HF 自动重新构建, ~5-10 分钟
```

---

## 🆘 卡住了?

发到群里 @LisLob3Bot, 我来 debug。常见问题:
- 构建 log 报错 (贴出来)
- 启动后 500 (贴 log)
- Lovable 调不通 (贴 Network 截图)
