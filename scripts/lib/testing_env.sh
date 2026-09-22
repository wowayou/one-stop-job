#!/usr/bin/env bash
# 冒烟/压测脚本的共用隔离层：一次 source，拿到「中和后的 config + 干净的环境变量」。
#
# 为什么不让每条脚本各写一遍：见 scripts/lib/testing_config.py 的模块 docstring。
# 简版——load_smoke.sh 曾经只清空 OPENAI_*，于是真实 config 的 telegram.enabled 和
# schedule.digest.collect_first 原样生效，压测进程会抢线上 bot 的 getUpdates、
# 读写真实 daily_digest_state.json 并压掉机主当天那次采集。
#
# 用法：
#   source "$ROOT_DIR/scripts/lib/testing_env.sh"
#   testing_env_setup "$ROOT_DIR" "$PYTHON" "$tmp_dir" "$db_path"
# 之后 $TESTING_CONFIG_PATH 可用，且本 shell 及其子进程已带上隔离环境变量。

# 生成中和配置并导出隔离环境变量。
#   $1 仓库根目录  $2 python 可执行文件  $3 临时目录  $4 sqlite 文件路径
testing_env_setup() {
  local root_dir="$1" python_bin="$2" tmp_dir="$3" db_path="$4"

  TESTING_CONFIG_PATH="$tmp_dir/config.yaml"
  "$python_bin" "$root_dir/scripts/lib/testing_config.py" \
    --out "$TESTING_CONFIG_PATH" --data-dir "$tmp_dir/data" >/dev/null
  export TESTING_CONFIG_PATH
  export JOB_ONE_STOP_CONFIG="$TESTING_CONFIG_PATH"
  export JOB_ONE_STOP_DATABASE_URL="sqlite:///$db_path"

  # 密钥一律置空而不是 unset：config.py 的 load_dotenv 会把 unset 掉的变量从真实
  # .env 重新填上（它不覆盖已存在的变量，但空值算「已存在」），而读取侧一律把空串
  # 当未配置。置空才真的关得掉。
  export OPENAI_API_KEY=""
  export OPENAI_BASE_URL=""
  export OPENAI_MODEL=""
  # 多 provider 的密钥各进不同 env 名，无法逐个枚举；靠 testing_config.py 删掉
  # ai.providers 从配置侧断掉——没有 provider 列表就没有「去哪个 env 读」。
  export TELEGRAM_BOT_TOKEN=""
  # 个人上下文仓库：置空 = 未配置。ContextWriter 拿不到 root 就不可能写进机主看板。
  export JOB_ONE_STOP_CONTEXT_REPO_PATH=""
}
