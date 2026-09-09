"use client";

import { ChatInterface } from "@/components/chat/ChatInterface";

/**
 * The front door, and it does not require an account.
 *
 * This used to redirect anyone without a token straight to `/login`, which made
 * the anonymous path unreachable despite being fully built on the server —
 * nullable `Session.employee_id`, `session_token`, the `X-Session-Token` branch
 * in `send_message`, its own row-level security predicate, and the agent's
 * whole no-record path.
 *
 * Leading with it is also the honest arrangement. Most of what this assistant
 * answers well — what a provision says, who an Article covers, what changes at
 * 60 — needs no record at all, and a member who states their own age and
 * service gets a figure whose every input traces to their own message. Signing
 * in adds a member record and conversation history, which is what the sign-up
 * prompt should say rather than pretending it is the price of entry.
 */
export default function Home() {
  return <ChatInterface />;
}
