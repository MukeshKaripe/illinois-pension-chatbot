import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cardFrom, isStopped, type CalculationCardData } from "@/lib/card";
import { linkSections, sectionFromHref } from "@/lib/sections";
import type { Message } from "@/lib/types";
import { CalculationCard } from "./CalculationCard";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
  onOpenSection: (section: string) => void;
  onOpenCalculation: (card: CalculationCardData) => void;
}

export function ChatMessage({ message, isStreaming, onOpenSection, onOpenCalculation }: ChatMessageProps) {
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
  const card = cardFrom(message.tool_calls);
  const stopped = message.stopped || isStopped(message.tool_calls);
  const verified = message.verified_citations ?? [];

  // Markdown is re-parsed on every token, so a half-written table renders as
  // literal pipes until its row closes. Everything up to the last blank line is
  // complete enough to parse; the tail after it is shown as plain text and
  // re-renders as markdown the moment the block closes.
  const split = isStreaming ? message.content.lastIndexOf("\n\n") : -1;
  const settled = split > 0 ? message.content.slice(0, split) : message.content;
  const tail = split > 0 ? message.content.slice(split) : "";

  return (
    <div>
      {card && !isStreaming && (
        <CalculationCard
          card={card}
          onOpen={() => onOpenCalculation(card)}
          onOpenSection={onOpenSection}
        />
      )}

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

      {stopped && (
        <p className="mt-2 text-xs text-muted-foreground">Stopped — not finished or checked</p>
      )}

      {/* Only the provisions the answer actually relied on, and that passed
          verification. Listing every retrieved section implied a reliance the
          answer did not have. */}
      {verified.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
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
  );
}
