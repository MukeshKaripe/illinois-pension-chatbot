/**
 * Section references in an answer, made openable.
 *
 * Only sections the answer rested on and that passed verification become
 * links — the same set the citation chips show — so a link never implies a
 * reliance the answer did not have. A bare "40 ILCS 5/11-134" is left alone:
 * the chips already cover it, and linking inside citations reads as clutter.
 */

const SECTION_REF = /(§§?\s*|Section\s+)(\d{1,2}[A-C]?-\d{1,4}(?:\.\d{1,3})?[a-z]?)/g;
const PREFIX = "#section-";

export function linkSections(markdown: string, verified: readonly string[]): string {
  if (!verified.length) return markdown;
  const allowed = new Set(verified);
  return markdown.replace(SECTION_REF, (match: string, _lead: string, id: string, offset: number, whole: string) =>
    // Already the text of a link: leave it as it is.
    allowed.has(id) && whole[offset - 1] !== "[" ? `[${match}](${PREFIX}${id})` : match,
  );
}

export function sectionFromHref(href: string | undefined): string | null {
  return href?.startsWith(PREFIX) ? href.slice(PREFIX.length) : null;
}
