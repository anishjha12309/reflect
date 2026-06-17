import Link from "next/link";

type FooterLink = { label: string; href: string; external?: boolean };

// Every link resolves to a real destination — an internal route/anchor, or the
// provider's own site. No decorative entries.
const COLUMNS: { heading: string; links: FooterLink[] }[] = [
  {
    heading: "Explore",
    links: [
      { label: "Research", href: "/#start" },
      { label: "How it works", href: "/#how-it-works" },
      { label: "Capabilities", href: "/#capabilities" },
      { label: "Architecture", href: "/architecture" },
      { label: "Quota Dashboard", href: "/metrics" },
    ],
  },
  {
    heading: "LLM Providers",
    links: [
      { label: "Cerebras", href: "https://cerebras.ai", external: true },
      { label: "Groq", href: "https://groq.com", external: true },
      { label: "Gemini (AI Studio)", href: "https://ai.google.dev", external: true },
      { label: "OpenRouter", href: "https://openrouter.ai", external: true },
    ],
  },
  {
    heading: "Search & Tooling",
    links: [
      { label: "Tavily", href: "https://tavily.com", external: true },
      { label: "Serper", href: "https://serper.dev", external: true },
      { label: "SearXNG", href: "https://docs.searxng.org", external: true },
      { label: "LangGraph", href: "https://langchain-ai.github.io/langgraph/", external: true },
    ],
  },
];

export default function Footer() {
  return (
    <footer className="no-print mt-20 px-4 sm:px-6">
      <div className="mx-auto max-w-7xl">
        {/* Twin call-to-action boxes — both navigate somewhere real. */}
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <CtaBox href="/#how-it-works" label="See how it works" variant="light" />
          <CtaBox href="/#start" label="Start researching" variant="dark" />
        </div>

        <div className="my-12 h-px w-full bg-edge" />

        <div className="grid grid-cols-2 gap-8 pb-16 sm:grid-cols-2 lg:grid-cols-4">
          <div className="col-span-2 sm:col-span-2 lg:col-span-1">
            <p className="text-sm text-fg">© 2026 Reflect.</p>
            <p className="mt-1 max-w-xs text-sm text-muted">
              Multi-agent open-web research, planned and self-correcting — running entirely on
              no-card free tiers.
            </p>
            <p className="mt-4 text-xs text-subtle">Built with LangGraph · FastAPI · Next.js</p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.heading}>
              <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-subtle">
                {col.heading}
              </h3>
              <ul className="space-y-3">
                {col.links.map((link) => (
                  <li key={link.label}>
                    {link.external ? (
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-sm text-fg transition-colors hover:text-muted"
                      >
                        {link.label}
                        <ExternalIcon />
                      </a>
                    ) : (
                      <Link
                        href={link.href}
                        className="text-sm text-fg transition-colors hover:text-muted"
                      >
                        {link.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </footer>
  );
}

function ExternalIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-subtle">
      <path d="M7 17 17 7M9 7h8v8" />
    </svg>
  );
}

function CtaBox({
  href,
  label,
  variant,
}: {
  href: string;
  label: string;
  variant: "light" | "dark";
}) {
  const styles = variant === "dark" ? "bg-accent text-accent-fg" : "bg-raised text-fg";
  return (
    <Link
      href={href}
      className={`group flex items-center justify-between rounded-xl px-8 py-14 transition-transform hover:-translate-y-0.5 sm:px-10 sm:py-16 ${styles}`}
    >
      <span className="display text-3xl font-medium sm:text-4xl">{label}</span>
      <svg
        width="36"
        height="36"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className="shrink-0 transition-transform group-hover:translate-x-1"
      >
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    </Link>
  );
}
