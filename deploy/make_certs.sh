#!/usr/bin/env bash
# Creates a private lab CA and a server certificate for the API's hostname/IP.
# Usage: deploy/make_certs.sh lab-api.local 192.168.1.50
# Output (in ./certs): ca.crt (copy to every client), server.crt, server.key (stays on the server).
set -euo pipefail
HOST="${1:?hostname, e.g. lab-api.local}"; IP="${2:-127.0.0.1}"
mkdir -p certs && cd certs
openssl req -x509 -newkey rsa:4096 -nodes -days 3650 -subj "/CN=Lab CA" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -keyout ca.key -out ca.crt 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=$HOST" -keyout server.key -out server.csr 2>/dev/null
printf "subjectAltName=DNS:%s,DNS:localhost,IP:%s,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n" "$HOST" "$IP" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile san.ext -out server.crt 2>/dev/null
chmod 600 ca.key server.key
rm server.csr san.ext ca.srl
echo "Created certs/ca.crt (distribute), certs/server.crt + server.key (server only). Keep ca.key offline."
