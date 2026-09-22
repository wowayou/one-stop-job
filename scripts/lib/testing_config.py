"""测试/冒烟用配置的唯一构造入口。

**为什么需要它**：pytest 与三条冒烟脚本此前各自「拷一份真实 config.yaml 再关掉几段」，
关掉哪几段全靠各自记得。实测漏项的后果不是「测试有点脏」：

- `scripts/load_smoke.sh` 拷了真实 config 却**只**清空 `OPENAI_*`，于是
  `telegram.enabled=true` 原样生效 → 压测进程起长轮询，和线上实例抢同一个 bot 的
  getUpdates，线上那个连吃 409；`schedule.digest.collect_first=true` 且
  `general.data_dir` 仍指向真实目录 → 读写**真实**的 daily_digest_state.json，
  当天还没发过时会真的发一次日清单并触发晨间采集，把 `last_collected` 标成今天，
  把机主真正那次采集压掉（红线 §3.3 频率上限每日一次，失败不重试，压掉就是压掉）。
- 同一份真实 config 的 `ai.providers` + 真实 `.env` 的 key 还在 → 压测跑
  `/api/sprint/brief` 会经 `prep_ops.tailor_interview_prep_llm` **真的调模型**
  （违反 CLAUDE.md §4「测试不得联网」，还烧真钱）。

d8843fa 已经给 `system_smoke.sh` 逐条补齐过一次；本模块把那份清单收敛成一处，
省掉「下一条脚本再漏一遍」。

**默认基线是 `config.example.yaml`（已入库的模板），不是机主的 `config.yaml`**：
后者被 gitignore，干净检出里根本没有。验收标准要求「没有个人 .env、没有个人
config.yaml 的干净检出中也能跑全部测试」，所以基线必须取已入库的那份。
需要拿本机真实配置对照排查时传 `--source config.yaml`，中和规则照样生效。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "config.example.yaml"


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    """取出 dict 型配置段；原值不是 dict（含 None）时替换成空 dict 再返回。"""
    value = config.get(key)
    if not isinstance(value, dict):
        value = {}
        config[key] = value
    return value


def neutralize(config: dict[str, Any], *, data_dir: str | Path) -> dict[str, Any]:
    """原地关掉「会对外动作 / 会碰真实数据目录」的配置段，返回同一个 dict。

    每一条都对应一个实际踩过或一眼可见的外泄面，不是「保险起见」：
    - `telegram.enabled`：长轮询会和线上实例抢同一个 bot 的 getUpdates。
    - `schedule.digest.*` / `automation.mode`：定时循环会真的发日清单、真的触发晨间采集。
    - `updates.enabled`：启动时会向 GitHub 发出站请求。
    - `ai.providers`：配置驱动的多 provider 会绕过 `OPENAI_API_KEY=""`，真的调模型。
    - `wechat.yuanbao_automation.enabled`：会拉起 Playwright 真开浏览器。
    - `collect.area_filter.enabled`：白名单是机主的个人城市，样例岗位全在「示例市」，
      不关掉会让所有走采集器的用例/冒烟随本机配置一起翻红。
    - `general.data_dir`：digest 状态文件、聊天附件都不该落进真实 data/job_one_stop。
    """
    _section(config, "telegram")["enabled"] = False

    digest = _section(_section(config, "schedule"), "digest")
    digest["enabled"] = False
    digest["collect_first"] = False

    _section(config, "automation")["mode"] = "manual"
    _section(config, "updates")["enabled"] = False

    # ai.enabled 刻意保留原值：pytest 基线需要它为真（否则 ingest 抽取整条链路短路，
    # 被桩掉的 extract_jobs_freeform 根本不会被调用）。真正阻断联网靠两件事——
    # 这里删掉 providers，以及运行侧把 OPENAI_API_KEY 置空。
    _section(config, "ai").pop("providers", None)

    _section(_section(config, "wechat"), "yuanbao_automation")["enabled"] = False
    _section(_section(config, "collect"), "area_filter")["enabled"] = False
    _section(config, "general")["data_dir"] = str(data_dir)
    return config


def build(*, data_dir: str | Path, source: str | Path | None = None) -> dict[str, Any]:
    """读取基线配置（默认 `config.example.yaml`）并返回中和后的副本。"""
    source_path = Path(source) if source else DEFAULT_SOURCE
    loaded: Any = {}
    if source_path.exists():
        with source_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    return neutralize(loaded, data_dir=data_dir)


def write(out_path: str | Path, *, data_dir: str | Path, source: str | Path | None = None) -> Path:
    """把中和后的配置写到 `out_path`，返回该路径。"""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(build(data_dir=data_dir, source=source), fh, allow_unicode=True, sort_keys=False)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="生成测试/冒烟用的中和配置")
    parser.add_argument("--out", required=True, help="输出的 config.yaml 路径")
    parser.add_argument("--data-dir", required=True, help="写进 general.data_dir 的隔离数据目录")
    parser.add_argument(
        "--source",
        default=None,
        help=f"基线配置，默认 {DEFAULT_SOURCE.name}（已入库模板）；排查时可传本机 config.yaml",
    )
    args = parser.parse_args()
    print(write(args.out, data_dir=args.data_dir, source=args.source))


if __name__ == "__main__":
    main()
