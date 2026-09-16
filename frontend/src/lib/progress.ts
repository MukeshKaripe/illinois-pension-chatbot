/** What the member is shown about work that is still happening. */

/** Below this a turn was quick, and showing the machinery is noise. */
export const PROGRESS_AFTER_SECONDS = 4;

export type ProgressStep = { text: string; at: number };

/**
 * The steps worth showing.
 *
 * Nothing at all unless the turn ran long enough to make the member wait — and
 * then the whole sequence, because the point is to explain the wait rather than
 * to narrate the last few seconds of it.
 *
 * The decision is here rather than on the server because whether a turn was
 * fast is only knowable in hindsight: the server reports every step as it
 * starts, carrying how long it had been going, and this decides what that was
 * worth showing.
 */
export const visibleSteps = (
  steps: ProgressStep[],
  { after = PROGRESS_AFTER_SECONDS }: { after?: number } = {},
): ProgressStep[] => (steps.some((step) => step.at >= after) ? steps : []);
