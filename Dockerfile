FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir \
    -r app/requirements-lock.txt

EXPOSE 8000

CMD ["python", "-m", "app.server"]