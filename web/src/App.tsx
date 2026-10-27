import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge } from "@xyflow/react";
import { api, type Graph, type Run, type ScreenParams, type StepRun } from "./api";
import { StepNode, type StepFlowNode } from "./components/StepNode";
import { DetailPanel } from "./components/DetailPanel";
import { parseDraft, sameScreen, toDraft, type ScreenDraft } from "./components/ScreenForm";

const nodeTypes = { step: StepNode };

export default function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [stepRuns, setStepRuns] = useState<StepRun[]>([]);
  const [selected, setSelected] = useState<string | null>("check");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [screenDefaults, setScreenDefaults] = useState<ScreenParams | null>(null);
  const [screenDraft, setScreenDraft] = useState<ScreenDraft | null>(null);

  useEffect(() => {
    Promise.all([api.graph(), api.runs(), api.screenDefaults()])
      .then(([g, r, s]) => {
        setGraph(g);
        setRuns(r);
        setScreenDefaults(s);
        setScreenDraft(toDraft(s));
        if (r[0]) setRunId(r[0].id);
      })
      .catch(() => setError("连不上后端。先在 backend 目录运行：uvicorn buffett_agent.api:app --port 8000"));
  }, []);

  useEffect(() => {
    if (!runId) return;
    api.steps(runId).then(setStepRuns).catch((e) => setError(e.message));
  }, [runId]);

  const parsed = useMemo(() => (screenDraft ? parseDraft(screenDraft) : null), [screenDraft]);
  const customScreen = !!parsed?.params && !sameScreen(parsed.params, screenDefaults);

  const runDemo = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.startDemo(parsed?.params);
      setRuns(await api.runs());
      setRunId(id);
      setSelected("screen");
    } catch (e: any) {
      setError(`运行失败：${e.message}`);
    } finally {
      setBusy(false);
    }
  }, [parsed]);

  const currentRun = runs.find((r) => r.id === runId);

  const byStep = useMemo(() => Object.fromEntries(stepRuns.map((s) => [s.step, s])), [stepRuns]);

  const nodes: StepFlowNode[] = useMemo(
    () =>
      (graph?.steps ?? []).map((s, i) => ({
        id: s.id,
        type: "step",
        position: { x: 0, y: i * 112 },
        data: { step: s, run: byStep[s.id], selected: s.id === selected },
        draggable: false,
      })),
    [graph, byStep, selected],
  );

  const edges: Edge[] = useMemo(
    () =>
      (graph?.edges ?? []).map((e) => ({
        id: `${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        className: byStep[e.target]?.status === "failed" ? "edge--failed" : undefined,
      })),
    [graph, byStep],
  );

  const failed: string[] = byStep["check"]?.output?.failed ?? [];
  const selectedStep = graph?.steps.find((s) => s.id === selected);

  return (
    <div className="app">
      <header className="bar">
        <div>
          <h1>巴菲特风格交易工作流</h1>
          <p className="bar__sub">只有研究和打分用模型，买卖和风控都是写死的规则。</p>
        </div>
        <div className="bar__actions">
          <label className="field">
            <span>运行记录</span>
            <select value={runId ?? ""} onChange={(e) => setRunId(e.target.value)} disabled={!runs.length}>
              {!runs.length && <option value="">还没有</option>}
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.started_at.replace("T", " ").slice(0, 16)}（{r.mode}，{r.status === "done" ? "完成" : r.status}
                  {screenDefaults && r.screen && !sameScreen(r.screen, screenDefaults) ? "，自定义阈值" : ""}）
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn"
            onClick={runDemo}
            disabled={busy || !graph || !parsed?.params}
            title={!parsed?.params ? "初筛阈值有误，点“1 规则初筛”查看" : undefined}
          >
            {busy ? "运行中…" : customScreen ? "用自定义阈值运行" : "运行演示"}
          </button>
        </div>
      </header>

      {error && <div className="banner banner--error" role="alert">{error}</div>}
      {stepRuns.length > 0 && (
        <div className={`banner ${failed.length ? "banner--warn" : "banner--ok"}`}>
          {failed.length
            ? `有检验没通过：${failed.join("、")}。报告里要写清楚原因。`
            : "所有预设检验都通过了。样本很小，先怀疑再相信。"}
        </div>
      )}

      <main className="body">
        <section className="canvas" aria-label="工作流画布">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelected(n.id)}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            nodesConnectable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </section>
        <DetailPanel
          step={selectedStep}
          run={selected ? byStep[selected] : undefined}
          screen={{
            draft: screenDraft,
            defaults: screenDefaults,
            used: currentRun?.screen ?? null,
            onDraftChange: setScreenDraft,
          }}
        />
      </main>
    </div>
  );
}
