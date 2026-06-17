import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";

// Inter — the closest freely-licensable match to Palantir's neo-grotesque display face.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Reflect — Multi-Agent Research",
  description: "Open-web, multi-step planned research with self-correction.",
};

// Runs before paint: applies the saved (or system) theme so there's no flash.
const themeScript = `
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (!t) t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    if (t === "dark") document.documentElement.classList.add("dark");
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen flex flex-col font-sans">
        <Navbar />
        <div className="flex-1">{children}</div>
        <Footer />
      </body>
    </html>
  );
}
