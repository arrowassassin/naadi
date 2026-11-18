import { Tile } from "@naadi/ui";
import { ExternalLink } from "lucide-react";
import { useCopy } from "../practice/copy";
import { OFFICIAL_GROUPS } from "./officialLinks";

/** `/practice/official`: the official free practice and self-assessments, as clearly labelled links only. */
export function OfficialPracticePage() {
  const t = useCopy();
  return (
    <section aria-labelledby="official-h" className="flex flex-col gap-8 pt-6 tablet:pt-10" data-testid="official-page">
      <h1
        id="official-h"
        className="m-0 font-display font-extrabold text-[clamp(2.75rem,1.5rem+5vw,5.5rem)] leading-[0.92] tracking-[-0.04em]"
      >
        {t("official.headline")} <span className="text-text-muted">{t("official.headline_muted")}</span>
      </h1>
      <p className="m-0 max-w-[48rem] text-text-2">{t("official.intro")}</p>
      <Tile as="section" aria-labelledby="official-how-h" className="flex flex-col gap-3">
        <h2 id="official-how-h" className="m-0 font-bold font-display text-[1.5rem] tracking-[-0.02em]">
          {t("official.how_title")}
        </h2>
        <ol className="m-0 flex flex-col gap-2 pl-5 text-text-2">
          <li>{t("official.how_1")}</li>
          <li>{t("official.how_2")}</li>
          <li>{t("official.how_3")}</li>
        </ol>
      </Tile>
      <div className="grid gap-5 tablet:grid-cols-2">
        {OFFICIAL_GROUPS.map((g) => (
          <Tile as="section" key={g.id} aria-labelledby={`official-${g.id}-h`} className="flex min-w-0 flex-col gap-3">
            <h2 id={`official-${g.id}-h`} className="m-0 font-bold font-display text-[1.25rem] tracking-[-0.02em]">
              {t(`official.group.${g.id}`)}
            </h2>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {g.links.map((l) => (
                <li key={l.id} className="flex min-w-0 flex-col gap-1" data-link={l.id} data-source={l.sourceId}>
                  <a
                    href={l.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-11 items-center gap-2 font-semibold text-text underline decoration-line-strong underline-offset-4"
                  >
                    <span className="min-w-0 break-words">{t(`official.link.${l.id}.title`)}</span>
                    <ExternalLink aria-hidden="true" size={16} className="shrink-0" />
                    <span className="sr-only">{t("official.new_tab")}</span>
                  </a>
                  <span className="text-ui-sm text-text-2">{t(`official.link.${l.id}.note`)}</span>
                </li>
              ))}
            </ul>
          </Tile>
        ))}
      </div>
      <p className="m-0 text-ui-sm text-text-3">{t("official.legal")}</p>
    </section>
  );
}
