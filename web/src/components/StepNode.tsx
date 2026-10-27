import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { Step, StepRun } from "../api";

export type StepNodeData = { step: Step; run?: StepRun; selected: boolean };
export type StepFlowNode = Node<StepNodeData, "step">;

const STATUS_TEXT: Record<string, string> = { ok: "完成", failed: "失败", skipped: "跳过" };

export function StepNode({ data }: NodeProps<StepFlowNode>) {
  const { step, run, selected } = data;
  const status = run?.status ?? "idle";
  return (
    <div
      className={`step step--${step.kind} step--${status}${selected ? " step--selected" : ""}`}
      title={step.desc}
    >
      <Handle type="target" position={Position.Top} />
      <div className="step__head">
        <span className="step__label">{step.label}</span>
        <span className="step__kind">{step.kind === "model" ? "模型" : "代码"}</span>
      </div>
      <p className="step__summary">{run ? run.summary : step.desc}</p>
      {run && <span className={`step__status step__status--${status}`}>{STATUS_TEXT[status]}</span>}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
