"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_BASE_URL, auth, fetchPersonas, type Persona } from "@/lib/api";
import { BrandMark } from "@/components/BrandMark";

/**
 * Tier as the statute has it, mirrored here only to show the consequence of a
 * date while it is being typed. The server derives it again from the same rule
 * and never reads a tier from this form — the column's `CHECK (tier IN (1,2))`
 * would accept a wrong answer silently, and tier selects the accrual rule
 * through Section 1-160 as well as keying the recipe registry.
 */
const TIER_2_FROM = "2011-01-01";
const tierFor = (hireDate: string) => (hireDate >= TIER_2_FROM ? 2 : 1);

const money = (value: string) =>
  Number(value).toLocaleString("en-US", { style: "currency", currency: "USD" });

type Step = "account" | "member" | "review";

export default function SignupPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("account");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [loadError, setLoadError] = useState("");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);

  // Edits start empty and only the ones actually touched are sent, so an
  // untouched field keeps whatever the persona says rather than being
  // round-tripped through this form.
  const [hireDate, setHireDate] = useState("");
  const [salary, setSalary] = useState("");
  const [service, setService] = useState("");

  const [error, setError] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    fetchPersonas()
      .then(setPersonas)
      .catch(() => setLoadError("Could not load the member presets. Is the API running?"));
  }, []);

  const persona = useMemo(
    () => personas.find((p) => p.key === chosen) ?? null,
    [personas, chosen],
  );

  const byArticle = useMemo(() => {
    const groups = new Map<number, Persona[]>();
    for (const option of personas) {
      groups.set(option.article_number, [...(groups.get(option.article_number) ?? []), option]);
    }
    return [...groups.entries()].sort(([a], [b]) => a - b);
  }, [personas]);

  const choose = (option: Persona) => {
    setChosen(option.key);
    setHireDate(option.hire_date);
    setSalary(option.current_annual_salary);
    setService(option.total_service_years);
    setProblems([]);
  };

  const accountReady =
    email.includes("@") && password.length >= 12 && password === confirm;

  const submit = async () => {
    if (!persona) return;
    setError("");
    setProblems([]);
    setIsSubmitting(true);

    const edits: Record<string, string> = {};
    if (hireDate !== persona.hire_date) edits.hire_date = hireDate;
    if (salary !== persona.current_annual_salary) edits.current_annual_salary = salary;
    if (service !== persona.total_service_years) edits.total_service_years = service;

    try {
      const response = await fetch(`${API_BASE_URL}/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, persona: persona.key, edits }),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        // The relational checks come back as a list, and each one names a real
        // inconsistency — hired before they were born, more service than
        // tenure. Showing them individually is more use than one summary line.
        if (Array.isArray(data?.detail?.problems)) {
          setProblems(data.detail.problems);
        } else if (Array.isArray(data?.detail)) {
          setProblems(data.detail.map((d: { msg?: string }) => d.msg ?? String(d)));
        } else {
          setError(data?.detail ?? "Could not create the account.");
        }
        return;
      }

      auth.signIn(data.access_token);
      router.push("/");
    } catch {
      setError("Could not reach the server.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const field =
    "w-full px-4 py-3 bg-background border border-input rounded-lg focus:ring-2 focus:ring-ring/30 focus:border-ring outline-none text-foreground";
  const label = "text-sm font-medium text-foreground";

  return (
    <div className="flex items-center justify-center min-h-screen bg-background text-foreground px-4 py-12">

      <div className="w-full max-w-2xl p-8 space-y-8 bg-card rounded-xl border border-border">
        <div className="text-center space-y-2">
          <div className="flex justify-center">
            <BrandMark size="lg" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Create an account
          </h1>
          <p className="text-sm text-muted-foreground">
            {step === "account" && "First, how you will sign in."}
            {step === "member" && "Now choose which member you would like to be."}
            {step === "review" && "Check the details before we set them up."}
          </p>
        </div>

        {/*
          Said once, plainly, and again on the review step. These are invented
          members, and someone demonstrating this to a real employee otherwise
          produces a figure that person has every reason to believe.
        */}
        <div className="p-4 text-sm text-foreground bg-muted border border-border rounded-xl leading-relaxed">
          These are <strong>synthetic members</strong> built for this prototype —
          not real people and not your own pension. Nothing here is a benefit
          determination; your fund decides that.
        </div>

        {loadError && (
          <div className="p-3 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-lg">
            {loadError}
          </div>
        )}
        {error && (
          <div className="p-3 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-lg">
            {error}
          </div>
        )}
        {problems.length > 0 && (
          <ul className="p-3 space-y-1 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-lg list-disc list-inside">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        )}

        {step === "account" && (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              setStep("member");
            }}
          >
            <div className="space-y-2">
              <label className={label} htmlFor="email">Email address</label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={field}
              />
            </div>
            <div className="space-y-2">
              <label className={label} htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                required
                minLength={12}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={field}
              />
              <p className="text-xs text-muted-foreground">
                At least 12 characters. A phrase you can remember beats a short
                one full of punctuation.
              </p>
            </div>
            <div className="space-y-2">
              <label className={label} htmlFor="confirm">Confirm password</label>
              <input
                id="confirm"
                type="password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={field}
              />
              {confirm.length > 0 && confirm !== password && (
                <p className="text-xs text-destructive">Those do not match.</p>
              )}
            </div>
            <button
              type="submit"
              disabled={!accountReady}
              className="w-full py-3 px-4 bg-primary text-primary-foreground font-medium rounded-lg hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Continue
            </button>
            <p className="text-center text-sm text-muted-foreground">
              Already have one?{" "}
              <Link href="/login" className="text-primary hover:underline">Sign in</Link>
              {" · "}
              <Link href="/" className="text-primary hover:underline">
                Continue without an account
              </Link>
            </p>
          </form>
        )}

        {step === "member" && (
          <div className="space-y-6">
            {byArticle.map(([article, options]) => (
              <div key={article} className="space-y-3">
                <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Article {article}
                </h2>
                <div className="grid gap-3">
                  {options.map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => choose(option)}
                      className={`text-left p-4 rounded-xl border ${
                        chosen === option.key
                          ? "border-primary bg-primary/5"
                          : "border-border hover:border-ring bg-card"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-foreground">{option.label}</span>
                        <span className="text-xs text-muted-foreground">
                          Tier {option.tier} · age {option.age}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-muted-foreground leading-relaxed">
                        {option.summary}
                      </p>
                    </button>
                  ))}
                </div>
              </div>
            ))}
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setStep("account")}
                className="px-4 py-3 text-muted-foreground hover:text-foreground"
              >
                Back
              </button>
              <button
                type="button"
                disabled={!persona}
                onClick={() => setStep("review")}
                className="flex-1 py-3 px-4 bg-primary text-primary-foreground font-medium rounded-lg hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Continue
              </button>
            </div>
          </div>
        )}

        {step === "review" && persona && (
          <div className="space-y-6">
            <div className="p-4 rounded-xl border border-border bg-muted/50 space-y-1">
              <p className="font-medium text-foreground">
                Article {persona.article_number} · {persona.label}
              </p>
              <p className="text-sm text-muted-foreground">{persona.summary}</p>
              <p className="text-sm text-muted-foreground">
                Born {persona.birth_date} · {persona.employment_status}
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <label className={label} htmlFor="hire">Start date</label>
                <input
                  id="hire"
                  type="date"
                  value={hireDate}
                  onChange={(e) => setHireDate(e.target.value)}
                  className={field}
                />
              </div>
              <div className="space-y-2">
                <label className={label} htmlFor="service">Years of service</label>
                <input
                  id="service"
                  type="number"
                  step="0.01"
                  min="0"
                  value={service}
                  onChange={(e) => setService(e.target.value)}
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
                  value={salary}
                  onChange={(e) => setSalary(e.target.value)}
                  className={field}
                />
              </div>
            </div>

            {/*
              Tier is never a field, and the start date is why: it is the whole
              test. Showing the consequence next to the cause is the only thing
              that makes dragging this date across 1 January 2011 visible, and
              it changes the accrual rule.
            */}
            <div className="p-4 rounded-xl border border-border bg-muted/50 text-sm text-muted-foreground leading-relaxed">
              <span className="text-foreground font-medium">
                Tier {tierFor(hireDate)}
              </span>
              {tierFor(hireDate) === 2
                ? " — anyone who first joined an Illinois public pension system on or after 1 January 2011. Section 1-160 changes how the annuity accrues."
                : " — first joined before 1 January 2011, so the Article's own accrual applies."}
              {tierFor(hireDate) !== persona.tier && (
                <span className="block mt-2 text-muted-foreground">
                  Changing the start date has moved this member from Tier{" "}
                  {persona.tier} to Tier {tierFor(hireDate)}, which changes the
                  calculation.
                </span>
              )}
            </div>

            {/*
              The annuity is computed from a four-consecutive-year average, not
              from today's pay, so the salary history moves with this figure.
              Without that the pension barely responds to the field the member
              just changed, and the app looks broken.
            */}
            {salary !== persona.current_annual_salary && (
              <p className="text-sm text-muted-foreground leading-relaxed">
                The ten years of salary history behind this member scale with
                that change, since the annuity is worked out from the best four
                consecutive years rather than from current pay. Currently{" "}
                {money(persona.current_annual_salary)} → {money(salary)}.
              </p>
            )}

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setStep("member")}
                className="px-4 py-3 text-muted-foreground hover:text-foreground"
              >
                Back
              </button>
              <button
                type="button"
                disabled={isSubmitting}
                onClick={submit}
                className="flex-1 py-3 px-4 bg-primary text-primary-foreground font-medium rounded-lg hover:bg-primary/90 disabled:opacity-50 flex items-center justify-center"
              >
                {isSubmitting ? (
                  <div className="w-5 h-5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
                ) : (
                  <span>Create account</span>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
