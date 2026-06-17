import Link from "next/link";
import ResearchView from "@/components/ResearchView";
import WorkflowSection from "@/components/sections/WorkflowSection";
import CapabilitiesSection from "@/components/sections/CapabilitiesSection";

// Server component shell; the streaming UI is a client component.
export default function Home() {
  return (
    <main>
      {/* Palantir-style oversized hero. */}
      <section className="mx-auto max-w-6xl px-4 pb-12 pt-16 sm:pt-24">
        <h1 className="display text-balance text-4xl font-medium text-fg sm:text-6xl lg:text-7xl">
          Open-web research,{" "}
          <span className="text-subtle">planned, parallel</span>, and self-correcting —
          from a single question to a fully cited report.
        </h1>
        <p className="mt-8 max-w-2xl text-lg text-muted">
          Reflect decomposes your topic into a task graph, runs specialised agents
          concurrently across free LLM tiers, and synthesises a structured report that
          streams back in real time.
        </p>
        <div className="mt-10 flex flex-wrap gap-3">
          <Link
            href="#start"
            className="rounded-full bg-accent px-6 py-3 text-sm font-medium text-accent-fg transition-opacity hover:opacity-90"
          >
            Start researching
          </Link>
          <Link
            href="#how-it-works"
            className="rounded-full border border-edge px-6 py-3 text-sm font-medium text-fg transition-colors hover:bg-raised"
          >
            See how it works
          </Link>
          <Link
            href="/architecture"
            className="rounded-full border border-edge px-6 py-3 text-sm font-medium text-fg transition-colors hover:bg-raised"
          >
            View architecture
          </Link>
        </div>
      </section>

      {/* The interactive research console. */}
      <section id="start" className="scroll-mt-24 mx-auto max-w-6xl px-4 pb-16">
        <h2 className="mb-6 display text-2xl font-semibold text-fg">Start a research run</h2>
        <ResearchView />
      </section>

      <WorkflowSection />
      <CapabilitiesSection />
    </main>
  );
}
