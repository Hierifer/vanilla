# 使用官方 Python 3.12 轻量级镜像
FROM crpi-goo4z49jdymjsvdp.cn-hangzhou.personal.cr.aliyuncs.com/nh_vanilla/python:3.12-slim-amd64

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
# 为了加快构建速度，使用了清华源，如果不需要可以去掉 -i 参数
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').getcode())" || exit 1

# 启动命令
CMD ["python", "main.py"]
