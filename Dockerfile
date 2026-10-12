FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "paho-mqtt>=2.0.0"

COPY bridge.py slot.py slotparse.py ./

CMD ["python", "bridge.py"]
