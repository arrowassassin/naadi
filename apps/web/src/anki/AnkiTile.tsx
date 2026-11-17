import { Tile, TileLabel } from "@naadi/ui";
import { useNavigate } from "@tanstack/react-router";
import { useUiText } from "../copy/useUiText";
import { AnkiActions } from "./AnkiActions";
import { useAnkiEnabled } from "./api";

/** The Library's Anki tile (S15 mounts it): import a deck in, export cards out. Absent while `features.anki` is off. */
export function AnkiTile() {
  const t = useUiText();
  const on = useAnkiEnabled();
  const navigate = useNavigate();
  if (!on) return null;
  return (
    <Tile as="section" aria-labelledby="anki-tile-h" className="flex flex-col gap-3">
      <TileLabel>{t("anki.tile_title")}</TileLabel>
      <h2 id="anki-tile-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("anki.tile_body")}
      </h2>
      <div className="flex flex-wrap gap-3">
        <AnkiActions onReview={() => void navigate({ to: "/cards" })} />
      </div>
    </Tile>
  );
}
