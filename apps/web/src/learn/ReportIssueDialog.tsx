import type { ReportIssueIn } from "@naadi/shared";
import { Button, ErrorState } from "@naadi/ui";
import { CheckCircle2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useReportIssue } from "./api";
import { Sheet } from "./Sheet";

const MAX = 4000;

export function ReportIssueDialog({
  open,
  onClose,
  refType,
  refId,
  what,
}: {
  open: boolean;
  onClose: () => void;
  refType: ReportIssueIn["ref_type"];
  refId: string;
  what: string;
}) {
  const report = useReportIssue();
  const [body, setBody] = useState("");
  const fieldId = useId();
  const hintId = useId();

  const { reset } = report;
  useEffect(() => {
    if (open) {
      reset();
      setBody("");
    }
  }, [open, reset]);

  const text = body.trim();
  return (
    <Sheet open={open} onClose={onClose} title="Report an issue">
      {report.isSuccess ? (
        <div role="status" className="flex flex-col items-start gap-4 pb-2">
          <p className="m-0 flex items-center gap-2 text-text">
            <CheckCircle2 aria-hidden="true" size={20} />
            Thanks. Your report about {what} was sent.
          </p>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4 pb-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text && !report.isPending) report.mutate({ ref_type: refType, ref_id: refId, body: text });
          }}
        >
          <label htmlFor={fieldId} className="font-semibold">
            What is wrong with {what}?
          </label>
          <textarea
            id={fieldId}
            aria-describedby={hintId}
            value={body}
            maxLength={MAX}
            rows={5}
            onChange={(e) => setBody(e.target.value)}
            className="min-h-32 w-full resize-y rounded-block border border-line-control bg-surface-2 p-3 text-[1rem] text-text"
          />
          <span id={hintId} className="text-text-3 text-ui-sm">
            A typo, a wrong fact, a figure that does not load… {body.length} / {MAX}
          </span>
          {report.isError ? (
            <ErrorState
              title="The report was not sent"
              message={report.error.message}
              requestId={report.error instanceof ApiError ? report.error.requestId : null}
            />
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="strong" disabled={!text || report.isPending}>
              {report.isPending ? "Sending…" : "Send report"}
            </Button>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}
