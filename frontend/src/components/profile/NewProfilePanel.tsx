"use client";

import { useEffect, useState } from "react";
import {
  createProfile,
  fetchPersonas,
  type Persona,
  type Profile,
  type ProfileEdits,
} from "@/lib/api";
import { buttonVariants } from "@/components/ui/button";
import { PresetPicker } from "./PresetPicker";
import { ProfileFields, type FieldValues } from "./ProfileFields";

/**
 * Another profile, from a preset. Never from a blank form: the record has to
 * stay a voice independent of the member's (see `personas.py`).
 */
export function NewProfilePanel({ onCreated }: { onCreated: (profile: Profile) => void }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [loadError, setLoadError] = useState("");
  const [chosen, setChosen] = useState<Persona | null>(null);
  const [values, setValues] = useState<FieldValues>({ hireDate: "", service: "", salary: "" });
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchPersonas()
      .then(setPersonas)
      .catch(() => setLoadError("Could not load the member presets."));
  }, []);

  const choose = (option: Persona) => {
    setChosen(option);
    setValues({
      hireDate: option.hire_date,
      service: option.total_service_years,
      salary: option.current_annual_salary,
    });
    setProblems([]);
  };

  const create = async () => {
    if (!chosen) return;
    const edits: ProfileEdits = {};
    if (values.hireDate !== chosen.hire_date) edits.hire_date = values.hireDate;
    if (values.service !== chosen.total_service_years) edits.total_service_years = values.service;
    if (values.salary !== chosen.current_annual_salary) edits.current_annual_salary = values.salary;
    setSaving(true);
    const result = await createProfile(chosen.key, edits);
    setSaving(false);
    if (result.profile) onCreated(result.profile);
    else setProblems(result.problems);
  };

  return (
    <div className="space-y-5">
      <p className="text-sm leading-relaxed text-muted-foreground">
        Start from a member like the one you want to explore: who they work for, then where they
        are in their career. Chats started with a profile use its facts.
      </p>

      {loadError && (
        <p role="alert" className="text-sm text-destructive">{loadError}</p>
      )}

      {!chosen ? (
        <PresetPicker personas={personas} chosen={null} onChoose={choose} />
      ) : (
        <>
          <div className="rounded-lg border border-border bg-muted/50 p-3 text-sm">
            <p className="font-medium">
              {chosen.fund} · {chosen.label}
            </p>
            <p className="text-muted-foreground">{chosen.summary}</p>
            <button
              type="button"
              onClick={() => setChosen(null)}
              className="mt-1 text-xs text-primary hover:underline"
            >
              Choose another
            </button>
          </div>

          {problems.length > 0 && (
            <ul
              role="alert"
              className="list-inside list-disc space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
            >
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}

          <ProfileFields
            values={values}
            onChange={setValues}
            original={{ tier: chosen.tier, current_annual_salary: chosen.current_annual_salary }}
            columns={1}
          />

          <button
            type="button"
            onClick={create}
            disabled={saving}
            className={buttonVariants({ size: "lg", className: "h-10 w-full" })}
          >
            {saving ? "Creating…" : "Create and start a chat"}
          </button>
        </>
      )}

      <p className="text-xs leading-relaxed text-muted-foreground">
        These are synthetic members built for this prototype — not real people, and not your own
        pension.
      </p>
    </div>
  );
}
