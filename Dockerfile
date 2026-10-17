# AI dashboard: tmux/batch control page (see README.md).
# The container's tmux must match the host's tmux server (`tmux -V`), because the
# client/server protocol must match to talk over the shared socket. Pick a base
# image whose tmux matches, via BASE_IMAGE in .env (ubuntu:22.04 ships 3.2a,
# ubuntu:24.04 ships 3.4, debian:12 ships 3.3a).
ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux python3 python3-qrcode \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py auth.py cron.py ./
COPY web/ ./web/

ENV PORT=8000 BASE_PATH=/ai LANG=C.UTF-8 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python3", "app.py"]
