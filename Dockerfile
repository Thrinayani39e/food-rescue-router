FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY frontend/ frontend/

ENV AWS_REGION=us-east-1
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "src.food_rescue_router.api:app", "--host", "0.0.0.0", "--port", "8080"]
