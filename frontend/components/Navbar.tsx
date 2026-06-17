"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import ThemeToggle from "./ThemeToggle";

const NAV_LINKS = [
  { href: "/#start", label: "Research" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#capabilities", label: "Capabilities" },
  { href: "/architecture", label: "Architecture" },
  { href: "/metrics", label: "Metrics" },
];

// Floating frosted pill nav (Palantir pattern): logo left; Get Started + search +
// menu right. Primary navigation lives behind the menu on every breakpoint.
export default function Navbar() {
  const [open, setOpen] = useState(false);

  // Lock body scroll while the menu overlay is open.
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header className="no-print sticky top-0 z-50 px-3 pt-3 sm:px-4 sm:pt-4">
      <nav className="frost mx-auto flex max-w-7xl items-center justify-between gap-3 rounded-2xl border border-edge px-4 py-2.5 shadow-sm sm:px-5">
        <Logo />

        <div className="flex items-center gap-2 sm:gap-3">
          <Link
            href="/#start"
            className="hidden rounded-full bg-accent px-5 py-2 text-sm font-medium text-accent-fg transition-opacity hover:opacity-90 sm:inline-flex"
          >
            Get Started
          </Link>
          <ThemeToggle />
          <button
            type="button"
            aria-label="Open menu"
            aria-expanded={open}
            onClick={() => setOpen(true)}
            className="grid h-9 w-9 place-items-center rounded-full border border-edge text-fg transition-colors hover:bg-raised"
          >
            <MenuIcon />
          </button>
        </div>
      </nav>

      {open && <MenuOverlay onClose={() => setOpen(false)} />}
    </header>
  );
}

function MenuOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 animate-fade-in">
      <button
        aria-label="Close menu"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-ink/40 backdrop-blur-sm"
      />
      <div className="frost absolute right-3 top-3 w-[min(22rem,calc(100vw-1.5rem))] rounded-2xl border border-edge p-4 shadow-xl sm:right-4 sm:top-4">
        <div className="mb-2 flex items-center justify-between">
          <Logo />
          <button
            type="button"
            aria-label="Close menu"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-full border border-edge text-fg hover:bg-raised"
          >
            <CloseIcon />
          </button>
        </div>
        <nav className="mt-2 flex flex-col">
          {NAV_LINKS.map((l) => (
            <Link
              key={l.label}
              href={l.href}
              onClick={onClose}
              className="flex items-center justify-between border-b border-edge py-3 text-lg text-fg transition-colors hover:text-muted"
            >
              {l.label}
              <ArrowIcon />
            </Link>
          ))}
        </nav>
        <Link
          href="/#start"
          onClick={onClose}
          className="mt-4 flex items-center justify-center rounded-full bg-accent px-5 py-3 text-sm font-medium text-accent-fg hover:opacity-90"
        >
          Get Started
        </Link>
      </div>
    </div>
  );
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2 text-fg">
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden className="shrink-0">
        <rect x="2" y="2" width="20" height="20" rx="5" fill="currentColor" />
        <circle cx="12" cy="12" r="4.4" className="fill-ink" />
        <circle cx="12" cy="12" r="1.9" fill="currentColor" />
      </svg>
      <span className="text-[17px] font-semibold tracking-tight">Reflect</span>
    </Link>
  );
}

function MenuIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-subtle">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
