# UI Consistency Audit - Task 0.2

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_ui.md §6, §7`](C_frontend/frontend_ui.md).
> Index: [`frontend_archive.md`](C_frontend/frontend_archive.md) §3.

## 1. Summary

The UI has a recognizable visual direction: light gray surfaces, white cards, soft borders, teal/blue/purple accents, compact dashboard typography, and utility-style Tailwind composition. Consistency is moderate but not yet systematized. Shared primitives exist for `Button`, `Input`, and `Card`, but many screens and components bypass or heavily override them with local classes. The dashboard is the most visually cohesive area, while settings, plan/payment flows, public pages, and chat use related but slightly different spacing, radius, button, badge, and state patterns.

The largest consistency risks are not individual bad styles, but the number of one-off variants: card padding varies, buttons mix shared primitives with raw buttons, statuses are represented differently by dots/badges/text/alerts, and responsive behavior is defined per component instead of through a shared app shell pattern.

## 2. Design System Observations

- Colors: The implicit palette is gray/slate surfaces with `anizai-teal`, `anizai-blue`, and `anizai-purple` accents from `client/tailwind.config.js`. Semantic colors use green, red, amber, and gray directly in component classes. Color usage is scattered across components rather than centralized as semantic tokens.
- Spacing: Common values are `p-4`, `p-5`, `p-6`, `p-8`, `gap-2`, `gap-3`, `gap-4`, `gap-6`, and `space-y-8`. Dashboard cards mostly use `gap-6`; settings often use `space-y-8`; public pages use larger marketing spacing such as `py-24` and `py-32`. Spacing is scattered.
- Radius: Shared `Card` uses `rounded-lg`. Settings and subscription surfaces often use `rounded-xl` or the modal uses `rounded-2xl`. Badges range from `rounded`, `rounded-md`, `rounded-lg`, to `rounded-full`. There is no single radius scale by component type.
- Typography: Most product UI uses `text-sm`, `text-xs`, `text-base`, `text-lg`, `text-xl`, and `text-2xl`. Public pages use larger `text-4xl` and above. Many small uppercase labels use `text-[10px]` or `text-[11px]` with tracking. Typography is locally chosen, not enforced through reusable heading/label components.
- Cards: A shared `Card` primitive exists with `rounded-lg border border-gray-200 bg-white shadow-sm`. Several components use it, but settings, subscription cards, empty states, plan cards, chat messages, and evidence key drivers create card-like surfaces manually.
- Buttons: A shared `Button` primitive exists with variants and sizes. Many places still use raw `<button>` elements with local classes, especially modals, settings, subscription/payment, landing/public pages, and icon buttons.
- Badges: Badges are hand-built inline. There is a `badge.tsx` primitive, but status/plan/confidence/source badges are mostly local spans.
- Forms: `Input` is shared in login/signup/chat, but sidebar search, forecast textarea, contact form fields, settings fields, and payment fields define their own classes.

## 3. Consistency Issues by Category

### Layout and spacing

Issue: Dashboard shell spacing is split between `DashboardPage` and `Dashboard`.
Where it appears: `client/src/pages/DashboardPage.tsx`, `client/src/components/Dashboard.tsx`.
Why it matters: The center panel, mobile header offset, and internal result spacing are coupled through classes like `pt-16`, `pt-28`, `lg:pt-8`, `px-6`, and `space-y-6`. Future layout changes can easily create double padding or cramped content.
Suggested fix later: Define app shell spacing and content container patterns for dashboard, mobile header offset, and panel content.

Issue: Large differences between product and marketing spacing.
Where it appears: dashboard uses `px-6 py-8 space-y-6`; landing/public pages use `py-24`, `py-32`, large headings, and wide sections.
Why it matters: The shift from public pages to dashboard feels like two separate visual systems.
Suggested fix later: Keep marketing and app density distinct, but document separate public-page and app-page spacing scales.

Issue: Sidebar spacing is locally dense and independent of dashboard card spacing.
Where it appears: `Sidebar` uses `px-5 py-5`, `p-4`, `px-4 py-2`, `px-3 py-3`, `space-y-1`.
Why it matters: Session items, actions, and profile footer use good compact spacing, but it is not reusable for future navigation surfaces.
Suggested fix later: Standardize nav item, sidebar section, and sidebar footer spacing.

Issue: Chat and evidence spacing use different density rules.
Where it appears: `ChatPanel` uses `p-6 space-y-4`; `EvidenceTimeline` uses `p-6`, `space-y-6`, `pl-8`, and multiple `text-[10px]` labels.
Why it matters: Both are feed-like patterns, but messages and evidence entries have different visual rhythm and label hierarchy.
Suggested fix later: Create feed item spacing guidance for messages, evidence, and activity lists.

### Cards

Issue: Card radius and padding are inconsistent.
Where it appears: shared `Card` is `rounded-lg`; empty forecast card is `rounded-lg p-6`; subscription surfaces are `rounded-xl p-5`; modal is `rounded-2xl`; plan cards are `rounded-lg p-8`; chat messages are `rounded-lg px-4 py-3`.
Why it matters: Similar surfaces feel subtly different, and future additions will likely add more variants.
Suggested fix later: Define card variants such as dashboard card, compact card, modal card, empty state card, and pricing card.

Issue: Card-like inner boxes create nested-card visual patterns.
Where it appears: `PredictionOverview` metric boxes inside a card, `EvidenceTimeline` narrative summary inside a card, `SubscriptionSettings` plan cards and usage meter inside modal content, settings info cards inside modal.
Why it matters: Nested surfaces can become visually busy, especially in settings and evidence areas.
Suggested fix later: Standardize when nested panels use background-only, border-only, or full card treatment.

Issue: Dashboard cards are not fully aligned in header treatment.
Where it appears: `PredictionOverview`, `MarketComparison`, `SentimentAnalysis`, `EvidenceTimeline`.
Why it matters: Some cards customize title sizes and header padding, while `SentimentAnalysis` relies on default `CardHeader`. This creates slight mismatches in title scale, description size, and header density.
Suggested fix later: Create a dashboard card header pattern for result cards and BI cards.

### Typography

Issue: Heading hierarchy is not centralized.
Where it appears: dashboard title `text-2xl`, create forecast title `text-3xl`, modal section titles `text-xl`, card titles `text-base`/`text-lg`, public pages `text-4xl` and larger.
Why it matters: The hierarchy is mostly sensible, but repeated ad hoc classes make it hard to preserve consistency as screens grow.
Suggested fix later: Define text styles for app page title, dashboard title, section title, card title, eyebrow/label, helper text, and metric value.

Issue: Numeric and percentage values vary.
Where it appears: prediction gauge uses `text-4xl`, sidebar probability uses `text-xs font-semibold`, chart labels use Recharts inline `fontSize: 12`, sentiment footer uses `text-xl`, subscription usage uses `text-sm`.
Why it matters: Forecast metrics are core product data; inconsistent numeric styling can blur importance.
Suggested fix later: Standardize metric sizes by context: hero metric, card metric, list metric, inline metric.

Issue: Error and help text styling is inconsistent.
Where it appears: `App` auth toast, settings alerts, payment field errors, profile/security messages, subscription alerts.
Why it matters: Users need to recognize error/success/warning states quickly across the product.
Suggested fix later: Define alert and field-error typography patterns.

### Buttons

Issue: Shared `Button` is inconsistently used.
Where it appears: `Sidebar`, `ChatPanel`, `CreateForecastView`, login/signup use `Button`; settings, subscription, modal dialogs, public pages, and many icon actions use raw buttons.
Why it matters: Button height, radius, focus state, disabled state, and color hierarchy diverge.
Suggested fix later: Extend `Button` variants and migrate future changes to use them gradually.

Issue: Primary buttons use multiple visual treatments.
Where it appears: `Button` `default` is black; `Button` `primary` is gradient; `CreateForecastView` manually applies gradient; subscription upgrade is gradient with `rounded-xl`; chat send is teal; plan selection uses gradient for premium and outline for free.
Why it matters: It is not always clear which action is primary across screens.
Suggested fix later: Define primary app action, promotional primary, destructive, secondary, ghost, and icon button variants.

Issue: Loading buttons are inconsistent.
Where it appears: `CreateForecastView` text changes to "Analyzing..."; subscription/payment uses spinners; security/profile use text/spinner combinations; delete confirm has no loading display.
Why it matters: Loading feedback feels uneven, and destructive actions may look clickable while blocked by parent logic.
Suggested fix later: Standardize loading button content and disabled affordance.

### Forms

Issue: Forecast textarea intentionally differs from standard inputs but lacks shared form-state patterns.
Where it appears: `CreateForecastView`.
Why it matters: The hero input is important, but validation is only disable/hints/counter; no error copy explains min/max requirements.
Suggested fix later: Create forecast-specific input states for empty, valid, invalid, loading, and plan-limited.

Issue: Input styling is scattered.
Where it appears: shared `Input`, sidebar search input, settings text inputs, subscription payment inputs, contact page fields, forecast textarea.
Why it matters: Focus rings, heights, radius, labels, and error states vary across product forms.
Suggested fix later: Standardize base field, search field, textarea, field label, help text, and error message styles.

Issue: Placeholder/help text varies in tone and density.
Where it appears: forecast rotating placeholders, chat input placeholder, login/signup placeholders, payment placeholders, sidebar search.
Why it matters: The UX voice and expected input specificity feel inconsistent.
Suggested fix later: Write placeholder guidelines and pair important constraints with visible helper/error text.

### Status indicators

Issue: Backend statuses are collapsed into `stable`/`volatile` UI statuses.
Where it appears: `App.tsx` maps `running` and `failed` to `volatile`; `Sidebar` renders a green/amber dot plus text.
Why it matters: Running, failed, done, queued, and volatile represent different product meanings but share visuals.
Suggested fix later: Create a session status badge system that maps each backend status distinctly.

Issue: Status visuals vary by context.
Where it appears: dashboard "Live Prediction" badge, confidence badge, sidebar dots, evidence impact text, subscription plan badges, alerts.
Why it matters: Similar semantic states use dots, pills, inline colored text, banners, or ribbons with no single rule.
Suggested fix later: Standardize status indicators by severity and role: pill, dot, banner, inline label.

Issue: Empty/error/loading states are partial and visually inconsistent.
Where it appears: dashboard no forecast dashed card, trending empty dashed card, loading text-only center state, subscription alerts, auth toast.
Why it matters: State transitions can feel unfinished and make the app harder to understand.
Suggested fix later: Create shared empty, error, loading, and warning state patterns.

### Icons

Issue: Icon sources are mixed.
Where it appears: `PlanSelection` imports `ArrowLeft` from `lucide-react`; most other icons are inline SVGs; Google icon is custom SVG.
Why it matters: Stroke widths, sizing, and style may drift.
Suggested fix later: Prefer a consistent icon strategy for app UI, using lucide where available and documenting exceptions such as brand icons.

Issue: Icon sizes vary without an explicit scale.
Where it appears: `w-3`, `w-3.5`, `w-4`, `w-5`, `w-6`, dashboard floating chat button, settings nav, evidence dots.
Why it matters: Icons sometimes feel like decoration and sometimes like controls; inconsistent sizes can weaken scanability.
Suggested fix later: Define icon sizes for inline label, nav, button, modal header, and floating action.

Issue: Functional icon buttons sometimes lack visible affordance or consistent touch target.
Where it appears: sidebar delete hover button, settings close/back buttons, dashboard mobile menu/chat buttons.
Why it matters: Some icon controls are small or hover-dependent, which is fragile on touch devices.
Suggested fix later: Standardize icon button dimensions, hover/focus state, and mobile accessibility.

### Modals/overlays

Issue: Modal/dialog treatment is inconsistent.
Where it appears: `SettingsModal` uses `bg-black/50`, `rounded-2xl`, `shadow-2xl`, `z-[100]`; `ConfirmDialog` uses `bg-opacity-50`, `rounded-lg`, `shadow-xl`, `z-50`; dashboard drawers use `z-40` overlays.
Why it matters: Overlay stacking, radius, and shadow differ for similar modal experiences.
Suggested fix later: Define overlay, modal, dialog, drawer, and popover primitives.

Issue: CTA hierarchy differs between dialog-like surfaces.
Where it appears: `ConfirmDialog`, `SubscriptionSettings` cancel confirmation, payment form cancel/submit, settings close/back controls.
Why it matters: Destructive, cancel, and primary actions should be visually predictable.
Suggested fix later: Standardize modal footer/action layout and destructive button variants.

Issue: Plan limit modal is not implemented.
Where it appears: No plan limit modal found; only subscription usage warning and hardcoded forecast usage text.
Why it matters: Future plan limit UI should not invent another modal pattern.
Suggested fix later: Use the eventual standardized dialog/alert pattern.

### Responsive layout

Issue: Mobile chat drawer has fixed width larger than some viewports.
Where it appears: `DashboardPage` uses `w-96` for chat drawer at mobile and tablet.
Why it matters: On narrow mobile screens, the drawer can overflow or feel clipped.
Suggested fix later: Use responsive drawer widths such as `w-full max-w-sm` or similar.

Issue: Settings modal does not stack its sidebar on mobile.
Where it appears: `SettingsModal` keeps `w-48` nav and content side-by-side.
Why it matters: On small screens, content can become cramped or horizontally constrained.
Suggested fix later: Add a mobile settings layout with top tabs/list or stacked nav.

Issue: Evidence filters can crowd on narrow cards.
Where it appears: `EvidenceTimeline` header keeps title area and filter tabs in one row.
Why it matters: Long titles or narrow screens may compress tabs and header content.
Suggested fix later: Allow filter tabs to wrap or stack under the title at small widths.

Issue: Chart cards do not have explicit compact empty/mobile states.
Where it appears: `MarketComparison`, `SentimentAnalysis`.
Why it matters: Labels and legends may crowd on mobile/tablet, and empty datasets can display awkwardly.
Suggested fix later: Add responsive chart configuration and shared empty chart state.

## 4. Component-Level Audit Table

| Component/file | Current pattern | Consistency issue | Suggested future cleanup priority |
|---|---|---|---|
| `client/src/pages/DashboardPage.tsx` | Three responsive shells with fixed sidebar/chat widths and overlays | Layout rules are duplicated across breakpoints; chat drawer fixed width can overflow mobile | High |
| `client/src/components/Dashboard.tsx` | `max-w-7xl`, `px-6`, `space-y-6`, card grid | App content spacing is local to this component | Medium |
| `client/src/components/Sidebar.tsx` | Compact nav list, manual search input, local icon buttons | Search field, session item, status dot, and profile menu are all bespoke | High |
| `client/src/components/CreateForecastView.tsx` | Large hero textarea, gradient CTA, hint chips | Form style intentionally custom but lacks shared validation/error states | High |
| `client/src/components/CreateForecastContext.tsx` | Right panel with section header and trending list | Uses local empty state and hover-only Analyze action | Medium |
| `client/src/components/ChatPanel.tsx` | Message bubbles, suggested actions, input row | Empty state missing; message bubbles and actions are not standardized | High |
| `client/src/components/cards/PredictionOverview.tsx` | Main result card with gauge, metric boxes, badge | Hardcoded labels and local nested metric card style | High |
| `client/src/components/cards/MarketComparison.tsx` | Recharts card with insight title and narrative footer | Card header/footer density differs from other result cards; no empty state | Medium |
| `client/src/components/cards/SentimentAnalysis.tsx` | Default card header, chart, footer metrics | Less customized than peer cards; undefined empty data display risk | Medium |
| `client/src/components/cards/EvidenceTimeline.tsx` | Card with tabs, summary panel, key drivers, timeline | Nested surfaces and status labels use several local patterns | High |
| `client/src/components/SettingsModal.tsx` | Large modal with sidebar nav and section content | Overlay/modal style differs from confirm dialog and is not mobile-stacked | High |
| `client/src/components/settings/SubscriptionSettings.tsx` | Local plan cards, alerts, payment form, cancel confirm | Many button/card/form/status patterns in one file; not aligned with primitives | High |
| `client/src/components/ui/ConfirmDialog.tsx` | Simple destructive confirmation modal | Different overlay/radius/button patterns; no loading state | Medium |
| `client/src/pages/PlanSelection.tsx` + `PlanCard` | Pricing page with large cards and gradient premium CTA | Separate plan-card style from subscription settings plan cards | Medium |
| `client/src/pages/LoginPage.tsx` / `SignupPage.tsx` | Centered auth forms with shared inputs/buttons | Generally consistent with primitives, but terms links are dead anchors and auth CTA styles are custom | Low |
| `client/src/pages/*` public content pages | Static pages with local headers/sections/cards | Public page headers and cards are duplicated rather than shared | Low |
| `client/src/components/ui/button.tsx` | Variant/size primitive | Existing variants do not cover all real button needs, leading to local raw buttons | High |
| `client/src/components/ui/card.tsx` | Base card primitive | Card variants are missing for compact/metric/modal/empty-state surfaces | High |
| `client/src/components/ui/input.tsx` | Base input primitive | Not used by all form fields; no built-in error/help/label pattern | Medium |

## 5. UI Patterns to Standardize Later

- App shell spacing.
- Dashboard panel layout and mobile drawer widths.
- Card base style and card variants.
- Dashboard/result card header style.
- Section heading and label style.
- Primary, secondary, ghost, destructive, icon, and loading button styles.
- Status badge and status dot style.
- Empty state component.
- Error state component.
- Loading skeleton/spinner style.
- Toast/alert/banner style.
- Forecast result metric style.
- Evidence item style.
- Evidence filter tabs.
- Chat message style.
- Suggested action chips/buttons.
- Form field, field label, helper text, and error text style.
- Modal, dialog, drawer, and popover overlay style.
- Plan/usage meter style.

## 6. Quick-Win Fixes for Later Tasks

- Replace mobile chat drawer `w-96` with a responsive width.
- Add no-message copy to `ChatPanel`.
- Add no-evidence and no-filter-results copy to `EvidenceTimeline`.
- Normalize dashboard card title sizes and header padding.
- Convert `ConfirmDialog` buttons to shared button variants.
- Add loading text/spinner to delete confirmation while `isDeletingSession` is true.
- Fix visible mojibake strings in sidebar, trending arrows, subscription text, and settings fallbacks.
- Use the shared `Input` or a shared search field style for sidebar search.
- Remove or wire inactive/hover-only actions after behavior decisions are made.
- Align plan-selection cards and subscription plan cards visually.

## 7. Bigger Refactor Candidates

- Extract dashboard shell/drawer layout from `DashboardPage`.
- Create a real app UI kit layer around `Button`, `Card`, `Input`, badges, alerts, dialogs, and form fields.
- Split `SubscriptionSettings` into smaller presentational components and shared payment/plan/status primitives.
- Create a shared public-page header/section/card pattern or remove unused duplicate header components.
- Introduce route ownership so page layouts can be standardized per route type.
- Standardize all forecast/session states around backend statuses before designing status UI.
- Create shared result-card components for metrics, chart cards, and evidence feeds.
- Define responsive behavior at the shell/component-pattern level instead of per screen.

## 8. Risk Notes

- Styling changes in `DashboardPage` can affect drawer visibility, overlay click behavior, and panel availability across breakpoints.
- Styling changes in `CreateForecastView` may affect perceived validation and submission readiness because validation is mainly expressed through disabled state, hints, and counter color.
- Refactoring buttons in settings/subscription can accidentally change submit/cancel behavior because many raw buttons are tied to local state transitions.
- Changing card structure in dashboard result cards can affect Recharts sizing because charts rely on parent dimensions.
- Adjusting `EvidenceTimeline` layout can affect filter usability because filter state is local to the component.
- Unifying status badges should wait until product semantics are clarified for `draft`, `running`, `done`, `failed`, `stable`, and `volatile`.
- Modal z-index/overlay changes could affect stacking between settings, confirm dialog, chat drawer, and mobile overlays.
- Shared form changes could affect Firebase-auth-related settings fields and mocked subscription payment validation.
