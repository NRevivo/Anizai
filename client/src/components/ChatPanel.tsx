import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage, SuggestedAction } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { StateMessage } from './ui/StateMessage';
import { formatRelativeTime } from '../lib/utils';
import type { FollowUpPendingState } from '../lib/followUpPending';
import {
    decideAutoScroll,
    getDistanceFromBottom,
    isNearBottom,
    resolveScrollBehavior,
} from '../lib/followUpScroll';
import { prefersReducedMotion } from '../lib/autoScroll';
import ReactMarkdown from 'react-markdown';

// Staggered delays for the three dots. Kept out of the class list because
// Tailwind has no arbitrary animation-delay utility configured here.
const DOT_DELAYS_MS = [0, 160, 320];

/**
 * Animated "the assistant is composing a reply" indicator, styled as an
 * assistant chat bubble so it sits in the thread where the answer will land.
 *
 * Motion: each dot runs the `thinking-dot` keyframes (tailwind.config.js) with a
 * staggered delay. Both the opacity and the offset live in the keyframes, so the
 * un-animated base state is three solid, fully visible dots — that is the
 * fallback the global `prefers-reduced-motion` rule in index.css produces, and
 * `motion-reduce:animate-none` states the same intent locally.
 *
 * Accessibility: the dots are decorative and hidden from assistive tech; the
 * `role="status"` container carries a screen-reader-only sentence instead, so
 * the pending state is announced politely rather than as a row of bullets.
 */
function ThinkingIndicator() {
    return (
        <div className="flex justify-start">
            <div
                role="status"
                aria-live="polite"
                className="min-w-0 rounded-lg border border-gray-100 bg-white px-3.5 py-3 shadow-sm"
            >
                <span className="sr-only">Thinking about your follow-up. The answer will appear here.</span>
                <span aria-hidden="true" className="flex items-center gap-1.5">
                    {DOT_DELAYS_MS.map((delay) => (
                        <span
                            key={delay}
                            style={{ animationDelay: `${delay}ms` }}
                            className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500 animate-thinking-dot motion-reduce:animate-none"
                        />
                    ))}
                </span>
            </div>
        </div>
    );
}

interface ChatPanelProps {
    messages: ChatMessage[];
    suggestedActions: SuggestedAction[];
    // Identifies which session the thread belongs to, so scroll state can be
    // reset on a switch instead of carrying one session's position into the
    // next. Not used for fetching — ChatPanel is presentational.
    sessionId?: string | null;
    isLoading?: boolean;
    isSendingMessage?: boolean;
    // T3 send-lock: true while the session is still producing an answer
    // (initial forecast processing, or a prior follow-up not yet answered).
    // Blocks sending a new message; the input stays editable so the user can
    // draft while they wait.
    isSendLocked?: boolean;
    // False until the session has a completed forecast result. There is nothing
    // to ask about before then, so the composer and the suggested-action chips
    // are not rendered at all. Message history is never gated on this.
    isComposerVisible?: boolean;
    // 'thinking' animates the dot indicator; 'stalled' replaces it with a static
    // notice once the answer is overdue, so the animation can never spin forever
    // when the hub goes silent. 'idle' renders nothing.
    followUpPendingState?: FollowUpPendingState;
    currentQuestion?: string;
    currentAnswer?: string;
    onSendMessage: (message: string) => void;
    onNewPrediction: (question: string) => void;
    onActionClick: (action: SuggestedAction) => void;
}

export function ChatPanel({
    messages,
    suggestedActions,
    sessionId = null,
    isLoading = false,
    isSendingMessage = false,
    isSendLocked = false,
    isComposerVisible = false,
    followUpPendingState = 'idle',
    onSendMessage,
    onActionClick
}: ChatPanelProps) {
    const [inputValue, setInputValue] = useState('');

    // --- Auto-scroll ------------------------------------------------------
    // Scrolls the thread's own container only. Never scrollIntoView: this panel
    // sits inside the dashboard grid, and scrolling an element into view walks
    // up every scrollable ancestor, dragging the whole page with it.
    const threadRef = useRef<HTMLDivElement | null>(null);
    // Whether the user was at the bottom BEFORE the current update rendered.
    // Maintained by the scroll handler rather than measured inside the effect —
    // by the time an effect runs, the new content has already pushed the bottom
    // away, so a fresh measurement would report "scrolled up" every time and
    // suppress every scroll after the first.
    const isPinnedToBottomRef = useRef(true);
    const hasPerformedInitialJumpRef = useRef(false);
    const lastSeenMessageIdRef = useRef<string | null>(null);

    const scrollToBottom = (behavior: ScrollBehavior) => {
        const thread = threadRef.current;
        if (!thread) {
            return;
        }

        thread.scrollTo({ top: thread.scrollHeight, behavior });
    };

    const handleThreadScroll = () => {
        const thread = threadRef.current;
        if (!thread) {
            return;
        }

        isPinnedToBottomRef.current = isNearBottom(getDistanceFromBottom(thread));
    };

    // Switching sessions starts a fresh thread: drop the previous session's
    // pinned state and re-arm the instant jump. In the layout phase so no frame
    // is painted with carried-over state.
    useLayoutEffect(() => {
        hasPerformedInitialJumpRef.current = false;
        isPinnedToBottomRef.current = true;
        lastSeenMessageIdRef.current = null;
    }, [sessionId]);

    // First paint of a session: jump instantly, before the browser paints, so
    // there is no visible scroll-from-top flash. Gated on `isLoading` because
    // the previous session's messages remain rendered until the new session's
    // first snapshot lands — jumping on those would spend the one instant jump
    // on the wrong thread and leave the real content to animate.
    useLayoutEffect(() => {
        if (hasPerformedInitialJumpRef.current || isLoading || messages.length === 0) {
            return;
        }

        scrollToBottom('auto');
        hasPerformedInitialJumpRef.current = true;
        lastSeenMessageIdRef.current = messages[messages.length - 1].id;
    });

    // Subsequent updates — a new message either way, the thinking indicator
    // appearing, or the answer replacing it.
    useEffect(() => {
        if (!hasPerformedInitialJumpRef.current) {
            return;
        }

        const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
        const isNewMessage = lastMessage !== null && lastMessage.id !== lastSeenMessageIdRef.current;
        const isOwnNewMessage = isNewMessage && lastMessage.role === 'user';
        lastSeenMessageIdRef.current = lastMessage?.id ?? null;

        const decision = decideAutoScroll({
            isPinnedToBottom: isPinnedToBottomRef.current,
            isOwnNewMessage,
        });

        if (!decision.scroll) {
            return;
        }

        scrollToBottom(
            resolveScrollBehavior({ isInitial: false, prefersReducedMotion: prefersReducedMotion() })
        );
    }, [messages, followUpPendingState]);

    // The container's own height changes when the composer or the suggested
    // action chips mount (the c75f15f result gate) — the content does not move,
    // but the bottom does. Re-pin instantly: this is a layout correction, not a
    // new message, so it should not animate.
    useEffect(() => {
        const thread = threadRef.current;
        if (!thread || typeof ResizeObserver === 'undefined') {
            return;
        }

        const observer = new ResizeObserver(() => {
            if (!hasPerformedInitialJumpRef.current || !isPinnedToBottomRef.current) {
                return;
            }

            scrollToBottom('auto');
        });

        observer.observe(thread);

        return () => observer.disconnect();
    }, []);

    // The send path is closed while a message is mid-flight (isSendingMessage)
    // or while the session is still answering (isSendLocked). When the composer
    // is not rendered at all there is no send path to speak of, but the guard
    // stays defensive so a stray handler can never fire without a result.
    const isSendDisabled = isSendingMessage || isSendLocked || !isComposerVisible;

    const handleSend = () => {
        if (isSendDisabled) {
            return;
        }

        if (inputValue.trim()) {
            onSendMessage(inputValue);
            setInputValue('');
        }
    };

    return (
        <div className="w-full h-full max-w-full bg-white border-l border-gray-200 flex flex-col overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 bg-white">
                <h2 className="text-sm font-semibold text-gray-900">Follow-up</h2>
                <p className="mt-0.5 text-xs text-gray-500">
                    {isComposerVisible
                        ? 'Ask about drivers, assumptions, or evidence.'
                        : 'Available once the forecast has a result.'}
                </p>
            </div>

            <div
                ref={threadRef}
                onScroll={handleThreadScroll}
                className="flex-1 min-h-0 overflow-y-auto p-3 sm:p-4 space-y-3"
            >
                {isLoading && messages.length === 0 ? (
                    <StateMessage
                        compact
                        variant="loading"
                        title="Loading follow-ups"
                        description="Pulling the latest conversation for this forecast."
                    />
                ) : messages.length === 0 ? (
                    <StateMessage
                        compact
                        title="No follow-ups yet"
                        description={isComposerVisible
                            ? 'Ask a question about the forecast, evidence, or key assumptions.'
                            : 'Follow-up questions open up once this forecast returns a result.'}
                    />
                ) : (
                    messages.map((message) => (
                        <div
                            key={message.id}
                            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                        >
                            <div
                                className={`max-w-[92%] min-w-0 rounded-lg px-3.5 py-2.5 ${message.role === 'user'
                                    ? 'bg-slate-100 text-gray-900'
                                    : 'bg-white text-gray-900 border border-gray-100 shadow-sm'
                                    }`}
                            >
                                <div className={`text-sm prose prose-sm max-w-none break-words [&_*]:break-words ${message.role === 'user' ? '' : 'prose-slate'}`}>
                                    <ReactMarkdown>{message.content}</ReactMarkdown>
                                </div>
                                <p className="text-[10px] mt-1.5 text-gray-400">
                                    {formatRelativeTime(message.timestamp)}
                                </p>
                            </div>
                        </div>
                    ))
                )}

                {followUpPendingState === 'thinking' ? <ThinkingIndicator /> : null}

                {followUpPendingState === 'stalled' ? (
                    <StateMessage
                        compact
                        variant="warning"
                        title="Still no answer"
                        description="This follow-up has not been answered yet. It may still arrive — if it does it will appear here. Otherwise the forecast itself is unaffected."
                    />
                ) : null}
            </div>

            {isComposerVisible && suggestedActions.length > 0 && (
                <div className="px-4 py-3 border-t border-gray-100 bg-gray-50/50 flex-shrink-0">
                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                        Suggested follow-ups
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {suggestedActions.map((action) => (
                            <Button
                                key={action.id}
                                variant="outline"
                                size="sm"
                                onClick={() => onActionClick(action)}
                                disabled={isSendDisabled}
                                className="h-auto min-h-8 max-w-full whitespace-normal text-xs hover:border-anizai-teal-400 hover:text-anizai-teal-600"
                            >
                                <svg className="mr-1.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                                </svg>
                                {action.label}
                            </Button>
                        ))}
                    </div>
                </div>
            )}

            {/* The composer is withheld until a forecast result exists — there is
                nothing to ask about while the run is still in flight, and nothing
                to ask about at all on a failed run. */}
            {isComposerVisible ? (
                <div className="p-3 border-t border-gray-100 flex-shrink-0 bg-white">
                    <div className="flex min-w-0 gap-2">
                        <Input
                            placeholder="Ask a follow-up about the forecast or evidence"
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                            disabled={isSendingMessage}
                            className="min-w-0 bg-gray-50 border-gray-200 focus:bg-white focus:border-anizai-teal-500 focus:ring-1 focus:ring-anizai-teal-500 transition-all text-sm"
                        />
                        <Button
                            onClick={handleSend}
                            disabled={!inputValue.trim() || isSendDisabled}
                            className="h-10 w-10 shrink-0 bg-anizai-teal-600 hover:bg-anizai-teal-700 text-white border-0 shadow-sm"
                        >
                            {isSendingMessage ? (
                                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                                    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" className="opacity-30" />
                                    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                                </svg>
                            ) : (
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                                </svg>
                            )}
                        </Button>
                    </div>
                </div>
            ) : null}
        </div>
    );
}
