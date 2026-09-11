"use client";

import { useState } from "react";
import { updateProfile, type Profile } from "@/lib/api";
import { EXPERIMENTAL_NOTE, editsFrom, valuesFrom } from "@/lib/funds";
import { buttonVariants } from "@/components/ui/button";
import { ProfileFields } from "./ProfileFields";
import { SupportBadge } from "./SupportBadge";

interface ProfilePanelProps {
  profile: Profile;
  isPrimary: boolean;
  onSaved: () => void;
  onMakePrimary: () => void;
}

/** Edit one profile, within the bounds of its own fund. */
export function ProfilePanel({ profile, isPrimary, onSaved, onMakePrimary }: ProfilePanelProps) {
  const initial = valuesFrom(profile.fields, profile);
  const [values, setValues] = useState(initial);
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    // Only what was touched: an untouched field keeps the stored value rather
    // than being round-tripped through this form.
    const edits = editsFrom(profile.fields, initial, values);
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
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {profile.member_label} · Article {profile.article_number}
          <SupportBadge level={profile.support_level} />
        </p>
        <h3 className="mt-1 text-lg font-semibold">{profile.label}</h3>
        <p className="text-sm text-muted-foreground">
          Age {profile.age} · {profile.employment_status} · Tier {profile.tier}
        </p>
        {profile.support_level === "experimental" && (
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{EXPERIMENTAL_NOTE}</p>
        )}
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
        fields={profile.fields}
        values={values}
        onChange={setValues}
        original={initial}
        columns={1}
      />

      <p className="text-xs leading-relaxed text-muted-foreground">
        The Article cannot change here: another fund is another profile. Changes apply to new
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
