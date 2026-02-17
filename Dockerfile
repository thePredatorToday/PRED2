FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# B8: Opravena cesta – main.py je v root PREDATOR, ne scripts/
CMD ["python", "main.py"]
