import type { Scenario } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

const TOKEN_KEY = "token";
const SESSION_TOKEN_KEY = "anonymousSessionToken";
const SESSION_ID_KEY = "anonymousSessionId";

/**
 * Browser storage, guarded.
 *
 * These run in client components, but a private window or a browser set to
 * block site data throws on access rather than returning null, and one throw
 * during render takes the whole page down. Signing in is not worth losing the
 * app over.
 */
function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* the session simply will not survive a reload */
  }
}

export const auth = {
  token: () => read(TOKEN_KEY),
  isSignedIn: () => read(TOKEN_KEY) !== null,
  signIn: (token: string) => {
    write(TOKEN_KEY, token);
    // The anonymous conversation belongs to a different principal, and the
    // backend scopes it by a token this browser no longer needs. Clearing it
    // avoids sending both credentials on the same request.
    write(SESSION_TOKEN_KEY, null);
    write(SESSION_ID_KEY, null);
  },
  signOut: () => {
    write(TOKEN_KEY, null);
    write(SESSION_TOKEN_KEY, null);
    write(SESSION_ID_KEY, null);
  },
};

/**
 * The anonymous session, remembered across reloads.
 *
 * Anonymous sessions already persist server-side — `sessions.session_token` is
 * what row-level security scopes them by. What was missing is that the browser
 * never kept the token, so every refresh started a new conversation and the
 * previous one became unreachable to everybody, including its author.
 */
export const anonymousSession = {
  token: () => read(SESSION_TOKEN_KEY),
  id: () => read(SESSION_ID_KEY),
  remember: (id: string, token: string) => {
    write(SESSION_ID_KEY, id);
    write(SESSION_TOKEN_KEY, token);
  },
  forget: () => {
    write(SESSION_ID_KEY, null);
    write(SESSION_TOKEN_KEY, null);
  },
};

/**
 * The credential headers for whichever principal this browser is.
 *
 * Sending `Authorization: Bearer null` is not a no-op — the string "null" is a
 * token as far as the header is concerned, `get_current_user` fails to decode
 * it, and the request 401s. That is why anonymous chat did not work the moment
 * it was reachable, despite being fully built on the server.
 */
export function credentialHeaders(): Record<string, string> {
  const token = auth.token();
  if (token) return { Authorization: `Bearer ${token}` };

  const sessionToken = anonymousSession.token();
  return sessionToken ? { "X-Session-Token": sessionToken } : {};
}

export async function fetchWithAuth(url: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers || {});
  for (const [name, value] of Object.entries(credentialHeaders())) {
    headers.set(name, value);
  }
  headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${url}`, { ...options, headers });

  // Only a signed-in user can have a session to lose. An anonymous 401 means
  // this browser's session token no longer matches anything — worth starting
  // over, not worth being sent to a login page they deliberately skipped.
  if (response.status === 401) {
    if (auth.isSignedIn()) {
      auth.signOut();
      window.location.href = "/login";
    } else {
      anonymousSession.forget();
    }
  }

  return response;
}

/** How well the assistant knows a fund — set in `funds.py`, never inferred. */
export type SupportLevel = "checked" | "experimental";

/** One field of the profile form, with the bounds of the profile's own fund. */
export type FieldSpec = {
  name: string;
  label: string;
  type: "date" | "years" | "money" | "status" | "yesno";
  min: string | null;
  max: string | null;
  options: string[];
  /** A fund's own field, read from and sent under `extra_facts`. */
  extra: boolean;
};

/** What a preset or profile carries about its fund (spec §6, Phase 3b). */
export type FundFacts = {
  /** The fund, in the words a member uses for their employer. */
  member_label: string;
  employer_group: string;
  support_level: SupportLevel;
  /** The edit form is rendered from these, so its bounds are the server's. */
  fields: FieldSpec[];
  /** Values of the fund's own fields, by name — Social Security coverage, say. */
  extra_facts: Record<string, boolean | string>;
};

export type Persona = FundFacts & {
  key: string;
  article_number: number;
  label: string;
  summary: string;
  tier: number;
  age: number;
  birth_date: string;
  hire_date: string;
  employment_status: string;
  total_service_years: string;
  current_annual_salary: string;
};

/** One of the account's profiles: a preset, as edited and saved. */
export type Profile = FundFacts & {
  id: string;
  persona_key: string | null;
  label: string;
  article_number: number;
  tier: number;
  age: number;
  birth_date: string;
  hire_date: string;
  employment_status: string;
  total_service_years: string;
  current_annual_salary: string;
};

/** Who is signed in, and every profile they have — one call, `GET /me`. */
export type Me = {
  email: string;
  display_name: string | null;
  primary_profile_id: string | null;
  profiles: Profile[];
};

/** The fields a profile may change, by `FieldSpec.name`, within its fund's bounds. */
export type ProfileEdits = { [field: string]: string | Record<string, string> };

/** A refused edit comes back as the list of what is inconsistent, not one line. */
export type ProfileResult = { profile: Profile | null; problems: string[] };

async function profileResult(response: Response): Promise<ProfileResult> {
  const data = await response.json().catch(() => ({}));
  if (response.ok) return { profile: data as Profile, problems: [] };
  if (Array.isArray(data?.detail?.problems)) return { profile: null, problems: data.detail.problems };
  if (Array.isArray(data?.detail)) {
    return { profile: null, problems: data.detail.map((d: { msg?: string }) => d.msg ?? String(d)) };
  }
  return { profile: null, problems: [String(data?.detail ?? "That did not work. Please try again.")] };
}

export async function fetchMe(): Promise<Me | null> {
  const response = await fetchWithAuth("/me");
  return response.ok ? response.json() : null;
}

export async function createProfile(persona: string, edits: ProfileEdits): Promise<ProfileResult> {
  return profileResult(
    await fetchWithAuth("/profiles", { method: "POST", body: JSON.stringify({ persona, edits }) }),
  );
}

export async function updateProfile(id: string, edits: ProfileEdits): Promise<ProfileResult> {
  return profileResult(
    await fetchWithAuth(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify({ edits }) }),
  );
}

export async function setPrimaryProfile(id: string): Promise<boolean> {
  return (await fetchWithAuth(`/profiles/${id}/primary`, { method: "POST" })).ok;
}

/** The scenarios a conversation is holding, for the chip row. */
export async function fetchScenarios(sessionId: string): Promise<Scenario[]> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/scenarios`);
  return response.ok ? response.json() : [];
}

/** Forget one scenario, or all of them; resolves to what remains, or null on failure. */
export async function forgetScenario(sessionId: string, label?: string): Promise<Scenario[] | null> {
  const query = label ? `?label=${encodeURIComponent(label)}` : "";
  const response = await fetchWithAuth(`/sessions/${sessionId}/scenarios${query}`, {
    method: "DELETE",
  });
  return response.ok ? response.json() : null;
}

export async function fetchPersonas(): Promise<Persona[]> {
  const response = await fetch(`${API_BASE_URL}/auth/personas`);
  if (!response.ok) throw new Error("Could not load the member presets.");
  return response.json();
}
