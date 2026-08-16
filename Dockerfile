# 天大赛事通 后端 Docker 镜像（用于 Koyeb / Render / 任意容器平台）
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码（*.py + 配置）
COPY *.py ./
COPY .gitignore ./ 2>/dev/null || true

# 开放端口
EXPOSE 8000

# 启动（PORT 由平台注入，默认 8000）
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"]
