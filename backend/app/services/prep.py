from __future__ import annotations

from ..models import Company, Job, UserProfile


def _clean(value: str | None, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _compact_lines(value: str | None, fallback: str, *, limit: int = 4) -> list[str]:
    text = str(value or "").replace("；", "\n").replace("。", "\n")
    parts = [part.strip(" \n\r\t-•,，") for part in text.splitlines()]
    lines = [part for part in parts if part]
    if not lines:
        return [fallback]
    return lines[:limit]


def _job_requirements(job: Job) -> str:
    return _clean(job.skills or job.description, "岗位要求暂缺，需要从 JD 或沟通中补充关键要求")


def build_interview_prep(job: Job, company: Company | None, profile: UserProfile) -> dict[str, str]:
    requirements = _job_requirements(job)
    profile_skills = _clean(profile.skills, "个人技能未填写")
    strengths = _clean(profile.strengths, "个人优势未填写")
    company_name = company.name if company else job.company_name
    location = " ".join(part for part in [job.city, job.area] if part) or "地点未披露"
    salary = job.salary_text or "薪资未披露"
    experience_lines = _compact_lines(profile.work_experience, "请补充一个与岗位要求匹配的真实项目经历")
    experience_summary = "；".join(experience_lines[:2])
    requirement_lines = _compact_lines(requirements, requirements, limit=3)

    jd_summary = (
        f"{company_name} 的 {job.title} 主要关注 {requirements}。"
        f"薪资信息：{salary}；地点：{location}。"
    )
    skill_gaps = (
        "面试前建议核对这些差距：\n"
        f"1. JD 要求是否覆盖：{requirements}\n"
        f"2. 你的可展示技能：{profile_skills}\n"
        "3. 对缺少证据的技能准备一个项目案例或学习补强计划。"
    )
    resume_points = (
        "简历/自我介绍可突出：\n"
        f"- 岗位关键词：{requirements}\n"
        f"- 个人技能匹配：{profile_skills}\n"
        f"- 核心优势：{strengths}\n"
        f"- 真实经历证据：{experience_lines[0]}\n"
        "- 尽量补充量化结果，例如流量、排名、询盘、转化率、内容产能或成本变化。"
    )
    star_stories = (
        "STAR 素材建议：\n"
        f"S：围绕 {company_name} 关注的 {requirement_lines[0]}，选择一个真实业务场景。\n"
        "T：说明你负责的指标、周期和约束。\n"
        f"A：结合你的经历展开：{experience_lines[0]}\n"
        "R：用具体数字说明结果，并补一句复盘。"
    )
    questions_to_ask = (
        "反问问题：\n"
        "1. 这个岗位入职 3 个月最重要的产出是什么？\n"
        "2. 目前 SEO/运营增长的主要瓶颈在哪？\n"
        "3. 团队如何评估内容、投放或独立站增长效果？\n"
        "4. 这个岗位与销售、产品、技术团队如何协作？"
    )
    core_pitch = (
        f"我关注到这个岗位重点需要 {requirements}。"
        f"我的核心优势是 {strengths}，过往经历中最相关的是 {experience_summary}。"
        f"如果加入 {company_name}，我可以先从岗位目标拆解、现有流量/内容/转化数据复盘、"
        "关键词和页面机会梳理入手，尽快形成可执行的增长清单。"
    )
    communication_draft = (
        f"您好，我对 {company_name} 的 {job.title} 岗位比较感兴趣。"
        f"我看到岗位关注 {requirements}，这和我过往的 {experience_lines[0]} 比较匹配。"
        f"我的优势是 {strengths}，能围绕目标拆解、内容/关键词优化和数据复盘推进结果。"
        "如果方便，期待进一步沟通岗位近期目标和团队协作方式。"
    )
    tailored_resume = (
        f"# {job.title} 定制简历\n\n"
        "## 求职定位\n"
        f"- 目标岗位：{job.title}\n"
        f"- 目标公司：{company_name}\n"
        f"- 匹配方向：{requirements}\n\n"
        "## 核心优势\n"
        f"- {strengths}\n"
        f"- 技能栈：{profile_skills}\n"
        f"- 可结合 {salary} / {location} 的岗位目标，优先推进可量化增长结果。\n\n"
        "## 相关工作经历\n"
        + "\n".join(f"- {line}" for line in experience_lines)
        + "\n\n## 与岗位要求的对应关系\n"
        + "\n".join(f"- {line}：准备一个真实项目、动作和结果数据。" for line in requirement_lines)
        + "\n\n## 投递前待补充\n"
        "- 把每段经历补成“动作 + 工具/方法 + 指标结果”。\n"
        "- 删除与本岗位无关的泛泛职责，优先保留 SEO/运营/数据复盘/增长相关内容。"
    )
    return {
        "jd_summary": jd_summary,
        "skill_gaps": skill_gaps,
        "resume_points": resume_points,
        "star_stories": star_stories,
        "questions_to_ask": questions_to_ask,
        "core_pitch": core_pitch,
        "communication_draft": communication_draft,
        "tailored_resume": tailored_resume,
    }
