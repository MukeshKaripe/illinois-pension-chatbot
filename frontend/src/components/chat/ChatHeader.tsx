import { PanelLeft, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/BrandMark";

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
      <h1 className="ml-2 flex items-center gap-2 text-sm font-semibold">
        <BrandMark />
        Illinois Pension Assistant
        <span className="hidden rounded border border-border px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground sm:inline">
          40 ILCS 5
        </span>
      </h1>
    </header>
  );
}
