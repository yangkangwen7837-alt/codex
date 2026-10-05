# ===========================================================================
# 未来迁移腾讯云 / 自建服务器的备用镜像（不影响 Streamlit Community Cloud）
# 构建：docker build -t bigfish-agent .
# 运行：docker run -p 8501:8501 -e TUSHARE_TOKEN=xxx bigfish-agent
#       数据用卷挂载：-v D:\GPT\基本面反转交易策略\data:/app/data
# ===========================================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 数据目录（无数据时页面给出友好提示；生产建议用卷挂载）
RUN mkdir -p /app/data/raw /app/data/processed /app/output

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=4)"

# 真实入口是 apps/streamlit_app.py（项目里没有 app.py）
CMD ["streamlit", "run", "apps/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
