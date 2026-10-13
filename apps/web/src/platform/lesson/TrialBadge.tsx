/** Model trials (ADR-056): the model an admin made a lesson with instead of the normal one. */
import { FlaskConical } from "lucide-react";
import { Badge } from "../ui/core";

/** The trial model's short name (after the provider), with the full id on hover; nothing for a normal lesson. */
export function TrialBadge({ model }: { model: string | null | undefined }) {
  if (!model) return null;
  return (
    <span className="trial-badge" title={`Made with ${model} through OpenRouter (model trial)`}>
      <Badge tone="warning" icon={FlaskConical}>
        {model.split("/").pop()}
      </Badge>
    </span>
  );
}
