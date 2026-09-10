import { PanelLeft, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ChatHeaderProps {
  onToggleSidebar: () => void;
  onNewChat: () => void;
  /** On a phone the sidebar is hidden by default, so New chat always shows there. */
  showNewChatOnDesktop: boolean;
}

export function ChatHeader({ onToggleSidebar, onNewChat, showNewChatOnDesktop }: ChatHeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-border px-3">
      <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Toggle sidebar">
        <PanelLeft className="size-[18px]" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onNewChat}
        aria-label="New chat"
        className={showNewChatOnDesktop ? "" : "md:hidden"}
      >
        <SquarePen className="size-[18px]" />
      </Button>
      <h1 className="ml-2 text-sm font-semibold">Illinois Pension Assistant</h1>
      {/* Phase 3: the profile chip sits at the right end of this bar. */}
    </header>
  );
}
