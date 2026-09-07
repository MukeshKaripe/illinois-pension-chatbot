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
  };
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";

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
            <div className="prose prose-invert prose-p:leading-relaxed prose-pre:bg-neutral-950 prose-pre:border prose-pre:border-neutral-800 max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Citations block for AI messages */}
        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 px-1">
            {message.citations.map((cite, idx) => (
              <div
                key={idx}
                className="flex items-center space-x-1.5 px-3 py-1.5 bg-neutral-900 border border-neutral-800 rounded-lg text-xs text-neutral-400 hover:text-neutral-200 hover:border-neutral-600 transition-colors cursor-pointer"
                title={cite.text}
              >
                <BookOpen size={12} className="text-blue-400" />
                <span className="font-medium">
                  {cite.article ? `Art ${cite.article}` : "Source"}
                </span>
                {cite.relevance_score && (
                  <span className="opacity-50">
                    ({(cite.relevance_score * 100).toFixed(0)}%)
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="mt-2 text-[10px] text-neutral-600 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {format(new Date(message.created_at), "h:mm a")}
        </div>
      </div>
    </div>
  );
}
