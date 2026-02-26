FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir flask flask_httpauth paho-mqtt

EXPOSE 5555

CMD ["python", "app.py"]
