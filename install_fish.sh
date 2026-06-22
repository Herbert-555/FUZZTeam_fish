#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="${FUZZTEAM_FISH_CONTAINER:-fuzzteam-fish}"
IMAGE_NAME="${FUZZTEAM_FISH_IMAGE:-fuzzteam-fish}"
MANAGE_PORT="${FUZZTEAM_FISH_MANAGE_PORT:-5000}"
LISTEN_PORT="${FUZZTEAM_FISH_LISTEN_PORT:-8080}"
DATA_DIR="${FUZZTEAM_FISH_DATA:-$PROJECT_ROOT/data}"
UPLOADS_DIR="${FUZZTEAM_FISH_UPLOADS:-$PROJECT_ROOT/uploads}"
OUTPUT_DIR="${FUZZTEAM_FISH_OUTPUT:-$PROJECT_ROOT/output}"
PIP_INDEX_URL="${FUZZTEAM_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

log() { printf '[+] %s\n' "$*"; }
fail() { printf '[-] %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "未找到命令: $1"; }

need_cmd docker
[ -f "$PROJECT_ROOT/run.py" ] || fail "请在 FUZZTeam_getfish 项目根目录运行"
[ -f "$PROJECT_ROOT/requirements.txt" ] || fail "未找到 requirements.txt"

for d in "$DATA_DIR" "$UPLOADS_DIR" "$OUTPUT_DIR"; do
    mkdir -p "$d"
done

if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    log "删除旧容器: $CONTAINER_NAME"
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

log "构建 Docker 镜像: $IMAGE_NAME"
docker build \
    --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
    -t "$IMAGE_NAME" \
    "$PROJECT_ROOT"

log "启动容器: $CONTAINER_NAME"
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "0.0.0.0:$MANAGE_PORT:5000" \
    -p "0.0.0.0:$LISTEN_PORT:8080" \
    -v "$DATA_DIR:/FUZZTeam_getfish/data" \
    -v "$UPLOADS_DIR:/FUZZTeam_getfish/uploads" \
    -v "$OUTPUT_DIR:/FUZZTeam_getfish/output" \
    "$IMAGE_NAME" \
    --host 0.0.0.0

log "等待 Web 服务启动"
for _ in $(seq 1 60); do
    if docker logs "$CONTAINER_NAME" 2>&1 | grep -q "http://0.0.0.0:$MANAGE_PORT/"; then
        break
    fi
    sleep 2
done

log "===================================================="
log "FUZZTeam_fish 部署完成"
log "查看管理面板地址: docker logs $CONTAINER_NAME"
log "管理端口: $MANAGE_PORT"
log "数据监听端口: $LISTEN_PORT"
log "===================================================="
