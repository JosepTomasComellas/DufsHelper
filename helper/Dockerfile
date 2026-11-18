FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Corre com a root: necessita el socket Docker i escriure a /data (carpetes) i /state.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:8080'+os.environ.get('BASE_PATH','/helper')+'/health')"
ENTRYPOINT ["python", "-m", "app"]
CMD ["serve"]
