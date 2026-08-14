#!/usr/bin/env bash
# 安装/卸载 Windows 登录自启：登录时静默拉起 WSL 与本应用（含晨间日清单的补发轮询）。
#
# 为什么需要它：日清单的轮询是应用进程内的协程，进程不在就什么都不会发。开机自启把
# "机器开着"变成"服务在跑"，配合补发逻辑（到点未发才发、当天只发一次），
# 发送时点没开机也能在开机后一个周期内收到当天清单。
#
# 用法：
#   scripts/install_autostart.sh            # 安装（幂等，重复执行只覆盖同一个文件）
#   scripts/install_autostart.sh --remove   # 卸载
#
# 所有机器相关的值（发行版名、Linux 用户名、项目路径、Windows 用户名）都在运行时探测，
# 不写死——换机器重跑一次即可，不需要手改脚本。
#
# 实现：.vbs 在 Startup 文件夹常遇 WSH 安全策略拦截（"内存不足"假报错）；
# 改用 .bat 批处理——Startup 原生支持、无额外依赖、0 窗口隐藏靠 start /min。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BAT_NAME="one-stop-job.bat"

if [[ -z "${WSL_DISTRO_NAME:-}" ]]; then
    echo "只在 WSL 内运行（未检测到 WSL_DISTRO_NAME）。" >&2
    exit 1
fi
if ! command -v cmd.exe >/dev/null 2>&1; then
    echo "找不到 cmd.exe，无法定位 Windows 启动文件夹。" >&2
    exit 1
fi

# Windows 用户名用 cmd.exe 探测，而不是猜 /mnt/c/Users 下哪个目录像。
WIN_USER="$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r\n')"
if [[ -z "$WIN_USER" ]]; then
    echo "无法取得 Windows 用户名。" >&2
    exit 1
fi
STARTUP_DIR="/mnt/c/Users/${WIN_USER}/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
if [[ ! -d "$STARTUP_DIR" ]]; then
    echo "启动文件夹不存在：$STARTUP_DIR" >&2
    exit 1
fi
TARGET="${STARTUP_DIR}/${BAT_NAME}"

if [[ "${1:-}" == "--remove" ]]; then
    if [[ -f "$TARGET" ]]; then
        rm -f "$TARGET"
        echo "已卸载自启：$BAT_NAME"
    else
        echo "未安装，无需卸载。"
    fi
    # 清理可能残留的旧 .vbs 文件
    OLD_VBS="${STARTUP_DIR}/one-stop-job.vbs"
    [[ -f "$OLD_VBS" ]] && rm -f "$OLD_VBS" && echo "已清理旧版 .vbs 文件"
    exit 0
fi

# @echo off 关闭命令回显
# start /min 最小化窗口（会闪一下任务栏，但不弹黑框前台）
# wsl.exe ... 进 WSL 跑 app.sh start（幂等，已运行则直接返回）
# CRLF 行尾是 Windows 批处理标准，虽然 LF-only 也能跑，但保持规范。
printf '%s\r\n' \
    '@echo off' \
    "start /min wsl.exe -d ${WSL_DISTRO_NAME} -u ${USER} -- ${PROJECT_DIR}/scripts/app.sh start" \
    >"$TARGET"

echo "已安装自启：$TARGET"
echo "  发行版：${WSL_DISTRO_NAME}｜用户：${USER}｜项目：${PROJECT_DIR}"
echo "验证：重启后不做任何操作，curl -s http://127.0.0.1:8000/api/health 应返回 ok。"
echo "卸载：scripts/install_autostart.sh --remove"
