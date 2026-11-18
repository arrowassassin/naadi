import { Button, Chip, Tile, TileLabel } from "@naadi/ui";
import { Check, ShieldCheck, Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { count } from "../learn/format";
import { requestErrorKey, useConfirmPlacements, useVaultDecision } from "./api";
import { batchSummary, type ConfirmGroup, defaultOption, type Item, optionWording, signalKey } from "./model";

/**
 * The assistant's proposal for where a batch goes (violet: an assistant proposal, segment 15). One card per batch;
 * each file shows its options (recommended preselected, alternatives listed) and the card has one Confirm, the
 * view's lime action when `primary`. Nothing is placed before the student confirms.
 */
export function ConfirmCard({ group, primary }: { group: ConfirmGroup; primary: boolean }) {
  const t = useUiText();
  const id = useId();
  const confirm = useConfirmPlacements();
  const [chosen, setChosen] = useState<Map<string, string>>(
    () => new Map(group.items.map((i) => [i.id, defaultOption(i)?.option_id ?? ""])),
  );
  const pick = (item: Item, option: string) => setChosen((m) => new Map(m).set(item.id, option));
  const many = group.items.length > 1;

  async function submit() {
    const choices = group.items.flatMap((i) => {
      const option = chosen.get(i.id) || defaultOption(i)?.option_id;
      return option ? [{ item_id: i.id, option_id: option }] : [];
    });
    if (!choices.length || confirm.isPending) return;
    try {
      await confirm.mutateAsync({ batchId: group.batchId, choices, items: group.items });
    } catch {
      // worded below
    }
  }

  return (
    <Tile
      as="article"
      variant="assistant"
      aria-labelledby={`${id}-h`}
      className="flex flex-col gap-5"
      data-confirm-card=""
      data-batch={group.batchId ?? undefined}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TileLabel variant="assistant">{t("library.confirm_label")}</TileLabel>
        <Chip tone="assistant" icon={<Sparkles size={14} />}>
          {t("library.status.needs_you")}
        </Chip>
      </div>
      <div className="flex flex-col gap-1">
        <h3 id={`${id}-h`} className="m-0 font-display text-[1.375rem] leading-tight tracking-[-0.02em]">
          {many
            ? t("library.confirm_title_batch", { files: count(t, "library.count_file", group.items.length) })
            : t("library.confirm_title")}
        </h3>
        <p className="m-0 text-ui-sm text-violet-text" data-confirm-summary="">
          {batchSummary(t, group.items, chosen)}
        </p>
      </div>
      <ul className="m-0 flex list-none flex-col gap-4 p-0">
        {group.items.map((item) => (
          <li key={item.id} className="flex flex-col gap-2">
            <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
              <legend className="mb-1 p-0 font-semibold [overflow-wrap:anywhere]">
                {item.name ?? t("library.untitled")}
              </legend>
              {item.duplicate ? (
                <p className="m-0 text-text-2 text-ui-sm">{t(`library.duplicate_${item.duplicate}`)}</p>
              ) : null}
              {item.options.map((o) => {
                const w = optionWording(t, o, item.name);
                const checked = (chosen.get(item.id) || defaultOption(item)?.option_id) === o.option_id;
                return (
                  <label
                    key={o.option_id}
                    className="flex min-h-12 cursor-pointer items-start gap-3 rounded-block border border-violet-line bg-ground/40 px-4 py-3 has-[:checked]:border-violet-text"
                  >
                    <input
                      type="radio"
                      name={`${id}-${item.id}`}
                      value={o.option_id}
                      checked={checked}
                      onChange={() => pick(item, o.option_id)}
                      className="mt-1 size-4 shrink-0 accent-[var(--color-violet)]"
                    />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold [overflow-wrap:anywhere]">{w.title}</span>
                        {o.recommended ? <Chip tone="assistant">{t("library.recommended")}</Chip> : null}
                      </span>
                      {w.detail ? <span className="text-text-2 text-ui-sm">{w.detail}</span> : null}
                    </span>
                  </label>
                );
              })}
            </fieldset>
          </li>
        ))}
      </ul>
      {confirm.error ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t(requestErrorKey(confirm.error))}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={primary ? "primary" : "strong"}
          size="cta"
          icon={<Check size={18} />}
          disabled={confirm.isPending}
          onClick={() => void submit()}
        >
          {confirm.isPending ? t("library.confirming") : t("library.confirm")}
        </Button>
        <span className="text-text-2 text-ui-sm">{t("library.confirm_hint")}</span>
      </div>
    </Tile>
  );
}

/**
 * The Vault prompt: the local check thinks the file is a personal document. It waits here, before any model has
 * seen it; Vault files are never sent to AI. Signals are worded from their codes.
 */
export function VaultCard({ item, primary }: { item: Item; primary: boolean }) {
  const t = useUiText();
  const id = useId();
  const decide = useVaultDecision();
  const signals = item.gate?.signals ?? [];
  const send = (vault: "vault" | "continue" | "discard") => {
    if (!decide.isPending) decide.mutate({ item, vault });
  };
  return (
    <Tile
      as="article"
      variant="assistant"
      aria-labelledby={`${id}-h`}
      className="flex flex-col gap-4"
      data-vault-card=""
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TileLabel variant="assistant">{t("library.vault_label")}</TileLabel>
        <Chip tone="assistant" icon={<ShieldCheck size={14} />}>
          {t("library.status.personal")}
        </Chip>
      </div>
      <h3 id={`${id}-h`} className="m-0 font-display text-[1.375rem] leading-tight tracking-[-0.02em]">
        {t("library.vault_title")}
      </h3>
      <p className="m-0 font-semibold [overflow-wrap:anywhere]">{item.name ?? t("library.untitled")}</p>
      <p className="m-0 text-text-2">{t("library.vault_body")}</p>
      {signals.length ? (
        <div className="flex flex-col gap-2">
          <span className="text-text-2 text-ui-sm">{t("library.vault_signals")}</span>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {[...new Set(signals.map(signalKey))].map((key) => (
              <li key={key}>
                <Chip tone="assistant">{t(key)}</Chip>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {decide.error ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t(requestErrorKey(decide.error))}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button
          variant={primary ? "primary" : "strong"}
          icon={<ShieldCheck size={16} />}
          disabled={decide.isPending}
          onClick={() => send("vault")}
        >
          {t("library.vault_store")}
        </Button>
        <Button variant="secondary" disabled={decide.isPending} onClick={() => send("continue")}>
          {t("library.vault_not_personal")}
        </Button>
        <Button variant="secondary" disabled={decide.isPending} onClick={() => send("discard")}>
          {t("library.vault_discard")}
        </Button>
      </div>
    </Tile>
  );
}
