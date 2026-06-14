import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Yad2 Rental Tracker",
  description: "Live dashboard of matching Yad2 rental listings",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
