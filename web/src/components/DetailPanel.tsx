import type { ScreenParams, Step, StepRun } from "../api";
import { ScreenForm, ScreenUsed, type ScreenDraft } from "./ScreenForm";

const SCREEN_CHECKS: [string, string][] = [
  ["roe", "ROE"],
  ["debt", "负债"],
  ["profitability", "盈利"],
  ["fcf", "现金流"],
  ["gross_margin", "毛利率"],
];

function ScreenResultView({ out }: { out: any }) {
  const results: Record<string, any> = out.results ?? {};
  const controls: string[] = out.controls ?? [];
  return (
    <>
    <h3>每家公司的结果</h3>
    <table className="grid">
      <thead>
        <tr>
          <th>股票</th>
          {SCREEN_CHECKS.map(([k, name]) => <th key={k}>{name}</th>)}
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(results).map(([t, r]) => (
          <tr key={t}>
            <td>{t}</td>
            {SCREEN_CHECKS.map(([k]) => {
              const c = r.checks?.[k];
              return (
                <td key={k} className={c?.pass ? "verdict--pass" : "verdict--fail"} title={c?.rule ?? c?.why}>
                  {c?.pass ? "✓" : "✗"}
                </td>
              );
            })}
            <td>{r.passed ? "通过" : controls.includes(t) ? "对照组" : "淘汰"}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </>
  );
}

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
};

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
      {step.id === "screen" && screen.draft && screen.defaults && (
        <ScreenForm draft={screen.draft} defaults={screen.defaults} onChange={screen.onDraftChange} />
      )}
      {!run && <p className="note">还没有运行记录。点“运行演示”开始。</p>}
      {run && (
        <>
          <p className={`panel__summary panel__summary--${run.status}`}>{run.summary}</p>
          {step.id === "screen" && screen.used && <ScreenUsed used={screen.used} defaults={screen.defaults} />}
          {step.id === "screen" && run.status === "ok" && <ScreenResultView out={run.output} />}
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
