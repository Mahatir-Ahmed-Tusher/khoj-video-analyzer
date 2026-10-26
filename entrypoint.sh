#!/usr/bin/env bash
# ==============================================================================
# Khoj Video Analyzer - Container Entrypoint
# Initializes the local Deno bgutil PO-Token server for YouTube anti-bot bypass
# and starts the FastAPI service via Uvicorn.
# ==============================================================================
set -e

POT_PORT=4416

if [ -d "/opt/bgutil/server" ] && command -v deno >/dev/null 2>&1; then
    echo "--> Starting local bgutil PO-Token server on port ${POT_PORT}..."
    (
        cd /opt/bgutil/server
        exec deno run --allow-env --allow-net --allow-ffi=. --allow-read=. src/main.ts
    ) >/tmp/bgutil.log 2>&1 &
    
    # Wait up to 15s for the PO-token server to be ready
    for i in $(seq 1 30); do
        if (exec 3<>"/dev/tcp/127.0.0.1/${POT_PORT}") 2>/dev/null; then
            exec 3>&- 3<&-
            echo "--> PO-Token server successfully online on port ${POT_PORT}."
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "--> Notice: PO-Token server start timed out (check /tmp/bgutil.log). Proceeding with startup."
        fi
        sleep 0.5
    done
else
    echo "--> Running without local PO-Token daemon (standard environment)."
fi

echo "--> Starting FastAPI service on port ${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
