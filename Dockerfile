FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Run as an unprivileged user; the only writable place is the /data volume.
RUN useradd --system --uid 10001 lab && mkdir /data && chown lab /data
USER lab
ENV LAB_DB=/data/lab.db
EXPOSE 8443
HEALTHCHECK CMD python -c "import ssl,urllib.request as u; c=ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE; u.urlopen('https://127.0.0.1:8443/health', context=c)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-certfile", "/certs/server.crt", "--ssl-keyfile", "/certs/server.key"]
