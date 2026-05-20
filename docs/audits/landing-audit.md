# Landing Page Audit — Slice 13

Read-only inventory of the public landing page (`appState === 'landing'`) as it
exists on `main` at the time of audit. No code changes, no design proposals.

Audit date: 2026-05-20.

---

## 1. File map

| Path | Role | Lines |
|---|---|---|
| `client/src/pages/LandingPage.tsx` | Page entry — renders top nav inline + composes section components | 78 |
| `client/src/components/landing/Hero.tsx` | Hero section (logo, headline, CTA, stats, scroll cue) | 147 |
| `client/src/components/landing/ProductExplanation.tsx` | "How it works" three-card grid + "trusted sources" wordmarks | 84 |
| `client/src/components/landing/UIShowcase.tsx` | Static mock of the dashboard in a browser chrome | 151 |
| `client/src/components/landing/HowItWorks.tsx` | "3 simple steps" numbered cards | 69 |
| `client/src/components/landing/WhoItsFor.tsx` | Three persona cards | 62 |
| `client/src/components/landing/FinalCTA.tsx` | "Start Forecasting Today" closer | 23 |
| `client/src/components/landing/Footer.tsx` | Logo, link columns (Product / Company / Legal), status pill | 143 |
| `client/src/components/auth/GoogleAuthButton.tsx` | Shared primitive — Google-logo button used by Hero and FinalCTA | 38 |
| `client/public/logo-brain.png` | The only image asset on the landing page (logo) | — |
| `client/index.html` | Loads Inter from Google Fonts; sets `<title>` "Anizai - AI-Powered Event Forecasting" | 19 |
| `client/tailwind.config.js` | Source of truth for `anizai-teal/-blue/-purple` palettes | ~48 |
| `client/src/index.css` | Tailwind base layer; declares brand RGB CSS vars but they are not used on the landing page | — |

The landing page does **not** use a shared site header (`SiteHeader.tsx`
exists in `components/` but is not imported here). Top nav is inlined in
`LandingPage.tsx` itself.

---

## 2. Section-by-section inventory

Render order: top-nav → Hero → ProductExplanation → UIShowcase → HowItWorks
→ WhoItsFor → FinalCTA → Footer.

### 2.0 Top nav bar (inline in `LandingPage.tsx:30–62`)

- **What it shows.** Left cluster: text links "About", "Home", "Contact".
  Right cluster: a single account-icon button (silhouette SVG) that
  triggers `onAuth` → routes to `LoginPage`.
- **Visual elements.** Plain text buttons, base font size, gray-600 default,
  gray-900 on hover. No logo on the left. No primary CTA in the bar.
- **Source of truth.** Hardcoded JSX.
- **Component file.** `client/src/pages/LandingPage.tsx`.
- **Data dependencies.** None.

### 2.1 Hero (`Hero.tsx`)

- **What it shows.** Centered: brain logo with blurred gradient glow,
  wordmark "Anizai", tagline, sub-copy, Google-style "Get started" CTA,
  microcopy with Terms/Privacy links, a 3-stat social-proof bar, and a
  "See how it works" scroll cue with a bouncing chevron.
- **Visual elements.**
  - Three absolute-positioned `radial-gradient` "orbs" (teal top-left,
    purple bottom-right, blue mid-right) animated via inline `@keyframes
    float` (18/20/25s loops, scale + translate).
  - Faint 60px grid overlay at opacity 0.03.
  - Logo with `mix-blend-multiply` over a blurred 150%-scaled
    teal→blue→purple gradient halo.
  - Headline is `bg-clip-text` over a near-monochrome gray-900→gray-800
    gradient. The word "evidence" inside the tagline is `bg-clip-text`
    over the teal→blue→purple gradient.
  - CTA is `<GoogleAuthButton>` enlarged to `h-14`, sized `max-w-sm`.
  - Section is `min-h-screen` — the hero alone fills the viewport.
- **Source of truth.** Hardcoded JSX, including stat numbers.
- **Component file.** `client/src/components/landing/Hero.tsx`.
- **Data dependencies.** None. Stats are static literals.

### 2.2 Product explanation (`ProductExplanation.tsx`)

- **What it shows.** Pill badge ("• How it works"), H2, sub-copy, then a
  three-card grid: "Evidence-Based", "Real-Time Updates", "Confidence
  Scoring". Below the grid, a "trusted sources" row of stylized
  wordmarks.
- **Visual elements.**
  - Each card has a 56px square icon tile with a brand-color gradient,
    soft brand-tinted shadow (`shadow-anizai-{color}-500/20`), and an
    inline heroicon-style SVG. Icons scale on hover.
  - Cards: white→gray-50 gradient background, gray border that switches
    to the matching brand-200 on hover, `hover:shadow-lg`.
  - "Trusted sources" row renders the source names as styled `<span>`
    text — alternating font-serif / font-sans / italic, gray-300 default,
    gray-500 on hover. No logos.
- **Source of truth.** Hardcoded JSX.
- **Component file.** `client/src/components/landing/ProductExplanation.tsx`.
- **Data dependencies.** None.

### 2.3 UI showcase (`UIShowcase.tsx`)

- **What it shows.** A static, non-interactive mock of the dashboard inside
  a fake macOS browser chrome.
- **Visual elements.**
  - Browser frame: three traffic-light dots, fake URL bar reading
    `app.anizai.com/dashboard`.
  - Mock header with question "Will AI regulation pass in the EU by Q2
    2026?" gradient-clipped onto teal/blue/purple, green "Active" badge,
    and "Last updated 06/01/2026, 10:30:00" timestamp.
  - Two mock cards side-by-side:
    1. **Probability gauge** — SVG donut with the brand gradient stroke,
       drop-shadow, large "72.3%" gradient-clipped number, "Confidence
       Score: 84/100", "+2.4% today" trend.
    2. **Evidence timeline** — two mock timeline items (EU Parliament
       vote / industry lobbying letter) sourced "Reuters" and "Financial
       Times", with relative ages "11h ago" / "1d ago".
  - Whole frame scales `hover:scale-[1.005]` over 500ms.
- **Source of truth.** Hardcoded JSX. None of these numbers, sources, or
  timestamps come from the backend; they are literal strings.
- **Component file.** `client/src/components/landing/UIShowcase.tsx`.
- **Data dependencies.** None.

### 2.4 How it works (`HowItWorks.tsx`)

- **What it shows.** Pill badge ("• 3 Simple Steps"), H2, sub-copy, then
  three numbered cards (1/2/3) connected by a horizontal gradient line.
- **Visual elements.**
  - 80px rounded-2xl tiles per step, each in a brand-color gradient
    (teal/blue/purple), with a large white number.
  - Tiles ship with a slight static rotation (`rotate-3` /
    `-rotate-3` / `rotate-3`), which un-rotates on hover.
  - Connecting line is `bg-gradient-to-r from-teal-300 via-blue-300
    to-purple-300`, hidden on mobile.
- **Source of truth.** Hardcoded JSX.
- **Component file.** `client/src/components/landing/HowItWorks.tsx`.
- **Data dependencies.** None.

### 2.5 Who it's for (`WhoItsFor.tsx`)

- **What it shows.** H2 "Who It's For", sub-copy, three persona cards.
- **Visual elements.**
  - Cards: plain white, gray-200 border, `shadow-sm`. No hover state.
  - Each card has a 48px square teal-100→blue-100 gradient icon tile
    with a teal-600 heroicon.
  - Personas defined in a local `personas` array.
- **Source of truth.** Hardcoded JSX (the `personas` array is local).
- **Component file.** `client/src/components/landing/WhoItsFor.tsx`.
- **Data dependencies.** None.

### 2.6 Final CTA (`FinalCTA.tsx`)

- **What it shows.** H2 "Start Forecasting Today", one line of sub-copy, a
  single `GoogleAuthButton`.
- **Visual elements.** Centered, white background, plain. No surrounding
  card, no gradient, no imagery.
- **Source of truth.** Hardcoded JSX.
- **Component file.** `client/src/components/landing/FinalCTA.tsx`.
- **Data dependencies.** None.

### 2.7 Footer (`Footer.tsx`)

- **What it shows.** Four columns (logo+blurb, Product, Company, Legal);
  bottom bar with copyright and a "Systems Operational" pill (pulsing
  green dot).
- **Visual elements.** Gray-50 background, gray-200 top border. All link
  labels are plain text buttons (no icons).
- **Links.**
  - Product: Features, Methodology, Pricing, Changelog
  - Company: About, Blog, Contact
  - Legal: Terms, Privacy, Cookies
- **Source of truth.** Hardcoded JSX. Copyright text reads `© 2026 Anizai
  Inc. All rights reserved.`
- **Component file.** `client/src/components/landing/Footer.tsx`.
- **Data dependencies.** None. The "Systems Operational" indicator is a
  static dot — not wired to any health check or status API.

---

## 3. Visual & design language assessment

### Typography

- **Family.** Inter, loaded from Google Fonts in `index.html` at weights
  300/400/500/600/700. No serif face is loaded; the "Reuters / Bloomberg
  / Economist" wordmarks use `font-serif` (the browser default serif —
  Times/Charter on most systems) and `italic` for "Nature". No type
  hierarchy is enforced by a tokenized system; sizes are picked per
  component.
- **Heading scale.** Inconsistent across sections:
  - Hero H1: `text-5xl sm:text-6xl lg:text-7xl`, `font-bold`,
    `tracking-[-0.03em]`.
  - ProductExplanation H2 & HowItWorks H2: `text-3xl sm:text-4xl`,
    `font-bold`, `tracking-[-0.02em]`.
  - UIShowcase H2, WhoItsFor H2, FinalCTA H2: `text-3xl`,
    `font-semibold`, **no negative tracking**.
  - Footer column headers: `text-sm font-semibold uppercase
    tracking-wider`.
- Two parallel heading conventions: `font-bold + tight tracking + xl
  step-up` (ProductExplanation, HowItWorks) vs. `font-semibold + no
  tracking + smaller` (UIShowcase, WhoItsFor, FinalCTA). The page looks
  like two designers worked on it.

### Color palette

- Brand palette as defined in `tailwind.config.js`: `anizai-teal`
  (#14b8a6 base), `anizai-blue` (#3b82f6 base), `anizai-purple`
  (#a855f7 base). All three are stock Tailwind hues
  (teal-500 / blue-500 / purple-500), only renamed.
- The page also uses raw RGB literals in inline styles (e.g.
  `rgba(20, 184, 166, 0.4)`, `rgba(59, 130, 246, 0.3)`,
  `rgba(168, 85, 247, 0.35)`) — same colors, but bypassing the token
  layer.
- Backgrounds alternate: `#fafbfc` (hero) → white (ProductExplanation)
  → gray-50 (UIShowcase) → gray-50→white gradient (HowItWorks) → gray-50
  (WhoItsFor) → white (FinalCTA) → gray-50 (Footer). Five distinct
  background tones across seven sections — no consistent rhythm.
- The teal→blue→purple gradient is reused as: hero halo, hero "evidence"
  word, GoogleAuthButton hover bar, ProductExplanation icon tiles
  (split per card), UIShowcase header text + gauge stroke + gauge
  number, HowItWorks step tiles + connecting line, WhoItsFor icon
  tile. It is the page's single most-used visual device.

### Spacing rhythm

- Section vertical padding varies: `py-32` (ProductExplanation,
  HowItWorks), `py-24` (UIShowcase, WhoItsFor, FinalCTA), `py-24 px-6`
  with custom min-height on Hero. Not on a single scale.
- Container widths also vary: `max-w-6xl` (UIShowcase, Footer),
  `max-w-5xl` (ProductExplanation, HowItWorks, WhoItsFor), `max-w-3xl`
  (FinalCTA). Hero clamps its inner column to `max-w-3xl` but the
  section itself is full-width.

### Imagery / illustrations

- One real image asset on the page: `logo-brain.png`, used in the hero
  and (smaller, faded) in the footer.
- Everything else is SVG: heroicons-style line icons in cards, a
  hand-rolled donut gauge in the UI showcase, the Google logo inside
  GoogleAuthButton, and an account-silhouette icon in the top nav.
- No product screenshots, no illustrations, no photography. The
  UIShowcase section is a **fabricated mockup of the dashboard rendered
  in JSX**, not a real screenshot.

### Animations / interactions

- Hero: three orbs with float keyframes (translate + scale, 18/20/25s).
- Hero scroll cue: `animate-bounce` chevron.
- ProductExplanation cards: border color, shadow, and icon scale on hover.
- UIShowcase frame: `hover:scale-[1.005]` (barely perceptible).
- HowItWorks step tiles: ship rotated, un-rotate on hover.
- Footer: pulsing green dot on the "Systems Operational" pill.
- GoogleAuthButton: gradient top-border fades in on hover.

None of the animation is gated on `prefers-reduced-motion`.

### Mobile responsiveness

- Section paddings drop one level (`py-24` stays, `py-32` does not get
  a smaller-screen override).
- Card grids use `grid-cols-1 md:grid-cols-3` — they stack on mobile.
- Hero CTA uses `flex-col` everywhere; stats bar uses `gap-8 sm:gap-12`
  but does not stack — three stats stay on one row on narrow viewports,
  which can crowd at <360px.
- The connecting gradient line in HowItWorks is `hidden md:block`.
- UIShowcase keeps the cards in `grid-cols-1 lg:grid-cols-2` — on
  mobile the dashboard mock collapses to a single column but the
  browser-chrome traffic lights remain at their fixed size; the fake
  URL bar uses `max-w-sm` and so doesn't grow with the viewport.
- Footer link columns: `grid-cols-2 md:grid-cols-4` — on mobile the
  logo block spans two columns, the three link groups become a 2×2
  grid (Product, Company on row 1; Legal alone on row 2).

---

## 4. Content audit

### Headlines (verbatim)

| Section | H2/H1 text |
|---|---|
| Hero (H1) | `Anizai` |
| Hero (H2 tagline) | `Forecast the future with evidence, not guesses.` |
| ProductExplanation | `Evidence-powered forecasting` |
| UIShowcase | `Comprehensive Analysis Dashboard` |
| HowItWorks | `From question to insight in seconds` |
| WhoItsFor | `Who It's For` |
| FinalCTA | `Start Forecasting Today` |

### Sub-copy (paraphrased)

- **Hero subhead.** "AI-powered probabilities, confidence scores, and
  real-time evidence tracking — all in one place."
- **ProductExplanation.** "Anizai synthesizes real-time data from news,
  expert consensus, and historical precedent into transparent,
  traceable probability assessments."
- **ProductExplanation cards.**
  - "Evidence-Based — Every forecast is grounded in verifiable data
    with traceable sources you can audit."
  - "Real-Time Updates — Probabilities update continuously as new
    information emerges from global sources."
  - "Confidence Scoring — Not just probability — understand exactly how
    certain we are about each prediction."
- **UIShowcase.** "Track predictions with real-time evidence, confidence
  metrics, and interactive insights in a unified workspace."
- **HowItWorks.** "Get evidence-backed probability forecasts for any
  future event"
- **HowItWorks steps.**
  1. "Ask any question — Submit any future event you want to forecast.
     Our AI begins gathering relevant evidence immediately."
  2. "We analyze evidence — Our system gathers news, expert opinions,
     public sentiment, and historical precedents in real-time."
  3. "Get actionable insights — Receive probability forecasts,
     confidence scores, and a complete evidence timeline that updates
     live."
- **WhoItsFor.** "Anizai serves anyone seeking clarity about future events"
  - "Prediction Market Users — Make more informed decisions with
    transparent, evidence-based probability assessments."
  - "Analysts & Researchers — Access structured, real-time data on
    emerging events and their likelihood."
  - "Curious Minds — Understand complex future events through clear,
    analytical forecasting."
- **FinalCTA.** "Join Anizai and gain clarity on the events that matter
  to you"
- **Footer blurb.** "Evidence-based forecasting for a complex world.
  Predict with confidence."

### CTAs (in render order)

| # | Label | Location | Action |
|---|---|---|---|
| 1 | (icon) | Top-nav account icon | → `onAuth` (Login) |
| 2 | `Get started — it's free` | Hero | → `onAuth` (Login) |
| 3 | `Terms` | Hero microcopy | → Terms page |
| 4 | `Privacy` | Hero microcopy | → Privacy page |
| 5 | `See how it works` | Hero scroll cue | **Inactive** — styled `cursor-pointer` but no onClick |
| 6 | `Get started with Google` | FinalCTA | → `onAuth` (Login) |
| 7 | All Footer column links | Footer | → respective pages |

The page has **three** functional auth entry points (top-nav icon, hero
CTA, final CTA). All three route to `LoginPage`, not `SignupPage` —
even though the hero copy says "Get started" and the FinalCTA says
"Start Forecasting Today". The signup page is unreachable from the
landing page except via Login → "Sign up".

### Implicit value proposition

Stated explicitly in the hero tagline: "Forecast the future with
**evidence**, not guesses." The hero subhead refines it: "AI-powered
probabilities, confidence scores, and real-time evidence tracking — all
in one place."

This is consistent through ProductExplanation and HowItWorks. The
WhoItsFor "Prediction Market Users" card is the only place that hints
at the actual decision use-case described in `CLAUDE.md` ("whether to
bet on a future event"), and it is buried as one of three personas.

### Inconsistencies

- **Title-case vs. sentence-case.** "Comprehensive Analysis Dashboard"
  (UIShowcase) and "Who It's For" (WhoItsFor) and "Start Forecasting
  Today" (FinalCTA) use Title Case. "Evidence-powered forecasting"
  (ProductExplanation) and "From question to insight in seconds"
  (HowItWorks) use sentence case. No consistent rule.
- **Pill badge presence.** ProductExplanation and HowItWorks have
  pill-style section badges ("How it works", "3 Simple Steps");
  UIShowcase, WhoItsFor, FinalCTA do not. Treatment is half-applied.
- **Product naming.** Only "Anizai" appears — no "AnizaiAI" stragglers.
- **"How it works" appears twice.** ProductExplanation's pill badge
  reads "How it works", and an entirely separate later section is
  titled HowItWorks. Different content, same label.

---

## 5. Comparison to product reality

### What the page claims vs. what the dashboard actually does

| Claim on landing | Reality in product |
|---|---|
| "Probabilities update continuously as new information emerges" (ProductExplanation card 2) | Forecasts are one-shot. The agent runs `claim → understand → embed → vault_query → rate_evidence → synthesize → write_to_firestore` once per session. There is no continuous re-forecast loop. |
| "Confidence scores… exactly how certain we are" (ProductExplanation card 3) | Backend returns `confidence` (0–1) and `confidenceLabel`. ✅ Accurate. |
| Evidence Timeline mock shows ages like "11h ago" / "1d ago" | Real evidence cards do render `publishedAt` ages. ✅ Plausible. |
| "+2.4% today" trend on the mock gauge | The dashboard does not show a daily delta for probability. There is no time-series of `finalProbability`. **Misleading.** |
| "Confidence Score: 84/100" on the mock | Real UI shows confidence as `High / Medium / Low Confidence` label, not a /100 score. **Misleading.** |
| HowItWorks step 2 lists "public sentiment" as a data source | `sentimentTimeSeries` is currently empty per `CLAUDE.md` ("agent writes empty arrays"). The promised data source is not yet wired. |
| FinalCTA: "Start Forecasting Today" / "Join Anizai" | After auth, new accounts route to plan selection, not directly to a forecast composer. Acceptable but worth noting. |
| Top-nav "About / Home / Contact" only | The real product surface (the dashboard) is not represented in the nav. There is no "Sign in" or "Pricing" link in the visible top nav. |
| Stats bar: "10K+ forecasts made / 92% accuracy rate / 500+ active users" | **All three numbers are hardcoded.** There is no usage telemetry, no accuracy backtest pipeline, and no published user count anywhere else in the codebase. These are fabricated. |
| "Trusted sources: Reuters, Bloomberg, Financial Times, The Economist, Nature" | The actual ingestion pipeline reads from sources defined in `data-pipeline/` — confirm against that team's source list, but the landing page is implying named partnerships that do not exist in the codebase. |

### Features mentioned that don't exist (or are not delivered as implied)

- Continuous / live probability updates.
- A daily delta on probability (`+2.4% today`).
- A 0–100 confidence score (the dashboard uses a 3-bucket label).
- A working "Systems Operational" status indicator (Footer dot is static).

### Features that exist but are omitted

The Slice 1–12 dashboard work surfaces several distinctive things the
landing page never mentions:

- **Drivers vs. headwinds** (key factors split by `direction`) — the
  dashboard's most legible signal.
- **What I didn't find** — the model's epistemic-honesty surface.
- **Reasoning chain** — the post-hoc chain-of-thought view.
- **Evidence credibility tiers** (tier_1 / tier_2 / tier_3) and
  per-evidence scoring (relevance, recency, impact).
- **Tier** (tier_1 / tier_2) of the forecast itself.
- **Consensus strength** (Strong / Moderate / Weak), one of the three
  family-of-metrics fields.
- **Follow-up messages** on a session.

### Does the landing page feel like the same product as the dashboard?

No. After Slices 1–12 the dashboard has settled into a calmer, more
typographic identity (verdict-first layout, restrained gradients,
tabular evidence). The landing page is doing the opposite — gradient
orbs, rotated icon tiles, brand-tinted shadows on every card, a
gradient-clipped headline inside a fake browser frame. It reads like an
earlier generation of the same brand, before the dashboard found its
voice.

---

## 6. Generic-AI-template indicators

Specific, observable evidence rather than vibe:

- **Gradient-on-everything.** The teal→blue→purple gradient appears on:
  the hero logo halo, the word "evidence", the GoogleAuthButton hover
  bar, three icon tiles in ProductExplanation, the dashboard mock H3,
  the gauge donut stroke, the gauge "72.3%" number, three step tiles
  in HowItWorks, the connecting line between steps, and the icon tile
  in WhoItsFor. Eleven uses of the same gradient on one page.
- **Animated radial-gradient "orbs" in the hero** — the most recognisable
  AI-landing-page cliché of the 2023–2025 cohort. Three of them, with
  float keyframes.
- **Faint grid overlay** behind the hero — same cliché, paired.
- **Generic copy that could describe any AI product.**
  - "AI-powered probabilities, confidence scores, and real-time
    evidence tracking — all in one place."
  - "Submit any future event… Our AI begins gathering relevant evidence
    immediately."
  - "Our system gathers news, expert opinions, public sentiment, and
    historical precedents in real-time."
- **Pill badges with a colored dot** ("• How it works", "• 3 Simple
  Steps") — the shadcn/Linear-clone tell.
- **Fabricated stats with the format `XK+ / XX% / XXX+`** — three stats,
  one of each shape. The "+2.4% today" sparkline-style microcopy on
  the gauge is also in that family.
- **Mac-browser-chrome wrapper** around a fake dashboard. This was a
  Stripe/Vercel pattern in 2020 that became universal in AI startup
  templates by 2024.
- **Fake URL bar reading `app.anizai.com/dashboard`** — the URL is
  invented (real product runs elsewhere).
- **"Trusted sources" row with stylized wordmarks** in serif/italic
  faux-logo styling. No actual logos, no partnership.
- **"Systems Operational" pulsing green dot in the footer** — a
  Vercel/PlanetScale-style affordance, but it's purely decorative here.
- **Step tiles ship pre-rotated** so they "un-rotate" on hover — a
  playful detail that has appeared on so many AI landing pages it now
  reads as a tell, not a delight.
- **"Curious Minds" persona** — the catch-all third persona is a
  template-y answer to "who are your users?".
- **Brand colors are renamed-but-unmodified Tailwind defaults** —
  `anizai-teal` is `teal`, `anizai-blue` is `blue`, `anizai-purple`
  is `purple`. Nothing in the palette is original.

Distinctive (not template-y) things on the page:

- The brain logo is a real custom asset.
- The hero tagline ("Forecast the future with **evidence**, not guesses")
  is a strong product-specific line — though it gets diluted by the
  subhead that follows.

---

## 7. Notable issues

Bugs / clearly-wrong, not opinion:

1. **`Hero.tsx:124` — fake affordance.** The "See how it works" scroll
   cue has `cursor-pointer`, hover-opacity transition, and a bouncing
   chevron, but no `onClick` handler. Clicking does nothing. (Same
   class of bug as Known UX problem #12 in `CLAUDE.md`.)
2. **`UIShowcase.tsx` — fictional dashboard.** The mock shows a 0–100
   confidence score and a "+2.4% today" daily delta — neither field
   exists in the real `SessionResult` payload. A new user landing on
   the page will form expectations the product cannot meet.
3. **`Hero.tsx:106–121` — fabricated social proof.** "10K+ forecasts
   made / 92% accuracy rate / 500+ active users" are hardcoded
   literals. Beyond product honesty, "92% accuracy rate" without a
   methodology link borders on a substantive claim.
4. **`Footer.tsx:137–138` — fake status pill.** "Systems Operational"
   with pulsing green dot is not wired to any health check.
5. **`LandingPage.tsx:30–62` — no logo in top nav.** The brand mark is
   not present in the top nav. Hero is the only place it appears
   above the fold.
6. **Top-nav "Home" link** points to `onNavigation.home()` →
   `setAppState('landing')` — i.e. you are already on it. It's a
   no-op from the landing page.
7. **CTA → wrong destination.** Three CTAs labeled "Get started" route
   to `LoginPage` rather than `SignupPage`. New visitors with no
   account are sent to a sign-in form first.
8. **Animations ignore `prefers-reduced-motion`.** Hero orbs, scroll
   chevron, hover-rotations, pulsing dot — none gated.
9. **Accessibility — gradient-clipped headings.** The hero H1 and
   tagline use `bg-clip-text text-transparent`. On browsers without
   `background-clip: text` support the text becomes invisible. Modern
   browsers handle it, but the fallback is none.
10. **Accessibility — color-only meaning in mock.** "Active" badge
    relies on green; "+2.4% today" relies on green; no
    icon/screenreader cues. Low-stakes since it's a mock, but it sets
    the pattern for what gets shipped.
11. **`UIShowcase.tsx:115–116` & throughout — `border-anizai-teal-200`
    and similar hover borders.** These exist in the palette but read
    very pale; the hover state is almost invisible on a white card.
12. **Inline `<style>` block in `Hero.tsx:135–142`** declaring the
    `float` keyframe. The keyframe is hero-specific but lives in a
    runtime-injected style tag rather than the global CSS. Multiple
    hero mounts (unlikely on this page but possible in tests/stories)
    would duplicate the rule.
13. **`Footer.tsx` props type mismatch.** `FooterProps.onNavigation`
    omits `home`, but the real `navigationHandlers` from `App.tsx`
    includes it. Not a bug — but the footer cannot link "back to home"
    even if asked to.
14. **Inter font weight 800/900 not requested.** `index.html` loads
    `wght@300;400;500;600;700`, but several headings use `font-bold`
    (700) with very tight tracking that wants 800/900 weight to read
    properly. Hero H1 in particular looks under-weighted at large
    sizes.
15. **No `<meta name="description">`, no Open Graph tags, no Twitter
    card** in `index.html`. The landing page has no SEO surface.

---

## 8. Open questions

- **Are the hero stats meant to become live?** "10K+ / 92% / 500+" — is
  there a roadmap item to wire these to real telemetry, or do they
  stay illustrative? If they stay, do we want them removed entirely
  (they read as a credibility claim that does not survive scrutiny)?
- **"92% accuracy rate" — does Anizai have an accuracy backtest?** The
  Methodology page might already speak to this; this audit did not
  read it. If there is no published methodology, the claim should not
  be on the landing page.
- **Are "Reuters, Bloomberg, Financial Times, The Economist, Nature"
  real partner sources?** The data-pipeline source list (owned by Ron)
  should be checked. If they're not actual ingest sources, the
  "trusted sources" row is misleading.
- **Should "Get started" CTAs route to signup or login?** Current
  behavior is login. The hero microcopy promises "No credit card
  required" — that's a signup promise, not a login one.
- **Is the UIShowcase meant to track the real dashboard?** After Slices
  1–12 the actual dashboard looks different — drivers/headwinds,
  evidence credibility tiers, etc. Is the mock meant to be a faithful
  preview or a stylized abstraction?
- **Does the "Systems Operational" pill need to be real?** Either wire
  it to a status endpoint or remove it.
- **Is there a target audience hierarchy?** The WhoItsFor section lists
  three personas as equals, but `CLAUDE.md` is explicit that the
  primary user is someone deciding whether to bet (Polymarket-style).
  Is that hierarchy meant to surface on the landing page?
- **Is a router (`react-router`) on the table for the redesign?** The
  current `useState`-as-router pattern in `App.tsx` means every public
  page hard-rerenders the SPA shell and there are no shareable URLs
  for `/features`, `/about`, etc. That's a constraint on what
  navigation patterns the new landing page can use.
- **Mobile / tablet support requirements?** The audit notes mobile
  behavior, but is mobile in scope for the redesign, or desktop-first?
- **Is `SiteHeader.tsx` meant to replace the inline nav?** The component
  exists but isn't used by `LandingPage.tsx`. Was it abandoned, or is
  the landing page expected to migrate to it?
