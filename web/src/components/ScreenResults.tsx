import { useMemo, useState } from "react";

type Rule = "roe" | "debt" | "profitability" | "fcf" | "gross_margin" | "roa" | "capital";
type Check = { pass: boolean; value: number | null; rule?: string; why?: string; na?: boolean; data?: string };
export type ScreenResult = { passed: boolean; checks: Partial<Record<Rule, Check>>; group?: string; sector?: string | null };

const GENERAL: Rule[] = ["roe", "debt", "profitability", "fcf", "gross_margin"];
const GROUPS: { key: string; label: string; desc: string; rules: Rule[]; provisional?: boolean }[] = [
  { key: "general", label: "通用规则", desc: "ROE、负债、盈利、现金流、毛利率", rules: GENERAL },
  { key: "financials", label: "金融规则", desc: "ROE、盈利、ROA、资本充足", rules: ["roe", "profitability", "roa", "capital"] },
  { key: "provisional", label: "地产和公用事业", desc: "规则待定，暂用通用规则", rules: GENERAL, provisional: true },
];
const RULE_LABEL: Record<Rule, string> = {
  roe: "ROE 达标", debt: "负债/利润", profitability: "盈利年数", fcf: "累计现金流",
  gross_margin: "毛利率波动", roa: "ROA 达标", capital: "权益/资产",
};
// How a number was obtained when it wasn't read straight from the filing (see screening.py).
const DATA_BADGE: Record<string, string> = { zero: "按0", estimated: "估", unknown: "未知" };

function money(v: number) {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  return v.toFixed(0);
}

function cellText(rule: Rule, c: Check, lookback: number): string {
  const v = c.value;
  if (v == null) return c.data === "unknown" ? "?" : c.data === "loss" ? "亏损" : "缺数据";
  switch (rule) {
    case "roe": case "roa": case "profitability": return `${v}/${lookback}`;
    case "debt": return `${v}x`;
    case "fcf": return money(v);
    case "gross_margin": case "capital": return `${(v * 100).toFixed(1)}%`;
  }
}

const SECTOR_CN: Record<string, string> = {
  "Information Technology": "信息技术", Industrials: "工业", Financials: "金融", "Health Care": "医疗",
  "Consumer Discretionary": "可选消费", "Consumer Staples": "必需消费", "Communication Services": "通信",
  Materials: "原材料", Energy: "能源", "Real Estate": "房地产", Utilities: "公用事业",
};

const groupOf = (r: ScreenResult) => r.group ?? "general";

export function ScreenResults({
  results, lookback, controls = [],
}: { results: Record<string, ScreenResult>; lookback: number; controls?: string[] }) {
  const counts = useMemo(() => {
    const out: Record<string, { n: number; passed: number }> = {};
    for (const r of Object.values(results)) {
      const g = (out[groupOf(r)] ??= { n: 0, passed: 0 });
      g.n += 1;
      if (r.passed) g.passed += 1;
    }
    return out;
  }, [results]);
  const present = GROUPS.filter((g) => counts[g.key]);
  const [picked, setPicked] = useState("general");
  const [onlyPassed, setOnlyPassed] = useState(false);
  const group = present.find((g) => g.key === picked) ?? present[0];

  const review = useMemo(
    () => Object.entries(results).filter(([, r]) => Object.values(r.checks).some((c) => c?.data === "unknown")),
    [results],
  );

  if (!group) return <p className="note">没有筛选结果。</p>;
  const rows = Object.entries(results)
    .filter(([, r]) => groupOf(r) === group.key && (!onlyPassed || r.passed))
    .sort(([a, ra], [b, rb]) => Number(rb.passed) - Number(ra.passed) || a.localeCompare(b));
  const showSector = rows.some(([, r]) => r.sector);

  return (
    <section aria-label="每家公司的结果">
      <h3>每家公司的结果</h3>
      {present.length > 1 && (
        <div className="groups" role="tablist">
          {present.map((g) => (
            <button
              key={g.key}
              role="tab"
              aria-selected={g.key === group.key}
              className={`group${g.key === group.key ? " group--on" : ""}`}
              onClick={() => setPicked(g.key)}
            >
              <span className="group__label">{g.label}</span>
              <span className="group__count">
                {counts[g.key].passed}<small> / {counts[g.key].n} 通过</small>
              </span>
              <span className={`group__desc${g.provisional ? " group__desc--warn" : ""}`}>{g.desc}</span>
            </button>
          ))}
        </div>
      )}
      {group.key === "financials" && (
        <p className="note">金融公司的负债就是存款和保费，所以不看负债、现金流、毛利率，改看 ROA 和资本垫子。</p>
      )}
      {group.provisional && <p className="note">这组还没有专门规则，结果只能参考。</p>}
      <label className="check">
        <input type="checkbox" checked={onlyPassed} onChange={(e) => setOnlyPassed(e.target.checked)} />
        只看通过的
      </label>
      <div className="grid-wrap">
        <table className="grid grid--screen">
          <thead>
            <tr>
              <th>股票</th>
              {showSector && <th>行业</th>}
              {group.rules.map((k) => <th key={k} className="num">{RULE_LABEL[k]}</th>)}
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([t, r]) => (
              <tr key={t}>
                <td>{t}</td>
                {showSector && <td className="muted">{r.sector ? SECTOR_CN[r.sector] ?? r.sector : ""}</td>}
                {group.rules.map((k) => {
                  const c = r.checks[k];
                  if (!c || c.na) return <td key={k} className="num muted" title={c?.why ?? "这条规则不适用"}>—</td>;
                  const badge = c.data && DATA_BADGE[c.data];
                  return (
                    <td key={k} className={`num ${c.pass ? "verdict--pass" : "verdict--fail"}`}
                        title={[c.rule, c.why].filter(Boolean).join("；")}>
                      {cellText(k, c, lookback)}
                      {badge && <span className="badge">{badge}</span>}
                    </td>
                  );
                })}
                <td>{r.passed ? "通过" : controls.includes(t) ? "对照组" : "淘汰"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">绿色是这条过了，红色是没过。鼠标停在格子上能看到规则和数据来源。黄标：按0 = 资产负债表上没有负债、利息也很少；估 = 用了其他负债科目；未知 = 取不到。</p>
      {review.length > 0 && (
        <p className="review">
          待人工核对 {review.length} 家（付利息但取不到负债余额，要看10-K原文）：{review.map(([t]) => t).join("、")}
        </p>
      )}
    </section>
  );
}
