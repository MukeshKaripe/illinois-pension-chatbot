"use client";

/**
 * The things a profile lets a member change, and what changing them does.
 *
 * Rendered from the fund's `fields` (spec §6, Phase 3b), so each Article's
 * bounds are the server's. Shared by sign-up and the profile drawer, so the
 * consequence shown beside a field — the tier a start date implies, the
 * history a salary rescales — is the same wherever the field appears.
 */
import type { FieldSpec } from "@/lib/api";
import type { FieldValues } from "@/lib/funds";

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

const STATUS_LABELS: Record<string, string> = {
  active: "Still working",
  separated: "Left, not yet retired",
  retired: "Retired",
};

/** The options a select offers, as [value, label]: yes/no fields store booleans. */
const choices = (field: FieldSpec): [string, string][] =>
  field.type === "yesno"
    ? [
        ["true", "Yes"],
        ["false", "No"],
      ]
    : field.options.map((option) => [option, STATUS_LABELS[option] ?? option]);

const input =
  "w-full px-3.5 py-2.5 bg-background border border-input rounded-lg focus:ring-2 focus:ring-ring/30 focus:border-ring outline-none text-foreground";
const labelClass = "text-sm font-medium text-foreground";

interface ProfileFieldsProps {
  fields: FieldSpec[];
  values: FieldValues;
  onChange: (next: FieldValues) => void;
  /** The values before this edit, so a change can say what it changes. */
  original: FieldValues;
  /** One column in the narrow drawer, two on the sign-up page. */
  columns?: 1 | 2;
}

export function ProfileFields({ fields, values, onChange, original, columns = 2 }: ProfileFieldsProps) {
  const set = (name: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...values, [name]: e.target.value });

  const tier = values.hire_date ? tierFor(values.hire_date) : null;
  const originalTier = original.hire_date ? tierFor(original.hire_date) : null;
  const salary = values.current_annual_salary ?? "";
  const salaryChanged =
    salary !== "" && Number(salary) !== Number(original.current_annual_salary ?? salary);

  return (
    <div className="space-y-4">
      <div className={`grid gap-4 ${columns === 2 ? "sm:grid-cols-2" : ""}`}>
        {fields.map((field) => {
          const id = `profile-${field.name}`;
          return (
            <div key={field.name} className="space-y-2">
              <label className={labelClass} htmlFor={id}>
                {field.label}
              </label>
              {field.type === "status" || field.type === "yesno" ? (
                <select id={id} value={values[field.name] ?? ""} onChange={set(field.name)} className={input}>
                  {!values[field.name] && <option value="">Not recorded</option>}
                  {choices(field).map(([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={id}
                  type={field.type === "date" ? "date" : "number"}
                  step={field.type === "date" ? undefined : "0.01"}
                  inputMode={field.type === "date" ? undefined : "decimal"}
                  min={field.min ?? undefined}
                  max={field.max ?? undefined}
                  value={values[field.name] ?? ""}
                  onChange={set(field.name)}
                  className={input}
                />
              )}
            </div>
          );
        })}
      </div>

      {/*
        Tier is never a field, and the start date is why: it is the whole test.
        Showing the consequence next to the cause is the only thing that makes
        dragging this date across 1 January 2011 visible, and it changes the
        accrual rule.
      */}
      {tier !== null && (
        <div className="rounded-lg border border-border bg-muted/50 p-4 text-sm leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Tier {tier}</span>
          {tier === 2
            ? " — anyone who first joined an Illinois public pension system on or after 1 January 2011. Section 1-160 changes how the annuity accrues."
            : " — first joined before 1 January 2011, so the Article's own accrual applies."}
          {originalTier !== null && tier !== originalTier && (
            <span className="mt-2 block">
              Changing the start date has moved this member from Tier {originalTier} to Tier {tier}, which
              changes the calculation.
            </span>
          )}
        </div>
      )}

      {/*
        The annuity is computed from a four-consecutive-year average, not from
        today's pay, so the salary history moves with this figure. Without that
        the pension barely responds to the field the member just changed.
      */}
      {salaryChanged && (
        <p className="text-sm leading-relaxed text-muted-foreground">
          The salary history behind this member scales with that change, since the annuity is worked
          out from the best consecutive years rather than from current pay. Currently{" "}
          {money(original.current_annual_salary)} → {money(salary)}.
        </p>
      )}
    </div>
  );
}
