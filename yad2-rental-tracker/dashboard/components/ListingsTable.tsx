"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase, type Listing } from "@/lib/supabaseClient";

type SortKey = "first_seen" | "price";

function fmtPrice(p: number | null): string {
  return p == null ? "—" : `${p.toLocaleString()}₪`;
}

function fmtNum(n: number | null): string {
  if (n == null) return "—";
  return Number.isInteger(n) ? String(n) : String(n);
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

export default function ListingsTable() {
  const [rows, setRows] = useState<Listing[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>("first_seen");
  const [loading, setLoading] = useState(true);

  async function load() {
    const { data, error } = await supabase
      .from("listings")
      .select("*")
      .eq("status", "active")
      .order("first_seen", { ascending: false });
    if (error) console.error(error);
    setRows(data ?? []);
    setLoading(false);
  }

  useEffect(() => {
    load();
    // Live updates via Supabase Realtime — no manual refresh (brief §9).
    const channel = supabase
      .channel("listings-changes")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "listings" },
        () => load()
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  const sorted = useMemo(() => {
    const copy = [...rows];
    if (sortKey === "price") {
      copy.sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
    } else {
      copy.sort(
        (a, b) =>
          new Date(b.first_seen).getTime() - new Date(a.first_seen).getTime()
      );
    }
    return copy;
  }, [rows, sortKey]);

  if (loading) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="toolbar">
        <span className="count">{sorted.length} active listings</span>
        <label>
          Sort by{" "}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
          >
            <option value="first_seen">Newest first</option>
            <option value="price">Price (low → high)</option>
          </select>
        </label>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Thumb</th>
              <th>Title</th>
              <th>City</th>
              <th>Neighborhood</th>
              <th>Price</th>
              <th>Δ</th>
              <th>Rooms</th>
              <th>Size</th>
              <th>Parking</th>
              <th>Type</th>
              <th>First seen</th>
              <th>Link</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const dropped = (r.price_diff ?? 0) > 0;
              const cls = [
                r.relisted ? "relisted" : "",
                dropped ? "dropped" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <tr key={r.id} className={cls}>
                  <td>
                    {r.image_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={r.image_url} alt="" className="thumb" />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{r.title ?? "—"}</td>
                  <td>{r.city ?? "—"}</td>
                  <td>{r.neighborhood ?? "—"}</td>
                  <td>{fmtPrice(r.price)}</td>
                  <td>
                    {dropped ? (
                      <span className="delta">
                        ▼ {fmtPrice(r.price_diff)}
                        <br />
                        <small>was {fmtPrice(r.previous_price)}</small>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{fmtNum(r.rooms)}</td>
                  <td>{r.size_sqm == null ? "—" : `${fmtNum(r.size_sqm)} m²`}</td>
                  <td>{r.has_parking ? "✅" : r.has_parking === false ? "—" : "?"}</td>
                  <td>{r.property_type ?? "—"}</td>
                  <td>{fmtDate(r.first_seen)}</td>
                  <td>
                    {r.url ? (
                      <a href={r.url} target="_blank" rel="noreferrer">
                        Open ↗
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
