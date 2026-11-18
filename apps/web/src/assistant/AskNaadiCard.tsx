import { Button, Tile, TileLabel } from "@naadi/ui";
import { MessageCircle, Sparkles } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { type AssistantAskContext, askAssistant, useAssistantAvailable } from "./ask";

/**
 * The assistant's coaching card (Qbank board): shown after the student answered, never before (the page
 * decides when; the server re-checks the attempt anyway). Renders nothing while the assistant cannot answer.
 */
export function AskNaadiCard({ context }: { context: AssistantAskContext }) {
  const t = useUiText();
  const available = useAssistantAvailable();
  if (!available) return null;
  return (
    <Tile
      variant="assistant"
      as="aside"
      aria-label={t("agent.coach.label")}
      className="flex flex-col gap-3"
      data-ask-card=""
    >
      <TileLabel variant="assistant">{t("agent.title")}</TileLabel>
      <p className="m-0 text-[1.0625rem] text-text leading-normal">{t("agent.coach.body")}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="strong"
          icon={<Sparkles size={16} />}
          onClick={() => askAssistant({ kind: "explain", context })}
        >
          {t("agent.coach.explain")}
        </Button>
        <Button
          variant="secondary"
          icon={<MessageCircle size={16} />}
          className="border-violet-line text-violet-text"
          onClick={() => askAssistant({ kind: "ask", context })}
        >
          {t("agent.coach.ask")}
        </Button>
      </div>
    </Tile>
  );
}
