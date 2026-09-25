FROM python:3.13-slim
WORKDIR /app
COPY mirror/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mirror/*.py ./
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "main.py"]
CMD ["--config", "/config/config.json", "--daily"]
