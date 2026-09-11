import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "@/lib/types";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
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

  // Markdown is re-parsed on every token, so a half-written table renders as
  // literal pipes until its row closes. Everything up to the last blank line is
  // complete enough to parse; the tail after it is shown as plain text and
  // re-renders as markdown the moment the block closes.
  const split = isStreaming ? message.content.lastIndexOf("\n\n") : -1;
  const settled = split > 0 ? message.content.slice(0, split) : message.content;
  const tail = split > 0 ? message.content.slice(split) : "";

  return (
    <div>
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
            }}
          >
            {settled}
          </ReactMarkdown>
          {tail && <div className="whitespace-pre-wrap">{tail}</div>}
        </div>
      )}

      {message.stopped && <p className="mt-2 text-xs text-muted-foreground">Stopped</p>}

      {/* Only the provisions the answer actually relied on, and that passed
          verification. Listing every retrieved section implied a reliance the
          answer did not have. */}
      {message.verified_citations && message.verified_citations.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {message.verified_citations.map((section) => {
            const detail = (message.citations ?? []).find((c) => c.section === section);
            return (
              <div
                key={section}
                className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs"
                title={`40 ILCS 5/${section}`}
              >
                <span className="font-medium tabular-nums text-primary">§ {section}</span>
                {detail?.title && (
                  <span className="max-w-[16rem] truncate text-muted-foreground">· {detail.title}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
