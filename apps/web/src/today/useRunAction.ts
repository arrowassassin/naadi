import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ApiError } from "../auth/apiError";
import { useCreateBlock, useCreateWeakSpotBlock } from "../practice/api";
import type { Action } from "./format";

/** Runs an action: links navigate, block actions build the block on the server first. */
export function useRunAction() {
  const nav = useNavigate();
  const block = useCreateBlock();
  const weak = useCreateWeakSpotBlock();
  const [failed, setFailed] = useState(false);
  const pending = block.isPending || weak.isPending;
  const open = (id: string, n: number) => void nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: id, n } });
  const run = (a: Action) => {
    setFailed(false);
    switch (a.kind) {
      case "resume_block":
        open(a.blockId, a.n);
        return;
      case "resume_reading":
        void nav({ to: "/learn/$chapterId/$sectionId", params: { chapterId: a.chapterId, sectionId: a.sectionId } });
        return;
      case "start_reading":
        void nav({ to: "/learn/$chapterId", params: { chapterId: a.chapterId } });
        return;
      case "practice":
        void nav({ to: "/practice" });
        return;
      case "cards":
        void nav({ to: "/cards" });
        return;
      case "weak_spot":
        weak.mutate(
          { exam: a.exam, count: a.count, mode: "tutor" },
          { onSuccess: (r) => open(r.block.id, r.block.next_n), onError: () => setFailed(true) },
        );
        return;
      case "block": {
        const make = (status: "unused" | "incorrect" | "all") =>
          block.mutate(
            { exam: a.exam, count: a.count, mode: "tutor", status },
            {
              onSuccess: (r) => open(r.id, r.next_n),
              // Fewer unused questions than the plan asks for: fall back to the whole pool.
              onError: (e) =>
                e instanceof ApiError && e.status === 422 && status === "unused" ? make("all") : setFailed(true),
            },
          );
        make(a.status);
        return;
      }
    }
  };
  return { run, pending, failed };
}
