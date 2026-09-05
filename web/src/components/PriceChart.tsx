import { useEffect, useRef } from "react";
import {
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { BarOut, EventOut } from "../lib/types";

interface Props {
  bars: BarOut[];
  entry: number | null;
  target: number | null;
  stop: number | null;
  windowStart: string;
  windowEnd: string;
  events: EventOut[];
}

/** Close-price line with entry / target / stop price lines, the idea window shaded, and resolution markers. */
export function PriceChart({ bars, entry, target, stop, windowStart, windowEnd, events }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: "#161b22" },
        textColor: "#8b949e",
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
        fontSize: 11,
      },
      grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: { borderColor: "#30363d", timeVisible: false },
      crosshair: { horzLine: { color: "#30363d" }, vertLine: { color: "#30363d" } },
    });
    chartRef.current = chart;

    // Window shading: a histogram on a hidden overlay scale that fills the full height inside the window.
    const shade = chart.addSeries(HistogramSeries, {
      priceScaleId: "shade",
      color: "rgba(240, 180, 41, 0.07)",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("shade").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    shade.setData(
      bars.filter((b) => b.as_of >= windowStart && b.as_of <= windowEnd).map((b) => ({ time: b.as_of as Time, value: 1 })),
    );

    const line = chart.addSeries(LineSeries, {
      color: "#4a9eff",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    line.setData(bars.map((b) => ({ time: b.as_of as Time, value: b.close })));

    if (entry !== null) line.createPriceLine({ price: entry, color: "#8b949e", lineStyle: LineStyle.Dashed, lineWidth: 1, title: "entry" });
    if (target !== null) line.createPriceLine({ price: target, color: "#f0b429", lineStyle: LineStyle.Solid, lineWidth: 1, title: "target" });
    if (stop !== null) line.createPriceLine({ price: stop, color: "#e74c3c", lineStyle: LineStyle.Dotted, lineWidth: 1, title: "stop" });

    const markers: SeriesMarker<Time>[] = events
      .filter((e) => e.event_type !== "progress")
      .map((e) => ({
        time: e.occurred_on as Time,
        position: e.event_type === "target_hit" ? "belowBar" : "aboveBar",
        color: e.event_type === "target_hit" ? "#2ecc71" : e.event_type === "stop_hit" ? "#e74c3c" : "#8b949e",
        shape: e.event_type === "target_hit" ? "arrowUp" : e.event_type === "stop_hit" ? "arrowDown" : "circle",
        text: e.event_type.replace("_", " "),
      }));
    if (markers.length) createSeriesMarkers(line, markers);

    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, entry, target, stop, windowStart, windowEnd, events]);

  if (bars.length === 0) {
    return <div className="rounded-md border border-line bg-surface-2 px-3 py-6 text-center text-sm text-muted">No stored bars yet. Run a price refresh.</div>;
  }
  return <div ref={ref} className="w-full" />;
}
