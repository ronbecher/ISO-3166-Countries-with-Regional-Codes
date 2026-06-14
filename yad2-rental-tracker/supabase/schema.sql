-- Yad2 Rental Tracker — Supabase schema (brief §6)
-- Single table keyed by Yad2 listing id.

create table if not exists listings (
  id               text primary key,          -- Yad2 listing id
  title            text,
  city             text,
  neighborhood     text,
  address          text,
  property_type    text,
  rooms            numeric,
  size_sqm         numeric,
  floor            int,
  has_parking      boolean,
  price            int,                        -- current price
  previous_price   int,                        -- set when a drop is detected
  price_diff       int,                        -- previous_price - price (positive = drop)
  price_changed_at timestamptz,
  url              text,
  image_url        text,
  status           text default 'active',      -- 'active' | 'removed'
  relisted         boolean default false,
  first_seen       timestamptz default now(),
  last_seen        timestamptz default now()
);

create index if not exists listings_status_idx on listings (status);
create index if not exists listings_city_hood_idx on listings (city, neighborhood);

-- Realtime: the dashboard subscribes to changes on this table.
-- (Enable Realtime for the table in the Supabase dashboard, or:)
alter publication supabase_realtime add table listings;

-- Row Level Security: the dashboard reads with the anon key; the Action writes
-- with the service-role key (which bypasses RLS). Enable RLS and allow anon
-- SELECT only.
alter table listings enable row level security;

drop policy if exists "anon can read listings" on listings;
create policy "anon can read listings"
  on listings for select
  to anon
  using (true);
