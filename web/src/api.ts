export type Step = { id: string; label: string; kind: "code" | "model"; desc: string };
export type Graph = { steps: Step[]; edges: { source: string; target: string }[] };
export type ScreenParams = {
  roe_min: number;
  roe_years_required: number;
  debt_to_income_max: number;
  profitable_years_required: number;
  fcf_years: number;
  gross_margin_std_max: number;
  lookback_years: number;
};
export type Run = {
  id: string;
  mode: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  screen: ScreenParams | null; // thresholds this run actually used
};
export type StepRun = {
  step: string;
  status: "ok" | "failed" | "skipped";
  started_at: string;
  finished_at: string | null;
  summary: string;
  output: any;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // FastAPI validation errors come back as a list of {loc, msg}
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: any) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg}`).join("；")
      : body.detail;
    throw new Error(detail ?? `请求失败（${res.status}）`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  graph: () => fetch("/api/pipeline").then((r) => json<Graph>(r)),
  screenDefaults: () => fetch("/api/config/screen").then((r) => json<ScreenParams>(r)),
  runs: () => fetch("/api/runs").then((r) => json<Run[]>(r)),
  steps: (id: string) => fetch(`/api/runs/${id}/steps`).then((r) => json<StepRun[]>(r)),
  startDemo: (screen?: Partial<ScreenParams>) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "demo", screen }),
    }).then((r) => json<{ id: string }>(r)),
};
