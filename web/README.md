# HardTech Writer Web

将 `write_article.py` CLI 工具封装为 Web 应用，供团队同事通过浏览器使用。

## 快速开始

### 本地开发

```bash
cd web
cp .env.example .env
# 编辑 .env 填入真实 API Key
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

浏览器访问 `http://localhost:8000`，输入 `.env` 中配置的 `ACCESS_KEY` 即可使用。

### Docker 部署

```bash
cd web
cp .env.example .env
# 编辑 .env 填入真实配置
docker-compose up -d --build
```

访问 `http://your-vps-ip`。

### VPS 部署步骤

1. 将代码推送到 GitHub
2. SSH 登录 VPS
3. `git clone` 仓库
4. `cd HardTech-Alert-V1/web`
5. `cp .env.example .env && nano .env` 填入配置
6. `docker-compose up -d --build`
7. （可选）配置 SSL：将证书放入 `web/certs/`，修改 `nginx.conf`

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `AI_BASE_URL` | 是 | AI API 地址 |
| `AI_API_KEY_CHEAP` | 是 | 低价模型 API Key |
| `AI_API_KEY_PREMIUM` | 是 | 高价模型 API Key |
| `AI_MODEL` | 是 | 默认模型名 |
| `AI_MODEL_BACKUP` | 是 | 备用模型名 |
| `JINA_API_KEY` | 是 | Jina 素材采集 Key |
| `ACCESS_KEY` | 是 | Web 访问密钥 |
| `EMAIL_HOST` | 否 | SMTP 服务器 |
| `EMAIL_PORT` | 否 | SMTP 端口 |
| `EMAIL_SENDER` | 否 | 发件人邮箱 |
| `EMAIL_PASSWORD` | 否 | 邮箱密码 |
| `EMAIL_RECEIVER` | 否 | 默认收件人 |

## 功能

- 关键词 / 大纲 / 思路 / 链接（至少填一项）
- 文风参考：选择已有风格 / 粘贴文本 / 粘贴链接
- SSE 实时日志（简洁 / 详细双模式）
- 成文在线编辑 + 复制
- 邮件发送（可改收件人）
- 素材来源链接展示
- 历史记录
- 模型切换
