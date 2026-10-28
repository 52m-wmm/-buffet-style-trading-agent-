import type { ScreenParams, ScreenReport, ScreenReportInfo, Step, StepRun } from "../api";
import { ScreenForm, ScreenUsed, type ScreenDraft } from "./ScreenForm";
import { ScreenResults } from "./ScreenResults";

const CHECK_NAMES: Record<string, string> = {
  consistency: "多次运行一致性",
  citations: "引用真实率",
  anonymization: "实名与匿名差异",
  control_group: "对照组区分",
};

function pct(x: number | null | undefined) {
  return x == null ? "—" : `${(x * 100).toFixed(1)}%`;
}

function CheckView({ out }: { out: any }) {
  const bt = out.backtest ?? {};
  return (
    <>
      <table className="kv">
        <tbody>
          {Object.entries(CHECK_NAMES).map(([k, name]) => {
            const v = out[k] ?? {};
            const state = v.pass === true ? "pass" : v.pass === false ? "fail" : "na";
            return (
              <tr key={k}>
                <th>{name}</th>
                <td className={`verdict verdict--${state}`}>
                  {state === "pass" ? "通过" : state === "fail" ? "未通过" : "无结论"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {out.anonymization?.leaked?.length > 0 && (
        <p className="note">匿名失败，已单列：{out.anonymization.leaked.join("、")}</p>
      )}
      <h3>短回测</h3>
      <table className="kv">
        <tbody>
          <tr><th>组合收益</th><td>{pct(bt.total_return)}</td></tr>
          <tr><th>基准收益</th><td>{pct(bt.benchmark_return)}</td></tr>
          <tr><th>超额收益</th><td>{pct(bt.excess_return)}</td></tr>
          <tr><th>最大回撤</th><td>{pct(bt.max_drawdown)}</td></tr>
          <tr><th>成交笔数</th><td>{bt.n_trades ?? "—"}</td></tr>
          <tr><th>手续费和价差</th><td>{bt.fees_paid ?? "—"}</td></tr>
        </tbody>
      </table>
      <p className="note">样本只有几个月，不做显著性结论。</p>
    </>
  );
}

function TradesView({ out }: { out: any }) {
  const trades: any[] = out.trades ?? [];
  if (!trades.length) return <p className="note">这次运行没有成交。</p>;
  return (
    <table className="grid">
      <thead>
        <tr><th>日期</th><th>股票</th><th>方向</th><th>价格</th><th>触发规则</th></tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i}>
            <td>{t.date}</td>
            <td>{t.ticker}</td>
            <td>{t.side === "BUY" ? "买入" : "卖出"}</td>
            <td className="num">{t.price.toFixed(2)}</td>
            <td>{t.rule}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type ScreenProps = {
  draft: ScreenDraft | null;
  defaults: ScreenParams | null;
  used: ScreenParams | null;
  onDraftChange: (d: ScreenDraft) => void;
  reports: ScreenReportInfo[];
  source: string | null; // null = the selected run, otherwise a report name
  onSource: (s: string | null) => void;
  report: ScreenReport | null;
};

function SourcePicker({ screen }: { screen: ScreenProps }) {
  if (!screen.reports.length) return null;
  return (
    <div className="seg" role="radiogroup" aria-label="看哪份结果">
      <button role="radio" aria-checked={screen.source == null}
              className={screen.source == null ? "seg--on" : undefined} onClick={() => screen.onSource(null)}>
        演示运行
      </button>
      {screen.reports.map((r) => (
        <button key={r.name} role="radio" aria-checked={screen.source === r.name}
                className={screen.source === r.name ? "seg--on" : undefined} onClick={() => screen.onSource(r.name)}>
          标普500 · {r.as_of}
        </button>
      ))}
    </div>
  );
}

function ReportView({ report, defaults }: { report: ScreenReport; defaults: ScreenParams | null }) {
  const skipped = Object.keys(report.skipped ?? {}).length;
  return (
    <>
      <p className="panel__summary panel__summary--ok">
        {report.as_of} 的标普500成分股：筛了 {report.screened} 家，{report.passed.length} 家通过
        {skipped ? `，${skipped} 家取不到数据跳过` : ""}。
      </p>
      <p className="note">
        这份报告是命令行生成的，只读 SEC 财报数据，不调用模型。改了阈值要在 backend 目录重新运行
        <code>python -m buffett_agent screen</code>（有缓存时十几秒）。
      </p>
      <ScreenResults results={report.results} lookback={report.thresholds.lookback_years} />
      <details className="raw">
        <summary>这份报告用的阈值</summary>
        <ScreenUsed used={report.thresholds} defaults={defaults} title="" />
      </details>
    </>
  );
}

export function DetailPanel({ step, run, screen }: { step?: Step; run?: StepRun; screen: ScreenProps }) {
  if (!step) {
    return (
      <aside className="panel panel--empty">
        <p>点画布上的一个步骤，查看它读了什么、产出了什么。</p>
      </aside>
    );
  }
  return (
    <aside className="panel">
      <header className="panel__head">
        <h2>{step.label}</h2>
        <span className={`tag tag--${step.kind}`}>{step.kind === "model" ? "调用模型" : "代码规则"}</span>
      </header>
      <p className="panel__desc">{step.desc}</p>
      {step.id === "screen" && <SourcePicker screen={screen} />}
      {step.id === "screen" && screen.source != null && (
        screen.report ? <ReportView report={screen.report} defaults={screen.defaults} /> : <p className="note">读取报告中…</p>
      )}
      {step.id === "screen" && screen.source == null && screen.draft && screen.defaults && (
        <ScreenForm draft={screen.draft} defaults={screen.defaults} onChange={screen.onDraftChange} />
      )}
      {!run && !(step.id === "screen" && screen.source != null) && <p className="note">还没有运行记录。点“运行演示”开始。</p>}
      {run && !(step.id === "screen" && screen.source != null) && (
        <>
          <p className={`panel__summary panel__summary--${run.status}`}>{run.summary}</p>
          {step.id === "screen" && screen.used && <ScreenUsed used={screen.used} defaults={screen.defaults} />}
          {step.id === "screen" && run.status === "ok" && (
            <ScreenResults results={run.output.results ?? {}} controls={run.output.controls ?? []}
                           lookback={screen.used?.lookback_years ?? screen.defaults?.lookback_years ?? 10} />
          )}
          {step.id === "check" && run.status === "ok" && <CheckView out={run.output} />}
          {step.id === "execute" && run.status === "ok" && <TradesView out={run.output} />}
          <details className="raw">
            <summary>完整输出（JSON）</summary>
            <pre>{JSON.stringify(run.output, null, 2)}</pre>
          </details>
        </>
      )}
    </aside>
  );
}
