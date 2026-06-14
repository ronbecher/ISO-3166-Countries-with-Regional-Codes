/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    // Yad2 serves listing thumbnails from its own CDN.
    remotePatterns: [
      { protocol: "https", hostname: "**.yad2.co.il" },
      { protocol: "https", hostname: "img.yad2.co.il" },
    ],
  },
};

module.exports = nextConfig;
