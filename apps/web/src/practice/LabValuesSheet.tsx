import { ErrorState, Skeleton } from "@naadi/ui";
import { ExternalLink } from "lucide-react";
import { Sheet } from "../learn/Sheet";
import { useLabValues } from "./api";
import { useCopy } from "./copy";

/**
 * The lab-values reference panel: Naadi's own table from app_config (GET /api/lab-values). The exam board's
 * table is © and is only linked to, through its `sources` row.
 */
export function LabValuesSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useCopy();
  const labs = useLabValues(open);
  return (
    <Sheet open={open} onClose={onClose} title={t("practice.labs")} className="tablet:w-[min(46rem,94vw)]">
      {labs.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <p role="status" className="sr-only">
            Loading lab values…
          </p>
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-48 rounded-block" />
        </div>
      ) : labs.data ? (
        <div className="flex flex-col gap-6 pb-2">
          {labs.data.groups.map((g) => (
            <section key={g.id} aria-labelledby={`labs-${g.id}`} className="flex flex-col">
              <h3 id={`labs-${g.id}`} className="m-0 pb-2 font-mono text-label text-text-3 uppercase">
                {g.label}
              </h3>
              <table className="w-full table-fixed border-collapse text-left text-ui-sm">
                <thead className="sr-only">
                  <tr>
                    <th scope="col">Test</th>
                    <th scope="col">Reference range</th>
                    <th scope="col">SI units</th>
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((r) => (
                    <tr key={r.analyte} className="border-line-soft border-t align-top">
                      <th scope="row" className="w-[42%] py-2.5 pr-3 font-medium text-text">
                        {r.analyte}
                      </th>
                      <td className="py-2.5 pr-3 font-mono text-text-2">{r.range}</td>
                      <td className="py-2.5 font-mono text-text-3">{r.si ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
          <p className="m-0 text-text-3 text-ui-sm">{t("practice.labs_source")}</p>
          {labs.data.official.url ? (
            <a
              href={labs.data.official.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-2 self-start text-text-2"
            >
              {t("practice.labs_official")}
              <ExternalLink aria-hidden="true" size={15} />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : null}
        </div>
      ) : (
        <ErrorState
          title={t("practice.load_error_title")}
          message={labs.error?.message ?? ""}
          onRetry={() => void labs.refetch()}
          retrying={labs.isFetching}
        />
      )}
    </Sheet>
  );
}
