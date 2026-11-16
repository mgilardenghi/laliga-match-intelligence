export type ChartIds = {
  readonly base: string;
  readonly titleId: string;
  readonly descId: string;
  readonly labelledBy: string;
};

function slugify(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
}

function hash(text: string): string {
  let value = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 16777619);
  }
  return (value >>> 0).toString(36);
}

/**
 * Build deterministic aria ids for a chart. The same text always produces the
 * same ids, so two charts with the same title need an explicit `id`.
 */
export function chartIds(text: string, id?: string): ChartIds {
  const slug = slugify(text) || "chart";
  const base = id !== undefined && id.length > 0 ? slugify(id) || "chart" : `${slug}-${hash(text)}`;
  return {
    base,
    titleId: `${base}-title`,
    descId: `${base}-desc`,
    labelledBy: `${base}-title ${base}-desc`,
  };
}
