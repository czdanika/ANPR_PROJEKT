FROM python:3.11-slim

WORKDIR /app

# Adatkönyvtárak előre létrehozva (volume mount ide kerül)
RUN mkdir -p /app/received_images

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 5555

CMD ["python", "app.py"]
