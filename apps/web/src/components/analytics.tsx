"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ChartSpec, Result, Row } from "@/lib/types";
echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

export function filterRows(rows: Row[], filter: string, dateColumn: string, start: string, end: string) {
  return rows.filter(row => (!filter || Object.values(row).some(value => String(value).toLowerCase().includes(filter.toLowerCase()))) &&
    (!start || String(row[dateColumn]) >= start) && (!end || String(row[dateColumn]).slice(0, 10) <= end));
}

export function Analytics({ result, initial, onSave, exportUrl }: { result: Result; initial?: ChartSpec; onSave?: (spec: ChartSpec) => void; exportUrl?: string }) {
  const [kind, setKind] = useState<ChartSpec["kind"]>(initial?.kind || "table");
  const [x, setX] = useState(initial?.x || result.columns[0] || "");
  const numeric = result.columns.filter(c => result.rows.some(r => typeof r[c] === "number"));
  const [y, setY] = useState(initial?.y || numeric[0] || "");
  const [filter, setFilter] = useState(""); const [start, setStart] = useState(""); const [end, setEnd] = useState("");
  const [group, setGroup] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const rows = useMemo(() => {
    const filtered = filterRows(result.rows, filter, x, start, end);
    if (!group || !x || !y) return filtered;
    const groups = new Map<string, number>();
    filtered.forEach(row => { const key = String(row[x]); groups.set(key, (groups.get(key) || 0) + Number(row[y] || 0)); });
    return [...groups].map(([key, value]) => ({ [x]: key, [y]: value }));
  }, [result, filter, x, y, start, end, group]);
  useEffect(() => {
    if (!container.current || kind === "table" || kind === "kpi") return;
    const chart = echarts.init(container.current);
    const values = rows.map(row => [row[x], row[y]]);
    chart.setOption({ animation: false, tooltip: { trigger: kind === "pie" ? "item" : "axis", renderMode: "richText" }, grid: { left: 65, top: 25, right: 25, bottom: 65 },
      xAxis: kind === "pie" ? undefined : { type: kind === "scatter" ? "value" : "category", data: kind === "scatter" ? undefined : rows.map(row => String(row[x])), axisLabel: { hideOverlap: true, color: "#888" } },
      yAxis: kind === "pie" ? undefined : { type: "value", axisLabel: { color: "#888" }, splitLine: { lineStyle: { color: "#8882" } } },
      series: [{ type: kind, data: kind === "pie" ? rows.map(row => ({ name: String(row[x]), value: row[y] })) : kind === "scatter" ? values : rows.map(row => row[y]), itemStyle: { color: "#5e7d70", borderRadius: kind === "bar" ? [4,4,0,0] : 0 }, radius: kind === "pie" ? ["40%", "70%"] : undefined }],
      dataZoom: kind !== "pie" && rows.length > 15 ? [{ type: "slider", bottom: 0 }] : undefined });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(container.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [rows, kind, x, y]);
  const columns = group ? [x, y] : result.columns;
  return <div className="analytics"><div className="analytics-controls">
    <select aria-label="Chart type" value={kind} onChange={e => setKind(e.target.value as ChartSpec["kind"])}>{["table", ...(numeric.length ? ["bar", "line", "pie", "scatter", "kpi"] : [])].map(k => <option key={k}>{k}</option>)}</select>
    <select aria-label="Group or date column" value={x} onChange={e => setX(e.target.value)}>{result.columns.map(c => <option key={c}>{c}</option>)}</select>
    {numeric.length > 0 && <select aria-label="Value column" value={y} onChange={e => setY(e.target.value)}>{numeric.map(c => <option key={c}>{c}</option>)}</select>}
    <input aria-label="Filter result" placeholder="Filter rows…" value={filter} onChange={e => setFilter(e.target.value)}/>
    <details><summary>Date & grouping</summary><div className="row"><input aria-label="Start date" type="date" value={start} onChange={e => setStart(e.target.value)}/><input aria-label="End date" type="date" value={end} onChange={e => setEnd(e.target.value)}/><label><input type="checkbox" checked={group} onChange={e => setGroup(e.target.checked)}/> Sum by selected column</label></div></details>
  </div>
    {kind === "table" ? <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}>{columns.map(c => <td key={c}>{String(row[c] ?? "—")}</td>)}</tr>)}</tbody></table></div> : kind === "kpi" ? <div className="kpi"><small>{y} · sum of visible rows</small><strong>{rows.reduce((sum, row) => sum + Number(row[y] || 0), 0).toLocaleString()}</strong></div> : <div ref={container} className="chart" role="img" aria-label={`${kind} chart of ${y} by ${x}`}/>}
    <div className="result-footer"><span>{rows.length} visible rows {result.truncated ? "· result capped" : ""}</span>{exportUrl && <a href={exportUrl}>Export original CSV</a>}{onSave && <button onClick={() => onSave({ kind, x, y, title: initial?.title || `${y || "Data"} by ${x}`, result_index: initial?.result_index || 0 })}>Save insight</button>}</div>
  </div>;
}
