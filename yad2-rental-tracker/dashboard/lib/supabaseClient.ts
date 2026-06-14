import { createClient } from "@supabase/supabase-js";

// The dashboard reads with the ANON key only (brief §9). Never put the
// service-role key in the frontend.
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL as string;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY as string;

if (!supabaseUrl || !supabaseAnonKey) {
  // Surfaced at build/runtime so misconfiguration is obvious.
  console.warn(
    "Missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY env vars"
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  realtime: { params: { eventsPerSecond: 5 } },
});

export type Listing = {
  id: string;
  title: string | null;
  city: string | null;
  neighborhood: string | null;
  address: string | null;
  property_type: string | null;
  rooms: number | null;
  size_sqm: number | null;
  floor: number | null;
  has_parking: boolean | null;
  price: number | null;
  previous_price: number | null;
  price_diff: number | null;
  price_changed_at: string | null;
  url: string | null;
  image_url: string | null;
  status: string;
  relisted: boolean;
  first_seen: string;
  last_seen: string;
};
