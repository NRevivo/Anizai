import type { SessionStatus } from '../types';

/**
 * Decide whether the follow-up composer (text input, send button, and the
 * suggested-action chips) should render for a session.
 *
 * A follow-up only makes sense once there is a forecast result to ask about,
 * so the composer is withheld entirely until then. Both inputs are required:
 *
 * - `status === 'done'` is the lifecycle signal. It alone is not enough: a
 *   session can read `done` while `sessionResults/{id}` is absent, in which
 *   case the BFF returns `result: null`.
 * - `hasForecastResult` is `SessionDetail.result != null`. It alone is not
 *   enough either: a result can outlive a re-queued run, because the
 *   clarification path re-queues an existing session rather than creating a
 *   new one.
 *
 * Note that the frontend `Prediction` object cannot stand in for
 * `hasForecastResult` — `toPrediction` in `App.tsx` returns a non-null
 * `Prediction` for any loaded session, filling probability and confidence with
 * `0` while the run is still in flight.
 *
 * This gates the input surface only. Follow-up message history is always
 * rendered, including on sessions that never produced a result.
 */
export function shouldShowFollowUpComposer(
    status: SessionStatus | null | undefined,
    hasForecastResult: boolean
): boolean {
    return status === 'done' && hasForecastResult;
}
