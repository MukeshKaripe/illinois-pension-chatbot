"use client";

import { useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cardsFrom, isStopped, type CalculationCardData } from "@/lib/card";
import { allChosen, suggestionFrom, whichOneQuestion, type Person, type Suggestion } from "@/lib/subject";
import { linkSections, sectionFromHref } from "@/lib/sections";
import { splitStreaming } from "@/lib/streaming";
import type { Message } from "@/lib/types";
import { CalculationCard } from "./CalculationCard";
import { SubjectChoice } from "./SubjectChoice";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
  onOpenSection: (section: string) => void;
  onOpenCalculation: (card: CalculationCardData) => void;
  /** Add a chosen candidate to the conversation; resolves false when that failed. */
  onChoosePerson?: (person: Person, suggestion: Suggestion) => Promise<boolean>;
  /** Ask the question again once every name in it has been chosen. */
  onResend?: (question: string) => void;
}

export function ChatMessage({
  message, isStreaming, onOpenSection, onOpenCalculation, onChoosePerson, onResend,
}: ChatMessageProps) {
  // Above the early returns: a hook after them would run conditionally.
  const [sourcesOpen, setSourcesOpen] = useState(false);
  // Which candidate was chosen from each card of a choice turn, and whether the
  // question has been asked again -- once, when every card has a choice.
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const resent = useRef(false);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-muted px-4 py-2.5">
          {message.content}
        </div>
      </div>
    );
  }

  // Nothing streamed yet: the status line below the list speaks for it.
  if (!message.content && isStreaming) return null;

  // Card and stopped state live in the audit trail, so a live message and a
  // reloaded one render identically.
  const cards = cardsFrom(message.tool_calls);
  const suggestion = suggestionFrom(message.tool_calls);

  const choose = async (index: number, person: Person) => {
    if (!suggestion || !onChoosePerson) return;
    if (!(await onChoosePerson(person, suggestion))) return;
    const next = { ...chosen, [index]: person.ref };
    setChosen(next);
    if (!resent.current && allChosen(suggestion, next)) {
      resent.current = true;
      onResend?.(suggestion.kind === "which_one" ? whichOneQuestion(suggestion.question, person) : suggestion.question);
    }
  };
  const stopped = message.stopped || isStopped(message.tool_calls);
  const verified = message.verified_citations ?? [];

  const { settled, tail } = splitStreaming(message.content, Boolean(isStreaming));

  return (
    <div>
      {!isStreaming &&
        cards.map((card, i) => (
          <CalculationCard
            key={`${card.person ?? "card"}-${i}`}
            card={card}
            onOpen={() => onOpenCalculation(card)}
            onOpenSection={onOpenSection}
          />
        ))}

      {message.content && (
        <div className="prose answer max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // A four-column comparison has nowhere to go in a narrow column.
              // eslint-disable-next-line @typescript-eslint/no-unused-vars
              table: ({ node, ...props }) => (
                <div className="overflow-x-auto">
                  <table {...props} />
                </div>
              ),
              // Section references the answer rested on open the statute text
              // beside the chat; anything else is an ordinary external link.
              // eslint-disable-next-line @typescript-eslint/no-unused-vars
              a: ({ node, href, children, ...props }) => {
                const section = sectionFromHref(href);
                if (section) {
                  return (
                    <button
                      type="button"
                      onClick={() => onOpenSection(section)}
                      className="font-medium text-primary underline decoration-primary/30 underline-offset-2 hover:decoration-primary"
                    >
                      {children}
                    </button>
                  );
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer" {...props}>
                    {children}
                  </a>
                );
              },
            }}
          >
            {linkSections(settled, verified)}
          </ReactMarkdown>
          {tail && <div className="whitespace-pre-wrap">{tail}</div>}
        </div>
      )}

      {suggestion && !isStreaming &&
        suggestion.choices.map((choice, i) => (
          <SubjectChoice
            key={i}
            choice={choice}
            chosenRef={chosen[i]}
            disabled={!onChoosePerson}
            onChoose={(person) => void choose(i, person)}
          />
        ))}

      {stopped && (
        <p className="mt-2 text-xs text-muted-foreground">Stopped — not finished or checked</p>
      )}

      {/* Only the provisions the answer actually relied on, and that passed
          verification. Listing every retrieved section implied a reliance the
          answer did not have.

          Collapsed by default, and the only place the answer's sources are
          gathered: the prose already carries them inline as clickable
          citations, and a "Legal basis" line used to restate them a third time
          from the backend. Click rather than hover, because hover has no touch
          equivalent and would put the chips out of reach on a phone. */}
      {verified.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setSourcesOpen((open) => !open)}
            aria-expanded={sourcesOpen}
            className="-ml-1.5 flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ChevronRight
              className={`size-3.5 transition-transform ${sourcesOpen ? "rotate-90" : ""}`}
              aria-hidden
            />
            Legal basis
            <span className="tabular-nums">
              · {verified.length} {verified.length === 1 ? "provision" : "provisions"}
            </span>
          </button>

          {sourcesOpen && (
            <div className="mt-2 flex flex-wrap gap-2">
              {verified.map((section) => {
                const detail = (message.citations ?? []).find((c) => c.section === section);
                return (
                  <button
                    key={section}
                    type="button"
                    onClick={() => onOpenSection(section)}
                    className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs hover:bg-muted"
                    title={`40 ILCS 5/${section}`}
                  >
                    <span className="font-medium tabular-nums text-primary">§ {section}</span>
                    {detail?.title && (
                      <span className="max-w-[16rem] truncate text-muted-foreground">· {detail.title}</span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
