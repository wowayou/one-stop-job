import type { FitScore } from "../types";

function dimensionLabel(key: string) {
  return (
    {
      role_match: "岗位匹配",
      salary_city: "薪资/城市",
      growth: "成长性",
      stability: "稳定性",
      reputation: "口碑",
      commute_rest: "通勤/作息",
      interview_roi: "面试投入产出"
    }[key] ?? key
  );
}

// 后端 score_job() 早就把逐维度分数/权重/理由存进了 FitScore.details（见 scoring.py），
// API 也一直原样透出——评分“透明化”不需要重造引擎或加字段，只是把已有数据画出来。
// 抽成共享组件：岗位详情抽屉和表格评分芯片弹层用同一份渲染，不重复维护两套 JSX。
function round1(value: number) {
  return Math.round(value * 10) / 10;
}

export function ScoreBreakdown({ score }: { score: FitScore }) {
  const dims = Object.entries(score.details?.dimensions ?? {});
  const dimSum = round1(dims.reduce((sum, [, value]) => sum + (value?.score ?? 0), 0));
  const weightSum = round1(dims.reduce((sum, [, value]) => sum + (value?.weight ?? 0), 0));
  return (
    <div className="score-breakdown">
      {score.hard_blocked && <strong className="danger-text">硬性条件阻断</strong>}
      {score.details?.hard_reasons?.map((reason) => <p key={reason}>{reason}</p>)}
      {!!dims.length && (
        <>
          <div className="dimension-list">
            {dims.map(([key, value]) => (
              <div key={key} className="dimension-row">
                <span className="dimension-copy">
                  <span>{dimensionLabel(key)}</span>
                  {value.note && <small>{value.note}</small>}
                </span>
                <meter value={value.score} max={value.weight || 1} />
                <strong>
                  {value.score}/{value.weight}
                </strong>
              </div>
            ))}
          </div>
          <p className="score-formula">
            {dims.map(([, value]) => value.score).join(" + ")} = {dimSum}
            {score.hard_blocked
              ? ` 分，命中硬性条件按 0.55 折算为总分 ${score.total}`
              : ` 分（满分 ${weightSum}），即总分 ${score.total}`}
          </p>
        </>
      )}
    </div>
  );
}
