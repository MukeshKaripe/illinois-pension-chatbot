"use client";

/**
 * The things a profile lets a member change, and what changing them does.
 *
 * Shared by sign-up and the profile drawer, so the consequence shown beside a
 * field — the tier a start date implies, the history a salary rescales — is the
 * same wherever the field appears.
 */

/**
 * Tier as the statute has it, mirrored here only to show the consequence of a
 * date while it is being typed. The server derives it again from the same rule
 * and never reads a tier from this form — the column's `CHECK (tier IN (1,2))`
 * would accept a wrong answer silently, and tier selects the accrual rule
 * through Section 1-160 as well as keying the recipe registry.
 */
const TIER_2_FROM = "2011-01-01";
export const tierFor = (hireDate: string) => (hireDate >= TIER_2_FROM ? 2 : 1);

const money = (value: string) =>
  Number(value).toLocaleString("en-US", { style: "currency", currency: "USD" });

export type FieldValues = { hireDate: string; service: string; salary: string };

const field =
  "w-full px-3.5 py-2.5 bg-background border border-input rounded-lg focus:ring-2 focus:ring-ring/30 focus:border-ring outline-none text-foreground";
const label = "text-sm font-medium text-foreground";

interface ProfileFieldsProps {
  values: FieldValues;
  onChange: (next: FieldValues) => void;
  /** The values before this edit, so a change can say what it changes. */
  original: { tier: number; current_annual_salary: string };
  /** One column in the narrow drawer, three on the sign-up page. */
  columns?: 1 | 3;
}

export function ProfileFields({ values, onChange, original, columns = 3 }: ProfileFieldsProps) {
  const set = (key: keyof FieldValues) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...values, [key]: e.target.value });
  const tier = tierFor(values.hireDate);

  return (
    <div className="space-y-4">
      <div className={`grid gap-4 ${columns === 3 ? "sm:grid-cols-3" : ""}`}>
        <div className="space-y-2">
          <label className={label} htmlFor="hire">Start date</label>
          <input id="hire" type="date" value={values.hireDate} onChange={set("hireDate")} className={field} />
        </div>
        <div className="space-y-2">
          <label className={label} htmlFor="service">Years of service</label>
          <input
            id="service"
            type="number"
            step="0.01"
            min="0"
            value={values.service}
            onChange={set("service")}
            className={field}
          />
        </div>
        <div className="space-y-2">
          <label className={label} htmlFor="salary">Annual salary</label>
          <input
            id="salary"
            type="number"
            step="0.01"
            min="1"
            value={values.salary}
            onChange={set("salary")}
            className={field}
          />
        </div>
      </div>

      {/*
        Tier is never a field, and the start date is why: it is the whole test.
        Showing the consequence next to the cause is the only thing that makes
        dragging this date across 1 January 2011 visible, and it changes the
        accrual rule.
      */}
      <div className="rounded-lg border border-border bg-muted/50 p-4 text-sm leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">Tier {tier}</span>
        {tier === 2
          ? " — anyone who first joined an Illinois public pension system on or after 1 January 2011. Section 1-160 changes how the annuity accrues."
          : " — first joined before 1 January 2011, so the Article's own accrual applies."}
        {tier !== original.tier && (
          <span className="mt-2 block">
            Changing the start date has moved this member from Tier {original.tier} to Tier {tier},
            which changes the calculation.
          </span>
        )}
      </div>

      {/*
        The annuity is computed from a four-consecutive-year average, not from
        today's pay, so the salary history moves with this figure. Without that
        the pension barely responds to the field the member just changed.
      */}
      {Number(values.salary) !== Number(original.current_annual_salary) && (
        <p className="text-sm leading-relaxed text-muted-foreground">
          The ten years of salary history behind this member scale with that change, since the
          annuity is worked out from the best four consecutive years rather than from current pay.
          Currently {money(original.current_annual_salary)} → {money(values.salary)}.
        </p>
      )}
    </div>
  );
}
