import ListingsTable from "@/components/ListingsTable";

export const dynamic = "force-dynamic";

export default function Home() {
  return (
    <main>
      <header className="page-header">
        <h1>🏠 Yad2 Rental Tracker</h1>
        <p className="muted">
          Live view of matching rentals · updates automatically via Supabase
          Realtime
        </p>
      </header>
      <ListingsTable />
      <footer className="legend">
        <span className="swatch dropped" /> price dropped
        <span className="swatch relisted" /> relisted
      </footer>
    </main>
  );
}
