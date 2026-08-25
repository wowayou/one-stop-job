"""候选卡「写入看板」（Phase 2，CLAUDE.md 红线 §3.10）。

聊天里被本人确认入库的候选（已 `committed`）可以再点一次「写入看板」，把一行卡片
插入个人操作仓库看板的「收集箱」列；点之前只展示预览，不写一个字节。不建立独立的
建议/审批实体——直接复用候选卡自身在 `metadata_json.candidates` 里的
`status` / `job_id` / `board_written` 字段（见 main.py 的
`board_write_candidates` 端点）。
"""

from __future__ import annotations

import re
from datetime import datetime

from ..config import Settings
from ..models import Job
from .context_repository import ContextWriter

# 写死更 KISS：新岗位线索只进这一列，暂不需要按岗位再细分列。
SECTION_HEADING = "收集箱"


def _flatten(text: str | None) -> str:
    """把标题/公司名里的换行和连续空白压成单空格，避免破坏看板的单行卡片格式。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def build_inbox_line(job: Job) -> str:
    """生成将插入看板「收集箱」列的一整行卡片（不含结尾换行）。

    字段顺序沿用看板既有卡片规则（见个人仓库 `toolkit/23-job-pipeline.md` 的「模板库」与
    活跃列样例）：`公司 - 岗位 - 薪资 - 渠道/日期 - 当前判断 - 下一步：… - [链接]`。
    `_card_summary` 只取前三段做摘要，`parse_board_companies` 只认第一段，两者都不受尾部影响。

    JD 原链接必须带上：收集箱这一行是本人后续在 Obsidian 里补齐主行、新建详情卡时的**唯一**
    入口，没有链接就得回 Web 岗位池反查，等于把刚做完的判断又丢了一次。链接放在行尾并用
    `[JD]` 标记，`board_sla._next_step_text` 会在该标记处截断动作区——否则 URL 里的数字
    （BOSS 的 `job_detail/...0825...`）会被 `_DATE_COMPACT` 误读成到期日期，凭空造出一条日清单动作。
    岗位没有 url 时整段省略，绝不写出空链接。
    """
    company = _flatten(job.company_name) or "?"
    title = _flatten(job.title)
    salary = _flatten(job.salary_text) or "薪资未知"
    date_tag = datetime.now().strftime("%m%d")
    line = (
        f"- [ ] {company} - {title} - {salary} - {job.source}/{date_tag} - 未判断 - "
        "下一步：补齐主行并新建详情卡"
    )
    url = _flatten(job.url)
    return f"{line} - [JD]({url})" if url else line


def write_candidate_to_board(settings: Settings, job: Job) -> None:
    """把一个已入库岗位写成一行卡片，插入看板「收集箱」列。

    上下文仓库未配置、路径不可用、看板文件不存在，或看板缺少「收集箱」列时，
    抛出 `ContextRepositoryError`（含子类 `ContextSectionNotFoundError`），
    由调用方（端点）转成可读的 HTTP 错误；调用前不做任何检测性读取，出错即失败。
    """
    ContextWriter(settings.context_repo_path).insert_line_in_section(
        "board", SECTION_HEADING, build_inbox_line(job)
    )
