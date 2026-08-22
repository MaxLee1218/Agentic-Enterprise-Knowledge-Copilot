#!/bin/bash

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.local-enterprise"
ENV_EXAMPLE="$PROJECT_DIR/.env.local-enterprise.example"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.local-enterprise.yml"
START_TIMEOUT_SECONDS="${COPILOT_START_TIMEOUT_SECONDS:-600}"

COMPOSE=(
  docker compose
  --project-directory "$PROJECT_DIR"
  --env-file "$ENV_FILE"
  -f "$COMPOSE_FILE"
)

pause_after_error() {
  if [[ -t 0 ]]; then
    printf '\n按 Enter 关闭窗口...'
    read -r _
  fi
}

fail() {
  printf '\n错误：%s\n' "$1" >&2
  pause_after_error
  exit 1
}

frontend_port() {
  local value
  value="$({
    awk -F= '/^[[:space:]]*FRONTEND_PORT[[:space:]]*=/ { value=$0 } END { print value }' "$ENV_FILE"
  } 2>/dev/null)"
  value="${value#*=}"
  value="${value%%#*}"
  value="$(printf '%s' "$value" | tr -d '[:space:]\"\047')"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    value="8080"
  fi
  printf '%s' "$value"
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || fail "未找到 Docker。请先安装 Docker Desktop。"

  if docker info >/dev/null 2>&1; then
    return
  fi

  if [[ "$(uname -s)" != "Darwin" ]] || ! command -v open >/dev/null 2>&1; then
    fail "Docker 引擎未运行。请启动 Docker 后重试。"
  fi

  printf '正在启动 Docker Desktop'
  open -a Docker >/dev/null 2>&1 || fail "无法启动 Docker Desktop。"

  local waited=0
  while ! docker info >/dev/null 2>&1; do
    if (( waited >= 180 )); then
      printf '\n'
      fail "Docker Desktop 在 3 分钟内未就绪。"
    fi
    printf '.'
    sleep 3
    waited=$((waited + 3))
  done
  printf ' 已就绪。\n'
}

show_failure_context() {
  printf '\n服务状态：\n'
  "${COMPOSE[@]}" ps 2>/dev/null || true
  printf '\n最近日志：\n'
  "${COMPOSE[@]}" logs --tail 80 2>/dev/null || true
}

wait_for_frontend() {
  local url="$1"
  local started_at=$SECONDS

  printf '正在等待前端就绪'
  while ! curl --fail --silent --show-error --max-time 3 "$url/health" >/dev/null 2>&1; do
    if (( SECONDS - started_at >= START_TIMEOUT_SECONDS )); then
      printf '\n'
      show_failure_context
      fail "前端在 ${START_TIMEOUT_SECONDS} 秒内未就绪。"
    fi

    local container_id
    container_id="$("${COMPOSE[@]}" ps -q frontend 2>/dev/null || true)"
    if [[ -n "$container_id" ]]; then
      local state
      state="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
      if [[ "$state" == "exited" || "$state" == "dead" ]]; then
        printf '\n'
        show_failure_context
        fail "前端容器已停止。"
      fi
    fi

    printf '.'
    sleep 3
  done
  printf ' 已就绪。\n'
}

main() {
  cd "$PROJECT_DIR" || fail "无法进入项目目录。"

  [[ -f "$COMPOSE_FILE" ]] || fail "缺少 docker-compose.local-enterprise.yml。"

  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ENV_EXAMPLE" ]] || fail "缺少 .env.local-enterprise.example。"
    cp "$ENV_EXAMPLE" "$ENV_FILE" || fail "无法创建 .env.local-enterprise。"
    printf '已为首次使用创建：%s\n' "$ENV_FILE"
    printf '请填写 LLM_API_KEY，保存后再次双击本快捷方式。\n'
    if [[ "$(uname -s)" == "Darwin" ]]; then
      open -t "$ENV_FILE" >/dev/null 2>&1 || true
    fi
    pause_after_error
    exit 1
  fi

  ensure_docker

  local running_services
  running_services="$("${COMPOSE[@]}" ps --status running --services 2>/dev/null || true)"
  if [[ -n "$running_services" ]]; then
    printf '检测到项目正在运行，正在关闭全部服务...\n'
    "${COMPOSE[@]}" down --remove-orphans || fail "服务关闭失败。"
    printf '\n已关闭全部服务。数据卷和 RAG 索引已保留。\n'
    return
  fi

  command -v curl >/dev/null 2>&1 || fail "未找到 curl，无法检查前端就绪状态。"
  "${COMPOSE[@]}" config --quiet || fail "Docker Compose 配置校验失败，请检查 .env.local-enterprise。"

  printf '正在构建并启动完整服务，首次启动可能需要较长时间...\n'
  if ! "${COMPOSE[@]}" up --build --detach; then
    show_failure_context
    fail "完整服务启动失败。"
  fi

  local url
  url="http://127.0.0.1:$(frontend_port)"
  wait_for_frontend "$url"

  printf '\n完整服务已启动：%s\n' "$url"
  printf '下次双击「一键启动或关闭.command」即可关闭全部服务。\n'
  if [[ "$(uname -s)" == "Darwin" ]]; then
    open "$url" >/dev/null 2>&1 || fail "服务已启动，但无法打开默认浏览器。请手动访问 $url。"
  fi
}

main "$@"
