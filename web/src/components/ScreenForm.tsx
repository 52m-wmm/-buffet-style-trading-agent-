import type { ScreenParams } from "../api";

type Key = keyof ScreenParams;
type FieldDef = { key: Key; label: string; unit: string; pct?: boolean; int?: boolean; hint: string };

export const SCREEN_FIELDS: FieldDef[] = [
  { key: "lookback_years", label: "回看年数", unit: "年", int: true, hint: "以下“几年”都在这个窗口里数" },
  { key: "roe_min", label: "ROE 门槛", unit: "%", pct: true, hint: "每年 ROE 要高于它才算达标" },
  { key: "roe_years_required", label: "ROE 达标年数", unit: "年", int: true, hint: "窗口内至少这么多年达标" },
  { key: "debt_to_income_max", label: "长期负债 / 净利润", unit: "倍", hint: "用最近一年，不超过这个倍数" },
  { key: "profitable_years_required", label: "盈利年数", unit: "年", int: true, hint: "净利润 > 0 的年数至少这么多" },
  { key: "fcf_years", label: "自由现金流统计年数", unit: "年", int: true, hint: "最近这么多年累计自由现金流要 > 0" },
  { key: "gross_margin_std_max", label: "毛利率标准差上限", unit: "%", pct: true, hint: "窗口内毛利率波动要小于它" },
  { key: "roa_min", label: "金融业：ROA 门槛", unit: "%", pct: true, hint: "只用于金融公司，代替负债、现金流、毛利率三条" },
  { key: "roa_years_required", label: "金融业：ROA 达标年数", unit: "年", int: true, hint: "窗口内至少这么多年 ROA 高于门槛" },
  { key: "equity_to_assets_min", label: "金融业：股东权益 / 总资产", unit: "%", pct: true, hint: "最近一年，资本垫子至少这么厚" },
];

/** What the user is typing, in display units (percent fields shown as 15, not 0.15). */
export type ScreenDraft = Record<Key, string>;

export function toDraft(p: ScreenParams): ScreenDraft {
  return Object.fromEntries(
    SCREEN_FIELDS.map((f) => [f.key, String(f.pct ? Number((p[f.key] * 100).toFixed(4)) : p[f.key])]),
  ) as ScreenDraft;
}

/** Parse the draft back to API units; returns per-field errors instead when something is off. */
export function parseDraft(d: ScreenDraft): { params?: ScreenParams; errors: Partial<Record<Key, string>> } {
  const errors: Partial<Record<Key, string>> = {};
  const out: Partial<ScreenParams> = {};
  for (const f of SCREEN_FIELDS) {
    const raw = d[f.key].trim();
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n) || n < 0) errors[f.key] = "要填非负数";
    else if (f.int && !Number.isInteger(n)) errors[f.key] = "要填整数";
    else out[f.key] = f.pct ? n / 100 : n;
  }
  const lb = out.lookback_years;
  if (lb != null) {
    for (const k of ["roe_years_required", "profitable_years_required", "fcf_years", "roa_years_required"] as Key[]) {
      if (out[k] != null && out[k]! > lb) errors[k] = `不能超过回看年数 ${lb}`;
    }
  }
  return Object.keys(errors).length ? { errors } : { params: out as ScreenParams, errors };
}

export function sameScreen(a: ScreenParams | null | undefined, b: ScreenParams | null | undefined) {
  return !!a && !!b && SCREEN_FIELDS.every((f) => a[f.key] === b[f.key]);
}

function show(f: FieldDef, v: number | undefined) {
  if (v == null) return "—";
  return f.pct ? `${Number((v * 100).toFixed(4))}%` : `${v}${f.unit === "倍" ? "x" : " 年"}`;
}

export function ScreenForm({
  draft, defaults, onChange,
}: { draft: ScreenDraft; defaults: ScreenParams; onChange: (d: ScreenDraft) => void }) {
  const { errors } = parseDraft(draft);
  const base = toDraft(defaults);
  const changed = SCREEN_FIELDS.some((f) => Number(draft[f.key]) !== Number(base[f.key]));
  return (
    <section className="form" aria-label="初筛阈值">
      <div className="form__head">
        <h3>下次运行用的阈值</h3>
        <button type="button" className="link" onClick={() => onChange(base)} disabled={!changed}>
          恢复默认
        </button>
      </div>
      {SCREEN_FIELDS.map((f) => {
        const isChanged = Number(draft[f.key]) !== Number(base[f.key]);
        return (
          <label key={f.key} className={`form__row${isChanged ? " form__row--changed" : ""}`}>
            <span className="form__label">
              {f.label}
              <small>{f.hint}</small>
            </span>
            <span className="form__input">
              <input
                inputMode="decimal"
                value={draft[f.key]}
                aria-invalid={!!errors[f.key]}
                onChange={(e) => onChange({ ...draft, [f.key]: e.target.value })}
              />
              <span className="form__unit">{f.unit}</span>
            </span>
            {errors[f.key] && <span className="form__error">{errors[f.key]}</span>}
            {isChanged && !errors[f.key] && <span className="form__default">默认 {show(f, defaults[f.key])}</span>}
          </label>
        );
      })}
      <p className="note">看到结果后再回头改阈值，最容易过拟合。每次运行都会记下当时用的阈值，方便事后对比。</p>
    </section>
  );
}

export function ScreenUsed({ used, defaults }: { used: ScreenParams; defaults: ScreenParams | null }) {
  return (
    <>
      <h3>这次运行用的阈值</h3>
      <table className="kv">
        <tbody>
          {SCREEN_FIELDS.filter((f) => used[f.key] != null).map((f) => {
            const differs = defaults != null && used[f.key] !== defaults[f.key];
            return (
              <tr key={f.key}>
                <th>{f.label}</th>
                <td className={differs ? "changed" : undefined}>
                  {show(f, used[f.key])}
                  {differs && <small>（默认 {show(f, defaults![f.key])}）</small>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
