"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { colorFor, cssVar, PROVIDERS, toChartRows, type CallRecord } from "@/lib/metrics";

// Re-render when the theme flips so CSS-variable-derived colors re-resolve.
function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    bump(); // resolve once after mount (vars are unavailable during SSR)
    window.addEventListener("themechange", bump);
    return () => window.removeEventListener("themechange", bump);
  }, []);
  return tick;
}

export default function UsageChart({ series }: { series: CallRecord[] }) {
  const rows = toChartRows(series);
  useThemeTick(); // re-render (and thus recolor) on theme toggle

  if (rows.length === 0) {
    return <p className="text-sm text-subtle">No usage recorded yet — run a research query.</p>;
  }

  const grid = cssVar("--edge", "228 228 231");
  const axis = cssVar("--subtle", "161 161 170");
  const surface = cssVar("--surface", "244 244 245");
  const muted = cssVar("--muted", "82 82 91");

  return (
    <ResponsiveContainer width="100%" height={360}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 16, left: 8 }}>
        <CartesianGrid stroke={grid} strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          stroke={axis}
          tick={{ fontSize: 12 }}
          tickMargin={10}
          minTickGap={32}
          height={40}
          unit="s"
        />
        <YAxis
          stroke={axis}
          tick={{ fontSize: 12 }}
          width={48}
          tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)}
        />
        <Tooltip
          contentStyle={{ background: surface, border: `1px solid ${grid}`, borderRadius: 10, fontSize: 12 }}
          labelStyle={{ color: muted }}
          itemStyle={{ color: muted }}
          formatter={(value: number, name: string) => [`${value.toLocaleString()} tok`, name]}
          labelFormatter={(t: number) => `${t}s`}
        />
        <Legend
          verticalAlign="bottom"
          wrapperStyle={{ fontSize: 12, textTransform: "capitalize", paddingTop: 20 }}
        />
        {PROVIDERS.map((p) => (
          <Line
            key={p}
            type="monotone"
            dataKey={p}
            stroke={colorFor(p)}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
