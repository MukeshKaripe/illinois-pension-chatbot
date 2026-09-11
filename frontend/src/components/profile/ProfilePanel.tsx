"use client";

import { useState } from "react";
import { updateProfile, type Profile, type ProfileEdits } from "@/lib/api";
import { buttonVariants } from "@/components/ui/button";
import { ProfileFields, type FieldValues } from "./ProfileFields";

interface ProfilePanelProps {
  profile: Profile;
  isPrimary: boolean;
  onSaved: () => void;
  onMakePrimary: () => void;
}

/** Edit one profile, within the bounds its preset allows. */
export function ProfilePanel({ profile, isPrimary, onSaved, onMakePrimary }: ProfilePanelProps) {
  const initial: FieldValues = {
    hireDate: profile.hire_date,
    service: profile.total_service_years,
    salary: profile.current_annual_salary,
  };
  const [values, setValues] = useState(initial);
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    // Only what was touched: an untouched field keeps the stored value rather
    // than being round-tripped through this form.
    const edits: ProfileEdits = {};
    if (values.hireDate !== initial.hireDate) edits.hire_date = values.hireDate;
    if (values.service !== initial.service) edits.total_service_years = values.service;
    if (values.salary !== initial.salary) edits.current_annual_salary = values.salary;
    if (!Object.keys(edits).length) {
      onSaved();
      return;
    }
    setSaving(true);
    const result = await updateProfile(profile.id, edits);
    setSaving(false);
    if (result.profile) onSaved();
    else setProblems(result.problems);
  };

  return (
    <div className="space-y-5">
      <div>
        <p className="text-xs text-muted-foreground">
          {profile.fund} · Article {profile.article_number}
        </p>
        <h3 className="mt-1 text-lg font-semibold">{profile.label}</h3>
        <p className="text-sm text-muted-foreground">
          Age {profile.age} · {profile.employment_status} · Tier {profile.tier}
        </p>
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
        original={{ tier: profile.tier, current_annual_salary: profile.current_annual_salary }}
        columns={1}
      />

      <p className="text-xs leading-relaxed text-muted-foreground">
        The Article cannot change here: another fund is another profile. Saved changes apply to new
        chats; a chat already under way keeps the facts it started with.
      </p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className={buttonVariants({ size: "lg", className: "h-10" })}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        {!isPrimary && (
          <button
            type="button"
            onClick={onMakePrimary}
            className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
          >
            Use for new chats
          </button>
        )}
      </div>
    </div>
  );
}
