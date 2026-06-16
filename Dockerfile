FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5001

# WSGI de produção (waitress). store.init() roda no import e cria as tabelas.
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5001", "app:app"]
