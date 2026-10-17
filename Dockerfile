FROM python:3.12-slim

WORKDIR /api
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY src/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]