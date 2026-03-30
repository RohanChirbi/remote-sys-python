#!/usr/bin/env bash
set -e
mkdir -p certs
cd certs

echo "==> Generating CA..."
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
    -subj "/CN=HealthMonitorCA/O=Demo/C=IN"

echo "==> Generating server key and CSR..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
    -subj "/CN=localhost/O=Demo/C=IN"

echo "==> Signing server cert with SAN for both localhost and 127.0.0.1..."
openssl x509 -req -days 3650 -in server.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \
    -out server.crt

echo "==> Generating client key and CSR..."
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
    -subj "/CN=agent-client/O=Demo/C=IN"

echo "==> Signing client cert..."
openssl x509 -req -days 3650 -in client.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out client.crt

rm -f server.csr client.csr ca.srl
echo "Done. Certs ready in ./certs/"
```

The critical line is:
```
-extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1")