import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { format } from "date-fns";
import { BookOpen } from "lucide-react";

interface ChatMessageProps {
  message: {
    id: string;
    role: string;
    content: string;
    created_at: string;
    citations?: any[];
    verified_citations?: string[];
  };
  isStreaming?: boolean;
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  const isUser = message.role === "user";

  // Markdown is re-parsed on every token, so a half-written table renders as
  // literal pipes until its row closes. Everything up to the last blank line is
  // complete enough to parse; the tail after it is shown as plain text and
  // re-renders as markdown the moment the block closes.
  const split = isStreaming ? message.content.lastIndexOf("\n\n") : -1;
  const settled = split > 0 ? message.content.slice(0, split) : message.content;
  const tail = split > 0 ? message.content.slice(split) : "";

  return (
    <div className={`flex w-full ${isUser ? "justify-end" : "justify-start"} group`}>
      <div
        className={`max-w-[80%] flex flex-col ${
          isUser ? "items-end" : "items-start"
        }`}
      >
        <div
          className={`px-5 py-4 rounded-2xl ${
            isUser
              ? "bg-blue-600 text-white rounded-br-sm shadow-md"
              : "bg-neutral-900 border border-neutral-800 text-neutral-200 rounded-bl-sm shadow-xl"
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : (
            <div className="prose prose-invert prose-p:leading-relaxed prose-h3:text-sm prose-h3:font-semibold prose-h3:text-neutral-300 prose-h3:mt-4 prose-h3:mb-2 prose-pre:bg-neutral-950 prose-pre:border prose-pre:border-neutral-800 max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // A four-column comparison inside an 80%-wide bubble has
                  // nowhere to go. Give it somewhere.
                  // eslint-disable-next-line @typescript-eslint/no-unused-vars
                  table: ({ node, ...props }) => (
                    <div className="overflow-x-auto -mx-1 px-1">
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
        </div>

        {/* Only the provisions the answer actually relied on, and that passed
            verification. Listing every retrieved section implied a reliance the
            answer did not have. */}
        {!isUser && message.verified_citations && message.verified_citations.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 px-1">
            {message.verified_citations.map((section: string) => {
              const detail = (message.citations ?? []).find(
                (c) => c.section === section
              );
              return (
                <div
                  key={section}
                  className="flex items-center space-x-1.5 px-3 py-1.5 bg-neutral-900 border border-neutral-800 rounded-lg text-xs text-neutral-400"
                  title={detail?.title ?? ""}
                >
                  <BookOpen size={12} className="text-blue-400" />
                  <span className="font-medium">40 ILCS 5/{section}</span>
                  {detail?.title && (
                    <span className="opacity-60 max-w-[16rem] truncate">
                      {detail.title}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-2 text-[10px] text-neutral-600 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {format(new Date(message.created_at), "h:mm a")}
        </div>
      </div>
    </div>
  );
}
