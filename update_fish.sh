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
STOP_TIMEOUT="${FUZZTEAM_FISH_STOP_TIMEOUT:-30}"
BACKUP_DIR="${FUZZTEAM_FISH_BACKUP:-$PROJECT_ROOT/backups}"
BACKUP_ENABLED="${FUZZTEAM_FISH_BACKUP_ENABLED:-true}"
SERVER_IP_URL="${FUZZTEAM_FISH_SERVER_IP_URL:-ifconfig.me}"

log() { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
fail() { printf '[-] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "未找到命令: $1"; }

need_cmd docker
[ -f "$PROJECT_ROOT/run.py" ] || fail "请在 FUZZTeam_getfish 项目根目录运行"
[ -f "$PROJECT_ROOT/requirements.txt" ] || fail "未找到 requirements.txt"

for d in "$DATA_DIR" "$UPLOADS_DIR" "$OUTPUT_DIR"; do
    mkdir -p "$d"
done

# --- Backup ---
CONTAINER_EXISTS=false
if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    CONTAINER_EXISTS=true
fi

if [ "$BACKUP_ENABLED" = "true" ] && [ "$CONTAINER_EXISTS" = true ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"
    mkdir -p "$BACKUP_PATH"
    for f in database.db exe_config.json exe_registry.json; do
        if [ -f "$DATA_DIR/$f" ]; then
            cp "$DATA_DIR/$f" "$BACKUP_PATH/$f"
        fi
    done
    log "已备份数据到: $BACKUP_PATH"

    # 保留最近5份备份
    BACKUP_COUNT=$(ls -1d "$BACKUP_DIR"/*/ 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 5 ]; then
        ls -1dt "$BACKUP_DIR"/*/ | tail -n +6 | while read -r old; do
            rm -rf "$old"
        done
    fi
fi

# --- 停止旧容器 ---
if [ "$CONTAINER_EXISTS" = true ]; then
    if docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
        log "正在停止容器: $CONTAINER_NAME (超时: ${STOP_TIMEOUT}s)"
        if ! docker stop -t "$STOP_TIMEOUT" "$CONTAINER_NAME" >/dev/null 2>&1; then
            warn "容器未在 ${STOP_TIMEOUT}s 内停止，尝试强制结束"
            docker kill "$CONTAINER_NAME" >/dev/null 2>&1 || true
        fi
        log "容器已停止"
    fi
    docker rm "$CONTAINER_NAME" >/dev/null
    log "已删除旧容器: $CONTAINER_NAME"
else
    log "未检测到已有容器，将全新启动"
fi

# --- 重建镜像 ---
log "构建 Docker 镜像: $IMAGE_NAME"
docker build \
    --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
    -t "$IMAGE_NAME" \
    "$PROJECT_ROOT"

# --- 启动新容器 ---
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

# --- 等待服务启动 ---
log "等待 Web 服务启动"
ADMIN_URL=""
SERVER_IP=$(curl -s --connect-timeout 3 "$SERVER_IP_URL" 2>/dev/null || echo '')
for _ in $(seq 1 60); do
    ADMIN_URL=$(docker logs "$CONTAINER_NAME" 2>&1 | sed -n 's/.*管理面板: *\(http:\/\/[^ ]*\).*/\1/p')
    if [ -n "$ADMIN_URL" ]; then
        if [ -n "$SERVER_IP" ]; then
            ADMIN_URL=$(echo "$ADMIN_URL" | sed "s|0\.0\.0\.0|$SERVER_IP|")
        fi
        break
    fi
    sleep 2
done

log "===================================================="
log "FUZZTeam_fish 更新完成"
if [ -n "$ADMIN_URL" ]; then
    log "管理面板: $ADMIN_URL"
else
    log "管理面板地址获取失败，查看日志: docker logs $CONTAINER_NAME"
fi
log "默认账号: fish / fishfish@123"
log "===================================================="
