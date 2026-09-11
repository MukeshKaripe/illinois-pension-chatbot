/**
 * The seal: a section mark in brass on Illinois navy.
 *
 * § is what every citation in this product begins with, so it is the one mark
 * that says what the assistant is about. Decorative — the product name always
 * sits beside it — hence aria-hidden.
 */
export function BrandMark({ size = "sm" }: { size?: "sm" | "lg" }) {
  const box = size === "lg" ? "size-10 rounded-lg text-2xl" : "size-6 rounded-md text-sm";
  return (
    <span
      aria-hidden="true"
      className={`inline-flex shrink-0 items-center justify-center bg-seal font-serif leading-none text-brass ${box}`}
    >
      §
    </span>
  );
}
